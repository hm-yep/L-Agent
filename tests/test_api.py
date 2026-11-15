# -*- coding: utf-8 -*-
"""API 接口测试（不真调 LLM 和网络）"""

import pytest
from fastapi.testclient import TestClient

from api import server as srv


@pytest.fixture
def client(monkeypatch):
    # 令牌来自环境变量，测试环境不该被外部配置影响：统一按"未配置令牌、
    # 仅本机回环"的口径跑（TestClient 的 host 是 testclient，视为本机）。
    monkeypatch.setattr(srv.config, "API_AUTH_TOKEN", "")
    return TestClient(srv.app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_index(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "凌零" in r.text


def test_chat_mock(client, monkeypatch):
    monkeypatch.setattr(srv, "agent_chat",
                        lambda *a, **kw: ("你好，我是凌零", []))
    r = client.post("/chat", json={"message": "你好", "use_rag": False,
                                   "use_search": False})
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "你好，我是凌零"
    assert "session_id" in body


def test_ingest_text(client, monkeypatch):
    # RAG 是可选依赖：CI 的干净环境没装 langchain_chroma 时路由会返回 503。
    # 这里显式把"依赖可用"打开，测的仍是真实的路由/参数处理逻辑。
    monkeypatch.setattr(srv, "_RAG_AVAILABLE", True)
    # server 通过 framework.rag_client 的模块级函数调用 RAG 子进程，
    # 这里替换模块属性即可，不需要真的拉起子进程
    monkeypatch.setattr(srv.rag_client, "add_text",
                        lambda text, source="manual": 3)
    monkeypatch.setattr(srv.rag_client, "count", lambda: 3)
    r = client.post("/ingest/text", json={"text": "测试内容", "source": "t"})
    assert r.status_code == 200
    assert r.json()["chunks"] == 3
    assert r.json()["total"] == 3


def test_ingest_file_path_traversal_contained(client, monkeypatch):
    # 恶意文件名（../../evil.txt）必须被剥离路径，落点始终在 data/uploads 内，
    # 绝不能写到 uploads 目录之外。这是对 /ingest/file 路径穿越的回归防线。
    from pathlib import Path
    captured = {}

    def fake_add_file(path):
        captured["path"] = path
        return 1

    monkeypatch.setattr(srv, "_RAG_AVAILABLE", True)
    monkeypatch.setattr(srv.rag_client, "add_file", fake_add_file)
    monkeypatch.setattr(srv.rag_client, "count", lambda: 1)

    files = {"file": ("../../evil.txt", b"payload", "text/plain")}
    r = client.post("/ingest/file", files=files)
    assert r.status_code == 200

    dst = Path(captured["path"]).resolve()
    uploads = Path("./data/uploads").resolve()
    assert dst.parent == uploads, f"落点越界：{dst}"
    assert dst.name == "evil.txt"
    dst.unlink(missing_ok=True)  # 清理测试产物


def test_search_endpoint(client, monkeypatch):
    monkeypatch.setattr(srv, "web_search",
                        lambda q, count=5: [{"title": "T", "link": "L",
                                             "content": "C", "media": "M"}])
    r = client.post("/search", json={"query": "测试", "count": 1})
    assert r.status_code == 200
    assert len(r.json()["results"]) == 1