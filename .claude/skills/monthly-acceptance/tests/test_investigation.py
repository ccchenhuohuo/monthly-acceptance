"""Contract-scope workflow fixtures; typed numeric/gap proofs have integration tests."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from acceptance_core import Run,  digest, read, write
from legacy_fixture import create_run
import acceptance_investigation as inv


@pytest.fixture
def investigation(tmp_path):
    base = tmp_path / '验收'; base.mkdir()
    (base / 'config.yaml').write_text('站点起始月份: {DE: 2024-01}\n')
    (base / '项目范围.md').write_text('Synthetic workflow test')
    # This suite freezes the v3.1 contract; v3.2 has its own substantive fixtures.
    write(base / 'policy-overrides.json', {'version': '3.1.0', 'review_gate': {'enabled': False},
          'case_workflow': {'enabled': False}, 'coverage_gate': {'enabled': False},
          'investigation_workflow': {'max_unexplained_ratio': 0, 'max_unexplained_band_pp': 0}})
    skill = Path(__file__).resolve().parents[1]
    run = Run(create_run(base, skill, '2026-07'))
    eid = run.evidence({'fixture': 'aggregate alarm, not an explanation'}, kind='synthetic_baseline')
    cs = [{'candidate_id': 'high-change', 'site': 'DE', 'level': 3, 'path': '["a","b","c"]',
           'month': '2026-07', 'comparison': None, 'kind': 'contract_scope_missing', 'priority': 'high', 'evidence_ids': [eid]},
          {'candidate_id': 'ordinary-change', 'site': 'DE', 'level': 3, 'path': '["a","b","d"]',
           'month': '2026-06', 'comparison': None, 'kind': 'contract_scope_missing', 'priority': 'normal', 'evidence_ids': [eid]}]
    write(run.path / 'candidates.json', cs); write(run.path / 'samples.json', [])
    run.event('independent_scan.frozen', candidate_hash=digest(cs), counts={'candidates': 2, 'samples': 0})
    inv.prepare(run, '/root')
    return run


def answer_for(ob, eid):
    answer = {'obligation_id': ob['obligation_id'], 'scope': ob['scope'],
              'conclusion': 'Synthetic fixture conclusion; no actual market claim.',
              'next_action': 'Retain this synthetic evidence for the workflow test.',
              'business_verdict': 'qualified', 'repair_state': 'not_needed', 'defect_confirmed': False,
              'checks': {name: {'status': 'verified', 'finding': 'Explicit synthetic fixture supports this check.',
                          'actions': ['Read the scoped fixture and compare both periods.'],
                          'counterevidence': 'Checked the alternative fixture branch.', 'evidence_ids': [eid]}
                         for name in ob['required_checks']}}
    if ob['requires_impact']:
        answer['impact'] = {m: {'state': 'measured', 'exposure': 100, 'unexplained': 0,
                              'unit': 'pp' if m == 'bands' else 'fixture units',
                              'basis': 'All rows of the explicitly synthetic fixture, both periods.', 'evidence_ids': [eid]}
                           for m in inv.METRICS}
    return answer


def make_submission(run, task, mutate=None):
    worker = Run(run.path / task['worker_dir'])
    eid = worker.evidence({'fixture': 'complete entity evidence', 'scopes': task['obligation_ids']}, kind='synthetic_detail')
    obs = inv.all_obligations(run)
    answers = [answer_for(obs[oid], eid) for oid in task['obligation_ids']]
    payload = {'task_id': task['task_id'], 'agent_id': task['agent_id'], 'attempt': task['attempt'], 'answers': answers}
    if mutate: mutate(payload)
    write(worker.path / 'submission.json', payload)
    return payload


def approval(run, task, reject=False):
    stored = read(inv.home(run) / 'submissions' / (task['task_id'] + '.json'))
    entries = []
    for a in stored['payload']['answers']:
        decision = 'return' if reject else 'accept'
        checked = set(e for c in a['checks'].values() for e in c['evidence_ids'])
        checked.update(e for c in a.get('impact', {}).values() for e in c['evidence_ids'])
        entries.append({'obligation_id': a['obligation_id'], 'decision': decision,
                        'reason': 'Reviewed synthetic evidence content and alternative branch.',
                        'scope_check': 'Exact fixture site, path and both comparison periods.',
                        'evidence_checked': sorted(checked),
                        'check_reviews': {n: {'decision': decision, 'reason': 'Compared this scoped fixture evidence.'} for n in a['checks']}})
    return {'reviewer_id': '/root', 'submission_sha256': stored['sha256'], 'obligations': entries}


def one_task(run):
    return inv.assign(run, '/root', '/root/investigator', ['candidate:high-change'])


def test_full_denominator_includes_ordinary_signals_and_project_checks(investigation):
    r = inv.audit(investigation)
    assert r['expected_obligations'] == 6 and r['unassigned'] == 6
    t = one_task(investigation); make_submission(investigation, t); inv.submit(investigation, '/root', t['task_id'])
    inv.review(investigation, '/root', t['task_id'], approval(investigation, t))
    r = inv.audit(investigation)
    assert r['verified'] == 1 and r['unassigned'] == 5 and not r['substantive_complete']


def test_missing_check_cannot_be_submitted(investigation):
    t = one_task(investigation)
    make_submission(investigation, t, lambda p: p['answers'][0]['checks'].pop('path_identity'))
    with pytest.raises(ValueError, match='未逐项回答'): inv.submit(investigation, '/root', t['task_id'])
    assert inv.audit(investigation)['verified'] == 0


def test_generic_pending_is_not_a_finished_investigation(investigation):
    t = one_task(investigation)
    make_submission(investigation, t, lambda p: p['answers'][0]['checks']['path_identity'].update(status='needs_evidence'))
    with pytest.raises(ValueError, match='不接受'): inv.submit(investigation, '/root', t['task_id'])


def test_small_semantic_defect_does_not_close_large_unexplained_exposure(investigation):
    t = one_task(investigation)
    def mutate(p):
        a = p['answers'][0]; a['defect_confirmed'] = True; a['business_verdict'] = 'reject'; a['repair_state'] = 'pending'
        a['impact']['units'].update(exposure=44610, unexplained=44609)
        a['conclusion'] = 'One misclassified accessory, one unit; remaining missing-path exposure not investigated.'
    make_submission(investigation, t, mutate)
    with pytest.raises(ValueError, match='残余超过'): inv.submit(investigation, '/root', t['task_id'])


def test_wrong_month_scope_is_rejected(investigation):
    t = one_task(investigation)
    make_submission(investigation, t, lambda p: p['answers'][0]['scope'].update(month='2025-07'))
    with pytest.raises(ValueError, match='月份'): inv.submit(investigation, '/root', t['task_id'])


def test_aggregate_evidence_cannot_replace_specific_investigation(investigation):
    t = one_task(investigation)
    ob = inv.all_obligations(investigation)['candidate:high-change']
    make_submission(investigation, t, lambda p: p['answers'][0]['checks']['path_identity'].update(evidence_ids=['main:' + ob['source_evidence_ids'][0]]))
    with pytest.raises(ValueError, match='仅引用聚合'): inv.submit(investigation, '/root', t['task_id'])


def test_worker_cannot_self_approve_or_update_central_state(investigation):
    t = one_task(investigation)
    make_submission(investigation, t, lambda p: p.update(approved=True))
    with pytest.raises(ValueError, match='自行批准'): inv.submit(investigation, '/root', t['task_id'])
    make_submission(investigation, t); inv.submit(investigation, '/root', t['task_id'])
    with pytest.raises(ValueError, match='仅登记的主Agent'): inv.review(investigation, t['agent_id'], t['task_id'], approval(investigation, t))


def test_main_review_must_check_each_question_and_evidence(investigation):
    t = one_task(investigation); make_submission(investigation, t); inv.submit(investigation, '/root', t['task_id'])
    a = approval(investigation, t); a['obligations'][0]['check_reviews'].pop('counterevidence')
    with pytest.raises(ValueError, match='逐调查动作'): inv.review(investigation, '/root', t['task_id'], a)
    a = approval(investigation, t); a['obligations'][0]['evidence_checked'] = []
    with pytest.raises(ValueError, match='全部动作证据'): inv.review(investigation, '/root', t['task_id'], a)


def test_rejection_and_timeout_requeue_preserve_every_obligation(investigation):
    t = one_task(investigation); make_submission(investigation, t); inv.submit(investigation, '/root', t['task_id'])
    inv.review(investigation, '/root', t['task_id'], approval(investigation, t, True))
    assert inv.audit(investigation)['states']['rework'] == 1
    inv.requeue(investigation, '/root', t['task_id'], 'Needs scoped evidence; reassign.')
    replacement = inv.assign(investigation, '/root', '/root/replacement', t['obligation_ids'])
    assert replacement['attempt'] == 2 and replacement['previous_task_ids'] == [t['task_id']]
    with pytest.raises(ValueError, match='过期'): inv.submit(investigation, '/root', t['task_id'])
    assert inv.audit(investigation)['expected_obligations'] == 6


def test_real_external_block_is_reviewed_but_not_verified(investigation):
    t = one_task(investigation)
    def blocked(p):
        a = p['answers'][0]; a['business_verdict'] = 'undetermined'
        a['checks']['path_identity'].update(status='blocked_external', blocking={
            'type': 'missing_external_record', 'required_record': '2026-07 SKU source capture and node assignment',
            'provider_role': 'Source-data provider', 'attempted_access': 'Examined all supplied historical records.',
            'why_unavailable': 'Source capture was absent from the supplied input package.',
            'decision_impact': 'Cannot validate this missing SKU source destination.', 'resume_action': 'Read capture when supplied.',
            'blocked_checks': ['path_identity']})
    make_submission(investigation, t, blocked); inv.submit(investigation, '/root', t['task_id'])
    inv.review(investigation, '/root', t['task_id'], approval(investigation, t))
    r = inv.audit(investigation)
    assert r['blocked_external'] == 1 and r['reviewed'] == 1 and r['verified'] == 0 and not r['substantive_complete']


def test_budget_exhaustion_is_not_external_evidence_block(investigation):
    t = one_task(investigation)
    make_submission(investigation, t, lambda p: p['answers'][0]['checks']['path_identity'].update(status='blocked_external', blocking={'type': 'budget_exhausted'}))
    with pytest.raises(ValueError, match='预算耗尽'): inv.submit(investigation, '/root', t['task_id'])


@pytest.mark.parametrize('mutation', ['answer', 'evidence', 'events'])
def test_post_approval_mutation_invalidates_review(investigation, mutation):
    t = one_task(investigation); make_submission(investigation, t); inv.submit(investigation, '/root', t['task_id'])
    inv.review(investigation, '/root', t['task_id'], approval(investigation, t))
    wp = investigation.path / t['worker_dir']
    if mutation == 'answer':
        p = read(wp / 'submission.json'); p['answers'][0]['conclusion'] = 'Changed'; write(wp / 'submission.json', p)
    elif mutation == 'evidence': next((wp / 'evidence').glob('*.json')).write_text('{}')
    else: Run(wp).event('new.activity.after.submission')
    assert inv.audit(investigation)['errors'] and not inv.audit(investigation)['substantive_complete']


def test_missing_report_conclusion_and_final_seal_are_blocked(investigation):
    t = one_task(investigation); make_submission(investigation, t); inv.submit(investigation, '/root', t['task_id'])
    inv.review(investigation, '/root', t['task_id'], approval(investigation, t))
    r = inv.audit(investigation, {'findings': []})
    assert any('未进入报告' in e for e in r['errors'])
    write(investigation.path / 'report-content.json', {'findings': []})
    with pytest.raises(ValueError, match='不能封存'): investigation.seal({'errors': []})


def test_deleting_or_shrinking_frozen_obligations_is_detected(investigation):
    p = read(inv.home(investigation) / 'plan.json'); p['payload']['obligations'].pop()
    p['sha256'] = digest(p['payload']); write(inv.home(investigation) / 'plan.json', p)
    assert inv.audit(investigation)['errors']


def test_complete_review_requires_every_obligation_and_report(investigation):
    ids = list(inv.all_obligations(investigation))
    t = inv.assign(investigation, '/root', '/root/full-fixture', ids)
    make_submission(investigation, t); inv.submit(investigation, '/root', t['task_id'])
    inv.review(investigation, '/root', t['task_id'], approval(investigation, t))
    r = inv.audit(investigation, {'findings': [{'obligation_ids': ids}]})
    assert r['substantive_complete'] and r['verified'] == len(ids) and not r['errors']


@pytest.mark.parametrize('historical_month', ['2026-06', '2026-07'])
def test_historical_reports_are_frozen_without_open_issue_file(tmp_path, historical_month):
    base = tmp_path / '验收'; base.mkdir()
    (base / 'config.yaml').write_text('站点起始月份: {DE: 2024-01}\n'); (base / '项目范围.md').write_text('fixture')
    old = base / 'runs' / historical_month / 'old'; old.mkdir(parents=True); (old / '报告.md').write_text('Known historical problem')
    (base / '案例库.md').write_text('Known counterexample')
    run = Run(create_run(base, Path(__file__).resolve().parents[1], '2026-07'))
    assert len(run.manifest['history_sources']) == 2
    assert all((run.path / p).is_file() for p in run.manifest['history_sources'])
