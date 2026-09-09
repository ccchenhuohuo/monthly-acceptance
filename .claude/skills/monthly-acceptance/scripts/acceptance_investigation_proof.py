"""Mechanical population proof for a result-change investigation.

Scope, complete SQL pages, named contributions and SKU populations are verified
here. Whether an entity's explanation and counterevidence establish its business
cause remains a separate, mandatory coordinator judgment.
"""
from collections import Counter, defaultdict
import math
from pathlib import Path
import re

from acceptance_core import canonical, digest, read, shift
from acceptance_diagnostics import IDENTITY_KEYS, diagnostic_job, traceable_sku
from acceptance_queries import drilldown
from acceptance_validate import verify_job
import acceptance_review as substantive


METRICS = ('spus', 'bands', 'units', 'amount')
EVIDENCE_RE = re.compile(r'(?:main:)?ev-[0-9a-f]{32}')
JOB_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,180}')


def _number(value, label, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('完整人口证明需要有限数值：' + label)
    if nonnegative and value < 0:
        raise ValueError('完整人口证明需要非负数值：' + label)
    return float(value)


def _unique(values, label, allow_empty=False):
    if not isinstance(values, list) or (not values and not allow_empty):
        raise ValueError('缺少显式清单：' + label)
    if any(not isinstance(x, str) or not x.strip() for x in values) or len(values) != len(set(values)):
        raise ValueError('清单包含无效/重复身份：' + label)
    return values


def _explanation(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('缺少具体业务说明：' + label)


def _close(actual, expected, metric, manifest, label):
    expected = _number(expected, label)
    tolerance = max(float(manifest['policy']['amount_tolerance']), abs(expected)*1e-9) if metric == 'amount' else 1e-9
    if abs(actual-expected) > tolerance:
        raise ValueError(f'完整贡献不守恒/自报分母不符：{label}，实算 {actual}，锚点 {expected}')


def _job_id(value):
    if not isinstance(value, str) or not JOB_RE.fullmatch(value):
        raise ValueError('非法证明任务ID')
    return value


def _verified_rows(worker, job, file_hashes, evidence_ids):
    errors = verify_job(worker, job)
    if errors:
        raise ValueError('完整调查SQL/实际请求/分页无效：' + '; '.join(errors))
    record = worker.job_record(job['job_id'])
    files = [worker.path/'records'/('job-'+job['job_id']+'.json')]
    for eid in record['evidence_ids']:
        evidence_ids.add(eid)
        meta_path = worker.path/'records'/(eid+'.json')
        meta = read(meta_path)
        files.extend([meta_path, worker.path/meta['path']])
        value = worker.get_evidence(eid)
        query_file = worker.path/'queries'/(value['request']['query_hash']+'.sql')
        if query_file.exists():
            files.append(query_file)
    for path in files:
        path = path.resolve()
        if not path.is_relative_to(worker.path.resolve()):
            raise ValueError('证明文件路径越界')
        relative = str(path.relative_to(worker.path.resolve()))
        sha = digest(path.read_bytes())
        if relative in file_hashes and file_hashes[relative] != sha:
            raise ValueError('证明检查期间文件发生变化：' + relative)
        file_hashes[relative] = sha
    return worker.job_rows(job['job_id'])


def _side(row, side):
    n = row.get(side+'_rows')
    if n is None:
        n = 0
    n = _number(n, side+'_rows', True)
    if n != int(n):
        raise ValueError('源行数必须为整数')
    result = {'rows': int(n)}
    for metric in ('units', 'amount'):
        value = row.get(side+'_'+metric)
        if n == 0:
            if value not in (None, 0):
                raise ValueError('无观测侧却含量额')
            result[metric] = 0.0
        else:
            result[metric] = _number(value, side+'_'+metric)
    invalid = row.get(side+'_invalid_rows')
    if invalid is None and n == 0:
        invalid = 0
    invalid = _number(invalid, side+'_invalid_rows', True)
    if invalid != int(invalid) or invalid > n:
        raise ValueError('无效源行数与观测源行数不一致')
    result['invalid_rows'] = int(invalid)
    return result


def _band(part, edges):
    if not part['rows']:
        return None
    if part['invalid_rows'] or part['units'] <= 0 or part['amount'] <= 0:
        return -1
    price = part['amount']/part['units']
    return next((i for i, edge in enumerate(edges) if price < edge), len(edges))


def _record_content(row):
    return {k: v for k, v in row.items() if k not in
            ('total_rows', 'matches_seed_product', 'matches_seed_sku', 'is_seed_record')}


def verify_result_proof(manifest, candidate, answer, worker_run, *, check_claims=True, source_lookup=None):
    """Rebuild a result candidate's full population and unreviewed exposure.

    With ``check_claims=False`` the same SQL/identity/conservation checks run, but
    answer.impact can be absent: use returned ``computed_impact`` to draft it.
    Entity status ``blocked_external`` never removes its contribution from the
    unreviewed residual. Unassessed entities remain explicit even at zero change.
    """
    if candidate.get('kind') != 'result_change':
        raise ValueError('此人口证明只适用于 result_change')
    if any(worker_run.manifest.get(k) != manifest.get(k) for k in ('sites', 'starts', 'data_month', 'policy')):
        raise ValueError('子包冻结范围/政策与主包不一致')
    proof = answer.get('population_proof')
    if not isinstance(proof, dict):
        raise ValueError('结果变化缺少完整 population_proof，不能用样本缺陷替代类目调查')
    if 'shared_package_id' in proof:
        if not substantive.enabled(manifest): raise ValueError('正式共享完整明细证明需要3.2协议')
        from acceptance_shared_proof import verify_shared_result_proof
        return verify_shared_result_proof(manifest, candidate, answer, worker_run,
                                          check_claims=check_claims, source_lookup=source_lookup)
    site, level, path, current = (candidate[k] for k in ('site', 'level', 'path', 'month'))
    try:
        lag = manifest['policy']['comparisons'][candidate['comparison']]
        base = shift(current, -lag)
        facts = candidate['facts']
        if facts['base_month'] != base or facts['current_month'] != current:
            raise ValueError('候选比较月份与自然月关系不一致')
        brand_job = diagnostic_job(manifest, 'brands', site, level, path, base, current)
    except (KeyError, TypeError) as exc:
        raise ValueError('候选缺少有效的冻结比较范围') from exc
    supplied_spu = _job_id(proof.get('spu_job_id'))
    supplied_brand = _job_id(proof.get('brand_job_id'))
    if supplied_spu != 'drill-'+candidate['candidate_id'] or supplied_brand != brand_job['job_id']:
        raise ValueError('SPU/品牌证明任务不属于此候选的完整比较范围')
    spu_job = {'job_id': supplied_spu, 'family': 'drill', 'site': site, 'candidate_id': candidate['candidate_id'],
               'key_fields': ['product_id'], 'sql': drilldown(manifest, site, path, level, current, lag)}
    file_hashes, evidence_ids = {}, set()
    rows = _verified_rows(worker_run, spu_job, file_hashes, evidence_ids)
    brands = _verified_rows(worker_run, brand_job, file_hashes, evidence_ids)
    if not rows or not brands:
        raise ValueError('完整贡献人口为空，不能证明已有结果变化')
    population = {}
    for row in rows:
        pid = row.get('product_id')
        if not isinstance(pid, str) or not pid.strip() or pid != pid.strip():
            raise ValueError('贡献人口含缺失/空白商品身份，须先解决身份口径，不能无声丢弃或代为结案')
        if pid in population:
            raise ValueError('完整商品人口包含重复ID')
        population[pid] = {side: _side(row, side) for side in ('base', 'current')}
        if not any(population[pid][s]['rows'] for s in ('base', 'current')):
            raise ValueError('贡献人口两期均无观测')
    anchors = {}
    for side in ('base', 'current'):
        actual = {'spus': sum(p[side]['rows'] > 0 for p in population.values()),
                  **{m: sum(p[side][m] for p in population.values()) for m in ('units', 'amount')}}
        for metric in ('spus', 'units', 'amount'):
            _close(actual[metric], facts['metrics'][metric][side], metric, manifest, side+'.'+metric)
        for metric in ('units', 'amount'):
            value = sum(_number(row.get(side+'_'+metric), '具名品牌.'+side+'.'+metric) for row in brands)
            _close(value, actual[metric], metric, manifest, '具名品牌.'+side+'.'+metric)
        anchors[side] = actual
    gross = {m: sum(abs(p['current'][m]-p['base'][m]) for p in population.values()) for m in ('units', 'amount')}
    for metric in ('units', 'amount'):
        baseline_gross = facts.get('spu_movement', {}).get(metric, {}).get('gross')
        if baseline_gross is not None:
            _close(gross[metric], baseline_gross, metric, manifest, '完整SPU绝对变动.'+metric)

    assessments = proof.get('entity_assessments')
    if not isinstance(assessments, list):
        raise ValueError('缺少 entity_assessments；尚未调查时也须明确给出空清单')
    statuses = {}
    for group in assessments:
        ids = _unique(group.get('product_ids'), '实体判断精确商品ID')
        if any(pid not in population or pid in statuses for pid in ids):
            raise ValueError('实体判断包含范围外或重复归属商品')
        if group.get('status') not in ('verified', 'blocked_external'):
            raise ValueError('实体判断必须为 verified 或 blocked_external')
        _explanation(group.get('reason'), '实体判断理由')
        _explanation(group.get('counterevidence'), '实体判断反证')
        for eid in _unique(group.get('evidence_ids'), '实体判断证据'):
            if not EVIDENCE_RE.fullmatch(eid):
                raise ValueError('实体判断证据引用格式无效')
            if not eid.startswith('main:'):
                worker_run.get_evidence(eid)
                evidence_ids.add(eid)
        statuses.update({pid: group['status'] for pid in ids})

    sku_job_ids = _unique(proof.get('sku_job_ids'), 'SKU轨迹任务', allow_empty=True)
    if statuses and not sku_job_ids:
        raise ValueError('声称调查了实体，但没有对应完整SKU轨迹')
    sku_records = {}
    for jid in sku_job_ids:
        _job_id(jid)
        record = worker_run.job_record(jid)
        if not record or record.get('kind') != 'sku_trace':
            raise ValueError('SKU证明必须来自完整 sku_trace 任务')
        expected = diagnostic_job(manifest, 'sku_trace', site, level, path, base, current,
                                  product_ids=record.get('product_ids'))
        if expected['job_id'] != jid:
            raise ValueError('SKU轨迹国家/路径/月份/商品分片与本义务不一致')
        data = _verified_rows(worker_run, expected, file_hashes, evidence_ids)
        if not data:
            raise ValueError('SKU轨迹没有定位到请求人口；空结果不表示已查')
        for row in data:
            key = canonical([row[k] for k in expected['key_fields']])
            if key in sku_records and _record_content(sku_records[key]) != _record_content(row):
                raise ValueError('共享SKU在不同完整分片中的记录发生变化')
            sku_records[key] = row
    target = defaultdict(list)
    raw_index = defaultdict(list)
    raw_parent_context = defaultdict(list)
    import json
    parts = json.loads(path)
    for row in sku_records.values():
        if row['site'] != site or not manifest['starts'][site]+'-01' <= row['month_dt'] <= manifest['data_month']+'-01':
            raise ValueError('SKU响应包含范围外国家/月份')
        if row['source_layer'] == 'std':
            std = json.loads(row['std_path'])
            in_target = std[:level] == parts
            if row['in_target_path'] != int(in_target):
                raise ValueError('SKU标准路径与目标归属标志不一致')
            if in_target and row['month_dt'] in (base+'-01', current+'-01'):
                target[(row['product_id'], row['month_dt'])].append(row)
        elif row['source_layer'] == 'raw':
            raw_parent_context[(row['platform'], row['product_id'], row['month_dt'])].append(row)
            if traceable_sku(row['sku_id']):
                raw_index[(row['platform'], row['sku_id'], row['month_dt'])].append(row)
        else:
            raise ValueError('SKU证据来源层无效')
    sku_observations = []; untraceable = set()
    for pid in statuses:
        found = []; parent_contexts = {}
        for side, mm in [('base', base+'-01'), ('current', current+'-01')]:
            records = target.get((pid, mm), [])
            part = population[pid][side]
            _close(sum(_number(r['source_rows'], 'SKU.source_rows', True) for r in records), part['rows'], 'spus', manifest, pid+'.'+side+'.SKU行数')
            for metric in ('units', 'amount'):
                total = sum(_number(r[metric], 'SKU.'+metric) for r in records)
                _close(total, part[metric], metric, manifest, pid+'.'+side+'.SKU.'+metric)
            _close(sum(_number(r['invalid_value_rows'], 'SKU.invalid_value_rows', True) for r in records), part['invalid_rows'], 'spus', manifest, pid+'.'+side+'.无效值行数')
            for row in records:
                if not traceable_sku(row['sku_id']) or not isinstance(row['platform'], str) or not row['platform'].strip():
                    untraceable.add(pid)
                    if statuses[pid] == 'verified':
                        raise ValueError('已核对象仍含不可追踪的平台/SKU身份；占位源行不能代为结案')
                    # Internal investigation can establish a genuine missing-ID
                    # blocker without inventing cross-parent SKU destinations.
                    # Preserve the complete same-parent source evidence as
                    # context, explicitly separate from SKU identity matches.
                    context = (row['platform'], pid, mm)
                    parent_contexts[canonical(context)] = {'month': mm[:7], 'platform': row['platform'],
                        'product_id': pid, 'is_sku_identity_match': False,
                        'raw_record_keys': sorted(canonical([r[k] for k in IDENTITY_KEYS])
                            for r in raw_parent_context.get(context, []))}
                    found.append({'month': mm[:7], 'platform': row['platform'], 'sku_id': row['sku_id'],
                        'std_product_id': pid, 'identity_state': 'untraceable_sku_or_platform',
                        'source_record_key': canonical([row[k] for k in IDENTITY_KEYS]),
                        'raw_observed': None, 'raw_product_ids': [], 'raw_paths': []})
                    continue
                destinations = raw_index.get((row['platform'], row['sku_id'], mm), [])
                found.append({'month': mm[:7], 'platform': row['platform'], 'sku_id': row['sku_id'],
                              'std_product_id': pid, 'raw_observed': bool(destinations),
                              'raw_product_ids': sorted({r['product_id'] for r in destinations if r['product_id'] is not None}),
                              'raw_paths': sorted({r['raw_path'] for r in destinations})})
        if not found:
            raise ValueError('SKU证据无法定位声称已查商品：'+pid)
        sku_observations.append({'product_id': pid, 'observations': found,
                                 'raw_parent_contexts': [parent_contexts[k] for k in sorted(parent_contexts)]})

    def cause_lookup(eid):
        if not eid.startswith('main:'): return worker_run.get_evidence(eid)
        if source_lookup: return source_lookup(eid[5:])
        if worker_run.manifest.get('parent_run'):
            from acceptance_core import Run
            return Run(worker_run.manifest['parent_run']).get_evidence(eid[5:])
        raise ValueError('主包原因证据缺可核对来源')
    population_records = defaultdict(list)
    for (pid, _), records in target.items(): population_records[pid].extend(records)
    credits = substantive.cause_credit(manifest, candidate, assessments, cause_lookup, population_records=population_records)
    investigated = {pid for pid, state in statuses.items() if state == 'verified'}
    verified = set.intersection(*credits.values())
    blocked = {pid for pid, state in statuses.items() if state == 'blocked_external'}
    unreviewed = set(population)-verified
    not_assessed = set(population)-set(statuses)
    band_rows, max_exposure, max_unreviewed = [], 0.0, 0.0
    edges = manifest['policy']['price_bands'][site]
    for side in ('base', 'current'):
        total = anchors[side]['spus']
        all_bands, pending_bands = Counter(), Counter()
        for pid, p in population.items():
            band = _band(p[side], edges)
            if band is not None:
                all_bands[band] += 1
                if pid not in credits['bands']:
                    pending_bands[band] += 1
        for band in sorted(all_bands):
            exposure = 100.0*all_bands[band]/total if total else 0.0
            pending = 100.0*pending_bands[band]/total if total else 0.0
            max_exposure, max_unreviewed = max(max_exposure, exposure), max(max_unreviewed, pending)
            band_rows.append({'side': side, 'band': band, 'spus': all_bands[band], 'unreviewed_spus': pending_bands[band],
                              'share_pp': exposure, 'unreviewed_share_pp': pending})
    computed = {
        'spus': {'state': 'measured', 'exposure': len(population), 'unexplained': len(set(population)-credits['spus']), 'unit': 'SPU',
                 'basis': '完整同路径双期SPU并集；未核为未获得verified实体判断的并集'},
        'bands': {'state': 'measured', 'exposure': max_exposure, 'unexplained': max_unreviewed, 'unit': 'pp',
                  'basis': '分别以各期完整SPU数为分母，取两期各固定价格带商品占比及其中未核占比的最大值；未知价格单列-1'},
        **{m: {'state': 'measured', 'exposure': gross[m],
               'unexplained': sum(abs(population[pid]['current'][m]-population[pid]['base'][m]) for pid in set(population)-credits[m]),
               'unit': '件' if m == 'units' else '站点本币',
               'basis': '完整同路径双期每个SPU绝对变动之和；未核部分保留未verified对象的绝对变动，正负不得抵消'} for m in ('units', 'amount')},
    }
    for item in computed.values():
        item['evidence_ids'] = sorted(evidence_ids)
    if check_claims:
        impacts = answer.get('impact')
        if not isinstance(impacts, dict) or set(impacts) != set(METRICS):
            raise ValueError('答卷缺少四项机算影响；先使用 check_claims=False 生成')
        for metric, expected in computed.items():
            claim = impacts[metric]
            if claim.get('state') != 'measured':
                raise ValueError('完整人口已可计算，不能把可算影响改成未知')
            for field in ('exposure', 'unexplained'):
                _close(_number(claim.get(field), metric+'.'+field, True), expected[field], metric, manifest, '答卷.'+metric+'.'+field)
    for relative, sha in file_hashes.items():
        if digest((worker_run.path/relative).read_bytes()) != sha:
            raise ValueError('人口证明计算期间证据发生变化：'+relative)
    extra = {}
    if substantive.enabled(manifest):
        for metric, item in computed.items():
            item['basis'] = item['basis'].replace('未获得verified实体判断', '未获得对应指标的有原始对象证据原因判断').replace('未verified对象', '未获得对应指标原因解释的对象')
        components = {}
        for metric in ('units', 'amount'):
            deltas = {pid: p['current'][metric]-p['base'][metric] for pid, p in population.items()}
            components[metric] = {'positive': sum(max(v, 0) for v in deltas.values()),
                'negative': sum(max(-v, 0) for v in deltas.values()), 'net': sum(deltas.values()), 'gross': gross[metric],
                'unexplained_positive': sum(max(v, 0) for pid, v in deltas.items() if pid not in credits[metric]),
                'unexplained_negative': sum(max(-v, 0) for pid, v in deltas.items() if pid not in credits[metric])}
        extra = {'investigated_product_ids': sorted(investigated),
                 'cause_credited_product_ids': {m: sorted(ids) for m, ids in credits.items()},
                 'metric_residual_product_ids': {m: sorted(set(population)-ids) for m, ids in credits.items()},
                 'contribution_components': components,
                 'population_movements': {name: sorted(pid for pid, p in population.items() if condition(p)) for name, condition in {
                     'entered': lambda p: not p['base']['rows'] and p['current']['rows'],
                     'exited': lambda p: p['base']['rows'] and not p['current']['rows'],
                     'continuing': lambda p: p['base']['rows'] and p['current']['rows']}.items()},
                 'continuation_requirements': substantive.continuation_requirements(manifest, candidate, computed, credits, population)}
    return {'protocol': 2 if substantive.enabled(manifest) else 1, **extra, 'candidate_id': candidate['candidate_id'], 'candidate_sha256': digest(candidate),
            'scope': {'site': site, 'level': level, 'path': path, 'base_month': base, 'current_month': current},
            'computed_impact': computed, 'anchors': anchors,
            'cohort': {'all': len(population), 'verified': len(verified), 'blocked_external': len(blocked), 'not_assessed': len(not_assessed)},
            'verified_product_ids': sorted(verified), 'blocked_product_ids': sorted(blocked),
            'not_assessed_product_ids': sorted(not_assessed), 'unreviewed_product_ids': sorted(unreviewed),
            'untraceable_product_ids': sorted(untraceable),
            'band_population': band_rows, 'sku_observations': sku_observations,
            'job_ids': [supplied_spu, supplied_brand]+sorted(sku_job_ids),
            'evidence_ids': sorted(evidence_ids), 'job_files_sha256': dict(sorted(file_hashes.items())),
            'entity_assessments_sha256': digest(assessments),
            'note': '机验保证比较范围、完整人口、证据分页和数值守恒；实体原因、反证和缺陷性质仍须主Agent实质审核。'}
