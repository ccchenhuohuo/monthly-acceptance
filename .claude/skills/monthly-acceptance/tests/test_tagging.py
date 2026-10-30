import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import tagging  # noqa: E402


def sample():
    rows = [('A', 'X', 'X1', 'p1', 1, 100), ('A', 'X', 'X1', 'p2', 2, 80), ('A', 'X', 'X1', 'p9', 40, 3),
            ('A', 'A-其他', 'A-其他', 'r1', 1, 50), ('A', 'A-其他', 'A-其他', 'r5', 5, 20)]
    return pd.DataFrame([{'l1': a, 'l2': b, 'l3': c, 'product_id': p, 'rk_units': str(rk), 'units': str(u)}
                         for a, b, c, p, rk, u in rows])


def judged(rows):
    return pd.DataFrame([dict(zip(tagging.COLUMNS, r)) for r in rows])


def test_group_of_splits_head_residual_and_random():
    assert tagging.group_of(3, 'X1') == '头部'
    assert tagging.group_of(4, 'A-其他') == '残差叶'
    assert tagging.group_of(40, 'X1') == '随机'


def test_tally_counts_standard_questions_apart_from_misfits():
    j = judged([('A', 'X', 'X1', 'p1', '不符', '否', '其他', '定义原句', '大众耳机'),
                ('A', 'X', 'X1', 'p2', '符合', '是', '', '', '准则没写'),
                ('A', 'X', 'X1', 'p9', '存疑', '否', '', '', '标题不足'),
                ('A', 'A-其他', 'A-其他', 'r5', '不符', '是', '待定', '', '应归不清')])
    m, problems = tagging.tally(sample(), j)
    assert problems == []
    head = m[m.group == '头部']
    assert tagging.rate(head) == '1 / 2（50.0%）'          # the standard question is not a misfit
    assert tagging.rate(m[m.group == '残差叶']) == '1 / 1（100.0%）'   # current leaf wrong, target unclear: still a misfit
    assert int(m.rule_issue.sum()) == 2


def test_tally_reports_rows_it_cannot_count():
    j = judged([('A', 'X', 'X1', 'p1', '不符', '否', '其他', '', ''),
                ('A', 'X', 'X1', 'p1', '符合', '否', '', '', ''),
                ('A', 'X', 'X1', 'zz', '不符', '否', '其他', '', ''),
                ('A', 'X', 'X1', 'p2', '边界', '否', '', '', '')])
    m, problems = tagging.tally(sample(), j)
    assert len(m) == 1
    assert any('结论不是' in p for p in problems)
    assert any('重复' in p for p in problems)
    assert any('不在样本里' in p for p in problems)


def test_systematic_lists_leaves_whose_misfits_form_a_pattern():
    # Mexico 监视器: all three units-top items were portable computer displays; one stray misfit elsewhere is not a pattern.
    m = pd.DataFrame([{'l1': '音视频类', 'l2': '监视器', 'l3': '监视器', 'product_id': p, 'verdict': '不符', 'group': '头部', 'amount': a}
                      for p, a in (('DISPLAY_1', 520000.0), ('DISPLAY_2', 240000.0), ('DISPLAY_3', 270000.0))] +
                     [{'l1': '灯光类', 'l2': '摄影补光灯', 'l3': 'COB补光灯', 'product_id': 'x', 'verdict': '不符', 'group': '随机', 'amount': 10.0}])
    rows = tagging.systematic(m, {('音视频类', '监视器', '监视器'): 8_500_000.0, ('灯光类', '摄影补光灯', 'COB补光灯'): 1e6})
    assert [r[0] for r in rows] == ['监视器 > 监视器'] and rows[0][1] == 3 and rows[0][2] == '12.1%'
