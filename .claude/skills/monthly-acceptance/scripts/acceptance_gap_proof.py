"""Complete seed and destination proof for raw/std path observation gaps.

A missing observation is not zero market sales. Exposure is anchored to the most
recent observed month strictly before the gap, and every seed is followed through
every remaining natural month. Business explanations still require main review.
"""
from collections import Counter, defaultdict
import json

from acceptance_core import canonical, digest, month, months, shift
from acceptance_diagnostics import diagnostic_job, traceable_sku
from acceptance_queries import job as baseline_job
from acceptance_validate import verify_job
import acceptance_review as substantive
from acceptance_investigation_proof import (
    EVIDENCE_RE, _band, _close, _explanation, _job_id, _number, _unique, _verified_rows,
)


SEED_KEYS = ('site', 'platform', 'seed_product_id', 'seed_sku_id', 'seed_raw_path',
             'seed_std_path', 'seed_attribute_key')
SEED_CONTEXT = ('seed_source_rows_context', 'seed_amount_context', 'seed_units_context',
                'seed_invalid_value_rows_context', 'seed_missing_amount_rows_context', 'seed_missing_units_rows_context')
OBSERVED_STATES = {'same_sku_same_parent', 'same_sku_other_parent'}


class _BaselinePages:
    """Read-only adapter so parent evidence pages use the same job verifier."""
    def __init__(self, manifest, job, evidence_ids, lookup):
        self.policy = manifest['policy']
        self.job = job
        self.values = {eid: lookup(eid) for eid in evidence_ids}
        try:
            ordered = sorted(evidence_ids, key=lambda e: self.values[e]['request']['offset'])
            rows = [row for eid in ordered for row in self.values[eid]['response']['data']]
            if not rows:
                raise ValueError('完整基线人口为空，无法建立缺口锚点')
            totals = {int(row['total_rows']) for row in rows}
            if len(totals) != 1 or next(iter(totals)) != len(rows):
                raise ValueError('候选缺口基线缺页或总行数不一致')
            self.record = {'execution_status': 'success', 'sql_hash': digest(job['sql']),
                           'key_fields': job['key_fields'], 'evidence_ids': ordered,
                           'expected_rows': len(rows), 'row_count': len(rows)}
        except (KeyError, TypeError) as exc:
            raise ValueError('缺口基线不是完整带请求/响应的查询证据') from exc

    def job_record(self, job_id):
        return self.record if job_id == self.job['job_id'] else None

    def get_evidence(self, eid):
        return self.values[eid]

    def job_rows_from_record(self, record, job_id):
        if record!=self.record:raise ValueError('基线收据与已绑定分页不一致')
        return self.job_rows(job_id)

    def job_rows(self, job_id):
        if job_id != self.job['job_id']:
            raise ValueError('基线任务不匹配')
        rows = []
        for eid in self.record['evidence_ids']:
            response = self.values[eid]['response']
            if not response.get('success') or response.get('row_count') != len(response['data']):
                raise ValueError('基线响应失败或行数无效')
            rows.extend(response['data'])
        keys = [canonical([row[key] for key in self.job['key_fields']]) for row in rows]
        if len(keys) != len(set(keys)):
            raise ValueError('基线分页存在重复月/路径身份')
        return rows


def _path(value):
    try:
        result = json.loads(value)
    except (ValueError, TypeError) as exc:
        raise ValueError('缺口证据缺少完整JSON路径') from exc
    if not isinstance(result, list):
        raise ValueError('缺口路径必须是数组')
    return result


def _count(value, label, *, positive=False):
    result = _number(value, label, True)
    if result != int(result) or (positive and result == 0):
        raise ValueError('缺口源行数/商品数必须是有效整数：'+label)
    return int(result)


def verify_gap_proof(manifest, candidate, answer, worker_run, source_lookup, *, check_claims=True):
    """Verify full baseline seeds and every calendar-month destination cell.

    ``source_lookup(eid)`` receives an unprefixed parent evidence ID and must
    validate its stored hash. Returned ``evidence_ids`` use ``main:`` for these
    parent references and bare IDs for worker evidence, ready for parent import.
    ``check_claims=False`` calculates measured impact before an answer is drafted.
    """
    kind = candidate.get('kind')
    if kind not in ('raw_presence_gap', 'std_presence_gap'):
        raise ValueError('此缺口证明仅支持 raw_presence_gap/std_presence_gap')
    if any(worker_run.manifest.get(k) != manifest.get(k) for k in ('sites', 'starts', 'data_month', 'policy')):
        raise ValueError('子包冻结范围/政策与主包不一致')
    proof = answer.get('gap_proof')
    if not isinstance(proof, dict):
        raise ValueError('路径缺口缺少完整 gap_proof，少数SKU文字不能替代全体去向')
    if 'shared_package_id' in proof:
        if not substantive.enabled(manifest): raise ValueError('正式共享缺口证明需要3.2协议')
        from acceptance_shared_proof import verify_shared_gap_proof
        return verify_shared_gap_proof(manifest, candidate, answer, worker_run, source_lookup, check_claims=check_claims)
    site, path, signal_month = (candidate[k] for k in ('site', 'path', 'month'))
    month(signal_month)
    if site not in manifest['sites'] or not manifest['starts'][site] <= signal_month <= manifest['data_month']:
        raise ValueError('缺口国家/月份超出冻结范围')
    level = 'raw' if kind == 'raw_presence_gap' else candidate['level']
    wanted_path = _path(path)
    source_job = baseline_job(manifest, 'raw_paths', site) if level == 'raw' else baseline_job(manifest, 'category', site, level=level)
    if candidate.get('source_jobs') is not None and source_job['job_id'] not in candidate['source_jobs']:
        raise ValueError('候选来源不包含对应完整路径基线任务')
    source_ids = _unique(candidate.get('evidence_ids'), '完整缺口基线证据')
    for eid in source_ids:
        if not EVIDENCE_RE.fullmatch(eid) or eid.startswith('main:'):
            raise ValueError('候选基线证据应为父包原始证据ID')
    baseline = _BaselinePages(manifest, source_job, source_ids, source_lookup)
    errors = verify_job(baseline, source_job)
    if errors:
        raise ValueError('完整缺口基线SQL/实际请求/分页无效：'+'; '.join(errors))
    previous = []
    for row in baseline.job_rows(source_job['job_id']):
        if _path(row['path']) != wanted_path:
            continue
        mm = row['month_dt'][:7]
        month(mm)
        if row['month_dt'] != mm+'-01':
            raise ValueError('基线日期不是自然月首日')
        if mm == signal_month:
            raise ValueError('缺口月份实际已有同路径观测，不能用无观测假设结案')
        if mm < signal_month:
            previous.append((mm, row))
    if not previous:
        raise ValueError('缺口前没有可用同路径观测，无法构成全量锚点；不能伪造0')
    base_month, anchor_row = max(previous, key=lambda x: x[0])
    anchors = {'month': base_month, 'spus': _count(anchor_row.get('spus'), '基线SPU', positive=True),
               'nrows': _count(anchor_row.get('nrows'), '基线源行数', positive=True),
               'units': _number(anchor_row.get('units'), '基线销量'),
               'amount': _number(anchor_row.get('amount'), '基线金额')}
    typed = diagnostic_job(manifest, 'source_coverage', site, level, path, base_month, manifest['data_month'])
    if _job_id(proof.get('source_coverage_job_id')) != typed['job_id']:
        raise ValueError('去向任务须为缺口前最近观测月到冻结末月的完整人口，不能改月份或只查商品分片')
    file_hashes = {}
    evidence_ids = {'main:'+eid for eid in source_ids}
    rows = _verified_rows(worker_run, typed, file_hashes, evidence_ids)
    if not rows:
        raise ValueError('完整缺口去向没有种子，不能视为已覆盖')
    seeds, cells, seen_cells = {}, defaultdict(list), defaultdict(set)
    future_months = months(shift(base_month, 1), manifest['data_month'])
    expected_cells = {(mm+'-01', layer) for mm in future_months for layer in ('raw', 'std')}
    for row in rows:
        seed_key = canonical([row[key] for key in SEED_KEYS])
        if row['site'] != site:
            raise ValueError('去向证据种子国家不符')
        seed_path = _path(row['seed_raw_path'] if level == 'raw' else row['seed_std_path'])
        if (seed_path if level == 'raw' else seed_path[:level]) != wanted_path:
            raise ValueError('去向证据种子不属于完整目标路径')
        if (row['month_dt'], row['source_layer']) not in expected_cells:
            raise ValueError('去向证据包含范围外月份/来源层')
        pid = row['seed_product_id']
        if not isinstance(pid, str) or not pid.strip() or pid != pid.strip():
            raise ValueError('种子商品身份缺失/空白，无法构成可逐实体审查的完整锚点')
        context = {key: row[key] for key in SEED_CONTEXT}
        n = _count(context['seed_source_rows_context'], '种子源行数', positive=True)
        for key in ('seed_invalid_value_rows_context', 'seed_missing_amount_rows_context', 'seed_missing_units_rows_context'):
            if _count(context[key], key) > n:
                raise ValueError('种子无效/缺失行数超过总源行数')
        _number(context['seed_amount_context'], '种子金额')
        _number(context['seed_units_context'], '种子销量')
        if seed_key in seeds and seeds[seed_key]['context'] != context:
            raise ValueError('重复呈现的同种子上下文发生变化')
        seeds[seed_key] = {'product_id': pid, 'platform': row['platform'], 'sku_id': row['seed_sku_id'],
                           'context': context, 'traceable': bool(isinstance(row['platform'], str) and row['platform'].strip()
                           and traceable_sku(row['seed_sku_id']))}
        cells[(seed_key, row['month_dt'], row['source_layer'])].append(row)
        seen_cells[seed_key].add((row['month_dt'], row['source_layer']))
    destination_summary = Counter()
    for seed_key, seed in seeds.items():
        actual_cells = seen_cells[seed_key]
        if actual_cells != expected_cells:
            raise ValueError('种子缺少后续自然月×raw/std去向/明确无观测记录：'+seed['product_id'])
        for mm, layer in expected_cells:
            found = cells[(seed_key, mm, layer)]
            states = {row['observation_state'] for row in found}
            if states <= OBSERVED_STATES:
                if not seed['traceable']:
                    raise ValueError('不可追踪种子被伪装为已定位去向')
                for row in found:
                    if row['destination_sku_id'] != seed['sku_id']:
                        raise ValueError('去向SKU身份与种子不一致')
                    _count(row['source_rows'], '去向源行数', positive=True)
                    _path(row['raw_path'])
                    if layer == 'std':
                        _path(row['std_path'])
                    expected_state = 'same_sku_same_parent' if row['destination_product_id'] == seed['product_id'] else 'same_sku_other_parent'
                    if row['observation_state'] != expected_state:
                        raise ValueError('去向父体身份与状态不一致')
            elif states in ({'no_observation'}, {'untraceable_seed_identity'}):
                if len(found) != 1:
                    raise ValueError('同种子同月同层的无观测记录重复')
                row = found[0]
                expected_state = 'no_observation' if seed['traceable'] else 'untraceable_seed_identity'
                if row['observation_state'] != expected_state:
                    raise ValueError('不可追踪身份不能记成已证明无观测')
                if any(row.get(k) is not None for k in ('destination_product_id', 'destination_sku_id', 'source_rows', 'amount', 'units', 'raw_path', 'std_path', 'attribute_key')):
                    raise ValueError('无观测记录混入了虚构去向或0销量')
            else:
                raise ValueError('同种子同月同层混合无观测和已有去向')
            for state in states:
                destination_summary[state] += 1
    population = defaultdict(lambda: {'rows': 0, 'amount': 0.0, 'units': 0.0, 'invalid_rows': 0})
    for seed in seeds.values():
        c, p = seed['context'], population[seed['product_id']]
        p['rows'] += c['seed_source_rows_context']
        p['amount'] += c['seed_amount_context']
        p['units'] += c['seed_units_context']
        p['invalid_rows'] += c['seed_invalid_value_rows_context']
    actual = {'spus': len(population), 'nrows': sum(p['rows'] for p in population.values()),
              **{m: sum(p[m] for p in population.values()) for m in ('units', 'amount')}}
    for metric in ('spus', 'nrows', 'units', 'amount'):
        _close(actual[metric], anchors[metric], metric, manifest, '缺口完整种子.'+metric)
    statuses = {}
    assessments = proof.get('entity_assessments')
    if not isinstance(assessments, list):
        raise ValueError('缺少缺口实体判断清单；尚未调查须显式给空清单')
    untraceable = {seed['product_id'] for seed in seeds.values() if not seed['traceable']}
    for group in assessments:
        ids = _unique(group.get('product_ids'), '缺口实体精确商品ID')
        if any(pid not in population or pid in statuses for pid in ids):
            raise ValueError('缺口实体判断含范围外或重复商品')
        if group.get('status') not in ('verified', 'blocked_external'):
            raise ValueError('缺口实体判断状态无效')
        if group['status'] == 'verified' and set(ids)&untraceable:
            raise ValueError('缺少SKU/平台身份的种子仍不能完成去向核实')
        _explanation(group.get('reason'), '缺口原因')
        _explanation(group.get('counterevidence'), '缺口反证')
        for eid in _unique(group.get('evidence_ids'), '缺口实体证据'):
            if not EVIDENCE_RE.fullmatch(eid):
                raise ValueError('缺口实体证据引用格式无效')
            if eid.startswith('main:'):
                source_lookup(eid[5:])
            else:
                worker_run.get_evidence(eid)
            evidence_ids.add(eid)
        statuses.update({pid: group['status'] for pid in ids})
    def cause_lookup(eid):
        return source_lookup(eid[5:]) if eid.startswith('main:') else worker_run.get_evidence(eid)
    population_records = defaultdict(list)
    for row in rows: population_records[row['seed_product_id']].append(row)
    credits = substantive.cause_credit(manifest, candidate, assessments, cause_lookup, population_records=population_records)
    investigated = {pid for pid, status in statuses.items() if status == 'verified'}
    verified = set.intersection(*credits.values())
    blocked = {pid for pid, status in statuses.items() if status == 'blocked_external'}
    pending = set(population)-verified
    not_assessed = set(population)-set(statuses)
    all_bands, pending_bands = Counter(), Counter()
    for pid, part in population.items():
        band = _band(part, manifest['policy']['price_bands'][site])
        all_bands[band] += 1
        if pid not in credits['bands']:
            pending_bands[band] += 1
    bands = [{'band': band, 'spus': count, 'unreviewed_spus': pending_bands[band],
              'share_pp': 100.0*count/len(population), 'unreviewed_share_pp': 100.0*pending_bands[band]/len(population)}
             for band, count in sorted(all_bands.items())]
    computed = {
        'spus': {'state': 'measured', 'exposure': len(population), 'unexplained': len(set(population)-credits['spus']), 'unit': 'SPU',
                 'basis': '缺口前最近观测月的完整同路径商品并集；未核保留所有未verified商品'},
        'bands': {'state': 'measured', 'exposure': max(r['share_pp'] for r in bands),
                  'unexplained': max(r['unreviewed_share_pp'] for r in bands), 'unit': 'pp',
                  'basis': '缺口前最近观测月每个固定价格带商品占完整SPU数的份额；取全体及未核部分最大占比，未知价格单列-1'},
        **{m: {'state': 'measured', 'exposure': sum(abs(p[m]) for p in population.values()),
               'unexplained': sum(abs(population[pid][m]) for pid in set(population)-credits[m]), 'unit': '件' if m == 'units' else '站点本币',
               'basis': '缺口前最近观测月完整种子按商品汇总的绝对观测'+('销量' if m == 'units' else '金额')+'；表示已知暴露基数，不是缺口月真实损失'}
           for m in ('units', 'amount')},
    }
    for item in computed.values():
        item['evidence_ids'] = sorted(evidence_ids)
    if check_claims:
        impacts = answer.get('impact')
        if not isinstance(impacts, dict) or set(impacts) != {'spus', 'bands', 'units', 'amount'}:
            raise ValueError('缺口答卷缺少四项机算影响；先以 check_claims=False 生成')
        for metric, item in computed.items():
            claim = impacts[metric]
            if claim.get('state') != 'measured':
                raise ValueError('已知种子暴露可计算，不得用unknown抹去')
            for key in ('exposure', 'unexplained'):
                _close(_number(claim.get(key), metric+'.'+key, True), item[key], metric, manifest, '缺口答卷.'+metric+'.'+key)
    for rel, sha in file_hashes.items():
        if digest((worker_run.path/rel).read_bytes()) != sha:
            raise ValueError('缺口证明计算期间子包证据发生变化')
    source_hashes = {eid: digest(value) for eid, value in baseline.values.items()}
    for eid, sha in source_hashes.items():
        if digest(source_lookup(eid)) != sha:
            raise ValueError('缺口证明计算期间父包基线发生变化')
    extra = {}
    if substantive.enabled(manifest):
        extra = {'investigated_product_ids': sorted(investigated),
                 'cause_credited_product_ids': {m: sorted(ids) for m, ids in credits.items()},
                 'metric_residual_product_ids': {m: sorted(set(population)-ids) for m, ids in credits.items()},
                 'continuation_requirements': substantive.continuation_requirements(manifest, candidate, computed, credits, population)}
    return {'protocol': 2 if substantive.enabled(manifest) else 1, **extra, 'candidate_id': candidate['candidate_id'], 'candidate_sha256': digest(candidate),
            'scope': {'site': site, 'level': level, 'path': path, 'signal_month': signal_month,
                      'base_month': base_month, 'current_month': manifest['data_month']},
            'computed_impact': computed, 'anchors': anchors,
            'cohort': {'all': len(population), 'verified': len(verified), 'blocked_external': len(blocked), 'not_assessed': len(not_assessed)},
            'seed_count': len(seeds), 'expected_destination_cells': len(seeds)*len(expected_cells),
            'verified_destination_cells': len(cells), 'destination_state_counts': dict(destination_summary),
            'verified_product_ids': sorted(verified), 'blocked_product_ids': sorted(blocked),
            'not_assessed_product_ids': sorted(not_assessed), 'unreviewed_product_ids': sorted(pending),
            'untraceable_product_ids': sorted(untraceable), 'band_population': bands,
            'job_ids': [typed['job_id']], 'evidence_ids': sorted(evidence_ids),
            'job_files_sha256': dict(sorted(file_hashes.items())), 'source_evidence_sha256': source_hashes,
            'entity_assessments_sha256': digest(assessments),
            'note': '机验保证最近历史锚点、完整种子和每个自然月去向；无观测不等于零销售，原因及合理性由主Agent逐组审核。'}
