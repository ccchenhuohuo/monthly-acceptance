"""Lossless investigation obligations, isolated workers, and coordinator review.

All mutations are performed by the coordinator under the CLI's run lock. Agent
identifiers provide accountable workflow roles, not an OS authentication boundary.
"""
from collections import Counter
from contextlib import nullcontext
from pathlib import Path
import fcntl
import json
import math
import re
import shutil
import uuid

from acceptance_core import Run, canonical, digest, now, read, write
from acceptance_snapshot import read_bytes, verification_snapshot
from acceptance_protocol import current
import acceptance_review as substantive

METRICS = ('spus', 'bands', 'units', 'amount')
RESULT_CHECKS = ['scope_baseline', 'brand_contribution', 'spu_contribution',
                 'sku_identity', 'price_volume', 'source_mapping',
                 'counterevidence', 'four_metric_impact']
GAP_CHECKS = ['scope_baseline', 'scope_contract', 'sku_destination',
              'source_mapping', 'counterevidence', 'four_metric_impact']
SAMPLE_CHECKS = ['sku_identity', 'price_volume', 'source_mapping', 'counterevidence', 'four_metric_impact']
SEMANTIC_CHECKS = ['sku_identity', 'classification_semantics', 'source_mapping', 'counterevidence', 'four_metric_impact']
# Maintained issue taxonomy, never caller-selected shortcuts. Semantics retains
# scope through the frozen contract/raw identity checks and all four impacts.
DISCOVERED_CHECKS = {**dict.fromkeys(('semantic', 'classification_semantics'), SEMANTIC_CHECKS),
                     **dict.fromkeys(('identity', 'sku_identity'), SAMPLE_CHECKS),
                     **dict.fromkeys(('numerical', 'result_change', 'price_volume'), RESULT_CHECKS),
                     **dict.fromkeys(('gap', 'raw_presence_gap', 'std_presence_gap'), GAP_CHECKS)}
DEEP_CHECKS = {'brand_contribution', 'spu_contribution', 'sku_identity',
               'price_volume', 'sku_destination', 'path_identity',
               'historical_object_match', 'classification_semantics',
               'platform_identity', 'sku_published_trace', 'internal_reconciliation'}
BLOCK_TYPES = {'missing_external_record', 'missing_contract', 'missing_definition',
               'missing_internal_data', 'external_access_denied'}


def enabled(run):
    version = tuple(int(p) for p in run.manifest['method_version'].split('.')[:2])
    return current(run) or version >= (3, 1)


def home(run):
    return run.path / 'investigations'


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('缺少具体说明：' + label)
    return value


def unique(values, label):
    if not isinstance(values, list) or len(values) != len(set(values)):
        raise ValueError('集合缺失或重复：' + label)
    return values


def scope(value):
    result = {k: value.get(k, value.get('first_observed_month') if k == 'month' else None) for k in ('site', 'path', 'level', 'month', 'comparison')}
    # Historical issues can span several distinct comparison windows. Keeping
    # only their first month silently shrinks the mandatory recheck.
    for key in ('months', 'months_incomplete', 'product_ids', 'sku_ids', 'platforms', 'source_layer', 'metrics'):
        if key in value: result[key] = value[key]
        elif key in value.get('legacy_detail', {}): result[key] = value['legacy_detail'][key]
    return result


def obligation(kind, ident, obj, checks, evidence=None, impact=True):
    return {'obligation_id': kind + ':' + ident, 'source_type': kind, 'source_id': ident,
            'source_hash': digest(obj), 'scope': scope(obj),
            'priority': obj.get('priority', 'high' if kind in ('historical', 'project') else 'normal'),
            'required_checks': checks, 'requires_impact': impact,
            'source_evidence_ids': sorted(set(evidence or [])),
            **({'source_material': obj} if kind not in ('candidate', 'sample') else {})}


def expected_obligations(run):
    import acceptance_questions as questions
    if questions.enabled(run):return sorted(questions.obligations(run).values(),key=lambda r:r["obligation_id"])
    """Reconstruct the denominator, irrespective of dispositions or prior approvals."""
    rows = []
    for c in read(run.path / 'candidates.json'):
        checks = RESULT_CHECKS if c['kind'] == 'result_change' else GAP_CHECKS
        if c['kind'] == 'contract_scope_missing':
            checks = ['scope_baseline', 'scope_contract', 'path_identity', 'counterevidence', 'four_metric_impact']
        rows.append(obligation('candidate', c['candidate_id'], c, checks, c['evidence_ids']))
    for s in read(run.path / 'samples.json'):
        rows.append(obligation('sample', s['sample_id'], s,
                    SAMPLE_CHECKS, s['evidence_ids']))
    for filename in ['open-issues.json', 'legacy-issues.json']:
        src = run.path / 'inputs/project' / filename
        if src.exists():
            for issue in read(src):
                # Closed defects and reasonable counterexamples still require regression review.
                issue_type = issue.get('issue_type')
                if issue_type in substantive.NON_OBJECT_GUARDS:
                    rows.append(obligation('historical', issue['issue_id'], issue,
                                ['historical_object_match', 'regression_check', 'counterevidence'], impact=False))
                    continue
                if issue_type == 'historical_population_import_unfinished':
                    rows.append(obligation('historical', issue['issue_id'], issue,
                                ['source_inventory', 'issue_extraction', 'known_problem_recheck'], impact=False))
                    continue
                if issue_type in ('internal_evidence_blocker', 'scope_contract_blocker', 'classification_granularity_limitation'):
                    checks = ['historical_object_match', 'scope_baseline', 'counterevidence',
                              'internal_reconciliation' if issue_type == 'internal_evidence_blocker' else 'scope_contract']
                    rows.append(obligation('historical', issue['issue_id'], issue, checks, impact=False))
                    continue
                rows.append(obligation('historical', issue['issue_id'], issue,
                            ['historical_object_match', 'sku_identity', 'source_mapping', 'counterevidence', 'four_metric_impact']))
    for rel in sorted(run.manifest.get('history_sources', [])):
        from acceptance_history import segment_source
        obj = {'site': 'global', 'path': '[]', 'month': run.manifest['data_month'], 'source_path': rel,
               'sha256': run.manifest['inputs'][rel]['sha256'], 'segments': segment_source(run.path / rel)}
        rows.append(obligation('history_source', digest(rel)[:20], obj,
                              ['source_inventory', 'issue_extraction', 'known_problem_recheck'], impact=False))
    for site in run.manifest['sites']:
        for purpose, checks in [
            ('contract_scope', ['scope_contract', 'path_identity', 'counterevidence']),
            ('named_brands', ['brand_contribution', 'internal_brand_focus', 'counterevidence', 'four_metric_impact']),
            ('internal', ['scope_baseline', 'internal_reconciliation', 'counterevidence']),
            ('published', ['release_source_match', 'sku_published_trace', 'counterevidence'])]:
            obj = {'site': site, 'path': '[]', 'month': run.manifest['data_month'], 'purpose': purpose}
            rows.append(obligation('project', site + ':' + purpose, obj, checks, impact='four_metric_impact' in checks))
        if site == 'cn':
            obj = {'site': site, 'path': '[]', 'month': run.manifest['data_month'], 'purpose': 'cross_platform'}
            rows.append(obligation('project', 'cn:cross_platform', obj,
                        ['platform_identity', 'source_mapping', 'counterevidence', 'four_metric_impact']))
    ids = [r['obligation_id'] for r in rows]
    unique(ids, '基础调查义务')
    if substantive.enabled(run):
        sources = {('candidate', c['candidate_id']): c for c in read(run.path / 'candidates.json')}
        sources.update({('sample', s['sample_id']): s for s in read(run.path / 'samples.json')})
        for row in rows:
            source = sources.get((row['source_type'], row['source_id']), row.get('source_material', {}))
            for singular, plural in (('product_id', 'product_ids'), ('sku_id', 'sku_ids'), ('platform', 'platforms')):
                value = source.get(singular, source.get('legacy_detail', {}).get(singular))
                if plural not in row['scope'] and value is not None: row['scope'][plural] = [str(value)]
            row['contract'] = substantive.contract(run.manifest, row, source)
    return sorted(rows, key=lambda r: r['obligation_id'])


def prepare(run, coordinator):
    import acceptance_questions as questions
    if questions.enabled(run):return questions.prepare(run,coordinator)
    run.writable(); text(coordinator, '主Agent ID')
    freezes=[json.loads(line) for line in run._event_snapshot().splitlines() if json.loads(line)['type']=='independent_scan.frozen']
    if not freezes:
        raise ValueError('先完成并冻结独立扫描，再分派调查')
    if freezes[-1].get('candidate_hash') != digest(read(run.path/'candidates.json')):
        raise ValueError('调查输入不等于已冻结的完整候选')
    obligations = expected_obligations(run)
    payload = {'protocol': 2 if substantive.enabled(run) else 1, 'coordinator_id': coordinator, 'obligations': obligations,
               'candidate_hash': digest(read(run.path / 'candidates.json')),
               'sample_hash': digest(read(run.path / 'samples.json')),
               'policy': run.policy['investigation_workflow']}
    p = home(run) / 'plan.json'
    if p.exists():
        previous = read(p)
        if previous['payload'] != payload: raise ValueError('调查计划已冻结，不得缩减或替换分母/主Agent')
        return previous
    value = {'payload': payload, 'sha256': digest(payload), 'created_at': now()}
    write(p, value); run.event('investigation.plan.frozen', plan_hash=value['sha256'], obligations=len(obligations), coordinator_id=coordinator)
    return value


def plan_data(run):
    import acceptance_questions as questions
    if questions.enabled(run):return questions.plan_data(run)
    value = read(home(run) / 'plan.json')
    if digest(value['payload']) != value['sha256']: raise ValueError('调查计划哈希失效')
    if not any(json.loads(line).get('plan_hash') == value['sha256'] for line in run._event_snapshot().splitlines()
               if json.loads(line)['type'] == 'investigation.plan.frozen'):
        raise ValueError('调查计划缺冻结事件')
    payload = value['payload']
    if payload['obligations'] != expected_obligations(run): raise ValueError('调查义务分母改变或遗漏')
    if payload['policy'] != run.policy['investigation_workflow']: raise ValueError('调查政策改变')
    return payload


def coordinator_only(run, actor):
    run.writable()
    p = plan_data(run)
    if actor != p['coordinator_id']: raise ValueError('仅登记的主Agent可更新中心任务与审核')
    return p


def task_path(run, task_id):
    if not re.fullmatch(r'task-[0-9a-f]{24}', task_id): raise ValueError('非法调查任务ID')
    return home(run) / 'tasks' / (task_id + '.json')


def task_records(run):
    latest = {}
    for line in run._event_snapshot().splitlines():
        event = json.loads(line)
        if event['type'] == 'investigation.task.saved': latest[event['task_id']] = event['task_hash']
    result = {}
    for p in sorted((home(run) / 'tasks').glob('*.json')):
        value = read(p)
        if value.get('state') not in {'assigned', 'submitted', 'rework', 'approved', 'blocked_external',
                                     'requeued', 'failed', 'timeout', 'capacity_pending', 'returned'}:
            raise ValueError('未知调查任务状态不能计完成')
        if digest(value) != latest.get(value['task_id']): raise ValueError('任务文件缺事件或已改变：' + p.name)
        if value.get('progress_path'):
            progress = read(home(run) / value['progress_path'])
            if digest(progress) != value['progress_sha256']: raise ValueError('保存的失败/超时/容量断点被修改')
            if value['state'] in ('failed', 'timeout', 'capacity_pending', 'returned', 'requeued'):
                root = (run.path / value['worker_dir']).resolve()
                for rel, sha in progress['worker_files_sha256'].items():
                    item = (root / rel).resolve()
                    if not item.is_relative_to(root) or not item.is_file() or digest(item.read_bytes()) != sha:
                        raise ValueError('已保存断点的原始工作文件改变，不能伪装可靠续跑进度')
        for entry in value.get('residual_followup_records', {}).values():
            path = (home(run) / entry['path']).resolve()
            if not path.is_relative_to(home(run)) or not path.is_file() or digest(read(path)) != entry['sha256']:
                raise ValueError('程序生成的重大残余续查任务被删除或修改')
        result[value['task_id']] = value
    if set(latest) != set(result): raise ValueError('已登记调查任务被删除')
    return result


def save_task(run, task, action):
    write(task_path(run, task['task_id']), task)
    run.event('investigation.task.saved', task_id=task['task_id'], task_hash=digest(task),
              state=task['state'], action=action, attempt=task['attempt'], agent_id=task['agent_id'])


def extra_obligations(run):
    result = []
    for line in run._event_snapshot().splitlines():
        e = json.loads(line)
        if e['type'] == 'investigation.obligations.added':
            value = read(home(run) / 'additions' / (e['addition_id'] + '.json'))
            if digest(value) != e['sha256']: raise ValueError('新增调查义务变更')
            result.extend(value['obligations'])
    unique([r['obligation_id'] for r in result], '新增调查义务')
    return result


def all_obligations(run, p=None):
    import acceptance_questions as questions
    if questions.enabled(run):return questions.obligations(run)
    p = p or plan_data(run)
    rows = p['obligations'] + extra_obligations(run)
    unique([r['obligation_id'] for r in rows], '完整调查义务')
    result = {r['obligation_id']: r for r in rows}
    for line in run._event_snapshot().splitlines():
        event = json.loads(line)
        if event['type'] != 'investigation.contract.amended': continue
        change = read(home(run) / 'amendments' / (event['amendment_id'] + '.json'))
        if digest(change) != event['sha256']: raise ValueError('调查合同变更记录被修改')
        oid = change['obligation_id']; original = result[oid]
        if original['contract']['sha256'] != change['previous_contract_sha256'] or change['reviewer_id'] != p['coordinator_id']:
            raise ValueError('调查合同变更未承接当前版本或未经主审')
        result[oid] = {**original, 'scope': change['scope'], 'contract': change['contract']}
    return result


def amend_contract_scope(run, actor, obligation_id, new_scope, reason, evidence_locators):
    import acceptance_questions as questions
    if questions.enabled(run):raise ValueError("当前协议通过 question-register 版本化修改完整议题范围")
    """Keep original denominator; only the registered coordinator may amend scope.

    Typed numerical candidates keep their exact comparison contract. Extensions
    for them must become separately tracked discovered obligations.
    """
    coordinator_only(run, actor); text(reason, '范围变更主审理由')
    if not substantive.enabled(run): raise ValueError('合同变更需要3.2协议')
    ob = all_obligations(run)[obligation_id]
    if any(obligation_id in t['obligation_ids'] and t['state'] != 'requeued' for t in task_records(run).values()):
        raise ValueError('先保存进度并重排所有受影响任务，再变更合同；旧审核不沿用')
    substantive.evidence_links(run.get_evidence, evidence_locators, ob)
    if ob['source_type'] == 'candidate':
        raise ValueError('固定候选比较不能改分母；扩展范围应新增承接义务')
    if not isinstance(new_scope, dict) or set(new_scope) != set(ob['scope']): raise ValueError('范围变更不得静默删除维度')
    if new_scope.get('site') not in run.manifest['sites'] + ['global']: raise ValueError('范围变更国家超出冻结总体')
    updated = {**ob, 'scope': new_scope}
    contract = substantive.contract(run.manifest, updated)
    contract['original_scope'] = ob['contract']['original_scope']
    contract['supersedes_contract_sha256'] = ob['contract']['sha256']
    contract['sha256'] = digest({k: v for k, v in contract.items() if k != 'sha256'})
    aid = uuid.uuid4().hex
    value = {'obligation_id': obligation_id, 'previous_contract_sha256': ob['contract']['sha256'],
             'scope': new_scope, 'contract': contract, 'reason': reason,
             'evidence_locators': evidence_locators, 'reviewer_id': actor, 'created_at': now()}
    write(home(run) / 'amendments' / (aid + '.json'), value)
    run.event('investigation.contract.amended', amendment_id=aid, sha256=digest(value), obligation_id=obligation_id)
    return value


def add_obligations(run, actor, issues):
    import acceptance_questions as questions
    if questions.enabled(run):raise ValueError("当前协议请使用 question-register 登记具体议题；观察不得自动生成义务")
    coordinator_only(run, actor); existing = all_obligations(run)
    added = []
    for issue in issues:
        text(issue.get('question'), '新增事项问题'); text(issue.get('discovered_from'), '发现来源')
        if issue.get('site') not in run.manifest['sites'] + ['global']: raise ValueError('新增事项站点超范围')
        for eid in issue.get('evidence_ids', []): run.get_evidence(eid)
        if not issue.get('evidence_ids'): raise ValueError('新增事项必须带发现证据')
        checks = RESULT_CHECKS
        if substantive.enabled(run):
            checks = DISCOVERED_CHECKS.get(issue.get('issue_type'))
            if checks is None: raise ValueError('新增事项须使用维护的语义/身份、数值或缺口issue_type，不可任选核查动作')
        if issue.get('required_checks', checks) != checks: raise ValueError('新增问题不能自行缩减所属问题类型的调查动作')
        row = obligation('discovered', digest(issue)[:24], issue, list(checks), issue['evidence_ids'])
        if substantive.enabled(run):
            for singular, plural in (('product_id','product_ids'), ('sku_id','sku_ids'), ('platform','platforms')):
                if singular in issue and plural not in row['scope']: row['scope'][plural] = [str(issue[singular])]
            row['contract'] = substantive.contract(run.manifest, row, issue)
            observation = issue.get('source_observation')
            substantive.locator(run.get_evidence, observation, row)
            if observation['evidence_id'] not in issue['evidence_ids']:
                raise ValueError('新增事项原始发现定位未进入证据清单')
            row['source_observation_sha256'] = digest(observation)
            if issue.get('coverage_requirement_id') and not re.fullmatch(r'[0-9a-f]{64}', issue.get('coverage_observation_sha256','')):
                raise ValueError('抽查漏检必须绑定原始coverage observation哈希')
        if row['obligation_id'] not in existing:
            added.append(row); existing[row['obligation_id']] = row
    if not added: return []
    aid = uuid.uuid4().hex; value = {'issues': issues, 'obligations': added, 'actor': actor, 'created_at': now()}
    write(home(run) / 'additions' / (aid + '.json'), value)
    run.event('investigation.obligations.added', addition_id=aid, sha256=digest(value), count=len(added))
    return [r['obligation_id'] for r in added]


def assign(run, coordinator, agent, ids=None, assignment_reason=None):
    p = coordinator_only(run, coordinator); text(agent, '子Agent ID')
    if agent == coordinator: raise ValueError('调查子Agent与主审核Agent必须不同')
    tasks = task_records(run); obligations = all_obligations(run, p)
    active = [t for t in tasks.values() if t['state'] in ('assigned', 'submitted')]
    if len(active) >= p['policy']['max_parallel_agents']: raise ValueError('已达到并行容量，保留队列等待可用容量')
    if any(t['agent_id'] == agent for t in active): raise ValueError('该子Agent仍有活跃任务')
    owners = {oid: t for t in tasks.values() if t['state'] != 'requeued' for oid in t['obligation_ids']}
    import acceptance_triage as triage
    routing = triage.status(run) if triage.enabled(run) else None
    deferred = set(routing['deferred_obligation_ids']) if routing else set()
    if ids is not None and set(ids) & deferred: raise ValueError('已审核暂留义务不会自动深查；先重新分类为investigate')
    import acceptance_questions as questions
    question_mode = questions.enabled(run)
    assignment_source = 'explicit_ids' if ids is not None else ('question_scope_queue' if question_mode else 'priority_queue')
    routed_cases = {}
    if substantive.enabled(run) and ids is None and run.policy.get('case_workflow', {}).get('enabled') and not run.policy.get('question_workflow',{}).get('enabled'):
        import acceptance_cases
        if not (acceptance_cases.home(run) / 'state.json').exists():
            raise ValueError('先建立案件映射、共享完整明细与机器诊断路由，再按残余分派；有界历史核查可显式指定IDs')
        registry = acceptance_cases.load(run)
        if registry.get('budget', {}).get('state') == 'exhausted':
            raise ValueError('调查容量/预算耗尽；保留容量队列和断点，不标成外部缺证')
        routed_cases = {oid: {'case_id': c['case_id'], 'case_version': c['case_version'], 'route': c['route']}
                        for c in registry['cases'] if c['route'] in ('needs_investigation', 'rule_disposition_pending_review')
                        for oid in c['obligation_ids']}
        assignment_source = 'machine_residual_case_queue'
    if routing and ids is None:
        routed_cases = {oid: c for c in routing['cases'] if c['investigation_route'] != 'defer' and not (c.get('merge_reference') or {}).get('waiting_for_shared_task') for oid in c['obligation_ids']}
        assignment_source = 'business_classification_queue'
    if ids is None:
        queue = sorted((r for oid, r in obligations.items() if oid not in owners and oid not in deferred),
                       key=lambda r: (r['priority'] != 'high', r['scope']['month'] != run.manifest['data_month'], r['obligation_id']))
        if assignment_source == 'machine_residual_case_queue': queue = [r for r in queue if r['obligation_id'] in routed_cases]
        if routing:
            queue = [r for r in queue if r['obligation_id'] in routed_cases]
            # Keep shared questions together within the existing task capacity.
            def group(r):
                c = routed_cases[r['obligation_id']]
                return (c['report_placement'] != 'main',
                        (c.get('merge_reference') or {}).get('target_case_id', c['case_id']))
            queue.sort(key=group)
        ids = [r['obligation_id'] for r in queue[:p['policy']['max_obligations_per_assignment']]]
    unique(ids, '分派义务')
    if not ids or len(ids) > p['policy']['max_obligations_per_assignment']: raise ValueError('分派为空或超过单任务容量')
    if any(oid not in obligations or oid in owners for oid in ids): raise ValueError('义务不存在或已有有效负责任务')
    tid = 'task-' + uuid.uuid4().hex[:24]
    old = [t for t in tasks.values() if set(ids) & set(t['obligation_ids'])]
    task = {'task_id': tid, 'obligation_ids': ids, 'agent_id': agent, 'coordinator_id': coordinator,
            'attempt': max((t['attempt'] for t in old), default=0) + 1,
            'previous_task_ids': [t['task_id'] for t in old], 'state': 'assigned', 'created_at': now()}
    if substantive.enabled(run):
        task['assignment_source'] = assignment_source
        task['assignment_reason'] = text(assignment_reason or ('显式选定完整义务进行有界调查；未缩减原合同' if assignment_source == 'explicit_ids'
                                                             else '依据当前议题优先级领取完整国家与事件范围' if question_mode
                                                             else '依据机器诊断残余及正式规则路由领取完整义务'), '分派原因')
        task['case_bindings'] = {oid: routed_cases[oid] for oid in ids if oid in routed_cases}
        task['contract_hashes'] = {oid: obligations[oid]['contract']['sha256'] for oid in ids}
        task['unresolved_return_items'] = [item for previous in old for item in previous.get('return_items', [])
                                           if item['obligation_id'] in ids]
        task['resume_checkpoints'] = [{'task_id': previous['task_id'], 'worker_dir': previous['worker_dir'],
                                      'progress_path': previous['progress_path'], 'progress_sha256': previous['progress_sha256']}
                                     for previous in old if previous.get('progress_path')]
        task['previous_followups'] = [entry for previous in old for oid, entry in previous.get('residual_followup_records', {}).items()
                                     if oid in ids and entry['pending_count']]
    if routing:
        task['business_questions'] = [c for c in routing['cases'] if set(c['obligation_ids']) & set(ids)]
        task['shared_query_rule'] = '主包先统一取证；按原月份/基期重新生成SQL任务计划，成功结果复用，只补未覆盖月份/身份。共享不免除各义务回答。'
    worker_dir = home(run) / 'workers' / tid
    worker_dir.mkdir(parents=True)
    shutil.copytree(run.path / 'inputs', worker_dir / 'inputs')
    manifest = {**run.manifest, 'run_id': run.manifest['run_id'] + '-' + tid,
                'parent_run': str(run.path), 'worker_task': tid, 'worker_agent': agent,
                'created_at': now()}
    write(worker_dir / 'run.json', manifest)
    for directory in ['evidence', 'records', 'queries', 'decisions', 'findings']:
        (worker_dir / directory).mkdir()
    worker = Run(worker_dir); worker.event('worker.assigned', task_id=tid, agent_id=agent)
    task['worker_dir'] = str(worker_dir.relative_to(run.path))
    candidates = {c['candidate_id']: c for c in read(run.path / 'candidates.json')}
    samples = {s['sample_id']: s for s in read(run.path / 'samples.json')}
    selected_candidates = [candidates[obligations[oid]['source_id']] for oid in ids if obligations[oid]['source_type'] == 'candidate']
    write(worker_dir / 'candidates.json', selected_candidates)
    brief = {'task': task, 'obligations': [obligations[oid] for oid in ids],
             'source_candidates': selected_candidates,
             'source_samples': [samples[obligations[oid]['source_id']] for oid in ids if obligations[oid]['source_type'] == 'sample'],
             'parent_evidence_root': str(run.path),
             'source_reference_rule': '主包已有证据用 main:ev-...；子包新证据用 ev-...。每个义务独立回答全部required_checks，不能自行审核。'}
    write(worker_dir / 'brief.json', brief)
    save_task(run, task, 'assigned')
    return task


def requeue(run, actor, task_id, reason):
    coordinator_only(run, actor); text(reason, '退回/重派原因')
    t = task_records(run)[task_id]
    if t['state'] == 'requeued': raise ValueError('该任务已重新入队')
    if substantive.enabled(run) and t['state'] == 'assigned' and not t.get('progress_sha256'):
        raise ValueError('运行中任务须先保存实际进度/剩余工作再重新排队')
    t = {**t, 'previous_state': t['state'], 'state': 'requeued', 'requeue_reason': reason, 'requeued_at': now()}
    save_task(run, t, 'requeued')
    return t


def record_progress(run, actor, task_id, state, progress):
    """Save failure/timeout/capacity checkpoints without creating completion credit."""
    coordinator_only(run, actor); task = task_records(run)[task_id]
    if state not in ('failed', 'timeout', 'capacity_pending', 'returned'):
        raise ValueError('进度状态必须区分失败、超时、容量待排或退回')
    if task['state'] in ('approved', 'requeued'): raise ValueError('终态任务不能覆盖为运行进度')
    if not isinstance(progress, dict): raise ValueError('缺少可续跑的具体进度')
    for key in ('reason', 'resume_action'): text(progress.get(key), '进度.'+key)
    if not isinstance(progress.get('attempted_actions'), list) or not progress['attempted_actions']:
        raise ValueError('进度需记录实际尝试，不能把预算耗尽包装成外部缺证')
    for action in progress['attempted_actions']: text(action, '实际尝试动作')
    remaining = progress.get('remaining_work')
    if not isinstance(remaining, dict) or set(remaining) != set(task['obligation_ids']):
        raise ValueError('续跑记录必须保留全部原始义务及剩余工作')
    for oid, actions in remaining.items():
        if not isinstance(actions, list) or not actions: raise ValueError('不能通过空剩余清单伪装已完成')
        for action in actions: text(action, oid+'剩余动作')
    worker = Run(run.path / task['worker_dir'])
    for eid in unique(progress.get('evidence_ids', []), '进度已有证据'):
        run.get_evidence(eid[5:]) if eid.startswith('main:') else worker.get_evidence(eid)
    files = {str(p.relative_to(worker.path)): digest(p.read_bytes()) for p in worker.path.rglob('*')
             if p.is_file() and 'inputs' not in p.relative_to(worker.path).parts and p.name != '.lock'}
    value = {'task_id': task_id, 'attempt': task['attempt'], 'state': state, 'progress': progress,
             'worker_files_sha256': files, 'created_at': now()}
    checkpoint_id = uuid.uuid4().hex
    relative = 'progress/' + checkpoint_id + '.json'
    write(home(run) / relative, value)
    task = {**task, 'state': state, 'progress_path': relative, 'progress_sha256': digest(value)}
    save_task(run, task, 'progress_saved')
    return task


def finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('影响必须是非负有限数值：' + label)


def entity_assessments(answer):
    keys = [key for key in ('population_proof', 'gap_proof') if key in answer]
    if len(keys) > 1: raise ValueError('不能混用不同类型的完整对象证明，调查与主审须核对同一组实体')
    proof = answer[keys[0]] if keys else {}
    if not isinstance(proof, dict): raise ValueError('完整对象证明必须是对象')
    return proof.get('entity_assessments', [])


def verify_answer(run, ob, answer, evidence_lookup):
    import acceptance_questions as questions
    if questions.enabled(run) and ob["source_type"]=="question_scope":return questions.verify_answer(run,ob,answer,evidence_lookup)
    for eid in substantive.referenced_evidence_ids(answer): evidence_lookup(eid)
    if answer.get('scope') != ob['scope']: raise ValueError('答卷国家/路径/月份/比较关系与义务不一致')
    checks = answer.get('checks', {})
    if set(checks) != set(ob['required_checks']): raise ValueError('调查动作未逐项回答或自行缩减')
    verdict = answer.get('business_verdict')
    if verdict not in ('pass', 'qualified', 'reject', 'undetermined'): raise ValueError('无效业务判断')
    if answer.get('repair_state') not in ('not_needed', 'pending', 'retested'): raise ValueError('必须独立记录修复进度')
    text(answer.get('conclusion'), '具体结论'); text(answer.get('next_action'), '下一动作')
    blocked = []
    for name, item in checks.items():
        if item.get('status') not in ('verified', 'not_applicable', 'blocked_external'): raise ValueError('不接受仅登记/预算耗尽/待核作为完成调查')
        text(item.get('finding'), name + '的事实'); text(item.get('counterevidence'), name + '的反证')
        if not isinstance(item.get('actions'), list) or not item['actions']: raise ValueError('缺少实际完成的动作：' + name)
        for action in item['actions']: text(action, name + '的动作')
        eids = unique(item.get('evidence_ids'), name + '证据')
        if not eids: raise ValueError('每项调查动作须引用证据')
        for eid in eids: evidence_lookup(eid)
        if name in DEEP_CHECKS and item['status'] == 'verified' and set(eids) <= set(ob['source_evidence_ids']):
            raise ValueError('深度调查仅引用聚合触发证据：' + name)
        if item['status'] == 'not_applicable':
            text(item.get('not_applicable_reason'), '不适用理由')
            if name in ('scope_baseline', 'four_metric_impact', 'source_inventory', 'issue_extraction'):
                raise ValueError('该核心动作不可豁免：' + name)
        if item['status'] == 'blocked_external':
            b = item.get('blocking', {})
            if b.get('type') not in BLOCK_TYPES: raise ValueError('运行错误、时间或预算耗尽不是外部证据阻塞')
            for key in ('required_record', 'provider_role', 'attempted_access', 'why_unavailable', 'decision_impact', 'resume_action'):
                text(b.get(key), '阻塞.' + key)
            blockers = unique(b.get('blocked_checks'), '受阻动作')
            if not blockers or not set(blockers) <= set(checks): raise ValueError('阻塞必须精确列明受阻动作')
            blocked.append(name)
    # Internal checks cannot disappear behind a generic external dependency.
    if blocked:
        claimed = set().union(*(set(checks[n]['blocking']['blocked_checks']) for n in blocked))
        if claimed != set(blocked): raise ValueError('阻塞范围与实际未核实动作不相等')
        if not any(i['status'] == 'verified' for i in checks.values()): raise ValueError('不能把全部调查动作统一挂外部阻塞')
        if verdict == 'pass': raise ValueError('外部阻塞不能放行')
    if ob['requires_impact']:
        impacts = answer.get('impact', {})
        if set(impacts) != set(METRICS): raise ValueError('必须分别量化SPU、价格带、销量、金额影响')
        for metric, item in impacts.items():
            text(item.get('basis'), '影响分母/' + metric); text(item.get('unit'), '影响单位/' + metric)
            eids = unique(item.get('evidence_ids'), '影响证据/' + metric)
            if not eids: raise ValueError('影响数值缺计算证据')
            for eid in eids: evidence_lookup(eid)
            if item.get('state') == 'unknown':
                if checks['four_metric_impact']['status'] != 'blocked_external': raise ValueError('影响未知不能记为已完成核实')
                text(item.get('missing'), '未知影响缺少什么')
                continue
            if item.get('state') != 'measured': raise ValueError('影响须为measured或确有阻塞的unknown')
            finite_number(item.get('exposure'), metric + '.exposure')
            finite_number(item.get('unexplained'), metric + '.unexplained')
            if item['unexplained'] > item['exposure']: raise ValueError('未解释影响超过定义的总暴露')
            if not blocked:
                policy = run.policy['investigation_workflow']
                limit = policy['max_unexplained_band_pp'] if metric == 'bands' else item['exposure'] * policy['max_unexplained_ratio']
                if item['unexplained'] > limit + 1e-12: raise ValueError('未解释残余超过冻结门槛，须继续调查或明确真实阻塞：' + metric)
    if answer.get('defect_confirmed') is True and verdict == 'pass': raise ValueError('已确认缺陷不能直接放行')
    if ob['source_type'] == 'history_source':
        from acceptance_history import verify_history_coverage
        concrete = {oid for oid, row in all_obligations(run).items() if row['source_type'] != 'history_source'}
        verify_history_coverage(ob['source_material']['segments'], answer.get('history_coverage'), concrete)
    elif ob.get('source_material', {}).get('issue_type') == 'historical_population_import_unfinished':
        coverage = answer.get('imported_obligation_ids')
        unique(coverage, '历史完整对象导入')
        if not coverage or not set(coverage) <= set(all_obligations(run)) or ob['obligation_id'] in coverage:
            raise ValueError('历史完整对象尚未逐项导入，不能只关闭导入任务')
        text(answer.get('inventory_reconciliation'), '历史完整清单与导入义务逐对象对账')
    substantive.validate_response(run, ob, answer, evidence_lookup, bool(blocked))
    return bool(blocked)


def submit(run, actor, task_id):
    coordinator_only(run, actor)
    t = task_records(run)[task_id]
    with (run.path / t['worker_dir'] / '.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('子Agent仍在写取证记录，须停止后再提交')
        import acceptance_questions as questions
        if questions.enabled(run):
            with verification_snapshot(run) as snapshot:
                return _submit_locked(run, actor, task_id, snapshot=snapshot)
        return _submit_locked(run, actor, task_id)


def _submit_locked(run, actor, task_id, *, snapshot=None):
    coordinator_only(run, actor); t = task_records(run)[task_id]
    if t['state'] != 'assigned': raise ValueError('任务不在当前可提交状态，过期/退回任务不能覆盖新任务')
    worker = Run(run.path / t['worker_dir'])
    if worker.verify_inputs() or worker.verify_events(): raise ValueError('子包输入或事件链无效')
    if worker.manifest.get('worker_task') != task_id or worker.manifest.get('worker_agent') != t['agent_id'] or worker.manifest.get('parent_run') != str(run.path):
        raise ValueError('子包执行身份与中心分派不一致')
    initial_submission_bytes = read_bytes(worker.path / 'submission.json')
    initial_events_bytes = read_bytes(worker.events_path)
    raw = json.loads(initial_submission_bytes)
    if raw.get('task_id') != task_id or raw.get('attempt') != t['attempt'] or raw.get('agent_id') != t['agent_id']:
        raise ValueError('答卷不属于当前任务/子Agent/尝试')
    if any(k in raw for k in ('approved', 'main_review', 'reviewer_id', 'population_proofs')): raise ValueError('子Agent不得提交自行批准字段或覆盖服务端机算证明')
    answers = raw.get('answers', [])
    ids = unique([a.get('obligation_id') for a in answers], '答卷义务')
    if set(ids) != set(t['obligation_ids']): raise ValueError('答卷漏答或包含未分派义务')
    observations = all_obligations(run)
    imports = {}
    imported_hashes = {}
    def lookup(eid):
        if eid.startswith('main:'): return run.get_evidence(eid[5:])
        value = worker.get_evidence(eid); imports[eid] = value
        evidence_bytes = read_bytes(worker.path / 'evidence' / (eid + '.json'))
        if json.loads(evidence_bytes) != value: raise ValueError('导入期间子证据发生变化')
        imported_hashes[eid] = digest(evidence_bytes)
        return value
    lookup.receipt_origin = lambda eid: (run, eid[5:]) if eid.startswith('main:') else (worker, eid)
    # Validate structure before importing any evidence; normalize main IDs for depth checks.
    normalized = json.loads(canonical(raw))
    candidates = {c['candidate_id']: c for c in read(run.path / 'candidates.json')}
    proofs = {}
    proof_file_hashes = {}
    parent_proof_file_hashes = {}
    for a in answers:
        ob = observations[a['obligation_id']]
        if substantive.enabled(run):
            if t['contract_hashes'][a['obligation_id']] != ob['contract']['sha256']:
                raise ValueError('分派后调查合同发生变更，旧答卷不得沿用')
            expected_returns = {r['return_id'] for r in t.get('unresolved_return_items', []) if r['obligation_id'] == a['obligation_id']}
            replies = a.get('return_responses', [])
            unique([r.get('return_id') for r in replies], '审核退回补查回应')
            if {r.get('return_id') for r in replies} != expected_returns:
                raise ValueError('前次审核退回项未逐项补查回应，不能计完成')
            for reply in replies:
                text(reply.get('response'), '审核退回的实际补查回应')
                substantive.evidence_links(lookup, reply.get('evidence_locators'), ob)
        candidate = candidates.get(ob['source_id']) if ob['source_type'] == 'candidate' else None
        kind = candidate['kind'] if candidate else None
        proof_key = 'population_proof' if kind == 'result_change' else 'gap_proof' if kind in ('raw_presence_gap', 'std_presence_gap') else None
        supplied_proofs = {key for key in ('population_proof', 'gap_proof') if key in a}
        if supplied_proofs != ({proof_key} if proof_key else set()):
            raise ValueError('完整对象证明类型必须与信号一致，不能混用、缺失或附加无关证明；要求：' + (proof_key or '不适用'))
        v32 = substantive.enabled(run)
        if not v32 or not proof_key: verify_answer(run, ob, a, lookup)
        if candidate and candidate['kind'] in ('result_change', 'raw_presence_gap', 'std_presence_gap'):
            if candidate['kind'] == 'result_change':
                from acceptance_investigation_proof import verify_result_proof
                proof = verify_result_proof(run.manifest, candidate, a, worker, source_lookup=run.get_evidence, check_claims=not v32)
            else:
                from acceptance_gap_proof import verify_gap_proof
                proof = verify_gap_proof(run.manifest, candidate, a, worker, run.get_evidence, check_claims=not v32)
            if v32:
                # Persist derived work before refusing a prematurely complete
                # answer. These are continuations of original obligations, not
                # new denominator entries or business closure.
                requirements = proof.get('continuation_requirements', [])
                oid = a['obligation_id']
                if requirements or oid in t.get('residual_followup_records', {}):
                    payload = {'task_id': task_id, 'attempt': t['attempt'], 'obligation_id': oid,
                               'contract_sha256': ob['contract']['sha256'], 'proof_sha256': digest(proof),
                               'worker_submission_sha256': digest(initial_submission_bytes),
                               'computed_impact': proof['computed_impact'], 'continuation_requirements': requirements}
                    rel = 'followups/' + digest(payload) + '.json'
                    write(home(run) / rel, payload)
                    entries = dict(t.get('residual_followup_records', {}))
                    entries[oid] = {'path': rel, 'sha256': digest(payload), 'pending_count': len(requirements)}
                    t = {**t, 'residual_followup_records': entries}
                    save_task(run, t, 'residual_continuations_generated')
                substantive.verify_computed_claims(run.manifest, a, proof)
                verify_answer(run, ob, a, lookup)
            if proof['cohort']['not_assessed']:
                raise ValueError('完整对象仍有未调查事项，不能用部分外部阻塞或数值容忍代替全部内部核查')
            if substantive.enabled(run) and proof['cohort']['blocked_external'] and not any(c['status'] == 'blocked_external' for c in a['checks'].values()):
                raise ValueError('实体仍有外部缺证，不能以低影响把未知身份/风险伪装为已核完毕')
            # Every typed page and entity explanation is part of the review,
            # regardless of the citations the worker put in its prose answer.
            for eid in proof['evidence_ids']: lookup(eid)
            for group in entity_assessments(a):
                for eid in group['evidence_ids']: lookup(eid)
            proofs[a['obligation_id']] = proof
            for rel, sha in proof['job_files_sha256'].items():
                if rel in proof_file_hashes and proof_file_hashes[rel] != sha:
                    raise ValueError('不同义务证明之间子证据发生变化')
                proof_file_hashes[rel] = sha
            for rel, sha in proof.get('parent_files_sha256', {}).items():
                path = (run.path / rel).resolve()
                if not path.is_relative_to(run.path) or not path.is_file(): raise ValueError('共享完整明细父包证明文件越界/缺失')
                if rel in parent_proof_file_hashes and parent_proof_file_hashes[rel] != sha:
                    raise ValueError('不同义务共享明细版本不一致')
                parent_proof_file_hashes[rel] = sha
    normalized['population_proofs'] = proofs
    for a in normalized['answers']:
        if a['obligation_id'] in proofs:
            a['impact'] = proofs[a['obligation_id']]['computed_impact']
    id_map = {}
    # The same snapshot spans both verifications. Only our own locked journal
    # appends may advance; source files and old events remain byte-bound.
    with snapshot.event_appends(run.events_path) if snapshot and imports else nullcontext():
        for eid, value in imports.items():
            id_map[eid] = run.evidence(value, kind='investigation_worker', source={'task_id': task_id, 'agent_id': t['agent_id'],
                              'worker_evidence_id': eid, 'worker_evidence_sha256': digest(read_bytes(worker.path / 'evidence' / (eid + '.json')))})
    normalized = substantive.rewrite_evidence_references(normalized,
        lambda eid: id_map.get(eid,eid[5:] if eid.startswith('main:') else eid))
    for a in normalized['answers']: verify_answer(run, observations[a['obligation_id']], a, run.get_evidence)
    if read_bytes(worker.path / 'submission.json') != initial_submission_bytes or read_bytes(worker.events_path) != initial_events_bytes:
        raise ValueError('导入期间子答卷或事件变化，必须重新提交')
    for eid, sha in imported_hashes.items():
        if digest(read_bytes(worker.path / 'evidence' / (eid + '.json'))) != sha:
            raise ValueError('导入期间子证据发生变化，必须重新提交')
    for rel, sha in proof_file_hashes.items():
        if digest(read_bytes(worker.path / rel)) != sha:
            raise ValueError('导入期间完整对象证明发生变化，必须重新提交')
    for rel, sha in parent_proof_file_hashes.items():
        if digest(read_bytes(run.path / rel)) != sha:
            raise ValueError('导入期间共享完整明细/诊断版本改变，必须重新提交')
    submission = {'payload': normalized, 'sha256': digest(normalized), 'original_sha256': digest(raw),
                  'worker_file_sha256': digest(initial_submission_bytes),
                  'worker_events_sha256': digest(initial_events_bytes),
                  'worker_evidence_hashes': imported_hashes,
                  'worker_proof_file_hashes': proof_file_hashes,
                  **({'parent_proof_file_hashes': parent_proof_file_hashes} if substantive.enabled(run) else {}),
                  'submitted_at': now(), 'evidence_map': id_map}
    if snapshot is not None:snapshot.finish()
    sp = home(run) / 'submissions' / (task_id + '.json'); write(sp, submission)
    t = {**t, 'state': 'submitted', 'submission_sha256': submission['sha256'],
         'submission_record_sha256': digest(submission), 'submitted_at': now()}
    save_task(run, t, 'submitted')
    return {'task_id': task_id, 'submission_sha256': submission['sha256'], 'imported_evidence_count': len(imports)}


def checked_submission(run, t):
    stored = read(home(run) / 'submissions' / (t['task_id'] + '.json'))
    if digest(stored) != t.get('submission_record_sha256'):
        raise ValueError('提交记录或证据来源在提交后被修改')
    if digest(stored['payload']) != stored['sha256'] or stored['sha256'] != t['submission_sha256']:
        raise ValueError('答卷在提交后被修改')
    wp = run.path / t['worker_dir']
    if digest(read_bytes(wp / 'submission.json')) != stored['worker_file_sha256']: raise ValueError('子Agent提交后修改答卷，旧审核失效')
    if digest(read(wp / 'submission.json')) != stored['original_sha256']: raise ValueError('子Agent原答卷与导入内容不对应')
    if digest(read_bytes(wp / 'events.jsonl')) != stored['worker_events_sha256']: raise ValueError('子Agent提交后继续取证，需重新提交并审核')
    for eid, sha in stored['worker_evidence_hashes'].items():
        if digest(read_bytes(wp / 'evidence' / (eid + '.json'))) != sha: raise ValueError('子Agent提交后修改证据')
    for rel, sha in stored.get('worker_proof_file_hashes', {}).items():
        if digest(read_bytes(wp / rel)) != sha: raise ValueError('子Agent提交后修改完整对象证明')
    for rel, sha in stored.get('parent_proof_file_hashes', {}).items():
        target = (run.path / rel).resolve()
        if not target.is_relative_to(run.path) or digest(read_bytes(target)) != sha:
            raise ValueError('共享完整明细/诊断在提交后改变，原调查和主审失效')
    for proof in stored['payload'].get('population_proofs', {}).values():
        for eid, sha in proof.get('source_evidence_sha256', {}).items():
            if digest(run.get_evidence(eid)) != sha: raise ValueError('主包缺口基线在提交后变化，原调查和审核失效')
    return stored


def _rebuild_proof(run, task, obligation, stored):
    """Re-run the raw typed population calculation at actual main review time."""
    if obligation['obligation_id'] not in stored['payload'].get('population_proofs', {}): return None
    worker = Run(run.path / task['worker_dir'])
    raw = read(worker.path / 'submission.json')
    answer = next(a for a in raw['answers'] if a['obligation_id'] == obligation['obligation_id'])
    candidate = next(c for c in read(run.path / 'candidates.json') if c['candidate_id'] == obligation['source_id'])
    if candidate['kind'] == 'result_change':
        from acceptance_investigation_proof import verify_result_proof
        return verify_result_proof(run.manifest, candidate, answer, worker, source_lookup=run.get_evidence)
    from acceptance_gap_proof import verify_gap_proof
    return verify_gap_proof(run.manifest, candidate, answer, worker, run.get_evidence)


def review(run, actor, task_id, value):
    with verification_snapshot(run) as snapshot:
        return _review(run, actor, task_id, value, snapshot)


def _review(run, actor, task_id, value, snapshot):
    coordinator_only(run, actor); t = task_records(run)[task_id]
    if t['state'] != 'submitted': raise ValueError('仅当前已提交答卷可进入主审核')
    if actor == t['agent_id'] or value.get('reviewer_id') != actor: raise ValueError('子Agent不能审核自己')
    stored = checked_submission(run, t)
    if value.get('submission_sha256') != stored['sha256']: raise ValueError('审核未绑定当前答卷哈希')
    entries = value.get('obligations', [])
    unique([r.get('obligation_id') for r in entries], '主审核义务')
    if {r['obligation_id'] for r in entries} != set(t['obligation_ids']): raise ValueError('主Agent必须逐义务审核，不能整包一键代替')
    obs = all_obligations(run); answers = {a['obligation_id']: a for a in stored['payload']['answers']}
    decisions = {}; rejected = False; return_items = []
    for r in entries:
        oid = r['obligation_id']; ob = obs[oid]; a = answers[oid]
        if r.get('decision') not in ('accept', 'return'): raise ValueError('审核必须接收或退回')
        text(r.get('reason'), '主审核理由'); text(r.get('scope_check'), '对象及分母复核')
        if set(r.get('check_reviews', {})) != set(ob['required_checks']): raise ValueError('主审核未逐调查动作签收')
        for name, cr in r['check_reviews'].items():
            if cr.get('decision') not in ('accept', 'return'): raise ValueError('调查动作缺审核结论')
            text(cr.get('reason'), '动作审核依据')
        checked = unique(r.get('evidence_checked'), '主审核实际核对证据')
        for eid in checked: run.get_evidence(eid)
        needed = set().union(*(set(x['evidence_ids']) for x in a['checks'].values()))
        needed.update(eid for item in a.get('impact', {}).values() for eid in item['evidence_ids'])
        proof = stored['payload'].get('population_proofs', {}).get(oid)
        if substantive.enabled(run): needed.update(substantive.evidence_ids(a, proof))
        if proof:
            needed.update(proof['evidence_ids'])
            for group in entity_assessments(a): needed.update(group['evidence_ids'])
        if r['decision'] == 'accept':
            if any(x['decision'] != 'accept' for x in r['check_reviews'].values()): raise ValueError('动作被退回，整个义务不能接收')
            if not needed <= set(checked): raise ValueError('主审核尚未核对答卷全部动作证据')
            if proof:
                entity_reviews = r.get('entity_reviews', [])
                hashes = unique([entry.get('assessment_sha256') for entry in entity_reviews], '实体组主审')
                expected_groups = {digest(g) for g in entity_assessments(a)}
                if set(hashes) != expected_groups: raise ValueError('主Agent未逐实体组核实原因与反证')
                for entry in entity_reviews:
                    if entry.get('decision') != 'accept': raise ValueError('实体组被退回，整个义务不能接收')
                    text(entry.get('reason'), '实体组主审理由')
            blocked = verify_answer(run, ob, a, run.get_evidence)
            if substantive.enabled(run):
                binding = substantive.case_binding(run, oid)
                if binding: r.update(binding)
                rebuilt = _rebuild_proof(run, t, ob, stored)
                if ob['source_type']=='question_scope':
                    from acceptance_questions import validate_main_review
                    validate_main_review(run,t,ob,a,r,stored['sha256'])
                else:substantive.validate_business_review(run, t, ob, a, proof, r, stored['sha256'], recomputed_proof=rebuilt)
                r['program_recomputation_sha256'] = digest(rebuilt) if rebuilt else None
            if ob['source_type'] == 'history_source':
                segment_reviews = r.get('history_segment_reviews', [])
                segment_ids = unique([entry.get('segment_id') for entry in segment_reviews], '历史逐段主审')
                expected_segments = {s['segment_id'] for s in ob['source_material']['segments']}
                if set(segment_ids) != expected_segments: raise ValueError('主Agent未逐段复核历史事项是否遗漏')
                for entry in segment_reviews:
                    if entry.get('decision') != 'accept': raise ValueError('历史段落被退回，整个来源不能接收')
                    text(entry.get('reason'), '历史段落主审理由')
            residual = substantive.enabled(run) and any(i.get('unexplained', 0) > 0 for i in a.get('impact', {}).values())
            decisions[oid] = 'blocked_external' if blocked else ('reviewed_residual' if residual else 'verified')
        else:
            if substantive.enabled(run):
                for item in substantive.unique(r.get('return_items'), '具体缺口及补查要求'):
                    if item.get('check_id') not in ob['required_checks']: raise ValueError('退回项未定位调查动作')
                    text(item.get('gap'), '主审核具体缺口'); text(item.get('required_action'), '退回补查动作')
                    payload = {**item, 'obligation_id': oid, 'submission_sha256': stored['sha256']}
                    return_items.append({**payload, 'return_id': 'return-'+digest(payload)[:24]})
            rejected = True; decisions[oid] = 'rework'
    snapshot.finish()
    rp = home(run) / 'reviews' / (task_id + '.json')
    record = {**value, 'reviewed_at': now(), 'outcomes': decisions}; write(rp, record)
    t = {**t, 'state': 'rework' if rejected else ('blocked_external' if 'blocked_external' in decisions.values() else 'approved'),
         'review_sha256': digest(record), 'outcomes': decisions,
         **({'return_items': return_items} if substantive.enabled(run) else {})}
    save_task(run, t, 'main_reviewed')
    return t


def audit(run, content=None):
    with verification_snapshot() as snapshot:
        return _audit(run, content, snapshot)


def _audit(run, content, snapshot):
    """Non-mutating audit. A queued/blocked obligation never contributes to verified."""
    errors = []; rows = {}; tasks = {}; states = {}; findings = {}; obligations = {}; dimensions = {}
    try:
        snapshot.pin_run(run)
        p = plan_data(run); obligations = all_obligations(run, p); tasks = task_records(run)
        states = {oid: 'queued' for oid in obligations}
        for tid, t in tasks.items():
            if t['state'] == 'requeued': continue
            if t.get('progress_path'):
                progress = read(home(run) / t['progress_path'])
                if digest(progress) != t['progress_sha256']: raise ValueError('失败/超时/容量进度记录被修改')
            for oid in t['obligation_ids']:
                if oid not in obligations or states[oid] != 'queued': raise ValueError('义务丢失/重复负责：' + oid)
                states[oid] = t['state']
            if t['state'] in ('submitted', 'approved', 'blocked_external', 'rework'):
                stored = checked_submission(run, t)
                answers = {a['obligation_id']: a for a in stored['payload']['answers']}
                if t['state'] in ('approved', 'blocked_external', 'rework'):
                    rv = read(home(run) / 'reviews' / (tid + '.json'))
                    if digest(rv) != t['review_sha256'] or rv['submission_sha256'] != t['submission_sha256'] or rv['reviewer_id'] != p['coordinator_id'] or rv['reviewer_id'] == t['agent_id']:
                        raise ValueError('主审核变更或与答卷/身份不对应')
                    for oid, state in t['outcomes'].items():
                        if state not in ('rework', 'verified', 'blocked_external', 'reviewed_residual'):
                            raise ValueError('未知审核结果不能冒充完成')
                        if state != 'rework':
                            is_blocked = verify_answer(run, obligations[oid], answers[oid], run.get_evidence)
                            if (state == 'blocked_external') != is_blocked: raise ValueError('审核把外部阻塞记为已核实')
                            if substantive.enabled(run):
                                entry = next(r for r in rv['obligations'] if r['obligation_id'] == oid)
                                proof = stored['payload'].get('population_proofs', {}).get(oid)
                                if obligations[oid]['source_type']=='question_scope':
                                    from acceptance_questions import validate_main_review
                                    validate_main_review(run,t,obligations[oid],answers[oid],entry,stored['sha256'])
                                else:substantive.validate_business_review(run, t, obligations[oid], answers[oid], proof,
                                    entry, stored['sha256'], recomputed_proof=proof)
                                has_residual = any(i.get('unexplained', 0) > 0 for i in answers[oid].get('impact', {}).values())
                                if not is_blocked and (state == 'reviewed_residual') != has_residual:
                                    raise ValueError('主审核把未解释残余记为全已核实')
                                dimensions[oid] = {**answers[oid]['status_dimensions'], 'review': 'approved'}
                            findings[oid] = answers[oid]
                        states[oid] = state
    except (ValueError, KeyError, FileNotFoundError, TypeError) as exc:
        errors.append(str(exc))
    routing = None; deferred = set()
    try:
        import acceptance_triage as triage
        if triage.enabled(run):
            routing = triage.status(run)
            errors.extend(routing['errors'])
            deferred = set(routing['deferred_obligation_ids'])
            if any(states.get(oid) != 'queued' for oid in deferred):
                raise ValueError('暂留义务仍被任务负责，须恢复实际调查状态')
            for oid in deferred: states[oid] = 'deferred'
    except (ValueError, KeyError, OSError, TypeError) as exc:
        errors.append(str(exc)); deferred = set()
    try:
        snapshot.finish()
    except (ValueError, OSError, TypeError) as exc:
        errors.append('verification_snapshot:'+str(exc))
        states = {oid: 'invalidated' for oid in obligations}
        findings = {}; dimensions = {}
    counts = dict(Counter(states.values()))
    coverage = set()
    if content is not None:
        for f in content.get('findings', []):
            for oid in f.get('obligation_ids', []):
                if oid not in findings: errors.append('报告引用未主审的调查结论：' + oid)
                coverage.add(oid)
        missing = set(findings) - coverage
        if missing: errors.append('已主审结论/阻塞未进入报告：' + str(len(missing)))
    total = len(obligations); applicable = total-len(deferred)
    for oid in obligations:
        if oid not in dimensions:
            s = states.get(oid, 'queued')
            dimensions[oid] = {'execution': s if s in ('failed', 'timeout') else 'not_executed' if s in ('queued', 'deferred') else 'in_progress',
                              'investigation': 'capacity_pending' if s == 'capacity_pending' else 'in_progress',
                              'defect': 'undetermined', 'cause': 'unexplained', 'impact': 'unknown',
                              'review': 'returned' if s in ('rework', 'returned') else 'pending', 'acceptability': 'undetermined'}
    source_states = {}
    for oid, ob in obligations.items():
        counter = source_states.setdefault(ob['source_type'], Counter())
        counter[states.get(oid, 'queued')] += 1
    return {'required': True, 'expected_obligations': total, 'states': counts,
            'source_states': {kind: dict(counter) for kind, counter in source_states.items()},
            'verified': counts.get('verified', 0), 'blocked_external': counts.get('blocked_external', 0),
            'unassigned': counts.get('queued', 0), 'assigned_tasks': len(tasks),
            'reviewed': counts.get('verified', 0) + counts.get('blocked_external', 0) + counts.get('reviewed_residual', 0),
            'reviewed_residual': counts.get('reviewed_residual', 0),
            'investigated': counts.get('verified', 0) + counts.get('reviewed_residual', 0),
            'substantive_completed': counts.get('verified', 0) + counts.get('reviewed_residual', 0),
            'pending_continuation_tasks': sum(entry['pending_count'] for t in tasks.values() if t['state'] != 'requeued'
                                              for entry in t.get('residual_followup_records', {}).values()),
            'substantive_complete': not errors and (total > 0 or run.policy.get('question_workflow',{}).get('enabled')) and counts.get('verified', 0) + counts.get('reviewed_residual', 0) == total,
            **({'state_dimensions': {key: dict(Counter(v[key] for v in dimensions.values())) for key in substantive.DIMENSIONS},
                'unreserved_acceptance_eligible': not errors and (total > 0 or run.policy.get('question_workflow',{}).get('enabled')) and counts.get('verified', 0) == total
                    and all(a['business_verdict'] == 'pass' for a in findings.values()),
                'machine_review_boundary': '程序核对范围、对象字段、版本、复算及职责；不能验证Agent是否真正理解，或仅凭相关性建立因果。'} if substantive.enabled(run) else {}),
            'errors': errors, 'report_covered': len(coverage),
            'business_verdicts': dict(Counter(a['business_verdict'] for a in findings.values())),
            **({'deferred': len(deferred), 'applicable_investigation_obligations': applicable,
                'applicable_investigation_complete': not errors and (total > 0 or run.policy.get('question_workflow',{}).get('enabled')) and counts.get('verified', 0)+counts.get('reviewed_residual', 0) == applicable,
                'classification': routing} if routing else {}),
            'note': '具体议题范围须经过调查与主审；覆盖、全景判读和基础检查分别核验。' if run.policy.get('question_workflow',{}).get('enabled') else '每条信号、样本、历史及专项义务须经过具体调查与主审核；证据阻塞不算核实。'}
