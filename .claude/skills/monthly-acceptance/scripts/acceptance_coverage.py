"""Independent scope, historical recall and reproducible no-signal audit gates.

Coverage starts from frozen scope/rules, never from the candidate ledger.  A
query-level obligation remains even when its entire country or category domain
is absent.  Complete query populations then expand the category obligations.
Sampling proves only the selected cells; neither historical routing nor ordinary
category signals count as rediscovery of a concrete historical defect.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import json
import re

from acceptance_core import business_key, canonical, digest, merge, months, quoted, read, shift, write
from acceptance_queries import path_expr, plan


# Historical schema; current coverage records are created by acceptance_questions.
SCHEMA = 'monthly-acceptance-coverage/3.2'
from acceptance_protocol import current, protocol
EXECUTION_STATES = {'success_clean', 'success_signal', 'missing_data', 'query_failed', 'not_executed'}
# Only frozen source classifications may enter this path. Population-import
# guards still have their formal source-inventory/imported-obligation checks;
# naming an ordinary unresolved data issue "guard" in a review cannot opt in.
NON_OBJECT_HISTORY_TYPES = frozenset({
    'known_method_defect_regression', 'known_interpretation_defect_regression',
    'provenance_and_comparability_guard', 'exposure_and_claim_boundary_guard',
    'coverage_execution_guard', 'report_contract_regression',
    'external_evidence_execution_guard', 'retired_workflow_not_data_issue',
    'retired_signal_regression', 'not_applicable_history',
    'historical_population_import_unfinished', 'scope_contract_blocker',
    'classification_granularity_limitation', 'internal_evidence_blocker',
})


def enabled(run):
    version = str(run.policy.get('version', run.manifest.get('method_version', '0')))
    numbers = tuple(int(v) for v in re.findall(r'\d+', version)[:2])
    return not run.manifest.get('worker_task') and (
        current(run) or numbers >= (3, 2) or run.policy.get('coverage_gate', {}).get('enabled') is True)


def _events(run, kind):
    if not run.events_path.exists():
        return []
    return [e for line in run._event_snapshot().splitlines()
            if (e := json.loads(line)).get('type') == kind]


def _freeze(run, name, payload):
    """Bind an immutable artifact to the existing append-only event chain."""
    wrapped = {'payload': payload, 'sha256': digest(payload)}
    path = run.path / (name + '.json')
    if path.exists():
        if _frozen(run, name) != payload:
            raise ValueError(name + ': 已冻结内容发生改变；须新建运行')
        return wrapped
    run.writable()
    write(path, wrapped)
    run.event('coverage.' + name + '.frozen', sha256=wrapped['sha256'])
    return wrapped


def _frozen(run, name):
    value = read(run.path / (name + '.json'))
    if value.get('sha256') != digest(value.get('payload')) or not any(
            e.get('sha256') == value['sha256'] for e in _events(run, 'coverage.' + name + '.frozen')):
        raise ValueError(name + ': 冻结内容或事件绑定失效')
    return value['payload']


def _source_manifest(run):
    import yaml
    errors = run.verify_inputs() + run.verify_events()
    if errors:
        raise ValueError('frozen_sources_invalid:' + ','.join(errors))
    root = run.path / 'inputs'
    policy = read(root / 'skill/policies/default.json')
    override = root / 'project/policy-overrides.json'
    if override.exists():
        policy = merge(policy, read(override))
    config = yaml.safe_load((root / 'project/config.yaml').read_text())
    excluded = set(config.get('暂不验收站点', []))
    starts = {str(k): str(v)[:7] for k, v in config['站点起始月份'].items() if k not in excluded}
    if starts != run.manifest['starts'] or sorted(starts) != run.manifest['sites']:
        raise ValueError('scope_differs_from_frozen_source')
    if policy != run.policy:
        raise ValueError('policy_differs_from_frozen_source')
    return {**run.manifest, 'sites': sorted(starts), 'starts': starts, 'policy': policy}


def _task(site, mm, rule, jobs, *, level=None, path=None, lag=None, applicability='applicable', reason=None):
    scope = {'site': site, 'month': mm, 'level': level, 'path': path,
             'category_scope': 'exact_path' if path is not None else 'all_paths_at_level'}
    if lag is not None:
        scope['base_month'] = shift(mm, -lag)
    return {'task_id': 'check-' + business_key(site, mm, rule, level, path), 'rule_id': rule,
            'scope': scope, 'applicability': applicability, 'not_applicable_reason': reason,
            'source_jobs': sorted({j['job_id'] for j in jobs})}


def _roots(manifest, jobs):
    result = []
    for site in manifest['sites']:
        def pick(family, **params):
            return [j for j in jobs if j['site'] == site and j['family'] == family
                    and all(j.get(k) == v for k, v in params.items())]
        for mm in months(manifest['starts'][site], manifest['data_month']):
            year = int(mm[:4])
            for entity in ('raw', 'std'):
                result.append(_task(site, mm, 'arrival_' + entity, pick('fingerprint', entity=entity)))
            result.append(_task(site, mm, 'identity_mapping', pick('mapping', year=year)))
            result.append(_task(site, mm, 'raw_presence', pick('raw_paths')))
            for level in manifest['policy']['levels']:
                cats, bands = pick('category', level=level), pick('bands', level=level)
                result.append(_task(site, mm, 'category_values', cats, level=level))
                result.append(_task(site, mm, 'price_bands', cats + bands, level=level))
                for name, lag in manifest['policy']['comparisons'].items():
                    applicable = shift(mm, -lag) >= manifest['starts'][site]
                    movement = pick('movement', level=level, lag=lag, year=year)
                    result.append(_task(site, mm, name, cats + bands + movement, level=level, lag=lag,
                        applicability='applicable' if applicable else 'not_applicable',
                        reason=None if applicable else '自然月基期早于冻结站点起始月：' + shift(mm, -lag)))
            # Historical semantic checks remain required. The legacy SQL sample
            # covers the latest month only, so it cannot satisfy these tasks.
            result.append(_task(site, mm, 'no_signal_semantic_sample', pick('category', level=3), level=3))
        result.append(_task(site, manifest['data_month'], 'fixed_sample_collection', pick('sample'), level=3))
    result.append(_task('global', manifest['data_month'], 'release_metadata',
                        [j for j in jobs if j['family'] == 'release']))
    return sorted(result, key=lambda t: t['task_id'])


def prepare(run):
    import acceptance_questions as questions
    if questions.enabled(run):return questions.prepare_coverage(run)
    """Freeze mandatory country/month/rule scopes before any candidate exists."""
    if not enabled(run):
        return {'enabled': False}
    manifest = _source_manifest(run)
    jobs = plan(manifest)
    return _freeze(run, 'coverage-plan', {'schema': SCHEMA,
        'policy_hash': digest(manifest['policy']),
        'scope': {'sites': manifest['sites'], 'starts': manifest['starts'], 'end': manifest['data_month']},
        'query_plan_hash': digest(jobs), 'tasks': _roots(manifest, jobs),
        'note': '全部路径查询是独立覆盖分母；完整原始结果再展开逐路径任务，不从信号生成范围。'})


def _query_states(run, jobs):
    from acceptance_validate import verify_job
    rows, states = {}, {}
    for job in jobs:
        jid = job['job_id']
        state = {'job_id': jid, 'execution_status': 'not_executed', 'errors': [], 'evidence_ids': [],
                 'pagination': {'complete': False}, 'data_time': {}, 'sql_hash': digest(job['sql'])}
        try:
            record = run.job_record(jid)
            if record:
                state['evidence_ids'] = record.get('evidence_ids', [])
                state['data_time'] = {k: record.get(k) for k in ('started_at', 'finished_at')}
                state['pagination'].update(expected_rows=record.get('expected_rows'),
                                           row_count=record.get('row_count'), pages=len(state['evidence_ids']))
                if record.get('execution_status') != 'success':
                    state['execution_status'] = 'query_failed'
                    state['errors'].append(record.get('error') or record.get('execution_status', 'failed'))
                else:
                    errors = verify_job(run, job)
                    if not state['evidence_ids']:
                        errors.append('empty_evidence_chain')
                    if errors:
                        state['execution_status'] = 'query_failed'
                        state['errors'] += errors
                    else:
                        rows[jid] = run.job_rows(jid)
                        state['execution_status'] = 'success'
                        state['pagination']['complete'] = True
                        state['data_time']['observed_months'] = sorted({str(r['month_dt'])[:7]
                            for r in rows[jid] if r.get('month_dt')})
                        state['data_time']['snapshot_limitation'] = '查询时间及结果指纹不是事务快照'
        except Exception as exc:
            state['execution_status'] = 'query_failed'
            state['errors'].append(str(exc))
        states[jid] = state
    return rows, states


def _domain(run, manifest, jobs, rows):
    """Expand every observed path over the *whole* frozen time range."""
    axes = defaultdict(set)
    for j in jobs:
        if j['family'] in ('category', 'raw_paths'):
            for row in rows.get(j['job_id'], []):
                path = row.get('path')
                if path is not None:
                    axes[(j['site'], j['family'], j.get('level'))].add(path)
    tasks = []
    for root in _roots(manifest, jobs):
        scope, rule = root['scope'], root['rule_id']
        if rule not in {'category_values', 'price_bands', 'raw_presence', *manifest['policy']['comparisons']}:
            continue
        family = 'raw_paths' if rule == 'raw_presence' else 'category'
        for path in sorted(axes[(scope['site'], family, scope['level'])]):
            task = deepcopy(root)
            task['scope'].update(path=path, category_scope='exact_path')
            task['task_id'] = 'check-' + business_key(scope['site'], scope['month'], rule, scope['level'], path)
            tasks.append(task)
    contract_path = run.path / 'inputs/project/scope-contract.json'
    if contract_path.exists():
        for entry in read(contract_path).get('entries', []):
            site = entry['site']
            if site not in manifest['sites']:
                continue
            for mm in months(manifest['starts'][site], manifest['data_month']):
                start, end = entry.get('effective_from'), entry.get('effective_to') or manifest['data_month']
                applicability = ('undetermined' if not start else
                    'applicable' if start <= mm <= end else 'not_applicable')
                task = _task(site, mm, 'contract:' + entry['id'],
                    [j for j in jobs if j['family'] == 'raw_paths' and j['site'] == site],
                    path=canonical(entry['path_prefix']), applicability=applicability,
                    reason='冻结合同有效期外' if applicability == 'not_applicable' else None)
                task['scope']['category_scope'] = 'contract_prefix'
                tasks.append(task)
    return sorted(tasks, key=lambda t: t['task_id'])


def _query_population_hash(jobs, rows):
    return digest([{'job_id': j['job_id'], 'rows_hash': digest(sorted(
        [{k: v for k, v in r.items() if k != 'total_rows'} for r in rows[j['job_id']]], key=canonical))}
        for j in sorted(jobs, key=lambda j: j['job_id'])])


def _source_rows(value):
    rows = value.get('response', {}).get('data', value.get('rows', []))
    if not isinstance(rows, list):
        raise ValueError('具体发现缺少可定位原始行')
    return rows


def _identity_token(value):
    return isinstance(value, str) and value.strip() not in ('', '0')


def _sample_identity_state(obj):
    if not _identity_token(obj.get('platform')):
        return 'unresolved_source_record'
    if _identity_token(obj.get('product_id')):
        return 'parent_scoped_placeholder_sku' if not _identity_token(obj.get('sku_id')) else 'scoped_source_record'
    if _identity_token(obj.get('sku_id')):
        return 'sku_scoped_missing_parent'
    return 'unresolved_source_record'


def _observations(run, value, *, identity_mode='strict'):
    """Require identity/month and nonidentity facts in the cited raw row.

    This checks relevance, not whether a title establishes a semantic defect.
    The latter remains an explicit, versioned human business judgment.
    """
    if identity_mode not in ('strict', 'sample_source_row'):
        raise ValueError('未知身份定位模式')
    observations = value.get('observations')
    if not isinstance(observations, list) or not observations:
        raise ValueError('发现必须绑定具体对象、月份、异常特征和原始证据')
    for obs in observations:
        obj = obs.get('object', {})
        if not isinstance(obj, dict):
            raise ValueError('具体身份必须为对象')
        if identity_mode == 'strict':
            if not obj.get('product_id') and not obj.get('sku_id'):
                raise ValueError('类目或历史保护记录不等于具体对象重发现')
            if any(not _identity_token(v) for v in obj.values()):
                raise ValueError('空白或占位身份不能构成重发现')
        elif set(obj) != {'platform', 'product_id', 'sku_id'}:
            raise ValueError('抽查源记录必须完整保留 platform/product_id/sku_id 原值')
        if not obs.get('site') or not re.fullmatch(r'\d{4}-\d{2}', str(obs.get('month', ''))):
            raise ValueError('具体发现缺少国家或月份')
        feature = obs.get('feature', {})
        facts = feature.get('facts')
        if not feature.get('code') or not isinstance(facts, dict) or not facts or not obs.get('assessment'):
            raise ValueError('具体发现缺少异常特征事实与判断')
        if set(facts) <= {'site', 'month', 'month_dt', 'product_id', 'sku_id', 'platform'}:
            raise ValueError('只有身份与月份，不能证明异常特征')
        value = run.get_evidence(obs['evidence_id'])
        sql = value.get('request', {}).get('sql', '')
        literal_months = set(re.findall(r"\bmonth_dt\s*=\s*'(\d{4}-\d{2}-\d{2})'", sql, re.I))
        month_predicate = (literal_months == {obs['month'] + '-01'} and
            not re.search(r'\bmonth_dt\s+(?:IN|BETWEEN)\b|\b(?:OR|UNION)\b', sql, re.I))
        matches = [r for r in _source_rows(value)
                   if all(r.get(k) == v for k, v in obj.items())
                   and (str(r.get('month_dt', r.get('month', '')))[:7] == obs['month'] or
                        ('month_dt' not in r and 'month' not in r and month_predicate))
                   and all(r.get(k) == v for k, v in facts.items())]
        # Site may be omitted by a grouped SQL result, but the evidence then
        # needs its genuine SQL predicate; an arbitrary caller scope is not proof.
        site_predicate = re.search(r'\bsite\s*=\s*' + re.escape(quoted(obs['site'])), sql, re.I)
        matches = [r for r in matches if r.get('site') == obs['site'] or
                   ('site' not in r and site_predicate)]
        if not matches:
            raise ValueError('发现证据与具体对象、月份或特征无关')
    return deepcopy(observations)


def freeze_independent(run, discoveries=None, *, provenance='deterministic_scan', history_blind=False):
    import acceptance_questions as questions
    if questions.enabled(run):return questions.freeze_discovery(run,discoveries,provenance=provenance,history_blind=history_blind)
    """Freeze independent outputs before loading/joining historical judgments.

    Aggregate candidates are kept separately from concrete discoveries.  Default
    ``history_blind=False`` avoids claiming a blind experiment merely because a
    file was created before another file in an already informed agent session.
    """
    if not enabled(run):
        return {'enabled': False}
    prepare(run)
    if ((run.path / 'coverage-history-plan.json').exists() or
            (run.path / 'coverage-history-comparison.json').exists() or _events(run, 'investigation.plan.frozen')):
        raise ValueError('历史对照已开始，不能补写为独立发现')
    manifest = _source_manifest(run)
    jobs = plan(manifest)
    rows, states = _query_states(run, jobs)
    bad = [jid for jid, state in states.items() if state['execution_status'] != 'success']
    if bad:
        raise ValueError('独立发现冻结需要全部原始查询完整成功：' + ','.join(bad))
    from acceptance_analysis import rebuild
    rebuilt = rebuild(run)
    for name in ('checks', 'candidates', 'samples'):
        path = run.path / (name + '.json')
        if not path.exists() or read(path) != rebuilt[name]:
            raise ValueError('独立扫描结果未冻结或已改变：' + name)
    specific = []
    for discovery in discoveries or []:
        observations = _observations(run, discovery)
        item = {**discovery, 'observations': observations}
        item['discovery_id'] = discovery.get('discovery_id') or 'discovery-' + business_key(observations)
        specific.append(item)
    if len({v['discovery_id'] for v in specific}) != len(specific):
        raise ValueError('独立发现 ID 重复')
    _freeze(run, 'coverage-domain', {'query_population_hash': _query_population_hash(jobs, rows),
                                   'tasks': _domain(run, manifest, jobs, rows)})
    value = _freeze(run, 'coverage-independent', {'schema': SCHEMA,
        'provenance': provenance, 'history_blind': history_blind,
        'query_population_hash': _query_population_hash(jobs, rows),
        'candidate_hash': digest(rebuilt['candidates']), 'sample_hash': digest(rebuilt['samples']),
        'aggregate_candidate_ids': [c['candidate_id'] for c in rebuilt['candidates']],
        'discoveries': sorted(specific, key=lambda d: d['discovery_id']),
        'note': '类目信号/固定样本入队不算具体问题重发现；非盲独立流程不能称盲测召回。'})
    prepare_sampling(run)
    return value


def _historical_parts(source):
    """Only named structured scope containers; never mine IDs from prose."""
    parts = [source]
    for key in ('legacy_detail', 'legacy_scope', 'scope'):
        child = source.get(key)
        if isinstance(child, dict):
            parts += _historical_parts(child)
        elif isinstance(child, list):
            for item in child:
                if isinstance(item, dict):
                    parts += _historical_parts(item)
    return parts


def _history_items(run):
    result = []
    seen = set()
    for filename in ('open-issues.json', 'legacy-issues.json'):
        path = run.path / 'inputs/project' / filename
        if not path.exists():
            continue
        for source in read(path):
            issue_id = source['issue_id']
            if issue_id in seen:
                raise ValueError('历史具体问题 ID 重复：' + issue_id)
            seen.add(issue_id)
            parts = _historical_parts(source)
            objects, observed_months = [], set()
            from acceptance_case_grouping import history_scope
            for part in parts:
                # Reuse the existing scope helper for its structured month
                # inventory. Its untyped entity union is deliberately not used
                # to infer parent/child links or merge equal cross-platform IDs.
                helper = dict(part)
                if isinstance(helper.get('months'), str):
                    helper['months'] = [helper['months']]
                if isinstance(helper.get('legacy_detail'), dict) and isinstance(helper['legacy_detail'].get('months'), str):
                    helper['legacy_detail'] = {**helper['legacy_detail'], 'months': [helper['legacy_detail']['months']]}
                part_months, _ = history_scope(helper)
                observed_months.update(str(mm)[:7] for mm in part_months if mm)
                observed_months.update(str(part[k])[:7] for k in ('month', 'current_month', 'base_month', 'first_observed_month') if part.get(k))
                for obj in list(part.get('sku_keys') or []) + list(part.get('objects') or []):
                    if isinstance(obj, dict) and (obj.get('product_id') or obj.get('sku_id')):
                        objects.append({k: v for k, v in obj.items()
                                        if k in ('site', 'platform', 'product_id', 'sku_id') and v is not None})
                for key, singular in (('product_ids', 'product_id'), ('sku_ids', 'sku_id')):
                    ids = part.get(key) or []
                    ids = [ids] if isinstance(ids, str) else ids
                    objects += [{singular: str(value)} for value in ids]
                    if part.get(singular):
                        objects.append({singular: str(part[singular])})
            objects = [json.loads(value) for value in sorted({canonical(o) for o in objects})]
            ms = sorted(observed_months)
            feature = next((part.get('anomaly_feature') or part.get('issue_type') or part.get('kind')
                            for part in parts if part.get('anomaly_feature') or part.get('issue_type') or part.get('kind')), None)
            guard = source.get('issue_type') in NON_OBJECT_HISTORY_TYPES
            missing = [k for k, v in [('objects', objects), ('months', ms), ('feature', feature)]
                       if not v and not (guard and k in ('objects', 'months'))]
            if not any(part.get('evidence_ids') or part.get('source_refs') for part in parts):
                missing.append('historical_evidence')
            result.append({'issue_id': issue_id,
                'site': next((part['site'] for part in parts if part.get('site')), None),
                'path': next((part['path'] for part in parts if part.get('path')), None),
                'objects': objects, 'months': ms, 'anomaly_feature': feature,
                'regression_kind': 'non_object_guard' if guard else 'data_issue',
                'previous_status': source.get('status'), 'previous_not_applicable_reason': source.get('not_applicable_reason'),
                'specification_missing': missing, 'historical_impact': source.get('impact', source.get('known_facts')),
                'source_path': str(path.relative_to(run.path)), 'source_hash': digest(path.read_bytes()),
                'source_material': source})
    return sorted(result, key=lambda x: x['issue_id'])


def prepare_history(run):
    import acceptance_questions as questions
    if questions.enabled(run):
        _frozen(run,"coverage-independent")
        return _freeze(run,"coverage-history-plan",{"protocol":protocol(run,4),"checks":[c for c in questions.baseline_checks(run) if c["kind"] in ("history_inventory","historical_regression")]})
    if not enabled(run):
        return {'enabled': False}
    _source_manifest(run)
    independent = _frozen(run, 'coverage-independent')
    return _freeze(run, 'coverage-history-plan', {'independent_hash': digest(independent),
        'items': _history_items(run), 'source_inventory': run.manifest.get('history_sources', []),
        'note': '历史重要具体问题逐项保留；来源清点仍由独立历史来源调查义务承担。'})


def _matches(issue, discovery, check_feature=True):
    if issue.get('regression_kind') == 'non_object_guard':
        return False
    observations = discovery.get('observations', [])
    feature = issue['anomaly_feature']
    code = feature.get('code') if isinstance(feature, dict) else feature
    required_facts = feature.get('facts', {}) if isinstance(feature, dict) else {}
    semantic = any(word in str(code).lower() for word in ('semantic', 'classification'))
    return not issue['specification_missing'] and all(any(
        obs['site'] == issue['site'] and obs['month'] == mm
        and all(obs['object'].get(k) == v for k, v in obj.items() if k != 'site')
        and (not check_feature or (obs['feature']['code'] == code
             and all(obs['feature']['facts'].get(k) == v for k, v in required_facts.items())
             and (not semantic or any(obs['feature']['facts'].get(k) for k in
                  ('title', 'product_title', 'product_title_cn', 'sku_title', 'product_kind', 'classification_definition')))))
        for obs in observations) for obj in issue['objects'] for mm in issue['months'])


def _guard_context(run):
    import acceptance_investigation as investigation
    import acceptance_review as review
    if not review.enabled(run):
        raise ValueError('非对象历史回归仍必须启用实质主审协议')
    payload = investigation.plan_data(run)
    return {'plan': payload, 'obligations': investigation.all_obligations(run, payload),
            'tasks': investigation.task_records(run)}


def _guard_review(run, issue, context):
    """Replay the formal current submission and substantive business approval."""
    import acceptance_investigation as investigation
    import acceptance_review as review
    ident = 'historical:' + issue['issue_id']
    result = {'obligation_id': ident, 'state': 'guard_review_pending'}
    try:
        if issue.get('specification_missing'):
            raise ValueError('非对象历史事项仍缺冻结原始问题或历史证据：' + ','.join(issue['specification_missing']))
        if context.get('error'):
            raise ValueError(context['error'])
        obligation = context['obligations'].get(ident)
        if not obligation or obligation.get('source_type') != 'historical' or obligation.get('source_id') != issue['issue_id']:
            raise ValueError('非对象历史事项没有原ID对应的正式强制调查义务')
        if obligation.get('source_hash') != digest(issue['source_material']) or obligation.get('source_material') != issue['source_material']:
            raise ValueError('非对象回归调查义务与冻结历史来源不一致')
        tasks = [t for t in context['tasks'].values() if ident in t['obligation_ids'] and t['state'] != 'requeued']
        if len(tasks) != 1 or tasks[0].get('outcomes', {}).get(ident) != 'verified':
            raise ValueError('非对象历史事项尚无当前verified调查及主审；阻塞/退回/残余不算完成')
        task = tasks[0]
        if task['state'] not in ('approved', 'blocked_external', 'rework'):
            raise ValueError('非对象历史事项没有有效的当前主审任务状态')
        stored = investigation.checked_submission(run, task)
        approval = read(investigation.home(run) / 'reviews' / (task['task_id'] + '.json'))
        coordinator = context['plan']['coordinator_id']
        if (digest(approval) != task.get('review_sha256') or approval.get('submission_sha256') != stored['sha256']
                or approval.get('reviewer_id') != coordinator or coordinator == task['agent_id']
                or approval.get('outcomes', {}).get(ident) != 'verified'):
            raise ValueError('非对象历史回归主审哈希、版本、结果或职责不一致')
        answer = next(a for a in stored['payload']['answers'] if a['obligation_id'] == ident)
        entry = next(e for e in approval['obligations'] if e['obligation_id'] == ident)
        if entry.get('decision') != 'accept' or set(entry.get('check_reviews', {})) != set(obligation['required_checks']) or any(
                e.get('decision') != 'accept' for e in entry['check_reviews'].values()):
            raise ValueError('非对象历史事项的必查动作仍未全部主审接收')
        if investigation.verify_answer(run, obligation, answer, run.get_evidence):
            raise ValueError('非对象历史事项仍存在实际外部阻塞')
        if any(i.get('unexplained', 0) > 0 for i in answer.get('impact', {}).values()):
            raise ValueError('非对象历史事项仍有未解释残余')
        proof = stored['payload'].get('population_proofs', {}).get(ident)
        if proof is not None:
            raise ValueError('非对象历史事项不能附加不相关商品人口证明')
        if not review.evidence_ids(answer) <= set(entry.get('evidence_checked', [])):
            raise ValueError('非对象主审未实际核对全部当前证据')
        review.validate_business_review(run, task, obligation, answer, None, entry, stored['sha256'], recomputed_proof=None)
        result.update(state='guard_reviewed', task_id=task['task_id'], submission_sha256=stored['sha256'],
            review_sha256=task['review_sha256'], source_hash=obligation['source_hash'],
            reviewer_id=approval['reviewer_id'], independent_reviewer_id=entry['independent_review']['reviewer_id'],
            evidence_ids=sorted(review.evidence_ids(answer)),
            note='正式非对象历史义务已调查并经实质独立复核和主审；不计具体数据问题重发现。')
    except (ValueError, KeyError, TypeError, FileNotFoundError, StopIteration) as exc:
        result['pending_reason'] = str(exc)
    return result


def _comparison_result(run, records):
    independent = _frozen(run, 'coverage-independent')
    history = _frozen(run, 'coverage-history-plan')
    if history['items'] != _history_items(run):
        raise ValueError('历史清单来源或任务缺失')
    if not isinstance(records, list):
        raise ValueError('历史对照必须为显式列表')
    known = {i['issue_id'] for i in history['items']}
    indexed = {}
    for record in records:
        ident = record.get('issue_id')
        if ident not in known or ident in indexed:
            raise ValueError('历史对照包含未知或重复事项')
        indexed[ident] = record
    for discovery in independent['discoveries']:
        _observations(run, discovery)
    guard_context = {}
    if any(i.get('regression_kind') == 'non_object_guard' for i in history['items']):
        try:
            guard_context = _guard_context(run)
        except (ValueError, KeyError, TypeError, FileNotFoundError) as exc:
            guard_context = {'error': str(exc)}
    results = []
    for issue in history['items']:
        ident = issue['issue_id']
        item = indexed.get(ident, {})
        if issue.get('regression_kind') == 'non_object_guard':
            guard = _guard_review(run, issue, guard_context)
            results.append({'issue_id': ident, 'state': guard['state'], 'regression_kind': 'non_object_guard',
                'independent_discovery_ids': [], 'history_guided_discovery_ids': [], 'unconfirmed_feature_matches': [],
                'same_snapshot_proven': False, 'objects': issue['objects'], 'months': issue['months'],
                'anomaly_feature': issue['anomaly_feature'], 'specification_missing': issue['specification_missing'],
                'limitations': [], 'record_present': ident in indexed, 'guard_review': guard})
            continue
        independent_ids = [d['discovery_id'] for d in independent['discoveries'] if _matches(issue, d)]
        feature_pending = [{'discovery_id': d['discovery_id'], 'route': 'independent',
                            'observed_feature_codes': sorted({o['feature']['code'] for o in d['observations']})}
                           for d in independent['discoveries']
                           if _matches(issue, d, check_feature=False) and not _matches(issue, d)]
        guided = []
        for discovery in item.get('guided_discoveries', []):
            _observations(run, discovery)
            if _matches(issue, discovery):
                guided.append(discovery.get('discovery_id') or 'guided-' + business_key(discovery))
            elif _matches(issue, discovery, check_feature=False):
                feature_pending.append({'discovery_id': discovery.get('discovery_id') or 'guided-' + business_key(discovery),
                    'route': 'history_guided', 'observed_feature_codes': sorted({o['feature']['code'] for o in discovery['observations']})})
        comparability = item.get('comparability', {})
        kind = comparability.get('kind', 'unknown')
        same_snapshot = (kind == 'same_snapshot'
            and issue['source_material'].get('snapshot_sha256') == independent['query_population_hash']
            and comparability.get('snapshot_sha256') == independent['query_population_hash'])
        limitations = []
        if kind == 'same_snapshot' and not same_snapshot:
            limitations.append('同快照声明缺少冻结历史结果与本轮完整查询总体相同的哈希证明')
        if not same_snapshot:
            limitations.append(comparability.get('reason') or '缺少同一数据快照证明；数据变化与发现能力不能混为一谈')
        state = ('independently_rediscovered' if independent_ids else
                 'history_guided_discovery' if guided else 'feature_mapping_pending' if feature_pending else
                 'not_reproduced' if same_snapshot else 'comparability_pending')
        correction = item.get('correction')
        if not independent_ids and not guided and correction:
            _observations(run, correction)
            if not correction.get('reason') or not correction.get('reviewer_id') or not correction.get('investigator_id'):
                raise ValueError('历史纠正缺少实质说明及审核者')
            if correction['reviewer_id'] == correction['investigator_id']:
                raise ValueError('历史纠正不能自行批准')
            material = {k: v for k, v in correction.items() if k not in ('reviewer_id', 'reviewed_hash')}
            if correction.get('reviewed_hash') != digest(material):
                raise ValueError('历史纠正批准版本失效')
            for obj in issue['objects']:
                for mm in issue['months']:
                    if not any(o['site'] == issue['site'] and o['month'] == mm and
                               all(o['object'].get(k) == v for k, v in obj.items() if k != 'site')
                               for o in correction['observations']):
                        raise ValueError('历史纠正未覆盖原具体对象或月份')
            state = 'evidenced_correction' if same_snapshot else 'comparability_pending'
        if issue['specification_missing']:
            state = 'specification_pending'
        results.append({'issue_id': ident, 'state': state, 'independent_discovery_ids': independent_ids,
            'regression_kind': 'data_issue',
            'history_guided_discovery_ids': guided, 'same_snapshot_proven': same_snapshot,
            'unconfirmed_feature_matches': feature_pending,
            'objects': issue['objects'], 'months': issue['months'], 'anomaly_feature': issue['anomaly_feature'],
            'specification_missing': issue['specification_missing'], 'limitations': limitations,
            'record_present': ident in indexed})
    counts = dict(Counter(i['state'] for i in results))
    errors = ['historical_regression_not_reproduced:' + i['issue_id'] for i in results if i['state'] == 'not_reproduced']
    errors += ['historical_regression_pending:' + i['issue_id'] for i in results
               if i['state'] in ('comparability_pending', 'specification_pending', 'feature_mapping_pending', 'guard_review_pending') or not i['record_present']]
    # A guided discovery protects the issue but is a miss by the independent
    # discovery process. Report this separately instead of hiding it in recall.
    return {'expected': len(results), 'counts': counts, 'items': results, 'errors': errors,
            'non_object_guard_expected': sum(i['regression_kind'] == 'non_object_guard' for i in results),
            'data_issue_expected': sum(i['regression_kind'] == 'data_issue' for i in results),
            'complete': not errors, 'independent_rediscovery_count': counts.get('independently_rediscovered', 0),
            'guided_only_count': counts.get('history_guided_discovery', 0),
            'note': '保护入队不算重发现；guided_only 单独披露，不能加到独立召回分子。'}


def compare_history(run, records=None):
    """Persist a versioned comparison, always computing recall from raw findings."""
    if not enabled(run):
        return {'enabled': False}
    prepare_history(run)
    path = run.path / 'coverage-history-comparison.json'
    if records is None:
        records = read(path).get('records', []) if path.exists() else []
    result = _comparison_result(run, records)
    value = {'records': records, 'result': result, 'independent_hash': digest(_frozen(run, 'coverage-independent'))}
    write(path, value)
    run.event('coverage.history.compared', sha256=digest(value), counts=result['counts'])
    return result


def _sample_population(run, manifest, jobs, rows, candidates):
    signaled = {(c['site'], c['month'], c['path']) for c in candidates}
    cells = []
    for job in jobs:
        if job['family'] != 'category' or job.get('level') != 3:
            continue
        for row in rows.get(job['job_id'], []):
            mm, path = row['month_dt'][:7], row['path']
            if (job['site'], mm, path) in signaled:
                continue
            cells.append({'cell_id': 'cell-' + business_key(job['site'], mm, 3, path),
                'site': job['site'], 'month': mm, 'level': 3, 'path': path,
                'source_job': job['job_id'], 'stratum': [job['site'], mm],
                'population_row_hash': digest({k: v for k, v in row.items() if k != 'total_rows'})})
    return sorted(cells, key=lambda c: c['cell_id'])


def prepare_sampling(run):
    import acceptance_questions as questions
    if questions.enabled(run):return questions.prepare_sampling(run)
    """One or more deterministic no-signal L3 cells in *each country/month*."""
    independent = _frozen(run, 'coverage-independent')
    manifest = _source_manifest(run)
    jobs = plan(manifest)
    rows, states = _query_states(run, jobs)
    if any(s['execution_status'] != 'success' for s in states.values()):
        raise ValueError('完整查询总体未齐，不能冻结无信号抽样分母')
    from acceptance_analysis import rebuild
    rebuilt = rebuild(run)
    if digest(rebuilt['candidates']) != independent['candidate_hash']:
        raise ValueError('无信号抽样分母的信号版本已变')
    cells = _sample_population(run, manifest, jobs, rows, rebuilt['candidates'])
    policy = run.policy.get('coverage_gate', {})
    n = policy.get('no_signal_samples_per_stratum', policy.get('sample_per_stratum', 1))
    seed = policy.get('sampling_seed', policy.get('seed', run.policy.get('sample_seed', 'acceptance-3.2')))
    if type(n) is not int or n < 1:
        raise ValueError('每层抽查数必须为正整数；不能用零关闭无信号检查')
    grouped = defaultdict(list)
    for cell in cells:
        grouped[tuple(cell['stratum'])].append(cell)
    selected = []
    for key in sorted(grouped):
        selected += sorted(grouped[key], key=lambda c: (digest([seed, c['cell_id']]), c['cell_id']))[:n]
    return _freeze(run, 'coverage-sampling-plan', {'independent_hash': digest(independent),
        'seed': seed, 'stratification': ['site', 'month'], 'per_stratum': n,
        'population': cells, 'selected_cell_ids': sorted(c['cell_id'] for c in selected),
        'expansion_scope': 'all_no_signal_cells_in_same_country_and_level',
        'coverage_claim': '仅抽中单元的具体对象；不代表全量语义核验'})


def _sampling_job(run, cell):
    """A stable object sample, including historical months, on formal identity."""
    policy = run.policy.get('coverage_gate', {})
    count = policy.get('semantic_objects_per_cell', run.policy.get('sample_per_category', 2))
    if type(count) is not int or count < 1:
        raise ValueError('每个抽查单元的对象数必须为正整数')
    seed = policy.get('sampling_seed', policy.get('seed', run.policy.get('sample_seed', 'acceptance-3.2')))
    path = path_expr(3)
    raw_path = 'CAST(JSON_ARRAY(category_1,category_2,category_3,category_4,category_5,sub_category) AS STRING)'
    # Restrict country, month and the complete standard path before aggregation.
    # platform remains part of identity; equal IDs across platforms are distinct.
    sql = f"""WITH scoped AS (
      SELECT site,month_dt,platform,product_id,sku_id,{path} AS path,
        MAX(COALESCE(NULLIF(TRIM(product_title),''),NULLIF(TRIM(sku_title),''))) AS title,
        MAX({raw_path}) AS raw_category,COUNT(DISTINCT {raw_path}) AS raw_path_count,
        COUNT(DISTINCT product_title) AS title_variants,COUNT(1) AS source_rows,
        SUM(`count`) AS units,SUM(discount_sales) AS amount
      FROM {run.policy['std_table']}
      WHERE site={quoted(cell['site'])} AND month_dt={quoted(cell['month'] + '-01')}
        AND {path}={quoted(cell['path'])}
      GROUP BY site,month_dt,platform,product_id,sku_id,path), ranked AS (
      SELECT *,COUNT(1) OVER() AS population_objects,ROW_NUMBER() OVER(ORDER BY
        MD5(CONCAT({quoted(seed)},COALESCE(platform,'<NULL>'),COALESCE(product_id,'<NULL>'),
                   COALESCE(sku_id,'<NULL>'))),platform,product_id,sku_id) AS sample_rank FROM scoped)
      SELECT *,COUNT(1) OVER() AS total_rows FROM ranked WHERE sample_rank<={count}"""
    return {'job_id': 'coverage-sample-' + business_key(cell['cell_id'], digest(sql)),
        'family': 'coverage_semantic_sample', 'site': cell['site'], 'cell_id': cell['cell_id'],
        'month': cell['month'], 'path': cell['path'], 'key_fields': ['platform', 'product_id', 'sku_id'],
        'sql': sql}


def sampling_jobs(run):
    import acceptance_questions as questions
    if questions.enabled(run):return questions.sampling_jobs(run)
    """Return pending/complete executable sample jobs for the current required set.

    Execute using the ordinary paginated ``execute_job`` transport. A miss adds
    all same-country historical cells to this queue; SQL remains read-only.
    """
    if not enabled(run):
        return []
    sampling = _frozen(run, 'coverage-sampling-plan')
    result = _sampling_audit(run)
    required = set(result['required_cell_ids'])
    return sorted([_sampling_job(run, c) for c in sampling['population'] if c['cell_id'] in required],
                  key=lambda j: j['job_id'])


def _sample_review(run, review, cell, query_record=None):
    if review.get('status') not in ('checked_clean', 'missed_signal', 'needs_investigation', 'external_blocked', 'identity_blocked'):
        raise ValueError('未知抽查状态')
    if review.get('cell_id') != cell['cell_id']:
        raise ValueError('抽查与必查单元不匹配')
    if not review.get('investigator_id') or not review.get('reviewer_id') or review['investigator_id'] == review['reviewer_id']:
        raise ValueError('抽查须由不同审核者复核，不能自行批准')
    # A sampled SQL row is located by its full original composite key and page,
    # never by placeholder SKU alone. This internal mode cannot be selected by
    # a history discovery payload and grants no cross-parent tracing identity.
    observations = _observations(run, review, identity_mode='sample_source_row')
    from acceptance_investigation import DISCOVERED_CHECKS
    if any(not isinstance(o.get('issue_type','identity'),str) or o.get('issue_type','identity') not in DISCOVERED_CHECKS for o in observations):
        raise ValueError('抽查新线索须使用维护的 issue_type；默认核身份和量价结构')
    if any(o.get('disposition') not in (None, 'missed_signal', 'needs_investigation', 'textually_compatible') for o in observations):
        raise ValueError('未知逐对象抽查判断')
    explicit_misses = [o for o in observations if o.get('disposition') == 'missed_signal']
    if explicit_misses and review['status'] == 'checked_clean':
        raise ValueError('具体对象已标漏检，不能将整格抽查记为无异常')
    if any(o.get('disposition') == 'needs_investigation' for o in observations) and review['status'] in ('checked_clean', 'missed_signal'):
        raise ValueError('具体对象仍需调查，整格抽查不能计作完成')
    if review['status'] == 'needs_investigation' and not all(
            isinstance(review.get(k), str) and review[k].strip() for k in ('reason', 'next_action')):
        raise ValueError('待调查抽查须保留具体理由和下一步动作；不得伪装外部受阻')
    unresolved = [o for o in observations if _sample_identity_state(o['object']) == 'unresolved_source_record']
    if unresolved and review['status'] != 'identity_blocked':
        raise ValueError('缺少有效平台和商品身份的源记录必须保留 identity_blocked，不能计作完成')
    if review['status'] == 'identity_blocked' and not all(review.get(k) for k in ('identity_problem', 'attempts', 'resume_condition')):
        raise ValueError('缺身份记录必须写明身份缺口、核查尝试和恢复条件')
    if any(o['site'] != cell['site'] or o['month'] != cell['month'] or
           o['feature']['facts'].get('path') != cell['path'] for o in observations):
        raise ValueError('抽查原始证据未绑定选定国家、历史月份与完整类目')
    from acceptance_validate import verify_job
    job = _sampling_job(run, cell)
    record=run.job_record(job['job_id']) if query_record is None else query_record
    query_errors = verify_job(run, job, record=record)
    if query_errors:
        raise ValueError('可复现对象抽样查询未完整成功：' + ','.join(query_errors))
    selected = run.job_rows_from_record(record,job['job_id'])
    identities = lambda rows: {canonical({k: row.get(k) for k in ('platform', 'product_id', 'sku_id')}) for row in rows}
    if not selected or identities(selected) != identities([o['object'] for o in observations]):
        raise ValueError('未检查程序依据完整总体和固定种子选定的全部对象')
    if any(o['evidence_id'] not in record['evidence_ids'] for o in observations):
        raise ValueError('抽查对象必须引用确定抽样查询的原始证据版本')
    if review['status'] == 'missed_signal' or explicit_misses:
        rule = review.get('new_rule', {})
        if not all(rule.get(k) for k in ('rule_id', 'version', 'mechanism', 'predicate', 'regression_requirement')):
            raise ValueError('漏检必须登记新规则、可执行条件与后续回归要求')
        _predicate(rule['predicate'], {})  # Validate operator even when fields are missing.
        if any(_predicate(rule['predicate'], o['feature']['facts']) is not True for o in explicit_misses):
            raise ValueError('明确标记的每个漏检对象都必须被当前回归条件命中，不能静默漏登记')
        if not _missed_observations(review):
            raise ValueError('新回归条件不能重新发现当前漏检的异常特征')
    if review['status'] == 'external_blocked' and not all(review.get(k) for k in ('attempts', 'missing_evidence', 'resume_condition')):
        raise ValueError('外部受阻必须写明尝试、具体缺证和恢复条件')
    material = {k: v for k, v in review.items() if k not in ('reviewer_id', 'reviewed_hash')}
    if review.get('reviewed_hash') != digest(material):
        raise ValueError('抽查审核未绑定确定材料版本')
    return review


def _predicate(predicate, row):
    """Small auditable rule language for future regression; never eval code."""
    if not isinstance(predicate, dict) or not predicate.get('field') or 'value' not in predicate:
        raise ValueError('回归条件必须含 field/op/value')
    op, expected = predicate.get('op'), predicate['value']
    if op not in ('eq', 'neq', 'contains', 'gt', 'lt'):
        raise ValueError('不支持的回归条件操作符')
    if predicate['field'] not in row:
        return None
    actual = row[predicate['field']]
    if op == 'eq':
        return actual == expected
    if op == 'neq':
        return actual != expected
    if op == 'contains':
        return str(expected) in str(actual)
    if actual is None:
        return None
    return float(actual) > float(expected) if op == 'gt' else float(actual) < float(expected)


def _missed_observations(review):
    """Explicit per-object judgments take precedence; unmarked v3.2 reviews retain legacy semantics."""
    return [o for o in review['observations'] if o.get('disposition') == 'missed_signal'
            or (o.get('disposition') is None and review['status'] == 'missed_signal'
                and _predicate(review['new_rule']['predicate'], o['feature']['facts']) is True)]


def _sample_source_locator(run, observation):
    """Locate this sampled source record, without inferring physical identity."""
    value = run.get_evidence(observation['evidence_id'])
    rows = _source_rows(value)
    root = '/response/data/' if 'data' in value.get('response', {}) else '/rows/'
    for index, row in enumerate(rows):
        if (all(row.get(k) == v for k, v in observation['object'].items())
                and all(row.get(k) == v for k, v in observation['feature']['facts'].items())
                and ('site' not in row or row['site'] == observation['site'])
                and (not any(k in row for k in ('month_dt', 'month'))
                     or str(row.get('month_dt', row.get('month')))[:7] == observation['month'])):
            keys = {k: row[k] for k in ('site', 'month', 'month_dt', 'path', 'platform', 'product_id', 'sku_id') if k in row}
            return {'evidence_id': observation['evidence_id'], 'pointer': root + str(index),
                'record_sha256': digest(row), 'object_keys': keys,
                'fields': sorted(set(keys) | set(observation['feature']['facts'])),
                'check_ids': [], 'role': 'support',
                'purpose': 'Read the exact deterministic sample row and anomaly facts; retain the original identity values.'}
    raise ValueError('抽查新线索无法定位原始具体记录')


def _missed_requirements(run, review, cell, query_record):
    requirements = []
    for obs in _missed_observations(review):
        observation_hash = digest(obs)
        requirements.append({'requirement_id': 'coverage-missed-' + business_key(cell['cell_id'], observation_hash),
            'cell_id': cell['cell_id'], 'site': cell['site'], 'month': cell['month'], 'path': cell['path'],
            'level': cell['level'], 'issue_type': obs.get('issue_type','identity'),
            'observation': deepcopy(obs), 'observation_sha256': observation_hash,
            'source_observation': _sample_source_locator(run, obs),
            'identity_use': 'sample_source_row_only', 'cross_parent_tracing_allowed': False})
    return {'schema': 'monthly-acceptance-missed-findings/3.2', 'sample_review_sha256': digest(review),
        'sample_query_record_sha256': digest(query_record),
        'requirements': sorted(requirements, key=lambda r: r['requirement_id'])}


def _finding_issue(requirement):
    observation = requirement['observation']
    obj = observation['object']
    issue = {k: requirement[k] for k in ('site', 'month', 'path', 'level', 'issue_type')}
    issue.update(priority='high',
        question='Investigate the concrete no-signal sample finding, its source identity, structural checks and complete aggregate impact: '
                 + observation['assessment'],
        discovered_from='Immutable no-signal sample finding ' + requirement['requirement_id'],
        coverage_requirement_id=requirement['requirement_id'],
        coverage_observation_sha256=requirement['observation_sha256'],
        coverage_observation=deepcopy(observation),
        source_observation=deepcopy(requirement['source_observation']),
        evidence_ids=[observation['evidence_id']])
    for key, field in (('product_id', 'product_ids'), ('sku_id', 'sku_ids'), ('platform', 'platforms')):
        if _identity_token(obj.get(key)):
            issue[field] = [obj[key]]
    return issue


def _finding_obligations(run, requirements):
    """Only registration is proved here; formal investigation still has its own gate."""
    rows, errors, plan = [], [], None
    obligations = {}
    if requirements:
        try:
            import acceptance_investigation as investigation
            plan = investigation.plan_data(run)
            obligations = investigation.all_obligations(run, plan)
        except (ValueError, KeyError, TypeError, FileNotFoundError) as exc:
            errors.append('finding_investigation_plan_pending:' + str(exc))
    for requirement in sorted(requirements.values(), key=lambda r: r['requirement_id']):
        rid = requirement['requirement_id']
        matches = [o for o in obligations.values() if o.get('source_type') == 'discovered'
                   and o.get('source_material', {}).get('coverage_requirement_id') == rid]
        valid = []
        expected = _finding_issue(requirement)
        for ob in matches:
            source = ob['source_material']
            if (source.get('coverage_observation_sha256') != requirement['observation_sha256']
                    or source.get('coverage_observation') != requirement['observation']
                    or source.get('source_observation') != requirement['source_observation']
                    or source.get('issue_type') != requirement['issue_type']
                    or source.get('priority') != 'high'
                    or ob.get('source_hash') != digest(source)
                    or ob.get('source_observation_sha256') != digest(requirement['source_observation'])
                    or ob.get('required_checks') != investigation.DISCOVERED_CHECKS[requirement['issue_type']]):
                continue
            fields = ('site', 'month', 'path', 'level', 'product_ids', 'sku_ids', 'platforms')
            if any(ob['scope'].get(k) != expected.get(k) for k in fields):
                continue
            contract = ob.get('contract', {})
            if contract.get('source_hash') != ob['source_hash'] or contract.get('scope') != ob['scope']:
                continue
            try:
                import acceptance_review as review
                review.locator(run.get_evidence, source['source_observation'], ob)
            except (ValueError, KeyError, TypeError, FileNotFoundError):
                continue
            valid.append(ob['obligation_id'])
        state = 'obligation_registered' if len(valid) == 1 else 'formal_investigation_pending'
        if state != 'obligation_registered':
            errors.append('missed_finding_obligation_pending:' + rid)
        rows.append({**requirement, 'state': state, 'obligation_ids': sorted(valid),
            'coordinator_id': plan['coordinator_id'] if plan else None})
    return {'expected': len(rows), 'registered': sum(r['state'] == 'obligation_registered' for r in rows),
        'pending_requirement_ids': [r['requirement_id'] for r in rows if r['state'] != 'obligation_registered'],
        'items': rows, 'errors': errors, 'complete': not errors,
        'completion_semantics': 'Formal discovered obligation registration only; investigation completion requires its current submission and main review.'}


def sampling_finding_requirements(run):
    """Read machine-derived, immutable missed findings and their formal routing state."""
    if not enabled(run):
        return {'enabled': False, 'items': [], 'complete': True}
    return _sampling_audit(run)['finding_obligations']


def register_sampling_findings(run, coordinator, requirement_ids=None):
    """The registered coordinator, never the sample reviewer, routes actual findings."""
    import acceptance_investigation as investigation
    investigation.coordinator_only(run, coordinator)
    state = sampling_finding_requirements(run)
    requirements = {r['requirement_id']: r for r in state['items']}
    ids = state['pending_requirement_ids'] if requirement_ids is None else requirement_ids
    if not isinstance(ids, list) or len(ids) != len(set(ids)) or not set(ids) <= set(requirements):
        raise ValueError('抽查新线索登记要求未知或重复')
    added = investigation.add_obligations(run, coordinator, [_finding_issue(requirements[rid]) for rid in ids])
    return {'registered_obligation_ids': added, 'finding_obligations': sampling_finding_requirements(run)}


def record_sample_review(run, review):
    sampling = _frozen(run, 'coverage-sampling-plan')
    cells = {c['cell_id']: c for c in sampling['population']}
    ident = review.get('cell_id')
    if ident not in cells:
        raise ValueError('抽查未知单元')
    _sample_review(run, review, cells[ident])
    path = run.path / 'coverage-sampling-reviews.json'
    records = read(path) if path.exists() else {}
    records[ident] = review
    write(run.path / 'coverage-sampling-history' / (digest(review) + '.json'), review)
    query_record = run.job_record(_sampling_job(run, cells[ident])['job_id'])
    write(run.path / 'coverage-sampling-history' / (digest(review) + '-query.json'), query_record)
    extra = {}
    if _missed_observations(review):
        requirements = _missed_requirements(run, review, cells[ident], query_record)
        write(run.path / 'coverage-sampling-history' / (digest(review) + '-requirements.json'), requirements)
        extra['finding_requirements_sha256'] = digest(requirements)
        extra['has_missed_findings'] = True
    write(path, records)
    run.event('coverage.sample.reviewed', cell_id=ident, sha256=digest(review), status=review['status'],
              query_record_hash=digest(query_record), **extra)
    return _sampling_audit(run)


def _validate_sampling_rule(run, rule):
    if not all(rule.get(k) for k in ('rule_id', 'version', 'predicate', 'author_id', 'reviewer_id')):
        raise ValueError('回归规则缺少版本、条件和独立审核')
    if rule['author_id'] == rule['reviewer_id']:
        raise ValueError('回归规则不能自行批准')
    tests = rule.get('tests', [])
    if {t.get('expected') for t in tests} != {True, False}:
        raise ValueError('新规则必须包含命中与反例的原始证据测试')
    for test in tests:
        value = run.get_evidence(test['evidence_id'])
        selector = test.get('selector')
        if not isinstance(selector, dict) or not selector:
            raise ValueError('规则测试缺少具体原始行选择器')
        matching = [r for r in _source_rows(value) if all(r.get(k) == v for k, v in selector.items())]
        if not matching or any(_predicate(rule['predicate'], row) is not test['expected'] for row in matching):
            raise ValueError('回归规则在实际证据上未通过命中/反例测试')
    material = {k: v for k, v in rule.items() if k not in ('reviewer_id', 'reviewed_hash')}
    if rule.get('reviewed_hash') != digest(material):
        raise ValueError('回归规则审核版本失效')


def register_sampling_rule(run, rule):
    """Register a shadow rule only after raw positive *and counterexample* tests.

    This persists the executable predicate and test evidence for subsequent
    regression. It does not authorize a business disposition or claim that all
    future data have already been scanned by this rule.
    """
    _validate_sampling_rule(run, rule)
    path = run.path / 'coverage-regression-rules.json'
    records = read(path) if path.exists() else {}
    key = rule['rule_id'] + '@' + rule['version']
    if key in records and records[key] != rule:
        raise ValueError('回归规则版本已经登记，不能覆盖')
    records[key] = rule
    write(path, records)
    run.event('coverage.rule.registered', rule_key=key, sha256=digest(rule), mode='shadow')
    return {'rule_key': key, 'mode': 'shadow', 'business_dispositions': 0}


def _sampling_audit(run):
    sampling = _frozen(run, 'coverage-sampling-plan')
    cells = {c['cell_id']: c for c in sampling['population']}
    path = run.path / 'coverage-sampling-reviews.json'
    reviews = read(path) if path.exists() else {}
    rules_path = run.path / 'coverage-regression-rules.json'
    rules = read(rules_path) if rules_path.exists() else {}
    errors, valid, missed, required = [], {}, [], set(sampling['selected_cell_ids'])
    historical_misses, requirements = {}, {}
    for event in _events(run, 'coverage.sample.reviewed'):
        if event.get('status') != 'missed_signal' and event.get('has_missed_findings') is not True:
            continue
        ident = event['cell_id']
        try:
            review = read(run.path / 'coverage-sampling-history' / (event['sha256'] + '.json'))
            if digest(review) != event['sha256']:
                raise ValueError('漏检历史版本哈希变更')
            historical_misses[(ident, event['sha256'])] = review
            query_record = read(run.path / 'coverage-sampling-history' / (event['sha256'] + '-query.json'))
            if digest(query_record) != event.get('query_record_hash'):
                raise ValueError('漏检原始查询版本哈希变更')
            _sample_review(run, review, cells[ident], query_record=query_record)
            expected = _missed_requirements(run, review, cells[ident], query_record)
            requirements.update((r['requirement_id'], r) for r in expected['requirements'])
            if event.get('finding_requirements_sha256'):
                stored = read(run.path / 'coverage-sampling-history' / (event['sha256'] + '-requirements.json'))
                if digest(stored) != event['finding_requirements_sha256'] or stored != expected:
                    raise ValueError('漏检正式调查要求被删除或改变')
            # Early v3.2 review events are replayed from their already frozen
            # original rows. They cannot escape investigation by lacking a new field.
        except Exception as exc:
            errors.append('missed_signal_history_invalid:' + ident + ':' + str(exc))
    for ident, review in reviews.items():
        try:
            if ident not in cells:
                raise ValueError('未知抽查单元')
            _sample_review(run, review, cells[ident])
            if not any(e.get('sha256') == digest(review) and e.get('cell_id') == ident
                       for e in _events(run, 'coverage.sample.reviewed')):
                raise ValueError('抽查记录无审核事件或材料已改变')
            valid[ident] = review
        except Exception as exc:
            errors.append('sampling_review_invalid:' + ident + ':' + str(exc))
    for (ident, _), review in historical_misses.items():
        missed.append(ident)
        required.update(c['cell_id'] for c in cells.values()
                        if (c['site'], c['level']) == (cells[ident]['site'], cells[ident]['level']))
        proposed = review['new_rule']
        key = proposed['rule_id'] + '@' + proposed['version']
        rule = rules.get(key)
        try:
            if not rule or rule.get('predicate') != proposed['predicate'] or not any(
                    e.get('rule_key') == key and e.get('sha256') == digest(rule)
                    for e in _events(run, 'coverage.rule.registered')):
                raise ValueError('规则未登记或确定版本变更')
            _validate_sampling_rule(run, rule)
        except Exception as exc:
            errors.append('missed_signal_regression_rule_pending:' + ident + ':' + str(exc))
    done = {ident for ident, r in valid.items() if r['status'] in ('checked_clean', 'missed_signal')}
    pending = sorted(required - done)
    if pending:
        errors.append('sampling_or_expansion_incomplete:' + str(len(pending)))
    sample_jobs = [_sampling_job(run, cells[ident]) for ident in sorted(required)]
    sample_rows, query_states = _query_states(run, sample_jobs)
    missing_samples = [j['cell_id'] for j in sample_jobs if query_states[j['job_id']]['execution_status'] != 'success'
                       or not sample_rows.get(j['job_id'])]
    if missing_samples:
        errors.append('sampling_queries_missing_or_failed:' + str(len(missing_samples)))
    findings = _finding_obligations(run, requirements)
    errors += findings['errors']
    return {'expected': len(required), 'selected': len(sampling['selected_cell_ids']),
        'expanded': len(required - set(sampling['selected_cell_ids'])), 'verified': len(required & done),
        'no_signal_population': len(cells), 'missed_signal_count': len(set(missed)),
        'pending_cell_ids': pending, 'required_cell_ids': sorted(required),
        'reviewed_cells': {k: v['status'] for k, v in valid.items()},
        'identity_scope_by_cell': {k: {
            'states': dict(Counter(_sample_identity_state(o['object']) for o in v['observations'])),
            'observation_identity_use': 'sample_source_row_only',
            'physical_identity_equivalence_proven': False, 'cross_parent_tracing_allowed': False}
            for k, v in valid.items()},
        'cell_query_states': {j['cell_id']: query_states[j['job_id']] for j in sample_jobs},
        'finding_obligations': findings,
        'complete': not errors, 'errors': errors, 'coverage_claim': sampling['coverage_claim']}


def _row_index(jobs, rows):
    indexed = defaultdict(list)
    for jid, items in rows.items():
        for row in items:
            mm = str(row.get('month_dt', ''))[:7] or None
            path = row.get('path')
            indexed[(jid, mm, None)].append(row)
            if path is not None:
                indexed[(jid, mm, path)].append(row)
    return indexed


def _candidate_index(candidates):
    indexed = defaultdict(list)
    for c in candidates:
        for level, path in {(None, None), (c.get('level'), None), (None, c['path']), (c.get('level'), c['path'])}:
            indexed[(c['site'], c['month'], level, path)].append(c)
    return indexed


def _controlled_country_months(jobs, rows, states):
    observed = set()
    for job in jobs.values():
        if job['family'] != 'fingerprint' or states[job['job_id']]['execution_status'] != 'success':
            continue
        for row in rows.get(job['job_id'], []):
            nrows = row.get('nrows')
            if isinstance(nrows, (int, float)) and not isinstance(nrows, bool) and nrows > 0:
                observed.add((job['site'], job['entity'], row['month_dt'][:7]))
    return observed


def _task_execution(task, jobs, rows, states, candidates, analyzed, sampling, sampling_plan, row_index, country_months):
    result = {**task, 'execution_status': 'not_executed', 'evidence_ids': [],
              'execution_completed': False, 'query_scope': task['scope'], 'pagination': {},
              'data_time': {}, 'signal_ids': [], 'errors': []}
    if task['applicability'] == 'not_applicable':
        if not task.get('not_applicable_reason'):
            result['errors'].append('not_applicable_without_reason')
        return result
    if task['applicability'] == 'undetermined':
        result['execution_status'] = 'missing_data'
        result['errors'].append('applicability_unknown')
        return result
    for jid in task['source_jobs']:
        source = states[jid]
        result['evidence_ids'] += source['evidence_ids']
        result['pagination'][jid] = source['pagination']
        result['data_time'][jid] = source['data_time']
    result['evidence_ids'] = sorted(set(result['evidence_ids']))
    if not task['source_jobs']:
        result['errors'].append('required_source_query_missing')
        return result
    if any(states[j]['execution_status'] == 'query_failed' for j in task['source_jobs']):
        result['execution_status'] = 'query_failed'
        return result
    if any(states[j]['execution_status'] != 'success' for j in task['source_jobs']):
        return result
    scope, rule = task['scope'], task['rule_id']
    if rule == 'no_signal_semantic_sample':
        if not sampling_plan:
            return result
        population = [c for c in sampling_plan['population'] if c['site'] == scope['site'] and c['month'] == scope['month']]
        if not population:
            observed = any(r['month_dt'][:7] == scope['month'] for j in task['source_jobs'] for r in rows[j])
            if observed:
                result.update(applicability='not_applicable', not_applicable_reason='完整该月类目总体内不存在无信号单元；全部信号义务仍须调查')
            else:
                result['execution_status'] = 'missing_data'
            return result
        required = set(sampling['required_cell_ids']) & {c['cell_id'] for c in population}
        checked = sampling['reviewed_cells']
        for ident in sorted(required):
            source = sampling['cell_query_states'][ident]
            jid = source['job_id']
            result['pagination'][jid] = source['pagination']
            result['data_time'][jid] = source['data_time']
            result['evidence_ids'] += source['evidence_ids']
        result['evidence_ids'] = sorted(set(result['evidence_ids']))
        if required and all(checked.get(c) in ('checked_clean', 'missed_signal') for c in required):
            result['execution_status'] = 'success_signal' if any(checked[c] == 'missed_signal' for c in required) else 'success_clean'
            result['execution_completed'] = True
            result['sampling_cell_ids'] = sorted(required)
        elif any(sampling['cell_query_states'][c]['execution_status'] == 'query_failed' for c in required):
            result['execution_status'] = 'query_failed'
        return result
    for jid in task['source_jobs']:
        job = jobs[jid]
        needed_months = [scope['month']]
        if scope.get('base_month') and job['family'] in ('category', 'bands'):
            needed_months.append(scope['base_month'])
        for mm in needed_months:
            actual_month = None if job['family'] in ('release', 'sample') else mm
            if scope['category_scope'] == 'contract_prefix':
                prefix = json.loads(scope['path'])
                matches = [r for r in row_index[(jid, actual_month, None)]
                           if json.loads(r.get('path', '[]'))[:len(prefix)] == prefix]
            else:
                matches = row_index[(jid, actual_month, scope['path'])]
            if not matches:
                result['execution_status'] = 'missing_data'
                result['errors'].append('no_observation:' + jid + ':' + mm)
                layer = 'raw' if job['family'] == 'raw_paths' else 'std'
                # A completed query can establish absence for a specific path.
                # Country-wide absence and unverified/truncated query populations
                # cannot. This is execution completion, never a clean-data verdict.
                result['execution_completed'] = (analyzed and scope['path'] is not None
                    and (scope['site'], layer, mm) in country_months)
                result['observation_state'] = ('confirmed_path_no_observation' if result['execution_completed']
                                               else 'data_availability_unresolved')
                result['business_issue_state'] = 'not_resolved_by_execution'
                result['signal_ids'] = sorted({c['candidate_id'] for c in candidates.get(
                    (scope['site'], mm, scope['level'], scope['path']), [])
                    if c['kind'] in ('raw_presence_gap', 'std_presence_gap', 'contract_scope_missing', 'band_conservation')})
                return result
    if not analyzed:
        result['errors'].append('rule_evaluation_not_rebuilt')
        return result
    kinds = {'arrival_raw': ('missing_country_month_raw', 'invalid_values_raw'),
        'arrival_std': ('missing_country_month_std', 'invalid_values_std'),
        'identity_mapping': ('identity_mapping',), 'raw_presence': ('raw_presence_gap',),
        'category_values': ('invalid_category_values', 'std_presence_gap'),
        'price_bands': ('band_conservation',), 'mom': ('result_change',), 'yoy': ('result_change',)}
    for candidate in candidates.get((scope['site'], scope['month'], scope['level'], scope['path']), []):
        if candidate['site'] != scope['site'] or candidate['month'] != scope['month']:
            continue
        if scope['path'] is not None and candidate['path'] != scope['path']:
            continue
        if scope['level'] is not None and candidate.get('level') != scope['level']:
            continue
        if rule.startswith('contract:'):
            matched = candidate['kind'] == 'contract_scope_missing' and candidate.get('facts', {}).get('contract_entry') == rule.split(':', 1)[1]
        else:
            matched = candidate['kind'] in kinds.get(rule, ()) and (rule not in ('mom', 'yoy') or candidate.get('comparison') == rule)
        if matched:
            result['signal_ids'].append(candidate['candidate_id'])
    result['execution_status'] = 'success_signal' if result['signal_ids'] else 'success_clean'
    result['execution_completed'] = True
    return result


def audit(run, include_tasks=False):
    import acceptance_questions as questions
    if questions.enabled(run):return questions.coverage_status(run,include_tasks)
    """Read-only, recomputed gate; failed/missing work always remains visible."""
    if not enabled(run):
        return {'enabled': False, 'errors': [], 'scan_complete': True, 'history_complete': True, 'sampling_complete': True}
    errors, limitations, tasks, states = [], [], [], {}
    historical = {'expected': None, 'complete': False, 'errors': ['historical_comparison_missing']}
    sampling = {'expected': None, 'complete': False, 'errors': ['no_signal_sampling_plan_missing']}
    try:
        manifest = _source_manifest(run)
        jobs = plan(manifest)
        job_index = {j['job_id']: j for j in jobs}
        expected_roots = _roots(manifest, jobs)
        try:
            frozen = _frozen(run, 'coverage-plan')
            if frozen.get('tasks') != expected_roots or frozen.get('query_plan_hash') != digest(jobs):
                errors.append('mandatory_tasks_missing_or_changed')
        except Exception as exc:
            errors.append('mandatory_plan_missing_or_invalid:' + str(exc))
        registered = read(run.path / 'plan.json') if (run.path / 'plan.json').exists() else {}
        if registered.get('jobs') != jobs or registered.get('hash') != digest(jobs):
            errors.append('source_query_plan_missing_or_changed')
        rows, states = _query_states(run, jobs)
        domain = _domain(run, manifest, jobs, rows)
        analyzed, candidates = False, []
        if all(s['execution_status'] == 'success' for s in states.values()):
            try:
                from acceptance_analysis import rebuild
                rebuilt = rebuild(run)
                candidates = rebuilt['candidates']
                independent = _frozen(run, 'coverage-independent')
                if independent['candidate_hash'] != digest(candidates) or independent['query_population_hash'] != _query_population_hash(jobs, rows):
                    raise ValueError('独立发现数据总体或信号版本改变')
                if _frozen(run, 'coverage-domain')['tasks'] != domain:
                    raise ValueError('逐类目必查任务缺失或改变')
                analyzed = True
            except Exception as exc:
                errors.append('independent_scan_unverified:' + str(exc))
        else:
            errors.append('required_queries_incomplete')
        sampling_plan = None
        try:
            sampling_plan = _frozen(run, 'coverage-sampling-plan')
            if analyzed and sampling_plan['population'] != _sample_population(run, manifest, jobs, rows, candidates):
                raise ValueError('无信号抽样总体改变')
            sampling = _sampling_audit(run)
        except Exception as exc:
            sampling_plan = None
            errors.append('no_signal_sampling_unverified:' + str(exc))
        row_index, candidate_index = _row_index(job_index, rows), _candidate_index(candidates)
        country_months = _controlled_country_months(job_index, rows, states)
        tasks = [_task_execution(t, job_index, rows, states, candidate_index, analyzed, sampling, sampling_plan, row_index, country_months)
                 for t in expected_roots + domain]
        try:
            path = run.path / 'coverage-history-comparison.json'
            historical = _comparison_result(run, read(path).get('records', []) if path.exists() else [])
            if not path.exists():
                historical['errors'].append('historical_comparison_missing')
                historical['complete'] = False
        except Exception as exc:
            errors.append('historical_regression_unverified:' + str(exc))
    except Exception as exc:
        errors.append('coverage_source_unverified:' + str(exc))
    incomplete = [t['task_id'] for t in tasks if t['applicability'] != 'not_applicable'
                  and not t['execution_completed']]
    if incomplete:
        errors.append('mandatory_checks_incomplete:' + str(len(incomplete)))
    errors += historical['errors'] + sampling['errors']
    limitations += ['无信号抽查不代表全量语义核验；未列入冻结合同的从未出现叶节点仍有范围限制。']
    result = {'enabled': True, 'schema': SCHEMA, 'expected': len(tasks),
        'applicable': sum(t['applicability'] != 'not_applicable' for t in tasks),
        'not_applicable': sum(t['applicability'] == 'not_applicable' for t in tasks),
        'execution_counts': dict(Counter(t['execution_status'] for t in tasks if t['applicability'] != 'not_applicable')),
        'execution_completed': sum(t['execution_completed'] for t in tasks if t['applicability'] != 'not_applicable'),
        'confirmed_path_absence_checks': sum(t['execution_status'] == 'missing_data' and t['execution_completed'] for t in tasks),
        'query_counts': dict(Counter(s['execution_status'] for s in states.values())),
        'scan_complete': bool(tasks) and not incomplete and not any(e.startswith((
            'mandatory_', 'coverage_source_', 'source_query_', 'independent_scan_', 'required_queries_')) for e in errors),
        'history_complete': historical['complete'], 'sampling_complete': sampling['complete'],
        'complete': not errors, 'history': historical, 'sampling': sampling,
        'errors': sorted(set(errors)), 'limitations': limitations}
    if include_tasks:
        result['tasks'] = tasks
    return result
