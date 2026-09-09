"""Runtime entry points cannot launder an archive or a stale completion claim."""
import asyncio
import shutil

import pytest

from test_execution import completed
from acceptance_core import Run,  digest, read, write
from legacy_fixture import create_run
from acceptance_queries import plan
from acceptance_replay import import_archive, import_verified_jobs, audit as replay_audit
from acceptance_transport import execute_job
from acceptance_validate import validate


def archived(run):
    write(run.path/'sealed.json', {'sha256': {
        str(p.relative_to(run.path)): digest(p.read_bytes())
        for p in run.path.rglob('*') if p.is_file()}})


def new_target(source, tmp_path, seed=None):
    base=tmp_path/'target';base.mkdir()
    for name in ['config.yaml','项目范围.md','policy-overrides.json']:
        shutil.copy2(source.path/'inputs/project'/name,base/name)
    if seed:
        override=read(base/'policy-overrides.json');override['sample_seed']=seed
        write(base/'policy-overrides.json',override)
    from pathlib import Path
    run=Run(create_run(base,Path(__file__).resolve().parents[1],source.manifest['data_month']))
    run.register_plan(plan(run.manifest));return run


def test_archived_pages_keep_original_observation_time_and_are_not_live_queries(completed,tmp_path):
    archived(completed);target=new_target(completed,tmp_path)
    receipt=import_archive(target,completed.path)
    assert len(receipt['imported_jobs'])==len(plan(target.manifest))
    assert receipt['pending_changed_jobs']==[] and receipt['fresh_full_scan'] is False
    job=plan(target.manifest)[1]
    assert target.job_record(job['job_id'])['started_at']==completed.job_record(job['job_id'])['started_at']
    assert not replay_audit(target)['errors']
    from acceptance_observability import summarize
    assert summarize(target)['actual_query_attempts']==0
    assert summarize(target)['model_tokens'] is None


def test_changed_query_is_pending_even_when_archive_claimed_success(completed,tmp_path):
    archived(completed);target=new_target(completed,tmp_path,seed='different-query-seed')
    receipt=import_archive(target,completed.path)
    assert [j['family'] for j in receipt['pending_changed_jobs']]==['sample']
    missing=receipt['pending_changed_jobs'][0]['job_id']
    assert target.job_record(missing) is None
    assert validate(target,False)['baseline_jobs']['verified']<len(plan(target.manifest))


def test_archive_record_and_receipt_mutations_are_detected(completed,tmp_path):
    archived(completed);target=new_target(completed,tmp_path)
    receipt=import_archive(target,completed.path)
    job=receipt['imported_jobs'][0]
    p=target.path/'records'/('job-'+job+'.json');v=read(p);v['row_count']+=1;write(p,v)
    assert 'archive_job_receipt_changed:'+job in replay_audit(target)['errors']
    receipt['fresh_full_scan']=True;write(target.path/'archive-replay.json',receipt)
    assert 'archive_replay_receipt_changed' in replay_audit(target)['errors']


def test_cached_success_checks_actual_sql_and_plan(completed):
    job=plan(completed.manifest)[1].copy();job['sql']+=' WHERE 1=0'
    class NeverCall:
        async def call_tool(self,*args,**kwargs):raise AssertionError('corrupt saved scope must not be reused')
    with pytest.raises(ValueError,match='完整复核'):
        asyncio.run(execute_job(completed,NeverCall(),job))


def test_direct_seal_cannot_use_forged_success_dict(completed):
    with pytest.raises(ValueError,match='不能封存'):
        completed.seal({'errors':[],'execution_state':'complete','business_verdict':'pass'})
    assert not (completed.path/'sealed.json').exists()


def test_verified_observation_reuse_keeps_provenance_and_is_not_new_execution(completed,tmp_path):
    target=new_target(completed,tmp_path)
    jobs=plan(target.manifest)[:2]
    receipt=import_verified_jobs(target,completed.path,jobs)
    assert receipt['imported_jobs']==[j['job_id'] for j in jobs]
    assert replay_audit(target)['errors']==[]
    from acceptance_observability import summarize
    cost=summarize(target)
    assert cost['actual_query_attempts']==0 and cost['verified_observation_jobs_reused']==2
    assert receipt['fresh_query_count']==0


def test_reuse_cannot_hide_job_mutation_by_relabeling_as_live(completed,tmp_path):
    target=new_target(completed,tmp_path);job=plan(target.manifest)[1]
    import_verified_jobs(target,completed.path,[job])
    p=target.path/'records'/('job-'+job['job_id']+'.json')
    record=read(p);record['execution_origin']='live';write(p,record)
    assert 'evidence_reused_job_receipt_changed:'+job['job_id'] in replay_audit(target)['errors']


def test_reuse_removed_receipt_and_changed_source_are_rejected(completed,tmp_path):
    target=new_target(completed,tmp_path);job=plan(target.manifest)[1]
    receipt=import_verified_jobs(target,completed.path,[job])
    completed.event('source.changed.after_reuse')
    assert replay_audit(target)['errors']==[]  # Unrelated append does not change the observed material.
    lines=completed.events_path.read_text().splitlines()
    import json
    changed=json.loads(lines[0]);changed['tampered']=True;lines[0]=json.dumps(changed)
    completed.events_path.write_text('\n'.join(lines)+'\n')
    assert any(e.startswith('evidence_reuse_source_events_changed:') for e in replay_audit(target)['errors'])
    (target.path/'reuse-receipts'/(digest(receipt)+'.json')).unlink()
    assert 'evidence_reuse_receipt_set_changed' in replay_audit(target)['errors']


def test_verified_reuse_rejects_different_sql_even_with_same_job_id(completed,tmp_path):
    target=new_target(completed,tmp_path);job=plan(target.manifest)[1].copy();job['sql']+=' WHERE 1=0'
    with pytest.raises(ValueError,match='来源查询'):
        import_verified_jobs(target,completed.path,[job])


def test_attach_cli_keeps_structured_records_and_original_source(completed,tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path
    from acceptance_review import locator
    source=tmp_path/'actual-response.json'
    raw='{"records": [{"site": "DE", "product_id": "p1", "units": 3}]}\n'
    source.write_text(raw)
    cli=Path(__file__).resolve().parents[1]/'scripts/acceptance_run.py'
    result=subprocess.run([sys.executable,str(cli),'attach',str(completed.path),'--file',str(source),
        '--kind','external-response','--source','original local response'],capture_output=True,text=True,check=True)
    eid=result.stdout.strip();saved=completed.get_evidence(eid)
    row=json.loads(raw)['records'][0]
    assert saved['original_text']==raw and saved['original_sha256']==digest(source.read_bytes())
    point={'evidence_id':eid,'pointer':'/content/records/0','record_sha256':digest(row),
           'fields':['units'],'object_keys':{'site':'DE','product_id':'p1'},'purpose':'Check the actual attached record.'}
    assert locator(completed.get_evidence,point)==row
    assert saved['text_lines'][0]['line']==1
