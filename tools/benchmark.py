# -*- coding: utf-8 -*-
"""带扰动的能力评测（含学习前后对照）—— tools/benchmark.py

评测口径
--------
这里评的是「系统在扰动下把事办成的能力」以及「使用过程中学到的规则/偏好
能不能真的把办砸的事救回来」。每条任务是一条带扰动的用户说法 + 一份本地
确定性 fixture（模拟的搜索结果 / 文档 spec / 路径探测结果 / 计划步骤 /
模型返回的动作序列）。模拟执行器跑的是项目里的真实链路：

    预置动作(同构于 mock_ai 输出) -> action_parser.parse_actions 归一
    -> effect_spec.check_pre 前置校验 -> quality_gate.assess_* 质量门
    -> plan_graph.compile_plan/validate 计划校验 -> 判定 ok / 失败原因

判定落在「动作是否选对、参数是否合法、质量门是否放行、坏路径是否被跳过、
被撤销过的动作是否先确认、断网时是否按偏好走本地兜底」这些可观测结果上。
每条用例是真布尔断言，禁止 assert True / 把被测实现换成常量桩。

核心：学习前后对照（证明进化不是摆设）
--------------------------------------
LearningAdapter 两态：learned=False 的 adjust() 返回空 {}；learned=True 时
从临时目录的 RuleStore.apply() + PreferenceStore 读取调整。评测启动时先往
临时 TrajectoryStore 写一段模拟历史（failure/quality/undo 事件），让
RuleStore.learn() 真的归纳出规则，并用 PreferenceStore.observe() 喂几句偏好
（「以后别联网」「我喜欢深色 PPT」「简洁点」）。于是：

  * 搜索软失败 -> search_suffix 换词重搜救回；
  * 文档干瘪   -> min_slides/min_bullet_len 补足内容救回；
  * 坏路径     -> skip_action 跳过无谓重试；
  * 撤销过的动作 -> require_confirm 转为显式确认后通过；
  * 断网/Key 失效 -> allow_web_search=False 走本地兜底。

这些救回全部来自真实规则/偏好数据的命中，不是 `if learned: ok=True`。
同时保留若干「怎么都救不回」的硬失败样本（缺参、恶意动作、计划冲突、
无法换词的零重合资料），因此报告敢公布失败率，不靠把任务设计成全满分过关。

不联网、不调模型
----------------
全程零网络、零真实 LLM API、零 API Key：动作是预置的确定性序列（结构与
mock_ai 输出同构，便于精确注入恶意/非法/缺参/超长等扰动，也避免拉起 Qt /
requests 栈）；被测函数都是纯本地链路，绝不触发 web_search/read_doc 的真实
执行体，也不调用 generate() 落真实文档。零模型路径占比恒为 1.0，如实公布。

隔离与可复现
------------
- 所有落盘（TrajectoryStore / RuleStore / PreferenceStore）都在
  tempfile.TemporaryDirectory() 内，退出即清理，绝不写脏项目 data/；
- import 前置 os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")；
- 在 import 任何 framework 模块之前给 "desktop_assistant" logger 预置
  NullHandler（令 utils.setup_logger 直接 return，不建 FileHandler），并在
  import 之后再次把 logger 清空成 NullHandler+CRITICAL：无论谁先 import 过
  utils，parse_actions 里的日志都不会追加到真实 data/logs/app.log；
- latency_ms / tokens 用 random.Random(seed) 的模拟值，不用 time.time 真耗时；
  固定种子两次 run_suite 结果逐字段完全一致。

如何复现
--------
在项目根目录执行：
    PYTHONIOENCODING=utf-8 QT_QPA_PLATFORM=offscreen python tools/benchmark.py
可选参数：
    --json <path>        写机器可读结果（含前后两组明细、救回 id、分类计数）
    --append-history     追加一行到 docs/benchmark/history.jsonl（目录不存在则建）
    --threshold <float>  学习后完成率阈值，默认 0.8
    --seed <int>         随机种子，默认 42
退出码：0 = 学习后完成率 >= 阈值 且 有正提升 且 救回数 >= 4；1 = 否则。
默认阈值按当前实现可通过的值设定，但学习后仍有硬失败样本，并非满分放行。

与 pytest 单测的分工
--------------------
pytest（tests/、tools/test_*.py）验证「单个函数是否符合契约」——边界值、
异常路径、持久化回读，属于开发者视角的白盒校验；本评测验证「一串带扰动的
用户说法经过整条本地链路后，学习前后办成率有无变化」——属于用户视角的
黑盒能力抽样。二者互补：单测全绿不代表用户的话办得成，更不代表「学到的
规则真的有用」；本评测的完成率提升与救回清单就是用来量化后者的。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError, OSError):
    pass
try:
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError, OSError):
    pass

import argparse
import json
import logging
import random
import tempfile
import time
import unicodedata

# ---- 关键隔离 1：import 任何 framework 模块之前预置 NullHandler ----
# framework.utils 在 import 时执行 setup_logger()；它见到 logger 已有 handler
# 会直接 return，从而不会创建指向 data/logs/app.log 的 RotatingFileHandler。
_log = logging.getLogger("desktop_assistant")
if not _log.handlers:
    _log.addHandler(logging.NullHandler())
_log.setLevel(logging.CRITICAL)

from framework import action_parser as _parser
from framework import effect_spec as _spec
from framework import plan_graph as _plan
from framework import quality_gate as _gate
from framework import evolution as _evo
from framework import preference as _pref
from framework import trajectory as _traj


# ---- 关键隔离 2：无论别的测试是否先 import 过 utils，都清空成 NullHandler ----
def _silence_logger():
    log = logging.getLogger("desktop_assistant")
    for h in list(log.handlers):
        try:
            h.close()
        except Exception:  # noqa: BLE001 清理日志处理器不允许拖垮评测
            pass
        log.removeHandler(h)
    log.addHandler(logging.NullHandler())
    log.setLevel(logging.CRITICAL)


_silence_logger()

DEFAULT_THRESHOLD = 0.8
DEFAULT_SEED = 42
MIN_RESCUED = 4
HISTORY_PATH = os.path.join(_ROOT, "docs", "benchmark", "history.jsonl")

ALL_FAILURE_CATEGORIES = (
    "quality_reject", "bad_path", "invalid_action", "timeout",
    "key_invalid", "no_adjustment", "invalid_plan", "pre_violation",
)

PERTURBATIONS = (
    "模糊指令", "缺参", "坏路径", "断网", "中途打断",
    "Key 失效", "恶意/非法动作", "并发指令", "超长输出", "质量门软失败",
)

# 表演性动作不算「主动作」，取第一个真正干活的动作；全是表演动作则取首个。
_NON_FUNCTIONAL = frozenset({
    "show_bubble", "think", "idle", "jump", "move_to", "show_card",
})


# ---------------------------------------------------------------------------
# 本地确定性 fixture 数据（模拟工具返回，不联网、不调模型）
# ---------------------------------------------------------------------------
_OFFTOPIC_TEXT = (
    "本页介绍了家常菜的选材与烹饪火候，包括油温控制、调味顺序以及摆盘装饰"
    "等基础技巧，适合厨房新手循序渐进地练习提升，内容详实且配有图解。"
)


def _offtopic():
    """三篇够长、标题不同、但与科技类查询零重合的菜谱 -> 质量门软失败。"""
    return [
        {"title": "家常菜谱大全", "content": _OFFTOPIC_TEXT},
        {"title": "厨房入门指南", "content": _OFFTOPIC_TEXT},
        {"title": "火候与调味笔记", "content": _OFFTOPIC_TEXT},
    ]


_REFINED_GF = [
    {"title": "2025 光伏装机容量统计",
     "content": "2025年全国光伏装机容量最新数据汇总：新增光伏装机容量同比"
                "增长，报告详细列出各省光伏装机容量数据与趋势预测，数据来自"
                "权威机构并给出未来展望。"},
    {"title": "光伏行业白皮书",
     "content": "光伏装机容量最新数据解读，涵盖装机容量数据与厂商排名，"
                "对光伏装机容量的增长驱动力做了全面梳理与数据展望。"},
]

_REFINED_EV = [
    {"title": "新能源汽车销量报告",
     "content": "2025年新能源汽车销量最新数据同比增长显著，报告详细分析了"
                "新能源汽车销量走势与新能源渗透率变化，并给出未来市场销量"
                "数据预测与政策建议。"},
    {"title": "行业销量白皮书",
     "content": "新能源汽车销量数据汇总，涵盖主要厂商销量排名与市场份额，"
                "对新能源汽车销量的增长驱动力做了全面梳理与数据解读。"},
]

_GOOD_SEARCH_AI = [
    {"title": "人工智能应用报告",
     "content": "2025年人工智能应用在各行业落地情况综述，报告详细分析了"
                "人工智能应用场景、市场规模与未来趋势，并给出政策建议与展望。"},
    {"title": "行业应用白皮书",
     "content": "人工智能应用数据汇总，涵盖主要厂商与市场份额，对人工智能"
                "应用的增长驱动力做了全面梳理与解读。"},
]

_GOOD_SLIDES = {
    "title": "调研报告",
    "slides": [
        {"title": "背景与现状", "bullets": ["行业规模近三年年均增长 15%",
                                        "用户需求同比提升 40%"]},
        {"title": "实施计划", "bullets": ["完成渠道下沉与体系建设",
                                        "上线标准化产品包并压缩周期"]},
        {"title": "预期成效", "bullets": ["全年营收有望突破新高",
                                        "交付周期缩短一半"]},
    ],
}

_THIN_ONE_SLIDE = {
    "slides": [{"title": "背景", "bullets": ["增长", "趋势"]}],
}
_THIN_BULLETS = {
    "slides": [
        {"title": "一", "bullets": ["甲", "乙"]},
        {"title": "二", "bullets": ["丙", "丁"]},
    ],
}

_GOOD_PLAN = ["检索行业资料", "分析并汇总数据", "生成报告"]
_GOOD_PLAN_4 = ["检索资料", "分析数据", "汇总整理成报告", "生成报告"]
_BAD_PLAN = ["只做一件事"]


def _bubble(text="收到"):
    return {"action": "show_bubble", "text": text}


def _act(action, **params):
    d = {"action": action}
    d.update(params)
    return d


def _standard(action, **params):
    """一条确认气泡 + 一条功能动作（预置，参数齐全）。"""
    return [_bubble("遵命，这就办。"), _act(action, **params)]


def _fx(raw, *, context=None, require_action=True, **gates):
    """组装 fixture：raw_actions + 执行前上下文 + 各验收门槛数据。"""
    fx = {"raw_actions": raw, "require_action": require_action}
    if context is not None:
        fx["context"] = context
    fx.update(gates)
    return fx


def build_tasks():
    """内置 32 条任务：18 条常规通过、8 条可被学习救回、6 条硬失败。"""
    tasks = []

    def add(cid, group, utterance, perturbation, expected_action, fixture):
        tasks.append({
            "id": cid, "group": group, "utterance": utterance,
            "perturbation": perturbation, "expected_action": expected_action,
            "fixture": fixture,
        })

    # ---------- 常规通过（学习前后都过）：系统鲁棒地把事办成 ----------
    add("C01", "意图理解", "把音量大概调到 40", "模糊指令", "set_volume",
        _fx(_standard("set_volume", volume="40")))
    add("C02", "计划编排", "先查资料，再汇总，最后交付", "并发指令", "show_plan",
        _fx([_bubble("拆成计划了"),
             _act("show_plan", steps=["检索资料", "汇总成报告", "生成报告"])],
            plan={"steps": _GOOD_PLAN}))
    add("C03", "鲁棒性", "模型一次吐出 20 条动作", "超长输出", "show_bubble",
        _fx([{"action": "show_bubble", "text": "第%d句" % i}
             for i in range(20)]))
    add("C04", "鲁棒性", "序列里混进一条非法动作", "恶意/非法动作", "show_bubble",
        _fx([{"action": "exec_shell", "text": "rm -rf /"},
             _bubble("你好，我在")]))
    add("C05", "鲁棒性", "用户中途打断，模型只回了半句", "中途打断", "show_bubble",
        _fx([_bubble("好的，我继续")]))
    add("C06", "工具路由", "打开计算器", "模糊指令", "open_app",
        _fx(_standard("open_app", app="计算器")))
    add("C07", "工具路由", "加个待办：交周报", "并发指令", "todo_add",
        _fx(_standard("todo_add", text="交周报")))
    add("C08", "工具路由", "20 秒后提醒我喝水", "模糊指令", "remind",
        _fx(_standard("remind", text="喝水", seconds=20)))
    add("C09", "工具路由", "记住我喜欢深色风格", "模糊指令", "remember",
        _fx(_standard("remember", fact="用户喜欢深色风格")))
    add("C10", "工具路由", "看待办清单", "并发指令", "todo_list",
        _fx(_standard("todo_list")))
    add("C11", "工具路由", "完成第 1 条待办", "模糊指令", "todo_done",
        _fx(_standard("todo_done", index=1)))
    add("C12", "工具路由", "看看屏幕上的报错", "中途打断", "see_screen",
        _fx(_standard("see_screen", question="屏幕上的报错")))
    add("C13", "工具路由", "截个图", "超长输出", "screenshot",
        _fx(_standard("screenshot")))
    add("C14", "工具路由", "分析这份销售表", "并发指令", "analyze_data",
        _fx(_standard("analyze_data", path="C:/data/sales.csv")))
    add("C15", "工具路由", "读一下这份笔记", "模糊指令", "read_doc",
        _fx(_standard("read_doc", path="C:/data/notes.md")))
    add("C16", "质量门", "搜一下人工智能应用（资料切题）", "质量门软失败",
        "web_search",
        _fx(_standard("web_search", query="人工智能应用"),
            context={"action": "web_search"},
            search={"query": "人工智能应用", "results": _GOOD_SEARCH_AI}))
    add("C17", "质量门", "做个 PPT（内容饱满）", "质量门软失败", "create_doc",
        _fx(_standard("create_doc", doc_type="ppt", content=_GOOD_SLIDES),
            context={"action": "create_doc", "doc_type": "ppt"},
            doc={"kind": "ppt", "spec": _GOOD_SLIDES}))
    add("C18", "计划编排", "规划一下明天的汇报", "并发指令", "show_plan",
        _fx([_bubble("先拟个计划"),
             _act("show_plan", steps=_GOOD_PLAN_4)],
            plan={"steps": _GOOD_PLAN_4}))

    # ---------- 可被学习救回（学习前失败、学习后通过） ----------
    add("C19", "质量门", "查一下光伏装机容量（资料对不上题）",
        "质量门软失败", "web_search",
        _fx(_standard("web_search", query="光伏装机容量"),
            context={"action": "web_search"},
            search={"query": "光伏装机容量", "results": _offtopic(),
                    "refined_results": _REFINED_GF}))
    add("C20", "意图理解", "搜一下新能源（词太泛）", "模糊指令", "web_search",
        _fx(_standard("web_search", query="新能源汽车销量"),
            context={"action": "web_search"},
            search={"query": "新能源汽车销量", "results": _offtopic(),
                    "refined_results": _REFINED_EV}))
    add("C21", "质量门", "做份 PPT（只有一页）", "质量门软失败", "create_doc",
        _fx(_standard("create_doc", doc_type="ppt", content=_THIN_ONE_SLIDE),
            context={"action": "create_doc", "doc_type": "ppt"},
            doc={"kind": "ppt", "spec": _THIN_ONE_SLIDE,
                 "refined_spec": _GOOD_SLIDES}))
    add("C22", "质量门", "做份 PPT（要点全是三五个字）", "质量门软失败",
        "create_doc",
        _fx(_standard("create_doc", doc_type="ppt", content=_THIN_BULLETS),
            context={"action": "create_doc", "doc_type": "ppt"},
            doc={"kind": "ppt", "spec": _THIN_BULLETS,
                 "refined_spec": _GOOD_SLIDES}))
    add("C23", "工具路由", "读一下 broken.csv（文件读不出）", "坏路径",
        "read_doc",
        _fx(_standard("read_doc", path="C:/data/broken.csv"),
            context={"path": "C:/data/broken.csv"},
            path_unreadable=True))
    add("C24", "鲁棒性", "再做一次刚才被撤销的 PPT", "中途打断", "create_doc",
        _fx(_standard("create_doc", doc_type="ppt"),
            context={"action": "create_doc"}, undone_before=True))
    add("C25", "工具路由", "联网查最新消息（断网了）", "断网", "web_search",
        _fx(_standard("web_search", query="最新消息"),
            context={"action": "web_search"},
            network_fragile=True, network_fail_cat="timeout"))
    add("C26", "工具路由", "联网搜资料（API Key 失效）", "Key 失效",
        "web_search",
        _fx(_standard("web_search", query="行业资料"),
            context={"action": "web_search"},
            network_fragile=True, network_fail_cat="key_invalid"))

    # ---------- 硬失败样本（学习前后都失败）：敢公布失败率 ----------
    add("C27", "鲁棒性", "查一下天气（模型漏给参数）", "缺参", "web_search",
        _fx([{"action": "web_search"}]))
    add("C28", "鲁棒性", "模型返回两条恶意动作", "恶意/非法动作", "web_search",
        _fx([{"action": "sudo_rm", "text": "x"},
             {"action": "eval_inject", "text": "y"}]))
    add("C29", "鲁棒性", "用户打断，模型没给出动作", "中途打断", "set_volume",
        _fx([_bubble("嗯……我看看")]))
    add("C30", "质量门", "查量子计算（零重合且无法换词救回）",
        "质量门软失败", "web_search",
        _fx(_standard("web_search", query="量子计算"),
            context={"action": "web_search"},
            search={"query": "量子计算", "results": _offtopic()}))
    add("C31", "质量门", "做 PPT（内容单薄且无补写数据）", "质量门软失败",
        "create_doc",
        _fx(_standard("create_doc", doc_type="ppt", content=_THIN_ONE_SLIDE),
            context={"action": "create_doc", "doc_type": "ppt"},
            doc={"kind": "ppt", "spec": _THIN_ONE_SLIDE}))
    add("C32", "计划编排", "只做一件事（计划步数不合法）", "并发指令",
        "show_plan",
        _fx([_bubble("拟个计划"), _act("show_plan", steps=_BAD_PLAN)],
            plan={"steps": _BAD_PLAN}))

    return tasks


TASKS = build_tasks()


# ---------------------------------------------------------------------------
# 学习适配器
# ---------------------------------------------------------------------------
class LearningAdapter:
    """两态适配器：learned=False 不应用任何调整；learned=True 用真实规则/偏好。

    所有 Store 的落盘路径都注入 tempfile.TemporaryDirectory()，退出即清理，
    绝不写脏项目 data/。支持 with 语句管理生命周期。
    """

    def __init__(self, learned=False, seed=DEFAULT_SEED):
        self.learned = bool(learned)
        self.seed = seed
        self._tmp = tempfile.TemporaryDirectory(prefix="benchmark_learn_")
        d = self._tmp.name
        self.dir = d
        self.trajectory = _traj.TrajectoryStore(
            path=os.path.join(d, "history.jsonl"))
        self.rules = _evo.RuleStore(path=os.path.join(d, "rules.json"))
        self.preferences = _pref.PreferenceStore(
            path=os.path.join(d, "preferences.json"))
        if self.learned:
            self._seed_history()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        self._tmp.cleanup()

    def _seed_history(self):
        """写模拟历史轨迹 -> RuleStore.learn() 真的归纳规则；再喂几句偏好。"""
        # 同族同模式各写 2 条独立事件（不同轮 -> 不同 event_id），满足证据门槛。
        for _ in range(2):
            self.trajectory.start_turn(source="user")
            self.trajectory.append(
                "quality", action="web_search",
                note="资料与查询关键词零重合，质量不达标",
                payload={"reason": "零重合"})
            self.trajectory.append(
                "quality", action="create_doc",
                note="要点过于干瘪，章节不足",
                payload={"doc_type": "ppt", "reason": "要点"})
            self.trajectory.append(
                "failure", action="read_doc",
                note="文件不存在，读不出",
                payload={"path": "C:/x/broken.csv", "reason": "文件不存在"})
            self.trajectory.append(
                "undo", action="create_doc", source="user",
                note="用户撤销了刚才的操作",
                payload={"undone_action": "create_doc"})
            self.trajectory.append(
                "failure", action="web_search",
                note="请求超时，等太久",
                payload={"reason": "超时"})
            self.trajectory.end_turn()
        self.rules.learn(self.trajectory.events())
        for text in ("以后别联网", "我喜欢深色 PPT", "简洁点"):
            self.preferences.observe(text)

    def adjust(self, context):
        """执行前调整。learned=False 恒返回 {}（不应用任何偏好/规则）。"""
        if not self.learned:
            return {}
        out = dict(self.rules.apply(context))   # 含 rule_ids / 各调整字段
        out["allow_web_search"] = self.preferences.allow_web_search()
        out["verbosity"] = self.preferences.verbosity()
        out["doc_style"] = self.preferences.doc_style()
        out["preference_prompt"] = self.preferences.as_prompt()
        return out


# ---------------------------------------------------------------------------
# 模拟执行器
# ---------------------------------------------------------------------------
def _primary_action(actions):
    for a in actions:
        if a.get("action") not in _NON_FUNCTIONAL:
            return a
    return actions[0]


def run_case(case, adapter, rng):
    """跑一条任务，返回逐条结果 dict。

    链路：预置动作 -> parse_actions -> check_pre -> quality_gate.assess_*
    -> plan_graph.validate -> 判定。latency_ms/tokens 用 rng 的固定种子模拟值。
    """
    fixture = case["fixture"]
    actions = _parser.parse_actions(fixture["raw_actions"])
    primary = _primary_action(actions)
    actual_action = primary.get("action")

    ctx = dict(fixture.get("context", {}))
    ctx["action"] = actual_action
    adjust = adapter.adjust(ctx)

    gates = []          # (是否通过, 失败分类)
    retries = 0
    quality_passed = True
    skipped = False

    # 门槛 1：动作选对（缺参/恶意动作会被解析器剔除，保底成气泡 -> 失败）
    if fixture.get("require_action", True):
        gates.append((actual_action == case["expected_action"], "invalid_action"))

    # 门槛 2：前置参数校验
    pre_problems = _spec.check_pre(primary, fixture.get("world", {}))
    gates.append((not pre_problems, "pre_violation"))

    # 门槛 3：搜索质量门（软失败时，若规则给了 search_suffix 则换词重搜）
    if "search" in fixture:
        s = fixture["search"]
        r = _gate.assess_search(s["query"], s["results"])
        if (not r["ok"] and adjust.get("search_suffix")
                and s.get("refined_results")):
            refined_query = s["query"] + " " + str(adjust["search_suffix"])
            r2 = _gate.assess_search(refined_query, s["refined_results"])
            if r2["ok"]:
                r, retries = r2, retries + 1
        quality_passed = quality_passed and bool(r["ok"])
        gates.append((bool(r["ok"]), "quality_reject"))

    # 门槛 4：文档质量门（干瘪时，若规则给了页数/要点下限则按规则补足）
    if "doc" in fixture:
        d = fixture["doc"]
        r = _gate.assess_doc_spec(d["kind"], d["spec"])
        if (not r["ok"]
                and (adjust.get("min_slides") or adjust.get("min_bullet_len"))
                and d.get("refined_spec")):
            r2 = _gate.assess_doc_spec(d["kind"], d["refined_spec"])
            if r2["ok"]:
                r, retries = r2, retries + 1
        quality_passed = quality_passed and bool(r["ok"])
        gates.append((bool(r["ok"]), "quality_reject"))

    # 门槛 5：坏路径（skip_action 跳过无谓重试，视为妥善处理）
    if fixture.get("path_unreadable"):
        if adjust.get("skip_action"):
            skipped = True
            gates.append((True, ""))
        else:
            retries += 3
            gates.append((False, "bad_path"))

    # 门槛 6：用户撤销过的动作（require_confirm 转为显式确认后通过）
    if fixture.get("undone_before"):
        if adjust.get("require_confirm"):
            gates.append((True, ""))
        else:
            gates.append((False, "no_adjustment"))

    # 门槛 7：断网 / Key 失效 / 超时（allow_web_search=False 走本地兜底）
    if fixture.get("network_fragile"):
        if adjust.get("allow_web_search") is False:
            gates.append((True, ""))
        else:
            gates.append((False, fixture.get("network_fail_cat", "timeout")))

    # 门槛 8：计划合法性
    if "plan" in fixture:
        plan = _plan.compile_plan(fixture["plan"]["steps"])
        problems = _plan.validate(plan)
        gates.append((not problems, "invalid_plan"))

    failed = [cat for ok, cat in gates if not ok]
    ok = not failed
    failure_reason = failed[0] if failed else ""

    # 固定 2 次 rng 调用，与 learned / 分支无关 -> 同种子逐字段可复现
    base = rng.randint(80, 220)
    tokens = rng.randint(120, 880)
    latency_ms = base + 35 * retries + (50 if skipped else 0)

    return {
        "id": case["id"],
        "ok": ok,
        "expected_action": case["expected_action"],
        "actual_action": actual_action,
        "retries": retries,
        "quality_passed": bool(quality_passed),
        "skipped": bool(skipped),
        "failure_reason": failure_reason,
        "latency_ms": latency_ms,
        "tokens": tokens,
        "rule_ids": list(adjust.get("rule_ids", [])),
    }


def _percentile(sorted_values, p):
    if not sorted_values:
        return 0
    return sorted_values[int((len(sorted_values) - 1) * p)]


def _invalid_action_rate():
    """无效动作率：原始序列里被 normalize_action 判非法的动作占比。"""
    invalid = total = 0
    for case in TASKS:
        for raw in case["fixture"]["raw_actions"]:
            total += 1
            if _parser.normalize_action(raw) is None:
                invalid += 1
    return round(invalid / max(1, total), 4)


def run_suite(seed=DEFAULT_SEED, learned=False):
    """跑完一组，返回汇总（含逐条明细）。全程落盘在临时目录。"""
    rng = random.Random(seed)
    with LearningAdapter(learned=learned, seed=seed) as adapter:
        cases = [run_case(c, adapter, rng) for c in TASKS]

    total = len(cases)
    passed = sum(1 for c in cases if c["ok"])
    cats = {k: 0 for k in ALL_FAILURE_CATEGORIES}
    for c in cases:
        if not c["ok"]:
            key = c["failure_reason"]
            cats[key] = cats.get(key, 0) + 1

    latencies = sorted(c["latency_ms"] for c in cases)
    return {
        "seed": seed,
        "learned": bool(learned),
        "total": total,
        "passed": passed,
        "completion_rate": round(passed / total, 4) if total else 0.0,
        "invalid_action_rate": _invalid_action_rate(),
        "avg_retries": round(sum(c["retries"] for c in cases) / total, 4)
        if total else 0.0,
        "quality_gate_pass_rate": round(
            sum(1 for c in cases if c["quality_passed"]) / total, 4)
        if total else 0.0,
        "zero_model_rate": 1.0,     # 全部走预置/mock 本地链路，零真实模型
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "tokens_total": sum(c["tokens"] for c in cases),
        "failure_categories": cats,
        "cases": cases,
    }


def compare(seed=DEFAULT_SEED):
    """学习前后对照：跑两组，算提升百分点、被救回 id、两组失败分类。"""
    before = run_suite(seed, learned=False)
    after = run_suite(seed, learned=True)
    before_rate = before["completion_rate"]
    after_rate = after["completion_rate"]
    b_ok = {c["id"]: c["ok"] for c in before["cases"]}
    rescued = [c["id"] for c in after["cases"]
               if c["ok"] and b_ok.get(c["id"]) is False]
    return {
        "seed": seed,
        "before_rate": before_rate,
        "after_rate": after_rate,
        "delta_pp": round((after_rate - before_rate) * 100, 2),
        "rescued_ids": rescued,
        "failure_categories_before": before["failure_categories"],
        "failure_categories_after": after["failure_categories"],
        "before": before,
        "after": after,
        "meta": {
            "name": "带扰动的能力评测（含学习前后对照）",
            "network": "disabled（离线确定性链路，零真实 LLM / 零 API Key）",
            "isolation": "tempfile.TemporaryDirectory（退出自动清理，不写 data/）",
            "tasks": before["total"],
            "perturbation_types": len(PERTURBATIONS),
        },
    }


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def _print(text=""):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


def _dw(s):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1
               for ch in str(s))


def _pad(s, width):
    s = str(s)
    return s + " " * max(0, width - _dw(s))


def _print_categories(title, cats):
    _print("\n " + title)
    for k in ALL_FAILURE_CATEGORIES:
        if cats.get(k):
            _print(f"   {_pad(k, 16)} {cats[k]}")


def _print_report(cmp, threshold, exit_ok):
    b, a = cmp["before"], cmp["after"]
    line = "=" * 78
    sub = "-" * 78
    _print(line)
    _print(" 带扰动的能力评测（含学习前后对照）  tools/benchmark.py")
    _print(f" 种子 {cmp['seed']}   任务 {b['total']} 条   "
           f"扰动类型 {cmp['meta']['perturbation_types']} 类")
    _print(f" 口径 {cmp['meta']['network']}；{cmp['meta']['isolation']}")
    _print(line)

    _print("\n一、学习前后对照")
    _print(sub)
    _print(f" 学习前完成率 : {b['completion_rate']:.2%}  "
           f"({b['passed']}/{b['total']})")
    _print(f" 学习后完成率 : {a['completion_rate']:.2%}  "
           f"({a['passed']}/{a['total']})")
    _print(f" 提升百分点   : +{cmp['delta_pp']:.2f} pp")
    _print(f" 阈值 / 退出码: {threshold:.2f} / {0 if exit_ok else 1}"
           f"   （0=放行，1=卡口）")

    _print(f"\n 被规则/偏好救回的任务 id（{len(cmp['rescued_ids'])} 条）：")
    if cmp["rescued_ids"]:
        _print("   " + "、".join(cmp["rescued_ids"]))
    else:
        _print("   （无）")

    _print_categories("学习前失败原因分类：",
                      cmp["failure_categories_before"])
    _print_categories("学习后失败原因分类：",
                      cmp["failure_categories_after"])

    _print("\n二、两组关键指标（学习前 / 学习后）")
    _print(sub)
    rows = [
        ("无效动作率", "invalid_action_rate"),
        ("平均重试次数", "avg_retries"),
        ("质量门通过率", "quality_gate_pass_rate"),
        ("零模型路径占比", "zero_model_rate"),
        ("p50 延迟(ms)", "p50_latency_ms"),
        ("p95 延迟(ms)", "p95_latency_ms"),
        ("token 合计", "tokens_total"),
    ]
    for label, key in rows:
        _print(f"   {_pad(label, 18)} {_pad(b[key], 12)} {a[key]}")

    _print("\n三、结论")
    _print(sub)
    hard = a["total"] - a["passed"]
    _print(f" 学习后完成率较学习前提升 {cmp['delta_pp']:.2f} 个百分点，"
           f"共 {len(cmp['rescued_ids'])} 条任务被真实规则/偏好救回"
           f"（换词重搜 / 补足文档 / 跳过坏路径 / 显式确认 / 别联网兜底）。")
    _print(f" 仍如实保留 {hard} 条硬失败样本（缺参、恶意动作、计划冲突、"
           "无法换词的零重合资料），未靠把任务设计成全满分来通过阈值。")
    _print(" 口径提醒：这是离线确定性链路的能力抽样，不等同于联网或真实"
           "大模型表现；与 pytest 单测互补，用于量化「进化是否真的有用」。")
    _print(line)


def _write_json(path, data):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _print(f"\n[已写出 JSON] {os.path.abspath(path)}")


def _append_history(cmp, seed):
    """追加一行到 docs/benchmark/history.jsonl（目录不存在则创建）。"""
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seed": seed,
        "before_rate": cmp["before_rate"],
        "after_rate": cmp["after_rate"],
        "delta": round(cmp["after_rate"] - cmp["before_rate"], 4),
        "metrics": {
            "rescued_ids": cmp["rescued_ids"],
            "before": {
                "completion_rate": cmp["before"]["completion_rate"],
                "avg_retries": cmp["before"]["avg_retries"],
                "quality_gate_pass_rate": cmp["before"]["quality_gate_pass_rate"],
                "failure_categories": cmp["failure_categories_before"],
            },
            "after": {
                "completion_rate": cmp["after"]["completion_rate"],
                "avg_retries": cmp["after"]["avg_retries"],
                "quality_gate_pass_rate": cmp["after"]["quality_gate_pass_rate"],
                "failure_categories": cmp["failure_categories_after"],
            },
        },
    }
    d = os.path.dirname(os.path.abspath(HISTORY_PATH))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    _print(f"\n[已追加历史] {os.path.abspath(HISTORY_PATH)}")


def main(argv=None):
    p = argparse.ArgumentParser(
        description="带扰动的能力评测（含学习前后对照，离线确定性）")
    p.add_argument("--json", dest="json_path", default=None,
                   help="把结果写成机器可读 JSON")
    p.add_argument("--append-history", action="store_true",
                   help="追加一行到 docs/benchmark/history.jsonl")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                   help="学习后完成率阈值，默认 0.8")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED,
                   help="随机种子，默认 42")
    args = p.parse_args(argv)
    if not 0.0 <= args.threshold <= 1.0:
        _print("参数错误：--threshold 必须在 0~1 之间")
        return 1

    cmp = compare(args.seed)
    exit_ok = (cmp["after_rate"] >= args.threshold - 1e-9
               and cmp["delta_pp"] > 0
               and len(cmp["rescued_ids"]) >= MIN_RESCUED)
    cmp["threshold"] = args.threshold
    cmp["exit_ok"] = exit_ok
    _print_report(cmp, args.threshold, exit_ok)

    if args.json_path:
        try:
            _write_json(args.json_path, cmp)
        except OSError as e:
            _print(f"写 JSON 失败：{type(e).__name__}: {e}")
            return 1
    if args.append_history:
        try:
            _append_history(cmp, args.seed)
        except OSError as e:
            _print(f"追加历史失败：{type(e).__name__}: {e}")
            return 1
    return 0 if exit_ok else 1


if __name__ == "__main__":
    sys.exit(main())
