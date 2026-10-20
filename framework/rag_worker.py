# -*- coding: utf-8 -*-
"""RAG 子进程：独立加载 LangChain + Chroma + BGE

为什么必须独立子进程：
    主进程加载了 PyQt5 / OpenCV / PyTorch 等原生库。实测 PyQt5 与
    HuggingFaceEmbeddings 底层的 PyTorch 在同一进程共存时，加载完
    嵌入模型就会触发访问冲突（Windows 退出码 0xC0000005），Python
    层异常钩子捕获不到，整个程序无声闪退。

    因此 RAG 跑在专用子进程里：
      - 子进程只加载 langchain / chroma / torch，不含 Qt/cv2/mediapipe
      - 父进程与子进程之间用 stdin/stdout 的 JSON 行协议通信
      - 子进程崩溃只影响当次 RAG 请求（自动重建），主程序不崩

子进程入口：python -m framework.rag_worker
"""

import json
import sys

from framework.utils import logger


def _redirect_logs_to_stderr():
    """worker 的 stdout 是协议通道，日志必须走 stderr。"""
    import logging
    for h in list(logger.handlers):
        if getattr(h, "stream", None) is sys.stdout:
            logger.removeHandler(h)
            logger.addHandler(logging.StreamHandler(sys.stderr))


def _handle(rag, req: dict) -> dict:
    op = req.get("op")
    if op == "ping":
        return {"ok": True, "pong": True}
    if op == "count":
        return {"ok": True, "count": rag.count()}
    if op == "add_text":
        n = rag.add_text(req["text"], source=req.get("source", "manual"))
        return {"ok": True, "chunks": n, "total": rag.count()}
    if op == "add_file":
        n = rag.add_file(req["path"])
        return {"ok": True, "chunks": n, "total": rag.count()}
    if op == "search":
        hits = rag.search(req["query"], top_k=int(req.get("top_k", 3)))
        return {"ok": True, "hits": hits}
    if op == "format_context":
        text = rag.format_context(req.get("hits") or [],
                                  max_chars=int(req.get("max_chars", 2000)))
        return {"ok": True, "text": text}
    if op == "reset":
        rag.reset()
        return {"ok": True}
    return {"ok": False, "error": f"未知操作: {op}"}


def _worker_main():
    _redirect_logs_to_stderr()
    print("[RAG] 子进程就绪（与主进程原生库隔离）",
          file=sys.stderr, flush=True)

    # 延迟加载：第一次请求才加载模型（首次会下载 BGE）
    from framework.rag_engine import RagEngine
    rag = RagEngine()

    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line.decode("utf-8"))
            resp = _handle(rag, req)
        except Exception as e:  # noqa: BLE001
            resp = {"ok": False, "error_type": type(e).__name__,
                    "error": str(e)}
        stdout.write((json.dumps(resp, ensure_ascii=False) + "\n")
                     .encode("utf-8"))
        stdout.flush()


if __name__ == "__main__":   # python -m framework.rag_worker
    _worker_main()