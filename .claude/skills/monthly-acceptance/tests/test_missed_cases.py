"""Cases the acceptance missed and downstream found (2026-09: the Q3 strategic report, by leaf × brand × amount).

Each test rebuilds one case at its real proportions (shares of the leaf, price against the leaf median, size against
the site) and asserts that the check meant to catch it does. Amounts are rounded and products renamed: the vendor's
estimates are licensed data and this repository is public. Add a test here whenever downstream finds something an
acceptance run did not report.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import digest  # noqa: E402
import tagging  # noqa: E402


def item(pid, units, amount, leaf_amount, leaf_median, c1='Electronics', main_root='Electronics', root_amount=None,
         l3='运动相机补光灯', l2='运动相机补光灯', leaf_spus=60, brand='', title=''):
    return {'site': 'US', 'platform': 'Amazon', 'l1': '灯光类', 'l2': l2, 'l3': l3, 'product_id': pid, 'brand': brand,
            'raw_brand': brand, 'units': float(units), 'amount': float(amount), 'price': amount / units,
            'leaf_units': 20000.0, 'leaf_amount': float(leaf_amount), 'leaf_spus': leaf_spus, 'leaf_median': float(leaf_median),
            'main_root': main_root, 'c1': c1, 'root_amount': float(root_amount if root_amount is not None else leaf_amount),
            'platform_path': f'{c1} > Camera & Photo > Lighting', 'title_cn': title, 'title_raw': '', 'rk_amount': 1}


def test_camera_kit_carrying_an_action_camera_light_leaf_is_listed_first():
    # US 运动相机补光灯: a DJI pocket-camera kit at about 700 USD holds 16% of a leaf of cheap lights (median 18 USD).
    # It sells a few dozen a month, so it never made the units top 3 the tagging sample drew; a cheap light did.
    # The leaf is 0.03% of the US focus amount, below the size floor for unmarked rows.
    leaf = 700000
    items = pd.DataFrame([item('POCKET4_KIT', 160, 112000, leaf, 18.0, brand='DJI/大疆', title='Osmo Pocket 4 补光灯套装'),
                          item('CHEAP_LIGHT', 3400, 70000, leaf, 18.0, brand='JJC'),
                          item('OTHERLEAF', 10, 900, 6000, 50.0, l3='摄影补光灯-其他', l2='摄影补光灯'),
                          item('FILLER', 10_000_000, 456_000_000, 2_280_000_000, 9.0, l3='手机充电', l2='手机充电')])
    rows = digest.leaf_dominance(items)
    assert rows[0][1] == 'POCKET4_KIT' and '单价≥小类中位数10倍' in rows[0][-1]
    assert all(r[1] != 'CHEAP_LIGHT' for r in rows)          # 10% of the leaf, cheap: not a dominance case


def test_one_product_that_is_the_whole_leaf_is_listed_whatever_the_leaf_size():
    # BR 运动相机补光灯: one camera kit is about 80% of the leaf; JP AI键盘 was a single product, 100%.
    items = pd.DataFrame([item('LUNA_KIT', 110, 576000, 722000, 140.0, brand='INSTA360/影石'),
                          item('BIG', 100000, 900000000, 900000000, 9.0, l3='手机充电', l2='手机充电')])
    rows = digest.leaf_dominance(items)
    assert any(r[1] == 'LUNA_KIT' and '撑起小类' in r[-1] for r in rows)


def test_a_price_that_was_wrong_from_the_start_is_caught_against_its_peers():
    # BR 蓝牙遥控器: a camera remote at about 26,000 BRL against a leaf median of 140 BRL; it never jumped month to month.
    leaf = dict(leaf_amount=300000, leaf_median=140.0, l3='蓝牙遥控器', l2='蓝牙遥控器', leaf_spus=40)
    items = pd.DataFrame([item('REMOTE', 3, 78500, **leaf, brand='SONY/索尼'),
                          item('PEER_REMOTE', 4, 1200, **leaf, brand='JJC')])
    named, resid = digest.price_outliers(items)
    assert [r[1] for r in named] == ['REMOTE'] and named[0][7] == '187' and resid == []


def test_camera_kits_are_not_drowned_by_the_residual_leaves_and_still_show_inside_them():
    # US: about two thirds of the SPUs priced ≥20× their leaf sat in 数码外设-其他, whose median (13 USD) mixes cables
    # with SSDs and 83-inch TVs; the camera kits in 运动相机补光灯 ranked past 200th. Residual leaves now go to a table
    # of their own, where only SPUs worth ≥1% of the leaf show: an action-camera kit at 10% of 运动相机配件-其他 does,
    # a TV at 0.1% of a huge leaf does not. A named leaf shows at most two.
    light = dict(leaf_amount=700000, leaf_median=18.0)
    cable = dict(leaf_amount=1.2e9, leaf_median=13.0, l3='数码外设-其他', l2='数码外设-其他')
    acc = dict(leaf_amount=5300000, leaf_median=19.0, l3='运动相机配件-其他', l2='运动相机配件')
    items = pd.DataFrame([item('KIT_A', 160, 112000, **light), item('KIT_B', 70, 45500, **light),
                          item('KIT_C', 60, 45000, **light),
                          item('OLED_TV', 600, 1.2e6, **cable),
                          item('ACTION6_KIT', 1040, 530000, **acc)])
    named, resid = digest.price_outliers(items)
    assert [r[1] for r in named] == ['KIT_A', 'KIT_B']
    assert [r[1] for r in resid] == ['ACTION6_KIT']


def test_an_instrument_case_among_camera_cases_is_flagged_by_its_platform_root():
    # US 摄影箱: an ATA case for a guitar effects floorboard sits under Musical Instruments, about 3% of a leaf that is
    # otherwise Electronics > Camera & Photo; a second root holding 30% of the leaf is normal and stays out.
    leaf = dict(leaf_amount=8150000, leaf_median=60.0, l3='摄影箱', l2='箱类')
    items = pd.DataFrame([item('GATOR_CASE', 950, 228000, **leaf, c1='Musical Instruments', root_amount=240000),
                          item('PHONE_CASE', 5000, 300000, **leaf, c1='Cell Phones & Accessories', root_amount=2450000)])
    rows = digest.off_root_items(items)
    assert [r[2] for r in rows] == ['GATOR_CASE']


def pairs(rows):
    return pd.DataFrame([{'site': s, 'platform': 'Amazon', 'brand': b, 'raw_brand': r, 'spus': n, 'units': float(u),
                          'amount': float(a), 'rk': 1} for s, b, r, n, u, a in rows])


def test_standard_brands_that_swallowed_unrelated_raw_brands_are_listed():
    # ROCK/洛克 took in Rockville and Rocketek; BS/贝尔森 took in Owl Labs and OBSBOT. Own-brand variants are mapped on
    # purpose; a Chinese standard name matching its Chinese raw name is fine.
    p = pairs([('US', 'ROCK/洛克', 'Rockville', 240, 33000, 3700000), ('US', 'ROCK/洛克', 'Rocketek', 110, 13000, 420000),
               ('US', 'ROCK/洛克', 'ROCK', 3, 40, 900), ('US', 'BS/贝尔森', 'Owl Labs', 5, 4500, 4700000),
               ('US', 'BS/贝尔森', 'OBSBOT', 3, 110, 13000), ('US', 'ULANZI/优篮子', 'VIJIM', 20, 3000, 40000),
               ('US', 'VILTROX/唯卓仕', '唯卓仕', 3, 290, 180000)])
    rows = digest.brand_mismatches(p, own={'ULANZI/优篮子'})
    assert [r[0] for r in rows] == ['BS/贝尔森', 'ROCK/洛克']
    assert rows[1][1].startswith('Rockville') and rows[1][2] == 2


def test_japanese_katakana_latin_names_are_the_same_brand():
    # Japan writes brands as 'カタカナ(Latin)': GoPro(ゴープロ) is GoPro, not a mismatch; Shimbol in AKG/爱科技 is.
    assert digest.raw_matches('GoPro(ゴープロ)', 'GoPro') and digest.raw_matches('スイッチボット(SwitchBot)', 'SwitchBot')
    assert not digest.raw_matches('Shimbol', 'AKG/爱科技') and not digest.raw_matches('Rockville', 'ROCK/洛克')


def test_one_brand_split_into_several_standard_names_and_spellings():
    # Raw 'Logitech G' went to two standard names in the US; a single JP listing under LOGICOOL is below the noise floor.
    p = pairs([('US', 'Logitech G', 'Logitech G', 12, 43000, 3500000), ('US', 'G/罗技', 'Logitech G', 1, 1600, 65000),
               ('US', 'G/罗技', 'Logitech', 83, 130000, 5300000), ('JP', 'LOGICOOL', 'Logitech', 1, 28, 140000),
               ('US', 'Peak Design', 'Peak Design', 42, 40000, 3000000), ('cn', 'peak design', 'peak design', 10, 110, 30000),
               ('JP', 'ピークデザイン', 'ピークデザイン', 27, 1000, 17700000)])
    splits = {r[0]: r[1] for r in digest.brand_splits(p)}
    assert set(splits) == {'Logitech G', 'Peak Design'} and 'G/罗技' in splits['Logitech G']
    dup, kata = digest.brand_variants(p)
    assert dup[0][0] == 'peakdesign' and kata[0][0] == 'ピークデザイン'


def test_words_that_are_not_brands_are_listed():
    p = pairs([('cn', 'NEUTRAL/中性', 'NEUTRAL', 5, 180000, 2500000), ('cn', '无', '无', 8, 2000, 75000),
               ('cn', 'BN/数码配件', 'BN', 410, 7000, 98000), ('cn', 'ROCK/洛克', 'ROCK/洛克', 245, 14500, 665000)])
    names = [r[0] for r in digest.non_brands(p, categories={'灯光类', '摄影补光灯'})]
    assert names == ['NEUTRAL/中性', 'BN/数码配件', '无']


def leaf_brands(rows):
    return pd.DataFrame([{'site': 'cn', 'platform': 'Tmall', 'l1': '灯光类', 'l2': l2, 'l3': l3, 'period': per, 'brand': b,
                          'spus': 10, 'units': 100.0, 'amount': float(a), 'leaf_units': 1000.0, 'leaf_amount': float(la), 'rk': 1}
                         for l2, l3, per, b, a, la in rows])


def test_a_category_that_lost_its_brands_shows_although_the_site_share_looks_fine():
    # Tmall 摄影补光灯: OTHERS went from about 3% of amount a year earlier to about 21%; the Tmall-wide share was under 2%.
    lb = leaf_brands([('摄影补光灯', 'COB补光灯', 'cur', 'OTHERS/其他', 21, 100), ('摄影补光灯', 'COB补光灯', 'cur', 'APUTURE/爱图仕', 30, 100),
                      ('摄影补光灯', 'COB补光灯', 'yoy', 'OTHERS/其他', 3, 100), ('摄影补光灯', 'COB补光灯', 'yoy', 'APUTURE/爱图仕', 40, 100),
                      ('手机配件', '手机壳', 'cur', 'OTHERS/其他', 1, 900), ('手机配件', '手机壳', 'yoy', 'OTHERS/其他', 1, 900)])
    rows = digest.others_by_l2(lb)
    assert [r[0] for r in rows] == ['灯光类 > 摄影补光灯'] and rows[0][2] == '21.0%' and '同比 +10pp 以上' in rows[0][-1]


def test_the_top_brands_of_each_leaf_are_shown_by_amount():
    # MX 监视器: computer-monitor makers lead a camera-monitor leaf.
    lb = leaf_brands([('监视器', '监视器', 'cur', 'LG/電子', 14, 100), ('监视器', '监视器', 'cur', 'ASUS/华硕', 11, 100),
                      ('监视器', '监视器', 'cur', 'OTHERS/其他', 2, 100)])
    rows = digest.leaf_top_brands(lb)
    assert rows[0][2].startswith('LG/電子 14.0%；ASUS/华硕 11.0%') and rows[0][3] == '2.0%'


def test_amount_top_and_pricey_sample_rows_get_their_own_groups():
    assert tagging.group_of(2, '运动相机补光灯', 1, 39.4) == '头部'
    assert tagging.group_of(17, '运动相机补光灯', 1, 39.4) == '金额头部'
    assert tagging.group_of(40, '运动相机补光灯', 6, 36.1) == '贵价'
    assert tagging.group_of(40, '运动相机补光灯') == '随机'           # samples drawn before 0.3.0
