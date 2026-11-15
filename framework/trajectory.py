# -*- coding: utf-8 -*-
"""事件溯源轨迹：把「一轮任务里发生了什么」变成可回放、可证明的数据。

以前只有 `_turn_actions`（动作名列表）、每轮一张 `_world_before` 快照、
以及给人看的中文 `log_work`。所以撤销只能撤上一轮，失败不留证据，
偏好和规则也无从归纳。这里把它们统一成一条 append-only 事件流：

    turn_start → action* → [failure | quality | undo | preference | rule]* → turn_end

每个事件都带：
  * `pre_hash` / `post_hash`：世界状态的指纹，回放时可逐条核对；
  * `source`：这条动作是谁决定的（model / mock / replay / user / system）；
  * `duration_ms` / `tokens`：成本与延迟，评测集直接用得上的数字；
  * `parent_id`：因果链，能回答「为什么走了这一步」；
  * `payload`：脱敏后的结构化细节（用户原话不落盘）。

`turn_end` 里存本轮的撤销账本，于是「回滚到任意轮」不再需要重写撤销逻辑：
把目标轮之后的账本逆序取出来，逐轮交给 effect_journal.apply_ops 即可。

零 Qt、零 requests，只依赖标准库；路径可注入，测试全程隔离在临时目录。
"""

import hashlib
import json
import os
import threading
import time

SCHEMA_VERSION = 1

KINDS = frozenset({
    "turn_start", "action", "action_result", "plan", "failure", "quality",
    "undo", "preference", "rule", "turn_end",
})

# 这些键的值一律不写进事件流（Key、路径原文、用户原话都可能含隐私）
_SENSITIVE_KEYS = frozenset({
    "api_key", "token", "password", "secret", "authorization",
    "raw_text", "user_text", "message", "content", "headers",
})
_MAX_STR = 240


def _default_path() -> str:
    from framework.utils import app_data_dir
    return os.path.join(app_data_dir(), "trajectory.jsonl")


def sanitize(payload) -> dict:
    """脱敏：丢掉敏感键、路径只留文件名、超长字符串截断。"""
    if not isinstance(payload, dict):
        return {"value": str(payload)[:_MAX_STR]} if payload is not None else {}

    def clean(key, value):
        if key.lower() in _SENSITIVE_KEYS:
            return "<redacted>"
        if isinstance(value, str):
            if key.lower() in ("path", "save_path", "filename", "file"):
                value = os.path.basename(value)
            return value[:_MAX_STR]
        if isinstance(value, dict):
            return {k: clean(k, v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(key, v) for v in value[:20]]
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return str(value)[:_MAX_STR]

    return {k: clean(k, v) for k, v in payload.items()}


def digest(obj) -> str:
    """稳定指纹：排序 + 不转义中文，保证跨机器、跨进程一致。"""
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def world_digest(world: dict) -> str:
    """五面世界状态的指纹（与 effect_journal.normalize 同一套判据）。"""
    if not isinstance(world, dict):
        return digest({})
    from framework import effect_journal
    norm = effect_journal.normalize(
        volume=world.get("volume"),
        todos=world.get("todos") or (),
        reminders=world.get("reminders") or (),
        deliveries=world.get("deliveries") or (),
        facts=world.get("facts") or ())
    return digest(norm)


class TrajectoryStore:
    """append-only JSONL 事件流。所有写操作加锁，读操作容忍半行损坏。"""

    def __init__(self, path: str = None):
        self.path = path or _default_path()
        # RLock：append 在"还没有当前轮"时会内部调用 start_turn，需要可重入
        self._lock = threading.RLock()
        self._turn_id = None
        self._seq = 0
        self._last_event_id = None
        self._restore()

    # ---------- 内部 ----------
    def _restore(self):
        rows = self._read_raw()
        if not rows:
            return
        self._last_event_id = rows[-1].get("event_id")
        for row in reversed(rows):
            if row.get("kind") == "turn_start":
                self._turn_id = row.get("turn_id")
                break
        self._seq = 0
        for row in reversed(rows):
            if row.get("turn_id") == self._turn_id:
                self._seq = int(row.get("seq", 0)) + 1
                break

    def _read_raw(self) -> list:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue    # 半行/损坏行跳过，不让一条脏数据毁掉整个回放
        return out

    def _write(self, event: dict):
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    # ---------- 写入 ----------
    def start_turn(self, *, source="user", pre_hash=None, note="") -> str:
        with self._lock:
            n = 0
            if self._turn_id and self._turn_id.startswith("t_"):
                try:
                    n = int(self._turn_id[2:])
                except ValueError:
                    n = 0
            self._turn_id = f"t_{n + 1:06d}"
            self._seq = 0
            self._last_event_id = None
            event = self._make("turn_start", source=source, pre_hash=pre_hash,
                               post_hash=pre_hash, note=note)
            self._write(event)
            return self._turn_id

    def _make(self, kind, **fields) -> dict:
        if kind not in KINDS:
            raise ValueError(f"未知事件类型: {kind}")
        payload = sanitize(fields.pop("payload", None))
        event = {
            "schema": SCHEMA_VERSION,
            "turn_id": self._turn_id or "t_000000",
            "seq": self._seq,
            "event_id": f"{self._turn_id or 't_000000'}#{self._seq}",
            "ts": round(time.time(), 3),
            "kind": kind,
            "source": fields.pop("source", "system"),
            "action": fields.pop("action", None),
            "payload": payload,
            "payload_digest": digest(payload),
            "pre_hash": fields.pop("pre_hash", None),
            "post_hash": fields.pop("post_hash", None),
            "duration_ms": int(fields.pop("duration_ms", 0) or 0),
            "tokens": int(fields.pop("tokens", 0) or 0),
            "parent_id": fields.pop("parent_id", self._last_event_id),
            "ok": fields.pop("ok", None),
            "note": str(fields.pop("note", "") or "")[:_MAX_STR],
        }
        self._seq += 1
        self._last_event_id = event["event_id"]
        return event

    def append(self, kind: str, **fields) -> dict:
        with self._lock:
            if self._turn_id is None:
                self.start_turn(source="system", note="auto-created turn")
            event = self._make(kind, **fields)
            self._write(event)
            return event

    def end_turn(self, *, post_hash=None, undo_ledger=None, ok=True,
                 note="") -> dict:
        payload = {}
        if undo_ledger:
            payload["undo_ledger"] = undo_ledger
        return self.append("turn_end", source="system", payload=payload,
                           post_hash=post_hash, ok=ok, note=note)

    # ---------- 读取 ----------
    def events(self, *, turn_id=None, kind=None, limit=None) -> list:
        rows = self._read_raw()
        if turn_id is not None:
            rows = [r for r in rows if r.get("turn_id") == turn_id]
        if kind is not None:
            rows = [r for r in rows if r.get("kind") == kind]
        if limit is not None:
            rows = rows[-int(limit):]
        return rows

    def turns(self) -> list:
        """按轮聚合，给时间线与评测用。"""
        out, current = [], None
        for e in self._read_raw():
            if e.get("kind") == "turn_start":
                current = {
                    "turn_id": e.get("turn_id"),
                    "started_at": e.get("ts"),
                    "ended_at": None,
                    "pre_hash": e.get("pre_hash"),
                    "post_hash": None,
                    "sources": set(),
                    "actions": [],
                    "failures": [],
                    "events": 0,
                    "tokens": 0,
                    "duration_ms": 0,
                }
                out.append(current)
            if current is None:
                continue
            current["events"] += 1
            current["tokens"] += int(e.get("tokens") or 0)
            current["duration_ms"] += int(e.get("duration_ms") or 0)
            if e.get("source"):
                current["sources"].add(e["source"])
            if e.get("action"):
                current["actions"].append(e["action"])
            if e.get("kind") == "failure" or e.get("ok") is False:
                current["failures"].append(e.get("note") or e.get("kind"))
            if e.get("kind") == "turn_end":
                current["ended_at"] = e.get("ts")
                current["post_hash"] = e.get("post_hash")
        for t in out:
            t["sources"] = sorted(t["sources"])
        return out

    def rollback_ledgers(self, turn_id: str) -> list:
        """回滚到指定轮：返回从最新轮到目标轮（含）的撤销账本，逆序。"""
        rows = self._read_raw()
        idx = next((i for i, e in enumerate(rows)
                    if e.get("kind") == "turn_start"
                    and e.get("turn_id") == turn_id), None)
        if idx is None:
            return []
        ledgers = [e.get("payload", {}).get("undo_ledger")
                   for e in rows[idx:]
                   if e.get("kind") == "turn_end"]
        return [x for x in reversed(ledgers) if x]

    def chain_ok(self, events: list = None) -> bool:
        """哈希链完整性：相邻事件的 post_hash / 下一条 pre_hash 必须接得上。

        只校验显式给了哈希的事件（很多事件不改动世界，允许缺省）。
        """
        rows = events if events is not None else self._read_raw()
        last = None
        for e in rows:
            pre, post = e.get("pre_hash"), e.get("post_hash")
            if pre and last and pre != last:
                return False
            if post:
                last = post
        return True

    def summarize(self) -> dict:
        rows = self._read_raw()
        kinds = {}
        for e in rows:
            kinds[e.get("kind", "?")] = kinds.get(e.get("kind", "?"), 0) + 1
        actions = kinds.get("action", 0)
        failures = kinds.get("failure", 0)
        return {
            "events": len(rows),
            "turns": kinds.get("turn_start", 0),
            "actions": actions,
            "failures": failures,
            "failure_rate": round(failures / actions, 4) if actions else 0.0,
            "tokens": sum(int(e.get("tokens") or 0) for e in rows),
            "duration_ms": sum(int(e.get("duration_ms") or 0) for e in rows),
            "chain_ok": self.chain_ok(rows),
            "kinds": kinds,
        }

    def clear(self):
        with self._lock:
            if os.path.exists(self.path):
                os.remove(self.path)
            self._turn_id, self._seq, self._last_event_id = None, 0, None
