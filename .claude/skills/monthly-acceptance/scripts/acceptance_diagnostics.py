"""Scoped, read-only investigation queries with complete, stable pagination keys.

The frozen manifest supplies the only permitted tables and observation window.
These queries collect observations; they do not establish that sales are genuine,
that a shared SKU is the same selling package, or that cross-platform IDs double
count sales. Missing rows are observations of absence, never inferred zero sales.

``diagnostic_query`` returns SQL. ``diagnostic_job`` adds keys and scope metadata
for ``acceptance_transport.execute_job``. No query chooses a Top-N. An explicit
product list is a visible shard of the two-period path cohort, not full coverage.
"""
from __future__ import annotations

import json
import re

from acceptance_core import business_key, canonical, month, months, quoted, readonly_sql, shift
from acceptance_queries import path_expr


KINDS = frozenset({'brands', 'sku_trace', 'cross_platform', 'source_coverage'})
RAW_FIELDS = ('category_1', 'category_2', 'category_3', 'category_4', 'category_5', 'sub_category')
ATTRIBUTES = ('product_title', 'product_title_cn', 'sku_title', 'std_brand_name',
              'product_url', 'discount_price', 'page_price', 'page_sales', 'product_listing_time')
IDENTITY_KEYS = ['source_layer', 'site', 'platform', 'month_dt', 'product_id',
                 'sku_id', 'raw_path', 'std_path', 'attribute_key']
TABLE_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*')


def traceable_sku(value):
    """Only a usable key may connect different parents; source rows stay intact.

    Zero is a known supplier placeholder. Do not guess additional placeholder
    tokens or normalize the stored key when matching otherwise valid SKUs.
    """
    return isinstance(value, str) and value.strip() not in ('', '0')


def _traceable_sku_sql(field):
    return f"(NULLIF(TRIM({field}),'') IS NOT NULL AND TRIM({field})<>'0')"


def _scope(manifest, kind, site, level, path, base_month, current_month, product_ids):
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError('未知诊断类型')
    if not isinstance(manifest, dict) or not isinstance(manifest.get('policy'), dict):
        raise ValueError('缺少冻结 manifest/policy')
    if (not isinstance(site, str) or not re.fullmatch(r'[A-Za-z0-9_]+', site)
            or site not in manifest.get('sites', []) or site not in manifest.get('starts', {})):
        raise ValueError('站点不在冻结范围内')
    if level != 'raw' and (type(level) is not int or level not in (0, 1, 2, 3)):
        raise ValueError('level 必须为 0/1/2/3 或 raw')
    if kind == 'brands' and level == 'raw':
        raise ValueError('具名品牌贡献必须使用标准类目范围')
    if kind == 'cross_platform' and site != 'cn':
        raise ValueError('跨平台同 ID 检查只适用于冻结的 cn 站点')
    try:
        values = json.loads(path) if isinstance(path, str) else path
    except (ValueError, TypeError) as exc:
        raise ValueError('path 必须为完整 JSON 路径数组') from exc
    arity = 6 if level == 'raw' else level
    if (not isinstance(values, (list, tuple)) or len(values) != arity
            or any(v is not None and not isinstance(v, str) for v in values)):
        raise ValueError('path 段数须与 level 一致；raw 必须保留全部 6 段及空值')
    window_start, window_end = manifest['starts'][site], manifest.get('data_month')
    for value in (window_start, window_end, base_month, current_month):
        if not isinstance(value, str):
            raise ValueError('月份必须是 YYYY-MM')
        month(value)
    if not window_start <= base_month <= current_month <= window_end:
        raise ValueError('比较月份必须有序且全部位于冻结数据窗口内')
    if kind in {'brands', 'source_coverage'} and base_month == current_month:
        raise ValueError('此诊断需要不同的基期和后期自然月')
    tables = {}
    for key in ('raw_table', 'std_table'):
        value = manifest['policy'].get(key)
        if not isinstance(value, str) or not TABLE_RE.fullmatch(value):
            raise ValueError('只接受冻结 policy 中的三段式数据表标识符')
        tables[key] = value
    products = None
    if product_ids is not None:
        if (not isinstance(product_ids, (list, tuple)) or not product_ids
                or any(not isinstance(v, str) or not v.strip() or '\x00' in v for v in product_ids)):
            raise ValueError('product_ids 必须是非空精确 ID 清单；None 才表示完整商品集合')
        products = sorted(set(product_ids))
        if kind == 'brands':
            raise ValueError('品牌贡献不能筛选商品，否则会改变完整类目分母')
    # Gap destinations only use the anchor month and following months. Earlier
    # history is required for sku_trace, but cannot contribute to this proof.
    if kind == 'source_coverage':
        window_start, window_end = base_month, current_month
    return {'kind': kind, 'site': site, 'level': level, 'path': canonical(list(values)),
            'base_month': base_month, 'current_month': current_month,
            'window_start': window_start, 'window_end': window_end,
            'product_ids': products, **tables}


def _raw_path():
    return 'CAST(JSON_ARRAY(' + ','.join(RAW_FIELDS) + ') AS STRING)'


def _target_path(scope):
    return _raw_path() if scope['level'] == 'raw' else path_expr(scope['level'])


def _product_filter(scope, alias=''):
    if scope['product_ids'] is None:
        return ''
    return ' AND ' + alias + 'product_id IN (' + ','.join(map(quoted, scope['product_ids'])) + ')'


def _seed(scope, base_only=False):
    seed_table = scope['raw_table'] if scope['level'] == 'raw' else scope['std_table']
    dates = [scope['base_month']] if base_only else [scope['base_month'], scope['current_month']]
    return f"""seed_rows AS (
        SELECT platform,site,month_dt,product_id,sku_id
        FROM {seed_table}
        WHERE site={quoted(scope['site'])} AND {_target_path(scope)}={quoted(scope['path'])}
        AND month_dt IN ({','.join(quoted(d+'-01') for d in sorted(set(dates)))}){_product_filter(scope)}
    )"""


def _all_rows(scope, seed_mode=None):
    """Keep every observed identity/description/path variant and duplicate count.

    Source layers are UNION ALL, never joined by an incomplete key. Attributes
    are grouped explicitly instead of selecting a convenient MAX title/parent.
    Identical source rows remain visible in source_rows; they are not deduplicated.
    """
    selects = []
    attrs = ','.join(ATTRIBUTES)
    for layer in ('raw', 'std'):
        std_path = path_expr(3) if layer == 'std' else 'CAST(NULL AS STRING)'
        applicable = layer == 'std' or scope['level'] == 'raw'
        target = (f'CASE WHEN {_target_path(scope)}={quoted(scope["path"])} THEN 1 ELSE 0 END'
                  if applicable else 'CAST(NULL AS INT)')
        gate = ''
        if seed_mode == 'products':
            gate = ' AND product_id IN (SELECT product_id FROM seed_rows WHERE product_id IS NOT NULL)'
        elif seed_mode == 'trace':
            # Filter before GROUP BY. Aggregating an entire country's history
            # first made even a one-product trace exceed Doris memory limits.
            # The final joins still enforce platform identity; this early
            # superset retains changed parents, paths and all SKU attributes.
            if scope['product_ids'] is not None:
                # Exact shards already supply the parent IDs. Keep the SKU
                # lookup for changed parents, without a correlated OR join.
                gate = (' AND (_observations.product_id IN (' + ','.join(map(quoted, scope['product_ids']))
                        + ') OR _observations.sku_id IN (SELECT sku_id FROM seed_rows WHERE '
                        + _traceable_sku_sql('sku_id') + ')')
            else:
                gate = (' AND (EXISTS (SELECT 1 FROM seed_rows _seed_items WHERE '
                        '_observations.product_id=_seed_items.product_id OR ('
                        + _traceable_sku_sql('_seed_items.sku_id')
                        + ' AND _observations.sku_id=_seed_items.sku_id))')
            # A shard's explicit parent membership already retains every seed
            # row, including missing SKU/platform identities. Only the full
            # cohort needs this fallback for seeds with null parent and SKU.
            if scope['product_ids'] is None and layer == ('raw' if scope['level'] == 'raw' else 'std'):
                dates = [scope['base_month']] if scope['kind'] == 'source_coverage' else [scope['base_month'], scope['current_month']]
                gate += (f" OR ({_target_path(scope)}={quoted(scope['path'])} AND month_dt IN ("
                         + ','.join(quoted(d+'-01') for d in sorted(set(dates))) + ')' + _product_filter(scope) + ')')
            gate += ')'
        selects.append(f"""SELECT {quoted(layer)} AS source_layer,platform,site,month_dt,product_id,sku_id,
            {_raw_path()} AS raw_path,{std_path} AS std_path,{target} AS in_target_path,
            {attrs},CAST(JSON_ARRAY({attrs}) AS STRING) AS attribute_key,
            COUNT(1) AS source_rows,SUM(discount_sales) AS amount,SUM(`count`) AS units,
            SUM(CASE WHEN discount_sales IS NULL THEN 1 ELSE 0 END) AS missing_amount_rows,
            SUM(CASE WHEN `count` IS NULL THEN 1 ELSE 0 END) AS missing_units_rows,
            SUM(CASE WHEN discount_sales IS NULL OR `count` IS NULL OR discount_sales<0 OR `count`<0 THEN 1 ELSE 0 END) AS invalid_value_rows
            FROM {scope[layer+'_table']} _observations
            WHERE site={quoted(scope['site'])} AND month_dt BETWEEN {quoted(scope['window_start']+'-01')} AND {quoted(scope['window_end']+'-01')}{gate}
            GROUP BY platform,site,month_dt,product_id,sku_id,raw_path,std_path,in_target_path,{attrs}""")
    return 'all_rows AS (' + '\nUNION ALL\n'.join(selects) + ')'


def _brands(scope):
    base, current = quoted(scope['base_month']+'-01'), quoted(scope['current_month']+'-01')
    brand = "NULLIF(TRIM(std_brand_name),'')"
    # Missing numeric values are separately counted. If all observed values for a
    # side are null, that side remains NULL; an absent side is an observed-set zero.
    columns = []
    for label, date in [('base', base), ('current', current)]:
        columns.extend([
            f'SUM(CASE WHEN month_dt={date} THEN 1 ELSE 0 END) AS {label}_rows',
            f'COUNT(DISTINCT CASE WHEN month_dt={date} THEN NULLIF(TRIM(product_id),\'\') END) AS {label}_spus',
            f'SUM(CASE WHEN month_dt={date} AND discount_sales IS NULL THEN 1 ELSE 0 END) AS {label}_missing_amount_rows',
            f'SUM(CASE WHEN month_dt={date} AND `count` IS NULL THEN 1 ELSE 0 END) AS {label}_missing_units_rows',
            f'SUM(CASE WHEN month_dt={date} AND (discount_sales IS NULL OR `count` IS NULL OR discount_sales<0 OR `count`<0) THEN 1 ELSE 0 END) AS {label}_invalid_value_rows',
        ])
        for metric, field in [('amount', 'discount_sales'), ('units', '`count`')]:
            columns.append(f"""CASE WHEN SUM(CASE WHEN month_dt={date} THEN 1 ELSE 0 END)>0
                AND COUNT(CASE WHEN month_dt={date} THEN {field} END)=0 THEN NULL
                ELSE SUM(CASE WHEN month_dt={date} THEN {field} ELSE 0 END) END AS {label}_{metric}""")
    return f"""WITH parts AS (
        SELECT {brand} AS brand,CAST(JSON_ARRAY({brand}) AS STRING) AS brand_key,
        {','.join(columns)}
        FROM {scope['std_table']} WHERE site={quoted(scope['site'])}
        AND {_target_path(scope)}={quoted(scope['path'])} AND month_dt IN ({base},{current})
        GROUP BY brand,brand_key
    ), changes AS (
        SELECT *,current_amount-base_amount AS amount_change,current_units-base_units AS units_change FROM parts
    ), totals AS (
        SELECT *,SUM(base_amount) OVER() AS category_base_amount,SUM(current_amount) OVER() AS category_current_amount,
        SUM(base_units) OVER() AS category_base_units,SUM(current_units) OVER() AS category_current_units,
        SUM(base_missing_amount_rows+current_missing_amount_rows) OVER() AS population_missing_amount_rows,
        SUM(base_missing_units_rows+current_missing_units_rows) OVER() AS population_missing_units_rows,
        SUM(ABS(amount_change)) OVER() AS gross_amount_change,SUM(ABS(units_change)) OVER() AS gross_units_change,
        SUM(CASE WHEN amount_change>0 THEN amount_change ELSE 0 END) OVER() AS positive_amount_change,
        SUM(CASE WHEN amount_change<0 THEN amount_change ELSE 0 END) OVER() AS negative_amount_change,
        SUM(CASE WHEN units_change>0 THEN units_change ELSE 0 END) OVER() AS positive_units_change,
        SUM(CASE WHEN units_change<0 THEN units_change ELSE 0 END) OVER() AS negative_units_change FROM changes
    ) SELECT {quoted(scope['site'])} AS site,{quoted(scope['path'])} AS path,
        {base} AS base_month,{current} AS current_month,*,
        CASE WHEN population_missing_amount_rows=0 THEN 1.0*amount_change/NULLIF(category_current_amount-category_base_amount,0) END AS amount_net_contribution,
        CASE WHEN population_missing_units_rows=0 THEN 1.0*units_change/NULLIF(category_current_units-category_base_units,0) END AS units_net_contribution,
        CASE WHEN population_missing_amount_rows=0 THEN 100.0*amount_change/NULLIF(category_base_amount,0) END AS amount_growth_pp,
        CASE WHEN population_missing_units_rows=0 THEN 100.0*units_change/NULLIF(category_base_units,0) END AS units_growth_pp,
        CASE WHEN population_missing_amount_rows=0 THEN 1.0*ABS(amount_change)/NULLIF(gross_amount_change,0) END AS amount_absolute_contribution,
        CASE WHEN population_missing_units_rows=0 THEN 1.0*ABS(units_change)/NULLIF(gross_units_change,0) END AS units_absolute_contribution,
        COUNT(1) OVER() AS total_rows FROM totals"""


def _sku_trace(scope):
    seed_layer = 'raw' if scope['level'] == 'raw' else 'std'
    base, current = quoted(scope['base_month']+'-01'), quoted(scope['current_month']+'-01')
    return f"""WITH {_seed(scope)}, {_all_rows(scope, 'trace')},
        seed_products AS (SELECT platform,site,product_id,1 AS in_cohort FROM seed_rows
            WHERE NULLIF(TRIM(platform),'') IS NOT NULL AND NULLIF(TRIM(product_id),'') IS NOT NULL
            GROUP BY platform,site,product_id),
        seed_skus AS (SELECT platform,site,sku_id,1 AS in_cohort FROM seed_rows
            WHERE NULLIF(TRIM(platform),'') IS NOT NULL AND {_traceable_sku_sql('sku_id')}
            GROUP BY platform,site,sku_id),
        traced AS (SELECT a.*,COALESCE(p.in_cohort,0) AS matches_seed_product,
            COALESCE(s.in_cohort,0) AS matches_seed_sku,
            CASE WHEN a.source_layer={quoted(seed_layer)} AND a.in_target_path=1
                AND a.month_dt IN ({base},{current}){_product_filter(scope,'a.')} THEN 1 ELSE 0 END AS is_seed_record
            FROM all_rows a LEFT JOIN seed_products p ON a.platform=p.platform AND a.site=p.site AND a.product_id=p.product_id
            LEFT JOIN seed_skus s ON a.platform=s.platform AND a.site=s.site AND a.sku_id=s.sku_id)
        SELECT *,CASE WHEN month_dt={base} THEN 1 ELSE 0 END AS is_base_month,
            CASE WHEN month_dt={current} THEN 1 ELSE 0 END AS is_current_month,
            CASE WHEN month_dt={quoted(shift(scope['current_month'],-1)+'-01')} THEN 1 ELSE 0 END AS is_previous_calendar_month,
            CASE WHEN month_dt={quoted(shift(scope['current_month'],1)+'-01')} THEN 1 ELSE 0 END AS is_next_calendar_month,
            COUNT(1) OVER() AS total_rows FROM traced
        WHERE matches_seed_product=1 OR matches_seed_sku=1 OR is_seed_record=1"""


def _cross_platform(scope):
    return f"""WITH {_seed(scope)}, {_all_rows(scope, 'products')},
        seed_ids AS (SELECT product_id FROM seed_rows WHERE NULLIF(TRIM(product_id),'') IS NOT NULL GROUP BY product_id),
        overlaps AS (SELECT a.source_layer,a.month_dt,a.product_id,COUNT(DISTINCT a.platform) AS platform_count
            FROM all_rows a INNER JOIN seed_ids s ON a.product_id=s.product_id
            WHERE NULLIF(TRIM(a.platform),'') IS NOT NULL
            GROUP BY a.source_layer,a.month_dt,a.product_id HAVING COUNT(DISTINCT a.platform)>1)
        SELECT a.*,o.platform_count,'identity_overlap_requires_verification' AS observation_state,
            COUNT(1) OVER() AS total_rows FROM all_rows a INNER JOIN overlaps o
            ON a.source_layer=o.source_layer AND a.month_dt=o.month_dt AND a.product_id=o.product_id"""


def _source_coverage(scope):
    seed_layer = 'raw' if scope['level'] == 'raw' else 'std'
    calendar = ' UNION ALL '.join('SELECT '+quoted(mm+'-01')+' AS month_dt'
                                for mm in months(shift(scope['base_month'],1), scope['current_month']))
    return f"""WITH {_seed(scope, base_only=True)}, {_all_rows(scope, 'trace')},
        seeds AS (SELECT * FROM all_rows WHERE source_layer={quoted(seed_layer)} AND in_target_path=1
            AND month_dt={quoted(scope['base_month']+'-01')}{_product_filter(scope)}),
        calendar AS ({calendar}),layers AS (SELECT 'raw' AS source_layer UNION ALL SELECT 'std' AS source_layer)
        SELECT s.site,s.platform,s.product_id AS seed_product_id,s.sku_id AS seed_sku_id,
            s.raw_path AS seed_raw_path,s.std_path AS seed_std_path,s.attribute_key AS seed_attribute_key,
            s.source_rows AS seed_source_rows_context,s.amount AS seed_amount_context,s.units AS seed_units_context,
            s.invalid_value_rows AS seed_invalid_value_rows_context,s.missing_amount_rows AS seed_missing_amount_rows_context,
            s.missing_units_rows AS seed_missing_units_rows_context,
            l.source_layer,c.month_dt,d.product_id AS destination_product_id,d.sku_id AS destination_sku_id,
            d.raw_path,d.std_path,d.attribute_key,d.in_target_path,
            {','.join('d.'+a for a in ATTRIBUTES)},
            d.source_rows,d.amount,d.units,d.missing_amount_rows,d.missing_units_rows,d.invalid_value_rows,
            CASE WHEN NULLIF(TRIM(s.platform),'') IS NULL OR NOT {_traceable_sku_sql('s.sku_id')} THEN 'untraceable_seed_identity'
                WHEN d.source_rows IS NULL THEN 'no_observation'
                WHEN d.product_id <=> s.product_id THEN 'same_sku_same_parent' ELSE 'same_sku_other_parent' END AS observation_state,
            COUNT(1) OVER() AS total_rows
        FROM seeds s CROSS JOIN calendar c CROSS JOIN layers l LEFT JOIN all_rows d
            ON NULLIF(TRIM(s.platform),'') IS NOT NULL AND {_traceable_sku_sql('s.sku_id')}
            AND s.site=d.site AND s.platform=d.platform AND s.sku_id=d.sku_id
            AND c.month_dt=d.month_dt AND l.source_layer=d.source_layer"""


def diagnostic_query(manifest, kind, site, level, path, base_month, current_month, product_ids=None):
    """Build one complete diagnostic query, without executing or paginating it.

    ``path`` is a JSON array or a list of exactly ``level`` standard components;
    ``level='raw'`` requires all six original raw components including nulls.
    SKU and cross-platform traces use the full frozen history. ``source_coverage``
    follows each base-month SKU through every natural month up to current_month,
    including explicit no-observation rows and parent changes. A missing SKU key
    is surfaced as untraceable, not substituted by an entire parent product.
    """
    scope = _scope(manifest, kind, site, level, path, base_month, current_month, product_ids)
    builder = {'brands': _brands, 'sku_trace': _sku_trace,
               'cross_platform': _cross_platform, 'source_coverage': _source_coverage}[kind]
    return readonly_sql(builder(scope))


def diagnostic_job(manifest, kind, site, level, path, base_month, current_month, product_ids=None):
    """Return a deterministic job compatible with existing complete pagination.

    Raw/std and out-of-path rows are diagnostic context, not additive target
    category exposure. Coverage seed amounts repeat by destination/month; count
    the unique seed keys, not the returned Cartesian follow-up rows.
    """
    scope = _scope(manifest, kind, site, level, path, base_month, current_month, product_ids)
    sql = diagnostic_query(manifest, kind, site, level, path, base_month, current_month, product_ids)
    keys = ['brand_key'] if kind == 'brands' else list(IDENTITY_KEYS)
    if kind == 'source_coverage':
        keys = ['site', 'platform', 'seed_product_id', 'seed_sku_id', 'seed_raw_path',
                'seed_std_path', 'seed_attribute_key', 'source_layer', 'month_dt',
                'destination_product_id', 'destination_sku_id', 'raw_path', 'std_path', 'attribute_key']
    exposure = ('in_target_path denotes only the exact raw path; no standard category mapping is inferred. Keep raw/std exposure separate.'
                if level == 'raw' else
                'Only source_layer=std and in_target_path=1 supplies standard-path exposure; raw and sibling-path amounts are context.')
    return {'job_id': 'diagnostic-'+business_key(scope), 'family': 'diagnostic', 'kind': kind,
            'site': site, 'level': level, 'path': scope['path'], 'base_month': base_month,
            'current_month': current_month, 'product_ids': scope['product_ids'],
            'cohort_is_shard': scope['product_ids'] is not None,
            'window_start': scope['window_start'], 'window_end': scope['window_end'],
            'next_calendar_month_in_scope': shift(current_month, 1) <= scope['window_end'],
            'observations_do_not_prove_identity_or_causality': True,
            'exposure_scope': exposure,
            'empty_result_does_not_establish_completion': True,
            'seed_context_is_additive': False, 'key_fields': keys, 'sql': sql}
