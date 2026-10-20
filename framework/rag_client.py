# -*- coding: utf-8 -*-
"""RAG 客户端：主进程侧，通过子进程调用 RAG

主进程不直接 import langchain / chroma / torch，只调用本模块。
子进程由本模块懒启动，崩溃自动重建。
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time

from framework.utils import logger

_TIMEOUT = float(os.environ.get("RAG_TIMEOUT", "600"))  # 首次含模型下载

_LOCK = threading.Lock()
_PROC = None
_QUEUE = None
_READER = None


def _pump_stdout(proc: subprocess.Popen, q: "queue.Queue"):
    """把子进程 stdout 的行投递到队列；EOF 投 None。"""
    try:
        for raw in proc.stdout:
            q.put(raw)
    finally:
        q.put(None)


def _spawn_worker():
    global _PROC, _QUEUE, _READER
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if getattr(sys, "frozen", False):
        # PyInstaller 打包后：exe --rag-worker
        cmd = [sys.executable, "--rag-worker"]
    else:
        cmd = [sys.executable, "-m", "framework.rag_worker"]
    proc = subprocess.Popen(
        cmd, cwd=root,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        env=os.environ.copy(),   # 继承 HF_ENDPOINT 等
    )
    q = queue.Queue()
    reader = threading.Thread(target=_pump_stdout, args=(proc, q),
                              daemon=True, name="rag-worker-reader")
    reader.start()
    _PROC, _QUEUE, _READER = proc, q, reader
    logger.info("[RAG] 子进程已启动（与主进程原生库隔离）")


def _get_worker():
    if _PROC is None or _PROC.poll() is not None:
        _drop_worker()
        _spawn_worker()
    return _PROC, _QUEUE


def _drop_worker():
    global _PROC, _QUEUE, _READER
    if _PROC is not None:
        try:
            _PROC.kill()
        except Exception:  # noqa: BLE001
            pass
        try:
            _PROC.stdin.close()
            _PROC.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            _PROC.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
    _PROC, _QUEUE, _READER = None, None, None


def _call(req: dict) -> dict:
    """发一条请求，等一条响应；子进程崩溃自动重建重试一次。"""
    req_line = json.dumps(req, ensure_ascii=False)
    with _LOCK:
        last_err = None
        for attempt in (1, 2):
            proc, q = _get_worker()
            try:
                proc.stdin.write((req_line + "\n").encode("utf-8"))
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                _drop_worker()
                last_err = e
                logger.warning(f"[RAG] 子进程管道断开（第 {attempt} 次），"
                               "已重建，将重试")
                continue

            deadline = time.monotonic() + _TIMEOUT
            while True:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    _drop_worker()
                    raise RuntimeError(
                        f"RAG 子进程超时（>{_TIMEOUT:.0f}s）")
                try:
                    raw = q.get(timeout=remain)
                except queue.Empty:
                    _drop_worker()
                    raise RuntimeError(
                        f"RAG 子进程超时（>{_TIMEOUT:.0f}s）")
                if raw is None:
                    _drop_worker()
                    last_err = RuntimeError("RAG 子进程异常退出")
                    logger.warning(f"[RAG] 子进程异常退出（第 {attempt} 次），"
                                   "已重建，将重试")
                    break
                try:
                    msg = json.loads(raw.decode("utf-8"))
                    if isinstance(msg, dict) and "ok" in msg:
                        return msg
                except (ValueError, UnicodeDecodeError):
                    # 子进程有第三方库直接往 stdout 打印时，跳过
                    logger.warning(f"[RAG] 忽略非协议输出: {raw[:100]!r}")
                    continue
        raise RuntimeError(
            "RAG 子进程连续崩溃（主程序已隔离保护，其他功能不受影响）"
        ) from last_err


# ============================================================
# 对外接口（在主进程任意地方直接调用）
# ============================================================
def add_text(text: str, source: str = "manual") -> int:
    r = _call({"op": "add_text", "text": text, "source": source})
    if not r.get("ok"):
        raise RuntimeError(r.get("error", "add_text 失败"))
    return int(r.get("chunks", 0))


def add_file(path: str) -> int:
    r = _call({"op": "add_file", "path": path})
    if not r.get("ok"):
        raise RuntimeError(r.get("error", "add_file 失败"))
    return int(r.get("chunks", 0))


def search(query: str, top_k: int = 3) -> list:
    r = _call({"op": "search", "query": query, "top_k": top_k})
    if not r.get("ok"):
        raise RuntimeError(r.get("error", "search 失败"))
    return r.get("hits") or []


def format_context(hits: list, max_chars: int = 2000) -> str:
    r = _call({"op": "format_context", "hits": hits,
               "max_chars": max_chars})
    if not r.get("ok"):
        raise RuntimeError(r.get("error", "format_context 失败"))
    return r.get("text") or ""


def count() -> int:
    try:
        r = _call({"op": "count"})
        return int(r.get("count", 0)) if r.get("ok") else -1
    except Exception:  # noqa: BLE001
        return -1


def reset():
    _call({"op": "reset"})


def warm_up():
    """可选：主进程启动时预热 RAG（会拉起子进程加载模型）。"""
    try:
        _call({"op": "ping"})
        _call({"op": "count"})
        logger.info("[RAG] 子进程预热完成")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[RAG] 预热失败（不影响其他功能）: {e}")