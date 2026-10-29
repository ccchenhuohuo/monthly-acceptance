#!/usr/bin/env python3
"""The only query path for an acceptance run.

Every query is checked read-only, sent straight to Doris over the MySQL
protocol, and saved next to its result, so each number in the report can be
traced to the exact SQL that produced it.

Why not the doris MCP tool: it returns an empty data list for any statement
that starts with WITH while still reporting the true row count, and it caps
rows per call. Neither is visible unless you know to look.

    python3 q.py RUN --name us_lights --file ../sql/drill/spu_changes.sql \
        --param SITE=US --param PLATFORM=Amazon --param L1=灯光类 ...
    python3 q.py RUN --name quick --sql "SELECT ..."
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import socket
import sys
import threading
import time
import tomllib
from datetime import datetime
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
TABLE = 'internal.flywheel.monthly_sales_wide'

_WRITE = re.compile(
    r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|LOAD|GRANT|REVOKE|SET|INTO|OUTFILE|'
    r'EXPORT|CALL|KILL|RENAME|INSTALL|UNINSTALL|ADMIN|CANCEL|BACKUP|RESTORE|RECOVER|REFRESH|SYNC|USE)\b',
    re.I)
_LITERAL = re.compile(r"'(?:\\.|''|[^'\\])*'|`(?:``|[^`])*`|\"(?:\\.|\"\"|[^\"\\])*\"")


def strip_comments(sql: str) -> str:
    """Drop whole-line `--` comments; the SQL files keep their explanations there."""
    return '\n'.join(line for line in sql.splitlines() if not line.lstrip().startswith('--')).strip()


def readonly(sql: str) -> str:
    """Return the statement to execute, or raise if it could change anything."""
    body = strip_comments(sql).rstrip().rstrip(';').strip()
    if not body:
        raise ValueError('空查询')
    bare = _LITERAL.sub(' ', body)
    if any(c in bare for c in ("'", '"', '`')):
        raise ValueError('字符串或标识符没有闭合')
    if ';' in bare or '--' in bare or '/*' in bare:
        raise ValueError('只接受单条语句，正文里不能有分号或行内注释')
    if not re.match(r'^\s*(SELECT|WITH|SHOW|DESC|DESCRIBE|EXPLAIN)\b', bare, re.I):
        raise ValueError('只允许 SELECT / WITH / SHOW / DESC / EXPLAIN')
    hit = _WRITE.search(bare)
    if hit:
        raise ValueError(f'含写操作关键字：{hit.group(0)}')
    return body


def render(sql: str, params: dict[str, str], lists: dict[str, list[str]] | None = None) -> str:
    """Fill {{NAME}} placeholders. Scalars are escaped for use inside quotes;
    lists become a quoted, comma separated SQL list."""
    values = {'T': TABLE, **{k: v.replace("'", "''") for k, v in params.items()}}
    for k, items in (lists or {}).items():
        values[k] = ', '.join("'" + x.replace("'", "''") + "'" for x in items) or "''"
    out = re.sub(r'\{\{([A-Z0-9_]+)\}\}', lambda m: values[m.group(1)] if m.group(1) in values else m.group(0), sql)
    missing = sorted(set(re.findall(r'\{\{([A-Z0-9_]+)\}\}', out)))
    if missing:
        raise ValueError('缺少参数：' + ', '.join(missing))
    return out


def credentials() -> dict:
    """Same Doris account as the configured doris MCP server; values never logged."""
    keys = ('DORIS_HOST', 'DORIS_PORT', 'DORIS_USER', 'DORIS_PASSWORD')
    if all(os.environ.get(k) for k in keys):
        env = {k: os.environ[k] for k in keys}
    else:
        env = {}
        claude = Path.home() / '.claude.json'
        if claude.exists():
            env = json.loads(claude.read_text()).get('mcpServers', {}).get('doris', {}).get('env', {})
        if not env:
            codex = Path.home() / '.codex/config.toml'
            if codex.exists():
                env = tomllib.loads(codex.read_text()).get('mcp_servers', {}).get('doris', {}).get('env', {})
        if not all(env.get(k) for k in keys):
            raise SystemExit('找不到 Doris 连接参数：设置 DORIS_HOST/PORT/USER/PASSWORD，或配置 doris MCP')
    return {'host': env['DORIS_HOST'], 'port': int(env['DORIS_PORT']),
            'user': env['DORIS_USER'], 'password': env['DORIS_PASSWORD']}


_local = threading.local()


def connect():
    """One connection per thread, so views can run side by side."""
    conn = getattr(_local, 'conn', None)
    if conn is None or not conn.open:
        import pymysql
        conn = pymysql.connect(**credentials(), charset='utf8mb4', read_timeout=1900, write_timeout=60,
                               connect_timeout=60, autocommit=True)
        # A long aggregation sends nothing for many minutes; keepalive stops the network
        # path from silently dropping the connection while the client keeps waiting.
        sock = conn._sock
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for name, value in (('TCP_KEEPALIVE', 60), ('TCP_KEEPIDLE', 60), ('TCP_KEEPINTVL', 30), ('TCP_KEEPCNT', 5)):
            if hasattr(socket, name):
                sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), value)
        with conn.cursor() as cur:
            # Session timeout only; not a data change, and never reachable from caller SQL.
            cur.execute('SET query_timeout = 1800')
        _local.conn = conn
    return conn


def execute(sql: str) -> tuple[list[str], list[tuple]]:
    statement = readonly(sql)
    with connect().cursor() as cur:
        cur.execute(statement)
        columns = [d[0] for d in cur.description or []]
        rows = list(cur.fetchall())
    return columns, rows


def _cell(v):
    if v is None:
        return ''
    if isinstance(v, float):
        return repr(v)
    return str(v)


def save(run: Path, name: str, sql: str, columns: list[str], rows: list[tuple], seconds: float,
         folder: str = 'queries') -> Path:
    """SQL and result side by side; one line per query in the run's query log."""
    base = run / folder
    base.mkdir(parents=True, exist_ok=True)
    (base / f'{name}.sql').write_text(strip_comments(sql) + '\n')
    path = base / f'{name}.csv'
    with path.open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(columns)
        w.writerows([_cell(v) for v in r] for r in rows)
    log(run, {'name': name, 'folder': folder, 'rows': len(rows), 'seconds': round(seconds, 1)})
    return path


_log_lock = threading.Lock()


def log(run: Path, entry: dict):
    with _log_lock, (run / 'queries.log').open('a') as f:
        f.write(json.dumps({'at': datetime.now().astimezone().isoformat(timespec='seconds'), **entry},
                           ensure_ascii=False) + '\n')


def run_query(run: Path, name: str, sql: str, folder: str = 'queries') -> tuple[list[str], list[tuple], Path]:
    start = time.time()
    columns, rows = execute(sql)
    path = save(run, name, sql, columns, rows, time.time() - start, folder)
    return columns, rows, path


def preview(columns: list[str], rows: list[tuple], limit: int) -> str:
    lines = [' | '.join(columns)]
    for r in rows[:limit]:
        lines.append(' | '.join(_cell(v) for v in r))
    if len(rows) > limit:
        lines.append(f'… 共 {len(rows)} 行，完整结果见 CSV')
    return '\n'.join(lines)


def parse_params(pairs: list[str]) -> dict[str, str]:
    out = {}
    for p in pairs or []:
        k, sep, v = p.partition('=')
        if not sep:
            raise SystemExit(f'参数格式应为 NAME=value：{p}')
        out[k.strip().upper()] = v
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run', type=Path, help='运行目录')
    ap.add_argument('--name', required=True, help='结果文件名（不含扩展名）')
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--sql')
    src.add_argument('--file', type=Path)
    ap.add_argument('--param', action='append', default=[], help='NAME=value，替换 {{NAME}}')
    ap.add_argument('--list', action='append', default=[], help='NAME=a,b,c，替换为带引号的 SQL 列表')
    ap.add_argument('--show', type=int, default=30, help='终端预览行数')
    a = ap.parse_args()
    if not a.run.is_dir():
        raise SystemExit(f'运行目录不存在：{a.run}')
    if not re.fullmatch(r'[\w\-.\u4e00-\u9fff]+', a.name):
        raise SystemExit('name 只能含字母、数字、中文、-、_、.')
    sql = a.sql if a.sql is not None else a.file.read_text()
    lists = {k: [x for x in v.split(',') if x] for k, v in parse_params(a.list).items()}
    sql = render(sql, parse_params(a.param), lists)
    columns, rows, path = run_query(a.run, a.name, sql)
    print(f'{len(rows)} 行 → {path}')
    print(preview(columns, rows, a.show))


if __name__ == '__main__':
    main()
