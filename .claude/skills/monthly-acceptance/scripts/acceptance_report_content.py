"""Macro audit coverage and optional auxiliary presentation; never completion credit."""
FAMILIES={'completeness':'完整性','duplication':'重复和放大','identity':'身份连续性',
          'conservation':'数值守恒','time_series':'时间序列','price_bands':'价格带',
          'historical_regression':'历史缺陷回归'}
STATES={'checked_no_signal':'已查未见信号','signal':'有信号，判断见证据',
        'pending':'待核实','not_applicable':'有据不适用'}


def errors(content):
    result=[];seen=set()
    overview=content.get('audit_overview',[])
    if not isinstance(overview,list):return ['report_audit_overview_not_list']
    for item in overview:
        if not isinstance(item,dict):
            result.append('report_audit_overview_invalid_item');continue
        family=item.get('family')
        if not isinstance(family,str) or family not in FAMILIES or family in seen:
            result.append('report_audit_family_unknown_or_duplicate');continue
        seen.add(family)
        state=item.get('state')
        if not isinstance(state,str) or state not in STATES:result.append('report_audit_state_invalid:'+family)
        if any(not isinstance(item.get(k),str) or not item[k].strip() for k in ('scope','basis')):
            result.append('report_audit_scope_or_basis_missing:'+family)
        refs=item.get('evidence_ids',[])
        if not isinstance(refs,list) or (state!='pending' and not refs):
            result.append('report_audit_evidence_missing:'+family)
    for finding in content.get('findings',[]):
        if finding.get('report_placement', 'main') not in ('main','brief','appendix'):
            result.append('finding_report_placement_invalid')
        if finding.get('presentation','main') not in ('main','auxiliary'):
            result.append('finding_presentation_invalid')
    return result


def placement(finding):
    return finding.get('report_placement', 'appendix' if finding.get('presentation') == 'auxiliary' else 'main')


def resolve_placements(content, classification):
    """Use the latest reviewed case decisions in every report outlet."""
    from copy import deepcopy
    content = deepcopy(content)
    by_id = {c['case_id']: c for c in classification.get('cases', [])}
    by_oid = {oid: c for c in by_id.values() for oid in c['obligation_ids']}
    problems = []
    for f in content.get('findings', []):
        linked = [by_id[cid] for cid in f.get('case_ids', []) if cid in by_id]
        linked += [by_oid[oid] for oid in f.get('obligation_ids', []) if oid in by_oid]
        if set(f.get('case_ids', []))-set(by_id): problems.append('finding_unknown_case')
        if not linked:
            problems.append('finding_case_classification_missing'); continue
        if any(not c['classified'] for c in linked): problems.append('finding_case_not_classified')
        # case_ids locate scope/observations, not per-obligation approval.
        # Keep this separate from the explicit conclusion-coverage IDs.
        f['related_obligation_ids'] = sorted({oid for c in linked for oid in c['obligation_ids']} | set(f.get('obligation_ids', [])))
        placements = {c['report_placement'] for c in linked}
        if len(placements) != 1:
            problems.append('finding_mixes_report_placements'); continue
        expected = next(iter(placements))
        if 'report_placement' in f and f['report_placement'] != expected:
            problems.append('finding_placement_differs_from_case_review')
        f['report_placement'] = expected
    return content, problems
