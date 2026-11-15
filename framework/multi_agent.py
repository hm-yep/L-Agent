# -*- coding: utf-8 -*-
"""多智能体编排：Planner → Executor → Reviewer 三角色协作

2026-XX 重构（本版核心：Executor 两阶段执行 + 检索受限双层拦截）：
1. 纯文本行协议（不用 JSON，避免 json.loads 崩）
2. LLM 预算 32，打回最多重做 1 次
3. 上下文打通：每步能看到前面所有步骤产出
4. Planner 强制 3~6 步，拆太少会追加纠偏
5. Executor 两阶段：判断 → 真去搜 → 拿资料重写
6. 检索受限双层拦截：
   - prompt 层：Reviewer 被告知"检索受限必须打回"
   - 代码层：结果含"检索受限"等标记直接判不通过（不依赖模型判断）
"""

import re
import time

from framework import config
from framework.utils import logger
from framework.prompt_blocks import PERSONA_BRIEF

_MAX_ROUNDS = 1
_MAX_LLM_CALLS = 32     # 两阶段执行后单步消耗 2~3 次，24 不够用

_SEARCH_TRIGGER_WORDS = (
    "检索", "查询", "搜索", "调研", "联网",
    "最新", "近期", "2025", "2026",
    "融资", "市场规模", "数据", "报告", "白皮书",
    "名单", "份额", "排名", "榜单",
)

_EMPTY_RESULT_MARKERS = (
    "需联网", "需检索", "待检索", "待确认", "待核实",
    "本步需要", "本步骤需要", "先联网", "请检索",
    "无法完成", "没有材料", "未收到",
)

# 结果里出现这些字样 = 这一步没拿到真实数据，Reviewer 必须打回
_LIMIT_MARKERS = (
    "检索受限", "检索失败", "未能联网", "无法联网",
    "未查到", "搜索无结果", "联网失败", "无法访问",
)


PLANNER_PROMPT = PERSONA_BRIEF + """你是 Planner，负责把用户任务拆成 3~6 个可执行步骤。

【硬性要求】
- 必须拆成 3~6 个步骤，【不允许】只拆 1~2 步
- 用户任务含多个维度（如"厂商+融资+技术"），每个维度至少一步
- 最后一步通常是"汇总成结构化报告"

输出格式（严格照做，一行一步）：
1. <第一个具体步骤>
2. <第二个具体步骤>
3. <第三个具体步骤>
...

【步骤写法】
- 每步都要写清"做什么、产出什么"，动词开头
- 涉及具体数字、最新动态、厂商名单、融资、市场规模等，
  必须写明"联网检索"
- 禁止"分析""思考""深入研究"这类空话

【示例】任务："调研 X 赛道现状，需要 A、B、C 三方面"
1. 联网检索 X 赛道头部厂商名单及各自产品定位，输出列表
2. 联网检索 X 赛道 2025-2026 年融资事件，记录公司/轮次/金额/投资方
3. 联网检索 X 赛道技术趋势（多智能体协作、工具调用、MCP 协议等）
4. 汇总以上三方面内容，输出结构化调研摘要

不要输出 JSON，不要加标题，只输出步骤清单。
"""

EXECUTOR_PROMPT = PERSONA_BRIEF + """你是 Executor，负责执行一步任务。

严格按下面三行输出：
RESULT: <这一步的具体产出>
SEARCH: <YES 或 NO>
QUERY: <SEARCH=YES 时填搜索词，否则填 NONE>

【最重要：禁止复述任务】
RESULT 里【绝对不能】出现这些字样：
"需联网""需检索""待检索""待确认""待核实""本步需要""先联网"
"请检索""无法完成""没有材料""未收到"。
这些是任务要求，不是执行结果。请直接产出内容。

【联网规则】
步骤里出现以下词，必须 SEARCH: YES 并给出精炼 QUERY：
- 检索、查询、搜索、调研、联网
- 2025、2026、最新、近期
- 融资、市场规模、数据、报告、白皮书
- 名单、份额、排名、榜单

【上下文规则】用户消息会给你【前面步骤的产出】：
- "汇总""整理"类步骤：直接基于前面产出归纳，不要说"没材料"
- "补充""细化"类步骤：结合前面产出往下写

规则：
- 只输出三行，不要其他内容
- RESULT 一行写不下就压缩，不要换行
- 不要输出 JSON
"""

EXECUTOR_REWRITE_PROMPT = PERSONA_BRIEF + """你是 Executor。用户已经帮你联网检索到了资料，
现在你【必须】基于这些资料写出具体结果，绝对不能再复述任务要求。

严格按下面三行输出：
RESULT: <基于资料写成的具体内容，包含厂商名/数字/要点等实质信息>
SEARCH: NO
QUERY: NONE

【硬性禁止】RESULT 里不能出现：
"需联网""需检索""待检索""待确认""待核实""本步需要"
"先联网""请检索""无法完成""没有材料""未收到"。
出现任何一条都视为失败。

如果资料不完整，就写"根据现有资料，可以确认：xxx；尚未确认：xxx"，
绝不能整段说"需要检索"。
"""

REVIEWER_PROMPT = PERSONA_BRIEF + """你是 Reviewer，审查一步的执行结果。

严格按下面三行输出：
PASS: <YES 或 NO>
REASON: <一句话审查意见>
FIX: <若不通过，一句话修改建议；若通过填 NONE>

【必须打回（PASS: NO）】
- 结果完全是"需检索/待核实/本步需要"这类任务复述，无实质内容
- 结果通篇空话，没有厂商名/数字/事实
- 明显错误（年份/公司名错、自相矛盾）
- 上游有材料，结果却说"没有材料"
- 步骤明确写了"检索/查询/最新/2026"，结果里没有任何具体来源或数据
- 【关键】结果里出现"检索受限""检索失败""未能联网""无法联网"
  "未查到""搜索无结果"等字样，说明这一步没拿到真实数据，
  无论内容多长都必须打回
- 【关键】步骤写了"联网检索"，结果里却没有引用任何来源网站名
  （如"据中汽协""据乘联会"），必须打回

以下可以 PASS: YES：
- 内容基本切题，即使不够详尽
- 有来源标注，数据是近似值或趋势判断
- 写明"根据现有资料，可确认 xxx；待补充 xxx"

默认倾向 PASS: YES，但上面"必须打回"的绝不放过。
"""


# ============================================================
# 纯文本协议解析
# ============================================================
def _parse_planner(text: str) -> list:
    steps = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(
            r"^[\s\(\[（【]*"
            r"(?:步骤|第)?"
            r"[0-9０-９一二三四五六七八九十①②③④⑤⑥⑦⑧⑨⑩]{1,3}"
            r"[\s.、,，)）:：】\]\-–—*•·]*",
            "", line).strip()
        line = line.lstrip("*-–—•· ").strip()
        if len(line) < 2:
            continue
        steps.append(line)
        if len(steps) >= 8:
            break
    return steps


def _parse_executor(text: str) -> dict:
    text = str(text or "")
    result = ""
    need_search = False
    query = ""

    m = re.search(r"RESULT\s*[:：]\s*(.+)", text, re.IGNORECASE)
    if m:
        result = m.group(1).strip()
    m = re.search(r"SEARCH\s*[:：]\s*(YES|NO|是|否)", text, re.IGNORECASE)
    if m:
        need_search = m.group(1).upper() in ("YES", "是")
    m = re.search(r"QUERY\s*[:：]\s*(.+)", text, re.IGNORECASE)
    if m:
        query = m.group(1).strip().strip('"\'`')
        if query.upper() in ("NONE", "无", "-"):
            query = ""

    if not result:
        cleaned = re.sub(
            r"^(RESULT|SEARCH|QUERY)\s*[:：].*$", "",
            text, flags=re.MULTILINE).strip()
        result = cleaned[:1000] or text.strip()[:1000]
    return {"result": result, "need_search": need_search, "search_query": query}


def _parse_reviewer(text: str) -> dict:
    text = str(text or "")
    passed = True
    reason = ""
    fix_hint = ""

    m = re.search(r"PASS\s*[:：]\s*(YES|NO|是|否|通过|不通过)",
                  text, re.IGNORECASE)
    if m:
        v = m.group(1).upper()
        passed = v in ("YES", "是", "通过")
    m = re.search(r"REASON\s*[:：]\s*(.+)", text, re.IGNORECASE)
    if m:
        reason = m.group(1).strip()
    m = re.search(r"FIX\s*[:：]\s*(.+)", text, re.IGNORECASE)
    if m:
        fix_hint = m.group(1).strip()
        if fix_hint.upper() in ("NONE", "无", "-"):
            fix_hint = ""
    return {"passed": passed, "reason": reason, "fix_hint": fix_hint}


def _is_empty_result(text: str) -> bool:
    """判断结果是不是在复述任务/说空话。"""
    s = str(text or "").strip()
    if len(s) < 15:
        return True
    head = s[:60]
    return any(w in head for w in _EMPTY_RESULT_MARKERS)


def _is_limited_result(text: str) -> bool:
    """判断结果是不是带有"检索受限"标记（说明没拿到真实数据）。"""
    s = str(text or "")
    return any(m in s for m in _LIMIT_MARKERS)


def _should_force_search(step: str, query_from_model: str) -> str:
    s = str(step or "")
    if not any(w in s for w in _SEARCH_TRIGGER_WORDS):
        return ""
    if query_from_model:
        return query_from_model
    q = re.sub(r"[，。；,;：:、\s]+", " ", s).strip()
    return q[:40]


# ============================================================
# 编排器
# ============================================================
class MultiAgentOrchestrator:
    """三角色协作编排器（纯文本协议 + 上下文打通 + 两阶段执行）"""

    def __init__(self):
        self.calls = 0
        self.context = {}
        self.results_so_far = []

    def _llm(self, system: str, user: str,
             max_tokens: int = 1024) -> str:
        if self.calls >= _MAX_LLM_CALLS:
            raise RuntimeError(
                f"LLM 调用超预算（{_MAX_LLM_CALLS}）。"
                "可尝试：① 一次只发一个维度的任务；"
                "② 调大 multi_agent.py 里的 _MAX_LLM_CALLS")
        self.calls += 1
        import requests
        url = config.DEEPSEEK_BASE_URL.rstrip("/") + "/v1/chat/completions"
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": config.DEEPSEEK_MODEL,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}],
                  "temperature": 0.3,
                  "max_tokens": max_tokens},
            timeout=90)
        resp.raise_for_status()
        return ((resp.json().get("choices") or [{}])[0]
                .get("message") or {}).get("content", "")

    def run(self, task: str) -> dict:
        t0 = time.time()
        self.calls = 0
        self.context = {}
        self.results_so_far = []

        logger.info(f"[MultiAgent] 任务开始: {task[:80]}")

        plan_text = self._llm(PLANNER_PROMPT,
                              f"用户任务：{task}", max_tokens=512)
        steps = _parse_planner(plan_text)

        if 0 < len(steps) < 3:
            logger.warning(f"[MultiAgent] Planner 只拆 {len(steps)} 步，"
                           f"追加纠偏请求")
            fix_prompt = (
                f"{plan_text}\n\n"
                "（系统提示：你只拆了 1~2 步，违反了 3~6 步的硬性要求。"
                "请把任务按维度拆得更细，至少 3 步，重新输出步骤清单，"
                "只输出步骤，不要解释。）")
            plan_text = self._llm(PLANNER_PROMPT, fix_prompt,
                                  max_tokens=512)
            steps = _parse_planner(plan_text)

        if not steps:
            logger.error(f"[MultiAgent] Planner 原文: {plan_text[:300]!r}")
            raise RuntimeError(
                "Planner 没有给出可执行步骤（可能是任务太长/太杂，"
                "请一次只发一个任务）")
        logger.info(f"[MultiAgent] 计划 {len(steps)} 步: {steps}")

        results = []
        for i, step in enumerate(steps, 1):
            step_result = self._execute_step(step)
            results.append({"step": step, "result": step_result})

            review = self._review(step, step_result)
            if not review["passed"]:
                for _ in range(_MAX_ROUNDS):
                    logger.warning(f"[MultiAgent] 第{i}步被打回: "
                                   f"{review['reason']}")
                    step_result = self._execute_step(
                        step, fix_hint=review["fix_hint"])
                    results[-1]["result"] = step_result
                    review = self._review(step, step_result)
                    if review["passed"]:
                        break
            results[-1]["review"] = review
            self.results_so_far.append((step, step_result))

        return {
            "task": task,
            "steps": steps,
            "results": results,
            "calls": self.calls,
            "elapsed": round(time.time() - t0, 1),
        }

    def _build_upstream(self) -> str:
        if not self.results_so_far:
            return ""
        lines = []
        for idx, (step, result) in enumerate(self.results_so_far, 1):
            lines.append(f"【第{idx}步】{step}\n产出：{result}")
        text = "\n\n".join(lines)
        if len(text) > 3000:
            text = text[-3000:]
        return text

    def _execute_step(self, step: str, fix_hint: str = "") -> str:
        upstream = self._build_upstream()
        ctx = ""
        if self.context:
            ctx = "；".join(list(self.context.values()))[:800]

        # ===== 阶段 1：判断是否需要联网 =====
        prompt1 = f"当前步骤：{step}\n"
        if upstream:
            prompt1 += f"\n【前面步骤的产出】\n{upstream}\n"
        if ctx:
            prompt1 += f"\n【已联网检索到的资料】\n{ctx}\n"
        if fix_hint:
            prompt1 += f"\n上次未通过，修改建议：{fix_hint}\n"

        out1_text = self._llm(EXECUTOR_PROMPT, prompt1, max_tokens=800)
        out1 = _parse_executor(out1_text)
        logger.info(f"[MultiAgent] 阶段1 RESULT 前 80 字: "
                    f"{out1['result'][:80]!r} / "
                    f"SEARCH={out1['need_search']} "
                    f"QUERY={out1['search_query']!r}")

        force_query = _should_force_search(step, out1["search_query"])
        need_search = ((out1["need_search"] and out1["search_query"])
                       or bool(force_query))

        if not need_search:
            return out1["result"]

        # ===== 阶段 2：真去搜，把资料塞回 Executor 重写 =====
        search_query = out1["search_query"] or force_query
        search_notes = ""
        try:
            from framework.web_search import web_search, format_notes
            hits = web_search(search_query, count=5)
            if hits:
                search_notes = format_notes(search_query, hits)
                self.context[step] = search_notes
                logger.info(f"[MultiAgent] 搜索补充 {len(hits)} 条 "
                            f"(query={search_query!r})")
            else:
                logger.warning(f"[MultiAgent] 搜索无结果 "
                               f"(query={search_query!r})")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[MultiAgent] 搜索失败: {e}")

        if search_notes:
            prompt2 = (
                f"当前步骤：{step}\n\n"
                f"【联网检索到的资料】\n{search_notes}\n\n"
                "请基于上面的资料，重新输出 RESULT / SEARCH / QUERY 三行。\n"
                "RESULT 必须包含资料里的具体厂商/数字/要点；\n"
                "SEARCH 填 NO，QUERY 填 NONE。"
            )
        else:
            prompt2 = (
                f"当前步骤：{step}\n\n"
                "（联网检索本次未返回有效结果。请基于你自己的知识"
                "给出一个【近似】结果，并在 RESULT 开头明确标注"
                "「检索受限，以下为近似结果：」，然后给出具体内容。"
                "禁止只写'需检索'或'待确认'这两个词。）\n"
                "SEARCH 填 NO，QUERY 填 NONE。"
            )
        out2_text = self._llm(EXECUTOR_REWRITE_PROMPT, prompt2,
                              max_tokens=1000)
        out2 = _parse_executor(out2_text)
        logger.info(f"[MultiAgent] 阶段2 RESULT 前 80 字: "
                    f"{out2['result'][:80]!r}")

        if out2["result"] and not _is_empty_result(out2["result"]):
            return out2["result"]
        logger.warning("[MultiAgent] 阶段2 结果仍空，退回阶段1 结果")
        return out1["result"]

    def _review(self, step: str, result: str) -> dict:
        # 【代码层兜底】结果带"检索受限"等标记，直接判不通过，
        # 不依赖 Reviewer 模型（模型有时会放过）
        if _is_limited_result(result):
            logger.info("[MultiAgent] 代码层拦截：结果含检索受限标记")
            return {"passed": False,
                    "reason": "结果自带检索受限标记，说明未拿到真实数据",
                    "fix_hint": "请尝试更换搜索关键词或重新联网检索"}

        upstream = self._build_upstream()
        user = f"步骤：{step}\n执行结果：{result}\n"
        if upstream:
            user += f"\n（供参考：上游已有材料如下）\n{upstream[:1500]}\n"
        user += "请审查。"
        text = self._llm(REVIEWER_PROMPT, user, max_tokens=300)
        return _parse_reviewer(text)


_orch = None


def get_orchestrator() -> MultiAgentOrchestrator:
    global _orch
    if _orch is None:
        _orch = MultiAgentOrchestrator()
    return _orch