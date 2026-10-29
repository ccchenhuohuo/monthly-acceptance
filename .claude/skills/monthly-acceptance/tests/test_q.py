import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import q  # noqa: E402

SQL = Path(__file__).resolve().parents[1] / 'sql'


@pytest.mark.parametrize('sql', [
    'SELECT 1',
    '-- 说明\nSELECT a FROM t WHERE b = \'x;y\'',
    'WITH x AS (SELECT 1) SELECT * FROM x',
    'SHOW PROCESSLIST',
    "SELECT UPDATE_TIME FROM information_schema.tables",
    'SELECT * FROM t GROUP BY GROUPING SETS ((a), (a, b))',
    'SELECT 1;',
])
def test_readonly_accepts(sql):
    assert q.readonly(sql)


@pytest.mark.parametrize('sql', [
    'DROP TABLE t',
    'SELECT 1; DROP TABLE t',
    'INSERT INTO t SELECT 1',
    'SELECT * INTO OUTFILE "x" FROM t',
    'SET query_timeout = 1',
    'SELECT 1 -- trailing comment',
    "SELECT 'unclosed",
    '',
])
def test_readonly_rejects(sql):
    with pytest.raises(ValueError):
        q.readonly(sql)


def test_render_quotes_scalars_and_lists():
    out = q.render("SELECT * FROM {{T}} WHERE site = '{{SITE}}' AND b IN ({{BRANDS}})",
                   {'SITE': "c'n"}, {'BRANDS': ['A', "B'C"]})
    assert "site = 'c''n'" in out
    assert "IN ('A', 'B''C')" in out
    assert q.TABLE in out


def test_render_reports_missing_params():
    with pytest.raises(ValueError, match='MONTH'):
        q.render("SELECT '{{MONTH}}'", {})


@pytest.mark.parametrize('path', sorted((SQL / 'views').glob('*.sql')) + sorted((SQL / 'drill').glob('*.sql')),
                         ids=lambda p: p.name)
def test_every_shipped_sql_is_readonly_and_cte_free(path):
    text = q.strip_comments(path.read_text())
    assert not text.lstrip().upper().startswith('WITH'), 'doris MCP returns no rows for WITH queries'
    params = {k: 'x' for k in ('START', 'END', 'RECENT_FROM', 'MONTH', 'PREV', 'YOY', 'RELEASE', 'SEED', 'N', 'FROM', 'LAG',
                               'START_MI', 'END_MI', 'SITE', 'PLATFORM', 'L1', 'L2', 'L3', 'BASE', 'CUR', 'C1',
                               'C2', 'C3', 'C4', 'BRAND', 'FROM_RANK', 'TO_RANK')}
    assert q.readonly(q.render(path.read_text(), params, {'BRANDS': ['a'], 'PIDS': ['1']}))
