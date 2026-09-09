"""Formal result-population proofs from complete shared machine observations.

No SQL is created, relabeled or copied into a worker. Source evidence remains in
its owning parent package; each assigned comparison independently rebuilds the
numeric population and applies exact-object, exact-metric causal review credit.
"""
from collections import Counter, defaultdict
from pathlib import Path
import json

from acceptance_core import Run, canonical, digest, read, shift
from acceptance_diagnostics import IDENTITY_KEYS, traceable_sku
from acceptance_machine import _compute, _combine_parts, _band, _path
from acceptance_investigation_proof import _close, _number, _unique, _explanation, EVIDENCE_RE
import acceptance_review as substantive


def _hash_file(root, path, hashes):
    path = Path(path).resolve()
    if not path.is_relative_to(root.path):
        raise ValueError('shared proof source file escapes its owning run')
    hashes[str(path.relative_to(root.path))] = digest(path.read_bytes())


def _verified_package(parent, package_id, expected_sha):
    if not isinstance(package_id, str) or not package_id or not all(c.isalnum() or c in '-_' for c in package_id):
        raise ValueError('invalid shared package ID')
    root = parent.path/'machine'/'packages'/package_id
    saved = read(root/'diagnosis.json')
    if digest(saved) != expected_sha:
        raise ValueError('shared diagnosis hash differs from the assigned exact version')
    computed = _compute(parent, saved['spec'], saved.get('cause_contribution_proposals', []), saved.get('requested_risk_bound'))
    if computed != saved:
        raise ValueError('shared machine population/evidence no longer recomputes')
    index = read(root/'index.json')
    if index.get('diagnosis_sha256') != expected_sha or index.get('diagnosis_id') != saved['diagnosis_id']:
        raise ValueError('shared machine evidence index changed')
    if read(root/'conflicts.json') != saved['conflicts']:
        raise ValueError('shared machine conflict index changed')
    from acceptance_core import business_key
    for member in saved['members']:
        if read(root/'members'/(business_key(member['member_id'])+'.json')) != member:
            raise ValueError('shared machine member index changed')
    if not saved['collection_complete']:
        raise ValueError('shared original source collection is incomplete')
    return saved, root


def _binding(manifest, candidate, answer, worker, proof):
    """Read current assignment and immutable original obligations, not worker text."""
    if not substantive.enabled(manifest):
        raise ValueError('shared formal population proof requires the substantive v3.2 review protocol')
    parent_path = worker.manifest.get('parent_run')
    if not parent_path:
        raise ValueError('shared proof worker has no owning parent run')
    parent = Run(parent_path)
    for owner in (parent, worker):
        if any(owner.manifest.get(k) != manifest.get(k) for k in ('sites', 'starts', 'data_month', 'policy')):
            raise ValueError('shared proof parent/worker frozen population or policy differs')
        if owner.verify_inputs() or owner.verify_events():
            raise ValueError('shared proof source input/event integrity failed')
    if manifest.get('run_id') not in (parent.manifest.get('run_id'), worker.manifest.get('run_id')):
        raise ValueError('shared proof manifest is neither the owning parent nor its current assigned worker')
    import acceptance_investigation as investigation
    tasks = investigation.task_records(parent)
    task = tasks.get(worker.manifest.get('worker_task'))
    member_id = proof.get('member_id')
    if (task is None or task.get('agent_id') != worker.manifest.get('worker_agent')
            or (parent.path/task['worker_dir']).resolve() != worker.path
            or task.get('state') not in ('assigned', 'submitted', 'approved', 'blocked_external')
            or member_id not in task['obligation_ids'] or answer.get('obligation_id') != member_id):
        raise ValueError('shared proof is not bound to the current parent/worker assignment and exact obligation')
    obligation = investigation.all_obligations(parent).get(member_id)
    if (not obligation or obligation['source_type'] != 'candidate'
            or obligation['source_id'] != candidate.get('candidate_id') or obligation['source_hash'] != digest(candidate)):
        raise ValueError('shared member does not match its frozen original candidate')
    sources = {c['candidate_id']: c for c in read(parent.path/'candidates.json')}
    if sources.get(candidate['candidate_id']) != candidate:
        raise ValueError('shared proof candidate differs from parent independent scan')
    if task.get('contract_hashes', {}).get(member_id) != obligation['contract']['sha256']:
        raise ValueError('shared proof assignment contract is no longer current')
    return parent, task, obligation


def _member(diagnosis, candidate, obligation, parent):
    oid = obligation['obligation_id']
    spec = diagnosis['spec']
    actual = [m for m in spec['members'] if (m.get('member_id') or m.get('obligation_id')) == oid]
    calculated = [m for m in diagnosis['members'] if m['member_id'] == oid]
    if len(actual) != 1 or len(calculated) != 1 or oid not in diagnosis['obligation_ids']:
        raise ValueError('shared receipt omits or duplicates the exact original obligation')
    raw, result = actual[0], calculated[0]
    if (raw.get('source_id') != candidate['candidate_id'] or raw.get('source_hash') != digest(candidate)
            or raw.get('source_type') != 'candidate'):
        raise ValueError('shared member source identity/hash is unrelated to the candidate')
    base = shift(candidate['month'], -parent.policy['comparisons'][candidate['comparison']])
    scope = result.get('scope', diagnosis['scope'])
    if (result.get('kind') != 'result_change' or result.get('state') != 'diagnosed' or not result.get('complete')
            or result['base_month'] != base or result['current_month'] != candidate['month']
            or result['comparison'] != candidate['comparison'] or scope['site'] != candidate['site']
            or scope['level'] != candidate['level'] or _path(scope['path']) != _path(candidate['path'])):
        raise ValueError('shared member population has a different country/path/comparison window')
    if diagnosis.get('computed_member_hash') != digest(sorted(spec['members'], key=canonical)):
        raise ValueError('opaque shared member hash is not a physical membership proof')
    registry_path = parent.path/'investigations/cases/state.json'
    if registry_path.exists():
        import acceptance_cases
        state = acceptance_cases.load(parent)
        packages = [p for p in state['collection_packages'] if p['package_id'] == diagnosis['package_id']]
        if len(packages) != 1:
            raise ValueError('shared diagnosis has no registered collection package')
        package = packages[0]
        covered = set(diagnosis['obligation_ids'])
        expected = [m for m in package['members'] if m['obligation_id'] in covered]
        if (not covered <= set(package['obligation_ids']) or diagnosis['member_hash'] != package['member_hash']
                or sorted(expected, key=canonical) != sorted(spec['members'], key=canonical)
                or any(spec[k] != package[k] for k in ('site', 'level')) or _path(spec['path']) != _path(package['path'])):
            raise ValueError('shared machine receipt scope/members differ from the registered original package')
    return result


def _machine_conflicts(diagnosis, member, entities):
    ids = {e['product_id'] for e in entities.values()}
    sku_keys = {tuple(json.loads(k)) for e in entities.values() for k in e['sku_keys']}
    periods = {member['base_month'], member['current_month']} | set(member.get('gap_months', []))
    results = []
    for item in diagnosis.get('conflicts', []):
        identity = item.get('identity')
        relevant = (item.get('entity_key') in entities or item.get('product_id') in ids)
        affected = {item['product_id']} if item.get('product_id') in ids else set()
        if item.get('entity_key') in entities:
            affected.add(entities[item['entity_key']]['product_id'])
        if identity and len(identity) >= 3 and tuple(identity[1:3]) in sku_keys:
            relevant = len(identity) < 4 or identity[3][:7] in periods
            affected.update(e['product_id'] for e in entities.values()
                            if canonical(list(identity[1:3])) in e['sku_keys'])
        if item.get('month') and item['month'] not in periods:
            relevant = False
        if relevant:
            results.append({'conflict_id': 'machine-conflict-'+digest(item)[:24], 'observation': item,
                            'affected_product_ids': sorted(affected), 'status': 'unanswered'})
    return results


def verify_shared_result_proof(manifest, candidate, answer, worker_run, *, check_claims=True, source_lookup=None):
    """Produce a formal independent-comparison proof using shared source pages.

    Assessments remain business assertions subject to main/independent review.
    ``status=verified`` alone earns no causal credit. The existing v3.2
    cause_credit gate checks exact objects, metrics and original evidence; this
    function additionally refuses credit for ambiguous platform/SKU identities.
    """
    proof = answer.get('population_proof')
    allowed = {'shared_package_id', 'shared_diagnosis_sha256', 'member_id', 'entity_assessments'}
    if not isinstance(proof, dict) or set(proof) != allowed or candidate.get('kind') != 'result_change':
        raise ValueError('shared proof must use only the mutually exclusive shared-package proof schema')
    parent, task, obligation = _binding(manifest, candidate, answer, worker_run, proof)
    diagnosis, package_dir = _verified_package(parent, proof['shared_package_id'], proof['shared_diagnosis_sha256'])
    member = _member(diagnosis, candidate, obligation, parent)
    entities = {e['entity_key']: e for e in member['entities']}
    population_entities = defaultdict(list)
    for entity in entities.values():
        pid = entity['product_id']
        if not isinstance(pid, str) or not pid.strip() or pid != pid.strip():
            raise ValueError('shared formal population has an unidentifiable parent; retain unknown diagnosis and investigation queue')
        population_entities[pid].append(entity)
    if not population_entities:
        raise ValueError('shared formal result population is empty')
    population = {pid: {side: _combine_parts([e[side] for e in parts]) for side in ('base', 'current')}
                  for pid, parts in sorted(population_entities.items())}
    # Formal published SPU counts remain source product_id counts; platform
    # observations remain separate for causal identity and signed contributions.
    anchors = {}
    for side in ('base', 'current'):
        values = {'spus': sum(p[side]['rows'] > 0 for p in population.values()),
                  **{m: member['totals'][side][m] for m in ('units', 'amount')}}
        for metric in ('spus', 'units', 'amount'):
            expected = candidate['facts']['metrics'][metric][side]
            if expected is None or values[metric] is None:
                if expected is not values[metric]:
                    raise ValueError('shared NULL denominator disagrees with original candidate')
            else:
                _close(values[metric], expected, metric, manifest, 'shared.'+side+'.'+metric)
        for metric in ('units', 'amount'):
            brand_parts = [b[side] for b in member['brands']]
            if all(b[metric+'_known'] for b in brand_parts):
                _close(sum(b[metric] for b in brand_parts), values[metric], metric, manifest, 'shared.brand.'+side+'.'+metric)
        anchors[side] = values
    for metric in ('units', 'amount'):
        if all(p['base'][metric+'_known'] and p['current'][metric+'_known'] for p in population.values()):
            formal_gross = sum(abs(p['current'][metric]-p['base'][metric]) for p in population.values())
            baseline_gross = candidate['facts'].get('spu_movement', {}).get(metric, {}).get('gross')
            if baseline_gross is not None:
                _close(formal_gross, baseline_gross, metric, manifest, 'shared.formal_product_gross.'+metric)
    parent_files = dict(diagnosis['source_files_sha256'])
    worker_files, parent_evidence = {}, {}
    eids = {'main:'+eid for eid in diagnosis['evidence_ids']}
    for path in (parent.path/'run.json', parent.path/'candidates.json', package_dir/'diagnosis.json', package_dir/'index.json', package_dir/'conflicts.json'):
        _hash_file(parent, path, parent_files)
    from acceptance_core import business_key
    _hash_file(parent, package_dir/'members'/(business_key(member['member_id'])+'.json'), parent_files)

    def lookup(eid):
        if not isinstance(eid, str) or not EVIDENCE_RE.fullmatch(eid):
            raise ValueError('shared assessment has an invalid evidence reference')
        if eid.startswith('main:'):
            bare = eid[5:]
            value = parent.get_evidence(bare)
            if source_lookup is not None and source_lookup(bare) != value:
                raise ValueError('shared parent evidence lookup points to another run')
            parent_evidence[bare] = digest(value)
            meta = read(parent.path/'records'/(bare+'.json'))
            for path in (parent.path/'records'/(bare+'.json'), parent.path/meta['path']):
                _hash_file(parent, path, parent_files)
        else:
            value = worker_run.get_evidence(eid)
            meta = read(worker_run.path/'records'/(eid+'.json'))
            for path in (worker_run.path/'records'/(eid+'.json'), worker_run.path/meta['path']):
                _hash_file(worker_run, path, worker_files)
        eids.add(eid)
        return value

    original_rows = {}
    row_sources = defaultdict(list)
    for eid in sorted(eids):
        for offset, row in enumerate(lookup(eid).get('response', {}).get('data', [])):
            if all(k in row for k in IDENTITY_KEYS):
                key = canonical([row[k] for k in IDENTITY_KEYS])
                contents = {k: v for k, v in row.items() if k != 'total_rows'}
                if key in original_rows and original_rows[key] != contents:
                    raise ValueError('shared original row changes across reused evidence pages')
                original_rows[key] = contents
                row_sources[key].append({'evidence_id': eid, 'pointer': '/response/data/'+str(offset), 'record_sha256': digest(row)})
    for entity in entities.values():
        if not set(entity['record_keys']) <= set(original_rows):
            raise ValueError('shared entity decomposition has no complete original SKU row population')
        for key in entity['record_keys']:
            row = original_rows[key]
            if row['source_layer'] != 'std' or row['product_id'] != entity['product_id'] or row['platform'] != entity['platform']:
                raise ValueError('shared entity original SKU identity differs from the machine population')
    assessments = proof['entity_assessments']
    if not isinstance(assessments, list):
        raise ValueError('shared formal proof needs explicit entity assessments, including an empty list when unfinished')
    statuses = {}
    untraceable = {pid for pid, parts in population_entities.items() if any(not e['identity_complete'] for e in parts)}
    ambiguous = {pid for pid, parts in population_entities.items() if len({e['platform'] for e in parts}) > 1}
    for group in assessments:
        ids = _unique(group.get('product_ids'), '共享实体精确父体ID')
        if any(pid not in population or pid in statuses for pid in ids):
            raise ValueError('shared assessment contains unrelated or duplicate population members')
        status = group.get('status')
        if status not in ('verified', 'blocked_external'):
            raise ValueError('shared assessment needs verified or an explicit external blocker')
        _explanation(group.get('reason'), '共享实体判断理由')
        _explanation(group.get('counterevidence'), '共享实体反证')
        for eid in _unique(group.get('evidence_ids'), '共享实体原始证据'):
            lookup(eid)
        if status == 'verified' and set(ids) & untraceable:
            raise ValueError('untraceable platform/SKU identities cannot be marked verified by shared evidence')
        statuses.update({pid: status for pid in ids})
    conflicts = _machine_conflicts(diagnosis, member, entities)
    responses = answer.get('machine_conflict_responses', [])
    if not isinstance(responses, list) or len({r.get('conflict_id') for r in responses}) != len(responses):
        raise ValueError('machine conflict responses must be an exact nonduplicate list')
    response_map = {r.get('conflict_id'): r for r in responses}
    if not set(response_map) <= {c['conflict_id'] for c in conflicts}:
        raise ValueError('machine conflict response is unrelated to this exact member')
    unresolved_conflict_pids = set()
    for conflict in conflicts:
        response = response_map.get(conflict['conflict_id'])
        if response is None:
            if check_claims:
                raise ValueError('machine source/identity/value conflict has no substantive investigation response: '+conflict['conflict_id'])
            unresolved_conflict_pids.update(conflict['affected_product_ids'])
            continue
        if response.get('status') not in ('resolved', 'blocked_external'):
            raise ValueError('machine conflict needs a resolved explanation or an explicit external blocker')
        _explanation(response.get('explanation'), '机器冲突的具体处理依据')
        rows = substantive.evidence_links(lookup, response.get('evidence_locators'), obligation)
        if not any(row.get('product_id', row.get('std_product_id')) in conflict['affected_product_ids'] for row in rows):
            raise ValueError('machine conflict response evidence is unrelated to its affected objects')
        conflict['status'] = response['status']
        conflict['response_sha256'] = digest(response)
        if response['status'] == 'blocked_external':
            unresolved_conflict_pids.update(conflict['affected_product_ids'])
    population_records = {pid: [original_rows[key] for entity in parts for key in entity['record_keys']]
                          for pid, parts in population_entities.items()}
    credits = substantive.cause_credit(manifest, candidate, assessments, lookup,
                                      population_records=population_records)
    # A product-only causal claim cannot silently cover a second platform with
    # the same numeric ID. Keep the entire source-ID contribution pending.
    for metric in credits:
        credits[metric] -= ambiguous | untraceable | unresolved_conflict_pids
    verified = set.intersection(*credits.values())
    blocked = {pid for pid, state in statuses.items() if state == 'blocked_external'}
    investigated = {pid for pid, state in statuses.items() if state == 'verified'}
    unreviewed = set(population)-verified
    missing_assessments = set(population)-set(statuses)
    computed = {
        'spus': {'state': 'measured', 'exposure': len(population), 'unexplained': len(set(population)-credits['spus']),
                 'unit': 'SPU', 'basis': '完整双期source product_id并集；平台身份保留，ID合并不证明实物同一。'}
    }
    band_rows = []; max_all = max_pending = 0.0; edges = diagnosis['price_bands']; valid_prices = True
    for side in ('base', 'current'):
        all_counts, pending_counts = Counter(), Counter()
        for pid, parts in population.items():
            band = _band(parts[side], edges)
            if band is None:
                continue
            valid_prices &= band != -1
            all_counts[band] += 1
            if pid not in credits['bands']:
                pending_counts[band] += 1
        for band, count in sorted(all_counts.items()):
            total = anchors[side]['spus']
            share, pending = 100*count/total, 100*pending_counts[band]/total
            max_all, max_pending = max(max_all, share), max(max_pending, pending)
            band_rows.append({'side': side, 'band': band, 'spus': count, 'unreviewed_spus': pending_counts[band],
                              'share_pp': share, 'unreviewed_share_pp': pending})
    computed['bands'] = {'state': 'measured', 'exposure': max_all, 'unexplained': max_pending,
                         'unit': 'pp', 'basis': '完整SPU按全部平台观测量额重算价格带；无效价格保留-1带，未知价格缺陷仍需主审。'}
    components = {}
    for metric in ('units', 'amount'):
        known = all(e[metric+'_change'] is not None for e in entities.values())
        if not known:
            computed[metric] = {'state': 'unknown', 'missing': '完整源记录仍有缺失量额，无法可靠计算全部贡献。',
                                'unit': '件' if metric == 'units' else '站点本币',
                                'basis': '机器验证了全部记录，缺失数值不会变成零或已解释贡献。'}
            continue
        deltas = {key: e[metric+'_change'] for key, e in entities.items()}
        residual = {key: delta for key, delta in deltas.items() if entities[key]['product_id'] not in credits[metric]}
        gross = sum(abs(delta) for delta in deltas.values())
        computed[metric] = {'state': 'measured', 'exposure': gross, 'unexplained': sum(abs(v) for v in residual.values()),
                            'unit': '件' if metric == 'units' else '站点本币',
                            'basis': '全部平台+父体双期绝对变化；每对象每指标原因仅计一次，跨平台正负不抵消。'}
        components[metric] = {'positive': sum(max(v, 0) for v in deltas.values()),
                              'negative': sum(max(-v, 0) for v in deltas.values()), 'net': sum(deltas.values()), 'gross': gross,
                              'unexplained_positive': sum(max(v, 0) for v in residual.values()),
                              'unexplained_negative': sum(max(-v, 0) for v in residual.values())}
    for value in computed.values():
        value['evidence_ids'] = sorted(eids)
        value['observed_exposure_is_not_risk_upper_bound'] = True
    if check_claims:
        if not isinstance(answer.get('impact'), dict) or set(answer['impact']) != set(computed):
            raise ValueError('shared proof answer lacks the four program-computed impact metrics')
        for metric, actual in computed.items():
            supplied = answer['impact'][metric]
            if supplied.get('state') != actual['state']:
                raise ValueError('shared proof reported impact state differs from original source completeness')
            if actual['state'] == 'measured':
                for field in ('exposure', 'unexplained'):
                    _close(_number(supplied.get(field), 'shared.'+metric+'.'+field, True), actual[field], metric, manifest, 'shared.claim.'+metric+'.'+field)
    continuation = substantive.continuation_requirements(manifest, candidate,
        {k: v for k, v in computed.items() if v['state'] == 'measured'}, credits, population)
    for metric, item in computed.items():
        if item['state'] == 'unknown':
            continuation.append({'followup_id': 'shared-unknown-'+business_key(candidate['candidate_id'], metric),
                                 'candidate_id': candidate['candidate_id'], 'metric': metric,
                                 'product_ids': sorted(population), 'required_action': item['missing']})
    for owner, hashes in ((parent, parent_files), (worker_run, worker_files)):
        for relative, sha in hashes.items():
            if digest((owner.path/relative).read_bytes()) != sha:
                raise ValueError('shared proof source evidence changed while calculating')
    return {'protocol': 3, 'proof_type': 'shared_machine_population',
            'candidate_id': candidate['candidate_id'], 'candidate_sha256': digest(candidate),
            'scope': {'site': candidate['site'], 'level': candidate['level'], 'path': candidate['path'],
                      'base_month': member['base_month'], 'current_month': member['current_month']},
            'shared_lineage': {'parent_run_id': parent.manifest['run_id'], 'worker_task': task['task_id'],
                               'worker_agent': task['agent_id'], 'assignment_attempt': task['attempt'],
                               'contract_sha256': obligation['contract']['sha256'], 'member_id': member['member_id'],
                               'package_id': diagnosis['package_id'], 'diagnosis_id': diagnosis['diagnosis_id'],
                               'diagnosis_sha256': digest(diagnosis), 'original_member_sha256': digest(next(m for m in diagnosis['spec']['members'] if m['member_id'] == member['member_id'])),
                               'computed_package_members_sha256': diagnosis['computed_member_hash']},
            'computed_impact': computed, 'anchors': anchors,
            'cohort': {'all': len(population), 'verified': len(verified), 'blocked_external': len(blocked), 'not_assessed': len(missing_assessments)},
            'verified_product_ids': sorted(verified), 'blocked_product_ids': sorted(blocked),
            'not_assessed_product_ids': sorted(missing_assessments), 'unreviewed_product_ids': sorted(unreviewed),
            'investigated_product_ids': sorted(investigated), 'untraceable_product_ids': sorted(untraceable),
            'ambiguous_cross_platform_product_ids': sorted(ambiguous),
            'machine_conflicts': conflicts,
            'unresolved_machine_conflict_ids': [c['conflict_id'] for c in conflicts if c['status'] != 'resolved'],
            'cause_credited_product_ids': {m: sorted(ids) for m, ids in credits.items()},
            'metric_residual_product_ids': {m: sorted(set(population)-ids) for m, ids in credits.items()},
            'contribution_components': components, 'band_population': band_rows,
            'population_movements': {name: sorted(pid for pid, p in population.items() if condition(p)) for name, condition in {
                'entered': lambda p: not p['base']['rows'] and p['current']['rows'],
                'exited': lambda p: p['base']['rows'] and not p['current']['rows'],
                'continuing': lambda p: p['base']['rows'] and p['current']['rows']}.items()},
            'platform_entity_population_count': len(entities), 'formal_source_spu_count': len(population),
            'sku_observations': [{'entity_key': e['entity_key'], 'product_id': e['product_id'], 'platform': e['platform'],
                                  'identity_complete': e['identity_complete'], 'source_record_keys': e['record_keys'],
                                  'original_evidence_locators': [link for key in e['record_keys'] for link in row_sources[key]]}
                                 for e in entities.values()],
            'brand_population': member['brands'], 'continuation_requirements': continuation,
            'job_ids': diagnosis['job_ids'], 'evidence_ids': sorted(eids),
            'job_files_sha256': dict(sorted(worker_files.items())), 'parent_files_sha256': dict(sorted(parent_files.items())),
            'source_evidence_sha256': dict(sorted(parent_evidence.items())),
            'entity_assessments_sha256': digest(assessments),
            'note': '正式总体证明复用主包完整明细；机验不证明原因，因果、反证与剩余风险仍需独立复核和主审。'}


def verify_shared_gap_proof(manifest, candidate, answer, worker_run, source_lookup=None, *, check_claims=True):
    """Formal gap proof from shared complete pre-gap seeds and every gap cell.

    Each original signal keeps its own latest preceding observed month and full
    intervening gap months. Shared raw pages remain in the parent; no worker
    source_coverage SQL is fabricated or re-executed.
    """
    proof = answer.get('gap_proof')
    allowed = {'shared_package_id', 'shared_diagnosis_sha256', 'member_id', 'entity_assessments'}
    if (not isinstance(proof, dict) or set(proof) != allowed
            or candidate.get('kind') not in ('raw_presence_gap', 'std_presence_gap')):
        raise ValueError('shared gap proof requires its mutually exclusive complete shared-package schema')
    parent, task, obligation = _binding(manifest, candidate, answer, worker_run, proof)
    diagnosis, package_dir = _verified_package(parent, proof['shared_package_id'], proof['shared_diagnosis_sha256'])
    oid = obligation['obligation_id']
    originals = [m for m in diagnosis['spec']['members'] if (m.get('member_id') or m.get('obligation_id')) == oid]
    calculated = [m for m in diagnosis['members'] if m['member_id'] == oid]
    if len(originals) != 1 or len(calculated) != 1 or oid not in diagnosis['obligation_ids']:
        raise ValueError('shared gap original member is missing or duplicated')
    original, member = originals[0], calculated[0]
    level = 'raw' if candidate['kind'] == 'raw_presence_gap' else candidate['level']
    wanted = _path(candidate['path'])
    if (original.get('source_type') != 'candidate' or original.get('source_id') != candidate['candidate_id']
            or original.get('source_hash') != digest(candidate) or original.get('site') != candidate['site']
            or _path(original.get('path')) != wanted or original.get('month') != candidate['month']
            or member.get('kind') != candidate['kind'] or member.get('state') != 'diagnosed' or not member.get('complete')
            or candidate['month'] != max(member.get('gap_months', ['']))
            or diagnosis.get('computed_member_hash') != digest(sorted(diagnosis['spec']['members'], key=canonical))):
        raise ValueError('shared gap member source/scope/month/complete population does not match the original obligation')
    if (parent.path/'investigations/cases/state.json').exists():
        import acceptance_cases
        registry = acceptance_cases.load(parent)
        packages = [p for p in registry['collection_packages'] if p['package_id'] == diagnosis['package_id']]
        if len(packages) != 1:
            raise ValueError('shared gap lacks its registered source package')
        package = packages[0]; covered = set(diagnosis['obligation_ids'])
        expected = [m for m in package['members'] if m['obligation_id'] in covered]
        if (not covered <= set(package['obligation_ids']) or diagnosis['member_hash'] != package['member_hash']
                or sorted(expected, key=canonical) != sorted(diagnosis['spec']['members'], key=canonical)
                or any(diagnosis['spec'][k] != package[k] for k in ('site','level'))
                or _path(diagnosis['spec']['path']) != _path(package['path'])):
            raise ValueError('shared gap receipt changed its registered members or source scope')
    files = dict(diagnosis['source_files_sha256']); worker_files = {}; source_hashes = {}
    eids = {'main:'+eid for eid in diagnosis['evidence_ids']}
    from acceptance_core import business_key, months
    for path in (parent.path/'run.json', parent.path/'candidates.json', package_dir/'diagnosis.json', package_dir/'index.json',
                 package_dir/'conflicts.json', package_dir/'members'/(business_key(oid)+'.json')):
        _hash_file(parent, path, files)

    def lookup(eid):
        if not isinstance(eid,str) or not EVIDENCE_RE.fullmatch(eid):
            raise ValueError('shared gap evidence ID is invalid')
        if eid.startswith('main:'):
            bare = eid[5:]; value = parent.get_evidence(bare)
            if source_lookup is not None and source_lookup(bare) != value:
                raise ValueError('shared gap source lookup belongs to a different run')
            source_hashes[bare] = digest(value)
            meta = read(parent.path/'records'/(bare+'.json'))
            for p in (parent.path/'records'/(bare+'.json'),parent.path/meta['path']):
                _hash_file(parent,p,files)
        else:
            value = worker_run.get_evidence(eid); meta = read(worker_run.path/'records'/(eid+'.json'))
            for p in (worker_run.path/'records'/(eid+'.json'),worker_run.path/meta['path']):
                _hash_file(worker_run,p,worker_files)
        eids.add(eid)
        return value
    rows = {}
    for eid in sorted(eids):
        for row in lookup(eid).get('response',{}).get('data',[]):
            if all(k in row for k in IDENTITY_KEYS):
                key = canonical([row[k] for k in IDENTITY_KEYS])
                value = {k:v for k,v in row.items() if k != 'total_rows'}
                if key in rows and rows[key] != value:
                    raise ValueError('shared gap source row differs across reused evidence pages')
                rows[key] = value
    # Recheck the full baseline job, not the worker's claimed baseline or a
    # recovered future observation. Its population independently anchors seeds.
    from acceptance_queries import job as baseline_job
    from acceptance_investigation_proof import _verified_rows
    baseline_job_spec = baseline_job(manifest,'raw_paths',candidate['site']) if level == 'raw' else baseline_job(manifest,'category',candidate['site'],level=level)
    baseline_eids = set()
    baseline_rows = _verified_rows(parent,baseline_job_spec,files,baseline_eids)
    for eid in baseline_eids:
        lookup('main:'+eid)
    matching = {r['month_dt'][:7]:r for r in baseline_rows if _path(r['path']) == wanted}
    preceding = [mm for mm in matching if mm < candidate['month']]
    if not preceding or candidate['month'] in matching:
        raise ValueError('shared gap lacks a valid pre-gap observation or the claimed missing month is observed')
    base = max(preceding)
    gap_months = months(shift(base,1),candidate['month'])
    if any(mm in matching for mm in gap_months) or member['base_month'] != base or member['gap_months'] != gap_months:
        raise ValueError('shared gap substituted its nearest prior baseline, skipped a gap month or crossed recovery')
    layer = 'raw' if level == 'raw' else 'std'
    seeds = {key:row for key,row in rows.items() if row['source_layer'] == layer and row['month_dt'][:7] == base
             and (_path(row['raw_path']) == wanted if level == 'raw' else _path(row['std_path'])[:level] == wanted)}
    if not seeds or len(seeds) != member['source_seed_count']:
        raise ValueError('shared gap pre-gap seed population is incomplete')
    seed_entities = defaultdict(list); untraceable = set(); platforms = defaultdict(set)
    from acceptance_machine import _part
    for row in seeds.values():
        pid = row['product_id']
        if not isinstance(pid,str) or not pid.strip() or pid != pid.strip():
            raise ValueError('shared gap has an unidentifiable source parent; cannot complete a formal object assessment')
        seed_entities[pid].append(row); platforms[pid].add(row['platform'])
        if not isinstance(row['platform'],str) or not row['platform'].strip() or not traceable_sku(row['sku_id']):
            untraceable.add(pid)
    population = {pid:_part(found) for pid,found in sorted(seed_entities.items())}
    total = _part(list(seeds.values()))
    anchors = {'month':base,'spus':len(population),'nrows':total['rows'],'units':total['units'],'amount':total['amount']}
    for metric in ('spus','nrows','units','amount'):
        expected = matching[base][metric]
        if anchors[metric] is None or expected is None:
            if anchors[metric] is not expected:
                raise ValueError('shared gap NULL source anchor differs')
        else:
            _close(anchors[metric],expected,metric,manifest,'shared gap pre-baseline.'+metric)
    destinations = defaultdict(list)
    for key,row in rows.items():
        if traceable_sku(row['sku_id']):
            destinations[(row['platform'],row['sku_id'],row['month_dt'][:7],row['source_layer'])].append(key)
    computed_cells = []
    for seed_key,seed in sorted(seeds.items()):
        traceable = isinstance(seed['platform'],str) and bool(seed['platform'].strip()) and traceable_sku(seed['sku_id'])
        for mm in gap_months:
            for destination_layer in ('raw','std'):
                keys = destinations.get((seed['platform'],seed['sku_id'],mm,destination_layer),[]) if traceable else []
                computed_cells.append({'seed_key':seed_key,'entity_key':canonical([seed['site'],seed['platform'],seed['product_id']]),
                    'month':mm,'source_layer':destination_layer,'state':'untraceable_identity' if not traceable else 'observed_elsewhere' if keys else 'no_observation',
                    'record_keys':sorted(keys),'destination_parent_ids':sorted({rows[k]['product_id'] for k in keys},key=canonical)})
    if sorted(computed_cells,key=canonical) != sorted(member['destination_cells'],key=canonical):
        raise ValueError('shared gap lost, altered or fabricated an explicit seed/month/source-layer destination')
    expected_cells = len(seeds)*len(gap_months)*2
    if len(computed_cells) != expected_cells or member['expected_destination_cells'] != expected_cells:
        raise ValueError('shared gap destination denominator is incomplete')
    assessments = proof['entity_assessments']; statuses = {}
    if not isinstance(assessments,list):
        raise ValueError('shared gap needs explicit entity assessments')
    for group in assessments:
        ids = _unique(group.get('product_ids'),'共享缺口精确父体')
        if any(pid not in population or pid in statuses for pid in ids):
            raise ValueError('shared gap assessment contains unrelated or duplicate objects')
        if group.get('status') not in ('verified','blocked_external'):
            raise ValueError('shared gap assessment state is invalid')
        if group['status'] == 'verified' and set(ids)&untraceable:
            raise ValueError('shared gap missing platform/SKU identity cannot be marked verified')
        _explanation(group.get('reason'),'共享缺口原因');_explanation(group.get('counterevidence'),'共享缺口反证')
        for eid in _unique(group.get('evidence_ids'),'共享缺口具体证据'):
            lookup(eid)
        statuses.update({pid:group['status'] for pid in ids})
    forensic_entities = {}
    for seed_key, seed in seeds.items():
        key = canonical([seed['site'], seed['platform'], seed['product_id']])
        forensic_entities.setdefault(key, {'entity_key':key, 'product_id':seed['product_id'], 'sku_keys':[]})
        if traceable_sku(seed['sku_id']):
            forensic_entities[key]['sku_keys'].append(canonical([seed['platform'], seed['sku_id']]))
    conflicts = _machine_conflicts(diagnosis, {'base_month':base, 'current_month':candidate['month'], 'gap_months':gap_months}, forensic_entities)
    responses = answer.get('machine_conflict_responses', [])
    if not isinstance(responses,list) or len({r.get('conflict_id') for r in responses}) != len(responses):
        raise ValueError('shared gap machine conflicts require an exact response list')
    response_map = {r.get('conflict_id'):r for r in responses}
    if not set(response_map) <= {c['conflict_id'] for c in conflicts}:
        raise ValueError('shared gap machine conflict response is unrelated')
    unresolved_conflict_pids = set()
    for conflict in conflicts:
        response = response_map.get(conflict['conflict_id'])
        if response is None:
            if check_claims:
                raise ValueError('shared gap machine conflict lacks a substantive response: '+conflict['conflict_id'])
            unresolved_conflict_pids.update(conflict['affected_product_ids'])
            continue
        if response.get('status') not in ('resolved','blocked_external'):
            raise ValueError('shared gap machine conflict status is invalid')
        _explanation(response.get('explanation'),'共享缺口机器冲突处理')
        evidence_rows = substantive.evidence_links(lookup,response.get('evidence_locators'),obligation)
        if not any(row.get('product_id',row.get('std_product_id')) in conflict['affected_product_ids'] for row in evidence_rows):
            raise ValueError('shared gap conflict response evidence is unrelated to affected identities')
        conflict['status'] = response['status']; conflict['response_sha256'] = digest(response)
        if response['status'] == 'blocked_external':
            unresolved_conflict_pids.update(conflict['affected_product_ids'])
    # Bind cause credit to every original pre-gap seed plus every actual
    # destination of that seed in each missing month and source layer. A new
    # destination parent remains related through the program-verified SKU edge;
    # do not rewrite its original record to pretend the old parent survived.
    credit_rows = {pid: {} for pid in population}
    for seed_key, seed in seeds.items():
        credit_rows[seed['product_id']][seed_key] = seed
    for cell in computed_cells:
        pid = seeds[cell['seed_key']]['product_id']
        for key in cell['record_keys']:
            credit_rows[pid][key] = rows[key]
    population_records = {pid: list(found.values()) for pid, found in credit_rows.items()}
    credits = substantive.cause_credit(manifest,candidate,assessments,lookup,
                                      population_records=population_records)
    ambiguous = {pid for pid,values in platforms.items() if len(values)>1}
    for metric in credits:
        credits[metric] -= untraceable|ambiguous|unresolved_conflict_pids
    verified = set.intersection(*credits.values()); blocked = {pid for pid,state in statuses.items() if state == 'blocked_external'}
    not_assessed = set(population)-set(statuses)
    counts,pending = Counter(),Counter()
    for pid,part in population.items():
        band = _band(part,diagnosis['price_bands']);counts[band]+=1
        if pid not in credits['bands']:
            pending[band]+=1
    bands = [{'band':band,'spus':n,'unreviewed_spus':pending[band],'share_pp':100*n/len(population),
              'unreviewed_share_pp':100*pending[band]/len(population)} for band,n in sorted(counts.items())]
    computed = {'spus':{'state':'measured','exposure':len(population),'unexplained':len(set(population)-credits['spus']),
                       'unit':'SPU','basis':'缺口前最近完整source product_id并集；非缺口月真实损失。'},
                'bands':{'state':'measured','exposure':max(b['share_pp'] for b in bands),
                         'unexplained':max(b['unreviewed_share_pp'] for b in bands),'unit':'pp',
                         'basis':'缺口前全部平台量额按正式SPU重算价格带，不加总逐月重复种子。'}}
    for metric in ('units','amount'):
        if all(p[metric+'_known'] for p in population.values()):
            computed[metric] = {'state':'measured','exposure':sum(abs(p[metric]) for p in population.values()),
                'unexplained':sum(abs(p[metric]) for pid,p in population.items() if pid not in credits[metric]),
                'unit':'件' if metric == 'units' else '站点本币','basis':'完整前基线观测暴露，每对象计一次；不冒充缺口月真实量额或风险上界。'}
        else:
            computed[metric] = {'state':'unknown','missing':'前基线源量额缺失，不能伪造缺口损失。',
                'unit':'件' if metric == 'units' else '站点本币','basis':'保留完整种子和缺失数值，继续补证。'}
    for item in computed.values():
        item['evidence_ids'] = sorted(eids);item['observed_exposure_is_not_risk_upper_bound'] = True
    if check_claims:
        if not isinstance(answer.get('impact'),dict) or set(answer['impact']) != set(computed):
            raise ValueError('shared gap answer lacks program-computed four-metric impacts')
        for metric,item in computed.items():
            claimed = answer['impact'][metric]
            if claimed.get('state') != item['state']:
                raise ValueError('shared gap claimed completeness state is false')
            if item['state'] == 'measured':
                for name in ('exposure','unexplained'):
                    _close(_number(claimed.get(name),'shared gap.'+metric+'.'+name,True),item[name],metric,manifest,'shared gap claim.'+metric+'.'+name)
    continuation = substantive.continuation_requirements(manifest,candidate,
        {m:v for m,v in computed.items() if v['state']=='measured'},credits,population)
    for owner,hashes in ((parent,files),(worker_run,worker_files)):
        for relative,sha in hashes.items():
            if digest((owner.path/relative).read_bytes()) != sha:
                raise ValueError('shared gap evidence changed during computation')
    return {'protocol':3,'proof_type':'shared_machine_gap_population','candidate_id':candidate['candidate_id'],
        'candidate_sha256':digest(candidate),'scope':{'site':candidate['site'],'level':level,'path':candidate['path'],
            'signal_month':candidate['month'],'base_month':base,'current_month':candidate['month'],'gap_months':gap_months},
        'shared_lineage':{'parent_run_id':parent.manifest['run_id'],'worker_task':task['task_id'],'worker_agent':task['agent_id'],
            'assignment_attempt':task['attempt'],'contract_sha256':obligation['contract']['sha256'],'member_id':oid,
            'package_id':diagnosis['package_id'],'diagnosis_id':diagnosis['diagnosis_id'],'diagnosis_sha256':digest(diagnosis),
            'original_member_sha256':digest(original),'computed_package_members_sha256':diagnosis['computed_member_hash']},
        'computed_impact':computed,'anchors':anchors,
        'cohort':{'all':len(population),'verified':len(verified),'blocked_external':len(blocked),'not_assessed':len(not_assessed)},
        'seed_count':len(seeds),'expected_destination_cells':expected_cells,'verified_destination_cells':len(computed_cells),
        'destination_state_counts':dict(Counter(c['state'] for c in computed_cells)),'destination_cells':computed_cells,
        'verified_product_ids':sorted(verified),'blocked_product_ids':sorted(blocked),'not_assessed_product_ids':sorted(not_assessed),
        'unreviewed_product_ids':sorted(set(population)-verified),'untraceable_product_ids':sorted(untraceable),
        'ambiguous_cross_platform_product_ids':sorted(ambiguous),
        'machine_conflicts':conflicts,
        'unresolved_machine_conflict_ids':[c['conflict_id'] for c in conflicts if c['status'] != 'resolved'],
        'investigated_product_ids':sorted(pid for pid,state in statuses.items() if state=='verified'),
        'cause_credited_product_ids':{m:sorted(ids) for m,ids in credits.items()},
        'metric_residual_product_ids':{m:sorted(set(population)-ids) for m,ids in credits.items()},
        'band_population':bands,'continuation_requirements':continuation,'job_ids':diagnosis['job_ids'],
        'evidence_ids':sorted(eids),'job_files_sha256':dict(sorted(worker_files.items())),
        'parent_files_sha256':dict(sorted(files.items())),'source_evidence_sha256':dict(sorted(source_hashes.items())),
        'entity_assessments_sha256':digest(assessments),
        'note':'正式缺口证明复用共享完整前基线和逐缺月去向；无观测不等于零成交，原因仍需逐实体独立复核和主审。'}
