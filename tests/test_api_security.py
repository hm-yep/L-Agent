# -*- coding: utf-8 -*-
"""单元测试：api/server.py 的安全层（鉴权 / 输入校验 / 错误脱敏 / 会话治理 / 路由）

覆盖：X-API-Key 与 Bearer 鉴权、无令牌时外网来源 403、文本/数值/session_id/
index/language 校验、500 不回显异常原文、会话过期与数量上限、任务台与会话
路由的隔离读写、/ingest/text 的 RAG 可用性分支、/agent/run 假编排器、
/voice 假语音模块与空/超大/非法语言、以及 CORS 白名单不再通配。

为什么不需要联网：所有会触网的入口都被替换成桩——agent_chat 抛异常或返回
固定值、rag_client.add_text/count 用桩、multi_agent.get_orchestrator 返回假
编排器、framework.voice_input 用 types.ModuleType 注入，联网搜索接口一律不
调用。TestClient 走 ASGI 内存传输，不发真实 HTTP 请求、不需要 API Key。
任务台/记忆被换成隔离到 tmp_path 的实例，绝不写脏 data/ 下的真实用户数据。
"""

import sys
import time
import types

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from api import server as srv
from framework import config


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """把共享的 MemorySystem / TaskBoard 隔离到 tmp_path，并清空会话表。

    server 在 import 时就构造了指向真实 data/ 的实例；测试里凡是要写这些
    对象的路由（POST /tasks/todos、/agent/run、/chat、/voice/chat），一律
    换成落在 tmp_path 的实例，绝不写脏用户的 memory.json / tasks.json。
    """
    import framework.memory_system as mem_mod
    import framework.task_board as tb_mod

    monkeypatch.setattr(tb_mod, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(mem_mod, "app_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(srv, "_tasks", tb_mod.TaskBoard())
    monkeypatch.setattr(srv, "_memory", mem_mod.MemorySystem())
    monkeypatch.setattr(srv, "_sessions", {})
    # 令牌基线：默认无令牌，TestClient 的 host 是 testclient（回环）故放行；
    # 鉴权测试自己在用例里再 set 成有令牌。
    monkeypatch.setattr(srv.config, "API_AUTH_TOKEN", "")


@pytest.fixture
def client():
    return TestClient(srv.app)


@pytest.fixture
def fake_voice(monkeypatch):
    """往 sys.modules 注入假的 framework.voice_input，提供 transcribe_bytes。"""
    import framework

    seen = {}
    fake = types.ModuleType("framework.voice_input")

    def transcribe(data, suffix=".wav", language="zh"):
        seen.update(data=data, suffix=suffix, language=language)
        return "识别出来的文字"

    fake.transcribe_bytes = transcribe
    monkeypatch.setitem(sys.modules, "framework.voice_input", fake)
    # 包对象上也指向假模块，避免 from-import 拿到真实模块
    monkeypatch.setattr(framework, "voice_input", fake, raising=False)
    # /voice 现在用 find_spec 真探测可选依赖；测试注入的是假模块，
    # 这里显式把「依赖可用」打开，让用例仍测得到转写链路本身。
    monkeypatch.setattr(srv, "_VOICE_AVAILABLE", True)
    return seen


# ---------------- 鉴权 ----------------
def test_no_token_without_header_is_401(client, monkeypatch):
    monkeypatch.setattr(srv.config, "API_AUTH_TOKEN", "unit-test-token")
    r = client.get("/health")
    assert r.status_code == 401
    assert r.json()["detail"] == "无效的访问令牌"


def test_x_api_key_header_is_accepted(client, monkeypatch):
    monkeypatch.setattr(srv.config, "API_AUTH_TOKEN", "unit-test-token")
    r = client.get("/health", headers={"X-API-Key": "unit-test-token"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["auth_required"] is True


def test_bearer_header_is_accepted(client, monkeypatch):
    monkeypatch.setattr(srv.config, "API_AUTH_TOKEN", "unit-test-token")
    r = client.get("/health",
                   headers={"Authorization": "Bearer unit-test-token"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_wrong_token_is_401(client, monkeypatch):
    monkeypatch.setattr(srv.config, "API_AUTH_TOKEN", "unit-test-token")
    r = client.get("/health", headers={"X-API-Key": "not-the-token"})
    assert r.status_code == 401


def test_no_token_foreign_host_is_403(monkeypatch):
    """没令牌时只认回环：直接单测 require_access，把 client.host 伪装成外网。"""
    monkeypatch.setattr(srv.config, "API_AUTH_TOKEN", "")
    req = Request({"type": "http",
                   "client": ("203.0.113.9", 54321), "headers": []})
    with pytest.raises(HTTPException) as ei:
        srv.require_access(req, x_api_key=None, authorization=None)
    assert ei.value.status_code == 403


def test_no_token_loopback_is_allowed(client):
    """TestClient 的 host 是 testclient，属于回环白名单，无令牌也能访问。"""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["auth_required"] is False


# ---------------- 输入校验 ----------------
def test_message_over_limit_is_422(client):
    r = client.post("/chat", json={
        "message": "x" * (config.API_MAX_MESSAGE_CHARS + 1)})
    assert r.status_code == 422


def test_top_k_over_max_is_422(client):
    r = client.post("/chat", json={"message": "你好", "top_k": 999})
    assert r.status_code == 422


def test_count_zero_is_422(client):
    r = client.post("/search", json={"query": "测试", "count": 0})
    assert r.status_code == 422


def test_illegal_session_id_is_400(client):
    r = client.post("/chat", json={"message": "你好",
                                   "session_id": "../etc/passwd"})
    assert r.status_code == 400


def test_todo_index_zero_is_400(client):
    r = client.delete("/tasks/todos/0")
    assert r.status_code == 400


def test_illegal_language_is_400(client):
    r = client.post("/voice",
                    files={"file": ("a.wav", b"123", "audio/wav")},
                    data={"language": "zh-CN"})
    assert r.status_code == 400


# ---------------- 错误不回显 ----------------
def test_chat_500_never_echoes_exception_text(client, monkeypatch):
    secret = "SENSITIVE_C:\\Users\\secret"

    def boom(*a, **k):
        raise RuntimeError(secret)

    monkeypatch.setattr(srv, "agent_chat", boom)
    r = client.post("/chat", json={"message": "你好"})
    assert r.status_code == 500
    assert "SENSITIVE" not in r.text
    assert "secret" not in r.text
    assert "Traceback" not in r.text
    assert r.json()["detail"] == srv._GENERIC_500


# ---------------- 会话治理 ----------------
def test_prune_sessions_drops_stale_but_keeps_fresh():
    now = time.time()
    srv._sessions.clear()
    srv._sessions["stale"] = (
        0, now - srv.config.API_SESSION_TTL_SEC - 60)
    srv._sessions["fresh"] = (0, now)
    srv._prune_sessions()
    assert "stale" not in srv._sessions
    assert "fresh" in srv._sessions


def test_prune_sessions_caps_at_max_sessions():
    now = time.time()
    srv._sessions.clear()
    n = srv.config.API_MAX_SESSIONS + 25
    for i in range(n):
        srv._sessions[f"s{i}"] = (0, now - i)
    assert len(srv._sessions) == n
    srv._prune_sessions()
    assert len(srv._sessions) == srv.config.API_MAX_SESSIONS


# ---------------- 路由覆盖（数据隔离到 tmp_path）----------------
def test_task_routes_are_isolated_and_return_200(client, tmp_path):
    r = client.get("/tasks/todos")
    assert r.status_code == 200
    assert r.json() == {"todos": [], "total": 0}

    r = client.post("/tasks/todos", json={"text": "写单元测试"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["total"] == 1

    r = client.get("/tasks/todos")
    assert r.json()["todos"] == ["写单元测试"]

    r = client.delete("/tasks/todos/1")
    assert r.status_code == 200
    assert r.json()["removed"] == "写单元测试"

    assert client.get("/tasks/todos").json()["total"] == 0
    # 真断言：落盘发生在 tmp_path，而不是真实 data/
    assert (tmp_path / "tasks.json").exists()
    assert (tmp_path / "memory.json").exists()


def test_reminders_and_session_routes_return_200(client):
    r = client.get("/tasks/reminders")
    assert r.status_code == 200
    assert r.json() == {"reminders": [], "total": 0}

    sid = "unit_session_1"
    r = client.get(f"/sessions/{sid}")
    assert r.status_code == 200
    assert r.json()["session_id"] == sid
    assert r.json()["history"] == []
    assert sid in srv._sessions

    r = client.delete(f"/sessions/{sid}")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert sid not in srv._sessions


# ---------------- /ingest/text 的 RAG 可用性分支 ----------------
def test_ingest_text_returns_503_when_rag_unavailable(client, monkeypatch):
    monkeypatch.setattr(srv, "_RAG_AVAILABLE", False)
    r = client.post("/ingest/text", json={"text": "知识片段"})
    assert r.status_code == 503


def test_ingest_text_returns_200_with_stubbed_rag(client, monkeypatch):
    monkeypatch.setattr(srv, "_RAG_AVAILABLE", True)
    calls = {}

    def fake_add_text(text, source="manual"):
        calls["text"] = text
        calls["source"] = source
        return 3

    monkeypatch.setattr(srv.rag_client, "add_text", fake_add_text)
    monkeypatch.setattr(srv.rag_client, "count", lambda: 7)
    r = client.post("/ingest/text",
                    json={"text": "知识片段", "source": "unit"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "chunks": 3, "total": 7}
    assert calls["text"] == "知识片段"
    assert calls["source"] == "unit"


# ---------------- /agent/run：假编排器，绝不真调 LLM ----------------
def test_agent_run_uses_fake_orchestrator(client, monkeypatch):
    import framework.multi_agent as ma

    class _FakeOrchestrator:
        def __init__(self):
            self.tasks = []

        def run(self, task):
            self.tasks.append(task)
            return {"task": task, "steps": ["调研", "成文"],
                    "results": [{"step": "调研", "ok": True}], "calls": 2}

    fake = _FakeOrchestrator()
    monkeypatch.setattr(ma, "get_orchestrator", lambda: fake)
    r = client.post("/agent/run",
                    json={"task": "写一份周报", "session_id": "agent_unit"})
    assert r.status_code == 200
    body = r.json()
    assert body["task"] == "写一份周报"
    assert body["session_id"] == "agent_unit"
    assert body["steps"] == ["调研", "成文"]
    assert body["results"] == [{"step": "调研", "ok": True}]
    assert body["calls"] == 2
    assert isinstance(body["elapsed"], (int, float))
    assert fake.tasks == ["写一份周报"]      # 确实只调了假编排器


# ---------------- /voice ----------------
def test_voice_returns_200_with_fake_module(client, fake_voice):
    r = client.post("/voice",
                    files={"file": ("voice.wav", b"RIFFDATA", "audio/wav")})
    assert r.status_code == 200
    assert r.json()["text"] == "识别出来的文字"
    assert fake_voice["data"] == b"RIFFDATA"
    assert fake_voice["suffix"] == ".wav"
    assert fake_voice["language"] == "zh"


def test_voice_empty_file_is_400(client, fake_voice):
    r = client.post("/voice",
                    files={"file": ("a.wav", b"", "audio/wav")})
    assert r.status_code == 400


def test_voice_oversize_is_413(client, fake_voice, monkeypatch):
    monkeypatch.setattr(srv.config, "API_MAX_UPLOAD_BYTES", 4)
    r = client.post("/voice",
                    files={"file": ("a.wav", b"0123456789", "audio/wav")})
    assert r.status_code == 413


# ---------------- CORS ----------------
def test_cors_is_not_wildcard():
    cors = [m for m in srv.app.user_middleware
            if m.cls.__name__ == "CORSMiddleware"]
    assert len(cors) == 1
    opts = getattr(cors[0], "kwargs", None) or getattr(cors[0], "options", {})
    origins = opts.get("allow_origins", [])
    assert origins
    assert "*" not in origins
    assert "*" not in opts.get("allow_methods", [])
    assert "*" not in opts.get("allow_headers", [])


# ---------------- 前端可达性与可选能力的诚实回显 ----------------
def test_index_is_reachable_with_token_but_api_still_protected(client, monkeypatch):
    """静态首页免令牌：否则 Docker 场景下页首的令牌输入框永远不可达。"""
    monkeypatch.setattr(srv.config, "API_AUTH_TOKEN", "unit-test-token")
    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 401
    assert client.get("/health",
                      headers={"X-API-Key": "unit-test-token"}).status_code == 200


def test_health_reports_optional_capabilities(client):
    body = client.get("/health").json()
    for key in ("rag_available", "voice_available", "llm_key_configured",
                "search_key_configured", "auth_required"):
        assert isinstance(body[key], bool), key


def test_voice_returns_503_when_dependency_missing(client, monkeypatch):
    monkeypatch.setattr(srv, "_VOICE_AVAILABLE", False)
    r = client.post("/voice",
                    files={"file": ("a.wav", b"RIFFDATA", "audio/wav")})
    assert r.status_code == 503
    assert "requirements-voice.txt" in r.json()["detail"]


def test_voice_chat_returns_503_when_dependency_missing(client, monkeypatch):
    monkeypatch.setattr(srv, "_VOICE_AVAILABLE", False)
    r = client.post("/voice/chat",
                    files={"file": ("a.wav", b"RIFFDATA", "audio/wav")})
    assert r.status_code == 503


def test_voice_chat_works_with_fake_module(client, fake_voice, monkeypatch):
    """前端「按住说话」走的就是这个端点，之前零测试覆盖。"""
    monkeypatch.setattr(srv, "agent_chat", lambda *a, **kw: ("收到", []))
    r = client.post("/voice/chat",
                    files={"file": ("voice.wav", b"RIFFDATA", "audio/wav")})
    assert r.status_code == 200
    assert "识别出来的文字" in r.json()["reply"]
    assert fake_voice["data"] == b"RIFFDATA"
    assert fake_voice["language"] == "zh"


def test_agent_run_explains_missing_key(client, monkeypatch):
    """无 Key 时给可诊断文案，而不是「编排失败，详见服务端日志」。"""
    import framework.multi_agent as ma

    class Boom:
        def run(self, task):
            raise RuntimeError("no key")

    monkeypatch.setattr(ma, "get_orchestrator", lambda: Boom())
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    r = client.post("/agent/run", json={"task": "写周报"})
    assert r.status_code == 500
    assert "DEEPSEEK_API_KEY" in r.json()["detail"]


def test_web_debug_console_gates_controls_on_health():
    """网页调试台按 /health 的真实能力置灰控件，不摆点了必失败的按钮。"""
    from pathlib import Path
    html = Path("api/static/index.html").read_text(encoding="utf-8")
    for token in ("loadCapabilities", "llm_key_configured", "voice_available",
                  "applyCaps", "newSessionBtn", "isComposing"):
        assert token in html, token
