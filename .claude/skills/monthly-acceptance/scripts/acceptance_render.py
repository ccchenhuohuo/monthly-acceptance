"""Readable business report plus local, searchable execution/evidence views."""
import json
from collections import Counter
from acceptance_core import read
from acceptance_report_content import FAMILIES,STATES,errors as content_errors,placement,resolve_placements

STATE = {'complete': '完整执行', 'limited': '受限结果（存在证据或范围限制）', 'incomplete': '未完成'}
VERDICT = {'pass': '放行', 'qualified': '有保留放行', 'reject': '建议打回受影响范围', 'undetermined': '尚不能整体放行'}
INVESTIGATION_COUNTS = ('expected_obligations', 'verified', 'reviewed_residual', 'investigated', 'blocked_external', 'unassigned',
                        'assigned_tasks', 'reviewed', 'report_covered')


def display_count(value):
    return '待核定' if value is None else str(value)


def investigation_view(run, validation):
    """Load the whole verified ledger; keep answer bodies in their source files.

    An unreadable plan is not an empty population. This view never fabricates a
    zero denominator or grants completion when validation/ledger state is stale.
    """
    audited = validation.get('investigation')
    version = str(run.manifest.get('method_version', '0')).split('.')
    required = ((tuple(int(p) for p in version[:2]) >= (3, 1))
                or bool(audited and audited.get('required', True))
                or (run.path / 'investigations/plan.json').exists())
    result = {'required': required, 'load_state': 'legacy' if not required else 'error',
              'errors': [], 'coordinator_id': None, 'substantive_complete': False,
              'counts': {k: None for k in INVESTIGATION_COUNTS}, 'states': {},
              'obligations': [], 'tasks': [], 'reviews': [], 'source_obligations': {}}
    if not required:
        return result
    if not isinstance(audited, dict):
        result['errors'].append('机器核验尚未包含本版调查结果，请重新运行 validate')
        audited = {}
    result['errors'].extend(str(e) for e in audited.get('errors', []))
    try:
        from acceptance_investigation import plan_data, all_obligations, task_records
        plan = plan_data(run)
        obligations = all_obligations(run, plan)
        tasks = task_records(run)
        result['coordinator_id'] = plan['coordinator_id']
        owners = {}
        for task in tasks.values():
            if task['state'] != 'requeued':
                for oid in task['obligation_ids']:
                    if oid not in obligations or oid in owners:
                        raise ValueError('调查义务不存在或重复负责：' + oid)
                    owners[oid] = task
            # Never embed submission answers or per-check review text in each row.
            slim = {k: task[k] for k in ('task_id', 'agent_id', 'coordinator_id', 'attempt',
                    'state', 'created_at', 'submitted_at', 'worker_dir', 'submission_sha256',
                    'review_sha256', 'previous_task_ids', 'requeue_reason') if k in task}
            slim['obligation_ids'] = task['obligation_ids']
            slim['obligation_count'] = len(task['obligation_ids'])
            slim['outcome_counts'] = dict(Counter(task.get('outcomes', {}).values()))
            slim['sites'] = sorted({obligations[oid]['scope'].get('site') for oid in task['obligation_ids']
                                    if oid in obligations and obligations[oid]['scope'].get('site')})
            slim['task_path'] = 'investigations/tasks/' + task['task_id'] + '.json'
            if task.get('submission_sha256'):
                slim['submission_path'] = 'investigations/submissions/' + task['task_id'] + '.json'
            if task.get('review_sha256'):
                slim['review_path'] = 'investigations/reviews/' + task['task_id'] + '.json'
                result['reviews'].append({k: slim[k] for k in ('task_id', 'agent_id', 'coordinator_id',
                    'attempt', 'state', 'sites', 'obligation_count', 'outcome_counts',
                    'submission_sha256', 'review_sha256', 'submission_path', 'review_path') if k in slim})
            result['tasks'].append(slim)
        import acceptance_triage as triage
        routing = triage.status(run) if triage.enabled(run) else None
        deferred = set(routing['deferred_obligation_ids']) if routing else set()
        if routing: result['classification'] = routing; result['errors'].extend(routing['errors'])
        states = Counter()
        for oid, obligation in obligations.items():
            task = owners.get(oid)
            state = task.get('outcomes', {}).get(oid, task['state']) if task else ('deferred' if oid in deferred else 'queued')
            states[state] += 1
            row = {k: obligation[k] for k in ('obligation_id', 'source_type', 'source_id', 'scope',
                   'priority', 'required_checks', 'requires_impact', 'source_evidence_ids') if k in obligation}
            material = obligation.get('source_material', {})
            row.update(state=state, task_id=task['task_id'] if task else None,
                       agent_id=task['agent_id'] if task else None,
                       question=material.get('question', material.get('purpose', '')))
            if material.get('source_path'):
                row['source_path'] = material['source_path']
            result['obligations'].append(row)
            key = obligation['source_type'] + ':' + obligation['source_id']
            result['source_obligations'].setdefault(key, []).append(oid)
        result['states'] = dict(states)
        loaded = {'expected_obligations': len(obligations), 'verified': states.get('verified', 0),
                  'reviewed_residual': states.get('reviewed_residual', 0),
                  'investigated': states.get('verified', 0) + states.get('reviewed_residual', 0),
                  'blocked_external': states.get('blocked_external', 0), 'unassigned': states.get('queued', 0),
                  'assigned_tasks': len(tasks),
                  'reviewed': states.get('verified', 0) + states.get('reviewed_residual', 0) + states.get('blocked_external', 0)}
        if routing:
            loaded.update(deferred=len(deferred), applicable_investigation_obligations=len(obligations)-len(deferred))
        for key, value in loaded.items():
            fallback = 0 if key == 'reviewed_residual' else audited.get('verified') if key == 'investigated' else None
            if audited.get(key, fallback) != value:
                result['errors'].append('调查台账与机器核验计数不一致：' + key + '；请重新运行 validate')
        if audited.get('states') != dict(states):
            result['errors'].append('调查义务状态与机器核验不一致；请重新运行 validate')
        if not result['errors']:
            result['counts'] = {**loaded, 'report_covered': audited.get('report_covered')}
            result['load_state'] = 'ok'
            result['substantive_complete'] = bool(audited.get('substantive_complete')
                and len(obligations) > 0 and loaded['investigated'] == len(obligations))
            result['applicable_investigation_complete'] = bool(audited.get('applicable_investigation_complete', result['substantive_complete'])
                and loaded['investigated'] == len(obligations)-len(deferred))
    except (ValueError, KeyError, FileNotFoundError, TypeError, ImportError, OSError) as exc:
        result['errors'].append('调查记录读取或核验失败：' + str(exc))
    result['errors'] = list(dict.fromkeys(result['errors']))
    return result


def presentation(validation, investigation):
    staged = investigation['required'] and not investigation.get('applicable_investigation_complete', investigation['substantive_complete'])
    state = 'incomplete' if staged else validation.get('execution_state', 'incomplete')
    if state not in STATE:
        state = 'incomplete'
    verdict = validation.get('business_verdict', 'undetermined')
    if verdict not in VERDICT:
        verdict = 'undetermined'
    if staged and verdict in ('pass', 'qualified'):
        verdict = 'undetermined'
    return staged, state, verdict


def finding_lines(run, findings, investigation_required):
    lines=[]
    for i, finding in enumerate(findings, 1):
        lines += ['', f"## {i}. {finding['title']}", '', f"**范围：**{finding['scope']}", '']
        lines += finding['facts'] if isinstance(finding['facts'], list) else [finding['facts']]
        lines += ['', '**证据及反证：**' + finding['counterevidence'], '', '**判断：**' + finding['assessment'],
                  '', '**行动：**' + finding['action']]
        refs = []
        for eid in finding['evidence_ids']:
            run.get_evidence(eid)
            refs.append(f'[{eid[-8:]}](evidence/{eid}.json)')
        related = f"关联候选 {len(finding.get('candidate_ids', []))} 个"
        if investigation_required:
            related += f"，关联原义务 {len(finding.get('related_obligation_ids', finding.get('obligation_ids', [])))} 项"
            if finding.get('case_ids') and not finding.get('obligation_ids'):
                related += '（案件观察引用，不计逐义务主审完成）'
        lines += ['', '可复核证据：' + '、'.join(refs) + '。' + related + '。']
    return lines


def render(run):
    import acceptance_questions as questions
    if questions.enabled(run):return questions.render(run)
    v = read(run.path / 'validation.json')
    m = run.manifest
    content_path = run.path / 'report-content.json'
    content = read(content_path) if content_path.exists() else {
        'summary': ['尚未形成业务结论，请按待办补足调查与证据。'], 'findings': []}
    problems=content_errors(content)
    if problems:raise ValueError('Invalid business report content: '+str(problems))
    investigation = investigation_view(run, v)
    if investigation.get('classification'):
        content, problems = resolve_placements(content, investigation['classification'])
        if problems: raise ValueError('报告与当前分类不一致，重新分类/validate后生成：'+str(problems))
    staged, state, verdict = presentation(v, investigation)
    title = f"# {m['data_month']} 大盘数据验收" + ('（阶段进度）' if staged else '')
    lines = [title, '', f"运行 `{m['run_id']}` · 方法 {m['method_version']} · {STATE[state]} · **{VERDICT[verdict]}**", '']
    if staged:
        lines += ['> **阶段进度：业务调查尚未全部核实，不能据此整体放行。** '
                  '基础扫描通过、子 Agent 提交或外部阻塞获主审接受，均不等于全部信号已核实。', '']
    lines += ['- ' + s for s in content.get('summary', [])]
    lines += ['', '## 本次看了什么', '',
        f"范围：{'、'.join(m['sites'])}；各国自配置起点至 {m['data_month']}，国家与标准类目 L1–L3，SPU 数量、固定价格带、销量和本币销售额。中国合并淘宝与天猫；不同国家金额不汇总。"]
    lines += ['', '## 七类大盘审计概览', '', '| 审计主线 | 状态 | 已查范围与依据 |', '| --- | --- | --- |']
    overview={item['family']:item for item in content.get('audit_overview',[])}
    def cell(value):return str(value).replace('|','\\|').replace('\n',' ')
    for family,label in FAMILIES.items():
        item=overview.get(family)
        if not item:
            lines += [f'| {label} | 待核实 | 尚未提交该主线的范围及结论，不能推定正常 |'];continue
        refs=[]
        for eid in item.get('evidence_ids',[]):
            run.get_evidence(eid);refs.append(f'[{eid[-8:]}](evidence/{eid}.json)')
        lines += [f"| {label} | {STATES[item['state']]} | {cell(item['scope'])}：{cell(item['basis'])} {'、'.join(refs)} |"]
    if content.get('normal_scope'):
        lines += ['', '已检查且未见规则信号的范围：' + content['normal_scope']]
    lines += finding_lines(run,[f for f in content.get('findings',[]) if placement(f)=='main'],investigation['required'])
    if investigation.get('classification'):
        routing = investigation['classification']
        lines += ['', '## 本轮调查范围与重要待核实事项', '',
            f"原始线索义务 {routing['original_obligations']} 项；本轮适用深查 {routing['applicable_investigation_obligations']} 项；"
            f"经审核暂留 {len(routing['deferred_obligation_ids'])} 项。暂留未计已核实、已修复或实质调查完成，无保留接收另行判断。"]
        main_groups = {}
        for c in routing['cases']:
            if c['report_placement']=='main':
                target=(c.get('merge_reference') or {}).get('target_case_id')
                owner=next((x for x in routing['cases'] if x['case_id']==target),c)
                key=owner['review_id'] if c['classified'] else (c['site'],c['kind'])
                main_groups.setdefault(key,[]).append(c)
        lines += ['']
        for group in main_groups.values():
            c=group[0]
            lines += [f"- {c['site']} · {c['business_question'] or '尚待判断的'+c['kind']+'范围'}：{len(group)} 个案件；{c['reason']}。下一步：{c['next_check']}。"]
        if not main_groups and not any(placement(f)=='main' for f in content.get('findings',[])):
            lines += ['本轮已分类问题暂无需正文单列的宏观缺陷；是否全盘已查以七类概览和独立覆盖门槛为准。']
    technical_start=len(lines)
    if investigation['required']:
        lines += ['', f"基础任务 {v['baseline_jobs']['verified']}/{v['baseline_jobs']['expected']} 通过独立复核；"
            f"候选主审接收 {display_count(v['candidates']['disposed'])}/{display_count(v['candidates']['expected'])}；"
            f"明细抽样主审接收 {display_count(v['semantic_sample']['reviewed'])}/{display_count(v['semantic_sample']['expected'])}。"
            '以上主审接收数包含外部阻塞；实质核实另计，旧处置/样本索引不决定本版完成状态。']
    else:
        lines += ['', f"基础任务 {v['baseline_jobs']['verified']}/{v['baseline_jobs']['expected']} 通过独立复核；"
            f"候选 {display_count(v['candidates']['disposed'])}/{display_count(v['candidates']['expected'])} 关联处理状态（包括待补证，不等同根因核实）；"
            f"固定明细抽样 {display_count(v['semantic_sample']['reviewed'])}/{display_count(v['semantic_sample']['expected'])} 有核验记录。"]
    if investigation['required']:
        count = investigation['counts']
        expected = display_count(count['expected_obligations'])
        lines += ['', '## 调查与主审进度', '',
            f"调查义务共 {expected} 项；实质核实 **{display_count(count['verified'])}/{expected}**；"
            f"实质调查完成 {display_count(count['investigated'])} 项（含有界残余已审 {display_count(count['reviewed_residual'])} 项）；"
            f"主审接收 {display_count(count['reviewed'])} 项（包含已确认受阻事项）；"
            f"外部阻塞 {display_count(count['blocked_external'])} 项；未分派 {display_count(count['unassigned'])} 项。",
            f"已登记调查任务 {display_count(count['assigned_tasks'])} 个（含历史重派记录）；"
            f"报告关联义务 {display_count(count['report_covered'])} 项。全部优先级均在调查台账中，不能只核高优先级。"]
        if investigation['states']:
            lines += ['', '调查状态：' + '；'.join(f'{key} {n}' for key, n in investigation['states'].items()) + '。']
        if investigation['errors']:
            lines += ['', '**调查记录存在读取或核验错误，计数待核定：**',
                      *['- ' + e for e in investigation['errors']]]
        lines += ['', '缺陷事实、变化解释及发布影响分别核实；已证缺陷的修复进度单独保留。'
                  '未派发、未提交、退回未改、未主审或内部动作未完成时继续调查；真实外部缺证仍不计入实质核实。']
    architecture=v.get('architecture',{})
    if architecture:
        coverage=architecture.get('mandatory_coverage',{})
        cases=architecture.get('case_routing',{})
        machine=architecture.get('machine_diagnostics',{})
        history=coverage.get('history',{})
        usage=architecture.get('resource_usage',{})
        lines += ['', '## 必查、案件与机器诊断', '',
            '| 层次 | 本轮程序复核结果 |', '| --- | --- |',
            f"| 独立必查任务 | {display_count(coverage.get('expected'))} 项；适用 {display_count(coverage.get('applicable'))}，有据不适用 {display_count(coverage.get('not_applicable'))} |",
            f"| 必查执行状态 | {'；'.join(str(k)+' '+str(n) for k,n in coverage.get('execution_counts',{}).items()) or '待核定'} |",
            f"| 原始义务 → 案件 | {display_count(cases.get('original_obligations'))} → {display_count(cases.get('handling_cases'))}；映射 {display_count(cases.get('obligation_mapping_count'))} 项 |",
            f"| 共享取证 | 结果变化范围 {display_count(cases.get('result_collection_scopes'))}；全部处理包 {display_count(cases.get('collection_packages'))} |",
            f"| 机器诊断 | 完整案件 {display_count(cases.get('machine_complete_cases'))}；已取证包 {display_count(machine.get('package_count'))}，其中完整 {display_count(machine.get('diagnosed_packages'))} |",
            f"| 规则证明 | {display_count(cases.get('rule_proven_check_dispositions'))} 项检查证明；业务审核、因果及放行另计 |",
            (f"| 业务分流 | {str(cases['business_classification'].get('route_counts',{}))}；暂留与实际主审分别计数 |"
                if cases.get('business_classification') else
                f"| 分流 | 待取证 {display_count(cases.get('awaiting_evidence'))}；需具体调查 {display_count(cases.get('needs_investigation'))}；外部缺证以调查主审台账为准 |"),
            f"| 历史回归 | 具体历史 {display_count(history.get('expected'))} 项；回归完成 {str(coverage.get('history_complete',False))}，保护入队不计重新发现 |",
            '', '归并率不是调查完成率；逻辑取证范围数不是 SQL 数、根因数或 Agent 数。'
            '量额正负残余和 SPU/价格带按成员复算，不能跨比较、层级、国家直接相加。',
            '', f"实际查询尝试 {display_count(usage.get('actual_query_attempts'))} 次；"
            f"查询执行耗时 {display_count(usage.get('query_elapsed_seconds'))} 秒；"
            f"SQL 排队 {display_count(usage.get('query_queue_seconds'))} 秒。"
            '模型 token 与计费不可从本地 CLI 取得，记未知，不按 Agent 数估算。',
            '', '[各层门槛与累计影响](validation.json) · [全量案件成员](investigations/cases/state.json)'
            ' · [逐项历史对照](coverage-history-comparison.json)']
        cumulative=cases.get('cumulative_exposure',[])
        if cumulative:
            lines += ['', f'已重算 {len(cumulative)} 个同国类月累计单元；详见上述门槛文件。']
    technical_lines=[]
    if investigation.get('classification'):
        technical_lines=lines[technical_start:];lines=lines[:technical_start]
    brief=[f for f in content.get('findings',[]) if placement(f)=='brief']
    if brief or investigation.get('classification'):
        lines += ['', '## 持续观察汇总', '']
        for f in brief:
            lines += [f"- **{f['title']}**（{f['scope']}）：{f['assessment']}；{f['action']}"]
        groups = {}
        for c in investigation.get('classification',{}).get('cases',[]):
            if c['report_placement']=='brief': groups.setdefault(c['review_id'],[]).append(c)
        for group in groups.values():
            c=group[0]; lines += [f"- {c['business_question']}：{len(group)} 个案件，{c['reason']}；复查/停止边界：{c['stop_condition']}。"]
    auxiliary=[f for f in content.get('findings',[]) if placement(f)=='appendix']
    if auxiliary:
        lines += ['', '## 辅助观察与明细证据', '', '以下记录用于补充判断；保留原始义务和审核状态。']
        lines += finding_lines(run,auxiliary,investigation['required'])
    if investigation.get('classification'):
        from acceptance_core import write
        write(run.path/'classification-queue.json',investigation['classification'])
        lines += ['', '## 分类与证据附件', '']
        appendix_groups={}
        for c in investigation['classification']['cases']:
            if c['report_placement']=='appendix':appendix_groups.setdefault(c['review_id'],[]).append(c)
        for group in appendix_groups.values():
            c=group[0]
            lines += [f"- {c['business_question']}：{len(group)} 个案件，{sum(len(x['obligation_ids']) for x in group)} 条原义务；{c['reason']}；当前路由 {c['investigation_route']}，未计实质核实。"]
        lines += ['', '[全部原始案件、报告位置与真实调查路由](classification-queue.json) · [成员、分流审核与累计证据](investigations/cases/state.json)']
    if technical_lines:lines += ['', '## 执行与审核附件', '', *technical_lines]
    lines += ['', '## 限制与使用边界', '',
              '数据源为服务商原始宽表和公司标准类目代理；价格与销售额属于估算。贡献分解仅定位变化，不能单独证明原因。没有记录的月份不视为零。']
    if content.get('limitations'):
        lines += ['', *['- ' + s for s in content['limitations']]]
    for key, n in Counter(x.split(':')[0] for x in v['limitations']).items():
        if key == 'candidate_needs_evidence':
            lines += [f'- {n} 个候选仍需补证，未按“正常”关闭。']
        elif key == 'sample_external_verification_pending':
            lines += ['- 部分明细抽样尚缺独立平台或经营证据。']
        else:
            lines += ['- ' + key]
    if v['errors']:
        lines += ['', '尚未完成的执行事项：', *['- ' + e for e in v['errors']]]
    lines += ['', '指纹复核只能发现已观测聚合值变化，不能提供事务快照保证。治理发布版本与已验收上游还须对应核对。', '',
              '[执行与证据看板](audit.html) · [机器核验结果](validation.json) · [冻结任务计划](plan.json)', '']
    (run.path / '报告.md').write_text('\n'.join(lines))
    dashboard(run, v, content, investigation)
    run.event('report.rendered', findings=len(content.get('findings', [])), execution_state=state,
              substantive_complete=investigation['substantive_complete'] if investigation['required'] else None)


def dashboard(run, validation, content, investigation=None):
    investigation = investigation if investigation is not None else investigation_view(run, validation)
    staged, state, verdict = presentation(validation, investigation)
    jobs = read(run.path / 'plan.json')['jobs'] if (run.path / 'plan.json').exists() else []
    candidates = read(run.path / 'candidates.json') if (run.path / 'candidates.json').exists() else []
    dispositions = run.dispositions()
    # Store a grouped decision once; repeating its candidate IDs per row is quadratic.
    decisions = {d['decision_id']: {**{k: val for k, val in d.items() if k != 'candidate_ids'},
                 'candidate_count': len(d['candidate_ids'])} for d in dispositions.values()}
    reviews = {}
    if (run.path / 'sample-reviews.json').exists():
        for review in read(run.path / 'sample-reviews.json'):
            for sid in review['sample_ids']:
                reviews[sid] = {k: val for k, val in review.items() if k != 'sample_ids'}
    display_validation = {**validation,
        'baseline_jobs': {'expected': None, 'verified': None, **validation.get('baseline_jobs', {})},
        'candidates': {'disposed': None, 'expected': None, **validation.get('candidates', {})},
        'semantic_sample': {'reviewed': None, 'expected': None, **validation.get('semantic_sample', {})}}
    data = {'validation': display_validation, 'decisions': decisions,
        'presentation': {'staged': staged, 'state': STATE[state], 'verdict': VERDICT[verdict]},
        'investigation': {k: value for k, value in investigation.items() if k not in ('obligations', 'tasks', 'reviews')},
        'obligations': investigation['obligations'], 'investigation_tasks': investigation['tasks'],
        'main_reviews': investigation['reviews'],
        'jobs': [{**{k: job[k] for k in ['job_id', 'family', 'site', 'level', 'entity', 'year', 'lag'] if k in job},
                  'record': run.job_record(job['job_id'])} for job in jobs],
        'candidates': [{**c, 'decision_id': dispositions.get(c['candidate_id'], {}).get('decision_id')} for c in candidates],
        'samples': [{**s, 'review': reviews.get(s['sample_id'])} for s in
                    (read(run.path / 'samples.json') if (run.path / 'samples.json').exists() else [])],
        'events': [json.loads(line) for line in run._event_snapshot().splitlines()],
        'checks': read(run.path / 'checks.json') if (run.path / 'checks.json').exists() else [],
        'summary': content.get('summary', [])}
    embedded = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    page = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>验收执行与证据</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f3f5f8;color:#182335;font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}header,main{max-width:1300px;margin:auto;padding:28px}header{padding-bottom:8px}h1{font-size:30px;margin:0}h2{font-size:20px}a{color:#1261aa}.muted{color:#56657a}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.card,section{background:white;border:1px solid #dae1e9;border-radius:12px;padding:18px;margin-bottom:18px}.big{font-size:28px;font-weight:700}.tabs{display:flex;gap:10px;flex-wrap:wrap}button,select,input{font:inherit;border:1px solid #b5c2d3;border-radius:7px;padding:7px 12px;background:white}button{cursor:pointer}button.active{background:#163d67;color:white}button:disabled{opacity:.45;cursor:default}input{min-width:330px;max-width:100%}.row{display:flex;gap:14px;align-items:center;flex-wrap:wrap}table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;vertical-align:top;padding:10px;border-bottom:1px solid #e0e6ed}th{background:#f6f8fb;position:sticky;top:0}pre{white-space:pre-wrap;word-break:break-word;font-size:12px;max-height:500px;overflow:auto}details{max-width:820px}summary{cursor:pointer}.tablewrap{overflow:auto;max-height:680px}.badge{display:inline-block;background:#edf3fa;padding:1px 8px;border-radius:10px}.warn{color:#9b4208}.error{color:#ab2435}.stage{background:#fff4e7;border:1px solid #deab74;color:#7c3e0d;padding:15px 18px;border-radius:10px;margin:18px 0}.stage p{margin:5px 0}.pager{margin:16px 0}.link-button{padding:2px 7px;font-size:12px;color:#1261aa}button:focus-visible,a:focus-visible{outline:3px solid #377cad;outline-offset:3px}@media(max-width:780px){header,main{padding:15px}.metrics{grid-template-columns:repeat(2,1fr)}input{min-width:220px}td,th{padding:7px}}@media print{.tabs,.filters,.pager{display:none}.tablewrap{max-height:none}}</style>
<header><p class="muted">MONTHLY ACCEPTANCE · 可观测的执行记录</p><h1>从验收结论追到每项调查与查询</h1><p id="identity" class="muted"></p><div id="stage" class="stage" hidden><strong>阶段进度：业务调查尚未全部核实，不能据此整体放行。</strong><p>本轮适用义务须调查；经审暂留单列并保留重查条件。基础扫描通过、子 Agent 提交或外部阻塞获主审接受，均不等于实质核实。</p></div></header>
<main><div class="metrics" id="metrics"></div><section><h2>本次结果</h2><ul id="summary"></ul><p id="investigation-status" class="muted"></p><p id="investigation-errors" class="error" role="status"></p><p><a href="报告.md">业务报告</a> · <a href="validation.json">独立核验</a> · <a href="run.json">冻结口径</a> · <a href="events.jsonl">全部事件</a></p><details><summary>执行缺口与证据限制</summary><pre id="limits"></pre></details></section>
<section><div class="tabs" id="tabs"></div><p class="muted">完整记录均可搜索和筛选；每页 50 条只限制展示，不改变调查分母。调查答卷与主审记录保存为一次提交的文件引用，不在每条候选中重复。金额保留原币，不跨国相加。</p><div class="row filters"><input id="search" aria-label="搜索完整记录" placeholder="查国家、类目、月份、义务、Agent 或错误"><select id="country" aria-label="按国家筛选"><option value="">所有国家</option><option>cn</option><option>US</option><option>DE</option><option>JP</option><option value="global">跨范围 / 历史来源</option></select><select id="investigation-state" aria-label="按调查状态筛选" hidden><option value="">所有调查状态</option><option value="queued">未分派</option><option value="deferred">经审暂留（未核实）</option><option value="assigned">已分派</option><option value="submitted">待主审</option><option value="verified">实质核实</option><option value="reviewed_residual">有界残余已审</option><option value="approved">任务已审</option><option value="blocked_external">外部阻塞</option><option value="rework">退回补查</option><option value="requeued">已重新入队</option></select><button id="reset">清空</button></div><div class="pager row"><button id="prev">上一页</button><span id="count"></span><button id="next">下一页</button></div><div class="tablewrap"><table><thead id="thead"></thead><tbody id="tbody"></tbody></table></div></section></main>
<script id="data" type="application/json">__DATA__</script><script>
const D=JSON.parse(document.getElementById('data').textContent),V=D.validation,I=D.investigation;let tab=I.required?'obligations':'jobs',page=0;
const $=s=>document.getElementById(s),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const count=v=>v==null?'待核定':String(v),stateNames={queued:'未分派',deferred:'经审暂留（未核实）',assigned:'已分派',submitted:'待主审',verified:'实质核实',reviewed_residual:'有界残余已审',approved:'任务已审',blocked_external:'外部阻塞',rework:'退回补查',requeued:'已重新入队'};
const stateName=s=>stateNames[s]||s||'待核定',C=I.counts;
$('identity').textContent=V.run_id+' · '+D.presentation.state+' · '+D.presentation.verdict;$('stage').hidden=!D.presentation.staged;
const metrics=[['基础任务',count(V.baseline_jobs.verified)+'/'+count(V.baseline_jobs.expected)]];
if(I.required)metrics.push(['实质核实',count(C.verified)+'/'+count(C.expected_obligations)],['有界残余已审',count(C.reviewed_residual)],['实质调查完成',count(C.investigated)],['主审接收（含阻塞）',count(C.reviewed)],['外部阻塞',count(C.blocked_external)],['未分派义务',count(C.unassigned)],['调查任务（含历史重派）',count(C.assigned_tasks)]);
else metrics.push(['候选登记',count(V.candidates.disposed)+'/'+count(V.candidates.expected)]);
if(C.deferred!=null)metrics.push(['本轮适用深查',count(C.applicable_investigation_obligations)],['经审暂留（未核实）',count(C.deferred)]);
metrics.push([I.required?'明细主审接收（含阻塞）':'明细样本记录',count(V.semantic_sample.reviewed)+'/'+count(V.semantic_sample.expected)],['原始证据',count(V.evidence_count)]);
$('metrics').innerHTML=metrics.map(([k,v])=>'<div class="card"><div class="muted">'+esc(k)+'</div><div class="big">'+esc(v)+'</div></div>').join('');$('summary').innerHTML=D.summary.map(s=>'<li>'+esc(s)+'</li>').join('');
$('investigation-status').textContent=I.required?'主 Agent：'+(I.coordinator_id||'待核定')+'；调查状态：'+Object.entries(I.states).map(([k,n])=>stateName(k)+' '+n).join('；')+'。缺陷修复与原因核实分别记录。':'本包没有 v3.1 调查义务台账；旧版候选登记和语义记录不能证明完整业务调查。';
$('investigation-errors').textContent=I.errors.length?'调查记录读取或核验错误，统计待核定：'+I.errors.join('；'):'';
$('limits').textContent=JSON.stringify({errors:V.errors,limitations:V.limitations,investigation_errors:I.errors,investigation:V.investigation},null,2);
const labels={jobs:'基础任务与分页',candidates:'候选与判断',checks:'覆盖矩阵',samples:'明细样本',events:'实际行为轨迹'};
if(I.required)Object.assign(labels,{obligations:'全部调查义务',investigation_tasks:'子 Agent 任务',main_reviews:'主 Agent 审核'});
$('tabs').innerHTML=Object.entries(labels).map(([k,v])=>'<button data-tab="'+k+'">'+v+'</button>').join('');$('tabs').onclick=e=>{if(e.target.dataset.tab){tab=e.target.dataset.tab;page=0;draw()}};
const taskById=Object.fromEntries(D.investigation_tasks.map(t=>[t.task_id,t]));
const obligationById=Object.fromEntries(D.obligations.map(o=>[o.obligation_id,o]));
function localRecord(path,label){return /^investigations\/(tasks|submissions|reviews)\/task-[a-f0-9]{24}\.json$/.test(path||'')?'<a href="'+esc(path)+'">'+esc(label)+'</a>':''}
function relation(r){const key=r.candidate_id?'candidate:'+r.candidate_id:r.sample_id?'sample:'+r.sample_id:null;return key?(I.source_obligations[key]||[]):[]}
function sourceReviewState(r){if(!I.required)return r.candidate_id?(D.decisions[r.decision_id]?.status||'未处置'):(r.review?.status||'未复核');
const ids=relation(r);if(I.errors.length||!ids.length||ids.some(id=>!obligationById[id]))return '调查状态待核定';
const states={};for(const id of ids){const state=obligationById[id].state;states[state]=(states[state]||0)+1}
return Object.entries(states).map(([state,n])=>stateName(state)+(ids.length>1?' '+n:'')).join('；')}
function detail(r){const decision=D.decisions[r.decision_id],task=taskById[r.task_id];let links=[];
for(const id of new Set([...(r.evidence_ids||r.source_evidence_ids||r.record?.evidence_ids||[]),...(decision?.evidence_ids||[]),...(r.review?.evidence_ids||[])])){if(/^ev-[a-f0-9]{32}$/.test(id))links.push('<a href="evidence/'+id+'.json">证据 '+id.slice(-8)+'</a>')}
if(r.query_path&&/^queries\/[a-f0-9]{64}\.sql$/.test(r.query_path))links.push('<a href="'+r.query_path+'">实际查询 SQL</a>');
for(const [key,label] of [['task_path','任务记录'],['submission_path','完整调查答卷'],['review_path','逐项主审记录']]){const link=localRecord(r[key]||task?.[key],label);if(link)links.push(link)}
for(const oid of relation(r))links.push('<button class="link-button" data-obligation="'+esc(oid)+'">关联调查 '+esc(oid)+'</button>');
let shown=decision?{...r,decision}:r;if(I.required&&(r.candidate_id||r.sample_id)){shown={...r,investigation_obligation_ids:relation(r),investigation_state:sourceReviewState(r)};if(decision)shown.legacy_decision=decision;if(r.review){shown.legacy_sample_review=r.review;delete shown.review}}
return '<details><summary>展开记录与证据</summary>'+links.join(' · ')+'<pre>'+esc(JSON.stringify(shown,null,2))+'</pre></details>'}
const searchIndex={};function draw(){const q=$('search').value.toLowerCase(),site=$('country').value,isInvestigation=['obligations','investigation_tasks','main_reviews'].includes(tab),status=$('investigation-state').value,rows=D[tab]||[];
$('investigation-state').hidden=!isInvestigation;if(!searchIndex[tab])searchIndex[tab]=rows.map(r=>JSON.stringify({...r,decision:D.decisions[r.decision_id],related_obligations:relation(r),investigation_state:r.candidate_id||r.sample_id?sourceReviewState(r):undefined}).toLowerCase());
let all=rows.filter((r,i)=>(!site||r.site===site||r.scope?.site===site||r.sites?.includes(site)||searchIndex[tab][i].includes('"site":"'+site.toLowerCase()+'"'))&&(!isInvestigation||!status||r.state===status)&&(!q||searchIndex[tab][i].includes(q)));
const total=Math.max(1,Math.ceil(all.length/50));page=Math.min(page,total-1);$('count').textContent='共 '+all.length+' 条 · 第 '+(page+1)+' / '+total+' 页'+(isInvestigation&&I.errors.length?' · 调查记录存在错误，完整性待核定':'');$('prev').disabled=page===0;$('next').disabled=page===total-1;
document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));
const headings={jobs:['国家 / 任务','状态','数据范围','证据'],candidates:['国家 / 类目','月份 / 类型',I.required?'优先级 / 调查主审':'优先级 / 判断','事实与证据'],checks:['国家 / 层级','月份 / 检查','状态','范围与依据'],samples:['国家 / 类目','商品',I.required?'调查主审':'语义判断','样本与证据'],events:['顺序 / 时间','行为','状态','细节'],obligations:['范围 / 问题','月份 / 比较 / 优先级','调查状态 / Agent','逐项要求与证据'],investigation_tasks:['任务 / 子 Agent','范围 / 义务数','状态 / 尝试','答卷与主审引用'],main_reviews:['任务 / 主 Agent','核实 / 阻塞 / 退回','审核绑定','逐项审核文件']}[tab];
$('thead').innerHTML='<tr>'+headings.map(h=>'<th>'+h+'</th>').join('')+'</tr>';
$('tbody').innerHTML=all.slice(page*50,(page+1)*50).map(r=>{let cells;
if(tab==='jobs')cells=[esc(r.site)+' / '+esc(r.family),esc(r.record?.execution_status||'未运行'),esc('L'+(r.level??'—')+' '+(r.entity||'')+' '+(r.year||'')+' '+(r.record?.row_count??'')+' 行'),detail(r)];
else if(tab==='candidates')cells=[esc(r.site)+'<br>'+esc(r.path),esc(r.month)+'<br>'+esc(r.comparison||r.kind),esc(r.priority)+'<br>'+esc(sourceReviewState(r)),detail(r)];
else if(tab==='checks')cells=[esc(r.site)+' / L'+esc(r.level??'—'),esc(r.month)+'<br>'+esc(r.family),esc(r.state),detail(r)];
else if(tab==='samples')cells=[esc(r.site)+'<br>'+esc(r.path),esc(r.product_id),esc(sourceReviewState(r)),detail(r)];
else if(tab==='obligations')cells=[esc(r.scope.site)+' / '+esc(r.scope.path)+'<br>'+esc(r.question||r.source_type)+'<br><small>'+esc(r.obligation_id)+'</small>',esc(r.scope.month)+' / '+esc(r.scope.comparison||'—')+'<br>'+esc(r.priority),'<span class="badge">'+esc(stateName(r.state))+'</span><br>'+esc(r.agent_id||'尚未分派'),detail(r)];
else if(tab==='investigation_tasks')cells=[esc(r.task_id)+'<br>'+esc(r.agent_id),esc(r.sites.join('、'))+'<br>'+esc(r.obligation_count)+' 项',esc(stateName(r.state))+'<br>尝试 '+esc(r.attempt),detail(r)];
else if(tab==='main_reviews')cells=[esc(r.task_id)+'<br>'+esc(r.coordinator_id)+'<br><span class="badge">'+esc(r.state==='requeued'?'历史审核：已重新入队':stateName(r.state))+'</span>',Object.entries(r.outcome_counts).map(([k,n])=>esc(stateName(k))+' '+esc(n)).join('<br>'),'<small>答卷 '+esc(r.submission_sha256)+'<br>主审 '+esc(r.review_sha256)+'</small>',detail(r)];
else cells=[esc(r.seq)+'<br>'+esc(r.time),esc(r.type),esc(r.status||''),detail(r)];
return '<tr>'+cells.map(c=>'<td>'+c+'</td>').join('')}).join('');if(!all.length)$('tbody').innerHTML='<tr><td colspan="4" class="muted">'+(isInvestigation&&I.errors.length?'调查记录不可完整读取，不能认定为零义务。':'当前筛选没有记录。')+'</td></tr>'}
$('tbody').onclick=e=>{if(e.target.dataset.obligation){tab='obligations';page=0;$('search').value=e.target.dataset.obligation;$('country').value='';$('investigation-state').value='';draw()}};
$('prev').onclick=()=>{page--;draw()};$('next').onclick=()=>{page++;draw()};let searchTimer;$('search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{page=0;draw()},180)};$('country').onchange=()=>{page=0;draw()};$('investigation-state').onchange=()=>{page=0;draw()};$('reset').onclick=()=>{$('search').value='';$('country').value='';$('investigation-state').value='';page=0;draw()};draw();
</script></html>'''
    (run.path / 'audit.html').write_text(page.replace('__DATA__', embedded))
