"""Machine-verifiable evidence/review contracts; no claim to verify understanding.

Locators validate the actual record and fields, arithmetic validates calculations,
and role/version checks prevent self-approval. Semantic relevance beyond structured
scope, causal validity, and an agent's actual understanding remain reviewer duties.
"""
import json
import math
import re
from pathlib import Path

from acceptance_core import Run, canonical, digest, read, shift
from acceptance_protocol import current

DIMENSIONS = {
    'execution': {'success', 'failed', 'timeout', 'not_executed'},
    'investigation': {'completed', 'in_progress', 'blocked_external', 'capacity_pending'},
    'defect': {'confirmed', 'not_found', 'undetermined'},
    'cause': {'explained', 'partially_explained', 'unexplained', 'not_applicable'},
    'impact': {'quantified', 'bounded', 'unknown', 'not_applicable'},
    'review': {'pending', 'approved', 'returned'},
    'acceptability': {'pass', 'qualified', 'reject', 'undetermined'},
}
QUESTIONS = {
    'scope_baseline': '国家、平台、完整路径、比较月份及完整对象分母是否与冻结义务一致？',
    'brand_contribution': '全部具名品牌的量额正负贡献、进出及尾部是否守恒？',
    'spu_contribution': '完整SPU并集的正负贡献、新入、退出及未解释部分分别是多少？',
    'sku_identity': '完整对象的真实平台/SKU、父子身份及相关历史是否支持判断？',
    'price_volume': '销量、金额、单位、包装与固定价格带变化分别如何解释？',
    'scope_contract': '有效期内的采集契约及全部成员是否支持范围判断？',
    'sku_destination': '缺口前最近完整基线的每个对象在每个缺口月及后续月去了哪里？',
    'source_mapping': '原始、标准、映射及发布来源的关系与异常传播是否有据？',
    'counterevidence': '主要替代解释、反例及历史异议如何处理，仍有什么不确定？',
    'four_metric_impact': 'SPU、价格带、销量、金额影响和未解释部分的完整分母是什么？',
    'historical_object_match': '历史具体对象、全部月份及异常特征是否逐项复现或有据纠正？',
}
NON_OBJECT_GUARDS = {'known_method_defect_regression', 'known_interpretation_defect_regression',
                    'provenance_and_comparability_guard', 'exposure_and_claim_boundary_guard',
                    'coverage_execution_guard', 'report_contract_regression',
                    'external_evidence_execution_guard', 'retired_workflow_not_data_issue',
                    'retired_signal_regression', 'not_applicable_history'}


def enabled(value):
    manifest = value.manifest if hasattr(value, 'manifest') else value
    policy = manifest.get('policy', manifest)
    version = tuple(int(x) for x in re.findall(r'\d+', str(policy.get('version', '0')))[:2])
    return current(manifest) or version >= (3, 2) or policy.get('review_gate', {}).get('enabled') is True


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('缺少实质记录：' + label)
    return value


def unique(value, label, empty=False):
    if not isinstance(value, list) or (not value and not empty):
        raise ValueError('缺少完整清单：' + label)
    if len(value) != len({canonical(x) for x in value}):
        raise ValueError('重复记录：' + label)
    return value


def contract(manifest, obligation, source=None):
    """Deterministic assignment; a change needs an immutable coordinator amendment."""
    source = source or obligation.get('source_material', {})
    scope = obligation['scope']
    site = scope.get('site')
    selected = list(scope.get('months') or [])
    if scope.get('month'): selected.append(scope['month'])
    comparison = scope.get('comparison')
    if comparison in manifest['policy']['comparisons'] and scope.get('month'):
        selected.append(shift(scope['month'], -manifest['policy']['comparisons'][comparison]))
    value = {
        'protocol': '3.2', 'obligation_id': obligation['obligation_id'],
        'source_id': obligation['source_id'], 'source_hash': obligation['source_hash'],
        'scope': scope, 'original_scope': scope,
        'object_population': {'selection': 'all_members_in_scope', 'product_ids': scope.get('product_ids'),
                             'sku_ids': scope.get('sku_ids'), 'platforms': scope.get('platforms', source.get('platforms')),
                             'identity': ['site', 'platform', 'month', 'sku', 'parent', 'raw_path', 'standard_path']},
        'history_window': {'required_comparison_months': sorted(set(selected)),
                           'history_start': manifest['starts'].get(site, min(manifest['starts'].values())),
                           'history_end': manifest['data_month'],
                           'gap_baseline_rule': 'latest_complete_observation_strictly_before_gap',
                           'gap_followup_rule': 'every_calendar_month_and_raw_std_through_history_end'},
        'questions': [{'check_id': n, 'question': QUESTIONS.get(n, '逐项核查 '+n+' 的完整对象、证据及结论边界。')}
                      for n in obligation['required_checks']],
        'required_evidence': [{'check_id': n, 'requires_raw_locator': True,
                              'requires_complete_population': n in ('spu_contribution', 'brand_contribution', 'sku_destination', 'four_metric_impact')}
                             for n in obligation['required_checks']],
        'drill_triggers': ['material_unexplained_positive_or_negative_contribution', 'unresolved_counterevidence',
                          'historical_problem_not_reproduced', 'cross_path_or_parent_migration', 'unbounded_risk'],
        'completion_conditions': ['every_question_has_scoped_evidence', 'all_internal_actions_performed',
                                  'full_population_reconciled_where_applicable', 'material_residual_continues',
                                  'all_returns_resolved', 'necessary_independent_review', 'version_bound_main_review'],
        'blocking_conditions': ['specific_unavailable_external_record_with_attempt_receipt_and_resume_condition'],
        'allowed_fulfillment_modes': ['direct', 'machine_rule'],
        'scope_change_authority': 'registered_coordinator_with_evidence_and_immutable_amendment',
        'policy_sha256': digest(manifest['policy'])}
    return {**value, 'sha256': digest(value)}


def pointer(value, path):
    if not isinstance(path, str) or not path.startswith('/') or path == '/':
        raise ValueError('原始证据需要具体JSON pointer，不能只引用整个文件')
    try:
        for token in path[1:].split('/'):
            token = token.replace('~1', '/').replace('~0', '~')
            value = value[int(token)] if isinstance(value, list) else value[token]
        return value
    except (KeyError, TypeError, IndexError, ValueError) as exc:
        raise ValueError('原始证据定位不存在：' + path) from exc


def _path(value):
    try: return json.loads(value) if isinstance(value, str) else value
    except ValueError: return value


def locator(lookup, value, obligation=None, check_id=None, *, object_ids=None):
    """Resolve one raw record with actual fields, identity and optional text lines."""
    if not isinstance(value, dict): raise ValueError('需要结构化原始证据定位')
    node = pointer(lookup(text(value.get('evidence_id'), 'locator.evidence_id')), value.get('pointer'))
    if not isinstance(node, dict): raise ValueError('原始证据定位须选具体记录对象，再指定实际字段/行号')
    if value.get('record_sha256') != digest(node): raise ValueError('原始证据定位的记录哈希不一致')
    fields = unique(value.get('fields'), '实际核对的原始字段')
    if any(not isinstance(k, str) or k not in node for k in fields): raise ValueError('核对字段不在原始记录中')
    keys = value.get('object_keys')
    if not isinstance(keys, dict) or not keys or any(k not in node or node[k] != v for k, v in keys.items()):
        raise ValueError('证据对象字段与所称对象不一致')
    text(value.get('purpose'), '原始证据与判断的关系')
    if check_id is not None and check_id not in value.get('check_ids', []): raise ValueError('原始证据未关联当前调查问题')
    if 'lines' in value:
        lines, field = value['lines'], value.get('text_field')
        if field not in fields or not isinstance(node.get(field), str) or not isinstance(lines, list) or len(lines) != 2:
            raise ValueError('原文须指定实际文本字段和行号')
        if any(isinstance(x, bool) or not isinstance(x, int) for x in lines) or not 1 <= lines[0] <= lines[1] <= len(node[field].splitlines()):
            raise ValueError('原文行号越界')
        if value.get('excerpt_sha256') != digest('\n'.join(node[field].splitlines()[lines[0]-1:lines[1]])):
            raise ValueError('原文行号内容哈希不一致')
    if object_ids is not None:
        found = {keys[k] for k in ('product_id', 'seed_product_id', 'std_product_id') if k in keys}
        if not found or not found <= set(object_ids): raise ValueError('原因证据与解释的具体对象无关')
    if obligation is not None:
        scope, source = obligation['scope'], obligation.get('source_material', {})
        source_match = False
        if source.get('source_path') and keys.get('source_path') == source['source_path'] and value.get('role') != 'frozen_source':
            raise ValueError('路径名相同不能证明读过被分派的原文，须使用真实frozen_source定位')
        if value.get('role') == 'frozen_source':
            source_match = _verified_frozen_source(lookup, value, obligation)
        if not source_match:
            if scope.get('site') not in (None, 'global') and keys.get('site') != scope['site']:
                raise ValueError('证据国家与结论范围无关')
            mm = str(keys.get('month', keys.get('month_dt', '')))[:7]
            window = obligation.get('contract', {}).get('history_window', {})
            required = window.get('required_comparison_months') or scope.get('months') or [scope.get('month')]
            if mm not in required:
                if value.get('role') != 'historical_context' or not window.get('history_start', '9999') <= mm <= window.get('history_end', ''):
                    raise ValueError('证据月份与结论比较窗口无关')
            ids = scope.get('product_ids') or object_ids
            if ids:
                actual = {keys[k] for k in ('product_id', 'seed_product_id', 'std_product_id') if k in keys}
                if not actual or not actual <= set(ids): raise ValueError('证据未定位义务的具体历史对象')
            elif _path(scope.get('path')) not in (None, []):
                paths = [_path(keys[k]) for k in ('path', 'std_path', 'raw_path', 'seed_std_path', 'seed_raw_path') if k in keys]
                target = _path(scope['path'])
                if not any(p == target or isinstance(p, list) and isinstance(target, list) and p[:len(target)] == target for p in paths):
                    related = value.get('target_relation')
                    if not isinstance(related, dict) or related.get('role') in ('counterevidence', 'destination'):
                        raise ValueError('证据路径与当前结论无关，跨路径须另附真实目标身份关联')
                    other = locator(lookup, related, obligation)
                    if not all(k in node and k in other and node[k] == other[k] for k in ('site', 'platform', 'sku_id')):
                        raise ValueError('跨路径证据缺少同国家/平台/实际SKU身份关联')
                    from acceptance_diagnostics import traceable_sku
                    if not traceable_sku(node['sku_id']): raise ValueError('占位SKU不能建立跨路径证据关系')
    return node


def evidence_links(lookup, values, obligation, check_id=None, *, object_ids=None):
    unique(values, '有对象及字段的原始证据定位')
    return [locator(lookup, v, obligation, check_id, object_ids=object_ids) for v in values]


def validate_dimensions(answer, blocked):
    values = answer.get('status_dimensions')
    if not isinstance(values, dict) or set(values) != set(DIMENSIONS):
        raise ValueError('执行、调查、缺陷、原因、影响、主审、业务接受必须分别记录')
    if any(v not in DIMENSIONS[k] for k, v in values.items()): raise ValueError('无效的独立状态维度')
    if values['execution'] != 'success' or values['investigation'] != ('blocked_external' if blocked else 'completed'):
        raise ValueError('失败、超时、未执行或未完成不能作为完成答卷；保存进度并重新入队')
    if values['review'] != 'pending': raise ValueError('子Agent无权填写已批准主审状态')
    if values['acceptability'] != answer.get('business_verdict'): raise ValueError('业务接受状态与业务判断不一致')
    if (answer.get('defect_confirmed') is True) != (values['defect'] == 'confirmed'):
        raise ValueError('缺陷状态不能由原因解释或调查完成替代')
    if values['cause'] == 'explained' and any(i.get('state') == 'unknown' or i.get('unexplained', 0) > 0 for i in answer.get('impact', {}).values()):
        raise ValueError('仍有未解释部分，不能声称整个异常原因已解释')
    if values['impact'] == 'unknown' and not blocked: raise ValueError('未知影响须保留真实阻塞或继续调查')
    if values['acceptability'] == 'pass' and (values['cause'] not in ('explained', 'not_applicable') or values['defect'] == 'undetermined'):
        raise ValueError('原因仍部分未解释或缺陷未定，不能给出无保留验收通过')


_EXTERNAL_OWNERS = {'missing_external_record': {'data_provider'},
                    'missing_contract': {'contract_owner'},
                    'missing_definition': {'taxonomy_owner'},
                    'missing_internal_data': {'internal_data_owner'},
                    'external_access_denied': {'data_provider', 'internal_data_owner'}}


def _receipt_origin(run, lookup, eid):
    """Follow recorded imports to the actual operation, never promote an attachment."""
    resolver = getattr(lookup, 'receipt_origin', None)
    if resolver:
        owner, original = resolver(eid)
    else:
        owner = getattr(lookup, '__self__', None)
        owner = owner if isinstance(owner, Run) else run
        original = eid
        if eid.startswith('main:'):
            owner = Run(owner.manifest['parent_run']) if owner.manifest.get('parent_run') else run
            original = eid[5:]
    visited = set()
    while True:
        key = (str(owner.path), original)
        if key in visited: raise ValueError('取证尝试来源存在循环')
        visited.add(key)
        value = owner.get_evidence(original)
        meta = read(owner.path / 'records' / (original+'.json'))
        source = meta.get('source')
        if meta.get('kind') == 'investigation_worker' and isinstance(source, dict):
            from acceptance_investigation import task_records
            task = task_records(owner).get(source.get('task_id'))
            if not task: raise ValueError('取证尝试缺原子任务来源')
            target, next_id = Run(owner.path/task['worker_dir']), source.get('worker_evidence_id')
            expected = source.get('worker_evidence_sha256')
        elif meta.get('kind') in ('verified-reused-query', 'archived-query-response') and isinstance(source, dict):
            target, next_id = Run(source['source_run']), source.get('source_evidence_id')
            expected = source.get('source_sha256')
        else:
            return owner, original, meta, value
        from acceptance_snapshot import read_bytes
        if not next_id or digest(read_bytes(target.path/'evidence'/(next_id+'.json'))) != expected or target.get_evidence(next_id) != value:
            raise ValueError('取证尝试导入与原始操作证据不一致')
        owner, original = target, next_id


def _source_record(run, relative_path):
    target = (run.path/relative_path).resolve()
    if relative_path != 'run.json' and (not target.is_relative_to(run.path/'inputs') or relative_path not in run.manifest['inputs']):
        raise ValueError('文档证据必须来自当前真实冻结输入或运行manifest')
    from acceptance_snapshot import read_bytes
    content = read_bytes(target)
    if relative_path != 'run.json' and digest(content) != run.manifest['inputs'][relative_path]['sha256']:
        raise ValueError('原始冻结文档哈希不一致')
    original = content.decode('utf-8')
    parsed = None
    if target.suffix == '.json': parsed = json.loads(original)
    elif target.suffix in ('.yaml','.yml'):
        import yaml
        parsed = yaml.safe_load(original)
    return {'source_path':relative_path,'source_sha256':digest(content),'text':original,'parsed':parsed}


def capture_frozen_source(run, relative_path):
    """Archive exact frozen text/JSON/YAML; it is never a synthetic data row."""
    run.writable()
    if run.verify_inputs(): raise ValueError('冻结输入失效，不能录为来源文档')
    value = _source_record(run,relative_path)
    eid = run.evidence({'source':value},kind='frozen-source-document',source={'run_id':run.manifest['run_id']})
    run.event('frozen_source.read',evidence_id=eid,source_sha256=value['source_sha256'],source_path=relative_path)
    return eid


def _verified_frozen_source(lookup, link, obligation):
    source = obligation.get('source_material', {})
    guard = (obligation['source_type'] == 'historical' and source.get('issue_type') in NON_OBJECT_GUARDS
             and obligation.get('requires_impact') is False)
    if not guard and obligation['source_type'] != 'history_source':
        raise ValueError('冻结文档不能替代具体商品、数量或缺口调查的原始成员证据')
    owner,eid,meta,value = _receipt_origin(None,lookup,link['evidence_id'])
    if meta.get('kind') != 'frozen-source-document' or link.get('pointer') != '/source':
        raise ValueError('非对象历史事项须引用实际读取的冻结原文')
    node = value.get('source',{}); relative = node.get('source_path','')
    if _source_record(owner,relative) != node: raise ValueError('历史事项所引原文/解析与真实冻结文件不同')
    if owner.verify_inputs() or owner.verify_events(): raise ValueError('历史文档来源运行的输入/事件不完整')
    events = [json.loads(s) for s in owner._event_snapshot().splitlines()]
    if not any(e['type'] == 'frozen_source.read' and e.get('evidence_id') == eid
               and e.get('source_path') == relative and e.get('source_sha256') == node['source_sha256'] for e in events):
        raise ValueError('缺少实际冻结文档读取事件')
    if any(link['object_keys'].get(k) != node[k] for k in ('source_path','source_sha256')):
        raise ValueError('原文定位须绑定真实文件路径和内容版本')
    if 'text' in link['fields'] and 'lines' not in link:
        raise ValueError('历史原文核对须定位实际行号，不能只引用整份报告')
    if obligation['source_type'] == 'history_source':
        if relative != source.get('source_path'): raise ValueError('历史来源清点不能替换被分派的原文件')
    elif relative.startswith('inputs/history/'):
        origin = owner.manifest.get('history_source_origins',{}).get(relative)
        matches = [ref for ref in source.get('source_refs',[]) if origin and
                   (ref.get('path') == origin or ref.get('path','').endswith('/'+origin))]
        if not matches or any(ref.get('sha256') != node['source_sha256'] for ref in matches):
            raise ValueError('历史原文并非该事项的确切原来源或版本')
    elif relative != 'run.json' and not relative.startswith(('inputs/project/', 'inputs/skill/')):
        raise ValueError('非对象回归文档须为实际项目配置、方法输入或该事项历史来源')
    return True


def capture_frozen_input_absence(run, relative_path, scope, required_record, provider_role, required_fields):
    """Record an actual local inventory inspection for a specific missing deliverable.

    This proves only absence from frozen inputs. The main reviewer still judges
    whether the named external material is necessary and its owner is correct.
    """
    run.writable()
    if provider_role not in set().union(*_EXTERNAL_OWNERS.values()): raise ValueError('缺失材料须指定外部材料负责角色')
    text(required_record, '缺失外部材料'); unique(required_fields, '恢复需交付的具体字段')
    target = (run.path / relative_path).resolve()
    if not target.is_relative_to(run.path/'inputs') or not relative_path.startswith(('inputs/internal/', 'inputs/project/')):
        raise ValueError('只可核对冻结内部/项目材料，不可把未运行代码或SQL列作缺失外部材料')
    if target.suffix.lower() not in ('.json', '.csv', '.tsv', '.md', '.txt', '.xlsx', '.xls', '.parquet', '.pdf', '.docx'):
        raise ValueError('须指定具体外部材料文件，而非未执行动作')
    if relative_path in run.manifest['inputs'] or target.exists(): raise ValueError('所称缺失材料实际存在，须读取而不能记外部阻塞')
    if run.verify_inputs(): raise ValueError('冻结输入失效，不能作为实际清点凭证')
    record = {'operation': 'frozen_input_inspection', 'outcome': 'absent_from_frozen_inputs',
              'relative_path': relative_path, 'required_record': required_record, 'provider_role': provider_role,
              'required_fields': required_fields, 'scope': scope, 'inputs_sha256': digest(run.manifest['inputs'])}
    eid = run.evidence({'inspection': record}, kind='frozen-input-inspection',
                       source={'run_id': run.manifest['run_id'], 'inputs_sha256': record['inputs_sha256']})
    run.event('external_input.inspected', evidence_id=eid, receipt_sha256=digest(record))
    return eid


def validate_blocking_attempt(run, lookup, obligation, blocking):
    """Require an executed source operation; elapsed budget is a workflow state."""
    if blocking.get('provider_role') not in _EXTERNAL_OWNERS.get(blocking.get('type'), set()):
        raise ValueError('外部阻塞须指定材料负责角色；本地Agent/SQL容量不是外部材料提供方')
    condition = blocking.get('resume_condition')
    if not isinstance(condition, dict) or condition.get('trigger') != 'external_material_available':
        raise ValueError('外部阻塞恢复条件须是具体外部材料可用，不能是运行时间或容量恢复')
    for key in ('provider_role', 'required_record'):
        if condition.get(key) != blocking[key]: raise ValueError('恢复材料/负责角色与实际缺证不一致')
    required_fields = unique(condition.get('required_fields'), '恢复所需具体字段')
    if condition.get('scope') != obligation['scope']: raise ValueError('恢复材料须覆盖当前完整结论范围')
    ids = set()
    for link in unique(blocking.get('attempt_receipts'), '实际取证尝试凭证'):
        attempt = link.get('attempt')
        if not isinstance(attempt, dict): raise ValueError('普通商品记录不是实际取证尝试凭证，须指定已执行操作')
        owner, eid, meta, value = _receipt_origin(run, lookup, link['evidence_id'])
        node = locator(lookup, link)
        if owner.verify_events(): raise ValueError('实际取证尝试事件链无效')
        if attempt.get('method') == 'source_field_absence':
            # Reused source pages retain the source's original call events.
            request, response = value.get('request', {}), value.get('response', {})
            events = [json.loads(s) for s in owner._event_snapshot().splitlines()]
            start = next((e for e in events if e['event_id'] == meta.get('parent_event_id') and e['type'] == 'tool.started'), None)
            finished = next((e for e in events if e.get('parent_event_id') == meta.get('parent_event_id')
                             and e['type'] == 'tool.finished' and e.get('status') == 'success' and e.get('evidence_id') == eid), None)
            if (meta.get('kind') != 'query-result' or meta.get('source') != 'configured:doris' or not start or not finished
                    or start.get('tool') != 'doris.exec_query' or response.get('success') is not True
                    or not re.fullmatch(r'/response/data/\d+', link.get('pointer', ''))):
                raise ValueError('字段缺失须引用真实成功源查询的原始行及实际调用完成凭证')
            sql = request.get('sql')
            if (not isinstance(sql, str) or request.get('query_hash') != digest(sql)
                    or start.get('query_hash') != request['query_hash'] or attempt.get('request_sha256') != digest(request)
                    or str(response.get('metadata', {}).get('query', '')).strip().rstrip(';') != sql.strip().rstrip(';')):
                raise ValueError('缺失字段凭证的实际源SQL/请求哈希不一致')
            query_path = owner.path/'queries'/(request['query_hash']+'.sql')
            if not query_path.exists() or query_path.read_text().strip() != sql.strip():
                raise ValueError('缺少真实源查询SQL归档')
            missing = unique(attempt.get('missing_fields'), '源查询实际缺失字段')
            if set(missing) != set(required_fields) or blocking['type'] not in ('missing_external_record', 'missing_definition'):
                raise ValueError('恢复所需材料与实际返回的缺失字段不一致')
            from acceptance_diagnostics import traceable_sku
            for field in missing:
                if field not in link['fields'] or field not in node or not (node[field] is None or isinstance(node[field], str) and not node[field].strip()
                        or field == 'sku_id' and not traceable_sku(node[field])):
                    raise ValueError('所称缺失字段实际有值，普通商品行不能冒充缺证尝试')
            locator(lookup, link, obligation)
        elif attempt.get('method') == 'frozen_input_absence':
            if meta.get('kind') != 'frozen-input-inspection' or link.get('pointer') != '/inspection':
                raise ValueError('缺失输入须有程序生成的实际冻结输入清点凭证')
            rel = node.get('relative_path', '')
            target = (owner.path/rel).resolve()
            events = [json.loads(s) for s in owner._event_snapshot().splitlines()]
            if (not target.is_relative_to(owner.path/'inputs') or not rel.startswith(('inputs/internal/', 'inputs/project/'))
                    or target.suffix.lower() not in ('.json', '.csv', '.tsv', '.md', '.txt', '.xlsx', '.xls', '.parquet', '.pdf', '.docx')
                    or rel in owner.manifest['inputs'] or target.exists() or owner.verify_inputs()
                    or node.get('inputs_sha256') != digest(owner.manifest['inputs'])
                    or node.get('operation') != 'frozen_input_inspection' or node.get('outcome') != 'absent_from_frozen_inputs'
                    or node.get('scope') != obligation['scope'] or node.get('required_record') != blocking['required_record']
                    or node.get('provider_role') != blocking['provider_role'] or node.get('required_fields') != required_fields
                    or not any(e['type'] == 'external_input.inspected' and e.get('evidence_id') == eid
                               and e.get('receipt_sha256') == digest(node) for e in events)):
                raise ValueError('实际冻结输入清点与缺失外部材料/恢复条件不一致')
        else:
            raise ValueError('未经支持的取证尝试类型，普通数据/失败超时/预算耗尽不能充当外部缺证凭证')
        ids.add(link['evidence_id'])
    return ids


def validate_response(run, obligation, answer, lookup, blocked):
    if not enabled(run): return
    frozen = obligation.get('contract')
    if not frozen or answer.get('contract_sha256') != frozen['sha256']:
        raise ValueError('答卷未绑定当前完整调查合同，不能自行缩减范围')
    validate_dimensions(answer, blocked)
    mode, proven = answer.get('fulfillment_mode', 'direct'), set()
    if mode == 'machine_rule':
        from acceptance_cases import verify_rule_fulfillment
        proven = set(verify_rule_fulfillment(run, obligation, answer.get('rule_fulfillment', {}))['checks_proven'])
    elif mode != 'direct': raise ValueError('义务处置方式未经批准')
    for name, check in answer['checks'].items():
        if name not in proven:
            records = evidence_links(lookup, check.get('evidence_locators'), obligation, name)
            if not {v['evidence_id'] for v in check['evidence_locators']} <= set(check['evidence_ids']):
                raise ValueError('原始定位未进入该问题的证据清单')
            if obligation['source_type'] in ('historical', 'sample') and name in ('historical_object_match', 'sku_identity'):
                pids = obligation['scope'].get('product_ids')
                required_months = frozen['history_window']['required_comparison_months']
                if pids and required_months:
                    expected = {(pid, mm) for pid in pids for mm in required_months}
                    actual = {(row.get('product_id', row.get('seed_product_id', row.get('std_product_id'))),
                               str(row.get('month', row.get('month_dt', '')))[:7]) for row in records}
                    if not expected <= actual:
                        raise ValueError('历史/样本具体对象×全部月份仍有未回应证据，不能以单个样本替代完整义务')
        if check['status'] == 'blocked_external':
            b = check['blocking']
            if not validate_blocking_attempt(run, lookup, obligation, b) <= set(check['evidence_ids']):
                raise ValueError('外部缺证的实际尝试凭证未归档')
    for item in answer.get('critical_conflicts', []):
        text(item.get('conflict_id'), '反证冲突ID'); text(item.get('resolution'), '反证处理/未决影响')
        evidence_links(lookup, item.get('evidence_locators'), obligation)
        if item.get('status') != 'resolved' and answer['business_verdict'] == 'pass':
            raise ValueError('关键反证冲突未解决不能无保留放行')
    if not blocked and any(i.get('unexplained', 0) > 0 for i in answer.get('impact', {}).values()):
        boundary = answer.get('residual_boundary')
        if not isinstance(boundary, dict) or boundary.get('state') != 'bounded':
            raise ValueError('低影响未核须有实际风险上界，观察变化不能冒充上界')
        for key in ('derivation', 'recheck_condition', 'acceptance_effect'): text(boundary.get(key), '残余边界.'+key)
        evidence_links(lookup, boundary.get('evidence_locators'), obligation)
        if boundary.get('bound_scope') != 'recorded_population_only':
            raise ValueError('残余上界须限定可核算的完整已记录总体，不能冒称真实市场误差上界')
        upper = boundary.get('upper_bounds')
        calculations = boundary.get('recomputations')
        if not isinstance(upper, dict) or set(upper) != set(answer['impact']): raise ValueError('残余须有四指标可复算上界')
        unique(calculations, '残余上界原始证据复算')
        if set(c.get('metric') for c in calculations) != set(upper) or len(calculations) != len(upper):
            raise ValueError('残余上界须逐指标独立复算，不能只报一个观察比例')
        for calculation in calculations:
            metric = calculation['metric']; actual = recompute(lookup, calculation, obligation)
            bound = _finite(upper[metric]); impact = answer['impact'][metric]
            if bound < impact['unexplained'] or bound < 0 or abs(actual-bound) > max(1e-9, abs(bound)*1e-9):
                raise ValueError('残余上界与原始证据复算/完整未解释暴露不符')
            policy = run.policy['investigation_workflow']
            limit = policy['max_unexplained_band_pp'] if metric == 'bands' else impact['exposure']*policy['max_unexplained_ratio']
            if bound > limit + 1e-12:
                raise ValueError('残余风险上界超过冻结容差；观察变化较小不能获得豁免')
        if answer['business_verdict'] == 'pass': raise ValueError('保留未解释残余时不得无保留放行')


def cause_credit(manifest, candidate, assessments, lookup, metrics=('spus', 'bands', 'units', 'amount'), *, population_records=None):
    """One exact object/metric cell can be explained by only one supported cause.

    No worker-authored fractional credit or causal amount is trusted. Merely
    verifying an object's existence/defect never removes its unexplained effect.
    """
    credit = {m: set() for m in metrics}
    if not enabled(manifest):
        for group in assessments:
            if group['status'] == 'verified':
                for metric in metrics: credit[metric].update(group['product_ids'])
        return credit
    ids_seen = set()
    def record_hash(row):
        return digest({k:v for k,v in row.items() if k not in ('total_rows', 'matches_seed_product', 'matches_seed_sku', 'is_seed_record')})
    population_records = population_records if isinstance(population_records, dict) else {}
    for group in assessments:
        for claim in unique(group.get('cause_claims', []), '原因判断清单（未解释可为空）', empty=True):
            cid = text(claim.get('claim_id'), '原因判断ID')
            if cid in ids_seen: raise ValueError('多个原因重复解释同一贡献或重复原因ID')
            ids_seen.add(cid)
            pids = unique(claim.get('product_ids'), '原因关联具体对象')
            if not set(pids) <= set(group['product_ids']): raise ValueError('局部缺陷被无依据推广到组外对象')
            selected = unique(claim.get('metrics'), '原因解释的指标')
            if not set(selected) <= set(metrics): raise ValueError('无效原因指标')
            text(claim.get('mechanism'), '对象机制与变动关系')
            text(claim.get('alternative_explanations'), '原因的竞争解释')
            if claim.get('status') not in ('supported', 'undetermined', 'refuted'): raise ValueError('原因状态必须独立于缺陷状态')
            expected = {record_hash(row) for pid in pids for row in population_records.get(pid, [])}
            if claim['status'] == 'supported' and (not expected or any(not population_records.get(pid) for pid in pids)):
                raise ValueError('原因信用缺少程序重建的完整比较/平台/SKU原始成员，不能仅凭同父体附件赋予信用')
            seen = set(); covered_records = set()
            for link in unique(claim.get('evidence_locators'), '原因的原始证据定位'):
                node = locator(lookup, link)
                keys = link['object_keys']
                if keys.get('site') != candidate['site']:
                    raise ValueError('原因证据国家与当前对象无关')
                mm = str(keys.get('month', keys.get('month_dt', '')))[:7]
                if not manifest['starts'][candidate['site']] <= mm <= manifest['data_month']:
                    raise ValueError('原因证据月份不在冻结调查窗口')
                if link['evidence_id'] not in group['evidence_ids']: raise ValueError('原因证据未纳入完整实体判断证据或与对象无关')
                signature = record_hash(node)
                if signature in expected: covered_records.add(signature)
                elif link.get('role', 'support') not in ('counterevidence', 'historical_context', 'external_context'):
                    raise ValueError('原因原始证据与实际完整比较/平台/路径/SKU记录无关，或与声称对象无关')
                # A traced destination may have a new parent. Its relationship
                # must come from the program's platform+SKU population mapping.
                seen.update(pid for pid in pids if signature in {record_hash(r) for r in population_records.get(pid, [])})
            if seen != set(pids): raise ValueError('原因证据没有覆盖声称解释的完整具体对象')
            if claim['status'] == 'supported':
                if covered_records != expected:
                    raise ValueError('原因证据未覆盖完整比较窗口每个平台和SKU成员；局部对象不能解释全部贡献')
                if group['status'] != 'verified': raise ValueError('外部受阻对象不能获得已解释贡献')
                if claim.get('causal_basis') not in ('direct_mechanism', 'controlled_comparison', 'deterministic_rule'):
                    raise ValueError('相关性、算术守恒或身份相同不能自行证明原因')
                for metric in selected:
                    if credit[metric] & set(pids): raise ValueError('多个原因重复解释同一部分贡献')
                    credit[metric].update(pids)
    return credit


def continuation_requirements(manifest, candidate, computed, credits, population):
    """Machine-generated follow-ups for remaining material metric populations."""
    policy = manifest['policy']['investigation_workflow']
    result = []
    for metric, item in computed.items():
        limit = policy['max_unexplained_band_pp'] if metric == 'bands' else item['exposure']*policy['max_unexplained_ratio']
        if item['unexplained'] <= limit + 1e-12: continue
        ids = sorted(set(population)-credits[metric])
        payload = {'candidate_id': candidate['candidate_id'], 'metric': metric, 'product_ids': ids,
                   'site': candidate['site'], 'path': candidate['path'], 'month': candidate['month'],
                   'comparison': candidate.get('comparison'), 'unexplained': item['unexplained'],
                   'exposure': item['exposure'], 'frozen_tolerance': limit,
                   'required_action': '调查剩余具体对象，处理反证；不得以局部缺陷替代整体异常原因'}
        result.append({'followup_id': 'residual-'+digest(payload)[:24], **payload})
    return result


def verify_computed_claims(manifest, answer, proof):
    """Validate claimed values against an already rebuilt full source proof."""
    from acceptance_investigation_proof import _close, _number
    impacts = answer.get('impact')
    if not isinstance(impacts, dict) or set(impacts) != set(proof['computed_impact']):
        raise ValueError('答卷缺少四项机算影响')
    for metric, actual in proof['computed_impact'].items():
        claim = impacts[metric]
        if claim.get('state') != actual['state']: raise ValueError('自报影响状态与完整源记录不符')
        if actual['state'] == 'measured':
            for field in ('exposure', 'unexplained'):
                _close(_number(claim.get(field), metric+'.'+field, True), actual[field], metric, manifest, '答卷.'+metric+'.'+field)
    required = {c['conflict_id'] for c in proof.get('machine_conflicts', [])}
    if not required <= {r.get('conflict_id') for r in answer.get('machine_conflict_responses', [])}:
        raise ValueError('完整机器诊断冲突未逐项实质回应，不能忽略后提交完成')


def case_binding(run, obligation_id):
    from pathlib import Path
    path = run.path / 'investigations/cases/state.json'
    if not path.exists(): return None
    import acceptance_cases
    state = acceptance_cases.load(run)
    links = [r for r in state['obligation_case_map'] if r['obligation_id'] == obligation_id]
    if len(links) != 1: raise ValueError('调查义务没有唯一当前案件映射')
    case = next(c for c in state['cases'] if c['case_id'] == links[0]['case_id'])
    return {'case_id': case['case_id'], 'case_version': case['case_version']}


def _evidence_reference_kind(key):
    if key=='evidence_id' or key.endswith('_evidence_id'): return 'one'
    if key=='evidence_ids' or key.endswith('_evidence_ids'): return 'many'
    return None


def rewrite_evidence_references(value, resolve):
    """Reference-named fields have one contract, including auxiliary fields."""
    if isinstance(value, dict):
        result={}
        for key,item in value.items():
            kind=_evidence_reference_kind(key)
            if kind:
                ids=[item] if kind=='one' else item
                if (not isinstance(ids,list) or any(not isinstance(eid,str)
                        or not re.fullmatch(r'(?:main:)?ev-[0-9a-f]{32}',eid) for eid in ids)
                        or len(ids)!=len(set(ids))):
                    raise ValueError('证据引用字段必须使用有效且不重复的ID：'+key)
                rewritten=[resolve(eid) for eid in ids]
                result[key]=rewritten[0] if kind=='one' else rewritten
            else:
                result[key]=rewrite_evidence_references(item,resolve)
        return result
    if isinstance(value,list): return [rewrite_evidence_references(item,resolve) for item in value]
    return value


def referenced_evidence_ids(value):
    result=set()
    def collect(eid):
        result.add(eid)
        return eid
    rewrite_evidence_references(value,collect)
    return result


def evidence_ids(answer, proof=None):
    result = {eid for c in answer['checks'].values() for eid in c['evidence_ids']}
    result.update(eid for c in answer.get('impact', {}).values() for eid in c['evidence_ids'])
    if proof: result.update(proof['evidence_ids'])
    for key in ('population_proof', 'gap_proof'):
        for group in answer.get(key, {}).get('entity_assessments', []): result.update(group['evidence_ids'])
    result.update(referenced_evidence_ids(answer))
    return result


def material_digest(submission_sha256, answer, proof, lookup):
    return digest({'submission_sha256': submission_sha256,
                   'evidence_sha256': {eid: digest(lookup(eid)) for eid in sorted(evidence_ids(answer, proof))},
                   'proof_sha256': digest(proof) if proof else None})


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value): raise ValueError('复算需真实有限数值')
    return value


def recompute(lookup, entry, obligation):
    """Deterministic arithmetic language; review records cannot execute code."""
    values = []
    for operand in unique(entry.get('operands'), '关键数值复算的原始操作数'):
        node = locator(lookup, operand['locator'], obligation)
        field = operand.get('field')
        if field not in operand['locator']['fields']: raise ValueError('复算字段未实际核对')
        value = node[field]
        if 'value_pointer' in operand: value = pointer(value, operand['value_pointer'])
        values.append(value)
    method = entry.get('method')
    if method == 'distinct_count': actual = len({canonical(v) for v in values})
    elif method == 'set_intersection_count':
        if len(values) < 2 or any(not isinstance(v,(dict,list)) for v in values):
            raise ValueError('集合交集复算须至少两份实际配置集合/映射键')
        actual = len(set.intersection(*({canonical(x) for x in v} for v in values)))
    else:
        values = [_finite(v) for v in values]
        if method == 'sum': actual = sum(values)
        elif method == 'sum_abs': actual = sum(abs(v) for v in values)
        elif method == 'max': actual = max(values)
        elif method == 'difference' and len(values) == 2: actual = values[1]-values[0]
        elif method == 'ratio' and len(values) == 2 and values[1] != 0: actual = values[0]/values[1]
        else: raise ValueError('不支持/不可定义的原始证据复算方法')
    if abs(actual-_finite(entry.get('result'))) > max(1e-9, abs(actual)*1e-9): raise ValueError('关键数值复算结果与原始证据不一致')
    text(entry.get('meaning'), '复算数值与当前判断的关系')
    return actual


def validate_business_review(run, task, obligation, answer, proof, entry, submission_sha256, *, recomputed_proof=None):
    if not enabled(run): return
    lookup = run.get_evidence
    if any(c.get('status') != 'resolved' for c in answer.get('critical_conflicts', [])):
        raise ValueError('关键证据冲突未解决，须列具体补查要求退回')
    if proof and proof.get('unresolved_machine_conflict_ids'):
        raise ValueError('完整机器诊断仍有未解决关键冲突，不能隐藏在答卷摘要后批准')
    boundary = answer.get('residual_boundary')
    if boundary and any(i.get('unexplained', 0) > 0 for i in answer.get('impact', {}).values()):
        if proof and (proof.get('ambiguous_cross_platform_product_ids') or proof.get('untraceable_product_ids')):
            raise ValueError('平台/SKU身份风险尚不明确，不得套用低影响上界豁免')
        remaining = proof.get('metric_residual_product_ids') if proof else None
        if remaining is None:
            ids = obligation['scope'].get('product_ids')
            if not ids: raise ValueError('缺少程序确定的完整残余对象，不能用自报低影响上界结案')
            remaining = {m: ids for m in answer['impact']}
        for calculation in boundary['recomputations']:
            observed = set()
            for operand in calculation['operands']:
                row = locator(lookup, operand['locator'], obligation)
                observed.update(row[k] for k in ('product_id', 'seed_product_id', 'std_product_id') if k in row)
            if not set(remaining[calculation['metric']]) <= observed:
                raise ValueError('残余上界复算未覆盖全部机器确定的未解释对象')
    current_case = case_binding(run, obligation['obligation_id'])
    if current_case and any(entry.get(k) != v for k, v in current_case.items()):
        raise ValueError('案件合并/拆分/反例重开后旧主审失效，须审当前成员版本')
    if entry.get('conclusion_scope') != obligation['scope']: raise ValueError('主审结论范围不等于当前调查合同')
    if entry.get('material_sha256') != material_digest(submission_sha256, answer, proof, lookup):
        raise ValueError('主审未绑定当前调查材料和全部关键证据版本')
    evidence_links(lookup, entry.get('raw_evidence_checks'), obligation)
    covered = {n for v in entry['raw_evidence_checks'] for n in v.get('check_ids', [])}
    if not set(obligation['required_checks']) <= covered: raise ValueError('主审只读摘要或没有核对每项判断的关键原始证据')
    for alt in unique(entry.get('alternative_explanations'), '主要替代解释及处理'):
        text(alt.get('hypothesis'), '替代解释'); text(alt.get('resolution'), '替代解释处理依据')
        if alt.get('status') not in ('rejected_with_evidence', 'supported', 'unresolved'): raise ValueError('替代解释未给出处理状态')
        evidence_links(lookup, alt.get('evidence_locators'), obligation)
        if alt['status'] == 'unresolved' and answer['business_verdict'] == 'pass': raise ValueError('未决主要替代解释不能无保留放行')
    uncertainty = entry.get('remaining_uncertainty')
    if not isinstance(uncertainty, dict): raise ValueError('主审缺少剩余不确定性及验收影响')
    text(uncertainty.get('description'), '剩余不确定性'); text(uncertainty.get('acceptance_effect'), '不确定性对验收的影响')
    calculations = entry.get('recomputations', [])
    if obligation['requires_impact']:
        if proof:
            numerical = entry.get('proof_recalculation', {})
            if recomputed_proof is None: raise ValueError('主审尚未从冻结原始完整证据重新计算')
            expected = {'anchors': recomputed_proof['anchors'], 'computed_impact': recomputed_proof['computed_impact']}
            def without_ids(obj):
                if isinstance(obj, dict): return {k: without_ids(v) for k, v in obj.items() if k != 'evidence_ids'}
                return [without_ids(v) for v in obj] if isinstance(obj, list) else obj
            if without_ids(numerical.get('result')) != without_ids(expected): raise ValueError('主审的完整人口/残余复算记录不符')
            text(numerical.get('assessment'), '完整分母复算的业务判断')
        if not calculations: raise ValueError('主Agent尚未记录实际关键原始数值复算')
        required = {'spus', 'bands', 'units', 'amount'} if not proof else {'units', 'amount'}
        if not required <= {c.get('metric') for c in calculations}: raise ValueError('主审关键指标复算不完整')
    for calculation in calculations:
        actual = recompute(lookup, calculation, obligation)
        target = text(calculation.get('target'), '关键复算绑定的当前结论字段')
        expected_value = pointer({'answer': answer, 'proof': proof}, target)
        if abs(actual-_finite(expected_value)) > max(1e-9, abs(actual)*1e-9):
            raise ValueError('复算虽算术正确但与当前结论的关键数值不一致')
        if not proof and target != '/answer/impact/'+str(calculation.get('metric'))+'/exposure':
            raise ValueError('非人口证明的主审复算必须关联四项完整影响分母')
    required = (obligation.get('priority') == 'high' or obligation['source_type'] == 'historical'
                or bool(answer.get('critical_conflicts')) or answer.get('historically_missed') is True
                or bool(proof and proof.get('machine_conflicts'))
                or answer.get('fulfillment_mode') == 'machine_rule')
    if required:
        independent = entry.get('independent_review')
        if not isinstance(independent, dict): raise ValueError('重大、历史/冲突或规则事项缺少必要独立复核')
        who = text(independent.get('reviewer_id'), '独立复核Agent身份')
        if who in (task['agent_id'], task['coordinator_id']): raise ValueError('独立复核者不得是原调查者或当前主审角色')
        if independent.get('material_sha256') != entry['material_sha256']: raise ValueError('独立复核未绑定当前材料与证据版本')
        if independent.get('decision') != 'accept': raise ValueError('独立复核退回或未完成，主审不能批准')
        text(independent.get('reason'), '独立复核具体结论依据')
        evidence_links(lookup, independent.get('raw_evidence_checks'), obligation)
        method = independent.get('method')
        if method == 'independent_source':
            fresh = {v['evidence_id'] for v in independent['raw_evidence_checks']} - evidence_ids(answer, proof)
            if not fresh:
                raise ValueError('独立来源复核仍只引用原调查材料')
            prior = [lookup(eid) for eid in evidence_ids(answer, proof)]
            prior_hashes = {digest(value) for value in prior}
            prior_queries = {value.get('request', {}).get('query_hash') for value in prior if isinstance(value, dict)} - {None}
            def different_source(eid):
                value = lookup(eid)
                query_hash = value.get('request', {}).get('query_hash') if isinstance(value, dict) else None
                return digest(value) not in prior_hashes and query_hash not in prior_queries
            if not any(different_source(eid) for eid in fresh):
                raise ValueError('同一原始证据/查询复制成新ID不构成独立来源')
            text(independent.get('source_independence'), '来源与原调查假设的独立性')
        elif method in ('alternative_method', 'counterexample_recalculation'):
            text(independent.get('method_difference'), '独立方法/反例区别')
            independent_values = [recompute(lookup, item, obligation) for item in unique(independent.get('recomputations'), '独立反例或替代方法复算')]
            if method == 'alternative_method':
                def recipe(item):
                    return digest({'method': item['method'], 'operands': [{k: operand['locator'][k] for k in ('evidence_id', 'pointer')} | {'field':operand['field'], 'value_pointer':operand.get('value_pointer')} for operand in item['operands']]})
                if not {recipe(c) for c in independent['recomputations']} - {recipe(c) for c in calculations}:
                    raise ValueError('替代方法与主审使用相同计算配方，不能仅改方法说明')
            if method == 'counterexample_recalculation':
                for case in unique(independent.get('counterexamples'), '实际反例及检验结果'):
                    text(case.get('hypothesis'), '反例假设'); text(case.get('result'), '反例实际结果')
                    evidence_links(lookup, case.get('evidence_locators'), obligation)
                    test = case.get('test', {})
                    index = test.get('recomputation_index')
                    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(independent_values):
                        raise ValueError('反例须绑定实际复算结果，不能只写已检查')
                    expected, actual = _finite(test.get('expected')), independent_values[index]
                    operations = {'eq': actual == expected, 'ne': actual != expected, 'lt': actual < expected,
                                  'le': actual <= expected, 'gt': actual > expected, 'ge': actual >= expected}
                    if test.get('relation') not in operations or not isinstance(test.get('outcome'), bool) or test['outcome'] != operations[test['relation']]:
                        raise ValueError('反例检验结论与实际重算不一致')
        else: raise ValueError('独立复核不能只阅读同一摘要表示同意')
