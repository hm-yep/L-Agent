# -*- coding: utf-8 -*-
"""工具契约的统一导出层：一份契约，三种消费形态。

数据只来自 action_registry.ACTION_SPECS（名称 / 描述 / 必填参数）和
effect_spec.SPECS（前置 / 后置 / 可逆 / 幂等 / 逆操作），本模块不重新定义
任何动作的行为，只做格式转换：

  * export_openai_tools()：OpenAI Function Calling 的 tools 数组；
  * export_mcp_tools()：MCP tools/list 的返回格式，带 annotations；
  * export_prompt_text()：给模型看的人话版工具清单。

为什么要这一层：
    动作契约原本只服务本项目内部（提示词 + 执行分发 + 撤销账本）。把它
    导出成标准 schema 后，同一套工具可以被外部编排器、MCP 客户端或别的
    模型直接消费，契约才真正成为「对外接口」而不只是内部约定。
"""

import json

from framework import action_registry
from framework.effect_spec import SPECS as EFFECT_SPECS

# create_doc 的规范类型；测试会与 effect_spec._DOC_ALIASES 交叉验证，
# 防止这里和撤销契约各说一套。
DOC_TYPES = ("ppt", "doc", "xlsx", "pdf", "html")

# ACTION_SPECS 只登记必填参数；少量动作有可选参数，在这里补充。
# 键必须能在 action_parser.PARAM_ALIASES 里找到（测试交叉验证，防止编造）。
OPTIONAL_PARAMS = {
    "shutdown": ["delay"],
    "restart": ["delay"],
    "see_screen": ["question"],
    "show_card": ["size"],
}

# 参数名 → JSON Schema 片段；未收录的参数按 string 处理。
PARAM_SCHEMAS = {
    "volume": {"type": "integer", "minimum": 0, "maximum": 100,
               "description": "目标音量百分比（0-100）"},
    "delay": {"type": "integer", "minimum": 0, "maximum": 3600,
              "description": "延时秒数，0 表示立即执行"},
    "doc_type": {"type": "string", "enum": list(DOC_TYPES),
                 "description": "文档类型：ppt / doc / xlsx / pdf / html"},
    "path": {"type": "string", "description": "本机文件或文件夹路径"},
    "anchor": {"type": "string", "description": "桌面锚点位置"},
    "skill": {"type": "string", "description": "已保存的技能名称"},
}

# 副作用类别 → MCP 工具注解。MCP 的 annotations 正好是这三个提示，
# 所以撤销契约可以直接映射过去，不需要另写一份安全分级。
_EFFECT_HINTS = {
    "no_effect": {"readOnlyHint": True, "destructiveHint": False},
    "reversible": {"readOnlyHint": False, "destructiveHint": False},
    "restorable": {"readOnlyHint": False, "destructiveHint": False},
    "blind": {"readOnlyHint": False, "destructiveHint": True},
    "undo": {"readOnlyHint": False, "destructiveHint": False},
}

_SAFETY = {
    "no_effect": "low",
    "reversible": "medium",
    "restorable": "medium",
    "blind": "high",
    "undo": "low",
}

_FALLBACK_EFFECT = {
    "pre": "", "post": "", "reversible": False, "idempotent": False,
    "inverse": None,
}


def parameters_schema(action: str) -> dict:
    """把必填 + 可选参数拼成 JSON Schema（object）。"""
    spec = action_registry.ACTION_SPECS[action]
    required = list(spec.get("params", []))
    optional = list(OPTIONAL_PARAMS.get(action, []))
    properties = {}
    for key in required + optional:
        properties[key] = dict(
            PARAM_SCHEMAS.get(key, {"type": "string", "description": key}))
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def tool_schema(action: str) -> dict:
    """单个工具的完整契约：描述、参数、前后置条件、可逆性、安全等级。"""
    spec = action_registry.ACTION_SPECS[action]
    effect = EFFECT_SPECS.get(action) or _FALLBACK_EFFECT
    hints = dict(_EFFECT_HINTS.get(spec["effect"],
                                   _EFFECT_HINTS["no_effect"]))
    hints["idempotentHint"] = bool(effect["idempotent"])
    return {
        "name": action,
        "description": spec["desc"],
        "parameters": parameters_schema(action),
        "precondition": effect["pre"],
        "postcondition": effect["post"],
        "reversible": bool(effect["reversible"]),
        "idempotent": bool(effect["idempotent"]),
        "inverse": effect["inverse"],
        "effect": spec["effect"],
        "safety": _SAFETY.get(spec["effect"], "medium"),
        "skill_safe": bool(spec.get("skill", False)),
        "annotations": hints,
    }


def all_tool_schemas() -> list:
    return [tool_schema(name) for name in sorted(action_registry.ACTION_SPECS)]


def export_openai_tools() -> list:
    """OpenAI Function Calling 的 tools 数组。"""
    return [{
        "type": "function",
        "function": {
            "name": item["name"],
            "description": item["description"],
            "parameters": item["parameters"],
        },
    } for item in all_tool_schemas()]


def export_mcp_tools() -> list:
    """MCP tools/list 的返回格式：inputSchema + annotations。"""
    return [{
        "name": item["name"],
        "description": item["description"],
        "inputSchema": item["parameters"],
        "annotations": item["annotations"],
    } for item in all_tool_schemas()]


def export_prompt_text() -> str:
    """给模型看的人话版清单：描述 + 参数 + 前后置 + 安全等级。"""
    tools = all_tool_schemas()
    lines = ["【工具契约】共 %d 个工具：" % len(tools)]
    for item in tools:
        params = ", ".join(item["parameters"].get("required", [])) or "无"
        lines.append(
            f"- {item['name']}：{item['description']}；必填 {params}；"
            f"前置 {item['precondition']}；后置 {item['postcondition']}；"
            f"可逆 {'是' if item['reversible'] else '否'}；"
            f"幂等 {'是' if item['idempotent'] else '否'}；"
            f"风险 {item['safety']}")
    return "\n".join(lines)


def export_all() -> dict:
    return {
        "openai": export_openai_tools(),
        "mcp": export_mcp_tools(),
        "prompt": export_prompt_text(),
    }


def dumps(payload) -> str:
    """统一 JSON 序列化口径：中文不转义，缩进 2，便于人读和 diff。"""
    return json.dumps(payload, ensure_ascii=False, indent=2)
