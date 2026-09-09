"""Business routing on real SQL populations; no taxonomy-accuracy exercises."""
from copy import deepcopy
from pathlib import Path
import asyncio
import json
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from acceptance_core import Run,read,write,digest,canonical
import acceptance_cases as cases
import acceptance_triage as triage
import acceptance_investigation as inv
import acceptance_machine as machine
import acceptance_transport as transport
from acceptance_report_content import resolve_placements
from test_cases_v32 import case_run
from test_diagnostics import db, observation, add, run_sql
from test_machine_v32 import Percentile
from test_execution import record
from test_review_v32 import loc


@pytest.fixture
def classified_run(case_run, db, monkeypatch):
    run, state, obs, source = case_run
    policy=read(Path(__file__).resolve().parents[1]/'policies/default.json')
    policy['page_size']=100
    policy.pop('architecture',None); policy['version']='3.3.0'
    manifest={**run.manifest,'method_version':'3.3.0','policy':policy,'policy_hash':digest(policy)}
    write(run.path/'run.json',manifest);run=Run(run.path);source.manifest=manifest
    db.create_aggregate('PERCENTILE_APPROX',2,Percentile)
    for mm in ('2025-07','2026-06','2026-07'):
        for pid,units in [('p-exact',1),('large',10000),('up',1 if mm!='2026-07' else 101),('down',101 if mm!='2026-07' else 1)]:
            add(db,observation(site='US',month_dt=mm+'-01',product_id=pid,sku_id='sku-'+pid,
                              stdcategory1='A',stdcategory2='leaf',stdcategory3='C',count=units,discount_sales=units*10))
    spec={'site':'US','level':0,'path':[],'window_start':'2025-07','window_end':'2026-07'}
    for j in machine.collection_jobs(run.manifest,spec):
        write(run.path/'records'/(j['job_id']+'-plan.json'),j)
        record(run,j,run_sql(db,run.manifest,j['sql']))
    from acceptance_queries import job
    for family in ('category','bands'):
        j=job(run.manifest,family,'US',level=0);record(run,j,run_sql(db,run.manifest,j['sql']))
    c=next(c for c in state['cases'] if c['kind']=='semantic')
    j=machine.collection_jobs(run.manifest,{**spec,'window_start':'2026-07'})[0]
    eid=run.job_record(j['job_id'])['evidence_ids'][0]
    row=next(r for r in run.get_evidence(eid)['response']['data'] if r['product_id']=='p-exact')
    idx=run.get_evidence(eid)['response']['data'].index(row)
    link=loc(eid,row,[],pointer='/response/data/'+str(idx))
    return run,state,obs,source,spec,c,link,db


def decision(fx, selected=None, route='defer', placement='appendix', entities=None):
    run,state,obs,source,spec,c,link,db=fx
    selected=selected or [c]
    d={'case_ids':[c['case_id'] for c in selected], 'investigation_route':route,'report_placement':placement,
       'route_reason':'完整总体中仅一条已定位源记录涉及待修正偏差，原比较范围保持。',
       'placement_reason':'重算国家及业务类目影响，保持台账供后续复查。',
       'business_question':'记录重复是否影响国家和业务类目的量额与价带',
       'next_check':'核对完整源记录后按影响贡献补查。','stop_condition':'问题已回答或影响边界经审核；数据变化重新检查。',
       'evidence_locators':[deepcopy(link)]}
    if route=='defer':
        d['assessments']={c['case_id']:{'specs':[spec],'entities':entities or [['US','Taobao','p-exact']]} for c in selected}
        d.update(triage.preview(run,d))
        d.update(uncertainty_boundary='有限域为指定实体已记录重复副本，其他未知源采集风险继续留在基础核查。',
                 counterevidence='完整国家总体及各类目重新核算，其他贡献对象没有被并成同一商品。',
                 cumulative_review='已阅读去重累计SPU/价带及正负量额，当前单一有限域不改变主要规模结论。',
                 unassessed_scope_review='其他未知原始案件继续适用深查，不能视作无风险。',
                 reopen_conditions='新增相关信号、共同规则或批次变化、数据/证据变化后重新核对。',
                 metric_review={m:'已核对该指标完整分母与实体暴露；该有限偏差不会改变主要业务判断。' for m in machine.METRICS})
    return d


def test_reviewed_defer_changes_only_applicable_denominator(classified_run):
    fx=classified_run; run,state,obs,_,_,c,_,_=fx
    d=decision(fx); result=triage.classify(run,'main',[d]); q=triage.status(run)
    assert q['deferred_obligation_ids']==c['obligation_ids']
    assert len(result['members'])==len(obs)
    a=inv.audit(run)
    assert not a['errors'] and a['deferred']==len(c['obligation_ids']) and a['verified']==0
    assert a['applicable_investigation_obligations']==len(obs)-len(c['obligation_ids'])
    assert not a['substantive_complete'] and not a['unreserved_acceptance_eligible']
    assert a['states']['deferred']==1


def test_group_defer_does_not_require_per_obligation_answers(classified_run):
    fx=classified_run; run,state,*_=fx
    cs=[c for c in state['cases'] if c['kind'] in ('result','semantic')]
    triage.classify(run,'main',[decision(fx,cs,entities=[['US','Taobao',p] for p in ('p-exact','up','down')])])
    q=triage.status(run)
    assert len(q['deferred_obligation_ids'])==sum(len(c['obligation_ids']) for c in cs)
    assert not inv.task_records(run)
    assert all(cases.load(run)['cases'][i]['states']['review']=='pending' for i in range(len(state['cases'])))


@pytest.mark.parametrize('field', ['facts_sha256','binding','metric_review','reopen_conditions','uncertainty_boundary'])
def test_defer_rejects_missing_review_or_changed_facts(classified_run,field):
    d=decision(classified_run);d.pop(field)
    with pytest.raises(ValueError):triage.classify(classified_run[0],'main',[d])


def test_unknown_impact_cannot_be_zero_defer(classified_run):
    fx=classified_run;d=decision(fx,route='investigate');d['investigation_route']='defer';d['report_placement']='appendix'
    with pytest.raises(ValueError):triage.classify(fx[0],'main',[d])


@pytest.mark.parametrize('kind',['raw_presence_gap','historical','history_source','project'])
def test_core_scope_and_regression_still_required(classified_run,kind):
    fx=classified_run;c=next(c for c in fx[1]['cases'] if c['kind']==kind)
    d=decision(fx,[c],route='investigate',placement='appendix')
    with pytest.raises(ValueError,match='关键范围'):triage.classify(fx[0],'main',[d])


def test_single_large_parent_may_stay_in_main(classified_run):
    fx=classified_run;d=decision(fx,route='investigate',placement='main')
    triage.classify(fx[0],'main',[d]);q=triage.status(fx[0])
    c=next(c for c in q['cases'] if c['case_id']==fx[5]['case_id'])
    assert c['report_placement']=='main' and not q['deferred_obligation_ids']


def test_new_related_signal_or_rule_version_reopens(classified_run):
    fx=classified_run;run=fx[0];triage.classify(run,'main',[decision(fx)])
    state=cases.load(run);other=next(c for c in state['cases'] if c['case_id']!=fx[5]['case_id'] and c['site']=='US')
    other['case_version']+=1;cases._persist(run,state,'synthetic.related_version')
    q=triage.status(run)
    assert q['errors'] and not q['deferred_obligation_ids']
    assert next(c for c in q['cases'] if c['case_id']==fx[5]['case_id'])['investigation_route']=='investigate'


def test_changed_query_evidence_cannot_keep_defer(classified_run):
    fx=classified_run;run=fx[0];triage.classify(run,'main',[decision(fx)])
    eid=fx[6]['evidence_id'];path=run.path/'evidence'/(eid+'.json');v=read(path);v['response']['data'][0]['units']+=1;write(path,v)
    assert not triage.status(run)['deferred_obligation_ids']


def test_all_risk_kinds_union_not_signal_sum_and_keep_cancellation(classified_run):
    fx=classified_run;run,state=fx[:2];cs=[c for c in state['cases'] if c['kind'] in ('result','semantic')]
    payload={'case_ids':[c['case_id'] for c in cs], 'assessments':{c['case_id']:{'specs':[fx[4]],'entities':[['US','Taobao','up'],['US','Taobao','down'],['US','Taobao','p-exact']]} for c in cs}}
    facts=triage.preview(run,payload)['facts']
    national=next(g for g in facts['groups'] if not g['path'])
    assert national['monthly']['2026-07']['affected_observed']['units']==103
    mom=next(c for c in national['changes'] if c['base_month']=='2026-06')
    assert mom['units']=={'positive':100,'negative':-100,'gross':200,'net':0}
    assert set(facts['all_related_kind_counts']) >= {'result','semantic','historical','raw_presence_gap'}


def test_report_placement_is_decided_by_current_case_review(classified_run):
    fx=classified_run;triage.classify(fx[0],'main',[decision(fx)])
    content={'findings':[{'title':'small','obligation_ids':fx[5]['obligation_ids']}]}
    resolved,errors=resolve_placements(content,triage.status(fx[0]))
    assert not errors and resolved['findings'][0]['report_placement']=='appendix'
    content['findings'][0]['report_placement']='main'
    assert resolve_placements(content,triage.status(fx[0]))[1]


def test_same_calendar_query_reuses_partial_window_but_not_scope(classified_run):
    run=classified_run[0];spec=classified_run[4]
    large=machine.collection_jobs(run.manifest,spec)
    small=machine.collection_jobs(run.manifest,{**spec,'window_start':'2026-06'})
    assert all(j in large for j in small)
    other=machine.collection_jobs(run.manifest,{**spec,'level':1,'path':['A']})
    assert not {j['job_id'] for j in other}&{j['job_id'] for j in large}
    assert len(machine.collection_jobs(run.manifest,{**spec,'window_start':'2026-07'}))==1


def test_same_running_query_is_awaited_once_and_completed_query_reused(classified_run,monkeypatch):
    run=classified_run[0];j=machine.collection_jobs(run.manifest,{**classified_run[4],'window_start':'2026-07'})[0]
    calls=[];original=transport._execute_job
    async def slow(*args):
        calls.append(1);await asyncio.sleep(.01);return await original(*args)
    monkeypatch.setattr(transport,'_execute_job',slow)
    async def exercise():return await asyncio.gather(transport.execute_job(run,None,j),transport.execute_job(run,None,j))
    a,b=asyncio.run(exercise())
    assert a==b and len(calls)==1
    events=[json.loads(s) for s in run.events_path.read_text().splitlines()]
    assert {e.get('reuse_kind') for e in events if e['type']=='query.reused'}=={'in_flight','verified_result'}


def test_old_frozen_run_remains_compatible(case_run):
    run,*_=case_run
    assert triage.status(run)=={'enabled':False,'deferred_obligation_ids':[],'cases':[],'errors':[]}


def install_dispatch_fixture(fx,monkeypatch):
    import acceptance_review as review
    run,state,obs,source=fx[:4]
    for ob in obs:
        ob.setdefault('priority','normal'); ob.setdefault('requires_impact',False)
        ob['contract']=review.contract(run.manifest,ob)
    p={'coordinator_id':'main','obligations':obs,'policy':run.policy['investigation_workflow']}
    monkeypatch.setattr(inv,'coordinator_only',lambda r,a:p if a=='main' else (_ for _ in ()).throw(ValueError('actor')))
    monkeypatch.setattr(inv,'plan_data',lambda r:p)
    (run.path/'inputs').mkdir(exist_ok=True)
    write(run.path/'candidates.json',source.candidates);write(run.path/'samples.json',source.samples)
    return p


def test_defer_skipped_by_default_and_explicit_dispatch(classified_run,monkeypatch):
    fx=classified_run;run=fx[0]
    install_dispatch_fixture(fx,monkeypatch)
    triage.classify(run,'main',[decision(fx)])
    task=inv.assign(run,'main','worker')
    assert not set(task['obligation_ids']) & set(fx[5]['obligation_ids'])
    with pytest.raises(ValueError,match='已审核暂留'):inv.assign(run,'main','another',fx[5]['obligation_ids'])
    assert task['assignment_source']=='business_classification_queue'
    assert task['business_questions'][0]['next_check']


def test_only_reviewed_defers_can_finish_applicable_work_without_verification(classified_run,monkeypatch):
    fx=classified_run;run,state,obs,source=fx[:4];c=fx[5]
    # A minimal scope for this completion invariant: one original sample, no
    # scope/regression obligations removed from a production or frozen run.
    state=deepcopy(state);state['cases']=[deepcopy(c)];state['members']={oid:state['members'][oid] for oid in c['obligation_ids']}
    state['classification_reviews']={}
    monkeypatch.setattr(cases,'load',lambda r:deepcopy(state))
    saved=[]
    monkeypatch.setattr(cases,'_persist',lambda r,s,*a:(saved.append(deepcopy(s)) or s))
    monkeypatch.setattr(inv,'all_obligations',lambda r,p=None:{o['obligation_id']:o for o in obs if o['obligation_id'] in c['obligation_ids']})
    triage.classify(run,'main',[decision((run,state,obs,source,*fx[4:]))])
    state=saved[-1]
    a=inv.audit(run)
    assert not a['errors'] and a['expected_obligations']==a['deferred']==1
    assert a['applicable_investigation_obligations']==0 and a['applicable_investigation_complete']
    assert a['verified']==a['substantive_completed']==0 and not a['substantive_complete']
    assert not a['unreserved_acceptance_eligible']


def test_merge_waits_for_shared_task_then_only_plans_remaining_months(classified_run,monkeypatch):
    fx=classified_run;run,state=fx[:2];install_dispatch_fixture(fx,monkeypatch)
    target=next(c for c in state['cases'] if c['kind']=='result')
    td=decision(fx,[target],route='investigate',placement='main');triage.classify(run,'main',[td])
    task=inv.assign(run,'main','shared-worker',target['obligation_ids'])
    d=decision(fx,route='merge',placement='brief')
    d.update(merge_target_case_id=target['case_id'],shared_question=td['business_question'],remaining_checks='共享完整当前月；原样本身份、源映射及四指标判断分别保留。')
    triage.classify(run,'main',[d])
    q=triage.status(run);c=next(c for c in q['cases'] if c['case_id']==fx[5]['case_id'])
    assert c['merge_reference']['waiting_for_shared_task']
    next_task=inv.assign(run,'main','next-worker')
    assert not set(next_task['obligation_ids']) & set(fx[5]['obligation_ids'])
    needed=triage.collection_plan(run,state,[fx[5]])
    for j in needed.values():
        write(run.path/'records'/(j['job_id']+'-plan.json'),j);record(run,j,run_sql(fx[-1],run.manifest,j['sql']))
    c=next(c for c in triage.status(run)['cases'] if c['case_id']==fx[5]['case_id'])
    assert not c['merge_reference']['waiting_for_shared_task']
    assert not c['merge_reference']['remaining_job_ids'] and c['merge_reference']['covered_job_ids']
    assert fx[5]['obligation_ids'][0] not in triage.status(run)['deferred_obligation_ids']


def test_orphan_and_cycle_merge_rejected(classified_run):
    fx=classified_run;target=next(c for c in fx[1]['cases'] if c['kind']=='result')
    td=decision(fx,[target],route='investigate',placement='main');triage.classify(fx[0],'main',[td])
    d=decision(fx,route='merge',placement='brief');d.update(merge_target_case_id=target['case_id'],shared_question=td['business_question'],remaining_checks='补查原始范围')
    with pytest.raises(ValueError,match='活跃任务'):triage.classify(fx[0],'main',[d])
    d['merge_target_case_id']=fx[5]['case_id']
    with pytest.raises(ValueError,match='循环'):triage.classify(fx[0],'main',[d])


def test_local_finding_cannot_hide_unexplained_macro_comparison(classified_run):
    fx=classified_run;target=next(c for c in fx[1]['cases'] if c['kind']=='result')
    with pytest.raises(ValueError,match='变化对象'):
        decision(fx,[target])


def test_completed_source_delta_is_removed_from_merge_remaining(classified_run,monkeypatch):
    fx=classified_run;run,state=fx[:2];install_dispatch_fixture(fx,monkeypatch)
    target=next(c for c in state['cases'] if c['kind']=='result')
    td=decision(fx,[target],route='investigate',placement='main');triage.classify(run,'main',[td])
    inv.assign(run,'main','owner',target['obligation_ids'])
    base_spec={'site':'US','path':['A'],'level':1,'window_start':'2025-01','window_end':'2025-02'}
    needed={j['job_id']:j for j in machine.collection_jobs(run.manifest,base_spec)}
    common={k:j for k,j in needed.items() if j['window_start']=='2025-02'}
    monkeypatch.setattr(triage,'collection_plan',lambda r,s,cs:common if cs[0]['case_id']==target['case_id'] else needed)
    for j in needed.values():
        write(run.path/'records'/(j['job_id']+'-plan.json'),j)
        record(run,j,run_sql(fx[-1],run.manifest,j['sql']))
    d=decision(fx,route='merge',placement='brief')
    d.update(merge_target_case_id=target['case_id'],shared_question=td['business_question'],remaining_checks='原问题完整保留，已成功的差额不再重复补查。')
    triage.classify(run,'main',[d])
    c=next(c for c in triage.status(run)['cases'] if c['case_id']==fx[5]['case_id'])
    assert not c['merge_reference']['remaining_job_ids']
    assert set(c['merge_reference']['covered_job_ids'])==set(needed)


def test_query_replacement_invalidates_classification_even_with_old_immutable_page(classified_run):
    fx=classified_run;run=fx[0];d=decision(fx,route='investigate',placement='brief')
    triage.classify(run,'main',[d])
    j=machine.collection_jobs(run.manifest,{**fx[4],'window_start':'2026-07'})[0]
    record(run,j,run_sql(fx[-1],run.manifest,j['sql']))
    assert run.get_evidence(fx[6]['evidence_id'])
    q=triage.status(run)
    assert q['errors'] and not next(c for c in q['cases'] if c['case_id']==fx[5]['case_id'])['classified']


def test_case_scope_reference_shows_obligations_without_fabricating_review_credit(classified_run):
    fx=classified_run;triage.classify(fx[0],'main',[decision(fx)])
    content={'findings':[{'title':'scope observation','case_ids':[fx[5]['case_id']]}]}
    resolved,errors=resolve_placements(content,triage.status(fx[0]))
    assert not errors
    f=resolved['findings'][0]
    assert f['related_obligation_ids']==fx[5]['obligation_ids'] and 'obligation_ids' not in f
    a=inv.audit(fx[0],resolved)
    assert not a['errors'] and a['report_covered']==a['verified']==0
