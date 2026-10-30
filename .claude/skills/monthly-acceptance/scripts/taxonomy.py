#!/usr/bin/env python3
"""Freeze the category standard for one run.

Pulls the Feishu Base named in config (类目树、类目定义与机判规则、适用标签、
特殊 SPU 命名规则、说明文档) with lark-cli, keeps the raw export, and writes
标准/判定标准.md — the text a reviewer judges tagging against. Rulings agreed
with the vendor after the Base was written (验收/准则裁定.md, frozen into the run
at init) are appended as the last section and override the definitions' wording.

    python3 taxonomy.py RUN
    python3 taxonomy.py RUN --rulings-only    # re-append the rulings to a standard frozen earlier
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from views import load_config  # noqa: E402


def lark(*args: str) -> dict:
    p = subprocess.run(['lark-cli', *args, '--as', 'user'], capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f'lark-cli 失败：{" ".join(args[:2])}\n{p.stderr.strip()[:800]}')
    return json.loads(p.stdout)


def export_table(base: str, table: str, out: Path) -> list[dict]:
    # lark-cli writes the ndjson relative to its working directory
    subprocess.run(['lark-cli', 'base', '+record-list', '--base-token', base, '--table-id', table, '--as', 'user',
                    '--output', out.name, '--overwrite'], cwd=out.parent, capture_output=True, text=True, check=True)
    return [json.loads(line) for line in out.read_text().splitlines() if line.strip()]


def first(v):
    return v[0] if isinstance(v, list) and v else v


RULINGS = '\n## 准则裁定（项目侧，优先于上面的定义字面）\n'


def append_rulings(run: Path) -> int:
    """Append the frozen 准则裁定.md to 判定标准.md, replacing any earlier copy; returns the number of rulings."""
    src, std = run / 'inputs' / '准则裁定.md', run / 'inputs' / '标准' / '判定标准.md'
    if not src.exists() or not std.exists():
        return 0
    body = src.read_text().split('\n', 1)[-1].strip()   # drop the file's own title
    std.write_text(std.read_text().split(RULINGS)[0].rstrip('\n') + '\n' + RULINGS + '\n' + body + '\n')
    return sum(1 for line in body.splitlines() if line.startswith('| K'))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run', type=Path)
    ap.add_argument('--rulings-only', action='store_true', help='只把准则裁定重新追加到已冻结的判定标准')
    a = ap.parse_args()
    run = a.run.resolve()
    if a.rulings_only:
        print(f'追加 {append_rulings(run)} 条准则裁定 → {run / "inputs" / "标准" / "判定标准.md"}')
        return
    cfg = load_config(run / 'inputs')['品类标签体系']
    out = run / 'inputs' / '标准'
    out.mkdir(parents=True, exist_ok=True)
    blocks = lark('base', '+base-block-list', '--base-token', cfg['base_token'])['data']['blocks']
    rev = {b['id']: b.get('rev') for b in blocks}
    tree = export_table(cfg['base_token'], cfg['数据表'], out / 'taxonomy.ndjson')
    naming = export_table(cfg['base_token'], cfg['特殊SPU命名规则'], out / 'spu_naming.ndjson')
    doc = lark('docs', '+fetch', '--doc', cfg['说明文档'], '--doc-format', 'markdown')
    doc_text = doc.get('data', {}).get('document', {}).get('content') or doc.get('data', {}).get('content') or ''
    if not doc_text:
        def find(o):
            if isinstance(o, dict):
                for v in o.values():
                    r = find(v)
                    if r:
                        return r
            elif isinstance(o, list):
                for v in o:
                    r = find(v)
                    if r:
                        return r
            elif isinstance(o, str) and len(o) > 500:
                return o
            return None
        doc_text = find(doc) or ''
    (out / '说明文档.md').write_text(doc_text)

    tree.sort(key=lambda r: (str(first(r.get('stdcategory1'))), str(r.get('stdcategory2')), str(r.get('stdcategory3'))))
    lines = [f"# 类目判定标准（{cfg['名称']}）", '',
             f"来源：{cfg['链接']}；数据表 rev {rev.get(cfg['数据表'])}；"
             f"抓取于 {datetime.now().astimezone().isoformat(timespec='minutes')}；共 {len(tree)} 个叶子。", '',
             '判定时以“类目定义”为准，“机判规则”是多词共现时的裁决顺序；定义里写明“不纳入统计”的商品应落在“其他”。', '']
    current = None
    for r in tree:
        l1 = first(r.get('stdcategory1'))
        if l1 != current:
            lines += ['', f'## {l1}', '', '| 二级 | 三级 | 适用标签 | 类目定义（含机判规则） | 备注 |', '|---|---|---|---|---|']
            current = l1
        cells = [r.get('stdcategory2') or '', r.get('stdcategory3') or '',
                 (r.get('标签') or '').replace('\n', '；'), (r.get('类目定义') or '').replace('\n', ' '),
                 (r.get('备注') or '').replace('\n', ' ')]
        lines.append('| ' + ' | '.join(c.replace('|', '/') for c in cells) + ' |')
    if naming:
        lines += ['', '## 特殊 SPU 命名规则', '', '| 一级 | 二级 | SPU 命名规则 | 沿用/新建 | 备注 |', '|---|---|---|---|---|']
        for r in naming:
            cells = [r.get('一级类目') or '', r.get('二级类目') or '', r.get('SPU命名规则') or '',
                     str(first(r.get('沿用or新建')) or ''), r.get('备注') or '']
            lines.append('| ' + ' | '.join(str(c).replace('\n', ' ').replace('|', '/') for c in cells) + ' |')
    (out / '判定标准.md').write_text('\n'.join(lines) + '\n')
    rulings = append_rulings(run)
    (out / 'manifest.json').write_text(json.dumps({
        'fetched_at': datetime.now().astimezone().isoformat(timespec='seconds'), 'base_token': cfg['base_token'],
        'revisions': rev, 'leaves': len(tree), 'naming_rules': len(naming), 'rulings': rulings},
        ensure_ascii=False, indent=2) + '\n')
    print(f"{len(tree)} 个叶子、{len(naming)} 条命名规则、{rulings} 条准则裁定 → {out / '判定标准.md'}")


if __name__ == '__main__':
    main()
