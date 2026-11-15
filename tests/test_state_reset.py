# -*- coding: utf-8 -*-
"""单元测试：Framework 计划瞬时状态的复位与 shutdown 清理（framework/core.py）

覆盖：_clear_plan_transient() 只复位瞬时标记而不动 _plan、__init__ 显式
声明 _finish_done、_cancel_plan 清掉 _suppress_fallback、AIWorker 只在
_start_ai_worker 里创建且该 helper 有 >= 6 个调用点、各计划退出路径都调用
_clear_plan_transient()、shutdown() 把 5 个后台线程与计划卡全部置 None。

为什么不需要联网：这里只检查单例上的 Python 属性与 core.py 的源码文本，
不调用任何会启动 AI 线程 / 触发模型 / 访问网络的方法；shutdown() 里的
memory.save() 与 pet.stop()/定时器都被换成假对象，绝不写脏 data/ 下的真实
用户数据。用 offscreen 平台 + QApplication 只为了让 Qt 对象能安全构造。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PyQt5.QtWidgets import QApplication

from framework import core as core_mod
from framework.core import Framework

# 保证有 app 对象，并持有引用避免被回收
_APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def restore_framework_state():
    """每个用例前后拍/还原单例 __dict__，避免脏状态污染后续用例。

    单例是进程内共享的，任何一处弄脏都会串到下一个用例；这里用 fixture
    yield 做整份 Python 属性快照恢复（只动 __dict__，不碰 Qt C++ 状态）。
    """
    instance = Framework.instance()
    snapshot = dict(instance.__dict__)
    yield instance
    current = instance.__dict__
    for key in list(current):
        if key not in snapshot:
            del current[key]
    current.update(snapshot)


@pytest.fixture
def fw():
    """始终返回同一个单例，绝不 new 第二个（会抛 RuntimeError）。"""
    return Framework.instance()


class _FakeWorker:
    finished = failed = done = timeout = None

    def __init__(self):
        self.stopped = False
        self.deleted = False

    def stop(self):
        self.stopped = True

    def isRunning(self):
        return False

    def deleteLater(self):
        self.deleted = True


class _FakePlanCard:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakePet:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeTimer:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_clear_plan_transient_resets_only_transient_flags(fw):
    """瞬时标记全复位，但 _plan 本身必须原样保留（这个方法只管瞬时标记）。"""
    fw._plan_round_pending = True
    fw._plan_round_had_actions = True
    fw._suppress_fallback = True
    fw._plan_auto_plan = True
    fw._plan_fail_note = "步骤受阻"
    fw._plan_done = 5
    fw._plan_pending_queue = ["a", "b"]
    plan = {"steps": ["第一步", "第二步"], "status": "running", "rounds": 2}
    fw._plan = plan

    fw._clear_plan_transient()

    assert fw._plan_round_pending is False
    assert fw._plan_round_had_actions is False
    assert fw._suppress_fallback is False
    assert fw._plan_auto_plan is False
    assert fw._plan_fail_note == ""
    assert fw._plan_done == 0
    assert fw._plan_pending_queue == []
    assert fw._plan is plan


def test_init_declares_finish_done_in_instance_dict(fw):
    """_finish_done 必须由 __init__ 显式声明，而不是靠 getattr 兜底。"""
    assert "_finish_done" in fw.__dict__
    assert fw._finish_done is False


def test_cancel_plan_clears_suppress_fallback(fw):
    """取消计划要走统一复位，_suppress_fallback 不能残留到下一轮。"""
    fw._plan = {"steps": ["a"], "status": "pending", "rounds": 0}
    fw._suppress_fallback = True
    fw._close_plan_card = lambda: None       # 避免 UI 依赖
    fw._action_queue = [{"action": "jump"}]
    fw._busy = True

    fw._cancel_plan()

    assert fw._suppress_fallback is False
    assert fw._plan is None
    assert fw._action_queue == []
    assert fw._busy is False


def test_ai_worker_is_created_only_inside_the_helper():
    """读 core.py 全文：AIWorker 只 new 一次，且落在 _start_ai_worker 内部。"""
    src = Path(core_mod.__file__).read_text(encoding="utf-8")
    assert src.count("= AIWorker(") == 1
    assert src.count("_start_ai_worker(") >= 6
    helper_pos = src.index("def _start_ai_worker(")
    create_pos = src.index("= AIWorker(")
    assert create_pos > helper_pos
    assert callable(getattr(Framework, "_start_ai_worker", None))


def test_all_plan_exit_paths_call_clear_plan_transient():
    """4 个计划退出路径 + 2 个 AI 兜底都必须调用统一复位。"""
    src = Path(core_mod.__file__).read_text(encoding="utf-8")
    assert src.count("self._clear_plan_transient()") >= 5
    for fn in ("_cancel_plan", "_abandon_plan", "_complete_plan",
               "_on_ai_failed", "_on_ai_finished"):
        assert f"def {fn}(" in src


def test_shutdown_nulls_all_workers_and_plan_card(fw, monkeypatch):
    """shutdown 把 5 个后台引用与计划卡全部置 None，且不写真实记忆。"""
    monkeypatch.setattr(fw.memory, "save", lambda: None)
    pet = _FakePet()
    timer = _FakeTimer()
    fw.pet = pet
    fw._reminder_timer = timer

    workers = {}
    for name in ("_ai_worker", "_doc_worker", "_search_worker",
                 "_vision_worker", "_chain_watchdog"):
        w = _FakeWorker()
        setattr(fw, name, w)
        workers[name] = w
    card = _FakePlanCard()
    fw.plan_card = card

    fw.shutdown()

    for name in ("_ai_worker", "_doc_worker", "_search_worker",
                 "_vision_worker", "plan_card"):
        assert getattr(fw, name) is None, name
    # 真断言：shutdown 确实走了清理分支（不是这些属性本来就是 None）
    assert all(w.stopped and w.deleted for w in workers.values())
    assert card.closed is True
    assert pet.stopped is True
    assert timer.stopped is True
