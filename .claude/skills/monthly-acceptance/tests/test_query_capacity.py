"""Real processes exercise local SQL slots; every SQL response here is synthetic."""
import asyncio
import copy
import json
import multiprocessing
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'scripts'))
from acceptance_core import Run,  digest, parallel_query_limit, read, write
from legacy_fixture import create_run
from acceptance_transport import call


def make_run(directory, *, capacity=1, parent=None, retries=0):
    directory.mkdir(parents=True)
    policy = read(SKILL / 'policies/default.json')
    policy.pop('architecture',None);policy['version']='3.1.0'
    if capacity is None:
        policy['investigation_workflow'].pop('max_parallel_queries', None)
    else:
        policy['investigation_workflow']['max_parallel_queries'] = capacity
    policy['retry_count'] = retries
    manifest = {'run_id': directory.name, 'method_version': '3.1.0', 'policy': policy,
        'policy_hash': digest(policy), 'inputs': {}, 'sites': ['DE'],
        'starts': {'DE': '2024-01'}, 'data_month': '2024-07'}
    if parent is not None:
        manifest['parent_run'] = str(parent.path)
    write(directory / 'run.json', manifest)
    for name in ['queries', 'records', 'evidence']:
        (directory / name).mkdir()
    run = Run(directory)
    run.event('synthetic.run.created')
    return run


def events(run):
    return [json.loads(line) for line in run.events_path.read_text().splitlines()]


def response(sql):
    return {'success': True, 'data': [], 'row_count': 0, 'metadata': {'query': sql}}


class ImmediateSession:
    async def call_tool(self, name, args, **kwargs):
        return response(args['sql'])


def process_query(path, barrier, active, peak, counter_lock):
    """Each spawned process owns one Run and therefore one event chain."""
    run = Run(path)

    class Session:
        async def call_tool(self, name, args, **kwargs):
            with counter_lock:
                active.value += 1
                peak.value = max(peak.value, active.value)
            try:
                await asyncio.sleep(0.3)
                return response(args['sql'])
            finally:
                with counter_lock:
                    active.value -= 1

    barrier.wait(timeout=15)
    asyncio.run(call(run, Session(), 'SELECT 1', 'synthetic-process-capacity'))


def run_processes(runs):
    context = multiprocessing.get_context('spawn')
    barrier = context.Barrier(len(runs))
    active, peak = context.Value('i', 0), context.Value('i', 0)
    counter_lock = context.Lock()
    processes = [context.Process(target=process_query,
        args=(str(run.path), barrier, active, peak, counter_lock)) for run in runs]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=20)
        assert [p.exitcode for p in processes] == [0] * len(processes)
        assert active.value == 0
        return peak.value
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)


@pytest.mark.parametrize('capacity', [1, 2])
def test_real_processes_share_parent_capacity_without_shared_ledger_writes(tmp_path, capacity):
    parent = make_run(tmp_path / 'parent', capacity=capacity)
    workers = [make_run(parent.path / 'workers' / f'worker-{i}', capacity=capacity,
        parent=parent) for i in range(3)]
    runs = [parent, *workers]
    assert run_processes(runs) == capacity
    slots = parent.path / 'resources' / 'query-slots'
    assert len(list(slots.glob('*.lock'))) == capacity
    for run in runs:
        recorded = events(run)
        assert len([e for e in recorded if e['type'] == 'tool.started']) == 1
        assert all(e['run_id'] == run.manifest['run_id'] for e in recorded)
        assert Run(run.path).verify_events() == []
        queued = [e for e in recorded if e['type'] == 'query.capacity.queued']
        assert queued[0]['resource_directory'] == str(slots)
    assert not any((w.path / 'resources').exists() for w in workers)


def test_independent_runs_are_not_accidentally_limited_by_each_other(tmp_path):
    runs = [make_run(tmp_path / f'independent-{i}') for i in range(2)]
    assert run_processes(runs) == 2
    assert runs[0].query_resource_scope()[0] != runs[1].query_resource_scope()[0]


def test_old_frozen_policy_defaults_to_one_without_mutating_it(tmp_path):
    parent = make_run(tmp_path / 'legacy-parent', capacity=None)
    worker = make_run(parent.path / 'worker', parent=parent, capacity=None)
    before = (parent.path / 'run.json').read_bytes()
    assert parent.query_resource_scope() == worker.query_resource_scope()
    assert worker.query_resource_scope()[1] == 1
    asyncio.run(call(worker, ImmediateSession(), 'SELECT 1', 'legacy-policy'))
    assert (parent.path / 'run.json').read_bytes() == before
    assert parallel_query_limit({}) == 1


@pytest.mark.parametrize('value', [0, -1, 9, True, False, 1.0, '2', None])
def test_invalid_capacity_is_rejected_before_any_sql(tmp_path, value):
    run = make_run(tmp_path / 'invalid')
    run.policy['investigation_workflow']['max_parallel_queries'] = value
    run.manifest['policy_hash'] = digest(run.policy)
    with pytest.raises(ValueError, match='max_parallel_queries'):
        asyncio.run(call(run, ImmediateSession(), 'SELECT 1', 'invalid-policy'))
    assert not [e for e in events(run) if e['type'] == 'tool.started']


@pytest.mark.parametrize('value', [1, 2, 8])
def test_valid_capacity_is_frozen_at_creation(tmp_path, value):
    base = tmp_path / 'project'
    base.mkdir()
    (base / 'config.yaml').write_text('站点起始月份: {DE: 2024-01}\n')
    (base / '项目范围.md').write_text('Synthetic fixture\n')
    write(base / 'policy-overrides.json', {'investigation_workflow': {'max_parallel_queries': value}})
    run = Run(create_run(base, SKILL, '2024-07'))
    assert run.policy['investigation_workflow']['max_parallel_queries'] == value
    assert run.policy['investigation_workflow']['max_parallel_agents'] == 8
    assert run.verify_inputs() == []


def test_invalid_override_does_not_create_a_run(tmp_path):
    base = tmp_path / 'project'
    base.mkdir()
    (base / 'config.yaml').write_text('站点起始月份: {DE: 2024-01}\n')
    (base / '项目范围.md').write_text('Synthetic fixture\n')
    write(base / 'policy-overrides.json', {'investigation_workflow': {'max_parallel_queries': 9}})
    with pytest.raises(ValueError, match='max_parallel_queries'):
        create_run(base, SKILL, '2024-07')
    assert not (base / 'runs').exists()


def test_worker_cannot_create_extra_slots_with_a_different_frozen_capacity(tmp_path):
    parent = make_run(tmp_path / 'parent')
    worker = make_run(parent.path / 'worker', parent=parent, capacity=2)
    with pytest.raises(ValueError, match='容量不一致'):
        asyncio.run(call(worker, ImmediateSession(), 'SELECT 1', 'mismatched-policy'))
    assert not (parent.path / 'resources').exists()


def test_parent_policy_hash_changes_and_parent_cycles_are_rejected(tmp_path):
    parent = make_run(tmp_path / 'parent')
    worker = make_run(parent.path / 'worker', parent=parent)
    original = read(parent.path / 'run.json')
    changed = copy.deepcopy(original)
    changed['policy']['retry_count'] = 3
    write(parent.path / 'run.json', changed)
    with pytest.raises(ValueError, match='政策哈希'):
        worker.query_resource_scope()
    original['parent_run'] = str(worker.path)
    write(parent.path / 'run.json', original)
    with pytest.raises(ValueError, match='出现循环'):
        worker.query_resource_scope()


def test_failed_call_releases_slot_before_retry_delay(tmp_path):
    parent = make_run(tmp_path / 'parent')
    retrying = make_run(parent.path / 'retrying', parent=parent, retries=1)
    sibling = make_run(parent.path / 'sibling', parent=parent)

    async def scenario():
        failed = asyncio.Event()

        class Session:
            count = 0

            async def call_tool(self, name, args, **kwargs):
                self.count += 1
                if self.count == 1:
                    failed.set()
                    raise RuntimeError('synthetic transient failure')
                return response(args['sql'])

        job = asyncio.create_task(call(retrying, Session(), 'SELECT 1', 'retry'))
        await failed.wait()
        await asyncio.wait_for(call(sibling, ImmediateSession(), 'SELECT 1', 'during-backoff'), 0.7)
        assert not job.done(), 'the retry backoff must not hold the only SQL slot'
        await job

    asyncio.run(scenario())
    recorded = events(retrying)
    assert len([e for e in recorded if e['type'] == 'query.capacity.released']) == 2
    assert [e['status'] for e in recorded if e['type'] == 'tool.finished'] == ['error', 'success']
    assert [e['attempt'] for e in recorded if e['type'] == 'query.capacity.queued'] == [1, 2]


def test_waiting_and_executing_cancellation_release_capacity_and_keep_local_trace(tmp_path):
    parent = make_run(tmp_path / 'parent')
    running = make_run(parent.path / 'running', parent=parent)
    waiting = make_run(parent.path / 'waiting', parent=parent)
    next_run = make_run(parent.path / 'next', parent=parent)

    async def scenario():
        entered = asyncio.Event()

        class Slow:
            async def call_tool(self, name, args, **kwargs):
                entered.set()
                await asyncio.Event().wait()

        active = asyncio.create_task(call(running, Slow(), 'SELECT 1', 'will-cancel-active'))
        await entered.wait()
        queued = asyncio.create_task(call(waiting, ImmediateSession(), 'SELECT 1', 'will-cancel-waiting'))
        await asyncio.sleep(0.08)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active
        await asyncio.wait_for(call(next_run, ImmediateSession(), 'SELECT 1', 'after-cancellation'), 0.5)

    asyncio.run(scenario())
    waiting_events = events(waiting)
    assert not [e for e in waiting_events if e['type'] == 'tool.started']
    assert not [e for e in waiting_events if e['type'] == 'query.capacity.acquired']
    assert [e['phase'] for e in waiting_events if e['type'] == 'query.capacity.cancelled'] == ['waiting']
    running_events = events(running)
    assert [e['phase'] for e in running_events if e['type'] == 'query.capacity.cancelled'] == ['executing']
    assert len([e for e in running_events if e['type'] == 'query.capacity.released']) == 1
    assert [e['status'] for e in running_events if e['type'] == 'tool.finished'] == ['cancelled']
    assert len(events(parent)) == 1, 'children must not append to the parent business ledger'
    assert all(Run(r.path).verify_events() == [] for r in [parent, running, waiting, next_run])


def process_hold_slot(path, entered):
    class Session:
        async def call_tool(self, name, args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

    asyncio.run(call(Run(path), Session(), 'SELECT 1', 'process-will-exit'))


def test_process_exit_does_not_leave_a_stale_capacity_lock(tmp_path):
    parent = make_run(tmp_path / 'parent')
    dying = make_run(parent.path / 'dying', parent=parent)
    successor = make_run(parent.path / 'successor', parent=parent)
    context = multiprocessing.get_context('spawn')
    entered = context.Event()
    process = context.Process(target=process_hold_slot, args=(str(dying.path), entered))
    try:
        process.start()
        assert entered.wait(timeout=10)
        process.terminate()
        process.join(timeout=5)
        assert process.exitcode is not None

        async def query():
            return await asyncio.wait_for(call(successor, ImmediateSession(), 'SELECT 1', 'after-process-exit'), 1)

        asyncio.run(query())
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)


def test_queue_wait_is_separate_from_actual_sql_elapsed_time(tmp_path):
    parent = make_run(tmp_path / 'parent')
    holder = make_run(parent.path / 'holder', parent=parent)
    waiting = make_run(parent.path / 'waiting', parent=parent)

    async def scenario():
        entered = asyncio.Event()

        class Holder:
            async def call_tool(self, name, args, **kwargs):
                entered.set()
                await asyncio.sleep(0.35)
                return response(args['sql'])

        task = asyncio.create_task(call(holder, Holder(), 'SELECT 1', 'hold-capacity'))
        await entered.wait()
        await call(waiting, ImmediateSession(), 'SELECT 1', 'measure-wait')
        await task

    asyncio.run(scenario())
    recorded = events(waiting)
    acquired = next(e for e in recorded if e['type'] == 'query.capacity.acquired')
    started = next(e for e in recorded if e['type'] == 'tool.started')
    finished = next(e for e in recorded if e['type'] == 'tool.finished')
    assert acquired['wait_seconds'] >= 0.25
    assert started['queue_wait_seconds'] == finished['queue_wait_seconds'] == acquired['wait_seconds']
    assert finished['duration_seconds'] < 0.1


CLI_DRIVER = r'''
import contextlib
import sys
sys.path.insert(0, sys.argv.pop(1))
import acceptance_run as cli
import acceptance_transport as transport

mode = sys.argv.pop(1)
class Session:
    count = 0
    async def call_tool(self, name, args, **kwargs):
        self.count += 1
        if mode == 'fail' or (mode == 'first-fails' and self.count == 1):
            return {'success': False, 'error': 'synthetic SQL failure'}
        return {'success': True, 'data': [], 'row_count': 0, 'metadata': {'query': args['sql']}}

@contextlib.asynccontextmanager
async def fake_connect(run, config=None):
    yield Session()

cli.connect = transport.connect = fake_connect
if sys.argv[1] in ['scan', 'finish-scan']:
    cli.plan = lambda manifest, final=False: [
        {'job_id': 'synthetic-job-' + str(i), 'family': 'synthetic', 'site': 'DE',
         'key_fields': [], 'sql': 'SELECT ' + str(i) + ' AS value'} for i in range(2)]
cli.main()
'''


def cli_process(run, command, mode):
    arguments = [command, str(run.path)]
    if command == 'drill':
        write(run.path / 'candidates.json', [{'candidate_id': 'synthetic-candidate', 'kind': 'result_change',
            'site': 'DE', 'path': ['Accessory'], 'level': 1, 'month': '2024-07', 'comparison': 'mom'}])
        arguments += ['--candidate-id', 'synthetic-candidate']
    if command == 'diagnose':
        spec = run.path / 'synthetic-spec.json'
        write(spec, {'kind': 'brands', 'site': 'DE', 'path': ['Accessory'], 'level': 1,
            'base_month': '2024-06', 'current_month': '2024-07'})
        arguments += ['--spec', str(spec)]
    if command == 'query':
        sql = run.path / 'synthetic.sql'
        sql.write_text('SELECT 1\n')
        arguments += ['--sql-file', str(sql), '--label', 'synthetic-free-query']
    return subprocess.run([sys.executable, '-c', CLI_DRIVER, str(SKILL / 'scripts'), mode, *arguments],
        text=True, capture_output=True, timeout=20)


@pytest.mark.parametrize('command', ['query', 'drill', 'diagnose', 'scan', 'finish-scan'])
@pytest.mark.parametrize('success', [True, False])
def test_cli_returns_actual_sql_success_and_failure_codes(tmp_path, command, success):
    run = make_run(tmp_path / 'cli-run')
    result = cli_process(run, command, 'ok' if success else 'fail')
    assert (result.returncode == 0) is success, result.stdout + result.stderr
    recorded = events(run)
    started = [e for e in recorded if e['type'] == 'tool.started']
    assert len(started) == (2 if command in ['scan', 'finish-scan'] else 1)
    assert len([e for e in recorded if e['type'] == 'query.capacity.acquired']) == len(started)
    assert len([e for e in recorded if e['type'] == 'query.capacity.released']) == len(started)
    assert [e['status'] for e in recorded if e['type'] == 'tool.finished'] == [('success' if success else 'error')] * len(started)
    if command != 'query':
        jobs = [read(p) for p in (run.path / 'records').glob('job-*.json')]
        assert len(jobs) == len(started)
        assert all(j['execution_status'] == ('success' if success else 'failed') for j in jobs)
        if not success:
            assert all(j['error'] == 'synthetic SQL failure' for j in jobs)
    assert Run(run.path).verify_events() == []


@pytest.mark.parametrize('command', ['scan', 'finish-scan'])
def test_scan_finishes_remaining_jobs_before_returning_failure(tmp_path, command):
    run = make_run(tmp_path / 'cli-run')
    result = cli_process(run, command, 'first-fails')
    assert result.returncode != 0
    assert run.job_record('synthetic-job-0')['execution_status'] == 'failed'
    assert run.job_record('synthetic-job-1')['execution_status'] == 'success'
    assert 'synthetic-job-0' in result.stderr
