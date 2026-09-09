"""Supplier placeholder rows retain exposure but cannot join unrelated parents."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from acceptance_core import canonical, read, write
from acceptance_diagnostics import _scope, _seed, _all_rows, diagnostic_job, traceable_sku
from acceptance_investigation_proof import verify_result_proof
import acceptance_investigation as investigation
from test_diagnostics import db, manifest, TARGET, OTHER, RAW, add, observation, query, run_sql
from test_execution import record
from test_gap_proof import gap, build as build_gap, compute as compute_gap
from test_investigation_proof import prepared, build, compute, CAP, RACK, NORMAL
from test_investigation_integration import integrated, submit, write_submission, reviewed_answer


PLACEHOLDERS = [None, '', '   ', '0', ' 0 ']


@pytest.mark.parametrize('sku', PLACEHOLDERS)
@pytest.mark.parametrize('products', [None, ['p1']])
def test_placeholder_trace_prunes_unrelated_rows_before_grouping_and_keeps_parent_context(db, manifest, sku, products):
    seed = observation(month_dt='2024-05-01', sku_id=sku, sku_title=None)
    add(db, seed); add(db, seed)
    add(db, observation(sku_id=sku, sku_title=None, discount_sales=200, count=2))
    add(db, observation(sku_id='new-usable-child', stdcategory3='sibling-path', discount_sales=300, count=3))
    for platform in ['Taobao', 'Tmall']:
        add(db, observation(product_id='unrelated-'+platform, platform=platform,
            sku_id=sku, stdcategory3='unrelated', sub_category='unrelated', discount_sales=9999))
    rows = query(db, manifest, 'sku_trace', product_ids=products)
    assert {r['product_id'] for r in rows} == {'p1'}
    assert len(rows) == 6 and all(r['matches_seed_product'] == 1 for r in rows)
    assert all(r['matches_seed_sku'] == 0 for r in rows if not traceable_sku(r['sku_id']))
    target = [r for r in rows if r['source_layer'] == 'std' and r['in_target_path'] == 1]
    assert sum(r['source_rows'] for r in target) == 3
    assert sum(r['amount'] for r in target) == 400
    assert sum(r['units'] for r in target) == 4
    scope = _scope(manifest, 'sku_trace', 'cn', 3, TARGET, '2024-05', '2024-06', products)
    # Checking only the final output would miss the old empty-SKU memory blowup.
    early = run_sql(db, manifest, 'WITH '+_seed(scope)+', '+_all_rows(scope, 'trace')+' SELECT * FROM all_rows')
    assert len(early) == 6 and {r['product_id'] for r in early} == {'p1'}


@pytest.mark.parametrize('sku', PLACEHOLDERS)
@pytest.mark.parametrize('level,path', [(3, TARGET), ('raw', RAW)])
def test_gap_placeholders_keep_seed_exposure_and_explicit_unknown_destination_cells(db, manifest, sku, level, path):
    for pid, units, amount in [('synthetic-parent-a', 64, 148416), ('synthetic-parent-b', 109, 15042)]:
        add(db, observation(product_id=pid, month_dt='2024-05-01', sku_id=sku, sku_title=None,
            count=units, discount_sales=amount))
        add(db, observation(product_id=pid, sku_id=sku, sku_title=None, sub_category='outside', stdcategory3='outside'))
    add(db, observation(product_id='unrelated', sku_id=sku, sub_category='outside', stdcategory3='outside'))
    rows = query(db, manifest, 'source_coverage', level=level, path=path)
    assert len(rows) == 4
    assert {r['observation_state'] for r in rows} == {'untraceable_seed_identity'}
    assert all(r['destination_product_id'] is None and r['source_rows'] is None and r['amount'] is None for r in rows)
    seeds = {r['seed_product_id']: r for r in rows}
    assert len(seeds) == 2 and sum(r['seed_source_rows_context'] for r in seeds.values()) == 2
    assert sum(r['seed_units_context'] for r in seeds.values()) == 173
    assert sum(r['seed_amount_context'] for r in seeds.values()) == 163458


@pytest.mark.parametrize('sku', ['valid-moving-sku', '00', '000', '-1', 'N/A'])
def test_usable_sku_reparenting_remains_visible_without_guessing_more_placeholder_tokens(db, manifest, sku):
    add(db, observation(month_dt='2024-05-01', product_id='old-parent', sku_id=sku))
    add(db, observation(product_id='new-parent', sku_id=sku, sub_category='moved', stdcategory3='moved'))
    assert traceable_sku(sku)
    for products in [None, ['old-parent']]:
        trace = query(db, manifest, 'sku_trace', product_ids=products)
        assert {r['product_id'] for r in trace} == {'old-parent', 'new-parent'}
        assert all(r['matches_seed_sku'] == 1 for r in trace if r['product_id'] == 'new-parent')
        coverage = query(db, manifest, 'source_coverage', product_ids=products)
        assert {r['observation_state'] for r in coverage} == {'same_sku_other_parent'}


def refreshed_result(case, db, manifest, sku, side='both'):
    where = 'product_id=?' + (" AND month_dt='2024-06-01'" if side == 'current' else '')
    for table in ['raw', 'std']:
        db.execute('UPDATE '+table+' SET sku_id=?,sku_title=NULL WHERE '+where, (sku, CAP))
    return build(case['worker'], db, manifest)


@pytest.mark.parametrize('sku', PLACEHOLDERS)
def test_one_target_period_untraceable_row_blocks_verified_and_allows_truthful_external_block(prepared, db, manifest, sku):
    refreshed = refreshed_result(prepared, db, manifest, sku, side='current')
    with pytest.raises(ValueError, match='不可追踪'):
        compute(refreshed, manifest, check_claims=False)
    proof = refreshed['answer']['population_proof']
    original = proof['entity_assessments'][0]
    proof['entity_assessments'] = [{**original, 'product_ids': [RACK, NORMAL]},
        {**original, 'product_ids': [CAP], 'status': 'blocked_external'}]
    result = compute(refreshed, manifest, check_claims=False)
    assert result['cohort'] == {'all': 3, 'verified': 2, 'blocked_external': 1, 'not_assessed': 0}
    assert result['anchors']['base']['units'] == 41 and result['anchors']['current']['units'] == 121
    assert result['computed_impact']['units']['unexplained'] == 100
    assert result['untraceable_product_ids'] == result['unreviewed_product_ids'] == [CAP]
    item = next(r for r in result['sku_observations'] if r['product_id'] == CAP)
    unknown = next(r for r in item['observations'] if r['month'] == '2024-06')
    assert unknown['raw_observed'] is None and unknown['raw_product_ids'] == []
    assert unknown['identity_state'] == 'untraceable_sku_or_platform'
    assert item['raw_parent_contexts']
    for context in item['raw_parent_contexts']:
        assert context['product_id'] == CAP and context['is_sku_identity_match'] is False
        assert context['raw_record_keys']
        assert all(json.loads(key)[4] == CAP for key in context['raw_record_keys'])


def test_result_proof_never_cross_links_two_placeholder_parents(prepared, db, manifest):
    for table in ['raw', 'std']:
        db.execute('UPDATE '+table+" SET sku_id='0',sku_title=NULL")
    refreshed = build(prepared['worker'], db, manifest)
    refreshed['answer']['population_proof']['entity_assessments'][0]['status'] = 'blocked_external'
    result = compute(refreshed, manifest, check_claims=False)
    assert result['cohort']['verified'] == 0 and result['cohort']['blocked_external'] == 3
    assert all(m['unexplained'] == m['exposure'] for m in result['computed_impact'].values())
    for item in result['sku_observations']:
        assert all(r['raw_observed'] is None and r['raw_product_ids'] == [] for r in item['observations'])
        assert all(json.loads(key)[4] == item['product_id']
            for context in item['raw_parent_contexts'] for key in context['raw_record_keys'])


@pytest.mark.parametrize('sku', ['0', ' 0 '])
def test_gap_proof_rejects_placeholder_verified_but_retains_blocker_and_denominators(gap, db, manifest, sku):
    for table in ['raw', 'std']:
        db.execute('UPDATE '+table+' SET sku_id=?,sku_title=NULL WHERE product_id=? AND month_dt=?',
            (sku, 'p2', '2024-05-01'))
    refreshed = build_gap(gap['parent'], gap['worker'], db, manifest, gap['kind'])
    with pytest.raises(ValueError, match='不能完成去向核实'):
        compute_gap(refreshed, manifest, check_claims=False)
    groups = refreshed['answer']['gap_proof']['entity_assessments']
    original = groups[0]
    groups[:] = [{**original, 'product_ids': ['p1']}, {**original, 'product_ids': ['p2'], 'status': 'blocked_external'}]
    result = compute_gap(refreshed, manifest, check_claims=False)
    assert result['cohort'] == {'all': 2, 'verified': 1, 'blocked_external': 1, 'not_assessed': 0}
    assert result['anchors'] == {'month': '2024-05', 'spus': 2, 'nrows': 2, 'units': 13, 'amount': 400}
    assert result['untraceable_product_ids'] == result['unreviewed_product_ids'] == ['p2']
    assert result['destination_state_counts']['untraceable_seed_identity'] == 4
    assert result['computed_impact']['units']['unexplained'] == 3


def blocked_submission(case, db, sku):
    parent, worker = case['parent'], case['worker']
    refreshed = refreshed_result(case, db, parent.manifest, sku)
    answer = case['payload']['answers'][0]
    answer['population_proof'] = refreshed['answer']['population_proof']
    original = answer['population_proof']['entity_assessments'][0]
    answer['population_proof']['entity_assessments'] = [{**original, 'product_ids': [RACK, NORMAL]},
        {**original, 'product_ids': [CAP], 'status': 'blocked_external'}]
    detail = worker.job_record(refreshed['sku']['job_id'])['evidence_ids']
    for check in answer['checks'].values():
        check['evidence_ids'] = detail
    answer['checks']['sku_identity'].update(status='blocked_external', blocking={
        'type': 'missing_external_record', 'required_record': 'Supplier variant identity for the synthetic placeholder rows',
        'provider_role': 'Source record owner', 'attempted_access': 'Read every synthetic raw/std parent record and preserved both-period totals',
        'why_unavailable': 'The synthetic source contains a placeholder and no variant description',
        'decision_impact': 'This parent retains its full unverified contribution',
        'resume_action': 'Recheck the exact rows after the supplier supplies variant identity',
        'blocked_checks': ['sku_identity']})
    answer['business_verdict'] = 'undetermined'
    answer['impact'] = verify_result_proof(parent.manifest, case['candidate'], answer, worker, check_claims=False)['computed_impact']
    write_submission(case)
    return answer


@pytest.mark.parametrize('sku', [None, '', '0'])
def test_complete_internal_investigation_with_missing_sku_can_submit_and_review_as_blocked(integrated, db, sku):
    blocked_submission(integrated, db, sku)
    submit(integrated)
    parent, task = integrated['parent'], integrated['task']
    investigation.review(parent, '/root', task['task_id'], reviewed_answer(integrated))
    audited = investigation.audit(parent, {'findings': [{'obligation_ids': [integrated['oid']]}]})
    assert audited['errors'] == []
    assert audited['blocked_external'] == 1 and audited['verified'] == 0 and not audited['substantive_complete']
    stored = read(investigation.home(parent) / 'submissions' / (task['task_id']+'.json'))
    proof = stored['payload']['population_proofs'][integrated['oid']]
    assert proof['untraceable_product_ids'] == [CAP]
    assert proof['cohort']['not_assessed'] == 0 and proof['computed_impact']['units']['unexplained'] == 100


def test_placeholder_entity_cannot_submit_a_forged_verified_status(integrated, db):
    answer = blocked_submission(integrated, db, '0')
    answer['population_proof']['entity_assessments'][1]['status'] = 'verified'
    write_submission(integrated)
    with pytest.raises(ValueError, match='不可追踪'):
        submit(integrated)
    assert investigation.task_records(integrated['parent'])[integrated['task']['task_id']]['state'] == 'assigned'
