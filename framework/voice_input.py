# -*- coding: utf-8 -*-
"""语音输入：Whisper 语音转文字（独立子进程运行）

为什么必须放独立子进程：
    桌面助手主进程加载了 PyQt5 / OpenCV / MediaPipe / PyTorch 等原生库。
    实测 PyQt5 与 faster-whisper 底层的 CTranslate2 同进程共存时，在
    WhisperModel 加载阶段就会触发访问冲突（Windows 退出码 0xC0000005），
    整个程序无声闪退，Python 层的异常钩子捕获不到。

    因此转写跑在一个专用的常驻子进程里（`python -m framework.voice_input`，
    打包 exe 后为 `exe --voice-worker`，见 main.py 顶部）：
      - 子进程只加载语音相关库（不含 Qt/cv2/mediapipe/torch），
        模型加载一次后常驻复用；
      - 父进程与子进程之间用 stdin/stdout 的 JSON 行协议通信；
      - 父进程退出时管道关闭，子进程自动结束；
      - 子进程崩溃只影响当次识别（自动重建、自动重试一次），
        桌宠窗口与 API 服务不受影响。

依赖（二选一）：
    pip install faster-whisper
    pip install openai-whisper

环境变量：
    WHISPER_MODEL     模型规格，默认 base
    VOICE_TIMEOUT     单次识别超时秒数，默认 600（首次含模型下载）
    VOICE_INPROCESS=1 调试用：强制进程内运行（桌面环境下会重新暴露闪退风险）
"""

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time

from framework.utils import logger

_MODEL_SIZE = os.environ.get("WHISPER_MODEL", "base")
_INPROCESS = os.environ.get("VOICE_INPROCESS", "") == "1"
_RESP_TIMEOUT = float(os.environ.get("VOICE_TIMEOUT", "600"))

# ---- 主进程侧状态 ----
_LOCK = threading.Lock()   # FastAPI 线程池并发调用时，串行复用同一个子进程
_PROC = None               # subprocess.Popen
_QUEUE = None              # queue.Queue，reader 线程投递子进程 stdout 行
_READER = None

# ---- 子进程侧状态 ----
_MODEL = None  # (model_size, (backend, model))


# ============================================================
# 子进程侧实现
# ============================================================
def _ensure_av_compat():
    """兼容补丁：PyAV 15+ 移除了 open() 的 metadata_errors 参数，
    而 faster-whisper 1.2.x 仍在传它，会直接 TypeError。"""
    import av

    if getattr(av.open, "_lingling_compat", False):
        return
    orig_open = av.open

    def _open_compat(*args, **kwargs):
        if "metadata_errors" in kwargs:
            try:
                return orig_open(*args, **kwargs)
            except TypeError:
                kwargs.pop("metadata_errors")
        return orig_open(*args, **kwargs)

    _open_compat._lingling_compat = True
    av.open = _open_compat


def _load_model(model_size: str):
    try:
        from faster_whisper import WhisperModel
        pair = ("faster", WhisperModel(model_size, device="cpu",
                                       compute_type="int8"))
        logger.info(f"[Voice] 已加载 faster-whisper ({model_size})")
        return pair
    except ImportError:
        try:
            import whisper
            pair = ("openai", whisper.load_model(model_size))
            logger.info(f"[Voice] 已加载 openai-whisper ({model_size})")
            return pair
        except ImportError:
            raise RuntimeError(
                "请先安装 faster-whisper 或 openai-whisper：\n"
                "  pip install faster-whisper\n"
                "或 pip install openai-whisper"
            )


def _transcribe_impl(path: str, language: str, model_size: str) -> str:
    """实际转写逻辑：在子进程中执行（或调试模式进程内执行）。"""
    global _MODEL
    _ensure_av_compat()
    if _MODEL is None or _MODEL[0] != model_size:
        _MODEL = (model_size, _load_model(model_size))
    backend, model = _MODEL[1]
    if backend == "faster":
        segments, info = model.transcribe(path, language=language,
                                          beam_size=5, vad_filter=True)
        return "".join(s.text for s in segments).strip()
    else:
        result = model.transcribe(path, language=language, fp16=False)
        return (result.get("text") or "").strip()


def _redirect_logs_to_stderr():
    """worker 的 stdout 是协议通道，日志必须走 stderr。"""
    import logging

    for h in list(logger.handlers):
        if getattr(h, "stream", None) is sys.stdout:
            logger.removeHandler(h)
            logger.addHandler(logging.StreamHandler(sys.stderr))


def _worker_main():
    """子进程入口：循环读取 stdin 的 JSON 请求行，写回 JSON 响应行。

    注意必须走 buffer 二进制读写并显式 UTF-8：Windows 子进程的
    stdin/stdout 默认用本地编码（中文系统为 GBK），识别出的中文文本
    会被编码成 GBK，而主进程按 UTF-8 解码会直接 UnicodeDecodeError。
    """
    _redirect_logs_to_stderr()
    _ensure_av_compat()
    print("[Voice] 识别子进程就绪（与主进程原生库隔离）", file=sys.stderr,
          flush=True)
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line.decode("utf-8"))
            text = _transcribe_impl(req["path"], req.get("language", "zh"),
                                    req.get("model") or _MODEL_SIZE)
            resp = {"ok": True, "text": text}
        except Exception as e:  # noqa: BLE001
            resp = {"ok": False, "error_type": type(e).__name__,
                    "error": str(e)}
        stdout.write((json.dumps(resp, ensure_ascii=False) + "\n")
                     .encode("utf-8"))
        stdout.flush()


# ============================================================
# 主进程侧：子进程管理
# ============================================================
def _pump_stdout(proc: subprocess.Popen, q: "queue.Queue"):
    """把子进程 stdout 的行投递到队列；进程退出后投递 None 作为 EOF 标记。"""
    try:
        for raw in proc.stdout:
            q.put(raw)
    finally:
        q.put(None)


def _spawn_worker():
    global _PROC, _QUEUE, _READER
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--voice-worker"]
    else:
        cmd = [sys.executable, "-m", "framework.voice_input"]
    proc = subprocess.Popen(
        cmd, cwd=root,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        env=os.environ.copy(),   # 继承 HF_ENDPOINT / WHISPER_MODEL 等
        # stderr 不重定向：子进程日志与 tqdm 进度直接进主控制台
    )
    q = queue.Queue()
    reader = threading.Thread(target=_pump_stdout, args=(proc, q),
                              daemon=True, name="voice-worker-reader")
    reader.start()
    _PROC, _QUEUE, _READER = proc, q, reader
    logger.info("[Voice] 识别子进程已启动（与主进程原生库隔离）")


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
        except Exception:
            pass
        try:
            _PROC.stdin.close()
            _PROC.stdout.close()
        except Exception:
            pass
        try:
            _PROC.wait(timeout=10)
        except Exception:
            pass
    _PROC, _QUEUE, _READER = None, None, None


def transcribe_file(path: str, language: str = "zh") -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    if _INPROCESS:
        logger.warning("[Voice] VOICE_INPROCESS=1，进程内运行"
                       "（桌面环境下可能触发 0xC0000005 闪退，仅限调试）")
        return _transcribe_impl(path, language, _MODEL_SIZE)

    req = json.dumps({"path": path, "language": language,
                      "model": _MODEL_SIZE}, ensure_ascii=False)
    with _LOCK:
        last_err = None
        for attempt in (1, 2):
            proc, q = _get_worker()
            try:
                proc.stdin.write((req + "\n").encode("utf-8"))
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                _drop_worker()
                last_err = e
                logger.warning(f"[Voice] 识别子进程管道断开（第 {attempt} 次），"
                               "已重建，将重试")
                continue

            deadline = time.monotonic() + _RESP_TIMEOUT
            resp = None
            while resp is None:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    _drop_worker()
                    raise RuntimeError(
                        f"语音识别超时（>{_RESP_TIMEOUT:.0f}s，"
                        "可能仍在下载模型），请稍后重试"
                    )
                try:
                    raw = q.get(timeout=remain)
                except queue.Empty:
                    _drop_worker()
                    raise RuntimeError(
                        f"语音识别超时（>{_RESP_TIMEOUT:.0f}s，"
                        "可能仍在下载模型），请稍后重试"
                    )
                if raw is None:
                    # EOF：子进程已退出（原生崩溃等），重建后重试一次
                    _drop_worker()
                    last_err = RuntimeError("识别子进程异常退出")
                    logger.warning(
                        f"[Voice] 识别子进程异常退出（第 {attempt} 次），"
                        "已重建，将重试")
                    break
                try:
                    msg = json.loads(raw.decode("utf-8"))
                    if isinstance(msg, dict) and "ok" in msg:
                        resp = msg
                    else:
                        raise ValueError("不是协议行")
                except (ValueError, UnicodeDecodeError):
                    # 子进程有第三方库直接往 stdout 打印内容时，
                    # 跳过该行而不是让整次请求失败
                    logger.warning("[Voice] 忽略子进程 stdout 的非协议输出: "
                                   f"{raw[:120]!r}")
                    continue
            if resp is None:
                continue
            if resp.get("ok"):
                return resp.get("text") or ""
            raise RuntimeError(f"语音识别失败：{resp.get('error_type')}: "
                               f"{resp.get('error')}")
        raise RuntimeError(
            "语音识别子进程连续崩溃（主程序已隔离保护，其他功能不受影响），"
            "请重试或检查音频文件格式"
        ) from last_err


def transcribe_bytes(data: bytes, suffix: str = ".wav",
                     language: str = "zh") -> str:
    """把音频字节写入临时文件后转写。

    注意：Windows 中文用户名/中文路径会让 faster-whisper 底层
    触发 UnicodeDecodeError，因此强制把临时文件放到纯英文目录。
    """
    tmp_dir = os.path.join(tempfile.gettempdir(), "lingling_voice")
    os.makedirs(tmp_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=suffix, dir=tmp_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return transcribe_file(tmp, language)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


if __name__ == "__main__":   # python -m framework.voice_input
    _worker_main()