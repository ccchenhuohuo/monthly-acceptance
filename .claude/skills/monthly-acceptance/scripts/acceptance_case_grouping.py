"""Maintained deterministic collection grouping; no business closure or routing.

Ported selectively from the frozen case-triage experiment. IDs and grouping
boundaries are intentionally stable; source validation uses the run APIs.
"""
from __future__ import annotations
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
import json
import math
import unicodedata
from acceptance_core import Run, canonical, digest as sha, read

PRIMARY = {'spus_change', 'units_change', 'amount_change', 'price_band_shift'}
METRICS = ('spus', 'units', 'amount')
METHOD_TYPES = {
    'known_method_defect_regression', 'known_interpretation_defect_regression',
    'provenance_and_comparability_guard', 'exposure_and_claim_boundary_guard',
    'coverage_execution_guard', 'report_contract_regression',
    'external_evidence_execution_guard', 'retired_workflow_not_data_issue',
    'retired_signal_regression', 'not_applicable_history',
    'historical_population_import_unfinished', 'classification_granularity_limitation',
    'scope_contract_blocker', 'internal_evidence_blocker',
}

def month_number(mm):
    return int(mm[:4]) * 12 + int(mm[5:7])

def consecutive_groups(months):
    result = []
    for mm in sorted(set(months)):
        if not result or month_number(mm) != month_number(result[-1][-1]) + 1:
            result.append([])
        result[-1].append(mm)
    return result

def close(a, b, metric):
    if a is None or b is None:
        return a is None and b is None
    tolerance = max(.01, abs(float(a)) * 1e-8) if metric == 'amount' else 1e-9
    return abs(float(a) - float(b)) <= tolerance

def new_case(kind, key, **fields):
    return {'case_id': 'case-' + sha([kind, key])[:24], 'kind': kind,
            'signal_ids': [], 'sample_ids': [], 'history_ids': [], 'original_paths': [],
            'root_cause_status': 'unverified', 'closure_eligible': False, **fields}

def build_cases(source):
    strict = {}
    alias_map = []
    gaps = defaultdict(list)
    for c in source.candidates:
        if c['kind'] != 'result_change':
            gaps[(c['kind'], c['site'], c['path'])].append(c)
            continue
        base = c['facts']['base_month']
        path = tuple(json.loads(c['path']))
        scope, chain = source.scope_for_pair(c['site'], path, base, c['month'])
        key = (c['site'], scope, base, c['month'])
        strict.setdefault(key, []).append(c)
        alias_map.append({'signal_id': c['candidate_id'], 'original_path': path,
                          'canonical_path': scope, 'proof': 'sole_child_equal_rows_metrics_and_bands_both_periods',
                          'alias_chain': chain})
    bundled = {}
    for (site, scope, base, current), members in strict.items():
        key = (site, scope, current)
        if key not in bundled:
            bundled[key] = new_case('result', key, site=site, path=scope, months=[current], comparisons=[],
                                     merge_basis='same_category_current_month_shared_collection; retain each comparison',
                                     cohort_identity_status='aggregate_scope_known; cross_month_entity_identity_not_proven')
        case = bundled[key]
        case['original_paths'].extend(json.loads(c['path']) for c in members)
        case['comparisons'].append({'base_month': base, 'current_month': current,
                                    'comparison': members[0]['comparison'],
                                    'signal_ids': sorted(c['candidate_id'] for c in members)})
        case['signal_ids'].extend(c['candidate_id'] for c in members)
    cases = list(bundled.values())
    for (kind, site, path), members in sorted(gaps.items()):
        for episode in consecutive_groups([c['month'] for c in members]):
            selected = [c for c in members if c['month'] in episode]
            case = new_case(kind, [site, path, episode], site=site, path=json.loads(path), months=episode,
                            merge_basis='same_exact_path_continuous_absence; each_month_retained',
                            cohort_identity_status='requires_seed_sku_trace')
            case['signal_ids'] = [c['candidate_id'] for c in selected]
            case['original_paths'] = [json.loads(path)]
            case['comparisons'] = []
            cases.append(case)
    # Stable semantic samples are batched by observed category, not closed.
    sample_cases = {}
    for sample in source.samples:
        scope = source.scope_for_month(sample['site'], tuple(json.loads(sample['path'])), sample['month'])
        key = sample['site'], scope, sample['month']
        sample_cases.setdefault(key, new_case('semantic', key, site=key[0], path=scope, months=[key[2]],
                                                merge_basis='same_category_semantic_batch; exact_samples_retained', comparisons=[]))
        sample_cases[key]['sample_ids'].append(sample['sample_id'])
        sample_cases[key]['original_paths'].append(json.loads(sample['path']))
    cases.extend(sample_cases.values())
    for case in cases:
        case['signal_ids'].sort()
        case['sample_ids'].sort()
        case['comparisons'].sort(key=lambda x: x['base_month'])
        case['original_paths'] = [json.loads(p) for p in sorted({canonical(p) for p in case['original_paths']})]
    return sorted(cases, key=lambda x: x['case_id']), alias_map, len(strict)

def history_scope(issue):
    detail = issue.get('legacy_detail') or {}
    months = set(issue.get('months') or []) | set(detail.get('months') or [])
    if issue.get('first_observed_month'):
        months.add(issue['first_observed_month'])
    entities = set()
    for obj in (issue, detail):
        entities.update(str(x) for key in ('product_ids', 'sku_ids') for x in obj.get(key, []))
        for key in obj.get('sku_keys', []):
            entities.update(str(key[k]) for k in ('product_id', 'sku_id') if key.get(k))
    return sorted(months), sorted(entities)

def history_links(cases, issues):
    by_key = defaultdict(list)
    for case in cases:
        for mm in case['months']:
            for original in [case['path'], *case['original_paths']]:
                path = tuple(original)
                by_key[(case['site'], path, mm)].append(case)
    output = []
    for issue in issues:
        path = tuple(json.loads(issue['path']))
        # Missing/compound history scope is a separate restoration job, not a
        # wildcard that promotes every country or every month to deep review.
        months, entities = history_scope(issue)
        method = issue.get('issue_type') in METHOD_TYPES
        linked = {case['case_id']: case for mm in months for case in by_key.get((issue['site'], path, mm), [])} if not method else {}
        linked = list(linked.values())
        for case in linked:
            case['history_ids'].append(issue['issue_id'])
        output.append({'issue_id': issue['issue_id'], 'issue_type': issue.get('issue_type'),
                       'linked_case_ids': sorted(c['case_id'] for c in linked),
                       'history_case_id': 'history-' + sha(issue['issue_id'])[:24],
                       'site': issue['site'], 'path': path, 'months': months, 'entity_ids': entities,
                       'source_issue': issue,
                       'route': 'method_or_contract_check' if method else ('protected_history_recheck' if months else 'restore_history_scope'),
                       'link_meaning': 'shared collection only; every exact historical question retains an independent obligation',
                       'identity_recheck_required': not method, 'business_closed': False})
    return output

def case_features(case, source, candidate_by_id):
    if case['kind'] != 'result':
        return {}
    site, path = case['site'], tuple(case['path'])
    primary, triggers, impact = set(), set(), defaultdict(float)
    observed_exposure, local_rate, band_pp = 0.0, 0.0, 0.0
    rollup = False
    min_spus = math.inf
    for c in (candidate_by_id[cid] for cid in case['signal_ids']):
        facts, cp = c['facts'], tuple(json.loads(c['path']))
        base, current = facts['base_month'], c['month']
        primary.update(set(facts['signals']) & PRIMARY)
        triggers.update(facts['signals'])
        if any(len(source.children.get((site, path, mm), set())) > 1 for mm in (base, current)):
            rollup = True
        benchmark_path = path[:1] if len(path) > 1 else ()
        parents = {side: source.categories.get((site, benchmark_path, mm)) for side, mm in [('base', base), ('current', current)]}
        if not all(parents.values()):
            raise ValueError('Missing materiality denominator')
        for metric in METRICS:
            values = facts['metrics'][metric]
            denominator = max(float(parents['base'][metric]), float(parents['current'][metric]), 1.)
            impact[metric + '_net'] = max(impact[metric + '_net'], abs(float(values['delta'])) / denominator)
            observed_exposure = max(observed_exposure, max(float(values['base']), float(values['current'])) / denominator)
            if values['rate'] is not None:
                local_rate = max(local_rate, abs(values['rate']))
            if metric in ('units', 'amount'):
                movement = facts['spu_movement'][metric]
                if movement['cancellation'] >= .7:
                    impact[metric + '_offset_gross'] = max(impact[metric + '_offset_gross'], movement['gross'] / denominator)
        for entry in facts.get('band_shifts', []):
            band_pp = max(band_pp, abs(entry['share_change_pp']))
            exposure = max(facts['metrics']['spus']['base'], facts['metrics']['spus']['current']) / max(parents['base']['spus'], parents['current']['spus'], 1)
            impact['band_weighted_shift'] = max(impact['band_weighted_shift'], abs(entry['share_change_pp']) / 100 * exposure)
        min_spus = min(min_spus, max(facts['metrics']['spus']['base'], facts['metrics']['spus']['current']))
    return {'primary_triggers': sorted(primary), 'all_triggers': sorted(triggers), 'is_rollup': rollup or len(path) == 0,
            'impact_scores': dict(impact), 'max_impact_score': max(impact.values(), default=0),
            'observed_exposure': observed_exposure, 'max_local_rate': local_rate, 'max_band_shift_pp': band_pp,
            'observed_spus': int(min_spus), 'denominator': 'same-country L1 (country for L1); max of compared periods',
            'meaning': 'routing salience, not defect probability or an upper bound on unobserved loss'}

def contract_crosswalk(source, cases):
    output = []
    for case in cases:
        if case['kind'] != 'contract_scope_missing':
            continue
        site, path = case['site'], tuple(case['path'])
        mechanism = 'unsupported'
        if site == 'JP' and len(path) == 1:
            mechanism = 'jp_width_normalization_candidate'
            matching = [(p, months) for (s, p), months in source.raw_paths.items() if s == site and
                        unicodedata.normalize('NFKC', p[0] or '') == unicodedata.normalize('NFKC', path[0])]
            depth = 1
        elif site == 'cn' and len(path) == 4:
            mechanism = 'cn_flattened_depth_candidate_requires_contract_review'
            proposed = path[:2] + path[-1:]
            matching = [(p, months) for (s, p), months in source.raw_paths.items() if s == site and p[:3] == proposed]
            depth = 3
        else:
            matching, depth = [], len(path)
        matched = sorted({mm for _, months in matching for mm in case['months'] if mm in months})
        prefixes = sorted({p[:depth] for p, _ in matching}, key=canonical)
        output.append({'case_id': case['case_id'], 'site': site, 'contract_path': path,
                       'signal_ids': case['signal_ids'], 'mechanism_candidate': mechanism,
                       'observed_prefixes': prefixes, 'covered_months': matched,
                       'missing_months': sorted(set(case['months']) - set(matched)),
                       'unique_observed_prefix': len(prefixes) == 1,
                       'raw_path_rows': len(matching), 'closure_eligible': False,
                       'proof_boundary': 'proves observed candidate prefix presence only; does not prove contract equivalence, leaf completeness, or factual sales'})
    return output

class Source:
    """Read complete archived or active scan evidence through existing Run APIs."""
    def __init__(self, run):
        self.run = run if hasattr(run, 'job_rows') else Run(run)
        self.path = self.run.path
        seal_path = self.path / 'sealed.json'
        self.seal = read(seal_path)['sha256'] if seal_path.exists() else None
        self.hashes = {}
        self.manifest = self.read('run.json')
        errors = self.run.verify_inputs() + self.run.verify_events()
        if errors:
            raise ValueError('Case source integrity: ' + '; '.join(errors))
        self.candidates = self.read('candidates.json')
        self.samples = self.read('samples.json')
        self.jobs = self.read('plan.json')['jobs']
        self.categories, self.bands = {}, defaultdict(dict)
        self.children, self.raw_paths = defaultdict(set), defaultdict(dict)
        self.movement, self.row_sources = {}, {}
        self.loaded_jobs = self.loaded_rows = 0
        for job in self.jobs:
            if job['family'] not in {'category', 'bands', 'raw_paths', 'movement'}:
                continue
            rows = self.rows(job)
            self.loaded_jobs += 1
            self.loaded_rows += len(rows)
            refs = self.run.job_record(job['job_id'])['evidence_ids']
            for row in rows:
                site, mm, path = job['site'], row['month_dt'][:7], tuple(json.loads(row['path']))
                key = (site, path, mm)
                self.row_sources[(job['family'], *key)] = refs
                if job['family'] == 'category':
                    if key in self.categories:
                        raise ValueError('Duplicate category population')
                    self.categories[key] = row
                    if path:
                        self.children[(site, path[:-1], mm)].add(path)
                elif job['family'] == 'bands':
                    band = int(row['band'])
                    if band in self.bands[key]:
                        raise ValueError('Duplicate price band population')
                    self.bands[key][band] = row
                elif job['family'] == 'raw_paths':
                    if mm in self.raw_paths[(site, path)]:
                        raise ValueError('Duplicate raw path population')
                    self.raw_paths[(site, path)][mm] = row
                else:
                    self.movement[(site, path, mm, job['lag'], job['entity'])] = row

    def read(self, relative):
        path = (self.path / relative).resolve()
        if not path.is_relative_to(self.path):
            raise ValueError('Source path escapes run')
        raw = path.read_bytes()
        hashed = sha(raw)
        if self.seal is not None and hashed != self.seal.get(str(relative)):
            raise ValueError('Archived source does not match seal: ' + str(relative))
        self.hashes[str(relative)] = hashed
        return json.loads(raw)

    def rows(self, job):
        record = self.read('records/job-' + job['job_id'] + '.json')
        if record['execution_status'] != 'success' or record['sql_hash'] != sha(job['sql']):
            raise ValueError('Incomplete or mismatched source job')
        # Reuse existing source/pagination validation, then retain the stronger
        # frozen plan key fields and server total checks required for aliases.
        rows = self.run.job_rows(job['job_id'])
        for eid in record['evidence_ids']:
            meta = self.read('records/' + eid + '.json')
            self.read(meta['path'])
            if self.hashes[meta['path']] != meta['sha256']:
                raise ValueError('Evidence metadata mismatch')
        if len(rows) != record['expected_rows'] or any(int(r['total_rows']) != len(rows) for r in rows):
            raise ValueError('Missing source pages')
        if len({canonical([r[k] for k in job['key_fields']]) for r in rows}) != len(rows):
            raise ValueError('Duplicate source page key')
        return rows

    @lru_cache(maxsize=None)
    def alias_parent(self, site, path, mm):
        if not path:
            return None
        parent = path[:-1]
        if self.children.get((site, parent, mm)) != {path}:
            return None
        a, b = self.categories.get((site, parent, mm)), self.categories.get((site, path, mm))
        required = (*METRICS, 'nrows', 'priced_spus', 'invalid_rows', 'missing_product_rows')
        if not a or not b or any(k not in a or k not in b for k in required):
            return None
        if any(a[k] or b[k] for k in ('invalid_rows', 'missing_product_rows')):
            return None
        if not all(close(a[k], b[k], k) for k in (*METRICS, 'nrows', 'priced_spus')):
            return None
        ab, bb = self.bands.get((site, parent, mm), {}), self.bands.get((site, path, mm), {})
        if (a['priced_spus'] or b['priced_spus']) and (not ab or not bb):
            return None
        if set(ab) != set(bb) or any(not all(close(ab[n][k], bb[n][k], k) for k in METRICS) for n in ab):
            return None
        return parent

    def scope_for_pair(self, site, path, base, current):
        chain = [path]
        while path:
            a, b = self.alias_parent(site, path, base), self.alias_parent(site, path, current)
            if a is None or a != b:
                break
            path = a
            chain.append(path)
        return path, chain

    def scope_for_month(self, site, path, mm):
        while path:
            parent = self.alias_parent(site, path, mm)
            if parent is None:
                break
            path = parent
        return path

    def alias_evidence(self, site, chain, base, current):
        """Bind the actual two-period rows, bands and unique-child evidence."""
        result = []
        for child, parent in zip(chain, chain[1:]):
            for mm in (base, current):
                if self.alias_parent(site, tuple(child), mm) != tuple(parent):
                    raise ValueError('Alias proof does not reproduce')
                evidence = set()
                for path in (tuple(child), tuple(parent)):
                    for family in ('category', 'bands'):
                        evidence.update(self.row_sources.get((family, site, path, mm), []))
                result.append({'site': site, 'month': mm, 'child': child, 'parent': parent,
                    'physical_population': {name: self.categories[(site, tuple(path), mm)] for name, path in [('child', child), ('parent', parent)]},
                    'price_bands': {name: self.bands[(site, tuple(path), mm)] for name, path in [('child', child), ('parent', parent)]},
                    'unique_child': sorted(self.children[(site, tuple(parent), mm)]),
                    'evidence_ids': sorted(evidence)})
        return result
