"""Executable full-population machine diagnostics, including adversarial claims."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from acceptance_core import Run, business_key, canonical, digest, read, write
from acceptance_machine import (audit, collection_jobs, collection_trace_jobs, cumulative_impact,
                                diagnose, residual_impact, verified_diagnostics, _comparison)
from acceptance_queries import job as baseline_job
from test_diagnostics import TARGET, RAW, OTHER, add, db, manifest, observation, run_sql
from test_execution import record


class Percentile:
    def __init__(self):
        self.values = []
    def step(self, value, quantile):
        if value is not None:
            self.values.append(value)
    def finalize(self):
        return sorted(self.values)[len(self.values)//2] if self.values else None


@pytest.fixture
def worker(tmp_path, manifest):
    manifest.pop('architecture',None);manifest['method_version']='3.2.0'
    manifest['policy'].pop('architecture',None);manifest['policy']['version']='3.2.0'
    manifest['policy']['page_size'] = 3
    manifest['policy']['machine_diagnostics'] = {'identity_shard_size': 2}
    root = tmp_path/'machine-run'; root.mkdir()
    write(root/'run.json', {**manifest, 'run_id': 'machine-fixture', 'method_version': '3.2.0',
                            'inputs': {}, 'policy_hash': digest(manifest['policy'])})
    for name in ('records', 'evidence', 'queries'):
        (root/name).mkdir()
    return Run(root)


def spec(**changes):
    return {'package_id': 'collection-test', 'site': 'cn', 'level': 3, 'path': TARGET,
            'window_start': '2024-05', 'window_end': '2024-06', 'obligation_ids': ['ob-1'],
            'members': [{'member_id': 'ob-1', 'obligation_id': 'ob-1', 'kind': 'result_change',
                         'month': '2024-06', 'comparison': 'mom', 'base_month': '2024-05'}], **changes}


def population(db):
    for pid, before, after, price in [('tiny', 0, 1, 1), ('big', 100, 30100, 5), ('down', 200, 100, 10)]:
        for mm, units in [('2024-05-01', before), ('2024-06-01', after)]:
            add(db, observation(product_id=pid, sku_id='sku-'+pid, month_dt=mm,
                                discount_sales=units*price, count=units, std_brand_name=pid))


def prepare(worker, db, specification=None):
    specification = specification or spec()
    db.create_aggregate('PERCENTILE_APPROX', 2, Percentile)
    for job in collection_jobs(worker.manifest, specification):
        record(worker, job, run_sql(db, worker.manifest, job['sql']))
    traces = collection_trace_jobs(worker, specification)
    for job in traces:
        record(worker, job, run_sql(db, worker.manifest, job['sql']))
    level, site = specification['level'], specification['site']
    jobs = [baseline_job(worker.manifest, 'raw_paths', site)] if level == 'raw' else [
        baseline_job(worker.manifest, 'category', site, level=level), baseline_job(worker.manifest, 'bands', site, level=level)]
    for job in jobs:
        record(worker, job, run_sql(db, worker.manifest, job['sql']))
    return specification, traces


def full(worker, specification):
    result = diagnose(worker, specification)
    return read(result['diagnosis_path'])


def cause(worker, diagnosis, pid='tiny', metric='units', contribution=1, cause_id='cause-small'):
    member = diagnosis['members'][0]
    entity = next(e for e in member['entities'] if e['product_id'] == pid)
    bindings = []
    for eid in diagnosis['evidence_ids']:
        rows = worker.get_evidence(eid)['response']['data']
        selected = [canonical([r[k] for k in ('source_layer','site','platform','month_dt','product_id','sku_id','raw_path','std_path','attribute_key')])
                    for r in rows if r.get('source_layer') == 'std' and r.get('product_id') == pid]
        selected = [k for k in selected if k in entity['record_keys']]
        if selected:
            bindings.append({'evidence_id': eid, 'record_keys': selected})
    return {'cause_id': cause_id, 'member_id': member['member_id'], 'entity_key': entity['entity_key'],
            'metric': metric, 'contribution': contribution, 'evidence_bindings': bindings,
            'mechanism': 'Specific observed identity hypothesis, requiring independent business review.',
            'counterevidence': 'Complete population remains available for alternative explanations.'}


def test_collection_is_scoped_before_aggregation_and_shards_use_exact_platforms(worker, db):
    population(db)
    specification, traces = prepare(worker, db)
    target = collection_jobs(worker.manifest, specification)[0]['sql']
    assert target.index('WHERE site=') < target.index('GROUP BY')
    assert 'JOIN' not in target and 'LIMIT' not in target
    assert all(len(j['identity_shard']) <= 2 for j in traces)
    assert all('platform=' in j['sql'] and 'JOIN' not in j['sql'] for j in traces)
    assert len({tuple(x) for j in traces for x in j['identity_shard']}) == 6


def test_all_entities_both_signs_brand_and_four_metrics_are_computed(worker, db):
    population(db); specification, _ = prepare(worker, db)
    d = full(worker, specification); m = d['members'][0]
    assert m['population_count'] == 3
    assert m['contributions']['units']['positive'] == 30001
    assert m['contributions']['units']['negative'] == -100
    assert m['contributions']['units']['gross'] == 30101
    assert sum(b['units_change'] for b in m['brands']) == 29901
    assert set(m['residual']) == {'spus','bands','units','amount'}
    assert d['states']['substantive_investigation'] == 'pending'
    assert not d['risk_bounded']
    assert audit(worker)['valid'] and not audit(worker)['business_complete']
    assert verified_diagnostics(worker)[0] == d


def test_small_local_defect_does_not_close_major_change(worker, db):
    population(db); specification, _ = prepare(worker, db)
    d = full(worker, specification); claim = cause(worker, d)
    receipt = diagnose(worker, specification, claims=[claim]); changed = read(receipt['diagnosis_path'])
    assert changed['proposed_residual_by_member']['ob-1']['units']['unexplained'] == 30100
    assert changed['residual_by_member']['ob-1']['units']['unexplained'] == 30101
    assert changed['states']['cause_explanation'] == 'unexplained'
    assert changed['cause_contribution_proposals'][0]['cause_acceptance_state'] == 'pending_business_review'
    assert verified_diagnostics(worker)[0] == changed


def test_self_reported_denominator_is_recomputed_and_rejected(worker, db):
    population(db); specification, _ = prepare(worker, db)
    d = full(worker, specification); claim = cause(worker, d); claim['denominator'] = 1
    with pytest.raises(ValueError, match='denominator mismatch'):
        diagnose(worker, specification, claims=[claim])


def test_true_existing_but_unrelated_evidence_cannot_support_cause(worker, db):
    population(db); specification, _ = prepare(worker, db)
    d = full(worker, specification); claim = cause(worker, d)
    unrelated = cause(worker, d, 'big', contribution=1)
    claim['evidence_bindings'] = unrelated['evidence_bindings']
    with pytest.raises(ValueError, match='unrelated'):
        diagnose(worker, specification, claims=[claim])


def test_shared_evidence_does_not_allow_duplicate_contribution(worker, db):
    population(db); specification, _ = prepare(worker, db)
    d = full(worker, specification); claim = cause(worker, d)
    with pytest.raises(ValueError, match='double-count'):
        diagnose(worker, specification, claims=[claim, {**claim, 'cause_id': 'another-cause'}])


@pytest.mark.parametrize('damage', ['missing_page', 'failed', 'duplicate_key'])
def test_incomplete_machine_evidence_cannot_be_low_impact(worker, db, damage):
    population(db); specification, traces = prepare(worker, db)
    job = collection_jobs(worker.manifest, specification)[0]
    saved = worker.job_record(job['job_id'])
    if damage == 'missing_page':
        saved['evidence_ids'].pop()
    elif damage == 'failed':
        saved['execution_status'] = 'failed'
    else:
        rows = worker.job_rows(job['job_id'])
        record(worker, job, rows+[rows[0]])
        saved = worker.job_record(job['job_id'])
    write(worker.path/'records'/('job-'+job['job_id']+'.json'), saved)
    with pytest.raises(ValueError):
        diagnose(worker, specification, risk_bounds={'upper_bound': .00001})


def test_missing_identity_shard_is_not_complete_check(worker, db):
    population(db); specification, traces = prepare(worker, db)
    (worker.path/'records'/('job-'+traces[-1]['job_id']+'.json')).unlink()
    with pytest.raises(ValueError, match='missing_or_failed_job'):
        diagnose(worker, specification)


def test_cross_platform_same_id_keeps_independent_values_and_spus(worker, db):
    for platform, units, price in [('Taobao', 1, 10), ('Tmall', 3, 200)]:
        for mm in ('2024-05-01', '2024-06-01'):
            add(db, observation(platform=platform, product_id='same', sku_id='same-sku', month_dt=mm,
                                count=units, discount_sales=units*price))
    specification, _ = prepare(worker, db); d = full(worker, specification)
    assert d['monthly']['2024-06']['source_id_spus'] == 1
    assert d['monthly']['2024-06']['platform_scoped_spus'] == 2
    assert d['monthly']['2024-06']['units'] == 4
    assert d['members'][0]['population_count'] == 2
    assert any(f['kind'] == 'cross_platform_numeric_id_overlap' for f in d['conflicts'])
    assert not d['risk_bounded']


@pytest.mark.parametrize('placeholder', [None, '', '0'])
def test_placeholder_sku_keeps_observed_exposure_without_cross_parent_trace(worker, db, placeholder):
    for mm, units in [('2024-05-01', 1), ('2024-06-01', 1000)]:
        add(db, observation(product_id='target-parent', sku_id=placeholder, month_dt=mm, count=units, discount_sales=units*100))
        add(db, observation(product_id='unrelated-parent', sku_id=placeholder, month_dt=mm, count=100000,
                            discount_sales=10000000, stdcategory1=OTHER[0], stdcategory2=OTHER[1], stdcategory3=OTHER[2]))
    specification, traces = prepare(worker, db); d = full(worker, specification)
    assert all(identity[1] == 'product_id' for j in traces for identity in j['identity_shard'])
    assert d['members'][0]['contributions']['units']['positive'] == 999
    assert d['trace_unique_row_count'] == 4
    assert any(f['kind'] == 'untraceable_identity' for f in d['conflicts'])
    assert not d['risk_bounded']


def test_missing_values_stay_unknown_instead_of_small_zero_residual(worker, db):
    add(db, observation(month_dt='2024-05-01', discount_sales=None, count=None))
    add(db, observation(month_dt='2024-06-01', discount_sales=100, count=1))
    specification, _ = prepare(worker, db); d = full(worker, specification)
    assert d['members'][0]['residual']['units']['state'] == 'unknown'
    assert d['members'][0]['residual']['amount']['state'] == 'unknown'
    assert d['members'][0]['residual']['bands']['state'] == 'unknown'


def gap_spec(**changes):
    return spec(level='raw', path=RAW, window_start='2024-04', window_end='2024-07',
                members=[{'member_id': 'gap-1', 'obligation_id': 'gap-1', 'kind': 'raw_presence_gap',
                          'month': '2024-05', 'gap_months': ['2024-05','2024-06']}],
                obligation_ids=['gap-1'], **changes)


def test_gap_uses_complete_latest_pre_gap_population_and_each_month(worker, db):
    for pid, units in [('one', 1), ('large', 1000)]:
        add(db, observation(month_dt='2024-04-01', product_id=pid, sku_id='sku-'+pid, count=units, discount_sales=units*100))
        add(db, observation(month_dt='2024-07-01', product_id=pid, sku_id='sku-'+pid, count=units, discount_sales=units*100))
    specification, _ = prepare(worker, db, gap_spec()); d = full(worker, specification); gap = d['members'][0]
    assert gap['base_month'] == '2024-04'
    assert gap['source_seed_count'] == 2
    assert gap['expected_destination_cells'] == 8
    assert len(gap['destination_cells']) == 8
    assert {c['month'] for c in gap['destination_cells']} == {'2024-05','2024-06'}
    assert gap['residual']['units']['exposure'] == 1001
    assert gap['future_recovery_not_used_as_baseline']


def test_future_recovery_cannot_supply_missing_gap_baseline(worker, db):
    add(db, observation(month_dt='2024-07-01'))
    specification, _ = prepare(worker, db, gap_spec()); d = full(worker, specification)
    assert not d['complete']
    assert d['members'][0]['state'] == 'missing_pre_gap_baseline'
    assert d['members'][0]['residual']['units']['state'] == 'unknown'


def test_skipping_an_intermediate_gap_month_is_rejected(worker, db):
    add(db, observation(month_dt='2024-04-01'))
    specification = gap_spec(); specification['members'][0]['gap_months'] = ['2024-06']
    specification['members'][0]['month'] = '2024-06'
    specification, _ = prepare(worker, db, specification)
    with pytest.raises(ValueError, match='intermediate'):
        diagnose(worker, specification)


def test_scope_omitting_comparison_month_is_rejected(worker):
    with pytest.raises(ValueError, match='baseline'):
        collection_jobs(worker.manifest, spec(window_start='2024-06'))


def test_mutated_machine_denominator_invalidates_receipt(worker, db):
    population(db); specification, _ = prepare(worker, db)
    receipt = diagnose(worker, specification); d = read(receipt['diagnosis_path'])
    d['members'][0]['contributions']['units']['gross'] = 1
    write(receipt['diagnosis_path'], d)
    assert not audit(worker)['valid']


def test_cumulative_small_cases_escalate_without_net_cancellation_or_overlap(worker, db):
    for n in range(100):
        for mm, amount in [('2024-05-01', 100), ('2024-06-01', 110 if n == 0 else 90 if n == 1 else 100)]:
            add(db, observation(product_id='p'+str(n), sku_id='s'+str(n), month_dt=mm,
                                count=amount, discount_sales=amount*10))
    specification, _ = prepare(worker, db); d = full(worker, specification)
    m = d['members'][0]
    entities = {e['product_id']: e['entity_key'] for e in m['entities']}
    selections = [{'case_id': 'small-'+str(i), 'diagnosis_id': d['diagnosis_id'], 'member_id': m['member_id'],
                   'entity_keys': [entities['p'+str(i)]]} for i in (0, 1)]
    first = cumulative_impact([d], selections[:1])[0]
    combined = cumulative_impact([d], selections + [selections[0]])[0]
    assert not first['requires_cumulative_review']
    assert combined['requires_cumulative_review']
    assert combined['deduplicated_pending_entity_count'] == 2
    assert combined['current_exposure']['spus']['pending'] == 2
    changes = combined['change_exposure_by_comparison'][0]['units']
    assert changes['remaining_positive'] == 10 and changes['remaining_negative'] == -10
    assert changes['gross_remaining'] == 20 and changes['net_is_not_risk_measure'] == 0
    assert sum(x['pending_spus'] for x in combined['current_exposure']['bands']['recomputed_population']) == 2


def test_shared_parent_package_diagnoses_exact_child_members(worker, db):
    population(db)
    for mm, units in [('2024-05-01', 20), ('2024-06-01', 25)]:
        add(db, observation(product_id='sibling', sku_id='sibling-sku', month_dt=mm, count=units,
                            discount_sales=units*100, stdcategory3='有线麦克风'))
    package = spec(level=2, path=TARGET[:2], members=[
        {'member_id': 'ob-child', 'obligation_id': 'ob-child', 'kind': 'result_change', 'source_hash': 'frozen-source-child',
         'site': 'cn', 'path': TARGET, 'month': '2024-06', 'comparison': 'mom', 'base_month': '2024-05'},
        {'member_id': 'ob-parent', 'obligation_id': 'ob-parent', 'kind': 'result_change', 'source_hash': 'frozen-source-parent',
         'site': 'cn', 'path': TARGET[:2], 'month': '2024-06', 'comparison': 'mom', 'base_month': '2024-05'}],
        obligation_ids=['ob-child','ob-parent'])
    prepare(worker, db, package)
    for family in ('category','bands'):
        job = baseline_job(worker.manifest, family, 'cn', level=3)
        record(worker, job, run_sql(db, worker.manifest, job['sql']))
    d = full(worker, package); by_id = {m['member_id']: m for m in d['members']}
    assert by_id['ob-child']['population_count'] == 3
    assert by_id['ob-child']['totals']['current']['units'] == 30201
    assert by_id['ob-parent']['population_count'] == 4
    assert by_id['ob-parent']['totals']['current']['units'] == 30226
    assert by_id['ob-child']['scope']['path'] == TARGET
    assert d['computed_member_hash'] == digest(sorted(package['members'], key=canonical))


def test_shared_child_can_cover_parent_only_with_comparison_scope_equality(worker, db):
    population(db)
    # A historical sibling outside the member's two periods must not prohibit
    # a valid equivalence for May/June, nor be incorrectly declared equivalent.
    add(db, observation(product_id='old-sibling', sku_id='old-sku', month_dt='2024-04-01', stdcategory3='有线麦克风'))
    package = spec(window_start='2024-04', members=[
        {'member_id': 'parent', 'obligation_id': 'parent', 'kind': 'result_change', 'path': TARGET[:2],
         'month': '2024-06', 'comparison': 'mom', 'base_month': '2024-05'}], obligation_ids=['parent'])
    prepare(worker, db, package)
    for family in ('category','bands'):
        job = baseline_job(worker.manifest, family, 'cn', level=2)
        record(worker, job, run_sql(db, worker.manifest, job['sql']))
    d = full(worker, package)
    assert d['members'][0]['population_count'] == 3
    # Adding an actual comparison-month sibling must defeat that equivalence.
    add(db, observation(product_id='new-sibling', sku_id='new-sku', month_dt='2024-06-01', stdcategory3='有线麦克风'))
    for family in ('category','bands'):
        job = baseline_job(worker.manifest, family, 'cn', level=2)
        record(worker, job, run_sql(db, worker.manifest, job['sql']))
    with pytest.raises(ValueError, match='denominator mismatch'):
        diagnose(worker, package)


def test_raw_level_six_and_original_later_gap_member_expand_intermediate_months(worker, db):
    add(db, observation(month_dt='2024-04-01'))
    package = gap_spec(); package['level'] = 6
    package['members'] = [{'member_id': 'original-june', 'obligation_id': 'original-june', 'kind': 'raw_presence_gap', 'month': '2024-06'}]
    package['obligation_ids'] = ['original-june']
    normalized = {**package, 'level': 'raw'}
    prepare(worker, db, normalized)
    d = full(worker, package)
    assert d['scope']['level'] == 'raw'
    assert d['members'][0]['gap_months'] == ['2024-05','2024-06']
    assert d['members'][0]['expected_destination_cells'] == 4


def test_cumulative_formal_spu_and_price_bands_recompute_same_id_across_platforms(worker, db):
    for platform, units, price in [('Taobao', 1, 10), ('Tmall', 3, 200)]:
        for mm in ('2024-05-01', '2024-06-01'):
            add(db, observation(platform=platform, product_id='same', sku_id='same-sku', month_dt=mm,
                                count=units, discount_sales=units*price))
    package, _ = prepare(worker, db); d = full(worker, package)
    assert d['members'][0]['residual']['spus']['exposure'] == 1
    assert d['members'][0]['residual']['spus']['platform_entity_count'] == 2
    value = cumulative_impact([d])[0]
    assert value['current_exposure']['spus']['pending'] == 1
    assert value['current_exposure']['spus']['pending_platform_entities'] == 2
    bands = value['current_exposure']['bands']['recomputed_population']
    assert len(bands) == 1 and bands[0]['all_spus'] == 1
    assert bands[0]['band'] == 2  # (10 + 600)/(1 + 3) = 152.5
    assert not value['current_exposure']['spus']['physical_identity_equivalence_proven']
