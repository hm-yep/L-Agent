# -*- coding: utf-8 -*-
"""单元测试：从问题事件里归纳规则，并让规则真正作用到下一次执行。

隔离原则：所有 RuleStore 都注入 tmp_path，绝不写脏真实 data/；
改 MAX_RULES / app_data_dir 一律用 monkeypatch，跑完自动还原。
真断言，不写 assert True。
"""

import ast
import inspect
import os

import pytest

import framework.evolution as evo
from framework.evolution import RULE_FAMILIES, RuleStore, summarize_rules


# ------------------------------------------------------------
# 脚手架：一类问题 = 一组同模式事件；ctx = 该类问题的执行前上下文
# ------------------------------------------------------------
FAMILY_EVENT = {
    "search_too_broad": dict(
        kind="quality", action="web_search",
        note="资料与查询关键词零重合，质量不达标"),
    "doc_too_thin": dict(
        kind="quality", action="create_doc",
        note="要点过于干瘪，正文不足",
        payload={"doc_type": "ppt", "reason": "全篇没有要点"}),
    "path_unreadable": dict(
        kind="failure", action="read_doc",
        note="文件不存在，读不出",
        payload={"path": "C:/Users/Wang/Desktop/报告.docx",
                 "reason": "文件不存在"}),
    "user_undid": dict(
        kind="undo", action="create_doc", source="user",
        note="用户撤销了刚才的操作",
        payload={"undone_action": "create_doc"}),
    "action_timeout": dict(
        kind="failure", action="web_search",
        note="请求超时，等太久"),
}

FAMILY_CONTEXT = {
    "search_too_broad": {"action": "web_search", "query": "新能源",
                         "doc_type": "ppt"},
    "doc_too_thin": {"action": "create_doc", "doc_type": "ppt"},
    "path_unreadable": {"action": "read_doc", "path": "报告.docx"},
    "user_undid": {"action": "create_doc"},
    "action_timeout": {"action": "web_search"},
}

EXPECTED_ADJ = {
    "search_too_broad": {"search_suffix": "最新 数据"},
    "doc_too_thin": {"min_slides": 8, "min_bullet_len": 12},
    "path_unreadable": {"skip_action": True},
    "user_undid": {"require_confirm": True},
    "action_timeout": {"retry_budget": 1},
}

REQUIRED_FIELDS = {
    "id", "family", "pattern", "adjustment", "triggers", "successes",
    "failures", "confidence", "enabled", "evidence", "created_at",
    "updated_at", "disabled_reason",
}


def make_event(family, seq, **overrides):
    """构造一条形状完整的 trajectory 事件（字段齐全，含绝对路径用于脱敏测试）。"""
    base = dict(FAMILY_EVENT[family])
    payload = dict(base.get("payload") or {})
    event = {
        "turn_id": "t_000001",
        "seq": seq,
        "event_id": "t_000001#%d" % seq,
        "ts": 1700000000.0 + seq,
        "kind": base["kind"],
        "source": base.get("source", "system"),
        "action": base.get("action"),
        "payload": payload,
        "payload_digest": "dg%04d" % seq,
        "pre_hash": None,
        "post_hash": None,
        "duration_ms": 0,
        "tokens": 0,
        "parent_id": None,
        "ok": None,
        "note": base.get("note", ""),
    }
    event.update(overrides)
    return event


def pair(family, start=0, n=2):
    """n 条同族同模式、event_id 互不相同的独立事件。"""
    return [make_event(family, start + i + 1) for i in range(n)]


def strip_ts(rules):
    """排除时间戳后比较规则内容（时间戳依赖挂钟，不参与确定性判定）。"""
    return [{k: v for k, v in r.items()
             if k not in ("created_at", "updated_at")} for r in rules]


@pytest.fixture
def rule_path(tmp_path):
    return str(tmp_path / "rules.json")


@pytest.fixture
def store(rule_path):
    return RuleStore(path=rule_path)


# ============================================================
# 一、规则族与归纳
# ============================================================
def test_rule_families_contains_all_five():
    assert RULE_FAMILIES == {
        "search_too_broad", "doc_too_thin", "path_unreadable",
        "user_undid", "action_timeout",
    }


@pytest.mark.parametrize("family", sorted(RULE_FAMILIES))
def test_each_family_can_be_learned(store, family):
    out = store.learn(pair(family))
    assert len(out) == 1
    assert out[0]["family"] == family
    rules = store.all()
    assert len(rules) == 1
    assert rules[0]["id"] == out[0]["id"]
    assert rules[0]["family"] == family


@pytest.mark.parametrize("family", sorted(RULE_FAMILIES))
def test_each_family_adjustment_matches_spec(store, family):
    store.learn(pair(family))
    assert store.all()[0]["adjustment"] == EXPECTED_ADJ[family]


@pytest.mark.parametrize("family", sorted(RULE_FAMILIES))
def test_single_event_does_not_create_rule(store, family):
    assert store.learn(pair(family, n=1)) == []
    assert store.all() == []


def test_evidence_must_be_distinct_event_ids(store):
    one = make_event("action_timeout", 1)
    assert store.learn([one, dict(one)]) == []   # 同一 event_id 只算 1 条
    assert store.all() == []


def test_cross_batch_accumulation_reaches_threshold(store):
    assert store.learn(pair("action_timeout", start=0, n=1)) == []
    assert store.all() == []
    out = store.learn(pair("action_timeout", start=1, n=1))
    assert len(out) == 1
    assert out[0]["triggers"] == 2
    assert len(set(out[0]["evidence"])) == 2


def test_rule_has_all_required_schema_fields(store):
    store.learn(pair("user_undid"))
    rule = store.all()[0]
    assert REQUIRED_FIELDS.issubset(set(rule))
    assert rule["enabled"] is True
    assert rule["disabled_reason"] == ""
    assert rule["created_at"] <= rule["updated_at"]
    assert rule["successes"] == 0 and rule["failures"] == 0
    assert all(isinstance(e, str) for e in rule["evidence"])


# ============================================================
# 二、幂等 / 证据去重 / 确定性
# ============================================================
def test_learn_is_idempotent(store):
    first = store.learn(pair("search_too_broad"))
    rid = first[0]["id"]
    snapshot = store.get(rid)
    second = store.learn(pair("search_too_broad"))
    assert len(store.all()) == 1
    assert store.get(rid) == snapshot
    assert store.get(rid)["triggers"] == 2
    assert len(store.get(rid)["evidence"]) == 2
    assert second == first


def test_learn_accumulates_only_new_evidence(store):
    store.learn(pair("doc_too_thin", start=0))
    out = store.learn(pair("doc_too_thin", start=2))
    rule = out[0]
    assert rule["triggers"] == 4
    assert len(rule["evidence"]) == 4
    assert len(set(rule["evidence"])) == 4


def test_evidence_is_deduped_and_capped_at_20(store):
    events = pair("action_timeout", start=0, n=25)
    store.learn(events)
    rule = store.all()[0]
    assert rule["triggers"] == 25
    assert len(rule["evidence"]) == 20
    assert len(set(rule["evidence"])) == 20
    store.learn(events)                       # 重复喂入不虚涨
    assert store.all()[0]["triggers"] == 25
    assert len(store.all()[0]["evidence"]) == 20


def test_determinism_two_fresh_stores(tmp_path):
    events = pair("search_too_broad", start=0, n=3) + \
        pair("doc_too_thin", start=3, n=2)
    s1 = RuleStore(path=str(tmp_path / "a.json"))
    s2 = RuleStore(path=str(tmp_path / "b.json"))
    s1.learn(events)
    s2.learn(events)
    assert strip_ts(s1.all()) == strip_ts(s2.all())


def test_determinism_repeated_learn_is_identical(store):
    events = pair("action_timeout", start=0, n=2)
    first = store.learn(events)
    snapshot = store.all()
    second = store.learn(events)
    assert second == first
    assert store.all() == snapshot


# ============================================================
# 三、confidence 公式与成效反馈
# ============================================================
def test_confidence_formula_is_documented():
    doc = evo.__doc__ or ""
    assert "confidence" in doc
    assert "(1 + successes)" in doc          # 公式明确写在 docstring 里


def test_confidence_formula_is_exact():
    assert evo._confidence(0, 0) == 0.5
    assert evo._confidence(1, 0) == 0.6667
    assert evo._confidence(0, 1) == 0.3333
    assert evo._confidence(2, 2) == 0.5
    assert evo._confidence(3, 0) == 0.8


def test_confidence_initial_is_half(store):
    store.learn(pair("action_timeout"))
    assert store.all()[0]["confidence"] == 0.5


def test_confidence_rises_on_success(store):
    rid = store.learn(pair("action_timeout"))[0]["id"]
    c0 = store.get(rid)["confidence"]
    c1 = store.record_outcome(rid, True)["confidence"]
    c2 = store.record_outcome(rid, True)["confidence"]
    assert c0 < c1 < c2
    assert c1 == evo._confidence(1, 0)
    assert c2 == evo._confidence(2, 0)


def test_confidence_falls_on_failure(store):
    rid = store.learn(pair("action_timeout"))[0]["id"]
    c_up = store.record_outcome(rid, True)["confidence"]
    c_down = store.record_outcome(rid, False)["confidence"]
    assert c_down < c_up
    assert c_down == evo._confidence(1, 1)


def test_record_outcome_updates_fields_and_persists(store, rule_path):
    rid = store.learn(pair("search_too_broad"))[0]["id"]
    updated = store.record_outcome(rid, True)
    assert updated["successes"] == 1
    assert updated["failures"] == 0
    assert updated["confidence"] == evo._confidence(1, 0)
    store.record_outcome(rid, False)
    reopened = RuleStore(path=rule_path)       # 模拟重启
    rule = reopened.get(rid)
    assert rule["successes"] == 1
    assert rule["failures"] == 1
    assert rule["confidence"] == evo._confidence(1, 1)


def test_record_outcome_unknown_rule_returns_empty_dict(store):
    assert store.record_outcome("does-not-exist", True) == {}
    assert store.all() == []


# ============================================================
# 四、自动淘汰
# ============================================================
def test_auto_retire_after_three_failures(store):
    rid = store.learn(pair("action_timeout"))[0]["id"]
    for _ in range(3):
        store.record_outcome(rid, False)
    rule = store.get(rid)
    assert rule["failures"] == 3
    assert rule["successes"] == 0
    assert rule["enabled"] is False
    assert isinstance(rule["disabled_reason"], str)
    assert rule["disabled_reason"]            # 写了具体停用原因


def test_no_retire_when_there_is_a_success(store):
    rid = store.learn(pair("action_timeout"))[0]["id"]
    store.record_outcome(rid, True)
    for _ in range(3):
        store.record_outcome(rid, False)
    assert store.get(rid)["enabled"] is True


def test_retired_rule_is_not_applied(store):
    rid = store.learn(pair("action_timeout"))[0]["id"]
    for _ in range(3):
        store.record_outcome(rid, False)
    adj = store.apply(FAMILY_CONTEXT["action_timeout"])
    assert adj == {"rule_ids": []}
    assert "retry_budget" not in adj
    assert rid not in adj["rule_ids"]


# ============================================================
# 五、apply：命中 / 不命中 / 追溯 / 合并
# ============================================================
@pytest.mark.parametrize("family", sorted(RULE_FAMILIES))
def test_apply_hits_each_family(store, family):
    rid = store.learn(pair(family))[0]["id"]
    adj = store.apply(FAMILY_CONTEXT[family])
    assert adj["rule_ids"] == [rid]
    for key, value in EXPECTED_ADJ[family].items():
        assert adj[key] == value
    assert isinstance(adj.get("note"), str) and adj["note"]


def test_apply_no_match_returns_only_rule_ids(store):
    assert store.apply({"action": "web_search"}) == {"rule_ids": []}
    store.learn(pair("doc_too_thin"))
    # 学过文档规则，但这次上下文不是生成 PPT，不该命中
    assert store.apply({"action": "web_search", "query": "新能源"}) \
        == {"rule_ids": []}


def test_apply_rule_ids_are_traceable(store):
    store.learn(pair("search_too_broad"))
    adj = store.apply(FAMILY_CONTEXT["search_too_broad"])
    known = {r["id"] for r in store.all()}
    assert adj["rule_ids"]
    assert set(adj["rule_ids"]).issubset(known)
    assert store.get(adj["rule_ids"][0]) is not None


def test_apply_merges_multiple_rules(store):
    r1 = store.learn(pair("search_too_broad"))[0]["id"]
    r2 = store.learn(pair("action_timeout", start=2))[0]["id"]
    adj = store.apply({"action": "web_search"})
    assert set(adj["rule_ids"]) == {r1, r2}
    assert adj["search_suffix"] == "最新 数据"
    assert adj["retry_budget"] == 1
    assert "；" in adj["note"]


def test_apply_note_is_chinese_without_raw_text(store):
    store.learn(pair("search_too_broad"))
    note = store.apply(FAMILY_CONTEXT["search_too_broad"])["note"]
    assert "搜索" in note
    assert "Zebra9" not in note
    assert "C:/Users" not in note


def test_path_unreadable_uses_basename_fingerprint(store):
    store.learn(pair("path_unreadable"))
    assert store.all()[0]["pattern"] == {"basename": "报告.docx"}
    assert store.apply({"action": "read_doc",
                        "path": "C:/其它目录/报告.docx"})["skip_action"] is True
    assert store.apply({"action": "read_doc",
                        "path": "报告.docx"})["skip_action"] is True
    assert store.apply({"action": "read_doc",
                        "path": "别的.docx"}) == {"rule_ids": []}


def test_path_fingerprint_falls_back_to_digest_without_filename(store):
    events = [
        {"kind": "failure", "event_id": "f1", "note": "读不出",
         "payload": {"reason": "文件不存在"}, "payload_digest": "abc123"},
        {"kind": "failure", "event_id": "f2", "note": "读不出",
         "payload": {"reason": "文件不存在"}, "payload_digest": "abc123"},
    ]
    store.learn(events)
    rule = store.all()[0]
    assert "digest" in rule["pattern"]
    assert "basename" not in rule["pattern"]


def test_unspecified_action_scope_matches_any_action(store):
    events = [
        {"kind": "undo", "event_id": "u1", "note": "撤销",
         "action": "undo_last"},
        {"kind": "undo", "event_id": "u2", "note": "撤销",
         "action": "undo_last"},
    ]
    store.learn(events)
    assert store.all()[0]["pattern"]["action"] == "*"
    assert store.apply({"action": "create_doc"})["require_confirm"] is True
    assert store.apply({}) == {"rule_ids": []}


# ============================================================
# 六、生命周期 / 持久化 / 上限 / 隐私
# ============================================================
def test_persistence_roundtrip(rule_path):
    s1 = RuleStore(path=rule_path)
    rid = s1.learn(pair("doc_too_thin"))[0]["id"]
    s2 = RuleStore(path=rule_path)
    assert s2.get(rid)["family"] == "doc_too_thin"
    assert s2.apply(FAMILY_CONTEXT["doc_too_thin"])["min_slides"] == 8


def test_forget_removes_rule_and_clears_pending(store):
    rid = store.learn(pair("search_too_broad"))[0]["id"]
    assert store.forget(rid) is True
    assert store.forget(rid) is False
    assert store.get(rid) is None
    assert store.all() == []
    assert store.apply(FAMILY_CONTEXT["search_too_broad"]) == {"rule_ids": []}
    # 待定证据被一并清掉，只回喂 1 条旧证据不会让规则「复活」
    assert store.learn(pair("search_too_broad", n=1)) == []
    assert store.all() == []


def test_clear_resets_everything(store):
    rid = store.learn(pair("user_undid"))[0]["id"]
    store.record_outcome(rid, True)
    store.clear()
    assert store.all() == []
    assert store.get(rid) is None
    assert store.apply(FAMILY_CONTEXT["user_undid"]) == {"rule_ids": []}
    assert summarize_rules(store.all())["total"] == 0


def test_max_rules_cap_drops_weakest(store, monkeypatch):
    from framework import config
    monkeypatch.setattr(config, "MAX_RULES", 2, raising=False)
    store.learn(pair("action_timeout", start=0))
    store.learn(pair("doc_too_thin", start=2))
    store.learn(pair("search_too_broad", start=4))
    rules = store.all()
    assert len(rules) == 2
    families = {r["family"] for r in rules}
    assert "action_timeout" not in families       # 最老、置信最低 → 先丢
    assert "search_too_broad" in families         # 最新留下


def test_no_absolute_path_or_user_words_on_disk(rule_path):
    s = RuleStore(path=rule_path)
    events = pair("path_unreadable", start=0)
    for i, e in enumerate(events):
        e["note"] = "读不出 Zebra9暗号%d" % i
        e["payload"] = {
            "path": "C:/Users/Wang/Desktop/秘密目录/报告.docx",
            "reason": "文件不存在",
            "query": "新能源原话ABC不该落盘",
        }
    out = s.learn(events)
    assert len(out) == 1
    assert out[0]["pattern"] == {"basename": "报告.docx"}
    with open(rule_path, encoding="utf-8") as f:
        content = f.read()
    assert "C:/Users" not in content
    assert "秘密目录" not in content
    assert "Zebra9" not in content
    assert "新能源原话ABC不该落盘" not in content
    assert "报告.docx" in content               # 只留文件名指纹


def test_injected_path_never_touches_real_data_dir(tmp_path, monkeypatch):
    import framework.utils as utils

    def boom():
        raise AssertionError("path 注入时不应调用 app_data_dir")

    monkeypatch.setattr(utils, "app_data_dir", boom, raising=False)
    p = str(tmp_path / "injected.json")
    s = RuleStore(path=p)
    s.learn(pair("user_undid"))
    assert os.path.exists(p)
    assert len(s.all()) == 1


def test_corrupt_file_is_treated_as_empty(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{这不是 json", encoding="utf-8")
    s = RuleStore(path=str(p))
    assert s.all() == []
    assert s.apply({"action": "web_search"}) == {"rule_ids": []}
    s.learn(pair("action_timeout"))            # 还能正常恢复写入
    assert len(s.all()) == 1


# ============================================================
# 七、鲁棒性 / 统计 / 依赖
# ============================================================
def test_abnormal_events_never_crash(store):
    bad = [None, 123, 3.14, "x", [], {},
           {"kind": None},
           {"kind": "quality", "note": None, "payload": "not a dict"},
           {"kind": "failure", "note": 12345, "payload": None},
           {"kind": "undo", "payload": {"undone_action": 5}},
           object()]
    assert isinstance(store.learn(bad), list)
    assert isinstance(store.all(), list)
    assert isinstance(store.apply({"action": "web_search"}), dict)
    assert isinstance(store.apply(None), dict)
    assert isinstance(summarize_rules(bad), dict)


def test_summarize_rules_counts(store):
    r1 = store.learn(pair("search_too_broad", start=0))[0]
    r2 = store.learn(pair("action_timeout", start=2))[0]
    r3 = store.learn(pair("doc_too_thin", start=4))[0]
    store.record_outcome(r1["id"], True)
    store.record_outcome(r2["id"], False)
    for _ in range(3):
        store.record_outcome(r3["id"], False)   # r3 被淘汰
    s = summarize_rules(store.all())
    assert s["total"] == 3
    assert s["enabled"] == 2
    assert s["disabled"] == 1
    assert s["by_family"]["search_too_broad"] == 1
    assert s["by_family"]["action_timeout"] == 1
    assert s["by_family"]["doc_too_thin"] == 1
    assert s["by_family"]["user_undid"] == 0
    assert s["successes"] == 1
    assert s["failures"] == 4
    assert s["triggers"] == 6
    assert s["success_rate"] == round(1 / 5, 4)
    assert 0 < s["avg_confidence"] < 1


def test_summarize_rules_empty_and_robust():
    empty = summarize_rules([])
    assert empty["total"] == 0
    assert empty["avg_confidence"] == 0.0
    assert empty["success_rate"] == 0.0
    assert set(empty["by_family"]) == RULE_FAMILIES
    assert summarize_rules(None)["total"] == 0
    assert summarize_rules([None, 1, "x"])["total"] == 0


def test_module_stays_dependency_free():
    tree = ast.parse(inspect.getsource(evo))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert not ({"PyQt5", "requests", "pandas", "numpy"} & mods), \
        sorted(mods)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
