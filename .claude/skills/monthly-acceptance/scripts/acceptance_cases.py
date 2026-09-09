"""Lossless handling cases above the existing obligation and review ledger.

Collection sharing never asserts a common cause. Only recomputed machine
receipts may move work past awaiting_evidence; the 2% score orders collection.
All final substantive approval remains in acceptance_investigation.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import argparse
import csv
import json
import math
import re
import time

from acceptance_core import Run, canonical, digest, now, read, write
from acceptance_case_grouping import (Source, build_cases, case_features,
    contract_crosswalk, history_links, history_scope, new_case)

PROTOCOL = '3.2.0'
METRICS = ('spus', 'bands', 'units', 'amount')
PROTECTED_TYPES = {'historical', 'history_source', 'project'}


def home(run):
    return run.path / 'investigations' / 'cases'


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Missing ' + label)
    return value


def _path(value):
    return json.loads(value) if isinstance(value, str) else list(value or [])


def _member(ob, obj):
    months, entities = history_scope(obj)
    if obj.get('month'):
        months = sorted(set(months) | {obj['month']})
    for name in ('product_id', 'sku_id'):
        if obj.get(name) is not None:
            entities = sorted(set(entities) | {str(obj[name])})
    facts = obj.get('facts') or {}
    return {'member_id': ob['obligation_id'], 'obligation_id': ob['obligation_id'],
        'source_type': ob['source_type'], 'source_id': ob['source_id'],
        **{key: deepcopy(obj.get(key, [])) for key in ('processing_rule_ids', 'batch_ids') if key in obj},
        'source_hash': ob['source_hash'], 'kind': obj.get('kind', ob['source_type']),
        'site': obj.get('site'), 'path': _path(obj.get('path')),
        'month': obj.get('month', obj.get('first_observed_month')),
        'current_month': obj.get('month'), 'base_month': facts.get('base_month'),
        'comparison': obj.get('comparison'), 'months': months,
        'entity_ids': entities,
        'sku_keys': deepcopy(obj.get('sku_keys', obj.get('legacy_detail', {}).get('sku_keys', []))),
        'platform': obj.get('platform'), 'product_id': obj.get('product_id'), 'sku_id': obj.get('sku_id'),
        'required_checks': list(ob['required_checks']), 'scope': deepcopy(ob['scope']),
        'contract_sha256': digest(ob['contract']) if ob.get('contract') else None,
        'source_evidence_ids': list(ob.get('source_evidence_ids', []))}


def _states():
    return {'check_execution': 'not_executed', 'investigation': 'pending',
        'defect': 'undetermined', 'cause': 'unexplained', 'impact': 'unbounded',
        'review': 'pending', 'business_acceptance': 'undetermined'}


def _refresh_case(case, members):
    ids = sorted(case['obligation_ids'])
    case['obligation_ids'] = ids
    case['member_hash'] = digest([members[oid] for oid in ids])
    case['signal_ids'] = sorted(members[oid]['source_id'] for oid in ids if members[oid]['source_type'] == 'candidate')
    case['sample_ids'] = sorted(members[oid]['source_id'] for oid in ids if members[oid]['source_type'] == 'sample')
    case['comparisons'] = [dict(p, signal_ids=[sid for sid in p['signal_ids'] if sid in case['signal_ids']])
        for p in case.get('comparisons', []) if set(p['signal_ids']) & set(case['signal_ids'])]
    case['months'] = sorted({mm for oid in ids for mm in members[oid]['months']})
    case['exact_entities'] = [dict(obligation_id=oid, entity_ids=members[oid]['entity_ids'],
        sku_keys=members[oid]['sku_keys'], platform=members[oid]['platform']) for oid in ids
        if members[oid]['entity_ids'] or members[oid]['sku_keys']]


def _collection_packages(source, cases, members):
    groups = {}
    for case in cases:
        # Result comparisons may reuse the full history within an exact canonical
        # scope. Gaps retain independent episodes and mandatory pre-gap seeds.
        key = ('result', case['site'], case['path']) if case['kind'] == 'result' else (case['kind'], case['case_id'])
        pid = 'collection-' + digest(key)[:24]
        package = groups.setdefault(pid, {'package_id': pid, 'collection_id': pid,
            'kind': case['kind'], 'site': case['site'], 'path': case['path'], 'level': 'raw' if case['kind'] == 'raw_presence_gap' else len(case['path']),
            'case_ids': [], 'obligation_ids': [], 'members': [], 'comparison_windows': [],
            'causal_merge_authorized': False, 'query_executed': False,
            'state': 'awaiting_evidence', 'collection_priority': 'normal',
            'data_requirement': 'complete scope-filtered monthly platform/SKU/parent identities, quantities, amounts, prices, raw and standard paths; all pages and duplicate keys checked'})
        package['case_ids'].append(case['case_id'])
        package['obligation_ids'].extend(case['obligation_ids'])
        package['members'].extend(members[oid] for oid in case['obligation_ids'])
        package['comparison_windows'].extend([[p['base_month'], p['current_month']] for p in case.get('comparisons', [])])
        if case['collection_priority'] == 'priority':
            package['collection_priority'] = 'priority'
        case['collection_ids'] = [pid]
        if case['kind'] in ('raw_presence_gap', 'std_presence_gap'):
            first = min(case['months'])
            populations = source.raw_paths.get((case['site'], tuple(case['path'])), {}) if case['kind'] == 'raw_presence_gap' else {
                mm: row for (site, path, mm), row in source.categories.items() if site == case['site'] and path == tuple(case['path'])}
            before = sorted(mm for mm, row in populations.items() if mm < first and float(row.get('nrows', 0)) > 0)
            package['seed_baseline_month'] = before[-1] if before else None
            package['gap_months'] = case['months']
            package['recovery_substitutes_baseline'] = False
            package['baseline_status'] = 'awaiting_complete_pre_gap_objects' if before else 'pre_gap_baseline_unavailable'
    for package in groups.values():
        package['case_ids'].sort()
        package['obligation_ids'].sort()
        package['members'].sort(key=lambda m: m['obligation_id'])
        package['member_hash'] = digest(package['members'])
        package['comparison_windows'] = [json.loads(x) for x in sorted({canonical(x) for x in package['comparison_windows']})]
        mm = {m for member in package['members'] for m in member['months']}
        mm.update(m for window in package['comparison_windows'] for m in window)
        if package.get('seed_baseline_month'):
            mm.add(package['seed_baseline_month'])
        package['window_start'] = min(mm) if mm else None
        package['window_end'] = max(mm) if mm else None
        eligible = package['site'] in source.manifest.get('sites', []) and (package['level'] == 'raw' or package['level'] in (0, 1, 2, 3))
        if eligible and mm:
            eligible = source.manifest['starts'][package['site']] <= min(mm) <= max(mm) <= source.manifest['data_month']
        package['machine_collection_eligible'] = eligible
        package['machine_collection_blocker'] = None if eligible else 'Contract/special scope requires an explicit validated machine scope; original obligation remains queued'
    return sorted(groups.values(), key=lambda p: p['package_id'])


def _validate_ordering_threshold(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 1:
        raise ValueError('collection_priority_threshold must be a finite number greater than 0 and at most 1')
    return value


def _ordering_threshold(policy):
    case_policy = policy.get('case_workflow', {})
    if not isinstance(case_policy, dict):
        raise ValueError('case_workflow must be an object')
    value = case_policy.get('collection_priority_threshold',
        case_policy.get('collection_ordering_threshold', .02))
    return _validate_ordering_threshold(value)


def build_registry(source, obligations, *, bridge=None, ordering_threshold=.02):
    """Pure lossless grouping. bridge is only for an explicitly frozen replay."""
    _validate_ordering_threshold(ordering_threshold)
    by_oid = {o['obligation_id']: o for o in obligations}
    if len(by_oid) != len(obligations):
        raise ValueError('Duplicate obligation ID')
    cases, aliases, strict = build_cases(source)
    cby = {c['candidate_id']: c for c in source.candidates}
    sby = {s['sample_id']: s for s in source.samples}
    if len(cby) != len(source.candidates) or len(sby) != len(source.samples):
        raise ValueError('Duplicate source ID')
    for kind, objects in [('candidate', cby), ('sample', sby)]:
        if {o['source_id'] for o in obligations if o['source_type'] == kind} != set(objects):
            raise ValueError('Source obligations do not cover all ' + kind + ' objects')
    history = [o['source_material'] for o in obligations if o['source_type'] == 'historical']
    links = history_links(cases, history)
    by_source = {(kind, sid): case for case in cases for kind, ids in [('candidate', case['signal_ids']), ('sample', case['sample_ids'])] for sid in ids}
    for h in links:
        case = new_case('historical', h['issue_id'], site=h['site'], path=list(h['path']),
            months=h['months'], comparisons=[], merge_basis='independent_exact_history_obligation')
        case['case_id'] = h['history_case_id']
        case['history_ids'] = [h['issue_id']]
        cases.append(case)
        by_source[('historical', h['issue_id'])] = case
    members, mapping = {}, []
    for ob in sorted(obligations, key=lambda o: o['obligation_id']):
        kind, sid = ob['source_type'], ob['source_id']
        obj = deepcopy((cby if kind == 'candidate' else sby)[sid]) if kind in ('candidate', 'sample') else ob['source_material']
        original_hash = digest(obj)
        if bridge is not None and kind in ('candidate', 'sample'):
            obj['evidence_ids'] = bridge['objects'][kind][sid]
        if digest(obj) != ob['source_hash']:
            raise ValueError('Source/evidence ID bridge does not reproduce obligation: ' + ob['obligation_id'])
        member = _member(ob, obj)
        members[ob['obligation_id']] = member
        case = by_source.get((kind, sid))
        if case is None:
            case = new_case(kind, ob['obligation_id'], site=obj.get('site', 'global'),
                path=_path(obj.get('path')), months=member['months'], comparisons=[],
                merge_basis='independent_special_or_new_obligation')
            case['case_id'] = 'ancillary-' + digest(ob['obligation_id'])[:24]
            cases.append(case)
        case.setdefault('obligation_ids', []).append(ob['obligation_id'])
        mapping.append({'obligation_id': ob['obligation_id'], 'source_type': kind, 'source_id': sid,
            'source_hash': ob['source_hash'], 'analysis_source_hash': original_hash,
            'case_id': case['case_id'], 'member_hash': digest(member),
            'required_checks': list(ob['required_checks']), 'business_verified': False})
    for case in cases:
        _refresh_case(case, members)
        case.update(case_version=1, states=_states(), route='awaiting_evidence',
            route_reason='complete machine diagnosis not yet available',
            investigation_complete=False, review_status='pending',
            residual={metric: None for metric in METRICS}, residual_bounded=False,
            diagnostic_receipts=[], rule_proof_ids=[], rule_versions=[], reopened=False)
        case['history_ids'] = sorted(set(case['history_ids']))
        case['features'] = case_features(case, source, cby)
        case['collection_priority'] = 'priority' if case['kind'] in PROTECTED_TYPES or case['history_ids'] or case['features'].get('max_impact_score', 0) >= ordering_threshold else 'normal'
    packages = _collection_packages(source, cases, members)
    for a in aliases:
        candidate = cby[a['signal_id']]
        a.update(site=candidate['site'], base_month=candidate['facts']['base_month'], current_month=candidate['month'])
        a['evidence'] = source.alias_evidence(a['site'], a['alias_chain'], a['base_month'], a['current_month'])
        a['proof_sha256'] = digest(a['evidence'])
    source_hashes = dict(source.hashes)
    state = {'protocol': PROTOCOL, 'version': 0, 'ordering_threshold': ordering_threshold,
        'ordering_meaning': 'observed salience for collection order only; no closure, bypass, or direct Agent dispatch',
        'source_binding': {'source_run': str(source.path), 'source_files': source_hashes,
            'obligations_sha256': digest(sorted(obligations, key=lambda o: o['obligation_id']))},
        'cases': sorted(cases, key=lambda c: c['case_id']), 'members': members,
        'obligation_case_map': mapping, 'collection_packages': packages,
        'history_route_map': links, 'alias_proofs': aliases,
        'contract_crosswalk_proposals': contract_crosswalk(source, cases),
        'strict_result_comparison_units': strict, 'rules': {}, 'rule_proofs': {}, 'changes': [],
        'budget': {'state': 'available', 'pending_work_preserved': True},
        'business_acceptance': 'undetermined'}
    verify_registry(state, obligations)
    return state


def verify_registry(state, obligations):
    expected = {o['obligation_id']: o for o in obligations}
    mapping = state['obligation_case_map']
    members = state['members']
    if len(mapping) != len(expected) or {m['obligation_id'] for m in mapping} != set(expected) or set(members) != set(expected):
        raise ValueError('Incomplete or duplicate original obligation mapping')
    cases = {c['case_id']: c for c in state['cases']}
    if len(cases) != len(state['cases']):
        raise ValueError('Duplicate case ID')
    owner = {}
    for case in cases.values():
        ids = case['obligation_ids']
        if not ids or len(ids) != len(set(ids)) or any(oid in owner or oid not in expected for oid in ids):
            raise ValueError('Case has missing, repeated, or multiply owned members')
        if case['member_hash'] != digest([members[oid] for oid in sorted(ids)]):
            raise ValueError('Case member version changed')
        owner.update({oid: case['case_id'] for oid in ids})
        if any(expected[oid]['source_type'] in PROTECTED_TYPES for oid in ids) and len(ids) != 1:
            raise ValueError('Historical and special obligations require independent cases')
        for oid in ids:
            m = members[oid]
            if m['source_type'] == 'candidate' and m['kind'] == 'result_change':
                matches = [p for p in case['comparisons'] if m['source_id'] in p['signal_ids']]
                if len(matches) != 1 or any(matches[0][k] != m[k] for k in ('base_month', 'current_month', 'comparison')):
                    raise ValueError('Original comparison or base period lost')
    if set(owner) != set(expected):
        raise ValueError('Dropped original obligation')
    for row in mapping:
        oid = row['obligation_id']; ob = expected[oid]
        if row['case_id'] != owner[oid] or row['source_hash'] != ob['source_hash'] or row['required_checks'] != ob['required_checks']:
            raise ValueError('Mapping source, checks, or owner changed')
        if row['member_hash'] != digest(members[oid]) or members[oid]['source_hash'] != ob['source_hash'] or members[oid]['scope'] != ob['scope']:
            raise ValueError('Exact member/obligation scope changed')
        if ob['source_type'] == 'historical':
            months, entity_ids = history_scope(ob['source_material'])
            if not set(months) <= set(members[oid]['months']) or not set(entity_ids) <= set(members[oid]['entity_ids']):
                raise ValueError('Historical exact object or month lost')
    package_ids = [p['package_id'] for p in state['collection_packages']]
    if len(package_ids) != len(set(package_ids)):
        raise ValueError('Duplicate logical collection package ID')
    collection_owners = Counter()
    reverse_packages = defaultdict(set)
    for package in state['collection_packages']:
        ids = package['obligation_ids']
        if any(oid not in expected for oid in ids):
            raise ValueError('Collection includes an unknown original obligation')
        collection_owners.update(ids)
        case_ids = package['case_ids']
        expected_case_ids = {owner[oid] for oid in ids}
        if len(case_ids) != len(set(case_ids)) or set(case_ids) != expected_case_ids:
            raise ValueError('Collection case reverse references differ from original member ownership')
        for cid in case_ids:
            reverse_packages[cid].add(package['package_id'])
        if len(ids) != len(set(ids)) or package['member_hash'] != digest(package['members']):
            raise ValueError('Collection member version changed')
        if sorted(m['obligation_id'] for m in package['members']) != sorted(ids):
            raise ValueError('Collection membership lost')
        for member in package['members']:
            if member != members[member['obligation_id']]:
                raise ValueError('Collection silently changed an original member')
    if set(collection_owners) != set(expected) or any(n != 1 for n in collection_owners.values()):
        raise ValueError('Every original obligation needs exactly one complete logical collection package')
    for cid, case in cases.items():
        refs = case.get('collection_ids', [])
        if len(refs) != len(set(refs)) or set(refs) != reverse_packages[cid]:
            raise ValueError('Case collection reverse references are missing or inconsistent')
    return True


def _latest_event(run, kind):
    matches = [json.loads(line) for line in run._event_snapshot().splitlines() if json.loads(line)['type'] == kind]
    return matches[-1] if matches else None


def _persist(run, state, action, actor=None):
    run.writable()
    state['version'] += 1
    serialized=canonical(state).encode();state_hash=digest(serialized)
    target = home(run) / 'versions' / ('v%06d.json' % state['version'])
    if target.exists():
        raise ValueError('Case version already exists')
    from acceptance_registry_storage import store,write_current,FORMAT
    snapshot,cost=store(home(run),target,state,state_hash)
    write_current(home(run)/'state.json',serialized,state_hash)
    run.event('case.registry.saved', version=state['version'], registry_hash=state_hash,
        action=action, actor=actor, path=str(target.relative_to(run.path)),
        storage_format=FORMAT,version_snapshot_sha256=digest(snapshot),
        current_state_bytes=(home(run)/'state.json').stat().st_size,**cost)
    return state


def load(run, *, verify_sources=True):
    value = read(home(run) / 'state.json')
    state = value['payload']
    latest = _latest_event(run, 'case.registry.saved')
    if digest(state) != value['sha256'] or not latest or latest['registry_hash'] != value['sha256'] or latest['version'] != state['version']:
        raise ValueError('Case registry changed or missing version event')
    previous = read(run.path / latest['path'])
    from acceptance_registry_storage import FORMAT,verify
    if latest.get('storage_format')==FORMAT and (previous.get('format')!=FORMAT
            or latest.get('version_snapshot_sha256')!=digest(previous)):
        raise ValueError('Case version changed')
    if previous.get('format')==FORMAT:
        if latest.get('version_snapshot_sha256')!=digest(previous):
            raise ValueError('Case version changed')
        verify(home(run),previous,value)
    elif previous != value:
        raise ValueError('Case version changed')
    if run.verify_events() or run.verify_inputs():
        raise ValueError('Case run source/event integrity failed')
    if verify_sources:
        source_root = Path(state['source_binding']['source_run'])
        if source_root.resolve() != run.path.resolve():
            raise ValueError('Production registry points outside this run')
        for relative, hashed in state['source_binding']['source_files'].items():
            if digest((run.path / relative).read_bytes()) != hashed:
                raise ValueError('Case source changed: ' + relative)
    return state


def prepare(run):
    import acceptance_questions as questions
    if questions.enabled(run):return questions._registry(run)
    from acceptance_investigation import all_obligations, plan_data
    run.writable()
    plan = plan_data(run)
    ordering_threshold = _ordering_threshold(run.policy)
    obligations = list(all_obligations(run, plan).values())
    if (home(run) / 'state.json').exists():
        state = load(run)
        if state['ordering_threshold'] != ordering_threshold:
            raise ValueError('Case collection threshold differs from the frozen policy')
        known = set(state['members']); current = {o['obligation_id'] for o in obligations}
        if not known <= current:
            raise ValueError('Original case obligations removed')
        if known == current:
            verify_registry(state, obligations)
            return state
        # Added findings never shrink, replace or silently regroup prior cases.
        source = Source(run)
        added = [o for o in obligations if o['obligation_id'] not in known]
        for ob in added:
            if ob['source_type'] in ('candidate', 'sample'):
                raise ValueError('Frozen discovery population changed')
            member = _member(ob, ob['source_material'])
            state['members'][ob['obligation_id']] = member
            case = new_case(ob['source_type'], ob['obligation_id'], site=member['site'], path=member['path'],
                months=member['months'], comparisons=[], obligation_ids=[ob['obligation_id']], case_version=1,
                states=_states(), route='awaiting_evidence', route_reason='new obligation requires full evidence',
                collection_priority='priority', features={}, diagnostic_receipts=[], rule_proof_ids=[], rule_versions=[],
                residual={m: None for m in METRICS}, residual_bounded=False, review_status='pending', reopened=False)
            case['case_id'] = 'ancillary-' + digest(ob['obligation_id'])[:24]
            _refresh_case(case, state['members'])
            state['cases'].append(case)
            state['obligation_case_map'].append({'obligation_id': ob['obligation_id'], 'source_type': ob['source_type'],
                'source_id': ob['source_id'], 'source_hash': ob['source_hash'], 'analysis_source_hash': ob['source_hash'],
                'member_hash': digest(member), 'case_id': case['case_id'], 'required_checks': ob['required_checks'], 'business_verified': False})
        state['collection_packages'] = _collection_packages(source, state['cases'], state['members'])
        state['source_binding']['obligations_sha256'] = digest(sorted(obligations, key=lambda o: o['obligation_id']))
        verify_registry(state, obligations)
        return _persist(run, state, 'append_obligations', plan['coordinator_id'])
    state = build_registry(Source(run), obligations,
        ordering_threshold=ordering_threshold)
    state['plan_sha256'] = digest(plan)
    return _persist(run, state, 'prepare', plan['coordinator_id'])


def _actor(run, actor):
    from acceptance_investigation import coordinator_only
    return coordinator_only(run, actor)


def _evidence(run, evidence_ids):
    if not isinstance(evidence_ids, list) or not evidence_ids or len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError('Concrete nonduplicate evidence required')
    return {eid: digest(run.get_evidence(eid)) for eid in evidence_ids}


def _invalidate(state, case, reason):
    case['case_version'] += 1
    case['states'] = _states()
    case['route'] = 'awaiting_evidence'
    case['route_reason'] = reason
    case['review_status'] = 'invalidated'
    case['reopened'] = True
    case['diagnostic_receipts'] = []
    case['review_receipts'] = []
    case['status_dimensions_by_obligation'] = {}
    case['reviewed_impact_by_obligation'] = {}
    case['reviewed_residual'] = False
    case['investigation_complete'] = False
    case['residual_bounded'] = False
    case['residual'] = {metric: None for metric in METRICS}
    for pid in case['rule_proof_ids']:
        state['rule_proofs'][pid]['state'] = 'invalidated'
    case['rule_proof_ids'] = []
    case['rule_versions'] = []


def reopen(run, actor, case_id, reason, evidence_ids):
    _actor(run, actor); _text(reason, 'reopen reason')
    refs = _evidence(run, evidence_ids)
    state = load(run)
    case = next(c for c in state['cases'] if c['case_id'] == case_id)
    _invalidate(state, case, reason)
    state['changes'].append({'type': 'reopen', 'case_id': case_id, 'reason': reason,
        'evidence': refs, 'actor': actor, 'at': now()})
    return _persist(run, state, 'reopen', actor)


def split(run, actor, case_id, obligation_ids, reason, evidence_ids):
    _actor(run, actor); _text(reason, 'counterexample/split reason')
    refs = _evidence(run, evidence_ids)
    state = load(run)
    case = next(c for c in state['cases'] if c['case_id'] == case_id)
    selected = set(obligation_ids)
    if len(selected) != len(obligation_ids) or not selected or not selected < set(case['obligation_ids']):
        raise ValueError('Split must preserve a nonempty parent and exact nonduplicate selected members')
    child = deepcopy(case)
    child['case_id'] = 'case-' + digest([case_id, case['case_version'], sorted(selected), reason])[:24]
    child['split_parent_id'] = case_id
    child['merge_basis'] = 'counterexample_split; each original obligation retained'
    child['obligation_ids'] = sorted(selected)
    case['obligation_ids'] = sorted(set(case['obligation_ids']) - selected)
    for item in (case, child):
        _invalidate(state, item, reason)
        _refresh_case(item, state['members'])
    child['case_version'] = 1
    state['cases'].append(child)
    for row in state['obligation_case_map']:
        if row['obligation_id'] in selected:
            row['case_id'] = child['case_id']
    # Splitting judgment need not force duplicate extraction. Shared package
    # retains all exact members but now indexes both independently reviewed cases.
    for package in state['collection_packages']:
        if case_id in package['case_ids']:
            package['case_ids'] = sorted(set(package['case_ids']) | {child['case_id']})
    state['changes'].append({'type': 'split', 'parent_case_id': case_id, 'child_case_id': child['case_id'],
        'obligation_ids': sorted(selected), 'reason': reason, 'evidence': refs, 'actor': actor, 'at': now()})
    from acceptance_investigation import all_obligations
    verify_registry(state, list(all_obligations(run).values()))
    return _persist(run, state, 'split', actor)


def _machine_routing(state, receipts):
    from acceptance_machine import cumulative_impact
    packages = {p['package_id']: p for p in state['collection_packages']}
    valid = []
    for item in receipts:
        package = packages.get(item.get('package_id'))
        if not package or item.get('member_hash') != package['member_hash']:
            continue
        covered = set(item.get('obligation_ids', []))
        if not covered <= set(package['obligation_ids']):
            raise ValueError('Diagnosis includes obligations outside its collection package')
        spec = item.get('spec', {})
        actual_members = spec.get('members', [])
        expected_members = [m for m in package['members'] if m['obligation_id'] in covered]
        if not covered or sorted(actual_members, key=canonical) != sorted(expected_members, key=canonical):
            raise ValueError('Diagnosis scope/member facts differ from the original collection members')
        if item.get('computed_member_hash') != digest(sorted(actual_members, key=canonical)):
            raise ValueError('Diagnosis opaque member hash is not a physical membership proof')
        if any(spec.get(k) != package[k] for k in ('site', 'level')) or _path(spec.get('path')) != _path(package['path']):
            raise ValueError('Diagnosis collection scope differs from the registered case package')
        needed = {mm for m in expected_members for mm in m['months']}
        needed.update(m['base_month'] for m in expected_members if m.get('base_month'))
        if needed and (not spec.get('window_start') or not spec.get('window_end') or spec['window_start'] > min(needed) or spec['window_end'] < max(needed)):
            raise ValueError('Diagnosis collection omits an original historical/comparison window')
        valid.append(item)
    facts, selections = {}, []
    for case in state['cases']:
        fact = {'route': 'awaiting_evidence', 'route_reason': 'complete member-bound machine evidence unavailable',
            'diagnostic_receipts': [], 'residual': {m: None for m in METRICS},
            'residual_by_obligation': {}, 'residual_bounded': False,
            'machine_execution': 'not_executed', 'impact_status': 'unbounded'}
        matching = [d for d in valid if set(case['obligation_ids']) <= set(d.get('obligation_ids', [])) and d.get('complete')]
        if matching:
            d = matching[-1]
            bound = d.get('risk_bound', {})
            fact['diagnostic_receipts'] = [{'package_id': d['package_id'], 'diagnosis_id': d.get('diagnosis_id'), 'sha256': digest(d)}]
            fact['machine_execution'] = 'success'
            by_member = {oid: deepcopy(d.get('residual_by_member', {}).get(oid)) for oid in case['obligation_ids']}
            fact['residual_by_obligation'] = by_member
            fact['residual'] = {metric: {'state': 'per_obligation_not_additive',
                'member_values': {oid: v.get(metric) if v else None for oid, v in by_member.items()},
                'basis': 'Original comparison denominators retained; no cause-proposal subtraction'} for metric in METRICS}
            fact['residual_bounded'] = bool(d.get('risk_bounded') and bound.get('risk_bounded'))
            fact['impact_status'] = 'bounded' if fact['residual_bounded'] else 'unbounded'
            known_members = {m['member_id']: m for m in d.get('members', [])}
            for oid in case['obligation_ids']:
                member = known_members.get(oid, {})
                if member.get('kind') == 'result_change' and member.get('state') == 'diagnosed':
                    selections.append({'case_id': case['case_id'], 'diagnosis_id': d['diagnosis_id'], 'member_id': oid})
            if case['rule_proof_ids']:
                fact.update(route='rule_disposition_pending_review', route_reason='deterministic checks proved; remaining checks and original obligation reviews still required')
            elif case['kind'] in PROTECTED_TYPES or case['history_ids']:
                fact.update(route='needs_investigation', route_reason='exact historical/special obligation remains unfulfilled')
            elif d.get('conflicts') or d.get('new_mechanisms') or (d.get('semantic_ambiguities') and not state.get('business_classification_enabled')):
                fact.update(route='needs_investigation', route_reason='evidence conflict, new mechanism, or semantic ambiguity')
            elif not fact['residual_bounded']:
                fact.update(route='needs_investigation', route_reason='residual risk has no verified upper bound')
            elif bound.get('low_impact_eligible') is True and bound.get('recheck_conditions'):
                fact.update(route='low_impact_unverified', route_reason='bounded residual retained for explicit review and recheck', recheck_conditions=bound['recheck_conditions'])
            else:
                fact.update(route='needs_investigation', route_reason='material or insufficiently qualified residual; no release inference')
        facts[case['case_id']] = fact
    cumulative = cumulative_impact(valid, selections=selections, threshold=state['ordering_threshold']) if selections else []
    # The cumulative observations are mandatory, but their 2% strength is only
    # ordering. An unknown boundary prevents low-impact exemption at any score.
    for group in cumulative:
        for cid in group['case_ids']:
            if facts[cid]['route'] == 'low_impact_unverified' and not group.get('risk_bounded'):
                facts[cid].update(route='needs_investigation', route_reason='cumulative residual risk lacks a verified bound')
    return facts, cumulative, selections


def route(run, diagnostics=None, *, actor=None, budget_exhausted=False, reason=None):
    """Route verified machine outputs; user summaries/score cutoffs cannot close."""
    from acceptance_investigation import plan_data
    from acceptance_machine import verified_diagnostics
    coordinator = plan_data(run)['coordinator_id']
    _actor(run, actor or coordinator)
    state = load(run)
    receipts = verified_diagnostics(run)
    if diagnostics is not None:
        supplied = diagnostics if isinstance(diagnostics, list) else [diagnostics]
        selected = {digest(x) for x in supplied}
        if not selected <= {digest(x) for x in receipts}:
            raise ValueError('Routing requires program-verified diagnostic receipts')
        receipts = [x for x in receipts if digest(x) in selected]
    from acceptance_triage import enabled as triage_enabled
    if triage_enabled(run): state['business_classification_enabled'] = True
    facts, cumulative, selections = _machine_routing(state, receipts)
    for case in state['cases']:
        fact = facts[case['case_id']]
        case.update({k: v for k, v in fact.items() if k not in ('machine_execution', 'impact_status')})
        case['states']['check_execution'] = fact['machine_execution']
        case['states']['impact'] = fact['impact_status']
        if case.get('review_receipts') and not case['reopened']:
            reviewed, original_reviews = _verified_case_review(run, case)
            if reviewed:
                _sync_review_state(case, original_reviews)
    state['cumulative_exposure'] = cumulative
    state['cumulative_selections'] = selections
    state['cumulative_input_receipts'] = sorted([{'diagnosis_id': d.get('diagnosis_id'), 'sha256': digest(d)} for d in receipts], key=canonical)
    state['budget'] = {'state': 'exhausted' if budget_exhausted else 'available',
        'pending_work_preserved': True, 'reason': reason,
        'resume_condition': 'capacity_available' if budget_exhausted else None}
    if budget_exhausted:
        _text(reason, 'budget/capacity checkpoint reason')
    return _persist(run, state, 'route_checkpoint' if budget_exhausted else 'route', actor or coordinator)


def shadow_rule(run, actor, definition, case_ids=None):
    """Run an explicitly bounded deterministic predicate without any disposition.

    The first maintained rule proves observed prefix presence only. Semantic
    contract equivalence, leaf completeness and sales truth remain separate.
    """
    _actor(run, actor)
    for key in ('rule_id', 'version', 'mechanism'):
        _text(definition.get(key), key)
    if definition['mechanism'] != 'contract_prefix_presence':
        raise ValueError('No maintained machine predicate for this mechanism')
    scope = definition.get('scope', {})
    _text(scope.get('site'), 'rule country')
    if not scope.get('contract_path') or not scope.get('observed_prefix'):
        raise ValueError('Rule requires exact contract and observed paths')
    state = load(run)
    rid = definition['rule_id'] + '@' + definition['version']
    if rid in state['rules']:
        raise ValueError('Rule version immutable; register a new version')
    source = Source(run)
    selected = set(case_ids or [c['case_id'] for c in state['cases'] if c['kind'] == 'contract_scope_missing' and c['site'] == scope['site'] and c['path'] == scope['contract_path']])
    eligible = [c for c in state['cases'] if c['case_id'] in selected]
    if len(eligible) != len(selected) or not eligible:
        raise ValueError('Rule shadow cases missing')
    results = []
    for case in eligible:
        if case['kind'] != 'contract_scope_missing' or case['site'] != scope['site'] or case['path'] != scope['contract_path']:
            raise ValueError('Rule scope does not match every selected member')
        prefix = tuple(scope['observed_prefix'])
        for oid in case['obligation_ids']:
            member = state['members'][oid]
            rows, evidence = [], set()
            for (site, path), months in source.raw_paths.items():
                if site == scope['site'] and path[:len(prefix)] == prefix and member['month'] in months:
                    rows.append(months[member['month']])
                    evidence.update(source.row_sources[('raw_paths', site, path, member['month'])])
            rows.sort(key=canonical)
            results.append({'obligation_id': oid, 'case_id': case['case_id'], 'case_version': case['case_version'],
                'member_hash': case['member_hash'], 'source_hash': member['source_hash'],
                'observed_month': member['month'], 'observed_prefix': list(prefix),
                'applies': bool(rows), 'row_count': len(rows), 'rows_sha256': digest(rows),
                'evidence_ids': sorted(evidence), 'checks_proven': ['scope_baseline'] if rows else [],
                'proof_boundary': 'Observed candidate prefix presence only; does not prove contract equivalence, leaf completeness, factual sales, or other required checks'})
    value = {'definition': deepcopy(definition), 'state': 'shadow', 'created_by': actor,
        'results': sorted(results, key=lambda r: r['obligation_id']), 'validation': None,
        'source_binding': deepcopy(state['source_binding']), 'automatic_business_closure': False}
    value['shadow_sha256'] = digest({k: v for k, v in value.items() if k != 'shadow_sha256'})
    state['rules'][rid] = value
    _persist(run, state, 'rule_shadow', actor)
    return value


def _verify_rule_control(run, state, rule, control, actor):
    """Validate one maintained boundary counterexample from a complete raw scan.

    A literal prefix observation can coexist with a missing previously observed
    named leaf. This is a testable limit of the rule, not semantic equivalence.
    Unrelated countries, invented leaf paths and free-form mechanisms cannot
    serve as negative controls.
    """
    from acceptance_investigation import task_records
    from acceptance_queries import plan as scan_plan
    from acceptance_validate import verify_job
    if control.get('kind') not in ('historical_counterexample', 'actual_exception'):
        raise ValueError('Unsupported independent rule control kind')
    oid = control.get('obligation_id')
    member = state['members'].get(oid)
    if not member:
        raise ValueError('Control is not a concrete registered obligation')
    if control['kind'] == 'historical_counterexample' and member['source_type'] != 'historical':
        raise ValueError('Historical control must preserve a historical obligation')
    scope = rule['definition']['scope']; prefix = tuple(scope['observed_prefix'])
    if member['site'] != scope['site']:
        raise ValueError('Rule control must concern the same rule country')
    path = tuple(_path(control.get('control_path')))
    if len(path) != 6 or path != tuple(member['path']) or path[:len(prefix)] != prefix:
        raise ValueError('Rule control must name the exact original raw leaf under the rule prefix')
    months = control.get('months')
    shadow_months = {r['observed_month'] for r in rule['results']}
    if not isinstance(months, list) or not months or len(months) != len(set(months)) or set(months) != set(member['months']) or not set(months) <= shadow_months:
        raise ValueError('Rule control must cover its full original months within the rule shadow window')
    if control.get('mechanism') != 'prefix_present_named_leaf_absent' or control.get('expected_presence') is not False:
        raise ValueError('No maintained predicate for this semantic exception; keep the rule in shadow')
    task = task_records(run).get(control.get('worker_task_id'))
    if not task or task['agent_id'] != actor or actor == rule['created_by'] or oid not in task['obligation_ids']:
        raise ValueError('Control must belong to an independent worker owning the exact obligation')
    worker = Run(run.path / task['worker_dir'])
    if worker.verify_inputs() or worker.verify_events():
        raise ValueError('Independent control worker provenance changed')
    job_id = _text(control.get('job_id'), 'independent control job')
    jobs = [j for j in scan_plan(worker.manifest) if j['job_id'] == job_id and j['family'] == 'raw_paths']
    if len(jobs) != 1 or jobs[0]['site'] != scope['site']:
        raise ValueError('Independent control needs the complete frozen raw-path job for the rule country')
    if worker.manifest['starts'][scope['site']] > min(months) or worker.manifest['data_month'] < max(months):
        raise ValueError('Independent control query does not cover all declared months')
    errors = verify_job(worker, jobs[0])
    if errors:
        raise ValueError('Independent control typed job verification failed: ' + '; '.join(errors))
    rows = worker.job_rows(job_id)
    scoped = [row for row in rows if tuple(_path(row['path'])) == path]
    prior = [row['month_dt'][:7] for row in scoped
        if row['month_dt'][:7] < min(months) and float(row['nrows']) > 0]
    baseline = max(prior) if prior else None
    if baseline is None or control.get('baseline_month') != baseline:
        raise ValueError('Rule counterexample requires the named leaf latest actual pre-gap baseline')
    observations = []
    for month in sorted(months):
        leaf_rows = [row for row in scoped if row['month_dt'][:7] == month and float(row['nrows']) > 0]
        prefix_rows = [row for row in rows if row['month_dt'][:7] == month
            and tuple(_path(row['path']))[:len(prefix)] == prefix and float(row['nrows']) > 0]
        if leaf_rows or not prefix_rows:
            raise ValueError('Rule boundary counterexample failed: each month needs prefix presence and named leaf absence')
        observations.append({'month': month, 'control_path': list(path),
            'leaf_present': False, 'prefix_present': True, 'prefix_rows_sha256': digest(sorted(prefix_rows, key=canonical))})
    return {'verified_worker_event_hash': worker.previous,
        'verified_job_sha256': digest(worker.job_record(job_id)),
        'verified_rows_sha256': digest(rows), 'verified_control_observations': observations,
        'verified_baseline_rows_sha256': digest(sorted([r for r in scoped if r['month_dt'][:7] == baseline], key=canonical))}


def _rule_recheck(run, state, rule):
    """Recompute presence for all members from frozen complete raw-path rows."""
    source = Source(run)
    if rule.get('validation'):
        validation = rule['validation']
        if digest(validation) != rule.get('validation_sha256'):
            raise ValueError('Independent rule validation changed')
        for control in validation['controls']:
            verified = _verify_rule_control(run, state, rule, control, validation['reviewer_id'])
            if any(control.get(key) != value for key, value in verified.items()):
                raise ValueError('Independent rule control evidence changed after validation')
    scope = rule['definition']['scope']; prefix = tuple(scope['observed_prefix'])
    cases = {c['case_id']: c for c in state['cases']}
    for result in rule['results']:
        case = cases[result['case_id']]
        if case['case_version'] != result['case_version'] or case['member_hash'] != result['member_hash']:
            raise ValueError('Rule member version changed')
        rows = [months[result['observed_month']] for (site, path), months in source.raw_paths.items()
            if site == scope['site'] and path[:len(prefix)] == prefix and result['observed_month'] in months]
        if digest(sorted(rows, key=canonical)) != result['rows_sha256'] or bool(rows) != result['applies']:
            raise ValueError('Deterministic rule result no longer reproduces')
        for eid in result['evidence_ids']:
            run.get_evidence(eid)
    return source


def validate_rule(run, actor, rule_id, rule_version, validation):
    """Register independently executed evidence checks before a rule can apply.

    Validation controls must be independently registered job results, not
    explained=true or a reviewer agreeing with the shadow summary.
    """
    run.writable(); _text(actor, 'independent validator')
    state = load(run); rid = rule_id + '@' + rule_version; rule = state['rules'][rid]
    if actor == rule['created_by']:
        raise ValueError('Rule author cannot independently validate own rule')
    if rule['state'] != 'shadow':
        raise ValueError('Only shadow rules can be independently validated')
    if validation.get('shadow_sha256') != rule['shadow_sha256']:
        raise ValueError('Independent validation binds a different rule version')
    from acceptance_investigation import plan_data
    if actor == plan_data(run)['coordinator_id']:
        raise ValueError('Independent rule check must precede separate coordinator approval')
    checked = validation.get('checked_obligation_ids', [])
    if not checked or len(checked) != len(set(checked)) or not set(checked) <= {r['obligation_id'] for r in rule['results']}:
        raise ValueError('Independent sample does not match rule members')
    _text(validation.get('alternative_explanations'), 'alternative mechanisms and rule limits')
    _text(validation.get('remaining_uncertainty'), 'remaining uncertainty')
    controls = validation.get('controls', [])
    if not {'historical_counterexample', 'actual_exception'} <= {c.get('kind') for c in controls}:
        raise ValueError('Historical counterexamples and actual exceptions are mandatory')
    source = _rule_recheck(run, state, rule)
    checked_evidence = _evidence(run, validation.get('checked_evidence_ids', []))
    sample_evidence = {eid for r in rule['results'] if r['obligation_id'] in checked for eid in r['evidence_ids']}
    if not sample_evidence <= set(checked_evidence):
        raise ValueError('Independent sample omits key raw evidence')
    if len({c.get('obligation_id') for c in controls}) != len(controls):
        raise ValueError('Independent controls must preserve distinct concrete obligations')
    verified_controls = []
    for control in controls:
        verified_controls.append({**deepcopy(control), **_verify_rule_control(run, state, rule, control, actor)})
    rule['state'] = 'independently_validated'
    rule['validation'] = {**deepcopy(validation), 'controls': verified_controls, 'reviewer_id': actor, 'evidence_hashes': checked_evidence}
    rule['validation_sha256'] = digest(rule['validation'])
    _persist(run, state, 'rule_independent_validation', actor)
    return rule


def apply_rule(run, actor, rule_id, rule_version):
    """Produce reviewable per-obligation machine facts, never business closure."""
    _actor(run, actor)
    state = load(run); rid = rule_id + '@' + rule_version; rule = state['rules'][rid]
    if rule['state'] != 'independently_validated' or not rule.get('validation'):
        raise ValueError('Rule requires shadow run and independent control validation')
    _rule_recheck(run, state, rule)
    cases = {c['case_id']: c for c in state['cases']}
    proofs = []
    for result in rule['results']:
        if not result['applies']:
            continue
        case = cases[result['case_id']]
        proof_id = 'proof-' + digest([rid, result, rule['validation_sha256']])[:24]
        proof = {'proof_id': proof_id, 'rule_id': rule_id, 'rule_version': rule_version,
            'case_id': result['case_id'], 'case_version': case['case_version'],
            'obligation_id': result['obligation_id'], 'obligation_ids': [result['obligation_id']],
            'member_hash': case['member_hash'], 'source_hash': result['source_hash'],
            'checks_proven': result['checks_proven'], 'evidence_ids': result['evidence_ids'],
            'result': result, 'independent_review_id': rule['validation_sha256'],
            'rule_shadow_sha256': rule['shadow_sha256'], 'rule_validation_sha256': rule['validation_sha256'],
            'state': 'pending_obligation_review', 'business_verified': False,
            'remaining_required_checks': sorted(set(state['members'][result['obligation_id']]['required_checks']) - set(result['checks_proven']))}
        proof_hash = digest(proof)
        write(home(run) / 'rule-proofs' / (proof_id + '.json'), {'payload': proof, 'sha256': proof_hash})
        state['rule_proofs'][proof_id] = {'sha256': proof_hash, 'state': 'pending_obligation_review', 'case_id': case['case_id']}
        case['rule_proof_ids'].append(proof_id)
        case['rule_versions'] = sorted(set(case['rule_versions']) | {rid})
        case['route'] = 'rule_disposition_pending_review'
        proofs.append({'rule_id': rule_id, 'rule_version': rule_version, 'proof_id': proof_id,
            'proof_sha256': proof_hash, 'case_id': case['case_id'], 'obligation_id': result['obligation_id']})
    rule['state'] = 'applied_pending_obligation_reviews'
    _persist(run, state, 'rule_apply', actor)
    return proofs


def verify_rule_fulfillment(run, obligation, fulfillment):
    state = load(run)
    pid = fulfillment.get('proof_id', '')
    if not re.fullmatch(r'proof-[0-9a-f]{24}', pid):
        raise ValueError('Invalid rule proof ID')
    pointer = state['rule_proofs'].get(pid)
    if not pointer or pointer['state'] != 'pending_obligation_review':
        raise ValueError('Rule proof absent, invalidated or reopened')
    value = read(home(run) / 'rule-proofs' / (pid + '.json')); proof = value['payload']
    if digest(proof) != value['sha256'] or value['sha256'] != pointer['sha256'] or value['sha256'] != fulfillment.get('proof_sha256'):
        raise ValueError('Rule proof changed')
    for key in ('rule_id', 'rule_version', 'case_id'):
        if proof[key] != fulfillment.get(key):
            raise ValueError('Rule fulfillment version mismatch')
    if proof['obligation_id'] != obligation['obligation_id'] or proof['source_hash'] != obligation['source_hash']:
        raise ValueError('Rule proof cannot close another obligation')
    case = next(c for c in state['cases'] if c['case_id'] == proof['case_id'])
    if case['member_hash'] != proof['member_hash'] or case['case_version'] != proof['case_version'] or pid not in case['rule_proof_ids']:
        raise ValueError('Rule proof case members/review version changed')
    rule = state['rules'][proof['rule_id'] + '@' + proof['rule_version']]
    if rule['state'] != 'applied_pending_obligation_reviews' or rule['validation_sha256'] != proof['rule_validation_sha256']:
        raise ValueError('Rule independent validation absent or invalidated')
    _rule_recheck(run, state, rule)
    return {key: proof[key] for key in ('checks_proven', 'evidence_ids', 'proof_id', 'rule_id', 'rule_version', 'independent_review_id')} | {'proof_sha256': value['sha256']}


def invalidate_rule(run, actor, rule_id, rule_version, reason, evidence_ids):
    _actor(run, actor); _text(reason, 'rule counterexample reason')
    refs = _evidence(run, evidence_ids)
    state = load(run); rid = rule_id + '@' + rule_version; rule = state['rules'][rid]
    affected = {r['case_id'] for r in rule['results']}
    rule['state'] = 'invalidated'
    rule['invalidated_by'] = {'actor': actor, 'reason': reason, 'evidence': refs}
    for case in state['cases']:
        if case['case_id'] in affected:
            _invalidate(state, case, 'rule counterexample: ' + reason)
    state['changes'].append({'type': 'rule_reopen', 'rule_id': rid, 'case_ids': sorted(affected),
        'reason': reason, 'evidence': refs, 'actor': actor, 'at': now()})
    return _persist(run, state, 'rule_counterexample_reopen', actor)


def _review_context(run, obligations=None):
    from acceptance_investigation import task_records,all_obligations,plan_data
    plan=plan_data(run)
    return {'tasks':task_records(run),
            'obligations':all_obligations(run,plan) if obligations is None else obligations,
            'main':plan['coordinator_id'],'submissions':{},'reviews':{}}


def _verify_review_context(run, context):
    """Reuse within one operation only; reject material changes before returning."""
    from acceptance_investigation import checked_submission,task_records
    if task_records(run)!=context['tasks']:
        raise ValueError('Case task changed during review synchronization')
    for tid in context['submissions']:
        if checked_submission(run,context['tasks'][tid])!=context['submissions'][tid]:
            raise ValueError('Case submission changed during review synchronization')
        current=read(run.path/'investigations'/'reviews'/(tid+'.json'))
        if current!=context['reviews'][tid]:raise ValueError('Case review changed during synchronization')


def _verified_case_review(run, case, *, context=None):
    from acceptance_investigation import (task_records, checked_submission, all_obligations,
        plan_data, verify_answer, _rebuild_proof, entity_assessments)
    import acceptance_review as substantive
    context=_review_context(run) if context is None else context
    tasks,obligations,main=(context[k] for k in ('tasks','obligations','main'))
    receipts = []
    for oid in case['obligation_ids']:
        matches = [t for t in tasks.values() if t['state'] != 'requeued' and oid in t['obligation_ids']]
        if len(matches) != 1 or matches[0].get('outcomes', {}).get(oid) not in ('verified', 'reviewed_residual', 'blocked_external'):
            return False, []
        task = matches[0]
        # checked_submission verifies both parent and worker source files; never
        # substitute cached proof bytes or swallow a stale-material failure.
        tid=task['task_id']
        if tid not in context['submissions']:
            context['submissions'][tid]=checked_submission(run,task)
            context['reviews'][tid]=read(run.path/'investigations'/'reviews'/(tid+'.json'))
        submission=context['submissions'][tid];review=context['reviews'][tid]
        if digest(review) != task['review_sha256'] or review['submission_sha256'] != task['submission_sha256'] or review['reviewer_id'] != main or main == task['agent_id']:
            raise ValueError('Case review is not a current independent coordinator review')
        entries = [r for r in review['obligations'] if r['obligation_id'] == oid]
        if len(entries) != 1:
            raise ValueError('Case review omits the original obligation')
        entry = entries[0]
        bound = entry.get('case_binding', {})
        reviewed_id = entry.get('case_id', bound.get('case_id'))
        version = entry.get('case_version', bound.get('case_version', review.get('case_versions', {}).get(case['case_id'])))
        if reviewed_id != case['case_id'] or version != case['case_version']:
            return False, []
        ob = obligations[oid]
        answer = next(a for a in submission['payload']['answers'] if a['obligation_id'] == oid)
        checks = entry.get('check_reviews', {})
        if entry.get('decision') != 'accept' or set(checks) != set(ob['required_checks']) or any(v.get('decision') != 'accept' for v in checks.values()):
            raise ValueError('Case review did not accept every original required check')
        _text(entry.get('reason'), 'original review reason'); _text(entry.get('scope_check'), 'original scope review')
        for check in checks.values():
            _text(check.get('reason'), 'original check review reason')
        proof = submission['payload'].get('population_proofs', {}).get(oid)
        required_evidence = {eid for check in answer['checks'].values() for eid in check['evidence_ids']}
        required_evidence.update(eid for impact in answer.get('impact', {}).values() for eid in impact['evidence_ids'])
        if substantive.enabled(run):
            required_evidence.update(substantive.evidence_ids(answer, proof))
        if proof:
            groups = entity_assessments(answer)
            required_evidence.update(proof['evidence_ids'])
            required_evidence.update(eid for group in groups for eid in group['evidence_ids'])
            reviews = entry.get('entity_reviews', [])
            if len(reviews) != len(groups) or {r.get('assessment_sha256') for r in reviews} != {digest(g) for g in groups} or any(r.get('decision') != 'accept' for r in reviews):
                raise ValueError('Case review omits an original entity-group assessment')
            for group in reviews:
                _text(group.get('reason'), 'original entity review reason')
        checked = _evidence(run, entry.get('evidence_checked', []))
        if not required_evidence <= set(checked):
            raise ValueError('Case review omitted original answer or proof evidence')
        blocked = verify_answer(run, ob, answer, run.get_evidence)
        outcome = task['outcomes'][oid]
        residual = substantive.enabled(run) and any(i.get('unexplained', 0) > 0 for i in answer.get('impact', {}).values())
        expected_outcome = 'blocked_external' if blocked else ('reviewed_residual' if residual else 'verified')
        if outcome != expected_outcome:
            raise ValueError('Case review changed the original blocker or reviewed residual outcome')
        if substantive.enabled(run):
            rebuilt = _rebuild_proof(run, task, ob, submission)
            substantive.validate_business_review(run, task, ob, answer, proof, entry,
                submission['sha256'], recomputed_proof=rebuilt)
            if 'program_recomputation_sha256' not in entry or entry['program_recomputation_sha256'] != (digest(rebuilt) if rebuilt else None):
                raise ValueError('Case original review program recomputation no longer reproduces')
        if ob['source_type'] == 'history_source':
            segments = entry.get('history_segment_reviews', [])
            expected = {segment['segment_id'] for segment in ob['source_material']['segments']}
            if len(segments) != len(expected) or {segment.get('segment_id') for segment in segments} != expected or any(segment.get('decision') != 'accept' for segment in segments):
                raise ValueError('Case review omitted an original history-source segment')
            for segment in segments:
                _text(segment.get('reason'), 'original history segment review reason')
        dimensions = deepcopy(answer.get('status_dimensions', answer.get('states', {})))
        receipts.append({'obligation_id': oid, 'task_id': task['task_id'],
            'review_sha256': task['review_sha256'], 'submission_sha256': task['submission_sha256'],
            'case_id': case['case_id'], 'case_version': case['case_version'], 'outcome': outcome,
            'answer_states': dimensions, 'status_dimensions': dimensions,
            'impact': deepcopy(answer.get('impact', {})),
            'residual_boundary': deepcopy(answer.get('residual_boundary')),
            'business_verdict': answer.get('business_verdict')})
    return True, receipts


def _sync_review_state(case, receipts):
    """Preserve the original findings separately from collection observations."""
    blocked = any(r['outcome'] == 'blocked_external' for r in receipts)
    residual = any(r['outcome'] == 'reviewed_residual' for r in receipts)
    case['review_receipts'] = receipts
    case['review_status'] = 'approved'
    case['states']['review'] = 'approved'
    case['states']['investigation'] = 'blocked_external' if blocked else 'completed'
    case['status_dimensions_by_obligation'] = {r['obligation_id']:
        {**deepcopy(r['status_dimensions']), 'review': 'approved'} for r in receipts}
    case['reviewed_impact_by_obligation'] = {r['obligation_id']:
        {'impact': deepcopy(r['impact']), 'residual_boundary': deepcopy(r['residual_boundary'])} for r in receipts}
    for target, dimension in (('defect', 'defect'), ('cause', 'cause'), ('impact', 'impact'), ('business_acceptance', 'acceptability')):
        values = {r['status_dimensions'].get(dimension, 'undetermined') for r in receipts}
        case['states'][target] = next(iter(values)) if len(values) == 1 else 'per_obligation'
    case['investigation_complete'] = not blocked
    case['reviewed_residual'] = residual
    case['route'] = 'blocked_external' if blocked else ('reviewed_residual' if residual else 'reviewed')
    case['route_reason'] = 'Current original obligation reviews synchronized; release is separately gated'


def acknowledge_review(run, actor, case_id):
    """Sync actual version-bound original reviews; never approve in the case layer."""
    return acknowledge_reviews(run,actor,[case_id])


def acknowledge_reviews(run, actor, case_ids):
    """Validate the entire selection before one persisted update; no partial approval."""
    _actor(run, actor)
    if (not isinstance(case_ids,list) or not case_ids or any(not isinstance(cid,str) for cid in case_ids)
            or len(case_ids)!=len(set(case_ids))):
        raise ValueError('Case selection must be a nonempty list of unique IDs')
    state = load(run)
    selected={c['case_id']:c for c in state['cases'] if c['case_id'] in set(case_ids)}
    if set(selected)!=set(case_ids):raise ValueError('Unknown case in review selection')
    context=_review_context(run);results={}
    for cid in case_ids:
        approved,receipts=_verified_case_review(run,selected[cid],context=context)
        if not approved:
            raise ValueError('Every original obligation requires a current review bound to this case version')
        results[cid]=receipts
    # Cache is scoped to this single locked operation and revalidated before commit.
    _verify_review_context(run,context)
    for cid,receipts in results.items():
        _sync_review_state(selected[cid],receipts)
        selected[cid]['reopened']=False
    return _persist(run,state,'acknowledge_reviews',actor)


def summary(state):
    cases = state['cases']; mapping = state['obligation_case_map']; packages = state['collection_packages']
    routes = dict(Counter(c['route'] for c in cases))
    complete = sum(c['states']['check_execution'] == 'success' for c in cases)
    return {'protocol': PROTOCOL, 'original_obligations': len(mapping),
        'original_obligation_types': dict(Counter(m['source_type'] for m in mapping)),
        'handling_cases': len(cases), 'case_kinds': dict(Counter(c['kind'] for c in cases)),
        'collection_packages': len(packages),
        'result_collection_scopes': sum(p['kind'] == 'result' for p in packages),
        'obligation_mapping_count': len({m['obligation_id'] for m in mapping}),
        'obligation_mapping_rate': len({m['obligation_id'] for m in mapping}) / len(mapping) if mapping else 0,
        'machine_complete_cases': complete,
        'machine_diagnosis_completion_rate': complete / len(cases) if cases else 0,
        'rule_proven_check_dispositions': sum(p['state'] == 'pending_obligation_review' for p in state['rule_proofs'].values()),
        'business_closed_by_rules': 0, 'routes': routes,
        'awaiting_evidence': routes.get('awaiting_evidence', 0),
        'needs_investigation': routes.get('needs_investigation', 0),
        'external_evidence_blocked': routes.get('blocked_external', 0),
        'reviewed_residual_cases': routes.get('reviewed_residual', 0),
        'original_review_outcomes': dict(Counter(r['outcome'] for c in cases for r in c.get('review_receipts', []))),
        'independent_historical_obligations': sum(m['source_type'] == 'historical' for m in mapping),
        'reopened_cases': sum(c['reopened'] for c in cases),
        'risk_unbounded_cases': sum(not c['residual_bounded'] for c in cases),
        'budget': state['budget'],
        'release_eligible': False,
        'interpretation': 'Handling/collection reduction is not investigation completion, historical rediscovery, saved runtime, final Agent count, or release approval.'}


def audit(run):
    import acceptance_questions as questions
    if questions.enabled(run):return questions.case_status(run)
    from acceptance_investigation import all_obligations
    if not (home(run) / 'state.json').exists():
        return {'required': True, 'complete': False, 'errors': ['case_registry_missing'],
            'original_obligations': len(all_obligations(run)), 'obligation_mapping_count': 0}
    errors = []
    try:
        state = load(run)
        obligations = list(all_obligations(run).values())
        verify_registry(state, obligations)
        if state['source_binding']['obligations_sha256'] != digest(sorted(obligations, key=lambda o: o['obligation_id'])):
            raise ValueError('Case original obligations changed')
        for pid, pointer in state['rule_proofs'].items():
            value = read(home(run) / 'rule-proofs' / (pid + '.json'))
            if digest(value['payload']) != value['sha256'] or value['sha256'] != pointer['sha256']:
                raise ValueError('Rule proof file changed')
        if 'cumulative_exposure' in state:
            from acceptance_machine import verified_diagnostics
            receipts = verified_diagnostics(run)
            expected_receipts = sorted([{'diagnosis_id': d.get('diagnosis_id'), 'sha256': digest(d)} for d in receipts], key=canonical)
            if expected_receipts != state.get('cumulative_input_receipts'):
                raise ValueError('Machine receipts changed after case routing; route again')
            facts, cumulative, selections = _machine_routing(state, receipts)
            if cumulative != state['cumulative_exposure'] or selections != state['cumulative_selections']:
                raise ValueError('Cumulative exposure or deduplicated membership changed')
            for case in state['cases']:
                fact = facts[case['case_id']]
                if case['states']['check_execution'] != fact['machine_execution'] or case['diagnostic_receipts'] != fact['diagnostic_receipts'] or case['residual'] != fact['residual']:
                    raise ValueError('Case machine completion/residual does not reproduce')
        review_context=_review_context(run,{o['obligation_id']:o for o in obligations})
        for case in state['cases']:
            if case.get('review_receipts'):
                approved, receipts = _verified_case_review(run, case,context=review_context)
                if not approved or receipts != case['review_receipts']:
                    raise ValueError('Case approval lost its exact original obligation review version')
        _verify_review_context(run,review_context)
        result = summary(state)
        from acceptance_triage import enabled as triage_enabled, status as triage_status
        reviewed_defers = set()
        if triage_enabled(run):
            routing = triage_status(run, state)
            result['business_classification'] = {k:v for k,v in routing.items() if k!='cases'}
            errors.extend(routing['errors'])
            reviewed_defers = {c['case_id'] for c in routing['cases'] if c['classified'] and c['investigation_route']=='defer'}
        pending_reopens = [c['case_id'] for c in state['cases'] if c['reopened'] and c['case_id'] not in reviewed_defers]
        if pending_reopens:
            errors.append('case_reopened_requires_new_obligation_review:' + str(len(pending_reopens)))
        result.update(required=True, complete=not errors, errors=errors,
            mapping_complete=True, business_complete=False,
            note='Mapping integrity is separate from machine coverage and substantive investigation approval.')
        return result
    except (ValueError, KeyError, TypeError, FileNotFoundError) as exc:
        errors.append(str(exc))
    return {'required': True, 'complete': False, 'mapping_complete': False,
        'business_complete': False, 'errors': errors}


def _csv(path, rows, columns):
    with Path(path).open('w', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=columns); writer.writeheader()
        for row in rows:
            writer.writerow({key: canonical(row.get(key)) if isinstance(row.get(key), (list, dict, tuple)) else row.get(key) for key in columns})


def offline_replay(source_path, frozen_inputs, output, *, comparison_dir=None):
    """Replay an explicit frozen population; no live history reads or mutations."""
    started = time.monotonic()
    source_path, frozen_inputs, output = Path(source_path).resolve(), Path(frozen_inputs).resolve(), Path(output).resolve()
    if output == frozen_inputs or output.is_relative_to(frozen_inputs) or frozen_inputs.is_relative_to(output):
        raise ValueError('Replay output must not overwrite or enclose frozen inputs')
    if output == source_path or output.is_relative_to(source_path) or source_path.is_relative_to(output):
        raise ValueError('Replay output must not overwrite or enclose archived source')
    if not (source_path / 'sealed.json').exists():
        raise ValueError('Offline comparison requires the exact sealed source')
    manifest_file = frozen_inputs.parent / 'input-hashes.json'
    frozen_manifest = read(manifest_file)
    hashes = {}
    for name, expected in frozen_manifest['snapshot_hashes'].items():
        actual = digest((frozen_inputs / name).read_bytes())
        if actual != expected:
            raise ValueError('Frozen comparison input changed: ' + name)
        hashes[name] = actual
    if Path(frozen_manifest['source_run']).resolve() != source_path:
        raise ValueError('Frozen comparison source does not match requested archive')
    source = Source(source_path)
    if source.hashes != frozen_manifest['sealed_source_files']:
        raise ValueError('Frozen comparison source file population changed')
    plan = read(frozen_inputs / 'obligation-plan.json')
    if digest(plan['payload']) != plan['sha256']:
        raise ValueError('Frozen obligation plan changed')
    bridge = read(frozen_inputs / 'evidence-id-bridge.json')
    state = build_registry(source, plan['payload']['obligations'], bridge=bridge)
    # The 120 source issues remain identical to the frozen history snapshot.
    history = read(frozen_inputs / 'open-issues.json') + read(frozen_inputs / 'legacy-issues.json')
    if {x['issue_id']: digest(x) for x in history} != {x['issue_id']: digest(x['source_issue']) for x in state['history_route_map']}:
        raise ValueError('Historical source population differs from frozen obligation plan')
    comparison_dir = Path(comparison_dir).resolve() if comparison_dir else frozen_inputs.parent
    previous = read(comparison_dir / 'obligation-case-map.json')
    previous_map = {row['obligation_id']: row['case_id'] for row in previous}
    current_map = {row['obligation_id']: row['case_id'] for row in state['obligation_case_map']}
    if set(previous_map) != set(current_map):
        raise ValueError('Replay denominator differs from frozen exploration')
    changed = [oid for oid in current_map if current_map[oid] != previous_map[oid]]
    compare = {'frozen_original_obligations': len(previous_map),
        'current_original_obligations': len(current_map), 'mapping_same': not changed,
        'changed_obligation_ids': changed,
        'frozen_handling_cases': len(set(previous_map.values())), 'current_handling_cases': len(state['cases']),
        'old_route_meaning': 'observed-impact direct-dispatch sensitivity scenario only',
        'current_route_meaning': 'all incomplete full-member diagnostics remain awaiting_evidence; 2% only orders collection',
        'historical_protection_is_rediscovery': False}
    regression = read(frozen_inputs / 'regression.json')
    guard_rows = []
    for example in regression:
        selected = [h for h in state['history_route_map'] if h['site'] == example['site'] and example['month'] in h['months'] and set(example['product_or_sku_ids']) & set(h['entity_ids'])]
        missing = sorted(set(example['product_or_sku_ids']) - {eid for h in selected for eid in h['entity_ids']})
        guard_rows.append({'issue': example['issue'], 'protected': bool(selected) and not missing,
            'case_ids': sorted(h['history_case_id'] for h in selected), 'missing_entity_ids': missing,
            'independently_rediscovered': None, 'business_verified': False})
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {'registry.json': state, 'cases.json': state['cases'],
        'obligation-case-map.json': state['obligation_case_map'],
        'collection-packages.json': state['collection_packages'],
        'history-route-map.json': state['history_route_map'], 'scope-alias-proof.json': state['alias_proofs'],
        'contract-crosswalk-proposals.json': state['contract_crosswalk_proposals'],
        'comparison.json': compare, 'historical-object-protection.json': guard_rows}
    result = summary(state)
    result.update(source_run=str(source_path), frozen_input_hashes=hashes,
        source_jobs=source.loaded_jobs, source_rows=source.loaded_rows,
        fresh_queries=0, actual_agent_dispatches=0, model_usage=None,
        model_usage_note='No model was dispatched by the replay; orchestration tokens not exposed by this program',
        duration_seconds=round(time.monotonic() - started, 3),
        selected_history_protected=sum(x['protected'] for x in guard_rows),
        independent_history_rediscovery='not_measured', comparison=compare)
    artifacts['summary.json'] = result
    for name, value in artifacts.items():
        write(output / name, value)
    _csv(output / 'obligation-case-map.csv', state['obligation_case_map'], list(state['obligation_case_map'][0]))
    write(output / 'input-hashes.json', {'source_run': str(source_path), 'source_files': source.hashes,
        'frozen_inputs': hashes, 'frozen_input_manifest_sha256': digest(manifest_file.read_bytes()),
        'implementation_sha256': digest(Path(__file__).read_bytes()),
        'grouping_sha256': digest(Path(__file__).with_name('acceptance_case_grouping.py').read_bytes())})
    write(output / 'validation.json', {'mapping_complete': True, 'mapping_identical_to_frozen_exploration': not changed,
        'selected_history_objects_protected': all(x['protected'] for x in guard_rows),
        'automatic_business_closures': 0, 'frozen_inputs_unchanged': all(digest((frozen_inputs / k).read_bytes()) == v for k, v in hashes.items()),
        'artifact_hashes': {name: digest((output / name).read_bytes()) for name in artifacts if name != 'summary.json'},
        'business_investigation_completed': False})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    replay = sub.add_parser('replay')
    replay.add_argument('--source', type=Path, required=True)
    replay.add_argument('--frozen-inputs', type=Path, required=True)
    replay.add_argument('--output', type=Path, required=True)
    replay.add_argument('--comparison-dir', type=Path)
    args = parser.parse_args()
    if args.command == 'replay':
        result = offline_replay(args.source, args.frozen_inputs, args.output, comparison_dir=args.comparison_dir)
        print(json.dumps({k: v for k, v in result.items() if k not in ('frozen_input_hashes',)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
