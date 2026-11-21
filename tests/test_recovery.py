# -*- coding: utf-8 -*-
"""失败分类与恢复策略测试：七类失败都有策略，预算与兜底行为可验证。"""

from framework import recovery


def test_every_failure_kind_has_valid_strategy():
    """七类失败都必须有策略，且动作在允许集合内。"""
    assert len(recovery.FAILURE_KINDS) == 7
    for kind in recovery.FAILURE_KINDS:
        rule = recovery.strategy(kind)
        assert rule["primary"] in recovery.RECOVERY_ACTIONS
        assert rule["fallback"] in recovery.RECOVERY_ACTIONS
        assert rule["reason"]


def test_plan_failures_replan_within_budget():
    """计划类失败在预算内应先重规划，不直接交还用户。"""
    for kind in ("tool_error", "precondition", "timeout", "no_progress"):
        action, _reason = recovery.decide(kind, replans=0, max_replans=2)
        assert action == "replan", kind


def test_plan_failures_escalate_when_budget_exhausted():
    """重规划次数用尽后必须交还用户，不能无限重试。"""
    for kind in ("tool_error", "precondition", "timeout", "no_progress"):
        action, reason = recovery.decide(kind, replans=2, max_replans=2)
        assert action == "escalate", kind
        assert "2" in reason


def test_model_and_permission_escalate_immediately():
    """模型失败和权限拒绝不自动重试：前者怕重复扣费，后者只能用户授权。"""
    assert recovery.decide("model_error")[0] == "escalate"
    assert recovery.decide("permission")[0] == "escalate"


def test_invalid_output_retries_then_escalates():
    """非法输出先给模型一次重新组织的机会，超过次数再交还用户。"""
    assert recovery.decide("invalid_output", attempts=0)[0] == "retry"
    assert recovery.decide("invalid_output", attempts=1,
                           max_retries=1)[0] == "escalate"


def test_record_and_summarize():
    """失败记录字段稳定，汇总能读。"""
    item = recovery.record("timeout", action="create_doc",
                           detail="x" * 500, attempt=1)
    assert item["kind"] == "timeout"
    assert item["action"] == "create_doc"
    assert len(item["detail"]) == 200
    assert item["attempt"] == 1

    text = recovery.summarize([item, recovery.record("no_progress")])
    assert "失败 2 次" in text
    assert "timeout×1" in text
    assert recovery.summarize([]) == "本轮没有失败记录"
