"""Typed population evidence through real assignment, submission and main review.

SQLite observations are synthetic. The tests exercise production workflow and
proof gates without bypassing either or making live market judgments.
"""
import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from acceptance_core import Run,digest,read,write
from legacy_fixture import create_run
import acceptance_investigation as inv
from acceptance_investigation_proof import verify_result_proof
from test_diagnostics import db
from test_investigation import answer_for,approval
from test_investigation_proof import CAP,RACK,NORMAL,build,populate


@pytest.fixture
def integrated(tmp_path,db):
    base=tmp_path/'验收';base.mkdir()
    (base/'config.yaml').write_text('站点起始月份: {cn: 2024-01}\n')
    (base/'项目范围.md').write_text('Synthetic population and workflow integration fixture')
    write(base/'policy-overrides.json',{'page_size':2, 'version':'3.1.0',
          'review_gate':{'enabled':False}, 'case_workflow':{'enabled':False}, 'coverage_gate':{'enabled':False},
          'investigation_workflow':{'max_unexplained_ratio':0, 'max_unexplained_band_pp':0}})
    parent=Run(create_run(base,Path(__file__).resolve().parents[1],'2024-07'))
    populate(db)
    # Reuse the independent complete-table anchors from the population fixture.
    # The parent observations form the alarm; assigned workers still collect
    # their own typed drill, brand and full SKU evidence below.
    initial=build(parent,db,parent.manifest)
    candidate=initial['candidate']
    eid=parent.evidence({'synthetic_candidate_observation':candidate['facts']},kind='synthetic_baseline')
    candidate={**candidate,'priority':'high','evidence_ids':[eid]}
    write(parent.path/'candidates.json',[candidate]);write(parent.path/'samples.json',[])
    parent.event('independent_scan.frozen',candidate_hash=digest([candidate]),counts={'candidates':1,'samples':0})
    inv.prepare(parent,'/root')
    oid='candidate:'+candidate['candidate_id']
    task=inv.assign(parent,'/root','/root/population-worker',[oid])
    worker=Run(parent.path/task['worker_dir'])
    prepared=build(worker,db,parent.manifest)
    prepared['candidate']=candidate
    local_eid=worker.job_record(prepared['sku']['job_id'])['evidence_ids'][0]
    answer=answer_for(inv.all_obligations(parent)[oid],local_eid)
    answer['population_proof']=copy.deepcopy(prepared['answer']['population_proof'])
    answer['impact']=verify_result_proof(parent.manifest,candidate,answer,worker,check_claims=False)['computed_impact']
    payload={'task_id':task['task_id'],'agent_id':task['agent_id'],'attempt':task['attempt'],'answers':[answer]}
    write(worker.path/'submission.json',payload)
    return {**prepared,'parent':parent,'task':task,'oid':oid,'payload':payload}


def submit(case):
    return inv.submit(case['parent'],'/root',case['task']['task_id'])


def write_submission(case):
    write(case['worker'].path/'submission.json',case['payload'])


def reviewed_answer(case):
    parent,task=case['parent'],case['task']
    value=approval(parent,task)
    stored=read(inv.home(parent)/'submissions'/(task['task_id']+'.json'))
    answers={a['obligation_id']:a for a in stored['payload']['answers']}
    for entry in value['obligations']:
        oid=entry['obligation_id'];a=answers[oid]
        proof=stored['payload']['population_proofs'][oid]
        eids=set(entry['evidence_checked'])|set(proof['evidence_ids'])
        for group in a['population_proof']['entity_assessments']:
            eids.update(group['evidence_ids'])
        entry['evidence_checked']=sorted(eids)
        entry['entity_reviews']=[{'assessment_sha256':digest(group),'decision':'accept',
             'reason':'Read the exact synthetic SKU rows, full named contributions, cause claim and counterevidence for this group.'}
             for group in a['population_proof']['entity_assessments']]
    return value


def test_result_change_without_population_proof_cannot_submit(integrated):
    integrated['payload']['answers'][0].pop('population_proof')
    write_submission(integrated)
    with pytest.raises(ValueError,match='population_proof'):
        submit(integrated)
    assert inv.task_records(integrated['parent'])[integrated['task']['task_id']]['state']=='assigned'


def test_one_rack_review_cannot_forge_zero_unexplained_population(integrated):
    answer=integrated['payload']['answers'][0]
    answer['population_proof']['entity_assessments'][0]['product_ids']=[RACK]
    # Retaining the previous all-verified numbers recreates the actual original
    # failure: the small known defect must not discharge the rest of the class.
    assert all(metric['unexplained']==0 for metric in answer['impact'].values())
    write_submission(integrated)
    with pytest.raises(ValueError,match='自报分母不符'):
        submit(integrated)
    assert inv.audit(integrated['parent'])['verified']==0


def test_external_blocker_does_not_hide_other_uninvestigated_entities(integrated):
    answer=integrated['payload']['answers'][0]
    group=answer['population_proof']['entity_assessments'][0]
    group.update(product_ids=[RACK],status='blocked_external')
    answer['checks']['price_volume'].update(status='blocked_external',blocking={
        'type':'missing_external_record','required_record':'Historical source price capture for the fixture rack',
        'provider_role':'Source record owner','attempted_access':'Inspected all provided fixture files',
        'why_unavailable':'No historical price capture was supplied for this object',
        'decision_impact':'This one object remains unverified','resume_action':'Read the missing capture when supplied',
        'blocked_checks':['price_volume']})
    answer['business_verdict']='undetermined'
    answer['impact']=verify_result_proof(integrated['parent'].manifest,integrated['candidate'],answer,
                                         integrated['worker'],check_claims=False)['computed_impact']
    write_submission(integrated)
    with pytest.raises(ValueError,match='仍有未调查事项'):
        submit(integrated)


def test_main_review_must_read_each_entity_groups_reason_and_counterevidence(integrated):
    submit(integrated)
    value=reviewed_answer(integrated)
    value['obligations'][0].pop('entity_reviews')
    with pytest.raises(ValueError,match='逐实体组'):
        inv.review(integrated['parent'],'/root',integrated['task']['task_id'],value)
    assert inv.audit(integrated['parent'])['verified']==0


def test_main_review_cannot_skip_a_typed_proof_evidence_page(integrated):
    submit(integrated)
    value=reviewed_answer(integrated)
    value['obligations'][0]['evidence_checked'].pop()
    with pytest.raises(ValueError,match='全部动作证据'):
        inv.review(integrated['parent'],'/root',integrated['task']['task_id'],value)


def test_complete_candidate_proof_is_imported_and_reviewed_without_erasing_other_obligations(integrated):
    result=submit(integrated)
    parent,task=integrated['parent'],integrated['task']
    stored=read(inv.home(parent)/'submissions'/(task['task_id']+'.json'))
    proof=stored['payload']['population_proofs'][integrated['oid']]
    assert proof['cohort']['verified']==3 and proof['unreviewed_product_ids']==[]
    assert proof['computed_impact']['units']['exposure']==120
    assert proof['computed_impact']['amount']['exposure']==2070
    assert result['imported_evidence_count']>=len(proof['evidence_ids'])>1
    assert all(parent.get_evidence(eid)['response']['success'] for eid in proof['evidence_ids'])
    assert stored['worker_proof_file_hashes']==proof['job_files_sha256']
    inv.review(parent,'/root',task['task_id'],reviewed_answer(integrated))
    audit=inv.audit(parent,{'findings':[{'obligation_ids':[integrated['oid']]}]})
    assert audit['errors']==[] and audit['verified']==1 and audit['reviewed']==1
    assert audit['expected_obligations']==6 and audit['unassigned']==5
    assert not audit['substantive_complete']


@pytest.mark.parametrize('phase',['before_review','after_review'])
def test_job_record_mutation_after_submission_invalidates_main_review(integrated,phase):
    submit(integrated)
    parent,task=integrated['parent'],integrated['task']
    value=reviewed_answer(integrated)
    if phase=='after_review':inv.review(parent,'/root',task['task_id'],value)
    path=integrated['worker'].path/'records'/('job-'+integrated['spu']['job_id']+'.json')
    record=read(path);record['changed_after_submission']=True;write(path,record)
    if phase=='before_review':
        with pytest.raises(ValueError,match='修改完整对象证明'):
            inv.review(parent,'/root',task['task_id'],value)
    audit=inv.audit(parent)
    assert any('完整对象证明' in error for error in audit['errors'])
    assert not audit['substantive_complete']


@pytest.mark.parametrize('mutation',['answer','evidence','job_record','events'])
def test_import_time_worker_mutation_never_binds_a_stale_submission(integrated,monkeypatch,mutation):
    parent,worker=integrated['parent'],integrated['worker']
    original=parent.evidence
    changed=False
    def importing(value,**kwargs):
        nonlocal changed
        if kwargs.get('kind')=='investigation_worker' and not changed:
            changed=True
            if mutation=='answer':
                path=worker.path/'submission.json';raw=read(path)
                raw['answers'][0]['conclusion']='Changed concurrently during import';write(path,raw)
            elif mutation=='evidence':
                eid=worker.job_record(integrated['spu']['job_id'])['evidence_ids'][0]
                path=worker.path/'evidence'/(eid+'.json');raw=read(path)
                raw['changed_during_import']=True;write(path,raw)
            elif mutation=='job_record':
                path=worker.path/'records'/('job-'+integrated['spu']['job_id']+'.json');raw=read(path)
                raw['changed_during_import']=True;write(path,raw)
            else:
                Run(worker.path).event('out_of_band.worker.activity')
        return original(value,**kwargs)
    monkeypatch.setattr(parent,'evidence',importing)
    with pytest.raises(ValueError,match='导入期间'):
        submit(integrated)
    assert changed
    current=inv.task_records(parent)[integrated['task']['task_id']]
    assert current['state']=='assigned'
    assert not (inv.home(parent)/'submissions'/(integrated['task']['task_id']+'.json')).exists()
    assert inv.audit(parent)['verified']==0
