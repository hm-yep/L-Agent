# -*- coding: utf-8 -*-
"""单元测试：动作契约单一真源 framework/action_registry.py

覆盖：validate() 在真实实现上为空、30 个动作都有合法 effect、必填参数与
action_parser.REQUIRED_PARAMS 一致、effect_journal.coverage() 无 unknown 且
分类与契约一致、skill_store.SKILL_SAFE 等于契约里 skill=True 的集合、
catalog_text() 覆盖全部动作且输出稳定；以及最关键的负向验证——临时篡改
真实实现的契约数据，validate() 必须报出问题，证明检查不是摆设。

为什么不需要联网：本模块及它比对的 action_parser / effect_journal /
skill_store 都是纯数据 + 纯函数，validate() 只做集合与字典比较，没有任何
网络、Qt 或 LLM 调用。所有篡改都走 monkeypatch.context()（自动恢复），
不改任何磁盘文件。
"""

import pytest

from framework import action_parser, action_registry as reg
from framework import effect_journal, skill_store


def test_validate_is_empty_on_the_real_contract():
    assert reg.validate() == []


def test_registry_has_exactly_32_actions():
    assert len(reg.ACTION_SPECS) == 32
    assert len(reg.all_actions()) == 32
    assert reg.all_actions() == set(reg.ACTION_SPECS)


def test_every_action_has_a_legal_effect_class():
    for name, spec in reg.ACTION_SPECS.items():
        assert {"params", "effect", "skill", "desc"} <= set(spec), name
        assert reg.effect_class(name) in reg.EFFECT_CLASSES, name
        assert isinstance(reg.is_skill_safe(name), bool), name
        assert isinstance(reg.required_params(name), list), name


def test_required_params_match_action_parser():
    for name in reg.all_actions():
        want = set(reg.required_params(name))
        got = set(action_parser.REQUIRED_PARAMS.get(name, []))
        assert want == got, name


def test_effect_journal_coverage_has_no_unknown():
    cov = effect_journal.coverage(action_parser.VALID_ACTIONS)
    assert cov["unknown"] == []


def test_effect_classification_matches_contract():
    cov = effect_journal.coverage(action_parser.VALID_ACTIONS)
    classified = {}
    for key in reg.EFFECT_CLASSES:
        for name in cov.get(key, []):
            classified[name] = key
    for name in reg.all_actions():
        assert classified.get(name) == reg.effect_class(name), name


def test_skill_safe_equals_contract_skill_true():
    want = {n for n, spec in reg.ACTION_SPECS.items() if spec["skill"]}
    assert set(skill_store.SKILL_SAFE) == want
    assert want == {n for n in reg.all_actions() if reg.is_skill_safe(n)}


def test_skill_safe_and_refused_are_disjoint():
    assert not (set(skill_store.SKILL_SAFE) & set(skill_store.SKILL_REFUSED))
    assert set(skill_store.SKILL_SAFE) <= set(action_parser.VALID_ACTIONS)


def test_catalog_text_contains_all_action_names():
    text = reg.catalog_text()
    for name in reg.all_actions():
        assert name in text, name
    assert "共 32 个动作" in text


def test_catalog_text_is_stable_and_sorted():
    first = reg.catalog_text()
    second = reg.catalog_text()
    assert first == second
    assert first
    lines = first.splitlines()
    names = [ln.split("：")[0].lstrip("- ") for ln in lines[1:]]
    assert names == sorted(names)
    assert len(names) == 32


# ---- 负向验证：篡改真实实现，validate() 必须能报出问题 ----
def test_negative_missing_action_is_reported(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(action_parser, "VALID_ACTIONS",
                  set(action_parser.VALID_ACTIONS) - {"show_bubble"})
        problems = reg.validate()
        assert any("动作池缺少" in p and "show_bubble" in p
                   for p in problems)
    assert reg.validate() == []      # 自动恢复后契约重新一致


def test_negative_wrong_required_params_is_reported(monkeypatch):
    with monkeypatch.context() as m:
        mutated = dict(action_parser.REQUIRED_PARAMS)
        mutated["open_app"] = ["WRONG_PARAM"]
        m.setattr(action_parser, "REQUIRED_PARAMS", mutated)
        problems = reg.validate()
        assert any("open_app" in p and "必填参数不一致" in p
                   for p in problems)
    assert reg.validate() == []


def test_negative_illegal_skill_safe_is_reported(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(skill_store, "SKILL_SAFE",
                  set(skill_store.SKILL_SAFE) | {"ghost_action"})
        problems = reg.validate()
        assert problems
        assert any("技能白名单" in p for p in problems)
        assert any("ghost_action" in p for p in problems)
    assert reg.validate() == []
