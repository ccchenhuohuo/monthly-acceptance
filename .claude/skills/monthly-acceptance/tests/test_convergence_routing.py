"""Current entrypoints cannot silently fall back to the legacy denominator."""
from pathlib import Path
import sys
from copy import deepcopy
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import acceptance_core as core
import acceptance_run as cli
import acceptance_investigation as inv
import acceptance_questions as q
from test_questions_v4 import v4, topic


def project(tmp_path, override):
    base = tmp_path / 'project'
    base.mkdir()
    (base / 'config.yaml').write_text('站点起始月份: {DE: 2024-01}\n')
    (base / '项目范围.md').write_text('DE Amazon')
    core.write(base / 'policy-overrides.json', override)
    return base


@pytest.mark.parametrize('override', [
    {'question_workflow': {'enabled': False}},
    {'version': '3.3.0'},
])
def test_new_run_rejects_legacy_protocol_override(tmp_path, override):
    base = project(tmp_path, override)
    with pytest.raises(ValueError, match='回退分母'):
        core.create_run(base, Path(__file__).resolve().parents[1], '2024-02')
    assert not (base / 'runs').exists()


def test_unused_legacy_tolerance_does_not_gate_new_run(tmp_path):
    base = project(tmp_path, {'materiality': {'basis': '', 'ratio_tolerance': None},
                             'investigation_workflow': {'max_unexplained_ratio': None}})
    run = core.Run(core.create_run(base, Path(__file__).resolve().parents[1], '2024-02'))
    assert q.enabled(run)


@pytest.mark.parametrize('command,extra', [
    ('case-queue', []),
    ('machine-collect', ['--spec', '/nonexistent']),
    ('machine-diagnose', ['--spec', '/nonexistent']),
    ('investigation-amend-scope', ['--coordinator', 'main', '--obligation-id', 'any', '--file', '/nonexistent']),
    ('investigation-add', ['--coordinator', 'main', '--file', '/nonexistent']),
])
def test_retired_commands_fail_before_evidence_or_query_work(v4, monkeypatch, command, extra):
    run, _ = v4
    before = run._event_snapshot()
    monkeypatch.setattr(sys, 'argv', ['acceptance_run.py', command, str(run.path), *extra])
    with pytest.raises(ValueError, match='3.x 入口'):
        cli.main()
    assert run._event_snapshot() == before
    assert not (run.path / 'machine').exists()
    assert not (run.path / 'investigations/amendments').exists()


def test_default_assignment_records_current_question_queue(v4):
    run, _ = v4
    c = q.register(run, 'main', topic(v4))
    task = inv.assign(run, 'main', 'worker')
    assert task['assignment_source'] == 'question_scope_queue'
    assert task['obligation_ids'] == c['obligation_ids']


def test_freeze_omits_generated_test_cache(tmp_path):
    import shutil
    source = Path(__file__).resolve().parents[1]
    skill = tmp_path / 'skill'
    shutil.copytree(source, skill, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    (skill / '.pytest_cache').mkdir()
    (skill / '.pytest_cache/README.md').write_text('generated test cache, not Skill instructions')
    base = project(tmp_path, {})
    run = core.Run(core.create_run(base, skill, '2024-02'))
    assert not any('.pytest_cache' in p or '__pycache__' in p for p in run.manifest['inputs'])
    assert (run.path / 'inputs/skill/SKILL.md').is_file()


def test_direct_legacy_amend_does_not_create_parallel_contract(v4):
    run, _ = v4
    c = q.register(run, 'main', topic(v4))
    before = run._event_snapshot()
    with pytest.raises(ValueError, match='question-register'):
        inv.amend_contract_scope(run, 'main', c['obligation_ids'][0], {}, 'legacy bypass', [])
    assert before == run._event_snapshot()
    assert not (run.path / 'investigations/amendments').exists()
