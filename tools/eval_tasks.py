# -*- coding: utf-8 -*-
"""端到端能力评测集（可复现、可进 CI）

评测口径
--------
这里评的是「能力」而非「代码行数」：给定一批贴近真实用户说法的任务，跑
**确定性本地链路**（离线规则引擎 mock_ai -> action_parser 校验归一 ->
quality_gate 体检 -> doc_generator / data_tool 真实产出交付物 ->
effect_journal 撤销账本 -> skill_store 技能重放），对**可观测结果**做真断言
（选了什么动作、参数是多少、文件里写进了什么、撤销后状态有没有复原），
产出通过率 + 按组通过率 + 失败原因分类表。它回答「用户吩咐的事，助手到底
办成了没有」。每条用例都是真布尔断言，禁止 assert True / 只测不抛异常 /
把被测实现换成常量桩。

不联网、不调模型
----------------
全程零网络、零真实 LLM API、零 API Key：意图走 framework.ai_engine.mock_ai
（离线规则引擎），文档与图表在本地渲染。被测模块里可能联网的函数
（fetch_images / test_connection / web_search 执行体等）本评测一律不触发。

隔离与可复现
------------
- 所有 TaskBoard / MemorySystem / SkillStore / data_tool 的落盘路径都通过
  改写模块级 app_data_dir 重定向到 tempfile.TemporaryDirectory()，退出即清理，
  绝不写脏真实用户数据；每个 TaskBoard/SkillStore 另用独立文件名，避免用例间串味；
- import 前置 os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")，
  且只调用纯函数/本地类，不依赖 QApplication 事件循环；
- 屏蔽 framework 的控制台/文件日志处理器，保证输出干净、不再追加真实日志；
- mock_ai 的随机性只影响气泡措辞（personality=沉稳），所有断言都落在非随机的
  动作参数上，因此结果逐次可复现；
- 输出纯中文、不含 emoji；stdout 主动 reconfigure 为 utf-8/replace 并做 try/except，
  PYTHONIOENCODING 未设置也不会崩；Windows / Linux 通用，单次运行 < 60 秒。

如何复现
--------
在项目根目录执行：
    PYTHONIOENCODING=utf-8 QT_QPA_PLATFORM=offscreen python tools/eval_tasks.py
可选参数：
    --json <path>        把结果写成机器可读 JSON（便于以后画趋势图）
    --threshold <float>  通过率阈值，默认 1.0（100% 才放行）
退出码：0 = 无「非已知缺陷」失败且总体通过率 >= 阈值；1 = 否则。
若发现项目真实缺陷：不改项目代码，把该用例标为 known_defect=True，报告里如实
列出；已知缺陷默认仍计入通过率，需显式用 --threshold 放行，退出码语义保持清晰。

与 pytest 单测的分工
--------------------
pytest（tests/、tools/test_*.py）验证「单个函数是否符合契约」——边界值、异常
路径、持久化回读，属于开发者视角的白盒校验；本评测验证「一串真实用户说法经过
整条本地链路后，交付结果是否正确」——属于用户视角的黑盒能力抽样。二者互补：
单测全绿不代表用户的话办得成，本评测的通过率就是用来量化后者、给 CI 做卡口
与趋势跟踪的，取代「119/123 项测试通过」这种只计数量不计能力表现的宣传。
"""

import os

# 必须在 import 任何 framework / PyQt 之前设置，避免无桌面会话的环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys

# 脚本位于 tools/ 下，需把项目根目录加入模块搜索路径
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 无 PYTHONIOENCODING 时也保证中文输出不崩
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
import platform
import tempfile
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

# 被测模块（按评测对象一一对应，来源一目了然）
from framework import config
from framework import utils
from framework import action_parser as _parser
from framework import ai_engine as _ai
from framework import effect_journal as _journal
from framework import skill_store as _skill
from framework import quality_gate as _gate
from framework import doc_generator as _doc
from framework import data_tool as _data
from framework import task_board as _board
from framework import memory_system as _memory

# 失败原因分类
_CAT_ASSERT = "断言不符"
_CAT_EXCEPTION = "运行异常"
_CAT_DEPENDENCY = "依赖缺失"
_CAT_KNOWN = "已知缺陷"


def _silence_logger():
    """关掉 framework 的控制台/文件日志处理器，保证评测输出干净且不追加真实日志。

    framework.utils 在 import 时已经建好 FileHandler（指向项目 data/logs），
    这里把它 close 并换成 NullHandler，之后的 logger.info/warning 全部成为 no-op。
    """
    log = logging.getLogger("desktop_assistant")
    for h in list(log.handlers):
        try:
            h.close()
        except Exception:  # noqa: BLE001 清理处理器不允许拖垮评测
            pass
        log.removeHandler(h)
    log.addHandler(logging.NullHandler())
    log.setLevel(logging.CRITICAL)


_silence_logger()


def _patch_app_data_dir(root: str):
    """把各持久化模块的 app_data_dir 全部重定向到临时目录。

    这些模块以 `from framework.utils import app_data_dir` 的形式在 import 时
    绑定了函数引用，因此必须逐个改模块属性；doc_generator.generate 直接写
    save_path，只要把路径传进临时目录即可，无需改它。
    """
    for mod in (_board, _memory, _skill, _data, utils):
        mod.app_data_dir = lambda r=root: r


class _Env:
    """用例共享的隔离环境：所有产出文件都落在唯一的临时目录下。"""

    def __init__(self, root: str):
        self.root = root

    def p(self, *parts) -> str:
        return os.path.join(self.root, *parts)


def _under(path: str, root: str) -> bool:
    """path 是否位于 root 之内（跨平台，Windows 不同盘符也不炸）。"""
    try:
        return os.path.commonpath([os.path.realpath(path),
                                   os.path.realpath(root)]) == os.path.realpath(root)
    except (ValueError, OSError):
        return False


@dataclass
class Case:
    """一条能力评测用例。

    fn 返回 (是否通过, 说明)：说明在通过时可为空，失败时作为「失败原因」详情；
    用例本身只描述「输入摘要 + 断言」，通过/失败与分类由 run_all 填。
    """
    cid: str
    group: str
    summary: str            # 输入摘要（贴近真实用户说法 / 输入数据）
    assertion: str          # 断言（对可观测结果的布尔判定）
    fn: Callable[[], tuple] = field(repr=False)
    known_defect: bool = False
    passed: bool = False
    fail_category: str = ""
    fail_detail: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.cid,
            "group": self.group,
            "summary": self.summary,
            "assertion": self.assertion,
            "passed": self.passed,
            "fail_category": self.fail_category,
            "fail_detail": self.fail_detail,
            "known_defect": self.known_defect,
        }


# ----------------------------------------------------------------------------
# 为什么这些用例能代表「能力」而非「代码行数」：
# 每条都取自真实用户会说的口语（"1分25秒后提醒我喝水" "把微信的音量调到30"
# "先…再…最后…"）或真实交付任务，断言落在「动作选对没有、参数算对没有、
# 文件里真的写进了什么、撤销后世界有没有复原、技能重放是否逐字节一致」这些
# 用户能感知的结果上。因此通过率刻画的是「办得成事」的比例，与覆盖了多少行
# 代码、多少个分支无关；单测里的边界/异常路径在这里刻意不重复。
# ----------------------------------------------------------------------------
def build_cases(env: _Env) -> list:
    cases: list = []

    def add(cid, group, summary, assertion, fn, known_defect=False):
        cases.append(Case(cid, group, summary, assertion, fn,
                          known_defect=known_defect))

    # =======================================================================
    # 组 1 意图理解：自然语言 -> 正确动作（mock_ai 离线规则引擎 + 解析归一）
    # =======================================================================
    def c01():
        out = _parser.parse_actions(_ai.mock_ai("把系统音量调到35", "沉稳"))
        sv = [a for a in out if a.get("action") == "set_volume"]
        ok = len(sv) == 1 and sv[0].get("volume") == "35" and not sv[0].get("app")
        return ok, f"set_volume 实际={sv}"

    add("C01", "意图理解", "把系统音量调到35",
        "路由到 set_volume，volume=35 且不带 app（识别为系统音量）", c01)

    def c02():
        out = _parser.parse_actions(_ai.mock_ai("把微信的音量调到30", "沉稳"))
        sv = [a for a in out if a.get("action") == "set_volume"]
        ok = len(sv) == 1 and sv[0].get("app") == "微信" and sv[0].get("volume") == "30"
        return ok, f"set_volume 实际={sv}"

    add("C02", "意图理解", "把微信的音量调到30",
        "路由到 set_volume，app=微信、volume=30（区分应用音量与系统音量）", c02)

    def c03():
        out = _parser.parse_actions(_ai.mock_ai("1分25秒后提醒我喝水", "沉稳"))
        rm = [a for a in out if a.get("action") == "remind"]
        ok = (len(rm) == 1 and rm[0].get("seconds") == 85.0
              and rm[0].get("text") == "喝水"
              and _board.format_duration(85) == "1 分 25 秒")
        return ok, f"remind 实际={rm}，format_duration(85)={_board.format_duration(85)!r}"

    add("C03", "意图理解", "1分25秒后提醒我喝水",
        "复合时长换算正确：remind.seconds=85，且文案为「1 分 25 秒」", c03)

    def c04():
        out = _parser.parse_actions(_ai.mock_ai("十分钟后提醒我开会", "沉稳"))
        rm = [a for a in out if a.get("action") == "remind"]
        ok = len(rm) == 1 and rm[0].get("seconds") == 600.0
        return ok, f"remind 实际={rm}"

    add("C04", "意图理解", "十分钟后提醒我开会",
        "中文数字时长换算正确：remind.seconds=600", c04)

    def c05():
        out = _parser.parse_actions(
            _ai.mock_ai("先打开微信，再查一下天气，最后截个图", "沉稳"))
        sp = [a for a in out if a.get("action") == "show_plan"]
        ok = len(sp) == 1 and sp[0].get("steps") == ["打开微信", "查一下天气", "截个图"]
        return ok, f"show_plan 实际={sp}"

    add("C05", "意图理解", "先打开微信，再查一下天气，最后截个图",
        "多步任务拆成计划，steps 恰为三段且不自动执行", c05)

    def c06():
        out = _parser.parse_actions(_ai.mock_ai("帮我做个PPT", "沉稳"))
        cd = [a for a in out if a.get("action") == "create_doc"]
        ok = (len(cd) == 1 and cd[0].get("doc_type") == "ppt"
              and isinstance(cd[0].get("content"), dict)
              and len(cd[0]["content"].get("slides", [])) >= 2
              and _gate.assess_doc_spec("ppt", cd[0]["content"]).get("ok") is True)
        return ok, f"create_doc 实际={cd}"

    add("C06", "意图理解", "帮我做个PPT",
        "路由到 create_doc(ppt)，且离线示例内容能通过质量门（不是空壳）", c06)

    def c07():
        out = _parser.parse_actions(_ai.mock_ai("搜一下新能源汽车销量", "沉稳"))
        ws = [a for a in out if a.get("action") == "web_search"]
        ok = len(ws) == 1 and ws[0].get("query") == "新能源汽车销量"
        return ok, f"web_search 实际={ws}"

    add("C07", "意图理解", "搜一下新能源汽车销量",
        "路由到 web_search，query 去掉口语噪声后为「新能源汽车销量」", c07)

    def c08():
        a1 = _parser.normalize_action({"action": "remember", "信息": "用户喜欢深色"})
        a2 = _parser.normalize_action({"action": "web_search", "搜索词": "天气"})
        a3 = _parser.normalize_action({"action": "create_doc", "类型": "表格",
                                       "内容": {"sheets": []}})
        ok = (a1 is not None and a1.get("fact") == "用户喜欢深色"
              and a2 is not None and a2.get("query") == "天气"
              and a3 is not None and a3.get("doc_type") == "xlsx")
        return ok, f"别名归一实际={(a1, a2, a3)}"

    add("C08", "意图理解", "模型把参数写成中文别名（信息/搜索词/类型）",
        "PARAM_ALIASES 容错：信息->fact、搜索词->query、类型/表格->xlsx", c08)

    def c09():
        ok = ("set_volume" in _parser.VALID_ACTIONS
              and _parser.REQUIRED_PARAMS.get("open_app") == ["app"]
              and "内容" in _parser.PARAM_ALIASES["text"]
              and "搜索词" in _parser.PARAM_ALIASES["query"]
              and _parser.REQUIRED_PARAMS.get("run_skill") == ["skill"])
        return ok, "动作池/必填表/别名表契约与实际不符"

    add("C09", "意图理解", "动作池与参数表结构",
        "VALID_ACTIONS / REQUIRED_PARAMS / PARAM_ALIASES 三张表契约正确", c09)

    def c10():
        out = _parser.parse_actions(_ai.mock_ai("打开计算器", "沉稳"))
        oa = [a for a in out if a.get("action") == "open_app"]
        ok = len(oa) == 1 and oa[0].get("app") == "计算器"
        return ok, f"open_app 实际={oa}"

    add("C10", "意图理解", "打开计算器",
        "路由到 open_app 且抽出的应用名为「计算器」", c10)

    # =======================================================================
    # 组 2 鲁棒性：脏输入下仍能给出干净、有界的动作序列
    # =======================================================================
    def c11():
        a = _parser.normalize_action({"action": "rm_rf", "text": "x"})
        out = _parser.parse_actions(
            [{"action": "fake_action"}, {"action": "show_bubble", "text": "你好"}])
        ok = a is None and [x["action"] for x in out] == ["show_bubble"]
        return ok, f"非法动作结果={a}，序列结果={out}"

    add("C11", "鲁棒性", "序列里混入不存在的动作 fake_action",
        "非法动作被剔除，合法动作原样保留", c11)

    def c12():
        a = _parser.normalize_action({"action": "open_app"})
        out = _parser.parse_actions(
            [{"action": "open_app"}, {"action": "show_bubble", "text": "你好"}])
        ok = a is None and [x["action"] for x in out] == ["show_bubble"]
        return ok, f"缺参结果={a}，序列结果={out}"

    add("C12", "鲁棒性", "open_app 缺必填参数 app",
        "缺必填参数的动作被剔除，不影响其余动作", c12)

    def c13():
        clean = _parser.normalize_action(
            {"action": "show_bubble", "text": "你好", "confidence": 0.9, "foo": [1, 2]})
        fenced = _ai.parse_ai_content(
            '```json\n[{"action":"show_bubble","text":"hi"}]\n```')
        bare = _ai.parse_ai_content("好的我这就来")
        noise_actions = _parser.parse_actions(fenced)
        ok = (clean == {"action": "show_bubble", "text": "你好"}
              and isinstance(fenced, list) and len(fenced) == 1
              and bare == [{"action": "show_bubble", "text": "好的我这就来"}]
              and noise_actions == [{"action": "show_bubble", "text": "hi"}])
        return ok, f"多余字段={clean}，代码块={fenced}，纯文本={bare}"

    add("C13", "鲁棒性", "模型输出带 markdown 代码块 / 多余字段 / 纯文本噪声",
        "多余字段不外泄，代码块被剥离，纯文本兜底成气泡", c13)

    def c14():
        out = _parser.parse_actions(
            [{"action": "show_bubble", "text": str(i)} for i in range(20)])
        ok = len(out) == 12
        return ok, f"20 条输入解析出 {len(out)} 条（期望 12）"

    add("C14", "鲁棒性", "模型一次吐出 20 条动作",
        "超长动作序列被裁到 12 条上限，不会拖垮执行", c14)

    def c15():
        long = "超" * 300
        out = _parser.parse_actions([{"action": "no_such_action", "content": long}])
        ok = (len(out) == 1 and out[0]["action"] == "show_bubble"
              and len(out[0]["text"]) == 200)
        return ok, f"抢救文本长度={len(out[0]['text']) if out else 'N/A'}（期望 200）"

    add("C15", "鲁棒性", "全无效序列里塞了 300 字超长文本",
        "保底气泡从噪声里抢救文本并裁剪到 200 字", c15)

    def c16():
        b = _board.TaskBoard()
        b._file = env.p("c16_tasks.json")       # 独立文件，避免用例间串味
        b.reminders, b.todos, b.deliveries, b.worklog = [], [], [], []
        b.add_todo("交报告")
        b.add_todo("交报告")
        ok = len(b.todos) == 1 and b.todos[0]["text"] == "交报告"
        return ok, f"重复加两次后待办数={len(b.todos)}"

    add("C16", "鲁棒性", "用户把「加个待办 交报告」说了两遍",
        "TaskBoard 对同名未完成待办去重，不产生两条", c16)

    # =======================================================================
    # 组 3 撤销账本：世界快照差集、逐项逆操作、不可逆标注、分类完备性
    # =======================================================================
    def c17():
        before = _journal.normalize(volume=80, todos=[("交周报", False)])
        after = _journal.normalize(volume=35, todos=[("交周报", False)])
        items = _journal.diff(before, after)
        ok = (len(items) == 1 and items[0]["kind"] == "volume"
              and items[0]["reversible"] is True
              and items[0]["undo"] == {"op": "set_volume", "percent": 80})
        return ok, f"diff 实际={items}"

    add("C17", "撤销账本", "本轮只把系统音量从 80 调到 35",
        "快照差集只报 1 项 volume，逆操作把音量还原到旧值 80", c17)

    def c18():
        before = _journal.normalize(volume=80, todos=[("交周报", False)],
                                    reminders=[], deliveries=[], facts=[])
        after = _journal.normalize(
            volume=35, todos=[("交周报", False), ("买咖啡", False)],
            reminders=["开会"], deliveries=["C:/x/a.pptx"], facts=["我在杭州"])
        items = _journal.diff(before, after)
        ops = _journal.undo_ops(items)
        ok = ([i["kind"] for i in items]
              == ["volume", "fact", "reminder", "todo_add", "delivery"]
              and ops == [
                  {"op": "set_volume", "percent": 80},
                  {"op": "forget_fact", "text": "我在杭州"},
                  {"op": "remove_reminder", "text": "开会"},
                  {"op": "remove_todo", "text": "买咖啡"},
                  {"op": "delete_file", "path": "C:/x/a.pptx"}])
        return ok, f"undo_ops 实际={ops}"

    add("C18", "撤销账本", "一轮里同时改音量/记事实/加提醒/加待办/生成文件",
        "差集按固定顺序给出 5 项，逐项逆操作与参数完全对应", c18)

    def c19():
        gone = _journal.diff(
            _journal.normalize(todos=[("买咖啡", False)]),
            _journal.normalize(todos=[]))
        spots = _journal.blind_spots(["open_app", "shutdown", "show_bubble"])
        ok = (len(gone) == 1 and gone[0]["kind"] == "todo_gone"
              and gone[0]["reversible"] is False and gone[0]["undo"] is None
              and _journal.undo_ops(gone) == []
              and len(spots) == 2 and all(s["reason"] for s in spots))
        return ok, f"todo_gone={gone}，blind_spots 条数={len(spots)}"

    add("C19", "撤销账本", "一条待办凭空消失 / 跑过开程序与关机",
        "不可逆项如实标 reversible=False 且不给 undo；盲区带人话原因", c19)

    def c20():
        cov = _journal.coverage(_parser.VALID_ACTIONS)
        allnames = (cov["reversible"] + cov["blind"] + cov["no_effect"]
                    + cov["undo"] + cov["unknown"])
        ok = (cov["unknown"] == []
              and len(allnames) == len(_parser.VALID_ACTIONS)
              and set(allnames) == set(_parser.VALID_ACTIONS))
        return ok, f"unknown={cov['unknown']}，分类总数={len(allnames)}"

    add("C20", "撤销账本", "遍历动作全集 VALID_ACTIONS 做可撤性分类",
        "unknown 必须为 0，且每个动作恰好落进一类（新增动作漏归类会被发现）", c20)

    def c21():
        p = env.p("c21_fresh.pptx")
        with open(p, "wb") as f:
            f.write(b"x" * 100)
        now = time.time()
        r1 = _journal.deletable(p, os.path.getsize(p), now - 10, os.path.getmtime(p))
        r2 = _journal.deletable(env.p("x.txt"), 1, now - 10, now)
        r3 = _journal.deletable(p, 100, now, now - 9999)
        ok = (r1 == (True, "") and r2[0] is False and "白名单" in r2[1]
              and r3[0] is False and "不是本轮生成" in r3[1])
        return ok, f"新鲜pptx={r1}，非白名单={r2}，旧文件={r3}"

    add("C21", "撤销账本", "回退删文件前的后缀/时间关卡",
        "本轮新生成的 .pptx 可删；.txt 与接手前的旧文件一律拒删", c21)

    def c22():
        b = _board.TaskBoard()
        b._file = env.p("c22_tasks.json")
        b.reminders, b.todos, b.deliveries, b.worklog = [], [], [], []
        started = time.time() - 5

        def snap(vol, facts):
            return _journal.normalize(
                volume=vol,
                todos=[(t["text"], t["done"]) for t in b.todos],
                reminders=[r["text"] for r in b.reminders],
                deliveries=[d["path"] for d in b.deliveries],
                facts=facts)

        before = snap(50, [])
        b.add_todo("交周报")
        b.add_reminder("开会", time.time() + 600)
        f = env.p("c22_交付.pptx")
        with open(f, "wb") as fh:
            fh.write(b"x" * 100)
        b.add_delivery("交付.pptx", f, "pptx")
        after = snap(35, ["我在杭州"])
        items = _journal.diff(before, after)
        calls = []
        api = {
            "set_volume": lambda v: (calls.append(("set_volume", v)) or True),
            "forget_fact": lambda t: (calls.append(("forget_fact", t)) or True),
            "remove_todo": lambda t: b.remove_todo_by_text(t),
            "reopen_todo": lambda t: b.reopen_todo(t),
            "remove_reminder": lambda t: b.remove_reminder_by_text(t),
            "unlink": lambda p: (os.remove(p) or True),
            "drop_delivery": lambda p: b.remove_delivery_by_path(p),
        }
        res = _journal.apply_ops(_journal.undo_ops(items), api, started_at=started)
        ok = (len(res) == 5 and all(r["ok"] for r in res)
              and ("set_volume", 50) in calls
              and ("forget_fact", "我在杭州") in calls
              and not os.path.exists(f)
              and len(b.todos) == 0 and len(b.reminders) == 0
              and len(b.deliveries) == 0)
        restored = (len(b.todos), len(b.reminders), len(b.deliveries))
        return ok, (f"apply_ops={[r['ok'] for r in res]}，"
                    f"复原后待办/提醒/交付={restored}")

    add("C22", "撤销账本", "用真实 TaskBoard 跑一轮改动再整体撤销",
        "apply_ops 接真手全部成功，文件删除且待办/提醒/交付记录全部复原", c22)

    # =======================================================================
    # 组 4 技能层：白名单、缺槽拒跑、同名不覆盖、重放确定性
    # =======================================================================
    def c23():
        r1 = _skill.skillable(
            [{"action": "create_doc", "doc_type": "ppt"},
             {"action": "todo_add", "text": "x"}])
        r2 = _skill.skillable(
            [{"action": "shutdown"}, {"action": "todo_add", "text": "x"}])
        r3 = _skill.skillable(
            [{"action": "undo_last"}, {"action": "todo_add", "text": "x"}])
        ok = (r1[0] is False and "文档" in r1[1]
              and r2[0] is False and "真机器" in r2[1]
              and r3[0] is False and "不可靠" in r3[1])
        return ok, f"白名单判定={(r1, r2, r3)}"

    add("C23", "技能层", "把 create_doc / shutdown / undo_last 存成技能",
        "SKILL_SAFE 白名单拒收，并给出各自动作为什么不能重放的原因", c23)

    def c24():
        sk = _skill.abstract(
            [{"action": "todo_add", "text": "交周报"},
             {"action": "remind", "text": "喝水"}])
        steps, missing = _skill.fill(sk, {"主题": "买咖啡"})
        ok = len(sk["slots"]) == 2 and steps == [] and missing == ["主题2"]
        return ok, f"slots={sk['slots']}，缺一槽时 steps={steps}，missing={missing}"

    add("C24", "技能层", "两个不同槽位只填了一个就想重放",
        "缺槽整条拒跑（steps 为空、如实列出缺哪些槽），不拿上次的值补", c24)

    def c25():
        st = _skill.SkillStore(path=env.p("c25_skills.json"))
        a1 = [{"action": "web_search", "query": "竞品动态"},
              {"action": "todo_add", "text": "归档"}]
        a2 = [{"action": "web_search", "query": "竞品动态"},
              {"action": "remind", "text": "归档"}]
        r1, _ = st.add("查竞品动态", a1)
        r2, _ = st.add("查竞品动态", a2)
        r3, w3 = st.add("查竞品动态", a1)      # 与 r1 同签名
        ok = (r1 is not None and r2 is not None and r3 is None
              and len(st.all()) == 2
              and r1["name"] == "查竞品动态" and r2["name"] == "查竞品动态2"
              and "不用再存" in w3)
        return ok, f"名字={[s['name'] for s in st.all()]}，同签名第三次={w3}"

    add("C25", "技能层", "同名/同签名反复存技能",
        "同签名拒收不覆盖；不同签名同名自动加后缀，旧技能完整保留", c25)

    def c26():
        sk = _skill.abstract(
            [{"action": "web_search", "query": "竞品动态"},
             {"action": "todo_add", "text": "竞品动态"}])
        outs = []
        for _ in range(20):
            steps, missing = _skill.fill(sk, {"主题": "新能源汽车"})
            outs.append(json.dumps(steps, ensure_ascii=False, sort_keys=True))
        first = json.loads(outs[0])
        ok = (len(set(outs)) == 1 and missing == []
              and first[0].get("query") == "新能源汽车"
              and first[1].get("text") == "新能源汽车")
        return ok, f"20 次重放去重后种类={len(set(outs))}，样例={outs[0]}"

    add("C26", "技能层", "同一技能 + 同一组槽位值连续重放 20 次",
        "20 次动作序列逐字节相等（零模型调用的可证保证）", c26)

    def c27():
        sk = _skill.abstract(
            [{"action": "web_search", "query": "竞品动态"},
             {"action": "todo_add", "text": "竞品动态"}])
        mv = _skill.match_values("查查「新能源汽车」", sk["slots"])
        steps, missing = _skill.fill(sk, mv)
        dumped = json.dumps(steps, ensure_ascii=False)
        ok = (mv == {"主题": "新能源汽车"} and missing == []
              and steps[0]["query"] == "新能源汽车"
              and steps[1]["text"] == "新能源汽车"
              and "竞品动态" not in dumped)
        return ok, f"match_values={mv}，填槽结果={dumped}"

    add("C27", "技能层", "换一句新话「查查新能源汽车」重放旧技能",
        "从新话里抠槽并注入，旧字面量不残留，缺槽为 0", c27)

    # =======================================================================
    # 组 5 质量门：工具「成功返回」不等于「结果可用」
    # =======================================================================
    def c28():
        kw = _gate.extract_keywords("新能源汽车市场 2025 销量")
        ok = all(x in kw for x in ("2025", "新能", "汽车", "市场", "销量"))
        return ok, f"关键词实际={kw}"

    add("C28", "质量门", "查询「新能源汽车市场 2025 销量」",
        "extract_keywords 抽出 ASCII 词 + 中文 2-gram（去重保序）", c28)

    def c29():
        long_off = ("本页介绍了家常菜的选材与烹饪火候，包括油温控制、调味顺序"
                    "以及摆盘装饰等基础技巧，适合厨房新手循序渐进地练习。")
        res = [{"title": t, "content": long_off}
               for t in ("家常菜谱大全", "厨房入门", "烹饪笔记")]
        r = _gate.assess_search("新能源汽车销量", res)
        ok = r["ok"] is False and "零重合" in r["reason"]
        return ok, f"零重合判定={r}"

    add("C29", "质量门", "搜「新能源汽车销量」却返回三篇长菜谱",
        "内容够长但与关键词零重合时判软失败，提示纠正而非当成功", c29)

    def c30():
        rel = [
            {"title": "新能源汽车销量报告",
             "content": "2025年新能源汽车销量同比增长显著，报告详细分析了销量走势"
                        "与新能源渗透率变化，并给出未来市场预测与政策建议。"},
            {"title": "行业白皮书",
             "content": "新能源汽车销量数据汇总，涵盖主要厂商销量排名与市场份额，"
                        "对销量增长驱动力做了全面梳理与解读。"}]
        r = _gate.assess_search("新能源汽车销量", rel)
        ok = r["ok"] is True and r["score"] >= 0.5 and r["reason"] == ""
        return ok, f"相关资料判定={r}"

    add("C30", "质量门", "搜「新能源汽车销量」返回两篇切题长文",
        "相关资料放行（不误杀造成纠正死循环），score 达标", c30)

    def c31():
        r0 = _gate.assess_search("天气", [])
        long_off = ("本页介绍了家常菜的选材与烹饪火候，包括油温控制、调味顺序"
                    "以及摆盘装饰等基础技巧，适合厨房新手循序渐进地练习。")
        dup = [{"title": "同一篇", "content": long_off} for _ in range(3)]
        r1 = _gate.assess_search("新能源汽车销量", dup)
        ok = (r0["ok"] is False and "没有可用结果" in r0["reason"]
              and r1["ok"] is False and "重复" in r1["reason"])
        return ok, f"空结果={r0}，全重复={r1}"

    add("C31", "质量门", "搜索结果为空 / 三条结果标题完全相同",
        "空结果与全重复两类软失败都被拦下", c31)

    def c32():
        r1 = _gate.assess_doc_spec("ppt", {"slides": [{"title": "x", "bullets": ["a"]}]})
        r2 = _gate.assess_doc_spec("ppt", {
            "slides": [{"title": "x", "bullets": ["一", "二"]},
                       {"title": "y", "bullets": ["三", "四"]}]})
        r3 = _gate.assess_doc_spec("doc", {
            "sections": [{"heading": "a", "paragraphs": ["一段"]}]})
        r4 = _gate.assess_doc_spec("xlsx", {
            "sheets": [{"name": "a", "headers": ["x"], "rows": [[1]]}]})
        good = _gate.assess_doc_spec("ppt", {
            "slides": [{"title": "背景", "bullets": ["行业规模近三年年均增长",
                                                   "用户需求同比提升"]},
                       {"title": "计划", "bullets": ["完成渠道下沉建设",
                                                   "上线标准化产品包"]}]})
        ok = (all(x["ok"] is False for x in (r1, r2, r3, r4))
              and "不足 2 页" in r1["reason"] and "干瘪" in r2["reason"]
              and "章节不足" in r3["reason"] and "数据行" in r4["reason"]
              and good["ok"] is True)
        return ok, f"四薄={(r1['reason'], r2['reason'], r3['reason'], r4['reason'])}，合格={good}"

    add("C32", "质量门", "四类文档的「太单薄」内容 + 一份合格 PPT",
        "ppt 页数/要点、doc 章节、xlsx 数据行分类型拦截，合格内容放行", c32)

    # =======================================================================
    # 组 6 交付物：四类文档真实生成回读、裁剪上限、数据分析真实出图
    # =======================================================================
    def c33():
        from pptx import Presentation
        path = env.p("c33.pptx")
        spec = {"title": "项目汇报", "subtitle": "凌零出品",
                "slides": [{"title": "背景", "bullets": ["要点一", "要点二"]},
                           {"title": "计划", "bullets": ["甲", "乙", "丙"]}]}
        ok_gen, err = _doc.generate("ppt", spec, path)
        prs = Presentation(path)
        titles = []
        for s in prs.slides:
            t = ""
            for sp in s.shapes:
                if getattr(sp, "has_text_frame", False) and sp.text_frame.text.strip():
                    t = sp.text_frame.text.strip()
                    break
            titles.append(t)
        texts = "\n".join(sp.text_frame.text for s in prs.slides
                          for sp in s.shapes if getattr(sp, "has_text_frame", False))
        ok = (ok_gen and os.path.isfile(path) and len(prs.slides) == 4
              and titles[0] == "项目汇报" and titles[1] == "背景"
              and "要点一" in texts)
        return ok, f"generate={ok_gen}/{err}，页数={len(prs.slides)}，标题={titles[:2]}"

    add("C33", "交付物", "生成一份 2 页内容的 PPT（临时目录）",
        "真实落盘并回读：封面+2内容+结束共 4 页，标题与要点写进文件", c33)

    def c34():
        import docx
        path = env.p("c34.docx")
        spec = {"title": "项目简报", "sections": [
            {"heading": "背景", "paragraphs": ["第一段", "第二段"],
             "table": {"headers": ["名", "值"], "rows": [["a", "1"], ["b", "2"]]}},
            {"heading": "结论", "paragraphs": ["完成"]}]}
        ok_gen, err = _doc.generate("doc", spec, path)
        d = docx.Document(path)
        heads = [p.text for p in d.paragraphs if p.style.name.startswith("Heading")]
        paras = [p.text for p in d.paragraphs]
        t = d.tables[0]
        ok = (ok_gen and os.path.isfile(path)
              and "背景" in heads and "结论" in heads and "第二段" in paras
              and t.rows[1].cells[0].text == "a" and t.rows[2].cells[1].text == "2")
        return ok, f"generate={ok_gen}/{err}，章节={heads}，表格单元格={(t.rows[1].cells[0].text, t.rows[2].cells[1].text)}"

    add("C34", "交付物", "生成带章节与内嵌表格的 Word（临时目录）",
        "真实落盘并回读：两个章节标题、正文段落、表格单元格内容正确", c34)

    def c35():
        from openpyxl import load_workbook
        path = env.p("c35.xlsx")
        spec = {"sheets": [{"name": "名单", "headers": ["姓名", "进度"],
                            "rows": [["凌零", "100%"], ["骑士", "60%"]]}]}
        ok_gen, err = _doc.generate("xlsx", spec, path)
        ws = load_workbook(path)["名单"]
        ok = (ok_gen and os.path.isfile(path) and ws["A1"].value == "姓名"
              and ws["B2"].value == "100%" and ws["A3"].value == "骑士")
        return ok, f"generate={ok_gen}/{err}，A1/B2/A3={(ws['A1'].value, ws['B2'].value, ws['A3'].value)}"

    add("C35", "交付物", "生成一张名单 Excel（临时目录）",
        "真实落盘并回读：表头与逐行单元格数据一致", c35)

    def c36():
        path = env.p("c36.html")
        spec = {"title": "市场调研", "subtitle": "凌零出品", "sections": [
            {"heading": "摘要", "paragraphs": ["行业增长 15%", "<风险>要转义"]},
            {"heading": "数据", "paragraphs": ["见下表"]}]}
        ok_gen, err = _doc.generate("html", spec, path)
        with open(path, encoding="utf-8") as f:
            html = f.read()
        ok = (ok_gen and os.path.isfile(path) and "市场调研" in html
              and "摘要" in html and "行业增长 15%" in html
              and "&lt;风险&gt;" in html)
        return ok, f"generate={ok_gen}/{err}，含标题/章节/转义={(('市场调研' in html), ('摘要' in html), ('&lt;风险&gt;' in html))}"

    add("C36", "交付物", "生成单文件网页报告（临时目录）",
        "真实落盘并回读：标题/章节写入，用户文本做了 HTML 转义", c36)

    def c37():
        from pptx import Presentation
        from openpyxl import load_workbook
        import docx
        many = {"title": "多页", "slides": [
            {"title": f"第{i}页", "bullets": ["x"]} for i in range(40)]}
        p1 = env.p("c37_many.pptx")
        ok1, _ = _doc.generate("ppt", many, p1)
        n_slides = len(Presentation(p1).slides)
        rows = {"sheets": [{"name": "大表", "headers": ["A"],
                            "rows": [[str(i)] for i in range(config.DOC_MAX_ROWS + 300)]}]}
        p2 = env.p("c37_big.xlsx")
        ok2, _ = _doc.generate("xlsx", rows, p2)
        n_rows = load_workbook(p2)["大表"].max_row
        long_text = "长" * (config.DOC_MAX_TEXT + 500)
        p3 = env.p("c37_long.docx")
        ok3, _ = _doc.generate(
            "doc", {"title": "长文",
                    "sections": [{"heading": "h", "paragraphs": [long_text]}]}, p3)
        paras = [p.text for p in docx.Document(p3).paragraphs]
        ok = (ok1 and ok2 and ok3
              and n_slides == 2 + config.DOC_MAX_SLIDES
              and n_rows == 1 + config.DOC_MAX_ROWS
              and any(config.DOC_MAX_TEXT - 2 <= len(p) <= config.DOC_MAX_TEXT
                      for p in paras))
        return ok, (f"PPT页数={n_slides}(期望{2 + config.DOC_MAX_SLIDES})，"
                    f"Excel行={n_rows}(期望{1 + config.DOC_MAX_ROWS})，"
                    f"长段最大长度={max(len(p) for p in paras)}")

    add("C37", "交付物", "40 页 PPT / 800 行 Excel / 2500 字段落",
        "规模防御生效：页数、行数、段落长度都裁到 config 上限，不撑爆文件", c37)

    def c38():
        csv_path = env.p("c38_sales.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("月份,区域,销售额\n"
                    "1月,华东,120.5\n2月,华北,88.0\n3月,华东,150.0\n4月,华南,99.9\n")
        res = _data.analyze(csv_path, x_col="月份", y_col="销售额", chart="柱状图")
        png = res.get("chart_path", "")
        ok = (res.get("rows") == 4 and res.get("cols") == 3
              and "4 行 × 3 列" in res.get("summary", "")
              and bool(png) and os.path.isfile(png) and _under(png, env.root))
        in_tmp = _under(png, env.root) if png else False
        return ok, (f"analyze rows/cols={(res.get('rows'), res.get('cols'))}，"
                    f"PNG={png}，位于临时目录={in_tmp}")

    add("C38", "交付物", "分析一张 4 行销售表并要求出柱状图（临时目录）",
        "真实读表出统计摘要，PNG 真实落盘且只写进临时目录", c38)

    return cases


def _execute(case: Case) -> tuple:
    """跑一条用例，返回 (是否通过, 失败原因分类, 详情)。"""
    try:
        ok, detail = case.fn()
    except (ImportError, ModuleNotFoundError) as e:
        return False, _CAT_DEPENDENCY, f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001 单条异常不拖垮整份报告
        return False, _CAT_EXCEPTION, f"{type(e).__name__}: {e}"
    if ok:
        return True, "", ""
    return False, (_CAT_KNOWN if case.known_defect else _CAT_ASSERT), str(detail)


def _conclusion(summary: dict) -> str:
    total = summary["total"]
    passed = summary["passed"]
    blocking = summary["blocking_failures"]
    known = summary["known_defects"]
    by_group = summary["by_group"]
    worst_g, worst_v = min(
        by_group.items(),
        key=lambda kv: (kv[1]["passed"] / kv[1]["total"], kv[1]["total"]))
    worst_rate = worst_v["passed"] / worst_v["total"]
    head = (f"总用例 {total} 条，通过 {passed} 条，通过率 {summary['pass_rate']:.1%}，"
            f"阻塞性失败 {blocking} 条，已知缺陷 {known} 条。")
    weakest = (f"最薄弱的一组：{worst_g}（{worst_v['passed']}/{worst_v['total']}，"
               f"{worst_rate:.1%}）。")
    if passed == total and blocking == 0:
        tail = ("各组均达标，未发现阻塞性缺陷。建议后续向最薄弱一组补充更刁钻的"
                "真实说法，让通过率产生区分度，而不是停在全员满分。")
    else:
        cats = summary["failure_categories"]
        top = "、".join(f"{k} {v} 条" for k, v in
                       sorted(((k, v) for k, v in cats.items() if v),
                              key=lambda kv: -kv[1]))
        tail = f"失败集中在：{top or '无'}，应优先修复对应链路；已知缺陷需显式用 --threshold 放行。"
    note = ("口径提醒：这是离线确定性链路的能力抽样，不等同于联网或真实大模型表现；"
            "与 pytest 单测互补，用于 CI 卡口与趋势跟踪。")
    return head + weakest + tail + note


def run_all(threshold: float = 1.0) -> dict:
    """跑完全部用例，返回机器可读的报告字典（case 明细 + 汇总 + 结论）。"""
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="eval_tasks_") as td:
        _patch_app_data_dir(td)
        env = _Env(td)
        cases = build_cases(env)
        for c in cases:
            c.passed, c.fail_category, c.fail_detail = _execute(c)
    elapsed = time.time() - t0

    total = len(cases)
    passed = sum(1 for c in cases if c.passed)
    pass_rate = passed / total if total else 0.0

    # 按组统计（保持用例出现的顺序）
    by_group = {}
    for c in cases:
        g = by_group.setdefault(c.group, {"total": 0, "passed": 0})
        g["total"] += 1
        if c.passed:
            g["passed"] += 1
    for g in by_group.values():
        g["pass_rate"] = g["passed"] / g["total"]

    # 失败原因分类计数
    categories = {_CAT_ASSERT: 0, _CAT_EXCEPTION: 0,
                  _CAT_DEPENDENCY: 0, _CAT_KNOWN: 0}
    for c in cases:
        if not c.passed and c.fail_category in categories:
            categories[c.fail_category] += 1

    known = sum(1 for c in cases if c.known_defect and not c.passed)
    blocking = sum(1 for c in cases if not c.passed and not c.known_defect)
    exit_ok = blocking == 0 and pass_rate >= threshold - 1e-9

    summary = {
        "total": total,
        "passed": passed,
        "pass_rate": pass_rate,
        "threshold": threshold,
        "by_group": by_group,
        "failure_categories": categories,
        "known_defects": known,
        "blocking_failures": blocking,
        "exit_ok": exit_ok,
        "elapsed_sec": round(elapsed, 3),
    }
    summary["conclusion"] = _conclusion(summary)

    return {
        "meta": {
            "name": "desktop_assistant 端到端能力评测",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "qt_qpa_platform": os.environ.get("QT_QPA_PLATFORM", ""),
            "network": "disabled（全程离线，无真实 LLM API / 无 API Key）",
            "isolation": "tempfile.TemporaryDirectory（退出自动清理）",
            "elapsed_sec": round(elapsed, 3),
        },
        "summary": summary,
        "cases": [c.to_dict() for c in cases],
    }


# ----------------------------------------------------------------------------
# 输出
# ----------------------------------------------------------------------------
def _print(text: str = ""):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


def _dw(s: str) -> int:
    """显示宽度：中日韩全角字符算 2 列，保证表格对齐。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _dw(s))


def _print_report(report: dict):
    meta, s = report["meta"], report["summary"]
    line = "=" * 78
    sub = "-" * 78
    _print(line)
    _print(" 端到端能力评测报告  eval_tasks.py")
    _print(f" 时间 {meta['generated_at']}   耗时 {meta['elapsed_sec']:.2f} 秒   "
           f"Python {meta['python']}")
    _print(f" 平台 {meta['platform']}")
    _print(f" 口径 {meta['network']}；{meta['isolation']}")
    _print(line)

    _print("\n一、用例明细")
    _print(sub)
    _print(_pad("编号", 6) + _pad("分组", 12) + _pad("结果", 10)
           + _pad("输入摘要", 36) + " 断言")
    for c in report["cases"]:
        mark = "通过" if c["passed"] else "失败"
        if c["known_defect"] and not c["passed"]:
            mark = "已知缺陷"
        # 摘要过长时仍补两个空格，避免与断言文字粘在一起
        _print(_pad(c["id"], 6) + _pad(c["group"], 12) + _pad(mark, 10)
               + _pad(c["summary"], 36) + " " + c["assertion"])
        if not c["passed"]:
            _print(f"      失败原因：{c['fail_category']} | {c['fail_detail']}")

    _print("\n二、汇总")
    _print(sub)
    _print(f" 总用例数 : {s['total']}")
    _print(f" 通过数   : {s['passed']}")
    _print(f" 通过率   : {s['pass_rate']:.1%}   （阈值 {s['threshold']:.1%}）")
    _print(f" 已知缺陷 : {s['known_defects']}")
    _print(f" 阻塞失败 : {s['blocking_failures']}")
    _print(f" 退出码   : {0 if s['exit_ok'] else 1}（0=放行，1=卡口）")

    _print("\n 按组通过率：")
    for g, v in s["by_group"].items():
        _print(f"   {_pad(g, 12)} {v['passed']:>2}/{v['total']:<2}  "
               f"{v['pass_rate']:>6.1%}")

    _print("\n 失败原因分类计数：")
    for k, v in s["failure_categories"].items():
        _print(f"   {_pad(k, 12)} {v}")

    _print("\n三、结论")
    _print(sub)
    _print(" " + s["conclusion"])
    _print(line)


def _write_json(path: str, report: dict):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    _print(f"\n[已写出 JSON] {os.path.abspath(path)}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="端到端能力评测（离线、确定性、可进 CI）")
    p.add_argument("--json", dest="json_path", default=None,
                   help="把结果写成机器可读 JSON，便于以后画趋势图")
    p.add_argument("--threshold", type=float, default=1.0,
                   help="通过率阈值，默认 1.0（100% 才放行）")
    args = p.parse_args(argv)
    if not 0.0 <= args.threshold <= 1.0:
        _print("参数错误：--threshold 必须在 0~1 之间")
        return 1

    report = run_all(threshold=args.threshold)
    _print_report(report)
    if args.json_path:
        try:
            _write_json(args.json_path, report)
        except OSError as e:
            _print(f"写 JSON 失败：{type(e).__name__}: {e}")
            return 1
    return 0 if report["summary"]["exit_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
