"""Execute diagnostic SQL against small adversarial observation populations.

SQLite supplies offline relational execution, with only table qualification,
STRING casts and the null-safe equality token adapted from the production dialect.
Fixtures exercise the old investigation failures; they are not live data verdicts.
"""
import copy
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from acceptance_core import canonical, readonly_sql
from acceptance_diagnostics import diagnostic_job, diagnostic_query
from acceptance_queries import paginated


TARGET = ['音频', '麦克风', '无线麦克风']
OTHER = ['配件', '连接配件', '转接头']
RAW = ['Electronics', 'Photo', None, None, None, 'Microphones']


@pytest.fixture
def manifest():
    policy = json.loads((Path(__file__).resolve().parents[1] / 'policies/default.json').read_text())
    # Legacy proof/placeholder suites use this explicit frozen v3.1 policy.
    # New gate tests opt in to v3.2 instead of weakening the production defaults.
    policy.update(version='3.1.0', review_gate={'enabled':False}, case_workflow={'enabled':False}, coverage_gate={'enabled':False})
    policy['investigation_workflow'].update(max_unexplained_ratio=0, max_unexplained_band_pp=0)
    return {'sites': ['DE', 'US', 'cn'], 'starts': {s: '2024-01' for s in ['DE', 'US', 'cn']},
            'data_month': '2024-07', 'policy': policy}


@pytest.fixture
def db():
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    columns = {
        'platform': 'TEXT', 'site': 'TEXT', 'month_dt': 'TEXT', 'product_id': 'TEXT',
        'sku_id': 'TEXT', 'product_title': 'TEXT', 'product_title_cn': 'TEXT',
        'sku_title': 'TEXT', 'std_brand_name': 'TEXT', 'product_url': 'TEXT',
        'discount_price': 'REAL', 'page_price': 'TEXT', 'page_sales': 'TEXT',
        'product_listing_time': 'TEXT', 'discount_sales': 'REAL', 'count': 'REAL',
        **{f'category_{i}': 'TEXT' for i in range(1, 6)}, 'sub_category': 'TEXT',
        **{f'stdcategory{i}': 'TEXT' for i in range(1, 4)},
    }
    for table in ['raw', 'std']:
        connection.execute('CREATE TABLE '+table+' ('+','.join('`'+k+'` '+v for k, v in columns.items())+')')
    yield connection
    connection.close()


def observation(**changes):
    row = {'platform': 'Taobao', 'site': 'cn', 'month_dt': '2024-06-01',
           'product_id': 'p1', 'sku_id': 's1', 'product_title': 'Wireless microphone',
           'product_title_cn': '无线麦克风', 'sku_title': 'Single pack',
           'std_brand_name': 'Example', 'product_url': 'https://example.invalid/p1',
           'discount_price': 100, 'page_price': '100', 'page_sales': '1 sold',
           'product_listing_time': '2024-01-01', 'discount_sales': 100, 'count': 1,
           **dict(zip([f'category_{i}' for i in range(1, 6)]+['sub_category'], RAW)),
           **dict(zip([f'stdcategory{i}' for i in range(1, 4)], TARGET))}
    row.update(changes)
    return row


def add(db, row, layers=('raw', 'std')):
    for layer in layers:
        db.execute('INSERT INTO '+layer+' ('+','.join('`'+k+'`' for k in row)+') VALUES ('
                   +','.join('?' for _ in row)+')', list(row.values()))


def run_sql(db, manifest, sql):
    adapted = sql.replace(manifest['policy']['raw_table'], 'raw').replace(manifest['policy']['std_table'], 'std')
    adapted = re.sub(r'\bAS STRING\b', 'AS TEXT', adapted).replace('<=>', 'IS')
    return [dict(row) for row in db.execute(adapted)]


def query(db, manifest, kind, **kwargs):
    return run_sql(db, manifest, diagnostic_query(manifest, kind, kwargs.pop('site', 'cn'),
                   kwargs.pop('level', 3), kwargs.pop('path', TARGET),
                   kwargs.pop('base_month', '2024-05'), kwargs.pop('current_month', '2024-06'), **kwargs))


def test_named_brands_preserve_both_signs_and_the_entire_parent_denominator(db, manifest):
    populations = [
        ('GODOX', '2024-05-01', 1000, 100), ('GODOX', '2024-06-01', 400, 40),
        ('ULANZI', '2024-05-01', 100, 10), ('ULANZI', '2024-06-01', 300, 30),
        ('FALCAM', '2024-06-01', 100, 10), (None, '2024-05-01', 50, 5),
    ]
    for i, (brand, mm, amount, units) in enumerate(populations):
        add(db, observation(std_brand_name=brand, month_dt=mm, discount_sales=amount,
                            count=units, product_id='p'+str(i)))
    add(db, observation(std_brand_name='GODOX', discount_sales=999999, stdcategory3='其他麦克风'))
    add(db, observation(site='US', discount_sales=999999))
    result = query(db, manifest, 'brands')
    assert len(result) == 4
    godox = next(r for r in result if r['brand'] == 'GODOX')
    assert (godox['amount_change'], godox['units_change']) == (-600, -60)
    assert (godox['category_base_amount'], godox['category_current_amount']) == (1150, 800)
    assert (godox['positive_amount_change'], godox['negative_amount_change']) == (300, -650)
    assert (godox['positive_units_change'], godox['negative_units_change']) == (30, -65)
    assert godox['amount_net_contribution'] == pytest.approx(600/350)
    assert sum(r['amount_change'] for r in result) == -350
    assert sum(r['units_change'] for r in result) == -35
    assert next(r for r in result if r['brand'] == 'FALCAM')['base_rows'] == 0
    assert next(r for r in result if r['brand'] is None)['current_rows'] == 0
    assert all(r['total_rows'] == 4 for r in result)


def test_unknown_numeric_values_do_not_become_known_zero_or_contribution(db, manifest):
    add(db, observation(month_dt='2024-05-01', discount_sales=None, count=None))
    add(db, observation(discount_sales=50, count=1))
    row = query(db, manifest, 'brands')[0]
    assert row['base_rows'] == 1 and row['base_amount'] is None and row['base_units'] is None
    assert row['amount_change'] is None and row['units_change'] is None
    assert row['amount_net_contribution'] is None and row['amount_growth_pp'] is None
    assert row['base_missing_amount_rows'] == row['base_missing_units_rows'] == 1


def test_net_zero_cancellation_keeps_positive_and_negative_components(db, manifest):
    for brand, mm, value in [('up', '2024-05-01', 1), ('up', '2024-06-01', 101),
                             ('down', '2024-05-01', 101), ('down', '2024-06-01', 1)]:
        add(db, observation(std_brand_name=brand, month_dt=mm, discount_sales=value, count=value))
    rows = query(db, manifest, 'brands')
    assert len(rows) == 2
    assert all(r['amount_net_contribution'] is None and r['gross_amount_change'] == 200 for r in rows)
    assert all(r['amount_absolute_contribution'] == .5 for r in rows)


def test_complete_named_population_survives_small_pages(db, manifest):
    for i in range(27):
        add(db, observation(std_brand_name='brand-'+str(i), product_id='product-'+str(i)))
    job = diagnostic_job(manifest, 'brands', 'cn', 3, TARGET, '2024-05', '2024-06')
    rows = []
    for offset in range(0, 27, 4):
        rows += run_sql(db, manifest, paginated(job, 4, offset))
    assert len(rows) == 27 and len({r['brand'] for r in rows}) == 27
    assert all(r['total_rows'] == 27 for r in rows)
    assert 'LIMIT' not in job['sql'].upper()


def test_sku_trace_sees_exact_historical_prices_and_all_six_raw_path_fields(db, manifest):
    # The historical 999-price failure requires the same SKUs in natural months,
    # not a change in a parent-level mean or a current-month random sample.
    for mm, price in [('2024-05-01', 100), ('2024-06-01', 999), ('2024-07-01', 100)]:
        for index in range(16):
            add(db, observation(product_id='658899257034', sku_id='variant-'+str(index),
                                month_dt=mm, discount_price=price, page_price=str(price),
                                discount_sales=price, category_4='fourth', category_5='fifth'))
    rows = query(db, manifest, 'sku_trace', product_ids=['658899257034'])
    assert len(rows) == 16*3*2
    for layer in ['raw', 'std']:
        for mm, price in [('2024-05-01', 100), ('2024-06-01', 999), ('2024-07-01', 100)]:
            subset = [r for r in rows if r['source_layer'] == layer and r['month_dt'] == mm]
            assert len(subset) == 16 and {r['discount_price'] for r in subset} == {price}
            assert {json.loads(r['raw_path'])[3] for r in subset} == {'fourth'}
            assert {json.loads(r['raw_path'])[4] for r in subset} == {'fifth'}
    assert all(r['is_next_calendar_month'] == (r['month_dt'] == '2024-07-01') for r in rows)


def test_trace_keeps_sibling_categories_out_of_target_exposure_and_finds_new_parent(db, manifest):
    add(db, observation(month_dt='2024-05-01', product_id='old-parent', sku_id='moving', discount_sales=10))
    add(db, observation(product_id='new-parent', sku_id='moving', discount_sales=20,
                        stdcategory1=OTHER[0], stdcategory2=OTHER[1], stdcategory3=OTHER[2], sub_category='Adapters'))
    add(db, observation(product_id='old-parent', sku_id='sibling', discount_sales=9999,
                        stdcategory1=OTHER[0], stdcategory2=OTHER[1], stdcategory3=OTHER[2]))
    add(db, observation(platform='Tmall', product_id='elsewhere', sku_id='moving', discount_sales=8888,
                        stdcategory3='Other platform category'))
    add(db, observation(site='US', product_id='old-parent', sku_id='moving', discount_sales=7777))
    add(db, observation(month_dt='2024-08-01', product_id='old-parent', discount_sales=6666))
    rows = query(db, manifest, 'sku_trace')
    assert any(r['product_id'] == 'new-parent' and r['matches_seed_sku'] == 1 for r in rows)
    assert any(r['sku_id'] == 'sibling' and r['in_target_path'] == 0 for r in rows if r['source_layer'] == 'std')
    assert sum(r['amount'] for r in rows if r['source_layer'] == 'std' and r['in_target_path'] == 1) == 10
    assert not any(r['amount'] in (8888, 7777, 6666) for r in rows)
    assert all(r['in_target_path'] is None for r in rows if r['source_layer'] == 'raw')


def test_trace_keeps_package_variants_and_duplicate_counts_without_max_title(db, manifest):
    single = observation(sku_title='One piece', discount_sales=100)
    add(db, single)
    add(db, single)
    add(db, observation(sku_title='Ten pieces', discount_price=700, discount_sales=700))
    add(db, observation(stdcategory1=OTHER[0], stdcategory2=OTHER[1], stdcategory3=OTHER[2], discount_sales=50))
    job = diagnostic_job(manifest, 'sku_trace', 'cn', 3, TARGET, '2024-05', '2024-06')
    rows = run_sql(db, manifest, job['sql'])
    single_rows = [r for r in rows if r['sku_title'] == 'One piece']
    assert len(single_rows) == 2 and all(r['source_rows'] == 2 and r['amount'] == 200 for r in single_rows)
    assert any(r['sku_title'] == 'Ten pieces' and r['amount'] == 700 for r in rows)
    identities = [canonical([r[k] for k in job['key_fields']]) for r in rows]
    assert len(identities) == len(set(identities))
    assert 'MAX(' not in job['sql'].upper()


def test_trace_surfaces_invalid_seed_identity_instead_of_dropping_or_broad_matching(db, manifest):
    add(db, observation(platform=None, product_id=None, sku_id=None))
    add(db, observation(month_dt='2024-07-01', platform=None, product_id=None, sku_id=None, discount_sales=777))
    rows = query(db, manifest, 'sku_trace')
    assert len(rows) == 1 and rows[0]['source_layer'] == 'std' and rows[0]['is_seed_record'] == 1
    assert rows[0]['matches_seed_product'] == rows[0]['matches_seed_sku'] == 0


def test_source_coverage_retains_missing_natural_months_and_sku_reparenting(db, manifest):
    add(db, observation(month_dt='2024-04-01', product_id='old-parent', sku_id='retained-child'))
    add(db, observation(month_dt='2024-06-01', product_id='new-parent', sku_id='retained-child',
                        stdcategory3='其他麦克风', sub_category='Other', discount_sales=123))
    rows = query(db, manifest, 'source_coverage', base_month='2024-04', current_month='2024-07')
    assert len(rows) == 6
    assert {r['month_dt'] for r in rows} == {'2024-05-01', '2024-06-01', '2024-07-01'}
    for row in rows:
        if row['month_dt'] == '2024-06-01':
            assert row['observation_state'] == 'same_sku_other_parent'
            assert row['destination_product_id'] == 'new-parent' and row['amount'] == 123
        else:
            assert row['observation_state'] == 'no_observation' and row['amount'] is None
    assert next(r for r in rows if r['month_dt'] == '2024-06-01' and r['source_layer'] == 'std')['in_target_path'] == 0


def test_source_coverage_does_not_join_missing_sku_to_unrelated_missing_sku(db, manifest):
    add(db, observation(month_dt='2024-05-01', sku_id=None))
    add(db, observation(sku_id=None, product_id='unrelated', discount_sales=9999))
    rows = query(db, manifest, 'source_coverage')
    assert len(rows) == 2
    assert all(r['observation_state'] == 'untraceable_seed_identity' and r['destination_product_id'] is None for r in rows)


def test_source_coverage_keys_keep_multiple_parents_paths_and_title_variants(db, manifest):
    add(db, observation(month_dt='2024-05-01', product_id='old-parent'))
    add(db, observation(product_id='new-parent', stdcategory3='Other'))
    add(db, observation(product_id='new-parent', stdcategory3='Other2'))
    add(db, observation(product_id='second-parent', sku_title='Different selling package'))
    job = diagnostic_job(manifest, 'source_coverage', 'cn', 3, TARGET, '2024-05', '2024-06')
    rows = run_sql(db, manifest, job['sql'])
    keys = [canonical([r[k] for k in job['key_fields']]) for r in rows]
    assert len(keys) == len(set(keys))
    assert len([r for r in rows if r['source_layer'] == 'std']) == 3
    assert job['seed_context_is_additive'] is False


def test_raw_six_component_scope_follows_cn_flattened_path_without_inventing_mapping(db, manifest):
    flat = [None, None, None, None, None, '相机滤镜']
    base = observation(month_dt='2024-05-01', **dict(zip([f'category_{i}' for i in range(1, 6)]+['sub_category'], flat)))
    add(db, base)
    add(db, observation(category_1='相机滤镜', sub_category=None))
    rows = query(db, manifest, 'source_coverage', level='raw', path=flat)
    assert len(rows) == 2 and all(r['observation_state'] == 'same_sku_same_parent' for r in rows)
    assert all(r['in_target_path'] == 0 for r in rows)
    assert all(json.loads(r['seed_raw_path']) == flat for r in rows)
    assert all(json.loads(r['raw_path'])[0] == '相机滤镜' for r in rows)


def test_cross_platform_exposes_each_selling_package_without_asserting_double_count(db, manifest):
    add(db, observation(product_id='synthetic-shared-product', platform='Taobao', discount_sales=100, count=10))
    add(db, observation(product_id='synthetic-shared-product', platform='Tmall', sku_title='Two-pack',
                        discount_sales=60, count=3, stdcategory3='Other platform mapping'))
    add(db, observation(product_id='single-platform', discount_sales=999))
    add(db, observation(product_id='synthetic-shared-product', platform='Taobao', site='US', discount_sales=9999))
    rows = query(db, manifest, 'cross_platform')
    assert len(rows) == 4
    assert all(r['product_id'] == 'synthetic-shared-product' and r['platform_count'] == 2 for r in rows)
    assert all(r['observation_state'] == 'identity_overlap_requires_verification' for r in rows)
    assert sum(r['amount'] for r in rows if r['source_layer'] == 'std') == 160
    assert sum(r['amount'] for r in rows if r['source_layer'] == 'std' and r['in_target_path'] == 1) == 100
    assert {r['sku_title'] for r in rows} == {'Single pack', 'Two-pack'}


def test_cross_platform_does_not_count_raw_and_std_layers_as_two_platforms(db, manifest):
    add(db, observation())
    assert query(db, manifest, 'cross_platform') == []


@pytest.mark.parametrize('override', [
    {'kind': 'DELETE'}, {'site': 'MX'}, {'site': "cn' OR 1=1"}, {'level': True},
    {'level': 4}, {'path': ['wireless']}, {'path': {'stdcategory3': 'wireless'}},
    {'path': '[not json]'}, {'path': ['a', 'b', 4]},
    {'base_month': '2023-12'}, {'current_month': '2024-08'}, {'base_month': '2024-07'},
    {'current_month': '2024-13'}, {'base_month': '2024-05-01'},
    {'product_ids': []}, {'product_ids': 'p1'}, {'product_ids': [None]}, {'product_ids': ['']},
    {'level': 'raw', 'path': ['only-leaf']},
])
def test_scope_and_input_guards(manifest, override):
    args = dict(kind='sku_trace', site='cn', level=3, path=TARGET,
                base_month='2024-05', current_month='2024-06')
    args.update(override)
    with pytest.raises(ValueError):
        diagnostic_query(manifest, **args)


def test_table_guard_cannot_escape_manifest_read_only_tables(manifest):
    for value in ['table', 'a.b.c; DELETE FROM x', 'a.b.c /*', 'a.b.c.d', 'a.b.`c`']:
        changed = copy.deepcopy(manifest)
        changed['policy']['raw_table'] = value
        with pytest.raises(ValueError):
            diagnostic_query(changed, 'sku_trace', 'cn', 3, TARGET, '2024-05', '2024-06')


def test_identifier_literals_cannot_change_query_scope(db, manifest):
    hostile = "x'); DELETE FROM std; --"
    path = ['音频', '麦克风', hostile]
    add(db, observation(product_id=hostile, stdcategory3=hostile))
    add(db, observation(product_id='unrelated', sku_id='unrelated-sku', stdcategory3=hostile, discount_sales=999))
    sql = diagnostic_query(manifest, 'sku_trace', 'cn', 3, path, '2024-05', '2024-06', [hostile])
    assert readonly_sql(sql) == sql
    rows = run_sql(db, manifest, sql)
    assert len(rows) == 2 and {r['product_id'] for r in rows} == {hostile}
    assert db.execute('SELECT COUNT(*) FROM std').fetchone()[0] == 2


def test_job_is_deterministic_and_makes_partial_cohorts_explicit(manifest):
    before = copy.deepcopy(manifest)
    first = diagnostic_job(manifest, 'sku_trace', 'cn', 3, TARGET, '2024-05', '2024-06', ['p2', 'p1', 'p2'])
    second = diagnostic_job(manifest, 'sku_trace', 'cn', 3, json.dumps(TARGET), '2024-05', '2024-06', ['p1', 'p2'])
    assert first == second
    assert first['cohort_is_shard'] and first['product_ids'] == ['p1', 'p2']
    assert first['next_calendar_month_in_scope']
    full = diagnostic_job(manifest, 'sku_trace', 'cn', 3, TARGET, '2024-05', '2024-07')
    assert not full['cohort_is_shard'] and not full['next_calendar_month_in_scope']
    assert manifest == before


def test_semantic_only_same_month_trace_allowed_but_no_fake_comparison(manifest):
    assert diagnostic_query(manifest, 'sku_trace', 'cn', 3, TARGET, '2024-06', '2024-06')
    for kind in ['brands', 'source_coverage']:
        with pytest.raises(ValueError):
            diagnostic_query(manifest, kind, 'cn', 3, TARGET, '2024-06', '2024-06')
    with pytest.raises(ValueError):
        diagnostic_query(manifest, 'brands', 'cn', 3, TARGET, '2024-05', '2024-06', ['p1'])
    with pytest.raises(ValueError):
        diagnostic_query(manifest, 'cross_platform', 'US', 3, TARGET, '2024-05', '2024-06')
    with pytest.raises(ValueError):
        diagnostic_query(manifest, 'brands', 'cn', 'raw', RAW, '2024-05', '2024-06')
