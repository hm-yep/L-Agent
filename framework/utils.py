# -*- coding: utf-8 -*-
"""工具模块：日志 + 资源路径"""

import logging
import os
import sys


def resource_path(relative: str) -> str:
    """
    资源文件加载统一入口。
    兼容开发环境与 PyInstaller 打包后的环境（_MEIPASS 临时目录）。
    """
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


def app_data_dir() -> str:
    """可写数据目录（记忆文件、日志），打包后也不写进程序目录"""
    if getattr(sys, "frozen", False):
        # exe 位于 <项目>\dist\ 时与开发版共用 <项目>\data，两版记录不分裂；
        # exe 被单独挪走时退回 exe 旁边的 data
        project_data = os.path.join(
            os.path.dirname(os.path.dirname(sys.executable)), "data")
        if os.path.isdir(project_data):
            return project_data
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data = os.path.join(base, "data")
    os.makedirs(data, exist_ok=True)
    return data


def setup_logger(name: str = "desktop_assistant") -> logging.Logger:
    """初始化日志：控制台 + 文件双输出"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    try:
        log_dir = os.path.join(app_data_dir(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.FileHandler(
            os.path.join(log_dir, "app.log"), encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass  # 打包环境无写权限时忽略

    return logger


logger = setup_logger()


def human_size(num_bytes: int) -> str:
    """字节数转可读字符串"""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} PB"
