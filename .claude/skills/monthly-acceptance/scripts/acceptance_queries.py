"""Deterministic read-only query plan. All aggregate cells, never a Top-N candidate scan."""
from acceptance_core import business_key, quoted, shift
from acceptance_protocol import current

KEYS = {
    'fingerprint': ['month_dt'], 'category': ['month_dt','path'],
    'bands': ['month_dt','path','band'], 'movement': ['month_dt','path'],
    'raw_paths': ['month_dt','path'], 'mapping': ['month_dt'],
    'platform': ['month_dt','platform'], 'sample': ['path','product_id'], 'release': ['release_id'],
}


def null_mismatch(left,right):
    return f'(({left} IS NULL AND {right} IS NOT NULL) OR ({left} IS NOT NULL AND {right} IS NULL))'


def path_expr(level):
    return "CAST('[]' AS STRING)" if level == 0 else 'CAST(JSON_ARRAY('+','.join('stdcategory'+str(i) for i in range(1,level+1))+') AS STRING)'


def where(m, site):
    return f"site={quoted(site)} AND month_dt BETWEEN {quoted(m['starts'][site]+'-01')} AND {quoted(m['data_month']+'-01')}"


def platform_identity(m):
    """Frozen 4.0.0/4.0.1 SQL remains reproducible with its original identity."""
    version=tuple(int(x) for x in m.get('method_version','0.0.0').split('.')[:3])
    return current(m) or version >= (4,0,2)


def spu_identity():
    # JSON tuples preserve component boundaries, including delimiter characters
    # and NULL platforms. Missing parents remain unpriced, uncounted source rows.
    return "CASE WHEN NULLIF(TRIM(product_id),'') IS NOT NULL THEN CAST(JSON_ARRAY(site,platform,product_id) AS STRING) END"


def product_cte(m, site, level, entity='product_id'):
    ident=spu_identity() if platform_identity(m) and entity=='product_id' else f"NULLIF(TRIM({entity}),'')"
    platform='platform,' if platform_identity(m) and entity=='product_id' else ''
    return f"""p AS (SELECT month_dt,{path_expr(level)} AS path,{platform}{ident} AS entity,
        SUM(discount_sales) AS amount,SUM(`count`) AS units,COUNT(1) AS nrows,
        SUM(CASE WHEN discount_sales IS NULL OR `count` IS NULL OR discount_sales<0 OR `count`<0 THEN 1 ELSE 0 END) AS invalid_rows
        FROM {m['policy']['std_table']} WHERE {where(m,site)}
        GROUP BY month_dt,path,{platform}entity)"""


def priced(m, site, level):
    return product_cte(m,site,level)+", v AS (SELECT *,CASE WHEN amount>0 AND units>0 AND invalid_rows=0 AND entity IS NOT NULL THEN amount/units END AS price FROM p)"


def band_expr(edges):
    return 'CASE WHEN price IS NULL THEN -1 '+''.join(f'WHEN price<{float(v)} THEN {i} ' for i,v in enumerate(edges))+f'ELSE {len(edges)} END'


def query(m, family, site, level=None, lag=None, entity=None, year=None, phase=None):
    pol=m['policy'];tbl=pol['std_table'];condition=where(m,site) if site in m['starts'] else ''
    spu=spu_identity() if platform_identity(m) else 'product_id'
    if family=='release':
        return 'SELECT release_id,status,built_at,published_at,max_complete_month,scope_version,pipeline_version,source_wide_update_time,upstream_dim_update_time,source_derived_update_time,COUNT(1) OVER() AS total_rows FROM internal.flywheel_gov.gov_release'
    if family=='fingerprint':
        tbl=pol['raw_table'] if entity=='raw' else tbl
        return f"""SELECT month_dt,COUNT(1) AS nrows,COUNT(DISTINCT {spu}) AS spus,
            SUM(`count`) AS units,SUM(discount_sales) AS amount,
            SUM(CASE WHEN product_id IS NULL OR TRIM(product_id)='' OR sku_id IS NULL OR month_dt IS NULL THEN 1 ELSE 0 END) AS invalid_keys,
            SUM(CASE WHEN discount_sales IS NULL OR `count` IS NULL OR discount_sales<0 OR `count`<0 THEN 1 ELSE 0 END) AS invalid_rows,
            COUNT(1) OVER() AS total_rows FROM {tbl} WHERE {condition} GROUP BY month_dt"""
    if family=='platform':
        tbl=pol['raw_table'] if entity=='raw' else tbl
        sku="CASE WHEN sku_id IS NOT NULL AND TRIM(sku_id) NOT IN ('','0') THEN CONCAT_WS('|',product_id,sku_id) END"
        if platform_identity(m):
            sku="CASE WHEN NULLIF(TRIM(product_id),'') IS NOT NULL AND sku_id IS NOT NULL AND TRIM(sku_id) NOT IN ('','0') THEN CAST(JSON_ARRAY(site,platform,product_id,sku_id) AS STRING) END"
        return f"""SELECT month_dt,platform,COUNT(1) AS nrows,COUNT(DISTINCT {spu}) AS spus,
            COUNT(DISTINCT {sku}) AS effective_skus,
            SUM(CASE WHEN sku_id IS NULL OR TRIM(sku_id) IN ('','0') THEN 1 ELSE 0 END) AS placeholder_sku_rows,
            SUM(`count`) AS units,SUM(discount_sales) AS amount,
            SUM(CASE WHEN discount_sales IS NULL OR `count` IS NULL OR discount_sales<0 OR `count`<0 THEN 1 ELSE 0 END) AS invalid_rows,
            COUNT(1) OVER() AS total_rows FROM {tbl} WHERE {condition} GROUP BY month_dt,platform"""
    if family=='category':
        return f"""WITH {priced(m,site,level)} SELECT month_dt,path,COUNT(entity) AS spus,SUM(amount) AS amount,SUM(units) AS units,
            SUM(nrows) AS nrows,SUM(invalid_rows) AS invalid_rows,
            COUNT(price) AS priced_spus,SUM(CASE WHEN entity IS NULL THEN nrows ELSE 0 END) AS missing_product_rows,
            PERCENTILE_APPROX(price,0.1) AS p10,PERCENTILE_APPROX(price,0.5) AS p50,PERCENTILE_APPROX(price,0.9) AS p90,
            COUNT(1) OVER() AS total_rows FROM v GROUP BY month_dt,path"""
    if family=='bands':
        return f"""WITH {priced(m,site,level)} SELECT month_dt,path,{band_expr(pol['price_bands'][site])} AS band,
            COUNT(entity) AS spus,SUM(amount) AS amount,SUM(units) AS units,COUNT(1) OVER() AS total_rows
            FROM v GROUP BY month_dt,path,band"""
    if family=='raw_paths':
        return f"""SELECT month_dt,CAST(JSON_ARRAY(category_1,category_2,category_3,category_4,category_5,sub_category) AS STRING) AS path,
            COUNT(1) AS nrows,COUNT(DISTINCT {spu}) AS spus,SUM(`count`) AS units,SUM(discount_sales) AS amount,
            COUNT(1) OVER() AS total_rows FROM {pol['raw_table']} WHERE {condition} GROUP BY month_dt,path"""
    if family=='movement':
        ident='product_id' if entity=='spu' else 'std_brand_name'
        platform_join=' AND b.platform <=> c.platform' if platform_identity(m) and entity=='spu' else ''
        begin=max(m['starts'][site],f'{year}-01');end=min(m['data_month'],f'{year}-12')
        # Absent sides describe observed entry/exit; they do not establish zero market sales.
        return f"""WITH {product_cte(m,site,level,ident)}, d AS (
            SELECT COALESCE(c.month_dt,CAST(DATE_ADD(CAST(b.month_dt AS DATE),INTERVAL {lag} MONTH) AS STRING)) AS month_dt,
            COALESCE(c.path,b.path) AS path,b.entity AS bentity,c.entity AS centity,
            b.nrows AS bn,c.nrows AS cn,COALESCE(b.amount,0) AS ba,COALESCE(c.amount,0) AS ca,
            COALESCE(b.units,0) AS bu,COALESCE(c.units,0) AS cu
            FROM p b FULL OUTER JOIN p c ON b.path=c.path AND b.entity <=> c.entity{platform_join}
            AND CAST(c.month_dt AS DATE)=DATE_ADD(CAST(b.month_dt AS DATE),INTERVAL {lag} MONTH))
            SELECT month_dt,path,COUNT(bn) AS base_entities,COUNT(cn) AS current_entities,
            SUM(CASE WHEN bn IS NULL THEN 1 ELSE 0 END) AS entered,SUM(CASE WHEN cn IS NULL THEN 1 ELSE 0 END) AS exited,
            SUM(ba) AS base_amount,SUM(ca) AS current_amount,SUM(bu) AS base_units,SUM(cu) AS current_units,
            SUM(GREATEST(ca-ba,0)) AS positive_amount,SUM(LEAST(ca-ba,0)) AS negative_amount,
            SUM(GREATEST(cu-bu,0)) AS positive_units,SUM(LEAST(cu-bu,0)) AS negative_units,
            COUNT(1) OVER() AS total_rows FROM d WHERE month_dt BETWEEN {quoted(begin+'-01')} AND {quoted(end+'-01')}
            GROUP BY month_dt,path"""
    if family=='mapping':
        begin=max(m['starts'][site],f'{year}-01');end=min(m['data_month'],f'{year}-12')
        cond=f"site={quoted(site)} AND month_dt BETWEEN {quoted(begin+'-01')} AND {quoted(end+'-01')}"
        keys='platform,site,month_dt,product_id,sku_id'
        agg=f'''SELECT {keys},COUNT(1) AS n,SUM(discount_sales) AS amount,SUM(`count`) AS units,
            SUM(CASE WHEN discount_sales IS NULL OR `count` IS NULL THEN 1 ELSE 0 END) AS null_values'''
        eq=' AND '.join(f'r.{k} <=> d.{k}' for k in keys.split(','))
        return f"""WITH r AS ({agg} FROM {pol['raw_table']} WHERE {cond} GROUP BY {keys}),
            d AS ({agg} FROM {tbl} WHERE {cond} GROUP BY {keys})
            SELECT COALESCE(r.month_dt,d.month_dt) AS month_dt,
            SUM(CASE WHEN r.n>1 THEN r.n-1 ELSE 0 END) AS raw_duplicate_rows,
            SUM(CASE WHEN d.n>1 THEN d.n-1 ELSE 0 END) AS std_duplicate_rows,
            SUM(CASE WHEN d.n IS NULL THEN 1 ELSE 0 END) AS raw_only_keys,
            SUM(CASE WHEN r.n IS NULL THEN 1 ELSE 0 END) AS std_only_keys,
            SUM(CASE WHEN r.n IS NOT NULL AND d.n IS NOT NULL THEN 1 ELSE 0 END) AS matched_keys,
            SUM(CASE WHEN r.n IS NOT NULL AND d.n IS NOT NULL AND (ABS(r.amount-d.amount)>{pol['amount_tolerance']} OR NOT (r.units <=> d.units) OR {null_mismatch('r.amount','d.amount')} OR r.null_values<>d.null_values) THEN 1 ELSE 0 END) AS value_mismatch_keys,
            COUNT(1) OVER() AS total_rows FROM r FULL OUTER JOIN d ON {eq} GROUP BY COALESCE(r.month_dt,d.month_dt)"""
    if family=='sample':
        n=int(pol['sample_per_category']);path=path_expr(3)
        title="COALESCE(NULLIF(TRIM(product_title),''),NULLIF(TRIM(product_title_cn),''),NULLIF(TRIM(sku_title),''))"
        raw_path='CAST(JSON_ARRAY(category_1,category_2,category_3,category_4,category_5,sub_category) AS STRING)'
        identity_columns='platform,product_id' if platform_identity(m) else 'product_id'
        ordering=f"MD5(CAST(JSON_ARRAY({quoted(pol['sample_seed'])},path,platform,product_id) AS STRING)),platform,product_id" if platform_identity(m) else f"MD5(CONCAT({quoted(pol['sample_seed'])},path,product_id)),product_id"
        # Fixed seed + full category path + entity yields a reproducible stratified sample, independent of anomaly values.
        return f"""WITH s AS (SELECT {path} AS path,{identity_columns},MAX({title}) AS title,MAX(std_brand_name) AS brand,
            MAX({raw_path}) AS raw_category,COUNT(DISTINCT {raw_path}) AS raw_path_count,
            COUNT(DISTINCT {title}) AS title_variants,MAX(product_url) AS product_url,COUNT(1) AS sku_rows,
            SUM(`count`) AS units,SUM(discount_sales) AS amount,
            MIN(discount_price) AS min_sku_price,MAX(discount_price) AS max_sku_price
            FROM {tbl} WHERE site={quoted(site)} AND month_dt={quoted(m['data_month']+'-01')} AND product_id IS NOT NULL
            GROUP BY path,{identity_columns}), ranked AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY path ORDER BY {ordering}) AS sample_rank FROM s)
            SELECT *,COUNT(1) OVER() AS total_rows FROM ranked WHERE sample_rank<={n}"""
    raise ValueError('未知查询族 '+family)


def job(m, family, site, **params):
    args={'family':family,'site':site,**params}
    keys=['path','platform','product_id'] if family=='sample' and platform_identity(m) else KEYS[family]
    return {**args,'job_id':business_key(args),'key_fields':keys, 'sql':query(m,**args)}


def plan(m, end_fingerprints=False):
    jobs=[] if end_fingerprints else [job(m,'release','global')]
    for site in m['sites']:
        for entity in ['raw','std']:
            jobs.append(job(m,'fingerprint',site,entity=entity,phase='end' if end_fingerprints else 'start'))
        if end_fingerprints:continue
        if (current(m) or tuple(int(x) for x in m.get('method_version','0.0').split('.')[:2]) >= (4,0)) and m.get('policy',{}).get('question_workflow',{}).get('enabled'):
            for entity in ('raw','std'):jobs.append(job(m,'platform',site,entity=entity))
        years=range(int(m['starts'][site][:4]),int(m['data_month'][:4])+1)
        jobs.append(job(m,'raw_paths',site))
        for year in years:jobs.append(job(m,'mapping',site,year=year))
        for level in m['policy']['levels']:
            jobs += [job(m,'category',site,level=level),job(m,'bands',site,level=level)]
            for lag in m['policy']['comparisons'].values():
                for year in years:
                    for entity in ['spu','brand']:
                        jobs.append(job(m,'movement',site,level=level,lag=lag,entity=entity,year=year))
        jobs.append(job(m,'sample',site))
    return jobs


def paginated(j, page_size, offset):
    return j['sql']+' ORDER BY '+','.join('`'+k+'`' for k in j['key_fields'])+f' LIMIT {page_size} OFFSET {offset}'


def drilldown(m, site, path, level, current, lag):
    """Full entity delta population for one cell; head/tail selection happens on complete local rows."""
    pth=path_expr(level);base=shift(current,-lag)
    return f"""WITH p AS (SELECT month_dt,product_id,MAX(product_title) AS title,MAX(std_brand_name) AS brand,
        MAX(sub_category) AS raw_category,MAX(product_url) AS product_url,COUNT(1) AS nrows,
        SUM(CASE WHEN discount_sales IS NULL OR `count` IS NULL OR discount_sales<0 OR `count`<0 THEN 1 ELSE 0 END) AS invalid_rows,
        SUM(`count`) AS units,SUM(discount_sales) AS amount,MIN(discount_price) AS min_price,MAX(discount_price) AS max_price
        FROM {m['policy']['std_table']} WHERE site={quoted(site)} AND {pth}={quoted(path)}
        AND month_dt IN ({quoted(base+'-01')},{quoted(current+'-01')}) GROUP BY month_dt,product_id),
        b AS (SELECT * FROM p WHERE month_dt={quoted(base+'-01')}),c AS (SELECT * FROM p WHERE month_dt={quoted(current+'-01')})
        SELECT COALESCE(b.product_id,c.product_id) AS product_id,COALESCE(c.title,b.title) AS title,COALESCE(c.brand,b.brand) AS brand,
        b.raw_category AS base_category,c.raw_category AS current_category,COALESCE(c.product_url,b.product_url) AS product_url,
        b.nrows AS base_rows,c.nrows AS current_rows,b.units AS base_units,c.units AS current_units,b.amount AS base_amount,c.amount AS current_amount,
        b.invalid_rows AS base_invalid_rows,c.invalid_rows AS current_invalid_rows,
        b.min_price AS base_min_price,b.max_price AS base_max_price,c.min_price AS current_min_price,c.max_price AS current_max_price,
        COUNT(1) OVER() AS total_rows FROM b FULL OUTER JOIN c ON b.product_id <=> c.product_id"""


def question_jobs(m,unit,mode='aggregate'):
    """Typed directed aggregates; natural-month shards do not split obligations.

    Raw under a standard subtree is the source population selected by exact
    platform/parent/SKU keys of that subtree, using EXISTS to avoid join fanout.
    Placeholder SKU is never used without its own platform and parent.
    Cohorts retain country/platform/parent identity and return complete grouped
    presence patterns, not Top N records or an all-SKU explanation queue.

    Lineage is deliberately a different population: an explicitly anchored
    standard subtree selects parents and their complete anchor-month children,
    then those keys are observed at every returned path. It supplements identity
    review and cannot replace the ordinary subtree reconciliation denominator.
    """
    from acceptance_core import canonical,digest,readonly_sql
    if mode=='lineage':return _question_lineage_jobs(m,unit)
    if mode not in ('aggregate','cohorts','details'):raise ValueError('unknown question query mode')
    site=unit['site'];period=unit.get('query_months',unit['event_months']+unit['background_months'])
    period=sorted(set(period));platforms=unit['platforms'];paths=unit['paths'];path_layer=unit.get('path_layer','std')
    if site not in m['sites'] or not period or any(not m['starts'][site]<=mm<=m['data_month'] for mm in period):raise ValueError('query scope outside frozen window')
    if not platforms or not set(platforms)<=set(m['policy'].get('expected_platforms',{}).get(site,[])):raise ValueError('platform outside frozen contract')
    def predicate(alias=''):
        prefix=alias+'.' if alias else ''
        cols=['stdcategory1','stdcategory2','stdcategory3'] if path_layer=='std' else ['category_1','category_2','category_3','category_4','category_5','sub_category']
        if any(len(p)>len(cols) for p in paths):raise ValueError('path scope exceeds layer')
        return '('+' OR '.join('('+' AND '.join(prefix+cols[i]+' <=> '+('NULL' if v is None else quoted(v)) for i,v in enumerate(p))+')' if p else '(1=1)' for p in paths)+')'
    def condition(layer,selected_months):
        outer='q';cond=f"q.site={quoted(site)} AND q.platform IN ({','.join(quoted(x) for x in platforms)}) AND q.month_dt IN ({','.join(quoted(mm+'-01') for mm in selected_months)})"
        if layer=='std' or path_layer=='raw':return cond+' AND '+predicate(outer)
        if paths==[[]]:return cond
        eq=' AND '.join('s.'+k+' <=> q.'+k for k in ('site','platform','month_dt','product_id','sku_id'))
        return cond+f" AND EXISTS (SELECT 1 FROM {m['policy']['std_table']} s WHERE {eq} AND {predicate('s')})"
    result=[]
    for layer in unit['source_layers']:
        tbl=m['policy']['raw_table' if layer=='raw' else 'std_table']
        windows=[[mm] for mm in period] if mode in ('aggregate','details') else [period]
        for window in windows:
            cond=condition(layer,window)
            if mode=='aggregate':
                sql=f"""WITH p AS (SELECT q.site,q.platform,q.month_dt,q.product_id,COUNT(1) AS nrows,
                  COUNT(DISTINCT CASE WHEN q.sku_id IS NOT NULL AND TRIM(q.sku_id) NOT IN ('','0') THEN q.sku_id END) AS effective_skus,
                  SUM(CASE WHEN q.sku_id IS NULL OR TRIM(q.sku_id) IN ('','0') THEN 1 ELSE 0 END) AS placeholder_rows,
                  SUM(CASE WHEN q.`count` IS NULL OR q.discount_sales IS NULL OR q.`count`<0 OR q.discount_sales<0 THEN 1 ELSE 0 END) AS invalid_rows,
                  SUM(q.`count`) AS units,SUM(q.discount_sales) AS amount
                  FROM {tbl} q WHERE {cond} GROUP BY q.site,q.platform,q.month_dt,q.product_id)
                  SELECT site,platform,month_dt,COUNT(product_id) AS spus,SUM(nrows) AS nrows,SUM(effective_skus) AS effective_skus,
                  SUM(placeholder_rows) AS placeholder_rows,SUM(invalid_rows) AS invalid_rows,
                  SUM(units) AS units,SUM(amount) AS amount,MAX(nrows) AS max_rows_per_spu,
                  PERCENTILE_APPROX(nrows,0.5) AS p50_rows_per_spu,PERCENTILE_APPROX(nrows,0.9) AS p90_rows_per_spu,
                  COUNT(1) OVER() AS total_rows FROM p GROUP BY site,platform,month_dt"""
                keys=['site','platform','month_dt']
            elif mode=='details':
                path="CAST(JSON_ARRAY("+','.join('q.'+x for x in (['category_1','category_2','category_3','category_4','category_5','sub_category'] if layer=='raw' else ['stdcategory1','stdcategory2','stdcategory3']))+") AS STRING)"
                sql=f"""SELECT q.site,q.platform,q.month_dt,q.product_id,q.sku_id,{path} AS path,
                  COUNT(1) AS source_rows,COUNT(DISTINCT q.product_title) AS title_variants,MAX(q.product_title) AS title,
                  SUM(q.`count`) AS units,SUM(q.discount_sales) AS amount,MIN(q.discount_price) AS min_price,MAX(q.discount_price) AS max_price,
                  COUNT(1) OVER() AS total_rows FROM {tbl} q WHERE {cond}
                  GROUP BY q.site,q.platform,q.month_dt,q.product_id,q.sku_id,path"""
                keys=['site','platform','month_dt','product_id','sku_id','path']
            else:
                flags=[f"MAX(CASE WHEN month_dt={quoted(mm+'-01')} THEN 1 ELSE 0 END) AS m{i}" for i,mm in enumerate(window)]
                values=[f"SUM(CASE WHEN month_dt={quoted(mm+'-01')} THEN {field} ELSE 0 END) AS {field}_{i}" for i,mm in enumerate(window) for field in ('units','amount','nrows')]
                pattern="CONCAT("+','.join(f'CAST(m{i} AS STRING)' for i in range(len(window)))+")"
                sums=','.join(f'SUM({field}_{i}) AS {field}_{i}' for i in range(len(window)) for field in ('units','amount','nrows'))
                sql=f"""WITH p AS (SELECT q.site,q.platform,q.month_dt,q.product_id,COUNT(1) AS nrows,
                  SUM(q.`count`) AS units,SUM(q.discount_sales) AS amount FROM {tbl} q WHERE {cond}
                  GROUP BY q.site,q.platform,q.month_dt,q.product_id), x AS (
                  SELECT site,platform,product_id,{','.join(flags+values)} FROM p GROUP BY site,platform,product_id)
                  SELECT site,platform,{pattern} AS presence_pattern,COUNT(product_id) AS spus,{sums},
                  COUNT(1) OVER() AS total_rows FROM x GROUP BY site,platform,presence_pattern"""
                keys=['site','platform','presence_pattern']
            spec={'site':site,'platforms':platforms,'source_layers':[layer],'paths':paths,'path_layer':path_layer,
                  'event_months':window,'background_months':[]}
            args={'family':'question_'+mode,'site':site,'layer':layer,'mode':mode,'months':window,'scope_spec':spec,
                  'key_fields':keys,'sql':readonly_sql(sql)}
            result.append({**args,'job_id':'question-'+digest([mode,site,layer,window,digest(sql)])[:24]})
    return result


def _question_lineage_jobs(m,unit):
    """Trace a fixed anchor definition without chaining discovered identities.

    The three membership relations are unique on their entire join keys. A
    source row therefore joins at most once to each relation, even when the
    anchor contains duplicate mappings or one SKU appears under many parents.
    Complete parent context includes new child keys; only a matching original
    parent/SKU key earns ``matches_anchor_original_key``. A placeholder cannot
    connect different parents. A usable SKU match is an observation of the
    source identifier, not proof that two records describe the same product.
    """
    from acceptance_core import canonical,digest,month,readonly_sql
    import re

    site=unit.get('site')
    if site not in m.get('sites',[]) or site not in m.get('starts',{}):
        raise ValueError('lineage site outside frozen contract')
    if unit.get('selection_mode','anchored_identity')!='anchored_identity':
        raise ValueError('lineage requires anchored_identity selection')
    if unit.get('anchor_path_layer','std')!='std':
        raise ValueError('lineage anchor must be an explicit standard subtree')

    def checked_months(values,name):
        if not isinstance(values,list) or not values or any(not isinstance(x,str) for x in values):
            raise ValueError(name+' must be an explicit nonempty month list')
        result=sorted(set(month(x) for x in values))
        if any(not m['starts'][site]<=x<=m['data_month'] for x in result):
            raise ValueError(name+' outside frozen window')
        return result

    def checked_paths(values,layer,name):
        if layer not in ('raw','std') or not isinstance(values,list) or not values:
            raise ValueError(name+' must be an explicit nonempty path list')
        if any(not isinstance(p,list) or len(p)>(3 if layer=='std' else 6)
               or any(v is not None and (not isinstance(v,str) or '\x00' in v) for v in p)
               for p in values):
            raise ValueError(name+' contains an invalid path')
        # Sorting gives an equivalent explicitly selected set one stable job ID.
        return [list(p) for p in sorted({canonical(p):p for p in values}.values(),key=canonical)]

    period=checked_months(unit.get('query_months',unit.get('event_months',[])+unit.get('background_months',[])),'query_months')
    anchors=checked_months(unit.get('anchor_months'),'anchor_months')
    anchor_paths=checked_paths(unit.get('anchor_paths'),'std','anchor_paths')
    path_layer=unit.get('path_layer','std')
    paths=checked_paths(unit.get('paths'),path_layer,'paths')
    platforms=unit.get('platforms')
    if (not isinstance(platforms,list) or not platforms
            or any(not isinstance(x,str) or not x.strip() for x in platforms)
            or not set(platforms)<=set(m['policy'].get('expected_platforms',{}).get(site,[]))):
        raise ValueError('lineage platform outside frozen contract')
    platforms=sorted(set(platforms))
    layers=unit.get('source_layers')
    if not isinstance(layers,list) or not layers or any(x not in ('raw','std') for x in layers):
        raise ValueError('lineage source_layers must contain raw and/or std')
    layers=sorted(set(layers))
    tables={key:m['policy'][key+'_table'] for key in ('raw','std')}
    if any(not isinstance(value,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*',value) for value in tables.values()):
        raise ValueError('lineage requires frozen three-part table identifiers')

    def membership(field,values):
        # A natural-month shard (and a single-platform site) has one exact
        # value, so it needs no set-membership operation in the native plan.
        return field+'='+quoted(values[0]) if len(values)==1 else field+' IN ('+','.join(map(quoted,values))+')'

    def base(alias,dates):
        return (f"{alias}.site={quoted(site)} AND "+membership(alias+'.platform',platforms)
                +' AND '+membership(alias+'.month_dt',[x+'-01' for x in dates]))

    def anchor_predicate(alias):
        return '('+' OR '.join('('+' AND '.join(f'{alias}.stdcategory{i+1} <=> '+('NULL' if v is None else quoted(v))
                                                for i,v in enumerate(p))+')' if p else '(1=1)' for p in anchor_paths)+')'

    def valid_parent(field):return f"NULLIF(TRIM({field}),'') IS NOT NULL"
    def valid_sku(field):return f"(NULLIF(TRIM({field}),'') IS NOT NULL AND TRIM({field})<>'0')"
    def equality(left,right,keys):return ' AND '.join(f'{left}.{key} <=> {right}.{key}' for key in keys)
    identity=['site','platform','product_id','sku_id']
    parent_keys=['site','platform','product_id'];sku_keys=['site','platform','sku_id']
    anchor_family=[]
    for layer in ('raw','std'):
        parent_member=f"EXISTS (SELECT 1 FROM anchor_parents p WHERE {equality('a','p',parent_keys)})"
        # Every valid-parent path seed is already in its complete parent
        # population. Keep invalid-parent path seeds in this same std branch,
        # rather than rereading anchor_path_keys through a third UNION branch.
        selector=f'({parent_member} OR {anchor_predicate("a")})' if layer=='std' else parent_member
        anchor_family.append(f"SELECT {','.join('a.'+k for k in identity)} FROM {tables[layer]} a "
                             f"WHERE {base('a',anchors)} AND {selector}")
    seed_sql=f"""anchor_path_keys AS (
        SELECT DISTINCT {','.join('s.'+k for k in identity)} FROM {tables['std']} s
        WHERE {base('s',anchors)} AND {anchor_predicate('s')}),
        anchor_parents AS (SELECT DISTINCT {','.join(parent_keys)},1 AS member FROM anchor_path_keys
            WHERE {valid_parent('product_id')}),
        anchor_members AS ({' UNION '.join(anchor_family)}),
        anchor_skus AS (SELECT DISTINCT {','.join(sku_keys)},1 AS member FROM anchor_members WHERE {valid_sku('sku_id')}),
        anchor_original_keys AS (SELECT DISTINCT {','.join(identity)},1 AS member FROM anchor_members WHERE {valid_parent('product_id')})"""

    # These source fields are already used for both raw and std by the existing
    # acceptance_diagnostics trace. Keep their actual variants, rather than MAX
    # titles that can combine unrelated child packages into one description.
    attrs=['product_title','product_title_cn','sku_title','std_brand_name','product_url',
           'discount_price','page_price','page_sales','product_listing_time']
    raw_fields=['category_1','category_2','category_3','category_4','category_5','sub_category']
    raw_path='CAST(JSON_ARRAY('+','.join('q.'+x for x in raw_fields)+') AS STRING)'
    full_std_path='CAST(JSON_ARRAY(q.stdcategory1,q.stdcategory2,q.stdcategory3) AS STRING)'
    title="COALESCE(NULLIF(TRIM(q.product_title),''),NULLIF(TRIM(q.product_title_cn),''),NULLIF(TRIM(q.sku_title),''))"
    title_source="CASE WHEN NULLIF(TRIM(q.product_title),'') IS NOT NULL THEN 'product_title' WHEN NULLIF(TRIM(q.product_title_cn),'') IS NOT NULL THEN 'product_title_cn' WHEN NULLIF(TRIM(q.sku_title),'') IS NOT NULL THEN 'sku_title' END"
    flags=['matches_anchor_parent','matches_anchor_sku','matches_anchor_original_key','is_anchor_path_record','in_anchor_std_path']
    keys=['source_layer','site','platform','month_dt','product_id','sku_id','raw_path','std_path','attribute_key']
    group_fields=['source_layer','site','platform','month_dt','product_id','sku_id','raw_path','std_path','path',
                  *attrs,'attribute_key','title','title_source',*flags]
    anchor_scope={'site':site,'platforms':platforms,'source_layer':'std','path_layer':'std','paths':anchor_paths,
                  'months':anchors,'member_source_layers':['raw','std'],'membership_expansion':'complete_parent_children_in_anchor_months'}
    jobs=[]
    for layer in layers:
        std_path=full_std_path if layer=='std' else 'CAST(NULL AS STRING)'
        in_anchor=f"CASE WHEN {anchor_predicate('q')} THEN 1 ELSE 0 END" if layer=='std' else 'CAST(NULL AS INT)'
        for mm in period:
            # The outer WHERE already fixes this exact natural month. Folding
            # its anchor-date test removes repeated membership subexpressions
            # without changing which seed records survive invalid identities.
            seed_record=in_anchor if layer=='std' and mm in anchors else '0'
            sql=f"""WITH {seed_sql}, selected AS (
                SELECT {quoted(layer)} AS source_layer,q.site,q.platform,q.month_dt,q.product_id,q.sku_id,
                {raw_path} AS raw_path,{std_path} AS std_path,{raw_path if layer=='raw' else full_std_path} AS path,
                {','.join('q.'+x for x in attrs)},CAST(JSON_ARRAY({','.join('q.'+x for x in attrs)}) AS STRING) AS attribute_key,
                {title} AS title,{title_source} AS title_source,
                COALESCE(p.member,0) AS matches_anchor_parent,COALESCE(s.member,0) AS matches_anchor_sku,
                COALESCE(k.member,0) AS matches_anchor_original_key,{seed_record} AS is_anchor_path_record,
                {in_anchor} AS in_anchor_std_path,q.`count` AS source_units,q.discount_sales AS source_amount
                FROM {tables[layer]} q
                LEFT JOIN anchor_parents p ON {equality('q','p',parent_keys)}
                LEFT JOIN anchor_skus s ON {equality('q','s',sku_keys)}
                LEFT JOIN anchor_original_keys k ON {equality('q','k',identity)}
                WHERE {base('q',[mm])} AND (p.member=1 OR s.member=1 OR ({seed_record})=1))
                SELECT {','.join(group_fields)},COUNT(1) AS source_rows,SUM(source_units) AS units,SUM(source_amount) AS amount,
                SUM(CASE WHEN source_units IS NULL THEN 1 ELSE 0 END) AS missing_units_rows,
                SUM(CASE WHEN source_amount IS NULL THEN 1 ELSE 0 END) AS missing_amount_rows,
                SUM(CASE WHEN source_units IS NULL OR source_amount IS NULL OR source_units<0 OR source_amount<0 THEN 1 ELSE 0 END) AS invalid_value_rows,
                COUNT(1) OVER() AS total_rows FROM selected GROUP BY {','.join(group_fields)}"""
            spec={'site':site,'platforms':platforms,'source_layers':[layer],'paths':paths,'path_layer':path_layer,
                  'event_months':[mm],'background_months':[],'anchor_months':anchors,'anchor_paths':anchor_paths,
                  'anchor_path_layer':'std','selection_mode':'anchored_identity'}
            returned={'site':site,'platforms':platforms,'source_layers':[layer],'months':[mm],'path_layer':layer,'paths':[[]],
                      'selection_mode':'anchored_identity','coverage_role':'supplemental_identity',
                      'parent_context':'all_children_of_original_anchor_parents',
                      'sku_identity':'exact_valid_sku_within_same_platform_only',
                      'placeholder_identity':'same_valid_parent_and_original_sku_key_only',
                      'transitive_expansion':False}
            args={'family':'question_lineage','site':site,'layer':layer,'mode':'lineage','months':[mm],
                  'scope_spec':spec,'anchor_scope':anchor_scope,'return_scope':returned,'key_fields':keys,'sql':readonly_sql(sql)}
            jobs.append({**args,'job_id':'question-'+digest(['lineage',site,layer,[mm],digest(sql)])[:24]})
    return jobs


def verify_question_job(m,j):
    expected=question_jobs(m,j['scope_spec'],j['mode'])
    if j not in expected:raise ValueError('定向查询与冻结范围/程序 SQL 不一致')
    return True
