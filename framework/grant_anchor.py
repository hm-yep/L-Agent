# -*- coding: utf-8 -*-
"""授权锚点：让网页端与桌面端共用同一份授权记录的一条通道。

## 为什么需要它

`main.py --api` 会在**同一个进程**的后台线程里跑 uvicorn，但 README 与 Docker
镜像也支持单独跑 `uvicorn api.server:app`——那就是一个**独立进程**，里面没有
Qt、没有桌宠、也就没有 `Framework`。两边的授权记录如果各存一份，就会出现
「网页端点了允许，桌宠还要再问一遍」这种分治。

所以定一条规矩：**桌面进程是锚点（anchor）**，它是唯一保管授权记录的地方。

  * 桌宠进程起一个只监听 127.0.0.1 的小 HTTP 服务（stdlib，无新依赖），
    把端口与一次性令牌写进本机的发现文件；
  * 网页端（不论同进程还是独立进程）先找锚点：找到就把「这条路径要不要授权、
    用户答了什么」全部交给锚点，答完授权落在桌面那份记录里；
  * 找不到锚点（桌宠没开）时网页端用**同一套**判定与状态机自己兜底
    （`framework.grant_service.GrantService`），此时授权只在网页端生效。

判定逻辑与状态机只有一份实现，进程边界只影响「授权记录归谁保管」——
这就是「不分治」的真实含义，见 ADR 0007。

## 安全口径

  * 只绑 127.0.0.1，不对外暴露；
  * 每个请求都要带本次运行的一次性令牌（`X-Grant-Token`），令牌比对用
    `secrets.compare_digest`；
  * 发现文件里**只有端口与令牌，没有任何路径**——授权记录仍然只在内存里，
    重启必然回到默认拒绝；
  * 令牌每次启动重新生成，旧发现文件自动失效。

本模块只依赖标准库，不 import Qt / requests：网页端与桌面端都能用。
"""

import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from framework.grant_service import DECISIONS

ANCHOR_FILE = "grant_anchor.json"
TOKEN_HEADER = "X-Grant-Token"
_MAX_BODY = 64 * 1024


def discovery_path() -> str:
    """发现文件的位置：本机应用数据目录（拿不到就退回临时目录）。"""
    try:
        from framework.utils import app_data_dir
        base = app_data_dir()
    except Exception:  # noqa: BLE001 拿不到就用临时目录，不能因此起不来
        import tempfile
        base = Path(tempfile.gettempdir())
    try:
        Path(base).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return str(Path(base) / ANCHOR_FILE)


def _write_discovery(path: str, payload: dict) -> bool:
    try:
        Path(path).write_text(json.dumps(payload, ensure_ascii=False),
                               encoding="utf-8")
        return True
    except OSError:
        return False


def _read_discovery(path: str) -> dict:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _remove_discovery(path: str) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass


class _Handler(BaseHTTPRequestHandler):
    """锚点的小 HTTP 服务：只认四个接口，且每个都要令牌。"""

    server_version = "LingLingGrant/1.0"

    # 安静一点，别把令牌和路径打进日志
    def log_message(self, fmt, *args):  # noqa: D102
        pass

    # ---- 工具 ----
    def _authorized(self) -> bool:
        token = getattr(self.server, "grant_token", "")
        supplied = self.headers.get(TOKEN_HEADER, "")
        if not token or not supplied:
            return False
        return secrets.compare_digest(str(supplied), str(token))

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return {}
        if length <= 0 or length > _MAX_BODY:
            return {}
        try:
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, UnicodeDecodeError):
            return {}

    # ---- 路由 ----
    def do_GET(self):  # noqa: N802
        if not self._authorized():
            self._send(401, {"ok": False, "error": "令牌无效"})
            return
        path = urlparse(self.path).path
        if path == "/grant/state":
            self._send(200, {"ok": True,
                             **(self.server.grant_state() or {})})
            return
        self._send(404, {"ok": False, "error": "没有这个接口"})

    def do_POST(self):  # noqa: N802
        if not self._authorized():
            self._send(401, {"ok": False, "error": "令牌无效"})
            return
        path = urlparse(self.path).path
        body = self._read_json()

        if path == "/grant/ask":
            target = str(body.get("path") or "")
            source = str(body.get("source") or "web")
            if not target:
                self._send(400, {"ok": False, "error": "path 不能为空"})
                return
            try:
                result = self.server.grant_ask(target, source)
            except Exception as e:      # noqa: BLE001 锚点不能因一次异常挂掉
                self._send(500, {"ok": False, "error": "锚点内部错误: %s"
                                 % type(e).__name__})
                return
            self._send(200, result)
            return

        if path == "/grant/answer":
            gid = str(body.get("gid") or "")
            decision = str(body.get("decision") or "")
            if decision not in DECISIONS:
                self._send(400, {"ok": False, "error": "decision 非法"})
                return
            try:
                result = self.server.grant_answer(gid, decision)
            except Exception as e:      # noqa: BLE001
                self._send(500, {"ok": False, "error": "锚点内部错误: %s"
                                 % type(e).__name__})
                return
            self._send(200, result)
            return

        if path == "/grant/revoke":
            try:
                cleared = self.server.grant_revoke()
            except Exception as e:      # noqa: BLE001
                self._send(500, {"ok": False, "error": "锚点内部错误: %s"
                                 % type(e).__name__})
                return
            self._send(200, {"ok": True, "cleared": int(cleared or 0)})
            return

        self._send(404, {"ok": False, "error": "没有这个接口"})


class GrantAnchor:
    """桌面进程里的授权锚点（一个只监听回环的小 HTTP 服务）。

    宿主需要提供三个回调：`ask` / `answer` / `state`（外加可选的 `revoke`），
    由 `Framework` 实现——这样本模块不必 import Qt。
    """

    def __init__(self, ask, answer, state, revoke=None, *,
                 host: str = "127.0.0.1", port: int = 0,
                 token: str = None, discovery: str = None):
        self._ask = ask
        self._answer = answer
        self._state = state
        self._revoke = revoke
        self.host = host
        self.port = int(port)
        self.token = token or secrets.token_urlsafe(32)
        self.discovery = discovery or discovery_path()
        self._server = None
        self._thread = None

    # ---------------- 生命周期 ----------------
    def start(self) -> bool:
        """起服务并写发现文件。失败只记日志，不影响桌面主链路。"""
        if self._server is not None:
            return True
        try:
            server = ThreadingHTTPServer((self.host, self.port), _Handler)
        except OSError:
            return False
        server.grant_token = self.token
        server.grant_ask = self._ask
        server.grant_answer = self._answer
        server.grant_state = self._state
        server.grant_revoke = self._revoke or (lambda: 0)
        server.daemon_threads = True
        self._server = server
        self.port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever,
                                  name="grant-anchor", daemon=True)
        thread.start()
        self._thread = thread
        _write_discovery(self.discovery, {
            "host": self.host,
            "port": self.port,
            "token": self.token,
            "pid": os.getpid(),
            "started_at": int(time.time()),
        })
        return True

    def stop(self):
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:  # noqa: BLE001 收尾失败不影响退出
                pass
            self._server = None
        _remove_discovery(self.discovery)

    @property
    def running(self) -> bool:
        return self._server is not None

    def url(self, path: str) -> str:
        return "http://%s:%d%s" % (self.host, self.port, path)


class GrantAnchorClient:
    """网页端用的锚点客户端：找锚点、要授权、回答案。

    找不到锚点时每个方法都返回 None —— 网页端据此切到自持模式，
    而不是伪造一个「已授权」。
    """

    def __init__(self, discovery: str = None, timeout: float = 5.0):
        self.discovery = discovery or discovery_path()
        self.timeout = float(timeout)

    # ---------------- 底层 ----------------
    def discover(self) -> dict:
        data = _read_discovery(self.discovery)
        if not data:
            return {}
        if not data.get("port") or not data.get("token"):
            return {}
        return data

    def _call(self, path: str, payload: dict = None, method: str = "POST") -> dict:
        import urllib.error
        import urllib.request

        info = self.discover()
        if not info:
            return {}
        url = "http://%s:%d%s" % (info.get("host", "127.0.0.1"),
                                  int(info.get("port") or 0), path)
        body = json.dumps(payload or {}).encode("utf-8") if method == "POST" \
            else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header(TOKEN_HEADER, str(info.get("token") or ""))
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (urllib.error.URLError, OSError, ValueError):
            # 桌宠没开、端口变了、令牌过期：都归为「没有锚点」
            return {}

    # ---------------- 对外的四件事 ----------------
    def ask(self, path: str, source: str = "web") -> dict:
        """问锚点这条路径要不要授权。

        :return: {"verdict": "allow|grant|deny", "gid": ..., ...}；
                 没有锚点时返回 {}（调用方切自持模式）。
        """
        return self._call("/grant/ask", {"path": path, "source": source})

    def answer(self, gid: str, decision: str) -> dict:
        return self._call("/grant/answer", {"gid": gid, "decision": decision})

    def state(self) -> dict:
        return self._call("/grant/state", method="GET")

    def revoke(self) -> dict:
        return self._call("/grant/revoke", {})
