"""Business progress must remain honest, complete and navigable in the renderer."""
from collections import Counter
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import acceptance_investigation as investigation
from acceptance_core import write, event_snapshot
from acceptance_render import dashboard, render


class SmallRun:
    def _event_snapshot(self):
        return event_snapshot(self.events_path)

    def __init__(self, path, version='3.1.0'):
        self.path = path
        self.manifest = {'run_id': 'render-fixture', 'method_version': version,
                         'data_month': '2026-07', 'sites': ['cn', 'US']}
        self.events_path = path / 'events.jsonl'
        self.events_path.write_text('')
        write(path / 'plan.json', {'jobs': []})
        write(path / 'candidates.json', [])
        write(path / 'samples.json', [])
        write(path / 'checks.json', [])
        write(path / 'report-content.json', {'summary': ['基础扫描完成，调查证据按义务复核。'], 'findings': []})

    def dispositions(self):
        return {}

    def job_record(self, _):
        return None

    def get_evidence(self, _):
        raise AssertionError('These renderer fixtures do not assert real evidence findings')

    def event(self, kind, **fields):
        with self.events_path.open('a') as f:
            f.write(json.dumps({'type': kind, **fields}) + '\n')


def baseline_validation():
    return {'run_id': 'render-fixture', 'execution_state': 'incomplete', 'business_verdict': 'undetermined',
            'baseline_jobs': {'expected': 2, 'verified': 2},
            'candidates': {'disposed': 0, 'expected': 0},
            'semantic_sample': {'reviewed': 0, 'expected': 0},
            'errors': [], 'limitations': [], 'evidence_count': 0}


def ledger(monkeypatch, run, states):
    obligations, tasks, candidates = {}, {}, []
    for i, state in enumerate(states):
        cid, oid = f'cand-{i:03d}', f'candidate:cand-{i:03d}'
        scope = {'site': 'cn' if i % 2 == 0 else 'US', 'path': '["器材","附件"]',
                 'level': 2, 'month': '2026-07', 'comparison': 'mom'}
        obligations[oid] = {'obligation_id': oid, 'source_type': 'candidate', 'source_id': cid,
            'scope': scope, 'priority': 'high' if i == 0 else 'normal',
            'required_checks': ['scope_baseline', 'four_metric_impact'],
            'requires_impact': True, 'source_evidence_ids': []}
        candidates.append({'candidate_id': cid, **scope, 'priority': obligations[oid]['priority'],
                           'kind': 'result_change', 'evidence_ids': []})
        if state != 'queued':
            tid = 'task-' + f'{i:024x}'
            task = {'task_id': tid, 'agent_id': f'worker-{i}', 'coordinator_id': 'main', 'attempt': 1,
                    'state': 'approved' if state == 'verified' else state, 'obligation_ids': [oid],
                    'worker_dir': 'investigations/workers/' + tid}
            if state in ('verified', 'blocked_external', 'rework'):
                task.update(outcomes={oid: state}, submission_sha256='a' * 64, review_sha256='b' * 64)
            tasks[tid] = task
    counts = dict(Counter(states))
    validation = baseline_validation()
    validation['candidates']['expected'] = len(candidates)
    validation['investigation'] = {'required': True, 'expected_obligations': len(obligations),
        'states': counts, 'verified': counts.get('verified', 0),
        'blocked_external': counts.get('blocked_external', 0), 'unassigned': counts.get('queued', 0),
        'assigned_tasks': len(tasks), 'reviewed': counts.get('verified', 0) + counts.get('blocked_external', 0),
        'substantive_complete': bool(states) and all(s == 'verified' for s in states),
        'errors': [], 'report_covered': counts.get('verified', 0) + counts.get('blocked_external', 0),
        'business_verdicts': {}}
    monkeypatch.setattr(investigation, 'plan_data', lambda _: {'coordinator_id': 'main'})
    monkeypatch.setattr(investigation, 'all_obligations', lambda *args: obligations)
    monkeypatch.setattr(investigation, 'task_records', lambda _: tasks)
    write(run.path / 'candidates.json', candidates)
    write(run.path / 'validation.json', validation)
    return obligations, tasks, validation


def embedded(run):
    page = (run.path / 'audit.html').read_text()
    raw = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1)
    return json.loads(raw), page


def page_assertions(run, assertions):
    """Exercise the real page logic without opening a browser or using a network."""
    harness = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const page=fs.readFileSync(process.argv[1],'utf8');
const raw=page.match(/<script id="data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const script=page.match(/<\/script><script>([\s\S]*?)<\/script>/)[1];
const nodes={};const document={getElementById(id){return nodes[id]??=(id==='data'?{textContent:raw}:{value:'',textContent:'',innerHTML:'',hidden:false,dataset:{}})},querySelectorAll(){return []}};
vm.runInNewContext(script+'\n'+process.argv[2],{document,assert,setTimeout,clearTimeout});
'''
    result = subprocess.run([shutil.which('node'), '-e', harness, str(run.path / 'audit.html'), assertions],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_full_ledger_keeps_normal_priority_and_stage_is_distinct_from_baseline(tmp_path, monkeypatch):
    run = SmallRun(tmp_path)
    ledger(monkeypatch, run, ['queued'] * 73)
    render(run)
    data, page = embedded(run)
    report = (run.path / '报告.md').read_text()
    assert len(data['obligations']) == 73
    assert data['obligations'][-1]['priority'] == 'normal'
    assert data['obligations'][-1]['obligation_id'] == 'candidate:cand-072'
    assert '基础任务 2/2' in report
    assert '实质核实 **0/73**' in report
    assert report.startswith('# 2026-07 大盘数据验收（阶段进度）')
    assert data['presentation']['staged'] is True
    assert '全部调查义务' in page and '主 Agent 审核' in page
    assert data['investigation']['source_obligations']['candidate:cand-072'] == ['candidate:cand-072']


def test_external_block_accepted_by_main_is_not_substantive_completion(tmp_path, monkeypatch):
    run = SmallRun(tmp_path)
    _, tasks, validation = ledger(monkeypatch, run, ['blocked_external'])
    # Rendering remains honest even if an old/post-processing validation label is permissive.
    validation.update(execution_state='limited', business_verdict='qualified')
    write(run.path / 'validation.json', validation)
    render(run)
    data, _ = embedded(run)
    report = (run.path / '报告.md').read_text()
    assert '主审接收 1 项' in report and '外部阻塞 1 项' in report
    assert '实质核实 **0/1**' in report
    assert '阶段进度' in report and '尚不能整体放行' in report
    assert '执行完成但' not in report
    assert data['main_reviews'][0]['outcome_counts'] == {'blocked_external': 1}
    assert data['main_reviews'][0]['submission_sha256'] == next(iter(tasks.values()))['submission_sha256']
    assert data['main_reviews'][0]['review_path'].startswith('investigations/reviews/')


def test_invalid_plan_shows_error_and_unknown_denominator_not_zero(tmp_path, monkeypatch):
    run = SmallRun(tmp_path)
    ledger(monkeypatch, run, [])
    def fail(_):
        raise ValueError('调查计划哈希失效')
    monkeypatch.setattr(investigation, 'plan_data', fail)
    render(run)
    data, _ = embedded(run)
    report = (run.path / '报告.md').read_text()
    assert data['investigation']['counts']['expected_obligations'] is None
    assert any('调查计划哈希失效' in e for e in data['investigation']['errors'])
    assert '调查义务共 待核定 项' in report
    assert '实质核实 **待核定/待核定**' in report
    assert '实质核实 **0/0**' not in report
    assert data['presentation']['staged'] is True


def test_changed_tasks_do_not_reuse_stale_validation_counts(tmp_path, monkeypatch):
    run = SmallRun(tmp_path)
    _, tasks, _ = ledger(monkeypatch, run, ['verified'])
    task = next(iter(tasks.values()))
    task['outcomes'] = {oid: 'blocked_external' for oid in task['obligation_ids']}
    task['state'] = 'blocked_external'
    render(run)
    data, _ = embedded(run)
    assert data['investigation']['counts']['verified'] is None
    assert any('计数不一致' in e for e in data['investigation']['errors'])
    assert data['presentation']['staged'] is True


def test_legacy_v3_report_does_not_invent_new_investigation_coverage(tmp_path, monkeypatch):
    run = SmallRun(tmp_path, version='3.0.1')
    validation = baseline_validation()
    validation['execution_state'] = 'limited'
    write(run.path / 'validation.json', validation)
    def unexpected(_):
        raise AssertionError('Legacy rendering must not require a v3.1 ledger')
    monkeypatch.setattr(investigation, 'plan_data', unexpected)
    render(run)
    data, _ = embedded(run)
    report = (run.path / '报告.md').read_text()
    assert data['investigation']['required'] is False
    assert '## 调查与主审进度' not in report
    assert '受限结果（存在证据或范围限制）' in report
    assert '执行完成但' not in report


def test_missing_validation_fields_never_default_to_pass_or_zero(tmp_path):
    run = SmallRun(tmp_path, version='3.0.1')
    dashboard(run, {'run_id': 'partial'}, {'summary': ['机器核验尚未完整提供']})
    data, _ = embedded(run)
    assert data['presentation']['state'] == '未完成'
    assert data['presentation']['verdict'] == '尚不能整体放行'
    assert data['validation']['baseline_jobs'] == {'expected': None, 'verified': None}
    assert data['validation']['candidates']['expected'] is None


def test_requeued_review_remains_history_and_does_not_count_as_current_verification(tmp_path, monkeypatch):
    run = SmallRun(tmp_path)
    _, tasks, validation = ledger(monkeypatch, run, ['verified'])
    next(iter(tasks.values()))['state'] = 'requeued'
    validation['investigation'].update(states={'queued': 1}, verified=0, unassigned=1,
                                      reviewed=0, report_covered=0, substantive_complete=False)
    write(run.path / 'validation.json', validation)
    render(run)
    data, _ = embedded(run)
    assert data['obligations'][0]['state'] == 'queued'
    assert data['investigation']['counts']['verified'] == 0
    assert data['main_reviews'][0]['state'] == 'requeued'
    assert data['presentation']['staged'] is True


def test_submission_payload_is_not_multiplied_per_candidate(tmp_path, monkeypatch):
    run = SmallRun(tmp_path)
    obligations, tasks, validation = ledger(monkeypatch, run, ['submitted'] * 120)
    # Reuse one task for many obligations, with a payload that must stay out of the dashboard.
    first = next(iter(tasks.values()))
    first['obligation_ids'] = list(obligations)
    first['submission_sha256'] = 'a' * 64
    first['answers'] = [{'obligation_id': oid, 'finding': 'ANSWER_BODY_MUST_REMAIN_IN_FILE' * 500}
                        for oid in obligations]
    tasks.clear()
    tasks[first['task_id']] = first
    validation['investigation']['assigned_tasks'] = 1
    write(run.path / 'validation.json', validation)
    render(run)
    data, page = embedded(run)
    assert len(data['obligations']) == 120 and len(data['investigation_tasks']) == 1
    assert all('answers' not in row and 'submission' not in row for row in data['obligations'])
    assert all('obligation_ids' not in row for row in data['candidates'])
    assert data['investigation_tasks'][0]['obligation_count'] == 120
    assert 'ANSWER_BODY_MUST_REMAIN_IN_FILE' not in page
    assert len(json.dumps(data)) < 120 * 2000


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is optional for the DOM-free navigation check')
def test_pagination_and_search_reach_the_last_normal_priority_obligation(tmp_path, monkeypatch):
    run = SmallRun(tmp_path)
    ledger(monkeypatch, run, ['queued'] * 73)
    render(run)
    page_assertions(run, r'''
assert.match($('count').textContent,/共 73 条/);
page=1;draw();assert.ok($('count').textContent.includes('第 2 / 2 页'));
assert.ok($('tbody').innerHTML.includes('candidate:cand-072'));
$('search').value='cand-072';page=0;draw();
assert.match($('count').textContent,/共 1 条/);
assert.ok($('tbody').innerHTML.includes('normal'));
$('country').value='US';draw();assert.match($('count').textContent,/共 0 条/);
$('country').value='cn';draw();assert.match($('count').textContent,/共 1 条/);
''')


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is optional for the DOM-free status check')
@pytest.mark.parametrize('version', ['3.1.0', '3.0.1'])
def test_candidate_and_sample_pages_use_the_version_appropriate_review_authority(tmp_path, monkeypatch, version):
    run = SmallRun(tmp_path, version)
    obligations, tasks, validation = ledger(monkeypatch, run, ['verified'])
    sample_id, oid = 'sample-001', 'sample:sample-001'
    scope = {'site': 'cn', 'path': '["器材","附件"]', 'level': 2, 'month': '2026-07', 'comparison': None}
    obligations[oid] = {'obligation_id': oid, 'source_type': 'sample', 'source_id': sample_id,
                       'scope': scope, 'priority': 'normal', 'required_checks': ['sku_identity'],
                       'requires_impact': False, 'source_evidence_ids': []}
    tid = 'task-' + '9' * 24
    tasks[tid] = {'task_id': tid, 'agent_id': 'sample-worker', 'coordinator_id': 'main',
                  'attempt': 1, 'state': 'blocked_external', 'obligation_ids': [oid],
                  'outcomes': {oid: 'blocked_external'}, 'submission_sha256': 'a' * 64,
                  'review_sha256': 'b' * 64, 'worker_dir': 'investigations/workers/' + tid}
    write(run.path / 'samples.json', [{'sample_id': sample_id, **scope, 'product_id': 'product-001'}])
    write(run.path / 'sample-reviews.json', [{'sample_ids': [sample_id], 'status': 'checked',
                                           'reason': '旧版有限语义检查', 'evidence_ids': []}])
    old_decision = {'decision_id': 'old-decision', 'candidate_ids': ['cand-000'],
                    'status': 'needs_evidence', 'evidence_ids': []}
    monkeypatch.setattr(run, 'dispositions', lambda: {'cand-000': old_decision})
    validation['semantic_sample'].update(expected=1, reviewed=1)
    validation['candidates']['disposed'] = 1
    validation['investigation'].update(expected_obligations=2, states={'verified': 1, 'blocked_external': 1},
        blocked_external=1, assigned_tasks=2, reviewed=2, report_covered=2, substantive_complete=False)
    if version == '3.0.1':
        del validation['investigation']
    write(run.path / 'validation.json', validation)
    render(run)
    candidate_state = '实质核实' if version == '3.1.0' else 'needs_evidence'
    sample_state = '外部阻塞' if version == '3.1.0' else 'checked'
    page_assertions(run, r'''
tab='candidates';draw();
let cells=$('tbody').innerHTML.match(/<td>[\s\S]*?<\/td>/g);
assert.ok(cells[2].includes(EXPECTED_CANDIDATE));
tab='samples';draw();cells=$('tbody').innerHTML.match(/<td>[\s\S]*?<\/td>/g);
assert.ok(cells[2].includes(EXPECTED_SAMPLE));
$('search').value=EXPECTED_SAMPLE;draw();assert.match($('count').textContent,/共 1 条/);
'''.replace('EXPECTED_CANDIDATE', json.dumps(candidate_state)).replace('EXPECTED_SAMPLE', json.dumps(sample_state)))
    report = (run.path / '报告.md').read_text()
    if version == '3.1.0':
        assert '候选主审接收 1/1' in report and '明细抽样主审接收 1/1' in report
        assert '旧处置/样本索引不决定本版完成状态' in report
