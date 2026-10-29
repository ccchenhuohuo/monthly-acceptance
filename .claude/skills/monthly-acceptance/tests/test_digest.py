import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import digest  # noqa: E402


def fp(nrows, spus, units, amount):
    return {'nrows': nrows, 'spus': spus, 'units': units, 'amount': amount}


def test_level_shift_flags_identity_jump_without_value():
    assert digest.level_shift(fp(100, 100, 1000, 1000), fp(300, 250, 1100, 1050)) == '台阶↑'


def test_level_shift_flags_identity_drop():
    assert digest.level_shift(fp(300, 300, 1000, 1000), fp(150, 160, 980, 990)) == '台阶↓'


def test_level_shift_flags_estimation_swing():
    assert digest.level_shift(fp(100, 100, 1000, 1000), fp(102, 98, 600, 640)) == '量动身份不动'


def test_level_shift_ignores_ordinary_growth():
    assert digest.level_shift(fp(100, 100, 1000, 1000), fp(120, 115, 1150, 1180)) is None


def test_ratio_and_pct_handle_missing_and_zero_base():
    assert digest.ratio(5, 0) is None
    assert digest.ratio(None, 3) is None
    assert digest.pct(digest.ratio(150, 100)) == '+50.0%'
    assert digest.pct(None) == '—'


def test_months_and_shift_cross_year():
    assert digest.shift('2026-01', -1) == '2025-12'
    assert digest.shift('2025-12', 13) == '2027-01'
    assert digest.months_between('2025-11', '2026-02') == ['2025-11', '2025-12', '2026-01', '2026-02']


def test_history_segments_merge_contiguous_months():
    rows = [{'m': '2026-01', 'l1': 'A', 'l2': 'B', 'l3': 'C', 'units': 1},
            {'m': '2026-02', 'l1': 'A', 'l2': 'B', 'l3': 'C', 'units': 2},
            {'m': '2026-04', 'l1': 'A', 'l2': 'B', 'l3': 'C', 'units': 4},
            {'m': '2026-05', 'l1': '其他', 'l2': 'X', 'l3': 'X', 'units': 5}]
    text = digest.history_segments(rows)
    assert text.startswith('2026-01~2026-02 A > B > C（3 件）')
    assert '2026-04 A > B > C（4 件）' in text
    assert '2026-05 其他 > X > X（5 件）' in text


def grid_rows(series):
    """series: {(lvl, l1, l2, l3): {month: units}} -> std grid frame."""
    import pandas as pd
    rows = []
    for (lvl, l1, l2, l3), by_month in series.items():
        for m, u in by_month.items():
            rows.append({'site': 'US', 'platform': 'Amazon', 'm': m, 'lvl': lvl, 'l1': l1, 'l2': l2, 'l3': l3,
                         'spus': 10, 'nrows': 10, 'units': float(u), 'amount': float(u) * 2, 'amount_page': float(u) * 2})
    return pd.DataFrame(rows)


def test_contributions_rank_children_and_skip_same_named_leaf():
    g = grid_rows({
        (1, 'A', '', ''): {'2026-08': 100, '2026-09': 160},
        (2, 'A', 'X', ''): {'2026-08': 60, '2026-09': 110},
        (2, 'A', 'Y', ''): {'2026-08': 40, '2026-09': 50},
        (3, 'A', 'X', 'X1'): {'2026-08': 30, '2026-09': 75},
        (3, 'A', 'X', 'X2'): {'2026-08': 30, '2026-09': 35},
        (3, 'A', 'Y', 'Y'): {'2026-08': 40, '2026-09': 50},
    })
    rows = digest.contributions(g, '2026-08', '2026-09')
    level2 = [r for r in rows if r[0] == '二级']
    assert [r[2] for r in level2] == ['X', 'Y']
    assert level2[0][5] == '83.3%'          # 50 of the parent's +60
    level3 = [r for r in rows if r[0] == '三级']
    assert [r[2] for r in level3] == ['X1', 'X2']   # Y > Y repeats its level-2 row
    assert level3[0][6] == '+75.0pp'        # +45 against the parent's base of 60


def test_anomalies_flag_a_leaf_that_breaks_its_own_history():
    months = [f'2025-{m:02d}' for m in range(1, 13)] + ['2026-01']
    steady = {m: 1000 + 10 * i for i, m in enumerate(months)}
    jump = dict(steady)
    jump['2026-01'] = 4000
    g = grid_rows({(0, '', '', ''): {m: 20000 for m in months},
                   (3, 'A', 'X', 'X1'): jump,
                   (3, 'A', 'X', 'X2'): steady})
    digest.META = {'month': '2026-01', 'site_start': {('US', 'Amazon'): '2025-01'}}
    digest.FLAGS.clear()
    found = digest.anomalies(g, 'US', 'Amazon', 'units', 0.005, 10)
    assert [r[0] for r in found] == ['2026-01']
    assert found[0][1] == 'A > X > X1'
