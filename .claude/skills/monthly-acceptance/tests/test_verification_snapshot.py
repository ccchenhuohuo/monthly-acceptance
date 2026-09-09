"""Read-volume and adversarial version checks for the actual verification paths."""
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from acceptance_core import Run, digest, read, write
from acceptance_snapshot import verification_snapshot, current_snapshot
import acceptance_investigation as inv
import acceptance_questions as q
import acceptance_review as review
import acceptance_validate as validation
from acceptance_queries import plan
from test_execution import record, rows_for
from test_questions_v4 import v4, topic, answer, submit, main_review


def assigned(v4, *, high=False):
    run, _ = v4
    value = topic(v4)
    if high: value['priority'] = 'high'
    case = q.register(run, 'main', value)
    task = inv.assign(run, 'main', 'worker', case['obligation_ids'])
    return run, task, answer(run, task)


def evidence_path(run, a):
    eid = a['calculations'][0]['operands'][0]['locator']['evidence_id'].removeprefix('main:')
    return run.path/'evidence'/(eid+'.json')


def change_units(path, *, resign=False):
    value = json.loads(path.read_bytes())
    value['response']['data'][0]['units'] += 1
    write(path, value)
    if resign:
        meta_path = path.parent.parent/'records'/path.name
        meta = json.loads(meta_path.read_bytes())
        meta['sha256'] = digest(path.read_bytes())
        write(meta_path, meta)


def test_one_large_page_is_read_twice_and_decoded_once_for_20000_lookups(v4, monkeypatch):
    run, _ = v4
    eid = run.evidence({'response': {'data': [{'units': 10, 'title': 'x'*900000}]}}, kind='fixture')
    page = run.path/'evidence'/(eid+'.json')
    blob = page.read_bytes()
    calls = Counter()
    original_read = Path.read_bytes
    original_loads = json.loads
    def tracked_read(path):
        if path == page: calls['reads'] += 1
        return original_read(path)
    def tracked_loads(value, *args, **kwargs):
        if isinstance(value, bytes) and value == blob: calls['parses'] += 1
        return original_loads(value, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_bytes', tracked_read)
    monkeypatch.setattr(json, 'loads', tracked_loads)
    with verification_snapshot(run):
        for _ in range(20000):
            assert run.get_evidence(eid)['response']['data'][0]['units'] == 10
    assert calls == {'reads': 2, 'parses': 1}  # first read + mandatory exit reread
    assert current_snapshot() is None


@pytest.mark.parametrize('operation', ['submit', 'review', 'audit'])
def test_large_answer_official_paths_read_unique_pages_at_boundaries(v4, monkeypatch, operation):
    run, task, a = assigned(v4)
    original = a['calculations'][1]
    a['calculations'] += [dict(deepcopy(original), calculation_id='repeat-'+str(i)) for i in range(1200)]
    assert sum(len(c['operands']) for c in a['calculations']) == 2406
    page = evidence_path(run, a)
    if operation != 'submit': submit(run, task, a)
    entry = main_review(run, task) if operation != 'submit' else None
    if operation == 'audit': inv.review(run, 'main', task['task_id'], entry)
    calls = Counter(); original_read = Path.read_bytes
    def tracked_read(path):
        if path == page: calls['page'] += 1
        return original_read(path)
    monkeypatch.setattr(Path, 'read_bytes', tracked_read)
    if operation == 'submit': submit(run, task, a)
    elif operation == 'review': inv.review(run, 'main', task['task_id'], entry)
    else: assert inv.audit(run)['verified'] == 1
    assert calls['page'] == 2


@pytest.mark.parametrize('damage', ['same_size_page', 'metadata', 'cache', 'run_object', 'symlink'])
def test_snapshot_exit_rejects_file_metadata_alias_and_memory_changes(v4, damage, tmp_path):
    run, task, a = assigned(v4)
    page = evidence_path(run, a); eid = page.stem
    with pytest.raises((ValueError, OSError)):
        with verification_snapshot(run):
            cached = run.get_evidence(eid)
            if damage == 'same_size_page':
                stat = page.stat(); before = page.read_bytes()
                after = before.replace(b'"units": 10', b'"units": 11', 1)
                assert before != after and len(before) == len(after)
                page.write_bytes(after); os.utime(page, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                assert run.get_evidence(eid) is cached
            elif damage == 'metadata':
                meta_path = run.path/'records'/page.name
                meta = json.loads(meta_path.read_bytes()); meta['kind'] = 'changed-origin'
                write(meta_path, meta)
            elif damage == 'cache': cached['response']['data'][0]['units'] += 1
            elif damage == 'run_object': run.manifest['data_month'] = '2099-01'
            else:
                replacement = tmp_path/'elsewhere.json'; replacement.write_bytes(page.read_bytes())
                page.unlink(); page.symlink_to(replacement)
    assert current_snapshot() is None


def test_same_evidence_id_in_two_owners_never_shares_rows(v4):
    run, task, a = assigned(v4)
    page = evidence_path(run, a); worker = Run(run.path/task['worker_dir'])
    other = json.loads(page.read_bytes()); other['response']['data'][0]['units'] = 987
    other_path = worker.path/'evidence'/page.name; write(other_path, other)
    meta = json.loads((run.path/'records'/page.name).read_bytes())
    meta['sha256'] = digest(other_path.read_bytes()); write(worker.path/'records'/page.name, meta)
    with verification_snapshot(run):
        assert run.get_evidence(page.stem)['response']['data'][0]['units'] == 10
        assert worker.get_evidence(page.stem)['response']['data'][0]['units'] == 987


@pytest.mark.parametrize('when', ['before_read', 'after_cached'])
def test_missing_actual_source_page_cannot_be_replaced_by_cached_import(v4, when):
    run, task, a = assigned(v4)
    page = evidence_path(run, a); worker = Run(run.path/task['worker_dir'])
    imported = worker.evidence(run.get_evidence(page.stem), kind='verified-reused-query', source={
        'source_run': str(run.path), 'source_evidence_id': page.stem,
        'source_sha256': digest(page.read_bytes())})
    if when == 'before_read': page.unlink()
    with pytest.raises((ValueError, OSError)):
        with verification_snapshot(worker):
            owner, original, _, _ = review._receipt_origin(worker, worker.get_evidence, imported)
            assert owner.path == run.path and original == page.stem
            page.unlink()


@pytest.mark.parametrize('operation', ['submit', 'review', 'audit'])
def test_mutation_after_last_calculation_cannot_commit_or_credit(v4, monkeypatch, operation):
    run, task, a = assigned(v4); page = evidence_path(run, a)
    if operation != 'submit': submit(run, task, a)
    entry = main_review(run, task) if operation != 'submit' else None
    if operation == 'audit': inv.review(run, 'main', task['task_id'], entry)
    target = 'verify_answer' if operation == 'submit' else 'validate_main_review'
    module = inv if operation == 'submit' else q
    original = getattr(module, target); calls = 0
    def mutated(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs); calls += 1
        if calls == (2 if operation == 'submit' else 1): change_units(page, resign=True)
        return result
    monkeypatch.setattr(module, target, mutated)
    if operation == 'audit':
        result = inv.audit(run)
        assert result['errors'] and result['verified'] == result['reviewed'] == 0
        assert not result['substantive_complete']
    else:
        with pytest.raises(ValueError, match='快照'):
            if operation == 'submit': submit(run, task, a)
            else: inv.review(run, 'main', task['task_id'], entry)
        state = inv.task_records(run)[task['task_id']]['state']
        assert state == ('assigned' if operation == 'submit' else 'submitted')
        folder = 'submissions' if operation == 'submit' else 'reviews'
        assert not (inv.home(run)/folder/(task['task_id']+'.json')).exists()


def test_fresh_review_and_audit_detect_same_id_changed_after_submission(v4):
    run, task, a = assigned(v4); submit(run, task, a)
    entry = main_review(run, task)
    inv.review(run, 'main', task['task_id'], deepcopy(entry))
    assert inv.audit(run)['verified'] == 1
    change_units(evidence_path(run, a), resign=True)
    result = inv.audit(run)
    assert result['errors'] and not result['substantive_complete']
    # A fresh submitted task also cannot consume a prior operation's page cache.
    with pytest.raises(ValueError): q.validate_main_review(run, task, inv.all_obligations(run)[a['obligation_id']],
        inv.checked_submission(run, inv.task_records(run)[task['task_id']])['payload']['answers'][0],
        entry['obligations'][0], entry['submission_sha256'])


def test_evidence_changed_between_submission_and_independent_review_is_rejected(v4):
    run, task, a = assigned(v4); submit(run, task, a)
    entry = main_review(run, task)
    change_units(evidence_path(run, a), resign=True)
    with pytest.raises(ValueError): inv.review(run, 'main', task['task_id'], entry)
    assert inv.task_records(run)[task['task_id']]['state'] == 'submitted'
    assert not (inv.home(run)/'reviews'/(task['task_id']+'.json')).exists()


@pytest.mark.parametrize('tamper', [False, True])
def test_worker_imports_advance_only_authored_events_and_keep_source_binding(v4, monkeypatch, tamper):
    run, task, a = assigned(v4); worker = Run(run.path/task['worker_dir'])
    job = next(j for j in plan(run.manifest) if j['family'] == 'category' and j['level'] == 0)
    original_id = 'main:'+run.job_record(job['job_id'])['evidence_ids'][0]
    record(worker, job, rows_for(worker, job)); write(worker.path/'records'/(job['job_id']+'-plan.json'), job)
    rec = worker.job_record(job['job_id']); eid = rec['evidence_ids'][0]
    a = review.rewrite_evidence_references(a, lambda key: eid if key == original_id else key)
    next(x for x in a['proof_jobs'] if x['job_id'] == job['job_id'])['record_sha256'] = digest(rec)
    if tamper:
        original_evidence = run.evidence
        def changed_source(*args, **kwargs):
            result = original_evidence(*args, **kwargs)
            change_units(worker.path/'evidence'/(eid+'.json'), resign=True)
            return result
        monkeypatch.setattr(run, 'evidence', changed_source)
        with pytest.raises(ValueError, match='快照'): submit(run, task, a)
        assert inv.task_records(run)[task['task_id']]['state'] == 'assigned'
    else:
        result = submit(run, task, a)
        assert result['imported_evidence_count'] == 1
        inv.review(run, 'main', task['task_id'], main_review(run, task))
        assert inv.audit(run)['verified'] == 1
        assert not run.verify_events() and not worker.verify_events()


@pytest.mark.parametrize('source', ['job_record', 'native_plan', 'native_record', 'events'])
def test_proof_dependencies_cannot_change_after_cached_validation(v4, source):
    import asyncio
    from acceptance_queries import question_jobs
    from acceptance_transport import execute_job
    from acceptance_job_versions import _path
    run, task, a = assigned(v4)
    job = question_jobs(run.manifest, topic(v4)['scope_units'][0], 'aggregate')[0]
    class NativeFixture:
        async def call_tool(self, name, args, **kwargs):
            row = {'site': 'DE', 'month_dt': job['months'][0]+'-01', 'platform': 'Amazon',
                'spus': 1, 'nrows': 1, 'units': 10, 'amount': 100, 'total_rows': 1}
            return {'success': True, 'data': [row], 'row_count': 1, 'metadata': {'query': args['sql']}}
    rec = asyncio.run(execute_job(run, NativeFixture(), job))
    assert rec['execution_status'] == 'success' and rec['attempt_id']
    jid = job['job_id']
    a['proof_jobs'] += [{'evidence_id': 'main:'+eid, 'job_id': jid, 'record_sha256': digest(rec)} for eid in rec['evidence_ids']]
    with pytest.raises((ValueError, OSError)):
        with verification_snapshot(run):
            q._proof_jobs(run, a, run.get_evidence)
            if source == 'events':
                with run.events_path.open('ab') as journal: journal.write(b'\n')
            else:
                path = (run.path/'records'/('job-'+jid+'.json') if source == 'job_record'
                    else _path(run, jid, rec['plan_sha256'], 'plan') if source == 'native_plan'
                    else _path(run, jid, digest(rec)))
                path.write_bytes(path.read_bytes()+b' ')


@pytest.mark.parametrize('missing', [False, True])
def test_proof_origins_are_linear_and_complete_multi_page_jobs_remain_required(v4, monkeypatch, missing):
    run, task, a = assigned(v4)
    job = next(j for j in plan(run.manifest) if j['family'] == 'category' and j['level'] == 3)
    base = rows_for(run, job)[0]
    record(run, job, [dict(base, path=json.dumps(['a', 'b', str(i)])) for i in range(12)])
    rec = run.job_record(job['job_id']); assert len(rec['evidence_ids']) == 3
    extra = [{'evidence_id': 'main:'+eid, 'job_id': job['job_id'], 'record_sha256': digest(rec)} for eid in rec['evidence_ids']]
    a['proof_jobs'] += extra[:1]+extra[2:] if missing else extra
    calls = Counter(); origin = review._receipt_origin; verify_job = validation.verify_job
    def counted_origin(*args, **kwargs): calls['origins'] += 1; return origin(*args, **kwargs)
    def counted_job(*args, **kwargs): calls['jobs'] += 1; return verify_job(*args, **kwargs)
    monkeypatch.setattr(review, '_receipt_origin', counted_origin)
    monkeypatch.setattr(validation, 'verify_job', counted_job)
    with verification_snapshot(run):
        if missing:
            with pytest.raises(ValueError, match='遗漏分页'): q._proof_jobs(run, a, run.get_evidence)
        else: q._proof_jobs(run, a, run.get_evidence)
    assert calls['origins'] == len(a['proof_jobs'])
    assert calls['jobs'] == len({x['job_id'] for x in a['proof_jobs']})


@pytest.mark.parametrize('reviewer', [None, 'worker', 'independent-reader'])
def test_independent_main_review_is_still_required_after_cached_submit(v4, reviewer):
    run, task, a = assigned(v4, high=True); submit(run, task, a)
    entry = main_review(run, task)
    if reviewer:
        ob = inv.all_obligations(run)[a['obligation_id']]
        entry['obligations'][0]['independent_review'] = {
            'reviewer_id': reviewer, 'contract_sha256': ob['contract']['sha256'],
            'submission_sha256': entry['submission_sha256'], 'finding': '独立检查合成测试的完整两月证据及结论边界。',
            'raw_locators': deepcopy(entry['obligations'][0]['raw_evidence_checks'])}
    if reviewer != 'independent-reader':
        with pytest.raises(ValueError, match='第三人'): inv.review(run, 'main', task['task_id'], entry)
        assert inv.task_records(run)[task['task_id']]['state'] == 'submitted'
    else:
        inv.review(run, 'main', task['task_id'], entry)
        assert inv.audit(run)['verified'] == 1


def test_blocked_render_counts_reviewed_and_verified_separately(v4):
    run, task, a = assigned(v4)
    check = a['checks']['conservation']
    check['status'] = 'blocked_external'
    check['blocking'] = {'type': 'missing_internal_data', 'required_record': '完整内部实际发布记录',
        'provider_role': 'internal_data_owner', 'attempted_access': '合成测试中核对现有完整凭证，缺少发布事实。',
        'why_unavailable': '测试没有发布源。', 'decision_impact': '无法确认发布承接守恒。',
        'resume_action': '取得完整发布源后按同一两月范围对账。', 'attempt_evidence_ids': check['evidence_ids']}
    a['quality_result'] = 'blocked_external'; a['business_verdict'] = 'qualified'
    a['status_dimensions'].update(investigation='blocked_external', defect='undetermined', cause='unexplained', acceptability='qualified')
    submit(run, task, a); inv.review(run, 'main', task['task_id'], main_review(run, task))
    result = inv.audit(run)
    assert result['reviewed'] == result['blocked_external'] == 1 and result['verified'] == 0
    rendered = q.render(run)
    content = Path(rendered['report']).read_text()
    assert '1 个范围经实质主审，0 个范围已核实，1 个范围外部受阻' in content
    assert '0 个范围经实质主审' not in content
