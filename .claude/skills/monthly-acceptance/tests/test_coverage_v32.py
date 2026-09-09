"""Behavioral regressions for independent scope, historical recall and sampling."""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from acceptance_core import Run,  digest, merge, read, write
from legacy_fixture import create_run
from acceptance_queries import plan
from acceptance_analysis import analyze
import acceptance_coverage as coverage
from test_execution import rows_for, record


def _make_run(tmp_path, sites=('DE',), history=False, two_paths=False, frozen=True, history_variant=None, path_gap=False):
    base = tmp_path / '验收'
    base.mkdir()
    (base / 'config.yaml').write_text('站点起始月份: {' + ', '.join(s + ': 2024-01' for s in sites) + '}\n')
    (base / '项目范围.md').write_text('Frozen camera categories')
    override = {'version': '3.2.0', 'page_size': 1,
                'coverage_gate': {'enabled': True, 'no_signal_samples_per_stratum': 1}}
    write(base / 'policy-overrides.json', override)
    skill = Path(__file__).resolve().parents[1]
    policy = merge(read(skill / 'policies/default.json'), override)
    policy.pop('architecture', None)
    manifest = {'sites': sorted(sites), 'starts': {s: '2024-01' for s in sites},
                'data_month': '2024-02', 'policy': policy}
    fake = SimpleNamespace(manifest=manifest)
    jobs = plan(manifest)

    def data(job):
        result = rows_for(fake, job)
        if two_paths and job.get('level') == 3 and job['family'] in ('category', 'bands', 'movement'):
            result += [{**row, 'path': '["a","b","d"]'} for row in result]
        if path_gap:
            if job.get('level') == 3 and job['family'] in ('category', 'bands'):
                result = [r for r in result if not (r['path'] == '["a","b","c"]' and r['month_dt'] == '2024-02-01')]
            if job['family'] == 'raw_paths':
                result += [{**row, 'path': '["Cameras"]'} for row in result]
                result = [r for r in result if not (r['path'] == '["Electronics"]' and r['month_dt'] == '2024-02-01')]
        return result

    if history:
        issue = {'issue_id': 'historical-p1', 'site': 'DE',
            'path': '["a","b","c"]', 'sku_keys': [{'platform': 'Amazon', 'product_id': 'p1', 'sku_id': 's1'}],
            'months': ['2024-01'], 'issue_type': 'semantic_conflict', 'status': 'open',
            'evidence_ids': ['frozen-old-report-reference'], 'impact': {'units': 1000},
            'snapshot_sha256': coverage._query_population_hash(jobs, {j['job_id']: data(j) for j in jobs})}
        if history_variant == 'closed':
            issue.update(status='closed', not_applicable_reason='Past repair was verified; this remains an important regression counterexample.')
        if history_variant == 'nested':
            issue.pop('sku_keys')
            issue.pop('months')
            issue['legacy_detail'] = {'months': ['2024-01'], 'legacy_scope': {
                'product_ids': ['p1'], 'sku_ids': ['s1']}}
        if history_variant in ('guard', 'fake_guard'):
            issue.pop('sku_keys')
            issue.pop('months')
            issue['path'] = '[]'
            issue['question'] = 'Verify the NULL-safe path comparison method and independently test the zero-execution counterexample.'
            issue['issue_type'] = 'known_method_defect_regression' if history_variant == 'guard' else 'historical_reported_price_defect_needs_recheck'
        write(base / 'open-issues.json', [issue])
    run = Run(create_run(base, skill, '2024-02'))
    run.register_plan(plan(run.manifest))
    coverage.prepare(run)
    for j in plan(run.manifest):
        record(run, j, data(j))
    analyze(run)
    if frozen and not (run.path / 'coverage-independent.json').exists():
        coverage.freeze_independent(run)
    return run


def _observation(run, *, month='2024-01', path='["a","b","c"]', product='p1', title='Battery wrongly classified'):
    row = {'site': 'DE', 'month_dt': month + '-01', 'platform': 'Amazon',
           'product_id': product, 'sku_id': 's1', 'path': path, 'title': title}
    eid = run.evidence({'rows': [row]}, kind='independent_raw_fixture', source='complete exact identity fixture')
    return {'site': 'DE', 'month': month,
            'object': {'platform': 'Amazon', 'product_id': product, 'sku_id': 's1'},
            'feature': {'code': 'semantic_conflict', 'facts': {'path': path, 'title': title}},
            'assessment': 'The actual title and complete category were checked against the business definition.',
            'evidence_id': eid}


def _review(run, cell, status='checked_clean'):
    obs = _observation(run, month=cell['month'], path=cell['path'])
    job = coverage._sampling_job(run, cell)
    record(run, job, [{'site': 'DE', 'month_dt': cell['month'] + '-01',
        'platform': 'Amazon', 'product_id': 'p1', 'sku_id': 's1', 'path': cell['path'],
        'title': obs['feature']['facts']['title'], 'population_objects': 1, 'sample_rank': 1}])
    obs['evidence_id'] = run.job_record(job['job_id'])['evidence_ids'][0]
    value = {'cell_id': cell['cell_id'], 'status': status, 'investigator_id': 'researcher',
             'observations': [obs]}
    if status == 'missed_signal':
        value['new_rule'] = {'rule_id': 'battery-title', 'version': '1', 'mechanism': 'Battery title needs review',
            'predicate': {'field': 'title', 'op': 'contains', 'value': 'Battery'},
            'regression_requirement': 'Recheck the concrete object and historical false positive before reuse.'}
    if status == 'external_blocked':
        value.update(attempts=['Checked source rows'], missing_evidence='Original manufacturer classification',
                     resume_condition='Manufacturer supplies product category record')
    value['reviewed_hash'] = digest(value)
    value['reviewer_id'] = 'independent-reviewer'
    return value


def _sampling(run):
    return coverage._frozen(run, 'coverage-sampling-plan')


def _compare_empty(run):
    coverage.prepare_history(run)
    coverage.compare_history(run, [])


def test_v31_frozen_runs_keep_legacy_behavior():
    run = SimpleNamespace(policy={'version': '3.1.0'}, manifest={})
    assert coverage.audit(run) == {'enabled': False, 'errors': [], 'scan_complete': True,
                                   'history_complete': True, 'sampling_complete': True}


def test_scope_plan_exists_before_queries_or_signals(tmp_path):
    run = _make_run(tmp_path, sites=('DE', 'US'))
    expected = coverage._frozen(run, 'coverage-plan')['tasks']
    assert {(t['scope']['site'], t['scope']['month']) for t in expected if t['rule_id'] == 'arrival_raw'} == {
        ('DE', '2024-01'), ('DE', '2024-02'), ('US', '2024-01'), ('US', '2024-02')}
    assert len({t['task_id'] for t in expected}) == len(expected)
    assert all(t['not_applicable_reason'] for t in expected if t['applicability'] == 'not_applicable')


def test_omitted_entire_country_cannot_shrink_check_denominator(tmp_path):
    run = _make_run(tmp_path, sites=('DE', 'US'))
    for job in plan(run.manifest):
        if job['site'] == 'US':
            (run.path / 'records' / ('job-' + job['job_id'] + '.json')).unlink()
    result = coverage.audit(run, include_tasks=True)
    missing = [t for t in result['tasks'] if t['scope']['site'] == 'US' and t['rule_id'] == 'arrival_raw']
    assert len(missing) == 2 and all(t['execution_status'] == 'not_executed' for t in missing)
    assert not result['scan_complete']
    assert 'required_queries_incomplete' in result['errors']


def test_omitted_month_is_missing_data_not_success_clean(tmp_path):
    run = _make_run(tmp_path)
    job = next(j for j in plan(run.manifest) if j['family'] == 'fingerprint' and j['entity'] == 'raw')
    record(run, job, [r for r in rows_for(run, job) if r['month_dt'] != '2024-01-01'])
    result = coverage.audit(run, include_tasks=True)
    task = next(t for t in result['tasks'] if t['rule_id'] == 'arrival_raw' and t['scope']['month'] == '2024-01')
    assert task['execution_status'] == 'missing_data'
    assert not result['scan_complete']


@pytest.mark.parametrize('failure', ['missing_page', 'failed', 'empty_evidence_success'])
def test_incomplete_query_cannot_be_marked_clean(tmp_path, failure):
    run = _make_run(tmp_path)
    job = next(j for j in plan(run.manifest) if j['family'] == 'category' and j['level'] == 3)
    rec = run.job_record(job['job_id'])
    if failure == 'missing_page':
        rec['evidence_ids'].pop()
    elif failure == 'failed':
        rec['execution_status'] = 'failed'
    else:
        rec.update(evidence_ids=[], expected_rows=0, row_count=0)
    write(run.path / 'records' / ('job-' + job['job_id'] + '.json'), rec)
    result = coverage.audit(run, include_tasks=True)
    tasks = [t for t in result['tasks'] if t['rule_id'] == 'category_values' and t['scope']['level'] == 3]
    assert tasks and all(t['execution_status'] == 'query_failed' for t in tasks)
    assert not result['scan_complete']


def test_task_tampering_is_detected_even_with_recomputed_local_hash(tmp_path):
    run = _make_run(tmp_path)
    value = read(run.path / 'coverage-plan.json')
    value['payload']['tasks'] = value['payload']['tasks'][1:]
    value['sha256'] = digest(value['payload'])
    write(run.path / 'coverage-plan.json', value)
    result = coverage.audit(run)
    assert any('mandatory_plan_missing_or_invalid' in e for e in result['errors'])
    assert not result['scan_complete']


def test_every_historical_month_has_required_semantic_sampling(tmp_path):
    run = _make_run(tmp_path)
    result = coverage.audit(run, include_tasks=True)
    tasks = [t for t in result['tasks'] if t['rule_id'] == 'no_signal_semantic_sample']
    assert {t['scope']['month'] for t in tasks} == {'2024-01', '2024-02'}
    assert all(t['applicability'] == 'applicable' and t['execution_status'] == 'not_executed' for t in tasks)
    assert not result['scan_complete']
    assert _sampling(run)['stratification'] == ['site', 'month']


def test_same_snapshot_history_miss_is_failed_not_protected_recall(tmp_path):
    run = _make_run(tmp_path, history=True)
    independent = coverage._frozen(run, 'coverage-independent')
    result = coverage.compare_history(run, [{'issue_id': 'historical-p1', 'status': 'protected',
        'independent_candidate_ids': ['some-category-change'],
        'comparability': {'kind': 'same_snapshot', 'snapshot_sha256': independent['query_population_hash']}}])
    assert result['counts'] == {'not_reproduced': 1}
    assert result['independent_rediscovery_count'] == 0
    assert not result['complete']
    assert 'historical_regression_not_reproduced:historical-p1' in result['errors']


def test_closed_historical_defect_and_counterexample_remain_in_required_denominator(tmp_path):
    run = _make_run(tmp_path, history=True, history_variant='closed')
    history = coverage.prepare_history(run)['payload']['items']
    assert len(history) == 1
    assert history[0]['previous_status'] == 'closed'
    assert history[0]['previous_not_applicable_reason']
    result = coverage.compare_history(run, [])
    assert result['expected'] == 1 and not result['complete']


def test_nested_legacy_months_and_typed_ids_are_restored_without_inventing_parent_link(tmp_path):
    run = _make_run(tmp_path, history=True, history_variant='nested')
    issue = coverage.prepare_history(run)['payload']['items'][0]
    assert issue['months'] == ['2024-01']
    assert {digest(o) for o in issue['objects']} == {digest({'product_id': 'p1'}), digest({'sku_id': 's1'})}
    assert not issue['specification_missing']
    assert not any('sku_id' in o and 'product_id' in o for o in issue['objects'])


def test_unproven_snapshot_equivalence_stays_explicitly_pending(tmp_path):
    run = _make_run(tmp_path, history=True)
    result = coverage.compare_history(run, [{'issue_id': 'historical-p1',
        'comparability': {'kind': 'same_snapshot', 'snapshot_sha256': '0' * 64}}])
    assert result['counts'] == {'comparability_pending': 1}
    assert not result['complete'] and result['items'][0]['limitations']


def test_guided_concrete_recheck_is_separate_from_independent_recall(tmp_path):
    run = _make_run(tmp_path, history=True)
    discovery = {'observations': [_observation(run)], 'discovery_id': 'guided-battery'}
    result = coverage.compare_history(run, [{'issue_id': 'historical-p1', 'guided_discoveries': [discovery]}])
    assert result['independent_rediscovery_count'] == 0
    assert result['guided_only_count'] == 1
    assert result['items'][0]['state'] == 'history_guided_discovery'
    with pytest.raises(ValueError, match='不能补写'):
        coverage.freeze_independent(run, [discovery])


def test_wrong_object_month_or_feature_does_not_rediscover_history(tmp_path):
    run = _make_run(tmp_path, history=True)
    for observation in [_observation(run, month='2024-02'), _observation(run, product='different')]:
        result = coverage.compare_history(run, [{'issue_id': 'historical-p1',
            'guided_discoveries': [{'observations': [observation]}]}])
        assert result['guided_only_count'] == 0
    invalid = _observation(run)
    invalid['feature']['code'] = 'ordinary_result_change'
    result = coverage.compare_history(run, [{'issue_id': 'historical-p1',
        'guided_discoveries': [{'observations': [invalid]}]}])
    assert result['guided_only_count'] == 0
    assert result['items'][0]['state'] == 'feature_mapping_pending'
    assert result['items'][0]['unconfirmed_feature_matches']


def test_actual_independent_specific_discovery_counts_with_raw_evidence(tmp_path, monkeypatch):
    # The analysis hook may be enabled by the integration layer. Keep this test
    # expressly before independent freeze while supplying the raw observation.
    saved = coverage.freeze_independent
    monkeypatch.setattr(coverage, 'freeze_independent', lambda *a, **k: {'fixture_deferred': True})
    run = _make_run(tmp_path, history=True, frozen=False)
    monkeypatch.setattr(coverage, 'freeze_independent', saved)
    saved(run, [{'observations': [_observation(run)], 'discovery_id': 'independent-battery'}])
    result = coverage.compare_history(run, [{'issue_id': 'historical-p1'}])
    assert result['independent_rediscovery_count'] == 1
    assert result['guided_only_count'] == 0


def test_investigation_history_entry_prevents_backfilled_independent_discovery(tmp_path):
    run = _make_run(tmp_path, history=True, frozen=False)
    run.event('investigation.plan.frozen', coordinator_id='main', plan_hash='historical-entry-used')
    with pytest.raises(ValueError, match='不能补写'):
        coverage.freeze_independent(run, [{'observations': [_observation(run)]}])


def test_existing_but_unrelated_raw_evidence_cannot_support_discovery(tmp_path):
    run = _make_run(tmp_path, history=True)
    obs = _observation(run)
    obs['object']['product_id'] = 'unsupported-product'
    with pytest.raises(ValueError, match='无关'):
        coverage.compare_history(run, [{'issue_id': 'historical-p1',
            'guided_discoveries': [{'observations': [obs]}]}])


def test_old_fixed_sample_infers_only_unique_exact_month_from_actual_sql(tmp_path):
    run = _make_run(tmp_path, history=True)
    obs = _observation(run)
    row = run.get_evidence(obs['evidence_id'])['rows'][0]
    row.pop('month_dt')
    row.pop('site')
    obs['evidence_id'] = run.evidence({'request': {'sql':
        "SELECT product_id,sku_id,path,title FROM original_rows WHERE site='DE' AND month_dt='2024-01-01'"},
        'response': {'data': [row]}}, kind='old_fixed_sample')
    assert coverage._observations(run, {'observations': [obs]}) == [obs]
    for condition in ["month_dt BETWEEN '2024-01-01' AND '2024-02-01'",
                      "month_dt='2024-01-01' OR month_dt='2024-02-01'",
                      "month_dt IN ('2024-01-01','2024-02-01')"]:
        obs['evidence_id'] = run.evidence({'request': {'sql':
            "SELECT product_id,sku_id,path,title FROM original_rows WHERE site='DE' AND " + condition},
            'response': {'data': [row]}}, kind='nonunique_month')
        with pytest.raises(ValueError, match='无关'):
            coverage._observations(run, {'observations': [obs]})


def test_clean_sampling_can_finish_without_claiming_full_semantic_population(tmp_path):
    run = _make_run(tmp_path, two_paths=True)
    sampling = _sampling(run)
    selected = set(sampling['selected_cell_ids'])
    for cell in sampling['population']:
        if cell['cell_id'] in selected:
            coverage.record_sample_review(run, _review(run, cell))
    _compare_empty(run)
    result = coverage.audit(run)
    assert result['sampling_complete'] and result['scan_complete']
    assert result['sampling']['selected'] == 2
    assert result['sampling']['no_signal_population'] == 4
    assert '不代表全量语义' in result['sampling']['coverage_claim']
    assert coverage.prepare_sampling(run)['payload'] == sampling


def test_missed_signal_expands_required_range_and_cannot_be_erased_by_clean_revision(tmp_path):
    run = _make_run(tmp_path, two_paths=True)
    sampling = _sampling(run)
    cell = next(c for c in sampling['population'] if c['cell_id'] in sampling['selected_cell_ids'])
    result = coverage.record_sample_review(run, _review(run, cell, 'missed_signal'))
    assert result['expected'] == 4 and result['expanded'] == 2
    assert not result['complete']
    assert any('sampling_or_expansion_incomplete' in e for e in result['errors'])
    assert any('regression_rule_pending' in e for e in result['errors'])
    first_requirements = result['finding_obligations']['pending_requirement_ids']
    assert len(first_requirements) == 1
    later = coverage.record_sample_review(run, _review(run, cell, 'checked_clean'))
    assert later['expected'] == 4 and later['missed_signal_count'] == 1
    assert later['finding_obligations']['pending_requirement_ids'] == first_requirements
    assert not later['complete']


def test_missing_data_or_blocked_sampling_is_not_a_completed_review(tmp_path):
    run = _make_run(tmp_path)
    cell = _sampling(run)['population'][0]
    result = coverage.record_sample_review(run, _review(run, cell, 'external_blocked'))
    assert result['verified'] == 0 and not result['complete']


def test_new_rule_must_pass_real_positive_and_counterexample_evidence(tmp_path):
    run = _make_run(tmp_path, two_paths=True)
    sampling = _sampling(run)
    first = next(c for c in sampling['population'] if c['cell_id'] in sampling['selected_cell_ids'])
    coverage.record_sample_review(run, _review(run, first, 'missed_signal'))
    for cell in sampling['population']:
        if cell != first:
            coverage.record_sample_review(run, _review(run, cell))
    eid = run.evidence({'rows': [{'product_id': 'bad', 'title': 'Battery grip'},
                               {'product_id': 'good', 'title': 'Camera lens'}]}, kind='rule_fixture')
    rule = {'rule_id': 'battery-title', 'version': '1', 'author_id': 'rule-author',
        'predicate': {'field': 'title', 'op': 'contains', 'value': 'Battery'},
        'tests': [{'evidence_id': eid, 'selector': {'product_id': 'bad'}, 'expected': True},
                  {'evidence_id': eid, 'selector': {'product_id': 'good'}, 'expected': False}]}
    rule['reviewed_hash'] = digest(rule)
    rule['reviewer_id'] = 'independent-reviewer'
    assert coverage.register_sampling_rule(run, rule)['business_dispositions'] == 0
    result = coverage._sampling_audit(run)
    assert not result['complete'] and result['finding_obligations']['expected'] == 1
    import acceptance_investigation as inv
    inv.prepare(run, '/root')
    assert len(coverage.register_sampling_findings(run, '/root')['registered_obligation_ids']) == 1
    result = coverage._sampling_audit(run)
    assert result['complete'] and result['expected'] == result['verified'] == 4
    tampered = deepcopy(rule)
    tampered['tests'][1]['expected'] = True
    with pytest.raises(ValueError, match='反例'):
        coverage.register_sampling_rule(run, tampered)


def test_review_material_changes_invalidate_sampling_approval(tmp_path):
    run = _make_run(tmp_path)
    cell = _sampling(run)['population'][0]
    review = _review(run, cell)
    coverage.record_sample_review(run, review)
    records = read(run.path / 'coverage-sampling-reviews.json')
    records[cell['cell_id']]['observations'][0]['assessment'] = 'Changed substantive assessment'
    write(run.path / 'coverage-sampling-reviews.json', records)
    result = coverage._sampling_audit(run)
    assert not result['complete'] and any('版本' in e for e in result['errors'])


def test_sample_query_rechecks_historical_month_and_does_not_cross_platform_identity(tmp_path):
    run = _make_run(tmp_path)
    jobs = coverage.sampling_jobs(run)
    assert len(jobs) == 2
    assert {j['month'] for j in jobs} == {'2024-01', '2024-02'}
    assert all(j['key_fields'] == ['platform', 'product_id', 'sku_id'] for j in jobs)
    assert all(j['path'] == '["a","b","c"]' and 'MD5' in j['sql'] for j in jobs)


def test_reviewer_cannot_choose_an_easy_object_instead_of_seeded_sample(tmp_path):
    run = _make_run(tmp_path)
    cell = _sampling(run)['population'][0]
    review = _review(run, cell)
    job = coverage._sampling_job(run, cell)
    rows = run.job_rows(job['job_id'])
    rows.append({**rows[0], 'product_id': 'also-required', 'sku_id': 's2', 'sample_rank': 2, 'population_objects': 2})
    record(run, job, rows)
    with pytest.raises(ValueError, match='全部对象'):
        coverage.record_sample_review(run, review)


def _identity_review(run, cell, *, product='valid-parent', sku='0', platform='Amazon', status='missed_signal'):
    review = _review(run, cell, status='missed_signal' if status == 'missed_signal' else 'checked_clean')
    review['status'] = status
    job = coverage._sampling_job(run, cell)
    rows = run.job_rows(job['job_id'])
    rows[0].update(platform=platform, product_id=product, sku_id=sku)
    record(run, job, rows)
    review['observations'][0]['object'] = {'platform': platform, 'product_id': product, 'sku_id': sku}
    review['observations'][0]['evidence_id'] = run.job_record(job['job_id'])['evidence_ids'][0]
    if status == 'identity_blocked':
        review.update(identity_problem='This exact source row has no usable parent/SKU/platform identity.',
                      attempts=['Verified the full deterministic sample and its exact source row.'],
                      resume_condition='Supply a valid platform-scoped parent or SKU key, preserving this original record.')
    review['reviewed_hash'] = digest({k: v for k, v in review.items() if k not in ('reviewer_id', 'reviewed_hash')})
    return review


@pytest.mark.parametrize('placeholder', ['0', '', ' ', None, 0])
def test_parent_scoped_placeholder_sku_can_be_retained_as_sample_miss_but_never_history_identity(tmp_path, placeholder):
    run = _make_run(tmp_path)
    cell = _sampling(run)['population'][0]
    review = _identity_review(run, cell, sku=placeholder)
    result = coverage.record_sample_review(run, review)
    assert result['reviewed_cells'][cell['cell_id']] == 'missed_signal'
    assert result['identity_scope_by_cell'][cell['cell_id']]['states'] == {'parent_scoped_placeholder_sku': 1}
    assert not result['identity_scope_by_cell'][cell['cell_id']]['cross_parent_tracing_allowed']
    assert run.get_evidence(review['observations'][0]['evidence_id'])['response']['data'][0]['sku_id'] == placeholder
    with pytest.raises(ValueError, match='占位身份'):
        coverage._observations(run, review)


def test_placeholder_sample_cannot_substitute_another_parent_or_omit_the_parent(tmp_path):
    run = _make_run(tmp_path)
    cell = _sampling(run)['population'][0]
    review = _identity_review(run, cell)
    for replacement in ({'platform': 'Amazon', 'product_id': 'another-parent', 'sku_id': '0'},
                        {'platform': 'Amazon', 'sku_id': '0'}):
        attack = deepcopy(review)
        attack['observations'][0]['object'] = replacement
        attack['reviewed_hash'] = digest({k: v for k, v in attack.items() if k not in ('reviewer_id', 'reviewed_hash')})
        with pytest.raises(ValueError):
            coverage.record_sample_review(run, attack)
    attack = deepcopy(review)
    attack['identity_mode'] = 'sample_source_row'
    with pytest.raises(ValueError, match='占位身份'):
        coverage._observations(run, attack)


@pytest.mark.parametrize('product,platform', [(None, 'Amazon'), ('', 'Amazon'), ('0', 'Amazon'), ('valid-parent', None)])
def test_unidentifiable_sample_record_stays_visible_and_incomplete_without_physical_identity(tmp_path, product, platform):
    run = _make_run(tmp_path)
    cell = _sampling(run)['population'][0]
    review = _identity_review(run, cell, product=product, platform=platform, status='identity_blocked')
    result = coverage.record_sample_review(run, review)
    assert result['reviewed_cells'][cell['cell_id']] == 'identity_blocked'
    assert cell['cell_id'] in result['pending_cell_ids'] and not result['complete']
    assert result['identity_scope_by_cell'][cell['cell_id']]['states'] == {'unresolved_source_record': 1}
    attack = deepcopy(review)
    attack['status'] = 'checked_clean'
    attack['reviewed_hash'] = digest({k: v for k, v in attack.items() if k not in ('reviewer_id', 'reviewed_hash')})
    with pytest.raises(ValueError, match='identity_blocked'):
        coverage.record_sample_review(run, attack)


def test_confirmed_path_absence_finishes_execution_without_becoming_clean_or_business_resolved(tmp_path):
    run = _make_run(tmp_path, two_paths=True, path_gap=True)
    sampling = _sampling(run)
    for cell in sampling['population']:
        if cell['cell_id'] in sampling['selected_cell_ids']:
            coverage.record_sample_review(run, _review(run, cell))
    _compare_empty(run)
    result = coverage.audit(run, include_tasks=True)
    gaps = [t for t in result['tasks'] if t['execution_status'] == 'missing_data']
    assert gaps and all(t['execution_completed'] for t in gaps)
    assert all(t['observation_state'] == 'confirmed_path_no_observation' for t in gaps)
    assert all(t['business_issue_state'] == 'not_resolved_by_execution' for t in gaps)
    assert any(t['signal_ids'] for t in gaps)
    assert result['scan_complete'] and result['confirmed_path_absence_checks'] == len(gaps)
    assert result['execution_counts']['missing_data'] == len(gaps)
    assert read(run.path / 'candidates.json')  # Original gap investigation obligations remain.


def _approved_guard(run):
    """A real formal submission and independent arithmetic review of a synthetic method record."""
    import acceptance_investigation as inv
    import acceptance_review as gate
    from test_review_v32 import loc
    coverage.prepare_history(run)
    inv.prepare(run, '/root')
    oid = 'historical:historical-p1'
    task = inv.assign(run, '/root', '/root/method-investigator', [oid])
    ob = inv.all_obligations(run)[oid]
    assert not ob['requires_impact'] and not ob['scope'].get('product_ids')
    worker = Run(run.path / task['worker_dir'])
    row = {'site': 'DE', 'month': '2024-02', 'path': '[]',
           'rule_id': 'null-safe-path', 'checked_queries': 1}
    eid = worker.evidence({'records': [row], 'provenance': 'Explicit synthetic method regression fixture'},
                          kind='synthetic_method_regression')
    locator = loc(eid, row, ob['required_checks'])
    locator['role'] = 'historical_context'
    answer = {'obligation_id': oid, 'scope': deepcopy(ob['scope']),
        'contract_sha256': ob['contract']['sha256'], 'fulfillment_mode': 'direct',
        'conclusion': 'The explicitly synthetic method fixture has one executed NULL-safe regression query.',
        'next_action': 'Retain the complete method record and zero-execution counterexample.',
        'business_verdict': 'pass', 'repair_state': 'not_needed', 'defect_confirmed': False,
        'status_dimensions': {'execution': 'success', 'investigation': 'completed', 'defect': 'not_found',
                             'cause': 'not_applicable', 'impact': 'not_applicable', 'review': 'pending', 'acceptability': 'pass'},
        'checks': {name: {'status': 'verified', 'finding': 'This scoped synthetic query record tests the frozen method question.',
                         'actions': ['Read the exact synthetic method execution record.'],
                         'counterevidence': 'Recalculate the explicit zero-execution alternative.',
                         'evidence_ids': [eid], 'evidence_locators': [deepcopy(locator)]}
                   for name in ob['required_checks']}}
    write(worker.path / 'submission.json', {'task_id': task['task_id'], 'agent_id': task['agent_id'],
        'attempt': task['attempt'], 'answers': [answer]})
    inv.submit(run, '/root', task['task_id'])
    stored = read(inv.home(run) / 'submissions' / (task['task_id'] + '.json'))
    normalized = stored['payload']['answers'][0]
    locator['evidence_id'] = normalized['checks'][ob['required_checks'][0]]['evidence_ids'][0]
    material = gate.material_digest(stored['sha256'], normalized, None, run.get_evidence)
    calculation = {'method': 'sum', 'operands': [{'locator': deepcopy(locator), 'field': 'checked_queries'}],
                   'result': 1, 'meaning': 'Recompute the number of actually executed synthetic method checks.'}
    entry = {'obligation_id': oid, 'decision': 'accept',
        'reason': 'Read the method record and independently test the zero-execution counterexample.',
        'scope_check': 'The original DE method obligation has no product population or data comparison month.',
        'conclusion_scope': ob['scope'], 'evidence_checked': [locator['evidence_id']],
        'material_sha256': material, 'raw_evidence_checks': [deepcopy(locator)],
        'check_reviews': {name: {'decision': 'accept', 'reason': 'Compared the exact synthetic method fields.'}
                          for name in ob['required_checks']},
        'alternative_explanations': [{'hypothesis': 'No query was executed.', 'status': 'rejected_with_evidence',
            'resolution': 'The exact method record counts one execution.', 'evidence_locators': [deepcopy(locator)]}],
        'remaining_uncertainty': {'description': 'Explicit synthetic fixture only.',
                                 'acceptance_effect': 'Does not establish any live data acceptance.'},
        'independent_review': {'reviewer_id': '/root/method-independent', 'material_sha256': material,
            'decision': 'accept', 'method': 'counterexample_recalculation',
            'method_difference': 'Test the numerical zero-execution hypothesis from the raw method record.',
            'raw_evidence_checks': [deepcopy(locator)], 'reason': 'One executed check disproves the zero-execution alternative.',
            'recomputations': [calculation], 'counterexamples': [{'hypothesis': 'The actual execution count is zero.',
                'result': 'Recomputation gives one; zero is false.',
                'test': {'recomputation_index': 0, 'relation': 'eq', 'expected': 0, 'outcome': False},
                'evidence_locators': [deepcopy(locator)]}]}}
    inv.review(run, '/root', task['task_id'], {'reviewer_id': '/root', 'submission_sha256': stored['sha256'],
                                            'obligations': [entry]})
    return task, worker


def test_non_object_history_requires_current_formal_guard_review_without_inventing_a_product(tmp_path):
    run = _make_run(tmp_path, history=True, history_variant='guard')
    item = coverage.prepare_history(run)['payload']['items'][0]
    assert item['regression_kind'] == 'non_object_guard' and not item['objects'] and not item['months']
    assert not item['specification_missing']
    pending = coverage.compare_history(run, [{'issue_id': item['issue_id'], 'state': 'guard_reviewed'}])
    assert pending['counts'] == {'guard_review_pending': 1} and not pending['complete']
    _approved_guard(run)
    result = coverage.compare_history(run, [{'issue_id': item['issue_id']}])
    assert result['complete'] and result['counts'] == {'guard_reviewed': 1}
    assert result['non_object_guard_expected'] == 1 and result['data_issue_expected'] == 0
    assert result['independent_rediscovery_count'] == result['guided_only_count'] == 0
    assert result['items'][0]['guard_review']['independent_reviewer_id'] == '/root/method-independent'


def test_arbitrary_scope_data_issue_cannot_self_label_as_a_non_object_guard(tmp_path):
    run = _make_run(tmp_path, history=True, history_variant='fake_guard')
    result = coverage.compare_history(run, [{'issue_id': 'historical-p1', 'regression_kind': 'non_object_guard',
                                            'state': 'guard_reviewed'}])
    assert result['non_object_guard_expected'] == 0 and result['data_issue_expected'] == 1
    assert result['counts'] == {'specification_pending': 1} and not result['complete']
    assert set(result['items'][0]['specification_missing']) == {'objects', 'months'}


@pytest.mark.parametrize('mutation', ['worker_submission', 'main_review', 'independent_review'])
def test_stale_or_forged_guard_review_does_not_complete_history(tmp_path, mutation):
    import acceptance_investigation as inv
    run = _make_run(tmp_path, history=True, history_variant='guard')
    task, worker = _approved_guard(run)
    path = (worker.path / 'submission.json' if mutation == 'worker_submission'
            else inv.home(run) / 'reviews' / (task['task_id'] + '.json'))
    value = read(path)
    if mutation == 'worker_submission':
        value['answers'][0]['conclusion'] = 'Changed after formal approval.'
    elif mutation == 'main_review':
        value['reviewer_id'] = task['agent_id']
    else:
        value['obligations'][0]['independent_review']['material_sha256'] = '0' * 64
    write(path, value)
    result = coverage.compare_history(run, [{'issue_id': 'historical-p1', 'state': 'guard_reviewed'}])
    assert result['counts'] == {'guard_review_pending': 1} and not result['complete']
    assert result['items'][0]['guard_review']['pending_reason']


def test_sample_miss_requires_coordinator_registration_and_keeps_formal_investigation_unfinished(tmp_path):
    import acceptance_investigation as inv
    run = _make_run(tmp_path)
    cell = _sampling(run)['population'][0]
    sample_review = _identity_review(run, cell)
    sample_state = coverage.record_sample_review(run, sample_review)
    finding = sample_state['finding_obligations']['items'][0]
    assert finding['state'] == 'formal_investigation_pending'
    assert finding['observation']['object']['sku_id'] == '0'
    event = coverage._events(run, 'coverage.sample.reviewed')[-1]
    stored = read(run.path / 'coverage-sampling-history' / (event['sha256'] + '-requirements.json'))
    assert event['finding_requirements_sha256'] == digest(stored)
    inv.prepare(run, '/root')
    with pytest.raises(ValueError, match='主Agent'):
        coverage.register_sampling_findings(run, sample_review['reviewer_id'])
    registered = coverage.register_sampling_findings(run, '/root')
    assert registered['finding_obligations']['complete']
    oid = registered['registered_obligation_ids'][0]
    ob = inv.all_obligations(run)[oid]
    assert ob['source_type'] == 'discovered'
    assert ob['required_checks'] == inv.DISCOVERED_CHECKS['identity']
    assert 'classification_semantics' not in ob['required_checks'] and 'price_volume' in ob['required_checks']
    assert 'brand_contribution' not in ob['required_checks'] and 'spu_contribution' not in ob['required_checks']
    assert ob['scope']['product_ids'] == ['valid-parent'] and 'sku_ids' not in ob['scope']
    assert ob['source_material']['source_observation']['object_keys']['sku_id'] == '0'
    assert not inv.audit(run)['substantive_complete']
    assert coverage.register_sampling_findings(run, '/root')['registered_obligation_ids'] == []


def test_text_claim_or_wrong_observation_hash_cannot_satisfy_a_sample_finding_requirement(tmp_path):
    import acceptance_investigation as inv
    run = _make_run(tmp_path)
    cell = _sampling(run)['population'][0]
    result = coverage.record_sample_review(run, _review(run, cell, 'missed_signal'))
    requirement = result['finding_obligations']['items'][0]
    inv.prepare(run, '/root')
    issue = coverage._finding_issue(requirement)
    issue['coverage_observation_sha256'] = '0' * 64
    assert len(inv.add_obligations(run, '/root', [issue])) == 1
    state = coverage.sampling_finding_requirements(run)
    assert not state['complete'] and state['registered'] == 0
    assert state['pending_requirement_ids'] == [requirement['requirement_id']]


def test_deleted_sample_finding_requirement_artifact_cannot_remove_the_denominator(tmp_path):
    run = _make_run(tmp_path)
    cell = _sampling(run)['population'][0]
    coverage.record_sample_review(run, _review(run, cell, 'missed_signal'))
    event = coverage._events(run, 'coverage.sample.reviewed')[-1]
    (run.path / 'coverage-sampling-history' / (event['sha256'] + '-requirements.json')).unlink()
    result = coverage._sampling_audit(run)
    assert not result['complete'] and result['finding_obligations']['expected'] == 1
    assert any(e.startswith('missed_signal_history_invalid:') for e in result['errors'])


def test_every_missed_source_object_gets_its_own_requirement_and_partial_registration_stays_pending(tmp_path):
    import acceptance_investigation as inv
    run = _make_run(tmp_path)
    cell = _sampling(run)['population'][0]
    review = _review(run, cell, 'missed_signal')
    job = coverage._sampling_job(run, cell)
    rows = run.job_rows(job['job_id'])
    rows[0]['population_objects'] = 2
    rows.append({**rows[0], 'product_id': 'p2', 'sku_id': 's2', 'sample_rank': 2})
    record(run, job, rows)
    observations = []
    for eid in run.job_record(job['job_id'])['evidence_ids']:
        for row in run.get_evidence(eid)['response']['data']:
            obs = deepcopy(review['observations'][0])
            obs.update(object={k: row[k] for k in ('platform', 'product_id', 'sku_id')}, evidence_id=eid)
            observations.append(obs)
    review['observations'] = observations
    review['reviewed_hash'] = digest({k: v for k, v in review.items() if k not in ('reviewer_id', 'reviewed_hash')})
    result = coverage.record_sample_review(run, review)
    assert result['finding_obligations']['expected'] == 2
    inv.prepare(run, '/root')
    chosen = result['finding_obligations']['pending_requirement_ids'][0]
    result = coverage.register_sampling_findings(run, '/root', [chosen])['finding_obligations']
    assert result['expected'] == 2 and result['registered'] == 1 and not result['complete']
    assert len(result['pending_requirement_ids']) == 1


def test_insufficient_sample_evidence_stays_needs_investigation_without_faking_external_block(tmp_path):
    run = _make_run(tmp_path)
    cell = _sampling(run)['population'][0]
    review = _review(run, cell)
    review.update(status='needs_investigation', reason='The source title does not establish the product function.',
                  next_action='Read the complete SKU detail and compare its category definition.')
    review['observations'][0]['disposition'] = 'needs_investigation'
    review['reviewed_hash'] = digest({k: v for k, v in review.items() if k not in ('reviewer_id', 'reviewed_hash')})
    result = coverage.record_sample_review(run, review)
    assert result['reviewed_cells'][cell['cell_id']] == 'needs_investigation'
    assert cell['cell_id'] in result['pending_cell_ids'] and not result['complete']
    assert result['finding_obligations']['expected'] == 0 and 'new_rule' not in review
    for mutation in ('missing_reason', 'fake_clean'):
        attack = deepcopy(review)
        if mutation == 'missing_reason':
            attack.pop('reason')
        else:
            attack['status'] = 'checked_clean'
        attack['reviewed_hash'] = digest({k: v for k, v in attack.items() if k not in ('reviewer_id', 'reviewed_hash')})
        with pytest.raises(ValueError):
            coverage.record_sample_review(run, attack)


def test_explicit_missed_observation_must_match_rule_and_cannot_be_silently_filtered(tmp_path):
    run = _make_run(tmp_path)
    cell = _sampling(run)['population'][0]
    review = _review(run, cell, 'missed_signal')
    review['observations'][0]['disposition'] = 'missed_signal'
    review['new_rule']['predicate']['value'] = 'A different product feature'
    review['reviewed_hash'] = digest({k: v for k, v in review.items() if k not in ('reviewer_id', 'reviewed_hash')})
    with pytest.raises(ValueError, match='每个漏检对象'):
        coverage.record_sample_review(run, review)
    review['new_rule']['predicate']['value'] = 'Battery'
    review['observations'][0]['disposition'] = 'textually_compatible'
    review['reviewed_hash'] = digest({k: v for k, v in review.items() if k not in ('reviewer_id', 'reviewed_hash')})
    with pytest.raises(ValueError, match='不能重新发现'):
        coverage.record_sample_review(run, review)


def test_mixed_cell_routes_explicit_miss_while_other_object_still_needs_investigation(tmp_path):
    run = _make_run(tmp_path, two_paths=True)
    cell = _sampling(run)['population'][0]
    review = _review(run, cell, 'missed_signal')
    job = coverage._sampling_job(run, cell)
    rows = run.job_rows(job['job_id'])
    rows[0]['population_objects'] = 2
    rows.append({**rows[0], 'product_id': 'p2', 'sku_id': 's2', 'sample_rank': 2})
    record(run, job, rows)
    observations = []
    for eid in run.job_record(job['job_id'])['evidence_ids']:
        for row in run.get_evidence(eid)['response']['data']:
            obs = deepcopy(review['observations'][0])
            obs.update(object={k: row[k] for k in ('platform', 'product_id', 'sku_id')}, evidence_id=eid,
                       disposition='missed_signal' if row['product_id'] == 'p1' else 'needs_investigation')
            observations.append(obs)
    review.update(observations=observations, status='needs_investigation',
                  reason='One observed conflict is routed; the other function remains uncertain.',
                  next_action='Investigate both exact source objects and settle the outstanding category evidence.')
    review['reviewed_hash'] = digest({k: v for k, v in review.items() if k not in ('reviewer_id', 'reviewed_hash')})
    result = coverage.record_sample_review(run, review)
    assert result['finding_obligations']['expected'] == 1 and result['expanded'] == 2
    assert cell['cell_id'] in result['pending_cell_ids'] and not result['complete']
    assert result['finding_obligations']['items'][0]['observation']['object']['product_id'] == 'p1'
    # The same broad rule matches both titles, but explicit compatibility is
    # not silently converted into another alleged defect.
    review['status'] = 'missed_signal'
    next(o for o in review['observations'] if o['object']['product_id'] == 'p2')['disposition'] = 'textually_compatible'
    review['reviewed_hash'] = digest({k: v for k, v in review.items() if k not in ('reviewer_id', 'reviewed_hash')})
    result = coverage.record_sample_review(run, review)
    assert result['finding_obligations']['expected'] == 1
