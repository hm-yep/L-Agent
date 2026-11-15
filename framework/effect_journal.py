# -*- coding: utf-8 -*-
"""效果记账：算出「这一轮在这台电脑上改了什么」，并给出逐项回退。

零模型调用、零 Qt 依赖，纯函数 + 纯数据，可离线单测。

为什么是「轮次首尾各读一次可观测状态、求差集」，而不是在每个 `_do_*` 里
手写一段 undo：那样判据会散落到十几个地方，漏掉的那一处恰好最危险——用户
以为能撤，实际撤不干净，而且没有任何测试能发现「漏了哪一处」。求差集只有
这一份判据：动作实现改了什么，这里就看见什么，两者不可能漂移。

代价是只看得见**可观测**的改动（音量、待办、提醒、交付文件、长期记忆）。
看不见的那些不假装没事：`blind_spots()` 按动作名如实列出原因，界面把它
念给用户听。「开了程序我不替你关，因为你窗口里可能有你没保存的活儿」这句
话本身就是这个模块想表达的东西——可逆性是个覆盖率的声称，不含糊它就不能用。
"""

import os
from collections import Counter

from framework import config

# 世界快照的字段（也是 diff 唯一认的五个可观测面）
WORLD_KEYS = ("volume", "todos", "reminders", "deliveries", "facts")

# 回退项的执行顺序（不是时间序：时间戳只有部分对象有，按固定优先级才保证确定性）
_UNDO_ORDER = {"volume": 0, "fact": 1, "reminder": 2,
               "todo_done": 3, "todo_add": 4, "delivery": 5}

# 每个动作对这五个面动没动手（diff 用它决定「这个动作值得查哪个面」）。
# 只影响 diff 的效率与标签，不改变判定——判定永远以快照差集为准。
_TOUCHES = {
    "set_volume": ("volume",),
    "todo_add": ("todos",),
    "todo_done": ("todos",),
    "remind": ("reminders",),
    "remember": ("facts",),
    "create_doc": ("deliveries",),
    "analyze_data": ("deliveries",),
    # 技能重放的效果就是它里面那串动作的效果——差集读的是世界，不是动作清单，
    # 所以 run_skill 不需要为撤销做任何特殊处理，撤得掉的东西自动撤得掉。
    "run_skill": WORLD_KEYS,
}

# 回退动作本身：给它再记一笔账就成了「撤销撤销」的套娃，跳过。
UNDO_ITSELF = {"undo_last"}

# 有后果、但快照看不见的项目。列在这里就是承认撤不了，并给出人话原因。
BLIND_SPOTS = {
    "open_app": "程序我已经帮你打开了，但我不替你关——你窗口里可能有你没存的活儿",
    "lock": "锁屏之后没法替你解锁，这一步只能你自己来",
    "shutdown": "关机一旦真的执行就撤不回；还在延时窗口里时用「取消关机」撤销",
    "restart": "重启一旦真的执行就撤不回；还在延时窗口里时用「取消关机」撤销",
    "screenshot": "截图是你亲手在对话框里选位置存的，那是你的文件，我不动",
    "run_workflow": "工作流会另起一整轮任务，本轮只记它这一步本身，不追进去撤",
    # 带 app 的音量调的是合成器里那个应用自己的会话，快照只读默认设备总音量，
    # 看不见它——所以它不能冒充「可撤」。
    "set_volume_app": "那个应用的音量是单独调的，我只看得见系统总音量，"
                      "不替你猜它原来是多少",
}


def trajectory_name(action: dict) -> str:
    """这一轮里这一步该被记成什么名字（判据只有这一份，接线处不许自己拼）。

    `set_volume` 带不带 `app` 是两个完全不同的后果：不带是系统总音量（快照
    看得见、能撤），带是某个应用自己的会话音量（快照看不见、撤不回）。
    """
    name = (action or {}).get("action", "")
    if name == "set_volume" and str(action.get("app") or "").strip():
        return "set_volume_app"
    return name

# 确定无外部副作用的动作（只改桌宠姿态/界面显示，或只读）
NO_EFFECT = {
    "think", "idle", "jump", "move_to", "show_bubble", "show_card",
    "search_app", "see_screen", "web_search", "read_doc", "work_log",
    "todo_list", "show_plan", "cancel_shutdown", "save_skill",
}


def normalize(volume=None, todos=(), reminders=(), deliveries=(), facts=()):
    """把各子系统读出来的原始数据归一成可比较的五元组。

    只做取字段和排序，不做任何判断：todos 留 done 位（划掉/新增要分开算），
    reminders/deliveries/facts 只看身份（提醒时刻每次都不同，比了没意义）。

    todos 认两种形状：`{"text":..., "done":...}`（任务台的原始条目）和
    `("交周报", False)`（接线层不想搬运内部对象时给的元组）。判据只在这里
    取一次字段——写错一处，标签就成了「新待办「('交周报', False)」」，
    回退时会去删一条根本不存在的待办。
    """
    def _text(item):
        return item.get("text", "") if isinstance(item, dict) else str(item)

    def _todo(item):
        if isinstance(item, dict):
            return _text(item), bool(item.get("done"))
        if isinstance(item, (tuple, list)) and item:
            return str(item[0]), bool(item[1]) if len(item) > 1 else False
        return _text(item), False

    pairs = [_todo(t) for t in todos]
    return {
        "volume": None if volume is None else int(volume),
        "todos": [{"text": text, "done": done} for text, done in pairs],
        "reminders": sorted(_text(r) for r in reminders),
        "deliveries": sorted(_text(d.get("path", d) if isinstance(d, dict)
                                   else d) for d in deliveries),
        "facts": [str(f) for f in facts],
    }


def diff(before: dict, after: dict) -> list:
    """两轮快照之差 → 回退项列表（按 `_UNDO_ORDER` 固定顺序）。

    判定只认快照差集，不认「本轮跑过哪些动作」：就算模型绕过了所有动作偷偷
    改了待办，这里照样看得见。
    """
    items = []

    bvol, avol = before.get("volume"), after.get("volume")
    if bvol is not None and avol is not None and bvol != avol:
        items.append({
            "kind": "volume", "reversible": True,
            "label": f"系统音量从 {bvol}% 调到了 {avol}%",
            "undo": {"op": "set_volume", "percent": bvol},
        })

    for f in after.get("facts", []):
        if f not in before.get("facts", []):
            items.append({
                "kind": "fact", "reversible": True,
                "label": f"记住了「{f}」",
                "undo": {"op": "forget_fact", "text": f},
            })

    for r in after.get("reminders", []):
        if r not in before.get("reminders", []):
            items.append({
                "kind": "reminder", "reversible": True,
                "label": f"新加了提醒「{r}」",
                "undo": {"op": "remove_reminder", "text": r},
            })

    # 待办要按 (内容, 是否已完成) 计次：同一条既可能被划掉也可能被重新打开，
    # add_todo 只保证未完成的条目不重复，已完成的可以同名好几条。
    b_todos = Counter((t["text"], t["done"]) for t in before.get("todos", []))
    a_todos = Counter((t["text"], t["done"]) for t in after.get("todos", []))
    gained, lost = a_todos - b_todos, b_todos - a_todos
    # 「本来就有没有这条待办」只认内容：把一条旧待办重新打开不是新增，
    # 误判成新增会让回退去删一条我们接手前就存在的条目。
    was_there = {text for (text, _done) in b_todos}
    still_there = {text for (text, _done) in a_todos}

    for (text, done) in sorted(gained):
        if not done and text not in was_there:
            items.append({
                "kind": "todo_add", "reversible": True,
                "label": f"新待办「{text}」",
                "undo": {"op": "remove_todo", "text": text},
            })
    for (text, done) in sorted(lost):
        if not done and (text, True) in gained:
            items.append({
                "kind": "todo_done", "reversible": True,
                "label": f"划掉了待办「{text}」",
                "undo": {"op": "reopen_todo", "text": text},
            })
        elif not done and text not in still_there:
            items.append({
                "kind": "todo_gone", "reversible": False,
                "label": f"待办「{text}」不见了（不是我划的，我不瞎补回来）",
                "undo": None,
            })

    for p in after.get("deliveries", []):
        if p not in before.get("deliveries", []):
            items.append({
                "kind": "delivery", "reversible": True,
                "label": f"生成了文件 {os.path.basename(p)}",
                "path": p,
                "undo": {"op": "delete_file", "path": p},
            })

    items.sort(key=lambda it: _UNDO_ORDER.get(it["kind"], 9))
    return items


def blind_spots(action_names) -> list:
    """本轮跑过的动作里，有哪些后果是快照看不见的（附人话原因）。"""
    return [{"action": name, "reason": BLIND_SPOTS[name]}
            for name in action_names if name in BLIND_SPOTS]


def undo_ops(items) -> list:
    """可回退项 → 操作序列（不可逆项在这里被丢掉，由 summarize 负责说明）。"""
    return [it["undo"] for it in items
            if it.get("reversible") and it.get("undo")]


def deletable(path: str, size: int = 0, started_at: float = 0.0,
              mtime: float = 0.0) -> tuple:
    """一个文件能不能被回退删掉 → (可以?, 不能的理由)。

    四道关卡，全过才删：开关、后缀白名单、大小上限、以及**必须是本轮之后
    才诞生的**（mtime 早于本轮开始时间说明这是我们接手前就存在的用户文件，
    哪怕它出现在了交付记录里也不许删）。
    """
    if not config.UNDO_DELETE_FILES:
        return False, "回退删文件已在设置里关闭"
    ext = os.path.splitext(path or "")[1].lower()
    if ext not in config.UNDO_FILE_EXTS:
        return False, f"后缀 {ext or '(无)'} 不在可删白名单里"
    if size > config.UNDO_DELETE_MAX_BYTES:
        return False, (f"文件 {size // 1024 // 1024}MB 超过回退上限 "
                       f"{config.UNDO_DELETE_MAX_BYTES // 1024 // 1024}MB")
    if started_at and mtime and mtime < started_at - 1.0:
        return False, "这个文件不是本轮生成的（创建时间早于这轮任务），我不动"
    return True, ""


def apply_ops(ops, api: dict, started_at: float = 0.0) -> list:
    """按顺序执行逆操作 → [{"op","ok","note","label"}]，每项都说清成没成、为什么。

    判据全在这里（包括删文件前的四道关卡），`api` 只提供手：set_volume /
    forget_fact / remove_todo / reopen_todo / remove_reminder / unlink /
    drop_delivery。这样"撤销"本身也能离线单测——用假手就能测出真手会不会
    把撤不成的事说成撤成了。

    任何一只手抛异常都算这一项失败，但不中断后面的项：撤到一半整个停摆，
    比撤得少更难收拾。
    """
    out = []
    for op in ops or []:
        kind = (op or {}).get("op", "")
        ok, note = False, ""
        if kind == "delete_file":
            path = op.get("path", "")
            try:
                size = os.path.getsize(path)
                mtime = os.path.getmtime(path)
            except OSError:
                size, mtime = 0, 0.0
            allowed, why = deletable(path, size, started_at, mtime)
            if not allowed:
                ok, note = False, why
            else:
                try:
                    api["unlink"](path)
                    ok = True
                except (OSError, KeyError) as e:
                    ok, note = False, f"文件没删掉：{e}"
                if ok:
                    # 文件没了，交付记录也得跟着撤，否则清单上留一条指向空气的行
                    try:
                        if not api["drop_delivery"](path):
                            note = "文件删了，但交付清单里那条没找到"
                    except (OSError, KeyError) as e:
                        note = f"文件删了，交付清单没更新：{e}"
        else:
            handler = api.get(kind)
            if handler is None:
                ok, note = False, f"没有 {kind} 的回退入口"
            else:
                arg = op.get("percent", "") if kind == "set_volume" \
                    else op.get("text", "")
                try:
                    ok = bool(handler(arg))
                    note = "" if ok else "这一项在本机上没生效"
                except Exception as e:  # noqa: BLE001 单项失败不拖垮整串回退
                    ok, note = False, f"执行出错：{e}"
        out.append({"op": kind, "ok": ok, "note": note, "label": op})
    return out


def summarize(items, spots=()) -> list:
    """回退项 + 撤不掉的东西 → 给人看的几行字。"""
    lines = []
    for it in items:
        mark = "可撤" if it.get("reversible") else "撤不回"
        lines.append(f"[{mark}] {it['label']}")
    for s in spots:
        lines.append(f"[撤不回] {s['reason']}")
    return lines


def coverage(valid_actions) -> dict:
    """把动作全集按「这轮做完能不能撤」分三类，供 README/自检引用。

    分类靠两张表：`_TOUCHES` 里的可观测面 → 可撤；`BLIND_SPOTS` → 撤不回；
    `NO_EFFECT` → 无副作用。**没被任何一张表收进来的动作会进 unknown**，
    调用方应当把它当错误处理（新增动作忘了归类，比分类分错更常见）。
    """
    out = {"reversible": [], "blind": [], "no_effect": [], "undo": [],
           "unknown": []}
    for name in sorted(set(valid_actions)):
        if name in UNDO_ITSELF:
            out["undo"].append(name)
        elif name in _TOUCHES:
            out["reversible"].append(name)
        elif name in BLIND_SPOTS:
            out["blind"].append(name)
        elif name in NO_EFFECT:
            out["no_effect"].append(name)
        else:
            out["unknown"].append(name)
    return out
