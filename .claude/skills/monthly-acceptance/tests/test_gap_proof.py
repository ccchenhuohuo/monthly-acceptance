"""Complete gap populations reuse the diagnostic SQLite/evidence fixtures."""
import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from acceptance_core import Run,canonical,digest,read,write
from acceptance_diagnostics import diagnostic_job
from acceptance_gap_proof import verify_gap_proof
from acceptance_queries import job as baseline_job
from test_diagnostics import db,manifest,TARGET,OTHER,RAW,add,observation,run_sql
from test_execution import record


def simple_run(directory,manifest):
    directory.mkdir()
    # This fixture exercises the frozen candidate-led 3.1 proof contract.
    manifest.pop('architecture',None);manifest['method_version']='3.1.0'
    manifest['policy'].pop('architecture',None);manifest['policy']['version']='3.1.0'
    write(directory/'run.json',{**manifest,'run_id':directory.name,'method_version':'3.1.0',
                               'inputs':{},'policy_hash':digest(manifest['policy'])})
    for folder in ('records','evidence','queries'):(directory/folder).mkdir()
    return Run(directory)


def populate(db):
    class Percentile:
        def __init__(self):self.values=[];self.percentile=.5
        def step(self,value,percentile):
            self.percentile=percentile
            if value is not None:self.values.append(value)
        def finalize(self):
            return sorted(self.values)[int((len(self.values)-1)*self.percentile)] if self.values else None
    db.create_aggregate('PERCENTILE_APPROX',2,Percentile)
    add(db,observation(month_dt='2024-04-01',product_id='old-april-only',sku_id='old-sku',count=99,discount_sales=9900))
    for pid,units,amount in [('p1',10,100),('p2',3,300)]:
        add(db,observation(month_dt='2024-05-01',product_id=pid,sku_id='sku-'+pid,
                           count=units,discount_sales=amount,discount_price=amount/units))
    add(db,observation(month_dt='2024-06-01',product_id='p1-new-parent',sku_id='sku-p1',count=8,discount_sales=80,
                       stdcategory1=OTHER[0],stdcategory2=OTHER[1],stdcategory3=OTHER[2],sub_category='Adapters'))
    for pid,sku,units,amount in [('p1-new-parent','sku-p1',9,90),('p2','sku-p2',4,400)]:
        add(db,observation(month_dt='2024-07-01',product_id=pid,sku_id=sku,
                           count=units,discount_sales=amount,discount_price=amount/units))


def build(parent,worker,db,manifest,kind):
    raw=kind=='raw_presence_gap';level='raw' if raw else 3;path=canonical(RAW if raw else TARGET)
    source=baseline_job(manifest,'raw_paths','cn') if raw else baseline_job(manifest,'category','cn',level=3)
    record(parent,source,run_sql(db,manifest,source['sql']))
    candidate={'candidate_id':'gap-fixture','kind':kind,'site':'cn','path':path,'level':None if raw else 3,
               'month':'2024-06','comparison':None,'facts':{'first_observed':'2024-04','last_observed':'2024-07'},
               'source_jobs':[source['job_id']],'evidence_ids':parent.job_record(source['job_id'])['evidence_ids']}
    coverage=diagnostic_job(manifest,'source_coverage','cn',level,path,'2024-05','2024-07')
    record(worker,coverage,run_sql(db,manifest,coverage['sql']))
    answer={'gap_proof':{'source_coverage_job_id':coverage['job_id'],'entity_assessments':[{
        'product_ids':['p1','p2'],'status':'verified','reason':'Each previous SKU was traced in every later month in this synthetic fixture.',
        'counterevidence':'July restoration does not prove June market sales were zero or that capture was complete.',
        'evidence_ids':worker.job_record(coverage['job_id'])['evidence_ids']}]}}
    return {'parent':parent,'worker':worker,'candidate':candidate,'answer':answer,'source':source,'coverage':coverage,'kind':kind}


@pytest.fixture(params=['raw_presence_gap','std_presence_gap'])
def gap(request,db,manifest,tmp_path):
    manifest['policy']['page_size']=2
    parent=simple_run(tmp_path/'parent',manifest);worker=simple_run(tmp_path/'worker',manifest)
    populate(db)
    return build(parent,worker,db,manifest,request.param)


def compute(gap,manifest,**kwargs):
    return verify_gap_proof(manifest,gap['candidate'],gap['answer'],gap['worker'],gap['parent'].get_evidence,**kwargs)


def test_gap_uses_latest_pre_gap_observation_and_keeps_later_recovery(gap,manifest):
    result=compute(gap,manifest,check_claims=False)
    assert result['anchors']=={'month':'2024-05','spus':2,'nrows':2,'units':13,'amount':400}
    assert result['scope']['signal_month']=='2024-06' and result['scope']['current_month']=='2024-07'
    assert result['seed_count']==2 and result['expected_destination_cells']==result['verified_destination_cells']==8
    assert result['destination_state_counts']['no_observation']==2
    assert result['destination_state_counts']['same_sku_other_parent']==4
    assert result['cohort']=={'all':2,'verified':2,'blocked_external':0,'not_assessed':0}
    assert all(v['unexplained']==0 for v in result['computed_impact'].values())
    assert any(e.startswith('main:') for e in result['evidence_ids'])
    assert any(not e.startswith('main:') for e in result['evidence_ids'])
    gap['answer']['impact']=result['computed_impact']
    assert compute(gap,manifest)['computed_impact']==result['computed_impact']


def test_a_single_reviewed_seed_cannot_hide_the_remaining_exposure(gap,manifest):
    gap['answer']['gap_proof']['entity_assessments'][0]['product_ids']=['p1']
    result=compute(gap,manifest,check_claims=False)
    assert result['unreviewed_product_ids']==['p2']
    assert result['computed_impact']['spus']['unexplained']==1
    assert result['computed_impact']['units']['unexplained']==3
    assert result['computed_impact']['amount']['unexplained']==300
    assert result['computed_impact']['bands']['unexplained']==50
    gap['answer']['impact']=result['computed_impact']
    for impact in gap['answer']['impact'].values():impact['unexplained']=0
    with pytest.raises(ValueError,match='自报分母不符'):compute(gap,manifest)


def test_explicit_source_coverage_shard_is_not_a_complete_gap_proof(gap,manifest,db):
    c=gap['candidate'];level='raw' if gap['kind']=='raw_presence_gap' else 3
    job=diagnostic_job(manifest,'source_coverage','cn',level,c['path'],'2024-05','2024-07',['p1'])
    record(gap['worker'],job,run_sql(db,manifest,job['sql']))
    gap['answer']['gap_proof']['source_coverage_job_id']=job['job_id']
    with pytest.raises(ValueError,match='完整人口'):compute(gap,manifest,check_claims=False)


def test_disappearing_entire_seed_is_detected_by_baseline_conservation(gap,manifest):
    worker,job=gap['worker'],gap['coverage']
    rows=[r for r in worker.job_rows(job['job_id']) if r['seed_product_id']=='p1']
    record(worker,job,rows)
    with pytest.raises(ValueError,match='完整种子.spus'):compute(gap,manifest,check_claims=False)


def test_missing_one_natural_month_layer_is_not_silently_filled_with_zero(gap,manifest):
    worker,job=gap['worker'],gap['coverage']
    rows=[r for r in worker.job_rows(job['job_id']) if not(r['seed_product_id']=='p2' and r['month_dt']=='2024-06-01' and r['source_layer']=='raw')]
    record(worker,job,rows)
    with pytest.raises(ValueError,match='后续自然月'):compute(gap,manifest,check_claims=False)


def test_no_observation_is_never_a_zero_sales_record(gap,manifest):
    worker,job=gap['worker'],gap['coverage'];rows=worker.job_rows(job['job_id'])
    row=next(r for r in rows if r['observation_state']=='no_observation')
    row['amount']=0;row['units']=0
    record(worker,job,rows)
    with pytest.raises(ValueError,match='0销量'):compute(gap,manifest,check_claims=False)


def test_latest_future_observation_cannot_be_used_as_a_prior_anchor(gap,manifest,db):
    c=gap['candidate'];level='raw' if gap['kind']=='raw_presence_gap' else 3
    job=diagnostic_job(manifest,'source_coverage','cn',level,c['path'],'2024-04','2024-07')
    record(gap['worker'],job,run_sql(db,manifest,job['sql']))
    gap['answer']['gap_proof']['source_coverage_job_id']=job['job_id']
    with pytest.raises(ValueError,match='最近观测月'):compute(gap,manifest,check_claims=False)


def test_baseline_pages_must_all_be_present_even_if_anchor_was_already_found(gap,manifest):
    assert len(gap['candidate']['evidence_ids'])>1
    gap['candidate']['evidence_ids'].pop()
    with pytest.raises(ValueError,match='缺页'):compute(gap,manifest,check_claims=False)


def test_no_prior_observation_cannot_create_a_zero_anchor(gap,manifest):
    gap['candidate']['month']='2024-01'
    with pytest.raises(ValueError,match='无法构成全量锚点'):compute(gap,manifest,check_claims=False)


def test_existing_signal_month_path_observation_invalidates_the_gap_assumption(gap,manifest,db):
    add(db,observation(month_dt='2024-06-01',product_id='actually-present',sku_id='present-sku'))
    refreshed=build(gap['parent'],gap['worker'],db,manifest,gap['kind'])
    with pytest.raises(ValueError,match='实际已有同路径观测'):compute(refreshed,manifest,check_claims=False)


def test_blocked_and_not_assessed_seeds_stay_in_the_residual(gap,manifest):
    group=gap['answer']['gap_proof']['entity_assessments'][0]
    group['product_ids']=['p1'];group['status']='blocked_external'
    result=compute(gap,manifest,check_claims=False)
    assert result['cohort']=={'all':2,'verified':0,'blocked_external':1,'not_assessed':1}
    assert result['unreviewed_product_ids']==['p1','p2']
    assert all(v['unexplained']==v['exposure'] for v in result['computed_impact'].values())


def test_invalid_source_rows_are_kept_in_the_unknown_price_band(gap,manifest,db):
    add(db,observation(month_dt='2024-05-01',product_id='p1',sku_id='sku-p1',count=-1,discount_sales=-5))
    refreshed=build(gap['parent'],gap['worker'],db,manifest,gap['kind'])
    result=compute(refreshed,manifest,check_claims=False)
    assert result['anchors']['nrows']==3 and result['seed_count']==3
    assert any(r['band']==-1 and r['spus']==1 for r in result['band_population'])


def test_repeated_seed_context_cannot_change_across_followup_rows(gap,manifest):
    worker,job=gap['worker'],gap['coverage'];rows=worker.job_rows(job['job_id'])
    rows[0]['seed_amount_context']+=1
    record(worker,job,rows)
    with pytest.raises(ValueError,match='上下文发生变化'):compute(gap,manifest,check_claims=False)


def test_submitted_proof_hashes_all_typed_job_and_evidence_files(gap,manifest):
    result=compute(gap,manifest,check_claims=False)
    assert result['job_files_sha256'] and result['source_evidence_sha256']
    assert all(digest((gap['worker'].path/p).read_bytes())==sha for p,sha in result['job_files_sha256'].items())
    assert all(digest(gap['parent'].get_evidence(eid))==sha for eid,sha in result['source_evidence_sha256'].items())


def test_entities_without_counterevidence_cannot_be_marked_verified(gap,manifest):
    gap['answer']['gap_proof']['entity_assessments'][0]['counterevidence']=''
    with pytest.raises(ValueError,match='反证'):compute(gap,manifest,check_claims=False)


def test_complete_unknown_sku_cells_are_explicit_but_cannot_be_verified(gap,manifest,db):
    db.execute("UPDATE raw SET sku_id=NULL WHERE product_id='p2' AND month_dt='2024-05-01'")
    db.execute("UPDATE std SET sku_id=NULL WHERE product_id='p2' AND month_dt='2024-05-01'")
    refreshed=build(gap['parent'],gap['worker'],db,manifest,gap['kind'])
    with pytest.raises(ValueError,match='不能完成去向核实'):compute(refreshed,manifest,check_claims=False)
    refreshed['answer']['gap_proof']['entity_assessments'][0]['status']='blocked_external'
    result=compute(refreshed,manifest,check_claims=False)
    assert result['untraceable_product_ids']==['p2']
    assert result['destination_state_counts']['untraceable_seed_identity']==4
