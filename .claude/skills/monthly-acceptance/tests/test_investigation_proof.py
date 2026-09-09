"""Population proof tests reuse offline SQL and recorded-evidence fixtures."""
import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from acceptance_core import Run, canonical, digest, read, write
from acceptance_diagnostics import diagnostic_job
from acceptance_investigation_proof import verify_result_proof
from acceptance_queries import drilldown
from test_diagnostics import db, manifest, TARGET, add, observation, run_sql
from test_execution import record


CAP, RACK, NORMAL = 'synthetic-lens-cap', 'synthetic-filter-rack', 'regular-filter'


def populate(db):
    for pid, before, after, price in [(CAP, 10, 110, .7), (RACK, 1, 1, 358), (NORMAL, 30, 10, 100)]:
        for mm, units in [('2024-05-01', before), ('2024-06-01', after)]:
            add(db, observation(product_id=pid, sku_id='sku-'+pid, month_dt=mm,
                                discount_sales=units*price, discount_price=price, count=units))


def build(worker, db, manifest):
    # Candidate anchors are computed directly from the complete source table,
    # independently of the proposed drill and worker answer.
    facts = {metric: {} for metric in ('spus', 'units', 'amount')}
    for side, mm in [('base', '2024-05-01'), ('current', '2024-06-01')]:
        values = db.execute('SELECT COUNT(DISTINCT product_id),SUM(`count`),SUM(discount_sales) FROM std '
                            'WHERE site=? AND month_dt=? AND stdcategory1=? AND stdcategory2=? AND stdcategory3=?',
                            ('cn', mm, *TARGET)).fetchone()
        for metric, value in zip(('spus', 'units', 'amount'), values):
            facts[metric][side] = value
    candidate = {'candidate_id': 'cand-population-fixture', 'kind': 'result_change', 'site': 'cn',
                 'level': 3, 'path': canonical(TARGET), 'month': '2024-06', 'comparison': 'mom',
                 'facts': {'base_month': '2024-05', 'current_month': '2024-06', 'metrics': facts}}
    spu = {'job_id': 'drill-'+candidate['candidate_id'], 'family': 'drill', 'site': 'cn',
           'candidate_id': candidate['candidate_id'], 'key_fields': ['product_id'],
           'sql': drilldown(manifest, 'cn', candidate['path'], 3, '2024-06', 1)}
    brand = diagnostic_job(manifest, 'brands', 'cn', 3, TARGET, '2024-05', '2024-06')
    sku = diagnostic_job(manifest, 'sku_trace', 'cn', 3, TARGET, '2024-05', '2024-06')
    for job in (spu, brand, sku):
        record(worker, job, run_sql(db, manifest, job['sql']))
    answer = {'population_proof': {'spu_job_id': spu['job_id'], 'brand_job_id': brand['job_id'],
              'sku_job_ids': [sku['job_id']], 'entity_assessments': [{
                  'product_ids': [CAP, RACK, NORMAL], 'status': 'verified',
                  'reason': '已核对各对象的具体售卖物品和双期完整SKU量额。',
                  'counterevidence': '价格与销量解释仍须主Agent阅读源记录确认，机验不自动建立因果。',
                  'evidence_ids': worker.job_record(sku['job_id'])['evidence_ids']}]}}
    return {'worker': worker, 'candidate': candidate, 'answer': answer, 'spu': spu, 'brand': brand, 'sku': sku}


@pytest.fixture
def prepared(db, manifest, tmp_path):
    manifest.pop('architecture',None);manifest['method_version']='3.1.0'
    manifest['policy'].pop('architecture',None);manifest['policy']['version']='3.1.0'
    manifest['policy']['page_size'] = 2
    directory = tmp_path/'worker'; directory.mkdir()
    write(directory/'run.json', {**manifest, 'run_id': 'population-worker', 'method_version': '3.1.0',
                                'inputs': {}, 'policy_hash': digest(manifest['policy'])})
    for folder in ['records', 'evidence', 'queries', 'decisions', 'findings']:
        (directory/folder).mkdir()
    worker = Run(directory)
    populate(db)
    return build(worker, db, manifest)


def compute(prepared, manifest, **kwargs):
    return verify_result_proof(manifest, prepared['candidate'], prepared['answer'], prepared['worker'], **kwargs)


def test_full_population_reconciles_and_compute_only_produces_exact_claims(prepared, manifest):
    result = compute(prepared, manifest, check_claims=False)
    assert result['cohort'] == {'all': 3, 'verified': 3, 'blocked_external': 0, 'not_assessed': 0}
    assert result['anchors']['base']['units'] == 41 and result['anchors']['current']['units'] == 121
    assert result['computed_impact']['units']['exposure'] == 120
    assert result['computed_impact']['amount']['exposure'] == 2070
    assert all(v['unexplained'] == 0 for v in result['computed_impact'].values())
    assert result['unreviewed_product_ids'] == []
    prepared['answer']['impact'] = result['computed_impact']
    assert compute(prepared, manifest)['computed_impact'] == result['computed_impact']
    assert any(p.startswith('records/job-') for p in result['job_files_sha256'])
    assert any(p.startswith('evidence/') for p in result['job_files_sha256'])
    assert all(digest((prepared['worker'].path/p).read_bytes()) == sha for p, sha in result['job_files_sha256'].items())


def test_one_filter_rack_cannot_discharge_the_rest_of_the_population(prepared, manifest):
    prepared['answer']['population_proof']['entity_assessments'][0]['product_ids'] = [RACK]
    result = compute(prepared, manifest, check_claims=False)
    assert result['unreviewed_product_ids'] == sorted([CAP, NORMAL])
    assert result['computed_impact']['spus']['unexplained'] == 2
    assert result['computed_impact']['units']['unexplained'] == 120
    assert result['computed_impact']['amount']['unexplained'] == 2070
    prepared['answer']['impact'] = result['computed_impact']
    prepared['answer']['impact']['units']['exposure'] = 1
    prepared['answer']['impact']['units']['unexplained'] = 0
    with pytest.raises(ValueError, match='自报分母不符'):
        compute(prepared, manifest)


def test_real_external_blockers_remain_in_all_four_unreviewed_measures(prepared, manifest):
    group = prepared['answer']['population_proof']['entity_assessments'][0]
    group['status'] = 'blocked_external'
    result = compute(prepared, manifest, check_claims=False)
    assert result['cohort']['verified'] == 0 and result['cohort']['blocked_external'] == 3
    assert result['unreviewed_product_ids'] == sorted([CAP, RACK, NORMAL])
    assert all(m['unexplained'] == m['exposure'] for m in result['computed_impact'].values())


def test_no_entities_investigated_is_explicit_and_not_a_full_result(prepared, manifest):
    prepared['answer']['population_proof']['entity_assessments'] = []
    prepared['answer']['population_proof']['sku_job_ids'] = []
    result = compute(prepared, manifest, check_claims=False)
    assert result['cohort']['not_assessed'] == 3 and result['cohort']['verified'] == 0
    assert len(result['not_assessed_product_ids']) == 3
    assert result['computed_impact']['spus']['unexplained'] == 3


def test_candidate_parent_denominators_cannot_be_replaced_by_worker(prepared, manifest):
    prepared['candidate']['facts']['metrics']['units']['current'] = 1
    with pytest.raises(ValueError, match='current.units'):
        compute(prepared, manifest, check_claims=False)


def test_missing_spu_tail_page_cannot_look_like_complete_population(prepared, manifest):
    worker, job = prepared['worker'], prepared['spu']
    saved = worker.job_record(job['job_id']); saved['evidence_ids'].pop()
    write(worker.path/'records'/('job-'+job['job_id']+'.json'), saved)
    with pytest.raises(ValueError, match='分页'):
        compute(prepared, manifest, check_claims=False)


def test_a_same_named_job_with_different_scope_sql_is_rejected(prepared, manifest, db):
    job = copy.deepcopy(prepared['spu'])
    job['sql'] = job['sql'].replace("site='cn'", "site='US'")
    record(prepared['worker'], job, [])
    with pytest.raises(ValueError, match='SQL'):
        compute(prepared, manifest, check_claims=False)


def test_wrong_month_actual_database_sql_is_rejected_even_when_request_looks_right(prepared, manifest):
    worker = prepared['worker']
    eid = worker.job_record(prepared['brand']['job_id'])['evidence_ids'][0]
    meta_path = worker.path/'records'/(eid+'.json'); meta = read(meta_path)
    path = worker.path/meta['path']; value = read(path)
    value['response']['metadata']['query'] = value['response']['metadata']['query'].replace('2024-06-01', '2024-07-01')
    write(path, value); meta['sha256'] = digest(path.read_bytes()); write(meta_path, meta)
    with pytest.raises(ValueError, match='实际 SQL'):
        compute(prepared, manifest, check_claims=False)


def test_wrong_month_sku_job_cannot_cover_this_months_entities(prepared, manifest, db):
    job = diagnostic_job(manifest, 'sku_trace', 'cn', 3, TARGET, '2024-04', '2024-05')
    record(prepared['worker'], job, run_sql(db, manifest, job['sql']))
    prepared['answer']['population_proof']['sku_job_ids'] = [job['job_id']]
    with pytest.raises(ValueError, match='SKU轨迹国家/路径/月份'):
        compute(prepared, manifest, check_claims=False)


def test_partial_sku_population_cannot_claim_other_entities(prepared, manifest, db):
    job = diagnostic_job(manifest, 'sku_trace', 'cn', 3, TARGET, '2024-05', '2024-06', [RACK])
    record(prepared['worker'], job, run_sql(db, manifest, job['sql']))
    prepared['answer']['population_proof']['sku_job_ids'] = [job['job_id']]
    with pytest.raises(ValueError, match='SKU行数'):
        compute(prepared, manifest, check_claims=False)


def test_explicit_complete_sku_shards_cover_all_entities_without_double_count(prepared, manifest, db):
    ids = []
    for products in [[RACK], [CAP, NORMAL]]:
        job = diagnostic_job(manifest, 'sku_trace', 'cn', 3, TARGET, '2024-05', '2024-06', products)
        record(prepared['worker'], job, run_sql(db, manifest, job['sql'])); ids.append(job['job_id'])
    prepared['answer']['population_proof']['sku_job_ids'] = ids
    result = compute(prepared, manifest, check_claims=False)
    assert result['cohort']['verified'] == 3
    assert result['computed_impact']['units']['exposure'] == 120


def test_missing_actual_sku_row_is_detected_despite_consistent_page_totals(prepared, manifest):
    worker, job = prepared['worker'], prepared['sku']
    data = worker.job_rows(job['job_id'])
    data = [r for r in data if not (r['source_layer'] == 'std' and r['product_id'] == CAP and r['month_dt'] == '2024-06-01')]
    record(worker, job, data)
    with pytest.raises(ValueError, match='SKU行数'):
        compute(prepared, manifest, check_claims=False)


def test_std_only_sku_is_preserved_as_raw_absence_not_falsely_dropped(prepared, manifest, db):
    db.execute('DELETE FROM raw WHERE product_id=?', (CAP,))
    updated = build(prepared['worker'], db, manifest)
    result = compute(updated, manifest, check_claims=False)
    item = next(r for r in result['sku_observations'] if r['product_id'] == CAP)
    assert all(r['raw_observed'] is False and r['raw_product_ids'] == [] for r in item['observations'])


def test_source_invalid_rows_remain_in_unknown_price_band(prepared, manifest, db):
    add(db, observation(product_id=CAP, sku_id='invalid-child', discount_sales=-1, count=-1))
    updated = build(prepared['worker'], db, manifest)
    result = compute(updated, manifest, check_claims=False)
    assert any(r['side'] == 'current' and r['band'] == -1 and r['spus'] == 1 for r in result['band_population'])


@pytest.mark.parametrize('change', ['no_evidence', 'no_counterevidence', 'duplicate', 'unknown', 'bad_status'])
def test_entity_assessments_cannot_be_generic_or_ambiguous(prepared, manifest, change):
    group = prepared['answer']['population_proof']['entity_assessments'][0]
    if change == 'no_evidence': group['evidence_ids'] = []
    if change == 'no_counterevidence': group['counterevidence'] = ''
    if change == 'duplicate': group['product_ids'].append(CAP)
    if change == 'unknown': group['product_ids'].append('not-in-this-category')
    if change == 'bad_status': group['status'] = 'unimportant'
    with pytest.raises(ValueError):
        compute(prepared, manifest, check_claims=False)


def test_primary_evidence_reference_is_not_mistaken_for_worker_reference(prepared, manifest):
    prepared['answer']['population_proof']['entity_assessments'][0]['evidence_ids'] = ['main:ev-'+'a'*32]
    assert compute(prepared, manifest, check_claims=False)['cohort']['verified'] == 3


def test_proof_detects_files_changed_during_computation(prepared, manifest, monkeypatch):
    import acceptance_investigation_proof as module
    original = module._verified_rows
    changed = False
    def changing(worker, job, hashes, eids):
        nonlocal changed
        rows = original(worker, job, hashes, eids)
        if not changed:
            changed = True
            eid = worker.job_record(job['job_id'])['evidence_ids'][0]
            path = worker.path/'evidence'/(eid+'.json'); value = read(path)
            value['post_verification_change'] = True
            write(path, value)
            mp = worker.path/'records'/(eid+'.json'); meta = read(mp)
            meta['sha256'] = digest(path.read_bytes()); write(mp, meta)
        return rows
    monkeypatch.setattr(module, '_verified_rows', changing)
    with pytest.raises(ValueError, match='证据发生变化'):
        compute(prepared, manifest, check_claims=False)
