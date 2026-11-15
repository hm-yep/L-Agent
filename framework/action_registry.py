# -*- coding: utf-8 -*-
"""动作契约的单一真源：一个动作该长什么样，只在这张表里说一次。

以前新增一个动作要人肉同步至少 8 处：动作池、必填参数、系统提示词、
分发分支、离线规则、意图兜底、撤销分类、技能白名单，外加文档里的计数。
漏掉任何一处都不会立刻报错——最危险的是漏掉撤销分类：撤销会悄悄少撤一项，
而测试仍然全绿。

这里不强行把 8 处全部改成读取本表（提示词和离线规则天然是文本，
硬要统一反而会让行为难以预测），而是采取更可靠的办法：

  * 本表登记每个动作的**期望契约**（必填参数、撤销类别、能否进技能、说明）；
  * `validate()` 把期望契约与 4 个真实实现逐一比对；
  * `tools/check_contracts.py` 在 CI 里调用它，并额外检查分发分支、
    提示词覆盖、人设一致性、依赖包名。**漏改任何一处，CI 立刻变红。**

于是"新增动作改 8 处"变成了"改 8 处但不可能漏"，这才是可维护的真正含义。
本模块只依赖标准库，不 import Qt / requests，方便在纯 Python 环境跑契约检查。
"""

# effect 取值必须与 effect_journal.coverage() 的分类键一致：
#   reversible 可观测且可撤 / blind 有后果但快照看不见 / no_effect 不改动世界 / undo 撤销自身
# skill 为 True 表示允许进 SKILL_SAFE（重放零模型调用）
ACTION_SPECS = {
    # ---- 桌面表现 ----
    "show_bubble":  {"params": ["text"],   "effect": "no_effect", "skill": False, "desc": "说一句话"},
    "think":        {"params": [],        "effect": "no_effect", "skill": False, "desc": "切思考姿态"},
    "idle":         {"params": [],        "effect": "no_effect", "skill": False, "desc": "回待机姿态"},
    "jump":         {"params": [],        "effect": "no_effect", "skill": False, "desc": "跳一下"},
    "move_to":      {"params": ["anchor"],"effect": "no_effect", "skill": False, "desc": "移动到锚点"},
    "show_card":    {"params": ["title"], "effect": "no_effect", "skill": False, "desc": "显示结果卡片"},
    "search_app":   {"params": ["app"],   "effect": "no_effect", "skill": False, "desc": "查找本机应用"},
    "open_app":     {"params": ["app"],   "effect": "blind",     "skill": True,  "desc": "打开应用"},
    "screenshot":   {"params": [],        "effect": "blind",     "skill": True,  "desc": "截图并让用户选位置"},
    "set_volume":   {"params": ["volume"],"effect": "reversible","skill": True,  "desc": "调系统音量"},
    # ---- 电源 ----
    "shutdown":     {"params": [],        "effect": "blind",     "skill": False, "desc": "延时关机"},
    "restart":      {"params": [],        "effect": "blind",     "skill": False, "desc": "延时重启"},
    "lock":         {"params": [],        "effect": "blind",     "skill": False, "desc": "立即锁屏"},
    "cancel_shutdown": {"params": [],     "effect": "no_effect", "skill": False, "desc": "取消待执行的关机"},
    # ---- 文档工厂 ----
    "create_doc":   {"params": ["doc_type"], "effect": "reversible", "skill": False,
                     "desc": "生成 PPT/Word/Excel/网页报告"},
    # ---- 视觉 / 记忆 / 规划 ----
    "see_screen":   {"params": [],        "effect": "no_effect", "skill": False, "desc": "看屏幕并回答"},
    "remember":     {"params": ["fact"],  "effect": "reversible","skill": True,  "desc": "记一条长期记忆"},
    "show_plan":    {"params": [],        "effect": "no_effect", "skill": False, "desc": "出计划卡等确认"},
    # ---- 事务台 ----
    "remind":       {"params": ["text"],  "effect": "reversible","skill": True,  "desc": "加一条定时提醒"},
    "todo_add":     {"params": ["text"],  "effect": "reversible","skill": True,  "desc": "加一条待办"},
    "todo_done":    {"params": [],        "effect": "reversible","skill": True,  "desc": "划掉一条待办"},
    "todo_list":    {"params": [],        "effect": "no_effect", "skill": False, "desc": "播报待办清单"},
    # ---- 检索与分析 ----
    "web_search":   {"params": ["query"], "effect": "no_effect", "skill": True,  "desc": "联网检索"},
    "read_doc":     {"params": ["path"],  "effect": "no_effect", "skill": True,  "desc": "读本机文档"},
    "analyze_data": {"params": ["path"],  "effect": "reversible","skill": True,  "desc": "分析表格并出图"},
    "work_log":     {"params": [],        "effect": "no_effect", "skill": False, "desc": "播报今日工作日志"},
    "run_workflow": {"params": ["name"],  "effect": "blind",     "skill": False, "desc": "启动一键工作流"},
    # ---- 后悔药 / 技能层 ----
    "undo_last":    {"params": [],        "effect": "undo",      "skill": False, "desc": "撤销上一轮"},
    "save_skill":   {"params": [],        "effect": "no_effect", "skill": False, "desc": "把上轮轨迹存成技能"},
    "run_skill":    {"params": ["skill"], "effect": "reversible","skill": False, "desc": "零模型重放技能"},
}

EFFECT_CLASSES = {"reversible", "blind", "no_effect", "undo"}


def all_actions() -> set:
    return set(ACTION_SPECS)


def required_params(name: str) -> list:
    return list(ACTION_SPECS.get(name, {}).get("params", []))


def effect_class(name: str) -> str:
    return ACTION_SPECS.get(name, {}).get("effect", "")


def is_skill_safe(name: str) -> bool:
    return bool(ACTION_SPECS.get(name, {}).get("skill", False))


def catalog_text() -> str:
    """给提示词/文档用的动作清单（按名称排序，输出稳定可比对）。"""
    lines = ["【动作池】共 %d 个动作：" % len(ACTION_SPECS)]
    for name in sorted(ACTION_SPECS):
        spec = ACTION_SPECS[name]
        lines.append(f"- {name}：{spec['desc']}"
                     f"（必填 {', '.join(spec['params']) or '无'}；"
                     f"撤销 {spec['effect']}；技能 {'可' if spec['skill'] else '否'}）")
    return "\n".join(lines)


def validate() -> list:
    """把期望契约与真实实现比对，返回问题描述列表（空 = 全部一致）。

    延迟 import：让本模块在没有装 Qt / 文档依赖的环境里也能被导入。
    """
    problems = []

    from framework import action_parser, effect_journal, skill_store

    expected = all_actions()
    actual = set(action_parser.VALID_ACTIONS)
    for name in sorted(expected - actual):
        problems.append(f"动作池缺少 {name}（action_parser.VALID_ACTIONS）")
    for name in sorted(actual - expected):
        problems.append(f"注册表缺少 {name}（动作池里有，契约表里没有）")

    # 必填参数：只比对两边都存在的动作
    for name in sorted(expected & actual):
        want = set(required_params(name))
        got = set(action_parser.REQUIRED_PARAMS.get(name, []))
        if want != got:
            problems.append(
                f"{name} 必填参数不一致：契约 {sorted(want)} / "
                f"解析器 {sorted(got)}")

    # 撤销分类：unknown 必须为 0，且每个动作归类要与契约一致
    coverage = effect_journal.coverage(actual)
    if coverage["unknown"]:
        problems.append(
            f"有动作没被 effect_journal 归类：{sorted(coverage['unknown'])}")
    classified = {}
    for key in EFFECT_CLASSES:
        for name in coverage.get(key, []):
            classified[name] = key
    for name in sorted(expected & actual):
        want = effect_class(name)
        got = classified.get(name)
        if want not in EFFECT_CLASSES:
            problems.append(f"{name} 的 effect 取值非法：{want!r}")
        elif got != want:
            problems.append(f"{name} 撤销分类不一致：契约 {want} / 实际 {got}")

    # 技能白名单：一个动作能否进技能必须由契约决定
    want_safe = {n for n in expected if is_skill_safe(n)}
    got_safe = set(skill_store.SKILL_SAFE)
    if want_safe != got_safe:
        problems.append(
            f"技能白名单不一致：契约多 {sorted(want_safe - got_safe)} / "
            f"实现多 {sorted(got_safe - want_safe)}")
    for name in sorted(got_safe - actual):
        problems.append(f"技能白名单含动作池之外的动作：{name}")
    for name in sorted(got_safe & set(skill_store.SKILL_REFUSED)):
        problems.append(f"{name} 同时出现在 SKILL_SAFE 与 SKILL_REFUSED")

    return problems
