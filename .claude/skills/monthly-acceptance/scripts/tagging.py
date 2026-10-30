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
GROUPS = ('头部', '残差叶', '随机')


def group_of(rk_units: int, l3: str) -> str:
    """头部 = the leaf's top 3 by units; 残差叶 = the rest of a '-其他' leaf's sample; 随机 = the shuffled picks."""
    if rk_units <= 3:
        return '头部'
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
    s = sample[KEYS + ['rk_units', 'units']]
    m = judged.merge(s, on=KEYS, how='left', indicator=True)
    orphan = m[m._merge == 'left_only']
    if len(orphan):
        problems.append(f'{len(orphan)} 行不在样本里（l1、l2、l3、product_id 要照抄样本，如 {orphan.product_id.iloc[0]}），未计入')
    m = m[m._merge == 'both'].drop(columns='_merge')
    m['group'] = [group_of(int(r), l3) for r, l3 in zip(m.rk_units, m.l3)]
    m['units'] = m.units.astype(float)
    return m, problems


def rate(x: pd.DataFrame) -> str:
    n, bad = len(x), int((x.verdict == '不符').sum())
    return f'{bad} / {n}（{bad / n * 100:.1f}%）' if n else '—'


def summarize(run: Path) -> str:
    sample = load(run / 'views' / '13_tag_sample.csv')
    files = sorted((run / 'queries').glob('打标判定_*_*.csv'))
    if not files:
        raise SystemExit('queries/ 下没有 打标判定_<站点>_<平台>.csv')
    rows, misfits, issues, notes = [], [], [], []
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
            '头部 = 各叶件数前 3；残差叶 = “-其他”叶的其余样本；随机 = 其他叶打散抽的 2 个。未判 = 样本里没有判定结论的行。\n\n'
            + table(['国家×平台', '头部：不符', '残差叶：不符', '随机：不符', '存疑', '准则问题', '未判'], rows)
            + '\n## 件数最大的不符（主 agent 复核从这里开始）\n\n'
            + table(['国家×平台', 'product_id', '样本组', '现叶子', '应归', '件', '说明'], [x[1] for x in misfits[:30]])
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
