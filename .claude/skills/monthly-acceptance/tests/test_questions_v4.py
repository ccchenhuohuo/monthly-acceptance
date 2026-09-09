"""V4 normal lifecycle and failure injection; ordinary observations are not debts."""
from copy import deepcopy
from pathlib import Path
import sys,json
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from acceptance_core import Run,create_run,read,write,digest
from acceptance_queries import plan,question_jobs,verify_question_job
import acceptance_questions as q
import acceptance_panorama as panorama
import acceptance_investigation as inv
import acceptance_review as review
from test_execution import record,rows_for

@pytest.fixture
def v4(tmp_path):
    base=tmp_path/'audit';base.mkdir()
    (base/'config.yaml').write_text('站点起始月份: {DE: 2024-01}\n')
    (base/'项目范围.md').write_text('Amazon DE Electronics; full test contract.')
    write(base/'policy-overrides.json',{'page_size':5})
    write(base/'scope-contract.json',{'complete_leaf_list':True,'entries':[{'id':'root','site':'DE','path_prefix':['Electronics'],'effective_from':'2024-01'}]})
    run=Run(create_run(base,Path(__file__).resolve().parents[1],'2024-02'));run.register_plan(plan(run.manifest));q.prepare_coverage(run)
    for j in plan(run.manifest):
        rows=rows_for(run,j)
        if j['family']=='platform':rows=[{'month_dt':mm+'-01','platform':'Amazon','nrows':1,'spus':1,'effective_skus':1,'placeholder_sku_rows':0,'invalid_rows':0,'units':10,'amount':100} for mm in ('2024-01','2024-02')]
        record(run,j,rows);write(run.path/'records'/(j['job_id']+'-plan.json'),j)
    index=panorama.build(run);q.prepare(run,'main')
    return run,index


def topic(v4):
    run,index=v4;j=next(j for j in plan(run.manifest) if j['family']=='category' and j['level']==0)
    ref={'job_id':j['job_id'],'row_index':1,'record_sha256':digest(run.job_rows(j['job_id'])[1])}
    unit={'scope_unit_id':'de-event','site':'DE','platforms':['Amazon'],'source_layers':['raw','std'],
        'paths':[[]],'path_layer':'std','event_months':['2024-02'],'background_months':['2024-01'],
        'stage':'aggregate_first','required_checks':['scope','conservation','counterevidence','impact'],
        'required_components':[{'component_id':'opposite','question':'核对基月与当月正负量额，不能只凭净变化。'}],
        'next_check':'按完整查询核对两月记录、量额及汇总是否守恒。','stop_condition':'所有限定范围的守恒疑问有证据答案，未决风险留待继续。','scope_rationale':'同一国家的一次汇总可比性问题。'}
    return {'business_question':'两月汇总是否因记录粒度变化而不守恒？','reason':'完整连续轨迹提出具体量额核查。','next_check':unit['next_check'],
        'stop_condition':unit['stop_condition'],'report_placement':'main','scope_units':[unit],'origin_refs':[ref],'constraint_ids':[]}


def answer(run,task):
    ob=inv.all_obligations(run)[task['obligation_ids'][0]];j=next(j for j in plan(run.manifest) if j['family']=='category' and j['level']==0)
    rec=run.job_record(j['job_id']);eid=rec['evidence_ids'][0];rows=run.get_evidence(eid)['response']['data']
    def link(n):
        row=rows[n]
        return {'evidence_id':'main:'+eid,'pointer':'/response/data/'+str(n),'record_sha256':digest(row),
            'fields':['spus','units','amount','month_dt','path'],'object_keys':{'month_dt':row['month_dt'],'path':row['path']},'purpose':'完整两月汇总量额核对'}
    links=[link(0),link(1)]
    calc={'calculation_id':'delta_units','method':'difference','operands':[{'locator':l,'field':'units'} for l in links],'result':0,'meaning':'限定国家两月的销量净差；不代表全部市场无缺陷。'}
    a={'obligation_id':ob['obligation_id'],'scope':ob['scope'],'business_verdict':'pass','repair_state':'not_needed','defect_confirmed':False,
       'quality_result':'ruled_out','conclusion':'测试完整两月汇总相等，限定的汇总不守恒疑问未成立。','next_action':'保留其他未立案范围的独立覆盖检查。',
       'stop_rationale':'完成定义的聚合疑问；普通市场变化无额外原因解释债务。',
       'proof_jobs':[{'evidence_id':'main:'+eid,'job_id':j['job_id'],'record_sha256':digest(rec)}],
       'checks':{k:{'status':'verified','finding':'本问题完整两月汇总一致。','counterevidence':'逐期核对，净量额稳定不替代身份真实性。',
           'actions':['读取完整原始聚合并复算两月差额。'],'evidence_ids':['main:'+eid],'raw_locators':deepcopy(links)} for k in ob['required_checks']},
       'component_responses':[{'component_id':'opposite','finding':'本测试两期量额与完整汇总同值，未将抵消当缺陷。','raw_locators':deepcopy(links)}],
       'calculations':[calc],'quality_impact':{k:{'finding':'观察销量差为0，限定汇总疑问未见错误；其他身份不在此结论中。','scope':'DE 2024-01/02 full country',
           'calculation_ids':['delta_units'] if k=='observation_change' else []} for k in ('observation_change','proven_error','unresolved_quality_risk')},
       'status_dimensions':{'execution':'success','investigation':'completed','defect':'not_found','cause':'not_applicable','impact':'bounded','review':'pending','acceptability':'pass'}}
    a['calculations']=[dict(calc,calculation_id='delta_'+metric,metric=metric,operands=[{'locator':l,'field':metric} for l in links]) for metric in ('spus','units','amount')]
    a['component_responses'][0]['calculation_ids']=['delta_units','delta_amount']
    a['metric_impacts']={metric:{'state':'measured','basis':'完整两月独立国家 '+metric+'，以原字段为单位。','calculation_ids':['delta_'+metric]} for metric in ('spus','units','amount')}
    a['metric_impacts']['bands']={'state':'not_applicable','basis':'本测试仅问汇总守恒，没有价格问题；不造百分比。'}
    for extra in plan(run.manifest):
        if extra['family']!='fingerprint' or extra['entity']!='raw':continue
        r=run.job_record(extra['job_id'])
        a['proof_jobs'] += [{'evidence_id':'main:'+e,'job_id':extra['job_id'],'record_sha256':digest(r)} for e in r['evidence_ids']]
    return a


def submit(run,t,a):
    write(run.path/t['worker_dir']/'submission.json',{'task_id':t['task_id'],'attempt':t['attempt'],'agent_id':t['agent_id'],'answers':[a]})
    return inv.submit(run,'main',t['task_id'])


def main_review(run,t):
    stored=inv.checked_submission(run,inv.task_records(run)[t['task_id']]);a=stored['payload']['answers'][0];ob=inv.all_obligations(run)[a['obligation_id']]
    links=deepcopy(a['checks']['scope']['raw_locators'])
    for l in links:l['check_ids']=ob['required_checks']
    entry={'obligation_id':a['obligation_id'],'decision':'accept','reason':'已核对完整两月和反向分量，答卷范围与问题一致。','scope_check':'DE 两月与全站路径均逐项核对。',
        'check_reviews':{k:{'decision':'accept','reason':'已读取所引原始字段，检查回应所问约束。'} for k in ob['required_checks']},
        'evidence_checked':sorted(review.evidence_ids(a)),**review.case_binding(run,ob['obligation_id']),
        'material_sha256':review.material_digest(stored['sha256'],a,None,run.get_evidence),'conclusion_scope':ob['scope'],
        'raw_evidence_checks':links,'recomputations':deepcopy(a['calculations']),
        'component_reviews':[{'component_id':'opposite','decision':'accept','reason':'已审计所列两期与正反分量边界。'}],
        'alternatives_assessment':'聚合守恒不证明商品身份真实，仅排除本范围的汇总不守恒。','remaining_uncertainty':'普通市场原因未解释不再变成质量调查欠账。'}
    return {'reviewer_id':'main','submission_sha256':stored['sha256'],'obligations':[entry]}


def test_default_protocol_and_no_candidate_debt(v4):
    run,index=v4
    assert run.manifest['method_version']=='0.0.1'
    write(run.path/'candidates.json',[{'candidate_id':str(i),'kind':'result_change'} for i in range(25000)])
    assert inv.expected_obligations(run)==[] and inv.audit(run)['substantive_complete']
    assert not q.validate(run,False)['execution_state']=='complete'  # reading/basic checks still mandatory


def test_real_lifecycle_submit_is_not_approval(v4):
    run,index=v4;c=q.register(run,'main',topic(v4));assert len(inv.all_obligations(run))==1
    t=inv.assign(run,'main','worker',c['obligation_ids']);a=answer(run,t);submit(run,t,a)
    assert inv.audit(run)['verified']==0
    inv.review(run,'main',t['task_id'],main_review(run,t))
    assert inv.audit(run)['verified']==1 and inv.audit(run)['substantive_complete']
    from acceptance_observability import summarize
    assert summarize(run)['investigation_assignments']==1


def test_archived_baseline_with_only_full_plan_can_support_review(v4):
    run,index=v4;c=q.register(run,'main',topic(v4));t=inv.assign(run,'main','worker',c['obligation_ids']);a=answer(run,t)
    for proof in a['proof_jobs']:(run.path/'records'/(proof['job_id']+'-plan.json')).unlink()
    submit(run,t,a);inv.review(run,'main',t['task_id'],main_review(run,t))
    assert inv.audit(run)['verified']==1


def test_modified_archive_full_plan_cannot_supply_proof(v4):
    run,index=v4;c=q.register(run,'main',topic(v4));t=inv.assign(run,'main','worker',c['obligation_ids']);a=answer(run,t)
    (run.path/'records'/(a['proof_jobs'][0]['job_id']+'-plan.json')).unlink()
    v=read(run.path/'plan.json');v['jobs'][0]['sql']+=' LIMIT 1';write(run.path/'plan.json',v)
    with pytest.raises(ValueError,match='计划哈希'):submit(run,t,a)


@pytest.mark.parametrize('damage',[None,'missing','changed'])
def test_native_versioned_query_plan_is_required_for_answer(v4,damage):
    import asyncio
    from acceptance_transport import execute_job
    from acceptance_job_versions import _path
    run,index=v4;c=q.register(run,'main',topic(v4));t=inv.assign(run,'main','worker',c['obligation_ids'])
    job=question_jobs(run.manifest,c['scope_units'][0],'aggregate')[0]
    class Native:
        async def call_tool(self,name,args,**kwargs):
            row={'site':'DE','month_dt':job['months'][0]+'-01','platform':'Amazon','spus':1,'nrows':1,'units':10,'amount':100,'total_rows':1}
            return {'success':True,'data':[row],'row_count':1,'metadata':{'query':args['sql']}}
    rec=asyncio.run(execute_job(run,Native(),job));assert rec['execution_status']=='success'
    a=answer(run,t)
    a['proof_jobs'] += [{'evidence_id':'main:'+e,'job_id':job['job_id'],'record_sha256':digest(rec)} for e in rec['evidence_ids']]
    path=_path(run,job['job_id'],rec['plan_sha256'],'plan')
    assert path.is_file() and not (run.path/'records'/(job['job_id']+'-plan.json')).exists()
    if damage:
        # A valid-looking flat legacy plan cannot replace this native attempt.
        write(run.path/'records'/(job['job_id']+'-plan.json'),job)
        if damage=='missing':path.unlink()
        else:write(path,{**job,'sql':job['sql']+' LIMIT 1'})
        with pytest.raises((ValueError,OSError)):submit(run,t,a)
    else:
        submit(run,t,a);inv.review(run,'main',t['task_id'],main_review(run,t))
        assert inv.audit(run)['verified']==1


def test_whole_site_lineage_cannot_replace_subtree_denominator(v4):
    run,index=v4;c=q.register(run,'main',topic(v4));ob=q.obligations(run)[c['obligation_ids'][0]]
    unit=dict(c['scope_units'][0],anchor_months=['2024-02'],anchor_paths=[[]])
    jobs=question_jobs(run.manifest,unit,'lineage')
    for job in jobs:assert verify_question_job(run.manifest,job)
    with pytest.raises(ValueError,match='未覆盖'):
        q._verify_scope_coverage(run,{'obligation_id':ob['obligation_id'],'scope':ob['scope']},[(run,j) for j in jobs])

@pytest.mark.parametrize('change',['scope','component','check','calculation','premature','self_approval'])
def test_incomplete_or_fabricated_answer_cannot_close(v4,change):
    run,index=v4;c=q.register(run,'main',topic(v4));t=inv.assign(run,'main','worker',c['obligation_ids']);a=answer(run,t)
    if change=='scope':a['scope']=dict(a['scope'],months=['2024-02'])
    if change=='component':a['component_responses']=[]
    if change=='check':a['checks'].pop('conservation')
    if change=='calculation':a['calculations'][0]['result']=100
    if change=='premature':a['quality_result']='unresolved'
    if change=='self_approval':a['status_dimensions']['review']='approved'
    with pytest.raises(ValueError):submit(run,t,a)
    assert not inv.audit(run)['substantive_complete']


def test_major_countercomponent_cannot_be_deleted(v4):
    run,index=v4;c=q.register(run,'main',topic(v4));v=topic(v4);v.update(case_id=c['case_id'],previous_case_version=1)
    v['scope_units'][0]['required_components']=[]
    with pytest.raises(ValueError,match='反向分量'):q.register(run,'main',v)


def test_active_task_not_cancelled_by_scope_edit(v4):
    run,index=v4;c=q.register(run,'main',topic(v4));inv.assign(run,'main','worker',c['obligation_ids']);v=topic(v4);v.update(case_id=c['case_id'],previous_case_version=1)
    with pytest.raises(ValueError,match='活跃'):q.register(run,'main',v)


def test_review_invalidated_by_topic_version(v4):
    run,index=v4;c=q.register(run,'main',topic(v4));t=inv.assign(run,'main','worker',c['obligation_ids']);submit(run,t,answer(run,t));inv.review(run,'main',t['task_id'],main_review(run,t))
    v=topic(v4);v.update(case_id=c['case_id'],previous_case_version=1);v['scope_units'][0]['next_check']='新增证据后核对另一已知量额边界。';q.register(run,'main',v)
    assert inv.audit(run)['errors'] and not inv.audit(run)['substantive_complete']


def test_reading_hash_is_not_reading_credit(v4):
    run,index=v4
    assert q.reading_status(run,index)['complete']==0
    ref=topic(v4)['origin_refs'][0]
    payload={'binding_sha256':index['binding_sha256'],'partition_ids':['DE:time_series'],'status':'partial','judgment':'仅阅读了该事实，尚未覆盖完整轨迹。','read_method':'精确读取原始定位。','unread_scope':'其余分区','independence_disclosure':'合成测试数据。','source_refs':[ref]}
    artifact=run.path/'reader-original.md';artifact.write_text('已核对所引事实，部分轨迹尚未读完。')
    payload['reader_artifacts']=[{'path':str(artifact),'sha256':digest(artifact.read_bytes())}]
    q.record_reading(run,'reader',payload)
    assert q.reading_status(run,index)['complete']==0
    payload.update(status='complete',unread_scope='none');q.record_reading(run,'reader',payload)
    assert q.reading_status(run,index)['complete']==1
    p=run.path/'panorama/facts-DE.json';v=read(p);v[0]['values']['units']=999;write(p,v)
    with pytest.raises(ValueError):panorama.load(run)


def test_hard_failure_cannot_vanish_without_candidate(v4):
    run,index=v4;j=next(j for j in plan(run.manifest) if j['family']=='mapping');rs=rows_for(run,j);rs[0]['value_mismatch_keys']=1;record(run,j,rs);panorama.build(run)
    state=q.coverage_status(run)
    assert state['unowned_failure_ids'] and not state['hard_failure_ownership_complete']
    assert not inv.all_obligations(run) # ownership gate, not automatic per-row deep debts


def test_query_shards_do_not_create_business_obligations(v4):
    run,index=v4;c=q.register(run,'main',topic(v4));unit=c['scope_units'][0]
    jobs=question_jobs(run.manifest,unit);assert len(jobs)==4 and len(inv.all_obligations(run))==1
    for j in jobs:assert verify_question_job(run.manifest,j)
    other=deepcopy(unit);other['event_months']=['2024-01'];other['background_months']=[]
    assert set(j['job_id'] for j in question_jobs(run.manifest,other))<=set(j['job_id'] for j in jobs)
    j=deepcopy(jobs[0]);j['sql']+=' LIMIT 1'
    with pytest.raises(ValueError):verify_question_job(run.manifest,j)


def test_raw_selection_uses_exists_and_full_placeholder_identity(v4):
    run,index=v4;unit=topic(v4)['scope_units'][0];unit['paths']=[['a']]
    raw=next(j for j in question_jobs(run.manifest,unit) if j['layer']=='raw')
    assert 'EXISTS' in raw['sql'] and 's.product_id <=> q.product_id' in raw['sql'] and 's.platform <=> q.platform' in raw['sql']
    assert 'TRIM(q.sku_id)' in raw['sql']


def test_sampling_includes_every_signaled_cell(v4):
    run,index=v4;q.freeze_discovery(run,{'independence_disclosure':'测试发现固定后抽查。'})
    write(run.path/'candidates.json',[{'site':'DE','month':mm,'path':'["a","b","c"]'} for mm in ('2024-01','2024-02')])
    p=q.prepare_sampling(run)['payload'];assert len(p['population'])==2 and len(p['selected_cell_ids'])==2


@pytest.mark.parametrize('field',['paths','site','path_layer','required_checks'])
def test_scope_reduction_rejected(v4,field):
    run,index=v4;c=q.register(run,'main',topic(v4));v=topic(v4);v.update(case_id=c['case_id'],previous_case_version=1)
    if field=='paths':v['scope_units'][0][field]=[['unrelated']]
    if field=='site':v['scope_units'][0][field]='US'
    if field=='path_layer':v['scope_units'][0][field]='raw'
    if field=='required_checks':v['scope_units'][0][field]=['scope','identity','counterevidence','impact']
    with pytest.raises(ValueError):q.register(run,'main',v)


def test_full_query_of_one_layer_is_not_full_question_evidence(v4):
    run,index=v4;c=q.register(run,'main',topic(v4));t=inv.assign(run,'main','worker',c['obligation_ids']);a=answer(run,t);a['proof_jobs']=a['proof_jobs'][:1]
    with pytest.raises(ValueError,match='未覆盖'):submit(run,t,a)


def test_major_negative_needs_actual_recomputation(v4):
    run,index=v4;c=q.register(run,'main',topic(v4));t=inv.assign(run,'main','worker',c['obligation_ids']);a=answer(run,t);a['component_responses'][0].pop('calculation_ids')
    with pytest.raises(ValueError,match='分量'):submit(run,t,a)


def test_deep_case_needs_directed_population(v4):
    run,index=v4;v=topic(v4);v['scope_units'][0]['stage']='deep_review';c=q.register(run,'main',v);t=inv.assign(run,'main','worker',c['obligation_ids'])
    with pytest.raises(ValueError,match='深查'):submit(run,t,answer(run,t))


def test_hard_failure_must_belong_to_actual_path(v4):
    run,index=v4;j=next(j for j in plan(run.manifest) if j['family']=='category' and j['level']==3);rs=rows_for(run,j);rs[1]['invalid_rows']=1;record(run,j,rs);ix=panorama.build(run)
    fail=next(c for c in ix['constraints'] if c['rule']=='valid_values' and c['state']=='failed')
    v=topic((run,ix));v['scope_units'][0]['paths']=[['unrelated']];v['constraint_ids']=[fail['check_id']]
    with pytest.raises(ValueError,match='范围'):q.register(run,'main',v)


def test_query_after_end_fingerprint_blocks_completion(v4):
    run,index=v4
    for j in plan(run.manifest,True):
        record(run,j,rows_for(run,j));p=run.path/'records'/('job-'+j['job_id']+'.json');v=read(p);v.update(started_at='2020-01-01T00:00:00+00:00',finished_at='2020-01-01T00:00:01+00:00');write(p,v)
    run.event('tool.started',parent_id='late-query')
    assert any('data_query_after_final_fingerprint' in x for x in q.validate(run,False)['errors'])
