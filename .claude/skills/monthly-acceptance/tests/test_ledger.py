import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ledger  # noqa: E402
import taxonomy  # noqa: E402
import views  # noqa: E402


def make_base(tmp_path):
    base = tmp_path / '验收'
    base.mkdir()
    (base / '服务商沟通台账.json').write_text(json.dumps({
        'title': '台账', 'about': ['说明'],
        'statements': [{'id': 'S01', 'effective': '2024', 'said': '原话', 'source': '群 2025-07-22', 'meaning': '含义'}],
        'agreements': [{'id': 'K01', 'date': '2025-09-18', 'content': '16 号交付', 'decided_by': '双方', 'source': '群', 'meaning': '开工'}],
        'issues': [{'id': 'Q01', 'raised': '2025-11-19', 'scope': 'US · B0DPPH1YBP', 'issue': '虚高', 'reply': '单点修', 'action': '修',
                    'status': '服务商称已修复', 'objects': [{'site': 'US', 'product_id': 'B0DPPH1YBP'}],
                    'checks': [{'run': 'runs/2026-09/20261029-0307', 'result': '仍在'}]},
                   {'id': 'Q02', 'raised': '2025-12-01', 'scope': 'cn', 'issue': '说明', 'reply': '口径', 'action': '—', 'status': '已说明',
                    'objects': [{'site': 'cn', 'product_id': '653138729740'}]}],
        'notes': ['备注一']}, ensure_ascii=False))
    snap = base / '参考' / '服务商需求表-20261030'
    snap.mkdir(parents=True)
    with (snap / '07_客户反馈清单.csv').open('w', newline='') as f:
        csv.writer(f).writerows([['反馈编号', '客户反馈时间', '客户反馈内容', '平台范围', '截图', '优先级', '预计', '反馈细分类', 'FW反馈', '附图', '需求进度'],
                                 ['1', '2025-09-22', '刷单 https://x.y/z', '阿里', '', '高', '', '数据剔除', '先follow', '', '已体现在交付物中']])
    return base


def test_render_lists_sections_open_count_rechecks_and_feedback(tmp_path):
    text = ledger.render(make_base(tmp_path))
    assert '## 一、' in text and '## 二、' in text and '3.1' in text and '3.2' in text
    assert '未闭环 1 条' in text
    assert '20261029-0307：仍在' in text
    assert '<链接>' in text and 'https://x.y/z' not in text
    assert '- 备注一' in text


def test_ledger_pids_merge_known_defects_and_vendor_issues(tmp_path):
    base = make_base(tmp_path)
    inputs = base / 'inputs'
    inputs.mkdir()
    (inputs / '服务商沟通台账.json').write_text((base / '服务商沟通台账.json').read_text())
    (inputs / '已知缺陷台账.json').write_text(json.dumps([{'objects': [{'product_id': '674045052752'}, {'product_id': 'B0DPPH1YBP'}]}]))
    assert views.ledger_pids(inputs) == ['653138729740', '674045052752', 'B0DPPH1YBP']


def test_previous_run_is_the_latest_report_of_this_skill_generation(tmp_path):
    base = tmp_path / '验收'
    for name, created, report, new_style in (('runs/2026-07/old', '2026-09-07', True, False),
                                             ('runs/2026-09/a', '2026-10-29T03:07', True, True),
                                             ('runs/2026-10/b', '2026-11-18T09:00', False, True)):
        d = base / name
        d.mkdir(parents=True)
        meta = {'created_at': created, **({'site_platforms': []} if new_style else {})}
        (d / 'run.json').write_text(json.dumps(meta))
        if report:
            (d / '报告.md').write_text('# 报告')
    assert views.previous_run(base) == 'runs/2026-09/a'


def test_rulings_are_appended_once_and_replace_an_earlier_copy(tmp_path):
    run = tmp_path / 'run'
    (run / 'inputs' / '标准').mkdir(parents=True)
    std = run / 'inputs' / '标准' / '判定标准.md'
    std.write_text('# 类目判定标准\n\n| 二级 | 三级 |\n')
    (run / 'inputs' / '准则裁定.md').write_text('# 准则裁定\n\n| 编号 | 裁定 |\n|---|---|\n| K13 | 残差叶不备注 |\n| K14 | 数据线归手机充电 |\n')
    assert taxonomy.append_rulings(run) == 2
    assert taxonomy.append_rulings(run) == 2
    text = std.read_text()
    assert text.count('准则裁定（项目侧') == 1 and 'K14 | 数据线归手机充电' in text and text.startswith('# 类目判定标准')


def test_caliber_lists_only_statements_with_a_reading():
    led = {'statements': [{'id': 'S01', 'effective': '2024', 'said': '原话', 'source': '群 2025-07-22', 'meaning': '含义',
                           'caliber': '历史按增长率拟合', 'reading': '跨 2024 的同比不是独立观测'},
                          {'id': 'S02', 'effective': '全期', 'said': '原话', 'source': '群', 'meaning': '含义'}]}
    text = ledger.caliber(led)
    assert text.startswith('# 服务商口径')
    assert '| S01 | 2024 | 历史按增长率拟合 | 跨 2024 的同比不是独立观测 | 群 2025-07-22 |' in text
    assert 'S02' not in text
