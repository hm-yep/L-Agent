# -*- coding: utf-8 -*-
"""网页端授权通道验收：跨端共用一次授权，档位不越界，重启失效，超时按拒。

关键口径：浏览器里点「允许整个文件夹」之后，切回桌宠再说同目录另一个
文件，必须不再弹卡。这条不过，就不算「不分治」。
"""

import time

import pytest
from fastapi.testclient import TestClient

import api.server as srv
from framework import path_guard
from framework.grant_anchor import GrantAnchor, GrantAnchorClient
from framework.grant_service import (
    DECISION_DENY, DECISION_FILE, DECISION_TREE, GrantService,
)


@pytest.fixture
def two_ends(tmp_path, monkeypatch):
    """桌面锚点 + 网页客户端，发现文件隔离到 tmp，白名单清空以逼出 grant。"""
    desktop = GrantService()
    web = GrantService()
    monkeypatch.setattr(srv, "_web_grants", web)
    monkeypatch.setattr(srv.config, "API_AUTH_TOKEN", "")
    monkeypatch.setattr(path_guard, "allowed_roots", lambda: [])

    def desk_ask(path, source="web"):
        verdict, real, reason = desktop.check(path)
        if verdict != "grant":
            return {"verdict": verdict, "path": real or "", "gid": "",
                    "reason": reason}
        info = desktop.request(real, source=source, reason=reason)
        return {"verdict": "grant", "gid": info["gid"], "path": real,
                "name": info.get("name", ""), "dir": info.get("dir", ""),
                "reason": info.get("reason", ""),
                "expires_in": info.get("expires_in", 0)}

    discovery = str(tmp_path / "grant_anchor.json")
    anchor = GrantAnchor(ask=desk_ask, answer=desktop.answer,
                         state=lambda: {"grants": desktop.list_grants(),
                                        "stats": desktop.stats()},
                         revoke=desktop.revoke_all, discovery=discovery)
    assert anchor.start()
    monkeypatch.setattr(srv, "_grant_client", GrantAnchorClient(discovery=discovery))
    yield desktop, web, tmp_path
    anchor.stop()


def _ask_web(api, path, message="读一下这份文件"):
    resp = api.post("/chat", json={"message": message, "read_path": str(path)})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _answer(api, gid, decision):
    resp = api.post("/grant/" + gid, json={"decision": decision})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_file_grant_does_not_leak_to_sibling_in_same_dir(two_ends):
    """第 3 条安全性断言：file 档只放开这一份，同目录另一份仍要问。"""
    desktop, _web, tmp = two_ends
    folder = tmp / "资料"
    folder.mkdir()
    a, b = folder / "甲.txt", folder / "乙.txt"
    a.write_text("甲的内容", encoding="utf-8")
    b.write_text("乙的内容", encoding="utf-8")
    api = TestClient(srv.app)

    pending = _ask_web(api, a)["pending_grant"]
    assert pending and pending["verdict"] == "grant" and pending["gid"]
    _answer(api, pending["gid"], DECISION_FILE)

    assert desktop.check(str(a))[0] == "allow"
    assert desktop.check(str(b))[0] == "grant", "file 档溢出到了同目录的另一个文件"


def test_web_tree_grant_reaches_desktop_anchor(two_ends):
    """杀手级验收：浏览器点整个文件夹 → 桌面说同目录另一份不再弹。"""
    desktop, _web, tmp = two_ends
    folder = tmp / "项目资料"
    folder.mkdir()
    a, b = folder / "a.txt", folder / "b.txt"
    a.write_text("甲", encoding="utf-8")
    b.write_text("乙", encoding="utf-8")
    other = tmp / "别的目录"
    other.mkdir()
    c = other / "c.txt"
    c.write_text("丙", encoding="utf-8")
    api = TestClient(srv.app)

    pending = _ask_web(api, a)["pending_grant"]
    assert pending and pending["gid"]
    res = _answer(api, pending["gid"], DECISION_TREE)
    assert "文件夹" in res["message"] and "本次会话" in res["message"]

    assert desktop.check(str(b))[0] == "allow", "网页端授权的 tree 档没落到桌面"
    assert desktop.check(str(c))[0] == "grant", "tree 档溢出到了别的目录"

    # 桌面侧再问同一路径：也不会重新弹（授权记录只有一份）
    assert desktop.check(str(a))[0] == "allow"


def test_grants_die_with_the_process(two_ends):
    """第 7 条安全性断言：重启后还要再问，授权不能落盘。"""
    desktop, _web, tmp = two_ends
    target = tmp / "一次性.txt"
    target.write_text("内容", encoding="utf-8")
    api = TestClient(srv.app)

    pending = _ask_web(api, target)["pending_grant"]
    _answer(api, pending["gid"], DECISION_TREE)
    assert desktop.check(str(target))[0] == "allow"

    fresh = GrantService()          # 新进程 = 新 GrantStore
    assert fresh.check(str(target))[0] == "grant", "重启后不应还记得这次授权"


def test_deny_never_becomes_allow(two_ends):
    """第 8 条：拒绝就是终态，不能因为同目录被放过就跟着放行。"""
    desktop, _web, tmp = two_ends
    folder = tmp / "混合"
    folder.mkdir()
    a = folder / "ok.txt"
    a.write_text("可以读", encoding="utf-8")
    api = TestClient(srv.app)

    pending = _ask_web(api, a)["pending_grant"]
    _answer(api, pending["gid"], DECISION_DENY)
    assert desktop.check(str(a))[0] == "grant", "拒绝后应回到未授权，可再问一次"


def test_timeout_is_deny_and_said_in_next_response(two_ends):
    """120 秒（测试里改为过期）自动拒绝，并在下一次响应里明说。"""
    desktop, web, tmp = two_ends
    target = tmp / "超时.txt"
    target.write_text("内容", encoding="utf-8")

    info = web.request(str(target), source="web")
    grant = web.get(info["gid"])
    grant.deadline = time.time() - 1

    # 超时后下一次响应必须明说「按拒绝处理」；note 是消费型，只能在这里验。
    resp = TestClient(srv.app).post(
        "/chat", json={"message": "你好", "read_path": str(target)})
    assert resp.status_code == 200
    assert "按拒绝处理" in resp.json().get("grant_note", "")
    assert grant.decision == DECISION_DENY and grant.timed_out


def test_grant_route_requires_token_and_valid_decision(monkeypatch):
    """/grant 不许匿名可达：配令牌必须带令牌，decision 只能三选一。"""
    monkeypatch.setattr(srv.config, "API_AUTH_TOKEN", "unit-test-token")
    monkeypatch.setattr(srv, "_web_grants", GrantService())
    monkeypatch.setattr(srv, "_grant_client", GrantAnchorClient(
        discovery=str(time.time()) + "-not-exist.json"))
    api = TestClient(srv.app)

    assert api.post("/grant/x", json={"decision": "tree"}).status_code == 401
    assert api.post("/grant/x", json={"decision": "tree"},
                    headers={"X-API-Key": "wrong"}).status_code == 401
    assert api.post("/grant/x", json={"decision": "everything"},
                    headers={"X-API-Key": "unit-test-token"}).status_code == 400
    # 令牌对了、decision 合法但 gid 不存在：业务 400，不是鉴权失败
    assert api.post("/grant/x", json={"decision": "tree"},
                    headers={"X-API-Key": "unit-test-token"}).status_code == 400


def test_agent_run_also_returns_pending_grant(two_ends):
    """/agent/run 与 /chat 共用同一通道，不另起一套授权。"""
    _desktop, _web, tmp = two_ends
    target = tmp / "给多智能体.txt"
    target.write_text("内容", encoding="utf-8")
    api = TestClient(srv.app)

    resp = api.post("/agent/run", json={
        "task": "总结这份文件", "read_path": str(target)})
    assert resp.status_code == 200, resp.text
    pending = resp.json().get("pending_grant")
    assert pending and pending["verdict"] == "grant" and pending["gid"]
