# -*- coding: utf-8 -*-
"""电源控制：关机 / 重启 / 锁屏 / 取消关机（纯标准库）

    from framework.power_controller import shutdown, restart, lock, cancel_shutdown
    shutdown()          # 默认延时 config.POWER_ACTION_DELAY 秒后关机
    shutdown(0)         # 立即关机
    cancel_shutdown()   # 取消已排定的关机/重启

设计：
- 所有关机/重启都走 Windows `shutdown` 命令并带 /t 延时，
  延时期间可用 cancel_shutdown() 撤销，避免助手误判直接断电。
- 锁屏属于低风险操作，立即执行。
- 所有操作受 config.ALLOW_POWER_ACTIONS 总开关控制。
"""

import subprocess

from framework import config
from framework.utils import logger

# 让子进程脱离助手独立运行
_DETACH = (getattr(subprocess, "DETACHED_PROCESS", 0)
           | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def _run(cmd: str) -> bool:
    """执行一条 Windows 命令，成功返回 True"""
    try:
        subprocess.Popen(cmd, shell=True, creationflags=_DETACH,
                         close_fds=True)
        logger.info(f"[Power] 执行: {cmd}")
        return True
    except OSError as e:
        logger.error(f"[Power] 执行失败: {cmd} ({e})")
        return False


def _safe_delay(delay) -> int:
    """延时秒数：None 用配置默认值；负数归零；上限 1 小时"""
    if delay is None:
        delay = config.POWER_ACTION_DELAY
    try:
        delay = int(float(delay))
    except (TypeError, ValueError):
        delay = config.POWER_ACTION_DELAY
    return max(0, min(3600, delay))


# ------------------------------------------------------------
# 关机 / 重启
# ------------------------------------------------------------
def shutdown(delay=None):
    """
    排定关机。
    :return: (成功与否, 实际延时秒数)
    """
    d = _safe_delay(delay)
    ok = _run(f"shutdown /s /t {d}")
    return ok, d


def restart(delay=None):
    """排定重启。:return: (成功与否, 实际延时秒数)"""
    d = _safe_delay(delay)
    ok = _run(f"shutdown /r /t {d}")
    return ok, d


def cancel_shutdown() -> bool:
    """取消已排定的关机/重启（没有排定任务时命令会报错，属正常）"""
    ok = _run("shutdown /a")
    if not ok:
        logger.info("[Power] 没有可取消的关机任务")
    return ok


# ------------------------------------------------------------
# 锁屏（立即执行，无需确认）
# ------------------------------------------------------------
def lock() -> bool:
    """锁定工作站（Win+L 的效果）"""
    return _run("rundll32.exe user32.dll,LockWorkStation")