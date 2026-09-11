"""Golden detection set: known defect shapes must light up their own constraint.

The rest of the suite proves engineering invariants — that the machinery is
self-consistent. It does not prove the machinery would *notice* a real defect.
Each case here injects one concrete defect into an otherwise clean run and
asserts, differentially against that clean run, that exactly the responsible
rule turns failed. A case that fires on the clean baseline too is a false
positive and fails just as loudly as one that never fires.
"""
from copy import deepcopy
from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from acceptance_core import Run, create_run, read, write
from acceptance_queries import plan
import acceptance_panorama as panorama
import acceptance_questions as q
from test_execution import record, rows_for


PLATFORM_ROWS = [{'month_dt': mm + '-01', 'platform': 'Amazon', 'nrows': 1, 'spus': 1, 'effective_skus': 1,
                  'placeholder_sku_rows': 0, 'invalid_rows': 0, 'units': 10, 'amount': 100}
                 for mm in ('2024-01', '2024-02')]


def build(tmp_path, mutate=None, site='DE'):
    """One clean run, optionally with a single injected defect."""
    base = tmp_path / 'audit'
    base.mkdir()
    (base / 'config.yaml').write_text('站点起始月份: {%s: 2024-01}\n' % site)
    (base / '项目范围.md').write_text('Golden detection fixture for ' + site + '.')
    write(base / 'policy-overrides.json', {'page_size': 5})
    write(base / 'scope-contract.json', {'complete_leaf_list': True,
                                         'entries': [{'id': 'root', 'site': site, 'path_prefix': ['Electronics'],
                                                      'effective_from': '2024-01'}]})
    run = Run(create_run(base, Path(__file__).resolve().parents[1], '2024-02'))
    run.register_plan(plan(run.manifest))
    q.prepare_coverage(run)
    for job in plan(run.manifest):
        rows = [dict(r, platform=p) for r in PLATFORM_ROWS
                for p in run.policy['expected_platforms'][site]] if job['family'] == 'platform' else rows_for(run, job)
        if mutate:
            rows = mutate(job, deepcopy(rows))
        record(run, job, rows)
        write(run.path / 'records' / (job['job_id'] + '-plan.json'), job)
    return run, panorama.build(run)


def failed_rules(index):
    return {c['rule'] for c in index['constraints'] if c['state'] == 'failed'}


def _at(family, level=None):
    """Match one baseline job family, optionally at one category level."""
    return lambda job: job['family'] == family and (level is None or job.get('level') == level)


def drop_last_month(family):
    def mutate(job, rows):
        return rows[:-1] if _at(family)(job) else rows
    return mutate


def set_field(family, field, value, *, level=None, index=-1):
    def mutate(job, rows):
        if _at(family, level)(job) and rows:
            rows[index][field] = value
        return rows
    return mutate


def level_shift(index=-1, **values):
    """Move one month's country fingerprint, leaving the other month alone."""
    def mutate(job, rows):
        if job['family'] == 'fingerprint' and rows:
            rows[index].update(values)
        return rows
    return mutate


def unexpected_platform(job, rows):
    if job['family'] == 'platform':
        return rows + [dict(rows[0], platform='Tmall')]
    return rows


def missing_contract_path(job, rows):
    # The authoritative contract promises Electronics; the raw layer stops showing it.
    if job['family'] == 'raw_paths':
        for row in rows:
            row['path'] = '["Furniture"]'
    return rows


# (case name, injected defect, the rule that must become failed)
CASES = [
    ('完整性：原始层整月缺失', drop_last_month('fingerprint'), 'arrival'),
    ('完整性：契约路径不再出现', missing_contract_path, 'contract'),
    ('完整性：出现未预期平台', unexpected_platform, 'platform_arrival'),
    ('重复放大：映射层重复行', set_field('mapping', 'raw_duplicate_rows', 3), 'mapping'),
    ('身份连续性：标准层多出无源键', set_field('mapping', 'std_only_keys', 4), 'mapping'),
    ('数值守恒：类目出现无效行', set_field('category', 'invalid_rows', 2, level=3), 'valid_values'),
    ('数值守恒：价格带汇总对不上类目', set_field('bands', 'units', 999, level=3), 'band_conservation'),
    ('数值守恒：子级汇总对不上父级', set_field('category', 'units', 999, level=3), 'hierarchy_conservation'),
    ('时间异常：变化分量与当月记录不符', set_field('movement', 'current_units', 777), 'movement_conservation'),
    ('价格带：带内金额与类目金额背离', set_field('bands', 'amount', 555, level=3), 'band_conservation'),
    ('时间序列：身份数量跨月跳涨而量额不动', level_shift(nrows=3, spus=3), 'level_shift'),
    ('时间序列：身份数量跨月塌陷而量额不动', level_shift(index=0, nrows=3, spus=3), 'level_shift'),
]


@pytest.fixture(scope='module')
def results(tmp_path_factory):
    """Build the clean baseline and every injected case once for the whole module."""
    built = {None: build(tmp_path_factory.mktemp('clean'))[1]}
    for i, (name, mutate, _) in enumerate(CASES):
        built[name] = build(tmp_path_factory.mktemp('case%d' % i), mutate)[1]
    return built


def test_a_clean_run_raises_no_hard_failure(results):
    """Every injected case below is differential against this baseline."""
    assert failed_rules(results[None]) == set()


@pytest.mark.parametrize('name,rule', [(c[0], c[2]) for c in CASES], ids=[c[0] for c in CASES])
def test_known_defect_shape_is_surfaced(results, name, rule):
    surfaced = failed_rules(results[name])
    assert rule in surfaced, f'{name}：注入后 {rule} 未点亮，实际失败规则 {sorted(surfaced)}'
    assert rule not in failed_rules(results[None]), f'{name}：{rule} 在干净运行里也失败，属于误报'


def test_every_audit_family_has_at_least_one_detection_case(results):
    """A family with no case is an untested detection surface, not a passing one."""
    covered = {c['family'] for name, _, rule in CASES
               for c in results[name]['constraints'] if c['state'] == 'failed' and c['rule'] == rule}
    # historical_regression is driven by the issue ledger rather than baseline
    # rows, and is covered by the regression fixtures in test_convergence_guards.
    assert {'completeness', 'duplication', 'conservation', 'price_bands', 'time_series'} <= covered


@pytest.mark.parametrize('name,mutate', [
    ('整体同比例增长是市场变化，不是水位台阶', level_shift(nrows=2, spus=2, units=20, amount=200)),
    ('只有行数变、SPU 不变属于粒度重复，归 mapping 管', level_shift(nrows=3)),
    ('只有 SPU 变、行数不变同样不足以断定水位', level_shift(spus=3)),
])
def test_level_shift_does_not_fire_on(tmp_path, name, mutate):
    """A rule that fires on ordinary growth would force ownership of nothing."""
    _, index = build(tmp_path, mutate)
    assert 'level_shift' not in failed_rules(index), name


def test_level_shift_is_reported_for_every_month_pair_and_layer(tmp_path):
    """Absent months become unavailable, never a silently missing check."""
    _, index = build(tmp_path, drop_last_month('fingerprint'))
    checks = [c for c in index['constraints'] if c['rule'] == 'level_shift']
    assert {(c['site'], c['scope']['layer'], c['month'], c['state']) for c in checks} == {
        ('DE', 'raw', '2024-02', 'unavailable'), ('DE', 'std', '2024-02', 'unavailable')}


def drop_one_platform(job, rows):
    """Tmall stops reporting this category while the site total is unchanged."""
    if job['family'] == 'category_platform':
        return [r for r in rows if not (r['platform'] == 'Tmall' and r['month_dt'].startswith('2024-02'))]
    return rows


@pytest.fixture(scope='module')
def cn(tmp_path_factory):
    return build(tmp_path_factory.mktemp('cn_clean'), site='cn')


def test_multi_platform_site_computes_the_split_and_stays_clean(cn):
    run, index = cn
    facts = read(run.path / 'panorama/facts-cn.json')
    split = next(f['platform_split'] for f in facts if f['observation'] == 'observed')
    assert sorted(split) == ['Taobao', 'Tmall'], split
    rules = {json.loads(x)['rule'] for x in (run.path / 'panorama/constraint-checks.jsonl').read_text().splitlines()}
    assert 'platform_split_conservation' in rules
    assert failed_rules(index) == set()


def test_single_platform_defect_is_caught_on_a_multi_platform_site(tmp_path, cn):
    """The site-level category total is untouched; only the split reveals it."""
    _, index = build(tmp_path, drop_one_platform, site='cn')
    assert 'platform_split_conservation' in failed_rules(index)
    assert 'platform_split_conservation' not in failed_rules(cn[1])
