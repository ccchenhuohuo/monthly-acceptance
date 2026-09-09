"""Two evidence-bound decisions on existing cases; no business closure credit.

A coordinator can review a group once. Measured exposure is recomputed from
complete observations; the coordinator still owns the finite uncertainty-domain
judgment. No ordering threshold is a materiality or release threshold.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import json
from acceptance_core import canonical, digest, read, months
from acceptance_report_content import FAMILIES
import acceptance_cases as cases
import acceptance_machine as machine
from acceptance_review import locator, text

ROUTES = {'investigate', 'merge', 'defer'}
PLACEMENTS = {'main', 'brief', 'appendix'}


def enabled(run):
    from acceptance_panorama import enabled as question_enabled
    if question_enabled(run):return False
    return (tuple(map(int, run.manifest.get('method_version', '0.0').split('.')[:2])) >= (3, 3)
            and run.policy.get('case_workflow', {}).get('enabled', False)
            and tuple(map(int, run.policy.get('version', '0.0').split('.')[:2])) >= (3,3))


def _ids(values, label):
    if not isinstance(values, list) or not values or any(not isinstance(x, str) for x in values) or len(set(values)) != len(values):
        raise ValueError('需要非空且不重复的 '+label)
    return sorted(values)


def _selected(state, ids):
    ids = _ids(ids, 'case_ids')
    found = [c for c in state['cases'] if c['case_id'] in ids]
    if len(found) != len(ids): raise ValueError('分类引用未知案件')
    return found


def critical(case, state):
    """Frozen scope and required regressions survive every route."""
    return any(m['source_type'] in ('historical', 'history_source', 'project')
               or m['kind'] in ('contract_scope_missing', 'raw_presence_gap', 'std_presence_gap', 'gap')
               or 'scope_contract' in m['required_checks'] or 'regression_check' in m['required_checks']
               for m in (state['members'][oid] for oid in case['obligation_ids']))


def _months(case, state, run):
    result = set(case.get('months', []))
    for oid in case['obligation_ids']:
        m = state['members'][oid]
        result.update(m.get('scope', {}).get('months', []))
        for value in (m.get('month'), m.get('current_month'), m.get('base_month')):
            if value: result.add(value)
        if m.get('comparison') and m.get('current_month'):
            from acceptance_core import shift
            result.add(shift(m['current_month'], -run.policy['comparisons'][m['comparison']]))
    return sorted(result)


def _tokens(case, state):
    return {str(token) for oid in case['obligation_ids'] for key in ('processing_rule_ids', 'batch_ids')
            for token in state['members'][oid].get(key, [])}


def related(state, selected):
    # All months and all risk families in a country participate. Known shared
    # batches/rules also invalidate judgments in other countries, without adding
    # their incomparable currencies. No risk-type whitelist hides small signals.
    sites = {c['site'] for c in selected}; tokens = set().union(*(_tokens(c, state) for c in selected))
    return [c for c in state['cases'] if c['site'] in sites or tokens & _tokens(c, state)]


def _binding(run, state, selected):
    rel = related(state, selected)
    return {'run_id': run.manifest['run_id'], 'data_month': run.manifest['data_month'],
            'policy_hash': run.manifest['policy_hash'], 'inputs_sha256': digest(run.manifest['inputs']),
            'members': {c['case_id']: [c['case_version'], c['member_hash']] for c in selected},
            'related_members_sha256': digest(sorted([[c['case_id'], c['case_version'], c['member_hash'],
                c.get('value_assessment')] for c in rel], key=canonical))}


def _entities(value):
    if not isinstance(value, list): raise ValueError('影响对象须显式列出国家/平台/父体')
    keys = []
    for item in value:
        if not isinstance(item, list) or len(item) != 3 or any(not isinstance(x, str) or not x.strip() for x in item):
            raise ValueError('影响身份需完整国家/平台/product_id；占位SKU不能作身份')
        keys.append(canonical(item))
    if len(keys) != len(set(keys)): raise ValueError('影响对象重复')
    return set(keys)


def _observations(run, spec):
    spec = machine._normal_spec(run.manifest, spec)
    if spec['level'] != 0 or spec['path']:
        raise ValueError('影响复核使用同国完整总体，业务类目由完整总体重算')
    files, eids = {}, set()
    rows = machine._verified_targets(run, spec, machine.collection_jobs(run.manifest, spec), files, eids)
    machine._reconcile(run, spec, rows, files, eids)
    if any(not r['platform'] or not r['product_id'] or r['invalid_value_rows']
           or r['missing_units_rows'] or r['missing_amount_rows'] for r in rows):
        raise ValueError('总体存在未知身份/数值，影响仍未界定，先补查')
    return spec, rows, files, eids


def _stats(rows, affected, edges):
    selected = [r for r in rows if machine._entity_key(r) in affected]
    whole = machine._source_summary(rows, edges, False) if rows else None
    part = machine._source_summary(selected, edges, False) if selected else None
    # Band exposure includes every row of each affected source SPU, even when
    # the exact affected identity was on only one platform. Do not sum shares.
    pids = {r['product_id'] for r in selected}
    bands = machine._source_summary([r for r in rows if r['product_id'] in pids], edges, False) if pids else None
    return {'observed': bool(rows), 'denominator': whole, 'affected_observed': part,
            'affected_spu_band_population': bands,
            'units_positive': sum(max(0, r['units']) for r in selected),
            'units_negative': sum(min(0, r['units']) for r in selected),
            'amount_positive': sum(max(0, r['amount']) for r in selected),
            'amount_negative': sum(min(0, r['amount']) for r in selected),
            'amount_share': sum(r['amount'] for r in selected)/whole['amount'] if whole and whole['amount'] else None,
            'units_share': sum(r['units'] for r in selected)/whole['units'] if whole and whole['units'] else None,
            'spu_share': len(pids)/whole['spus'] if whole and whole['spus'] else None}


def measure(run, state, selected, observation_cache=None):
    """Recompute the deduplicated union for ALL assessed related risk types.

    The caller supplies exact potentially affected identities, never a numeric
    numerator/denominator. Identity selection and the finite error domain remain
    explicit coordinator judgments and are not inferred from observed changes.
    """
    rel = related(state, selected); populations = {}; file_hashes = {}; eids = set()
    selected_ids = {c['case_id'] for c in selected}
    observation_cache = {} if observation_cache is None else observation_cache
    assessed = [c for c in rel if c.get('value_assessment')]
    for c in assessed:
        a = c['value_assessment']; entities = _entities(a.get('entities'))
        specs = a.get('specs', [])
        if not specs: raise ValueError('暂留前需完整总体统计')
        covered = set(); available = set()
        for raw_spec in specs:
            token = digest(raw_spec)
            if token not in observation_cache: observation_cache[token] = _observations(run, raw_spec)
            populations[token] = observation_cache[token]
            spec, rows, files, refs = populations[token]; file_hashes.update(files); eids.update(refs)
            if spec['site'] != c['site']: raise ValueError('影响总体国家与案件不同')
            covered.update(months(spec['window_start'], spec['window_end']))
            available.update(machine._entity_key(r) for r in rows)
        if not set(_months(c, state, run)) <= covered: raise ValueError('影响总体缺少原始月份或比较基期')
        if not entities <= available: raise ValueError('影响对象不在完整总体中；不能把无观测当作零')
        local = {}
        for raw_spec in specs:
            for row in populations[digest(raw_spec)][1]: local[machine._row_key(row)] = row
        # A small local finding cannot remove an unexamined macro change from
        # this round's deep-investigation denominator. Keep every changed
        # identity in every original comparison, including opposing changes.
        for oid in c['obligation_ids']:
            m = state['members'][oid]
            if m['kind'] != 'result_change': continue
            base, current = m.get('base_month'), m.get('current_month')
            if not base or not current: raise ValueError('结果变化义务缺少实际比较基期')
            path = cases._path(m.get('path', c['path']))
            scoped = [r for r in local.values() if machine._path(r['std_path'])[:len(path)] == path]
            before = machine._aggregate([r for r in scoped if r['month_dt'][:7]==base], machine._entity_key)
            after = machine._aggregate([r for r in scoped if r['month_dt'][:7]==current], machine._entity_key)
            changed = {key for key in set(before)|set(after) if
                       tuple(before.get(key,{}).get(k) for k in ('rows','units','amount')) !=
                       tuple(after.get(key,{}).get(k) for k in ('rows','units','amount'))}
            if changed-entities: raise ValueError('影响选择遗漏原宏观比较的变化对象；微观发现不能替代未解释范围')

        for oid in c['obligation_ids']:
            m = state['members'][oid]
            required = set(m.get('scope', {}).get('product_ids', [])) | ({m['product_id']} if m.get('product_id') else set())
            if required - {json.loads(x)[2] for x in entities}: raise ValueError('影响选择遗漏原义务商品')
    if any(not c.get('value_assessment') for c in selected): raise ValueError('本组尚无范围影响统计')
    union = {}
    for _, rows, _, _ in populations.values():
        for row in rows:
            key = machine._row_key(row)
            if key in union and machine._content(union[key]) != machine._content(row): raise ValueError('累计总体的重叠证据冲突')
            union[key] = row
    rows = list(union.values()); groups = []
    for site in sorted({c['site'] for c in assessed}):
        cs = [c for c in assessed if c['site'] == site]
        # Country and every affected business category, including ancestor L1.
        paths = {canonical([])}
        for c in cs:
            p = cases._path(c['path'])
            for n in range(1, min(len(p), 3)+1): paths.add(canonical(p[:n]))
        keys = set().union(*(_entities(c['value_assessment']['entities']) for c in cs))
        for path in sorted(paths):
            p = json.loads(path); scoped = [r for r in rows if r['site'] == site and machine._path(r['std_path'])[:len(p)] == p]
            by_month = {mm: [r for r in scoped if r['month_dt'][:7] == mm]
                        for mm in sorted({r['month_dt'][:7] for r in rows if r['site'] == site})}
            monthly = {mm: _stats(part, keys, run.policy['price_bands'][site]) for mm, part in by_month.items()}
            changes = []
            pairs = {(m.get('base_month'), m.get('current_month'), m.get('comparison'))
                     for c in cs for oid in c['obligation_ids'] for m in [state['members'][oid]]
                     if m.get('base_month') and m.get('current_month')}
            from acceptance_core import shift
            pairs.update((shift(mm,-1),mm,'mom') for mm in by_month if shift(mm,-1) in by_month)
            for base, current, comparison in sorted(pairs):
                amounts = {}
                for metric in ('units', 'amount'):
                    before = defaultdict(float); after = defaultdict(float)
                    for r in by_month.get(base, []): before[machine._entity_key(r)] += r[metric]
                    for r in by_month.get(current, []): after[machine._entity_key(r)] += r[metric]
                    deltas = [after[k]-before[k] for k in keys]
                    amounts[metric] = {'positive': sum(max(0, x) for x in deltas), 'negative': sum(min(0, x) for x in deltas),
                                       'gross': sum(abs(x) for x in deltas), 'net': sum(deltas)}
                changes.append({'base_month': base, 'current_month': current, 'comparison': comparison, **amounts})
            groups.append({'site': site, 'path': p, 'monthly': monthly, 'changes': changes})
    return {'groups': groups, 'assessed_case_ids': sorted(c['case_id'] for c in assessed),
            'unassessed_related_case_ids': sorted(c['case_id'] for c in rel if not c.get('value_assessment')),
            'all_related_kind_counts': dict(Counter(c['kind'] for c in rel)),
            'source_files_sha256': file_hashes, 'evidence_ids': sorted(eids),
            'note': '观察暴露与变化均非错误上界；未知相关案件没有被当作零。影响对象及有限不确定域须由主Agent核查。'}


def preview(run, payload, state=None):
    if isinstance(payload, list):
        staged = deepcopy(cases.load(run) if state is None else state)
        for item in payload:
            for c in _selected(staged, item['case_ids']):
                if c['case_id'] in item.get('assessments', {}): c['value_assessment'] = item['assessments'][c['case_id']]
        return [preview(run, item, staged) for item in payload]
    state = deepcopy(cases.load(run) if state is None else state)
    selected = _selected(state, payload['case_ids'])
    assessments = payload.get('assessments', {})
    if set(assessments) - {c['case_id'] for c in selected}: raise ValueError('影响统计含组外案件')
    for c in selected:
        if c['case_id'] in assessments: c['value_assessment'] = assessments[c['case_id']]
    measured = measure(run, state, selected)
    return {'case_ids': sorted(c['case_id'] for c in selected), 'assessments': assessments,
            'binding': _binding(run, state, selected), 'facts': measured, 'facts_sha256': digest(measured)}


def collection_plan(run, state, selected):
    """Exact query contracts for shared collection; no causal completion."""
    jobs = {}
    for c in selected:
        for package in state['collection_packages']:
            if c['case_id'] not in package['case_ids'] or not package.get('machine_collection_eligible'): continue
            required = _months(c, state, run)
            if not required: continue
            spec = {k: package[k] for k in ('site','path','level')}
            spec.update(window_start=max(min(required), run.manifest['starts'][c['site']]), window_end=max(required))
            for j in machine.collection_jobs(run.manifest, spec): jobs[j['job_id']] = j
    return jobs


def _merge(run, state, selected, decision, tasks):
    target_id = decision.get('merge_target_case_id')
    target = _selected(state, [target_id])[0]
    if target_id in {c['case_id'] for c in selected}: raise ValueError('不能循环/自合并')
    if target.get('investigation_route') != 'investigate': raise ValueError('合并目标必须为直接调查案件，不能形成链或循环')
    target_review = state.get('classification_reviews', {}).get(target.get('classification_review_id'), {})
    if target_review.get('binding') != _binding(run, state, [c for c in state['cases'] if c['case_id'] in target_review.get('case_ids', [])]):
        raise ValueError('合并目标的分类已失效')
    for c in selected:
        if c['site'] != target['site']: raise ValueError('不同国家不能自动合并')
    active = [t for t in tasks.values() if t['state'] in ('assigned', 'submitted', 'approved') and set(t['obligation_ids']) & set(target['obligation_ids'])]
    job_ids = decision.get('shared_job_ids', [])
    verified = []; needed = collection_plan(run, state, selected)
    target_jobs = collection_plan(run, state, [target])
    common = set(needed) & set(target_jobs)
    if not common: raise ValueError('合并任务没有相同国家/平台总体/层/筛选/月份/核对目的的共享范围')
    from acceptance_validate import verify_job
    for jid in job_ids:
        j = read(run.path/'records'/(jid+'-plan.json'))
        if jid not in common or j != needed[jid] or verify_job(run, j): raise ValueError('复用查询无效或国家不匹配')
        verified.append({'job_id': jid, 'plan_sha256': digest(j), 'record_sha256': digest(run.job_record(jid))})
    if not active and not verified: raise ValueError('合并目标无活跃任务或有效查询证据')
    for key in ('shared_question', 'remaining_checks', 'stop_condition'):
        text(decision.get(key), key)
    if decision['shared_question'] != target_review.get('business_question'):
        raise ValueError('合并目标未调查相同业务问题')
    available = {jid for jid in needed if not verify_job(run, needed[jid])}
    remaining = set(needed)-available
    return {'target_case_id': target_id, 'covered_job_ids': sorted(available),
            'remaining_job_ids': sorted(remaining),
            'waiting_for_shared_task': bool(active and (common-available) and any(t['state'] in ('assigned','submitted') for t in active)),
            'task_ids': sorted(t['task_id'] for t in active), 'queries': verified,
            'remaining_checks': decision['remaining_checks'],
            'note': '共享取证不自动履行任何源义务；完整与差额查询按原范围重新计划。'}


def _query_versions(run, refs):
    events = {e['event_id']:e for e in (json.loads(x) for x in run._event_snapshot().splitlines())}
    bindings = {}
    for link in refs:
        meta = read(run.path/'records'/(link['evidence_id']+'.json'))
        event = events.get(meta.get('parent_event_id'), {})
        jid = event.get('parent_id')
        if jid:
            current = run.job_record(jid)
            if current and link['evidence_id'] in current.get('evidence_ids', []): bindings[jid] = digest(current)
            elif current: raise ValueError('分类引用已被新查询替代的旧页，须审核当前结果')
    return bindings


def classify(run, actor, decisions):
    from acceptance_investigation import coordinator_only, task_records
    coordinator_only(run, actor)
    if not enabled(run): raise ValueError('分类改造仅适用于3.3及后续新冻结运行')
    if not isinstance(decisions, list) or not decisions: raise ValueError('分类输入须为非空组列表')
    state = cases.load(run); tasks = task_records(run); seen = set()
    # Stage assessments first so every review sees the same cumulative union.
    for d in decisions:
        selected = _selected(state, d['case_ids'])
        if seen & set(d['case_ids']): raise ValueError('批量分类重复案件')
        seen.update(d['case_ids'])
        if set(d.get('assessments', {})) - set(d['case_ids']): raise ValueError('影响统计含组外案件')
        for c in selected:
            if c['case_id'] in d.get('assessments', {}): c['value_assessment'] = d['assessments'][c['case_id']]
    reviews = state.setdefault('classification_reviews', {})
    for d in decisions:
        selected = _selected(state, d['case_ids']); route = d.get('investigation_route'); placement = d.get('report_placement')
        if route not in ROUTES or placement not in PLACEMENTS: raise ValueError('分类须明确两个业务决策')
        for key in ('route_reason', 'placement_reason', 'business_question', 'next_check', 'stop_condition'):
            text(d.get(key), key)
        guarded = any(critical(c, state) for c in selected)
        if guarded and (route == 'defer' or placement != 'main'): raise ValueError('关键范围与必要回归不能暂留或移出正文')
        if route == 'defer' and placement == 'main': raise ValueError('正文级未解决风险应继续调查')
        refs = d.get('evidence_locators', [])
        if not refs: raise ValueError('分类须核对实际原始证据定位')
        covered = set()
        for link in refs:
            record = locator(run.get_evidence, link)
            for c in selected:
                original = {eid for oid in c['obligation_ids'] for eid in state['members'][oid]['source_evidence_ids']}
                site_matches = record.get('site') == c['site'] or (record.get('site') is None and link['evidence_id'] in original) or c['site']=='global'
                mm = str(record.get('month_dt', record.get('month', '')))[:7]
                needed = _months(c, state, run)
                if site_matches and (not needed or mm in needed or c['site']=='global'):
                    paths = [cases._path(record[k]) for k in ('path','std_path') if record.get(k) is not None]
                    cp = cases._path(c['path'])
                    if paths and cp and not any(p[:len(cp)]==cp or cp[:len(p)]==p for p in paths): continue
                    covered.add(c['case_id'])
        if covered != {c['case_id'] for c in selected}: raise ValueError('分类证据未覆盖本组国家、月份及业务范围')
        measured = None
        if route == 'defer' or 'facts_sha256' in d:
            measured = measure(run, state, selected)
            if d.get('facts_sha256') != digest(measured) or d.get('binding') != _binding(run, state, selected):
                raise ValueError('先用case-assess读取本批当前累计统计，再审核；统计或相关范围已变化')
        if route == 'defer':
            if any(t['state'] != 'requeued' and set(t['obligation_ids']) & set(oid for c in selected for oid in c['obligation_ids']) for t in tasks.values()):
                raise ValueError('已有负责任务；先保留progress并requeue后再审核暂留')
            for key in ('uncertainty_boundary', 'counterevidence', 'cumulative_review', 'unassessed_scope_review', 'reopen_conditions'):
                text(d.get(key), key)
            if set(d.get('metric_review', {})) != set(machine.METRICS): raise ValueError('暂留须审核SPU、价格带、销量、金额各自影响边界')
            for key, value in d['metric_review'].items(): text(value, key+'影响判断')
        merge_ref = _merge(run, state, selected, d, tasks) if route == 'merge' else None
        receipt = {**deepcopy(d), 'reviewer_id': actor, 'case_ids': sorted(d['case_ids']),
                   'binding': _binding(run, state, selected), 'facts': measured, 'merge_reference': merge_ref,
                   'query_versions': _query_versions(run, refs),
                   'evidence_sha256': {x['evidence_id']: digest(run.get_evidence(x['evidence_id'])) for x in refs}}
        rid = 'value-'+digest(receipt)[:24]; reviews[rid] = receipt
        for c in selected:
            c.update(investigation_route=route, report_placement=placement,
                     classification_review_id=rid, classification_reason=d['route_reason'])
    return cases._persist(run, state, 'business_classified', actor)


def status(run, state=None):
    if not enabled(run): return {'enabled': False, 'deferred_obligation_ids': [], 'cases': [], 'errors': []}
    if state is None and not (cases.home(run)/'state.json').exists():
        from acceptance_investigation import all_obligations
        n=len(all_obligations(run))
        return {'enabled':True,'original_obligations':n,'case_count':0,'classified_cases':0,
                'deferred_obligation_ids':[],'applicable_investigation_obligations':n,
                'route_counts':{},'placement_counts':{},'cases':[],'errors':[]}
    state = cases.load(run) if state is None else state
    from acceptance_investigation import plan_data, task_records
    main = plan_data(run)['coordinator_id']; checked = {}; dependencies = {}; observation_cache = {}; results = []; deferred = []; errors = []
    for c in state['cases']:
        rid = c.get('classification_review_id'); r = state.get('classification_reviews', {}).get(rid)
        if rid and rid not in checked:
            try:
                if not r or rid != 'value-'+digest(r)[:24] or r['reviewer_id'] != main: raise ValueError('分类审核凭证不匹配')
                selected = _selected(state, r['case_ids'])
                if r['binding'] != _binding(run, state, selected): raise ValueError('成员、数据/规则版本或相关影响统计变化')
                for jid, expected in r.get('query_versions', {}).items():
                    if digest(run.job_record(jid)) != expected: raise ValueError('分类引用查询已有新版本')
                for eid, expected in r['evidence_sha256'].items():
                    if digest(run.get_evidence(eid)) != expected: raise ValueError('分类证据变化')
                if r.get('facts') is not None and measure(run, state, selected, observation_cache) != r['facts']: raise ValueError('影响总体、累计或查询证据变化')
                if r['investigation_route'] == 'merge': dependencies[rid] = _merge(run, state, selected, r, task_records(run))
                checked[rid] = None
            except (ValueError, KeyError, OSError, TypeError) as exc: checked[rid] = str(exc)
        stale = checked.get(rid) if rid else '尚未作业务分类'
        if r and not stale and (c.get('investigation_route') != r['investigation_route'] or c.get('report_placement') != r['report_placement']):
            stale = '案件与审核决策不一致'
        valid = bool(r and not stale)
        route = r['investigation_route'] if valid else 'investigate'
        placement = r['report_placement'] if valid else 'main'
        if valid and route == 'defer': deferred.extend(c['obligation_ids'])
        if rid and stale: errors.append(c['case_id']+':需重新分类:'+stale)
        results.append({'case_id': c['case_id'], 'obligation_ids': c['obligation_ids'], 'site': c['site'], 'path': c['path'],
                        'kind': c['kind'], 'classified': valid, 'investigation_route': route, 'report_placement': placement,
                        'review_id': rid, 'reason': stale or r['route_reason'], 'business_question': r.get('business_question') if r else None,
                        'next_check': r.get('next_check') if valid else '先核对应有范围和聚合规模，再按主要贡献下钻',
                        'stop_condition': r.get('stop_condition') if valid else '范围、规模和未解释问题已核对；未知不按零',
                        'merge_reference': dependencies.get(rid, r.get('merge_reference')) if valid else None})
    return {'enabled': True, 'original_obligations': len(state['members']), 'case_count': len(results),
            'classified_cases': sum(c['classified'] for c in results), 'deferred_obligation_ids': sorted(deferred),
            'applicable_investigation_obligations': len(state['members'])-len(deferred),
            'route_counts': dict(Counter(c['investigation_route'] for c in results)),
            'placement_counts': dict(Counter(c['report_placement'] for c in results)),
            'cases': results, 'errors': errors,
            'note': '暂留不计已核实/修复/实质完成；旧缺字段保持待判断。'}
