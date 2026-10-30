#!/usr/bin/env python3
"""Open a run and compute every view the acceptance reads.

    python3 views.py init --month 2026-09        # 新建运行目录，冻结配置，记下交付身份与现役发布
    python3 views.py run RUN [--only 02 05]      # 跑全部视图（全历史、全部站点），结果写入 RUN/views/
    python3 views.py check RUN                   # 收尾复核：运行期间源表或现役发布有没有变

Views are full-population aggregates; nothing here ranks globally or keeps a
Top N across sites. Looped views run once per site×platform and are concatenated.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import q  # noqa: E402
from ledger import caliber  # noqa: E402

SKILL = q.SKILL
SQL = SKILL / 'sql' / 'views'


def shift(month: str, delta: int) -> str:
    y, m = map(int, month.split('-'))
    n = y * 12 + m - 1 + delta
    return f'{n // 12:04d}-{n % 12 + 1:02d}'


def index(month: str) -> int:
    y, m = map(int, month.split('-'))
    return y * 12 + m


def load_config(base: Path) -> dict:
    import yaml
    return yaml.safe_load((base / 'config.yaml').read_text())


def site_platforms(config: dict) -> list[tuple[str, str, str]]:
    return [(site, platform, str(start)[:7]) for site, platforms in config['站点'].items()
            for platform, start in platforms.items()]


def latest_release(run: Path) -> dict:
    cols, rows, _ = q.run_query(run, '00_release', (SQL / '00_release.sql').read_text(), folder='views')
    published = [dict(zip(cols, r)) for r in rows if r[cols.index('status')] == 'published']
    if not published:
        raise SystemExit('治理层没有 published 的发布代次')
    return {k: str(v) for k, v in published[0].items()}


def delivery(run: Path, name: str) -> dict:
    cols, rows, _ = q.run_query(run, name, (SQL / '00_delivery.sql').read_text(), folder='views')
    if not rows:
        raise SystemExit('information_schema 里找不到源表 monthly_sales_wide')
    return {k: str(v) for k, v in zip(cols, rows[0])}


def skill_version() -> dict:
    def git(*args):
        return subprocess.run(['git', '-C', str(SKILL), *args], capture_output=True, text=True).stdout.strip()
    return {'commit': git('rev-parse', 'HEAD'), 'branch': git('rev-parse', '--abbrev-ref', 'HEAD'),
            'dirty': bool(git('status', '--porcelain', '--', '.'))}


PROJECT_FILES = ('config.yaml', '项目范围.md', 'scope.json', '已知缺陷台账.json', '服务商沟通台账.json', '准则裁定.md')


def previous_run(base: Path) -> str | None:
    """The latest earlier run of this skill generation that has a report; the one step 6 reconciles against."""
    runs = []
    for meta in base.glob('runs/*/*/run.json'):
        try:
            m = json.loads(meta.read_text())
        except ValueError:
            continue
        if 'site_platforms' in m and (meta.parent / '报告.md').exists():
            runs.append((m.get('created_at', ''), meta.parent))
    return str(max(runs)[1].relative_to(base)) if runs else None


def ledger_pids(inputs: Path) -> list[str]:
    """Products to trace every run: known-defect objects plus the objects of vendor-ledger issues."""
    pids = set()
    for name, key in (('已知缺陷台账.json', None), ('服务商沟通台账.json', 'issues')):
        p = inputs / name
        if p.exists():
            items = json.loads(p.read_text())
            for item in items.get(key, []) if key else items:
                pids |= {o['product_id'] for o in item.get('objects', []) if o.get('product_id')}
    return sorted(pids)


def init(month: str, base: Path) -> Path:
    config = load_config(base)
    stamp = datetime.now().strftime('%Y%m%d-%H%M')
    last = previous_run(base)
    run = base / 'runs' / month / stamp
    run.mkdir(parents=True, exist_ok=False)
    inputs = run / 'inputs'
    inputs.mkdir()
    for name in PROJECT_FILES:
        if (base / name).exists():
            shutil.copy2(base / name, inputs / name)
    if (inputs / '服务商沟通台账.json').exists():
        (inputs / '服务商口径.md').write_text(caliber(json.loads((inputs / '服务商沟通台账.json').read_text())))
    internal = base / '内部数据' / month
    if internal.is_dir() and any(internal.iterdir()):
        shutil.copytree(internal, inputs / '内部数据')
    meta = {
        'month': month,
        'created_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'skill': skill_version(),
        'site_platforms': [{'site': s, 'platform': p, 'start': st} for s, p, st in site_platforms(config)],
        'previous_run': last,
        'delivery_start': delivery(run, '00_delivery_start'),
        'release': latest_release(run),
    }
    (run / 'run.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n')
    (run / 'notes.md').write_text(f'# {month} 验收工作记录\n\n按模块记下现象、已做检查、排除了什么、初步定性；对照历史前不读台账。\n')
    return run


def taxonomy_l1(path: Path) -> list[str]:
    """Level-1 names of the frozen standard (empty when taxonomy.py did not freeze one)."""
    if not path.exists():
        return []
    names = set()
    for line in path.read_text().splitlines():
        v = json.loads(line).get('stdcategory1') if line.strip() else None
        names |= set(v if isinstance(v, list) else [v] if v else [])
    return sorted(n for n in names if n and n != '其他')


def plan(run: Path) -> list[dict]:
    meta = json.loads((run / 'run.json').read_text())
    config = load_config(run / 'inputs')
    end = meta['month']
    start = min(sp['start'] for sp in meta['site_platforms'])
    recent = shift(end, -12)
    brands = list(config.get('本品品牌', [])) + list(config.get('本品杂音品牌', []))
    common = {'START': start, 'END': end, 'RECENT_FROM': recent, 'MONTH': end, 'PREV': shift(end, -1), 'YOY': shift(end, -12),
              'Q_FROM': shift(end, -2), 'YOY_Q_FROM': shift(end, -14),
              'RELEASE': meta['release']['release_id'], 'SEED': f'acceptance-{end}'}
    pids = ledger_pids(run / 'inputs')
    # Every query scans the whole table whatever its site filter, so each view runs
    # once over all sites; only the churn view runs twice, once per comparison lag.
    single = ['01_fingerprint', '02_std_grid', '04_price_bands', '05_price_jumps', '05_price_jump_counts',
              '06_spu_shape', '07_fixed_cohort', '08_brands', '10_tag_values', '11_tag_flow',
              '13_tag_sample', '14_own_brand', '16_gov_coverage', '17_gov_mapping', '18_gov_tags',
              '20_leaf_paths', '21_spu_concentration']
    jobs = [{'view': v, 'parts': [{}]} for v in single]
    # Month-grained views with high-cardinality keys run one month at a time: the
    # network path to Doris drops connections on queries that run ten minutes or more,
    # and a single month prunes to one partition.
    all_months = [start]
    while all_months[-1] < end:
        all_months.append(shift(all_months[-1], 1))
    for v in ('09_platform_paths', '15_gov_recon'):
        jobs.append({'view': v, 'parts': [{'START': mm, 'END': mm, 'CHUNK': mm} for mm in all_months]})
    # The leaf-level views aggregate every SPU over three months; one query for all sites runs past the
    # ten-minute limit of the network path, so they run one site × platform at a time, and Tmall (six times
    # the rows of Amazon US) one level-1 category at a time. A leaf never spans two parts, so nothing is cut.
    l1s = taxonomy_l1(run / 'inputs' / '标准' / 'taxonomy.ndjson')
    for v in ('22_leaf_items', '23_brand_map', '24_leaf_brands'):
        parts = []
        for sp in meta['site_platforms']:
            base = {'SITE': sp['site'], 'PLATFORM': sp['platform'], 'L1': '%', 'CHUNK': f"{sp['site']}_{sp['platform']}"}
            if v != '23_brand_map' and sp['platform'] == 'Tmall' and l1s:
                parts += [{**base, 'L1': l1, 'CHUNK': f"{base['CHUNK']}_{l1}"} for l1 in l1s]
            else:
                parts.append(base)
        jobs.append({'view': v, 'parts': parts})
    jobs.append({'view': '03_churn', 'parts': [
        {'LAG': str(lag), 'FROM': shift(start, -lag), 'START_MI': str(index(start)), 'END_MI': str(index(end))}
        for lag in (1, 12)]})
    jobs.append({'view': '12_tag_drift', 'parts': [{'FROM': shift(recent, -1)}]})
    if pids:
        jobs.append({'view': '19_known_defects', 'parts': [{}], 'lists': {'PIDS': pids}})
    for j in jobs:
        j['params'] = {**common, 'N': '200'}
        j.setdefault('lists', {})['BRANDS'] = brands
        j['lists'].setdefault('PIDS', pids or ['__none__'])
    return sorted(jobs, key=lambda j: j['view'])


def run_job(run: Path, job: dict) -> str:
    template = (SQL / f"{job['view']}.sql").read_text()
    header, rows, started = None, [], time.time()
    for part in job['parts']:
        suffix = f"lag{part['LAG']}" if 'LAG' in part else part.get('CHUNK', '')
        name = job['view'] + (f'__{suffix}' if suffix else '')
        sql = q.render(template, {**job['params'], **part}, job['lists'])
        done = run / 'views' / 'parts' / f'{name}.csv'
        if suffix and done.exists() and done.with_suffix('.sql').read_text().strip() == q.strip_comments(sql).strip():
            # Resume: a part already fetched with the same SQL in this run is reused.
            with done.open(newline='') as f:
                reader = csv.reader(f)
                cols, part_rows = next(reader), [tuple(r) for r in reader]
            if 'LAG' in part:
                cols, part_rows = cols + ['lag'], [r + (int(part['LAG']),) for r in part_rows]
            header = header or cols
            rows.extend(part_rows)
            continue
        for attempt in (1, 2):
            try:
                cols, part_rows, _ = q.run_query(run, name, sql, folder='views/parts' if suffix else 'views')
                break
            except Exception as exc:  # Doris returns resource errors as generic OperationalError
                q.log(run, {'name': name, 'error': str(exc)[:500], 'attempt': attempt})
                q._local.conn = None
                if attempt == 2:
                    raise
        if 'LAG' in part:
            cols, part_rows = cols + ['lag'], [r + (int(part['LAG']),) for r in part_rows]
        header = header or cols
        rows.extend(part_rows)
    if suffix:
        with (run / 'views' / f"{job['view']}.csv").open('w', newline='') as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows([q._cell(v) for v in r] for r in rows)
    return f"{job['view']}: {len(rows)} 行，{time.time() - started:.0f}s"


def run_views(run: Path, only: list[str] | None, workers: int):
    """Failures are recorded in views/errors.json and do not stop the other views;
    the module that needs a failed view is reported as not checked."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    (run / 'views').mkdir(exist_ok=True)
    jobs = [j for j in plan(run) if not only or any(j['view'].startswith(o) for o in only)]
    # Longest first, so the pool does not end on one slow view.
    order = ['03_churn', '07_fixed_cohort', '06_spu_shape', '02_std_grid', '04_price_bands', '08_brands', '09_platform_paths']
    jobs.sort(key=lambda j: order.index(j['view']) if j['view'] in order else len(order))
    errors_path = run / 'views' / 'errors.json'
    errors = json.loads(errors_path.read_text()) if errors_path.exists() else {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_job, run, j): j['view'] for j in jobs}
        for f in as_completed(futures):
            view = futures[f]
            try:
                print(f.result(), flush=True)
                errors.pop(view, None)
            except Exception as exc:
                errors[view] = str(exc)[:500]
                print(f'{view}: 失败 —— {errors[view]}', flush=True)
    errors_path.write_text(json.dumps(errors, ensure_ascii=False, indent=2) + '\n')
    if errors:
        print(f'{len(errors)} 个视图失败，见 views/errors.json；可用 --only 重跑')


def check(run: Path):
    meta = json.loads((run / 'run.json').read_text())
    now = delivery(run, '00_delivery_end')
    release = latest_release(run)
    changes = []
    if now['update_time'] != meta['delivery_start']['update_time'] or now['create_time'] != meta['delivery_start']['create_time']:
        changes.append(f"源表在运行期间被重建或更新：{meta['delivery_start']['update_time']} → {now['update_time']}")
    if release['release_id'] != meta['release']['release_id']:
        changes.append(f"现役发布已变：{meta['release']['release_id']} → {release['release_id']}")
    meta['delivery_end'] = now
    meta['release_end'] = release
    meta['consistency'] = changes or ['运行期间源表与现役发布未变']
    (run / 'run.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n')
    print('\n'.join(meta['consistency']))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('init')
    p.add_argument('--month', required=True)
    p.add_argument('--base', type=Path, default=Path('验收'))
    p = sub.add_parser('run')
    p.add_argument('run', type=Path)
    p.add_argument('--only', nargs='*')
    p.add_argument('--workers', type=int, default=1, help='同时跑的视图数。集群与线上 flywheel MCP 共用、工作组内存上限约 17GB，并行容易被杀，默认串行')
    p = sub.add_parser('check')
    p.add_argument('run', type=Path)
    a = ap.parse_args()
    if a.cmd == 'init':
        print(init(a.month, a.base.resolve()))
    elif a.cmd == 'run':
        run_views(a.run.resolve(), a.only, a.workers)
    else:
        check(a.run.resolve())


if __name__ == '__main__':
    main()
