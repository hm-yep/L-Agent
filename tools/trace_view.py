# -*- coding: utf-8 -*-
"""可交互事件时间线 —— tools/trace_view.py

把 TrajectoryStore 的 append-only 事件流渲染成**单文件、无外链**的 HTML：

    python tools/trace_view.py --trace data/trajectory.jsonl --out report.html
    python tools/trace_view.py --trace <不存在的路径> --out report.html
    python tools/trace_view.py --trace <path> --out <html> --max-events 500

特性
----
- 按 turn 分组的时间线，每轮一张卡片、轮内按 seq 排列；
- 顶部汇总指标直接来自 TrajectoryStore.summarize()：轮数 / 事件数 /
  失败率 / token 合计 / chain_ok，并单列「状态哈希链」区块；
- 每个事件卡片展示 kind / action / source / ts / note / payload 以及
  pre_hash → post_hash、payload_digest（哈希链状态逐条可查）；
- failure 与 quality 事件高亮（is-problem），preference / rule 事件单独配色；
- 「只看失败」按钮：原生 JS 切换 body.only-fail，隐藏非问题事件；
- 纯标准库，内联 CSS + 少量原生 JS，不引用任何 CDN；用
  @media (prefers-color-scheme: dark) 自适应深浅色；
- **HTML 转义**：note / payload 一律经 html.escape；payload 先过
  trajectory.sanitize()（敏感键如 message/user_text 变成 <redacted> 后再
  转义），因此 <script> 之类不会被执行，输出也不含用户原话；
- 传入不存在的轨迹路径时生成友好占位页，不抛异常、不创建轨迹文件。

render(events, summary) 可直接 import 调用，供单测隔离验证。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError, OSError):
    pass

import argparse
import html
import json

from framework import trajectory as _traj


def _esc(value):
    """HTML 转义：note / payload / hash 等一切动态文本都走这里。"""
    return html.escape("" if value is None else str(value), quote=True)


def _payload_text(payload):
    """脱敏 + JSON 序列化 + 转义，绝不让 <script> 或用户原话进入 HTML。"""
    try:
        source = dict(payload) if isinstance(payload, dict) else {}
        # 撤销账本里存着用户待办/记忆的原文（回滚时必须保留），
        # 但时间线是给人看的：只展示数量，不把明细渲染出来。
        ledger = source.get("undo_ledger")
        if isinstance(ledger, dict):
            source["undo_ledger"] = {
                "items": len(ledger.get("items") or []),
                "blind_spots": len(ledger.get("spots") or []),
                "note": "明细保留在轨迹中供回滚使用，不在时间线展示",
            }
        clean = _traj.sanitize(source)
        dumped = json.dumps(clean, ensure_ascii=False, sort_keys=True,
                            default=str)
    except Exception:  # noqa: BLE001 单条脏 payload 不许拖垮整页
        dumped = str(payload)
    return _esc(dumped)


def _hash_text(value):
    return _esc(value) if value else "—"


# ---------------------------------------------------------------------------
# 静态片段：不用 str.format / %，避免 CSS 里的花括号与百分号冲突
# ---------------------------------------------------------------------------
_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>事件时间线 · 轨迹回放</title>
<style>
:root{
  --bg:#f5f6f8; --card:#ffffff; --text:#1f2329; --muted:#6b7280;
  --line:#e3e6eb; --accent:#2f6feb; --soft:#f0f3f8;
  --fail:#c0392b; --fail-bg:#fdecea; --warn:#b9770e; --warn-bg:#fff6e5;
  --pref:#7d3c98; --pref-bg:#f4ecf7; --rule:#1f7a5a; --rule-bg:#e9f6f0;
  --shadow:0 1px 3px rgba(20,30,50,.08);
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#12151a; --card:#1c2129; --text:#e7e9ed; --muted:#9aa3af;
    --line:#2b313b; --accent:#6ea8fe; --soft:#232932;
    --fail:#ff7a6e; --fail-bg:#3a1f1e; --warn:#f0c060; --warn-bg:#3a2f18;
    --pref:#c79adf; --pref-bg:#2e2135; --rule:#6fd3a8; --rule-bg:#173029;
    --shadow:0 1px 3px rgba(0,0,0,.4);
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
  line-height:1.55}
.wrap{max-width:960px;margin:0 auto;padding:28px 18px 60px}
header h1{margin:0 0 4px;font-size:24px;letter-spacing:.5px}
header .sub{margin:0 0 22px;color:var(--muted);font-size:13px}
h2{font-size:16px;margin:0 0 12px;padding-left:10px;
  border-left:3px solid var(--accent)}
section{margin-bottom:26px}
.cards{display:flex;flex-wrap:wrap;gap:12px}
.card{flex:1 1 150px;background:var(--card);border:1px solid var(--line);
  border-radius:10px;padding:12px 14px;box-shadow:var(--shadow)}
.card span{display:block;color:var(--muted);font-size:12px;margin-bottom:6px}
.card b{font-size:20px;font-weight:650}
.card b.ok{color:var(--rule)} .card b.bad{color:var(--fail)}
.chain{margin-top:12px;background:var(--soft);border:1px dashed var(--line);
  border-radius:8px;padding:10px 12px;font-size:13px;color:var(--muted)}
.chain b{color:var(--text)}
.toolbar{display:flex;align-items:center;gap:12px;margin:0 0 20px}
.toolbar button{cursor:pointer;border:1px solid var(--accent);
  background:var(--card);color:var(--accent);border-radius:8px;
  padding:7px 16px;font-size:13px}
.toolbar button:hover{background:var(--accent);color:#fff}
.toolbar span{color:var(--muted);font-size:12px}
.turn{background:var(--card);border:1px solid var(--line);border-radius:12px;
  margin-bottom:16px;overflow:hidden;box-shadow:var(--shadow)}
.turn-head{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;
  padding:12px 16px;background:var(--soft);border-bottom:1px solid var(--line);
  font-size:12px;color:var(--muted)}
.turn-head .turn-id{font-size:15px;font-weight:650;color:var(--text)}
.turn-head code{color:var(--accent);font-size:12px}
.turn-events{padding:10px 14px 14px}
.event{border:1px solid var(--line);border-left:4px solid var(--line);
  border-radius:8px;padding:10px 12px;margin:8px 0;background:var(--card)}
.ev-head{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;
  font-size:12px;margin-bottom:6px}
.seq{color:var(--muted);font-variant-numeric:tabular-nums}
.badge{padding:1px 8px;border-radius:999px;font-size:11px;font-weight:600;
  background:var(--soft);border:1px solid var(--line)}
.badge-failure{color:var(--fail);border-color:var(--fail)}
.badge-quality{color:var(--warn);border-color:var(--warn)}
.badge-preference{color:var(--pref);border-color:var(--pref)}
.badge-rule{color:var(--rule);border-color:var(--rule)}
.act{font-weight:600} .src,.ts{color:var(--muted)}
.ev-note{font-size:13px;margin:2px 0}
.ev-note.empty{color:var(--muted)}
.ev-payload{margin:6px 0 0;padding:8px 10px;background:var(--soft);
  border-radius:6px;font-size:11.5px;white-space:pre-wrap;word-break:break-all;
  font-family:ui-monospace,Consolas,"Courier New",monospace}
.ev-chain{margin-top:6px;font-size:11px;color:var(--muted);
  font-family:ui-monospace,Consolas,"Courier New",monospace}
.ev-chain code{color:var(--accent)}
.event-failure{border-left-color:var(--fail);background:var(--fail-bg)}
.event-quality{border-left-color:var(--warn);background:var(--warn-bg)}
.event-preference{border-left-color:var(--pref);background:var(--pref-bg)}
.event-rule{border-left-color:var(--rule);background:var(--rule-bg)}
.empty{background:var(--card);border:1px dashed var(--line);border-radius:12px;
  padding:34px 20px;text-align:center;color:var(--muted)}
.empty b{display:block;color:var(--text);font-size:16px;margin-bottom:8px}
footer{color:var(--muted);font-size:11px;text-align:center;margin-top:30px}
body.only-fail .event:not(.is-problem){display:none}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>事件时间线</h1>
  <p class="sub">按 turn 分组的轨迹回放 · 事件本身已脱敏，输出不含用户原话、不引用任何外部资源</p>
</header>
"""

_TAIL = """<footer>单文件离线页面 · 内联样式与脚本 · 无外部资源 · 深浅色自适应</footer>
</div>
<script>
(function(){
  var btn = document.getElementById('filterBtn');
  var hint = document.getElementById('filterHint');
  btn.addEventListener('click', function(){
    var on = document.body.classList.toggle('only-fail');
    btn.textContent = on ? '显示全部' : '只看失败';
    hint.textContent = on ? '仅显示失败 / 质量预警事件' : '显示全部事件';
  });
})();
</script>
</body>
</html>
"""


_SPECIAL_KINDS = frozenset({"failure", "quality", "preference", "rule"})


def _event_class(ev):
    """CSS class：失败/质量高亮 + is-problem，preference/rule 单独配色。"""
    kind = str(ev.get("kind") or "")
    ok_false = ev.get("ok") is False
    is_problem = kind in ("failure", "quality") or ok_false
    classes = ["event"]
    if kind == "failure" or (is_problem and kind == "failure"):
        classes.append("event-failure")
    elif kind == "quality":
        classes.append("event-quality")
    elif kind == "preference":
        classes.append("event-preference")
    elif kind == "rule":
        classes.append("event-rule")
    elif is_problem:
        classes.append("event-failure")   # ok=False 但 kind 普通的也高亮
    if is_problem:
        classes.append("is-problem")
    return " ".join(classes), kind, is_problem


def _render_event(ev):
    css_class, kind, _ = _event_class(ev)
    badge = "badge-" + kind if kind in _SPECIAL_KINDS else ""
    note = ev.get("note")
    note_html = (_esc(note) if str(note or "").strip()
                 else '<span class="empty">（无备注）</span>')
    return (
        '<div class="%s" data-kind="%s">\n' % (css_class, _esc(kind))
        + '  <div class="ev-head">\n'
        + ('    <span class="seq">#%s</span>\n' % _esc(ev.get("seq", "—")))
        + ('    <span class="badge %s">%s</span>\n' % (badge, _esc(kind)))
        + ('    <span class="act">%s</span>\n'
           % _esc(ev.get("action") or "—"))
        + ('    <span class="src">source=%s</span>\n'
           % _esc(ev.get("source") or "—"))
        + ('    <span class="ts">ts=%s</span>\n'
           % _esc(ev.get("ts") or "—"))
        + '  </div>\n'
        + ('  <div class="ev-note">%s</div>\n' % note_html)
        + ('  <pre class="ev-payload">%s</pre>\n'
           % _payload_text(ev.get("payload")))
        + ('  <div class="ev-chain">pre_hash <code>%s</code> &rarr; '
           'post_hash <code>%s</code> &middot; digest <code>%s</code></div>\n'
           % (_hash_text(ev.get("pre_hash")),
              _hash_text(ev.get("post_hash")),
              _hash_text(ev.get("payload_digest"))))
        + '</div>\n'
    )


def _render_turn(turn_id, events):
    events = sorted(
        events,
        key=lambda e: int(e.get("seq") or 0) if isinstance(e, dict) else 0)
    pre = events[0].get("pre_hash") if events else None
    post = events[-1].get("post_hash") if events else None
    out = [
        '<div class="turn">\n',
        '  <div class="turn-head">\n',
        '    <span class="turn-id">%s</span>\n' % _esc(turn_id),
        '    <span>轮内事件 %d 条</span>\n' % len(events),
        '    <span>pre_hash <code>%s</code></span>\n' % _hash_text(pre),
        '    <span>post_hash <code>%s</code></span>\n' % _hash_text(post),
        '  </div>\n',
        '  <div class="turn-events">\n',
    ]
    for ev in events:
        out.append(_render_event(ev))
    out.append('  </div>\n</div>\n')
    return "".join(out)


def _render_summary(summary):
    if not isinstance(summary, dict):
        summary = {}
    turns = summary.get("turns", 0)
    events = summary.get("events", 0)
    failures = summary.get("failure_rate", 0)
    tokens = summary.get("tokens", 0)
    chain_ok = summary.get("chain_ok", True)
    try:
        fail_pct = "%.2f%%" % (float(failures) * 100)
    except (TypeError, ValueError):
        fail_pct = "0.00%"
    chain_label = "完整" if chain_ok else "异常"
    chain_css = "ok" if chain_ok else "bad"
    chain_desc = ("相邻事件的 post_hash 与下一条 pre_hash 衔接正常"
                  if chain_ok else
                  "检测到哈希链断点，请核查事件是否被篡改或漏写")
    return (
        '<section class="summary">\n'
        '  <h2>汇总（来自 TrajectoryStore.summarize）</h2>\n'
        '  <div class="cards">\n'
        + ('    <div class="card"><span>轮数</span><b>%s</b></div>\n'
           % _esc(turns))
        + ('    <div class="card"><span>事件数</span><b>%s</b></div>\n'
           % _esc(events))
        + ('    <div class="card"><span>失败率</span><b>%s</b></div>\n'
           % _esc(fail_pct))
        + ('    <div class="card"><span>token 合计</span><b>%s</b></div>\n'
           % _esc(tokens))
        + ('    <div class="card"><span>状态哈希链</span>'
           '<b class="%s">%s</b></div>\n' % (chain_css, _esc(chain_label)))
        + '  </div>\n'
        + ('  <p class="chain">状态哈希链：<b>%s</b> —— %s；'
           '每个事件卡片下方逐条列出 pre_hash &rarr; post_hash 与 payload_digest。'
           '</p>\n' % (_esc(chain_label), _esc(chain_desc)))
        + '</section>\n'
    )


def render(events, summary) -> str:
    """把事件流 + 汇总渲染成完整单文件 HTML 字符串。

    events: 事件 dict 列表（通常来自 TrajectoryStore.events()）
    summary: 汇总 dict（通常来自 TrajectoryStore.summarize()）
    空数据时生成友好占位页，不抛异常。
    """
    if not isinstance(events, (list, tuple)):
        events = []
    valid = [e for e in events if isinstance(e, dict)]

    grouped = {}
    for ev in valid:
        tid = str(ev.get("turn_id") or "t_000000")
        grouped.setdefault(tid, []).append(ev)

    timeline = [
        '<section class="timeline">\n',
        '  <h2>时间线（按轮）</h2>\n',
    ]
    if grouped:
        for tid in sorted(grouped):
            timeline.append(_render_turn(tid, grouped[tid]))
    else:
        timeline.append(
            '<div class="empty">\n'
            '  <b>暂无轨迹数据</b>\n'
            '  <p>传入的轨迹文件不存在，或里面还没有任何事件。</p>\n'
            '  <p>请先用 TrajectoryStore 记录几轮任务，再重新生成时间线。</p>\n'
            '</div>\n')
    timeline.append('</section>\n')

    toolbar = (
        '<div class="toolbar">\n'
        '  <button id="filterBtn" type="button">只看失败</button>\n'
        '  <span id="filterHint">显示全部事件</span>\n'
        '</div>\n')

    return _HEAD + _render_summary(summary) + toolbar + "".join(timeline) \
        + _TAIL


def main(argv=None):
    p = argparse.ArgumentParser(
        description="把 TrajectoryStore 事件流渲染成单文件 HTML 时间线")
    p.add_argument("--trace", required=True, help="trajectory.jsonl 路径")
    p.add_argument("--out", required=True, help="输出 HTML 路径")
    p.add_argument("--max-events", type=int, default=500,
                   help="最多渲染多少条事件（默认 500）")
    args = p.parse_args(argv)

    # 不存在的路径 -> 空事件流，生成友好占位页，绝不崩、不创建轨迹文件
    if os.path.isfile(args.trace):
        store = _traj.TrajectoryStore(path=args.trace)
        events = store.events(limit=max(0, args.max_events))
        summary = store.summarize()
    else:
        events, summary = [], {
            "turns": 0, "events": 0, "failure_rate": 0.0,
            "tokens": 0, "chain_ok": True, "kinds": {},
        }

    page = render(events, summary)
    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(page)
    print("已生成时间线：%s（事件 %d 条）" % (os.path.abspath(args.out),
                                       len(events)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
