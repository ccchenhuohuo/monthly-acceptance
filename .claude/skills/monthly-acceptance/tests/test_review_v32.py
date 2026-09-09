"""v3.2 attacks and positive workflow. All observations are explicitly synthetic."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from acceptance_core import Run,  digest, read, write
from legacy_fixture import create_run
import acceptance_investigation as inv
import acceptance_review as gate
from acceptance_investigation_proof import verify_result_proof
from test_diagnostics import db, manifest
from test_investigation_proof import prepared, CAP, RACK, NORMAL


def loc(eid, row, checks, pointer='/records/0'):
    keys = {k: row[k] for k in ('site', 'month', 'month_dt', 'path', 'std_path', 'raw_path', 'platform', 'sku_id',
                              'product_id', 'seed_product_id') if k in row}
    return {'evidence_id': eid, 'pointer': pointer, 'record_sha256': digest(row), 'object_keys': keys,
            'fields': list(row), 'purpose': 'Read this exact synthetic record to test the asserted scope and values.',
            'check_ids': list(checks), 'role': 'support'}


@pytest.fixture
def v32(tmp_path):
    base = tmp_path/'acceptance'; base.mkdir()
    (base/'config.yaml').write_text('站点起始月份: {DE: 2024-01}\n')
    (base/'项目范围.md').write_text('Explicitly synthetic v3.2 adversarial tests')
    write(base/'policy-overrides.json', {'version':'3.2.0', 'review_gate':{'enabled':True},
        'case_workflow':{'enabled':False}, 'coverage_gate':{'enabled':False}})
    run = Run(create_run(base, Path(__file__).resolve().parents[1], '2026-07'))
    eid = run.evidence({'synthetic': 'scoped alarm, never causal evidence'}, kind='synthetic_alarm')
    candidate = {'candidate_id':'fixture', 'site':'DE', 'level':3, 'path':'["a","b","c"]',
                 'month':'2026-07', 'comparison':None, 'kind':'contract_scope_missing',
                 'priority':'high', 'evidence_ids':[eid]}
    write(run.path/'candidates.json', [candidate]); write(run.path/'samples.json', [])
    run.event('independent_scan.frozen', candidate_hash=digest([candidate]))
    inv.prepare(run, '/root')
    task = inv.assign(run, '/root', '/root/investigator', ['candidate:fixture'])
    worker = Run(run.path/task['worker_dir'])
    ob = inv.all_obligations(run)['candidate:fixture']
    row = {'site':'DE', 'month':'2026-07', 'path':'["a","b","c"]', 'product_id':'synthetic-one',
           'spus':1, 'bands':100, 'units':7, 'amount':42}
    eid = worker.evidence({'records':[row], 'provenance':'Explicit synthetic fixture'}, kind='synthetic_detail')
    locator = loc(eid, row, ob['required_checks'])
    answer = {'obligation_id':ob['obligation_id'], 'scope':copy.deepcopy(ob['scope']),
        'contract_sha256':ob['contract']['sha256'], 'fulfillment_mode':'direct',
        'conclusion':'This synthetic path fixture establishes only the fixture scope.',
        'next_action':'Retain the fully reviewed synthetic test result.', 'business_verdict':'pass',
        'repair_state':'not_needed', 'defect_confirmed':False,
        'status_dimensions':{'execution':'success', 'investigation':'completed', 'defect':'not_found',
                             'cause':'explained', 'impact':'quantified', 'review':'pending', 'acceptability':'pass'},
        'checks':{name:{'status':'verified', 'finding':'Exact synthetic record covers this test question.',
                       'actions':['Read the explicitly scoped original fixture.'],
                       'counterevidence':'Compared the alternate synthetic branch.', 'evidence_ids':[eid],
                       'evidence_locators':[copy.deepcopy(locator)]} for name in ob['required_checks']},
        'impact':{metric:{'state':'measured', 'exposure':row[metric], 'unexplained':0,
                         'unit':'synthetic units', 'basis':'All observations in the explicit synthetic fixture.',
                         'evidence_ids':[eid]} for metric in inv.METRICS}}
    payload = {'task_id':task['task_id'], 'agent_id':task['agent_id'], 'attempt':task['attempt'], 'answers':[answer]}
    write(worker.path/'submission.json', payload)
    return dict(run=run, task=task, worker=worker, ob=ob, row=row, payload=payload, answer=answer)


def resave(case): write(case['worker'].path/'submission.json', case['payload'])


def submit(case): return inv.submit(case['run'], '/root', case['task']['task_id'])


def approval(case):
    run, task, ob = (case[k] for k in ('run', 'task', 'ob'))
    stored = read(inv.home(run)/'submissions'/(task['task_id']+'.json'))
    answer = stored['payload']['answers'][0]
    eid = answer['checks'][ob['required_checks'][0]]['evidence_ids'][0]
    locator = loc(eid, case['row'], ob['required_checks'])
    material = gate.material_digest(stored['sha256'], answer, None, run.get_evidence)
    calculations = [{'metric':m, 'method':'sum', 'operands':[{'locator':copy.deepcopy(locator), 'field':m}],
                     'result':case['row'][m], 'target':'/answer/impact/'+m+'/exposure',
                     'meaning':'Rebuilt full synthetic '+m+' denominator.'} for m in inv.METRICS]
    entry = {'obligation_id':ob['obligation_id'], 'decision':'accept',
        'reason':'Checked scoped original rows, arithmetic, alternate branch and claim boundary.',
        'scope_check':'Exact synthetic DE path and July 2026.', 'conclusion_scope':ob['scope'],
        'evidence_checked':[eid], 'material_sha256':material, 'raw_evidence_checks':[locator],
        'check_reviews':{n:{'decision':'accept', 'reason':'Compared the original fixture fields.'} for n in ob['required_checks']},
        'alternative_explanations':[{'hypothesis':'A different fixture path was substituted.',
            'status':'rejected_with_evidence', 'resolution':'Exact raw identity fields match the assigned path.',
            'evidence_locators':[copy.deepcopy(locator)]}],
        'remaining_uncertainty':{'description':'Synthetic test only; no live market judgment.',
                                 'acceptance_effect':'No real market data is accepted by this test.'},
        'recomputations':calculations,
        'independent_review':{'reviewer_id':'/root/independent', 'material_sha256':material, 'decision':'accept',
            'method':'counterexample_recalculation', 'method_difference':'Recalculate an explicit alternate denominator counterexample.',
            'raw_evidence_checks':[copy.deepcopy(locator)], 'reason':'Independent test of scoped amount rather than reading the summary.',
            'recomputations':[copy.deepcopy(calculations[-1])],
            'counterexamples':[{'hypothesis':'A mismatched amount could still pass the arithmetic gate.',
                                'result':'Raw amount is 42; substitution of 43 fails recomputation.',
                                'test':{'recomputation_index':0, 'relation':'eq', 'expected':43, 'outcome':False},
                                'evidence_locators':[copy.deepcopy(locator)]}]}}
    return {'reviewer_id':'/root', 'submission_sha256':stored['sha256'], 'obligations':[entry]}


def test_contract_has_complete_obligations_history_and_frozen_policy(v32):
    contract = v32['ob']['contract']
    assert contract['history_window']['history_start'] == '2024-01'
    assert contract['history_window']['required_comparison_months'] == ['2026-07']
    assert {q['check_id'] for q in contract['questions']} == set(v32['ob']['required_checks'])
    assert contract['object_population']['selection'] == 'all_members_in_scope'
    assert contract['completion_conditions'] and contract['blocking_conditions'] and contract['drill_triggers']
    assert contract['sha256'] == digest({k:v for k,v in contract.items() if k != 'sha256'})


def test_v32_positive_review_records_all_separate_dimensions(v32):
    submit(v32)
    inv.review(v32['run'], '/root', v32['task']['task_id'], approval(v32))
    audit = inv.audit(v32['run'])
    assert not audit['errors'] and audit['verified'] == 1 and not audit['substantive_complete']
    assert audit['state_dimensions']['cause']['explained'] == 1
    assert audit['state_dimensions']['review']['approved'] == 1


@pytest.mark.parametrize('attack', ['wrong_site','wrong_month','wrong_path','missing_locator','scope_shrink','fake_approved','failed_complete'])
def test_true_but_unrelated_evidence_or_unperformed_work_is_rejected(v32, attack):
    answer = v32['answer']
    check = answer['checks']['path_identity']
    if attack.startswith('wrong_'):
        row = {**v32['row'], {'wrong_site':'site', 'wrong_month':'month', 'wrong_path':'path'}[attack]:
               {'wrong_site':'US', 'wrong_month':'2024-01', 'wrong_path':'["unrelated"]'}[attack]}
        eid = v32['worker'].evidence({'records':[row]}, kind='synthetic_unrelated_record')
        check.update(evidence_ids=[eid], evidence_locators=[loc(eid, row, ['path_identity'])])
    elif attack == 'missing_locator': check.pop('evidence_locators')
    elif attack == 'scope_shrink': answer['contract_sha256'] = '0'*64
    elif attack == 'fake_approved': answer['status_dimensions']['review'] = 'approved'
    elif attack == 'failed_complete': answer['status_dimensions']['execution'] = 'failed'
    resave(v32)
    with pytest.raises(ValueError): submit(v32)
    assert inv.audit(v32['run'])['verified'] == 0


@pytest.mark.parametrize('attack', ['no_raw','no_independent','self_independent','stale_independent','wrong_calculation','unrelated_calculation','wrong_scope','summary_only'])
def test_main_review_cannot_approve_without_substantive_checks(v32, attack):
    submit(v32); review = approval(v32); item = review['obligations'][0]
    if attack == 'no_raw': item.pop('raw_evidence_checks')
    elif attack == 'no_independent': item.pop('independent_review')
    elif attack == 'self_independent': item['independent_review']['reviewer_id'] = v32['task']['agent_id']
    elif attack == 'stale_independent': item['independent_review']['material_sha256'] = '0'*64
    elif attack == 'wrong_calculation': item['recomputations'][-1]['result'] = 43
    elif attack == 'unrelated_calculation':
        calc = item['recomputations'][-1]; calc['operands'][0]['field'] = 'units'; calc['result'] = 7
    elif attack == 'wrong_scope': item['conclusion_scope'] = {**v32['ob']['scope'], 'month':'2024-01'}
    elif attack == 'summary_only': item['independent_review']['method'] = 'read_same_summary'
    with pytest.raises(ValueError): inv.review(v32['run'], '/root', v32['task']['task_id'], review)
    assert inv.audit(v32['run'])['verified'] == 0


def test_material_mutation_after_substantive_approval_invalidates_review(v32):
    submit(v32); inv.review(v32['run'], '/root', v32['task']['task_id'], approval(v32))
    v32['answer']['conclusion'] = 'Changed after approval'; resave(v32)
    audit = inv.audit(v32['run'])
    assert audit['errors'] and not audit['substantive_complete']


@pytest.mark.parametrize('state', ['failed','timeout','capacity_pending','returned'])
def test_failed_timed_out_capacity_or_returned_work_keeps_real_checkpoint(v32, state):
    run, task = v32['run'], v32['task']
    checkpoint = inv.record_progress(run, '/root', task['task_id'], state, {
        'reason':'Synthetic interrupted attempt', 'resume_action':'Continue exact remaining comparison',
        'attempted_actions':['Read the first fixture; stopped before validation.'],
        'remaining_work':{'candidate:fixture':['Validate every outstanding check and get independent review.']}, 'evidence_ids':[]})
    assert inv.audit(run)['states'][state] == 1 and inv.audit(run)['verified'] == 0
    assert read(inv.home(run)/checkpoint['progress_path'])['worker_files_sha256']
    with pytest.raises(ValueError, match='可提交状态'): submit(v32)
    inv.requeue(run, '/root', task['task_id'], 'Resume saved checkpoint')
    new = inv.assign(run, '/root', '/root/replacement', task['obligation_ids'])
    assert new['attempt'] == 2 and new['previous_task_ids'] == [task['task_id']]


def test_review_returns_require_concrete_gaps_and_replacement_must_respond(v32):
    submit(v32); review = approval(v32); item = review['obligations'][0]
    item['decision'] = 'return'
    with pytest.raises(ValueError, match='具体缺口'): inv.review(v32['run'], '/root', v32['task']['task_id'], review)
    item['return_items'] = [{'check_id':'path_identity', 'gap':'Counterexample not covered.',
                             'required_action':'Read the actual alternate object and compare exact identity.'}]
    inv.review(v32['run'], '/root', v32['task']['task_id'], review)
    inv.requeue(v32['run'], '/root', v32['task']['task_id'], 'Address exact review gap')
    new = inv.assign(v32['run'], '/root', '/root/replacement', ['candidate:fixture'])
    assert len(new['unresolved_return_items']) == 1
    payload = copy.deepcopy(v32['payload']); payload.update(task_id=new['task_id'], attempt=new['attempt'], agent_id=new['agent_id'])
    # Reuse normalized parent evidence so the only unfulfilled requirement is the return.
    stored = read(inv.home(v32['run'])/'submissions'/(v32['task']['task_id']+'.json'))
    payload['answers'] = stored['payload']['answers']
    def prefix(obj):
        if isinstance(obj, dict):
            return {k:['main:'+x for x in v] if k == 'evidence_ids' else 'main:'+v if k == 'evidence_id' else prefix(v) for k,v in obj.items()}
        return [prefix(v) for v in obj] if isinstance(obj,list) else obj
    payload = prefix(payload)
    write(v32['run'].path/new['worker_dir']/'submission.json', payload)
    with pytest.raises(ValueError, match='退回项未逐项'): inv.submit(v32['run'], '/root', new['task_id'])


def upgrade(prepared, manifest):
    policy = manifest['policy']; policy['version'] = '3.2.0'; policy['review_gate'] = {'enabled':True}
    policy['investigation_workflow'].update(max_unexplained_ratio=.01, max_unexplained_band_pp=.5)
    worker = prepared['worker']; worker.manifest['policy'] = copy.deepcopy(policy); worker.policy = worker.manifest['policy']
    worker.manifest['policy_hash'] = digest(policy); write(worker.path/'run.json', worker.manifest)


def cause(prepared, pids, claim_id='mechanism'):
    worker = prepared['worker']; group = prepared['answer']['population_proof']['entity_assessments'][0]
    links = []
    for pid in pids:
        for eid in group['evidence_ids']:
            data = worker.get_evidence(eid)['response']['data']
            for i,row in enumerate(data):
                if row['product_id'] == pid and row['source_layer'] == 'std' and row['month_dt'][:7] in ('2024-05','2024-06'):
                    links.append(loc(eid,row, ['sku_identity'], '/response/data/'+str(i)))
    return {'claim_id':claim_id, 'product_ids':pids, 'metrics':list(inv.METRICS), 'status':'supported',
            'causal_basis':'direct_mechanism', 'mechanism':'Explicit synthetic fixture mechanism only.',
            'alternative_explanations':'Compare alternate synthetic identity and unit branch.', 'evidence_locators':links}


def population(prepared, manifest, claims):
    upgrade(prepared, manifest)
    prepared['answer']['population_proof']['entity_assessments'][0]['cause_claims'] = claims
    return verify_result_proof(manifest, prepared['candidate'], prepared['answer'], prepared['worker'], check_claims=False)


def test_verified_defect_does_not_automatically_explain_growth(prepared, manifest):
    result = population(prepared, manifest, [])
    assert result['cohort']['verified'] == 0 and len(result['investigated_product_ids']) == 3
    assert result['computed_impact']['units']['unexplained'] == 120
    assert result['contribution_components']['units']['positive'] == 100
    assert result['contribution_components']['units']['negative'] == 20
    assert len(result['continuation_requirements']) == 4


def test_one_small_defect_leaves_large_residual_and_generates_followups(prepared, manifest):
    result = population(prepared, manifest, [cause(prepared, [RACK])])
    assert result['verified_product_ids'] == [RACK]
    assert result['computed_impact']['units']['unexplained'] == 120
    assert any(f['metric'] == 'units' and set(f['product_ids']) == {CAP,NORMAL} for f in result['continuation_requirements'])


def test_all_entity_mechanisms_with_scoped_evidence_can_reconcile(prepared, manifest):
    result = population(prepared, manifest, [cause(prepared,[CAP,RACK,NORMAL])])
    assert result['cohort']['verified'] == 3
    assert all(r['unexplained'] == 0 for r in result['computed_impact'].values())
    assert not result['continuation_requirements']


def test_duplicate_causes_cannot_double_count_same_contribution(prepared, manifest):
    with pytest.raises(ValueError, match='重复解释'):
        population(prepared, manifest, [cause(prepared,[CAP]),cause(prepared,[CAP], 'duplicate')])


def test_cause_real_evidence_from_one_object_cannot_explain_all_objects(prepared, manifest):
    claim = cause(prepared,[RACK]); claim['product_ids'] = [CAP,RACK,NORMAL]
    with pytest.raises(ValueError, match='完整具体对象'): population(prepared, manifest, [claim])


def test_self_reported_explained_amount_is_recomputed_from_complete_rows(prepared, manifest):
    result = population(prepared, manifest, [cause(prepared,[RACK])])
    prepared['answer']['impact'] = result['computed_impact']
    prepared['answer']['impact']['units']['unexplained'] = 0
    with pytest.raises(ValueError, match='自报分母不符'):
        verify_result_proof(manifest, prepared['candidate'], prepared['answer'], prepared['worker'])


def test_correlation_is_not_an_accepted_causal_basis(prepared, manifest):
    claim = cause(prepared,[CAP]); claim['causal_basis'] = 'same_curve'
    with pytest.raises(ValueError, match='相关性'): population(prepared, manifest, [claim])


def test_incomplete_cause_cannot_be_unreserved_pass_even_at_zero_reported_residual(v32):
    v32['answer']['status_dimensions']['cause'] = 'partially_explained'; resave(v32)
    with pytest.raises(ValueError, match='无保留'): submit(v32)


def test_sample_contract_preserves_singular_product_and_sku_identity(v32):
    run = v32['run']
    eid = run.evidence({'synthetic':True}, kind='synthetic_sample')
    write(run.path/'samples.json', [{'sample_id':'single-object', 'site':'DE', 'month':'2026-07',
        'path':'["a","b","c"]', 'level':3, 'product_id':'p1', 'sku_id':'s1', 'platform':'Amazon', 'evidence_ids':[eid]}])
    ob = next(o for o in inv.expected_obligations(run) if o['source_type'] == 'sample')
    assert ob['scope']['product_ids'] == ['p1'] and ob['scope']['sku_ids'] == ['s1'] and ob['scope']['platforms'] == ['Amazon']


def test_historical_multiobject_multimonth_cannot_be_replaced_with_latest_sample(v32):
    ob = copy.deepcopy(v32['ob']); ob['source_type'] = 'historical'
    ob['required_checks'] = ['historical_object_match']; ob['requires_impact'] = False
    ob['scope'].update(months=['2026-06','2026-07'], product_ids=['p1','p2'])
    ob['contract'] = gate.contract(v32['run'].manifest, ob)
    answer = copy.deepcopy(v32['answer']); answer['scope'] = ob['scope']; answer['contract_sha256'] = ob['contract']['sha256']
    answer['checks'] = {'historical_object_match':copy.deepcopy(answer['checks']['path_identity'])}
    row = {**v32['row'], 'product_id':'p1'}
    eid = v32['worker'].evidence({'records':[row]}, kind='synthetic_history')
    check = answer['checks']['historical_object_match']
    check.update(evidence_ids=[eid], evidence_locators=[loc(eid,row,['historical_object_match'])])
    with pytest.raises(ValueError, match='全部月份'): inv.verify_answer(v32['run'],ob,answer,v32['worker'].get_evidence)
    rows = [{**v32['row'], 'product_id':pid, 'month':mm} for pid in ['p1','p2'] for mm in ['2026-06','2026-07']]
    eid = v32['worker'].evidence({'records':rows}, kind='synthetic_complete_history')
    check.update(evidence_ids=[eid], evidence_locators=[loc(eid,row,['historical_object_match'],'/records/'+str(i)) for i,row in enumerate(rows)])
    assert inv.verify_answer(v32['run'],ob,answer,v32['worker'].get_evidence) is False


def test_saved_timeout_checkpoint_cannot_be_silently_rewritten(v32):
    task = v32['task']; run = v32['run']
    inv.record_progress(run,'/root',task['task_id'],'timeout',{'reason':'Interrupted synthetic attempt',
        'resume_action':'Restore exact recorded work', 'attempted_actions':['Read first original record'],
        'remaining_work':{'candidate:fixture':['Complete original questions']}, 'evidence_ids':[]})
    inv.requeue(run,'/root',task['task_id'],'Continue saved work')
    v32['answer']['conclusion'] = 'Altered after timeout'; resave(v32)
    with pytest.raises(ValueError, match='断点'): inv.assign(run,'/root','/root/replacement',['candidate:fixture'])


def test_independent_source_cannot_be_a_copy_of_same_evidence_with_new_id(v32):
    submit(v32); review = approval(v32); independent = review['obligations'][0]['independent_review']
    original = independent['raw_evidence_checks'][0]['evidence_id']
    clone = v32['run'].evidence(v32['run'].get_evidence(original), kind='synthetic_duplicate_source')
    independent['raw_evidence_checks'][0]['evidence_id'] = clone
    independent.update(method='independent_source', source_independence='Claimed independence, actually exact same source')
    with pytest.raises(ValueError, match='复制成新ID'): inv.review(v32['run'],'/root',v32['task']['task_id'],review)


def test_counterexample_cannot_report_opposite_of_computed_result(v32):
    submit(v32); review = approval(v32)
    review['obligations'][0]['independent_review']['counterexamples'][0]['test']['outcome'] = True
    with pytest.raises(ValueError, match='反例检验结论'): inv.review(v32['run'],'/root',v32['task']['task_id'],review)


def bounded_case(case):
    run = case['run']
    row = {**case['row'], 'bound_spus':0, 'bound_bands':0, 'bound_units':.02, 'bound_amount':.1}
    source = run.evidence({'records':[row], 'provenance':'Synthetic finite measurement-error control'}, kind='synthetic_bounded_source')
    oid = inv.add_obligations(run,'/root',[{'question':'Check the stated finite measurement bounds of this exact synthetic object.',
        'discovered_from':'Explicit synthetic raw measurement-control record', 'site':'DE', 'month':'2026-07',
        'issue_type':'numerical', 'source_observation':loc(source,row,[]),
        'path':'["a","b","c"]','level':3,'product_ids':['synthetic-one'],'evidence_ids':[source]}])[0]
    ob = inv.all_obligations(run)[oid]
    task = inv.assign(run,'/root','/root/bounded-worker',[oid])
    worker = Run(run.path/task['worker_dir'])
    eid = worker.evidence({'records':[row], 'provenance':'Full synthetic single-object measurement interval'},kind='synthetic_interval')
    link = loc(eid,row,ob['required_checks'])
    answer = copy.deepcopy(case['answer']); answer.update(obligation_id=oid,scope=ob['scope'],contract_sha256=ob['contract']['sha256'])
    template = copy.deepcopy(next(iter(answer['checks'].values())))
    template.update(evidence_ids=[eid], evidence_locators=[copy.deepcopy(link)])
    answer['checks'] = {name:copy.deepcopy(template) for name in ob['required_checks']}
    for name, impact in answer['impact'].items():
        impact.update(evidence_ids=[eid], unexplained={'spus':0,'bands':0,'units':.01,'amount':.05}[name])
    answer['business_verdict'] = 'qualified'
    answer['status_dimensions'].update(cause='partially_explained',acceptability='qualified')
    answer['residual_boundary'] = {'state':'bounded','bound_scope':'recorded_population_only',
        'derivation':'Synthetic instrument-control field gives finite error bounds for the only declared object.',
        'recheck_condition':'Reopen if any interval or object membership changes.',
        'acceptance_effect':'Accept only the represented fixture population with its explicit measurement interval.',
        'evidence_locators':[copy.deepcopy(link)],
        'upper_bounds':{m:row['bound_'+m] for m in inv.METRICS},
        'recomputations':[{'metric':m,'method':'sum','operands':[{'locator':copy.deepcopy(link),'field':'bound_'+m}],
            'result':row['bound_'+m],'meaning':'Read the complete synthetic error interval for '+m} for m in inv.METRICS]}
    payload = {'task_id':task['task_id'],'agent_id':task['agent_id'],'attempt':task['attempt'],'answers':[answer]}
    write(worker.path/'submission.json',payload)
    return dict(run=run,task=task,worker=worker,ob=ob,row=row,payload=payload,answer=answer)


def test_legitimate_nonzero_bounded_residual_can_finish_investigation_without_cause_credit(v32):
    case = bounded_case(v32); submit(case)
    inv.review(case['run'],'/root',case['task']['task_id'],approval(case))
    audit = inv.audit(case['run'])
    assert not audit['errors'] and audit['reviewed_residual'] == 1 and audit['verified'] == 0
    assert audit['substantive_completed'] == 1 and audit['reviewed'] == 1
    assert audit['state_dimensions']['cause']['partially_explained'] == 1
    assert not audit['unreserved_acceptance_eligible']


def test_a_bound_label_without_actual_four_metric_recomputation_cannot_discharge_residual(v32):
    case = bounded_case(v32); case['answer']['residual_boundary'].pop('recomputations'); resave(case)
    with pytest.raises(ValueError, match='上界原始证据复算'): submit(case)


def test_true_small_observed_residual_cannot_hide_a_larger_upper_bound(v32):
    case = bounded_case(v32)
    case['answer']['impact']['units']['exposure'] = 1
    resave(case)
    with pytest.raises(ValueError, match='风险上界超过'): submit(case)


def block(case, receipt, *, kind='missing_external_record', owner='data_provider', fields=None):
    answer = case['answer']; check = answer['checks']['path_identity']
    fields = fields or ['sku_id']; required = 'Original platform SKU for the exact scoped source object'
    check['status'] = 'blocked_external'
    check['evidence_ids'] = sorted(set(check['evidence_ids']+[receipt['evidence_id']]))
    check['blocking'] = {'type':kind,'required_record':required,'provider_role':owner,
        'attempted_access':'Read the recorded source response or actual frozen input inventory.',
        'why_unavailable':'The original external field is absent in the recorded source.',
        'decision_impact':'Identity cannot be verified without the specified original source material.',
        'resume_action':'Recheck the exact object after the named external material is delivered.',
        'blocked_checks':['path_identity'],'attempt_receipts':[receipt],
        'resume_condition':{'trigger':'external_material_available','required_record':required,
            'provider_role':owner,'required_fields':fields,'scope':case['ob']['scope']}}
    answer['business_verdict'] = 'qualified'
    answer['status_dimensions'].update(investigation='blocked_external', cause='unexplained',acceptability='qualified')
    return check['blocking']


def source_field_receipt(case, value=None):
    import asyncio
    from acceptance_transport import call
    row = {**case['row'], 'platform':'Amazon', 'sku_id':value}
    class SyntheticSession:
        async def call_tool(self, name, args, **kwargs):
            assert name == 'exec_query'
            return {'success':True,'data':[row],'row_count':1,'metadata':{'query':args['sql']}}
    eid, _ = asyncio.run(call(case['worker'],SyntheticSession(),'SELECT sku_id FROM synthetic_explicit_fixture', 'Synthetic source field inspection'))
    link = loc(eid,row,['path_identity'],'/response/data/0')
    link['attempt'] = {'method':'source_field_absence', 'missing_fields':['sku_id'],
                       'request_sha256':digest(case['worker'].get_evidence(eid)['request'])}
    return link


@pytest.mark.parametrize('rename_more', [False, True])
def test_budget_relabel_with_an_ordinary_row_cannot_be_external_block(v32, rename_more):
    receipt = copy.deepcopy(v32['answer']['checks']['path_identity']['evidence_locators'][0])
    b = block(v32,receipt)
    b.update(required_record='Unexecuted SQL', attempted_access='No query capacity remained',
             why_unavailable='Allocated runtime exhausted', resume_action='Resume local SQL')
    if rename_more:
        receipt['attempt'] = {'method':'source_field_absence','missing_fields':['sku_id'],'request_sha256':'0'*64}
        b['resume_condition']['required_record'] = b['required_record']
    else:
        b['provider_role'] = 'local worker'; b['resume_condition'] = 'Local capacity available'
    resave(v32)
    with pytest.raises(ValueError): submit(v32)
    assert inv.audit(v32['run'])['verified'] == 0


def test_actual_successful_source_missing_field_can_be_submitted_as_blocked(v32):
    receipt = source_field_receipt(v32)
    block(v32,receipt)
    v32['answer']['checks']['path_identity']['evidence_locators'] = [receipt]
    resave(v32); submit(v32)
    assert inv.task_records(v32['run'])[v32['task']['task_id']]['state'] == 'submitted'
    assert inv.audit(v32['run'])['verified'] == 0


def test_actual_query_with_present_sku_cannot_claim_source_field_absence(v32):
    block(v32,source_field_receipt(v32,'real-sku'))
    resave(v32)
    with pytest.raises(ValueError, match='实际有值'): submit(v32)


def test_missing_field_attachment_without_actual_query_operation_is_rejected(v32):
    row = {**v32['row'],'sku_id':None}
    eid = v32['worker'].evidence({'records':[row]},kind='synthetic_attachment')
    receipt = loc(eid,row,['path_identity'])
    receipt['attempt'] = {'method':'source_field_absence','missing_fields':['sku_id'],'request_sha256':'0'*64}
    block(v32,receipt); resave(v32)
    with pytest.raises(ValueError, match='实际调用完成凭证'): submit(v32)


def test_actual_frozen_input_inspection_supports_specific_missing_contract(v32):
    required = 'Original platform SKU for the exact scoped source object'
    eid = gate.capture_frozen_input_absence(v32['worker'],'inputs/project/provider-contract.json',
        v32['ob']['scope'],required,'contract_owner',['effective_platform_scope'])
    row = v32['worker'].get_evidence(eid)['inspection']
    receipt = {'evidence_id':eid,'pointer':'/inspection','record_sha256':digest(row),
        'object_keys':{'operation':row['operation'],'relative_path':row['relative_path']},'fields':list(row),
        'purpose':'Read the actual frozen inventory absence for the required external contract.',
        'attempt':{'method':'frozen_input_absence'}}
    block(v32,receipt,kind='missing_contract',owner='contract_owner',fields=['effective_platform_scope'])
    resave(v32); submit(v32)
    assert inv.audit(v32['run'])['verified'] == 0


def test_unexecuted_sql_or_existing_input_cannot_be_missing_external_material(v32):
    for path in ['inputs/project/unexecuted.sql', 'inputs/project/config.yaml']:
        with pytest.raises(ValueError):
            gate.capture_frozen_input_absence(v32['worker'],path,v32['ob']['scope'],'Unexecuted SQL','data_provider',['sql_result'])


def test_discovered_semantic_issue_uses_fixed_questions_and_exact_raw_observation(v32):
    run = v32['run']; row = v32['row']
    eid = run.evidence({'records':[row]},kind='synthetic_new_semantic_observation')
    issue = {'question':'Check the object classification against its exact observed source title.',
             'discovered_from':'Explicitly synthetic semantic sample','issue_type':'classification_semantics',
             'site':'DE','month':'2026-07','path':row['path'],'level':3,'product_id':row['product_id'],
             'evidence_ids':[eid],'source_observation':loc(eid,row,[])}
    oid = inv.add_obligations(run,'/root',[issue])[0]
    ob = inv.all_obligations(run)[oid]
    assert ob['required_checks'] == inv.SEMANTIC_CHECKS
    assert ob['source_material']['source_observation'] == issue['source_observation']
    assert ob['source_observation_sha256'] == digest(issue['source_observation'])
    issue['required_checks'] = ['sku_identity']
    with pytest.raises(ValueError, match='所属问题类型'): inv.add_obligations(run,'/root',[issue])
    issue.pop('required_checks'); issue['source_observation']['record_sha256'] = '0'*64
    with pytest.raises(ValueError, match='记录哈希'): inv.add_obligations(run,'/root',[issue])


def test_cause_population_binding_allows_program_traced_new_parent_and_rejects_unrelated(v32):
    row = {**v32['row'],'platform':'Amazon','sku_id':'exact-sku','product_id':'new-parent'}
    eid = v32['worker'].evidence({'records':[row]},kind='synthetic_traced_destination')
    claim = {'claim_id':'migration','product_ids':['old-parent'],'metrics':list(inv.METRICS),'status':'supported',
        'mechanism':'Synthetic verified exact-platform SKU migration to a new parent.',
        'alternative_explanations':'The synthetic counterexample with a different SKU is excluded.',
        'causal_basis':'direct_mechanism','evidence_locators':[loc(eid,row,[])]}
    groups = [{'product_ids':['old-parent'],'status':'verified','evidence_ids':[eid],'cause_claims':[claim]}]
    candidate = {'site':'DE'}
    result = gate.cause_credit(v32['run'].manifest,candidate,groups,v32['worker'].get_evidence,population_records={'old-parent':[row]})
    assert all(value == {'old-parent'} for value in result.values())
    with pytest.raises(ValueError, match='记录无关'):
        gate.cause_credit(v32['run'].manifest,candidate,groups,v32['worker'].get_evidence,
                          population_records={'old-parent':[{**row,'sku_id':'different-sku'}]})


def test_rejected_early_completion_persists_program_residual_tasks_and_requeues_them(tmp_path,db,manifest):
    # Runtime imports avoid a fixture module cycle: shared tests reuse loc().
    from test_shared_proof_v32 import _build, _answer_for_submit, compute
    from test_diagnostics import add, observation
    for pid,before,after in [('tiny',1,2),('major',100,30100),('down',200,100)]:
        for mm,units in [('2024-05-01',before),('2024-06-01',after)]:
            add(db,observation(product_id=pid,sku_id='sku-'+pid,month_dt=mm,count=units,discount_sales=units*10))
    manifest['policy'].pop('architecture', None); manifest['policy']['version']='3.2.0'
    root = tmp_path/'residual-parent'; root.mkdir()
    write(root/'run.json',{**manifest,'run_id':'synthetic-residual-parent','method_version':'3.2.0',
                           'inputs':{},'policy_hash':digest(manifest['policy'])})
    for folder in ('records','evidence','queries'): (root/folder).mkdir()
    case = _build(Run(root),db); ob = _answer_for_submit(case)
    for group in case['answer']['population_proof']['entity_assessments']: group['cause_claims'] = []
    proof = compute(case)
    payload = read(case['worker'].path/'submission.json')
    payload['answers'][0] = case['answer']; payload['answers'][0]['impact'] = proof['computed_impact']
    write(case['worker'].path/'submission.json',payload)
    with pytest.raises(ValueError,match='未解释残余超过'):
        inv.submit(case['parent'],'/root',case['task']['task_id'])
    task = inv.task_records(case['parent'])[case['task']['task_id']]
    record = task['residual_followup_records'][ob['obligation_id']]
    saved = read(inv.home(case['parent'])/record['path'])
    assert saved['continuation_requirements'] == proof['continuation_requirements']
    assert record['pending_count'] > 0 and task['state'] == 'assigned'
    inv.record_progress(case['parent'],'/root',task['task_id'],'capacity_pending',{
        'reason':'Synthetic stopped attempt after all source rows were retained.',
        'resume_action':'Continue every generated residual requirement.', 'attempted_actions':['Rebuilt full original member.'],
        'remaining_work':{ob['obligation_id']:['Investigate every program residual and independently review.']},'evidence_ids':[]})
    inv.requeue(case['parent'],'/root',task['task_id'],'Resume the exact residual requirements.')
    next_task = inv.assign(case['parent'],'/root','/root/residual-replacement',[ob['obligation_id']])
    assert next_task['previous_followups'] and len(inv.all_obligations(case['parent'])) == len(inv.expected_obligations(case['parent']))


def frozen_link(run, path, checks):
    eid = gate.capture_frozen_source(run,path); row = run.get_evidence(eid)['source']
    return {'evidence_id':eid,'pointer':'/source','record_sha256':digest(row),
        'object_keys':{k:row[k] for k in ('source_path','source_sha256')},'fields':['text','parsed'],
        'purpose':'Inspect the actual synthetic frozen configuration and exact source lines.',
        'text_field':'text','lines':[1,len(row['text'].splitlines())],
        'excerpt_sha256':digest('\n'.join(row['text'].splitlines())),
        'role':'frozen_source','check_ids':checks}


def test_monthless_historical_guard_uses_actual_frozen_config_and_independent_set_counterexample(tmp_path):
    base = tmp_path/'guard';base.mkdir()
    (base/'config.yaml').write_text('站点起始月份: {DE: 2024-01}\n暂不验收站点: [MX, BR]\n')
    (base/'项目范围.md').write_text('Explicit synthetic guard: only DE is in the active scope.\n')
    write(base/'policy-overrides.json',{'version':'3.2.0','review_gate':{'enabled':True},
        'coverage_gate':{'enabled':False},'case_workflow':{'enabled':False}})
    write(base/'legacy-issues.json',[{'issue_id':'synthetic-scope-guard','site':'global','path':'[]',
        'issue_type':'not_applicable_history','first_observed_month':None,'months':[],
        'question':'Check the original excluded countries without claiming their data is correct.'}])
    run = Run(create_run(base,Path(__file__).resolve().parents[1],'2026-07'))
    write(run.path/'candidates.json',[]);write(run.path/'samples.json',[])
    run.event('independent_scan.frozen',candidate_hash=digest([]));inv.prepare(run,'/root')
    oid = 'historical:synthetic-scope-guard';task=inv.assign(run,'/root','/root/guard-worker',[oid])
    worker=Run(run.path/task['worker_dir']);ob=inv.all_obligations(run)[oid]
    link=frozen_link(worker,'inputs/project/config.yaml',ob['required_checks'])
    answer={'obligation_id':oid,'scope':ob['scope'],'contract_sha256':ob['contract']['sha256'],
        'conclusion':'The synthetic DE-only scope excludes MX/BR; their data quality is not evaluated.',
        'next_action':'Reopen the original guard if the active configuration adds either country.',
        'business_verdict':'pass','repair_state':'not_needed','defect_confirmed':False,
        'status_dimensions':{'execution':'success','investigation':'completed','defect':'not_found',
            'cause':'not_applicable','impact':'not_applicable','review':'pending','acceptability':'pass'},
        'checks':{c:{'status':'verified','finding':'The actual frozen scope and exclusion list are disjoint.',
             'actions':['Read the exact original frozen YAML fields.'],'counterevidence':'Including MX/BR would produce a nonempty intersection.',
             'evidence_ids':[link['evidence_id']],'evidence_locators':[copy.deepcopy(link)]} for c in ob['required_checks']}}
    write(worker.path/'submission.json',{'task_id':task['task_id'],'agent_id':task['agent_id'],
                                         'attempt':task['attempt'],'answers':[answer]})
    inv.submit(run,'/root',task['task_id'])
    stored=inv.checked_submission(run,inv.task_records(run)[task['task_id']]);a=stored['payload']['answers'][0]
    link=a['checks'][ob['required_checks'][0]]['evidence_locators'][0]
    material=gate.material_digest(stored['sha256'],a,None,run.get_evidence)
    calc={'method':'set_intersection_count','operands':[{'locator':copy.deepcopy(link),'field':'parsed','value_pointer':p}
          for p in ('/站点起始月份','/暂不验收站点')],'result':0,'meaning':'Actual configured country intersection with excluded countries.'}
    entry={'obligation_id':oid,'decision':'accept','reason':'Exact frozen scope excludes the original countries without judging their data.',
        'scope_check':'Only the non-object configuration guard is accepted.','conclusion_scope':ob['scope'],
        'material_sha256':material,'evidence_checked':[link['evidence_id']],'raw_evidence_checks':[link],
        'check_reviews':{c:{'decision':'accept','reason':'Read the exact original scope fields.'} for c in ob['required_checks']},
        'alternative_explanations':[{'hypothesis':'MX or BR remains included despite the exclusion list.',
            'status':'rejected_with_evidence','resolution':'Actual configured country keys and exclusions are disjoint.','evidence_locators':[link]}],
        'remaining_uncertainty':{'description':'MX/BR data was not inspected.','acceptance_effect':'No MX/BR data quality judgment.'},
        'independent_review':{'reviewer_id':'/root/guard-independent','material_sha256':material,'decision':'accept',
            'method':'counterexample_recalculation','method_difference':'Compute the intersection directly from actual parsed original YAML.',
            'raw_evidence_checks':[link],'reason':'The direct set counterexample supports only the configuration scope.',
            'recomputations':[calc],'counterexamples':[{'hypothesis':'At least one excluded country is active.','result':'The actual intersection count is zero.',
              'evidence_locators':[link],'test':{'recomputation_index':0,'relation':'gt','expected':0,'outcome':False}}]}}
    inv.review(run,'/root',task['task_id'],{'reviewer_id':'/root','submission_sha256':stored['sha256'],'obligations':[entry]})
    assert inv.audit(run)['verified']==1 and not inv.audit(run)['errors']
    bad=copy.deepcopy(entry);bad['independent_review']['counterexamples'][0]['test']['outcome']=True
    with pytest.raises(ValueError,match='反例检验结论'):
        gate.validate_business_review(run,task,ob,a,None,bad,stored['sha256'])


def test_frozen_document_cannot_replace_a_data_object_or_be_a_renamed_attachment(v32):
    link=frozen_link(v32['worker'],'inputs/project/config.yaml',v32['ob']['required_checks'])
    with pytest.raises(ValueError,match='不能替代具体商品'): gate.locator(v32['worker'].get_evidence,link,v32['ob'])
    ob=copy.deepcopy(v32['ob']);ob.update(source_type='historical',requires_impact=False,
        source_material={'issue_type':'not_applicable_history'})
    value=v32['worker'].get_evidence(link['evidence_id'])
    link['evidence_id']=v32['worker'].evidence(value,kind='synthetic_copy')
    with pytest.raises(ValueError,match='实际读取的冻结原文'):gate.locator(v32['worker'].get_evidence,link,ob)
