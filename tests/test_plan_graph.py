# -*- coding: utf-8 -*-
"""单元测试：计划依赖图 framework/plan_graph.py

覆盖：线性编译顺序正确、数据依赖识别、稳定拓扑序、环检测、affected_subgraph
只重算后继、validate 能发现非法 deps / 未知动作 / 不可逆未标记、needs_confirm
正确、steps 边界、空/异常输入不崩、plan_summary 统计正确。

真断言，不联网、不调模型、不写 data/；全部是纯 dict 进纯 dict 出。
"""

import pytest

from framework import plan_graph as pg


# ---- 编译 / 依赖推断 ----
def test_linear_compile_keeps_original_order():
    steps = ["开始", "检索资料", "生成报告", "收尾"]
    plan = pg.compile_plan(steps)
    assert plan["steps"] == steps
    assert [n["id"] for n in plan["nodes"]] == ["n0", "n1", "n2", "n3"]
    assert [n["deps"] for n in plan["nodes"]] == [[], ["n0"], ["n1"], ["n2"]]
    assert pg.topological_order(plan) == ["n0", "n1", "n2", "n3"]


def test_data_dependency_is_detected():
    plan = pg.compile_plan(["联网检索行业资料", "基于资料生成报告"])
    n0, n1 = plan["nodes"]
    assert n0["hints"]["action"] == "web_search"
    assert n0["hints"]["produces"] == "资料"
    assert n1["hints"]["action"] == "create_doc"
    assert n1["hints"]["consumes"] == "资料"
    assert "n0" in n1["hints"]["data_deps"]
    assert "n0" in n1["deps"]
    assert pg.validate(plan) == []


def test_explicit_order_words_are_annotated():
    plan = pg.compile_plan(["先检索资料", "然后汇总成报告"])
    assert plan["nodes"][0]["hints"]["explicit_order"] is True
    assert plan["nodes"][1]["hints"]["explicit_order"] is True
    assert "n0" in plan["nodes"][1]["hints"]["data_deps"]


def test_action_inference_can_return_none():
    plan = pg.compile_plan(["开始", "结束"])
    assert all(n["hints"]["action"] is None for n in plan["nodes"])


def test_edges_are_exactly_the_dependency_pairs():
    plan = pg.compile_plan(["联网检索资料", "基于资料生成报告", "整理成要点"])
    expected = {(d, n["id"]) for n in plan["nodes"] for d in n["deps"]}
    assert {(a, b) for a, b in plan["edges"]} == expected
    assert len(plan["edges"]) == len(expected)


# ---- 拓扑排序 / 环 ----
def test_topological_order_is_stable_and_same_layer_keeps_source_order():
    plan = {
        "nodes": [
            {"id": "c", "text": "C", "deps": [], "hints": {}},
            {"id": "a", "text": "A", "deps": [], "hints": {}},
            {"id": "b", "text": "B", "deps": ["c"], "hints": {}},
        ],
        "edges": [["c", "b"]],
        "steps": ["C", "A", "B"],
    }
    assert pg.topological_order(plan) == ["c", "a", "b"]
    assert pg.topological_order(plan) == pg.topological_order(plan)


def test_cycle_is_detected_and_raises():
    plan = {
        "nodes": [
            {"id": "a", "text": "A", "deps": ["b"], "hints": {}},
            {"id": "b", "text": "B", "deps": ["a"], "hints": {}},
        ],
        "edges": [["b", "a"], ["a", "b"]],
        "steps": ["A", "B"],
    }
    assert any("环" in p for p in pg.validate(plan))
    with pytest.raises(ValueError):
        pg.topological_order(plan)


def test_self_dependency_counts_as_cycle():
    plan = {
        "nodes": [{"id": "a", "text": "A", "deps": ["a"], "hints": {}},
                  {"id": "b", "text": "B", "deps": [], "hints": {}}],
        "edges": [["a", "a"]],
        "steps": ["A", "B"],
    }
    assert any("环" in p for p in pg.validate(plan))


# ---- 增量重算 ----
def test_affected_subgraph_only_contains_successors():
    plan = pg.compile_plan(["开始", "检索资料", "基于资料生成报告", "收尾"])
    result = pg.affected_subgraph(plan, "n1")
    assert result == ["n1", "n2", "n3"]
    assert "n0" not in result                    # 不受影响的前置不重算
    assert pg.affected_subgraph(plan, "n3") == ["n3"]


def test_affected_subgraph_unknown_node_is_empty():
    plan = pg.compile_plan(["开始", "结束"])
    assert pg.affected_subgraph(plan, "nope") == []
    assert pg.affected_subgraph(plan, None) == []


# ---- validate ----
def test_validate_reports_illegal_deps():
    plan = pg.compile_plan(["开始", "结束"])
    plan["nodes"][1]["deps"].append("n999")
    assert any("不存在" in p and "n999" in p for p in pg.validate(plan))


def test_validate_reports_unknown_action():
    plan = pg.compile_plan(["开始", "结束"])
    plan["nodes"][0]["hints"]["action"] = "ghost_action"
    assert any("未知动作" in p and "ghost_action" in p
               for p in pg.validate(plan))


def test_validate_reports_missing_needs_confirm_flag():
    plan = pg.compile_plan(["打开应用", "收尾"])
    assert pg.validate(plan) == []
    plan["nodes"][0]["hints"].pop("needs_confirm")
    assert any("needs_confirm" in p for p in pg.validate(plan))


def test_validate_reports_duplicate_ids():
    plan = pg.compile_plan(["开始", "结束"])
    plan["nodes"][1]["id"] = "n0"
    assert any("唯一" in p for p in pg.validate(plan))


def test_steps_boundary_2_to_6():
    assert any("步骤数" in p
               for p in pg.validate(pg.compile_plan(["only one"])))
    assert any("步骤数" in p
               for p in pg.validate(pg.compile_plan([str(i) for i in range(7)])))
    assert not any("步骤数" in p
                   for p in pg.validate(pg.compile_plan(["a", "b"])))
    assert not any("步骤数" in p
                   for p in pg.validate(pg.compile_plan(
                       [str(i) for i in range(6)])))


def test_needs_confirm_is_correct():
    plan = pg.compile_plan(["先打开应用", "然后关机"])
    assert pg.validate(plan) == []
    assert pg.plan_summary(plan)["needs_confirm"] == ["open_app", "shutdown"]
    assert all(n["hints"]["needs_confirm"] for n in plan["nodes"])


def test_custom_valid_actions_are_honoured():
    plan = pg.compile_plan(["检索资料", "生成报告"])
    # 把 web_search 从合法集合里拿掉，validate 应判它为未知动作
    problems = pg.validate(plan, valid_actions={"create_doc"})
    assert any("web_search" in p for p in problems)


# ---- 统计 / 健壮性 ----
def test_plan_summary_counts():
    plan = pg.compile_plan([
        "联网检索行业资料",
        "基于资料生成一份网页报告",
        "整理成要点加进待办",
        "然后打开应用查看",
    ])
    s = pg.plan_summary(plan)
    assert s["nodes"] == 4
    assert s["edges"] == 4
    assert s["data_deps"] == 3
    assert s["needs_confirm"] == ["open_app"]
    assert s["order"] == ["n0", "n1", "n2", "n3"]
    assert pg.validate(plan) == []


def test_empty_and_weird_inputs_do_not_crash():
    assert pg.compile_plan(None)["nodes"] == []
    assert pg.compile_plan([])["edges"] == []
    assert len(pg.compile_plan("abc")["nodes"]) == 1
    assert len(pg.compile_plan([1, None, {}])["nodes"]) == 3
    assert isinstance(pg.validate(None), list)
    assert pg.validate(None)
    assert pg.topological_order(None) == []
    assert pg.affected_subgraph(None, "x") == []
    assert pg.plan_summary(None)["nodes"] == 0
    assert pg.plan_summary(None)["order"] == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
