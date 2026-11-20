# -*- coding: utf-8 -*-
"""单元测试：形式化动作契约 framework/effect_spec.py

覆盖：validate_specs() 在真实实现上为空、32 动作全覆盖、effect/observables
与 coverage()/_TOUCHES 一致、负向 monkeypatch（删动作 / 加新动作 / 必填参数
漂移）能被报错、check_pre 各动作参数的合法与非法、check_post 能发现「声明
无副作用却改动了世界」且绝不抛异常、involution_check 全通过且同 seed 确定、
inverse_ops 与 effect_journal.undo_ops 行为一致。

不联网、不调模型、不写任何 data/ 文件：本模块与它比对的都是纯数据 + 纯函数，
involution_check 用的是纯内存假手。所有篡改都走 monkeypatch.context() 自动恢复。
"""

import pytest

from framework import (action_parser, action_registry as reg,
                       effect_journal, effect_spec)
from framework.action_parser import VALID_ACTIONS


# ---- 契约自洽 / CI 卡口 ----
def test_validate_specs_empty_on_real_impl():
    assert effect_spec.validate_specs() == []


def test_specs_cover_all_32_actions():
    assert len(effect_spec.SPECS) == 32
    assert set(effect_spec.SPECS) == reg.all_actions() == set(VALID_ACTIONS)
    for name in reg.all_actions():
        s = effect_spec.spec_for(name)
        assert {"observables", "reversible", "idempotent", "inverse",
                "pre", "post", "reason"} <= set(s), name


def test_classification_and_observables_match_coverage():
    cov = effect_journal.coverage(VALID_ACTIONS)
    classified = {}
    for key in ("reversible", "restorable", "blind", "no_effect", "undo"):
        for name in cov[key]:
            classified[name] = key
    touches = effect_journal._TOUCHES
    for name in reg.all_actions():
        s = effect_spec.spec_for(name)
        assert s["effect"] == classified[name], name
        # restorable 也撤得回来（靠副本账本），所以 reversible 同为 True，
        # 它与 reversible 的区别只在逆操作来源，不在「能不能撤」。
        assert s["reversible"] is (
            classified[name] in ("reversible", "restorable")), name
        assert sorted(s["observables"]) == sorted(touches.get(name, ())), name
        assert set(s["observables"]) <= set(effect_journal.WORLD_KEYS), name


def test_negative_deleting_an_action_is_reported(monkeypatch):
    with monkeypatch.context() as m:
        mutated = dict(reg.ACTION_SPECS)
        del mutated["show_bubble"]
        m.setattr(reg, "ACTION_SPECS", mutated)
        problems = effect_spec.validate_specs()
        assert problems
        assert any("show_bubble" in p for p in problems)
    assert effect_spec.validate_specs() == []       # 自动恢复后重新一致


def test_negative_new_action_without_spec_is_reported(monkeypatch):
    """CI 卡口的核心：动作池新增一个动作却漏写 spec，必须被报出来。"""
    with monkeypatch.context() as m:
        mutated = dict(reg.ACTION_SPECS)
        mutated["ghost_action"] = {"params": [], "effect": "no_effect",
                                   "skill": False, "desc": "新增但没写契约"}
        m.setattr(reg, "ACTION_SPECS", mutated)
        problems = effect_spec.validate_specs()
        assert any("ghost_action" in p and "缺少 EffectSpec" in p
                   for p in problems)
    assert effect_spec.validate_specs() == []


def test_negative_required_params_drift_is_reported(monkeypatch):
    with monkeypatch.context() as m:
        mutated = dict(action_parser.REQUIRED_PARAMS)
        mutated["open_app"] = ["WRONG_PARAM"]
        m.setattr(action_parser, "REQUIRED_PARAMS", mutated)
        problems = effect_spec.validate_specs()
        assert any("open_app" in p and "REQUIRED_PARAMS" in p
                   for p in problems)
    assert effect_spec.validate_specs() == []


def test_blind_specs_explain_why_irreversible():
    cov = effect_journal.coverage(VALID_ACTIONS)
    assert cov["blind"]
    for name in cov["blind"]:
        s = effect_spec.spec_for(name)
        assert s["reversible"] is False, name
        assert "撤" in s["reason"] and len(s["reason"]) > 8, name


def test_reversible_specs_all_carry_inverse():
    cov = effect_journal.coverage(VALID_ACTIONS)
    assert cov["reversible"]
    for name in cov["reversible"]:
        assert effect_spec.spec_for(name)["inverse"], name


def test_spec_for_returns_independent_copy():
    s = effect_spec.spec_for("set_volume")
    assert s["observables"] == ["volume"]
    s["observables"].append("facts")
    s["params"].append("injected")
    assert effect_spec.SPECS["set_volume"]["observables"] == ["volume"]
    assert effect_spec.SPECS["set_volume"]["params"] == ["volume"]
    assert effect_spec.spec_for("no_such_action")["observables"] == []


# ---- 执行前断言 ----
def test_check_pre_set_volume_valid():
    assert effect_spec.check_pre({"action": "set_volume", "volume": 50}, {}) == []
    assert effect_spec.check_pre({"action": "set_volume", "volume": "0"}, {}) == []
    assert effect_spec.check_pre({"action": "set_volume", "volume": 100}, {}) == []


def test_check_pre_set_volume_out_of_range():
    for bad in (-1, 101, 150):
        problems = effect_spec.check_pre(
            {"action": "set_volume", "volume": bad}, {})
        assert any("0~100" in p or "越界" in p for p in problems), bad


def test_check_pre_set_volume_not_a_number():
    problems = effect_spec.check_pre(
        {"action": "set_volume", "volume": "loud"}, {})
    assert any("数字" in p for p in problems)


def test_check_pre_path_required():
    assert effect_spec.check_pre(
        {"action": "read_doc", "path": "C:/a.docx"}, {}) == []
    assert effect_spec.check_pre(
        {"action": "analyze_data", "path": "C:/a.xlsx"}, {}) == []
    for action in ("read_doc", "analyze_data"):
        problems = effect_spec.check_pre({"action": action}, {})
        assert any("path" in p for p in problems), action


def test_check_pre_text_required():
    assert effect_spec.check_pre(
        {"action": "todo_add", "text": "买菜"}, {}) == []
    assert effect_spec.check_pre(
        {"action": "remind", "text": "开会"}, {}) == []
    for action in ("todo_add", "remind"):
        problems = effect_spec.check_pre({"action": action, "text": "  "}, {})
        assert any("text" in p for p in problems), action


def test_check_pre_create_doc_type():
    for good in ("ppt", "word", "xlsx", "pdf", "网页"):
        assert effect_spec.check_pre(
            {"action": "create_doc", "doc_type": good}, {}) == [], good
    problems = effect_spec.check_pre(
        {"action": "create_doc", "doc_type": "unknown_kind"}, {})
    assert any("doc_type" in p for p in problems)


def test_check_pre_never_raises_on_junk():
    for action in (None, "not a dict", 42, {}, {"action": "ghost"}):
        out = effect_spec.check_pre(action, None)
        assert isinstance(out, list)


# ---- 执行后断言 ----
def test_check_post_detects_no_effect_world_change():
    before = effect_journal.normalize(volume=50)
    after = effect_journal.normalize(volume=50, facts=["偷偷记住了一条"])
    problems = effect_spec.check_post({"action": "web_search"}, before, after)
    assert any("声明无副作用却改动了世界" in p for p in problems)
    assert effect_spec.check_post({"action": "web_search"},
                                  before, before) == []


def test_check_post_reports_missing_observable_change():
    w = effect_journal.normalize(volume=50)
    problems = effect_spec.check_post({"action": "set_volume"}, w, w)
    assert any("声明了副作用但未观测到" in p for p in problems)
    changed = effect_journal.normalize(volume=20)
    assert effect_spec.check_post({"action": "set_volume"}, w, changed) == []


def test_check_post_never_raises_on_junk():
    for args in ((None, None, None),
                 ({"action": "web_search"}, "junk", "junk"),
                 ({}, [], {}),
                 ("not a dict", {}, {})):
        out = effect_spec.check_post(*args)
        assert isinstance(out, list)


# ---- 逆操作与「撤销是对合」的属性证据 ----
def test_inverse_ops_matches_undo_ops():
    before = effect_journal.normalize(volume=40)
    after = effect_journal.normalize(
        volume=70, todos=[("买菜", False)], facts=["我在杭州"])
    items = effect_journal.diff(before, after)
    assert items
    assert effect_spec.inverse_ops(items) == effect_journal.undo_ops(items)
    assert effect_spec.inverse_ops(None) == []


def test_involution_check_all_pass():
    result = effect_spec.involution_check(seed=7, trials=100)
    assert result["trials"] == 100
    assert result["passed"] == 100
    assert result["failed"] == 0
    assert result["seed"] == 7


def test_involution_check_is_deterministic():
    a = effect_spec.involution_check(None, None, None, seed=123, trials=40)
    b = effect_spec.involution_check(None, None, None, seed=123, trials=40)
    assert a == b
    assert a["failed"] == 0
    c = effect_spec.involution_check(seed=999, trials=40)
    assert c["failed"] == 0
    assert c["seed"] == 999


def test_involution_check_with_custom_ops_and_template():
    template = effect_journal.normalize(volume=30, todos=[("旧事", False)],
                                        facts=["旧记忆"])
    result = effect_spec.involution_check(
        ops=["set_volume", "todo_add", "remember"], api={}, world=template,
        seed=5, trials=50)
    assert result["failed"] == 0
    assert result["passed"] == 50


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
