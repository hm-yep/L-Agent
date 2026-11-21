# -*- coding: utf-8 -*-
"""工具契约导出层测试：三种格式同源、字段完整、注解映射正确。"""

import json

from framework import (
    action_parser, action_registry, effect_spec, tool_schema)


def test_every_action_has_schema():
    """每个动作都必须能导出，且三份数据源的键集合完全一致。"""
    names = {item["name"] for item in tool_schema.all_tool_schemas()}
    assert names == action_registry.all_actions()
    assert names == set(effect_spec.SPECS)


def test_required_params_match_parser():
    """导出的必填参数必须与解析器一致，不能另起一套。"""
    for item in tool_schema.all_tool_schemas():
        got = item["parameters"].get("required", [])
        want = action_parser.REQUIRED_PARAMS.get(item["name"], [])
        assert got == want, item["name"]


def test_openai_shape():
    tools = tool_schema.export_openai_tools()
    assert len(tools) == len(action_registry.ACTION_SPECS)
    for item in tools:
        assert item["type"] == "function"
        function = item["function"]
        assert function["name"]
        assert function["description"]
        assert function["parameters"]["type"] == "object"


def test_mcp_shape_and_annotations():
    """MCP 的 inputSchema 与 annotations 必须由契约直接推导。"""
    tools = tool_schema.export_mcp_tools()
    assert len(tools) == len(action_registry.ACTION_SPECS)
    for item in tools:
        assert item["inputSchema"]["type"] == "object"
        hints = item["annotations"]
        effect = action_registry.effect_class(item["name"])
        if effect == "no_effect":
            assert hints["readOnlyHint"] is True
            assert hints["destructiveHint"] is False
        if effect == "blind":
            assert hints["destructiveHint"] is True
        assert "idempotentHint" in hints


def test_doc_type_enum_matches_effect_spec():
    """create_doc 的类型枚举不能和撤销契约的类型表脱节。"""
    params = tool_schema.parameters_schema("create_doc")
    assert params["properties"]["doc_type"]["enum"] == list(
        tool_schema.DOC_TYPES)
    assert set(tool_schema.DOC_TYPES) == set(effect_spec._DOC_ALIASES)


def test_optional_params_are_known():
    """可选参数必须是真实存在的参数名，不允许凭空编造。"""
    for name, params in tool_schema.OPTIONAL_PARAMS.items():
        assert name in action_registry.ACTION_SPECS
        for key in params:
            assert key in action_parser.PARAM_ALIASES


def test_exports_are_json_serializable():
    payload = tool_schema.export_all()
    text = json.dumps(payload, ensure_ascii=False)
    assert "openai" in payload and "mcp" in payload and "prompt" in payload
    assert "工具契约" in text
