# -*- coding: utf-8 -*-
"""智能体稳定性 + 动态重规划 + 工作流模板 + 历史折叠 专项测试

运行（离屏）: QT_QPA_PLATFORM=offscreen python tools/test_agent_stability.py

覆盖：
一、动作链异常兜底：单个动作抛异常 -> 跳过继续，不闪退不停摆
二、_finish_action 双收尾保护 / UI 回调异常保护
三、独立进程验证：槽内未捕获异常 + 全局钩子 -> 进程存活
四、动态重规划：受阻登记 -> 触发重规划轮 -> show_plan 自动接续；
    预算用尽交还用户；重规划轮没给计划则按原计划继续
五、工作流模板：名称解析 / run_workflow 动作 / 队列收口自动开新任务
六、历史记录折叠：长回复折叠 + 展开按钮 + 展开保持
七、视觉链路加固：网络抖动重试 / 限流重试 / 鉴权分流 / 看门狗收尾 / 线程兜底
"""

import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QApplication, QLabel, QPushButton

app = QApplication([])

from framework import config
from framework.core import Framework
from framework.task_board import TaskBoard

passed, failed = 0, 0


def check(name, cond, detail=""):
    global passed, failed
    ok = bool(cond)
    passed += ok
    failed += not ok
    print(f"  [{'PASS]' if ok else 'FAIL]'} {name}"
          + (f"  {detail}" if detail and not ok else ""))


# 测试数据隔离：绝不读写真实的 tasks.json / memory.json
fw = Framework.instance()
fw.tasks._file = os.path.join(tempfile.gettempdir(), "pet_test_stab_tasks.json")
fw.tasks.reminders, fw.tasks.todos = [], []
fw.tasks.deliveries, fw.tasks.worklog = [], []
fw.memory._file = os.path.join(tempfile.gettempdir(),
                               "pet_test_stab_memory.json")
fw.memory.history, fw.memory.facts = [], []

bubbles = []
fw.show_bubble = lambda text: bubbles.append(text)
fw.set_pose = lambda pose: None

# ============================================================
print("\n一、动作链异常兜底（不闪退、跳过出错动作、队列继续）")
# ============================================================


def _boom():
    raise RuntimeError("故意制造的动作异常")


orig_todo_list = fw._do_todo_list
fw._do_todo_list = _boom
fw._busy = True
fw._reply_parts = []
fw._current_user_text = "稳定性测试"
fw._action_queue = [{"action": "todo_list"}, {"action": "idle"}]
fw._run_next_action()
check("出错动作被跳过且气泡提示",
      any("小状况" in b for b in bubbles), str(bubbles))
check("剩余动作保留在队列", fw._action_queue == [{"action": "idle"}],
      str(fw._action_queue))
check("出错后队列继续推进（finish 被调）", fw._finish_done is True)
fw._do_todo_list = orig_todo_list

# 最外层兜底：收尾分支自身异常也要落地
fw._action_queue = []
fw._busy = True


def _inner_boom():
    raise RuntimeError("收尾分支异常")


fw._run_next_action_inner = _inner_boom
fw._run_next_action()
check("收尾分支异常被最外层兜底",
      fw._busy is False and fw._action_queue == []
      and any("先到这里" in b for b in bubbles))
del fw._run_next_action_inner   # 恢复实例遮蔽，回到类方法

# ============================================================
print("\n二、双收尾保护 / UI 回调异常保护")
# ============================================================
fw._finish_done = False
fw._action_queue = []
fw._busy = True
fw._finish_action()
fw._finish_action()
# busy 复位发生在收尾定时器（150ms）触发的空队列分支里，驱动一下事件循环
deadline = time.time() + 2.0
while time.time() < deadline and fw._busy:
    app.processEvents()
    time.sleep(0.02)
check("动作收尾只推进一次", fw._finish_done is True and fw._busy is False)

fw.register_callback("show_bubble", lambda t: 1 / 0)
try:
    fw.show_bubble("回调炸了也不该抛")
    no_crash = True
except ZeroDivisionError:
    no_crash = False
fw.register_callback("show_bubble", lambda t: bubbles.append(t))
check("UI 回调异常不外抛", no_crash)

# ============================================================
print("\n三、独立进程：槽内未捕获异常 + 全局钩子 -> 进程不闪退")
# ============================================================
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
probe = (
    "import sys\n"
    f"sys.path.insert(0, r'{ROOT}')\n"
    "from PyQt5.QtWidgets import QApplication\n"
    "from PyQt5.QtCore import QTimer\n"
    "from main import install_crash_guards\n"
    "install_crash_guards()\n"
    "app = QApplication(['-platform', 'offscreen'])\n"
    "QTimer.singleShot(10, lambda: 1 / 0)\n"
    "alive = {}\n"
    "QTimer.singleShot(300, lambda: (alive.__setitem__('a', True),"
    " app.quit()))\n"
    "app.exec_()\n"
    "print('ALIVE' if alive.get('a') else 'DEAD')\n"
)
r = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                   text=True, timeout=60)
check("槽内异常后进程存活（钩子生效）", "ALIVE" in r.stdout,
      (r.stdout + r.stderr)[-160:])

# ============================================================
print("\n四、动态重规划")
# ============================================================
import framework.core as core_mod
from PyQt5.QtCore import QObject, pyqtSignal


class FakeWorker(QObject):
    """可编程的 AIWorker 替身：记录收到的消息，start 即返回预设动作"""
    finished = pyqtSignal(list)
    failed = pyqtSignal(str)
    sent = []

    def __init__(self, message, history, personality, parent=None,
                 facts="", notes=""):
        super().__init__(parent)
        self.message = message
        FakeWorker.sent.append(message)

    def isRunning(self):
        return False

    def stop(self):
        pass

    def start(self):
        self.finished.emit(list(FakeWorker.replies))


FakeWorker.replies = []
orig_aiworker = core_mod.AIWorker
core_mod.AIWorker = FakeWorker


def _fresh_plan():
    fw._plan = {"steps": ["调研市场", "生成PPT", "下午3点提醒我"],
                "status": "running", "rounds": 1, "replans": 0}
    fw._plan_done = 0
    fw._plan_round_had_actions = True
    fw._plan_round_pending = False
    fw._plan_fail_note = ""
    fw._plan_auto_plan = False
    fw._busy = True
    fw._action_queue = []
    fw._reply_parts = []
    fw._current_user_text = "[计划执行] 0/3"


# 4.1 受阻登记 -> 队列收口走重规划路线（不推进到下一步）
_fresh_plan()
fw._plan_done = 1              # 受阻发生在第 2 步（生成PPT）执行期间
fw._note_plan_failure("文档生成失败（boom）")
check("受阻登记带上当前步骤",
      "生成PPT" in fw._plan_fail_note and "文档生成失败" in fw._plan_fail_note,
      fw._plan_fail_note)
fw._run_next_action()          # 队列空 + fail_note + 预算内 -> 排重规划轮
check("受阻后进入重规划调度（busy 已放开）", fw._busy is False)
check("受阻原因保留给重规划轮", "文档生成失败" in fw._plan_fail_note)

# 4.2 重规划轮：AI 返回新 show_plan -> 自动接续（不走确认卡）
FakeWorker.sent = []
FakeWorker.replies = [{"action": "show_plan",
                       "steps": ["换个模板再生成PPT", "下午3点提醒我"]}]
fw._plan_replan()
check("重规划消息带受阻原因",
      bool(FakeWorker.sent) and "计划重规划" in FakeWorker.sent[0]
      and "文档生成失败" in FakeWorker.sent[0])
check("重规划后步骤已替换", fw._plan["steps"]
      == ["换个模板再生成PPT", "下午3点提醒我"], str(fw._plan["steps"]))
check("重规划计数 +1 且状态保持执行中",
      fw._plan.get("replans") == 1 and fw._plan["status"] == "running")
check("重规划不走确认流（自动接续）",
      fw._plan_auto_plan is False and fw._plan_done == 0)
check("重规划气泡已提示", any("计划已调整" in b for b in bubbles))
fw._plan = None
fw._plan_round_pending = False

# 4.3 重规划预算用尽：交还用户，不再发起重规划
_fresh_plan()
fw._plan["replans"] = config.PLAN_MAX_REPLANS
fw._plan_fail_note = "步骤「生成PPT」没查到「xx」的资料"
fw._run_next_action()
check("预算用尽交还用户", fw._busy is False
      and any("卡住了" in b for b in bubbles), str(bubbles[-1:]))
check("预算用尽后受阻原因已清", fw._plan_fail_note == "")
fw._plan = None

# 4.4 重规划轮没返回 show_plan：按原计划继续，标记复位
_fresh_plan()
fw._plan_auto_plan = True
fw._on_ai_finished_inner([{"action": "show_bubble", "text": "我想想"}])
check("无计划的重规划轮按原计划继续",
      fw._plan_auto_plan is False and fw._plan["status"] == "running")
fw._plan = None

core_mod.AIWorker = orig_aiworker

# ============================================================
print("\n五、工作流模板")
# ============================================================
check("模板已配置且字段齐全",
      len(config.WORKFLOW_TEMPLATES) >= 3
      and all(t.get("name") and t.get("desc") and t.get("command")
              for t in config.WORKFLOW_TEMPLATES))
check("模板名全等命中", fw._resolve_workflow("每日科技早报") is not None)
check("模板名模糊命中（子串）",
      (fw._resolve_workflow("早报") or {}).get("name") == "每日科技早报")
check("未知模板返回 None", fw._resolve_workflow("根本不存在") is None)

sent = []
fw.send_command = lambda text: sent.append(text)

fw._workflow_start = ""
fw._do_run_workflow({"action": "run_workflow", "name": "每日科技早报"})
tpl = config.WORKFLOW_TEMPLATES[0]
check("run_workflow 登记模板命令", fw._workflow_start == tpl["command"])
fw._do_run_workflow({"action": "run_workflow", "name": "乱写的"})
check("未知模板礼貌提示", any("没有叫" in b for b in bubbles)
      and fw._workflow_start == tpl["command"])

# 队列收口：工作流作为新任务启动
fw._action_queue = []
fw._plan = None
fw._busy = True
fw._reply_parts = []
fw._current_user_text = "工作流测试"
fw._run_next_action()
check("队列收口后自动启动工作流",
      sent == [tpl["command"]] and fw._workflow_start == ""
      and fw._busy is False, str(sent))
fw.send_command = Framework.send_command.__get__(fw, Framework)

# ============================================================
print("\n五·五、产品身份认知（离线自我介绍）")
# ============================================================
from framework import ai_engine

intro = ai_engine.mock_ai("你是谁？", "活泼")
intro_text = " ".join(a.get("text", "") for a in intro)
check("自我介绍自称凌零·桌面AI助手",
      "凌零" in intro_text and "桌面AI助手" in intro_text, intro_text)
check("自我介绍绝不自称皮皮/桌宠",
      "皮皮" not in intro_text and "桌宠" not in intro_text, intro_text)
check("SYSTEM_PROMPT 身份规则在位",
      '你是"凌零"' in ai_engine.SYSTEM_PROMPT
      and "绝不" in ai_engine.SYSTEM_PROMPT)
check("SYSTEM_PROMPT 中皮皮仅出现在禁用规则里",
      ai_engine.SYSTEM_PROMPT.count("皮皮") == 1
      and '自称"桌宠""宠物""皮皮"' in ai_engine.SYSTEM_PROMPT)

# ============================================================
print("\n六、历史记录折叠 + 展开按钮")
# ============================================================
from ui.input_dialog import HistoryPanel, FOLD_TEXT_LEN


class FakeMem:
    def __init__(self, pairs):
        self._pairs = pairs

    def get_pairs(self):
        return self._pairs

    def delete_pair(self, i):
        return True

    def clear(self):
        pass


panel = HistoryPanel()
long_reply = "这是一段很长的回复，用来验证折叠功能是否正常工作。" * 20
short_reply = "短回复，不折叠。"
now = time.time()
panel.bind_memory(FakeMem([
    {"user": "帮我调研并出个报告", "reply": long_reply,
     "ts": now, "new_session": True},
    {"user": "在吗", "reply": short_reply, "ts": now, "new_session": False},
]))


def _find_row(text):
    """按完整原文找记录行，返回 (item, 内容QLabel, 折叠按钮|None)"""
    for i in range(panel._list.count()):
        item = panel._list.item(i)
        if item.data(Qt.UserRole) != text:
            continue
        w = panel._list.itemWidget(item)
        if w is None:
            continue
        label = next((lb for lb in w.findChildren(QLabel)
                      if lb.objectName().startswith("historyText")), None)
        btn = next((b for b in w.findChildren(QPushButton)
                    if "展开" in b.text() or "收起" in b.text()), None)
        return item, label, btn
    return None, None, None


item, label, btn = _find_row(long_reply)
check("长回复默认折叠显示开头", label is not None
      and label.text() == long_reply[:FOLD_TEXT_LEN] + "……",
      label.text()[:40] if label else "no label")
check("折叠行有「展开全文」按钮", btn is not None and "展开" in btn.text())
folded_h = item.sizeHint().height()

btn.click()
for _ in range(20):
    app.processEvents()
    time.sleep(0.01)
item, label, btn = _find_row(long_reply)
check("点击后展开完整内容", label is not None
      and label.text() == long_reply, label.text()[:30] if label else "")
check("展开后按钮变为「收起」", btn is not None and "收起" in btn.text())
expanded_h = item.sizeHint().height()
check("展开后行高变大", expanded_h > folded_h,
      f"{folded_h} -> {expanded_h}")

panel.refresh_history()
item, label, _btn = _find_row(long_reply)
check("刷新后仍保持展开（不丢状态）", label is not None
      and label.text() == long_reply)

item, label, btn = _find_row(long_reply)
btn.click()
for _ in range(20):
    app.processEvents()
    time.sleep(0.01)
item, label, btn = _find_row(long_reply)
check("再次点击收起", label is not None
      and label.text() == long_reply[:FOLD_TEXT_LEN] + "……")

_, label_short, btn_short = _find_row(short_reply)
check("短回复不折叠无按钮", label_short is not None
      and label_short.text() == short_reply and btn_short is None)

# ============================================================
print("\n七、视觉链路加固（看屏幕不稳）")
# ============================================================
import types
import framework.core as _core_mod2
from framework.core import (_VISION_MIN_TIMEOUT, _VisionWorker,
                            vision_timeout_seconds)

_vision_img = os.path.join(tempfile.gettempdir(), "pet_test_vision.jpg")


class _FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}

    def json(self):
        return self._body


class _FakeSession:
    """可编程 requests 替身：按剧本逐个返回（异常实例或 _FakeResp）"""
    script = []

    def post(self, url, **kw):
        item = _FakeSession.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


import requests as _real_requests
_fake_requests = types.ModuleType("requests")
_fake_requests.RequestException = _real_requests.RequestException
_fake_requests.ReadTimeout = _real_requests.ReadTimeout
_fake_requests.ConnectTimeout = _real_requests.ConnectTimeout
_fake_requests.Session = _FakeSession
_real_requests_mod = sys.modules.get("requests")
sys.modules["requests"] = _fake_requests

_orig_delays = _core_mod2._VISION_ATTEMPT_DELAYS
_core_mod2._VISION_ATTEMPT_DELAYS = (0.0, 0.0)   # 测试不等真实退避
_orig_vt = config.VISION_TIMEOUT


def _mk_img():
    with open(_vision_img, "wb") as f:
        f.write(b"\xff\xd8fakejpg")


def _run_ask():
    _mk_img()
    w = _VisionWorker(_vision_img, "屏幕上有什么")
    return w._ask(), w.attempts


_mk_img()
_FakeSession.script = [
    _real_requests.ReadTimeout("t"), _real_requests.ReadTimeout("t"),
    _FakeResp(200, {"choices": [{"message": {"content": "屏幕上是桌面。"}}]})]
ask, att = _run_ask()
check("网络抖动自动重试后成功",
      ask == (True, "屏幕上是桌面。") and att == 3, f"{ask} attempts={att}")

_mk_img()
_FakeSession.script = [
    _FakeResp(429),
    _FakeResp(200, {"choices": [{"message": {"content": "看到了。"}}]})]
ask, att = _run_ask()
check("限流 429 自动重试后成功", ask == (True, "看到了。") and att == 2,
      f"{ask} attempts={att}")

_mk_img()
_FakeSession.script = [_FakeResp(401)]
ask, att = _run_ask()
check("鉴权失败不重试并提示查Key",
      ask[0] is False and "401" in ask[1] and att == 1, f"{ask}")

_mk_img()
_FakeSession.script = [
    _FakeResp(200, {"error": {"code": "1113", "message": "balance"}}),
    _FakeResp(200, {"choices": [{"message": {"content": "好了。"}}]})]
ask, att = _run_ask()
check("200错误体按失败重试", ask == (True, "好了。") and att == 2, f"{ask}")

_mk_img()
_FakeSession.script = [_real_requests.ConnectTimeout("c")] * 3
ask, att = _run_ask()
check("重试耗尽返回明确失败原因",
      ask[0] is False and "网络" in ask[1] and att == 3, f"{ask}")

w = _VisionWorker(_vision_img, "q")
w._ask = lambda: 1 / 0           # 实例遮蔽：模拟线程体炸了
got = []
w.done.connect(lambda ok, ans: got.append((ok, ans)))
w.run()                          # 不 start，直接在本线程验证兜底
check("线程体异常仍保证 done 恰好一次",
      len(got) == 1 and got[0][0] is False
      and "ZeroDivisionError" in got[0][1], str(got))

config.VISION_TIMEOUT = 0
check("超时配置异常时兜底下限",
      vision_timeout_seconds() == _VISION_MIN_TIMEOUT,
      str(vision_timeout_seconds()))
config.VISION_TIMEOUT = _orig_vt
_core_mod2._VISION_ATTEMPT_DELAYS = _orig_delays
sys.modules["requests"] = _real_requests_mod

fw._vision_worker = _VisionWorker(_vision_img, "q")   # 未启动的线程即可
fw._vision_watchdog = QTimer()
fw._vision_watchdog.setSingleShot(True)
_zombies_before = len(fw._zombie_workers)
fw._on_vision_watchdog()
check("看门狗强制收尾并给出提示",
      fw._vision_worker is None
      and len(fw._zombie_workers) == _zombies_before + 1
      and any("等了好久" in b for b in bubbles), str(bubbles[-1:]))

try:
    os.remove(_vision_img)
except OSError:
    pass

# ============================================================
print("\n八、动作链看门狗 + 多步任务拆分 + 文档样式/位置")
# ============================================================
from framework import ai_engine
from framework.ai_engine import (split_multi_task, _extract_style_hints,
                                 _extract_location)

steps3 = split_multi_task("先帮我做个PPT，再查一下天气，最后整理成报告")
check("多步任务拆成计划步骤",
      steps3 == ["帮我做个PPT", "查一下天气", "整理成报告"], str(steps3))
check("单步任务不拆",
      split_multi_task("帮我做个PPT") == [] and split_multi_task("") == [])
fw._current_user_text = "先做个PPT，再发周报，最后提醒我"
fw._action_queue = []
acts = fw._ensure_tool_intent([])
check("意图兜底把多步任务补成 show_plan",
      bool(acts) and acts[-1]["action"] == "show_plan"
      and len(acts[-1]["steps"]) == 3, str(acts))
fw._current_user_text = "稳定性测试"

h = _extract_style_hints("做个PPT，标题红色，正文用宋体，1.5倍行距")
check("样式提示提取",
      h.get("title_color") == "红色" and h.get("font") == "宋体"
      and h.get("line_spacing") == 1.5, str(h))
check("保存位置提取",
      _extract_location("帮我做个PPT保存到桌面") == "desktop"
      and _extract_location("做个PPT") == "")
acts2 = ai_engine.mock_ai("做个PPT，标题红色，保存到桌面", "活泼")
doc_act = next((x for x in acts2 if x.get("action") == "create_doc"), {})
check("离线模拟 create_doc 带 style/location",
      doc_act.get("style", {}).get("title_color") == "红色"
      and doc_act.get("location") == "desktop", str(doc_act)[:120])

# 看门狗：动作超时未收尾 -> 强制收尾 + 说明原因（绝不无声卡死）
fw._busy = True
fw._action_queue = []
fw._finish_done = False
fw._current_action_name = "create_doc"
fw._reply_parts = []
if fw._chain_watchdog is None:
    fw._chain_watchdog = QTimer()
    fw._chain_watchdog.setSingleShot(True)
fw._chain_watchdog.stop()
fw._on_chain_watchdog()
check("看门狗强制收尾并说明原因",
      fw._finish_done is True and any("跳过" in b for b in bubbles)
      and not fw._chain_watchdog.isActive(), str(bubbles[-1:]))
check("看门狗预算表：慢动作放宽",
      fw._ACTION_WATCHDOG_BUDGETS.get("create_doc") == 300
      and fw._ACTION_WATCHDOG_BUDGETS.get("see_screen") == 240
      and fw._ACTION_WATCHDOG_DEFAULT == 90)
fw._busy = False

# ============================================================
print("\n九、质量门（软失败自我纠正）")
# ============================================================
from framework.quality_gate import assess_doc_spec, assess_search, \
    extract_keywords
from PyQt5.QtWidgets import QFileDialog

kws = extract_keywords("新能源汽车市场 2025 销量")
check("查询关键词提取",
      "汽车" in kws and "2025" in kws and "销量" in kws, str(kws))
good = assess_search("新能源汽车市场", [
    {"title": "新能源汽车市场全景扫描", "content": "新能源汽车市场渗透率持续攀升，插电混动与纯电车型增速分化，头部品牌集中度进一步提高"},
    {"title": "电动车销量数据分析", "content": "新能源汽车销量再创历史新高，季度环比增长明显，充电基础设施配套同步放量"}])
check("相关资料通过质量门", good["ok"] is True and good["score"] > 0,
      str(good))
bad = assess_search("新能源汽车市场", [
    {"title": "今日菜价", "content": "西红柿批发价每斤三块五，黄瓜价格保持平稳，市民菜篮子供应充足，近期价格波动不大，批发生意稳中有升"},
    {"title": "足球赛果", "content": "昨晚联赛决赛上演点球大战，双方门将各扑出两粒点球，最终胜负分晓，现场数万球迷见证了这场经典对决"}])
check("不相关资料被拦下", bad["ok"] is False and "零重合" in bad["reason"],
      str(bad))
check("内容几乎为空被拦下",
      not assess_search("任意", [{"title": "t", "content": "短"}])["ok"])
check("重复结果被拦下",
      not assess_search("新闻", [{"title": "同一标题",
                                  "content": "长长长长长长长长长长"}] * 4)["ok"])

check("PPT 体检：单薄/干瘪拦下，充实放行",
      not assess_doc_spec("ppt", {"slides": [
          {"title": "a", "bullets": ["字"]}]}).get("ok")
      and not assess_doc_spec("ppt", {"slides": [
          {"title": "t", "bullets": ["哈哈"]},
          {"title": "b", "bullets": ["呵呵"]}]}).get("ok")
      and assess_doc_spec("ppt", {"slides": [
          {"title": "背景", "bullets": ["行业规模近三年年均增长 15%"]},
          {"title": "计划", "bullets": ["第一阶段完成渠道下沉与代理体系建设"]}]})["ok"])
check("Word 体检：章节/段落数",
      not assess_doc_spec("doc", {"sections": [
          {"heading": "a", "paragraphs": ["p1"]}]}).get("ok")
      and assess_doc_spec("doc", {"sections": [
          {"heading": "a", "paragraphs": ["p1", "p2"]},
          {"heading": "b", "paragraphs": ["p3"]}]}).get("ok"))
check("Excel 体检：数据行数",
      not assess_doc_spec("xlsx", {"sheets": [
          {"name": "s", "headers": ["A"], "rows": [[1]]}]}).get("ok")
      and assess_doc_spec("xlsx", {"sheets": [
          {"name": "s", "headers": ["A"], "rows": [[1], [2]]}]}).get("ok"))

# 集成：计划内搜索软失败 -> 登记质量原因 -> 触发重规划
_fresh_plan()
fw._plan_done = 1
fw._on_search_done(True, "新能源汽车市场", [
    {"title": "今日菜价", "content": "西红柿批发价每斤三块五，黄瓜价格保持平稳，市民菜篮子供应充足，近期价格波动不大，批发生意稳中有升"},
    {"title": "足球赛果", "content": "昨晚联赛决赛上演点球大战，双方门将各扑出两粒点球，最终胜负分晓，现场数万球迷见证了这场经典对决"}])
check("计划内搜索软失败登记质量原因",
      "检索质量不达标" in fw._plan_fail_note, fw._plan_fail_note)
check("软失败气泡告知纠正意图", any("换个思路" in b for b in bubbles))
fw._plan = None
fw._plan_round_pending = False

# 集成：单发搜索软失败 -> 自动换词重试（重试接管收尾）
fw._plan = None
fw._search_quality_retried = False
retried = []
fw._do_web_search = lambda action: retried.append(action["query"])
fw._busy = True
fw._finish_done = False
fw._on_search_done(True, "新能源汽车市场最新动态", [
    {"title": "今日菜价", "content": "西红柿批发价每斤三块五，黄瓜价格保持平稳，市民菜篮子供应充足，近期价格波动不大，批发生意稳中有升"}])
check("单发软失败自动换词重试",
      len(retried) == 1 and retried[0] != "新能源汽车市场最新动态"
      and fw._search_quality_retried is True, str(retried))
fw._do_web_search = Framework._do_web_search.__get__(fw, Framework)

# 集成：文档内容单薄 -> 计划内登记质量原因 / 单发发起纠正轮重写
_fresh_plan()
fw._plan_done = 1
fw._do_create_doc({"doc_type": "ppt",
                   "content": {"title": "t", "slides": [
                       {"title": "a", "bullets": ["字"]}]}})
check("计划内文档软失败登记质量原因",
      "文档内容质量不达标" in fw._plan_fail_note, fw._plan_fail_note)
fw._plan = None
fw._plan_round_pending = False


class _CapWorker(QObject):
    finished = pyqtSignal(list)
    failed = pyqtSignal(str)
    sent = ""

    def __init__(self, message, history, personality, parent=None,
                 facts="", notes=""):
        super().__init__(parent)
        _CapWorker.sent = message

    def isRunning(self):
        return False

    def stop(self):
        pass

    def start(self):
        pass


_orig_aiw = core_mod.AIWorker
core_mod.AIWorker = _CapWorker
_orig_real = config.USE_REAL_AI
config.USE_REAL_AI = True
fw._plan = None
fw._doc_quality_retried = False
fw._busy = True
fw._current_user_text = "做个PPT"
fw._do_create_doc({"doc_type": "ppt",
                   "content": {"title": "t", "slides": [
                       {"title": "a", "bullets": ["字"]}]}})
check("单发文档软失败发起纠正轮重写",
      "质量纠错" in _CapWorker.sent and fw._doc_quality_retried is True
      and fw._suppress_fallback is True, _CapWorker.sent[:100])
core_mod.AIWorker = _orig_aiw

# 离线/二次仍不达标：降级为预警后照常产出（不弹真对话框）
config.USE_REAL_AI = False
fw._doc_quality_retried = False
fw._suppress_fallback = False
fw._busy = True
fw._action_queue = []
fw._finish_done = False
QFileDialog.getSaveFileName = staticmethod(lambda *a, **kw: ("", ""))
fw._do_create_doc({"doc_type": "ppt",
                   "content": {"title": "t", "slides": [
                       {"title": "a", "bullets": ["字"]}]}})
check("离线降级预警后照常走流程",
      any("偏单薄" in b for b in bubbles)
      and any("先不生成" in b for b in bubbles), str(bubbles[-3:]))
config.USE_REAL_AI = _orig_real

# ============================================================
print("\n============================================================")
print(f"结果: {passed} 通过 / {failed} 失败")
print("============================================================")
try:
    os.remove(fw.tasks._file)
    os.remove(fw.memory._file)
except OSError:
    pass
sys.exit(0 if failed == 0 else 1)
