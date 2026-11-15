# -*- coding: utf-8 -*-
"""单元测试：可交互事件时间线（单文件 HTML）。

所有轨迹 / HTML 都写进 tmp_path；XSS 用真断言验证转义，禁止 assert True。
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_ROOT, "tools")
for _p in (_ROOT, _TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import trace_view as tv
from framework import trajectory as traj


def _summary(**overrides):
    base = {"turns": 2, "events": 6, "failure_rate": 0.25,
            "tokens": 1234, "chain_ok": True, "kinds": {}}
    base.update(overrides)
    return base


def _events():
    return [
        {"turn_id": "t_000001", "seq": 0, "kind": "turn_start",
         "source": "user", "pre_hash": "aaaa1111", "post_hash": "aaaa1111",
         "note": "开始"},
        {"turn_id": "t_000001", "seq": 1, "kind": "action",
         "action": "web_search", "source": "model", "note": "检索资料",
         "payload": {"query": "新能源"}, "pre_hash": "aaaa1111",
         "post_hash": "bbbb2222"},
        {"turn_id": "t_000001", "seq": 2, "kind": "failure",
         "action": "read_doc", "note": "文件不存在", "ok": False,
         "payload": {"path": "C:/x/a.docx"}, "pre_hash": "bbbb2222",
         "post_hash": "cccc3333", "payload_digest": "feed0001"},
        {"turn_id": "t_000002", "seq": 0, "kind": "quality",
         "action": "create_doc", "note": "要点干瘪",
         "payload": {"doc_type": "ppt"}, "pre_hash": "cccc3333",
         "post_hash": "dddd4444"},
        {"turn_id": "t_000002", "seq": 1, "kind": "preference",
         "note": "用户偏好深色", "payload": {"key": "doc_theme"},
         "post_hash": "eeee5555"},
        {"turn_id": "t_000002", "seq": 2, "kind": "rule",
         "note": "命中规则", "payload": {"rule_id": "r1"},
         "post_hash": "ffff6666"},
    ]


# ---------------------------------------------------------------------------
# 1. HTML 含「时间线」与「汇总」，并按 turn 分组
# ---------------------------------------------------------------------------
def test_timeline_and_summary_present():
    html = tv.render(_events(), _summary())
    assert "时间线" in html
    assert "汇总" in html
    assert 'class="turn"' in html
    assert html.count('class="turn"') == 2       # 两轮各一张卡片
    assert "t_000001" in html and "t_000002" in html
    assert "web_search" in html


# ---------------------------------------------------------------------------
# 2. 汇总指标 + 哈希链状态展示
# ---------------------------------------------------------------------------
def test_summary_metrics_and_hash_chain_state():
    html = tv.render(_events(), _summary())
    assert "轮数" in html and "事件数" in html
    assert "失败率" in html and "token 合计" in html
    assert "1234" in html and "25.00%" in html
    assert "状态哈希链" in html
    assert "完整" in html
    # 逐条哈希链：pre_hash -> post_hash、digest 都出现在页面
    assert "aaaa1111" in html and "bbbb2222" in html
    assert "feed0001" in html
    assert "pre_hash" in html and "post_hash" in html
    # chain_ok=False 时应如实展示异常
    bad = tv.render(_events(), _summary(chain_ok=False))
    assert "异常" in bad and 'class="bad"' in bad


# ---------------------------------------------------------------------------
# 3. 失败 / quality 事件高亮标记
# ---------------------------------------------------------------------------
def test_failure_and_quality_highlighted():
    html = tv.render(_events(), _summary())
    assert "event-failure" in html
    assert "event-quality" in html
    assert "is-problem" in html
    assert 'data-kind="failure"' in html
    assert 'class="badge badge-failure"' in html
    assert "文件不存在" in html


# ---------------------------------------------------------------------------
# 4. preference / rule 事件单独配色
# ---------------------------------------------------------------------------
def test_preference_and_rule_have_own_colors():
    html = tv.render(_events(), _summary())
    assert 'class="event event-preference"' in html
    assert 'class="event event-rule"' in html
    assert "badge-preference" in html and "badge-rule" in html
    # 单独配色卡片不应被标成问题事件
    pref_card = html.split('class="event event-preference"', 1)[1].split(
        "</div>", 1)[0]
    assert "is-problem" not in pref_card


# ---------------------------------------------------------------------------
# 5. 空数据生成友好占位页，不崩
# ---------------------------------------------------------------------------
def test_empty_data_placeholder_page():
    html = tv.render([], _summary(turns=0, events=0, failure_rate=0,
                                  tokens=0))
    assert "暂无轨迹数据" in html
    assert "时间线" in html and "汇总" in html
    assert 'class="empty"' in html
    assert "请先用 TrajectoryStore" in html
    # 容错：None 也不崩
    assert "暂无轨迹数据" in tv.render(None, None)


# ---------------------------------------------------------------------------
# 6. XSS 转义 + 输出不含用户原话
# ---------------------------------------------------------------------------
def test_xss_escaped_and_no_raw_user_text():
    evil = "<script>alert('xss')</script>"
    events = [{
        "turn_id": "t_000001", "seq": 1, "kind": "failure", "ok": False,
        "note": evil,
        "payload": {"message": "用户私下说的原话 SECRET123",
                    "reason": evil, "user_text": "私人信息 ABC987"},
    }]
    html = tv.render(events, _summary(events=1, failure_rate=1.0))
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert" in html
    assert "&lt;/script&gt;" in html
    assert "SECRET123" not in html
    assert "ABC987" not in html
    assert "redacted" in html            # 敏感键被脱敏
    assert "<img" not in html


# ---------------------------------------------------------------------------
# 7. render() 可直接调用、返回完整可复现的 HTML 字符串
# ---------------------------------------------------------------------------
def test_render_is_directly_callable_and_deterministic(tmp_path):
    before = set(os.listdir(tmp_path))
    html = tv.render(_events(), _summary())
    assert isinstance(html, str)
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "</html>" in html
    assert "<style>" in html and "<script>" in html
    assert html == tv.render(_events(), _summary())   # 确定性
    assert set(os.listdir(tmp_path)) == before        # render 不落盘


# ---------------------------------------------------------------------------
# 8. 不依赖网络：无 CDN / 外链；含「只看失败」过滤与深浅色自适应
# ---------------------------------------------------------------------------
def test_no_network_and_filter_controls():
    html = tv.render(_events(), _summary())
    assert "http://" not in html
    assert "https://" not in html
    assert "cdn" not in html.lower()
    assert "src=" not in html                 # 无外部脚本 / 图片引用
    assert '<script>' in html                 # 但确有内联原生 JS
    assert "只看失败" in html
    assert "filterBtn" in html
    assert "only-fail" in html
    assert "prefers-color-scheme" in html


# ---------------------------------------------------------------------------
# 9. CLI：传入不存在的轨迹路径 -> 友好占位页，不崩、不创建轨迹文件
# ---------------------------------------------------------------------------
def test_cli_nonexistent_trace(tmp_path):
    trace = tmp_path / "no_such_trajectory.jsonl"
    out = tmp_path / "report.html"
    rc = tv.main(["--trace", str(trace), "--out", str(out)])
    assert rc == 0
    assert not trace.exists()                 # 不擅自创建轨迹文件
    assert out.is_file()
    html = out.read_text(encoding="utf-8")
    assert "暂无轨迹数据" in html
    assert "时间线" in html and "汇总" in html


# ---------------------------------------------------------------------------
# 10. CLI：真实 TrajectoryStore 事件流 -> 时间线含事件、转义、哈希链
# ---------------------------------------------------------------------------
def test_cli_with_real_trajectory(tmp_path):
    trace = tmp_path / "trajectory.jsonl"
    store = traj.TrajectoryStore(path=str(trace))
    store.start_turn(source="user", pre_hash="h0start")
    store.append("action", action="web_search", source="model",
                 note="检索资料", pre_hash="h0start", post_hash="h1")
    store.append("failure", action="read_doc", note="文件不存在",
                 pre_hash="h1", post_hash="h1", ok=False)
    store.end_turn(post_hash="h1", ok=False)

    out = tmp_path / "nested" / "timeline.html"
    rc = tv.main(["--trace", str(trace), "--out", str(out),
                  "--max-events", "500"])
    assert rc == 0
    assert out.is_file()                      # 目录不存在会自动创建
    html = out.read_text(encoding="utf-8")
    assert "时间线" in html and "状态哈希链" in html
    assert "web_search" in html and "event-failure" in html
    assert "h0start" in html
    assert "<script>alert" not in html
