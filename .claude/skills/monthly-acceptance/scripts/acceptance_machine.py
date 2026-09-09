"""Complete shared observations, deterministic machine diagnosis and residuals.

The module never calls the database. Run collection_jobs first, then the complete
collection_trace_jobs list through acceptance_transport.execute_job. diagnose
replays the existing SQL/page/hash verifier, rebuilds denominators from those
observations, and reconciles them against the independent baseline jobs.

Machine decomposition does not establish a cause. Measured observations are not
risk upper bounds. Invalid identities and absent observations remain explicit.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from acceptance_core import Run, business_key, canonical, digest, month, months, quoted, read, readonly_sql, shift, write
from acceptance_diagnostics import ATTRIBUTES, IDENTITY_KEYS, RAW_FIELDS, TABLE_RE, traceable_sku
from acceptance_queries import path_expr, job as baseline_job
from acceptance_investigation_proof import _verified_rows

PROTOCOL = 'monthly-acceptance-machine/3.2'
METRICS = ('spus', 'bands', 'units', 'amount')
NOTE = 'Observed exposure is not a risk upper bound; decomposition does not establish causality.'


def _path(value):
    value = json.loads(value) if isinstance(value, str) else value
    if not isinstance(value, (list, tuple)) or any(x is not None and not isinstance(x, str) for x in value):
        raise ValueError('machine path must be a complete JSON array')
    return list(value)


def _identity(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError('machine evidence contains a non-finite/non-numeric value')
    return float(value)


def _normal_spec(manifest, spec):
    if not isinstance(spec, dict):
        raise ValueError('machine collection requires an explicit scope object')
    spec = {**spec.get('scope', {}), **spec}
    site = spec.get('site')
    if site not in manifest['sites']:
        raise ValueError('machine site is outside the frozen manifest')
    path = _path(spec.get('path', []))
    level = spec.get('level', len(path))
    if level == 6 and len(path) == 6:
        level = 'raw'
    if (level != 'raw' and (type(level) is not int or level not in (0, 1, 2, 3))) or len(path) != (6 if level == 'raw' else level):
        raise ValueError('machine path arity must match level; raw needs all six components')
    start, end = spec.get('window_start') or manifest['starts'][site], spec.get('window_end') or manifest['data_month']
    month(start); month(end)
    if not manifest['starts'][site] <= start <= end <= manifest['data_month']:
        raise ValueError('machine window is outside the frozen manifest')
    for key in ('std_table', 'raw_table'):
        if not TABLE_RE.fullmatch(manifest['policy'][key]):
            raise ValueError('machine SQL tables must be frozen three-part identifiers')
    members = spec.get('members', [])
    if not isinstance(members, list):
        raise ValueError('machine package members must be explicit')
    members = sorted(members, key=canonical)
    ids = [x.get('member_id') or x.get('obligation_id') or x.get('source_id') for x in members]
    if any(not _identity(x) for x in ids) or len(set(ids)) != len(ids):
        raise ValueError('machine package has missing or duplicate member IDs')
    for item in members:
        if item.get('site', site) != site:
            raise ValueError('machine member has a different country')
        mm = item.get('current_month') or item.get('month')
        if mm and not start <= mm <= end:
            raise ValueError('machine collection omits a member current month')
        comparison = item.get('comparison')
        base = item.get('base_month')
        if comparison in manifest['policy']['comparisons'] and mm:
            expected = shift(mm, -manifest['policy']['comparisons'][comparison])
            if base and base != expected:
                raise ValueError('machine comparison base is not its natural calendar month')
            if manifest['starts'][site] <= expected and not start <= expected <= end:
                raise ValueError('machine collection omits a member comparison baseline')
        elif base and not start <= base <= end:
            raise ValueError('machine collection omits a member baseline')
    scope = {'site': site, 'level': level, 'path': path, 'window_start': start, 'window_end': end}
    package_id = spec.get('package_id') or spec.get('collection_id') or 'collection-'+business_key(scope)
    if not isinstance(package_id, str) or not all(x.isalnum() or x in '-_' for x in package_id):
        raise ValueError('machine package_id must be a safe identifier')
    obligation_ids = sorted(set(spec.get('obligation_ids', []) + [x['obligation_id'] for x in members if x.get('obligation_id')]))
    return {**scope, 'package_id': package_id, 'members': members, 'obligation_ids': obligation_ids,
            'member_hash': spec.get('member_hash') or digest(members), 'computed_member_hash': digest(members),
            'case_ids': sorted(set(spec.get('case_ids', []))), 'source_hash': spec.get('source_hash')}


def _raw_expr():
    return 'CAST(JSON_ARRAY('+','.join(RAW_FIELDS)+') AS STRING)'


def _target_condition(spec, layer):
    if spec['level'] == 'raw':
        return _raw_expr()+'='+quoted(canonical(spec['path']))
    if layer == 'raw':
        return '1=0'
    return path_expr(spec['level'])+'='+quoted(canonical(spec['path']))


def _select(manifest, spec, layer, gate):
    attrs = ','.join(ATTRIBUTES)
    std = path_expr(3) if layer == 'std' else 'CAST(NULL AS STRING)'
    return f"""SELECT {quoted(layer)} AS source_layer,site,platform,month_dt,product_id,sku_id,
        {_raw_expr()} AS raw_path,{std} AS std_path,
        CASE WHEN {_target_condition(spec, layer)} THEN 1 ELSE 0 END AS in_target_path,
        {attrs},CAST(JSON_ARRAY({attrs}) AS STRING) AS attribute_key,
        COUNT(1) AS source_rows,SUM(discount_sales) AS amount,SUM(`count`) AS units,
        SUM(CASE WHEN discount_sales IS NULL THEN 1 ELSE 0 END) AS missing_amount_rows,
        SUM(CASE WHEN `count` IS NULL THEN 1 ELSE 0 END) AS missing_units_rows,
        SUM(CASE WHEN discount_sales IS NULL OR `count` IS NULL OR discount_sales<0 OR `count`<0 THEN 1 ELSE 0 END) AS invalid_value_rows
        FROM {manifest['policy'][layer+'_table']}
        WHERE site={quoted(spec['site'])} AND month_dt BETWEEN {quoted(spec['window_start']+'-01')} AND {quoted(spec['window_end']+'-01')}
        AND ({gate})
        GROUP BY site,platform,month_dt,product_id,sku_id,raw_path,std_path,in_target_path,{attrs}"""


def _month_partitions(manifest, spec, kind):
    """Deterministic contiguous months; an absent policy preserves old SQL."""
    policy = manifest['policy'].get('machine_diagnostics', {})
    span = policy.get('collection_month_span')
    if kind == 'identity_trace':
        span = policy.get('trace_month_span', span)
    if span is not None and (type(span) is not int or not 1 <= span <= 120):
        raise ValueError('frozen collection/trace_month_span must be between 1 and 120 months')
    entire = months(spec['window_start'], spec['window_end'])
    if tuple(map(int, manifest.get('method_version', '0.0').split('.')[:2])) >= (3, 3):
        span = 1  # Stable calendar units permit exact partial-window reuse.
    span = span or len(entire)
    return [(entire[offset], entire[min(offset+span, len(entire))-1])
            for offset in range(0, len(entire), span)]


def _partition_fields(spec, intervals, index, manifest=None):
    if manifest and tuple(map(int, manifest.get('method_version', '0.0').split('.')[:2])) >= (3, 3):
        return {}  # Requested outer windows do not change an identical SQL job.
    if len(intervals) == 1:
        return {}  # Preserve complete legacy job shape and exact SQL reuse.
    return {'collection_scope': {k: spec[k] for k in ('site', 'level', 'path', 'window_start', 'window_end')},
            'month_partition': {'algorithm': 'contiguous_calendar_months/v1',
                'index': index, 'count': len(intervals), 'start': intervals[index][0], 'end': intervals[index][1],
                'complete_window_hash': digest([spec['window_start'], spec['window_end']])}}


def collection_jobs(manifest, spec):
    """Return the complete target query (no sampling and no unbounded history join).

    An explicit window must cover every member's comparison. Full baseline jobs
    are reused at diagnosis; collection may never substitute an Agent denominator.
    """
    spec = _normal_spec(manifest, spec)
    layer = 'raw' if spec['level'] == 'raw' else 'std'
    intervals = _month_partitions(manifest, spec, 'target')
    jobs = []
    for index, (start, end) in enumerate(intervals):
        part = {**spec, 'window_start': start, 'window_end': end}
        sql = readonly_sql('WITH scoped AS ('+_select(manifest, part, layer, _target_condition(spec, layer))+
                           ') SELECT *,COUNT(1) OVER() AS total_rows FROM scoped')
        content = {key: part[key] for key in ('site', 'level', 'path', 'window_start', 'window_end')}
        jobs.append({'job_id': 'collection-'+business_key(content), 'family': 'machine_collection',
            'kind': 'target', **content, 'key_fields': list(IDENTITY_KEYS), 'sql': sql,
            'observation_scope': 'complete target path and explicit observation window',
            'cross_platform_ids_are_not_deduplicated': True, **_partition_fields(spec, intervals, index, manifest)})
    return jobs


def _verified(run, jobs, file_hashes, eids):
    rows = []
    for item in jobs:
        page_rows = _verified_rows(run, item, file_hashes, eids)
        if item.get('family') == 'machine_collection':
            record = run.job_record(item['job_id'])
            if not record.get('evidence_ids') or record.get('row_count') != len(page_rows):
                raise ValueError('machine partition has no verified page or inconsistent recorded row count')
        rows.extend(page_rows)
    return rows


def _trace_jobs(manifest, spec, target):
    # Parent and SKU are separate exact identities. Missing platform cannot trace;
    # null/blank/0 SKU can retain a valid same-platform parent, never another parent.
    identities = set()
    for row in target:
        if not _identity(row['platform']):
            continue
        if _identity(row['product_id']):
            identities.add((row['platform'], 'product_id', row['product_id']))
        if traceable_sku(row['sku_id']):
            identities.add((row['platform'], 'sku_id', row['sku_id']))
    size = manifest['policy'].get('machine_diagnostics', {}).get('identity_shard_size', 100)
    if type(size) is not int or not 1 <= size <= 250:
        raise ValueError('frozen identity_shard_size must be between 1 and 250')
    ordered = sorted(identities)
    jobs = []
    intervals = _month_partitions(manifest, spec, 'identity_trace')
    for offset in range(0, len(ordered), size):
        shard = ordered[offset:offset+size]
        grouped = defaultdict(list)
        for platform, field, value in shard:
            grouped[(platform, field)].append(value)
        gate = ' OR '.join('('+f'platform={quoted(platform)} AND {field} IN ('+','.join(map(quoted, values))+'))'
                           for (platform, field), values in sorted(grouped.items()))
        for index, (start, end) in enumerate(intervals):
            part = {**spec, 'window_start': start, 'window_end': end}
            sql = readonly_sql('WITH scoped AS ('+' UNION ALL '.join(_select(manifest, part, layer, gate)
                                                                     for layer in ('raw', 'std'))+
                               ') SELECT *,COUNT(1) OVER() AS total_rows FROM scoped')
            content = {key: part[key] for key in ('site', 'level', 'path', 'window_start', 'window_end')}
            jobs.append({'job_id': 'collection-trace-'+business_key(content, shard), 'family': 'machine_collection',
                'kind': 'identity_trace', **content, 'identity_shard': [list(x) for x in shard],
                **({'identity_population_hash': digest(ordered), 'identity_population_size': len(ordered)}
                   if tuple(map(int, manifest.get('method_version', '0.0').split('.')[:2])) < (3, 3) else {}),
                'key_fields': list(IDENTITY_KEYS), 'sql': sql, **_partition_fields(spec, intervals, index, manifest)})
    return jobs


def collection_trace_jobs(run, spec):
    """Derive *every* exact-identity shard from verified complete target pages."""
    spec = _normal_spec(run.manifest, spec)
    rows = _verified_targets(run, spec, collection_jobs(run.manifest, spec), {}, set())
    return _trace_jobs(run.manifest, spec, rows)


def _partition_coverage(spec, jobs, kind):
    """Each target/identity group must cover each frozen month exactly once."""
    expected = months(spec['window_start'], spec['window_end'])
    grouped = defaultdict(list)
    ids = set()
    for item in jobs:
        if item['job_id'] in ids or item.get('kind') != kind:
            raise ValueError('machine partition has a duplicate job or wrong kind')
        ids.add(item['job_id'])
        if any(item[k] != spec[k] for k in ('site', 'level', 'path')):
            raise ValueError('machine partition changes the full collection category/country')
        if not spec['window_start'] <= item['window_start'] <= item['window_end'] <= spec['window_end']:
            raise ValueError('machine partition lies outside its complete collection window')
        group = canonical(item['identity_shard']) if kind == 'identity_trace' else 'target'
        grouped[group].append(item)
    if kind == 'target' and not grouped:
        raise ValueError('machine target partition plan is empty')
    proofs = []
    for group, parts in sorted(grouped.items()):
        observed = Counter(mm for part in parts for mm in months(part['window_start'], part['window_end']))
        if observed != Counter(expected):
            raise ValueError('machine partition plan omits or overlaps required calendar months')
        proofs.append({'group_id': kind+'-'+business_key(group),
            'identity_shard': json.loads(group) if kind == 'identity_trace' else None,
            'covered_months': sorted(observed), 'each_month_once': True,
            'partitions': [{'job_id': p['job_id'], 'window_start': p['window_start'], 'window_end': p['window_end']}
                           for p in sorted(parts, key=lambda p: p['window_start'])]})
    return {'kind': kind, 'required_months': expected, 'group_count': len(grouped),
            'job_count': len(jobs), 'groups': proofs, 'complete': True}


def _verified_targets(run, spec, jobs, file_hashes, eids):
    _partition_coverage(spec, jobs, 'target')
    target, seen = [], set()
    layer = 'raw' if spec['level'] == 'raw' else 'std'
    for item in jobs:
        part = {**spec, 'window_start': item['window_start'], 'window_end': item['window_end']}
        for row in _verified(run, [item], file_hashes, eids):
            _valid_row(row, part)
            if row['source_layer'] != layer or row['in_target_path'] != 1:
                raise ValueError('target SQL returned unrelated rows')
            key = _row_key(row)
            if key in seen:
                raise ValueError('machine target rows repeat across month partitions')
            seen.add(key)
            target.append(row)
    return target


def _row_key(row):
    return canonical([row[key] for key in IDENTITY_KEYS])


def _content(row):
    return {k: v for k, v in row.items() if k != 'total_rows'}


def _entity_key(row):
    if _identity(row['product_id']):
        return canonical([row['site'], row['platform'], row['product_id']])
    # Missing parents cannot share an invented product identity. Their observed
    # amount/units survive; the row identity is only an accounting bucket.
    return canonical([row['site'], row['platform'], None, _row_key(row)])


def _valid_row(row, spec):
    if row.get('source_layer') not in ('raw', 'std') or row.get('site') != spec['site']:
        raise ValueError('machine row has an unrelated source/country')
    mm = row.get('month_dt', '')
    if not isinstance(mm, str) or mm != mm[:7]+'-01' or not spec['window_start'] <= mm[:7] <= spec['window_end']:
        raise ValueError('machine row has a non-calendar or out-of-scope month')
    month(mm[:7])
    if len(_path(row['raw_path'])) != 6 or (row['source_layer'] == 'std' and len(_path(row['std_path'])) != 3):
        raise ValueError('machine row loses raw/standard path identity')
    nr = _number(row.get('source_rows'))
    if nr is None or nr < 1 or int(nr) != nr:
        raise ValueError('machine source row count must be a positive integer')
    for key in ('missing_amount_rows', 'missing_units_rows', 'invalid_value_rows'):
        n = _number(row.get(key))
        if n is None or n != int(n) or not 0 <= n <= nr:
            raise ValueError('machine invalid/missing counts do not match source rows')
    for metric in ('units', 'amount'):
        value = _number(row.get(metric))
        missing = row['missing_'+metric+'_rows']
        if (value is None) != (missing == nr):
            raise ValueError('machine NULL sum and missing source values disagree')
    expected = (_path(row['raw_path']) == spec['path'] if spec['level'] == 'raw'
                else row['source_layer'] == 'std' and _path(row['std_path'])[:spec['level']] == spec['path'])
    if row.get('in_target_path') != int(expected):
        raise ValueError('machine target flag does not match complete path')


def _part(rows):
    result = {'rows': sum(int(r['source_rows']) for r in rows),
              'invalid_rows': sum(int(r['invalid_value_rows']) for r in rows)}
    for metric in ('units', 'amount'):
        values = [float(r[metric]) for r in rows if r[metric] is not None]
        missing = sum(int(r['missing_'+metric+'_rows']) for r in rows)
        result[metric] = sum(values) if values else (0.0 if not rows else None)
        result['missing_'+metric+'_rows'] = missing
        result[metric+'_known'] = missing == 0
    return result


def _combine_parts(parts):
    result = {'rows': sum(p['rows'] for p in parts), 'invalid_rows': sum(p['invalid_rows'] for p in parts)}
    for metric in ('units', 'amount'):
        known_values = [p[metric] for p in parts if p[metric] is not None]
        result[metric] = sum(known_values) if known_values else None
        result['missing_'+metric+'_rows'] = sum(p['missing_'+metric+'_rows'] for p in parts)
        result[metric+'_known'] = result['missing_'+metric+'_rows'] == 0
    return result


def _band(part, edges):
    if not part['rows']:
        return None
    if part['invalid_rows'] or not part['units_known'] or not part['amount_known'] or part['units'] <= 0 or part['amount'] <= 0:
        return -1
    return next((i for i, edge in enumerate(edges) if part['amount']/part['units'] < edge), len(edges))


def _close(actual, expected, metric, manifest, label):
    expected = _number(expected)
    if actual is None or expected is None:
        if actual is not expected:
            raise ValueError('machine denominator mismatch: '+label)
        return
    tolerance = max(manifest['policy']['amount_tolerance'], abs(expected)*1e-9) if metric == 'amount' else 1e-9
    if abs(actual-expected) > tolerance:
        raise ValueError(f'machine denominator mismatch: {label}; computed={actual}, baseline={expected}')


def _aggregate(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return {k: _part(v) for k, v in sorted(groups.items(), key=lambda x: canonical(x[0]))}


def _source_spu_key(row, raw=False):
    pid = row['product_id']
    return pid if raw else (pid.strip() or None) if isinstance(pid, str) else None


def _source_summary(rows, edges, raw=False):
    result = _part(rows)
    products = _aggregate(rows, lambda r: _source_spu_key(r, raw))
    result['spus'] = sum(key is not None for key in products)
    result['nrows'] = result['rows']
    if not raw:
        bands = {}
        for key, part in products.items():
            band = -1 if key is None else _band(part, edges)
            target = bands.setdefault(str(band), {'spus': 0, 'units': 0.0, 'amount': 0.0, 'null_units': 0, 'null_amount': 0})
            target['spus'] += key is not None
            for metric in ('units', 'amount'):
                if part[metric] is None:
                    target['null_'+metric] += 1
                else:
                    target[metric] += part[metric]
        for band, target in bands.items():
            members = sum((-1 if k is None else _band(p, edges)) == int(band) for k, p in products.items())
            for metric in ('units', 'amount'):
                if target['null_'+metric] == members:
                    target[metric] = None
        result['bands'] = bands
    return result


def _baseline(run, spec, file_hashes, eids):
    raw = spec['level'] == 'raw'
    j = baseline_job(run.manifest, 'raw_paths', spec['site']) if raw else baseline_job(run.manifest, 'category', spec['site'], level=spec['level'])
    rows = _verified(run, [j], file_hashes, eids)
    wanted = {r['month_dt'][:7]: r for r in rows if _path(r['path']) == spec['path']}
    bands = {}
    jobs = [j]
    if not raw:
        bj = baseline_job(run.manifest, 'bands', spec['site'], level=spec['level'])
        br = _verified(run, [bj], file_hashes, eids)
        for r in br:
            if _path(r['path']) == spec['path']:
                bands.setdefault(r['month_dt'][:7], {})[str(r['band'])] = r
        jobs.append(bj)
    return wanted, bands, jobs


def _reconcile(run, spec, target, file_hashes, eids, only_months=None):
    anchors, band_anchors, jobs = _baseline(run, spec, file_hashes, eids)
    by_month = defaultdict(list)
    for row in target:
        by_month[row['month_dt'][:7]].append(row)
    edges = run.policy['price_bands'][spec['site']]
    monthly = {}
    for mm in (only_months or months(spec['window_start'], spec['window_end'])):
        rows = by_month[mm]
        anchor = anchors.get(mm)
        if bool(rows) != bool(anchor):
            raise ValueError('machine target completeness disagrees with baseline month '+mm)
        if not rows:
            monthly[mm] = {'state': 'no_observation', 'market_values_are_zero': False}
            continue
        computed = _source_summary(rows, edges, spec['level'] == 'raw')
        for metric in ('nrows', 'spus', 'units', 'amount'):
            _close(computed[metric], anchor[metric], metric, run.manifest, mm+'.'+metric)
        if spec['level'] != 'raw':
            if set(computed['bands']) != set(band_anchors.get(mm, {})):
                raise ValueError('machine price band population differs from baseline: '+mm)
            for band, expected in band_anchors[mm].items():
                for metric in ('spus', 'units', 'amount'):
                    _close(computed['bands'][band][metric], expected[metric], metric, run.manifest, mm+'.band'+band+'.'+metric)
        products = _aggregate(rows, _entity_key)
        identity_complete = all(_identity(r['platform']) and _identity(r['product_id']) for r in rows)
        monthly[mm] = {'state': 'observed', **_part(rows), 'source_id_spus': computed['spus'],
                       'platform_scoped_spus': len(products) if identity_complete else None,
                       'platform_scoped_accounting_entities': len(products),
                       'spus': computed['spus'],
                       'identity_complete': identity_complete,
                       'bands': computed.get('bands'),
                       'platform_scoped_bands': dict(sorted(Counter(str(_band(p, edges)) for p in products.values()).items())),
                       'same_source_id_aggregation_does_not_prove_physical_identity': True}
    return monthly, anchors, jobs


def _mapping_observations(target, all_rows):
    flags = []
    sku_month = defaultdict(list); sku_history = defaultdict(list); parent_platforms = defaultdict(set)
    for row in all_rows:
        if _identity(row['product_id']):
            parent_platforms[(row['month_dt'], row['product_id'])].add(row['platform'])
        if _identity(row['platform']) and traceable_sku(row['sku_id']):
            sku_month[(row['site'], row['platform'], row['sku_id'], row['month_dt'])].append(row)
            sku_history[(row['site'], row['platform'], row['sku_id'])].append(row)
    for (mm, pid), platforms in sorted(parent_platforms.items(), key=lambda x: canonical(x[0])):
        if len(platforms) > 1:
            flags.append({'kind': 'cross_platform_numeric_id_overlap', 'month': mm[:7], 'product_id': pid,
                          'platforms': sorted(platforms, key=canonical), 'identity_equivalence_proven': False})
    for row in target:
        if not _identity(row['platform']) or not _identity(row['product_id']) or not traceable_sku(row['sku_id']):
            flags.append({'kind': 'untraceable_identity', 'entity_key': _entity_key(row), 'record_key': _row_key(row),
                          'same_parent_observation_is_not_sku_trace': True})
    for key, found in sorted(sku_month.items(), key=lambda x: canonical(x[0])):
        layers = {layer: [r for r in found if r['source_layer'] == layer] for layer in ('raw', 'std')}
        for layer, rows in layers.items():
            parents = {r['product_id'] for r in rows}; paths = {r['raw_path'] if layer == 'raw' else r['std_path'] for r in rows}
            if len(parents) > 1 or len(paths) > 1:
                flags.append({'kind': 'sku_multi_parent_or_path', 'identity': list(key), 'source_layer': layer,
                              'parents': sorted(parents, key=canonical), 'paths': sorted(paths), 'record_keys': sorted(map(_row_key, rows))})
        if not layers['raw'] or not layers['std']:
            flags.append({'kind': 'raw_std_unmatched_identity', 'identity': list(key),
                          'observed_layers': [k for k, v in layers.items() if v], 'record_keys': sorted(map(_row_key, found))})
        else:
            raw, std = _part(layers['raw']), _part(layers['std'])
            mismatch = [k for k in ('rows', 'units', 'amount', 'missing_amount_rows', 'missing_units_rows') if raw[k] != std[k]]
            if mismatch:
                flags.append({'kind': 'raw_std_values_or_multiplicity', 'identity': list(key), 'mismatched': mismatch,
                              'raw': raw, 'std': std, 'record_keys': sorted(map(_row_key, found))})
    for key, found in sorted(sku_history.items(), key=lambda x: canonical(x[0])):
        if len({r['product_id'] for r in found}) > 1:
            flags.append({'kind': 'parent_identity_change', 'identity': list(key),
                          'months': sorted({r['month_dt'][:7] for r in found}),
                          'parents': sorted({r['product_id'] for r in found}, key=canonical),
                          'mechanism_proven': False})
    return flags


def _comparison(spec, target, base, current, member_id, comparison, edges):
    base_rows = [r for r in target if r['month_dt'][:7] == base]
    current_rows = [r for r in target if r['month_dt'][:7] == current]
    if not base_rows or not current_rows:
        return {'member_id': member_id, 'kind': 'result_change', 'comparison': comparison,
                'base_month': base, 'current_month': current, 'state': 'missing_data',
                'reason': 'Missing target observations are not zero market sales.', 'complete': False,
                'residual': {m: {'state': 'unknown', 'note': NOTE} for m in METRICS}}
    grouped = {side: defaultdict(list) for side in ('base', 'current')}
    for side, rows in (('base', base_rows), ('current', current_rows)):
        for row in rows:
            grouped[side][_entity_key(row)].append(row)
    entities = []
    for key in sorted(set(grouped['base']) | set(grouped['current'])):
        br, cr = grouped['base'][key], grouped['current'][key]
        b, c = _part(br), _part(cr)
        representative = (cr or br)[0]
        state = 'entered' if not br else 'exited' if not cr else 'retained'
        identity_complete = all(_identity(r['platform']) and _identity(r['product_id']) and traceable_sku(r['sku_id']) for r in br+cr)
        entry = {'entity_key': key, 'site': spec['site'], 'platform': representative['platform'],
                 'product_id': representative['product_id'], 'observation_state': state,
                 'identity_complete': identity_complete, 'base': b, 'current': c,
                 'base_band': _band(b, edges), 'current_band': _band(c, edges),
                 'sku_keys': sorted({canonical([r['platform'], r['sku_id']]) for r in br+cr if traceable_sku(r['sku_id'])}),
                 'record_keys': sorted(map(_row_key, br+cr)),
                 'units_amount_are_observed_not_market_total': True}
        for metric in ('units', 'amount'):
            entry[metric+'_change'] = c[metric]-b[metric] if b[metric+'_known'] and c[metric+'_known'] else None
        entry['unit_price_base'] = b['amount']/b['units'] if entry['base_band'] not in (None, -1) else None
        entry['unit_price_current'] = c['amount']/c['units'] if entry['current_band'] not in (None, -1) else None
        if entry['unit_price_base'] is not None and entry['unit_price_current'] is not None:
            # One exact additive ordering; the interaction is allocated to price.
            entry['amount_quantity_component'] = (c['units']-b['units'])*entry['unit_price_base']
            entry['amount_price_component'] = c['units']*(entry['unit_price_current']-entry['unit_price_base'])
        else:
            entry['amount_quantity_component'] = entry['amount_price_component'] = None
        entities.append(entry)
    brands = []
    brand_groups = {side: _aggregate(rows, lambda r: (r.get('std_brand_name') or '').strip() or None)
                    for side, rows in (('base', base_rows), ('current', current_rows))}
    for brand in sorted(set(brand_groups['base']) | set(brand_groups['current']), key=canonical):
        b, c = (brand_groups[side].get(brand, _part([])) for side in ('base', 'current'))
        brands.append({'brand': brand, 'base': b, 'current': c,
                       **{m+'_change': c[m]-b[m] if b[m+'_known'] and c[m+'_known'] else None for m in ('units', 'amount')}})
    totals = {'base': _part(base_rows), 'current': _part(current_rows)}
    contributions = {}
    for metric in ('units', 'amount'):
        known = all(e[metric+'_change'] is not None for e in entities)
        changes = [e[metric+'_change'] for e in entities if e[metric+'_change'] is not None]
        pos, neg = sum(max(0, d) for d in changes), sum(min(0, d) for d in changes)
        gross = pos-neg
        contributions[metric] = {'state': 'measured' if known else 'unknown', 'positive': pos, 'negative': neg,
                                 'gross': gross, 'net': pos+neg, 'cancellation_ratio': 1-abs(pos+neg)/gross if gross else 0.0,
                                 'change_ratio': (totals['current'][metric]-totals['base'][metric])/totals['base'][metric]
                                 if known and totals['base'][metric] else None,
                                 'observed_sum_excludes_missing_values': not known}
    result = {'member_id': member_id, 'kind': 'result_change', 'comparison': comparison,
              'base_month': base, 'current_month': current, 'state': 'diagnosed', 'complete': True,
              'scope': {k: spec[k] for k in ('site', 'level', 'path')},
              'population_count': len(entities), 'entities': entities, 'brands': brands, 'totals': totals,
              'observed_movement': dict(Counter(e['observation_state'] for e in entities)),
              'contributions': contributions, 'cause_state': 'unexplained', 'diagnosis_proves_cause': False}
    result['residual'] = _residual(result, [], edges)
    return result


def _residual(member, claims, edges):
    entities = {e['entity_key']: e for e in member.get('entities', [])}
    if not entities or member.get('state') != 'diagnosed':
        return {m: {'state': 'unknown', 'note': NOTE} for m in METRICS}
    allocated = {m: defaultdict(float) for m in METRICS}
    for claim in claims:
        metric, key = claim['metric'], claim['entity_key']
        if metric not in METRICS or key not in entities:
            raise ValueError('cause contribution refers to an unrelated entity/metric')
        if metric in ('units', 'amount'):
            delta = entities[key][metric+'_change']
            if delta is None:
                raise ValueError('missing numerical values cannot receive an explained contribution')
            amount = _number(claim.get('contribution'))
            if amount is None or amount*delta < 0 or abs(amount) > abs(delta)+1e-9:
                raise ValueError('cause contribution exceeds or reverses its exact entity change')
            allocated[metric][key] += amount
            if abs(allocated[metric][key]) > abs(delta)+1e-9:
                raise ValueError('duplicate causes double-count the same contribution')
        else:
            if key in allocated[metric]:
                raise ValueError('duplicate entity membership explanation for '+metric)
            if claim.get('contribution') != 1:
                raise ValueError('SPU/band claims require exact entity membership, not an invented ratio')
            allocated[metric][key] = 1
    result = {}
    for metric in ('units', 'amount'):
        values = list(entities.values())
        if any(e[metric+'_change'] is None for e in values):
            result[metric] = {'state': 'unknown', 'reason': 'Missing source values prevent a complete contribution denominator.', 'note': NOTE}
            continue
        residual = {key: e[metric+'_change']-allocated[metric][key] for key, e in entities.items()}
        gross = sum(abs(e[metric+'_change']) for e in values)
        positive, negative = sum(max(0, d) for d in residual.values()), sum(min(0, d) for d in residual.values())
        unexplained = positive-negative
        result[metric] = {'state': 'measured', 'exposure': gross, 'unexplained': unexplained,
                          'remaining': unexplained, 'explained': gross-unexplained,
                          'remaining_positive': positive, 'remaining_negative': negative,
                          'remaining_by_entity': residual, 'denominator': gross,
                          'unexplained_ratio': unexplained/gross if gross else None,
                          'unit': 'units' if metric == 'units' else 'site currency', 'note': NOTE}
    parents_valid = all(_identity(e['platform']) and _identity(e['product_id']) for e in entities.values())
    pending_spu = sorted(set(entities)-set(allocated['spus']))
    formal_key = lambda e: e['product_id'].strip() if isinstance(e['product_id'], str) else None
    formal_ids = {formal_key(e) for e in entities.values() if formal_key(e)}
    pending_ids = {formal_key(entities[k]) for k in pending_spu if formal_key(entities[k])}
    result['spus'] = {'state': 'measured' if parents_valid else 'unknown', 'exposure': len(formal_ids) if parents_valid else None,
                       'unexplained': len(pending_ids) if parents_valid else None,
                       'remaining': len(pending_ids) if parents_valid else None,
                       'platform_entity_count': len(entities), 'pending_platform_entity_count': len(pending_spu),
                       'pending_entity_keys': pending_spu, 'unit': 'frozen distinct source product_id SPUs',
                       'physical_identity_equivalence_proven': False, 'note': NOTE}
    bands = []; pending_bands = set(entities)-set(allocated['bands'])
    valid_prices = True
    for side in ('base', 'current'):
        products = defaultdict(list)
        for e in entities.values():
            if e[side]['rows']:
                products[formal_key(e)].append(e)
        counts, residual_counts = Counter(), Counter()
        for pid, members in products.items():
            combined = _combine_parts([e[side] for e in members])
            band = -1 if not pid else _band(combined, edges)
            valid_prices &= band != -1
            if pid:
                counts[band] += 1
                if any(e['entity_key'] in pending_bands for e in members):
                    residual_counts[band] += 1
        denominator = sum(counts.values())
        for band, n in sorted(counts.items()):
            bands.append({'side': side, 'band': band, 'spus': n, 'pending_spus': residual_counts[band],
                          'denominator': denominator, 'share_pp': 100*n/denominator,
                          'remaining_pp': 100*residual_counts[band]/denominator})
    band_known = parents_valid and valid_prices
    result['bands'] = {'state': 'measured' if band_known else 'unknown',
                       'exposure': max((b['share_pp'] for b in bands), default=0) if band_known else None,
                       'unexplained': max((b['remaining_pp'] for b in bands), default=0) if band_known else None,
                       'remaining': max((b['remaining_pp'] for b in bands), default=0) if band_known else None,
                       'band_population': bands, 'pending_entity_keys': sorted(pending_bands), 'unit': 'percentage points',
                       'basis': 'All platform observations summed per frozen source product_id before SPU pricing; no physical equivalence inferred.',
                       'reason': None if band_known else 'Missing identities or invalid prices leave band effect unknown; band -1 remains visible.', 'note': NOTE}
    return result


def _gap(run, spec, member, target, context, anchors, edges):
    member_id = member.get('member_id') or member.get('obligation_id') or member['source_id']
    explicit_gap_months = member.get('gap_months')
    gap_months = explicit_gap_months or [member.get('month') or member.get('current_month')]
    gap_months = sorted(set(gap_months))
    for mm in gap_months:
        month(mm)
    first = min(gap_months)
    before = [mm for mm in anchors if mm < first]
    result = {'member_id': member_id, 'kind': member['kind'], 'gap_months': gap_months,
              'cause_state': 'unexplained', 'diagnosis_proves_cause': False}
    if not before:
        return {**result, 'state': 'missing_pre_gap_baseline', 'complete': False,
                'residual': {m: {'state': 'unknown', 'reason': 'No observed month before the gap; future recovery cannot replace a baseline.', 'note': NOTE} for m in METRICS}}
    baseline = max(before)
    if member.get('base_month') and member['base_month'] != baseline:
        raise ValueError('gap baseline must be the nearest observed month strictly before the gap')
    if not spec['window_start'] <= baseline:
        raise ValueError('gap collection does not include the complete pre-gap baseline')
    between = months(shift(baseline, 1), max(gap_months))
    if any(mm in anchors for mm in between):
        raise ValueError('continuous gap contains an observed recovery; split the case before diagnosis')
    if explicit_gap_months and gap_months != between:
        raise ValueError('gap skips one or more intermediate missing calendar months')
    gap_months = between
    result['gap_months'] = gap_months
    seeds = [r for r in target if r['month_dt'][:7] == baseline]
    if not seeds:
        raise ValueError('gap baseline evidence is incomplete')
    destination = defaultdict(list)
    for row in context:
        if _identity(row['platform']) and traceable_sku(row['sku_id']):
            destination[(row['platform'], row['sku_id'], row['month_dt'][:7], row['source_layer'])].append(row)
    cells = []
    for seed in sorted(seeds, key=_row_key):
        traceable = _identity(seed['platform']) and traceable_sku(seed['sku_id'])
        for mm in gap_months:
            for layer in ('raw', 'std'):
                found = destination.get((seed['platform'], seed['sku_id'], mm, layer), []) if traceable else []
                cells.append({'seed_key': _row_key(seed), 'entity_key': _entity_key(seed),
                              'month': mm, 'source_layer': layer,
                              'state': 'untraceable_identity' if not traceable else 'observed_elsewhere' if found else 'no_observation',
                              'record_keys': sorted(map(_row_key, found)),
                              'destination_parent_ids': sorted({r['product_id'] for r in found}, key=canonical)})
    population = _aggregate(seeds, _entity_key)
    parts = _part(seeds)
    parents_valid = all(_identity(r['platform']) and _identity(r['product_id']) for r in seeds)
    formal_population = _aggregate(seeds, lambda r: _source_spu_key(r, spec['level'] == 'raw'))
    formal_count = sum(k is not None for k in formal_population)
    counts = Counter(-1 if k is None else _band(p, edges) for k, p in formal_population.items())
    known_bands = parents_valid and -1 not in counts
    residual = {
        'spus': {'state': 'measured' if parents_valid else 'unknown',
                 'exposure': formal_count if parents_valid else None,
                 'unexplained': formal_count if parents_valid else None,
                 'platform_entity_count': len(population),
                 'pending_entity_keys': sorted(population), 'unit': 'pre-gap frozen source product_id SPUs', 'note': NOTE},
        'bands': {'state': 'measured' if known_bands else 'unknown',
                  'exposure': max((100*n/formal_count for n in counts.values()), default=0) if known_bands else None,
                  'unexplained': max((100*n/formal_count for n in counts.values()), default=0) if known_bands else None,
                  'band_population': [{'band': k, 'spus': n, 'denominator': formal_count} for k, n in sorted(counts.items())],
                  'unit': 'pre-gap percentage points', 'note': NOTE},
    }
    for metric in ('units', 'amount'):
        known = parts[metric+'_known']
        residual[metric] = {'state': 'measured' if known else 'unknown',
                            'exposure': parts[metric] if known else None, 'unexplained': parts[metric] if known else None,
                            'remaining_positive': parts[metric] if known else None, 'remaining_negative': 0 if known else None,
                            'basis': 'Complete pre-gap observed exposure, counted once per member; not missing-month sales.',
                            'unit': 'units' if metric == 'units' else 'site currency', 'note': NOTE}
    return {**result, 'state': 'diagnosed', 'complete': True, 'base_month': baseline,
            'population_count': len(population), 'source_seed_count': len(seeds), 'destination_cells': cells,
            'expected_destination_cells': len(seeds)*len(gap_months)*2, 'baseline_totals': parts,
            'baseline_population': [{'entity_key': k, **v} for k, v in population.items()],
            'future_recovery_not_used_as_baseline': True, 'residual': residual}


def _validate_claims(run, diagnosis, claims):
    if not isinstance(claims, list):
        raise ValueError('cause contributions must be an explicit list')
    members = {m['member_id']: m for m in diagnosis['members']}
    seen = set(); checked = []
    for claim in claims:
        if not isinstance(claim, dict) or not _identity(claim.get('cause_id')):
            raise ValueError('cause contribution needs an exact cause_id')
        member = members.get(claim.get('member_id'))
        if member is None or member.get('kind') != 'result_change':
            raise ValueError('cause contribution has an unrelated member')
        entities = {e['entity_key']: e for e in member.get('entities', [])}
        entity = entities.get(claim.get('entity_key'))
        if entity is None or claim.get('metric') not in METRICS:
            raise ValueError('cause contribution has an unrelated entity or metric')
        if 'denominator' in claim:
            metric = claim['metric']
            expected = member['residual'][metric].get('denominator', member['residual'][metric].get('exposure'))
            _close(_number(claim['denominator']), expected, metric, run.manifest, 'cause self-reported denominator')
        token = (claim['cause_id'], claim['member_id'], claim['entity_key'], claim['metric'])
        if token in seen:
            raise ValueError('duplicate cause contribution')
        seen.add(token)
        bindings = claim.get('evidence_bindings')
        if not isinstance(bindings, list) or not bindings:
            raise ValueError('cause needs original evidence bound to the exact object, not an existing unrelated file')
        matched = set()
        for binding in bindings:
            eid = binding.get('evidence_id')
            if eid not in diagnosis['evidence_ids']:
                raise ValueError('cause cites evidence outside the verified machine collection')
            value = run.get_evidence(eid)
            rows = value.get('response', {}).get('data', [])
            keys = {_row_key(r): r for r in rows if all(k in r for k in IDENTITY_KEYS)}
            requested = binding.get('record_keys')
            if not isinstance(requested, list) or not requested or len(set(requested)) != len(requested):
                raise ValueError('cause evidence must select exact nonduplicate original rows')
            for key in requested:
                if key not in keys or key not in entity['record_keys']:
                    raise ValueError('cause evidence exists but is unrelated to the claimed entity/window')
            matched.update(requested)
        if not set(entity['record_keys']) <= matched:
            raise ValueError('cause proof omits part of its exact two-period entity population')
        if not _identity(claim.get('mechanism')) or not _identity(claim.get('counterevidence')):
            raise ValueError('cause proposal needs mechanism and counterevidence statements')
        # Decomposition/identity observations can support a proposal, but this
        # receipt is never an independent business approval of that mechanism.
        checked.append({**claim, 'cause_acceptance_state': 'pending_business_review'})
    for member_id, member in members.items():
        selected = [c for c in checked if c['member_id'] == member_id]
        if selected:
            _residual(member, selected, diagnosis['price_bands'])
    return checked


def residual_impact(diagnosis, claims=None):
    """Recompute exact-object proposal residuals; this is not cause approval.

    The persisted diagnose path validates original evidence bindings first.
    Callers using this pure function must not present its result as accepted
    explanatory coverage. Numeric denominators are never read from claims.
    """
    claims = claims or []
    result = {}
    for member in diagnosis['members']:
        selected = [c for c in claims if c.get('member_id') == member['member_id']]
        result[member['member_id']] = _residual(member, selected, diagnosis['price_bands']) if member['kind'] == 'result_change' else member['residual']
    known = {m['member_id'] for m in diagnosis['members']}
    if any(c.get('member_id') not in known for c in claims):
        raise ValueError('cause proposal has an unrelated member')
    return result


def _risk_bound(diagnosis, target, context, requested, manifest):
    """An intentionally narrow, auditable finite error model.

    Only duplicate copies relative to complete raw source observations can be
    bounded here. The bound says nothing about omitted real-world transactions,
    raw collection truth or semantic correctness. Merely supplying a number or
    an "exhaustive" statement cannot create a bound.
    """
    result = {'state': 'unbounded', 'risk_bounded': False,
              'reason': 'No complete, validated finite error domain; observed exposure cannot substitute for a risk upper bound.'}
    if requested is None:
        return result
    model = requested.get('model') if isinstance(requested, dict) else None
    allowed = manifest['policy'].get('machine_diagnostics', {}).get('allowed_risk_models', [])
    if model != 'recorded_duplicate_deletion_only' or model not in allowed:
        return {**result, 'reason': 'Requested risk model is unsupported or absent from frozen policy.'}
    if diagnosis['scope']['level'] == 'raw' or not diagnosis['complete']:
        return {**result, 'reason': 'Duplicate correction needs a complete standard-layer machine collection.'}
    bad = [f for f in diagnosis['conflicts'] if f['kind'] != 'raw_std_values_or_multiplicity']
    if bad or any(not _identity(r['platform']) or not _identity(r['product_id']) or not traceable_sku(r['sku_id']) or r['invalid_value_rows'] for r in target):
        return {**result, 'reason': 'Identity, mapping ambiguity or invalid values prevents a closed correction domain.'}
    signature = lambda r: canonical([r[k] for k in ('site', 'platform', 'month_dt', 'product_id', 'sku_id', 'raw_path', 'attribute_key')])
    raws = defaultdict(list)
    for row in context:
        if row['source_layer'] == 'raw':
            raws[signature(row)].append(row)
    corrected = []; extras = []
    for row in target:
        matches = raws.get(signature(row), [])
        if len(matches) != 1:
            return {**result, 'reason': 'Every target record needs one exact raw counterpart including attributes.'}
        raw = matches[0]
        factor = row['source_rows']/raw['source_rows']
        if factor < 1 or factor != int(factor) or raw['invalid_value_rows']:
            return {**result, 'reason': 'Observed multiplicity is not an exact finite set of recorded duplicates.'}
        for metric in ('amount', 'units'):
            if row[metric] is None or raw[metric] is None or not math.isclose(row[metric], factor*raw[metric], rel_tol=1e-9, abs_tol=manifest['policy']['amount_tolerance'] if metric == 'amount' else 1e-9):
                return {**result, 'reason': 'Amounts/units do not conserve by exact raw duplicate multiplicity.'}
        corrected.append({**row, **{k: raw[k] for k in ('source_rows', 'amount', 'units', 'invalid_value_rows', 'missing_amount_rows', 'missing_units_rows')}})
        if factor > 1:
            extras.append({'record_key': _row_key(row), 'entity_key': _entity_key(row), 'month': row['month_dt'][:7],
                           'units': row['units']-raw['units'], 'amount': row['amount']-raw['amount'],
                           'recorded_extra_copies': row['source_rows']-raw['source_rows']})
    if not extras:
        return {**result, 'reason': 'No observed duplicate domain; this model cannot bound other remaining uncertainty.'}
    monthly = []
    for mm in months(diagnosis['scope']['window_start'], diagnosis['scope']['window_end']):
        observed = _aggregate([r for r in target if r['month_dt'][:7] == mm], _entity_key)
        fixed = _aggregate([r for r in corrected if r['month_dt'][:7] == mm], _entity_key)
        affected = {x['entity_key'] for x in extras if x['month'] == mm}
        # An arbitrary partial deletion may move any affected parent's band;
        # bound its full share, not just endpoint-band difference.
        monthly.append({'month': mm, 'spus_upper_bound': 0,
                        'bands_upper_bound_pp': 100*len(affected)/len(observed) if observed else 0,
                        'units_upper_bound': sum(x['units'] for x in extras if x['month'] == mm),
                        'amount_upper_bound': sum(x['amount'] for x in extras if x['month'] == mm),
                        'observed_bands': dict(Counter(str(_band(p, diagnosis['price_bands'])) for p in observed.values())),
                        'fully_corrected_bands': dict(Counter(str(_band(p, diagnosis['price_bands'])) for p in fixed.values()))})
    return {'state': 'bounded_for_stated_model_only', 'risk_bounded': False, 'model_bound_proven': True,
            'model': model, 'model_policy_hash': manifest.get('policy_hash', digest(manifest['policy'])),
            'scope': 'Deletion of observed exact duplicate copies relative to raw; other unreviewed causes remain unbounded.',
            'covers_all_remaining_uncertainty': False, 'monthly': monthly, 'affected_records': extras,
            'evidence_ids': diagnosis['evidence_ids'],
            'note': 'A model-specific arithmetic bound does not grant low-impact business retention without a reviewed claim that this is the entire uncertainty domain.'}


def _compute(run, spec, claims=None, risk_bounds=None):
    spec = _normal_spec(run.manifest, spec)
    files, eids = {}, set()
    target_jobs = collection_jobs(run.manifest, spec)
    target = _verified_targets(run, spec, target_jobs, files, eids)
    trace_jobs = _trace_jobs(run.manifest, spec, target)
    partition_coverage = {'target': _partition_coverage(spec, target_jobs, 'target'),
                          'identity_trace': _partition_coverage(spec, trace_jobs, 'identity_trace')}
    union = {_row_key(r): r for r in target}
    for item in trace_jobs:
        rows = _verified(run, [item], files, eids)
        part = {**spec, 'window_start': item['window_start'], 'window_end': item['window_end']}
        for row in rows:
            _valid_row(row, part)
            if not any(row['platform'] == platform and row[field] == value for platform, field, value in item['identity_shard']):
                raise ValueError('identity shard returned unrelated rows')
            key = _row_key(row)
            if key in union and _content(union[key]) != _content(row):
                raise ValueError('shared identity evidence changed across complete shards')
            union[key] = row
        observed_keys = {_row_key(r) for r in rows}
        expected_keys = {_row_key(r) for r in target
                         if item['window_start'] <= r['month_dt'][:7] <= item['window_end']
                         and any(r['platform'] == platform and r[field] == value for platform, field, value in item['identity_shard'])}
        if not expected_keys <= observed_keys:
            raise ValueError('identity shard omits original target records')
    context = [union[k] for k in sorted(union)]
    monthly, anchors, base_jobs = _reconcile(run, spec, target, files, eids)
    members = spec['members']
    if not members:
        members = []
        for mm in months(spec['window_start'], spec['window_end']):
            for comparison, lag in run.policy['comparisons'].items():
                members.append({'member_id': 'window-'+business_key(spec['package_id'], mm, comparison),
                                'kind': 'result_change', 'month': mm, 'comparison': comparison,
                                'base_month': shift(mm, -lag)})
    results = []; edges = run.policy['price_bands'][spec['site']]
    for member in members:
        mid = member.get('member_id') or member.get('obligation_id') or member['source_id']
        kind = member.get('kind', 'result_change')
        mm = member.get('current_month') or member.get('month')
        if kind in ('raw_presence_gap', 'std_presence_gap'):
            member_path = _path(member.get('path', spec['path']))
            gap_specification, gap_target, gap_anchors = spec, target, anchors
            if member_path != spec['path']:
                if spec['level'] == 'raw' or member_path[:len(spec['path'])] != spec['path']:
                    raise ValueError('gap member requires its exact full source path, not an unproved alias')
                gap_specification = {**spec, 'path': member_path, 'level': len(member_path)}
                gap_target = [r for r in target if _path(r['std_path'])[:len(member_path)] == member_path]
                _, gap_anchors, _ = _reconcile(run, gap_specification, gap_target, files, eids)
            results.append(_gap(run, gap_specification, member, gap_target, context, gap_anchors, edges)); continue
        if kind != 'result_change':
            results.append({'member_id': mid, 'kind': kind, 'state': 'semantic_or_specialist_review_required',
                            'complete': False, 'cause_state': 'unexplained',
                            'residual': {m: {'state': 'unknown', 'note': NOTE} for m in METRICS}}); continue
        comparison = member.get('comparison')
        if comparison not in run.policy['comparisons'] or not mm:
            raise ValueError('result member lacks a frozen comparison/current month')
        base = shift(mm, -run.policy['comparisons'][comparison])
        if base < run.manifest['starts'][spec['site']]:
            results.append({'member_id': mid, 'kind': kind, 'comparison': comparison, 'base_month': base, 'current_month': mm,
                            'state': 'not_applicable', 'complete': True,
                            'reason': 'Natural comparison baseline precedes the frozen provider coverage start.',
                            'residual': {m: {'state': 'unknown', 'applicable': False, 'note': NOTE} for m in METRICS}}); continue
        if base < spec['window_start']:
            results.append({'member_id': mid, 'kind': kind, 'comparison': comparison, 'base_month': base, 'current_month': mm,
                            'state': 'outside_collection_window', 'complete': not bool(spec['members']),
                            'reason': 'Not collected in this explicit shared window; no comparison conclusion.',
                            'residual': {m: {'state': 'unknown', 'applicable': False, 'note': NOTE} for m in METRICS}}); continue
        member_path = _path(member.get('path', spec['path']))
        member_spec, member_target = spec, target
        if member_path != spec['path']:
            if spec['level'] == 'raw':
                raise ValueError('raw member path is not covered by its shared collection')
            member_spec = {**spec, 'path': member_path, 'level': len(member_path)}
            if member_path[:len(spec['path'])] == spec['path']:
                member_target = [r for r in target if _path(r['std_path'])[:len(member_path)] == member_path]
            elif spec['path'][:len(member_path)] != member_path:
                raise ValueError('member path is not covered by its shared collection')
            # Child evidence may cover a parent only where each exact member
            # comparison reconciles. Do not extrapolate equivalence to history.
            _reconcile(run, member_spec, member_target, files, eids, only_months=[base, mm])
        result = _comparison(member_spec, member_target, base, mm, mid, comparison, edges)
        result['obligation_id'] = member.get('obligation_id')
        result['source_hash'] = member.get('source_hash')
        results.append(result)
    conflicts = _mapping_observations(target, context)
    scope = {k: spec[k] for k in ('site', 'level', 'path', 'window_start', 'window_end')}
    diagnosis = {'protocol': PROTOCOL, 'package_id': spec['package_id'], 'scope': scope,
                 'spec': spec, 'member_hash': spec['member_hash'], 'computed_member_hash': spec['computed_member_hash'],
                 'obligation_ids': spec['obligation_ids'], 'case_ids': spec['case_ids'],
                 'collection_complete': True, 'complete': all(r['complete'] for r in results),
                 'members': results, 'monthly': monthly, 'price_bands': edges,
                 'conflicts': conflicts, 'new_mechanisms': [],
                 'target_row_count': len(target), 'source_row_count': sum(r['source_rows'] for r in target),
                 'trace_unique_row_count': len(context),
                 'identity_shard_count': partition_coverage['identity_trace']['group_count'],
                 'target_job_count': len(target_jobs), 'trace_job_count': len(trace_jobs),
                 'partition_coverage': partition_coverage,
                 'job_ids': [j['job_id'] for j in target_jobs+trace_jobs+base_jobs],
                 'evidence_ids': sorted(eids), 'evidence_refs': sorted(eids),
                 'source_files_sha256': dict(sorted(files.items())), 'source_rows_sha256': digest([_content(r) for r in sorted(target, key=_row_key)]),
                 'states': {'execution': 'success', 'substantive_investigation': 'pending', 'data_problem': 'undetermined',
                            'cause_explanation': 'unexplained', 'impact_quantification': 'machine_observations',
                            'main_review': 'pending', 'business_acceptance': 'undetermined'},
                 'note': NOTE}
    checked = _validate_claims(run, diagnosis, claims or [])
    diagnosis['cause_contribution_proposals'] = checked
    diagnosis['residual_by_member'] = residual_impact(diagnosis)
    diagnosis['proposed_residual_by_member'] = residual_impact(diagnosis, checked)
    # A package can contain incompatible comparisons; summing them would double
    # count. Keep per-member exposure, and use cumulative_impact for same-scope
    # exact cohorts instead of inventing one overall explanatory percentage.
    diagnosis['residual'] = {m: {'state': 'unknown', 'basis': 'Per-member exact comparisons retained; package totals are not additive.',
                                 'member_values': {k: v[m] for k, v in diagnosis['residual_by_member'].items()}, 'note': NOTE} for m in METRICS}
    diagnosis['risk_bound'] = _risk_bound(diagnosis, target, context, risk_bounds, run.manifest)
    diagnosis['risk_bounded'] = diagnosis['risk_bound']['risk_bounded']
    diagnosis['requested_risk_bound'] = risk_bounds
    diagnosis['diagnosis_id'] = 'diagnosis-'+business_key(spec, diagnosis['source_rows_sha256'], diagnosis['source_files_sha256'], checked, risk_bounds)
    for relative, sha in files.items():
        if digest((run.path/relative).read_bytes()) != sha:
            raise ValueError('machine source changed during diagnosis: '+relative)
    return diagnosis


def _brief(diagnosis, run):
    root = run.path/'machine'/'packages'/diagnosis['package_id']
    return {'diagnosis_id': diagnosis['diagnosis_id'], 'package_id': diagnosis['package_id'],
            'obligation_ids': diagnosis['obligation_ids'], 'member_hash': diagnosis['member_hash'],
            'complete': diagnosis['complete'], 'collection_complete': diagnosis['collection_complete'],
            'member_count': len(diagnosis['members']),
            'members_diagnosed': sum(x['state'] == 'diagnosed' for x in diagnosis['members']),
            'target_row_count': diagnosis['target_row_count'], 'identity_shard_count': diagnosis['identity_shard_count'],
            'target_job_count': diagnosis['target_job_count'], 'trace_job_count': diagnosis['trace_job_count'],
            'required_month_count': len(diagnosis['partition_coverage']['target']['required_months']),
            'conflict_count': len(diagnosis['conflicts']), 'risk_bounded': diagnosis['risk_bounded'],
            'index_path': str(root/'index.json'), 'diagnosis_path': str(root/'diagnosis.json'),
            'sha256': digest(diagnosis), 'note': NOTE}


def diagnose(run, spec, claims=None, risk_bounds=None, *, persist=True):
    """Verify and diagnose a shared package; return a compact evidence index.

    Raw source rows stay in their hashed query pages. Analysts get all-member
    contribution files and suspicious-object indexes without repeated raw dumps.
    """
    run = Run(run) if not hasattr(run, 'manifest') else run
    diagnosis = _compute(run, spec, claims, risk_bounds)
    if persist:
        run.writable()
        root = run.path/'machine'/'packages'/diagnosis['package_id']
        previous = read(root/'diagnosis.json') if (root/'diagnosis.json').exists() else None
        if previous and previous['diagnosis_id'] != diagnosis['diagnosis_id']:
            write(root/'revisions'/(previous['diagnosis_id']+'.json'), previous)
        write(root/'diagnosis.json', diagnosis)
        index = {'diagnosis_id': diagnosis['diagnosis_id'], 'diagnosis_sha256': digest(diagnosis),
                 'members': [{'member_id': m['member_id'], 'state': m['state'], 'complete': m['complete'],
                              'population_count': m.get('population_count'),
                              'path': 'members/'+business_key(m['member_id'])+'.json'} for m in diagnosis['members']],
                 'conflict_counts': dict(Counter(x['kind'] for x in diagnosis['conflicts'])),
                 'conflicts_path': 'conflicts.json', 'source_files_sha256': diagnosis['source_files_sha256'],
                 'note': 'Every member is present; sorting and pagination for display do not reduce the machine population.'}
        write(root/'index.json', index)
        write(root/'conflicts.json', diagnosis['conflicts'])
        for member in diagnosis['members']:
            write(root/'members'/(business_key(member['member_id'])+'.json'), member)
        run.event('machine.diagnosed', package_id=diagnosis['package_id'], diagnosis_id=diagnosis['diagnosis_id'],
                  diagnosis_sha256=digest(diagnosis), complete=diagnosis['complete'], member_count=len(diagnosis['members']))
    return _brief(diagnosis, run)


def verified_diagnostics(run):
    """Recompute all persisted receipts; altered evidence/denominators are rejected."""
    run = Run(run) if not hasattr(run, 'manifest') else run
    results = []
    for path in sorted((run.path/'machine'/'packages').glob('*/diagnosis.json')):
        saved = read(path)
        recomputed = _compute(run, saved['spec'], saved.get('cause_contribution_proposals', []), saved.get('requested_risk_bound'))
        if saved != recomputed:
            raise ValueError('machine diagnosis changed or no longer reproducible: '+str(path))
        index = read(path.parent/'index.json')
        if index.get('diagnosis_sha256') != digest(saved) or index.get('diagnosis_id') != saved['diagnosis_id']:
            raise ValueError('machine evidence index no longer matches the diagnosis')
        for member in saved['members']:
            if read(path.parent/'members'/(business_key(member['member_id'])+'.json')) != member:
                raise ValueError('machine member evidence index changed')
        if read(path.parent/'conflicts.json') != saved['conflicts']:
            raise ValueError('machine conflict index changed')
        results.append(saved)
    return results


def audit(run):
    run = Run(run) if not hasattr(run, 'manifest') else run
    errors = []
    try:
        values = verified_diagnostics(run)
    except Exception as exc:
        errors.append(str(exc)); values = []
    return {'protocol': PROTOCOL, 'errors': errors, 'valid': not errors,
            'package_count': len(values), 'diagnosed_packages': sum(v['complete'] for v in values),
            'obligation_count': len({x for v in values for x in v['obligation_ids']}),
            'member_count': sum(len(v['members']) for v in values),
            'members_diagnosed': sum(m['state'] == 'diagnosed' for v in values for m in v['members']),
            'unbounded_packages': sum(not v['risk_bounded'] for v in values),
            'business_complete': False, 'note': NOTE}


def cumulative_impact(diagnoses, selections=None, *, threshold=0.02):
    """Union overlapping small cases in each country/full path/current month.

    ``selections`` optionally contains {case_id, diagnosis_id, member_id,
    entity_keys}. An omitted entity list means the whole unreviewed member.
    Quantities and amounts keep positive and negative change separately. MoM and
    YoY keep separate calendar denominators; current exposure/SPU/bands use the
    union of exact platform-scoped identities, never a sum of percentages.
    The threshold orders investigation; it is not a data-acceptance tolerance.
    """
    if isinstance(threshold, bool) or not isinstance(threshold, (float, int)) or not 0 <= threshold <= 1:
        raise ValueError('cumulative investigation threshold must be a proportion')
    lookup = {d['diagnosis_id']: d for d in diagnoses}
    if len(lookup) != len(diagnoses):
        raise ValueError('duplicate diagnosis receipts in cumulative input')
    if selections is None:
        selections = [{'case_id': m['member_id'], 'diagnosis_id': d['diagnosis_id'], 'member_id': m['member_id']}
                      for d in diagnoses for m in d['members'] if m['kind'] == 'result_change' and m['state'] == 'diagnosed']
    grouped = defaultdict(list)
    for selection in selections:
        diagnosis = lookup.get(selection.get('diagnosis_id'))
        if diagnosis is None:
            raise ValueError('cumulative selection references an unknown machine receipt')
        matches = [m for m in diagnosis['members'] if m['member_id'] == selection.get('member_id')]
        if len(matches) != 1 or matches[0]['kind'] != 'result_change' or matches[0]['state'] != 'diagnosed':
            raise ValueError('cumulative selection needs a fully diagnosed exact comparison')
        member = matches[0]; population = {e['entity_key'] for e in member['entities']}
        selected = selection.get('entity_keys', sorted(population))
        if not isinstance(selected, list) or len(selected) != len(set(selected)) or not set(selected) <= population:
            raise ValueError('cumulative case contains duplicate or out-of-population objects')
        scope = member.get('scope', diagnosis['scope'])
        key = (scope['site'], canonical(scope['path']), member['current_month'])
        grouped[key].append((diagnosis, member, selection, set(selected)))
    results = []
    for (site, path, mm), items in sorted(grouped.items()):
        current_population = {}; pending = set(); comparisons = defaultdict(dict); all_comparisons = defaultdict(dict)
        cases = set(); unknown = False
        edges = items[0][0]['price_bands']
        for diagnosis, member, selection, selected in items:
            if diagnosis['price_bands'] != edges:
                raise ValueError('cumulative cases have inconsistent frozen price bands')
            cases.add(selection.get('case_id') or member['member_id']); pending.update(selected)
            comparison_key = (member['comparison'], member['base_month'])
            for entity in member['entities']:
                key = entity['entity_key']
                current = {'part': entity['current'], 'band': entity['current_band'],
                           'platform': entity['platform'], 'product_id': entity['product_id']}
                if key in current_population and current_population[key] != current:
                    raise ValueError('overlapping cumulative evidence disagrees for the same current object')
                current_population[key] = current
                previous = all_comparisons[comparison_key].get(key)
                if previous is not None and previous != entity:
                    raise ValueError('overlapping cumulative evidence has inconsistent contribution denominators')
                all_comparisons[comparison_key][key] = entity
                if key in selected:
                    comparisons[comparison_key][key] = entity
                unknown |= not entity['identity_complete']
        current_observed = {k: v for k, v in current_population.items() if v['part']['rows']}
        current_pending = {k: v for k, v in current_observed.items() if k in pending}
        totals = {}; exposure = {}
        for metric in ('units', 'amount'):
            known = all(v['part'][metric+'_known'] for v in current_observed.values())
            totals[metric] = sum(v['part'][metric] for v in current_observed.values()) if known else None
            exposure[metric] = sum(v['part'][metric] for v in current_pending.values()) if known else None
            unknown |= not known
        formal_population = defaultdict(list)
        for key, value in current_observed.items():
            pid = value['product_id'].strip() if isinstance(value['product_id'], str) else None
            formal_population[pid].append((key, value))
        formal_pending = {pid for pid, entries in formal_population.items() if pid and any(k in pending for k, _ in entries)}
        formal_population_count = sum(bool(pid) for pid in formal_population)
        band_total, band_pending = Counter(), Counter()
        for pid, entries in formal_population.items():
            part = _combine_parts([v['part'] for _, v in entries])
            band = -1 if not pid else _band(part, edges)
            if pid:
                band_total[band] += 1
                if pid in formal_pending:
                    band_pending[band] += 1
        band_known = -1 not in band_total and not unknown
        bands = [{'band': b, 'all_spus': n, 'pending_spus': band_pending[b],
                  'denominator': formal_population_count, 'pending_share_pp': 100*band_pending[b]/formal_population_count}
                 for b, n in sorted(band_total.items())]
        changes = []; strengths = []
        for (comparison, base), population in sorted(all_comparisons.items()):
            selected = comparisons[(comparison, base)]
            entry = {'comparison': comparison, 'base_month': base, 'current_month': mm,
                     'pending_entity_count': len(selected), 'population_count': len(population)}
            for metric in ('units', 'amount'):
                known = all(e[metric+'_change'] is not None for e in population.values())
                if not known:
                    entry[metric] = {'state': 'unknown'}; unknown = True; continue
                pos = sum(max(0, e[metric+'_change']) for e in selected.values())
                neg = sum(min(0, e[metric+'_change']) for e in selected.values())
                denominator = sum(abs(e[metric+'_change']) for e in population.values())
                base_total = sum(e['base'][metric] for e in population.values())
                current_total = sum(e['current'][metric] for e in population.values())
                scale = max(abs(base_total), abs(current_total))
                entry[metric] = {'state': 'measured', 'remaining_positive': pos, 'remaining_negative': neg,
                                 'gross_remaining': pos-neg, 'gross_denominator': denominator,
                                 'net_is_not_risk_measure': pos+neg,
                                 'share_of_observed_gross': (pos-neg)/denominator if denominator else None,
                                 'exposure_scale': scale,
                                 'observed_change_strength': (pos-neg)/scale if scale else None}
                if scale:
                    strengths.append((pos-neg)/scale)
            changes.append(entry)
        spu_share = len(formal_pending)/formal_population_count if formal_population_count else None
        if spu_share is not None:
            strengths.append(spu_share)
        if band_known:
            strengths.extend(b['pending_share_pp']/100 for b in bands)
        result = {'site': site, 'path': json.loads(path), 'month': mm, 'case_ids': sorted(cases),
                  'case_count': len(cases), 'deduplicated_pending_entity_count': len(pending),
                  'current_exposure': {'spus': {'state': 'unknown' if unknown else 'measured',
                                               'pending': len(formal_pending), 'denominator': formal_population_count,
                                               'pending_platform_entities': len(current_pending),
                                               'platform_entity_denominator': len(current_observed),
                                               'physical_identity_equivalence_proven': False, 'share': spu_share},
                                       'bands': {'state': 'measured' if band_known else 'unknown', 'recomputed_population': bands},
                                       **{metric: {'state': 'measured' if exposure[metric] is not None else 'unknown',
                                                   'pending': exposure[metric], 'denominator': totals[metric]} for metric in ('units', 'amount')}},
                  'change_exposure_by_comparison': changes, 'cumulative_observed_strength': max(strengths, default=None),
                  'investigation_priority_threshold': threshold, 'risk_bounded': False,
                  'requires_cumulative_review': unknown or max(strengths, default=0) >= threshold,
                  'priority_escalation': max(strengths, default=0) >= threshold,
                  'requires_supplementary_evidence': True,
                  'automatic_deep_assignment': False,
                  'reason': 'unknown_boundary' if unknown else 'cumulative_observed_threshold' if max(strengths, default=0) >= threshold else 'below_sorting_threshold_still_unreviewed',
                  'note': NOTE}
        results.append(result)
    return results
