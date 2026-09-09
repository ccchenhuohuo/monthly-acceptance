"""Immutable query executions and verifiable replacement of an earlier receipt."""
import json
import re
import uuid

from acceptance_core import canonical, digest, read, write


def _path(run, job_id, hashed, kind='record'):
    if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', job_id):
        raise ValueError('Invalid versioned job ID')
    if not isinstance(hashed, str) or not re.fullmatch(r'[0-9a-f]{64}', hashed):
        raise ValueError('Invalid job version hash')
    return run.path/'records'/'job-versions'/job_id/(kind+'-'+hashed+'.json')


def _save(run, job_id, value, kind='record'):
    hashed=digest(value); path=_path(run,job_id,hashed,kind)
    if path.exists():
        if digest(read(path))!=hashed:
            raise ValueError('Immutable job version changed')
    else:
        write(path,value)
    return hashed


def begin(run, job, previous, started_at):
    previous_hash=_save(run,job['job_id'],previous) if previous else None
    plan_hash=_save(run,job['job_id'],job,'plan')
    attempt=uuid.uuid4().hex
    binding={'attempt_id':attempt,'plan_sha256':plan_hash,'supersedes_record_sha256':previous_hash}
    run.event('job.started',job_id=job['job_id'],family=job['family'],site=job['site'],**binding)
    record={**{k:v for k,v in job.items() if k!='sql'},'sql_hash':digest(job['sql']),
            'execution_status':'running','started_at':started_at,'evidence_ids':[],**binding}
    write(run.path/'records'/('job-'+job['job_id']+'.json'),record)
    return binding


def finish(run, record):
    hashed=_save(run,record['job_id'],record)
    write(run.path/'records'/('job-'+record['job_id']+'.json'),record)
    status=record['execution_status']
    run.event('job.finished',job_id=record['job_id'],status=status,
              attempt_id=record['attempt_id'],record_sha256=hashed,
              rows=record.get('row_count'),pages=len(record.get('evidence_ids',[])),
              **({'error':record['error']} if 'error' in record else {}))


def execution_files(run, record):
    """Files binding this native attempt, for pinning a reused source observation."""
    if 'attempt_id' not in record:return []
    return [_path(run,record['job_id'],record['plan_sha256'],'plan'),
            _path(run,record['job_id'],digest(record))]


def verify_execution(run, record, job, events=None):
    """An event-bound new attempt cannot be manufactured by relabeling a reuse."""
    if 'attempt_id' not in record:
        return  # Read old frozen receipts without inventing execution history.
    events=events if events is not None else [json.loads(x) for x in run._event_snapshot().splitlines()]
    jid=record['job_id']; attempt=record['attempt_id']; plan_hash=record['plan_sha256']
    saved_plan=read(_path(run,jid,plan_hash,'plan'))
    if digest(saved_plan)!=plan_hash or saved_plan!=job or job['job_id']!=jid:
        raise ValueError('Job execution plan changed')
    if any(record.get(k)!=v for k,v in job.items() if k!='sql') or record.get('sql_hash')!=digest(job['sql']):
        raise ValueError('Job execution scope changed')
    starts=[e for e in events if e['type']=='job.started' and e.get('job_id')==jid and e.get('attempt_id')==attempt]
    if len(starts)!=1 or any(starts[0].get(k)!=record.get(k) for k in ('plan_sha256','supersedes_record_sha256')):
        raise ValueError('Job attempt has no matching start event')
    start=starts[0]
    ends=[e for e in events if e['type']=='job.finished' and e.get('job_id')==jid and e.get('attempt_id')==attempt]
    status=record['execution_status']
    if status=='running':
        if ends: raise ValueError('Running receipt conflicts with a finished attempt')
        end_seq=float('inf')
    else:
        if (len(ends)!=1 or ends[0].get('record_sha256')!=digest(record)
                or ends[0].get('status')!=status or ends[0]['seq']<=start['seq']):
            raise ValueError('Job receipt has no matching finish event')
        if digest(read(_path(run,jid,digest(record))))!=digest(record):
            raise ValueError('Immutable executed receipt changed')
        end_seq=ends[0]['seq']
    ids=record.get('evidence_ids',[])
    if len(ids)!=len(set(ids)) or (status=='success' and not ids):
        raise ValueError('Job execution evidence is empty or repeated')
    for eid in ids:
        meta=read(run.path/'records'/(eid+'.json'))
        source=[e for e in events if e['event_id']==meta.get('parent_event_id') and e['type']=='tool.started']
        if (len(source)!=1 or source[0].get('job_attempt_id')!=attempt or source[0].get('parent_id')!=jid
                or not start['seq']<source[0]['seq']<end_seq or meta.get('kind')!='query-result'
                or meta.get('source')!='configured:doris'):
            raise ValueError('Replacement is not backed by this actual query attempt')
        finished=[e for e in events if e['type']=='tool.finished' and e.get('parent_event_id')==source[0]['event_id']
                  and e.get('status')=='success' and e.get('evidence_id')==eid and source[0]['seq']<e['seq']<end_seq]
        if len(finished)!=1:
            raise ValueError('Replacement query did not produce a successful evidence event')
        run.get_evidence(eid)


def reused_receipt_matches(run, job_id, imported_hash, events):
    """Follow only native, complete execution links to the immutable import."""
    from acceptance_validate import verify_job
    current=run.job_record(job_id)
    seen=set(); expected_plan=None
    while current and digest(current)!=imported_hash:
        hashed=digest(current)
        if hashed in seen or not current.get('attempt_id'):
            return False
        seen.add(hashed)
        snapshot=read(_path(run,job_id,hashed))
        if snapshot!=current:
            return False
        job=read(_path(run,job_id,current['plan_sha256'],'plan'))
        if expected_plan is not None and job!=expected_plan:
            return False
        expected_plan=job
        verify_execution(run,current,job,events)
        if current['execution_status']=='success' and verify_job(run,job,record=current):
            return False
        previous=current.get('supersedes_record_sha256')
        if not previous:
            return False
        current=read(_path(run,job_id,previous))
        if digest(current)!=previous:
            return False
    if not current:
        return False
    # The old observation remains subject to its original SQL, scope and pages.
    if expected_plan is not None and (any(current.get(k)!=v for k,v in expected_plan.items() if k!='sql')
            or current.get('sql_hash')!=digest(expected_plan['sql']) or verify_job(run,expected_plan,record=current)):
        return False
    return True
