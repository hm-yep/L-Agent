# -*- coding: utf-8 -*-
"""计划执行轮不许问本地微调 + 零推进不自旋。

背景（真机复现）：用户在计划卡上点「开始执行」后计划卡一直停在 0/4，
日志里每轮都只有一个 jump 动作。根因是本地微调适配器的训练集是 41 条
单指令样本，喂它越界的「计划执行」提示时它退化成背诵训练动作（实测只回
一个 jump）；jump 合动作协议，所以协议检查放行，可它推不动计划，计划就
原地打转到轮次预算耗尽。

这里钉三件事：
  * 计划轮 / 重规划轮一律走云端，不问本地微调；
  * 普通轮次仍然是「本地优先」，不能被这次改动连累；
  * 计划轮跑了动作却一步没推进，连续几轮就按「步骤受阻」处理（走重规划），
    不再把轮次预算耗光、让用户看着计划卡原地打转。

不联网、不调模型、不跑 Qt 事件循环；数据全部落在 conftest 设置的临时
LINGLING_DATA_DIR 里。
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

import framework.core as core_mod  # noqa: E402
from framework import config  # noqa: E402
from framework import lora_engine  # noqa: E402
from framework.ai_engine import AIWorker  # noqa: E402
from framework.core import Framework  # noqa: E402

# 持有应用引用，避免 Qt 在多个用例之间回收 Framework 的 C++ 对象。
_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def fw():
    f = Framework.instance()
    f._plan = None
    f._plan_done = 0
    f._plan_done_at_round_start = 0
    f._plan_no_progress_rounds = 0
    f._plan_round_had_actions = False
    f._plan_round_pending = False
    f._plan_auto_plan = False
    f._plan_fail_note = ""
    f._action_queue = []
    f._busy = False
    yield f
    f._plan = None
    f._plan_round_pending = False
    f._plan_auto_plan = False
    f._action_queue = []
    f._busy = False


@pytest.fixture
def no_timers(monkeypatch):
    """把 QTimer.singleShot 掐掉：测试只关心状态推进，不让定时器真去跑。"""
    monkeypatch.setattr(core_mod.QTimer, "singleShot",
                        staticmethod(lambda *a, **k: None))


# ============================================================
# 一、provider 路由：计划轮不走本地微调
# ============================================================
def test_plan_round_worker_refuses_local_model(monkeypatch):
    """allow_lora=False 的轮次：本地通路一次都不许被问。"""
    monkeypatch.setattr(config, "LORA_ENABLED", True)

    def _boom(*a, **k):
        raise AssertionError("计划轮不该问本地微调")

    monkeypatch.setattr(lora_engine, "generate_for", _boom)
    monkeypatch.setattr(
        "framework.ai_engine.AIWorker._request_deepseek",
        lambda self: [{"action": "read_doc", "path": "1.txt"}])
    worker = AIWorker("【计划执行】继续推进", [], "活泼", allow_lora=False)
    actions = worker._request_model()
    assert actions == [{"action": "read_doc", "path": "1.txt"}]
    assert worker.provider_used != "lora"


def test_normal_round_still_prefers_local_model(monkeypatch):
    """普通轮次不受影响：仍然是本地优先。"""
    monkeypatch.setattr(config, "LORA_ENABLED", True)
    monkeypatch.setattr(
        lora_engine, "generate_for",
        lambda *a, **k: '[{"action": "open_app", "app": "计算器"}]')

    def _boom(*a, **k):
        raise AssertionError("本地答得上来，不该去调云端")

    monkeypatch.setattr("framework.ai_engine.AIWorker._request_deepseek", _boom)
    worker = AIWorker("帮我打开计算器", [], "活泼")
    actions = worker._request_model()
    assert actions == [{"action": "open_app", "app": "计算器"}]
    assert worker.provider_used == "lora"


def test_plan_round_starts_worker_without_lora(fw, monkeypatch):
    """计划执行轮启动 worker 时明确关掉本地微调。"""
    captured = {}

    def _fake_start(message, **kwargs):
        captured["message"] = message
        captured.update(kwargs)
        return None

    monkeypatch.setattr(core_mod.Framework, "_start_ai_worker",
                        staticmethod(_fake_start))
    fw._plan = {"steps": ["读取桌面 1.txt", "联网搜索"], "status": "running",
                "rounds": 0}
    fw._plan_next_round()
    assert captured.get("allow_lora") is False
    assert "【计划执行】" in captured["message"]


def test_replan_round_starts_worker_without_lora(fw, monkeypatch):
    """重规划轮同样关掉本地微调：改步骤也得靠真能改的模型。"""
    captured = {}

    def _fake_start(message, **kwargs):
        captured["message"] = message
        captured.update(kwargs)
        return None

    monkeypatch.setattr(core_mod.Framework, "_start_ai_worker",
                        staticmethod(_fake_start))
    fw._plan = {"steps": ["步骤A"], "status": "running", "rounds": 1,
                "replans": 0}
    fw._plan_fail_note = "步骤「步骤A」受阻"
    fw._plan_replan()
    assert captured.get("allow_lora") is False
    assert "【计划重规划】" in captured["message"]


# ============================================================
# 二、零推进不自旋：连续空转就按步骤受阻处理
# ============================================================
def test_no_progress_round_is_counted(fw, no_timers):
    """本轮跑了动作却一步没推进：记一次空转，但先不惊动用户。"""
    fw._plan = {"steps": ["步骤A", "步骤B"], "status": "running", "rounds": 1}
    fw._plan_done = 0
    fw._plan_done_at_round_start = 0
    fw._plan_round_had_actions = True      # 模型给了动作（但只是表演类）
    fw._busy = True
    fw._run_next_action_inner()
    assert fw._plan_no_progress_rounds == 1
    assert fw._plan_fail_note == ""         # 还没到上限，先不打断


def test_no_progress_twice_triggers_replan(fw, no_timers):
    """连续两轮空转：按步骤受阻处理，走重规划那条路。"""
    fw._plan = {"steps": ["步骤A", "步骤B"], "status": "running", "rounds": 1}
    fw._plan_done = 0
    fw._plan_done_at_round_start = 0
    fw._plan_round_had_actions = True
    fw._busy = True
    fw._run_next_action_inner()
    fw._busy = True
    fw._run_next_action_inner()
    assert fw._plan_no_progress_rounds == 0        # 触发后归零，等重规划结果
    assert "没有推进步骤" in fw._plan_fail_note


def test_progress_resets_the_counter(fw, no_timers):
    """真推进了就清零：空转计数只针对「什么都没做成」的轮次。"""
    fw._plan = {"steps": ["步骤A", "步骤B"], "status": "running", "rounds": 1}
    fw._plan_done = 1                       # 这一轮推进了 1 步
    fw._plan_done_at_round_start = 0
    fw._plan_round_had_actions = True
    fw._plan_no_progress_rounds = 1         # 上一轮空转过
    fw._busy = True
    fw._run_next_action_inner()
    assert fw._plan_no_progress_rounds == 0
    assert fw._plan_fail_note == ""


def test_talking_only_round_is_not_counted_as_stuck(fw, no_timers):
    """模型只说话没调工具：按既有约定算口头完成，不算空转。"""
    fw._plan = {"steps": ["步骤A", "步骤B"], "status": "running", "rounds": 1}
    fw._plan_done = 0
    fw._plan_done_at_round_start = 0
    fw._plan_round_had_actions = False     # 只说话
    fw._busy = True
    fw._run_next_action_inner()
    assert fw._plan_no_progress_rounds == 0
    assert fw._plan_done == 1              # 口头完成推进一格
    assert fw._plan_fail_note == ""


def test_plan_round_records_starting_point(fw, monkeypatch):
    """每轮开始时记下起点，用来判断这轮有没有真推进。"""
    captured = {}

    def _fake_start(message, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(core_mod.Framework, "_start_ai_worker",
                        staticmethod(_fake_start))
    fw._plan = {"steps": ["步骤A", "步骤B"], "status": "running", "rounds": 0}
    fw._plan_done = 1
    fw._plan_next_round()
    assert fw._plan_done_at_round_start == 1
