# -*- coding: utf-8 -*-
"""动作解析器：校验 AI 返回的动作序列

动作池包含三十二个动作，AI 返回的 JSON 经本校验后
生成可执行的标准化动作列表（非法项被剔除/修正）。
"""

import re

from framework.doc_generator import normalize_doc_type
from framework.utils import logger

# 合法动作集合
VALID_ACTIONS = {
    "search_app", "open_app", "show_bubble", "jump",
    "think", "show_card", "move_to", "idle", "screenshot",
    "set_volume",
    # ---- 电源操作 ----
    "shutdown", "restart", "lock", "cancel_shutdown",
    # ---- 文档生成（PPT / Word / Excel）----
    "create_doc",
    # ---- 屏幕视觉理解 / 传统 CV 定位 / 长期记忆 / 任务规划 / 提醒待办 ----
    "see_screen", "screen_locate", "remember", "show_plan",
    "remind", "todo_add", "todo_done", "todo_list",
    # ---- 联网检索 / 本机文档阅读 / 工作日志 / 工作流编排 ----
    "web_search", "read_doc", "list_dir", "edit_doc", "work_log",
    "run_workflow",
    # ---- 数据分析（表格统计与出图）----
    "analyze_data",
    # ---- 后悔药（撤销上一轮）/ 技能层（存一次，以后零规划重放）----
    "undo_last", "save_skill", "run_skill",
}

# 各动作的必填参数
REQUIRED_PARAMS = {
    "search_app": ["app"],
    "open_app": ["app"],
    "show_bubble": ["text"],
    "show_card": ["title"],
    "move_to": ["anchor"],
    "set_volume": ["volume"],
    # shutdown / restart / lock / cancel_shutdown 都无必填参数（delay 可选）
    "create_doc": ["doc_type"],
    # see_screen 的 question 不设为必填：模型偶尔不给参数时也应继续看屏幕
    # （缺参剔除会让用户只看到"收到"就没有后续），question 由执行层兜底
    # screen_locate 必须给 target，否则不知道该在屏幕上找什么
    "screen_locate": ["target"],
    "remember": ["fact"],
    "remind": ["text"],
    "todo_add": ["text"],
    "web_search": ["query"],
    "read_doc": ["path"],
    "list_dir": ["path"],
    "edit_doc": ["path", "instruction"],
    "run_workflow": ["name"],
    "analyze_data": ["path"],
    "run_skill": ["skill"],
    # undo_last / save_skill 无参数：撤销对象是上一轮的账，存对象是这一轮的轨迹
    # show_plan 的 steps 必须是非空列表，走专用校验；
    # todo_done 的 index/text 二选一，由执行层处理
}

# move_to 合法锚点
VALID_ANCHORS = {"top_left", "top_right", "bottom_left", "bottom_right",
                 "center", "taskbar", "tray"}

# 参数名容错：模型偶尔会把 text 写成 content/message/回复 等，逐个别名兜底
PARAM_ALIASES = {
    "text":   ["text", "content", "message", "reply", "answer",
               "内容", "文本", "回复", "回答", "话"],
    "app":    ["app", "name", "application", "应用", "程序", "软件"],
    "title":  ["title", "标题", "name"],
    "path":   ["path", "content", "路径", "完整路径", "文件", "文件路径",
               "filepath", "file", "filename", "内容"],
    "size":   ["size", "大小", "备注"],
    "anchor": ["anchor", "position", "位置", "锚点", "目标"],
    "volume": ["volume", "音量", "percent", "值", "数值", "level", "音量值"],
    "delay":  ["delay", "time", "seconds", "延时", "延迟", "秒", "等待"],
    # 文档生成（create_doc）
    "doc_type": ["doc_type", "type", "filetype", "kind", "文档类型", "类型"],
    "filename": ["filename", "file_name", "name", "文件名", "名称"],
    "content":  ["content", "data", "spec", "内容", "内容结构"],
    # 屏幕视觉 / 传统 CV 定位 / 长期记忆 / 提醒待办
    "question": ["question", "问题", "提问", "想知道"],
    "target":   ["target", "target_desc", "description", "目标", "控件",
                 "目标控件", "描述", "要找的东西"],
    "template": ["template", "template_path", "模板", "模板图", "模板路径"],
    "fact":     ["fact", "info", "信息", "记忆", "事实"],
    "seconds":  ["seconds", "second", "sec", "延时秒数", "秒", "等待秒"],
    "minutes":  ["minutes", "minute", "delay", "延时", "分钟", "时间"],
    "index":    ["index", "no", "number", "id", "序号", "编号"],
    # 联网检索 / 读文档 / 工作流
    "query":    ["query", "keyword", "search", "搜索词", "关键词", "问题", "查询"],
    "name":     ["name", "workflow", "template", "模板", "工作流"],
    "skill":    ["skill", "skill_name", "技能", "技能名", "name"],
    # 改写指令（edit_doc）：模型常把「怎么改」写成 content / 要求 / 指令
    "instruction": ["instruction", "content", "要求", "怎么改", "改什么",
                    "修改要求", "指令", "说明", "改动", "edit", "修改说明"],
    # 文档保存位置（create_doc 可选）
    "location": ["location", "save_to", "save_dir",
                 "保存位置", "保存到", "存放位置", "位置"],
    # 数据分析（analyze_data 可选）
    "group_by": ["group_by", "groupby", "group", "by", "分组", "分组列",
                 "按列分组", "分类列"],
    "x_col":    ["x_col", "xcol", "x_column", "x", "横轴", "横坐标",
                "x轴", "类别列", "维度列"],
    "y_col":    ["y_col", "ycol", "y_column", "y", "纵轴", "纵坐标",
                "y轴", "数值列", "指标列"],
    "chart":    ["chart", "chart_type", "chart_kind", "图表", "图表类型",
                "图", "图类型"],
}


def _get_param(item: dict, param: str) -> str:
    """按别名表从动作字典里取参数值（跳过空值），取不到返回空串"""
    for key in PARAM_ALIASES.get(param, [param]):
        value = item.get(key)
        if value is not None and value != "":
            return str(value).strip()
    return ""


def parse_actions(raw_actions) -> list:
    """
    校验并标准化 AI 返回的动作序列。
    :param raw_actions: AI 返回的原始 list[dict]
    :return: 合法动作列表 [dict]，非法动作被剔除
    """
    if not isinstance(raw_actions, list):
        logger.warning("[Parser] 动作序列不是列表，丢弃")
        return []

    valid = []
    # 序列长度上限：防止 AI 返回超长序列拖慢执行
    for item in raw_actions[:12]:
        action = normalize_action(item)
        if action is not None:
            valid.append(action)

    # 保底：解析结果为空时，先从原始返回里抢救出可显示的文字，
    # 实在抢救不出来才用提示语（绝不显示干巴巴的"……"让用户等下文）
    if not valid:
        rescued = _rescue_text(raw_actions)
        valid = [{"action": "show_bubble", "text": rescued}]

    logger.info(f"[Parser] 解析出 {len(valid)} 个动作: "
                + ",".join(a["action"] for a in valid))
    return valid


def _rescue_text(raw_actions) -> str:
    """从模型返回的原始结构里尽量抢救出一段人话（200 字内）"""
    for item in raw_actions[:12] if isinstance(raw_actions, list) else []:
        if isinstance(item, dict):
            text = _get_param(item, "text")
            if text:
                return text[:200]
        elif isinstance(item, str) and item.strip():
            return item.strip()[:200]
    return "我刚才有点走神没听清，麻烦再说一遍好吗？"


_QUOTE_PAIRS = (('"', '"'), ("'", "'"), ("「", "」"), ("“", "”"), ("‘", "’"))


def _clean_path(value) -> str:
    """剥掉路径两端的空白与成对引号。

    为什么需要：Windows 用户名带空格时（`C:/Users/Wang zhi de/...`），用户
    和模型都会很自然地把整条路径用引号或「」包起来。原样交给 path_guard
    会被当成「文件不存在」——错在引号，不在文件。这里统一剥一次，
    读取与分析的入口都不必各自处理。
    """
    s = str(value or "").strip()
    for left, right in _QUOTE_PAIRS:
        if len(s) >= 2 and s[0] == left and s[-1] == right:
            s = s[1:-1].strip()
            break
    return s


def normalize_action(item) -> dict | None:
    """校验单个动作，非法返回 None"""
    if not isinstance(item, dict):
        return None
    action = str(item.get("action", "")).strip().lower()
    if action not in VALID_ACTIONS:
        logger.warning(f"[Parser] 非法动作被剔除: {action}")
        return None

    normalized = {"action": action}

    # 必填参数校验（带别名容错）
    for param in REQUIRED_PARAMS.get(action, []):
        # read_doc 可一次读多份：paths 数组可以替代单个 path。模型常把「这几份」
        # 写成数组，若仍强制 path 会把整批动作剔掉，批量入口就永远走不到。
        if action == "read_doc" and param == "path":
            batch = item.get("paths")
            if isinstance(batch, (list, tuple)):
                paths = [str(p).strip().strip('"') for p in batch
                         if str(p or "").strip()]
                if paths:
                    normalized["paths"] = paths[:12]
                    continue
        value = _get_param(item, param)
        if not value:
            logger.warning(f"[Parser] 动作 {action} 缺少参数 {param}，剔除")
            return None
        normalized[param] = value

    # 可选参数
    if action == "see_screen":
        normalized["question"] = _get_param(item, "question")   # 可缺省
    if action == "show_card":
        normalized.setdefault("path", _get_param(item, "path"))
        normalized.setdefault("size", _get_param(item, "size"))
    if action == "move_to" and normalized["anchor"] not in VALID_ANCHORS:
        normalized["anchor"] = "center"
    if action == "set_volume":
        normalized.setdefault("app", _get_param(item, "app"))   # 空=系统音量

    # 路径类参数统一去引号：带空格的路径被引号包住是常态，不该判成文件不存在
    if "path" in normalized:
        normalized["path"] = _clean_path(normalized["path"])
    if "paths" in normalized:
        normalized["paths"] = [_clean_path(p) for p in normalized["paths"]
                               if _clean_path(p)]

    # ---- 电源操作：delay 可选（秒），缺省交给 power_controller 用配置默认值 ----
    if action in ("shutdown", "restart"):
        normalized["delay"] = _get_param(item, "delay") or ""

    # ---- 文档生成：doc_type 归一化；content 必须是 dict（结构校验在执行层做）----
    if action == "create_doc":
        kind = normalize_doc_type(normalized["doc_type"])
        if not kind:
            logger.warning(f"[Parser] create_doc 类型无法识别: "
                           f"{normalized['doc_type']}，剔除")
            return None
        normalized["doc_type"] = kind
        content = item.get("content")
        normalized["content"] = content if isinstance(content, dict) else {}
        normalized["filename"] = _get_param(item, "filename")
        # 用户样式定制（颜色/字号/行距/字体）与保存位置（桌面/文档/下载）
        st = item.get("style") if isinstance(item.get("style"), dict) else {}
        if st:
            normalized["style"] = st
        normalized["location"] = _get_param(item, "location")

    # ---- 任务规划：steps 必须整理成非空列表（字符串自动拆分）----
    if action == "show_plan":
        steps = item.get("steps")
        if isinstance(steps, str):
            steps = _split_steps(steps)
        elif isinstance(steps, list):
            steps = [str(s).strip() for s in steps]
        else:
            steps = []
        steps = [s for s in steps if s][:8]
        if not steps:
            logger.warning("[Parser] show_plan 没有有效步骤，剔除")
            return None
        normalized["steps"] = steps

    # ---- 数据分析：分组列 / 横纵轴 / 图表类型都可缺省（缺省交给执行层）----
    if action == "analyze_data":
        for param in ("group_by", "x_col", "y_col", "chart"):
            normalized[param] = _get_param(item, param)

    # ---- 联网搜索：count 可选（1~10 条），非法就交给执行层用默认值 ----
    if action == "web_search":
        count = _get_param(item, "count")
        if count:
            try:
                normalized["count"] = max(1, min(10, int(float(count))))
            except ValueError:
                pass

    # ---- 定时提醒：优先 seconds（秒，模型换算准），兼容 minutes（分钟）----
    if action == "remind":
        sec_raw = _get_param(item, "seconds")
        min_raw = _get_param(item, "minutes")
        if sec_raw:
            try:
                normalized["seconds"] = float(sec_raw)
            except ValueError:
                pass
        elif min_raw:
            try:
                normalized["minutes"] = float(min_raw)
            except ValueError:
                pass

    # ---- 完成待办：index 尽量转成整数（转不了交执行层按文本匹配）----
    if action == "todo_done":
        raw = _get_param(item, "index")
        try:
            normalized["index"] = int(float(raw))
        except (TypeError, ValueError):
            normalized["index"] = 0
        normalized["text"] = _get_param(item, "text")

    return normalized


def _split_steps(raw: str) -> list:
    """把一段文字拆成步骤列表：按换行/分号切分，去掉'1.' '第X步'等序号前缀"""
    parts = re.split(r"[\n;；]+", str(raw))
    out = []
    for part in parts:
        part = re.sub(
            r"^[\s　]*(?:第?[0-9０-９一二三四五六七八九十]{1,3}"
            r"\s*[、.．:：)）步]|[-–—•·])\s*", "", part)
        part = part.strip()
        if part:
            out.append(part)
    return out


def is_action_sequence(text: str) -> bool:
    """简单判断用户输入是否需要走 AI 动作流程（而非纯闲聊）"""
    return True  # 当前所有输入都交给 AI 决定动作
