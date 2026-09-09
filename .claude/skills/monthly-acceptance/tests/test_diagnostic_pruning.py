"""Compare optimized diagnostics with the complete, unpruned relational result."""
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from acceptance_core import canonical
from acceptance_diagnostics import _scope, _seed, _all_rows, diagnostic_query, diagnostic_job
from test_diagnostics import db, manifest, TARGET, OTHER, RAW, add, observation, run_sql


@pytest.mark.parametrize('kind,level,path,products', [
    ('sku_trace', 3, TARGET, ['p1']), ('sku_trace', 3, TARGET, None),
    ('sku_trace', 'raw', RAW, ['p1']), ('source_coverage', 3, TARGET, ['p1']),
    ('source_coverage', 'raw', RAW, None), ('cross_platform', 3, TARGET, ['p1']),
])
def test_early_filter_preserves_all_final_observations(db, manifest, kind, level, path, products):
    for mm in ['2024-03-01', '2024-05-01', '2024-06-01', '2024-07-01']:
        add(db, observation(month_dt=mm))
    # One SKU changes parent and target path; raw/std attributes disagree.
    add(db, observation(month_dt='2024-07-01', product_id='new-parent',
                        stdcategory1=OTHER[0], stdcategory2=OTHER[1], stdcategory3=OTHER[2]))
    add(db, observation(month_dt='2024-06-01', platform='Tmall', product_title='Different platform identity'))
    add(db, observation(month_dt='2024-05-01', product_id=None, sku_id=None))
    add(db, observation(month_dt='2024-05-01', product_id='missing-sku', sku_id=None))
    add(db, observation(month_dt='2024-05-01', product_id=None, sku_id='traceable-sku'))
    for i in range(60):
        add(db, observation(product_id='unrelated-'+str(i), sku_id='other-'+str(i),
            month_dt='2024-06-01', stdcategory3='unrelated', sub_category='unrelated'))
    scope = _scope(manifest, kind, 'cn', level, path, '2024-05', '2024-06', products)
    mode = 'products' if kind == 'cross_platform' else 'trace'
    optimized = diagnostic_query(manifest, kind, 'cn', level, path, '2024-05', '2024-06', products)
    reference = optimized.replace(_all_rows(scope, mode), _all_rows(scope))
    assert reference != optimized
    assert sorted(map(canonical, run_sql(db, manifest, optimized))) == sorted(map(canonical, run_sql(db, manifest, reference)))
    prefix = 'WITH '+_seed(scope, base_only=kind == 'source_coverage')+', '
    pruned_count = run_sql(db, manifest, prefix+_all_rows(scope, mode)+' SELECT COUNT(*) AS n FROM all_rows')[0]['n']
    complete_count = run_sql(db, manifest, prefix+_all_rows(scope)+' SELECT COUNT(*) AS n FROM all_rows')[0]['n']
    assert pruned_count < complete_count / 2


@pytest.mark.parametrize('level,path', [(3, TARGET), ('raw', RAW)])
def test_gap_window_excludes_unused_history_without_changing_destinations(db, manifest, level, path):
    for mm, amount in [('2024-03-01', 9000), ('2024-05-01', 100),
                       ('2024-06-01', 200), ('2024-07-01', 8000)]:
        add(db, observation(month_dt=mm, discount_sales=amount))
    scope = _scope(manifest, 'source_coverage', 'cn', level, path, '2024-05', '2024-06', None)
    job = diagnostic_job(manifest, 'source_coverage', 'cn', level, path, '2024-05', '2024-06')
    entire_history = {**scope, 'window_start': manifest['starts']['cn'], 'window_end': manifest['data_month']}
    reference = job['sql'].replace(_all_rows(scope, 'trace'), _all_rows(entire_history, 'trace'))
    assert reference != job['sql']
    rows = run_sql(db, manifest, job['sql'])
    assert len(rows) == 2 and {r['month_dt'] for r in rows} == {'2024-06-01'}
    assert sorted(map(canonical, rows)) == sorted(map(canonical, run_sql(db, manifest, reference)))
    assert (job['window_start'], job['window_end']) == ('2024-05', '2024-06')
    trace = diagnostic_job(manifest, 'sku_trace', 'cn', level, path, '2024-05', '2024-06')
    assert (trace['window_start'], trace['window_end']) == (manifest['starts']['cn'], manifest['data_month'])
