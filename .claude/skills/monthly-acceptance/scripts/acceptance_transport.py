"""Configured Doris MCP transport. Credentials stay in the host configuration, outside evidence."""
import asyncio
import contextlib
import fcntl
import json
import os
import sys
import time
import tempfile
import tomllib
from datetime import timedelta
from pathlib import Path
from acceptance_core import digest, now, readonly_sql, write
from acceptance_queries import paginated


@contextlib.asynccontextmanager
async def connect(run, config_path=None):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    config_path=Path(config_path or Path.home()/'.codex/config.toml')
    config=tomllib.loads(config_path.read_text())['mcp_servers']['doris']
    env={**os.environ,**config.get('env',{})}
    # Temporary startup logs are redacted on failure and discarded on success.
    with tempfile.TemporaryDirectory(prefix='acceptance-mcp-') as tmp:
        env.update(AUDIT_FILE_PATH=str(Path(tmp)/'audit.log'),LOG_FILE_PATH=str(Path(tmp)/'server.log'))
        server=StdioServerParameters(command=config['command'],args=config.get('args',[]),env=env)
        try:
            with open(Path(tmp)/'stderr.log','w') as err:
                async with stdio_client(server,errlog=err) as (reader,writer):
                    async with ClientSession(reader,writer,read_timeout_seconds=timedelta(seconds=run.policy['timeout_seconds']+60)) as session:
                        await session.initialize()
                        run.event('transport.connected',server='configured:doris',protocol='MCP/stdio',credential_values_recorded=False)
                        yield session
        except asyncio.CancelledError:
            run.event('transport.cancelled',reason='execution interrupted')
            raise
        except BaseException as exc:
            diagnostic='\n'.join(p.read_text(errors='replace') for p in Path(tmp).glob('*.log'))
            for secret in sorted(config.get('env',{}).values(),key=len,reverse=True):
                if len(secret)>2:diagnostic=diagnostic.replace(secret,'[configuration value]')
            diagnostic='\n'.join(line for line in diagnostic.splitlines() if any(k in line for k in ['ERROR','Error','error','failed','Permission','Traceback']))[-6000:]
            run.event('transport.error',error_type=type(exc).__name__,diagnostic=diagnostic)
            raise RuntimeError('Doris MCP connection failed: '+diagnostic) from exc


def decode(result):
    if isinstance(result,dict):return result
    chunks=[c.text for c in result.content if getattr(c,'type',None)=='text']
    if result.isError:raise ValueError('MCP error: '+str(chunks)[:3000])
    for t in chunks:
        try:
            value=json.loads(t)
            if isinstance(value,dict) and 'success' in value:return value
        except json.JSONDecodeError:pass
    raise ValueError('工具响应无法解析为结果对象')


@contextlib.asynccontextmanager
async def query_slot(run, *, label, query_hash, parent, attempt):
    """Process-wide slots for one run family; unrelated DB clients are outside it.

    The files are permanent lock identities, not mutable shared business records.
    Every lifecycle event belongs to the calling run's single-writer event chain.
    Kernel locks are also released if a worker process exits unexpectedly.
    """
    directory, capacity = run.query_resource_scope()
    directory.mkdir(parents=True, exist_ok=True)
    queued_at = time.monotonic()
    request = run.event('query.capacity.queued', label=label, query_hash=query_hash,
        parent_id=parent, attempt=attempt, capacity=capacity, resource_directory=str(directory))
    handle = None; slot = None; acquired_at = None
    try:
        while handle is None:
            for number in range(capacity):
                candidate = (directory / f'slot-{number}.lock').open('a')
                try:
                    fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    candidate.close()
                    continue
                except BaseException:
                    candidate.close()
                    raise
                handle, slot, acquired_at = candidate, number, time.monotonic()
                break
            if handle is None:
                await asyncio.sleep(0.05)
        wait_seconds = round(acquired_at - queued_at, 6)
        run.event('query.capacity.acquired', parent_event_id=request, slot=slot,
            capacity=capacity, wait_seconds=wait_seconds)
        yield {'request_event_id': request, 'slot': slot, 'wait_seconds': wait_seconds}
    except asyncio.CancelledError:
        run.event('query.capacity.cancelled', parent_event_id=request,
            phase='executing' if handle is not None else 'waiting', slot=slot,
            wait_seconds=round((acquired_at or time.monotonic()) - queued_at, 6))
        raise
    finally:
        if handle is not None:
            held_seconds = round(time.monotonic() - acquired_at, 6)
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            finally:
                handle.close()
            run.event('query.capacity.released', parent_event_id=request,
                slot=slot, held_seconds=held_seconds)


async def call(run, session, sql, label, *, parent=None, offset=None, page_size=None, job_attempt_id=None):
    sql=readonly_sql(sql);timeout=run.policy['timeout_seconds'];qhash=digest(sql)
    qpath=run.path/'queries'/(qhash+'.sql')
    if not qpath.exists():qpath.write_text(sql+'\n')
    for attempt in range(1,run.policy['retry_count']+2):
        eid=None;duration=0;queued_seconds=0
        try:
            async with query_slot(run,label=label,query_hash=qhash,parent=parent,attempt=attempt) as ticket:
                queued_seconds=ticket['wait_seconds']
                eid=run.event('tool.started',tool='doris.exec_query',label=label,query_hash=qhash,
                    query_path=str(qpath.relative_to(run.path)),parent_id=parent,attempt=attempt,offset=offset,page_size=page_size,
                    capacity_request_id=ticket['request_event_id'],queue_wait_seconds=queued_seconds,
                    parameters={'max_rows':page_size or run.policy['page_size'],'timeout':timeout},
                    **({'job_attempt_id':job_attempt_id} if job_attempt_id else {}))
                t=time.monotonic()
                try:
                    raw=await session.call_tool('exec_query',{'sql':sql,'max_rows':page_size or run.policy['page_size'],'timeout':timeout},
                        read_timeout_seconds=timedelta(seconds=timeout+30))
                finally:
                    duration=round(time.monotonic()-t,6)
            response=decode(raw)
            if not response.get('success'):raise ValueError(str(response.get('error',response))[:4000])
            rows=response.get('data')
            if not isinstance(rows,list) or len(rows)!=response.get('row_count'):raise ValueError('工具返回数据或行数无效')
            evidence=run.evidence({'request':{'sql':sql,'query_hash':qhash,'offset':offset,'page_size':page_size},'response':response},
                kind='query-result',parent_event=eid,source='configured:doris')
            run.event('tool.finished',parent_event_id=eid,status='success',duration_seconds=duration,queue_wait_seconds=queued_seconds,
                evidence_id=evidence,rows=len(rows),actual_query=response.get('metadata',{}).get('query'))
            return evidence,response
        except asyncio.CancelledError:
            if eid is not None:
                run.event('tool.finished',parent_event_id=eid,status='cancelled',duration_seconds=duration,queue_wait_seconds=queued_seconds)
            raise
        except Exception as exc:
            if eid is not None:
                run.event('tool.finished',parent_event_id=eid,status='error',duration_seconds=duration,queue_wait_seconds=queued_seconds,
                    error={'type':type(exc).__name__,'message':str(exc)[:4000]})
            if attempt>run.policy['retry_count']:raise
            await asyncio.sleep(min(attempt,3))


_IN_FLIGHT = {}


async def execute_job(run,session,j,force=False):
    # Same-run callers await one actual job. Never share across frozen runs or
    # different SQL/purpose/filter contracts. CLI mutations additionally hold
    # the existing per-run process lock.
    key = (str(run.path.resolve()), j['job_id'])
    running = _IN_FLIGHT.get(key)
    if running:
        if running[0] != digest(j): raise ValueError('相同任务ID存在不同范围或核对目的')
        result = await asyncio.shield(running[1])
        run.event('query.reused', job_id=j['job_id'], reuse_kind='in_flight')
        return result
    task = asyncio.create_task(_execute_job(run,session,j,force))
    _IN_FLIGHT[key] = (digest(j), task)
    try:
        return await task
    finally:
        _IN_FLIGHT.pop(key, None)


async def _execute_job(run,session,j,force=False):
    old=run.job_record(j['job_id'])
    if old and old['execution_status']=='success' and not force:
        from acceptance_validate import verify_job
        failures = verify_job(run,j)
        if failures:
            raise ValueError('已存成功任务不能通过完整复核：'+str(failures))
        run.event('query.reused', job_id=j['job_id'], reuse_kind='verified_result', record_sha256=digest(old))
        return old
    if old:
        run.event('query.retry_planned', job_id=j['job_id'], reason='explicit_force' if force else 'previous_'+old['execution_status'])
    run.writable();start=now();eids=[];offset=0;expected=None
    from acceptance_job_versions import begin,finish
    binding=begin(run,j,old,start)
    base={**{k:v for k,v in j.items() if k!='sql'},'sql_hash':digest(j['sql']),**binding}
    try:
        page_size=int(run.policy['page_size'])
        while True:
            eid,data=await call(run,session,paginated(j,page_size,offset),j['family'],parent=j['job_id'],offset=offset,page_size=page_size,job_attempt_id=binding['attempt_id'])
            eids.append(eid);rows=data['data']
            totals={int(r['total_rows']) for r in rows}
            if len(totals)>1:raise ValueError('页内总行数不一致')
            total=next(iter(totals)) if totals else 0
            if expected is None:expected=total
            if total!=expected:raise ValueError('分页期间总行数变化或缺页')
            if len(rows)>page_size or offset+len(rows)>expected:raise ValueError('分页边界无效')
            offset+=len(rows)
            if offset==expected:break
            if not rows or len(rows)!=page_size:raise ValueError('结果截断：尚未取齐预期行数')
        record={**base,'execution_status':'success',
            'evidence_ids':eids,'expected_rows':expected,'row_count':offset,'started_at':start,'finished_at':now()}
        run.job_rows_from_record(record,j['job_id'])
        finish(run,record)
        print(json.dumps({'job':j['family'],'site':j['site'],'level':j.get('level'),'entity':j.get('entity'),'rows':offset,'status':'success'},ensure_ascii=False),flush=True)
        return record
    except asyncio.CancelledError:
        finish(run,{**base,'execution_status':'cancelled',
            'evidence_ids':eids,'started_at':start,'finished_at':now(),'reason':'execution interrupted'})
        raise
    except Exception as exc:
        finish(run,{**base,'execution_status':'failed',
            'evidence_ids':eids,'error':str(exc),'started_at':start,'finished_at':now()})
        print(json.dumps({'job':j['family'],'site':j['site'],'status':'failed','error':str(exc)},ensure_ascii=False),flush=True)
        return run.job_record(j['job_id'])


async def scan(run,jobs,config=None,force=False):
    records=[]
    async with connect(run,config) as session:
        for j in jobs:records.append(await execute_job(run,session,j,force=force))
    return records
