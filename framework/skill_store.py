# -*- coding: utf-8 -*-
"""技能库：把"做过并且做成了"的一串动作存成可重放技能。

一条设计红线，它决定了这个模块所有其他形状：

**重放一个技能必须是零模型调用的。** 所以只有"参数完全由用户的话决定"的
动作才允许进技能（`SKILL_SAFE`）。带 `create_doc` 的轨迹一律不提议保存——
那份 PPT 的内容是我上次替用户写的，原样重放等于把上个月的报告再发一遍，
那不叫学会，那叫复读。同理，凡是抽不出槽位值的步骤也不许瞎补上次的值。

于是"一次学会"这件事在这里是可证的：同一条技能 + 同一组槽位值，重放出来
的动作序列逐字节相同（测试里跑 20 遍断言相等）。跑不出这个保证的功能宁可不放进来。

字面量抽成槽位靠一张确定表（哪个参数名算哪类槽），不靠模型猜：
`D:/下载/发票.csv` 是路径、`三月` 是日期、`竞品动态` 是主题，规则写死、可单测。
"""

import json
import os
import re
import time

from framework import config
from framework.utils import app_data_dir, logger

# 允许进技能的动作：参数是"用户会说的话"或"用户给的文件"，重放不依赖模型再创作
SKILL_SAFE = {
    "set_volume", "open_app", "web_search", "read_doc", "analyze_data",
    "todo_add", "todo_done", "remind", "remember", "screenshot",
}

# 禁止进技能的动作 + 一句为什么（拒收时如实说给用户听）
SKILL_REFUSED = {
    "create_doc": "那份文档的内容是我上次替你写的，原样重放等于把旧报告再发一遍",
    "show_plan": "计划是当时那件事的计划，不该被当成固定流程",
    "run_workflow": "工作流会另起一整轮任务，不该套进技能里",
    "shutdown": "关机/重启/锁屏永远不进技能：重放一次就动一次真机器",
    "restart": "关机/重启/锁屏永远不进技能：重放一次就动一次真机器",
    "lock": "关机/重启/锁屏永远不进技能：重放一次就动一次真机器",
    "cancel_shutdown": "它只在有一件事排着的时候才有意义",
}

# 表演性动作：进技能只会让重放慢得像卡了，一律剔除
THEATRE = {"think", "idle", "jump", "move_to", "show_bubble", "show_card",
           "search_app", "todo_list", "work_log", "see_screen"}

# 参数名 → 槽位名（抽槽只认这张表，不认"看起来像不像"）
SLOT_BY_KEY = {
    "path": "路径", "file": "路径", "save_path": "路径", "filename": "路径",
    "app": "应用", "query": "主题", "text": "主题", "fact": "主题",
    "title": "主题", "name": "主题",
}

# 槽位类型：值本身长得像日期时，无论参数名是什么都优先按日期抽
_DATE_RE = re.compile(
    r"(今天|明天|后天|昨天|本周|这周|本月|这个月|上周|上个月"
    r"|星期[一二三四五六日天]|周[一二三四五六日天]"
    r"|[一二三四五六七八九十]{1,3}月"
    r"|\d{2,4}[-/年]\d{1,2}[-/月]?(\d{1,2}日?)?)")

# 值里出现这些就当路径（参数名不在表里时的兜底识别）
_PATH_MARKS = (".pptx", ".docx", ".xlsx", ".csv", ".pdf", ".png", ".jpg",
               ".md", ".txt", ".zip")

# 从用户新说的一句话里抠槽位值用的确定表：每类槽一组正则，第 1 组是值本身。
# \x00 是"这段已经被别的槽用走"的掩码占位符，所有字符类都要排除它。
_MINE_RES = {
    "路径": (re.compile(r"([A-Za-z]:[\\/][^\s，。；;\x00]+)"),
             re.compile(r"([^\s，。；;\x00/]+[\\/][^\s，。；;\x00]+)"),
             re.compile(r"([^\s，。；;\x00]+\.(?:csv|xlsx|docx|pptx|txt|md|"
                        r"pdf|png|jpe?g|zip))")),
    "应用": (re.compile(r"(?:打开|启动|运行)\s*"
                        r"([^\s，。；;\x00了]+)"),),
    "日期": (_DATE_RE,),
    "主题": (re.compile(r"[「“\"']([^」”\"'\x00]{2,30})[」”\"']"),
             re.compile(r"(?:关于|搜索|查查|查一下|查阅|看看|看下|查)\s*"
                        r"[一]?[下次遍]*\s*"
                        r"([^\s，。；;\x00的「」]{2,20})")),
}

# 抠值顺序：越具体越先抠（路径能吃掉"三月"这种文件名，所以它必须比日期先跑）
_MINE_ORDER = {"路径": 0, "应用": 1, "日期": 2, "主题": 3}

# 每个动作一个动词，用来拼"这条技能是干什么的"的人话名字
_VERB = {
    "set_volume": "调音量", "open_app": "开程序", "web_search": "联网查",
    "read_doc": "读文档", "analyze_data": "分析表格", "todo_add": "加待办",
    "todo_done": "销待办", "remind": "设提醒", "remember": "记一件事",
    "screenshot": "截个屏",
}


def _looks_like_path(value: str) -> bool:
    low = value.lower()
    return ("\\" in value or "/" in value or bool(re.match(r"^[A-Za-z]:", value))
            or any(m in low for m in _PATH_MARKS))


def slot_key_for(param: str, value) -> str:
    """这个参数值该抽成哪类槽位？返回 "" 表示原样保留（不当参数）。

    整段就是一句时间才当日期：`query="三月的发票"` 抽成日期会连带把"发票"
    丢掉，那种值应该走主题。
    """
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip()
    if _DATE_RE.fullmatch(text):
        return "日期"
    if SLOT_BY_KEY.get(param):
        return SLOT_BY_KEY[param]
    if _looks_like_path(text):
        return "路径"
    return ""


def skillable(actions) -> tuple:
    """这串动作配不配当技能 → (配, 原因)。不配的时候原因是要念给用户听的。"""
    tool_steps = [a for a in actions if a.get("action") not in THEATRE]
    if not tool_steps:
        return False, "这一轮没有实质操作，不用记"
    for a in tool_steps:
        name = a.get("action")
        if name in SKILL_REFUSED:
            return False, SKILL_REFUSED[name]
        if name not in SKILL_SAFE:
            return False, f"「{name}」这一步重放起来不可靠，我不存"
    if len(tool_steps) < config.SKILL_MIN_STEPS:
        return False, "就一步，不值得记"
    if len(tool_steps) > config.SKILL_MAX_STEPS:
        return False, (f"这一轮有 {len(tool_steps)} 步，太杂了——"
                       "技能是固定流程，不是一次性的大工程")
    return True, ""


def abstract(actions) -> dict:
    """把一串执行过的动作抽成技能骨架：字面量换成 {槽位}，同值合并同槽。"""
    steps, slots, by_value = [], [], {}
    for a in actions:
        name = a.get("action")
        if name in THEATRE or name not in SKILL_SAFE:
            continue
        step = {"action": name}
        for key, value in a.items():
            if key == "action":
                continue
            slot = slot_key_for(key, value)
            if not slot:
                step[key] = value
                continue
            text = value.strip()
            if text not in by_value:
                base = slot
                i = 2
                while slot in slots:
                    slot = f"{base}{i}"
                    i += 1
                slots.append(slot)
                by_value[text] = slot
            step[key] = "{" + by_value[text] + "}"
        steps.append(step)

    uniq = []
    for s in steps:
        if s not in uniq:
            uniq.append(s)
    # values 留着当"上次是这个值"的默认提示，重放时不自动采用（见 fill）
    defaults = {slot: text for text, slot in by_value.items()}
    return {"steps": uniq, "slots": slots, "values": defaults}


def signature(skill: dict) -> str:
    """技能骨架的身份：步骤序列一样就是同一条技能（不重复存）。"""
    return json.dumps(skill.get("steps", []), ensure_ascii=False,
                      sort_keys=True)


def label(steps) -> str:
    """技能名：按动词串起来，够用户认出是哪件事就行。"""
    verbs = []
    for s in steps:
        v = _VERB.get(s.get("action"), s.get("action"))
        if v and v not in verbs:
            verbs.append(v)
    text = "·".join(verbs) or "技能"
    return text[:config.SKILL_NAME_MAX]


def name_for(user_text: str, steps) -> str:
    """优先用用户自己的说法当名字（下次他还这么说话就能命中），否则用动词串。"""
    text = re.sub(r"[，。！？、,.!?\s]+", "", (user_text or "").strip())
    if len(text) >= 4:
        return text[:config.SKILL_NAME_MAX]
    return label(steps)


def fill(skill: dict, values: dict) -> tuple:
    """技能 + 槽位值 → 具体动作序列 → (steps, 缺哪些槽)。

    缺槽就整条不跑（steps 给空），让上层去问一句：绝不拿上次的值补，那条
    待办可能是上周的事；也绝不"能跑几步算几步"，静默少跑一步比不跑更难发现。
    """
    wanted = set()
    for step in skill.get("steps", []):
        for value in step.values():
            if isinstance(value, str) and value.startswith("{") \
                    and value.endswith("}"):
                wanted.add(value[1:-1])
    missing = [s for s in sorted(wanted)
               if not str((values or {}).get(s, "")).strip()]
    if missing:
        return [], missing

    steps = []
    for step in skill.get("steps", []):
        out = {}
        for key, value in step.items():
            if isinstance(value, str) and value.startswith("{") \
                    and value.endswith("}"):
                out[key] = str(values[value[1:-1]]).strip()
            else:
                out[key] = value
        steps.append(out)
    return steps, []


def match_values(user_text: str, slots) -> dict:
    """从用户新说的这句话里把槽位值抠出来。抠不到就不给，不猜。

    抠的顺序是"越具体越先抠"，抠走的那段立刻涂掉，后面的槽不能再读它。
    不这么做的话 `D:/报表/三月.csv` 会被日期槽读走一个"三月"——路径长得像
    日期，但那是文件名，不是时间。同类槽（路径/路径2）按出现顺序发不同的
    值，只有一份路径时第二个槽留空去问，绝不把同一个值塞进两个槽。
    """
    text = (user_text or "").strip()
    if not text or not slots:
        return {}

    bases = []
    for slot in slots:
        base = re.sub(r"\d+$", "", slot)      # 主题2 / 路径3 → 主题 / 路径
        if base in _MINE_RES and base not in bases:
            bases.append(base)
    bases.sort(key=lambda b: _MINE_ORDER.get(b, 99))

    pool = {}
    masked = text
    for base in bases:
        taken = []
        hits = []
        for pat in _MINE_RES[base]:
            for m in pat.finditer(masked):
                start = m.start(1) if m.lastindex else m.start()
                stop = m.end(1) if m.lastindex else m.end()
                if any(start < s2 and stop > s1 for s1, s2 in taken):
                    continue
                taken.append((m.start(), m.end()))
                hits.append(masked[start:stop].strip())
        pool[base] = hits
        for a, b in taken:                     # 涂掉：这一段已经被这个槽用走了
            masked = masked[:a] + "\x00" * (b - a) + masked[b:]

    found = {}
    for slot in slots:
        base = re.sub(r"\d+$", "", slot)
        if slot in found or base not in pool or not pool[base]:
            continue
        value = pool[base].pop(0)
        if value:
            found[slot] = value
    return found


class SkillStore:
    """技能持久化（JSON，重启不丢）。坏文件不炸主程序，只当没有。"""

    def __init__(self, path: str = None):
        self.path = path or os.path.join(app_data_dir(), config.SKILLS_FILE)
        self.items = []
        self.load()

    # ------------------------------------------------------------
    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.items = [s for s in data.get("skills", [])
                          if isinstance(s, dict) and s.get("steps")]
        except FileNotFoundError:
            self.items = []
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning(f"[Skill] 技能文件读不出，按空处理: {e}")
            self.items = []

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"skills": self.items}, f, ensure_ascii=False,
                          indent=2)
            return True
        except OSError as e:
            logger.warning(f"[Skill] 技能存不下去: {e}")
            return False

    # ------------------------------------------------------------
    def all(self) -> list:
        return list(self.items)

    def add(self, user_text: str, actions) -> tuple:
        """把这轮动作存成技能 → (技能, 失败原因)。"""
        ok, why = skillable(actions)
        if not ok:
            return None, why
        skill = abstract(actions)
        if not skill["steps"]:
            return None, "抽完参数这轮什么都不剩了，不存"
        if len(skill["slots"]) > config.SKILL_SLOT_MAX:
            return None, (f"这一轮要现填 {len(skill['slots'])} 个参数，"
                          "槽位多到这份上说明流程根本不固定，我不存")
        sig = signature(skill)
        if any(signature(s) == sig for s in self.items):
            return None, "这条我已经会了，不用再存一遍"
        name = name_for(user_text, skill["steps"])
        base, i = name, 2
        # 占位判断必须逐字比：get() 是模糊查找，会把「查竞品动态2」也算成被前一名占着
        while any(s["name"] == name for s in self.items):
            name = f"{base}{i}"
            i += 1
            if i > len(self.items) + 2:
                return None, "重名太多，先删几条"
        item = {"name": name, "slots": skill["slots"], "steps": skill["steps"],
                "values": skill["values"],
                "created": time.time(), "used": 0, "source": user_text or ""}
        self.items.append(item)
        if len(self.items) > config.MAX_SKILLS:
            self.items = self.items[-config.MAX_SKILLS:]
        self.save()
        logger.info(f"[Skill] 学会技能: {name} ({len(item['steps'])} 步)")
        return item, ""

    def get(self, name: str):
        """按名字找技能（整名命中优先，否则取最长能被包含的那个，避免「查」命中一堆）。"""
        name = (name or "").strip()
        if not name:
            return None
        for s in self.items:
            if s["name"] == name:
                return s
        hits = [s for s in self.items
                if s["name"] in name or name in s["name"]]
        if not hits:
            return None
        return max(hits, key=lambda s: len(os.path.commonprefix(
            [s["name"], name])))

    def remove(self, name: str):
        s = self.get(name)
        if not s:
            return None
        self.items.remove(s)
        self.save()
        return s

    def mark_used(self, skill: dict):
        skill["used"] = int(skill.get("used", 0)) + 1
        self.save()

    def catalog_text(self) -> str:
        """注入提示词的技能清单：让模型知道 run_skill 有名可挑、缺什么槽要问。"""
        if not self.items:
            return ""
        lines = []
        for s in self.items:
            slots = f"（需要：{'、'.join(s['slots'])}）" if s.get("slots") else ""
            lines.append(f"- {s['name']}{slots}")
        return ("\n\n【已学会的技能】下面是我做成功过、存下来的流程。"
                "用户的话对得上就用 run_skill 直接重放（不再重新规划），"
                "括号里标了需要哪些参数，话里没提到就先问一句再跑：\n"
                + "\n".join(lines))
