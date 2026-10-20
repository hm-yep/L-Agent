# -*- coding: utf-8 -*-
"""智能体新能力测试：屏幕视觉 / 任务规划 / 长期记忆 / 提醒待办

- 视觉调用不真联网：只验证消息体构造与无 Key 的友好降级
- 提醒/待办走临时文件，不污染真实任务数据
- Framework 集成链在离屏 QApplication 里跑真实执行器
"""

import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def section(title):
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


# ============================================================
section("一、动作解析器：新动作校验与归一化")
# ============================================================
from framework.action_parser import parse_actions, normalize_action

a = normalize_action({"action": "see_screen", "question": "屏幕上是什么"})
check("see_screen 正常保留", a and a["question"] == "屏幕上是什么", str(a))
a = normalize_action({"action": "see_screen"})
check("see_screen 缺 question 不再剔除（question 兜底为空）",
      a is not None and a.get("question") == "", str(a))

a = normalize_action({"action": "show_plan",
                      "steps": ["调研", "写稿", "交付"]})
check("show_plan 列表步骤", a and a["steps"] == ["调研", "写稿", "交付"],
      str(a))
a = normalize_action({"action": "show_plan",
                      "steps": "第1步调研；2. 写稿\n三、交付"})
check("show_plan 字符串自动拆步骤", a and a["steps"]
      == ["调研", "写稿", "交付"], str(a and a["steps"]))
check("show_plan 空步骤剔除",
      normalize_action({"action": "show_plan", "steps": []}) is None)
a = normalize_action({"action": "remember", "信息": "用户喜欢深色"})
check("remember 中文别名容错", a and a["fact"] == "用户喜欢深色", str(a))
a = normalize_action({"action": "remind", "text": "开会", "minutes": "10"})
check("remind minutes 兼容保留", a and a.get("minutes") == 10.0, str(a))
a = normalize_action({"action": "remind", "text": "喝水", "seconds": 20})
check("remind seconds 保留", a and a.get("seconds") == 20.0
      and "minutes" not in a, str(a))
a = normalize_action({"action": "todo_done", "index": "2"})
check("todo_done 序号转整数", a and a["index"] == 2, str(a))
a = normalize_action({"action": "todo_done"})
check("todo_done 无序号仍保留(index=0)", a and a["index"] == 0, str(a))
check("todo_list 无参数可用",
      normalize_action({"action": "todo_list"}) is not None)

acts = parse_actions([
    {"action": "show_bubble", "text": "遵命"},
    {"action": "show_plan", "steps": "1.拆解；2.执行"},
    {"action": "remind", "text": "喝水", "minutes": 5},
    {"action": "todo_add", "text": "交报告"},
    {"action": "fake_action"},
])
check("完整序列解析", [x["action"] for x in acts]
      == ["show_bubble", "show_plan", "remind", "todo_add"],
      str([x["action"] for x in acts]))

# ============================================================
section("二、长期记忆：读写 / 去重 / 上限 / 持久化")
# ============================================================
from framework import config as _cfg
from framework.memory_system import MemorySystem

tmp = os.path.join(tempfile.gettempdir(), "pet_test_memory_facts.json")
if os.path.exists(tmp):
    os.remove(tmp)
ms = MemorySystem()
ms._file = tmp                     # 改写到临时文件，不碰真实记忆
ms.facts = []
ms.history = []

check("remember_fact 新增", ms.remember_fact("用户喜欢深色PPT"))
check("remember_fact 去重(不区分大小写)",
      not ms.remember_fact("用户喜欢深色ppt") and len(ms.facts) == 1)
check("remember_fact 空串拒绝", not ms.remember_fact("  "))
check("get_facts_text 格式",
      ms.get_facts_text() == "- 用户喜欢深色PPT", ms.get_facts_text())

old_cap = _cfg.MAX_FACTS
_cfg.MAX_FACTS = 3
for f in ("事实二", "事实三", "事实四"):
    ms.remember_fact(f)
check("条数上限丢最老", ms.facts == ["事实二", "事实三", "事实四"],
      str(ms.facts))
_cfg.MAX_FACTS = old_cap

check("forget_fact 返回内容", ms.forget_fact(0) == "事实二")
check("forget_fact 越界返回 None", ms.forget_fact(9) is None)
ms.save()
ms2 = MemorySystem()
ms2._file = tmp
ms2.load()
check("长期记忆持久化回读", ms2.facts == ["事实三", "事实四"], str(ms2.facts))
os.remove(tmp)

# ============================================================
section("三、任务台：提醒 / 待办 / 持久化")
# ============================================================
from framework.task_board import TaskBoard, format_duration

check("时长格式化 秒", format_duration(45) == "45 秒", format_duration(45))
check("时长格式化 分秒", format_duration(85) == "1 分 25 秒",
      format_duration(85))
check("时长格式化 整分", format_duration(120) == "2 分钟",
      format_duration(120))
check("时长格式化 时分", format_duration(5400) == "1 小时 30 分钟",
      format_duration(5400))
check("时长格式化 天", format_duration(90000) == "1 天 1 小时",
      format_duration(90000))

tfile = os.path.join(tempfile.gettempdir(), "pet_test_tasks.json")
if os.path.exists(tfile):
    os.remove(tfile)
board = TaskBoard()
board._file = tfile
board.reminders, board.todos = [], []

now = time.time()
board.add_reminder("早点睡", now - 5)
board.add_reminder("开会", now + 600)
check("提醒按时间排序",
      [r["text"] for r in board.reminders] == ["早点睡", "开会"])
due = board.pop_due_reminders(now=now)
check("到期提醒取出", [r["text"] for r in due] == ["早点睡"], str(due))
check("未到期提醒保留", board.reminder_count() == 1)
check("未来时刻取出剩余提醒",
      [r["text"] for r in board.pop_due_reminders(now=now + 3600)]
      == ["开会"])
check("提醒清空后 pop 返回空", board.pop_due_reminders(now) == [])

board.add_reminder("待删除提醒", now + 500)
check("remove_reminder 按序删除",
      board.remove_reminder(1)["text"] == "待删除提醒"
      and board.reminder_count() == 0)
check("remove_reminder 越界返回 None", board.remove_reminder(9) is None)

board.add_reminder("临时提醒", now + 99999)
board.reminders = []
board.save()

board.add_todo("交报告")
dup = board.add_todo("交报告")
check("待办去重", len(board.todos) == 1 and dup is board.todos[0])
board.add_todo("买咖啡")
check("open_todos 顺序", [t["text"] for t in board.open_todos()]
      == ["交报告", "买咖啡"])
item = board.complete_todo(1)
check("按序号完成待办(1起)", item and item["text"] == "交报告"
      and item["done"])
check("越界序号完成失败", board.complete_todo(9) is None
      and board.complete_todo(0) is None)
check("剩余待办正确", [t["text"] for t in board.open_todos()] == ["买咖啡"])
board.add_todo("临时A")
board.complete_todo(2)          # 未完成清单里的第 2 条 = 临时A
board.clear_done()
check("clear_done 只留未完成", [t["text"] for t in board.todos]
      == ["买咖啡"])

board.add_todo("待删除待办")
check("remove_todo 按序删除",
      board.remove_todo(2)["text"] == "待删除待办"
      and [t["text"] for t in board.todos] == ["买咖啡"])
check("remove_todo 越界返回 None", board.remove_todo(5) is None
      and board.remove_todo(0) is None)

board.save()
board2 = TaskBoard()
board2._file = tfile
board2.load()
check("任务台持久化回读", [t["text"] for t in board2.todos] == ["买咖啡"])
os.remove(tfile)

# ============================================================
section("四、离线模拟 mock_ai：新意图分支")
# ============================================================
from framework.ai_engine import mock_ai, extract_remind
from framework import config

acts = mock_ai("记住我喜欢深色PPT", "活泼")
names = [a["action"] for a in acts]
check("记住不走文档分支", "remember" in names, str(names))
fact = next((a["fact"] for a in acts if a["action"] == "remember"), "")
check("记住的事实含原文", "深色PPT" in fact, fact)

acts = mock_ai("10分钟后提醒我开会", "活泼")
remind = next((a for a in acts if a["action"] == "remind"), {})
check("提醒解析秒数", remind.get("seconds") == 600, str(remind))
check("提醒内容干净", "开会" in remind.get("text", "")
      and "分钟" not in remind.get("text", ""), str(remind))

acts = mock_ai("半小时后提醒我拿快递", "活泼")
remind = next((a for a in acts if a["action"] == "remind"), {})
check("半小时=1800秒", remind.get("seconds") == 1800, str(remind))

acts = mock_ai("2小时后提醒我关空调", "活泼")
remind = next((a for a in acts if a["action"] == "remind"), {})
check("小时换算秒", remind.get("seconds") == 7200, str(remind))

acts = mock_ai("提醒我1分25秒后看电视", "活泼")
remind = next((a for a in acts if a["action"] == "remind"), {})
check("复合时长 1分25秒=85秒", remind.get("seconds") == 85, str(remind))
check("复合时长内容干净", remind.get("text") == "看电视", str(remind))

acts = mock_ai("提醒我20秒后喝水", "活泼")
remind = next((a for a in acts if a["action"] == "remind"), {})
check("20秒提醒不被进位", remind.get("seconds") == 20, str(remind))

seconds, content = extract_remind("提醒我1小时30分后开会")
check("复合时长 1小时30分=5400秒", seconds == 5400 and "开会" in content,
      f"{seconds} / {content}")

minutes, content = extract_remind("提醒我开会")
check("无时间提醒用默认", minutes is None and "开会" in content,
      f"{minutes} / {content}")

acts = mock_ai("帮我看看屏幕上这个报错", "活泼")
check("看屏幕意图", any(a["action"] == "see_screen" for a in acts),
      str([a["action"] for a in acts]))

acts = mock_ai("加个待办：周五交报告", "活泼")
todo = next((a for a in acts if a["action"] == "todo_add"), {})
check("加待办提取内容", todo.get("text") == "周五交报告", str(todo))

check("看待办意图", any(a["action"] == "todo_list" for a in
                        mock_ai("看看我的待办", "活泼")))
check("完成待办意图", any(a["action"] == "todo_done" for a in
                          mock_ai("完成待办", "活泼")))
acts = mock_ai("帮我规划一下明天的汇报", "活泼")
names = [a["action"] for a in acts]
check("任务规划意图出计划", "show_plan" in names, str(names))
check("计划不自动执行（无工具动作）",
      set(names) <= {"show_bubble", "show_plan"}, str(names))

# ============================================================
section("五、AIWorker：长期记忆注入提示词")
# ============================================================
from framework.ai_engine import AIWorker

worker = AIWorker("你好", [], "活泼", facts="- 用户喜欢深色PPT\n- 用户叫小明")
msgs = worker._messages()
sys_text = msgs[0]["content"]
check("系统提示含长期记忆标记", "【长期记忆】" in sys_text)
check("记忆条目注入", "用户喜欢深色PPT" in sys_text
      and "用户叫小明" in sys_text)
worker2 = AIWorker("你好", [], "活泼")
check("无记忆不注入", "【长期记忆】" not in worker2._messages()[0]["content"])

# ============================================================
section("六、视觉消息体构造")
# ============================================================
from framework.core import build_vision_messages

msg = build_vision_messages("QUJD", "屏幕上是什么")
check("消息体含图片", msg["content"][0]["image_url"]["url"]
      == "data:image/jpeg;base64,QUJD")
check("消息体含问题", msg["content"][1]["text"] == "屏幕上是什么")
msg = build_vision_messages("QUJD", "")
check("空问题给默认问句",
      msg["content"][1]["text"] == "描述一下这个屏幕上有什么")

from framework.core import build_vision_payload
p = build_vision_payload("glm-4.6v-flash", [msg])
check("GLM推理视觉模型默认关思考", p.get("thinking") == {"type": "disabled"},
      str(p.get("thinking")))
check("请求体带模型名", p["model"] == "glm-4.6v-flash")
p2 = build_vision_payload("glm-4.1v-thinking-flash", [msg])
check("4.1v-thinking 同样关思考",
      p2.get("thinking") == {"type": "disabled"})
p3 = build_vision_payload("gpt-4o-mini", [msg])
check("其他模型不加思考参数", "thinking" not in p3)

# ============================================================
section("七、Framework 集成链（真实执行器）")
# ============================================================
from PyQt5.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from framework.core import Framework

fw = Framework.instance()
# 任务数据隔离到临时文件：提醒/待办/计划/日志都不写进真实 tasks.json
fw.tasks._file = os.path.join(
    tempfile.gettempdir(), "pet_test_tasks_integration.json")
fw.tasks.reminders, fw.tasks.todos = [], []
fw.tasks.deliveries, fw.tasks.worklog = [], []
bubbles = []
fw.show_bubble = lambda text: bubbles.append(str(text))


def _pump(ms_total=700):
    """跑一小段事件循环，让 QTimer.singleShot 的回调真正执行"""
    end = time.time() + ms_total / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


# ---- remember ----
before = len(fw.memory.facts)
fw._do_remember({"fact": "用户正在测试凌零"})
check("remember 写入记忆", len(fw.memory.facts) == before + 1
      and "用户正在测试凌零" in fw.memory.facts)
check("remember 气泡确认", any("记住了" in b for b in bubbles),
      str(bubbles[-1:]))
idx = fw.memory.facts.index("用户正在测试凌零")
fw.memory.forget_fact(idx)        # 清理，不留测试数据

fw._do_remember({"fact": ""})
check("remember 空事实友好提示", any("记住什么" in b for b in bubbles))

# ---- remind / todo ----
fw.tasks.reminders = [r for r in fw.tasks.reminders if "自测" not in r["text"]]
fw._do_remind({"text": "自测喝水", "seconds": "20"})
r = next((r for r in fw.tasks.reminders if r["text"] == "自测喝水"), None)
check("remind 登记任务台", r is not None)
check("remind 20秒精确登记",
      r is not None and 13 <= r["at"] - time.time() <= 25,
      str(r and r["at"] - time.time()))
check("remind 气泡含秒数", any("20 秒" in b and "自测喝水" in b
                              for b in bubbles), str(bubbles[-2:]))
if r:
    fw.tasks.reminders.remove(r)
fw.tasks.save()

fw._do_remind({"text": "自测复合", "minutes": "90"})
r = next((r for r in fw.tasks.reminders if r["text"] == "自测复合"), None)
check("remind 兼容 minutes(90分=1小时30分)",
      r is not None and 5390 <= r["at"] - time.time() <= 5410
      and any("1 小时 30 分钟" in b and "自测复合" in b for b in bubbles),
      str(r and r["at"] - time.time()))
if r:
    fw.tasks.reminders.remove(r)
fw.tasks.save()

fw._do_remind({"text": "", "seconds": "5"})
check("remind 空内容提示", any("提醒你什么" in b for b in bubbles))

n_open = len(fw.tasks.open_todos())
fw._do_todo_add({"text": "自测待办X"})
check("todo_add 加入清单", len(fw.tasks.open_todos()) == n_open + 1)
check("todo_add 气泡", any("自测待办X" in b for b in bubbles))
fw._do_todo_done({"index": n_open + 1})
check("todo_done 按序号完成",
      all(t["text"] != "自测待办X" or t["done"]
          for t in fw.tasks.todos))
fw.tasks.todos = [t for t in fw.tasks.todos if "自测" not in t["text"]]
fw.tasks.save()

fw._do_todo_done({"index": 99})
check("todo_done 越界友好提示", any("没找到" in b or "空的" in b
                                    for b in bubbles))

# ---- show_plan 确认流：出计划 -> 确认 -> 打勾 -> 完成汇报 ----
fw._plan = None
fw._plan_done = 0
fw._plan_pending_queue = []
fw._do_show_plan({"steps": ["第一步", "第二步"]})
check("show_plan 进入待确认",
      fw._plan is not None and fw._plan["status"] == "pending"
      and fw._plan["steps"] == ["第一步", "第二步"], str(fw._plan))
check("show_plan 暂不执行（等确认）", fw._plan_done == 0 and not fw._busy)
check("计划卡已创建", fw.plan_card is not None)

fw._plan_pending_queue = [
    {"action": "show_bubble", "text": "落实第一步"},
    {"action": "todo_add", "text": "自测计划第一步"},
    {"action": "show_bubble", "text": "落实第二步"},
    {"action": "todo_add", "text": "自测计划第二步"},
]
fw._confirm_plan()
check("确认后进入执行", fw._plan is not None
      and fw._plan["status"] == "running")
_pump(1500)         # 四个动作 + 间隔 + 收尾完成播报（说话类动作不打勾）
check("计划完成播报", any("计划执行完毕" in b for b in bubbles),
      str(bubbles[-2:]))
check("计划状态复位", fw._plan is None and fw._plan_done == 0)
fw.tasks.todos = [t for t in fw.tasks.todos
                  if "自测计划" not in t["text"]]
fw.tasks.save()
if fw.plan_card is not None:
    fw.plan_card.close()
    fw.plan_card = None

# ---- 计划执行循环：全自动推进，纯说话轮也过步骤，直到完成 ----
saved_use_real = config.USE_REAL_AI
config.USE_REAL_AI = False          # 强制离线 mock，测试不真联网
fw._plan = {"steps": ["步骤A", "步骤B"], "status": "running", "rounds": 1}
fw._plan_done = 0
fw._plan_round_had_actions = True
fw._action_queue = [
    {"action": "show_bubble", "text": "正在执行步骤A"},
    {"action": "todo_add", "text": "自测循环步骤"},
]
fw._busy = True
fw._run_next_action()
_pump(6000)         # 工具轮推进 1 步 -> mock 说话轮自动过步骤 -> 完成播报
check("计划全自动完成", fw._plan is None and not fw._busy, str(fw._plan))
check("自动完成播报", any("计划执行完毕" in b for b in bubbles),
      str(bubbles[-2:]))
fw.tasks.todos = [t for t in fw.tasks.todos
                  if t["text"] != "自测循环步骤"]
fw.tasks.save()
config.USE_REAL_AI = saved_use_real

# ---- 在途计划轮被打断后不留死标记（曾经的"计划卡死"根因）----
from framework.ai_engine import AIWorker as _AW
w = _AW("x", [], "活泼")
w.isRunning = lambda: True          # 模拟一轮请求还在路上
fw._ai_worker = w
fw._plan_round_pending = True
fw._plan = {"steps": ["步骤A", "步骤B"], "status": "running", "rounds": 1}
fw.send_command("继续")
check("打断在途请求后计划轮可重新推进",
      fw._plan is not None and fw._plan.get("rounds") == 2, str(fw._plan))
_pump(2500)         # 新一轮 mock 秒回 -> 说话轮过步骤 -> 剩 1 步待续
fw._plan = None     # 清理

# ---- PlanCard 控件：信号 / 渲染 / 跟随 ----
from ui.plan_card import PlanCard
card = PlanCard()
signals = []
card.confirm_requested.connect(lambda: signals.append("confirm"))
card.cancel_requested.connect(lambda: signals.append("cancel"))
card.popup(["甲", "乙", "丙"], pet_x=200, pet_y=200)
check("计划卡可见", card.isVisible())
check("计划卡按钮可见", not card._btn_start.isHidden())
card._btn_start.click()
check("开始执行信号", "confirm" in signals)
card._btn_cancel.click()
check("取消信号", "cancel" in signals)
card.update_progress(1)
check("执行中打勾渲染", "✔" in card._steps_label.text()
      and "▶" in card._steps_label.text())
card.mark_done()
check("完成态标题", "计划完成" in card._title.text())
card.close()

# ---- 一轮回复最多一条提醒（防冲突）----
fw._plan = None
dup = fw._dedupe_reminds([
    {"action": "remind", "text": "A", "seconds": 60},
    {"action": "remind", "text": "B", "seconds": 90},
    {"action": "show_bubble", "text": "x"},
])
check("重复 remind 去重", [a["action"] for a in dup]
      == ["remind", "show_bubble"], str(dup))
dup2 = fw._dedupe_reminds([
    {"action": "show_plan", "steps": ["a"]},
    {"action": "show_plan", "steps": ["b"]},
])
check("重复 show_plan 去重", len(dup2) == 1, str(dup2))

# ---- see_screen 无 Key 降级 ----
saved_key = config.VISION_API_KEY
config.VISION_API_KEY = ""
fw._do_see_screen({"question": "屏幕上是什么"})
check("无视觉Key友好降级", any("看不懂屏幕" in b or "视觉" in b
                                for b in bubbles))
config.VISION_API_KEY = saved_key
_pump(400)          # 收尾 _finish_action 跑完，复位 _busy

# ---- 意图兜底（模型只回话不给动作时）----
fw._current_user_text = "10分钟后提醒我吃饭"
acts = fw._ensure_tool_intent([])
check("兜底补充 remind(600秒)", acts and acts[-1]["action"] == "remind"
      and acts[-1]["seconds"] == 600, str(acts))
fw._current_user_text = "提醒我1分25秒后看电视"
acts = fw._ensure_tool_intent([])
check("兜底复合时长(85秒)", acts and acts[-1].get("seconds") == 85
      and acts[-1].get("text") == "看电视", str(acts))
fw._current_user_text = "看看屏幕上有什么"
acts = fw._ensure_tool_intent([])
check("兜底补充 see_screen", acts and acts[-1]["action"] == "see_screen")
fw._current_user_text = ""
check("兜底空文本不动", fw._ensure_tool_intent([]) == [])

# ---- send_command 快捷控制：待确认时说「取消计划」 ----
fw._plan = {"steps": ["唯一一步"], "status": "pending", "rounds": 0}
fw.send_command("取消计划")
check("待确认说取消即取消", fw._plan is None and not fw._busy, str(fw._plan))

# ============================================================
section("八、联网检索 / 读文档 / 交付 / 工作日志")
# ============================================================
import json
from framework.web_search import parse_search_payload, format_notes

# 任务数据沿用第七节的临时隔离文件（fw.tasks._file 已指向 temp）
iso = fw.tasks._file
fw.tasks.deliveries, fw.tasks.worklog = [], []

# ---- 动作解析 ----
a = normalize_action({"action": "web_search", "query": "新能源市场",
                      "count": "99"})
check("web_search 归一+条数封顶", a and a["query"] == "新能源市场"
      and a["count"] == 10, str(a))
check("web_search 缺词剔除",
      normalize_action({"action": "web_search"}) is None)
a = normalize_action({"action": "read_doc", "path": "D:/报告.docx"})
check("read_doc 归一", a and a["path"] == "D:/报告.docx", str(a))
check("read_doc 缺路径剔除",
      normalize_action({"action": "read_doc"}) is None)
a = normalize_action({"action": "analyze_data", "文件": "D:/销售.csv",
                      "分组": "区域", "图表": "饼图"})
check("analyze_data 中文参数别名归一",
      a and a["path"] == "D:/销售.csv" and a["group_by"] == "区域"
      and a["chart"] == "饼图", str(a))
check("analyze_data 缺路径剔除",
      normalize_action({"action": "analyze_data"}) is None)
check("work_log 无参数保留",
      normalize_action({"action": "work_log"}) is not None)
check("create_doc 网页别名归一",
      normalize_action({"action": "create_doc", "doc_type": "网页",
                        "content": {}}) is not None)

# ---- 搜索返回解析（两种历史格式 + 容错）----
payload = {"choices": [{"message": {"role": "tool", "tools": [
    {"search_result": [
        {"title": "结果一", "link": "https://a.com",
         "content": "x" * 900, "media": "A网"},
        {"title": "结果二", "link": "https://b.com",
         "content": "b内容", "media": "B网"}]}]}}]}
rs = parse_search_payload(payload)
check("搜索解析(工具字段)", len(rs) == 2 and rs[0]["title"] == "结果一",
      str(len(rs)))
check("摘要按上限截断",
      len(rs[0]["content"]) <= config.WEBSEARCH_SNIPPET_MAX)
payload2 = {"choices": [{"message": {"content": json.dumps(
    {"search_result": [{"title": "老格式", "link": "https://c.com",
                        "content": "c"}]})}}]}
rs2 = parse_search_payload(payload2)
check("搜索解析(content字符串)",
      len(rs2) == 1 and rs2[0]["title"] == "老格式")
check("搜索解析坏JSON返回空",
      parse_search_payload({"choices": [{"message": {
          "content": "not json"}}]}) == [])
note = format_notes("测试词", rs)
check("资料格式化含来源", "测试词" in note and "A网" in note)

# ---- mock 离线分支 ----
acts = mock_ai("帮我搜一下新能源最新消息", "活泼")
check("mock 搜索分支", any(a.get("action") == "web_search" for a in acts),
      str([a.get("action") for a in acts]))
acts = mock_ai("你今天干了啥", "活泼")
check("mock 日志分支", any(a.get("action") == "work_log" for a in acts))

# ---- 资料缓冲 ----
fw._reference_notes = []
fw._add_note("短资料")
fw._add_note("长" * 2000)
check("资料入库+截断", len(fw._reference_notes) == 2
      and len(fw._reference_notes[1]) == config.REFERENCE_NOTE_MAX)
for i in range(config.REFERENCE_NOTES_MAX + 3):
    fw._add_note(f"n{i}")
check("资料条数封顶丢最老", len(fw._reference_notes)
      == config.REFERENCE_NOTES_MAX
      and fw._reference_notes[-1] == f"n{config.REFERENCE_NOTES_MAX + 2}")
check("资料拼接注入", "n4" in fw._notes_text())

# ---- 无搜索Key友好降级 ----
saved_ws = config.WEBSEARCH_API_KEY
config.WEBSEARCH_API_KEY = ""
fw._do_web_search({"action": "web_search", "query": "测试词"})
check("无搜索Key友好降级", any("搜索" in b and "Key" in b
                                for b in bubbles))
config.WEBSEARCH_API_KEY = saved_ws
_pump(400)

# ---- read_doc：txt 读取入库 + 记日志 + 跟进轮 ----
tmpdir = tempfile.mkdtemp(prefix="pet_read_doc_")
txt = os.path.join(tmpdir, "资料.txt")
with open(txt, "w", encoding="utf-8") as f:
    f.write("凌零测试文档：本季营收增长 30%。")
fw._reference_notes = []
fw._do_read_doc({"action": "read_doc", "path": txt})
check("read_doc 读取入库",
      fw._reference_notes and "营收增长" in fw._reference_notes[0])
check("read_doc 记工作日志",
      any("资料.txt" in w["text"] for w in fw.tasks.worklog))
check("read_doc 气泡确认", any("读完了" in b and "资料.txt" in b
                                for b in bubbles))
fw._do_read_doc({"action": "read_doc",
                 "path": os.path.join(tmpdir, "不存在.txt")})
check("read_doc 缺文件友好", any("不存在" in b for b in bubbles))
_pump(1600)          # 跟进轮跑完，复位状态

# ---- 交付 / 工作日志（TaskBoard 层）----
d1 = fw.tasks.add_delivery("报告.pptx", "C:/x/报告.pptx", "ppt")
d2 = fw.tasks.add_delivery("报告.pptx", "C:/x/报告.pptx", "ppt")
check("交付按路径去重", d1 is d2 and len(fw.tasks.deliveries) == 1)
fw.tasks.add_delivery("数据.xlsx", "C:/x/数据.xlsx", "xlsx")
check("交付按序删除",
      fw.tasks.remove_delivery(1)["name"] == "报告.pptx"
      and len(fw.tasks.deliveries) == 1)
check("交付越界返回None", fw.tasks.remove_delivery(5) is None)
fw.tasks.log_work("plan", "完成了一个测试计划")
fw.tasks.worklog.append({"ts": time.time() - 86400,
                         "kind": "misc", "text": "昨天的事"})
today = [w["text"] for w in fw.tasks.today_worklog()]
check("今日日志过滤昨天", "完成了一个测试计划" in today
      and "昨天的事" not in today, str(today))
fw._do_work_log()
check("work_log 动作产出汇报条目",
      any(w["kind"] == "report" for w in fw.tasks.worklog))
os.remove(iso)

# ---- analyze_data：表格统计入库 + 出图交付 + 记日志 ----
from framework import data_tool

csv_path = os.path.join(tmpdir, "月度销售.csv")
with open(csv_path, "w", encoding="utf-8-sig") as f:
    f.write("月份,区域,销售额\n1月,华东,100\n2月,华北,80\n3月,华东,150\n")
chart_dir = os.path.join(tmpdir, "analysis")
os.makedirs(chart_dir, exist_ok=True)
data_tool.app_data_dir = lambda: chart_dir    # 图表别写进真实 data/
fw._reference_notes = []
fw._do_analyze_data({"action": "analyze_data", "path": csv_path,
                     "group_by": "区域", "chart": "柱状图"})
check("analyze_data 统计入资料缓冲",
      fw._reference_notes and "数据分析" in fw._reference_notes[0]
      and "华东:250" in fw._reference_notes[0], str(fw._reference_notes[:1]))
check("analyze_data 图表进交付面板",
      any(d["kind"] == "png" and os.path.isfile(d["path"])
          for d in fw.tasks.deliveries))
check("analyze_data 记工作日志",
      any("月度销售.csv" in w["text"] for w in fw.tasks.worklog))
check("analyze_data 气泡确认", any("算完了" in b for b in bubbles))
fw._do_analyze_data({"action": "analyze_data",
                     "path": os.path.join(tmpdir, "不存在.csv")})
check("analyze_data 缺文件友好", any("表格文件不存在" in b for b in bubbles))
_pump(1600)

# ============================================================
print(f"\n{'=' * 60}\n结果: {PASS} 通过 / {FAIL} 失败\n{'=' * 60}")
sys.exit(1 if FAIL else 0)
