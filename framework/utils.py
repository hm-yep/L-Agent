# -*- coding: utf-8 -*-
"""工具模块：日志 + 资源路径"""

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler


# ------------------------------------------------------------
# 日志脱敏：Key / Bearer / password 一律不落盘
# ------------------------------------------------------------
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}"
    r"|Bearer\s+[A-Za-z0-9._\-]{8,}"
    r"|(?:api[_-]?key|apikey|token|password|passwd|secret)\s*[=:]\s*"
    r"[^\s,;\"']{6,})",
    re.IGNORECASE,
)


def redact_for_log(text: str) -> str:
    """把明显是凭据的片段替换掉；用于所有可能来自外部输入的日志。"""
    return _SECRET_RE.sub("***", str(text))


class _RedactingFilter(logging.Filter):
    """兜底过滤器：即使某个调用点忘了脱敏，也不会把凭据写进 app.log。"""

    def filter(self, record):
        try:
            if record.msg is not None:
                record.msg = redact_for_log(str(record.msg))
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: redact_for_log(str(v))
                                   for k, v in record.args.items()}
                else:
                    record.args = tuple(redact_for_log(str(a))
                                        for a in record.args)
        except Exception:                      # noqa: BLE001 日志绝不能反过来搞崩程序
            pass
        return True


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
    """可写数据目录（记忆文件、日志、轨迹、偏好、规则），打包后也不写进程序目录。

    测试用 `LINGLING_DATA_DIR` 覆盖：pytest / 集成自检会在 import framework
    之前设好它，保证轨迹与偏好这类新增落盘文件绝不写脏用户的真实 data/。
    """
    override = os.environ.get("LINGLING_DATA_DIR", "").strip()
    if override:
        os.makedirs(override, exist_ok=True)
        return override
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
    console.addFilter(_RedactingFilter())
    logger.addHandler(console)

    try:
        log_dir = os.path.join(app_data_dir(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        # 轮转：单文件 2 MB、最多留 3 份，避免日志无限增长
        fh = RotatingFileHandler(
            os.path.join(log_dir, "app.log"), encoding="utf-8",
            maxBytes=2 * 1024 * 1024, backupCount=3,
        )
        fh.setFormatter(fmt)
        fh.addFilter(_RedactingFilter())
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


def normalize_intent(text: str) -> str:
    """去掉标点、空格、大小写：短指令逐字比较前都先过这里。

    判据只有这一份——「撤销」的快路径和技能直放必须用同一套写法归一，
    否则同一句话在一条路上被认出来，在另一条路上被当成闲聊。
    """
    return re.sub(r"[\s，。！？、,.!?；;:：“”\"'~～]", "",
                  (text or "").strip()).lower()
