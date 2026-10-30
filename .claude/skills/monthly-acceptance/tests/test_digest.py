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


def test_spu_impacts_flag_one_item_that_swings_its_category():
    import pandas as pd
    digest.META = {'month': '2026-08', 'site_start': {('JP', 'Amazon'): '2024-01'}}
    g = pd.DataFrame([{'lvl': 1, 'l1': '灯光类', 'l2': '', 'l3': '', 'm': '2026-07', 'units': 29108.0, 'amount': 190.0e6},
                      {'lvl': 1, 'l1': '灯光类', 'l2': '', 'l3': '', 'm': '2026-08', 'units': 27368.0, 'amount': 234.7e6}])
    c = pd.DataFrame([
        {'l1': '灯光类', 'product_id': 'B08', 'units_prev': 455, 'units_cur': 1324, 'amount_prev': 20.2e6, 'amount_cur': 76.2e6,
         'brand': 'Kose', 'title_cn': '', 'title_raw': 'LED パーライト', 'platform_path': '家電＆カメラ > イヤホン・ヘッドホン > 本体'},
        {'l1': '灯光类', 'product_id': 'small', 'units_prev': 100, 'units_cur': 110, 'amount_prev': 1.0e6, 'amount_cur': 1.1e6,
         'brand': 'X', 'title_cn': '小灯', 'title_raw': '', 'platform_path': 'a > b'}])
    rows = digest.spu_impacts(c, g)
    assert [r[1] for r in rows] == ['B08']
    assert '主导类目额环比' in rows[0][-1] and '单品占类目≥25%' in rows[0][-1]
    assert rows[0][3] == 'LED パーライト'       # falls back to the raw title
    assert rows[0][4] == 'イヤホン・ヘッドホン > 本体'
    assert rows[0][-2] == '-6.7%'               # without it the category fell


def test_leaf_mix_lists_heavy_and_residual_leaves_with_their_platform_nodes():
    import pandas as pd
    rows = [('音视频类', '耳机', '耳机', 'Electronics', 'Headphones', 'Over-Ear', '', 900),
            ('音视频类', '耳机', '耳机', 'Musical Instruments', 'Studio', 'Headphones', '', 100),
            ('支撑类', '支撑类-其他', '支撑类-其他', 'Electronics', 'TV', 'Mounts', '', 5),
            ('配件类', '相机配件', '小叶', 'Electronics', 'Camera', 'Misc', '', 3)]
    lp = pd.DataFrame([dict(zip(['l1', 'l2', 'l3', 'c1', 'c2', 'c3', 'c4', 'units'], r)) for r in rows])
    out = digest.leaf_mix(lp, min_share=0.01)
    assert [r[0] for r in out] == ['音视频类 > 耳机', '支撑类 > 支撑类-其他']   # the small named leaf is left out
    assert out[0][4].startswith('Electronics > Headphones > Over-Ear 90.0%')
    assert out[0][5] == '100.0%'


def test_spu_impacts_do_not_call_a_flat_category_dominated():
    import pandas as pd
    digest.META = {'month': '2026-09', 'site_start': {('DE', 'Amazon'): '2024-01'}}
    g = pd.DataFrame([{'lvl': 1, 'l1': '配件类', 'l2': '', 'l3': '', 'm': '2026-08', 'units': 1000.0, 'amount': 10000.0},
                      {'lvl': 1, 'l1': '配件类', 'l2': '', 'l3': '', 'm': '2026-09', 'units': 1010.0, 'amount': 10100.0}])
    base = {'l1': '配件类', 'brand': 'T', 'title_cn': '手机壳', 'title_raw': '', 'platform_path': 'a > b'}
    c = pd.DataFrame([{**base, 'product_id': 'new', 'units_prev': 1, 'units_cur': 120, 'amount_prev': 10.0, 'amount_cur': 1200.0},
                      {**base, 'product_id': 'old', 'units_prev': 115, 'units_cur': 6, 'amount_prev': 1150.0, 'amount_cur': 60.0}])
    rows = {r[1]: r[-1] for r in digest.spu_impacts(c, g)}
    assert rows == {'new': '单品大幅变动', 'old': '单品大幅变动'}   # +1% category: offsetting swings, not dominance
