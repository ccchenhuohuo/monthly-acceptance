"""Formal shared evidence path: no per-signal worker SQL or forged job records."""
import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from acceptance_core import Run, canonical, digest, read, write
from acceptance_machine import diagnose
from acceptance_shared_proof import verify_shared_result_proof, verify_shared_gap_proof
import acceptance_investigation as inv
from acceptance_queries import job as baseline_job
from test_diagnostics import db, manifest, observation, add, TARGET
from test_machine_v32 import worker, prepare, spec
from test_review_v32 import loc


def _candidate(parent, db, cid='shared-signal', current='2024-06', base='2024-05', comparison='mom'):
    facts = {m: {} for m in ('spus','units','amount')}
    for side, mm in [('base', base), ('current', current)]:
        row = db.execute('SELECT COUNT(DISTINCT product_id),SUM(`count`),SUM(discount_sales) FROM std WHERE site=? AND month_dt=? AND stdcategory1=? AND stdcategory2=? AND stdcategory3=?',
                         ('cn', mm+'-01', *TARGET)).fetchone()
        for metric, value in zip(('spus','units','amount'), row):
            facts[metric][side] = value
    source = baseline_job(parent.manifest, 'category', 'cn', level=3)
    return {'candidate_id': cid, 'kind': 'result_change', 'site': 'cn', 'level': 3, 'path': canonical(TARGET),
            'month': current, 'comparison': comparison, 'priority': 'high',
            'facts': {'base_month': base, 'current_month': current, 'metrics': facts},
            'evidence_ids': parent.job_record(source['job_id'])['evidence_ids'], 'source_jobs': [source['job_id']]}


def _build(parent, db, candidates=None, specifications=None):
    parent.policy['version'] = '3.2.0'
    parent.policy['review_gate'] = {'enabled': True}
    parent.manifest['policy_hash'] = digest(parent.policy)
    write(parent.path/'run.json', parent.manifest)
    if not (parent.path/'inputs').exists():
        (parent.path/'inputs').mkdir()
    initial = specifications or spec()
    prepare(parent, db, initial)
    candidates = candidates or [_candidate(parent, db)]
    write(parent.path/'candidates.json', candidates); write(parent.path/'samples.json', [])
    parent.event('independent_scan.frozen', candidate_hash=digest(candidates))
    inv.prepare(parent, '/root')
    ids = ['candidate:'+c['candidate_id'] for c in candidates]
    task = inv.assign(parent, '/root', '/root/shared-investigator', ids)
    child = Run(parent.path/task['worker_dir'])
    members = [{'member_id': oid, 'obligation_id': oid, 'source_type': 'candidate', 'source_id': c['candidate_id'],
                'source_hash': digest(c), 'site': c['site'], 'level': c['level'], 'path': TARGET,
                'kind': c['kind'], 'month': c['month'], 'current_month': c['month'],
                'base_month': c['facts']['base_month'], 'comparison': c['comparison']}
               for oid, c in zip(ids, candidates)]
    package = {**initial, 'members': members, 'obligation_ids': ids}
    receipt = diagnose(parent, package)
    diagnosis = read(receipt['diagnosis_path'])
    answers = [{'obligation_id': oid, 'population_proof': {'shared_package_id': package['package_id'],
               'shared_diagnosis_sha256': digest(diagnosis), 'member_id': oid, 'entity_assessments': []}}
               for oid in ids]
    return {'parent': parent, 'worker': child, 'task': task, 'package': package, 'diagnosis': diagnosis,
            'candidate': candidates[0], 'candidates': candidates, 'answer': answers[0], 'answers': answers}


@pytest.fixture
def shared(worker, db):
    for pid, before, after, price in [('tiny',1,2,1),('major',100,30100,5),('down',200,100,10)]:
        for mm, units in [('2024-05-01',before),('2024-06-01',after)]:
            add(db, observation(product_id=pid, sku_id='sku-'+pid, month_dt=mm, count=units,
                                discount_sales=units*price, std_brand_name=pid))
    return _build(worker, db)


def compute(case, *, check_claims=False, answer=None, candidate=None):
    return verify_shared_result_proof(case['parent'].manifest, candidate or case['candidate'], answer or case['answer'],
                                      case['worker'], check_claims=check_claims, source_lookup=case['parent'].get_evidence)


def source_locators(case, record_keys):
    from acceptance_machine import IDENTITY_KEYS
    wanted = set(record_keys); seen = set(); links = []
    for eid in case['diagnosis']['evidence_ids']:
        for n, row in enumerate(case['parent'].get_evidence(eid).get('response',{}).get('data',[])):
            if not all(k in row for k in IDENTITY_KEYS):
                continue
            key = canonical([row[k] for k in IDENTITY_KEYS])
            if key in wanted and key not in seen:
                links.append(loc('main:'+eid,row,[],'/response/data/'+str(n)))
                seen.add(key)
    assert seen == wanted
    return links


def assessment(case, pid, metrics=None, status='verified'):
    member = next(m for m in case['diagnosis']['members'] if m['member_id'] == case['answer']['obligation_id'])
    keys = {key for entity in member['entities'] if entity['product_id'] == pid for key in entity['record_keys']}
    links = source_locators(case,keys)
    group = {'product_ids': [pid], 'status': status, 'reason': 'Checked the exact synthetic object and all shared observations.',
             'counterevidence': 'The remaining objects retain independent mechanisms and remain in the population.',
             'evidence_ids': sorted({p['evidence_id'] for p in links}), 'cause_claims': []}
    if metrics:
        group['cause_claims'] = [{'claim_id': 'cause-'+pid, 'product_ids': [pid], 'metrics': metrics,
                                 'mechanism': 'Direct synthetic mechanism, requiring independent semantic review.',
                                 'alternative_explanations': 'Compared the scoped synthetic alternative, not a market claim.',
                                 'status': 'supported', 'causal_basis': 'direct_mechanism',
                                 'evidence_locators': links}]
    return group


def test_shared_formal_proof_rebuilds_complete_denominator_without_worker_queries(shared):
    proof = compute(shared)
    assert proof['proof_type'] == 'shared_machine_population'
    assert proof['anchors']['current']['units'] == 30202
    assert proof['cohort'] == {'all':3,'verified':0,'blocked_external':0,'not_assessed':3}
    assert proof['computed_impact']['units']['exposure'] == 30101
    assert proof['job_files_sha256'] == {}
    assert list((shared['worker'].path/'records').glob('job-*')) == []
    assert all(eid.startswith('main:') for eid in proof['evidence_ids'])
    assert len(proof['evidence_ids']) == len(set(proof['evidence_ids']))
    assert proof['parent_files_sha256'] and proof['source_evidence_sha256']
    assert 'events.jsonl' not in proof['parent_files_sha256']
    assert not any('drill' in jid or jid.startswith('diagnostic-') for jid in proof['job_ids'])


def test_shared_verified_object_without_causal_claim_does_not_explain_change(shared):
    shared['answer']['population_proof']['entity_assessments'] = [assessment(shared,pid) for pid in ('tiny','major','down')]
    proof = compute(shared)
    assert proof['cohort']['not_assessed'] == 0
    assert proof['cohort']['verified'] == 0
    assert proof['investigated_product_ids'] == ['down','major','tiny']
    assert proof['computed_impact']['units']['unexplained'] == 30101


def test_local_cause_leaves_material_residual_and_machine_followup(shared):
    shared['answer']['population_proof']['entity_assessments'] = [assessment(shared,'tiny',['units'])]
    proof = compute(shared)
    assert proof['computed_impact']['units']['unexplained'] == 30100
    assert proof['computed_impact']['amount']['unexplained'] == proof['computed_impact']['amount']['exposure']
    assert proof['cause_credited_product_ids']['units'] == ['tiny']
    assert proof['cohort']['not_assessed'] == 2
    assert any(r['metric'] == 'units' and 'major' in r['product_ids'] for r in proof['continuation_requirements'])


def test_complete_supported_entity_mechanisms_produce_formal_zero_residual_before_separate_review(shared):
    shared['answer']['population_proof']['entity_assessments'] = [assessment(shared,pid,['spus','bands','units','amount']) for pid in ('tiny','major','down')]
    proof = compute(shared)
    assert proof['cohort']['verified'] == 3
    assert all(m['unexplained'] == 0 for m in proof['computed_impact'].values())
    shared['answer']['impact'] = proof['computed_impact']
    assert compute(shared, check_claims=True)['computed_impact'] == proof['computed_impact']
    assert 'main_review' not in proof and 'approved' not in proof


def test_shared_proof_rejects_fake_denominator_or_mixed_legacy_jobs(shared):
    proof = compute(shared); shared['answer']['impact'] = copy.deepcopy(proof['computed_impact'])
    shared['answer']['impact']['units']['exposure'] = 1
    with pytest.raises(ValueError, match='自报分母'):
        compute(shared, check_claims=True)
    shared['answer']['population_proof']['spu_job_id'] = 'drill-fake'
    with pytest.raises(ValueError, match='mutually exclusive'):
        compute(shared)


def test_shared_duplicate_cause_and_unrelated_original_row_are_rejected(shared):
    group = assessment(shared,'tiny',['units'])
    group['cause_claims'].append({**group['cause_claims'][0], 'claim_id':'second'})
    shared['answer']['population_proof']['entity_assessments'] = [group]
    with pytest.raises(ValueError, match='重复解释'):
        compute(shared)
    group = assessment(shared,'tiny',['units'])
    other = assessment(shared,'major',['units'])
    group['cause_claims'][0]['evidence_locators'] = other['cause_claims'][0]['evidence_locators']
    shared['answer']['population_proof']['entity_assessments'] = [group]
    with pytest.raises(ValueError, match='对象无关'):
        compute(shared)


@pytest.mark.parametrize('tamper', ['hash','member','worker','candidate'])
def test_shared_lineage_cannot_be_substituted(shared, tamper):
    if tamper == 'hash':
        shared['answer']['population_proof']['shared_diagnosis_sha256'] = '0'*64
    elif tamper == 'member':
        shared['answer']['population_proof']['member_id'] = 'candidate:unassigned'
    elif tamper == 'worker':
        shared['worker'].manifest['worker_agent'] = '/root/not-assigned'
    else:
        shared['candidate']['facts']['metrics']['units']['current'] = 1
    with pytest.raises(ValueError):
        compute(shared)


def test_changed_original_page_invalidates_shared_proof(shared):
    eid = shared['diagnosis']['evidence_ids'][0]
    meta = read(shared['parent'].path/'records'/(eid+'.json'))
    path = shared['parent'].path/meta['path']
    value = read(path); value['response']['data'][0]['units'] = 999
    write(path,value)
    with pytest.raises(ValueError):
        compute(shared)


def test_cross_platform_same_pid_keeps_gross_cancellation_and_refuses_pid_only_causal_credit(worker,db):
    for platform,before,after in [('Taobao',100,110),('Tmall',100,90)]:
        for mm,units in [('2024-05-01',before),('2024-06-01',after)]:
            add(db, observation(platform=platform, product_id='same', sku_id='sku-same', month_dt=mm,
                                count=units,discount_sales=units*100))
    case = _build(worker,db)
    case['answer']['population_proof']['entity_assessments'] = [assessment(case,'same',['spus','bands','units','amount'])]
    proof = compute(case)
    assert proof['anchors']['current']['spus'] == 1 and proof['platform_entity_population_count'] == 2
    assert proof['computed_impact']['units']['exposure'] == 20
    assert proof['computed_impact']['units']['unexplained'] == 20
    assert proof['ambiguous_cross_platform_product_ids'] == ['same']
    assert proof['cause_credited_product_ids']['units'] == []


def test_formal_comparisons_share_parent_pages_but_never_share_causal_credit(shared):
    # Two explicit obligations can share their population only while retaining
    # separate candidate identity, member and assessment state.
    first = compute(shared)
    assert first['shared_lineage']['member_id'] == 'candidate:shared-signal'
    wrong = copy.deepcopy(shared['answer'])
    wrong['population_proof']['member_id'] = 'candidate:other-comparison'
    with pytest.raises(ValueError, match='assignment'):
        compute(shared, answer=wrong)


def test_mom_yoy_share_complete_original_pages_but_keep_separate_denominators(worker,db):
    worker.manifest['starts']['cn'] = '2023-01'
    write(worker.path/'run.json', worker.manifest)
    for mm,units in [('2023-06-01',10),('2024-05-01',100),('2024-06-01',200)]:
        add(db,observation(product_id='one',sku_id='sku-one',month_dt=mm,count=units,discount_sales=units*100))
    window = spec(window_start='2023-06', window_end='2024-06',members=[],obligation_ids=[])
    prepare(worker,db,window)
    candidates = [_candidate(worker,db,'mom',comparison='mom'),
                  _candidate(worker,db,'yoy',base='2023-06',comparison='yoy')]
    case = _build(worker,db,candidates=candidates,specifications=window)
    first = compute(case,answer=case['answers'][0],candidate=candidates[0])
    second = compute(case,answer=case['answers'][1],candidate=candidates[1])
    assert first['anchors']['base']['units'] == 100
    assert second['anchors']['base']['units'] == 10
    assert first['computed_impact']['units']['exposure'] == 100
    assert second['computed_impact']['units']['exposure'] == 190
    assert first['evidence_ids'] == second['evidence_ids']
    assert first['shared_lineage']['member_id'] != second['shared_lineage']['member_id']
    assert not list((case['worker'].path/'records').glob('job-*'))


def _answer_for_submit(case):
    answer = case['answer']
    answer['population_proof']['entity_assessments'] = [assessment(case,pid,['spus','bands','units','amount']) for pid in ('tiny','major','down')]
    proof = compute(case)
    ob = inv.all_obligations(case['parent'])[answer['obligation_id']]
    locators = []
    for group in answer['population_proof']['entity_assessments']:
        original = copy.deepcopy(group['cause_claims'][0]['evidence_locators'][0])
        original['check_ids'] = list(ob['required_checks'])
        locators.append(original)
    citations = sorted({p['evidence_id'] for p in locators})
    answer.update({'scope': copy.deepcopy(ob['scope']), 'contract_sha256': ob['contract']['sha256'],
        'fulfillment_mode':'direct','conclusion':'All explicitly synthetic entity mechanisms are individually reviewed.',
        'next_action':'Retain a separately reviewed synthetic proof, without making a real market judgment.',
        'business_verdict':'pass','repair_state':'not_needed','defect_confirmed':False,
        'status_dimensions':{'execution':'success','investigation':'completed','defect':'not_found','cause':'explained',
                             'impact':'quantified','review':'pending','acceptability':'pass'},
        'impact':proof['computed_impact'],
        'checks':{name:{'status':'verified','finding':'The complete shared fixture and exact original rows support this synthetic check.',
                       'actions':['Read the shared original evidence and exact member decomposition.'],
                       'counterevidence':'Checked the other synthetic objects and alternate denominator.',
                       'evidence_ids':citations,'evidence_locators':copy.deepcopy(locators)} for name in ob['required_checks']}})
    payload = {'task_id':case['task']['task_id'],'attempt':case['task']['attempt'],'agent_id':case['task']['agent_id'],
               'answers':[answer]}
    write(case['worker'].path/'submission.json',payload)
    return ob


def _review_for_shared(case, ob):
    import acceptance_review as review
    parent,task = case['parent'],case['task']
    stored = read(inv.home(parent)/'submissions'/(task['task_id']+'.json'))
    answer = next(a for a in stored['payload']['answers'] if a['obligation_id'] == ob['obligation_id']); proof = stored['payload']['population_proofs'][ob['obligation_id']]
    typed_key = 'population_proof' if 'population_proof' in answer else 'gap_proof'
    anchors = proof['anchors'].get('base', proof['anchors'])
    anchor_target = '/proof/anchors/base/' if 'base' in proof['anchors'] else '/proof/anchors/'
    evidence = sorted(review.evidence_ids(answer,proof))
    material = review.material_digest(stored['sha256'],answer,proof,parent.get_evidence)
    raw_checks = copy.deepcopy(answer['checks'][ob['required_checks'][0]]['evidence_locators'])
    operands = []
    from acceptance_machine import collection_jobs
    target = collection_jobs(parent.manifest,case['package'])[0]
    for eid in parent.job_record(target['job_id'])['evidence_ids']:
        for i,row in enumerate(parent.get_evidence(eid)['response']['data']):
            if row['month_dt'] == '2024-05-01':
                operand = loc(eid,row,ob['required_checks'],'/response/data/'+str(i))
                operand['role'] = 'historical_context'
                operands.append(operand)
    calculations = [{'metric':m,'method':'sum','operands':[{'locator':copy.deepcopy(p),'field':m} for p in operands],
                     'result':anchors[m],'target':anchor_target+m,
                     'meaning':'Recompute the full synthetic baseline from original shared target rows.'} for m in ('units','amount')]
    entry = {'obligation_id':ob['obligation_id'],'decision':'accept','reason':'Read exact original shared rows, rechecked member scope and causal alternatives.',
             'scope_check':'Full original candidate path and independent comparison window.', 'conclusion_scope':ob['scope'],
             'evidence_checked':evidence,'material_sha256':material,'raw_evidence_checks':raw_checks,
             'check_reviews':{name:{'decision':'accept','reason':'Checked the corresponding original synthetic evidence.'} for name in ob['required_checks']},
             'entity_reviews':[{'assessment_sha256':digest(g),'decision':'accept','reason':'Individually read the exact synthetic mechanism and alternative.'}
                               for g in answer[typed_key]['entity_assessments']],
             'alternative_explanations':[{'hypothesis':'One minor synthetic defect could have explained all changes.',
                 'status':'rejected_with_evidence','resolution':'Full per-object denominators and independent claims prevent that inference.',
                 'evidence_locators':copy.deepcopy(raw_checks)}],
             'remaining_uncertainty':{'description':'Synthetic test proves workflow behavior only.','acceptance_effect':'No real-world batch is accepted by this test.'},
             'proof_recalculation':{'result':{'anchors':proof['anchors'],'computed_impact':proof['computed_impact']},
                                    'assessment':'Rebuilt exact shared population without worker SQL or a worker denominator.'},
             'recomputations':calculations,
             'independent_review':{'reviewer_id':'/root/shared-independent','material_sha256':material,'decision':'accept',
                 'method':'counterexample_recalculation','method_difference':'Test a competing raw baseline value against complete original data.',
                 'reason':'Independent numerical counterexample verifies the original data rather than agreeing with a summary.',
                 'raw_evidence_checks':copy.deepcopy(raw_checks),'recomputations':[copy.deepcopy(calculations[0])],
                 'counterexamples':[{'hypothesis':'Replacing the baseline by one unit would preserve the investigation.',
                     'result':'The complete raw baseline differs from one unit.', 'evidence_locators':copy.deepcopy(raw_checks),
                     'test':{'recomputation_index':0,'relation':'ne','expected':0,'outcome':True}}]}}
    return {'reviewer_id':'/root','submission_sha256':stored['sha256'],'obligations':[entry]}


def test_shared_population_uses_real_submit_and_substantive_review_without_duplicate_collection(shared):
    ob = _answer_for_submit(shared)
    result = inv.submit(shared['parent'],'/root',shared['task']['task_id'])
    assert result['imported_evidence_count'] == 0
    stored = read(inv.home(shared['parent'])/'submissions'/(shared['task']['task_id']+'.json'))
    proof = stored['payload']['population_proofs'][ob['obligation_id']]
    assert proof['proof_type'] == 'shared_machine_population'
    assert all(not eid.startswith('main:') for eid in proof['evidence_ids'])
    assert stored['parent_proof_file_hashes']
    inv.review(shared['parent'],'/root',shared['task']['task_id'],_review_for_shared(shared,ob))
    assert inv.task_records(shared['parent'])[shared['task']['task_id']]['state'] == 'approved'
    assert list((shared['worker'].path/'records').glob('job-*')) == []
    path = shared['parent'].path/'machine/packages'/shared['package']['package_id']/'diagnosis.json'
    value = read(path); value['note'] += ' changed after review'; write(path,value)
    with pytest.raises(ValueError,match='主包|parent|共享'):
        inv.checked_submission(shared['parent'],inv.task_records(shared['parent'])[shared['task']['task_id']])


def test_shared_cli_compute_accepts_only_the_actual_bound_worker_manifest(shared):
    direct = compute(shared)
    cli = verify_shared_result_proof(shared['worker'].manifest,shared['candidate'],shared['answer'],shared['worker'],check_claims=False)
    assert cli == direct
    wrong = copy.deepcopy(shared['worker'].manifest); wrong['run_id'] = 'unrelated-parent'
    with pytest.raises(ValueError,match='owning parent'):
        verify_shared_result_proof(wrong,shared['candidate'],shared['answer'],shared['worker'],check_claims=False)


def test_machine_identity_conflicts_cannot_be_omitted_from_formal_submission(worker,db):
    for platform,units in [('Taobao',1),('Tmall',2)]:
        for mm in ('2024-05-01','2024-06-01'):
            add(db,observation(platform=platform,product_id='shared-id',sku_id='shared-sku',month_dt=mm,count=units,discount_sales=100*units))
    case = _build(worker,db)
    result = compute(case)
    assert result['machine_conflicts'] and result['unresolved_machine_conflict_ids']
    case['answer']['impact'] = result['computed_impact']
    with pytest.raises(ValueError,match='no substantive investigation response'):
        compute(case,check_claims=True)


@pytest.fixture
def shared_gap(worker,db):
    add(db,observation(product_id='gap-parent',sku_id='gap-sku',month_dt='2024-05-01',count=1,discount_sales=8399))
    window = spec(window_start='2024-05',window_end='2024-07',members=[],obligation_ids=[])
    prepare(worker,db,window)
    job = baseline_job(worker.manifest,'category','cn',level=3)
    candidates = [{'candidate_id':'gap-'+mm,'kind':'std_presence_gap','site':'cn','level':3,'path':canonical(TARGET),
                   'month':mm,'comparison':None,'priority':'high','facts':{'base_month':'2024-05'},
                   'evidence_ids':worker.job_record(job['job_id'])['evidence_ids'],'source_jobs':[job['job_id']]}
                  for mm in ('2024-06','2024-07')]
    case = _build(worker,db,candidates=candidates,specifications=window)
    for answer in case['answers']:
        answer['gap_proof'] = answer.pop('population_proof')
    return case


def gap_compute(case,index=0,check_claims=False):
    return verify_shared_gap_proof(case['parent'].manifest,case['candidates'][index],case['answers'][index],case['worker'],
                                    case['parent'].get_evidence,check_claims=check_claims)


def test_gap_comparisons_share_prior_baseline_but_recompute_every_original_month(shared_gap):
    june,july = gap_compute(shared_gap,0),gap_compute(shared_gap,1)
    assert june['anchors'] == july['anchors'] == {'month':'2024-05','spus':1,'nrows':1,'units':1,'amount':8399}
    assert june['expected_destination_cells'] == 2
    assert july['expected_destination_cells'] == 4
    assert june['destination_state_counts'] == {'no_observation':2}
    assert july['destination_state_counts'] == {'no_observation':4}
    assert june['computed_impact']['amount']['exposure'] == july['computed_impact']['amount']['exposure'] == 8399
    assert june['evidence_ids'] == july['evidence_ids']
    assert june['cohort']['not_assessed'] == 1 and july['cohort']['not_assessed'] == 1
    assert not list((shared_gap['worker'].path/'records').glob('job-*'))


def _gap_answers(case):
    obligations = inv.all_obligations(case['parent'])
    for index,answer in enumerate(case['answers']):
        ob=obligations[answer['obligation_id']]
        member=next(m for m in case['diagnosis']['members'] if m['member_id']==answer['obligation_id'])
        keys={c['seed_key'] for c in member['destination_cells']}
        keys.update(k for c in member['destination_cells'] for k in c['record_keys'])
        links=source_locators(case,keys)
        source_ids=sorted({p['evidence_id'] for p in links})
        check_links=copy.deepcopy(links)
        for locator in check_links:
            locator['check_ids']=list(ob['required_checks']);locator['role']='historical_context'
        group={'product_ids':['gap-parent'],'status':'verified','reason':'Checked full synthetic pre-gap identity and every missing calendar cell.',
               'counterevidence':'Future observations were not substituted for the prior seed or skipped gap cells.',
               'evidence_ids':source_ids,'cause_claims':[{'claim_id':'fixture-gap-'+str(index),'product_ids':['gap-parent'],
                   'metrics':['spus','bands','units','amount'],'mechanism':'Synthetic controlled source omission in this test only.',
                   'alternative_explanations':'Compared an explicit wrong-baseline hypothesis against preserved original evidence.',
                   'status':'supported','causal_basis':'controlled_comparison','evidence_locators':links}]}
        answer['gap_proof']['entity_assessments']=[group]
        proof=gap_compute(case,index)
        answer.update({'scope':copy.deepcopy(ob['scope']),'contract_sha256':ob['contract']['sha256'],'fulfillment_mode':'direct',
            'conclusion':'The synthetic gap is explicitly confined to its complete pre-gap population and all natural months.',
            'next_action':'Retain synthetic proof only; no real gap cause is accepted by this test.',
            'business_verdict':'pass','repair_state':'not_needed','defect_confirmed':False,
            'status_dimensions':{'execution':'success','investigation':'completed','defect':'not_found','cause':'explained',
                                 'impact':'quantified','review':'pending','acceptability':'pass'},
            'impact':proof['computed_impact'],
            'checks':{name:{'status':'verified','finding':'Checked the synthetic shared baseline and explicit destination population.',
                           'actions':['Inspect each preserved prior seed and every natural gap month.'],
                           'counterevidence':'Rejected substituting recovery or net-zero arithmetic for gap evidence.',
                           'evidence_ids':source_ids,'evidence_locators':copy.deepcopy(check_links)} for name in ob['required_checks']}})
    payload={'task_id':case['task']['task_id'],'attempt':case['task']['attempt'],'agent_id':case['task']['agent_id'],'answers':case['answers']}
    write(case['worker'].path/'submission.json',payload)
    return [obligations[a['obligation_id']] for a in case['answers']]


def test_shared_gap_formal_submit_and_review_reuses_parent_pages_once(shared_gap):
    obs=_gap_answers(shared_gap)
    submitted=inv.submit(shared_gap['parent'],'/root',shared_gap['task']['task_id'])
    assert submitted['imported_evidence_count'] == 0
    approvals=[_review_for_shared(shared_gap,ob) for ob in obs]
    value=approvals[0];value['obligations']=[row for approval in approvals for row in approval['obligations']]
    inv.review(shared_gap['parent'],'/root',shared_gap['task']['task_id'],value)
    assert inv.task_records(shared_gap['parent'])[shared_gap['task']['task_id']]['state']=='approved'
    assert not list((shared_gap['worker'].path/'records').glob('job-*'))


def test_shared_gap_does_not_accept_altered_destination_cell_or_forged_impact(shared_gap):
    proof=gap_compute(shared_gap)
    shared_gap['answers'][0]['impact']=copy.deepcopy(proof['computed_impact'])
    shared_gap['answers'][0]['impact']['amount']['exposure']=1
    with pytest.raises(ValueError,match='自报分母'):
        gap_compute(shared_gap,check_claims=True)
    path=shared_gap['parent'].path/'machine/packages'/shared_gap['package']['package_id']/'diagnosis.json'
    value=read(path);value['members'][0]['destination_cells'].pop();write(path,value)
    with pytest.raises(ValueError,match='hash differs'):
        gap_compute(shared_gap)


@pytest.mark.parametrize('field,value', [('platform','UnrelatedPlatform'),('month_dt','2024-04-01'),
                                         ('std_path','["other","scope","path"]'),('sku_id','other-sku')])
def test_shared_same_pid_unrelated_actual_record_cannot_replace_population_credit(shared,field,value):
    group=assessment(shared,'tiny',['units'])
    reference=group['cause_claims'][0]['evidence_locators'][0]
    raw=copy.deepcopy(shared['parent'].get_evidence(reference['evidence_id'][5:])['response']['data'][int(reference['pointer'].rsplit('/',1)[-1])])
    raw[field]=value
    eid=shared['worker'].evidence({'records':[raw],'purpose':'Explicitly unrelated synthetic attachment'},kind='synthetic_wrong_scope')
    group['evidence_ids'].append(eid)
    group['cause_claims'][0]['evidence_locators'][0]=loc(eid,raw,[])
    shared['answer']['population_proof']['entity_assessments']=[group]
    with pytest.raises(ValueError,match='实际完整比较|完整比较窗口'):
        compute(shared)


def test_shared_cause_requires_both_actual_comparison_months(shared):
    group=assessment(shared,'tiny',['units'])
    group['cause_claims'][0]['evidence_locators'].pop()
    shared['answer']['population_proof']['entity_assessments']=[group]
    with pytest.raises(ValueError,match='未覆盖完整比较窗口'):
        compute(shared)


def test_shared_gap_cause_cannot_replace_prior_seed_with_same_pid_attachment(shared_gap):
    _gap_answers(shared_gap)
    answer=shared_gap['answers'][0]
    group=answer['gap_proof']['entity_assessments'][0]
    reference=group['cause_claims'][0]['evidence_locators'][0]
    raw=copy.deepcopy(shared_gap['parent'].get_evidence(reference['evidence_id'][5:])['response']['data'][int(reference['pointer'].rsplit('/',1)[-1])])
    raw['sku_id']='unrelated-gap-sku'
    eid=shared_gap['worker'].evidence({'records':[raw]},kind='synthetic_wrong_gap_seed')
    group['evidence_ids'].append(eid)
    group['cause_claims'][0]['evidence_locators']=[loc(eid,raw,[])]
    with pytest.raises(ValueError,match='实际完整比较|完整比较窗口'):
        gap_compute(shared_gap)
