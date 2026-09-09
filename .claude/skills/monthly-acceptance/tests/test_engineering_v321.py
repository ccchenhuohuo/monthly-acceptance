"""Continuation, reference provenance and batch storage regressions; offline only."""
import asyncio
import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))

from acceptance_core import Run,canonical,digest,read,write
from acceptance_queries import plan
from acceptance_transport import execute_job
from acceptance_validate import verify_job
from acceptance_replay import import_verified_jobs,import_archive,audit as replay_audit
import acceptance_investigation as inv
import acceptance_review as review
import acceptance_cases as cases
from test_execution import completed,rows_for
from test_runtime_v32 import new_target,archived
from test_review_v32 import v32,submit,approval,resave,loc
from test_cases_v32 import case_run,_prepare_review_cases


class SyntheticSession:
    """Exercise the native transport with explicit synthetic paginated responses."""
    def __init__(self,rows):self.rows=rows
    async def call_tool(self,name,args,**kwargs):
        size,offset=map(int,re.findall(r'LIMIT (\d+) OFFSET (\d+)',args['sql'])[-1])
        rows=[{**r,'total_rows':len(self.rows)} for r in self.rows[offset:offset+size]]
        return {'success':True,'data':rows,'row_count':len(rows),'metadata':{'query':args['sql']}}


def refreshed(source,target,job):
    return asyncio.run(execute_job(target,SyntheticSession(rows_for(source,job)),job,force=True))


def test_reused_end_fingerprints_can_be_refreshed_twice_without_losing_import(completed,tmp_path):
    target=new_target(completed,tmp_path)
    jobs=plan(target.manifest,True)
    receipt=import_verified_jobs(target,completed.path,jobs)
    original={j['job_id']:digest(target.job_record(j['job_id'])) for j in jobs}
    for _ in range(2):
        for job in jobs:
            assert refreshed(completed,target,job)['execution_status']=='success'
            assert not verify_job(target,job)
        assert replay_audit(target)['errors']==[]
    assert read(target.path/'reuse-receipts'/(digest(receipt)+'.json'))==receipt
    for jid,hashed in original.items():
        assert digest(read(target.path/'records/job-versions'/jid/('record-'+hashed+'.json')))==hashed
    events=[json.loads(x) for x in target.events_path.read_text().splitlines()]
    assert len([e for e in events if e['type']=='job.finished'])==len(jobs)*2


def test_archived_baseline_can_be_reexecuted_with_its_original_receipt_retained(completed,tmp_path):
    archived(completed);target=new_target(completed,tmp_path)
    import_archive(target,completed.path)
    job=plan(target.manifest)[0]
    assert refreshed(completed,target,job)['execution_status']=='success'
    assert not replay_audit(target)['errors']


def test_reusing_a_native_versioned_execution_does_not_invent_target_execution(completed,tmp_path):
    first=new_target(completed,tmp_path);job=plan(first.manifest,True)[0]
    refreshed(completed,first,job)
    folder=tmp_path/'second';folder.mkdir()
    second=new_target(first,folder)
    import_verified_jobs(second,first.path,[job])
    record=second.job_record(job['job_id'])
    assert 'attempt_id' not in record and record['source_execution_binding']['attempt_id']
    assert not verify_job(second,job) and not replay_audit(second)['errors']
    version=first.path/'records/job-versions'/job['job_id']/('plan-'+record['source_execution_binding']['plan_sha256']+'.json')
    value=read(version);value['sql']='SELECT 0';write(version,value)
    assert any('source_file_changed' in e for e in replay_audit(second)['errors'])


def test_public_cli_reuse_regenerates_jobs_and_rejects_unknown_ids(completed,tmp_path):
    target=new_target(completed,tmp_path);job=plan(target.manifest,True)[0]
    ids=tmp_path/'job-ids.json';write(ids,[job['job_id']])
    script=Path(__file__).resolve().parents[1]/'scripts/acceptance_run.py'
    cmd=[sys.executable,str(script),'evidence-reuse',str(target.path),'--source',str(completed.path),'--job-ids-file',str(ids)]
    result=subprocess.run(cmd,capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)['imported_jobs']==1 and not replay_audit(target)['errors']
    write(ids,['nonexistent-job'])
    result=subprocess.run(cmd,capture_output=True,text=True)
    assert result.returncode!=0 and 'outside the current frozen scan plan' in result.stderr


def test_cancelled_replacement_can_resume_without_turning_cancellation_into_success(completed,tmp_path):
    target=new_target(completed,tmp_path);job=plan(target.manifest,True)[0]
    import_verified_jobs(target,completed.path,[job])
    class Cancelled:
        async def call_tool(self,*args,**kwargs):raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):asyncio.run(execute_job(target,Cancelled(),job,force=True))
    assert target.job_record(job['job_id'])['execution_status']=='cancelled'
    assert verify_job(target,job)
    assert replay_audit(target)['errors']==[]
    refreshed(completed,target,job)
    assert not verify_job(target,job) and not replay_audit(target)['errors']


@pytest.mark.parametrize('attack',['original_version','missing_page','fabricated_link','wrong_scope','wrong_sql','missing_execution_event'])
def test_replacement_does_not_launder_corruption(completed,tmp_path,attack):
    target=new_target(completed,tmp_path);job=plan(target.manifest,True)[0]
    import_verified_jobs(target,completed.path,[job])
    old_hash=digest(target.job_record(job['job_id']))
    record=refreshed(completed,target,job)
    current=target.path/'records'/('job-'+job['job_id']+'.json')
    if attack=='original_version':
        path=target.path/'records/job-versions'/job['job_id']/('record-'+old_hash+'.json')
        value=read(path);value['row_count']+=1;write(path,value)
    elif attack=='missing_page':
        (target.path/'evidence'/(record['evidence_ids'][0]+'.json')).unlink()
    elif attack=='fabricated_link':
        record['supersedes_record_sha256']='0'*64;write(current,record)
    elif attack=='wrong_scope':
        record['site']='unrelated';write(current,record)
    elif attack=='wrong_sql':
        record['sql_hash']='0'*64;write(current,record)
    else:
        events=[json.loads(x) for x in target.events_path.read_text().splitlines()]
        target.events_path.write_text('\n'.join(canonical(e) for e in events
            if not (e['type']=='job.finished' and e.get('attempt_id')==record['attempt_id']))+'\n')
    assert replay_audit(target)['errors']


def test_auxiliary_reference_is_imported_even_when_not_in_primary_checks(v32):
    worker=v32['worker']
    eid=worker.evidence({'records':[v32['row']],'calculation':'independent sum'},kind='synthetic_calculation')
    v32['answer']['calculation_evidence_id']=eid
    v32['answer']['appendix']={'support_evidence_ids':[eid]}
    resave(v32);submit(v32)
    stored=read(inv.home(v32['run'])/'submissions'/(v32['task']['task_id']+'.json'))
    central=stored['evidence_map'][eid];answer=stored['payload']['answers'][0]
    assert answer['calculation_evidence_id']==central
    assert answer['appendix']['support_evidence_ids']==[central]
    assert v32['run'].get_evidence(central)==worker.get_evidence(eid)
    assert central in review.evidence_ids(answer)
    main=approval(v32)
    main['obligations'][0]['evidence_checked'].append(central)
    inv.review(v32['run'],'/root',v32['task']['task_id'],main)
    (worker.path/'evidence'/(eid+'.json')).write_text('{"changed":true}')
    assert inv.audit(v32['run'])['errors']


@pytest.mark.parametrize('value',['ev-'+'0'*32,None,42,['ev-'+'0'*32]])
def test_invalid_auxiliary_reference_cannot_survive_submission(v32,value):
    v32['answer']['calculation_evidence_id']=value;resave(v32)
    with pytest.raises((ValueError,FileNotFoundError)):submit(v32)
    assert inv.task_records(v32['run'])[v32['task']['task_id']]['state']=='assigned'


def test_auxiliary_main_reference_normalizes_without_rewriting_prose(v32):
    eid=v32['run'].evidence({'records':[v32['row']]},kind='synthetic_parent_calculation')
    v32['answer']['calculation_evidence_id']='main:'+eid
    text='Literal source note main:'+eid
    v32['answer']['calculation_note']=text
    resave(v32);submit(v32)
    stored=read(inv.home(v32['run'])/'submissions'/(v32['task']['task_id']+'.json'))
    answer=stored['payload']['answers'][0]
    assert answer['calculation_evidence_id']==eid and answer['calculation_note']==text


def test_changed_case_reuses_unchanged_chunks_and_old_full_snapshot_remains_readable(case_run):
    run,state,_,_=case_run
    state=cases.load(run)
    initial={p.name:p.read_bytes() for p in (cases.home(run)/'chunks').glob('*.json')}
    version=state['version'];state['cases'][0]['route_reason']='Explicit new checkpoint reason'
    cases._persist(run,state,'synthetic_reason_update','main')
    assert cases.load(run)==state
    current={p.name for p in (cases.home(run)/'chunks').glob('*.json')}
    # Scalar version bytes may already exist as another scalar's content chunk.
    assert 1 <= len(current-set(initial)) <= 2
    assert all((cases.home(run)/'chunks'/name).read_bytes()==raw for name,raw in initial.items())
    assert (cases.home(run)/'versions'/('v%06d.json'%version)).exists()
    # A legacy full snapshot must remain usable by the new reader.
    legacy_path=run.path/'legacy'
    manifest=copy.deepcopy(run.manifest);manifest['run_id']='legacy-storage-fixture'
    write(legacy_path/'run.json',manifest);legacy=Run(legacy_path)
    old_state=copy.deepcopy(state);old_state['source_binding']['source_run']=str(legacy.path)
    value={'payload':old_state,'sha256':digest(old_state)}
    path=cases.home(legacy)/'versions'/('v%06d.json'%old_state['version']);write(path,value)
    write(cases.home(legacy)/'state.json',value)
    legacy.event('case.registry.saved',version=old_state['version'],registry_hash=value['sha256'],
                 path=str(path.relative_to(legacy.path)))
    assert cases.load(legacy)==old_state


@pytest.mark.parametrize('attack',['chunk','manifest','current_state'])
def test_chunk_storage_rejects_corruption(case_run,attack):
    run,state,_,_=case_run
    if attack=='chunk':next((cases.home(run)/'chunks').glob('*.json')).write_text('{}')
    elif attack=='manifest':
        path=cases.home(run)/'versions'/('v%06d.json'%state['version'])
        value=read(path);value['components'].pop();write(path,value)
    else:
        path=cases.home(run)/'state.json';value=read(path);value['payload']['cases'].pop();write(path,value)
    with pytest.raises(ValueError):cases.load(run)


def test_batch_validation_failure_never_partially_updates_an_approved_case(v32,monkeypatch):
    cid=_prepare_review_cases(v32,monkeypatch)
    submit(v32);inv.review(v32['run'],'/root',v32['task']['task_id'],approval(v32))
    state=cases.load(v32['run']);unreviewed=next(c['case_id'] for c in state['cases'] if c['case_id']!=cid)
    before=(cases.home(v32['run'])/'state.json').read_bytes()
    with pytest.raises(ValueError,match='current review bound'):
        cases.acknowledge_reviews(v32['run'],'/root',[cid,unreviewed])
    assert (cases.home(v32['run'])/'state.json').read_bytes()==before
    assert next(c for c in cases.load(v32['run'])['cases'] if c['case_id']==cid)['review_status']=='pending'


def test_three_actual_reviews_sync_once_and_keep_every_obligation(v32,monkeypatch):
    run=v32['run'];extra=[]
    for number in range(2):
        row={**v32['row'],'product_id':'synthetic-total-check-'+str(number)}
        eid=run.evidence({'records':[row]},kind='synthetic_aggregate_reconciliation')
        issue={'question':'Reconcile this synthetic aggregate with its full source population.',
               'discovered_from':'Offline numerical reconciliation fixture','issue_type':'numerical',
               'site':'DE','path':row['path'],'level':3,'month':'2026-07','product_id':row['product_id'],
               'evidence_ids':[eid],'source_observation':loc(eid,row,inv.RESULT_CHECKS)}
        oid=inv.add_obligations(run,'/root',[issue])[0]
        extra.append((oid,row))
    _prepare_review_cases(v32,monkeypatch)
    submit(v32);inv.review(run,'/root',v32['task']['task_id'],approval(v32))
    for number,(oid,row) in enumerate(extra):
        task=inv.assign(run,'/root','/root/numerical-'+str(number),[oid])
        worker=Run(run.path/task['worker_dir']);ob=inv.all_obligations(run)[oid]
        eid=worker.evidence({'records':[row]},kind='synthetic_full_population')
        point=loc(eid,row,ob['required_checks'])
        answer=copy.deepcopy(v32['answer'])
        answer.update(obligation_id=oid,scope=ob['scope'],contract_sha256=ob['contract']['sha256'])
        template=copy.deepcopy(next(iter(answer['checks'].values())))
        template.update(evidence_ids=[eid],evidence_locators=[point])
        answer['checks']={name:copy.deepcopy(template) for name in ob['required_checks']}
        for item in answer['impact'].values():item['evidence_ids']=[eid]
        payload={'task_id':task['task_id'],'agent_id':task['agent_id'],'attempt':task['attempt'],'answers':[answer]}
        write(worker.path/'submission.json',payload)
        fixture={'run':run,'task':task,'worker':worker,'ob':ob,'row':row,'answer':answer,'payload':payload}
        submit(fixture);inv.review(run,'/root',task['task_id'],approval(fixture))
    prior=cases.load(run)
    ids={v32['ob']['obligation_id'],*(oid for oid,_ in extra)}
    cids=[c['case_id'] for c in prior['cases'] if set(c['obligation_ids'])&ids]
    original=inv.all_obligations;calls=[]
    def count(r,p=None):
        calls.append(1);return original(r,p)
    monkeypatch.setattr(inv,'all_obligations',count)
    current=cases.acknowledge_reviews(run,'/root',cids)
    assert len(calls)==1
    assert current['version']==prior['version']+1
    assert len(current['obligation_case_map'])==len(prior['obligation_case_map'])
    reviewed=[c for c in current['cases'] if c['case_id'] in cids]
    assert len(reviewed)==3 and all(c['route']=='reviewed' for c in reviewed)
    assert {r['obligation_id'] for c in reviewed for r in c['review_receipts']}==ids


def test_case_audit_detects_material_changed_during_operation(v32,monkeypatch):
    cid=_prepare_review_cases(v32,monkeypatch)
    submit(v32);inv.review(v32['run'],'/root',v32['task']['task_id'],approval(v32))
    cases.acknowledge_review(v32['run'],'/root',cid)
    original=cases._verified_case_review
    def change_after_check(run,case,**kwargs):
        result=original(run,case,**kwargs)
        write(v32['worker'].path/'submission.json',{'changed_after_first_verification':True})
        return result
    monkeypatch.setattr(cases,'_verified_case_review',change_after_check)
    audited=cases.audit(v32['run'])
    assert audited['errors'] and not audited['complete']


def test_business_report_leads_with_macro_findings_and_keeps_auxiliary_evidence(completed):
    from acceptance_validate import validate
    from acceptance_render import render
    from acceptance_report_content import FAMILIES
    eid=completed.job_record(plan(completed.manifest)[0]['job_id'])['evidence_ids'][0]
    base={'scope':'DE synthetic observed month range','facts':['Explicitly synthetic report fixture'],
          'counterevidence':'No business claim from this fixture','assessment':'Pending business verification',
          'action':'Reconcile the full population','evidence_ids':[eid]}
    content={'summary':['Synthetic macro reporting order check'],
        'audit_overview':[{'family':'completeness','state':'pending','scope':'DE all configured months',
                          'basis':'Evidence collected; business investigation remains unfinished','evidence_ids':[eid]}],
        'findings':[{**base,'title':'Supporting detail','presentation':'auxiliary'},
                    {**base,'title':'Aggregate completeness finding'}]}
    write(completed.path/'report-content.json',content);validate(completed);render(completed)
    report=(completed.path/'报告.md').read_text()
    assert report.index('七类大盘审计概览')<report.index('Aggregate completeness finding')<report.index('调查与主审进度')<report.index('Supporting detail')
    assert all(label in report for label in FAMILIES.values())
    assert report.count('尚未提交该主线的范围及结论，不能推定正常')==6
    assert report.count('可复核证据：')==2
