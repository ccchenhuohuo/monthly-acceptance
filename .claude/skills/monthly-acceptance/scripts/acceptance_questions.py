"""Question-scope adapter for the existing case/task/submission/review ledgers.

There is one authoritative case registry. Panorama cells and query shards are
not investigation debts. Legacy candidate-led runs keep their frozen protocol.
"""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import json
import re

from acceptance_core import Run, canonical, digest, now, read, write, months, month, quoted, event_snapshot
import acceptance_panorama as panorama
import acceptance_review as review
from acceptance_protocol import protocol
from acceptance_scope import constraint_coverage, row_scope, evidence_within

enabled=panorama.enabled
FAMILIES=panorama.FAMILIES
CHECKS={'scope','counterevidence','impact',*FAMILIES}


def _text(v,label): return review.text(v,label)
def _list(v,label,empty=False): return review.unique(v,label,empty=empty)
def _events(run,kind):
    return [e for line in run._event_snapshot().splitlines() if (e:=json.loads(line))['type']==kind]


def prepare(run,coordinator):
    import acceptance_cases as cases
    _text(coordinator,'coordinator');run.writable()
    p=run.path/'investigations/plan.json'
    if p.exists():
        value=plan_data(run)
        if value['coordinator_id']!=coordinator:raise ValueError('主 Agent 已冻结')
        return read(p)
    panorama.load(run)
    payload={'protocol':protocol(run,4),'coordinator_id':coordinator,'policy':run.policy['investigation_workflow'],
             'obligations':[], 'denominator':'active question scope units in existing case registry'}
    value={'payload':payload,'sha256':digest(payload),'created_at':now()}
    write(p,value);run.event('investigation.plan.frozen',plan_hash=value['sha256'],obligations=0,coordinator_id=coordinator)
    state={'protocol':protocol(run,'4.0.0'),'version':0,'coordinator_id':coordinator,'cases':[],'members':{},
        'obligation_case_map':[],'collection_packages':[],'rules':[],'rule_proofs':[],'changes':[],
        'source_binding':{'source_run':str(run.path),'source_files':{},'policy_sha256':run.manifest['policy_hash']}}
    cases._persist(run,state,'question_registry_created',coordinator)
    # Empty legacy containers only satisfy shared worker packaging. They never
    # determine the question denominator and never contain ordinary observations.
    for name in ('candidates','samples'):
        if not (run.path/(name+'.json')).exists():write(run.path/(name+'.json'),[])
    return value


def plan_data(run):
    value=read(run.path/'investigations/plan.json');payload=value['payload']
    if payload.get('protocol')!=protocol(run,4) or digest(payload)!=value['sha256'] or not any(e['plan_hash']==value['sha256'] for e in _events(run,'investigation.plan.frozen')):
        raise ValueError('问题调查计划版本/事件失效')
    if payload['policy']!=run.policy['investigation_workflow']:raise ValueError('调查资源政策改变')
    return payload


def _registry(run):
    import acceptance_cases as cases
    state=cases.load(run)
    if state['protocol']!=protocol(run,'4.0.0') or state['coordinator_id']!=plan_data(run)['coordinator_id']:
        raise ValueError('案件协议或主 Agent 不一致')
    return state


def _unit(run,u):
    u=deepcopy(u);site=u.get('site')
    if site not in run.manifest['sites']:raise ValueError('范围国家不在冻结集合')
    _text(u.get('scope_unit_id'),'scope_unit_id')
    for k in ('event_months','background_months'):
        _list(u.get(k),k,empty=k=='background_months')
        for mm in u[k]:
            month(mm)
            if not run.manifest['starts'][site]<=mm<=run.manifest['data_month']:raise ValueError('问题月份越出冻结数据窗')
        u[k]=sorted(u[k])
    u['months']=sorted(set(u['event_months']+u['background_months']))
    _list(u.get('platforms'),'platforms');_list(u.get('source_layers'),'source_layers')
    if not set(u['source_layers'])<= {'raw','std'}:raise ValueError('未知数据层')
    expected=run.policy.get('expected_platforms',{}).get(site,[])
    if not set(u['platforms'])<=set(expected):raise ValueError('平台选择未在冻结预期中；先补充项目契约并新建运行')
    paths=u.get('paths')
    if not isinstance(paths,list) or not paths or len({canonical(p) for p in paths})!=len(paths):raise ValueError('需完整子树路径集合；全站写 [[]]')
    if any(not isinstance(p,list) or len(p)>6 or any(x is not None and not isinstance(x,str) for x in p) for p in paths):raise ValueError('路径须为完整数组')
    u.setdefault('path_layer','std')
    if u['path_layer'] not in ('raw','std'):raise ValueError('路径层错误')
    if u['path_layer']=='std' and any(len(p)>3 for p in paths):raise ValueError('标准层级越界')
    if u['path_layer']=='raw' and set(u['source_layers'])!={'raw'}:raise ValueError('raw 路径不能直接套到 std；按问题另建身份承接查询')
    for k in ('next_check','stop_condition','scope_rationale'):_text(u.get(k),k)
    if u.get('stage') not in ('aggregate_first','deep_review'):raise ValueError('stage 必须区分聚合初查与深查')
    checks=_list(u.get('required_checks'),'required_checks')
    if not {'scope','counterevidence','impact'}<=set(checks) or not set(checks)<=CHECKS or not set(checks)&set(FAMILIES):raise ValueError('检查必须回应具体质量疑问并保留范围/反证/影响')
    u.setdefault('impact_metrics',['spus','units','amount']+(['bands'] if 'price_bands' in checks else []))
    if not set(u['impact_metrics'])<= {'spus','units','amount','bands'} or not u['impact_metrics']:raise ValueError('问题适用影响指标需冻结')
    comps=u.get('required_components')
    if not isinstance(comps,list):raise ValueError('required_components 必须显式列出，可以为空')
    _list([c.get('component_id') for c in comps],'component IDs',empty=True)
    for c in comps:
        _text(c.get('component_id'),'component_id');_text(c.get('question'),'反向分量问题')
    return u


def _owns_constraint(run,u,c):
    required,covered=constraint_coverage(run,c,[u])
    return required==covered


def register(run,actor,value,*,_index=None,_rows=None):
    """Add or revise one topic; scope removal requires explicit successors.

    Active tasks must first preserve progress and requeue through the existing
    engine. Any topic version change invalidates all its older scope reviews.
    """
    import acceptance_cases as cases
    import acceptance_investigation as inv
    if actor!=plan_data(run)['coordinator_id']:raise ValueError('仅主 Agent 可修改权威问题范围')
    index=_index or panorama.load(run);state=_registry(run);v=deepcopy(value)
    for k in ('business_question','next_check','stop_condition','reason'):_text(v.get(k),k)
    if v.get('report_placement') not in ('main','brief','appendix'):raise ValueError('需明确报告位置')
    if v.get('priority','normal') not in ('normal','high'):raise ValueError('priority 错误')
    units=[_unit(run,u) for u in v.get('scope_units',[])]
    _list([u['scope_unit_id'] for u in units],'scope_unit_id')
    origins=v.get('origin_refs',[]);_list(origins,'起因原始定位')
    for ref in origins:panorama.resolve(run,ref,index=index,rows_cache=_rows)
    constraint_ids=_list(v.get('constraint_ids',[]),'constraint_ids',empty=True)
    constraints={c['check_id']:c for c in index['constraints']}
    for cid in constraint_ids:
        c=constraints.get(cid)
        if not c or c['state']!='failed':raise ValueError('起因约束不是当前明确失败')
        _,covered=constraint_coverage(run,c,units)
        if not covered:raise ValueError('明确失败缺少同范围、同约束的核查')
    cid=v.get('case_id') or 'case-'+digest([run.manifest['run_id'],v['business_question'],units])[:24]
    old=next((c for c in state['cases'] if c['case_id']==cid),None)
    if old:
        if v.get('previous_case_version')!=old['case_version']:raise ValueError('更新未绑定当前议题版本')
        owners=[t for t in inv.task_records(run).values() if set(t['obligation_ids']) & set(old['obligation_ids']) and t['state'] in ('assigned','submitted')]
        if owners:raise ValueError('活跃调查先保存进度并重排，不可被修改范围静默撤销')
        old_units={u['scope_unit_id']:u for u in old['scope_units']};new_units={u['scope_unit_id']:u for u in units}
        for uid,u in old_units.items():
            successor=v.get('scope_successors',{}).get(uid,uid)
            if successor not in new_units:raise ValueError('原范围删除必须指向替代范围')
            n=new_units[successor]
            if n['site']!=u['site'] or n['path_layer']!=u['path_layer']:raise ValueError('原核查国家或路径层不得改变')
            if not all(any(oldpath[:len(newpath)]==newpath for newpath in n['paths']) for oldpath in u['paths']):raise ValueError('原路径范围不得静默缩减')
            if not set(u['impact_metrics'])<=set(n['impact_metrics']):raise ValueError('原影响指标不得删除')
            if not set(u['required_checks'])<=set(n['required_checks']):raise ValueError('原质量检查不得删除')
            if any(c not in n['required_components'] for c in u['required_components']):raise ValueError('反向分量内容不得替换或弱化')
            oldcomps={c['component_id'] for c in u['required_components']};newcomps={c['component_id'] for c in n['required_components']}
            if not oldcomps<=newcomps:raise ValueError('反向分量不得通过删改完成')
            if not set(u['months'])<=set(n['months']) or not set(u['source_layers'])<=set(n['source_layers']) or not set(u['platforms'])<=set(n['platforms']):
                raise ValueError('缩小时间/平台/层须先将排除范围登记为有证据结论的独立范围，不能删除责任')
        state['cases'].remove(old)
    case={**v,'case_id':cid,'case_version':old['case_version']+1 if old else 1,'scope_units':units,
          'panorama_binding_sha256':index['binding_sha256'],'route':'needs_investigation','kind':'question',
          'constraint_ids':constraint_ids,'scope_status':'active','registered_by':actor,'registered_at':now()}
    case['obligation_ids']=['question_scope:'+cid+':'+u['scope_unit_id'] for u in units]
    state['cases'].append(case)
    state['obligation_case_map']=[r for r in state['obligation_case_map'] if r['case_id']!=cid]+[{'obligation_id':oid,'case_id':cid} for oid in case['obligation_ids']]
    state['changes'].append({'case_id':cid,'previous_case_version':old['case_version'] if old else None,'case_version':case['case_version'],'reason':v['reason'],'actor':actor})
    cases._persist(run,state,'question_registered' if not old else 'question_revised',actor)
    return case


def obligations(run):
    state=_registry(run);rows={}
    for c in state['cases']:
        if c['scope_status']!='active':continue
        for u,oid in zip(c['scope_units'],c['obligation_ids']):
            scope={k:deepcopy(u[k]) for k in ('site','platforms','source_layers','paths','path_layer','event_months','background_months','months')}
            scope.update(month=u['event_months'][-1],path=canonical(u['paths'][0]) if len(u['paths'])==1 else None,level=None,comparison='event')
            contract={'protocol':protocol(run,4),'case_id':c['case_id'],'case_version':c['case_version'],'scope':scope,
                'business_question':c['business_question'],'required_checks':u['required_checks'],'required_components':u['required_components'],
                'stage':u['stage'],'impact_metrics':u['impact_metrics'],'next_check':u['next_check'],'stop_condition':u['stop_condition'],
                'origin_refs':c['origin_refs'],'constraint_ids':c['constraint_ids']}
            contract['sha256']=digest(contract)
            rows[oid]={'obligation_id':oid,'source_type':'question_scope','source_id':u['scope_unit_id'],'source_hash':digest([c,u]),
                'scope':scope,'priority':c.get('priority','normal'),'required_checks':u['required_checks'],
                'requires_impact':False,'source_evidence_ids':[], 'source_material':{'question':c['business_question'],'case':c,'scope_unit':u},'contract':contract}
    return rows


def record_reading(run,actor,value):
    index=panorama.load(run);v=deepcopy(value);_text(actor,'实际判读 Agent')
    if v.get('binding_sha256')!=index['binding_sha256']:raise ValueError('全景判读未绑定当前版本')
    byid={p['partition_id']:p for p in index['partitions']}
    ids=_list(v.get('partition_ids'),'实际阅读分区')
    if not set(ids)<=set(byid):raise ValueError('未知阅读分区')
    for key in ('judgment','read_method','unread_scope','independence_disclosure'):_text(v.get(key),key)
    if v.get('status') not in ('complete','partial'):raise ValueError('阅读状态应为 complete/partial')
    if v['status']=='complete' and v['unread_scope']!='none':raise ValueError('未读范围未清空不能记全读')
    refs=_list(v.get('source_refs'),'关键原始定位')
    for ref in refs:panorama.resolve(run,ref)
    files=_list(v.get('reader_artifacts'),'判读原始产物')
    archive=run.path/'panorama/readings/artifacts'
    for f in files:
        p=Path(f['path'])
        source=p if p.is_absolute() else run.path/p
        if not source.is_file():raise ValueError('判读原始记录缺失或变更')
        data=source.read_bytes()
        if digest(data)!=f['sha256']:raise ValueError('判读原始记录缺失或变更')
        # Archive the bytes inside the run: a reader's own file can be moved or
        # edited later, and a sealed run must stay verifiable without it.
        stored=archive/(f['sha256']+source.suffix)
        if not stored.exists():
            archive.mkdir(parents=True,exist_ok=True)
            tmp=stored.with_name(stored.name+'.tmp');tmp.write_bytes(data);tmp.replace(stored)
        f.update(path=str(stored.relative_to(run.path)),source_path=str(source),original_name=source.name)
    payload={**v,'reader_artifacts':files,'reader_id':actor,'recorded_at':now()};rid=digest(payload)
    write(run.path/'panorama/readings'/(rid+'.json'),payload)
    run.event('panorama.reading.saved',reading_id=rid,sha256=digest(payload),reader_id=actor,partition_ids=ids)
    return payload


def reading_status(run,index=None):
    index=index or panorama.load(run);current={};errors=[]
    for e in _events(run,'panorama.reading.saved'):
        v=read(run.path/'panorama/readings'/(e['reading_id']+'.json'))
        if digest(v)!=e['sha256']:raise ValueError('阅读记录变更')
        for f in _list(v.get('reader_artifacts'),'判读原始产物'):
            p=Path(f['path'])
            # Runs frozen before artifacts were archived keep their absolute paths.
            p=p if p.is_absolute() else run.path/p
            if not p.is_file() or digest(p.read_bytes())!=f['sha256']:raise ValueError('判读材料审后变更')
        if v['binding_sha256']==index['binding_sha256']:
            for pid in v['partition_ids']:current[pid]=v
    return {'partitions':len(index['partitions']),'complete':sum(current.get(p['partition_id'],{}).get('status')=='complete' for p in index['partitions']),
        'unread_partition_ids':[p['partition_id'] for p in index['partitions'] if current.get(p['partition_id'],{}).get('status')!='complete'],
        'readers':sorted({v['reader_id'] for v in current.values()}),'errors':errors}


def _proof_jobs(run,answer,lookup):
    ids,receipts,checked_jobs=_verified_query_proofs(run,answer.get('proof_jobs'),lookup,answer['scope']['site'])
    _verify_scope_coverage(run,answer,checked_jobs)
    return ids,receipts


def _verified_query_proofs(run,proofs,lookup,site,*,native_only=False):
    """Check full typed query receipts, including inherited/imported evidence."""
    from acceptance_validate import verify_job
    ids={};receipts=[];checked_jobs=[];groups={}
    for item in _list(proofs,'完整查询证明'):
        eid=item['evidence_id'];owner,original,meta,value=review._receipt_origin(run,lookup,eid)
        key=(owner.path,item['job_id'])
        groups.setdefault(key,[]).append((item,owner,original))
    for (_,jid),members in groups.items():
        owner=members[0][1];record=owner.job_record(jid)
        if not record:raise ValueError('完整查询原始任务缺失')
        if native_only and not record.get('attempt_id'):raise ValueError('历史回归通过须绑定原生执行记录，附件自报 SQL 不算执行')
        individual=owner.path/'records'/(jid+'-plan.json')
        if record.get('attempt_id'):
            # Native executions pin their plan by hash, independently of a
            # legacy flat plan or the initial baseline plan. Never fall back
            # when this immutable attempt's plan is missing or tampered with.
            from acceptance_job_versions import _path,verify_execution
            job=read(_path(owner,jid,record['plan_sha256'],'plan'))
            verify_execution(owner,record,job)
        elif individual.exists():job=read(individual)
        else:
            # Early frozen runs stored baseline jobs only in the signed full
            # plan. Resolve the original job without writing into the archive.
            full=read(owner.path/'plan.json');plans=full.get('jobs',[])
            if full.get('hash')!=digest(plans) or full.get('policy_hash')!=owner.manifest['policy_hash']:
                raise ValueError('原始完整查询计划哈希失效')
            if not any(e.get('plan_hash')==full['hash'] for e in _events(owner,'plan.frozen')):
                raise ValueError('原始完整查询计划缺冻结事件')
            matches=[j for j in plans if j['job_id']==jid]
            if len(matches)!=1:raise ValueError('原始完整查询计划未唯一定位任务')
            job=matches[0]
        if job.get('site')!=site:raise ValueError('完整查询来源国家与问题不同')
        record_hash=digest(record)
        for item,_,original in members:
            if item.get('record_sha256')!=record_hash or original not in record['evidence_ids']:raise ValueError('完整查询版本/原始证据归属错误')
        errs=verify_job(owner,job)
        if errs:raise ValueError('不完整调查查询：'+canonical(errs))
        # All pages must be cited, otherwise uncited evidence could change after review.
        expected=set(record['evidence_ids']);provided={original for _,_,original in members}
        if provided!=expected:raise ValueError('完整查询证明遗漏分页')
        checked_jobs.append((owner,job))
        for item,_,_ in members:
            ids[item['evidence_id']]={'job':job,'platforms':owner.policy.get('expected_platforms',{}).get(site) or run.policy.get('expected_platforms',{}).get(site,[])}
            receipts.append({'job_id':jid,'owner':str(owner.path),'record_sha256':record_hash,'sql_hash':record['sql_hash']})
    return ids,receipts,checked_jobs


def _verify_scope_coverage(run,answer,checked_jobs):
    from acceptance_queries import plan,verify_question_job
    scope=answer['scope'];covered=set();direct=set();allowed=set(scope['months'])
    required={(mm,layer,pl,canonical(path)) for mm in scope['months'] for layer in scope['source_layers'] for pl in scope['platforms'] for path in scope['paths']}
    for owner,j in checked_jobs:
        family=j['family'];spec=j.get('scope_spec')
        if family.startswith('question_'):
            verify_question_job(owner.manifest,j)
            # Whole-site identity follow-up expands beyond the original subtree.
            # It is admissible supporting evidence, not its comparable denominator.
            if j.get('mode')=='lineage':continue
            if spec['path_layer']!=scope['path_layer']:continue
            layers=spec['source_layers'];platforms=spec['platforms'];period=j['months'];paths=spec['paths']
        else:
            if j not in plan(owner.manifest):raise ValueError('新议题证明只接受当前程序可重建的基础/定向查询')
            period=months(owner.manifest['starts'][scope['site']],owner.manifest['data_month'])
            platforms=owner.policy.get('expected_platforms',{}).get(scope['site'],[])
            # Legacy aggregate receipts lacked a platform contract. A matching
            # current frozen single-platform country still needs platform facts
            # in the overall coverage gate; it cannot split a merged CN query.
            if not platforms:platforms=run.policy.get('expected_platforms',{}).get(scope['site'],[])
            if family=='platform':layers=[j['entity']];paths=[[]]
            elif family=='fingerprint' and len(platforms)==1:layers=[j['entity']];paths=[[]]
            elif family in ('category','bands') and len(platforms)==1 and scope['path_layer']=='std':
                layers=['std'];paths=[p for p in scope['paths'] if len(p)<=j.get('level',0)]
            elif family=='raw_paths' and len(platforms)==1 and scope['path_layer']=='raw':layers=['raw'];paths=scope['paths']
            else:continue
            if family in ('fingerprint','platform') and scope['paths']!=[[]]:continue
        for mm in period:
            for layer in layers:
                for pl in platforms:
                    for target in scope['paths']:
                        if any(target[:len(p)]==p for p in paths):
                            cell=(mm,layer,pl,canonical(target));covered.add(cell)
                            if family in ('question_cohorts','question_details'):direct.add(cell)
    if required-covered:raise ValueError('完整查询未覆盖全部问题国家/平台/月份/数据层/子树：'+canonical(sorted(required-covered)))
    # Business deep-review units need object/cohort evidence for the event; old
    # trigger aggregates alone cannot certify deeper identity or price findings.
    ob=obligations(run).get(answer['obligation_id'])
    if ob and ob['contract']['stage']=='deep_review':
        event={c for c in required if c[0] in scope['event_months']}
        if event-direct:raise ValueError('深查事件缺完整身份群体或明细证据，只有全景触发不能完成')


def _locator(lookup,link,allowed,scope,*,require_target=False):
    if link['evidence_id'] not in allowed:raise ValueError('原始定位未属于已核验完整查询')
    if not re.fullmatch(r'/response/data/\d+',link.get('pointer','')):raise ValueError('查询定位必须指向实际结果行')
    row=review.locator(lookup,link)
    role=link.get('scope_role','target')
    if role not in ('target','context','counterevidence','destination'):raise ValueError('证据用途必须区分目标与背景/反证/去向')
    actual=row_scope(allowed[link['evidence_id']],row)
    if role=='target':
        if not evidence_within(actual,scope):raise ValueError('目标证据超出问题国家/平台/月份/数据层/路径范围')
    else:
        _text(link.get('scope_relation'),'跨范围证据与目标的关系')
        if require_target:raise ValueError('背景、去向或反证不能计入目标影响')
    return row


def verify_answer(run,ob,answer,lookup):
    if answer.get('scope')!=ob['scope']:raise ValueError('答卷未覆盖完整问题范围')
    allowed,receipts=_proof_jobs(run,answer,lookup)
    if set(answer.get('checks',{}))!=set(ob['required_checks']):raise ValueError('漏答质量检查')
    if answer.get('business_verdict') not in ('pass','qualified','reject','undetermined'):raise ValueError('业务判断缺失')
    if answer.get('repair_state') not in ('not_needed','pending','retested'):raise ValueError('修复状态缺失')
    for key in ('conclusion','next_action'):_text(answer.get(key),key)
    blocked=False
    for name,item in answer['checks'].items():
        if item.get('status') not in ('verified','blocked_external','not_applicable'):raise ValueError('调查未回答的质量缺口必须继续，不可作为完成提交')
        for k in ('finding','counterevidence'):_text(item.get(k),name+'.'+k)
        _list(item.get('actions'),'实际动作');_list(item.get('evidence_ids'),'证据')
        if not set(item['evidence_ids'])<=set(allowed):raise ValueError('核查证据不属于完整查询')
        links=_list(item.get('raw_locators'),'原始字段核对')
        for link in links:_locator(lookup,link,allowed,ob['scope'])
        if item['status']=='not_applicable':
            _text(item.get('not_applicable_reason'),'约束不适用证据解释')
            if name in ('scope','counterevidence','impact'):raise ValueError('问题核心检查不能豁免')
        if item['status']=='blocked_external':
            blocked=True;b=item.get('blocking',{})
            if b.get('type') not in ('missing_external_record','missing_contract','missing_definition','missing_internal_data','external_access_denied'):raise ValueError('预算、查询失败不是外部阻塞')
            for k in ('required_record','provider_role','attempted_access','why_unavailable','decision_impact','resume_action'):_text(b.get(k),'blocking.'+k)
            _list(b.get('attempt_evidence_ids'),'实际缺证尝试')
            for eid in b['attempt_evidence_ids']:lookup(eid)
    expected={c['component_id'] for c in ob['contract']['required_components']}
    responses=answer.get('component_responses',[])
    _list([r.get('component_id') for r in responses],'反向分量回应',empty=True)
    if {r['component_id'] for r in responses}!=expected:raise ValueError('重要反向分量漏答')
    for r in responses:
        _text(r.get('finding'),'反向分量结论')
        for link in _list(r.get('raw_locators'),'反向分量证据'):_locator(lookup,link,allowed,ob['scope'])
    calculations=answer.get('calculations',[])
    _list([c.get('calculation_id') for c in calculations],'关键计算')
    for calc in calculations:
        role=calc.get('scope_role','target')
        if role not in ('target','context'):raise ValueError('计算用途必须区分目标影响与背景比较')
        if role=='context':_text(calc.get('scope_relation'),'背景计算与问题的关系')
        for operand in calc.get('operands',[]):_locator(lookup,operand['locator'],allowed,ob['scope'],require_target=role=='target')
        review.recompute(lookup,calc,None)
    calcs={c['calculation_id'] for c in calculations}
    target_calcs={c['calculation_id'] for c in calculations if c.get('scope_role','target')=='target'}
    metrics=answer.get('metric_impacts',{})
    if set(metrics)!={'spus','bands','units','amount'}:raise ValueError('四指标分别交代可量化范围或不适用原因')
    for metric,item in metrics.items():
        _text(item.get('basis'),'指标分母与单位')
        if metric in ob['contract']['impact_metrics']:
            if item.get('state')=='unknown' and blocked:_text(item.get('missing'),'缺少的影响证据')
            elif item.get('state')=='measured':
                ids=_list(item.get('calculation_ids'),'指标实际复算')
                if not set(ids)<=calcs:raise ValueError('指标引用不存在的复算')
                if not set(ids)<=target_calcs:raise ValueError('背景计算不能计入目标指标影响')
                for ident in ids:
                    calc=next(c for c in calculations if c['calculation_id']==ident)
                    if calc.get('metric')!=metric:raise ValueError('指标与复算量纲不一致')
            else:raise ValueError('适用指标尚未量化，须继续或保留实际阻塞')
        elif item.get('state')!='not_applicable':raise ValueError('未要求指标应明确不适用边界，不编造零值')
    for response in responses:
        ids=_list(response.get('calculation_ids'),'反向分量实际复算')
        if not set(ids)<=calcs:raise ValueError('反向分量复算未绑定')
    impacts=answer.get('quality_impact',{})
    if set(impacts)!={'observation_change','proven_error','unresolved_quality_risk'}:raise ValueError('观察变化、已证错误、未解决风险须分开')
    for key,item in impacts.items():
        _text(item.get('finding'),key);_text(item.get('scope'),key+'范围')
        if not set(item.get('calculation_ids',[]))<=calcs:raise ValueError('影响引用未知计算')
        if not set(item.get('calculation_ids',[]))<=target_calcs:raise ValueError('背景计算不能计入目标质量影响')
    if not impacts['observation_change'].get('calculation_ids'):raise ValueError('观察影响须绑定原始复算')
    if answer.get('quality_result') not in ('ruled_out','confirmed_defect','blocked_external'):raise ValueError('未回答质量疑问须继续；普通波动无市场原因不构成欠账')
    if (answer['quality_result']=='blocked_external')!=blocked:raise ValueError('缺证状态不一致')
    if blocked and answer['business_verdict']=='pass':raise ValueError('受阻不能放行')
    if answer['quality_result']=='confirmed_defect':
        if answer.get('defect_confirmed') is not True or answer['business_verdict']=='pass':raise ValueError('已证缺陷状态冲突')
        if not impacts['proven_error'].get('calculation_ids'):raise ValueError('确认错误须量化对应对象影响，不能把普通波动当错误')
    elif answer.get('defect_confirmed'):raise ValueError('缺陷成立状态冲突')
    _text(answer.get('stop_rationale'),'针对质量疑问的停止理由')
    review.validate_dimensions(answer,blocked)
    return blocked


def validate_main_review(run,task,ob,answer,entry,submission_hash):
    if task['contract_hashes'][ob['obligation_id']]!=ob['contract']['sha256']:raise ValueError('议题/范围版本改变，旧审核失效')
    binding=review.case_binding(run,ob['obligation_id'])
    if any(entry.get(k)!=v for k,v in binding.items()):raise ValueError('主审未绑定当前议题版本')
    if entry.get('conclusion_scope')!=ob['scope']:raise ValueError('主审结论范围缩减')
    if entry.get('material_sha256')!=review.material_digest(submission_hash,answer,None,run.get_evidence):raise ValueError('主审材料未绑定答卷及全部证据')
    allowed,_=_proof_jobs(run,answer,run.get_evidence)
    for l in _list(entry.get('raw_evidence_checks'),'主审实际原始字段'):_locator(run.get_evidence,l,allowed,ob['scope'])
    checks={k for l in entry['raw_evidence_checks'] for k in l.get('check_ids',[])}
    if not set(ob['required_checks'])<=checks:raise ValueError('主审核原始证据未覆盖各动作')
    calculations=entry.get('recomputations',[])
    if {c.get('calculation_id') for c in calculations}!={c['calculation_id'] for c in answer['calculations']}:raise ValueError('主审未复算全部关键影响')
    for c in calculations:
        expected=next(x for x in answer['calculations'] if x['calculation_id']==c['calculation_id'])
        if c!=expected:raise ValueError('复算操作数与答卷不一致')
        review.recompute(run.get_evidence,c,None)
    expected={c['component_id'] for c in ob['contract']['required_components']}
    if {c.get('component_id') for c in entry.get('component_reviews',[])}!=expected:raise ValueError('主审未逐反向分量审核')
    for c in entry.get('component_reviews',[]):
        if c.get('decision')!='accept':raise ValueError('反向分量未通过须退回')
        _text(c.get('reason'),'反向分量主审理由')
    _text(entry.get('alternatives_assessment'),'竞争解释处理');_text(entry.get('remaining_uncertainty'),'未决质量风险')
    # High-impact normal judgments and historical/conflicting findings keep the
    # independent third-reader requirement from the original review contract.
    needs_independent=(ob['priority']=='high' and answer['quality_result']=='ruled_out') or 'historical_regression' in ob['required_checks'] or answer.get('evidence_conflict')
    if needs_independent:
        v=entry.get('independent_review',{})
        if not v.get('reviewer_id') or v['reviewer_id'] in (task['agent_id'],task['coordinator_id']):raise ValueError('此判断需要实际第三人独立复核')
        if v.get('contract_sha256')!=ob['contract']['sha256'] or v.get('submission_sha256')!=submission_hash:raise ValueError('独立复核版本未绑定')
        _text(v.get('finding'),'独立复核结论')
        for link in _list(v.get('raw_locators'),'独立复核证据'):_locator(run.get_evidence,link,allowed,ob['scope'])
    return True


def prepare_coverage(run):
    from acceptance_queries import plan
    from acceptance_coverage import _freeze
    return _freeze(run,'coverage-plan',{'schema':'monthly-acceptance-coverage/'+protocol(run,'4.0'),
        'scope':{'sites':run.manifest['sites'],'starts':run.manifest['starts'],'end':run.manifest['data_month'],
                 'expected_platforms':run.policy.get('expected_platforms',{})},
        'policy_hash':run.manifest['policy_hash'],'query_plan_hash':digest(plan(run.manifest)),
        'tasks':[{'task_id':j['job_id'],'site':j['site'],'family':j['family'],'job_id':j['job_id']} for j in plan(run.manifest)],
        'ordinary_observations_not_tasks':True})


def freeze_discovery(run,value=None,**kwargs):
    from acceptance_coverage import _freeze
    index=panorama.load(run);reading=reading_status(run,index)
    _text((value or {}).get('independence_disclosure',kwargs.get('provenance')),'实际独立性说明')
    return _freeze(run,'coverage-independent',{'protocol':protocol(run,4),'panorama_binding_sha256':index['binding_sha256'],
        'reading_status':reading,'case_registry_sha256':digest(_registry(run)),
        'declared_history_blind':bool(kwargs.get('history_blind')),'disclosure':value or kwargs,
        'candidate_hash':digest([]),'ordinary_observations_create_obligations':False})


def prepare_sampling(run):
    """Same frozen seed and stratum count; the whole observed L3 population."""
    from acceptance_coverage import _freeze,_frozen,_sample_population
    from acceptance_queries import plan
    _frozen(run,'coverage-independent');index=panorama.load(run)
    jobs=plan(run.manifest);data={j['job_id']:run.job_rows(j['job_id']) for j in jobs if j['family']=='category' and j.get('level')==3}
    cells=_sample_population(run,run.manifest,jobs,data,[])
    policy=run.policy['coverage_gate'];n=policy.get('sample_per_stratum',1);seed=policy.get('seed',run.policy['sample_seed'])
    if type(n)!=int or n<1:raise ValueError('抽查不能设为零')
    groups={}
    for c in cells:groups.setdefault(tuple(c['stratum']),[]).append(c)
    selected=[c['cell_id'] for key,g in sorted(groups.items()) for c in sorted(g,key=lambda c:(digest([seed,c['cell_id']]),c['cell_id']))[:n]]
    return _freeze(run,'coverage-sampling-plan',{'protocol':protocol(run,4),'population':cells,'selected_cell_ids':sorted(selected),
        'seed':seed,'stratification':['site','month'],'per_stratum':n,'population_version':'all_applicable_observed_L3/'+protocol(run,'4.0'),
        'panorama_binding_sha256':index['binding_sha256'],'coverage_claim':'只证明选中对象；抽查身份、源行映射结构与量价，不做类目语义测试'})


def sampling_jobs(run):
    from acceptance_coverage import _frozen,_sampling_job
    s=_frozen(run,'coverage-sampling-plan');required=set(s['selected_cell_ids'])
    # A concrete miss expands the checking population, not business obligations.
    for e in _events(run,'question.baseline.reviewed'):
        v=read(run.path/'baseline-reviews'/(e['review_id']+'.json'))
        if digest(v)!=e['sha256']:raise ValueError('抽查扩围记录改变')
        if v['check_id'].startswith('sample:') and v['status']=='failed':
            cid=v['check_id'][7:];cell=next(c for c in s['population'] if c['cell_id']==cid)
            required.update(c['cell_id'] for c in s['population'] if c['site']==cell['site'])
    return [_sampling_job(run,c) for c in s['population'] if c['cell_id'] in required]


def baseline_checks(run):
    """Ancillary checks retain execution records without per-item deep tasks."""
    result=[]
    for site in run.manifest['sites']:
        for family in ('internal_reconciliation','published_handoff'):
            result.append({'check_id':family+':'+site,'site':site,'kind':family,'required':True})
    for rel in run.manifest.get('history_sources',[]):
        from acceptance_history import segment_source
        result.append({'check_id':'history_source:'+digest(rel)[:24],'kind':'history_inventory','site':'global',
            'source_path':rel,'source_sha256':run.manifest['inputs'][rel]['sha256'],
            'required_segment_ids':[s['segment_id'] for s in segment_source(run.path/rel)]})
    for name in ('open-issues.json','legacy-issues.json'):
        p=run.path/'inputs/project'/name
        if not p.exists():continue
        for issue in read(p):
            result.append({'check_id':'regression:'+name+':'+issue['issue_id'],'kind':'historical_regression','site':issue.get('site','global'),
                'source_path':str(p.relative_to(run.path)),'source_sha256':digest(p.read_bytes()),'source_issue':issue})
    if not any(x['kind'] in ('history_inventory','historical_regression') for x in result):
        result.append({'check_id':'history_inventory:absent','kind':'history_inventory','site':'global','inventory_absent':True})
    p=run.path/'coverage-sampling-plan.json'
    if p.exists():
        for j in sampling_jobs(run):result.append({'check_id':'sample:'+j['cell_id'],'kind':'sample','site':j['site'],'job':j})
    else:result.append({'check_id':'sampling:not_planned','kind':'sample','site':'global'})
    return result


def _regression_objects(run,issue):
    """Keep historical product/SKU identities typed and bound to their platform."""
    detail=issue.get('legacy_detail',{})
    def values(plural,singular):
        result=list(issue.get(plural,detail.get(plural,[])))
        for source in (detail,issue):
            if singular in source:result.append(source[singular])
        return result
    site=issue.get('site',detail.get('site'))
    if site not in run.manifest['sites']:raise ValueError('历史对象国家未明确；须保留 unavailable')
    products={str(p) for p in values('product_ids','product_id') if p is not None and str(p).strip() not in ('','0')}
    sku_keys=issue.get('sku_keys',detail.get('sku_keys',[]))
    platforms=issue.get('platforms',detail.get('platforms',[]))
    platform=issue.get('platform',detail.get('platform'))
    platforms=set(platforms) | ({platform} if platform else set())
    platforms.update(k['platform'] for k in sku_keys if k.get('platform'))
    if not platforms:platforms=set(run.policy.get('expected_platforms',{}).get(site,[]))
    def platform_for(value=None):
        if value:return value
        if len(platforms)!=1:raise ValueError('历史对象平台不唯一；须补精确身份或保留 unavailable')
        return next(iter(platforms))
    targets=[]
    def add(product,platform,sku=None,with_sku=False):
        if product is None or str(product).strip() in ('','0'):raise ValueError('历史 SKU 缺有效父体；占位 SKU 不能跨父体回归')
        target={'site':site,'platform':platform_for(platform),'product_id':str(product)}
        if with_sku:target['sku_id']=None if sku is None else str(sku)
        if target not in targets:targets.append(target)
    for key in sku_keys:
        if 'sku_id' not in key:raise ValueError('历史复合 SKU 身份缺 sku_id')
        add(key.get('product_id'),key.get('platform'),key['sku_id'],True)
    for product in products:
        if not any(t['product_id']==product for t in targets):add(product,None)
    for sku in values('sku_ids','sku_id'):
        normalized=None if sku is None else str(sku)
        if any(t.get('sku_id',object())==normalized for t in targets):continue
        if len(products)!=1:raise ValueError('历史 SKU 缺唯一父体映射；须提供 sku_keys 或保留 unavailable')
        add(next(iter(products)),None,sku,True)
    if not targets:raise ValueError('回归缺精确历史对象；须保留 unavailable')
    return targets


PREDICATES=('must_exist','must_not_exist','property','aggregate')
COMPARISONS={'eq':lambda a,b:a==b,'ne':lambda a,b:a!=b,'lt':lambda a,b:a<b,
             'le':lambda a,b:a<=b,'gt':lambda a,b:a>b,'ge':lambda a,b:a>=b}


def _regression_expectation(issue,layer=None):
    """A repaired defect often means the object must be GONE, not still present.

    Absent an explicit expectation the historical rule stays exactly as before:
    the object must still be observable in every historical month and the current
    month. Issues that expect removal or a corrected value say so themselves. The
    expectation may be written per data layer: a classification repair means the
    object leaves the wrong std path while it still exists in raw.
    """
    spec=issue.get('expectation') or issue.get('legacy_detail',{}).get('expectation')
    if isinstance(spec,dict) and spec and set(spec)<= {'raw','std'}:
        if layer is None:raise ValueError('分层预期须指明数据层')
        spec=spec.get(layer)
    if spec is None:return {'predicate':'must_exist','months':None}
    if not isinstance(spec,dict) or spec.get('predicate') not in PREDICATES:
        raise ValueError('历史回归预期谓词须为 '+'/'.join(PREDICATES))
    out={'predicate':spec['predicate'],'months':spec.get('months')}
    if out['months'] is not None:
        _list(out['months'],'预期适用月份')
        for mm in out['months']:month(mm)
    if out['predicate'] in ('property','aggregate'):
        _text(spec.get('field'),'预期字段')
        if spec.get('comparison') not in COMPARISONS:raise ValueError('预期比较符须为 '+'/'.join(sorted(COMPARISONS)))
        if 'value' not in spec:raise ValueError('预期缺比较值')
        out.update(field=spec['field'],comparison=spec['comparison'],value=spec['value'])
    _text(spec.get('basis'),'预期依据')
    return out


def _row_month(row):
    return str(row.get('month_dt',row.get('month','')))[:7]


def _row_matches(row,target):
    return all(k in row and (None if row[k] is None else str(row[k]))==value for k,value in target.items())


def _compare(expect,observed):
    op=COMPARISONS[expect['comparison']];want=expect['value']
    if expect['comparison'] in ('eq','ne'):
        return op(None if observed is None else str(observed),None if want is None else str(want))
    if observed is None:return False
    return op(float(observed),float(want))


def _verify_regression_layer(run,issue,rows,complete_rows=None,layer=None):
    targets=_regression_objects(run,issue)
    historical_months=set()
    for source in (issue.get('legacy_detail',{}),issue):
        historical_months.update(source.get('months',[]))
        if source.get('first_observed_month') is not None:historical_months.add(source['first_observed_month'])
    if not historical_months:raise ValueError('历史时期未明确；仅有新月不能通过回归，须保留 unavailable')
    required_months=historical_months | {run.manifest['data_month']}
    for mm in required_months:month(mm)
    expect=_regression_expectation(issue,layer)
    if expect['predicate']=='must_exist':
        for target in targets:
            for mm in required_months:
                if not any(_row_month(r)==mm and _row_matches(r,target) for r in rows):
                    raise ValueError('各层回归缺精确国家/平台/父体/SKU 的历史月或新月原始对象；须保留 unavailable')
        return
    checked=set(expect['months'] or {run.manifest['data_month']})
    if checked-required_months:raise ValueError('预期月份越出历史时期与当月')
    # Absence and corrected values are claims about the whole queried space, so
    # they are judged against the complete verified result, never a cited row.
    universe=rows if complete_rows is None else complete_rows
    for mm in checked:
        if not any(_row_month(r)==mm for r in universe):
            raise ValueError('该月完整查询无任何结果，不能区分已修复与未观测；须保留 unavailable')
    if expect['predicate']=='aggregate':
        for mm in checked:
            total=sum(float(r.get(expect['field']) or 0) for r in universe
                      if _row_month(r)==mm and any(_row_matches(r,t) for t in targets))
            if not _compare(expect,total):raise ValueError('回归聚合预期未达成：'+mm)
        return
    for target in targets:
        for mm in sorted(historical_months-checked):
            if not any(_row_month(r)==mm and _row_matches(r,target) for r in rows):
                raise ValueError('缺原时期原始对象，不能证明缺陷曾经成立；须保留 unavailable')
        for mm in sorted(checked):
            present=[r for r in universe if _row_month(r)==mm and _row_matches(r,target)]
            if expect['predicate']=='must_not_exist':
                if present:raise ValueError('对象在应消失的月份仍然存在：'+mm)
            elif not present:raise ValueError('缺该月原始对象，无法核对预期属性；须保留 unavailable')
            elif not all(_compare(expect,r.get(expect['field'])) for r in present):
                raise ValueError('对象属性未达成回归预期：'+mm)


def record_baseline_review(run,actor,value,*,_audit=False):
    if actor!=plan_data(run)['coordinator_id']:raise ValueError('基础检查须主审登记')
    v=deepcopy(value);known={c['check_id']:c for c in baseline_checks(run)};check=known.get(v.get('check_id'))
    if not check:raise ValueError('未知基础检查')
    if v.get('reviewer_id')!=actor or not v.get('investigator_id') or actor==v['investigator_id']:raise ValueError('基础检查需要实际调查人和不同审核人')
    if v.get('status') not in ('passed','failed','unavailable','not_applicable'):raise ValueError('未知基础检查状态')
    for k in ('finding','action','counterevidence'):_text(v.get(k),k)
    eids=_list(v.get('evidence_ids'),'基础检查证据')
    for eid in eids:run.get_evidence(eid)
    links=_list(v.get('raw_locators'),'基础检查原始核对')
    located=[review.locator(run.get_evidence,l) for l in links]
    if check['kind'] in ('internal_reconciliation','published_handoff') and v['status']=='not_applicable':raise ValueError('项目内部对账/发布承接不可用不等于不适用')
    if check['kind']=='internal_reconciliation' and v['status']=='passed':
        sources=_list(v.get('internal_source_paths'),'实际内部对账文件')
        if not all(rel.startswith('inputs/internal/') and rel in run.manifest['inputs'] for rel in sources):raise ValueError('内部对账无冻结内部原始数据')
        for rel in sources:
            if digest((run.path/rel).read_bytes())!=run.manifest['inputs'][rel]['sha256']:raise ValueError('内部输入版本改变')
        calculations=_list(v.get('reconciliations'),'内部与大盘原始值复算')
        for c in calculations:review.recompute(run.get_evidence,c,None)
        if v.get('reconciliation_contract') is None:
            v['contract_limitation']='内部对账未声明左右来源/匹配键/期间/单位/容差，本次不构成无保留通过'
        else:_reconciliation_contract(v,calculations)
        if not any(l['evidence_id'] in v.get('internal_evidence_ids',[]) for c in calculations for l in [o['locator'] for o in c['operands']]):raise ValueError('复算未包含内部原始凭据')
        for eid in _list(v.get('internal_evidence_ids'),'内部证据'):
            owner,origin,meta,payload=review._receipt_origin(run,run.get_evidence,eid)
            if meta.get('kind')!='frozen-source-document' or payload.get('source',{}).get('source_path') not in sources:raise ValueError('内部证据未绑定冻结文件原文')
            if review._source_record(owner,payload['source']['source_path'])!=payload['source']:raise ValueError('内部原文与冻结来源不一致')
    if check['kind']=='published_handoff' and v['status']=='passed':
        pub=_list(v.get('published_locators'),'实际发布对象定位')
        records=[review.locator(run.get_evidence,l) for l in pub]
        if not all(r.get('release_id') and r.get('product_id') and ('units' in r or 'amount' in r) for r in records):raise ValueError('发布元数据不能替对象量额承接')
        for c in _list(v.get('reconciliations'),'发布承接复算'):review.recompute(run.get_evidence,c,None)
    if check['kind']=='sample':
        from acceptance_validate import verify_job
        j=check.get('job')
        if not j or verify_job(run,j):raise ValueError('抽样查询未完整成功')
        rows=run.job_rows(j['job_id']);record=run.job_record(j['job_id'])
        actual={digest(review.locator(run.get_evidence,l)) for l in links if l['evidence_id'] in record['evidence_ids']}
        if actual!={digest(r) for r in rows}:raise ValueError('未逐一核对全体确定样本')
        v['query_record_sha256']=digest(record)
    if check['kind']=='history_inventory':
        _text(v.get('inventory_reconciliation'),'历史来源完整清点')
        if check.get('inventory_absent') and v['status']=='passed':raise ValueError('历史清单缺失不等于回归完成')
        expected=set(check.get('required_segment_ids',[]));actual={s.get('segment_id') for s in v.get('segments',[])}
        if actual!=expected:raise ValueError('历史来源段落清点遗漏')
        for s in v.get('segments',[]):
            if s.get('disposition') not in ('linked_regression','semantic_auxiliary','reasonable_counterexample','non_issue'):raise ValueError('历史段落处置须明确')
            _text(s.get('reason'),'历史段落依据')
            if s['disposition']=='linked_regression':
                ids=_list(s.get('check_ids'),'历史段落实际回归承接')
                if not set(ids)<=set(known) or any(known[cid]['kind']!='historical_regression' for cid in ids):raise ValueError('历史段落未指向真实回归检查')
                _text(s.get('object_match_basis'),'历史段落与回归对象匹配依据')
    if check['kind']=='historical_regression':
        issue=check['source_issue'];scope=issue.get('legacy_detail',{})
        if v['status']=='passed':
            if set(v.get('source_layers_verified',[]))!={'raw','std'}:raise ValueError('必须回归底层 raw/std；仅展示修正不算回归')
            for layer in ('raw','std'):
                layer_refs=_list(v.get(layer+'_object_locators'),'各层原始历史对象')
                allowed,_,checked_jobs=_verified_query_proofs(run,v.get(layer+'_proof_jobs'),run.get_evidence,
                    issue.get('site',scope.get('site')),native_only=True)
                if any(l['evidence_id'] not in allowed for l in layer_refs):raise ValueError('历史对象定位未属于该层完整原生查询证明')
                complete=[r for owner,job in checked_jobs for r in owner.job_rows(job['job_id'])]
                _verify_regression_layer(run,issue,[review.locator(run.get_evidence,l) for l in layer_refs],complete,layer=layer)
                table=run.policy[layer+'_table']
                for l in layer_refs:
                    _,_,_,payload=review._receipt_origin(run,run.get_evidence,l['evidence_id'])
                    if table not in payload.get('request',{}).get('sql',''):raise ValueError('回归声称的数据层与原查询不一致')
        if v['status']=='not_applicable':_text(v.get('applicability_evidence'),'历史事项不适用依据；不得以当前金额小豁免')
    if v['status']=='failed':
        ids=_list(v.get('case_ids'),'基础失败议题归属');cases={c['case_id'] for c in _registry(run)['cases']}
        if not set(ids)<=cases:raise ValueError('基础检查失败缺活跃议题归属')
        units=[u for c in _registry(run)['cases'] if c['case_id'] in ids for u in c['scope_units']]
        site=check['site']
        if site in run.manifest['sites'] and not any(u['site']==site for u in units):raise ValueError('基础失败归属不同国家')
        if check['kind']=='sample':
            job=check['job'];path=json.loads(job['path'])
            if not any(u['site']==site and job['month'] in u['months'] and u['path_layer']=='std' and any(path[:len(p)]==p for p in u['paths']) for u in units):raise ValueError('抽查失败未被同国家/月份/子树负责')
        if check['kind']=='historical_regression':
            issue=check['source_issue'];historical_months=set(issue.get('months',[])) | {issue.get('first_observed_month',run.manifest['data_month'])}
            if not any(u['site']==site and historical_months<=set(u['months']) and 'historical_regression' in u['required_checks'] for u in units):raise ValueError('回归失败原时期及机制未归属')
            _text(v.get('historical_object_linkage'),'具体历史对象在议题中的对应定位')
    if v['status'] in ('unavailable','not_applicable'):
        _text(v.get('limitation'),'检查能力限制');_text(v.get('resume_condition'),'恢复条件')
    referenced=set(review.referenced_evidence_ids(v)) | set(eids)
    binding={eid:digest(run.get_evidence(eid)) for eid in sorted(referenced)}
    if _audit:
        if v.get('evidence_sha256')!=binding:raise ValueError('基础主审全材料版本改变')
        return True
    v['evidence_sha256']=binding
    v.update(check_sha256=digest(check),recorded_at=now());rid=digest(v)
    write(run.path/'baseline-reviews'/(rid+'.json'),v);run.event('question.baseline.reviewed',review_id=rid,sha256=digest(v),check_id=v['check_id'])
    return v


def case_status(run):
    state=_registry(run);obs=obligations(run)
    return {'required':True,'protocol':protocol(run,4),'cases':len(state['cases']),'scope_units':len(obs),'errors':[],
        'stages':dict(Counter(u['stage'] for c in state['cases'] for u in c['scope_units'])),
        'report_placements':dict(Counter(c['report_placement'] for c in state['cases'])),
        'rows':[{k:c[k] for k in ('case_id','case_version','business_question','report_placement','obligation_ids','scope_units')} for c in state['cases']]}


def coverage_status(run,include_tasks=False):
    from acceptance_coverage import _frozen
    from acceptance_queries import plan
    index=panorama.load(run);frozen=_frozen(run,'coverage-plan')
    if frozen['query_plan_hash']!=digest(plan(run.manifest)) or frozen['policy_hash']!=run.manifest['policy_hash']:raise ValueError('覆盖计划变更')
    reading=reading_status(run,index);case_state=_registry(run) if (run.path/'investigations/cases/state.json').exists() else {'cases':[]}
    constraints={c['check_id']:c for c in index['constraints']}
    failed={c['check_id'] for c in index['constraints'] if c['state']=='failed'}
    # Only explicit failures are claimable, so passed/unavailable constraints are
    # never forced to resolve a frozen platform set they do not need.
    ownership={}
    for cid in failed:
        units=[u for case in case_state['cases'] if cid in case['constraint_ids'] for u in case['scope_units']]
        required,covered=constraint_coverage(run,constraints[cid],units)
        ownership[cid]={'required':len(required),'covered':len(covered),'missing_cells':[list(x) for x in sorted(required-covered,key=canonical)]}
    owned={cid for cid,v in ownership.items() if not v['missing_cells']}
    reviews={};review_limitations=[]
    for e in _events(run,'question.baseline.reviewed'):
        v=read(run.path/'baseline-reviews'/(e['review_id']+'.json'))
        if digest(v)!=e['sha256']:raise ValueError('基础检查审核记录变更')
        for eid in v['evidence_ids']:run.get_evidence(eid)
        for link in v['raw_locators']:review.locator(run.get_evidence,link)
        reviews[v['check_id']]=v
        if v.get('contract_limitation'):review_limitations.append(v['check_id']+'：'+v['contract_limitation'])
    checks=baseline_checks(run);states={}
    for c in checks:
        v=reviews.get(c['check_id']);states[c['check_id']]=v['status'] if v and v['check_sha256']==digest(c) else 'not_executed'
        if v and v['check_sha256']==digest(c):record_baseline_review(run,plan_data(run)['coordinator_id'],v,_audit=True)
        if v and c.get('job'):
            from acceptance_validate import verify_job
            if verify_job(run,c['job']) or v.get('query_record_sha256')!=digest(run.job_record(c['job']['job_id'])):states[c['check_id']]='query_failed'
    resolved_failed=set()
    failed_reviews={cid:v for cid,v in reviews.items() if states.get(cid)=='failed'}
    if failed_reviews:
        import acceptance_investigation as inv
        result=inv.audit(run)
        if not result['errors']:
            owners={oid:t for t in inv.task_records(run).values() if t['state']!='requeued' for oid in t['obligation_ids']}
            bycase={c['case_id']:c for c in case_state['cases']}
            for check_id,v in failed_reviews.items():
                covered=[oid for cid in v['case_ids'] for oid in bycase.get(cid,{}).get('obligation_ids',[])]
                if covered and all(owners.get(oid,{}).get('outcomes',{}).get(oid)=='verified' for oid in covered):resolved_failed.add(check_id)
    counts=dict(Counter(r['state'] for r in index['baseline_jobs'].values()))
    limitations=[c for c in index['constraints'] if c['state']=='unavailable']
    return {'required':True,'protocol':protocol(run,4),'query_jobs':len(index['baseline_jobs']),'query_states':counts,
        'fact_cells':index['fact_cells'],'observed_cells':index['observed_cells'],
        'constraint_states':index.get('constraint_states',dict(Counter(c['state'] for c in index['constraints']))),
        'unowned_failure_ids':sorted(failed-owned),'panorama_reading':reading,
        'failure_scope_coverage':{cid:ownership[cid] for cid in sorted(failed)},
        'baseline_check_states':dict(Counter(states.values())),'baseline_checks':[{**c,'state':states[c['check_id']]} for c in checks],
        'query_complete':counts.get('success',0)==len(index['baseline_jobs']),
        'reading_complete':not reading['unread_partition_ids'],'hard_failure_ownership_complete':failed<=owned,
        'baseline_checks_complete':all(s in ('passed','not_applicable') or cid in resolved_failed for cid,s in states.items()),
        'failed_checks_with_reviewed_disposition':sorted(resolved_failed),
        'limitations_count':len(limitations),'limitations':limitations,
        'review_limitations':sorted(review_limitations),'errors':[]}


def _reconciliation_contract(v,calculations):
    """Arithmetic alone cannot prove the two sides were ever comparable.

    A recomputation checks addition; it does not notice that one side is CNY and
    the other USD, that the periods differ, or that SKU rows were matched against
    SPU rows. The contract states left/right identity, match keys and tolerance so
    those are checkable rather than assumed.
    """
    c=v.get('reconciliation_contract')
    if not isinstance(c,dict):raise ValueError('内部对账须声明左右来源、匹配键、期间、单位、容差与差异')
    sides={}
    for name in ('left','right'):
        side=c.get(name)
        if not isinstance(side,dict):raise ValueError('对账缺 '+name+' 方来源声明')
        for k in ('source','grain','period','unit'):_text(side.get(k),name+'.'+k)
        sides[name]=side
    for k in ('grain','period','unit'):
        if sides['left'][k]!=sides['right'][k]:
            raise ValueError('对账两方 '+k+' 不一致，本身不可比：'+str(sides['left'][k])+' / '+str(sides['right'][k]))
    _list(c.get('match_keys'),'对账匹配键')
    tol=c.get('tolerance')
    if not isinstance(tol,dict) or not isinstance(tol.get('absolute'),(int,float)) or isinstance(tol.get('absolute'),bool) or tol['absolute']<0:
        raise ValueError('对账容差须为非负数值')
    diff=c.get('difference')
    if not isinstance(diff,dict):raise ValueError('对账缺差异声明')
    calc=next((x for x in calculations if x.get('calculation_id')==diff.get('calculation_id')),None)
    if calc is None:raise ValueError('对账差异未绑定实际复算')
    if not isinstance(diff.get('value'),(int,float)) or isinstance(diff['value'],bool) or float(calc['result'])!=float(diff['value']):
        raise ValueError('对账差异与复算结果不一致')
    if abs(float(diff['value']))>float(tol['absolute']) and v['status']=='passed':
        raise ValueError('对账差异超出声明容差，不能记通过')
    residual=c.get('unmatched')
    if not isinstance(residual,dict):raise ValueError('对账缺未匹配残差')
    for k in ('left_only_rows','right_only_rows'):
        if type(residual.get(k)) is not int or residual[k]<0:raise ValueError('未匹配行数 '+k+' 须为非负整数')
    if residual['left_only_rows'] or residual['right_only_rows']:
        _text(residual.get('basis'),'未匹配残差的具体说明')
        for k in ('left_only_amount','right_only_amount'):
            if not isinstance(residual.get(k),(int,float)) or isinstance(residual[k],bool):
                raise ValueError('未匹配残差须给出金额规模：'+k)
    return c


def report_quantities(run,content,findings):
    """A reported number must BE an audited target calculation, not a retyped figure.

    Structural checks alone let a report state an impact no calculation supports,
    or quietly drop one that was measured. Stating a figure no calculation backs is
    a false claim and stays a hard error; merely omitting one is a gap, so it is
    carried as a limitation that still denies an unreserved pass.
    """
    errors=[];limitations=[]
    for f in content.get('findings',[]):
        owned=[oid for oid in f.get('obligation_ids',[]) if oid in findings]
        measured={}
        for oid in owned:
            answer=findings[oid];calcs={c['calculation_id']:c for c in answer.get('calculations',[])}
            for metric,item in answer.get('metric_impacts',{}).items():
                if item.get('state')!='measured':continue
                measured[(oid,metric)]={cid:calcs.get(cid) for cid in item.get('calculation_ids',[])}
        items=f.get('quantities',[])
        if not isinstance(items,list):errors.append('报告量化数字须为列表');items=[]
        stated=set()
        for q in items:
            if not isinstance(q,dict):errors.append('报告量化数字须为结构化条目');continue
            oid=q.get('obligation_id');metric=q.get('metric');cid=q.get('calculation_id')
            if not isinstance(q.get('statement'),str) or not q['statement'].strip():
                errors.append('报告数字缺口径说明：'+str(cid));continue
            if oid not in owned:errors.append('报告数字引用不属于该结论的范围：'+str(oid));continue
            calc=measured.get((oid,metric),{}).get(cid)
            if calc is None:errors.append('报告数字未绑定该指标的已审复算：'+str(cid));continue
            if calc.get('scope_role','target')!='target':errors.append('报告数字引用了背景计算：'+str(cid));continue
            if calc.get('metric')!=metric:errors.append('报告数字与复算量纲不一致：'+str(cid));continue
            value,result=q.get('value'),calc.get('result')
            same=panorama._close(value,result) if metric=='amount' else value==result
            if not same:errors.append('报告数字与已审复算结果不一致：'+str(cid));continue
            stated.add((oid,metric))
        for key in measured:
            if key not in stated:limitations.append('已审量化影响未在报告中按原复算列出：'+key[0]+':'+key[1])
    return errors,limitations


def aggregate_verdict(investigation,errors=(),limitations=()):
    """Execution completion is distinct from unreserved business acceptance."""
    counts=investigation.get('business_verdicts',{})
    if (not isinstance(counts,dict) or any(k not in ('pass','qualified','reject','undetermined') or
            type(v)!=int or v<0 for k,v in counts.items())):return 'undetermined'
    if errors or limitations:return 'undetermined'
    if counts.get('reject'):return 'reject'
    if counts.get('undetermined'):return 'undetermined'
    if counts.get('qualified'):return 'qualified'
    return 'pass' if investigation.get('unreserved_acceptance_eligible') is True else 'undetermined'


def validate(run,persist=True):
    from acceptance_queries import plan
    from acceptance_validate import verify_job
    import acceptance_investigation as inv
    errors=run.verify_inputs()+run.verify_events();cov={};investigation={};limitations=[]
    from acceptance_replay import audit as replay_audit
    reuse=replay_audit(run);errors.extend(reuse.get('errors',[]))
    try:
        # Reconstruct source policy and scope, rather than trusting run.json alone.
        from acceptance_coverage import _source_manifest
        _source_manifest(run)
        cov=coverage_status(run)
        for name in ('query_complete','reading_complete','hard_failure_ownership_complete','baseline_checks_complete'):
            if not cov[name]:errors.append(name+':incomplete')
        limitations=cov['limitations']
        if limitations:errors.append('objective_check_applicability_or_evidence_unavailable')
        # Declared gaps deny an unreserved pass without hard-stopping the run.
        limitations=limitations+cov.get('review_limitations',[])
    except (ValueError,OSError,KeyError,TypeError) as exc:errors.append('coverage:'+str(exc))
    content=read(run.path/'report-content.json') if (run.path/'report-content.json').exists() else None
    if content is None:errors.append('report_content_missing')
    try:
        investigation=inv.audit(run,content)
        errors.extend(investigation['errors']);limitations=limitations+investigation.get('limitations',[])
        if not investigation['substantive_complete']:errors.append('question_investigation_incomplete')
    except (ValueError,OSError,KeyError,TypeError) as exc:errors.append('investigation:'+str(exc))
    # The original bracketing fingerprints remain mandatory and are never
    # manufactured from historical imports. Mixed observation dates stay visible.
    for end in plan(run.manifest,True):
        errs=verify_job(run,end);errors.extend(errs)
        if errs:continue
        if end['family']=='delivery':
            # Country fingerprints cannot see a rebuild that leaves every aggregate
            # intact and only moves objects between categories; the upstream table's
            # own update time can, so it brackets the run independently.
            begin=next(j for j in plan(run.manifest) if j['family']=='delivery')
            try:
                stamp=lambda rows:{(r['table_schema'],r['table_name']):r['update_time'] for r in rows}
                a=stamp(run.job_rows(begin['job_id']));b=stamp(run.job_rows(end['job_id']))
                if set(a)!=set(b):errors.append('delivery_tables_changed')
                for t in sorted(set(a)&set(b)):
                    if a[t]!=b[t]:errors.append('delivery_rebuilt_during_run:'+'.'.join(t))
            except (ValueError,OSError,KeyError) as exc:errors.append(str(exc))
            continue
        begin=next(j for j in plan(run.manifest) if j['family']=='fingerprint' and j['site']==end['site'] and j['entity']==end['entity'])
        try:
            a={r['month_dt']:r for r in run.job_rows(begin['job_id'])};b={r['month_dt']:r for r in run.job_rows(end['job_id'])}
            if set(a)!=set(b):errors.append('fingerprint_months_changed:'+end['site'])
            for mm in set(a)&set(b):
                for k in ('nrows','spus','units','amount','invalid_keys','invalid_rows'):
                    if not (panorama._close(a[mm][k],b[mm][k]) if k=='amount' else a[mm][k]==b[mm][k]):errors.append('fingerprint_changed:'+end['site']+':'+mm+':'+k)
        except (ValueError,OSError,KeyError) as exc:errors.append(str(exc))
    ends=[run.job_record(j['job_id']) for j in plan(run.manifest,True)]
    if ends and all(r and r.get('execution_status')=='success' for r in ends):
        from datetime import datetime
        first_end=min(datetime.fromisoformat(r['started_at']) for r in ends)
        if first_end.tzinfo is None:first_end=first_end.astimezone()
        end_ids={j['job_id'] for j in plan(run.manifest,True)}
        for events in [run.events_path,*sorted((run.path/'investigations/workers').glob('*/events.jsonl'))]:
            for line in event_snapshot(events).splitlines():
                e=json.loads(line)
                if e['type']=='tool.started' and e.get('parent_id') not in end_ids:
                    t=datetime.fromisoformat(e['time'])
                    if t.tzinfo is None:t=t.astimezone()
                    if t>first_end:errors.append('data_query_after_final_fingerprint:'+str(events.relative_to(run.path)))
    if content:
        if not content.get('summary'):errors.append('report_summary_missing')
        known={c['case_id']:c for c in _registry(run)['cases']}
        for f in content.get('findings',[]):
            if f.get('case_id') not in known:errors.append('report_unknown_topic')
            elif f.get('case_version')!=known[f['case_id']]['case_version']:errors.append('report_stale_topic')
            for eid in f.get('evidence_ids',[]):run.get_evidence(eid)
        reported={f.get('case_id') for f in content.get('findings',[])}
        if not set(known)<=reported:errors.append('report_omits_registered_topic')
    verdict=aggregate_verdict(investigation,errors,limitations)
    result={'method_version':run.manifest['method_version'],'execution_state':'incomplete' if errors else 'complete',
        'business_verdict':verdict,'errors':sorted(set(errors)),'limitations':limitations,'coverage':cov,'investigation':investigation,
        'data_consistency':run.manifest['data_consistency'],'ordinary_observations_create_obligations':False}
    if persist:write(run.path/'validation.json',result);run.event('run.validated',validation_sha256=digest(result),execution_state=result['execution_state'])
    return result


def render(run):
    from acceptance_observability import summarize, resume_state
    validation=validate(run,persist=False);state=case_status(run);cov=validation.get('coverage',{});inv=validation.get('investigation',{})
    index=panorama.load(run);cost=summarize(run);queue=resume_state(run)
    content=read(run.path/'report-content.json') if (run.path/'report-content.json').exists() else {'summary':['调查进行中；不能整体放行。'],'findings':[]}
    lines=['# '+run.manifest['data_month']+' 大盘数据验收（全景与议题）','',
        '执行状态：'+validation['execution_state']+'；业务结论：'+validation['business_verdict']+'。',
        '',*content.get('summary',[]),'',
        f"全景：{cov.get('query_states',{}).get('success',0)}/{cov.get('query_jobs',0)} 个聚合查询；{cov.get('panorama_reading',{}).get('complete',0)}/{cov.get('panorama_reading',{}).get('partitions',0)} 个风险分区已判读。",
        f"事实：{index['fact_cells']} 个路径×月单元，{index['observed_cells']} 个有观测；无观测保留原状态。",
        f"调查：{state['cases']} 个议题、{state['scope_units']} 个独立范围；{cost['investigation_assignments']} 次实际分派，{inv.get('reviewed',0)} 个范围经实质主审，{inv.get('verified',0)} 个范围已核实，{inv.get('blocked_external',0)} 个范围外部受阻。普通观察不形成待办。",
        '队列状态：'+canonical({k:len(v) for k,v in queue['obligations_by_state'].items()})+'。',
        f"取证成本：本运行及子包 {cost['actual_query_attempts']} 次实际请求，{cost['verified_observation_jobs_reused']} 个来源任务复用，{cost['shared_query_requests_reused']} 次同查询共享，{cost['query_retries_planned']} 次计划重试。详细口径见 execution-metrics.json；模型 token/费用未知。",'']
    findings={f['case_id']:f for f in content.get('findings',[])}
    for placement,title in [('main','重点问题'),('brief','补充核查'),('appendix','辅助观察')]:
        cases=[c for c in _registry(run)['cases'] if c['report_placement']==placement]
        if not cases:continue
        lines += ['## '+title,'']
        for c in cases:
            # 没有单独的短标题时，标题就是业务问题本身；再原样打一遍只是逐字重复。
            f=findings.get(c['case_id'],{});head=c.get('display_title') or c['business_question']
            lines += ['### '+head,'']
            if c['business_question']!=head:lines += [c['business_question'],'']
            lines += [f.get('assessment','待调查；起因事实尚不能证明缺陷。'),'',
                '范围：'+'；'.join(u['site']+' '+','.join(u['platforms'])+' '+','.join(u['event_months'])+' '+canonical(u['paths']) for u in c['scope_units'])]
            for field,label in [('observation_change','观察变化'),('proven_error','已证错误影响'),('unresolved_quality_risk','未解决质量风险'),('counterevidence','反证与边界')]:
                if f.get(field):lines += [label+'：'+(f[field] if isinstance(f[field],str) else canonical(f[field]))]
            for qty in f.get('quantities',[]) or []:
                lines += ['量化影响（'+str(qty.get('metric'))+'，复算 '+str(qty.get('calculation_id'))+'）：'
                          +str(qty.get('value'))+'　'+str(qty.get('statement'))]
            if f.get('evidence_ids'):
                lines += ['证据：'+'、'.join('['+eid+']('+str(run.path/'evidence'/(eid+'.json'))+')' for eid in f['evidence_ids'])]
            lines += ['下一核查：'+f.get('next_check',c['next_check']), '停止条件：'+c['stop_condition'],'']
    lines+=['## 检查覆盖与限制','',
        '客观检查：'+canonical(cov.get('constraint_states',{}))+'；未归属失败 '+str(len(cov.get('unowned_failure_ids',[])))+'。',
        '抽查、历史、内部与发布承接：'+canonical(cov.get('baseline_check_states',{}))+'。',
        '待完成门槛：'+('、'.join(validation['errors']) or '无')]
    # A limitation denies an unreserved pass. Left unprinted, a run reads as
    # undetermined with no gate named. 客观检查不可判定同时是待完成门槛（见上一行），
    # 所以这里不能笼统写「不阻断执行」——那与它自己列出的条目矛盾。
    stated=[x for x in validation['limitations'] if not isinstance(x,dict)]
    records=[x for x in validation['limitations'] if isinstance(x,dict)]
    lines+=['已声明限制（不构成无保留通过；其中客观检查不可判定同时计入上方待完成门槛）：']
    lines+=['- '+str(x) for x in stated]
    if records:
        grouped=Counter(str(r.get('family'))+'/'+str(r.get('rule'))+' '+str(r.get('site')) for r in records)
        lines+=['- 客观检查不可判定 '+str(len(records))+' 条：'+canonical(dict(sorted(grouped.items())))
                +'；逐条 check_id 见 validation.json 的 limitations。']
    if not validation['limitations']:lines+=['- 无']
    lines+=['','各国金额分别保留本币。查询复用不是新查询，指纹相同也不代表事务快照。']
    (run.path/'报告.md').write_text('\n'.join(lines)+'\n')
    write(run.path/'execution-metrics.json',cost)
    write(run.path/'resume.json',{**queue,'case_registry_sha256':digest(_registry(run)),'scope_units':list(obligations(run)),
        'missing_gates':validation['errors'],'final_delivery':not validation['errors']})
    return {'report':str(run.path/'报告.md'),'execution_state':validation['execution_state'],'business_verdict':validation['business_verdict']}


def register_many(run,actor,values):
    index=panorama.load(run);cache={}
    result=[register(run,actor,v,_index=index,_rows=cache) for v in values]
    panorama.load(run)
    return result
