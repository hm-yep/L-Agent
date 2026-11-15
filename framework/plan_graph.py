# -*- coding: utf-8 -*-
"""计划依赖图：把线性 steps 列表编译成可校验、可增量重算的 DAG。

以前计划只是 `steps: list[str]`，谁依赖谁全靠「按列表顺序执行」这个隐含约定。
这里把它显式化成一个 DAG：默认顺序依赖 + 从中文措辞推断的数据依赖。于是可以
校验（id 唯一 / deps 存在 / 无环 / 步骤数 / 动作合法 / 不可逆动作标记
needs_confirm）、可以稳定拓扑排序、可以在某步失败时只重算它的传递后继
（affected_subgraph）——这是「增量重算」的核心证据。

规则保守且确定：默认每个节点依赖前一个节点，保证拓扑序等于原顺序；文本含
「基于/据此/用上面/根据前面/汇总/整理成/写成报告」时，向前寻找含「检索/搜索/
查/读取/分析/资料」的节点并追加数据依赖；「先/再/然后/接着/最后」只做顺序
标注。动作名用正则从文本推断，匹配不到为 None。

纯函数、零模型、零 Qt、零副作用；只依赖标准库 + config（开关一律用
getattr(config, "NAME", default) 读取，不硬依赖尚未写入的常量）。
"""

import re

from framework import config

# 触发「数据依赖」的措辞：这一步要用到前面产出的资料
_DATA_TRIGGERS = re.compile(
    r"基于|据此|用上面|根据前面|汇总|整理成|写成报告|生成报告|出报告")
# 「资料提供方」节点：文本含这些词就认为它产出资料
_DATA_SOURCES = re.compile(r"检索|搜索|查|读取|分析|资料")
# 显式顺序词（默认链已保证顺序，这里只做标注）
_ORDER_WORDS = re.compile(r"先|再|然后|接着|最后")

# 不可逆动作：执行前必须让用户确认
IRREVERSIBLE = frozenset({"shutdown", "restart", "lock", "open_app"})

# 动作名推断：有序规则表，命中第一个即止（匹配不到返回 None）
_ACTION_PATTERNS = (
    (re.compile(r"关机|关闭(电脑|计算机|系统)"), "shutdown"),
    (re.compile(r"重启|重新启动"), "restart"),
    (re.compile(r"锁屏|锁定(屏幕|电脑)"), "lock"),
    (re.compile(r"打开(应用|程序|软件)|启动(应用|程序|软件)|运行(应用|程序)"),
     "open_app"),
    (re.compile(r"检索|搜索|搜一搜|联网查|上网查"), "web_search"),
    (re.compile(r"读(取)?(文档|文件)|打开文档|阅读(文档|文件)"), "read_doc"),
    (re.compile(r"分析|统计(数据|表格)?|出图|画(图|图表)"), "analyze_data"),
    (re.compile(r"记住|记一条|长期记忆"), "remember"),
    (re.compile(r"提醒|定时提醒"), "remind"),
    (re.compile(r"待办|加(个|条|进|上)?待办|任务清单"), "todo_add"),
    (re.compile(r"生成|制作|写(成)?报告|汇总|整理成|出报告|报告|做(一份|个|成)?"),
     "create_doc"),
    (re.compile(r"调(系统)?音量|设置音量|音量(调|设)?"), "set_volume"),
)


def _infer_action(text: str):
    for pattern, action in _ACTION_PATTERNS:
        if pattern.search(text):
            return action
    return None


def _hints(text: str, action, data_deps, explicit: bool) -> dict:
    produces = None
    if action in ("web_search", "read_doc", "analyze_data"):
        produces = "资料"
    elif action == "create_doc":
        produces = "报告"
    elif action == "todo_add":
        produces = "待办"
    elif _DATA_SOURCES.search(text):
        produces = "资料"
    return {
        "action": action,
        "produces": produces,
        "consumes": "资料" if _DATA_TRIGGERS.search(text) else None,
        "explicit_order": bool(explicit),
        # 数据依赖推断出的目标（含与默认前驱重合者，作为「识别到数据依赖」的证据）
        "data_deps": list(data_deps),
        "needs_confirm": action in IRREVERSIBLE,
    }


def compile_plan(steps: list) -> dict:
    """把线性步骤编译成 DAG：{"nodes", "edges", "steps"}。

    nodes 每项 {"id","text","deps","hints"}；edges 为 [[from,to],...]；
    节点 id 形如 n0/n1/...，默认顺序依赖保证拓扑序 == 原顺序。
    """
    if steps is None:
        steps = []
    elif not isinstance(steps, (list, tuple)):
        steps = [steps]
    texts = [str(s) if s is not None else "" for s in steps]

    ids = [f"n{i}" for i in range(len(texts))]
    nodes = []
    for i, text in enumerate(texts):
        deps = []
        if i > 0:
            deps.append(ids[i - 1])                      # 默认顺序依赖
        data_deps = []
        if _DATA_TRIGGERS.search(text):
            for j in range(i):
                if _DATA_SOURCES.search(texts[j]):
                    data_deps.append(ids[j])             # 额外数据依赖
        for d in data_deps:
            if d not in deps:
                deps.append(d)
        action = _infer_action(text)
        explicit = bool(_ORDER_WORDS.search(text))
        nodes.append({
            "id": ids[i],
            "text": text,
            "deps": deps,
            "hints": _hints(text, action, data_deps, explicit),
        })

    edges, seen = [], set()
    for node in nodes:
        for d in node["deps"]:
            pair = (d, node["id"])
            if pair not in seen:
                seen.add(pair)
                edges.append([d, node["id"]])
    return {"nodes": nodes, "edges": edges, "steps": list(texts)}


# ---------- 内部：容忍手工构造 / 异常输入的读取 ----------
def _nodes_of(plan) -> list:
    if not isinstance(plan, dict):
        return []
    return [n for n in (plan.get("nodes") or [])
            if isinstance(n, dict) and "id" in n]


def _deps_of(node) -> list:
    d = node.get("deps") or []
    return [str(x) for x in d] if isinstance(d, (list, tuple)) else []


def topological_order(plan: dict) -> list:
    """稳定的 Kahn 算法：同层按节点在原 nodes 里的顺序。有环抛 ValueError。"""
    nodes = _nodes_of(plan)
    if not nodes:
        return []
    ids = [n["id"] for n in nodes]
    idx = {x: i for i, x in enumerate(ids)}
    id_set = set(ids)
    indeg = {x: 0 for x in ids}
    succ = {x: [] for x in ids}
    for n in nodes:
        for d in _deps_of(n):
            if d in id_set:
                indeg[n["id"]] += 1
                succ[d].append(n["id"])
    ready = sorted([x for x in ids if indeg[x] == 0], key=lambda x: idx[x])
    result = []
    while ready:
        cur = ready.pop(0)
        result.append(cur)
        for s in succ[cur]:
            indeg[s] -= 1
            if indeg[s] == 0:
                ready.append(s)
        ready.sort(key=lambda x: idx[x])          # 同层保持原顺序，稳定
    if len(result) != len(ids):
        raise ValueError("计划依赖图存在环，无法拓扑排序")
    return result


def validate(plan: dict, valid_actions=None) -> list:
    """校验计划，返回中文问题描述列表（空 = 全部合法）。

    检查：id 唯一、deps 指向存在的节点、无环、steps 数量在 2~6、
    推断出的 action 属于 valid_actions（默认 action_parser.VALID_ACTIONS）、
    不可逆动作（shutdown/restart/lock/open_app）已标记 needs_confirm。
    """
    if valid_actions is None:
        from framework import action_parser        # 延迟：避免顶层重依赖
        valid_actions = action_parser.VALID_ACTIONS
    valid_actions = set(valid_actions or ())

    if not isinstance(plan, dict):
        return ["计划不是 dict，无法校验"]
    problems = []
    nodes = _nodes_of(plan)
    ids = [n["id"] for n in nodes]

    lo = int(getattr(config, "PLAN_MIN_STEPS", 2))
    hi = int(getattr(config, "PLAN_MAX_STEPS", 6))
    steps = plan.get("steps")
    n_steps = len(steps) if isinstance(steps, (list, tuple)) else 0
    if not (lo <= n_steps <= hi):
        problems.append(f"计划步骤数 {n_steps} 不在 {lo}~{hi} 之间")

    if len(ids) != len(set(ids)):
        dup = sorted({x for x in ids if ids.count(x) > 1})
        problems.append(f"节点 id 不唯一：{dup}")
    id_set = set(ids)
    for n in nodes:
        for d in _deps_of(n):
            if d not in id_set:
                problems.append(f"节点 {n['id']} 的 deps 指向不存在的节点 {d}")

    try:
        topological_order(plan)
    except ValueError:
        problems.append("计划依赖图存在环")

    for n in nodes:
        hints = n.get("hints") or {}
        action = hints.get("action")
        if action is not None and action not in valid_actions:
            problems.append(f"节点 {n['id']} 推断出未知动作 {action}")
        if action in IRREVERSIBLE and not hints.get("needs_confirm"):
            problems.append(f"节点 {n['id']} 是不可逆动作 {action}，"
                            f"必须标记 needs_confirm")
    return problems


def affected_subgraph(plan: dict, failed_node: str) -> list:
    """增量重算范围：失败节点本身 + 它的所有传递后继（按原顺序）。

    不含不受影响的前置节点；failed_node 不存在时返回空列表。
    """
    nodes = _nodes_of(plan)
    ids = [n["id"] for n in nodes]
    key = str(failed_node)
    if key not in set(ids):
        return []
    children = {x: [] for x in ids}
    for n in nodes:
        for d in _deps_of(n):
            if d in children and n["id"] not in children[d]:
                children[d].append(n["id"])
    seen, stack = {key}, [key]
    while stack:
        cur = stack.pop()
        for c in children.get(cur, []):
            if c not in seen:
                seen.add(c)
                stack.append(c)
    idx = {x: i for i, x in enumerate(ids)}
    return sorted(seen, key=lambda x: idx[x])


def plan_summary(plan: dict) -> dict:
    """{"nodes","edges","data_deps","needs_confirm","order"} 统计。"""
    nodes = _nodes_of(plan)
    edges = (plan.get("edges") or []) if isinstance(plan, dict) else []
    data_deps = 0
    needs_confirm = []
    for n in nodes:
        hints = n.get("hints") or {}
        data_deps += len(hints.get("data_deps") or [])
        action = hints.get("action")
        if action in IRREVERSIBLE and action not in needs_confirm:
            needs_confirm.append(action)
    try:
        order = topological_order(plan)
    except ValueError:
        order = [n["id"] for n in nodes]
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "data_deps": data_deps,
        "needs_confirm": needs_confirm,
        "order": order,
    }
