import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import vendor  # noqa: E402


def test_redact_drops_password_messages_and_masks_secrets():
    assert vendor.redact('账号密码：a@b.com Xyz12345') == '[含账号密码的消息，已略去]'
    assert vendor.redact('723951') == '[疑似验证码，已略去]'
    assert '<已打码>' in vendor.redact('curl -u user:Secret@123 -T a.csv http://x')
    assert 'Secret' not in vendor.redact("auth:('user', 'Secret@123')")
    assert 'abcdef0123456789abcdef' not in vendor.redact('mcp token：abcdef0123456789abcdef01')
    assert vendor.redact('B0FYM1KFRH 销量异常，id=898536131934') == 'B0FYM1KFRH 销量异常，id=898536131934'


def test_annotated_rows_keep_multiline_records():
    text = '[row=1] 反馈编号,FW反馈\n[row=2] 1,"第一行\n第二行"\n[row=3] 2,好\n'
    assert vendor.annotated_rows(text) == [['反馈编号', 'FW反馈'], ['1', '第一行\n第二行'], ['2', '好']]


def msg(t, text, tenant='t1', name='甲', replies=()):
    return {'msg_type': 'text', 'create_time': t, 'content': text, 'sender': {'name': name, 'tenant_key': tenant},
            'thread_replies': list(replies)}


def test_render_keeps_only_messages_after_the_freeze_point_including_thread_replies():
    since = vendor.to_cst('2026-10-01 10:00').isoformat()
    old_root = msg('2026-09-20 09:00', '旧消息', replies=[msg('2026-10-02 09:00', '话题里的新回复', tenant='t2', name='乙')])
    text, n, newest = vendor.render('问题群', [old_root, msg('2026-10-01 09:00', '冻结前'), msg('2026-10-03 08:00', '新消息')],
                                    since, {'t1': '我方', 't2': '服务商'})
    assert n == 2 and '旧消息' not in text and '冻结前' not in text
    assert text.index('话题里的新回复') < text.index('新消息')
    assert '乙·服务商' in text
    assert newest == vendor.to_cst('2026-10-03 08:00').isoformat()


def test_last_sync_takes_the_newest_earlier_freeze_and_skips_the_current_run(tmp_path):
    base = tmp_path / '验收'
    for run, until in (('runs/2026-09/a', '2026-10-20T10:00:00+08:00'), ('runs/2026-10/b', '2026-11-20T10:00:00+08:00')):
        m = base / run / 'inputs' / '服务商' / 'manifest.json'
        m.parent.mkdir(parents=True)
        m.write_text(json.dumps({'chats': {'oc_1': {'until': until}}}))
    current = base / 'runs/2026-10/b'
    got = vendor.last_sync(base, current, {'covered_until': {'oc_1': '2026-10-01T00:00:00+08:00', 'oc_2': '2026-09-01T00:00:00+08:00'}})
    assert got == {'oc_1': '2026-10-20T10:00:00+08:00', 'oc_2': '2026-09-01T00:00:00+08:00'}
