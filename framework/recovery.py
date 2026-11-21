# -*- coding: utf-8 -*-
"""失败分类与恢复策略：把散落在执行链里的兜底判断收拢成一张可测的表。

之前失败处理是各写各的：模型请求失败在 _on_ai_failed 里提示，动作超时在
看门狗里强制收尾，计划无推进在 _run_next_action_inner 里数轮次。行为都对，
但「哪类失败该重试、该重规划、还是该交还用户」只存在于代码分支里，既没法
单独测试，也回答不了「为什么这里重试、那里不重试」。

本模块把这件事显式化：

  * FAILURE_KINDS：七类失败，覆盖模型、工具、权限、超时、计划推进；
  * STRATEGY：每类失败的策略（与现有行为一致，不是另起一套）；
  * decide()：结合已重试 / 已重规划次数给出动作，纯函数、可单测；
  * record() / summarize()：失败事件的结构化记录，供轨迹和日志复用。

零 Qt、零网络：只依赖标准库，可在纯 Python 环境跑。
"""

FAILURE_KINDS = (
    "model_error",      # 模型请求失败：网络、鉴权、额度
    "invalid_output",   # 模型输出不合法：动作名 / 参数被剔除
    "tool_error",       # 工具执行抛异常或返回失败
    "precondition",     # 前置条件不满足，原步骤不可执行
    "timeout",          # 动作超过看门狗预算仍未收尾
    "permission",       # 权限拒绝，需要用户授权
    "no_progress",      # 计划连续多轮没有推进
)

RECOVERY_ACTIONS = ("retry", "replan", "rollback", "escalate", "abort")

# 策略表：与现有执行链行为一一对应。
# primary 是首选动作，fallback 是预算用尽后的兜底动作。
STRATEGY = {
    "model_error": {
        "primary": "escalate", "fallback": "abort",
        "reason": "模型请求失败，自动重试可能重复扣费，交还用户决定",
    },
    "invalid_output": {
        "primary": "retry", "fallback": "escalate",
        "reason": "非法动作已剔除，让模型下一轮重新组织",
    },
    "tool_error": {
        "primary": "replan", "fallback": "escalate",
        "reason": "工具执行失败，调整剩余步骤绕开这一步",
    },
    "precondition": {
        "primary": "replan", "fallback": "escalate",
        "reason": "前置条件不满足，原步骤不可执行，需要换路径",
    },
    "timeout": {
        "primary": "replan", "fallback": "escalate",
        "reason": "动作超时，先跳过再评估剩余步骤",
    },
    "permission": {
        "primary": "escalate", "fallback": "abort",
        "reason": "权限由用户掌握，系统不能替用户授权",
    },
    "no_progress": {
        "primary": "replan", "fallback": "escalate",
        "reason": "连续多轮没有推进，原计划已失效，需要重规划",
    },
}


def strategy(kind: str) -> dict:
    """取某类失败的策略；未知类型按「交还用户」兜底。"""
    return dict(STRATEGY.get(kind, {
        "primary": "escalate", "fallback": "abort",
        "reason": "未知失败类型，交还用户",
    }))


def decide(kind: str, *, attempts: int = 0, replans: int = 0,
           max_retries: int = 1, max_replans: int = 2):
    """给出恢复动作与理由。

    :return: (action, reason)，action 属于 RECOVERY_ACTIONS
    """
    rule = strategy(kind)
    primary = rule["primary"]
    fallback = rule["fallback"]
    if primary == "retry" and attempts >= max_retries:
        return fallback, "已重试 %d 次，改为 %s" % (attempts, fallback)
    if primary == "replan" and replans >= max_replans:
        return fallback, "已重规划 %d 次，改为 %s" % (replans, fallback)
    return primary, rule["reason"]


def record(kind: str, *, action: str = "", detail: str = "",
           attempt: int = 0) -> dict:
    """把一次失败结构化，写轨迹 / 日志时字段稳定。"""
    return {
        "kind": kind if kind in STRATEGY else "unknown",
        "action": str(action or ""),
        "detail": str(detail or "")[:200],
        "attempt": int(attempt),
    }


def summarize(records) -> str:
    """把失败记录汇总成一句人话，供状态栏或日志使用。"""
    records = list(records or [])
    if not records:
        return "本轮没有失败记录"
    counts = {}
    for item in records:
        kind = item.get("kind", "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    parts = ["%s×%d" % (kind, counts[kind]) for kind in sorted(counts)]
    return "失败 %d 次：%s" % (len(records), "、".join(parts))
