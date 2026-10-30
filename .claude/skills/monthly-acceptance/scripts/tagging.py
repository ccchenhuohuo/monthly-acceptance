#!/usr/bin/env python3
"""Tally the tagging judgments into one comparable summary.

    python3 tagging.py RUN

Reads every RUN/queries/打标判定_<站点>_<平台>.csv written by the judges, matches each row to the
sample in RUN/views/13_tag_sample.csv, and writes RUN/queries/打标判定_汇总.md: misfit rates by
sample group, unclear and standard-question counts, unjudged sample rows, and the largest misfits.
The counting rules are in references/tagging.md; this script only applies them the same way to
every country × platform.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

KEYS = ['l1', 'l2', 'l3', 'product_id']
COLUMNS = KEYS + ['verdict', 'rule_issue', 'target', 'basis', 'note']
VERDICTS = ('符合', '不符', '存疑')
GROUPS = ('头部', '金额头部', '贵价', '残差叶', '随机')


def group_of(rk_units: int, l3: str, rk_amount: float | None = None, price_ratio: float | None = None) -> str:
    """头部 = the leaf's top 3 by units; 金额头部 = top 3 by amount; 贵价 = priced ≥10× the leaf median;
    残差叶 = the rest of a '-其他' leaf's sample; 随机 = the shuffled picks. Samples drawn before 0.3.0
    carry no amount rank or price ratio and fall into the first three groups only."""
    if rk_units <= 3:
        return '头部'
    if rk_amount is not None and rk_amount == rk_amount and rk_amount <= 3:
        return '金额头部'
    if price_ratio is not None and price_ratio == price_ratio and price_ratio >= 10:
        return '贵价'
    return '残差叶' if l3.endswith('-其他') else '随机'


def load(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def tally(sample: pd.DataFrame, judged: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Judged rows joined to their sample rows, plus the problems that make a row uncountable."""
    problems = []
    missing_cols = [c for c in COLUMNS if c not in judged.columns]
    if missing_cols:
        return judged.iloc[0:0], [f'缺列 {"、".join(missing_cols)}']
    judged = judged.assign(verdict=judged.verdict.str.strip(), rule_issue=judged.rule_issue.str.strip() == '是')
    bad = judged[~judged.verdict.isin(VERDICTS)]
    if len(bad):
        problems.append(f'{len(bad)} 行结论不是 符合 / 不符 / 存疑（如 {bad.product_id.iloc[0]}），未计入')
    dup = judged[judged.duplicated(KEYS, keep=False)]
    if len(dup):
        problems.append(f'{len(dup)} 行重复判了同一样本（如 {dup.product_id.iloc[0]}），只计第一条')
    judged = judged[judged.verdict.isin(VERDICTS)].drop_duplicates(KEYS)
    extra = [c for c in ('rk_amount', 'price_ratio', 'amount') if c in sample.columns]
    s = sample[KEYS + ['rk_units', 'units'] + extra]
    m = judged.merge(s, on=KEYS, how='left', indicator=True)
    orphan = m[m._merge == 'left_only']
    if len(orphan):
        problems.append(f'{len(orphan)} 行不在样本里（l1、l2、l3、product_id 要照抄样本，如 {orphan.product_id.iloc[0]}），未计入')
    m = m[m._merge == 'both'].drop(columns='_merge')
    num = lambda col: pd.to_numeric(m[col], errors='coerce') if col in m else pd.Series([None] * len(m), index=m.index)
    m['group'] = [group_of(int(r), l3, ra, pr) for r, l3, ra, pr in zip(m.rk_units, m.l3, num('rk_amount'), num('price_ratio'))]
    m['units'] = m.units.astype(float)
    m['amount'] = num('amount')
    return m, problems


def systematic(m: pd.DataFrame, leaf_amount: dict) -> list[list]:
    """Leaves where the sample shows a pattern rather than a stray error: two or more misfits among the units or
    amount top 3, or misfits worth ≥10% of the leaf's amount. Each one goes into the report, sized for the whole leaf;
    a pattern that stays in the judgment files has not been reported (2026-09: Mexico's monitor leaf)."""
    rows = []
    for (l1, l2, l3), g in m[m.verdict == '不符'].groupby(['l1', 'l2', 'l3']):
        head = int(g.group.isin(['头部', '金额头部']).sum())
        la = leaf_amount.get((l1, l2, l3))
        amt = g.amount.sum() if 'amount' in g else float('nan')
        s = amt / la if la and amt == amt else None
        if head >= 2 or (s is not None and s >= 0.10):
            rows.append((s or 0, [f'{l2} > {l3}', head, f'{s * 100:.1f}%' if s is not None else '—',
                                  '、'.join(g.sort_values('amount', ascending=False).product_id.head(3))]))
    rows.sort(key=lambda x: -x[0])
    return [x[1] for x in rows]


def rate(x: pd.DataFrame) -> str:
    n, bad = len(x), int((x.verdict == '不符').sum())
    return f'{bad} / {n}（{bad / n * 100:.1f}%）' if n else '—'


def leaf_amounts(run: Path) -> dict:
    """Leaf amounts of the sampled month from 02_std_grid, keyed (site, platform) -> {(l1, l2, l3): amount}."""
    grid = run / 'views' / '02_std_grid.csv'
    if not grid.exists():
        return {}
    import json
    month = json.loads((run / 'run.json').read_text())['month']
    g = pd.read_csv(grid, dtype=str, keep_default_na=False)
    g = g[(g.m == month) & (g.lvl == '3')]
    out = {}
    for r in g.to_dict('records'):
        out.setdefault((r['site'], r['platform']), {})[(r['l1'], r['l2'], r['l3'])] = float(r['amount'] or 0)
    return out


def summarize(run: Path) -> str:
    sample = load(run / 'views' / '13_tag_sample.csv')
    leaves = leaf_amounts(run)
    files = sorted((run / 'queries').glob('打标判定_*_*.csv'))
    if not files:
        raise SystemExit('queries/ 下没有 打标判定_<站点>_<平台>.csv')
    rows, misfits, issues, notes, pricey, patterns = [], [], [], [], [], []
    for f in files:
        site, platform = f.stem.split('_', 1)[1].split('_', 1)
        s = sample[(sample.site == site) & (sample.platform == platform)]
        m, problems = tally(s, load(f))
        notes += [f'- {f.name}：{p}' for p in problems]
        unjudged = len(s) - len(m)
        rows.append([f'{site} / {platform}'] + [rate(m[m.group == g]) for g in GROUPS] +
                    [int((m.verdict == '存疑').sum()), int(m.rule_issue.sum()), unjudged])
        for r in m[m.verdict == '不符'].to_dict('records'):
            misfits.append((r['units'], [f'{site} / {platform}', r['product_id'], r['group'], f"{r['l2']} > {r['l3']}",
                                         r['target'], f"{r['units']:,.0f}", r['note'][:50]]))
        for r in m[(m.verdict != '符合') & m.group.isin(['金额头部', '贵价'])].sort_values('amount', ascending=False).to_dict('records'):
            pricey.append([f'{site} / {platform}', r['product_id'], r['group'], r['verdict'], f"{r['l2']} > {r['l3']}",
                           r['target'], f"{r['units']:,.0f}", f"{r['amount']:,.0f}" if r['amount'] == r['amount'] else '—', r['note'][:50]])
        patterns += [[f'{site} / {platform}'] + r for r in systematic(m, leaves.get((site, platform), {}))]
        for (l2, l3), g in m[m.rule_issue].groupby(['l2', 'l3']):
            issues.append((g.units.sum(), [f'{site} / {platform}', f'{l2} > {l3}', len(g), int((g.verdict == '不符').sum()),
                                           f'{g.units.sum():,.0f}', g.note.iloc[0][:50]]))
    misfits.sort(key=lambda x: -x[0])
    issues.sort(key=lambda x: -x[0])

    def table(headers, body):
        clean = lambda v: str(v).replace('|', '/').replace('\n', ' ')
        lines = ['| ' + ' | '.join(headers) + ' |', '|' + '---|' * len(headers)]
        return '\n'.join(lines + ['| ' + ' | '.join(clean(v) for v in r) + ' |' for r in body]) + '\n' if body else '（无）\n'

    return ('# 打标判定汇总\n\n'
            '不符率 = 不符 / 判定（含存疑）。准则问题是另一列：现叶子按定义字面说得通、问题在准则的，结论记“符合”，不计不符。'
            '头部 = 各叶件数前 3；金额头部 = 金额前 3（不在件数前 3 里的）；贵价 = 单价 ≥ 叶子中位数 10 倍的金额前 3；'
            '残差叶 = “-其他”叶的其余样本；随机 = 其他叶打散抽的 2 个。未判 = 样本里没有判定结论的行。\n\n'
            + table(['国家×平台', '头部：不符', '金额头部：不符', '贵价：不符', '残差叶：不符', '随机：不符', '存疑', '准则问题', '未判'], rows)
            + '\n## 成片不符的叶子（必须写进报告，并按整叶量化）\n\n'
            '头部（件数或金额前 3）有 2 条以上不符，或不符商品合计占叶子当月金额 ≥10%。用 09 摘要的叶子构成、07 的小类前五品牌或'
            ' 04 的单品对小类表估整叶占比。\n\n'
            + table(['国家×平台', '叶子', '头部不符', '不符商品占叶子额', '例'], patterns)
            + '\n## 件数最大的不符（主 agent 复核从这里开始）\n\n'
            + table(['国家×平台', 'product_id', '样本组', '现叶子', '应归', '件', '说明'], [x[1] for x in misfits[:30]])
            + '\n## 金额头部与贵价组的不符和存疑（件数少、金额大，按站内金额排）\n\n'
            + table(['国家×平台', 'product_id', '样本组', '结论', '现叶子', '应归', '件', '额（本币）', '说明'], pricey[:30])
            + '\n## 准则问题按叶子归并\n\n'
            + table(['国家×平台', '叶子', '行', '其中不符', '件', '示例说明'], [x[1] for x in issues[:30]])
            + ('\n## 没计入的行\n\n' + '\n'.join(notes) + '\n' if notes else ''))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run', type=Path)
    a = ap.parse_args()
    out = a.run.resolve() / 'queries' / '打标判定_汇总.md'
    out.write_text(summarize(a.run.resolve()))
    print(out)


if __name__ == '__main__':
    main()
