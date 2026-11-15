# -*- coding: utf-8 -*-
"""端到端集成：偏好发现 → 行为调整 → 问题归纳 → 任意轮回滚 → 时间线。

这些测试守的是「接线」，不是单个模块的纯函数：
  * 说一句普通的话，偏好要真的被发现、进轨迹、进系统提示词；
  * 使用中的失败要真的被归纳成规则，并改变下一次的行为；
  * 任意轮回滚要真的调用逆操作把世界改回去；
  * 整条事件流的哈希链要接得上，还能渲染成可看的时间线。

不联网、不调模型、不跑 Qt 事件循环；数据全部落在 conftest 设置的
临时 LINGLING_DATA_DIR 里，绝不写脏用户 data/。
"""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from framework import config  # noqa: E402
from framework.core import Framework  # noqa: E402


@pytest.fixture
def fw():
    QApplication.instance() or QApplication([])
    f = Framework.instance()
    for store in (f.trajectory, f.prefs, f.rules):
        if store is not None:
            store.clear()
    f.tasks.todos = []
    f.tasks.reminders = []
    f.tasks.deliveries = []
    f._last_undo = None
    f.memory.facts = []
    yield f
    for store in (f.trajectory, f.prefs, f.rules):
        if store is not None:
            store.clear()


def _ledger(text):
    return {"items": [{"kind": "todo_add", "reversible": True,
                       "label": f"新待办「{text}」",
                       "undo": {"op": "remove_todo", "text": text}}],
            "spots": [], "before": {}}


def test_preference_is_discovered_without_being_asked(fw):
    """用户没说「记住」，只是聊了一句，偏好也要被发现并留证。"""
    fw._begin_turn("我喜欢深色 PPT")
    fw._learn_preferences("我喜欢深色 PPT")

    assert fw.prefs.get("doc_theme") is not None
    assert "用户偏好" in fw.prefs.as_prompt()
    kinds = [e["kind"] for e in fw.trajectory.events()]
    assert "turn_start" in kinds and "preference" in kinds
    # 隐私：事件流里不能出现用户原话
    raw = fw.trajectory.path
    assert "我喜欢深色 PPT" not in Path(raw).read_text(encoding="utf-8")


def test_preference_really_changes_document_and_prompt(fw):
    """偏好不是存着好看：它要进渲染样式，也要进模型上下文。"""
    fw._begin_turn("我喜欢深色 PPT")
    fw._learn_preferences("我喜欢深色 PPT")
    style = fw.prefs.doc_style()
    assert style, "深色偏好应产出可用的样式字段"

    from framework.doc_generator import resolve_style
    assert resolve_style({"style": style}), "样式必须能被渲染器解析"

    from framework.ai_engine import AIWorker
    worker = AIWorker("你好", [], "活泼", prefs_text=fw.prefs.as_prompt())
    system_msg = worker._messages()[0]["content"]
    assert "用户偏好" in system_msg

    # 主链路确实调用了偏好样式（防「模块有了但没接线」的回归）
    core_src = Path("framework/core.py").read_text(encoding="utf-8")
    assert "self.prefs.doc_style()" in core_src
    assert "_learn_preferences(text)" in core_src


def test_changing_mind_overrides_old_preference(fw):
    fw._begin_turn("我喜欢深色 PPT")
    fw._learn_preferences("我喜欢深色 PPT")
    first = fw.prefs.get("doc_theme")
    fw._begin_turn("算了，以后别用深色了，简洁点")
    fw._learn_preferences("算了，以后别用深色了，简洁点")
    assert fw.prefs.get("doc_theme") != first or \
        fw.prefs.verbosity() == "concise"
    assert len(fw.trajectory.events(kind="preference")) >= 2


def test_repeated_failures_become_a_rule(fw):
    """两次同类问题才归纳成规则；一次偶发失败不该改行为。"""
    from framework.evolution import RULE_FAMILIES

    fw._begin_turn("读一下不存在的文件")
    fw._note_plan_failure("文件不存在：报告.docx")
    fw._traj_end(ok=False)
    assert not fw.rules.all(), "单条证据不该生成规则"

    fw._begin_turn("再读一次那个文件")
    fw._note_plan_failure("文件不存在：报告.docx")
    fw._traj_end(ok=False)
    rules = fw.rules.all()
    assert rules, "两条独立证据应归纳出规则"
    assert any(r["family"] in RULE_FAMILIES for r in rules)
    assert fw.trajectory.events(kind="rule"), "归纳结果要回到轨迹里"


def test_learned_rule_changes_next_action(fw):
    """规则要作用到下一次执行，而不是只躺在文件里。"""
    for i in range(2):
        fw._begin_turn(f"第 {i} 次搜索没资料")
        fw._traj_event("quality", action="web_search", source="system",
                       ok=False, note="资料与查询关键词零重合")
        fw._traj_end(ok=False)
    assert fw.rules.all(), "应先学到规则"

    original = {"action": "web_search", "query": "新能源"}
    adjusted = fw._adjust_action(dict(original))
    assert adjusted is not None
    assert adjusted["query"] != original["query"], \
        "换词规则应真的改写下一次的搜索词"


def test_rollback_to_any_turn_restores_the_world(fw):
    """不只撤上一轮：可以回滚到任意轮，逆操作真的执行。"""
    t1 = fw.trajectory.start_turn(pre_hash="h0")
    fw.tasks.add_todo("第一条")
    fw.trajectory.end_turn(post_hash="h1", undo_ledger=_ledger("第一条"))

    fw.trajectory.start_turn(pre_hash="h1")
    fw.tasks.add_todo("第二条")
    fw.trajectory.end_turn(post_hash="h2", undo_ledger=_ledger("第二条"))

    texts = [t["text"] for t in fw.tasks.todos]
    assert "第一条" in texts and "第二条" in texts

    res = fw.rollback_to(t1)
    assert res["ok"] is True
    assert res["turns"] == 2
    assert res["ops"] == 2
    after = [t["text"] for t in fw.tasks.todos]
    assert "第一条" not in after and "第二条" not in after
    assert fw.trajectory.events(kind="undo"), "回滚本身也要留痕"


def test_hash_chain_and_timeline_render(fw):
    from tools.trace_view import render

    fw._begin_turn("加个待办：交周报")
    fw.tasks.add_todo("交周报")
    fw._review_effects(offer=False)
    events = fw.trajectory.events()
    assert len(events) >= 2
    assert fw.trajectory.chain_ok(), "首尾状态哈希必须接得上"

    html = render(events, fw.trajectory.summarize())
    assert "时间线" in html
    assert "交周报" not in html, "时间线不该泄露用户原话"
    assert "<script>alert" not in html


def test_adaptive_switches_can_be_turned_off(fw, monkeypatch):
    """关掉开关后，偏好/进化不得再影响行为（可降级是硬要求）。"""
    monkeypatch.setattr(config, "PREFERENCE_LEARNING", False)
    monkeypatch.setattr(config, "EVOLUTION_ENABLED", False)
    fw._begin_turn("我喜欢深色 PPT")
    fw._learn_preferences("我喜欢深色 PPT")
    assert not fw.trajectory.events(kind="preference")
    action = {"action": "web_search", "query": "新能源"}
    assert fw._adjust_action(dict(action)) == action
