# -*- coding: utf-8 -*-
"""音量功能回归（会真实读写系统音量，测完恢复原值）

用法: python tools/test_volume.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))   # 项目根目录

from framework import volume_controller as volume
from framework.action_parser import parse_actions
from framework.ai_engine import mock_ai, _extract_volume_number, _extract_volume_app

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


print("一、纯解析（不动系统）")
for text, num, app in (
    ("把音量调到 30", 30, ""),
    ("音量调到百分之六十", 60, ""),
    ("帮我把音量调到50%", 50, ""),
    ("把微信的音量调到 20", 20, "微信"),
    ("把网易云的音量调到80", 80, "网易云"),
    ("调一下chrome的音量到 15", 15, "chrome"),
    ("声音调大一点", None, ""),
):
    got_n, got_a = _extract_volume_number(text), _extract_volume_app(text)
    check(f"「{text}」-> {num}/{app!r}", (got_n, got_a) == (num, app),
          f"实际 {got_n}/{got_a!r}")

out = parse_actions([{"action": "set_volume", "volume": "55", "app": "微信"}])
check("解析 set_volume 带 app", out and out[0].get("app") == "微信", str(out))
acts = [a["action"] for a in mock_ai("帮我调节音量到40", "活泼")]
check("离线模拟落到 set_volume", "set_volume" in acts, str(acts))

print("\n二、真机读写（测完恢复原值）")
before = volume.get_system_volume()
print(f"     当前系统音量 {before}%")
check("读得到系统音量", isinstance(before, int) and 0 <= before <= 100)

target = 40 if before != 40 else 45
applied = volume.set_system_volume(target)
read_back = volume.get_system_volume()
check(f"设置 {target}% 后读回一致", read_back == target,
      f"set={applied} 读回={read_back}")
check("越界值被夹紧（250 -> 100）",
      volume.get_system_volume() == target and volume.set_system_volume(250) in (100, -1)
      and volume.get_system_volume() == 100, f"读回={volume.get_system_volume()}")

restored = volume.set_system_volume(before)
check(f"恢复原音量 {before}%", volume.get_system_volume() == before,
      f"读回={volume.get_system_volume()}")

print("\n三、单应用音量：只动应用，不动系统")
kept = volume.get_system_volume()
matched = volume.set_app_volume("绝对不存在的应用xyzzy", 50)
check("找不到会话时返回空列表", matched == [], str(matched))
check("系统音量未被改动", volume.get_system_volume() == kept,
      f"{kept} -> {volume.get_system_volume()}")

hit = volume.set_app_volume("wallpaper", 50)
print(f"     关键词 wallpaper 命中: {hit}")
check("命中时返回被调的应用名", bool(hit) and all(isinstance(x, str) for x in hit),
      str(hit))
check("调应用音量后系统音量不变", volume.get_system_volume() == kept,
      f"{kept} -> {volume.get_system_volume()}")

print()
if FAILS:
    print(f"共 {len(FAILS)} 项失败: {FAILS}")
    sys.exit(1)
print("ALL TESTS PASS")
