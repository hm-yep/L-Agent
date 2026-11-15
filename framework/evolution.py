# -*- coding: utf-8 -*-
"""从「使用过程中遇到的问题」里归纳规则，让系统下次自动调整行为。

数据流：`TrajectoryStore.events()` 的 append-only 事件流喂进 `RuleStore.learn()`，
按「问题族 + 作用域指纹」归纳成可复用的规则；`RuleStore.apply(context)` 在动作
执行前返回调整建议（追加搜索限定词、提高 PPT 页数下限、跳过读不出的路径、
先向用户确认、降低重试预算等），命中规则的 id 一并回填，便于事后追溯成效。

设计要点（全部本地确定性，不调模型，零 Qt / 零网络）：
- **证据门槛**：同族同模式至少 2 条独立事件（不同 event_id）才生成规则，
  避免一次偶发失败就改行为；跨批次累积（一次只喂到 1 条时先记 pending）。
- **幂等**：已计入过的 event_id 不再重复计数。重复喂同一批事件不新增规则、
  triggers 不虚涨、evidence 去重（展示窗口上限 20）。因此同一批事件 learn
  两次结果完全一致（确定性）。
- **成效反馈**：`record_outcome(rule_id, ok)` 累加 successes / failures。

  confidence 公式（Beta(1,1) 后验均值，写在这里以便评测核对）：

      confidence = round((1 + successes) / (2 + successes + failures), 4)

  初始 successes=failures=0 时为 0.5；successes 上升则单调上升，failures
  上升则单调下降，恒在 (0, 1) 区间内。

- **自动淘汰**：`failures >= 3 且 successes == 0` → enabled=False 并写
  disabled_reason；被淘汰规则仍可查询 / forget，但不再出现在 apply() 里。
- **上限**：`getattr(config, "MAX_RULES", 24)`，超出按
  (enabled, confidence, updated_at) 丢最弱的（停用、置信低、最老的先丢）。
- **隐私**：规则文件只写「族名 + 结构化 pattern（动作名 / 文档类型 / 文件
  basename / 16 位指纹）+ event_id」。路径一律取 basename 或哈希，绝不写绝对
  路径；用户原话、note 文本、payload 里的自由文本都不落盘。path 注入时
  全程不碰真实 data/（app_data_dir 仅默认路径时惰性导入）。

落盘结构：{"schema":1, "rules":{id: rule}, "pending":{group_key: [event_id...]}}
"""

import hashlib
import json
import os
import threading
import time

# 支持的规则族名称集合
RULE_FAMILIES = frozenset({
    "search_too_broad", "doc_too_thin", "path_unreadable",
    "user_undid", "action_timeout",
})

# 证据门槛 / 证据窗口 / 去重记忆上限 / 淘汰阈值
_EVIDENCE_THRESHOLD = 2
_MAX_EVIDENCE = 20
_MAX_SEEN = 60
_RETIRE_FAILURES = 3

# 各族的判定关键词（出现在 note / payload.reason / payload.message 等即命中）
_SEARCH_KW = ("零重合", "不相关", "质量不达标", "对不上题", "换词重搜")
_DOC_KW = ("干瘪", "不足", "单薄", "要点", "全篇没有", "章节不足",
           "几乎没有数据行")
_PATH_KW = ("文件不存在", "读不出", "路径被拒绝", "表格算不出来",
            "打不开", "找不到文件")
_TIMEOUT_KW = ("超时", "等太久", "耗时过长", "timeout")
_UNDO_KW = ("撤销", "撤回", "恢复原样", "undo")

# 撤销动作本身不是「被撤动作类别」，归纳作用域时要过滤掉
_UNDO_ACTION_NAMES = frozenset({"undo", "undo_last", "撤销", "撤回"})

# 各族的固定调整建议（apply 只输出这里出现过的字段，不凭空造）
_ADJUSTMENTS = {
    "search_too_broad": {"search_suffix": "最新 数据"},
    "doc_too_thin": {"min_slides": 8, "min_bullet_len": 12},
    "path_unreadable": {"skip_action": True},
    "user_undid": {"require_confirm": True},
    "action_timeout": {"retry_budget": 1},
}

# apply 给日志/用户的中文原因（由族名派生，不落盘，因此文件里没有自由文本）
_FAMILY_NOTE = {
    "search_too_broad": "此前搜索结果与查询零重合，建议补充限定词或换词",
    "doc_too_thin": "此前文档内容偏单薄，已提高页数与要点长度下限",
    "path_unreadable": "此前有同类文件读不出，建议跳过该路径",
    "user_undid": "用户曾撤销该类动作，执行前先确认",
    "action_timeout": "此前该类动作超时，已降低重试预算",
}

# 从 payload 里取作用域时只认这些白名单键，绝不把 query / text / 原话当指纹
_PATH_KEYS = ("path", "save_path", "filename", "file", "target",
              "target_path")


# ------------------------------------------------------------
# 纯函数：脱敏 / 指纹 / 分类
# ------------------------------------------------------------
def _digest(obj) -> str:
    """稳定指纹（与 trajectory.digest 同判据，自包含不依赖那个模块）。"""
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _as_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_token(value) -> str:
    """作用域值清洗：截掉路径分隔符、限长，杜绝绝对路径混入规则文件。"""
    s = str(value).strip().replace("\\", "/").split("/")[-1]
    return s[:64]


def _basename(value) -> str:
    """跨平台取文件名（Windows 反斜杠在 POSIX 上不是分隔符，自己切）。"""
    s = str(value).strip().replace("\\", "/").rstrip("/")
    return s.split("/")[-1][:120] if s else ""


def _confidence(successes, failures) -> float:
    """Beta(1,1) 后验均值：round((1+s)/(2+s+f), 4)。

    successes 上升单调上升、failures 上升单调下降，恒在 (0,1)。
    """
    s = max(0, _as_int(successes))
    f = max(0, _as_int(failures))
    return round((1 + s) / (2 + s + f), 4)


def _event_text(event: dict) -> str:
    """只用于「是否命中问题族」的判定文本，绝不写进规则文件。"""
    parts = []
    note = event.get("note")
    if isinstance(note, str):
        parts.append(note)
    payload = event.get("payload")
    if isinstance(payload, dict):
        for key in ("reason", "message", "detail", "error"):
            value = payload.get(key)
            if isinstance(value, str):
                parts.append(value)
    return " ".join(parts)


def _event_id(event: dict) -> str:
    """独立事件标识；缺 event_id 时用稳定内容指纹兜底，保证幂等。"""
    eid = event.get("event_id")
    if isinstance(eid, (str, int)) and str(eid).strip():
        return str(eid)[:120]
    return "auto:" + _digest(event)


def _action_pattern(event: dict, family: str, payload: dict) -> dict:
    """动作类族的作用域：优先 payload 里被撤/失败动作，再退到 event.action。"""
    default = "web_search" if family == "search_too_broad" else "*"
    candidates = (
        payload.get("undone_action"), payload.get("target_action"),
        payload.get("failed_action"), payload.get("undone"),
        payload.get("action"), event.get("action"),
    )
    for cand in candidates:
        if isinstance(cand, str) and cand.strip() \
                and cand.strip() not in _UNDO_ACTION_NAMES:
            return {"action": _safe_token(cand)}
    # "*" 表示没学到具体动作类别：apply 时对任意动作都生效（宁可多确认/少重试）
    return {"action": default}


def _doc_pattern(event: dict, payload: dict) -> dict:
    for cand in (payload.get("doc_type"), payload.get("kind"),
                 payload.get("type"), event.get("doc_type")):
        if isinstance(cand, str) and cand.strip():
            return {"doc_type": _safe_token(cand)}
    return {"doc_type": "ppt"}


def _path_pattern(event: dict, payload: dict) -> dict:
    """路径指纹：baseName 优先（稳定、可跨事件聚合），取不到才退到哈希。

    绝不使用绝对路径；basename 存在时不放 digest，否则每条事件 digest 不同会
    把同一类问题拆成多个分组，永远凑不齐 2 条证据。
    """
    for key in _PATH_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            name = _basename(value)
            if name:
                return {"basename": name}
    fp = event.get("payload_digest")
    if not isinstance(fp, str) or not fp.strip():
        fp = _digest(payload or {"note": event.get("note")})
    return {"digest": _safe_token(fp)[:32]}


def _classify(event):
    """一条事件 → (family, pattern)，不属于任何问题族返回 None。

    一个事件只归入最先命中的一族（先具体后宽泛），避免同一证据被重复计数。
    """
    if not isinstance(event, dict):
        return None
    kind = event.get("kind")
    text = _event_text(event)
    payload = event.get("payload")
    payload = payload if isinstance(payload, dict) else {}

    # 1) 用户撤销
    if kind == "undo" or (event.get("source") == "user"
                          and any(k in text for k in _UNDO_KW)):
        return ("user_undid", _action_pattern(event, "user_undid", payload))
    # 2) failure：路径读不出
    if kind == "failure" and any(k in text for k in _PATH_KW):
        return ("path_unreadable", _path_pattern(event, payload))
    # 3) failure：超时
    if kind == "failure" and any(k in text for k in _TIMEOUT_KW):
        return ("action_timeout", _action_pattern(event, "action_timeout",
                                                  payload))
    # 4) quality：文档太薄
    if kind == "quality" and any(k in text for k in _DOC_KW):
        return ("doc_too_thin", _doc_pattern(event, payload))
    # 5) quality：搜索太宽
    if kind == "quality" and any(k in text for k in _SEARCH_KW):
        return ("search_too_broad", _action_pattern(event, "search_too_broad",
                                                    payload))
    return None


def _group_key(family: str, pattern: dict) -> str:
    return family + "|" + json.dumps(
        pattern, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _rule_id(group_key: str) -> str:
    family = group_key.split("|", 1)[0]
    return family + ":" + _digest(group_key)


def _matches(rule: dict, ctx: dict) -> bool:
    """apply 的命中判据：规则作用域与 context 对应字段相等才命中。"""
    family = rule.get("family")
    pattern = rule.get("pattern")
    if not isinstance(pattern, dict):
        return False
    if family in ("search_too_broad", "user_undid", "action_timeout"):
        want = pattern.get("action")
        got = ctx.get("action")
        if want == "*":
            return bool(got)
        return bool(want) and got == want
    if family == "doc_too_thin":
        want = pattern.get("doc_type")
        return bool(want) and ctx.get("doc_type") == want
    if family == "path_unreadable":
        want = pattern.get("basename")
        if want:
            raw = ctx.get("path") or ctx.get("basename")
            if raw:
                return _basename(raw) == want
        fp = pattern.get("digest")
        if fp and ctx.get("digest"):
            return ctx.get("digest") == fp
        return False
    return False


# ------------------------------------------------------------
# 规则存储
# ------------------------------------------------------------
class RuleStore:
    """归纳 / 应用 / 反馈 / 淘汰 规则，JSON 原子落盘，路径可注入。"""

    def __init__(self, path: str = None):
        if path:
            self.path = str(path)
        else:
            # 惰性导入：只有走默认路径时才碰真实 data/；path 注入时全程不碰
            from framework.utils import app_data_dir
            self.path = os.path.join(app_data_dir(), "rules.json")
        self._lock = threading.RLock()

    # ---------- 底层读写 ----------
    @staticmethod
    def _empty() -> dict:
        return {"schema": 1, "rules": {}, "pending": {}}

    def _max_rules(self) -> int:
        from framework import config
        try:
            n = int(getattr(config, "MAX_RULES", 24))
        except (TypeError, ValueError):
            n = 24
        return max(1, n)

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return self._empty()
        if not isinstance(data, dict):
            return self._empty()
        rules, pending = data.get("rules"), data.get("pending")
        if not isinstance(rules, dict) or not isinstance(pending, dict):
            return self._empty()
        return {"schema": 1, "rules": rules, "pending": pending}

    def _write(self, data: dict):
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)          # 原子替换，读侧永远看到完整文件

    @staticmethod
    def _now(rules: dict) -> float:
        """严格递增时间戳：保证上限裁剪时「谁最老」可判定、可复现。"""
        t = time.time()
        for r in rules.values():
            if not isinstance(r, dict):
                continue
            try:
                t = max(t, float(r.get("updated_at") or 0) + 1e-6)
            except (TypeError, ValueError):
                continue
        return round(t, 6)

    @staticmethod
    def _retire(rule: dict) -> bool:
        """自动淘汰：连续失败且毫无成功成效 → 停用。返回是否发生改动。"""
        if (_as_int(rule.get("failures")) >= _RETIRE_FAILURES
                and _as_int(rule.get("successes")) == 0
                and rule.get("enabled")):
            rule["enabled"] = False
            rule["disabled_reason"] = "连续失败且无成功成效，已自动停用"
            return True
        return False

    def _cap(self, rules: dict):
        """超出上限按 (enabled, confidence, updated_at) 丢最弱的。"""
        def weakness(item):
            rid, r = item
            if not isinstance(r, dict):
                return (0, 0.0, 0.0, str(rid))
            return (1 if r.get("enabled") else 0,
                    _as_float(r.get("confidence")),
                    _as_float(r.get("updated_at")),
                    str(rid))

        max_n = self._max_rules()
        while len(rules) > max_n:
            del rules[min(rules.items(), key=weakness)[0]]

    # ---------- 归纳 ----------
    def learn(self, events: list) -> list:
        """从事件流归纳/更新规则，返回本次变化的规则（dict 列表）。

        同族同模式需 >= 2 条独立 event_id 才生成；已计入的证据重复喂入不改变
        任何字段，因此 learn 幂等且同一批事件两次结果一致。
        """
        if not isinstance(events, (list, tuple)):
            events = [events] if events is not None else []
        with self._lock:
            data = self._read()

            # 本批次按 (族, pattern) 分组，收集独立 event_id
            groups = {}
            for ev in events:
                try:
                    info = _classify(ev)
                    if not info:
                        continue
                    family, pattern = info
                    gk = _group_key(family, pattern)
                    eid = _event_id(ev)
                except Exception:                 # 脏事件不许拖垮整次归纳
                    continue
                g = groups.get(gk)
                if g is None:
                    g = {"family": family, "pattern": pattern, "ids": []}
                    groups[gk] = g
                if eid not in g["ids"]:
                    g["ids"].append(eid)

            changed, dirty = [], False
            for gk in sorted(groups):            # 固定顺序 → 确定性
                g = groups[gk]
                raw = data["pending"].get(gk)
                seen = list(raw) if isinstance(raw, list) else []
                seen_set = set(seen)
                new_ids = [e for e in g["ids"] if e not in seen_set]
                if new_ids:
                    seen.extend(new_ids)
                    seen = seen[-_MAX_SEEN:]
                    data["pending"][gk] = seen
                    dirty = True
                if len(seen) < _EVIDENCE_THRESHOLD:
                    continue                     # 证据不足：只记 pending

                rid = _rule_id(gk)
                rule = data["rules"].get(rid)
                if not isinstance(rule, dict):
                    ts = self._now(data["rules"])
                    rule = {
                        "id": rid,
                        "family": g["family"],
                        "pattern": dict(g["pattern"]),
                        "adjustment": dict(_ADJUSTMENTS[g["family"]]),
                        "triggers": len(seen),
                        "successes": 0,
                        "failures": 0,
                        "confidence": _confidence(0, 0),
                        "enabled": True,
                        "evidence": list(seen[-_MAX_EVIDENCE:]),
                        "created_at": ts,
                        "updated_at": ts,
                        "disabled_reason": "",
                    }
                    data["rules"][rid] = rule
                    dirty = True
                elif new_ids:
                    ev_list = rule.get("evidence")
                    ev_list = list(ev_list) if isinstance(ev_list, list) else []
                    merged = list(dict.fromkeys(ev_list + new_ids))
                    rule["evidence"] = merged[-_MAX_EVIDENCE:]
                    rule["triggers"] = _as_int(rule.get("triggers")) + len(new_ids)
                    rule["updated_at"] = self._now(data["rules"])
                    dirty = True
                # 无新证据时不改任何字段 → 重复喂入完全幂等
                if self._retire(rule):
                    dirty = True
                changed.append(rid)

            self._cap(data["rules"])
            if dirty or changed:
                self._write(data)
            return [dict(data["rules"][r]) for r in changed
                    if isinstance(data["rules"].get(r), dict)]

    # ---------- 应用 ----------
    def apply(self, context: dict) -> dict:
        """执行前给出调整建议；`rule_ids` 恒存在（必填，便于追溯成效）。

        只输出命中规则 adjustment 里真实出现过的字段 + 中文 note，不凭空造。
        """
        ctx = context if isinstance(context, dict) else {}
        with self._lock:
            rules = self._read()["rules"]
        matched = [r for r in rules.values()
                   if isinstance(r, dict) and r.get("enabled")
                   and _matches(r, ctx)]
        matched.sort(key=lambda r: str(r.get("id") or ""))
        out = {"rule_ids": [str(r.get("id")) for r in matched]}
        if not matched:
            return out

        for r in matched:
            adj = r.get("adjustment")
            if not isinstance(adj, dict):
                continue
            for key, value in adj.items():
                if key in ("skip_action", "require_confirm"):
                    out[key] = bool(out.get(key)) or bool(value)
                elif key in ("min_slides", "min_bullet_len"):
                    out[key] = max(_as_int(out.get(key)),
                                   _as_int(value))
                elif key == "retry_budget":
                    cur = out.get(key)
                    out[key] = _as_int(value) if cur is None \
                        else min(_as_int(cur), _as_int(value))
                elif key in ("search_suffix", "search_prefix"):
                    prev = str(out.get(key, ""))
                    text = str(value)
                    if text and text not in prev:
                        out[key] = (prev + " " + text).strip()
                # 其它键忽略：apply 不输出没有规则依据的字段

        notes = []
        for r in matched:
            note = _FAMILY_NOTE.get(r.get("family"))
            if note and note not in notes:
                notes.append(note)
        if notes:
            out["note"] = "；".join(notes)
        return out

    # ---------- 成效反馈 ----------
    def record_outcome(self, rule_id: str, ok: bool) -> dict:
        """记一次成效，更新 successes/failures/confidence 并落盘；无此规则返回 {}。"""
        with self._lock:
            data = self._read()
            rule = data["rules"].get(rule_id)
            if not isinstance(rule, dict):
                return {}
            if ok:
                rule["successes"] = _as_int(rule.get("successes")) + 1
            else:
                rule["failures"] = _as_int(rule.get("failures")) + 1
            rule["confidence"] = _confidence(rule["successes"],
                                             rule["failures"])
            rule["updated_at"] = self._now(data["rules"])
            self._retire(rule)
            self._cap(data["rules"])
            self._write(data)
            cur = data["rules"].get(rule_id)
            return dict(cur) if isinstance(cur, dict) else dict(rule)

    # ---------- 查询 / 删除 ----------
    def all(self) -> list:
        with self._lock:
            rules = self._read()["rules"]
        out = [dict(v) for v in rules.values() if isinstance(v, dict)]
        out.sort(key=lambda r: str(r.get("id") or ""))
        return out

    def get(self, rule_id: str):
        with self._lock:
            r = self._read()["rules"].get(rule_id)
        return dict(r) if isinstance(r, dict) else None

    def forget(self, rule_id: str) -> bool:
        with self._lock:
            data = self._read()
            rule = data["rules"].get(rule_id)
            if not isinstance(rule, dict):
                return False
            del data["rules"][rule_id]
            # 同步清掉待定证据，否则下次 learn 会用旧证据把规则「复活」
            try:
                gk = _group_key(rule.get("family"), rule.get("pattern") or {})
                data["pending"].pop(gk, None)
            except Exception:
                pass
            self._write(data)
            return True

    def clear(self):
        with self._lock:
            self._write(self._empty())


# ------------------------------------------------------------
# 评测 / 文档用统计
# ------------------------------------------------------------
def summarize_rules(rules: list) -> dict:
    """规则列表 → 统计 dict（鲁棒：非 dict 条目跳过，空列表也给完整结构）。"""
    items = [r for r in rules if isinstance(r, dict)] \
        if isinstance(rules, (list, tuple)) else []
    by_family = {f: 0 for f in sorted(RULE_FAMILIES)}
    enabled = disabled = triggers = successes = failures = 0
    conf_sum = 0.0
    for r in items:
        fam = r.get("family")
        if fam in by_family:
            by_family[fam] += 1
        if r.get("enabled"):
            enabled += 1
        else:
            disabled += 1
        triggers += _as_int(r.get("triggers"))
        successes += _as_int(r.get("successes"))
        failures += _as_int(r.get("failures"))
        conf_sum += _as_float(r.get("confidence"))
    total = len(items)
    outcomes = successes + failures
    return {
        "total": total,
        "enabled": enabled,
        "disabled": disabled,
        "by_family": by_family,
        "avg_confidence": round(conf_sum / total, 4) if total else 0.0,
        "triggers": triggers,
        "successes": successes,
        "failures": failures,
        "success_rate": round(successes / outcomes, 4) if outcomes else 0.0,
    }
