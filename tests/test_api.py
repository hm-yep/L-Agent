# -*- coding: utf-8 -*-
"""API 接口测试（不真调 LLM 和网络）"""

import pytest
from fastapi.testclient import TestClient

from api import server as srv


@pytest.fixture
def client():
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
    # server 通过 framework.rag_client 的模块级函数调用 RAG 子进程，
    # 这里替换模块属性即可，不需要真的拉起子进程
    monkeypatch.setattr(srv.rag_client, "add_text",
                        lambda text, source="manual": 3)
    monkeypatch.setattr(srv.rag_client, "count", lambda: 3)
    r = client.post("/ingest/text", json={"text": "测试内容", "source": "t"})
    assert r.status_code == 200
    assert r.json()["chunks"] == 3
    assert r.json()["total"] == 3


def test_search_endpoint(client, monkeypatch):
    monkeypatch.setattr(srv, "web_search",
                        lambda q, count=5: [{"title": "T", "link": "L",
                                             "content": "C", "media": "M"}])
    r = client.post("/search", json={"query": "测试", "count": 1})
    assert r.status_code == 200
    assert len(r.json()["results"]) == 1