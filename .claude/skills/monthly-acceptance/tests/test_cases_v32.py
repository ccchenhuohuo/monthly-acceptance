"""Behavioral regression for lossless grouping, machine-first routing and reopening."""
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
import json
import sys
import types

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import acceptance_cases as cases
import acceptance_case_grouping as grouping
import acceptance_investigation as inv
from acceptance_core import Run, digest, read, write


def alias_source(tmp_path, *, row_difference=False, band_difference=False):
    source = object.__new__(grouping.Source)
    source.path = tmp_path
    source.hashes = {}
    source.categories, source.bands = {}, defaultdict(dict)
    source.children, source.raw_paths = defaultdict(set), defaultdict(dict)
    source.movement, source.row_sources = {}, defaultdict(list)
    source.manifest = {'sites': ['US'], 'starts': {'US': '2025-01'}, 'data_month': '2026-07'}
    parent, child = ('A',), ('A', 'leaf')
    for mm in ('2025-07', '2026-06', '2026-07'):
        source.children[('US', (), mm)] = {parent, ('B',)}
        source.children[('US', parent, mm)] = {child}
        for path in ((), parent, child):
            source.categories[('US', path, mm)] = {'spus': 2, 'units': 10, 'amount': 100,
                'nrows': 2, 'priced_spus': 2, 'invalid_rows': 0, 'missing_product_rows': 0}
            source.bands[('US', path, mm)] = {0: {'spus': 2, 'units': 10, 'amount': 100}}
    if row_difference:
        source.categories[('US', parent, '2026-06')]['nrows'] = 3
    if band_difference:
        source.bands[('US', child, '2026-07')] = {1: {'spus': 2, 'units': 10, 'amount': 100}}
    return source


def candidate(ident, path, base='2026-06', comparison='mom'):
    return {'candidate_id': ident, 'site': 'US', 'path': json.dumps(path), 'month': '2026-07',
        'comparison': comparison, 'kind': 'result_change', 'priority': 'high', 'evidence_ids': [],
        'facts': {'base_month': base, 'signals': ['units_change'],
            'metrics': {m: {'base': 2 if m == 'spus' else 100, 'current': 20 if m == 'spus' else 10000,
                'delta': 18 if m == 'spus' else 9900, 'rate': 99.0} for m in ('spus', 'units', 'amount')},
            'spu_movement': {m: {'gross': 9900, 'cancellation': 0} for m in ('units', 'amount')}, 'band_shifts': []}}


def obligation(kind, ident, obj):
    return {'obligation_id': kind + ':' + ident, 'source_type': kind, 'source_id': ident,
        'source_hash': digest(obj), 'scope': inv.scope(obj), 'required_checks': ['scope_baseline', 'counterevidence'],
        'source_evidence_ids': obj.get('evidence_ids', []),
        **({'source_material': obj} if kind not in ('candidate', 'sample') else {})}


def population(source):
    source.candidates = [candidate('mom-parent', ['A']), candidate('mom-child', ['A', 'leaf']),
        candidate('yoy-child', ['A', 'leaf'], '2025-07', 'yoy')]
    for mm in ('2026-01', '2026-02', '2026-04'):
        source.candidates.append({'candidate_id': 'gap-' + mm, 'site': 'US', 'path': '["raw","leaf","","","",""]',
            'month': mm, 'comparison': None, 'kind': 'raw_presence_gap', 'evidence_ids': []})
    source.raw_paths[('US', ('raw', 'leaf', '', '', '', ''))] = {
        '2025-12': {'nrows': 2}, '2026-03': {'nrows': 2}}
    source.samples = [{'sample_id': 'sample-exact', 'site': 'US', 'month': '2026-07', 'path': '["A","leaf"]',
        'product_id': 'p-exact', 'evidence_ids': []}]
    history = {'issue_id': 'cross-platform-exact', 'issue_type': 'cross_platform_identity_unresolved',
        'site': 'US', 'path': '[]', 'months': ['2025-07'], 'product_ids': ['same-id'],
        'sku_keys': [{'platform': 'one', 'product_id': 'same-id', 'sku_id': 'sku-one'},
                     {'platform': 'two', 'product_id': 'same-id', 'sku_id': 'sku-two'}]}
    return [obligation('candidate', c['candidate_id'], c) for c in source.candidates] + [
        obligation('sample', s['sample_id'], s) for s in source.samples] + [
        obligation('historical', history['issue_id'], history),
        obligation('history_source', 'source', {'site': 'global', 'path': '[]', 'month': '2026-07'}),
        obligation('project', 'special', {'site': 'US', 'path': '[]', 'month': '2026-07'})]


@pytest.fixture
def case_run(tmp_path, monkeypatch):
    policy = {}; manifest = {'run_id': 'case-fixture', 'method_version': '3.2.0',
        'inputs': {}, 'policy': policy, 'policy_hash': digest(policy),
        'sites': ['US'], 'starts': {'US': '2025-01'}, 'data_month': '2026-07'}
    write(tmp_path / 'run.json', manifest)
    run = Run(tmp_path); run.event('fixture.created')
    source = alias_source(tmp_path); obs = population(source)
    source.manifest = manifest
    monkeypatch.setattr(cases, 'Source', lambda r: source)
    monkeypatch.setattr(inv, 'plan_data', lambda r: {'coordinator_id': 'main', 'obligations': obs})
    monkeypatch.setattr(inv, 'all_obligations', lambda r, p=None: {o['obligation_id']: o for o in obs})
    def coordinator(r, actor):
        if actor != 'main':
            raise ValueError('Only main coordinator')
        return {'coordinator_id': 'main'}
    monkeypatch.setattr(inv, 'coordinator_only', coordinator)
    state = cases.prepare(run)
    return run, state, obs, source


def test_alias_requires_both_periods_and_physical_population(tmp_path):
    source = alias_source(tmp_path, row_difference=True)
    assert source.scope_for_pair('US', ('A', 'leaf'), '2026-06', '2026-07')[0] == ('A', 'leaf')


def test_different_price_bands_cannot_alias(tmp_path):
    source = alias_source(tmp_path, band_difference=True)
    assert source.alias_parent('US', ('A', 'leaf'), '2026-07') is None


def test_missing_price_evidence_cannot_alias(tmp_path):
    source = alias_source(tmp_path)
    source.bands.clear()
    assert source.alias_parent('US', ('A', 'leaf'), '2026-07') is None


def test_unrelated_equal_aggregate_cannot_alias(tmp_path):
    source = alias_source(tmp_path)
    assert source.alias_parent('US', ('A',), '2026-07') is None


def test_same_month_mom_yoy_keep_exact_comparisons_and_obligations(case_run):
    _, state, obs, _ = case_run
    result = next(c for c in state['cases'] if c['kind'] == 'result')
    assert len(result['obligation_ids']) == 3
    assert {p['base_month'] for p in result['comparisons']} == {'2025-07', '2026-06'}
    assert cases.verify_registry(state, obs)
    assert result['states']['cause'] == 'unexplained'
    assert not result['closure_eligible']


def test_mom_evidence_cannot_replace_yoy_obligation(case_run):
    _, state, obs, _ = case_run
    result = next(c for c in state['cases'] if c['kind'] == 'result')
    result['comparisons'] = [p for p in result['comparisons'] if p['comparison'] == 'mom']
    with pytest.raises(ValueError, match='comparison'):
        cases.verify_registry(state, obs)


def test_recovered_gaps_split_and_seed_before_each_episode(case_run):
    _, state, _, _ = case_run
    gaps = [p for p in state['collection_packages'] if p['kind'] == 'raw_presence_gap']
    assert len(gaps) == 2
    assert {p['seed_baseline_month'] for p in gaps} == {'2025-12', '2026-03'}
    assert {tuple(p['gap_months']) for p in gaps} == {('2026-01', '2026-02'), ('2026-04',)}
    assert all(not p['recovery_substitutes_baseline'] for p in gaps)


def test_history_source_and_special_independent_of_threshold(case_run):
    _, state, _, _ = case_run
    for kind in cases.PROTECTED_TYPES:
        group = next(c for c in state['cases'] if c['kind'] == kind)
        assert len(group['obligation_ids']) == 1
        assert group['collection_priority'] == 'priority'
        assert group['route'] == 'awaiting_evidence'
    member = state['members']['historical:cross-platform-exact']
    assert member['months'] == ['2025-07']
    assert member['sku_keys'][0]['platform'] != member['sku_keys'][1]['platform']
    assert 'sku-one' in member['entity_ids'] and 'sku-two' in member['entity_ids']


def test_all_obligations_have_unique_destination(case_run):
    _, state, obs, _ = case_run
    state['obligation_case_map'].pop()
    with pytest.raises(ValueError, match='mapping'):
        cases.verify_registry(state, obs)


def test_preparation_does_not_dispatch_on_high_observed_salience(case_run):
    _, state, _, _ = case_run
    result = next(c for c in state['cases'] if c['kind'] == 'result')
    assert result['features']['max_impact_score'] > .02
    assert result['collection_priority'] == 'priority'
    assert result['route'] == 'awaiting_evidence'


def test_user_explained_boolean_cannot_route_or_close(case_run):
    run, _, _, _ = case_run
    with pytest.raises(ValueError, match='program-verified'):
        cases.route(run, [{'explained': True, 'complete': True}], actor='main')
    assert not any(row['business_verified'] for row in cases.load(run)['obligation_case_map'])


def test_budget_exhaustion_preserves_queue_not_external_block(case_run):
    run, state, _, _ = case_run
    pending = {c['case_id'] for c in state['cases']}
    updated = cases.route(run, actor='main', budget_exhausted=True, reason='No model capacity remains in this trial')
    assert {c['case_id'] for c in updated['cases']} == pending
    assert all(c['route'] == 'awaiting_evidence' for c in updated['cases'])
    assert updated['budget']['state'] == 'exhausted'
    assert cases.summary(updated)['external_evidence_blocked'] == 0


def test_machine_recomputed_but_unbounded_residual_needs_investigation(case_run, monkeypatch):
    run, state, _, _ = case_run
    package = next(p for p in state['collection_packages'] if p['kind'] == 'result')
    receipt = {'package_id': package['package_id'], 'member_hash': package['member_hash'],
        'obligation_ids': package['obligation_ids'], 'spec': deepcopy(package),
        'computed_member_hash': digest(sorted(package['members'], key=cases.canonical)), 'complete': True, 'risk_bounded': False,
        'residual': {m: None for m in cases.METRICS}}
    import acceptance_machine
    monkeypatch.setattr(acceptance_machine, 'verified_diagnostics', lambda r: [receipt])
    updated = cases.route(run, actor='main')
    result = next(c for c in updated['cases'] if c['kind'] == 'result')
    assert result['route'] == 'needs_investigation'
    assert result['states']['review'] == 'pending'
    assert not result['investigation_complete']


def test_incomplete_machine_receipt_not_counted_as_completed(case_run, monkeypatch):
    run, state, _, _ = case_run
    package = next(p for p in state['collection_packages'] if p['kind'] == 'result')
    receipt = {'package_id': package['package_id'], 'member_hash': package['member_hash'],
        'obligation_ids': package['obligation_ids'], 'spec': deepcopy(package),
        'computed_member_hash': digest(sorted(package['members'], key=cases.canonical)), 'complete': False, 'risk_bounded': True}
    import acceptance_machine
    monkeypatch.setattr(acceptance_machine, 'verified_diagnostics', lambda r: [receipt])
    updated = cases.route(run, actor='main')
    assert cases.summary(updated)['machine_complete_cases'] == 0


def test_registry_hash_rewrite_without_event_rejected(case_run):
    run, _, _, _ = case_run
    value = read(cases.home(run) / 'state.json')
    value['payload']['cases'][0]['route'] = 'verified'
    value['sha256'] = digest(value['payload'])
    write(cases.home(run) / 'state.json', value)
    with pytest.raises(ValueError, match='version event'):
        cases.load(run)


def test_counterexample_split_keeps_every_original_obligation_and_shared_collection(case_run):
    run, state, obs, _ = case_run
    result = next(c for c in state['cases'] if c['kind'] == 'result')
    eid = run.evidence({'counterexample': 'One comparison has a different mechanism'}, kind='counterexample')
    updated = cases.split(run, 'main', result['case_id'], ['candidate:yoy-child'], 'YoY alternative mechanism', [eid])
    assert cases.verify_registry(updated, obs)
    descendants = [c for c in updated['cases'] if c['kind'] == 'result']
    assert len(descendants) == 2 and all(c['reopened'] for c in descendants)
    assert len({p for c in descendants for p in c['collection_ids']}) == 1
    assert sum(len(c['obligation_ids']) for c in descendants) == 3
    assert cases.audit(run)['errors']


def test_reopening_cannot_keep_existing_approval(case_run):
    run, state, _, _ = case_run
    result = next(c for c in state['cases'] if c['kind'] == 'result')
    eid = run.evidence({'counterevidence': 'changed identity evidence'}, kind='counterexample')
    updated = cases.reopen(run, 'main', result['case_id'], 'Identity contradicts the earlier mechanism', [eid])
    reopened = next(c for c in updated['cases'] if c['case_id'] == result['case_id'])
    assert reopened['case_version'] == 2
    assert reopened['review_status'] == 'invalidated'
    assert reopened['route'] == 'awaiting_evidence'


def test_source_hash_mismatch_rejects_replay_bridge(tmp_path):
    source = alias_source(tmp_path); obs = population(source)
    bridge = {'objects': {'candidate': {c['candidate_id']: ['wrong'] for c in source.candidates},
                          'sample': {s['sample_id']: [] for s in source.samples}}}
    with pytest.raises(ValueError, match='does not reproduce'):
        cases.build_registry(source, obs, bridge=bridge)


def test_input_order_does_not_change_mapping_or_comparisons(tmp_path):
    source = alias_source(tmp_path); obs = population(source)
    first = cases.build_registry(source, obs)
    source.candidates.reverse(); source.samples.reverse(); obs.reverse()
    second = cases.build_registry(source, obs)
    assert first['obligation_case_map'] == second['obligation_case_map']
    assert first['cases'] == second['cases']


def test_no_rule_proof_can_be_fabricated_with_explained_boolean(case_run):
    run, _, obs, _ = case_run
    with pytest.raises(ValueError, match='Invalid rule proof'):
        cases.verify_rule_fulfillment(run, obs[0], {'explained': True})


def test_unsupported_rule_cannot_bypass_maintained_predicate(case_run):
    run, _, _, _ = case_run
    with pytest.raises(ValueError, match='maintained machine predicate'):
        cases.shadow_rule(run, 'main', {'rule_id': 'unsafe', 'version': '1', 'mechanism': 'model_explained_all'})


def test_machine_receipt_changed_after_routing_invalidates_case_audit(case_run, monkeypatch):
    run, state, _, _ = case_run
    package = next(p for p in state['collection_packages'] if p['kind'] == 'result')
    receipt = {'package_id': package['package_id'], 'member_hash': package['member_hash'],
        'obligation_ids': package['obligation_ids'], 'spec': deepcopy(package),
        'computed_member_hash': digest(sorted(package['members'], key=cases.canonical)), 'complete': True, 'risk_bounded': False,
        'diagnosis_id': 'diag-fixture', 'members': [], 'residual_by_member': {}}
    import acceptance_machine
    monkeypatch.setattr(acceptance_machine, 'verified_diagnostics', lambda r: [receipt])
    cases.route(run, actor='main')
    receipt['complete'] = False
    assert any('receipts changed' in e for e in cases.audit(run)['errors'])


def test_acknowledge_cannot_approve_unreviewed_obligations(case_run):
    run, state, _, _ = case_run
    with pytest.raises(ValueError, match='Every original obligation'):
        cases.acknowledge_review(run, 'main', state['cases'][0]['case_id'])


def _shadow_fixture(case_run):
    run, state, obs, source = case_run
    candidate = {'candidate_id': 'contract-one', 'site': 'US', 'path': '["Contract"]',
        'month': '2026-07', 'kind': 'contract_scope_missing', 'comparison': None, 'evidence_ids': []}
    source.candidates.append(candidate)
    obs.append(obligation('candidate', 'contract-one', candidate))
    path = ('raw', 'leaf', '', '', '', '')
    raw_row = {'nrows': 2, 'month_dt': '2026-07-01', 'path': json.dumps(path)}
    source.raw_paths[('US', path)]['2026-07'] = raw_row
    eid = run.evidence({'response': {'success': True, 'data': [raw_row], 'row_count': 1}}, kind='query-result')
    source.row_sources[('raw_paths', 'US', path, '2026-07')] = [eid]
    updated = cases.build_registry(source, obs)
    updated['version'] = state['version']
    cases._persist(run, updated, 'synthetic_rule_fixture', 'main')
    definition = {'rule_id': 'prefix', 'version': '1', 'mechanism': 'contract_prefix_presence',
        'scope': {'site': 'US', 'contract_path': ['Contract'], 'observed_prefix': ['raw', 'leaf']}}
    return run, cases.shadow_rule(run, 'main', definition)


def test_shadow_rule_proves_only_observed_prefix_not_contract_or_sales(case_run):
    run, rule = _shadow_fixture(case_run)
    assert rule['state'] == 'shadow'
    assert all(r['checks_proven'] == ['scope_baseline'] for r in rule['results'])
    assert all('does not prove contract equivalence' in r['proof_boundary'] for r in rule['results'])
    assert cases.summary(cases.load(run))['business_closed_by_rules'] == 0
    with pytest.raises(ValueError, match='independent control validation'):
        cases.apply_rule(run, 'main', 'prefix', '1')


def test_rule_author_cannot_self_validate(case_run):
    run, rule = _shadow_fixture(case_run)
    with pytest.raises(ValueError, match='cannot independently validate'):
        cases.validate_rule(run, 'main', 'prefix', '1', {'shadow_sha256': rule['shadow_sha256']})


def test_rule_needs_historical_counterexamples_and_actual_exceptions(case_run):
    run, rule = _shadow_fixture(case_run)
    with pytest.raises(ValueError, match='Historical counterexamples and actual exceptions'):
        cases.validate_rule(run, 'independent-worker', 'prefix', '1', {
            'shadow_sha256': rule['shadow_sha256'],
            'checked_obligation_ids': [r['obligation_id'] for r in rule['results']],
            'alternative_explanations': 'Prefix presence cannot establish taxonomy equivalence.',
            'remaining_uncertainty': 'No semantic contract authority is available.', 'controls': []})


def test_opaque_hash_cannot_replace_original_machine_members(case_run, monkeypatch):
    run, state, _, _ = case_run
    package = next(p for p in state['collection_packages'] if p['kind'] == 'result')
    spec = deepcopy(package)
    spec['members'][0]['base_month'] = '2026-05'
    receipt = {'package_id': package['package_id'], 'member_hash': package['member_hash'],
        'obligation_ids': package['obligation_ids'], 'spec': spec, 'computed_member_hash': digest(sorted(spec['members'], key=cases.canonical)),
        'complete': True, 'risk_bounded': False, 'diagnosis_id': 'diag-shifted', 'members': []}
    import acceptance_machine
    monkeypatch.setattr(acceptance_machine, 'verified_diagnostics', lambda r: [receipt])
    with pytest.raises(ValueError, match='original collection members'):
        cases.route(run, actor='main')


@pytest.mark.parametrize('remove_all', [True, False])
def test_missing_logical_collection_packages_cannot_pass_mapping(case_run, remove_all):
    _, state, obs, _ = case_run
    if remove_all:
        state['collection_packages'] = []
    else:
        state['collection_packages'].pop()
    with pytest.raises(ValueError, match='exactly one complete logical collection'):
        cases.verify_registry(state, obs)


def test_wrong_collection_reverse_reference_rejected(case_run):
    _, state, obs, _ = case_run
    state['collection_packages'][0]['case_ids'] = ['wrong-case-id']
    with pytest.raises(ValueError, match='case reverse references'):
        cases.verify_registry(state, obs)


def test_case_cannot_point_to_another_collection_package(case_run):
    _, state, obs, _ = case_run
    state['cases'][0]['collection_ids'] = ['missing-package']
    with pytest.raises(ValueError, match='Case collection reverse references'):
        cases.verify_registry(state, obs)


def test_overlapping_logical_packages_do_not_double_count_original_members(case_run):
    _, state, obs, _ = case_run
    duplicate = deepcopy(state['collection_packages'][0])
    duplicate['package_id'] = 'distinct-but-duplicate-population'
    state['collection_packages'].append(duplicate)
    with pytest.raises(ValueError, match='exactly one complete logical collection'):
        cases.verify_registry(state, obs)


def test_rule_validation_cannot_be_replaced_after_shadow(case_run):
    run, rule = _shadow_fixture(case_run)
    state = cases.load(run)
    stored = state['rules']['prefix@1']
    stored['validation'] = {'reviewer_id': 'independent-worker', 'controls': [], 'result': 'different'}
    stored['validation_sha256'] = digest({'reviewer_id': 'independent-worker', 'controls': [], 'result': 'original'})
    with pytest.raises(ValueError, match='Independent rule validation changed'):
        cases._rule_recheck(run, state, stored)


@pytest.mark.parametrize('value', [None, True, False, '0.02', float('nan'), float('inf'), 0, -.01, 1.01])
def test_collection_priority_threshold_rejects_invalid_configuration(tmp_path, value):
    source = alias_source(tmp_path); obs = population(source)
    with pytest.raises(ValueError, match='collection_priority_threshold'):
        cases.build_registry(source, obs, ordering_threshold=value)


@pytest.mark.parametrize('configuration,expected', [
    ({'collection_priority_threshold': .3}, .3),
    ({'collection_ordering_threshold': .4}, .4),
    ({'collection_priority_threshold': .5, 'collection_ordering_threshold': .1}, .5),
    ({'collection_priority_threshold': 1}, 1),
])
def test_prepare_honors_primary_threshold_and_legacy_fallback(case_run, tmp_path, configuration, expected, monkeypatch):
    run, _, _, source = case_run
    target = tmp_path / 'new-threshold-run'
    manifest = deepcopy(run.manifest)
    manifest.update(policy={'case_workflow': configuration}, run_id='threshold-fixture')
    manifest['policy_hash'] = digest(manifest['policy'])
    write(target / 'run.json', manifest)
    fresh = Run(target); fresh.event('fixture.created')
    copied = deepcopy(source); copied.path = fresh.path; copied.manifest = manifest
    monkeypatch.setattr(cases, 'Source', lambda r: copied)
    state = cases.prepare(fresh)
    assert state['ordering_threshold'] == expected
    assert all(c['route'] == 'awaiting_evidence' for c in state['cases'])


def test_invalid_primary_threshold_cannot_fall_back_silently(case_run):
    run, _, _, _ = case_run
    run.policy['case_workflow'] = {'collection_priority_threshold': 'invalid', 'collection_ordering_threshold': .02}
    with pytest.raises(ValueError, match='collection_priority_threshold'):
        cases.prepare(run)


# Use the actual substantive submission/review fixture, not fabricated approved
# booleans, to exercise the cross-layer acknowledgement contract.
from test_review_v32 import v32, bounded_case, submit, approval


def _prepare_review_cases(case, monkeypatch):
    run = case['run']
    source = alias_source(run.path)
    source.manifest = run.manifest
    source.candidates = read(run.path / 'candidates.json')
    source.samples = read(run.path / 'samples.json')
    monkeypatch.setattr(cases, 'Source', lambda r: source)
    state = cases.prepare(run)
    return next(c['case_id'] for c in state['cases'] if case['ob']['obligation_id'] in c['obligation_ids'])


def _reviewed_case(case, monkeypatch):
    cid = _prepare_review_cases(case, monkeypatch)
    submit(case)
    inv.review(case['run'], '/root', case['task']['task_id'], approval(case))
    return cid


def test_acknowledge_preserves_actual_reviewed_residual_and_seven_dimensions(v32, monkeypatch):
    case = bounded_case(v32)
    cid = _reviewed_case(case, monkeypatch)
    updated = cases.acknowledge_review(case['run'], '/root', cid)
    found = next(c for c in updated['cases'] if c['case_id'] == cid)
    oid = case['ob']['obligation_id']
    assert found['route'] == 'reviewed_residual' and found['investigation_complete']
    assert found['states']['investigation'] == 'completed'
    assert found['states']['cause'] == 'partially_explained'
    assert found['states']['business_acceptance'] == 'qualified'
    assert found['review_receipts'][0]['status_dimensions'] == case['answer']['status_dimensions']
    assert found['status_dimensions_by_obligation'][oid]['review'] == 'approved'
    assert found['reviewed_impact_by_obligation'][oid]['impact']['amount']['unexplained'] == .05
    assert found['reviewed_impact_by_obligation'][oid]['residual_boundary']['upper_bounds']['amount'] == .1
    assert found['residual']['amount'] is None  # no fabricated machine residual
    assert cases.summary(updated)['reviewed_residual_cases'] == 1
    assert not cases.summary(updated)['release_eligible']
    rerouted = cases.route(case['run'], actor='/root')
    assert next(c for c in rerouted['cases'] if c['case_id'] == cid)['route'] == 'reviewed_residual'
    assert not cases.audit(case['run'])['errors']


def test_acknowledge_zero_residual_review_keeps_completed_separate_from_cause(v32, monkeypatch):
    cid = _reviewed_case(v32, monkeypatch)
    updated = cases.acknowledge_review(v32['run'], '/root', cid)
    found = next(c for c in updated['cases'] if c['case_id'] == cid)
    assert found['route'] == 'reviewed'
    assert found['states']['investigation'] == 'completed'
    assert found['states']['cause'] == 'explained'
    assert not found['reviewed_residual']


def test_acknowledge_rechecks_real_raw_review_even_with_new_valid_storage_hash(v32, monkeypatch):
    cid = _reviewed_case(v32, monkeypatch)
    run = v32['run']; task = inv.task_records(run)[v32['task']['task_id']]
    path = inv.home(run) / 'reviews' / (task['task_id'] + '.json')
    review = read(path)
    review['obligations'][0]['raw_evidence_checks'] = []
    write(path, review)
    task['review_sha256'] = digest(review)
    inv.save_task(run, task, 'synthetic_formal_approval_attack')
    with pytest.raises(ValueError):
        cases.acknowledge_review(run, '/root', cid)
    assert next(c for c in cases.load(run)['cases'] if c['case_id'] == cid)['review_status'] == 'pending'


def test_acknowledge_propagates_stale_worker_submission(v32, monkeypatch):
    cid = _reviewed_case(v32, monkeypatch)
    path = v32['worker'].path / 'submission.json'
    payload = read(path); payload['answers'][0]['conclusion'] = 'Changed after approval'
    write(path, payload)
    with pytest.raises(ValueError):
        cases.acknowledge_review(v32['run'], '/root', cid)


def test_old_review_cannot_clear_case_reopen(v32, monkeypatch):
    cid = _reviewed_case(v32, monkeypatch)
    run = v32['run']; cases.acknowledge_review(run, '/root', cid)
    eid = run.evidence({'synthetic_counterexample': 'Reopen the actual approved fixture.'}, kind='synthetic_counterexample')
    cases.reopen(run, '/root', cid, 'New specific synthetic counterexample', [eid])
    with pytest.raises(ValueError, match='current review bound'):
        cases.acknowledge_review(run, '/root', cid)
    found = next(c for c in cases.load(run)['cases'] if c['case_id'] == cid)
    assert found['reopened'] and found['status_dimensions_by_obligation'] == {}


def _independent_control_fixture(case_run, monkeypatch, *, wrong_actual_sql=False):
    run, rule = _shadow_fixture(case_run)
    _, _, obs, _ = case_run
    paths = [('raw', 'leaf', label, '', '', 'named-missing') for label in ('historical', 'actual')]
    controls = []
    for kind, source_type, path in zip(('historical_counterexample', 'actual_exception'), ('historical', 'project'), paths):
        obj = {'site': 'US', 'path': json.dumps(path), 'months': ['2026-07'], 'issue_id': 'control-' + kind}
        ob = obligation(source_type, obj['issue_id'], obj); obs.append(ob)
        controls.append({'kind': kind, 'obligation_id': ob['obligation_id'], 'worker_task_id': 'independent-controls',
            'control_path': list(path), 'months': ['2026-07'], 'baseline_month': '2026-06',
            'mechanism': 'prefix_present_named_leaf_absent', 'expected_presence': False})
    cases.prepare(run)
    target = run.path / 'control-worker'
    manifest = deepcopy(run.manifest)
    policy = read(Path(__file__).resolve().parents[1] / 'policies' / 'default.json')
    policy['page_size'] = 100
    policy.pop('architecture', None); policy['version'] = manifest['method_version']
    manifest.update(policy=policy, policy_hash=digest(policy), run_id='independent-control-fixture')
    write(target / 'run.json', manifest)
    worker = Run(target); worker.event('fixture.created')
    from acceptance_queries import plan, paginated
    job = next(j for j in plan(worker.manifest) if j['family'] == 'raw_paths')
    rows = [{'month_dt': '2026-06-01', 'path': json.dumps(path), 'nrows': 2, 'total_rows': 3} for path in paths]
    rows.append({'month_dt': '2026-07-01', 'path': '["raw","leaf","present","","","present"]', 'nrows': 2, 'total_rows': 3})
    sql = paginated(job, worker.policy['page_size'], 0)
    eid = worker.evidence({'request': {'sql': sql, 'query_hash': digest(sql), 'offset': 0, 'page_size': worker.policy['page_size']},
        'response': {'success': True, 'data': rows, 'row_count': len(rows),
                     'metadata': {'query': 'SELECT * FROM unrelated_country' if wrong_actual_sql else sql}}}, kind='synthetic_query')
    write(worker.path / 'records' / ('job-' + job['job_id'] + '.json'),
        {'execution_status': 'success', 'sql_hash': digest(job['sql']), 'key_fields': job['key_fields'],
         'evidence_ids': [eid], 'expected_rows': len(rows)})
    task = {'task_id': 'independent-controls', 'agent_id': 'independent-worker', 'worker_dir': 'control-worker',
        'obligation_ids': [c['obligation_id'] for c in controls]}
    monkeypatch.setattr(inv, 'task_records', lambda r: {task['task_id']: task})
    for control in controls:
        control['job_id'] = job['job_id']
    validation = {'shadow_sha256': rule['shadow_sha256'], 'checked_obligation_ids': [r['obligation_id'] for r in rule['results']],
        'checked_evidence_ids': sorted({eid for r in rule['results'] for eid in r['evidence_ids']}),
        'alternative_explanations': 'Prefix occurrence does not establish delivery of a named missing leaf.',
        'remaining_uncertainty': 'No semantic equivalence or business cause is proved.', 'controls': controls}
    return run, rule, validation


def test_related_independent_controls_prove_only_literal_rule_boundary(case_run, monkeypatch):
    run, _, validation = _independent_control_fixture(case_run, monkeypatch)
    rule = cases.validate_rule(run, 'independent-worker', 'prefix', '1', validation)
    assert rule['state'] == 'independently_validated'
    assert all(c['verified_control_observations'][0]['leaf_present'] is False for c in rule['validation']['controls'])
    proofs = cases.apply_rule(run, 'main', 'prefix', '1')
    assert proofs and cases.summary(cases.load(run))['business_closed_by_rules'] == 0


def test_rule_control_requires_actual_database_sql_not_just_job_label(case_run, monkeypatch):
    run, _, validation = _independent_control_fixture(case_run, monkeypatch, wrong_actual_sql=True)
    with pytest.raises(ValueError, match='typed job verification'):
        cases.validate_rule(run, 'independent-worker', 'prefix', '1', validation)
    assert cases.load(run)['rules']['prefix@1']['state'] == 'shadow'


@pytest.mark.parametrize('attack,message', [('country', 'same rule country'), ('path', 'exact original raw leaf'),
    ('months', 'full original months'), ('mechanism', 'No maintained predicate'), ('baseline', 'latest actual pre-gap baseline')])
def test_irrelevant_or_unproven_rule_controls_cannot_validate(case_run, monkeypatch, attack, message):
    run, rule, validation = _independent_control_fixture(case_run, monkeypatch)
    state = cases.load(run); control = deepcopy(validation['controls'][0])
    if attack == 'country': state['members'][control['obligation_id']]['site'] = 'JP'
    elif attack == 'path': control['control_path'] = ['other', 'leaf', '', '', '', 'missing']
    elif attack == 'months': control['months'] = ['2025-07']
    elif attack == 'mechanism': control['mechanism'] = 'model_says_mapping_equivalent'
    elif attack == 'baseline': control['baseline_month'] = '2026-05'
    with pytest.raises(ValueError, match=message):
        cases._verify_rule_control(run, state, rule, control, 'independent-worker')


def test_rule_control_revalidation_reexecutes_typed_job_verifier(case_run, monkeypatch):
    run, _, validation = _independent_control_fixture(case_run, monkeypatch)
    rule = cases.validate_rule(run, 'independent-worker', 'prefix', '1', validation)
    import acceptance_validate
    monkeypatch.setattr(acceptance_validate, 'verify_job', lambda r, j: ['synthetic page changed'])
    with pytest.raises(ValueError, match='typed job verification'):
        cases._rule_recheck(run, cases.load(run), rule)
