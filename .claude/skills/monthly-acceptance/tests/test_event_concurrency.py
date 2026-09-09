"""Independent collectors must append one durable, linear evidence journal."""
import multiprocessing
from pathlib import Path
import sys,json
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from acceptance_core import Run,read,write

def append_worker(path,barrier,worker):
    run=Run(path)  # Deliberately created before any process appends.
    barrier.wait()
    for index in range(20):
        run.event('independent.collector',worker=worker,index=index,payload='x'*12000)
        assert run.verify_events()==[]


def journal(tmp_path):
    write(tmp_path/'run.json',{'run_id':'concurrent-evidence-test','policy':{}})
    return Run(tmp_path)


def test_stale_run_instances_use_current_tail(tmp_path):
    first=journal(tmp_path);second=Run(tmp_path)
    first.event('first');second.event('second');first.event('third')
    assert second.verify_events()==[]
    assert [json.loads(x)['type'] for x in second.events_path.read_text().splitlines()]==['first','second','third']


def test_processes_do_not_fork_or_lose_evidence_events(tmp_path):
    run=journal(tmp_path);run.event('start')
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(4)
    workers=[ctx.Process(target=append_worker,args=(str(tmp_path),barrier,i)) for i in range(4)]
    for worker in workers:worker.start()
    for worker in workers:worker.join(25);assert worker.exitcode==0
    assert run.verify_events()==[]
    events=[json.loads(line) for line in run.events_path.read_text().splitlines()]
    assert len(events)==81
    assert {(v['worker'],v['index']) for v in events[1:]}=={(w,i) for w in range(4) for i in range(20)}


def test_partial_or_tampered_tail_is_not_silently_repaired(tmp_path):
    run=journal(tmp_path);run.event('first')
    original=run.events_path.read_bytes();run.events_path.write_bytes(original[:-1])
    with pytest.raises(ValueError,match='尾部不完整'):run.event('second')
    assert run.events_path.read_bytes()==original[:-1]
    v=json.loads(original);v['type']='tampered';run.events_path.write_text(json.dumps(v)+'\n')
    with pytest.raises(ValueError,match='哈希失效'):run.event('second')
    assert run.verify_events()
