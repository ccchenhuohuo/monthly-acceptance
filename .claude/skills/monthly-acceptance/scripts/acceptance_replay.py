"""Explicit, hash-bound reuse of archived query evidence, never a live scan."""
from pathlib import Path

from acceptance_core import Run, digest, read, write
from acceptance_queries import plan


def original_observed_at(meta):
    source=meta.get('source')
    if isinstance(source,dict):
        return source.get('source_observed_at',source.get('observed_at',meta['created_at']))
    return meta['created_at']


def event_prefix_bytes(run, lines):
    data=run.events_path.read_bytes().splitlines(keepends=True)
    if len(data)<lines:raise ValueError('来源事件链前缀被截断')
    return b''.join(data[:lines])


def imported_record(old, evidence_ids, origin, source):
    binding={k:old[k] for k in ('attempt_id','plan_sha256','supersedes_record_sha256') if k in old}
    return {**{k:v for k,v in old.items() if k not in binding},'evidence_ids':evidence_ids,
            'execution_origin':origin,'source_run':str(source),
            **({'source_execution_binding':binding} if binding else {})}


def import_planned_jobs(run, source_path, job_ids):
    """Public bounded entry: regenerate typed baseline/end jobs in the new run."""
    if (not isinstance(job_ids,list) or not job_ids or any(not isinstance(j,str) for j in job_ids)
            or len(job_ids)!=len(set(job_ids))):
        raise ValueError('Reuse selection must be a nonempty list of unique job IDs')
    jobs={j['job_id']:j for j in plan(run.manifest)+plan(run.manifest,True)}
    if not set(job_ids)<=set(jobs):raise ValueError('Reuse selection is outside the current frozen scan plan')
    return import_verified_jobs(run,source_path,[jobs[jid] for jid in job_ids])


def import_verified_jobs(run, source_path, jobs):
    """Reuse typed completed observations in a newly frozen implementation.

    This is used after a code correction, without editing the original inputs
    or pretending its SQL was executed by the new build. The caller supplies
    current generated jobs; final domain proof still rebuilds those jobs again.
    """
    from acceptance_validate import verify_job
    from acceptance_job_versions import execution_files
    source=Run(source_path)
    if source.path==run.path or run.verify_inputs() or source.verify_inputs() or source.verify_events():
        raise ValueError('证据复用需要不同且完整的冻结运行')
    frozen_events=source.events_path.read_bytes()
    source_event_lines=len(frozen_events.splitlines())
    source_events=digest(frozen_events)
    imported, reused, mapping, files = [], [], {}, {}
    for job in jobs:
        if verify_job(source,job):
            raise ValueError('来源查询与新冻结任务不一致或缺页：'+job['job_id'])
        old=source.job_record(job['job_id'])
        if run.job_record(job['job_id']):
            if verify_job(run,job) or digest(run.job_rows(job['job_id']))!=digest(source.job_rows(job['job_id'])):
                raise ValueError('目标已存不同观测，拒绝覆盖：'+job['job_id'])
            reused.append(job['job_id']);continue
        relative='records/job-'+job['job_id']+'.json'
        files[relative]=digest((source.path/relative).read_bytes())
        for path in execution_files(source,old):
            files[str(path.relative_to(source.path))]=digest(path.read_bytes())
        for eid in old['evidence_ids']:
            meta=read(source.path/'records'/(eid+'.json'))
            files['records/'+eid+'.json']=digest((source.path/'records'/(eid+'.json')).read_bytes())
            files[meta['path']]=meta['sha256']
            if eid not in mapping:
                mapping[eid]=run.evidence(source.get_evidence(eid),kind='verified-reused-query',source={
                    'source_run':str(source.path),'source_evidence_id':eid,'source_sha256':meta['sha256'],
                    'source_observed_at':original_observed_at(meta), 'source_events_sha256':source_events,
                    'source_event_lines':source_event_lines,
                    'immediate_source_metadata_created_at':meta['created_at'],
                    'retrieval':'reused exact typed SQL/pages from an earlier frozen implementation; not a fresh call'})
        record=imported_record(old,[mapping[e] for e in old['evidence_ids']],
                               'verified_evidence_reuse',source.path)
        write(run.path/'records'/('job-'+job['job_id']+'.json'),record)
        write(run.path/'records'/(job['job_id']+'-plan.json'),job)
        run.event('evidence.job.reused',job_id=job['job_id'],source_run=str(source.path),record_sha256=digest(record))
        imported.append(job['job_id'])
    if source_events!=digest(event_prefix_bytes(source,source_event_lines)) or any(digest((source.path/p).read_bytes())!=sha for p,sha in files.items()):
        raise ValueError('复用时来源材料或原事件前缀改变；拒绝不稳定观测')
    receipt={'source_run':str(source.path),'source_events_sha256':source_events,
             'source_event_lines':source_event_lines,'source_files':files,
             'imported_jobs':imported,'already_identical_jobs':reused,'evidence_map':mapping,'fresh_query_count':0}
    rid=digest(receipt)
    write(run.path/'reuse-receipts'/(rid+'.json'),receipt)
    run.event('evidence.reuse.frozen',receipt_sha256=rid,source_run=str(source.path),imported_jobs=len(imported))
    return receipt


def import_archive(run, source_path):
    """Import only jobs whose current frozen SQL and pagination are identical.

    A changed query stays pending. Neither a legacy success label nor equal row
    counts establishes semantic equivalence of different query definitions.
    """
    from acceptance_validate import verify_job
    from acceptance_job_versions import execution_files
    source = Run(source_path)
    if run.path == source.path:
        raise ValueError('归档重放必须使用新运行')
    target = run.path / 'archive-replay.json'
    if target.exists():
        raise ValueError('归档来源已冻结；不能在运行中替换')
    if run.verify_inputs() or source.verify_inputs() or source.verify_events():
        raise ValueError('归档或目标冻结输入/事件不完整')
    sealed = read(source.path / 'sealed.json')
    used = {}

    def verified(relative):
        path = (source.path / relative).resolve()
        if not path.is_relative_to(source.path) or not path.is_file():
            raise ValueError('归档来源路径无效：' + relative)
        sha = digest(path.read_bytes())
        if sealed['sha256'].get(relative) != sha:
            raise ValueError('归档来源与封存不一致：' + relative)
        used[relative] = sha
        return read(path)

    manifest = verified('run.json')
    original = {j['job_id']: j for j in verified('plan.json')['jobs']}
    if (manifest['sites'], manifest['starts'], manifest['data_month']) != (
            run.manifest['sites'], run.manifest['starts'], run.manifest['data_month']):
        raise ValueError('归档国家或月份与冻结范围不一致')
    reused, changed, ids = [], [], {}
    for job in plan(run.manifest):
        prior = original.get(job['job_id'])
        if prior != job or source.policy['page_size'] != run.policy['page_size']:
            changed.append({'job_id': job['job_id'], 'family': job['family'],
                            'site': job['site'], 'reason': 'query_or_page_policy_changed'})
            continue
        if run.job_record(job['job_id']):
            raise ValueError('目标任务已有记录，拒绝覆盖：' + job['job_id'])
        record = verified('records/job-' + job['job_id'] + '.json')
        for path in execution_files(source,record):verified(str(path.relative_to(source.path)))
        for eid in record['evidence_ids']:
            meta = verified('records/' + eid + '.json')
            value = verified(meta['path'])
            if digest((source.path / meta['path']).read_bytes()) != meta['sha256']:
                raise ValueError('归档证据元数据不一致')
            if eid not in ids:
                ids[eid] = run.evidence(value, kind='archived-query-response', source={
                    'source_run': str(source.path), 'source_evidence_id': eid,
                    'source_sha256': meta['sha256'], 'observed_at': meta['created_at'],
                    'retrieval': 'frozen archive replay; no live database call'})
        errors = verify_job(source, job)
        if errors:
            raise ValueError('归档查询未通过完整核验：' + str(errors))
        imported=imported_record(record,[ids[e] for e in record['evidence_ids']],
                                 'archived_replay',source.path)
        write(run.path / 'records' / ('job-' + job['job_id'] + '.json'), imported)
        if verify_job(run, job):
            raise ValueError('导入查询与当前冻结计划不一致')
        run.event('archive.job.imported', job_id=job['job_id'],
                  source_run=str(source.path), record_sha256=digest(imported))
        reused.append(job['job_id'])
    receipt = {'source_run': str(source.path), 'source_seal_sha256': digest(
        (source.path / 'sealed.json').read_bytes()), 'source_files': used,
        'imported_jobs': reused, 'pending_changed_jobs': changed, 'evidence_map': ids,
        'fresh_full_scan': False, 'transaction_snapshot': False,
        'comparability': 'archived baseline; subsequent live diagnostics require explicit comparison',
        'note': '来源相同只证明重放可比；在线数据不能假定与归档逐行相同。'}
    write(target, receipt)
    run.event('archive.replay.frozen', sha256=digest(receipt), imported=len(reused),
              pending=len(changed), fresh_full_scan=False)
    return receipt


def audit(run):
    import json
    from acceptance_job_versions import reused_receipt_matches
    events=list(map(json.loads,run._event_snapshot().splitlines()))
    def matches_import(jid, kind, source=None):
        anchors=[e for e in events if e.get('type')==kind and e.get('job_id')==jid
                 and (source is None or e.get('source_run')==source)]
        try:
            return len(anchors)==1 and reused_receipt_matches(run,jid,anchors[0]['record_sha256'],events)
        except (ValueError,KeyError,TypeError,OSError):
            return False
    errors=[]
    reused_jobs=set()
    receipts={p.stem:p for p in (run.path/'reuse-receipts').glob('*.json')}
    frozen={e['receipt_sha256'] for e in events if e.get('type')=='evidence.reuse.frozen'}
    if set(receipts)!=frozen:
        errors.append('evidence_reuse_receipt_set_changed')
    for rid,p in receipts.items():
        value=read(p)
        if digest(value)!=rid:
            errors.append('evidence_reuse_receipt_changed:'+rid)
            continue
        source=Run(value['source_run'])
        if source.verify_inputs() or source.verify_events():
            errors.append('evidence_reuse_source_integrity:'+rid)
        prefix=(event_prefix_bytes(source,value['source_event_lines']) if 'source_event_lines' in value else source.events_path.read_bytes())
        if digest(prefix)!=value['source_events_sha256']:
            errors.append('evidence_reuse_source_events_changed:'+rid)
        for relative,sha in value['source_files'].items():
            path=(source.path/relative).resolve()
            if not path.is_relative_to(source.path) or not path.is_file() or digest(path.read_bytes())!=sha:
                errors.append('evidence_reuse_source_file_changed:'+relative)
        for jid in value['imported_jobs']:
            if jid in reused_jobs:
                errors.append('evidence_reuse_duplicate_import:'+jid)
            reused_jobs.add(jid)
            if not matches_import(jid,'evidence.job.reused',value['source_run']):
                errors.append('evidence_reused_job_receipt_changed:'+jid)
        for old_eid,new_eid in value['evidence_map'].items():
            try:
                origin=read(source.path/'records'/(old_eid+'.json'))
                meta=read(run.path/'records'/(new_eid+'.json'))
                payload=run.get_evidence(new_eid)
                link=meta['source']
                if (link.get('source_run')!=value['source_run'] or link.get('source_evidence_id')!=old_eid
                        or link.get('source_events_sha256')!=value['source_events_sha256']
                        or link.get('source_event_lines')!=value.get('source_event_lines')
                        or link.get('source_sha256')!=origin['sha256']
                        or link.get('source_observed_at')!=original_observed_at(origin)
                        or digest(payload)!=digest(source.get_evidence(old_eid))):
                    errors.append('evidence_reuse_provenance_changed:'+new_eid)
            except (KeyError,ValueError,OSError):
                errors.append('evidence_reuse_provenance_missing:'+new_eid)
    reused_events={e['job_id'] for e in events if e.get('type')=='evidence.job.reused'}
    if reused_events!=reused_jobs:
        errors.append('evidence_reuse_job_set_changed')
    path = run.path / 'archive-replay.json'
    if not path.exists():
        return {'required':bool(receipts or frozen),'errors':errors,
                'verified_jobs_reused':len(reused_jobs),'fresh_query_count_from_reuse':0,
                'limitations':['复用此前冻结运行的完整观测，并未重新执行 SQL；观测时间保留于证据来源。'] if frozen else []}
    value = read(path)
    bound = any(e.get('type') == 'archive.replay.frozen' and e.get('sha256') == digest(value)
                for e in events)
    if not bound:
        errors.append('archive_replay_receipt_changed')
    for jid in value['imported_jobs']:
        record = run.job_record(jid)
        if not record:
            errors.append('archive_imported_job_missing:' + jid)
        else:
            if not matches_import(jid,'archive.job.imported',value['source_run']):
                errors.append('archive_job_receipt_changed:' + jid)
    return {'required': True, 'errors': errors,
            'imported_jobs': len(value['imported_jobs']),
            'verified_jobs_reused':len(reused_jobs),'fresh_query_count_from_reuse':0,
            'pending_changed_jobs_at_import': len(value['pending_changed_jobs']),
            'fresh_full_scan': False, 'limitations': [
                '本运行复用有来源证明的归档扫描；后续在线取证并非同一事务快照，不能宣称新版全量在线验收。']}
