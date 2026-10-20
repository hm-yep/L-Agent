# -*- coding: utf-8 -*-
"""打两个发布包（输出到项目上级目录）：
1. 凌零桌面AI助手.zip —— dist/凌零桌面AI助手.exe
2. desktop_assistant.zip —— 源码（排除 .venv/build/dist/data/.git/__pycache__ 等）
"""
import os
import zipfile

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根
OUT = os.path.dirname(PROJ)               # 输出到上级 workspace 目录

# 与 .gitignore 对齐的排除项；data/ 内含 API Key，必须排除
SKIP_DIRS = {".venv", "venv", "__pycache__", ".idea", "dist", "build",
             "data", ".git", ".vscode"}
SKIP_EXT = {".pyc", ".pyo", ".zip"}       # .zip：防止把旧打包残留/输出收进包里


def zip_source(zpath):
    n = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, dirs, files in os.walk(PROJ):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                if os.path.splitext(f)[1].lower() in SKIP_EXT:
                    continue
                p = os.path.join(root, f)
                arc = os.path.join("desktop_assistant",
                                   os.path.relpath(p, PROJ))
                z.write(p, arc)
                n += 1
    return n


def zip_exe(zpath):
    exe = os.path.join(PROJ, "dist", "凌零桌面AI助手.exe")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(exe, "凌零桌面AI助手.exe")
    return 1


if __name__ == "__main__":
    src_zip = os.path.join(OUT, "desktop_assistant.zip")
    exe_zip = os.path.join(OUT, "凌零桌面AI助手.zip")
    n = zip_source(src_zip)
    zip_exe(exe_zip)
    print(f"源码包: {src_zip}  文件数 {n}")
    print(f"exe 包: {exe_zip}")

    # 安全校验：包内不得出现 data/ 或任何设置/密钥文件
    for zp in (src_zip, exe_zip):
        bad = []
        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
            for nm in names:
                low = nm.lower()
                if "/data/" in low or low.endswith(("user_settings.json",
                                                    "memory.json", ".env")):
                    bad.append(nm)
        print(f"  {os.path.basename(zp)}: {len(names)} 项, "
              f"敏感文件检查: {'发现 ' + str(bad) if bad else '通过'}")
