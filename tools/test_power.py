# -*- coding: utf-8 -*-
"""电源功能离线验证（真机执行被替换为打桩，绝不会真的关机/重启/锁屏）

覆盖：动作解析白名单、离线模拟、意图兜底、Framework 执行分支、总开关拦截。
用法: python tools/test_power.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))   # 项目根目录

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

from framework import config, power_controller
from framework.action_parser import parse_actions
from framework.ai_engine import mock_ai

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


# ============================================================
# 打桩：任何真实电源操作都只记录，不执行
# ============================================================
import subprocess

calls = []
power_controller.shutdown = lambda delay=None: (calls.append(("shutdown", delay)) or (True, 60))
power_controller.restart = lambda delay=None: (calls.append(("restart", delay)) or (True, 60))
power_controller.lock = lambda: (calls.append(("lock", None)) or True)
power_controller.cancel_shutdown = lambda: (calls.append(("cancel_shutdown", None)) or True)

# 兜底：万一有代码路径绕过上面的替换，也不允许真的调用 shutdown.exe。
# 同时记下真实命令字符串，用于校验参数拼装。
cmds = []
_orig_popen = subprocess.Popen


def _guard(cmd, *a, **kw):
    cmds.append(cmd)
    if "shutdown" in str(cmd) or "LockWorkStation" in str(cmd):
        raise OSError("测试禁止真实执行电源命令")   # _run 按失败处理，不影响被测逻辑
    return _orig_popen(cmd, *a, **kw)


subprocess.Popen = _guard
power_controller.subprocess.Popen = _guard

app = QApplication(sys.argv)


def pump(seconds=2.0):
    """转事件循环若干真实时间（QTimer 需要真实经过的时间才会触发）"""
    import time
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)

# ============================================================
print("一、动作解析：四个电源动作进白名单，delay 可选")
# ============================================================
for raw, expect in (
    ([{"action": "shutdown"}], "shutdown"),
    ([{"action": "restart", "delay": "30"}], "restart"),
    ([{"action": "lock"}], "lock"),
    ([{"action": "cancel_shutdown"}], "cancel_shutdown"),
    ([{"action": "shutdown", "延时": 120}], "shutdown"),
    ([{"action": "shutdown", "delay": "90秒"}], "shutdown"),
):
    out = parse_actions(raw)
    ok = len(out) == 1 and out[0].get("action") == expect
    extra = ""
    if expect in ("shutdown", "restart") and ok:
        extra = f"delay={out[0].get('delay')!r}"
    check(f"解析 {raw}", ok, extra)

out = parse_actions([{"action": "shutdown"}])
check("shutdown 无必填参数也能通过", len(out) == 1, str(out))
only = parse_actions([{"action": "reboot_os"}])
check("未知动作仍被剔除",
      all(a.get("action") != "reboot_os" for a in only), str(only))

# ============================================================
print("\n二、离线模拟：口语说法都能落到正确动作")
# ============================================================
for text, expect in (
    ("帮我关机", "shutdown"),
    ("关电脑", "shutdown"),
    ("重启一下电脑", "restart"),
    ("锁一下屏幕", "lock"),
    ("取消关机", "cancel_shutdown"),
    ("别关机了", "cancel_shutdown"),
):
    acts = [a["action"] for a in mock_ai(text, "活泼")]
    check(f"「{text}」-> {expect}", expect in acts, str(acts))

check("确认语提醒可以反悔", any("取消关机" in a.get("text", "")
                            for a in mock_ai("帮我关机", "活泼")))

# ============================================================
print("\n三、真实命令拼装（subprocess 已打桩，只记录命令字符串）")
# ============================================================
_orig = power_controller._run
cmds.clear()
_orig("shutdown /s /t 30")
_orig("shutdown /r /t 60")
_orig("shutdown /a")
_orig("rundll32.exe user32.dll,LockWorkStation")
check("关机命令 shutdown /s /t N", cmds[0] == "shutdown /s /t 30", str(cmds[0]))
check("重启命令 shutdown /r /t N", cmds[1] == "shutdown /r /t 60", str(cmds[1]))
check("取消命令 shutdown /a", cmds[2] == "shutdown /a", str(cmds[2]))
check("锁屏命令 LockWorkStation",
      cmds[3] == "rundll32.exe user32.dll,LockWorkStation", str(cmds[3]))

# ============================================================
print("\n四、Framework 执行：走真实代码路径，命令被替换为打桩")
# ============================================================
from ui.pet_window import PetWindow

win = PetWindow()
fw = win.fw
config.POWER_REPLY_MS = 10          # 别等 3 秒确认气泡

state = {"bubbles": []}
fw.register_callback("show_bubble", lambda t: state["bubbles"].append(t))


def scenario(label, text, expect_call):
    calls.clear()
    state["bubbles"].clear()
    fw.send_command(text)
    pump(1.2)                       # 等动作队列跑空（含 10ms 定时器）
    ok = bool(calls) and calls[0][0] == expect_call
    said = " / ".join(b for b in state["bubbles"] if b)[:70]
    check(f"{label}「{text}」", ok, f"调用={calls} 气泡={said!r}")


scenario("关机", "帮我关机", "shutdown")
scenario("重启", "重启电脑", "restart")
scenario("锁屏", "锁一下屏幕", "lock")
scenario("取消", "取消关机", "cancel_shutdown")

# ============================================================
print("\n五、总开关：ALLOW_POWER_ACTIONS=False 时一律拒绝")
# ============================================================
config.ALLOW_POWER_ACTIONS = False
calls.clear()
state["bubbles"].clear()
fw.send_command("帮我关机")
pump(1.2)
check("总开关关闭时不执行关机", not calls, f"调用={calls}")
check("总开关关闭时给出提示",
      any("禁用" in b for b in state["bubbles"]),
      " / ".join(b for b in state["bubbles"] if b)[:70])
config.ALLOW_POWER_ACTIONS = True

# ============================================================
print("\n六、延时参数：默认值/自定义/越界")
# ============================================================
default = config.POWER_ACTION_DELAY
for given, expect in ((None, default), ("", default), ("30", 30),
                      ("120秒", default),    # 单位由 _do_power 先剥离，这里解析不了就用默认
                      (-5, 0), ("99999", 3600)):
    got = power_controller._safe_delay(given)
    check(f"延时 {given!r} -> {expect}", got == expect, f"实际 {got}")

print()
subprocess.Popen = _orig_popen
power_controller.subprocess.Popen = _orig_popen
win.close()
if FAILS:
    print(f"共 {len(FAILS)} 项失败: {FAILS}")
    sys.exit(1)
print("ALL TESTS PASS")
