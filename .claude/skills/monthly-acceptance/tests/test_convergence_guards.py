"""V4 rejects unsupported reading and historical layer completion claims."""
from copy import deepcopy
from pathlib import Path
import sys
import asyncio
import re
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from acceptance_core import Run,create_run,read,write,digest
from acceptance_queries import plan
from test_execution import record,rows_for
from test_questions_v4 import v4,topic
import acceptance_questions as q
import acceptance_panorama as panorama


def reading(v4):
    run,index=v4
    return {'binding_sha256':index['binding_sha256'],'partition_ids':[p['partition_id'] for p in index['partitions']],
        'status':'complete','judgment':'已完成全景判读，保留原始判断。','read_method':'逐分区阅读连续轨迹。',
        'unread_scope':'none','independence_disclosure':'已披露参考历史，未声称盲测。','source_refs':topic(v4)['origin_refs']}


@pytest.mark.parametrize('artifacts',[None,[]])
def test_reading_requires_original_artifact(v4,artifacts):
    run,_=v4;value=reading(v4)
    if artifacts is not None:value['reader_artifacts']=artifacts
    with pytest.raises(ValueError,match='判读原始产物'):q.record_reading(run,'reader',value)
    assert q.reading_status(run)['complete']==0


def test_reading_artifact_is_bound_without_forcing_blindness(v4,tmp_path):
    run,index=v4;value=reading(v4);p=tmp_path/'reading.md';p.write_text('连续全景原始判读与不同解释。')
    value['reader_artifacts']=[{'path':str(p),'sha256':digest(p.read_bytes())}]
    q.record_reading(run,'main',value)
    assert q.reading_status(run)['complete']==len(index['partitions'])
    p.write_text('改写判读。')
    with pytest.raises(ValueError,match='审后变更'):q.reading_status(run)


def regression_run(tmp_path,issue):
    base=tmp_path/'audit';base.mkdir()
    (base/'config.yaml').write_text('站点起始月份: {DE: 2024-01}\n')
    (base/'项目范围.md').write_text('Amazon DE historical regression fixture.')
    write(base/'policy-overrides.json',{'page_size':1})
    write(base/'open-issues.json',[{'issue_id':'historical-object','site':'DE','months':['2024-01'],**issue}])
    run=Run(create_run(base,Path(__file__).resolve().parents[1],'2024-02'))
    run.register_plan(plan(run.manifest))
    for job in plan(run.manifest):record(run,job,rows_for(run,job))
    panorama.build(run);q.prepare(run,'main')
    assert run.verify_inputs()==[]
    return run


def regression_value(run,raw_rows,std_rows):
    value={'check_id':'regression:open-issues.json:historical-object','reviewer_id':'main','investigator_id':'worker',
        'status':'passed','finding':'历史对象在两层两月均核验。','action':'读取精确对象源行。','counterevidence':'保留非目标对象区别。',
        'source_layers_verified':['raw','std'],'evidence_ids':[],'raw_locators':[]}
    for layer,rows in [('raw',raw_rows),('std',std_rows)]:
        from acceptance_transport import execute_job
        job={'job_id':'history-'+digest([layer,rows])[:24],'family':'history_object','site':'DE','entity':layer,
             'key_fields':['site','platform','product_id','sku_id','month'],
             'sql':'SELECT *, COUNT(*) OVER() AS total_rows FROM '+run.policy[layer+'_table']}
        class Native:
            async def call_tool(self,name,args,**kwargs):
                size,offset=map(int,re.search(r'LIMIT (\d+) OFFSET (\d+)$',args['sql']).groups())
                page=[{**row,'total_rows':len(rows)} for row in rows[offset:offset+size]]
                return {'success':True,'data':page,'row_count':len(page),'metadata':{'query':args['sql']}}
        rec=asyncio.run(execute_job(run,Native(),job));assert rec['execution_status']=='success'
        links=[]
        for eid in rec['evidence_ids']:
            for n,row in enumerate(run.get_evidence(eid)['response']['data']):
                links.append({'evidence_id':eid,'pointer':'/response/data/'+str(n),'record_sha256':digest(row),'fields':list(row),
                              'object_keys':row,'purpose':'历史复合身份与月份核对。'})
        value[layer+'_proof_jobs']=[{'evidence_id':eid,'job_id':job['job_id'],'record_sha256':digest(rec)} for eid in rec['evidence_ids']]
        value['evidence_ids'].extend(rec['evidence_ids']);value[layer+'_object_locators']=links;value['raw_locators'].extend(links)
    return value


def rows(product='P1',sku='S1'):
    return [{'site':'DE','platform':'Amazon','product_id':product,'sku_id':sku,'month':mm} for mm in ['2024-01','2024-02']]


@pytest.mark.parametrize('issue',[{'product_id':'P1'},{'product_ids':['P1']},{'product_id':'P1','sku_id':'S1'},
    {'product_ids':['P1'],'sku_ids':['S1']},{'sku_keys':[{'platform':'Amazon','product_id':'P1','sku_id':'S1'}]}])
def test_exact_objects_in_both_layers_and_months_pass(tmp_path,issue):
    run=regression_run(tmp_path,issue);value=regression_value(run,rows(),rows())
    assert q.record_baseline_review(run,'main',value)['status']=='passed'


@pytest.mark.parametrize('change',['missing_current','other_product','other_country','other_platform','sku_in_product_field','other_sku'])
def test_each_layer_must_cover_exact_history_identity(tmp_path,change):
    run=regression_run(tmp_path,{'product_id':'P1','sku_id':'S1'})
    raw=rows();std=rows()
    if change=='missing_current':std=std[:1]
    if change=='other_product':
        for row in std:row['product_id']='OTHER'
    if change=='other_country':
        for row in std:row['site']='US'
    if change=='other_platform':
        for row in std:row['platform']='Other'
    if change=='sku_in_product_field':
        for row in std:row.update(product_id='S1',sku_id='P1')
    if change=='other_sku':
        for row in std:row['sku_id']='S2'
    value=regression_value(run,raw,std)
    with pytest.raises(ValueError,match='各层回归缺精确'):q.record_baseline_review(run,'main',value)
    value.update(status='unavailable',limitation='新月精确对象证据缺失。',resume_condition='补齐各层精确对象源行。')
    assert q.record_baseline_review(run,'main',value)['status']=='unavailable'


@pytest.mark.parametrize('sku',[None,'',0])
def test_placeholder_sku_cannot_borrow_other_parent(tmp_path,sku):
    run=regression_run(tmp_path,{'sku_keys':[{'platform':'Amazon','product_id':'P1','sku_id':sku}]})
    with pytest.raises(ValueError,match='各层回归缺精确'):
        q.record_baseline_review(run,'main',regression_value(run,rows('P1',sku),rows('P2',sku)))
    assert q.record_baseline_review(run,'main',regression_value(run,rows('P1',sku),rows('P1',sku)))['status']=='passed'


@pytest.mark.parametrize('detail',[{'months':['2024-01']},{'first_observed_month':'2024-01'}])
def test_legacy_detail_historical_month_is_required(tmp_path,detail):
    run=regression_run(tmp_path,{'product_id':'P1','months':[],'legacy_detail':detail})
    with pytest.raises(ValueError,match='各层回归缺精确'):
        q.record_baseline_review(run,'main',regression_value(run,rows()[1:],rows()[1:]))
    assert q.record_baseline_review(run,'main',regression_value(run,rows(),rows()))['status']=='passed'


def test_no_explicit_historical_period_cannot_pass_on_new_month(tmp_path):
    run=regression_run(tmp_path,{'product_id':'P1','months':[]})
    with pytest.raises(ValueError,match='历史时期未明确'):
        q.record_baseline_review(run,'main',regression_value(run,rows()[1:],rows()[1:]))


@pytest.mark.parametrize('damage',['attachment_sql','missing_page','changed_receipt','missing_versioned_plan'])
def test_history_pass_requires_complete_native_execution(tmp_path,damage):
    run=regression_run(tmp_path,{'product_id':'P1'})
    value=regression_value(run,rows(),rows())
    proof=value['std_proof_jobs'][0];rec=run.job_record(proof['job_id'])
    if damage=='attachment_sql':
        original=value['std_object_locators'][0]
        eid=run.evidence(run.get_evidence(original['evidence_id']),kind='attachment')
        value['std_proof_jobs'][0]['evidence_id']=eid
        value['std_object_locators'][0]['evidence_id']=eid
    elif damage=='missing_page':
        value['std_proof_jobs']=value['std_proof_jobs'][:1]
    elif damage=='changed_receipt':
        rec['row_count']=0
        write(run.path/'records'/('job-'+proof['job_id']+'.json'),rec)
    else:
        from acceptance_job_versions import _path
        _path(run,proof['job_id'],rec['plan_sha256'],'plan').unlink()
    with pytest.raises((ValueError,OSError)):q.record_baseline_review(run,'main',value)


def test_history_review_invalidates_when_native_receipt_changes(tmp_path):
    run=regression_run(tmp_path,{'product_id':'P1'})
    value=q.record_baseline_review(run,'main',regression_value(run,rows(),rows()))
    proof=value['raw_proof_jobs'][0];record=run.job_record(proof['job_id'])
    record['expected_rows']=1
    write(run.path/'records'/('job-'+proof['job_id']+'.json'),record)
    with pytest.raises(ValueError):q.record_baseline_review(run,'main',value,_audit=True)
