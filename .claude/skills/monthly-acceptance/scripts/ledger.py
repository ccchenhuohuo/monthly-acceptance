#!/usr/bin/env python3
"""Render the vendor-communication ledger for reading.

    python3 ledger.py render [--base 验收]

验收/服务商沟通台账.json is the source of truth; 服务商沟通台账.md is generated from it together
with the newest frozen 客户反馈清单 (a run's inputs/服务商/需求表/, else 参考/服务商需求表-*/).
Edit the JSON — new issues, statuses, re-checks — and render again; never edit the md by hand.

Ledger layout: statements (S, how the data is made), agreements (K, scope / delivery / standard
rulings), issues (Q, a problem and the vendor's reply, with objects to trace and one re-check per run).

Statements that carry `caliber` and `reading` are the vendor's data-generation calibers. They stay on
the project side; views.py init freezes them into RUN/inputs/服务商口径.md, read at the start of a run.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

OPEN = ('待回复', '承诺修复', '服务商称已修复', '未兑现', '未闭环')
CLOSED = ('已说明', '已修复', '我方接受', '已关闭')


def cell(s, n: int | None = None) -> str:
    s = re.sub(r'https?://\S+', '<链接>', str(s or ''))
    s = re.sub(r'\s*\n\s*', '；', s.strip()).replace('|', '/')
    return s if n is None or len(s) <= n else s[:n] + '…'


def table(headers: list[str], rows: list[list]) -> str:
    if not rows:
        return '（无）\n'
    out = ['| ' + ' | '.join(headers) + ' |', '|' + '---|' * len(headers)]
    return '\n'.join(out + ['| ' + ' | '.join(cell(c) for c in r) + ' |' for r in rows]) + '\n'


def feedback_csv(base: Path) -> Path | None:
    found = list(base.glob('runs/*/*/inputs/服务商/需求表/*客户反馈清单.csv')) + list(base.glob('参考/服务商需求表-*/*客户反馈清单.csv'))
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def feedback_rows(path: Path) -> list[list]:
    with path.open(newline='') as f:
        rows = list(csv.reader(f))
    head = rows[0]
    col = {name: head.index(name) for name in ('反馈编号', '客户反馈时间', '客户反馈内容', '平台范围', '反馈细分类', 'FW反馈', '需求进度')
           if name in head}
    get = lambda r, k: r[col[k]] if k in col and col[k] < len(r) else ''
    return [[get(r, '反馈编号'), get(r, '客户反馈时间'), cell(get(r, '平台范围'), 40), cell(get(r, '反馈细分类'), 20),
             cell(get(r, '客户反馈内容'), 170), cell(get(r, 'FW反馈'), 280) or '（空）', cell(get(r, '需求进度'), 20)]
            for r in rows[1:] if get(r, '反馈编号').strip()]


def checks(item: dict) -> str:
    return '；'.join(f"{c['run'].split('/')[-1]}：{c['result']}" for c in item.get('checks', [])) or '—'


def caliber(led: dict) -> str:
    rows = [[s['id'], s['effective'], s['caliber'], s['reading'], s['source']] for s in led['statements'] if s.get('reading')]
    return ('# 服务商口径（开工读）\n\n服务商说过的、长期影响判读的数据生成方式，由 views.py init 从 `服务商沟通台账.json` 的声明条目冻结。'
            '具体问题与修复承诺不在这里，第 6 步才对照。\n\n'
            + table(['编号', '生效期 / 范围', '口径', '判读含义', '出处'], rows))


def render(base: Path) -> str:
    led = json.loads((base / '服务商沟通台账.json').read_text())
    parts = [f"# {led['title']}\n", '\n'.join(led.get('about', [])) + '\n',
             '## 一、口径与数据来源声明（服务商说法，按生效期）\n',
             table(['编号', '生效期 / 范围', '服务商说法（原话要点）', '出处', '对验收的含义'],
                   [[s['id'], s['effective'], s['said'], s['source'], s['meaning']] for s in led['statements']]),
             '\n## 二、范围、交付与口径约定（双方确认过的决定）\n',
             table(['编号', '日期', '约定', '谁定的', '出处', '落在哪里', '对验收的含义'],
                   [[k['id'], k['date'], k['content'], k['decided_by'], k['source'], k.get('lands_in', '—'), k['meaning']]
                    for k in led['agreements']]),
             '\n## 三、问题与回复对照\n',
             f"### 3.1 群聊与反馈函里的问题 → 回复（未闭环 {sum(1 for q in led['issues'] if q['status'] not in CLOSED)} 条）\n",
             table(['编号', '提出', '站点 · 对象', '问题', '服务商回复', '处置', '状态', '复验', '关联'],
                   [[q['id'], q['raised'], q['scope'], q['issue'], q['reply'], q['action'], q['status'], checks(q),
                     q.get('refs', '—')] for q in led['issues']])]
    src = feedback_csv(base)
    if src:
        parts += [f"\n### 3.2 服务商《客户反馈清单》全量\n\n服务商原文压缩：链接换成“<链接>”，换行换成“；”，长回复截断。“进度”是服务商自己的标注。"
                  f"来源 `{src.relative_to(base)}`。\n",
                  table(['#', '反馈日期', '平台', '细分类', '我方反馈', '服务商回复', '进度'], feedback_rows(src))]
    if led.get('notes'):
        parts += ['\n## 四、备注\n', '\n'.join(f'- {n}' for n in led['notes']) + '\n']
    return '\n'.join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('render')
    p.add_argument('--base', type=Path, default=Path('验收'))
    a = ap.parse_args()
    base = a.base.resolve()
    out = base / '服务商沟通台账.md'
    out.write_text(render(base))
    print(out)


if __name__ == '__main__':
    main()
