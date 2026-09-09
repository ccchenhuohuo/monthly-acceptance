"""The 0.0.1 release label must not disable the panorama architecture's gates."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from test_questions_v4 import v4,topic,answer,submit,main_review
from test_question_identity import db,manifest,row,add
from test_convergence_platform import run as sql_rows
from acceptance_core import Run,create_run,write,read,digest
from pathlib import Path
from acceptance_queries import plan,job,platform_identity
import acceptance_questions as q
import acceptance_investigation as investigation
import acceptance_review as review
import acceptance_coverage as coverage
import acceptance_panorama as panorama

RELEASE='0.0.1'
ARCHITECTURE='panorama_questions'


def test_release_run_and_all_active_artifact_protocols(v4):
    run,index=v4
    assert run.manifest['method_version']==RELEASE
    assert run.manifest['architecture']==ARCHITECTURE
    assert run.policy['version']==RELEASE
    assert run.policy['architecture']==ARCHITECTURE
    assert index['protocol']==RELEASE
    assert q.plan_data(run)['protocol']==RELEASE
    assert q._registry(run)['protocol']==RELEASE
    assert q.case_status(run)['protocol']==RELEASE
    assert read(run.path/'coverage-plan.json')['payload']['schema']=='monthly-acceptance-coverage/'+RELEASE
    case=q.register(run,'main',topic(v4))
    obligation=q.obligations(run)[case['obligation_ids'][0]]
    assert obligation['contract']['protocol']==RELEASE
    frozen=q.freeze_discovery(run,{'independence_disclosure':'测试合成数据，明确披露非盲。'})
    assert frozen['payload']['protocol']==RELEASE
    assert q.prepare_sampling(run)['payload']['protocol']==RELEASE
    assert coverage.prepare_history(run)['payload']['protocol']==RELEASE
    assert q.coverage_status(run)['protocol']==RELEASE
    assert any(j['family']=='platform' for j in plan(run.manifest))


def test_architecture_keeps_low_version_gates_enabled(v4):
    run,_=v4
    m=deepcopy(run.manifest)
    m['policy']['coverage_gate']['enabled']=False
    m['policy']['review_gate']['enabled']=False
    projected=SimpleNamespace(manifest=m,policy=m['policy'])
    assert panorama.enabled(projected)
    assert investigation.enabled(projected)
    assert coverage.enabled(projected)
    assert review.enabled(projected)
    assert platform_identity(m)


def test_release_seal_rebuild_rejects_supplied_success(v4):
    run,_=v4
    fake={'execution_state':'complete','business_verdict':'pass','errors':[]}
    write(run.path/'report-content.json',{'summary':['伪造调用方通过，不代表门槛已完成。'],'findings':[]})
    write(run.path/'validation.json',fake)
    (run.path/'报告.md').write_text('故意提供未经当前门槛验证的报告。')
    with pytest.raises(ValueError,match='门槛|完整|调查|主审'):run.seal(fake)
    assert not (run.path/'sealed.json').exists()


def test_live_run_cannot_disable_seal_gates_by_relabeling_manifest(v4):
    run,_=v4
    run.manifest.pop('architecture');run.manifest['method_version']='0.0.0'
    fake={'execution_state':'complete','business_verdict':'pass','errors':[]}
    write(run.path/'validation.json',fake)
    (run.path/'报告.md').write_text('调用者提供的未验证报告。')
    with pytest.raises(ValueError):run.seal(fake)
    assert not (run.path/'sealed.json').exists()


def test_frozen_release_policy_prevents_forged_old_manifest_seal(v4):
    run,_=v4;m=deepcopy(run.manifest)
    m.pop('architecture');m['policy'].pop('architecture')
    m['method_version']='0.0.0';m['policy']['version']='0.0.0'
    m['policy_hash']=digest(m['policy'])
    write(run.path/'run.json',m)
    fake={'execution_state':'complete','business_verdict':'pass','errors':[]}
    write(run.path/'validation.json',fake)
    (run.path/'报告.md').write_text('冻结政策未改变，不能靠重写manifest回退门槛。')
    with pytest.raises(ValueError):Run(run.path).seal(fake)
    assert not (run.path/'sealed.json').exists()


@pytest.mark.parametrize('damage',['manifest_marker','policy_marker','both_markers','version_mismatch','disabled_questions'])
def test_reopening_release_manifest_rejects_gate_downgrade(v4,damage):
    run,_=v4;m=deepcopy(run.manifest)
    if damage in ('manifest_marker','both_markers'):m.pop('architecture')
    if damage in ('policy_marker','both_markers'):m['policy'].pop('architecture')
    if damage=='version_mismatch':m['method_version']='0.0.0'
    if damage=='disabled_questions':m['policy']['question_workflow']['enabled']=False
    write(run.path/'run.json',m)
    with pytest.raises(ValueError,match='架构|版本|门槛'):Run(run.path)


@pytest.mark.parametrize('override',[{'version':'3.3.0'},{'architecture':'legacy'},{'question_workflow':{'enabled':False}}])
def test_new_release_cannot_override_back_to_legacy(tmp_path,override):
    base=tmp_path/'audit';base.mkdir()
    (base/'config.yaml').write_text('站点起始月份: {DE: 2024-01}\n')
    (base/'项目范围.md').write_text('DE Amazon controlled release fixture.')
    write(base/'policy-overrides.json',override)
    with pytest.raises(ValueError,match='架构|版本|回退|新运行'):
        create_run(base,Path(__file__).resolve().parents[1],'2024-02')


def test_release_submission_still_requires_actual_main_review(v4):
    run,_=v4
    case=q.register(run,'main',topic(v4))
    task=investigation.assign(run,'main','worker',case['obligation_ids'])
    submit(run,task,answer(run,task))
    assert investigation.audit(run)['verified']==0
    investigation.review(run,'main',task['task_id'],main_review(run,task))
    assert investigation.audit(run)['verified']==1


def release_manifest(manifest):
    m=deepcopy(manifest)
    m.update(method_version=RELEASE,architecture=ARCHITECTURE,data_month='2024-06')
    m['policy'].update(version=RELEASE,architecture=ARCHITECTURE)
    m['policy']['sample_per_category']=100
    m['policy']['price_bands']['cn']=[20,50]
    return m


def test_release_sql_keeps_cross_platform_identity(db,manifest):
    m=release_manifest(manifest)
    add(db,row(month_dt='2024-06-01',product_id='shared',sku_id=None,discount_sales=10))
    add(db,row(month_dt='2024-06-01',platform='Tmall',product_id='shared',sku_id=None,discount_sales=90))
    assert sql_rows(db,m,'fingerprint',entity='std')[0]['spus']==2
    assert sql_rows(db,m,'category',level=3)[0]['spus']==2
    assert {(r['band'],r['spus']) for r in sql_rows(db,m,'bands',level=3)}=={(0,1),(2,1)}
    assert job(m,'sample','cn')['key_fields']==['path','platform','product_id']
    assert len(sql_rows(db,m,'sample'))==2


@pytest.mark.parametrize('old_version',['3.3.0','4.0.0','4.0.1'])
def test_unmarked_old_frozen_sql_retains_original_identity(db,manifest,old_version):
    m=release_manifest(manifest)
    m.pop('architecture');m['policy'].pop('architecture')
    m['method_version']=old_version;m['policy']['version']=old_version
    add(db,row(month_dt='2024-06-01',product_id='shared',discount_sales=10))
    add(db,row(month_dt='2024-06-01',platform='Tmall',product_id='shared',discount_sales=90))
    assert not platform_identity(m)
    assert sql_rows(db,m,'fingerprint',entity='std')[0]['spus']==1
    assert job(m,'sample','cn')['key_fields']==['path','product_id']
