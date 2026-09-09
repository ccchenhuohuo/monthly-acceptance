"""Complete path-gap evidence through assignment, import and substantive review.

The source rows are SQLite fixtures, not live market observations. Both raw and
standard path gaps exercise the production workflow and proof gates unchanged.
"""
import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from acceptance_core import Run, canonical,  digest, read, write
from legacy_fixture import create_run
from acceptance_diagnostics import diagnostic_job
from acceptance_gap_proof import verify_gap_proof
import acceptance_investigation as inv
from acceptance_queries import job as baseline_job
from test_diagnostics import db, RAW, TARGET, run_sql
from test_execution import record
from test_gap_proof import populate
from test_investigation import answer_for, approval
from test_investigation_integration import integrated as result_workflow


@pytest.fixture(params=['raw_presence_gap', 'std_presence_gap'])
def gap_workflow(request, tmp_path, db):
    base = tmp_path / '验收'; base.mkdir()
    (base / 'config.yaml').write_text('站点起始月份: {cn: 2024-01}\n')
    (base / '项目范围.md').write_text('Synthetic complete path-gap workflow fixture')
    # Retain the archived v3.1 contract; v3.2 gates have dedicated attack tests.
    write(base / 'policy-overrides.json', {'page_size': 2, 'version': '3.1.0',
          'review_gate': {'enabled': False}, 'case_workflow': {'enabled': False}, 'coverage_gate': {'enabled': False},
          'investigation_workflow': {'max_unexplained_ratio': 0, 'max_unexplained_band_pp': 0}})
    parent = Run(create_run(base, Path(__file__).resolve().parents[1], '2024-07'))
    populate(db)
    raw = request.param == 'raw_presence_gap'
    level = 'raw' if raw else 3
    source = baseline_job(parent.manifest, 'raw_paths', 'cn') if raw else baseline_job(parent.manifest, 'category', 'cn', level=3)
    record(parent, source, run_sql(db, parent.manifest, source['sql']))
    source_ids = parent.job_record(source['job_id'])['evidence_ids']
    candidate = {'candidate_id': 'gap-integration', 'kind': request.param, 'priority': 'high',
                 'site': 'cn', 'level': None if raw else 3, 'path': canonical(RAW if raw else TARGET),
                 'month': '2024-06', 'comparison': None,
                 'facts': {'first_observed': '2024-04', 'last_observed': '2024-07'},
                 'source_jobs': [source['job_id']], 'evidence_ids': source_ids}
    write(parent.path / 'candidates.json', [candidate]); write(parent.path / 'samples.json', [])
    parent.event('independent_scan.frozen', candidate_hash=digest([candidate]), counts={'candidates': 1, 'samples': 0})
    inv.prepare(parent, '/root')
    oid = 'candidate:' + candidate['candidate_id']
    task = inv.assign(parent, '/root', '/root/gap-worker', [oid])
    worker = Run(parent.path / task['worker_dir'])
    coverage = diagnostic_job(parent.manifest, 'source_coverage', 'cn', level, candidate['path'], '2024-05', '2024-07')
    record(worker, coverage, run_sql(db, parent.manifest, coverage['sql']))
    local_ids = worker.job_record(coverage['job_id'])['evidence_ids']
    answer = answer_for(inv.all_obligations(parent)[oid], local_ids[0])
    answer['checks']['scope_baseline']['evidence_ids'] = ['main:' + eid for eid in source_ids]
    answer['checks']['sku_destination']['evidence_ids'] = local_ids
    answer['gap_proof'] = {'source_coverage_job_id': coverage['job_id'], 'entity_assessments': [
        {'product_ids': ['p1'], 'status': 'verified',
         'reason': 'The same fixture SKU moves to another parent and path in June, then returns to the target path in July.',
         'counterevidence': 'Observed SKU continuity does not establish the reason for the parent or category change.',
         'evidence_ids': local_ids + ['main:' + source_ids[0]]},
        {'product_ids': ['p2'], 'status': 'verified',
         'reason': 'The second fixture SKU has explicit no-observation cells in June and observed recovery cells in July.',
         'counterevidence': 'No observation establishes neither zero market sales nor a complete source capture.',
         'evidence_ids': local_ids},
    ]}
    answer['impact'] = verify_gap_proof(parent.manifest, candidate, answer, worker, parent.get_evidence,
                                        check_claims=False)['computed_impact']
    payload = {'task_id': task['task_id'], 'agent_id': task['agent_id'], 'attempt': task['attempt'], 'answers': [answer]}
    write(worker.path / 'submission.json', payload)
    return {'parent': parent, 'worker': worker, 'candidate': candidate, 'source': source,
            'coverage': coverage, 'task': task, 'oid': oid, 'payload': payload,
            'source_ids': source_ids, 'local_ids': local_ids}


def submit(case):
    return inv.submit(case['parent'], '/root', case['task']['task_id'])


def write_submission(case):
    write(case['worker'].path / 'submission.json', case['payload'])


def stored_submission(case):
    return read(inv.home(case['parent']) / 'submissions' / (case['task']['task_id'] + '.json'))


def reviewed_answer(case):
    value = approval(case['parent'], case['task'])
    stored = stored_submission(case)
    answer = stored['payload']['answers'][0]
    proof = stored['payload']['population_proofs'][case['oid']]
    entry = value['obligations'][0]
    groups = answer['gap_proof']['entity_assessments']
    entry['evidence_checked'] = sorted(set(entry['evidence_checked']) | set(proof['evidence_ids']) |
                                       {eid for group in groups for eid in group['evidence_ids']})
    entry['entity_reviews'] = [
        {'assessment_sha256': digest(group), 'decision': 'accept',
         'reason': 'Read this explicit fixture entity group, each destination month and layer, its explanation and counterevidence.'}
        for group in groups]
    return value


def test_complete_gap_import_preserves_parent_sources_and_requires_main_review(gap_workflow):
    case = gap_workflow
    result = submit(case)
    parent, task = case['parent'], case['task']
    stored = stored_submission(case)
    proof = stored['payload']['population_proofs'][case['oid']]
    normalized = stored['payload']['answers'][0]
    assert proof['anchors'] == {'month': '2024-05', 'spus': 2, 'nrows': 2, 'units': 13, 'amount': 400}
    assert proof['cohort'] == {'all': 2, 'verified': 2, 'blocked_external': 0, 'not_assessed': 0}
    assert proof['seed_count'] == 2 and proof['verified_destination_cells'] == proof['expected_destination_cells'] == 8
    assert proof['unreviewed_product_ids'] == []
    assert proof['source_evidence_sha256'] == {eid: digest(parent.get_evidence(eid)) for eid in case['source_ids']}
    assert normalized['checks']['scope_baseline']['evidence_ids'] == case['source_ids']
    assert set(case['source_ids']) <= set(proof['evidence_ids'])
    assert not any(eid.startswith('main:') for eid in proof['evidence_ids'])
    assert set(stored['evidence_map']) == set(case['local_ids'])
    assert not set(case['source_ids']) & set(stored['evidence_map'])
    assert result['imported_evidence_count'] == len(case['local_ids']) > 1
    assert stored['worker_proof_file_hashes'] == proof['job_files_sha256']
    for local_id, imported_id in stored['evidence_map'].items():
        assert parent.get_evidence(imported_id) == case['worker'].get_evidence(local_id)
        provenance = read(parent.path / 'records' / (imported_id + '.json'))['source']
        assert provenance['task_id'] == task['task_id'] and provenance['worker_evidence_id'] == local_id
    assert inv.audit(parent)['verified'] == 0
    inv.review(parent, '/root', task['task_id'], reviewed_answer(case))
    audit = inv.audit(parent, {'findings': [{'obligation_ids': [case['oid']]}]})
    assert audit['errors'] == [] and audit['verified'] == audit['reviewed'] == 1
    assert audit['expected_obligations'] == 6 and audit['unassigned'] == 5
    assert not audit['substantive_complete']


def test_gap_without_full_proof_cannot_be_submitted(gap_workflow):
    case = gap_workflow
    case['payload']['answers'][0].pop('gap_proof')
    write_submission(case)
    with pytest.raises(ValueError, match='gap_proof'):
        submit(case)
    assert inv.task_records(case['parent'])[case['task']['task_id']]['state'] == 'assigned'
    assert inv.audit(case['parent'])['verified'] == 0


def test_full_job_label_cannot_hide_an_entire_missing_seed(gap_workflow):
    case = gap_workflow
    # Repage the truncated response with internally consistent row counts and
    # the correct full-population SQL. Only independent anchor conservation
    # catches that an entire seed was omitted from this otherwise valid job.
    rows = [r for r in case['worker'].job_rows(case['coverage']['job_id']) if r['seed_product_id'] == 'p1']
    record(case['worker'], case['coverage'], rows)
    with pytest.raises(ValueError, match='完整种子.spus'):
        submit(case)
    assert inv.task_records(case['parent'])[case['task']['task_id']]['state'] == 'assigned'


def test_external_blocker_cannot_hide_the_other_uninvestigated_gap_seed(gap_workflow):
    case = gap_workflow
    answer = case['payload']['answers'][0]
    answer['gap_proof']['entity_assessments'].pop()
    answer['gap_proof']['entity_assessments'][0]['status'] = 'blocked_external'
    answer['checks']['sku_destination'].update(status='blocked_external', blocking={
        'type': 'missing_external_record', 'required_record': 'Historical provider source capture for fixture p1',
        'provider_role': 'Source record owner', 'attempted_access': 'Inspected all supplied fixture source rows',
        'why_unavailable': 'The provider capture supporting this one identity change was not supplied',
        'decision_impact': 'This one entity remains unverified', 'resume_action': 'Review its capture when supplied',
        'blocked_checks': ['sku_destination']})
    answer['business_verdict'] = 'undetermined'
    answer['impact'] = verify_gap_proof(case['parent'].manifest, case['candidate'], answer, case['worker'],
                                        case['parent'].get_evidence, check_claims=False)['computed_impact']
    write_submission(case)
    with pytest.raises(ValueError, match='仍有未调查事项'):
        submit(case)


@pytest.mark.parametrize('omission', ['all_entity_reviews', 'one_entity_review', 'source_page'])
def test_main_cannot_accept_without_every_entity_group_and_source_page(gap_workflow, omission):
    case = gap_workflow
    submit(case)
    value = reviewed_answer(case)
    entry = value['obligations'][0]
    if omission == 'all_entity_reviews':
        entry.pop('entity_reviews')
    elif omission == 'one_entity_review':
        entry['entity_reviews'].pop()
    else:
        entry['evidence_checked'].remove(case['source_ids'][0])
    with pytest.raises(ValueError, match='逐实体组|全部动作证据'):
        inv.review(case['parent'], '/root', case['task']['task_id'], value)
    assert inv.audit(case['parent'])['verified'] == 0


@pytest.mark.parametrize('phase', ['before_review', 'after_review'])
def test_changed_parent_anchor_invalidates_submission_and_prior_review(gap_workflow, phase):
    case = gap_workflow
    submit(case)
    parent, task = case['parent'], case['task']
    value = reviewed_answer(case)
    if phase == 'after_review':
        inv.review(parent, '/root', task['task_id'], value)
    anchor_eid = next(eid for eid in case['source_ids']
                      if any(r['month_dt'] == '2024-05-01' and r['path'] == case['candidate']['path']
                             for r in parent.get_evidence(eid)['response']['data']))
    path = parent.path / 'evidence' / (anchor_eid + '.json')
    evidence = read(path)
    anchor = next(r for r in evidence['response']['data']
                  if r['month_dt'] == '2024-05-01' and r['path'] == case['candidate']['path'])
    anchor['amount'] += 1
    write(path, evidence)
    # Even replacing the evidence's own metadata hash cannot replace the
    # independent frozen anchor hash bound into the submitted gap proof.
    metadata_path = parent.path / 'records' / (anchor_eid + '.json')
    metadata = read(metadata_path); metadata['sha256'] = digest(path.read_bytes()); write(metadata_path, metadata)
    assert parent.get_evidence(anchor_eid) == evidence
    if phase == 'before_review':
        with pytest.raises(ValueError, match='主包缺口基线'):
            inv.review(parent, '/root', task['task_id'], value)
    audit = inv.audit(parent)
    assert any('主包缺口基线' in error for error in audit['errors'])
    assert audit['verified'] == 0 and not audit['substantive_complete']


@pytest.mark.parametrize('populated', [False, True], ids=['empty_foreign_proof', 'different_entity_groups'])
def test_gap_cannot_substitute_another_proof_familys_entity_groups(gap_workflow, populated):
    case = gap_workflow
    # The numeric gap proof must not review one set of groups while the main
    # workflow signs another set chosen by a truthy foreign proof object.
    answer = case['payload']['answers'][0]
    answer['population_proof'] = {'entity_assessments': [copy.deepcopy(answer['gap_proof']['entity_assessments'][0])]} if populated else {}
    write_submission(case)
    with pytest.raises(ValueError, match='proof|证明|实体组'):
        submit(case)


@pytest.mark.parametrize('populated', [False, True], ids=['empty_foreign_proof', 'different_entity_groups'])
def test_result_cannot_mix_a_gap_proof_into_its_entity_review(result_workflow, populated):
    case = result_workflow
    answer = case['payload']['answers'][0]
    answer['gap_proof'] = {'entity_assessments': [copy.deepcopy(answer['population_proof']['entity_assessments'][0])]} if populated else {}
    write_submission(case)
    with pytest.raises(ValueError, match='proof|证明|实体组'):
        submit(case)
