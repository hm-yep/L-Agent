# -*- coding: utf-8 -*-
"""授权状态机：把「这条路径要不要你点头」这件事变成一份可等待、可超时、可撤销的记录。

为什么单独一层：`path_guard.check_read` 只负责**判定**（allow / grant / deny），
判定完还得有人记住「我在等谁点头、等多久、答了算哪一档」。这一层就是那份记忆：

  * `GrantService` —— 授权记录（`framework.grants.GrantStore`）+ 待决请求的登记表；
  * 桌面浮层卡片与网页确认条都只是它的两个渲染，**判定与状态只有这一份实现**；
  * 超时（默认 120 秒）按**拒绝**处理，并把「这是超时按拒处理的」记在请求上，
    下一次回复必须如实说出来，不能悄悄当成用户点的拒绝。

三条硬约定：
  1. **不落盘**：授权记录只在内存里，重启回到默认拒绝（沿用 `grants.py` 的不变量）；
  2. **线程安全**：网页端的 HTTP 线程、桌面的 Qt 主线程会同时碰这份状态；
  3. **不抛异常**：任何一步出错都退化成「按拒绝处理」，绝不把调用方带崩。

本模块只依赖标准库 + 项目内的纯数据模块（`grants` / `path_guard`），
不 import Qt / requests —— 网页端（Docker 镜像里没有 PyQt5）也要能用它。
"""

import threading
import time
import uuid

from framework.grants import GRANT_FILE, GRANT_TREE, GrantStore
from framework.path_guard import check_read

# 三个按钮对应的决定（卡片与网页确认条共用这三个值）
DECISION_FILE = GRANT_FILE          # 只读这一个
DECISION_TREE = GRANT_TREE          # 允许整个文件夹（本次会话）
DECISION_DENY = "deny"              # 拒绝
DECISIONS = (DECISION_FILE, DECISION_TREE, DECISION_DENY)

# 等你点头的最长时间：超过就按拒绝处理
GRANT_TIMEOUT_SEC = 120

# 超时文案（多处复用，措辞一致才不会各说一套）
TIMEOUT_NOTE = "等了太久没等到你确认，我按拒绝处理了"


class PendingGrant:
    """一条待决的授权请求。"""

    __slots__ = ("gid", "path", "source", "reason", "created_at",
                 "deadline", "decision", "note", "timed_out")

    def __init__(self, gid: str, path: str, source: str, reason: str,
                 created_at: float, deadline: float):
        self.gid = gid
        self.path = path
        self.source = source          # desktop / web：谁发起的这次请求
        self.reason = reason
        self.created_at = created_at
        self.deadline = deadline
        self.decision = ""            # 空 = 还没答
        self.note = ""
        self.timed_out = False

    def remaining_sec(self, now: float = None) -> int:
        now = time.time() if now is None else now
        return max(0, int(round(self.deadline - now)))

    def expired(self, now: float = None) -> bool:
        now = time.time() if now is None else now
        return now >= self.deadline

    def to_dict(self) -> dict:
        """给界面/接口看的字典（不含判据细节，只有用户需要知道的）。"""
        return {
            "gid": self.gid,
            "path": self.path,
            "name": _basename(self.path),
            "dir": _dirname(self.path),
            "source": self.source,
            "reason": self.reason,
            "decision": self.decision,
            "note": self.note,
            "timed_out": self.timed_out,
            "expires_in": self.remaining_sec(),
        }


def _basename(path: str) -> str:
    import os
    return os.path.basename(str(path or "")) or str(path or "")


def _dirname(path: str) -> str:
    import os
    return os.path.dirname(str(path or ""))


class GrantService:
    """授权记录 + 待决请求。一个进程一份，桌面与网页端共用同一套判定。

    桌面进程里由 `Framework` 持有；网页端独立运行时自己持有一份（自持模式）。
    两种模式的**判定逻辑与状态机完全相同**，差别只在于授权记录归谁保管。
    """

    def __init__(self, timeout_sec: int = GRANT_TIMEOUT_SEC):
        self.timeout_sec = int(timeout_sec)
        self.store = GrantStore()
        self._pending = {}
        self._granted_at = {}       # 授权键 → 放开时刻（展示用，不参与判据）
        self._lock = threading.RLock()

    # ---------------- 判定 ----------------
    def check(self, raw_path) -> tuple:
        """三态判定：allow / grant / deny。判据与 `path_guard` 是同一份。"""
        with self._lock:
            return check_read(raw_path, grants=self.store)

    # ---------------- 待决请求 ----------------
    def request(self, path: str, *, source: str = "desktop",
                reason: str = "") -> dict:
        """登记一条待决授权请求，返回给界面/接口的字典。

        调用方不阻塞：弹卡片、显示确认条都可以立刻返回。
        """
        now = time.time()
        gid = uuid.uuid4().hex[:12]
        grant = PendingGrant(gid=gid, path=str(path or ""), source=source,
                             reason=reason or "这个路径不在我平常能读的目录里，"
                                              "要授权我才读",
                             created_at=now,
                             deadline=now + self.timeout_sec)
        with self._lock:
            self._pending[gid] = grant
        return grant.to_dict()

    def get(self, gid: str):
        with self._lock:
            return self._pending.get(str(gid or ""))

    def pending(self) -> list:
        """当前还在等答案的请求（先到期的排前面）。"""
        with self._lock:
            items = [g for g in self._pending.values() if not g.decision]
        items.sort(key=lambda g: g.deadline)
        return [g.to_dict() for g in items]

    def answer(self, gid: str, decision: str, *, note: str = "") -> dict:
        """回答一条请求：落授权 + 标记已决。

        :return: {"ok": bool, "grant": {...}, "applied": "file|tree|deny",
                  "newly": bool, "message": "给用户看的一句话"}

        `newly` 表示「这条请求是第一次被回答」。调用方（Framework）只在
        newly 时才广播给界面：卡片被关闭时也会补发一次拒绝，若无条件广播，
        网页端刚点的「允许」会被后到的「拒绝」覆盖掉。
        """
        decision = str(decision or "").strip()
        if decision not in DECISIONS:
            return {"ok": False, "applied": "", "newly": False,
                    "message": "不认识这个选择：%s" % (decision or "(空)"),
                    "grant": {}}
        with self._lock:
            grant = self._pending.get(str(gid or ""))
            if grant is None:
                return {"ok": False, "applied": "", "newly": False,
                        "message": "这条授权请求已经不在了（可能已超时）",
                        "grant": {}}
            if grant.decision:
                # 已经答过（超时按拒也算答过）：不重复生效，但如实回报现状
                return {"ok": True, "applied": grant.decision,
                        "newly": False,
                        "message": "这条请求之前已经按「%s」处理了"
                                   % _decision_label(grant.decision),
                        "grant": grant.to_dict()}
            grant.decision = decision
            grant.note = note
            if decision == DECISION_FILE:
                self.store.add(GRANT_FILE, grant.path)
                self._granted_at[_grant_key(GRANT_FILE, grant.path)] = time.time()
                message = "好，这一个文件我这次读"
            elif decision == DECISION_TREE:
                root = _tree_root(grant.path)
                self.store.add(GRANT_TREE, root)
                self._granted_at[_grant_key(GRANT_TREE, root)] = time.time()
                message = "好，这个文件夹本次会话里我都能读"
            else:
                message = "好，这个我不读"
        return {"ok": True, "applied": decision, "newly": True,
                "message": message, "grant": grant.to_dict()}

    def expire_due(self, now: float = None) -> list:
        """把到期的请求按**拒绝**处理，返回被超时拒绝的请求字典列表。"""
        now = time.time() if now is None else now
        expired = []
        with self._lock:
            for gid, grant in list(self._pending.items()):
                if grant.decision or not grant.expired(now):
                    continue
                grant.decision = DECISION_DENY
                grant.timed_out = True
                grant.note = TIMEOUT_NOTE
                expired.append(grant.to_dict())
        return expired

    def forget(self, gid: str) -> bool:
        """把一条请求从登记表里删掉（已决且恢复完动作后调用，避免表越积越多）。"""
        with self._lock:
            return self._pending.pop(str(gid or ""), None) is not None

    # ---------------- 展示与撤销 ----------------
    def list_grants(self) -> list:
        """当前放开了哪些（档位 + 路径 + 放开时刻），展示用。"""
        with self._lock:
            out = self.store.entries()
            for item in out:
                key = _grant_key(item.get("level"), item.get("path"))
                item["granted_at"] = self._granted_at.get(key, 0)
            return out

    def revoke_all(self) -> int:
        """全部撤销：授权记录清空，返回清掉的条数。

        撤销后同一个路径必须重新弹卡——判据只看 `store`，清空即回到未授权。
        """
        with self._lock:
            count = len(self.store)
            self.store.clear()
            self._granted_at.clear()
        return count

    def stats(self) -> dict:
        with self._lock:
            return {
                "granted": len(self.store),
                "pending": len([g for g in self._pending.values()
                                if not g.decision]),
                "timeout_sec": self.timeout_sec,
            }


def _grant_key(level: str, path: str) -> str:
    """授权记录里的键（与 GrantStore 的归一化口径一致，仅用于挂时间戳）。"""
    from framework.grants import _key
    return "%s|%s" % (level, _key(path))


def _decision_label(decision: str) -> str:
    return {DECISION_FILE: "只读这一个",
            DECISION_TREE: "允许整个文件夹",
            DECISION_DENY: "拒绝"}.get(decision, decision or "（无）")


def _tree_root(path: str) -> str:
    """「允许整个文件夹」授权的是哪一级？

    目标是文件 → 授权它的父目录（否则「同一个文件夹里的另一个文件」还得再问）；
    目标本身是目录 → 授权它自己。
    """
    import os
    p = str(path or "")
    if os.path.isdir(p):
        return p
    parent = os.path.dirname(p)
    return parent or p
