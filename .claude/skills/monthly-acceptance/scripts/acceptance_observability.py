"""Actual execution cost and resumable queue counts; unavailable usage is null."""
from collections import Counter
from datetime import datetime
import json
from acceptance_core import event_snapshot


def resume_state(run):
    """A reproducible queue snapshot; completion still comes from live audits."""
    from acceptance_core import digest, read
    from acceptance_investigation import all_obligations, task_records
    from acceptance_queries import plan
    from acceptance_validate import verify_job
    result={'run_id':run.manifest['run_id'],'final_delivery':False,
            'next_baseline_jobs':[j['job_id'] for j in plan(run.manifest)+plan(run.manifest,True) if verify_job(run,j)],
            'obligations_by_state':{},'task_attempts':[],'file_hashes':{},'errors':[]}
    try:
        obligations=all_obligations(run)
        tasks=task_records(run)
        owners={}
        for task in tasks.values():
            result['task_attempts'].append({k:task[k] for k in (
                'task_id','agent_id','state','attempt','worker_dir','progress_sha256','requeue_reason') if k in task})
            if task['state']!='requeued':
                for oid in task['obligation_ids']:
                    if oid in owners:raise ValueError('重复活动归属：'+oid)
                    owners[oid]=task
        import acceptance_triage as triage
        routing=triage.status(run) if triage.enabled(run) else None
        deferred=set(routing['deferred_obligation_ids']) if routing else set()
        if routing: result['classification']=routing; result['errors'].extend(routing['errors'])
        for oid in sorted(obligations):
            task=owners.get(oid)
            state=task.get('outcomes',{}).get(oid,task['state']) if task else ('deferred' if oid in deferred else 'queued')
            result['obligations_by_state'].setdefault(state,[]).append(oid)
    except (ValueError,KeyError,OSError) as exc:
        result['errors'].append('调查队列待恢复：'+str(exc))
    for relative in ['investigations/plan.json','investigations/cases/state.json',
                     'coverage-sampling-plan.json','coverage-plan.json','coverage-history-plan.json']:
        path=run.path/relative
        if path.exists():result['file_hashes'][relative]=digest(path.read_bytes())
    case_path=run.path/'investigations/cases/state.json'
    if case_path.exists():
        cases=read(case_path).get('payload',{})
        result['case_state_path']=str(case_path)
        result['case_state_sha256']=digest(case_path.read_bytes())
        result['case_route_counts']=dict(Counter(c.get('route','awaiting_evidence') for c in cases.get('cases',[])))
    result['note']='队列用于定位续跑，不授予完成状态。预算、容量、查询失败与外部缺证分别处理；恢复后重新验证引用材料。'
    return result


def summarize(run):
    paths = [run.events_path]
    paths += sorted((run.path/'investigations'/'workers').glob('*/events.jsonl'))
    starts, ends, durations, actors = [], [], [], set()
    assignment_attempts = set()
    types, status, queued_seconds = Counter(), Counter(), 0.0
    for path in paths:
        for line in event_snapshot(path).splitlines():
            event = json.loads(line)
            types[event['type']] += 1
            if event.get('agent_id'):
                actors.add(event['agent_id'])
            if event['type'] == 'investigation.task.saved' and event.get('action') == 'assigned':
                assignment_attempts.add((event['task_id'], event.get('attempt', 1)))
            if event['type'] == 'tool.started':
                starts.append(event)
            elif event['type'] == 'tool.finished':
                ends.append(event)
                status[event.get('status', 'unknown')] += 1
                duration = event.get('duration_seconds', event.get('duration'))
                if isinstance(duration, (int, float)):
                    durations.append(duration)
            elif event['type'] == 'query.capacity.acquired':
                queued_seconds += event.get('wait_seconds', 0)
    times = [e.get('time') for e in starts+ends if e.get('time')]
    elapsed = None
    if times:
        try:
            parsed = [datetime.fromisoformat(t) for t in times]
            elapsed = (max(parsed)-min(parsed)).total_seconds()
        except ValueError:
            pass
    return {'actual_query_attempts': len(starts), 'terminal_query_attempts': len(ends),
        'query_status_counts': dict(status),
        'query_elapsed_seconds': sum(durations) if durations else None,
        'query_wall_span_seconds': elapsed, 'query_queue_seconds': round(queued_seconds, 6),
        'archived_jobs_imported': types['archive.job.imported'],
        'verified_observation_jobs_reused': types['evidence.job.reused'],
        'shared_query_requests_reused': types['query.reused'], 'query_retries_planned': types['query.retry_planned'],
        'investigation_assignments': len(assignment_attempts),
        'investigation_attempt_events': {k: v for k, v in types.items()
            if k.startswith('investigation.') and any(w in k for w in ('assign','progress','requeue','review'))},
        'recorded_agent_ids': sorted(actors), 'model_tokens': None, 'model_billing': None,
        'usage_limitation': 'CLI does not expose model tokens or billing. Agent IDs and task attempts are not a token/cost estimate; archive replay is not a database call.'}
