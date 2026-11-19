# -*- coding: utf-8 -*-
"""从自然对话里自动发现用户偏好，并让偏好真正作用到行为。

与 memory_system 的长期记忆（要用户显式说「记住…」）不同：这里不需要用户
发话，每轮对话过一遍 observe() 即可。命中的偏好落盘，系统提示词、文档样式、
联网开关、回答详略都从「当前落盘状态」实时读取——改主意后下一句立刻生效，
不玩「只存不用」的假深度。

设计要点：
- 纯本地确定性规则，不调模型；零 Qt / 零网络依赖；路径可注入，测试全程隔离。
- 隐私：落盘只存「命中短语 + 原话哈希」，绝不把用户原话明文写进文件。
  原话哈希等价于 framework.trajectory.digest(原话)，但这里自包含实现，
  不依赖那个模块是否已随版本一起提交。
- 落盘结构 {"schema":1, "items":{key: {...}}}，同一 key 只保留一条当前决定：
  同值重复累加 hits/evidence，改主意覆盖 value 但保留 evidence 全历史与
  supersedes 链指针，不简单丢弃。

主执行者接进 core.py / ai_engine.py / doc_generator.py 时：
- as_prompt() -> 追加进系统提示词；
- doc_style() -> 合并进 generate 的 spec["style"]；
- verbosity() / allow_web_search() -> 直接驱动行为开关。
"""

import hashlib
import json
import os
import re
import threading
import time

# 支持的偏好维度集合
PREFERENCE_KEYS = {
    "doc_theme", "verbosity", "tone", "web_search", "language",
    "reminder_style",
}

# 超长输入只处理前段，防止正则/哈希被撑爆（其余正常返回，不抛异常）
_MAX_TEXT = 2000
# 单条 item 的 evidence 历史上限（防文件无限膨胀）
_MAX_EVIDENCE = 50

# ---- 大模型批量抽取（observe_batch）的护栏 ----
# 这些数字是刻意的：一次最多收 6 条、单条值不超过 120 字。
# 模型容易把"帮我查天气"总结成"喜欢户外"，所以条数少一点、值短一点，
# 再由下面的原话引用校验逐条核对——宁可漏，不能编。
_MAX_LLM_ITEMS = 6
_MAX_LLM_VALUE = 120
# 开放式偏好的 key：小写字母开头，只允许小写/数字/下划线，2~40 位
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")

# ------------------------------------------------------------
# 主题色：dark / theme 取自 doc_generator.PPT_TEMPLATES 的深色系，
# 统一成 #RRGGBB（resolve_style -> parse_color 能直接解析 6 位 hex）
# ------------------------------------------------------------
_THEME_COLOR = {
    "深色":   "#0F2B46",
    "深空蓝": "#0F2B46",
    "翡翠绿": "#0E332A",
    "暖橙":   "#4A250F",
    "绛紫":   "#2C1A40",
    "雅灰":   "#25272A",
}

# 主题别名 -> 规范值（长词在前，避免「深空蓝」被「深色/蓝」截断）。
# 全部 ≥2 字，且不含「红」——红色不是支持的主题，只在「别用红色」里作为
# 被拒绝的对象出现，由下面的否定规则单独处理，避免「用红笔」这类误抓。
_THEME_ALIASES = [
    ("深空蓝", "深空蓝"), ("深蓝色", "深空蓝"), ("深蓝", "深空蓝"),
    ("蓝色", "深空蓝"),
    ("深色调", "深色"), ("暗色调", "深色"), ("黑色系", "深色"),
    ("深色", "深色"), ("暗色", "深色"),
    ("翡翠绿", "翡翠绿"), ("青绿", "翡翠绿"), ("翠绿", "翡翠绿"),
    ("绿色", "翡翠绿"),
    ("暖橙色", "暖橙"), ("橘色", "暖橙"), ("橙色", "暖橙"),
    ("暖色", "暖橙"), ("暖橙", "暖橙"),
    ("绛紫色", "绛紫"), ("紫色", "绛紫"), ("绛紫", "绛紫"),
    ("雅灰色", "雅灰"), ("灰色", "雅灰"), ("极简", "雅灰"),
    ("简约", "雅灰"), ("雅灰", "雅灰"),
]

# 正向主题需要有「设计/偏好意图」语境，避免「查一下绿色能源」被误判成偏好。
# 判据刻意宽松：喜欢/偏好/以后都/用/设成/PPT/配色……都算意图。
_STYLE_CUES = (
    "喜欢", "偏好", "偏爱", "最爱", "就爱", "以后", "都", "一律", "永远",
    "每次", "用", "给", "要", "设", "做", "改成", "换成", "选", "做成",
    "设成", "生成", "弄", "配色", "主题", "风格", "模板", "模版", "背景",
    "页面", "ppt", "PPT", "文档", "幻灯片", "报告",
)

# 强/弱语气标记：用于 confidence 分层
_STRONG_MARKERS = (
    "我喜欢", "我偏好", "我偏爱", "以后都", "以后", "一律", "永远",
    "每次都", "都要", "最爱", "就爱",
)
_VAGUE_MARKERS = (
    "可能", "也许", "大概", "或许", "似乎", "好像", "貌似", "感觉",
    "估计", "说不准", "不确定",
)

# ------------------------------------------------------------
# 规则表：(触发短语, key, value, polarity)
# value 是「结果值」：否定句已经转成对应 key 的反向/期望值，例如
#   「太长了/简洁点/不喜欢花哨」 -> verbosity=concise
#   「别太活泼/正式一点」        -> tone=formal
#   「以后别联网/不用搜了」      -> web_search=False
# polarity 记录这句话是「显式喜好(positive)」还是「否定/抱怨(negative)」，
# 供 as_prompt 措辞与 doc_style 判定（negative 的 doc_theme 不设颜色）使用。
# ------------------------------------------------------------
_RULES = [
    # doc_theme 否定：拒绝某个颜色（value 表示被拒绝的颜色）
    ("别用红色", "doc_theme", "红色", "negative"),
    ("不要用红色", "doc_theme", "红色", "negative"),
    ("不用红色", "doc_theme", "红色", "negative"),
    ("不要红色", "doc_theme", "红色", "negative"),
    ("别用红", "doc_theme", "红色", "negative"),
    ("不喜欢红色", "doc_theme", "红色", "negative"),
    ("讨厌红色", "doc_theme", "红色", "negative"),

    # verbosity: concise（嫌长/否定花哨）
    ("简洁一点", "verbosity", "concise", "negative"),
    ("简洁点", "verbosity", "concise", "negative"),
    ("简洁些", "verbosity", "concise", "negative"),
    ("简单说", "verbosity", "concise", "negative"),
    ("别写太长", "verbosity", "concise", "negative"),
    ("别太长", "verbosity", "concise", "negative"),
    ("太长了", "verbosity", "concise", "negative"),
    ("太长", "verbosity", "concise", "negative"),
    ("短一点", "verbosity", "concise", "negative"),
    ("写短点", "verbosity", "concise", "negative"),
    ("精简", "verbosity", "concise", "negative"),
    ("啰嗦", "verbosity", "concise", "negative"),
    ("不喜欢花哨", "verbosity", "concise", "negative"),
    ("花哨", "verbosity", "concise", "negative"),
    # verbosity: detailed
    ("详细一点", "verbosity", "detailed", "positive"),
    ("详细点", "verbosity", "detailed", "positive"),
    ("详细些", "verbosity", "detailed", "positive"),
    ("展开说", "verbosity", "detailed", "positive"),
    ("展开讲", "verbosity", "detailed", "positive"),
    ("写全一些", "verbosity", "detailed", "positive"),
    ("写全点", "verbosity", "detailed", "positive"),
    ("说详细", "verbosity", "detailed", "positive"),

    # tone: formal
    ("正式一点", "tone", "formal", "positive"),
    ("正式点", "tone", "formal", "positive"),
    ("正式些", "tone", "formal", "positive"),
    ("严肃些", "tone", "formal", "positive"),
    ("严肃点", "tone", "formal", "positive"),
    ("别太活泼", "tone", "formal", "negative"),
    ("不要太活泼", "tone", "formal", "negative"),
    ("别太随意", "tone", "formal", "negative"),
    # tone: lively
    ("活泼一点", "tone", "lively", "positive"),
    ("活泼点", "tone", "lively", "positive"),
    ("活泼些", "tone", "lively", "positive"),
    ("轻松点", "tone", "lively", "positive"),
    ("幽默点", "tone", "lively", "positive"),

    # web_search: off
    ("以后别联网", "web_search", False, "negative"),
    ("别联网", "web_search", False, "negative"),
    ("不要联网", "web_search", False, "negative"),
    ("不用联网", "web_search", False, "negative"),
    ("不用搜了", "web_search", False, "negative"),
    ("别搜了", "web_search", False, "negative"),
    ("别查了", "web_search", False, "negative"),
    ("不用查了", "web_search", False, "negative"),
    # web_search: on
    ("多查点资料", "web_search", True, "positive"),
    ("多搜点资料", "web_search", True, "positive"),
    ("多查资料", "web_search", True, "positive"),
    ("多联网查", "web_search", True, "positive"),

    # language
    ("用英文", "language", "en", "positive"),
    ("用英语", "language", "en", "positive"),
    ("英文回答", "language", "en", "positive"),
    ("说英文", "language", "en", "positive"),
    ("用中文", "language", "zh", "positive"),
    ("中文回答", "language", "zh", "positive"),
    ("说中文", "language", "zh", "positive"),
    ("用汉语", "language", "zh", "positive"),

    # reminder_style: quiet
    ("提醒别太吵", "reminder_style", "quiet", "negative"),
    ("提醒小声点", "reminder_style", "quiet", "negative"),
    ("别太吵", "reminder_style", "quiet", "negative"),
    ("安静点", "reminder_style", "quiet", "negative"),
    ("安静些", "reminder_style", "quiet", "negative"),
    ("别老提醒", "reminder_style", "quiet", "negative"),
]


# ------------------------------------------------------------
# as_prompt 用的中文标签（纯文本，不含证据原文 / match / 哈希 / 轮次）
# ------------------------------------------------------------
_KEY_LABELS = {
    "doc_theme": "文档配色",
    "verbosity": "回答详略",
    "tone": "语气",
    "web_search": "联网搜索",
    "language": "回答语言",
    "reminder_style": "提醒方式",
}
_VALUE_LABELS = {
    ("verbosity", "concise"): "简洁",
    ("verbosity", "detailed"): "详细",
    ("verbosity", "normal"): "适中",
    ("tone", "formal"): "正式",
    ("tone", "lively"): "活泼",
    ("web_search", True): "开启",
    ("web_search", False): "关闭",
    ("language", "en"): "英文",
    ("language", "zh"): "中文",
    ("reminder_style", "quiet"): "安静",
}


def _text_digest(text: str) -> str:
    """原话指纹（等价于 trajectory.digest，但自包含、无额外依赖）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _confidence(text: str) -> float:
    """模糊词 0.5 / 显式喜好·以后都 0.9 / 「…点…些」及否定 0.7。"""
    if any(w in text for w in _VAGUE_MARKERS):
        return 0.5
    if any(w in text for w in _STRONG_MARKERS):
        return 0.9
    return 0.7


def extract_preferences(text: str) -> list:
    """纯函数：从一句中文里抽取偏好。

    返回 [{"key","value","polarity","confidence","match"}]，抽不到返回 []。
    polarity ∈ {"positive","negative"}；confidence ∈ [0,1]；match ≤20 字。
    同一句话里同一维度只保留最先命中的一条。
    """
    if not isinstance(text, str):
        return []
    text = text.strip()
    if not text:
        return []
    if len(text) > _MAX_TEXT:
        text = text[:_MAX_TEXT]

    conf = _confidence(text)
    found = {}

    # 正向主题（需带设计/偏好意图语境，且红色不在别名表里）
    if any(cue in text for cue in _STYLE_CUES):
        for alias, canon in _THEME_ALIASES:
            if alias in text:
                found["doc_theme"] = {
                    "key": "doc_theme", "value": canon,
                    "polarity": "positive", "confidence": conf,
                    "match": alias[:20],
                }
                break

    # 其余规则（含红色否定）；同一维度只取首个命中
    for trigger, key, value, polarity in _RULES:
        if key in found:
            continue
        if trigger in text:
            found[key] = {
                "key": key, "value": value, "polarity": polarity,
                "confidence": conf, "match": trigger[:20],
            }
    return list(found.values())


def _norm_ws(text: str) -> str:
    """去掉所有空白，只用于「引用是否真的出现在原话里」的比对。"""
    return re.sub(r"\s+", "", text or "")


def parse_llm_preferences(raw, turns, *, max_items: int = _MAX_LLM_ITEMS) -> list:
    """把模型返回的 JSON 校验成可落盘的偏好；任何一条不合格就丢掉。

    防幻觉的唯一硬手段：**每条偏好的 evidence 必须逐字出现在用户原话里**。
    比对前双方都去掉空白，容忍换行/空格的差异，但不容忍改字。
    返回 [{"key","value","polarity","confidence","match","evidence_text"}]。
    """
    if not isinstance(raw, str) or not raw.strip():
        return []
    text = raw.strip()
    if text.startswith("```"):                       # 容忍 ```json 包裹
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    if isinstance(data, dict):                       # 容忍 {"preferences": [...]}
        data = data.get("preferences") or data.get("items") or []
    if not isinstance(data, list):
        return []

    corpus = _norm_ws("".join(
        str(t.get("content") or "") for t in (turns or [])
        if isinstance(t, dict)))
    if not corpus:
        return []

    out, seen = [], set()
    for item in data:
        if len(out) >= max(1, int(max_items)):
            break
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip().lower()
        value = str(item.get("value") or "").strip()
        quote = str(item.get("evidence") or "").strip()
        if not _KEY_RE.match(key) or key in seen:
            continue
        if not value or len(value) > _MAX_LLM_VALUE:
            continue
        needle = _norm_ws(quote)
        if len(needle) < 4 or needle not in corpus:   # 引用太短或查无实据 → 丢
            continue
        seen.add(key)
        out.append({
            "key": key,
            "value": value,
            "polarity": "positive",
            "confidence": 0.72,
            "match": quote[:20],
            "evidence_text": quote,
        })
    return out


def _merge_items(items: dict, specs: list, *, source: str, source_turn: str,
                 max_n: int, now: float) -> list:
    """把一批偏好并进 items（原地修改），返回出现变化的 key 列表。

    规则层与大模型层共用这一处合并逻辑，避免两套「改主意」语义分叉：
    同 key 同值时只累加 hits / 证据；改主意则覆盖 value，但保留历史证据
    与 supersedes 链——「改过什么」本身也是信息。
    """
    changed = []
    ts_base = float(now or time.time())
    # 一批里每条都比上一条晚一点点，否则上限裁剪时「谁最老」判定不稳
    for it in items.values():
        if isinstance(it, dict):
            try:
                ts_base = max(ts_base, float(it.get("updated_at") or 0) + 1e-6)
            except (TypeError, ValueError):
                continue
    for idx, p in enumerate(specs):
        key = str(p.get("key") or "").strip()
        if not key:
            continue
        value = p.get("value")
        now = round(ts_base + idx * 1e-6, 6)
        raw = str(p.get("evidence_text") or "")
        ev = {
            "text_digest": _text_digest(raw) if raw else "",
            "match": str(p.get("match") or "")[:20],
            "turn_id": str(source_turn or "")[:64],
            "ts": now,
        }
        cur = items.get(key)
        same = (isinstance(cur, dict)
                and cur.get("value") == value
                and cur.get("polarity") == p.get("polarity"))
        if same:
            cur["hits"] = int(cur.get("hits") or 0) + 1
            history = list(cur.get("evidence") or [])
            history.append(ev)
            cur["evidence"] = history[-_MAX_EVIDENCE:]
            cur["confidence"] = max(float(cur.get("confidence") or 0),
                                    float(p.get("confidence") or 0))
            cur["updated_at"] = now
            items[key] = cur
        else:
            prev, history, created = None, [], now
            if isinstance(cur, dict):
                prev = {
                    "value": cur.get("value"),
                    "polarity": cur.get("polarity"),
                    "confidence": cur.get("confidence"),
                    "updated_at": cur.get("updated_at"),
                    "supersedes": cur.get("supersedes"),
                }
                history = list(cur.get("evidence") or [])
                created = cur.get("created_at") or now
            history.append(ev)
            items[key] = {
                "key": key,
                "value": value,
                "polarity": p.get("polarity") or "positive",
                "confidence": float(p.get("confidence") or 0.7),
                "source": source,
                "evidence": history[-_MAX_EVIDENCE:],
                "hits": 1,
                "created_at": created,
                "updated_at": now,
                "supersedes": prev,
            }
        changed.append(key)

    # 上限：超出按 updated_at 丢最老的（刚写的永远最新，不会被丢）
    while len(items) > max(1, int(max_n)):
        oldest = min(
            items,
            key=lambda k: float(items[k].get("updated_at") or 0)
            if isinstance(items[k], dict) else 0.0)
        del items[oldest]
    return changed


def _strength(item: dict):
    """偏好「强度」排序键：置信度 > 命中次数 > 新鲜度。"""
    return (
        float(item.get("confidence") or 0),
        int(item.get("hits") or 0),
        float(item.get("updated_at") or 0),
    )


def _describe(item: dict) -> str:
    """把一条偏好翻成系统提示词里的一行纯文本（不含任何证据原文）。"""
    key = item.get("key")
    value = item.get("value")
    if key not in _KEY_LABELS:
        # 大模型抽出的开放式偏好：value 本身就是一句人话，
        # 再套「标签：值」反而变成「work_style：喜欢先看结论」这种机翻味。
        text = str(value or "").strip()
        return text[:_MAX_LLM_VALUE]
    label = _KEY_LABELS.get(key, str(key))
    if key == "doc_theme":
        val = ("不用" + str(value)) if item.get("polarity") == "negative" \
            else str(value)
        return "%s：%s" % (label, val)
    try:
        vlabel = _VALUE_LABELS.get((key, value))
    except TypeError:                       # value 不可哈希时走兜底
        vlabel = None
    if vlabel is None:
        if isinstance(value, bool):
            vlabel = "开启" if value else "关闭"
        else:
            vlabel = str(value)
    return "%s：%s" % (label, vlabel)


class PreferenceStore:
    """偏好存储：observe 落盘，行为方法实时从当前落盘状态读。"""

    def __init__(self, path: str = None):
        if path:
            self.path = str(path)
        else:
            # 惰性导入：只有走默认路径时才碰真实 data/；path 注入时全程不碰
            from framework.utils import app_data_dir
            self.path = os.path.join(app_data_dir(), "preferences.json")
        self._lock = threading.Lock()

    # ---------- 底层：落盘结构读写 ----------
    def _max_items(self) -> int:
        from framework import config
        try:
            n = int(getattr(config, "MAX_PREFERENCES", 40))
        except (TypeError, ValueError):
            n = 40
        return max(1, n)

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {"schema": 1, "items": {}}
        if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
            return {"schema": 1, "items": {}}
        return {"schema": 1, "items": data["items"]}

    def _write(self, data: dict):
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)          # 原子替换，读侧永远看到完整文件

    @staticmethod
    def _now(items: dict) -> float:
        """严格递增时间戳：保证上限裁剪时「谁最老」可判定。"""
        t = time.time()
        for it in items.values():
            if not isinstance(it, dict):
                continue
            try:
                t = max(t, float(it.get("updated_at") or 0) + 1e-6)
            except (TypeError, ValueError):
                continue
        return round(t, 6)

    # ---------- 写入 ----------
    def observe(self, text: str, *, source_turn: str = None) -> list:
        """抽取 + 落盘，返回本句导致新增/更新的偏好（item dict 列表）。"""
        prefs = extract_preferences(text)
        if not prefs:
            return []
        raw = text if isinstance(text, str) else ""
        for p in prefs:
            p["evidence_text"] = raw          # 规则层：整句就是它的证据
        with self._lock:
            data = self._read()
            items = data["items"]
            changed = _merge_items(
                items, prefs, source="rule", source_turn=source_turn or "",
                max_n=self._max_items(), now=self._now(items))
            data["items"] = items
            self._write(data)
            return [dict(items[k]) for k in changed if k in items]

    def observe_batch(self, turns, llm_fn, *, source_turn: str = None) -> list:
        """每 N 轮把最近对话交给大模型，补一层规则抓不到的开放式偏好。

        `turns` 是 [{"role","content"}, ...]，`llm_fn(turns) -> str` 返回模型
        原文。解析后每条都要过原话引用校验——模型编的、查无实据的一律丢。
        任何异常都吞掉返回 []：偏好学习失败绝不能影响主对话。
        """
        if not turns or llm_fn is None:
            return []
        try:
            raw = llm_fn(turns)
        except Exception:                     # noqa: BLE001
            return []
        specs = parse_llm_preferences(raw, turns)
        if not specs:
            return []
        with self._lock:
            data = self._read()
            items = data["items"]
            changed = _merge_items(
                items, specs, source="llm", source_turn=source_turn or "",
                max_n=self._max_items(), now=self._now(items))
            data["items"] = items
            self._write(data)
            return [dict(items[k]) for k in changed if k in items]

    # ---------- 读取（每次都从当前落盘状态读） ----------
    def get(self, key: str, default=None):
        """返回该维度完整偏好记录（含 value/evidence/hits/supersedes 等），
        无此偏好时返回 default。"""
        item = self._read()["items"].get(key)
        return dict(item) if isinstance(item, dict) else default

    def all(self) -> list:
        items = self._read()["items"]
        out = [dict(v) for v in items.values() if isinstance(v, dict)]
        out.sort(key=lambda d: float(d.get("updated_at") or 0), reverse=True)
        return out

    def as_prompt(self) -> str:
        """注入系统提示词的短片段：最强前 6 条、纯文本、不含证据原文；
        无偏好返回 ""。"""
        items = self.all()
        if not items:
            return ""
        items.sort(key=_strength, reverse=True)
        lines = [_describe(it) for it in items[:6]]
        lines = [ln for ln in lines if ln]
        if not lines:
            return ""
        return "【用户偏好】\n" + "\n".join("- " + ln for ln in lines)

    def doc_style(self) -> dict:
        """可直接合并进 spec["style"]，字段均为 resolve_style() 能识别的。
        没有正向主题偏好（或否定颜色）时返回 {}（不覆盖内置样式）。"""
        item = self.get("doc_theme")
        if not item or item.get("polarity") == "negative":
            return {}
        color = _THEME_COLOR.get(item.get("value"))
        if not color:
            return {}
        return {"title_color": color, "accent_color": color}

    def verbosity(self) -> str:
        item = self.get("verbosity")
        value = item.get("value") if item else None
        return value if value in ("concise", "detailed") else "normal"

    def allow_web_search(self) -> bool:
        item = self.get("web_search")
        if not item:
            return True                       # 无偏好默认允许联网
        value = item.get("value")
        if value is None:
            return True
        return bool(value)

    # ---------- 删除 ----------
    def forget(self, key: str) -> bool:
        with self._lock:
            data = self._read()
            if key not in data["items"]:
                return False
            del data["items"][key]
            self._write(data)
            return True

    def clear(self):
        with self._lock:
            self._write({"schema": 1, "items": {}})
