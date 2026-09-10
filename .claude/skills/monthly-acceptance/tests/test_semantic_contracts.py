"""Negative-path contracts for current verdict, scope and evidence semantics."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from acceptance_core import Run, create_run, digest, write
from acceptance_scope import constraint_coverage
from test_questions_v4 import v4, topic, answer, submit, main_review
from test_execution import record, rows_for
from acceptance_queries import plan
import acceptance_investigation as inv
import acceptance_panorama as panorama
import acceptance_questions as questions
import acceptance_review as review


def test_undetermined_never_defaults_to_pass():
    base={'business_verdicts':{'undetermined':1},'unreserved_acceptance_eligible':True}
    assert questions.aggregate_verdict(base)=='undetermined'
    assert questions.aggregate_verdict({'business_verdicts':{'pass':2,'undetermined':1},
                                        'unreserved_acceptance_eligible':True})=='undetermined'
    assert questions.aggregate_verdict({'business_verdicts':{},'unreserved_acceptance_eligible':True})=='pass'
    assert questions.aggregate_verdict({'business_verdicts':{'pass':1},
                                        'unreserved_acceptance_eligible':False})=='undetermined'


def test_incomplete_execution_cannot_become_a_business_pass():
    assert questions.aggregate_verdict({'business_verdicts':{'pass':1},
                                        'unreserved_acceptance_eligible':True},
                                       errors=['coverage_incomplete'])=='undetermined'
    assert questions.aggregate_verdict({'business_verdicts':{'reject':1},
                                        'unreserved_acceptance_eligible':False},
                                       limitations=['missing_contract'])=='undetermined'


def test_global_mapping_constraint_requires_all_platforms_and_layers(v4):
    """A constraint without an explicit scope means every frozen platform and layer."""
    run,_=v4
    constraint={'site':'DE','month':'2024-02','family':'duplication','rule':'mapping','scope':{}}
    raw=deepcopy(topic(v4)['scope_units'][0])
    raw['required_checks']=['scope','duplication','counterevidence','impact']
    unit=questions._unit(run,raw)
    # DE freezes Amazon alone, so one unit covering both layers owns it fully.
    assert questions._owns_constraint(run,unit,constraint)
    for gap,value in (('source_layers',['std']),('months',['2024-01']),
                      ('required_checks',['scope','conservation','counterevidence','impact'])):
        partial=deepcopy(unit);partial[gap]=value
        assert not questions._owns_constraint(run,partial,constraint)
    # cn freezes two platforms. The unit validator would reject them for DE, so
    # coverage is exercised directly: one platform cannot claim the pair, and an
    # explicit union of both can.
    constraint['site']='cn'
    taobao=deepcopy(unit);taobao['site']='cn';taobao['platforms']=['Taobao']
    tmall=deepcopy(unit);tmall['site']='cn';tmall['platforms']=['Tmall']
    assert not questions._owns_constraint(run,taobao,constraint)
    required,covered=constraint_coverage(run,constraint,[taobao,tmall])
    assert len(required)==4 and covered==required


def _evidence_link(row, *, role='target', relation=None):
    link={'evidence_id':'e1','pointer':'/response/data/0','record_sha256':digest(row),
          'fields':list(row),'object_keys':{k:row[k] for k in ('site','platform','month_dt','path')},
          'purpose':'验证当前问题范围内的结果行。','scope_role':role}
    if relation is not None:link['scope_relation']=relation
    return link


def test_quantitative_locator_rejects_real_row_from_other_platform_or_path():
    row={'site':'cn','platform':'Tmall','month_dt':'2024-02-01','path':'["Other"]','units':3}
    allowed={'e1':{'job':{'family':'category','site':'cn','entity':'std','key_fields':['month_dt','path']},
                   'platforms':['Taobao','Tmall']}}
    scope={'site':'cn','platforms':['Taobao'],'source_layers':['std'],'months':['2024-02'],
           'paths':[['Target']],'path_layer':'std'}
    with pytest.raises(ValueError,match='目标证据超出|证据路径'):
        questions._locator(lambda _: {'response':{'data':[row]}},_evidence_link(row),allowed,scope,require_target=True)


def test_cross_scope_context_requires_relation_and_cannot_be_quantified():
    row={'site':'cn','platform':'Tmall','month_dt':'2024-02-01','path':'["Other"]','units':3}
    allowed={'e1':{'job':{'family':'category','site':'cn','entity':'std','key_fields':['month_dt','path']},
                   'platforms':['Taobao','Tmall']}}
    scope={'site':'cn','platforms':['Taobao'],'source_layers':['std'],'months':['2024-02'],
           'paths':[['Target']],'path_layer':'std'}
    link=_evidence_link(row,role='counterevidence')
    with pytest.raises(ValueError,match='跨范围证据与目标的关系'):
        questions._locator(lambda _: {'response':{'data':[row]}},link,allowed,scope)
    link['scope_relation']='同月另一平台的反证，仅用于比较，不计入目标影响。'
    with pytest.raises(ValueError,match='不能计入目标影响'):
        questions._locator(lambda _: {'response':{'data':[row]}},link,allowed,scope,require_target=True)


@pytest.mark.parametrize('change',['context_metric','context_operand','summary_pointer'])
def test_out_of_scope_evidence_cannot_close_a_real_submission(v4,change):
    """The same rules must hold on the real submit path, not only in helpers."""
    run,_=v4
    case=questions.register(run,'main',topic(v4))
    task=inv.assign(run,'main','worker',case['obligation_ids'])
    a=answer(run,task);expected='背景计算不能计入目标指标影响'
    calc=next(c for c in a['calculations'] if c['calculation_id']=='delta_units')
    if change=='context_metric':
        calc['scope_role']='context';calc['scope_relation']='另一国家同月的对照，仅用于比较。'
    if change=='context_operand':
        expected='不能计入目标影响'
        calc['operands'][0]['locator'].update(scope_role='counterevidence',
                                              scope_relation='范围外的反证行，仅说明该差额不是本问题造成。')
    if change=='summary_pointer':
        expected='查询定位必须指向实际结果行'
        a['checks']['scope']['raw_locators'][0]['pointer']='/response/data'
    with pytest.raises(ValueError,match=expected):submit(run,task,a)
    assert not inv.audit(run)['substantive_complete']


@pytest.mark.parametrize('drift',['rebuilt','table_missing',None])
def test_upstream_rebuild_during_a_run_is_caught_even_when_totals_hold(v4,drift):
    """Country fingerprints miss a pure recomposition; delivery identity must not."""
    run,_=v4
    for job in plan(run.manifest,True):
        rows=rows_for(run,job)
        if job['family']=='delivery':
            if drift=='rebuilt':rows[0]['update_time']='2026-03-04T05:06:07'
            if drift=='table_missing':rows=rows[:1]
        record(run,job,rows)
    errors=questions.validate(run,False)['errors']
    hit=[e for e in errors if e.startswith('delivery_')]
    if drift is None:
        assert not hit
    else:
        assert hit==['delivery_rebuilt_during_run:flywheel.monthly_sales_wide_new'] if drift=='rebuilt' \
            else hit==['delivery_tables_changed']
    # The aggregates never moved, so the existing fingerprint guard stays silent.
    assert not [e for e in errors if e.startswith('fingerprint_')]


def _quantity(oid,metric,value=0):
    return {'obligation_id':oid,'metric':metric,'calculation_id':'delta_'+metric,'value':value,
            'statement':'限定两月的'+metric+'净差，按原字段单位。'}


@pytest.mark.parametrize('shape',['ok','missing','wrong_value','wrong_metric_calc','foreign_scope'])
def test_report_numbers_must_be_the_audited_calculation(v4,shape):
    """Structure-only report checks let an unaudited figure through; this closes it."""
    run,_=v4
    case=questions.register(run,'main',topic(v4))
    task=inv.assign(run,'main','worker',case['obligation_ids'])
    submit(run,task,answer(run,task));inv.review(run,'main',task['task_id'],main_review(run,task))
    oid=case['obligation_ids'][0]
    quantities=[_quantity(oid,m) for m in ('spus','units','amount')]
    if shape=='missing':quantities=[]
    if shape=='wrong_value':quantities[1]['value']=99
    if shape=='wrong_metric_calc':quantities[1]['calculation_id']='delta_amount'
    if shape=='foreign_scope':quantities[1]['obligation_id']='question_scope:other:unit'
    content={'summary':['x'],'findings':[{'title':'t','obligation_ids':[oid],'quantities':quantities}]}
    audit=inv.audit(run,content)
    omitted=lambda *metrics:['已审量化影响未在报告中按原复算列出：'+oid+':'+m for m in metrics]
    expected={'ok':([],[]),
        # A missing figure is a declared gap, not a false claim.
        'missing':([],omitted('spus','units','amount')),
        'wrong_value':(['报告数字与已审复算结果不一致：delta_units'],omitted('units')),
        'wrong_metric_calc':(['报告数字未绑定该指标的已审复算：delta_amount'],omitted('units')),
        'foreign_scope':(['报告数字引用不属于该结论的范围：question_scope:other:unit'],omitted('units'))}[shape]
    assert sorted(audit['errors'])==sorted(expected[0]),audit['errors']
    assert sorted(audit['limitations'])==sorted(expected[1]),audit['limitations']
    # Either way the run can no longer be an unreserved pass.
    assert audit['unreserved_acceptance_eligible'] is (shape=='ok')


def _internal_run(tmp_path,amount=100):
    base=tmp_path/'audit';base.mkdir()
    (base/'config.yaml').write_text('站点起始月份: {DE: 2024-01}\n')
    (base/'项目范围.md').write_text('Amazon DE internal reconciliation fixture.')
    write(base/'policy-overrides.json',{'page_size':5})
    ledger=base/'内部数据'/'2024-02';ledger.mkdir(parents=True)
    write(ledger/'ledger.json',{'site':'DE','period':'2024-02','unit':'EUR','amount':amount})
    run=Run(create_run(base,Path(__file__).resolve().parents[1],'2024-02'))
    run.register_plan(plan(run.manifest));questions.prepare_coverage(run)
    for job in plan(run.manifest):
        record(run,job,rows_for(run,job));write(run.path/'records'/(job['job_id']+'-plan.json'),job)
    panorama.build(run);questions.prepare(run,'main')
    return run


def _reconciliation(run,result=0,**contract):
    job=next(x for x in plan(run.manifest) if x['family']=='category' and x['level']==0)
    rec=run.job_record(job['job_id']);eid=rec['evidence_ids'][0]
    rows=run.get_evidence(eid)['response']['data']
    n=next(i for i,r in enumerate(rows) if str(r['month_dt']).startswith('2024-02'))
    market={'evidence_id':eid,'pointer':'/response/data/'+str(n),'record_sha256':digest(rows[n]),
        'fields':['amount','month_dt','path'],'object_keys':{'month_dt':rows[n]['month_dt']},'purpose':'大盘同期金额。'}
    ieid=review.capture_frozen_source(run,'inputs/internal/ledger.json');row=run.get_evidence(ieid)['source']
    internal={'evidence_id':ieid,'pointer':'/source','record_sha256':digest(row),
        'object_keys':{k:row[k] for k in ('source_path','source_sha256')},'fields':['parsed'],'purpose':'内部台账原文。'}
    calc={'calculation_id':'diff_amount','method':'difference','meaning':'内部与大盘同期金额差。','result':result,
        'operands':[{'locator':internal,'field':'parsed','value_pointer':'/amount'},{'locator':market,'field':'amount'}]}
    side={'grain':'product_id','period':'2024-02','unit':'EUR'}
    base_contract={'left':{'source':'inputs/internal/ledger.json',**side},'right':{'source':'大盘 std 聚合',**side},
        'match_keys':['site','month_dt'],'tolerance':{'absolute':0.01},
        'difference':{'calculation_id':'diff_amount','value':0},
        'unmatched':{'left_only_rows':0,'right_only_rows':0}}
    base_contract.update(contract)
    return {'check_id':'internal_reconciliation:DE','reviewer_id':'main','investigator_id':'worker','status':'passed',
        'finding':'内部台账与大盘同期金额一致。','action':'逐笔核对冻结台账与大盘聚合。','counterevidence':'仅覆盖已冻结内部口径。',
        'internal_source_paths':['inputs/internal/ledger.json'],'internal_evidence_ids':[ieid],
        'evidence_ids':[ieid,eid],'raw_locators':[market,internal],'reconciliations':[calc],'reconciliation_contract':base_contract}


def test_matching_internal_reconciliation_passes(tmp_path):
    run=_internal_run(tmp_path)
    assert questions.record_baseline_review(run,'main',_reconciliation(run))['status']=='passed'


@pytest.mark.parametrize('flaw,message',[
    ('currency','unit 不一致'),
    ('period','period 不一致'),
    ('grain','grain 不一致'),
    ('unbound_difference','差异未绑定实际复算'),
    ('lying_difference','差异与复算结果不一致'),
    ('over_tolerance','超出声明容差'),
    ('silent_residual','未匹配残差的具体说明')])
def test_incomparable_sides_cannot_pass_reconciliation(tmp_path,flaw,message):
    """Recomputation proves addition; it never proved the two sides were comparable."""
    over=flaw=='over_tolerance'
    run=_internal_run(tmp_path,amount=105 if over else 100)
    value=_reconciliation(run,result=-5 if over else 0)
    c=value['reconciliation_contract']
    if flaw=='currency':c['right']=dict(c['right'],unit='CNY')
    if flaw=='period':c['right']=dict(c['right'],period='2024-01')
    if flaw=='grain':c['right']=dict(c['right'],grain='sku_id')
    if flaw=='unbound_difference':c['difference']={'calculation_id':'nope','value':0}
    if flaw=='lying_difference':c['difference']={'calculation_id':'diff_amount','value':7}
    if over:c['difference']={'calculation_id':'diff_amount','value':-5}
    if flaw=='silent_residual':c['unmatched']={'left_only_rows':3,'right_only_rows':0}
    with pytest.raises(ValueError,match=message):questions.record_baseline_review(run,'main',value)


def test_missing_reconciliation_contract_is_a_declared_gap_not_a_hard_stop(tmp_path):
    """The review can still be recorded; it just cannot be an unreserved pass."""
    run=_internal_run(tmp_path)
    value=_reconciliation(run);value.pop('reconciliation_contract')
    saved=questions.record_baseline_review(run,'main',value)
    assert saved['status']=='passed' and '未声明左右来源' in saved['contract_limitation']
    assert any('未声明左右来源' in x for x in questions.coverage_status(run)['review_limitations'])


def test_declared_limitations_are_named_in_the_report(tmp_path):
    """A limitation denies an unreserved pass, so hiding it makes the run read as clean."""
    run=_internal_run(tmp_path)
    value=_reconciliation(run);value.pop('reconciliation_contract')
    questions.record_baseline_review(run,'main',value)
    result=questions.validate(run,persist=False)
    assert result['limitations'] and result['business_verdict']=='undetermined'
    questions.render(run)
    report=(run.path/'报告.md').read_text()
    assert '已声明限制' in report
    for limitation in result['limitations']:
        if isinstance(limitation,dict):assert limitation['rule'] in report,limitation['check_id']
        else:assert limitation in report,limitation
