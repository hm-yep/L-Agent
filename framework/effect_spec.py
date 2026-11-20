# -*- coding: utf-8 -*-
"""形式化动作契约：把「人工分类表 + 单测钉死 unknown==0」升级为可运行时校验的契约。

action_registry.validate() 保证的是「目录完整」（每个动作都归了类），
却保证不了「撤销行为正确」：分类写对了，逆操作仍可能悄悄漏撤一项，而测试
依旧全绿。本模块给每个动作补一份可执行的契约（observables / reversible /
inverse / pre / post），并在运行时做三件事：

  * validate_specs()：把 32 份契约与真实实现（动作池、coverage、必填参数）
    逐一比对。任何新增动作漏写 spec，这里立刻报出来，是 CI 卡口。
  * check_pre / check_post：执行前断言参数合法；执行后用 effect_journal.diff
    断言「声明的副作用确实被观测到」或「声明无副作用却没动世界」。误报可解释，
    且绝不抛异常拖垮主流程。
  * involution_check：属性测试。用纯假手跑「正向编辑 → diff → undo → 世界
    哈希回到 before」，给出「撤销是对合」的可证明证据（failed == 0）。

零模型、零 Qt、零真实副作用：只依赖标准库 + 项目内的纯数据模块，不碰真实
文件 / 音量 / 任务台。action_parser（会拉起 requests/pptx）一律延迟 import。
"""

import random

from framework import action_registry, config, effect_journal, trajectory

WORLD_KEYS = effect_journal.WORLD_KEYS

# diff 的回退项种类 → 它落在哪个可观测面上
_KIND_TO_KEY = {
    "volume": "volume",
    "fact": "facts",
    "reminder": "reminders",
    "delivery": "deliveries",
    "todo_add": "todos",
    "todo_done": "todos",
    "todo_gone": "todos",
}

# 合法 doc_type（与 doc_generator.normalize_doc_type 的别名表保持一致，
# 但不 import 那个会拉起 requests / pptx 的模块，保持本层纯数据）。
_DOC_ALIASES = {
    "ppt": ("ppt", "pptx", "幻灯片", "演示文稿", "slides", "presentation"),
    "doc": ("doc", "docx", "word", "文档", "文章", "报告", "周报"),
    "xlsx": ("xlsx", "excel", "表格", "电子表格", "sheet", "spreadsheet"),
    "pdf": ("pdf", "pdf文档", "pdf报告"),
    "html": ("html", "htm", "网页", "网页报告", "报告页", "web", "webpage"),
}
_VALID_DOC_TYPES = {v for group in _DOC_ALIASES.values() for v in group}

_CLASS_REASON = {
    "reversible": "改动落在可观测面上，diff 能生成逆操作逐项回退",
    "restorable": "改写前已落副本，撤销按账本把原件还原回原样"
                  "（撤得回，但逆操作不来自五面快照 diff）",
    "blind": "有外部后果但五面快照看不见，无法生成逆操作（撤不回）",
    "no_effect": "只读或只改桌宠姿态/界面显示，不改动五面世界快照",
    "undo": "撤销动作本身，再记一笔账会套娃，跳过记账",
}

_REQUIRED_FIELDS = ("observables", "reversible", "idempotent", "inverse",
                    "pre", "post", "reason", "effect", "params")


def _spec(effect, observables=(), inverse=None, *, idempotent=False,
          params=(), reason="", pre="", post=""):
    """构造一份契约。SPECS 的键显式写死 32 个，值由此填充以避免重复。"""
    rev = effect in ("reversible", "restorable")
    if not post:
        if effect == "restorable":
            post = "原件已落副本，按账本能还原回原样"
        elif rev:
            post = "声明的可观测面上应出现对应变化"
        else:
            post = "五面世界快照不应改变"
    return {
        "observables": list(observables),
        "reversible": rev,
        "idempotent": bool(idempotent),
        "inverse": inverse,
        "pre": pre or "必填参数齐全且取值合法",
        "post": post,
        "reason": reason or _CLASS_REASON[effect],
        "effect": effect,
        "params": list(params),
    }


# 32 个动作的显式契约清单。新增动作必须在这里补一行，否则 validate_specs 报错。
# observables 与 effect_journal._TOUCHES 一一对应；blind 逐条写清撤不回的原因；
# restorable 的逆操作来自改写时写下的副本账本，不进五面快照。
SPECS = {
    "show_bubble": _spec("no_effect", idempotent=True, params=["text"],
                         pre="text 非空"),
    "think": _spec("no_effect", idempotent=True),
    "idle": _spec("no_effect", idempotent=True),
    "jump": _spec("no_effect", idempotent=True),
    "move_to": _spec("no_effect", idempotent=True, params=["anchor"]),
    "show_card": _spec("no_effect", idempotent=True, params=["title"]),
    "search_app": _spec("no_effect", idempotent=True, params=["app"]),
    "open_app": _spec("blind", params=["app"],
                      reason="程序已帮你打开，窗口里可能有没保存的活儿，"
                             "不替你关，这一步撤不回"),
    "screenshot": _spec("blind",
                        reason="截图是你在对话框里亲手选位置存的，那是你的"
                               "文件，不追踪也不删，这一步撤不回"),
    "set_volume": _spec("reversible", ("volume",),
                        "set_volume(恢复旧音量)", params=["volume"],
                        pre="volume 可转成 0~100 的数字"),
    "shutdown": _spec("blind",
                      reason="关机一旦真的执行就撤不回；仅在延时窗口里"
                             "可用「取消关机」撤销"),
    "restart": _spec("blind",
                     reason="重启一旦真的执行就撤不回；仅在延时窗口里"
                            "可用「取消关机」撤销"),
    "lock": _spec("blind", reason="锁屏之后没法替你解锁，这一步撤不回"),
    "cancel_shutdown": _spec("no_effect", idempotent=True),
    "create_doc": _spec("reversible", ("deliveries",), "delete_file",
                        params=["doc_type"],
                        pre="doc_type 属于 ppt/doc/xlsx/pdf/html"),
    "see_screen": _spec("no_effect", idempotent=True),
    "remember": _spec("reversible", ("facts",), "forget_fact",
                      params=["fact"]),
    "show_plan": _spec("no_effect", idempotent=True),
    "remind": _spec("reversible", ("reminders",), "remove_reminder",
                    params=["text"], pre="text 非空"),
    "todo_add": _spec("reversible", ("todos",), "remove_todo",
                      params=["text"], pre="text 非空"),
    "todo_done": _spec("reversible", ("todos",), "reopen_todo"),
    "todo_list": _spec("no_effect", idempotent=True),
    "web_search": _spec("no_effect", idempotent=True, params=["query"]),
    "read_doc": _spec("no_effect", idempotent=True, params=["path"],
                      pre="path 非空"),
    "list_dir": _spec("no_effect", idempotent=True, params=["path"],
                      pre="path 非空"),
    "edit_doc": _spec("restorable", (), "restore_file",
                      params=["path", "instruction"],
                      pre="path 非空、instruction 非空，且文件是可改写的文档",
                      reason="改写用户文件前已落副本，撤销能把原件还原回原样"
                             "（撤得回，但逆操作来自副本账本，不是五面快照 diff）"),
    "analyze_data": _spec("reversible", ("deliveries",), "delete_file",
                          params=["path"], pre="path 非空"),
    "work_log": _spec("no_effect", idempotent=True),
    "run_workflow": _spec("blind", params=["name"],
                          reason="工作流会另起一整轮任务，本轮只记这一步"
                                 "本身，不追进去撤，这一步撤不回"),
    "undo_last": _spec("undo"),
    "save_skill": _spec("no_effect", idempotent=True),
    "run_skill": _spec("reversible", WORLD_KEYS,
                       "按 diff 账本逐项回退(undo_ops)",
                       params=["skill"]),
}

_UNKNOWN_SPEC = _spec(
    "no_effect",
    reason="未知动作：保守按无副作用处理，validate_specs 会把缺契约报出来")


def spec_for(action_name: str) -> dict:
    """取一份契约的副本（未知动作给保守默认，绝不抛 KeyError）。"""
    s = SPECS.get(str(action_name or "").strip().lower()) or _UNKNOWN_SPEC
    out = dict(s)
    out["observables"] = list(s["observables"])
    out["params"] = list(s["params"])
    return out


def validate_specs() -> list:
    """把 32 份契约与真实实现逐一比对，返回中文问题描述（空 = 全部一致）。

    检查：动作池基数与全覆盖、effect 分类与 coverage() 一致、observables 与
    effect_journal._TOUCHES 一致且只能来自 WORLD_KEYS、可逆动作必须有 inverse、
    blind 类必须 reversible=False 且 reason 写清、必填参数在契约 / 注册表 /
    action_parser.REQUIRED_PARAMS 三处一致。新增动作漏写 spec 必被报出。
    """
    problems = []
    from framework import action_parser        # 延迟：避免顶层拉起 requests/pptx

    actual = action_registry.all_actions()
    spec_names = set(SPECS)
    expected_count = int(getattr(config, "EXPECTED_ACTION_COUNT", 32))

    if len(actual) != expected_count:
        problems.append(f"动作池有 {len(actual)} 个动作，契约基准是 "
                        f"{expected_count} 个（数量漂移）")
    if len(spec_names) != expected_count:
        problems.append(f"EffectSpec 覆盖 {len(spec_names)} 个动作，"
                        f"应为 {expected_count} 个")

    for name in sorted(actual - spec_names):
        problems.append(f"动作 {name} 缺少 EffectSpec 契约"
                        f"（新增动作必须补 spec，这是 CI 卡口）")
    for name in sorted(spec_names - actual):
        problems.append(f"EffectSpec 里的 {name} 不在动作池中"
                        f"（多余或已删除的动作）")

    cov = effect_journal.coverage(actual)
    for name in sorted(cov.get("unknown", [])):
        problems.append(f"动作 {name} 没被 effect_journal 归类（unknown）")

    classified = {}
    for key in ("reversible", "restorable", "blind", "no_effect", "undo"):
        for name in cov.get(key, []):
            classified[name] = key
    touches = getattr(effect_journal, "_TOUCHES", {})
    required = action_parser.REQUIRED_PARAMS
    # 「可还原」也算撤得回：它的逆操作来自副本账本，不是快照 diff
    reversible_set = (set(cov.get("reversible", []))
                      | set(cov.get("restorable", [])))

    for name in sorted(spec_names & actual):
        s = SPECS[name]
        for field in _REQUIRED_FIELDS:
            if field not in s:
                problems.append(f"{name} 的契约缺少字段 {field}")

        obs = list(s.get("observables", []))
        for o in obs:
            if o not in WORLD_KEYS:
                problems.append(f"{name} 声明了非法可观测面 {o!r}"
                                f"（只能取自 WORLD_KEYS={WORLD_KEYS}）")
        want_obs = list(touches.get(name, ()))
        if sorted(obs) != sorted(want_obs):
            problems.append(f"{name} 的 observables 与 _TOUCHES 不一致："
                            f"契约 {sorted(obs)} / 实际 {sorted(want_obs)}")

        want_cls = classified.get(name)
        if s.get("effect") != want_cls:
            problems.append(f"{name} 的 effect 分类不一致：契约 "
                            f"{s.get('effect')!r} / coverage {want_cls!r}")
        want_rev = name in reversible_set
        if bool(s.get("reversible")) != want_rev:
            problems.append(f"{name} 的 reversible 与 coverage()['reversible'] "
                            f"不一致：契约 {s.get('reversible')!r} / "
                            f"期望 {want_rev}")

        if want_rev and not s.get("inverse"):
            problems.append(f"{name} 声明可逆却缺少 inverse 逆操作说明")
        if want_cls == "blind":
            if s.get("reversible"):
                problems.append(f"{name} 是 blind 类，reversible 必须为 False")
            if not str(s.get("reason") or "").strip():
                problems.append(f"{name} 是 blind 类，reason 必须写清撤不回的原因")

        want_params = list(required.get(name, []))
        if sorted(s.get("params", [])) != sorted(want_params):
            problems.append(f"{name} 的必填参数与 REQUIRED_PARAMS 不一致："
                            f"契约 {sorted(s.get('params', []))} / "
                            f"解析器 {sorted(want_params)}")
        reg_params = list(action_registry.required_params(name))
        if sorted(reg_params) != sorted(want_params):
            problems.append(f"{name} 在 action_registry 的必填参数与 "
                            f"REQUIRED_PARAMS 不一致："
                            f"{sorted(reg_params)} / {sorted(want_params)}")

        if not isinstance(s.get("idempotent"), bool):
            problems.append(f"{name} 的 idempotent 必须是 bool")
        for field in ("pre", "post", "reason"):
            if not isinstance(s.get(field), str):
                problems.append(f"{name} 的 {field} 必须是 str")
    return problems


def check_pre(action: dict, world: dict) -> list:
    """执行前断言，返回违规中文说明列表（空 = 通过）。

    world 预留给「依赖世界状态的前置条件」；当前规则只校验动作参数本身，
    保持与 action_parser 的必填参数 / 别名表一致，并对几个高风险动作做真实
    取值校验（音量范围、路径非空、文本非空、文档类型合法）。
    """
    problems = []
    try:
        if not isinstance(action, dict):
            return ["前置校验：动作不是 dict，无法读取参数"]
        name = str(action.get("action", "") or "").strip().lower()

        def param(*keys):
            for k in keys:
                v = action.get(k)
                if v is not None and str(v).strip() != "":
                    return str(v).strip()
            return ""

        from framework import action_parser
        aliases = getattr(action_parser, "PARAM_ALIASES", {})
        for p in action_parser.REQUIRED_PARAMS.get(name, []):
            if not param(*tuple(aliases.get(p, [p]))):
                problems.append(f"{name} 缺少必填参数 {p}")

        if name == "set_volume":
            raw = param("volume", "音量", "percent", "值", "数值", "level")
            try:
                value = float(raw)
            except (TypeError, ValueError):
                problems.append(f"set_volume 的 volume 必须能转成 0~100 的数字，"
                                f"拿到 {raw!r}")
            else:
                if not (0 <= value <= 100):
                    problems.append(f"set_volume 的 volume={value} 越界，"
                                    f"必须在 0~100 之间")
        if name in ("read_doc", "analyze_data"):
            if not param("path", "路径", "文件", "文件路径"):
                problems.append(f"{name} 的 path 不能为空")
        if name in ("todo_add", "remind"):
            if not param("text", "内容", "回复", "话"):
                problems.append(f"{name} 的 text 不能为空")
        if name == "create_doc":
            dt = param("doc_type", "type", "类型", "文档类型", "kind")
            if dt and dt.lower() not in _VALID_DOC_TYPES:
                problems.append(f"create_doc 的 doc_type={dt!r} 不在合法集合内"
                                f"（应为 ppt/doc/xlsx/pdf/html 及其别名）")
    except Exception as e:  # noqa: BLE001 前置断言绝不把主流程带崩
        problems.append(f"前置校验自身异常（已降级）：{e}")
    return problems


def check_post(action: dict, before: dict, after: dict) -> list:
    """执行后断言，返回违规中文说明列表（空 = 通过），绝不抛异常。

    * 声明 no_effect 的动作：五面快照一旦出现变化 → 「声明无副作用却改动了世界」；
    * 声明 observables 的可逆动作：若这些面上都没观测到变化 →
      「声明了副作用但未观测到」（可能是动作幂等/失败导致的可解释误报）。
    """
    problems = []
    try:
        name = ""
        if isinstance(action, dict):
            name = str(action.get("action", "") or "").strip().lower()
        spec = spec_for(name)
        b = before if isinstance(before, dict) else {}
        a = after if isinstance(after, dict) else {}
        items = effect_journal.diff(b, a)
        effect = spec.get("effect", "")
        if effect == "no_effect":
            if items:
                problems.append(f"{name or '未知动作'} 声明无副作用却改动了世界："
                                f"diff 观测到 {len(items)} 项变化")
        elif spec.get("observables"):
            touched = {_KIND_TO_KEY.get(i.get("kind")) for i in items}
            declared = set(spec["observables"])
            if not (declared & touched):
                problems.append(f"{name} 声明了副作用但未观测到：可观测面 "
                                f"{sorted(declared)} 上没有任何变化")
    except Exception as e:  # noqa: BLE001 后置断言绝不把主流程带崩
        problems.append(f"后置校验自身异常（已降级）：{e}")
    return problems


def inverse_ops(items: list) -> list:
    """对 diff 产出的 items 生成逆操作序列（复用 effect_journal.undo_ops）。"""
    if not isinstance(items, list):
        return []
    return effect_journal.undo_ops(items)


# 只覆盖「可纯内存回退」的可逆动作：delivery 面的 delete_file 依赖真实文件与
# 删文件守卫，不属于对合属性测试的证明范围（由 check_post / 单测另行覆盖）。
_MEMORY_REVERSIBLE_OPS = ("set_volume", "todo_add", "todo_done",
                          "remind", "remember")


def _clone_world(w: dict) -> dict:
    return {
        "volume": w.get("volume"),
        "todos": [dict(t) for t in w.get("todos", [])],
        "reminders": list(w.get("reminders", [])),
        "deliveries": list(w.get("deliveries", [])),
        "facts": list(w.get("facts", [])),
    }


def _random_world(rng, template, n: int) -> dict:
    """基于 world 模板随机生成一个五面快照（deliveries 恒空）。"""
    if isinstance(template, dict) and template:
        base = effect_journal.normalize(
            volume=template.get("volume"),
            todos=template.get("todos") or (),
            reminders=template.get("reminders") or (),
            deliveries=template.get("deliveries") or (),
            facts=template.get("facts") or ())
    else:
        base = effect_journal.normalize()
    w = _clone_world(base)
    w["volume"] = rng.randint(0, 100)
    w["deliveries"] = []                      # 刻意排除 delete_file 分支
    for i in range(rng.randint(1, 2)):        # 保证 todo_done 有对象可划
        w["todos"].append({"text": f"seed{n}_{i}", "done": False})
    return w


def _apply_forward(op: str, w: dict, rng, token: str) -> None:
    """把一个可逆「正向动作」作用到内存世界上（纯数据，不碰真实系统）。"""
    if op == "set_volume":
        w["volume"] = rng.randint(0, 100)
    elif op == "todo_add":
        w["todos"].append({"text": f"add{token}", "done": False})
    elif op == "todo_done":
        # 只划「初始未完成」待办，避免「新增后立刻划掉」这种 diff 无法生成
        # 逆操作的中间态（属性测试要证明对合，就不制造不可回退的态）。
        cand = [t for t in w["todos"]
                if not t["done"] and str(t["text"]).startswith("seed")]
        if cand:
            rng.choice(cand)["done"] = True
    elif op == "remind":
        w["reminders"].append(f"rem{token}")
    elif op == "remember":
        w["facts"].append(f"fact{token}")
    # 其余动作（含 create_doc/analyze_data/run_skill）不产生可纯内存回退的
    # 变化，按 no-op 处理，不影响对合结论。


def _build_fake_api(state: dict) -> dict:
    """纯假手：apply_ops 调用它，只改内存里的 state['world']。"""
    def cur():
        return state["world"]

    def set_volume(v):
        cur()["volume"] = int(v)
        return True

    def forget_fact(t):
        f = cur()["facts"]
        if t in f:
            f.remove(t)
            return True
        return False

    def remove_reminder(t):
        r = cur()["reminders"]
        if t in r:
            r.remove(t)
            return True
        return False

    def remove_todo(t):
        todos = cur()["todos"]
        for i, x in enumerate(todos):
            if x["text"] == t and not x["done"]:
                todos.pop(i)
                return True
        return False

    def reopen_todo(t):
        for x in cur()["todos"]:
            if x["text"] == t and x["done"]:
                x["done"] = False
                return True
        return False

    def unlink(p):
        return True

    def drop_delivery(p):
        return True

    return {
        "set_volume": set_volume,
        "forget_fact": forget_fact,
        "remove_todo": remove_todo,
        "reopen_todo": reopen_todo,
        "remove_reminder": remove_reminder,
        "unlink": unlink,
        "drop_delivery": drop_delivery,
    }


def involution_check(ops=None, api=None, world=None, seed=42, trials=100) -> dict:
    """属性测试：证明「撤销是对合」——undo 之后世界哈希回到 before。

    用 random.Random(seed) 生成 trials 组随机世界与随机可撤动作序列，用纯假手
    执行 apply_ops(inverse_ops(diff(before, after)), api)。每组用
    trajectory.world_digest 校验 after 经回退后与 before 的哈希一致。

      ops   : 候选正向动作名（可空，默认取可纯内存回退的可逆子集）
      api   : 假手覆盖（传 None/{} 即用内置纯假手，切勿传真手）
      world : 随机世界的初始模板（可空）

    返回 {"trials":N, "passed":N, "failed":0, "seed":seed}。failed==0 即
    「撤销是对合」的可证明证据。局部 Random 保证同 seed 结果完全确定。
    """
    rng = random.Random(seed)
    if isinstance(ops, (list, tuple, set)):
        pool = [str(o) for o in ops if str(o) in _MEMORY_REVERSIBLE_OPS]
    else:
        pool = []
    if not pool:
        pool = list(_MEMORY_REVERSIBLE_OPS)

    state = {"world": effect_journal.normalize()}
    fake = _build_fake_api(state)
    if isinstance(api, dict) and api:
        merged = dict(fake)                  # 内置假手始终作用于内存世界
        merged.update(api)                  # 调用方可覆盖个别手做验证
        fake = merged

    n = max(0, int(trials))
    passed = 0
    for i in range(n):
        before = _random_world(rng, world, i)
        after = _clone_world(before)
        seq = [rng.choice(pool) for _ in range(rng.randint(1, 4))]
        for j, op in enumerate(seq):
            _apply_forward(op, after, rng, f"{i}_{j}")
        items = effect_journal.diff(before, after)
        undo = inverse_ops(items)
        state["world"] = after
        result = effect_journal.apply_ops(undo, fake, started_at=0.0)
        ok = (all(r.get("ok") for r in result)
              and trajectory.world_digest(state["world"])
              == trajectory.world_digest(before))
        if ok:
            passed += 1
    return {"trials": n, "passed": passed, "failed": n - passed,
            "seed": seed}
