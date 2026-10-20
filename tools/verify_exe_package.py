# -*- coding: utf-8 -*-
"""校验打包产物：exe 内确实含最新代码，且没有把 API Key / 聊天记录打进去

用法: python tools/verify_exe_package.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyInstaller.archive.readers import (CArchiveReader,
                                          ZlibArchiveReader)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
exe = os.path.join(ROOT, "dist", "凌零桌面AI助手.exe")

reader = CArchiveReader(exe)
names = list(reader.toc)
print(f"exe: {exe}")
print(f"归档条目: {len(names)} 个")

# ---- 1. 修复代码在不在包里 ----
def _collect(code, out: set):
    """递归收集代码对象里的函数名/全局名，用来证明某个实现确实在包里"""
    out.add(code.co_name)
    out.update(code.co_names)
    for const in code.co_consts:
        if hasattr(const, "co_name"):
            _collect(const, out)
        elif isinstance(const, str):
            out.add(const)


pyz = reader.open_embedded_archive("PYZ.pyz")
mods = list(pyz.toc)
print(f"PYZ 模块: {len(mods)} 个")

wanted = [("ui.ui_manager", "show_near"), ("ui.pet_window", "show_near"),
          ("framework.power_controller", "cancel_shutdown"),
          ("framework.core", "ALLOW_POWER_ACTIONS"),
          ("framework.config", "SETTINGS_APP_DIR"),
          ("framework.doc_generator", "normalize_doc_type"),
          ("framework.core", "_do_create_doc"),
          ("framework.action_parser", "create_doc"),
          ("framework.doc_generator", "fetch_images"),
          ("framework.doc_editor", "extract_content"),
          ("framework.core", "request_doc_edit"),
          # ---- 本轮新增能力（缺失=包里是旧代码）----
          ("framework.core", "_plan_replan"),
          ("framework.core", "_do_run_workflow"),
          ("PYZ:main", "install_crash_guards"),
          ("framework.action_parser", "run_workflow"),
          ("ui.input_dialog", "_toggle_fold"),
          ("ui.plan_card", "update_steps"),
          # ---- 视觉链路加固（缺失=包里是旧代码）----
          ("framework.core", "vision_timeout_seconds"),
          ("framework.core", "_on_vision_watchdog"),
          # ---- 样式定制/保存位置/看门狗/多步任务（缺失=包里是旧代码）----
          ("framework.doc_generator", "resolve_style"),
          ("framework.doc_generator", "resolve_location"),
          ("framework.core", "_on_chain_watchdog"),
          ("framework.ai_engine", "split_multi_task"),
          # ---- 质量门（软失败自我纠正，缺失=包里是旧代码）----
          ("framework.core", "_handle_search_quality"),
          ("framework.core", "_doc_quality_followup"),
          ("framework.quality_gate", "assess_search"),
          ("framework.quality_gate", "assess_doc_spec")]
for mod, needle in wanted:
    if mod == "PYZ:main":
        # 入口脚本 main.py 不在 PYZ，而是作为 'main' 条目附在 CArchive 里
        if "main" not in reader.toc:
            print("  [FAIL] 包里没有入口模块 main")
            continue
        import marshal
        raw = reader.extract("main")
        try:
            code = marshal.loads(raw)
        except (ValueError, EOFError):
            code = marshal.loads(raw[16:])   # 带 pyc 头时跳过
        mod = "main"
    elif mod not in mods:
        print(f"  [FAIL] 包里没有模块 {mod}")
        continue
    else:
        code = pyz.extract(mod)
    symbols = set()
    _collect(code, symbols)
    ok = needle in symbols
    print(f"  [{'PASS' if ok else 'FAIL'}] {mod} 含符号 {needle!r}"
          + ("" if ok else f"（共 {len(symbols)} 个符号）"))

# ---- 1b. 设置确实存到 %APPDATA%（exe 挪位置/换电脑都不丢 key）----
cfg_symbols = set()
_collect(pyz.extract("framework.config"), cfg_symbols)
ok = "APPDATA" in cfg_symbols and "DesktopPet" in cfg_symbols
print(f"  [{'PASS' if ok else 'FAIL'}] "
      f"framework.config 使用 %APPDATA%\\DesktopPet 固定存储用户设置")

# ---- 1c. 文档生成依赖（pptx/docx/openpyxl）连同模板数据入包 ----
doc_mods = {"pptx", "docx", "openpyxl"}
for m in doc_mods:
    hit = any(n == m or n.startswith(m + ".") for n in mods)
    print(f"  [{'PASS' if hit else 'FAIL'}] PYZ 含文档库 {m}")
doc_data = [n for n in names if "pptx" in n.lower() and
            n.lower().endswith((".pptx", ".png"))]
print(f"    [{'PASS' if doc_data else 'FAIL'}] "
      f"pptx 模板数据入包（{len(doc_data)} 个文件）")

# ---- 2. 密钥/聊天记录绝不能进包 ----
key = ""
settings = os.path.join(os.environ.get("APPDATA", ""),
                        "DesktopPet", "user_settings.json")
if not os.path.exists(settings):
    settings = os.path.join(ROOT, "data", "user_settings.json")
if os.path.exists(settings):
    with open(settings, encoding="utf-8") as f:
        key = json.load(f).get("api_key", "")

blob = b""
for n in names:
    try:
        blob += reader.extract(n)
    except Exception:      # 二进制/压缩条目跳过
        pass
print(f"  扫描 {len(blob)} 字节归档内容:")
if key:
    print(f"    [{'FAIL' if key.encode() in blob else 'PASS'}] "
          f"API Key 未被打包（key 长度 {len(key)}）")
else:
    print("    [SKIP] 本地没有配置 API Key")
for word in ("user_settings.json", "memory.json"):
    print(f"    [{'FAIL' if word.encode() in blob else 'PASS'}] 不含 {word}")

# ---- 3. 资源是否齐全 ----
for need in ("assets/icon.ico", "ui/style.qss"):
    hit = [n for n in names if n.replace("\\", "/").endswith(need)]
    print(f"    [{'PASS' if hit else 'FAIL'}] 资源 {need}")
