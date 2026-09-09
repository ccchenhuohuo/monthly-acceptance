"""Deterministic calendar shards retain the complete collection denominator."""
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from acceptance_core import canonical, digest, months, read, write
from acceptance_machine import (collection_jobs, collection_trace_jobs, diagnose,
                                _normal_spec, _partition_coverage, _trace_jobs)
from test_diagnostics import TARGET, add, db, manifest, observation, run_sql
from test_execution import record
from test_machine_v32 import worker, prepare, spec, population


def _long_window(worker, span=6, trace_span=None):
    worker.manifest['data_month'] = '2026-07'
    worker.policy['machine_diagnostics']['collection_month_span'] = span
    if trace_span is not None:
        worker.policy['machine_diagnostics']['trace_month_span'] = trace_span
    worker.manifest['policy_hash'] = digest(worker.policy)
    write(worker.path / 'run.json', worker.manifest)
    return spec(window_start='2024-01', window_end='2026-07', members=[{
        'member_id': 'ob-1', 'obligation_id': 'ob-1', 'kind': 'result_change',
        'month': '2026-07', 'comparison': 'yoy', 'base_month': '2025-07'}])


def _long_population(db):
    for index, mm in enumerate(months('2024-01', '2026-07'), 1):
        for pid, factor in [('p1', 1), ('p2', 2)]:
            add(db, observation(product_id=pid, sku_id='sku-' + pid, month_dt=mm + '-01',
                                count=index * factor, discount_sales=index * factor * 100))


def test_short_target_and_trace_jobs_are_exactly_unchanged_under_six_month_policy(worker, db):
    population(db)
    specification = spec(window_end='2024-07')
    original = collection_jobs(worker.manifest, specification)
    for job in original:
        record(worker, job, run_sql(db, worker.manifest, job['sql']))
    original_traces = collection_trace_jobs(worker, specification)
    worker.policy['machine_diagnostics']['collection_month_span'] = 6
    assert collection_jobs(worker.manifest, specification) == original
    assert collection_trace_jobs(worker, specification) == original_traces
    assert len(original) == 1 and 'month_partition' not in original[0]


def test_absent_partition_setting_preserves_old_long_window_job(worker):
    specification = _long_window(worker)
    worker.policy['machine_diagnostics'].pop('collection_month_span')
    jobs = collection_jobs(worker.manifest, specification)
    assert len(jobs) == 1
    assert jobs[0]['window_start'] == '2024-01' and jobs[0]['window_end'] == '2026-07'
    assert 'month_partition' not in jobs[0]


def test_thirty_one_month_target_is_partitioned_deterministically_without_window_reduction(worker):
    specification = _long_window(worker)
    jobs = collection_jobs(worker.manifest, specification)
    assert [(j['window_start'], j['window_end']) for j in jobs] == [
        ('2024-01', '2024-06'), ('2024-07', '2024-12'), ('2025-01', '2025-06'),
        ('2025-07', '2025-12'), ('2026-01', '2026-06'), ('2026-07', '2026-07')]
    assert jobs == collection_jobs(worker.manifest, deepcopy(specification))
    assert len({j['job_id'] for j in jobs}) == 6
    assert all(j['collection_scope']['window_start'] == '2024-01' and
               j['collection_scope']['window_end'] == '2026-07' for j in jobs)
    assert Counter(mm for j in jobs for mm in months(j['window_start'], j['window_end'])) == Counter(months('2024-01', '2026-07'))
    assert all(j['sql'].index('WHERE site=') < j['sql'].index('GROUP BY') and 'LIMIT' not in j['sql'] for j in jobs)


def test_trace_partitions_each_complete_identity_group_over_every_month(worker):
    specification = _normal_spec(worker.manifest, _long_window(worker, trace_span=3))
    target = [{'platform': 'Taobao', 'product_id': 'parent1', 'sku_id': 'child1'},
              {'platform': 'Tmall', 'product_id': 'parent1', 'sku_id': 'child1'}]
    jobs = _trace_jobs(worker.manifest, specification, target)
    grouped = defaultdict(list)
    for job in jobs:
        grouped[canonical(job['identity_shard'])].append(job)
    assert len(grouped) == 2 and len(jobs) == 22
    assert {tuple(key) for job in jobs for key in job['identity_shard']} == {
        ('Taobao', 'product_id', 'parent1'), ('Taobao', 'sku_id', 'child1'),
        ('Tmall', 'product_id', 'parent1'), ('Tmall', 'sku_id', 'child1')}
    for group in grouped.values():
        assert Counter(mm for j in group for mm in months(j['window_start'], j['window_end'])) == Counter(months('2024-01', '2026-07'))
        assert all(len(months(j['window_start'], j['window_end'])) <= 3 for j in group)
        assert all(j['sql'].count('month_dt BETWEEN') == 2 for j in group)


@pytest.mark.parametrize('bad', [0, -1, True, 1.5, '6', 121])
def test_invalid_partition_policy_is_rejected(worker, bad):
    specification = _long_window(worker, span=bad)
    with pytest.raises(ValueError, match='month_span'):
        collection_jobs(worker.manifest, specification)


def test_missing_or_overlapping_calendar_partition_is_rejected(worker):
    specification = _long_window(worker)
    jobs = collection_jobs(worker.manifest, specification)
    with pytest.raises(ValueError, match='omits or overlaps'):
        _partition_coverage(_normal_spec(worker.manifest, specification), jobs[:-1], 'target')
    overlapping = deepcopy(jobs)
    overlapping[1]['window_start'] = overlapping[0]['window_end']
    with pytest.raises(ValueError, match='omits or overlaps'):
        _partition_coverage(_normal_spec(worker.manifest, specification), overlapping, 'target')


def test_all_verified_partitions_reconcile_full_thirty_one_month_denominator(worker, db):
    specification = _long_window(worker)
    _long_population(db)
    _, traces = prepare(worker, db, specification)
    diagnosis = read(diagnose(worker, specification)['diagnosis_path'])
    assert diagnosis['scope']['window_start'] == '2024-01' and diagnosis['scope']['window_end'] == '2026-07'
    assert len(diagnosis['monthly']) == 31 and diagnosis['target_row_count'] == 62
    assert diagnosis['target_job_count'] == 6 and diagnosis['trace_job_count'] == len(traces) == 12
    assert diagnosis['identity_shard_count'] == 2
    assert diagnosis['partition_coverage']['target']['required_months'] == months('2024-01', '2026-07')
    assert all(g['each_month_once'] and len(g['covered_months']) == 31
               for layer in diagnosis['partition_coverage'].values() for g in layer['groups'])
    assert diagnosis['members'][0]['base_month'] == '2025-07'
    assert diagnosis['members'][0]['current_month'] == '2026-07'
    assert diagnosis['members'][0]['contributions']['units']['net'] == 36


@pytest.mark.parametrize('damage', ['target_missing', 'target_failed', 'target_missing_page',
                                  'trace_missing', 'trace_missing_page', 'trace_empty_chain'])
def test_missing_failed_or_incomplete_partition_cannot_diagnose(worker, db, damage):
    specification = _long_window(worker)
    _long_population(db)
    _, traces = prepare(worker, db, specification)
    job = traces[0] if damage.startswith('trace_') else collection_jobs(worker.manifest, specification)[0]
    path = worker.path / 'records' / ('job-' + job['job_id'] + '.json')
    rec = read(path)
    if damage.endswith('_missing'):
        path.unlink()
    else:
        if damage.endswith('_failed'):
            rec['execution_status'] = 'failed'
        elif damage.endswith('_missing_page'):
            rec['evidence_ids'].pop()
        else:
            rec.update(evidence_ids=[], expected_rows=0, row_count=0)
        write(path, rec)
    with pytest.raises(ValueError):
        diagnose(worker, specification)
    assert not (worker.path / 'machine/packages/collection-test/diagnosis.json').exists()
    if damage.startswith('target_'):
        with pytest.raises(ValueError):
            collection_trace_jobs(worker, specification)


@pytest.mark.parametrize('kind', ['target', 'identity_trace'])
def test_recorded_rows_outside_their_own_partition_cannot_hide_in_the_full_window(worker, db, kind):
    specification = _long_window(worker)
    _long_population(db)
    _, traces = prepare(worker, db, specification)
    job = collection_jobs(worker.manifest, specification)[0] if kind == 'target' else traces[0]
    rows = worker.job_rows(job['job_id'])
    rows[0] = {**rows[0], 'month_dt': '2026-07-01'}
    record(worker, job, rows)
    with pytest.raises(ValueError, match='out-of-scope month'):
        diagnose(worker, specification)


def test_empty_partition_still_needs_successful_empty_page_and_monthly_reconciliation(worker, db):
    specification = _long_window(worker)
    for mm in ('2025-07', '2026-07'):
        add(db, observation(month_dt=mm + '-01'))
    prepare(worker, db, specification)
    diagnosis = read(diagnose(worker, specification)['diagnosis_path'])
    assert diagnosis['collection_complete'] and len(diagnosis['monthly']) == 31
    assert diagnosis['monthly']['2024-01'] == {'state': 'no_observation', 'market_values_are_zero': False}
    assert len(diagnosis['partition_coverage']['target']['groups'][0]['partitions']) == 6
    first = collection_jobs(worker.manifest, specification)[0]
    assert worker.job_record(first['job_id'])['row_count'] == 0
    assert worker.job_record(first['job_id'])['evidence_ids']
