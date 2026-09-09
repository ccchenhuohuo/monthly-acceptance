import asyncio
import copy
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from acceptance_core import Run,read,write,digest,months,shift,readonly_sql,quoted
from legacy_fixture import create_run
from acceptance_queries import plan,paginated
from acceptance_analysis import analyze,rebuild
from acceptance_validate import validate
from acceptance_transport import execute_job


def rows_for(run,j):
    ms=months(run.manifest['starts'].get(j['site'],'2024-01'),run.manifest['data_month'])
    f=j['family'];path=json.dumps(['a','b','c'][:j.get('level',0)],ensure_ascii=False,separators=(',',':'))
    if j.get('year'):ms=[m for m in ms if int(m[:4])==j['year']]
    if f=='release':return [{'release_id':'r1','status':'published'}]
    if f=='sample':return [{**({'platform':'Amazon'} if 'platform' in j['key_fields'] else {}),'path':'["a","b","c"]','product_id':'p1','title':'Example camera accessory','brand':'Example','raw_category':'Electronics','units':10,'amount':100}]
    result=[]
    for mm in ms:
        base={'month_dt':mm+'-01'}
        if f=='fingerprint':base.update(nrows=1,spus=1,units=10,amount=100,invalid_keys=0,invalid_rows=0)
        if f=='category':base.update(path=path,nrows=1,spus=1,units=10,amount=100,invalid_rows=0,missing_product_rows=0,priced_spus=1,p10=10,p50=10,p90=10)
        if f=='bands':base.update(path=path,band=1,spus=1,units=10,amount=100)
        if f=='movement':base.update(path=path,base_entities=1,current_entities=1,entered=0,exited=0,base_amount=100,current_amount=100,base_units=10,current_units=10,positive_amount=0,negative_amount=0,positive_units=0,negative_units=0)
        if f=='raw_paths':base.update(path='["Electronics"]',spus=1,nrows=1,units=10,amount=100)
        if f=='mapping':base.update(raw_duplicate_rows=0,std_duplicate_rows=0,raw_only_keys=0,std_only_keys=0,matched_keys=1,value_mismatch_keys=0)
        result.append(base)
    return result


def record(run,j,rows):
    size=run.policy['page_size'];eids=[]
    for offset in range(0,max(1,len(rows)),size):
        sql=paginated(j,size,offset);page=[{**r,'total_rows':len(rows)} for r in rows[offset:offset+size]]
        started=run.event('tool.started',parent_id=j['job_id'],query_hash=digest(sql),offset=offset,page_size=size)
        eid=run.evidence({'request':{'sql':sql,'query_hash':digest(sql),'offset':offset,'page_size':size},
            'response':{'success':True,'data':page,'row_count':len(page),'metadata':{'query':sql}}},kind='fixture',parent_event=started)
        run.event('tool.finished',parent_event_id=started,status='success',evidence_id=eid);eids.append(eid)
    write(run.path/'records'/('job-'+j['job_id']+'.json'),{**{k:v for k,v in j.items() if k!='sql'},'sql_hash':digest(j['sql']),
        'execution_status':'success','evidence_ids':eids,'expected_rows':len(rows),'row_count':len(rows),'started_at':'2099-01-01T00:00:00','finished_at':'2099-01-01T00:00:01'})


@pytest.fixture
def completed(tmp_path):
    base=tmp_path/'验收';base.mkdir();(base/'config.yaml').write_text('站点起始月份: {DE: 2024-01}\n');(base/'项目范围.md').write_text('Electronics')
    write(base/'policy-overrides.json',{'page_size':5})
    skill=Path(__file__).resolve().parents[1];run=Run(create_run(base,skill,'2025-02'));jobs=plan(run.manifest);run.register_plan(jobs)
    for j in jobs:record(run,j,rows_for(run,j))
    analyze(run)
    s=read(run.path/'samples.json');write(run.path/'sample-reviews.json',[{'sample_ids':[v['sample_id'] for v in s],'status':'checked','reason':'核对了固定样本的商品身份与上下文','evidence_ids':s[0]['evidence_ids']}])
    for j in plan(run.manifest,True):record(run,j,rows_for(run,j))
    write(run.path/'report-content.json',{'summary':['测试范围内的固定检查完成，输入限制单独披露。'],'findings':[]})
    return run


def test_baseline_complete_does_not_claim_investigation_complete(completed):
    v=validate(completed,False)
    assert v['execution_state']=='incomplete' and v['business_verdict']=='undetermined'
    assert 'business_investigation_incomplete' in v['errors']
    assert v['baseline_jobs']['expected']==v['baseline_jobs']['verified']
    assert v['coverage']['mom:comparable']==13*4
    assert v['coverage']['yoy:comparable']==2*4


def test_removing_entire_movement_family_is_detected(completed):
    for j in plan(completed.manifest):
        if j['family']=='movement':(completed.path/'records'/('job-'+j['job_id']+'.json')).unlink()
    v=validate(completed,False)
    assert v['execution_state']=='incomplete'
    assert v['baseline_jobs']['verified']<v['baseline_jobs']['expected']
    assert 'baseline_incomplete' in v['errors']


def test_shrinking_plan_does_not_shrink_denominator(completed):
    p=read(completed.path/'plan.json');p['jobs']=[j for j in p['jobs'] if j.get('lag')!=12];p['hash']=digest(p['jobs']);write(completed.path/'plan.json',p)
    assert 'plan_incomplete_or_changed' in validate(completed,False)['errors']


def test_gap_is_not_previous_observed_month(completed):
    for j in plan(completed.manifest):
        if j['family'] in ['category','bands']:
            record(completed,j,[r for r in rows_for(completed,j) if r['month_dt']!='2024-02-01'])
    value=rebuild(completed)
    checks=[c for c in value['checks'] if c['family']=='mom' and c['month']=='2024-03']
    assert len(checks)==4 and all(c['state']=='no_baseline' and c['base_month']=='2024-02' for c in checks)
    assert not [c for c in value['candidates'] if c['kind']=='result_change' and c['month']=='2024-03' and c['comparison']=='mom']


def test_missing_evidence_page_cannot_pass(completed):
    j=next(j for j in plan(completed.manifest) if j['family']=='category');r=completed.job_record(j['job_id']);r['evidence_ids'].pop(1)
    write(completed.path/'records'/('job-'+j['job_id']+'.json'),r)
    assert any(e.startswith('invalid_job:') for e in validate(completed,False)['errors'])


def test_duplicate_page_keys_detected(completed):
    j=next(j for j in plan(completed.manifest) if j['family']=='category');rs=rows_for(completed,j);rs[5]=rs[0].copy();record(completed,j,rs)
    with pytest.raises(ValueError,match='重复'):completed.job_rows(j['job_id'])


def test_frozen_scope_and_policy_cannot_be_silently_changed(completed):
    completed.policy['signals']['spu_change']=.99
    assert 'policy_hash_mismatch' in completed.verify_inputs()
    assert 'policy_differs_from_frozen_source' in validate(completed,False)['errors']


def test_modified_evidence_invalidates_hash(completed):
    p=next((completed.path/'evidence').glob('*.json'));p.write_text('{}')
    with pytest.raises(ValueError,match='哈希'):completed.get_evidence(p.stem)


@pytest.mark.parametrize('sql',['DELETE FROM internal.flywheel.monthly_sales_wide_new','WITH p AS (SELECT 1) INSERT INTO a SELECT * FROM p','EXPLAIN DELETE FROM a','SELECT 1; SELECT 2','SELECT 1 INTO OUTFILE \'/tmp/a\'','SELECT 1 -- mask','SELECT \'unterminated'])
def test_readonly_guard(sql):
    with pytest.raises(ValueError):readonly_sql(sql)


def test_sql_literals_and_calendar():
    for value in ["x'; DELETE FROM t; --",'a\\b',"a -- example"]:
        assert readonly_sql('SELECT '+quoted(value))
    assert shift('2024-01',-1)=='2023-12' and shift('2024-02',12)=='2025-02'


def test_page_order_does_not_change_candidates(completed):
    original=rebuild(completed)
    for j in plan(completed.manifest):record(completed,j,list(reversed(rows_for(completed,j))))
    after=rebuild(completed)
    assert [c['candidate_id'] for c in original['candidates']]==[c['candidate_id'] for c in after['candidates']]
    assert [(c['check_id'],c['state']) for c in original['checks']]==[(c['check_id'],c['state']) for c in after['checks']]


def test_transport_stops_on_truncation_and_records_failure(completed,capsys):
    class Fake:
        async def call_tool(self,name,args,**kw):
            return {'success':True,'data':[{'month_dt':'2024-01-01','total_rows':10}],'row_count':1,'metadata':{'query':args['sql']}}
    j=copy.deepcopy(plan(completed.manifest)[1]);j['job_id']='truncated'
    result=asyncio.run(execute_job(completed,Fake(),j))
    assert result['execution_status']=='failed' and '截断' in result['error']
    assert any(json.loads(l)['type']=='job.finished' and json.loads(l).get('status')=='failed' for l in completed.events_path.read_text().splitlines())


def test_sealing_incomplete_run_never_means_pass(completed):
    from acceptance_render import render
    (completed.path/'sample-reviews.json').unlink()
    v=validate(completed);render(completed)
    with pytest.raises(ValueError,match='不能封存'):completed.seal(v)
    assert not (completed.path/'sealed.json').exists()
    assert v['execution_state']=='incomplete' and v['business_verdict']=='undetermined'
    completed.event('continue.investigation')


def test_missing_baseline_shows_unknown_population(completed):
    j=next(j for j in plan(completed.manifest) if j['family']=='movement')
    (completed.path/'records'/('job-'+j['job_id']+'.json')).unlink()
    v=validate(completed,False)
    assert v['candidates']['expected'] is None and v['semantic_sample']['expected'] is None
    assert not v['candidate_population_verified']
    assert not any(e.startswith('unknown_or_duplicate_sample_review:') for e in v['errors'])


def test_null_comparison_truth_table():
    import sqlite3
    from acceptance_queries import null_mismatch
    conn=sqlite3.connect(':memory:')
    for left,right,expected in [(1,1,0),(None,None,0),(None,1,1),(1,None,1),(0,0,0)]:
        result=conn.execute('SELECT '+null_mismatch('l','r')+' FROM (SELECT ? AS l,? AS r)',(left,right)).fetchone()[0]
        assert result==expected


def test_cancelled_query_has_terminal_records(completed):
    class Interrupted:
        async def call_tool(self,*a,**kw):raise asyncio.CancelledError()
    j=copy.deepcopy(plan(completed.manifest)[1]);j['job_id']='cancelled-test'
    with pytest.raises(asyncio.CancelledError):asyncio.run(execute_job(completed,Interrupted(),j))
    assert completed.job_record(j['job_id'])['execution_status']=='cancelled'
    assert any(json.loads(l).get('status')=='cancelled' for l in completed.events_path.read_text().splitlines())


def test_dashboard_preserves_all_candidates_without_quadratic_group_repetition(completed):
    import re
    from acceptance_render import dashboard
    eid=read(completed.path/'samples.json')[0]['evidence_ids'][0]
    candidates=[{'candidate_id':'display-'+str(i),'site':'DE','path':'["a"]','month':'2025-02','kind':'display_fixture','priority':'high','evidence_ids':[eid]} for i in range(500)]
    write(completed.path/'candidates.json',candidates)
    did=completed.decide({'candidate_ids':[c['candidate_id'] for c in candidates],'title':'共同待补证事项 </script>','facts':'所有完整候选保留。','reason':'显示分组不能改变分母。','next_action':'补充证据。','scope':'测试','evidence_ids':[eid],'counterevidence':'尚不能判断原因。','status':'needs_evidence','business_verdict':'undetermined'})
    dashboard(completed,{'run_id':'fixture'}, {'summary':['完整候选保留']})
    page=(completed.path/'audit.html').read_text()
    embedded=re.search(r'<script id="data" type="application/json">(.*?)</script>',page,re.S).group(1)
    data=json.loads(embedded)
    assert len(data['candidates'])==500 and len(data['decisions'])==1
    assert all(c['decision_id']==did and 'disposition' not in c for c in data['candidates'])
    assert data['decisions'][did]['candidate_count']==500 and 'candidate_ids' not in data['decisions'][did]
    assert len(data['samples'])==len(read(completed.path/'samples.json'))
    assert '\\u003c/script>' in embedded


def test_cached_references_still_detect_evidence_changed_after_first_read(completed,monkeypatch):
    eid=completed.evidence({'observation':'original'},kind='review_fixture')
    samples=read(completed.path/'sample-reviews.json');samples[0]['evidence_ids'].append(eid);write(completed.path/'sample-reviews.json',samples)
    write(completed.path/'report-content.json',{'summary':['验证最终哈希复查'],'findings':[{'title':'共享证据','scope':'fixture','facts':['观测'],'counterevidence':'范围受限','assessment':'待核','action':'复查','evidence_ids':[eid]} for _ in range(100)]})
    original=completed.get_evidence;reads=[]
    def changing(e):
        value=original(e)
        if e==eid:
            reads.append(e)
            if len(reads)==1:(completed.path/'evidence'/(e+'.json')).write_text('{"observation":"changed"}')
        return value
    monkeypatch.setattr(completed,'get_evidence',changing)
    result=validate(completed,False)
    assert any(eid in e and '哈希' in e for e in result['errors'])
    assert len(reads)==1  # the final read fails integrity validation before returning
