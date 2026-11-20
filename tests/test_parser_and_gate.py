# -*- coding: utf-8 -*-
"""单元测试：动作解析 + 质量门 + 搜索结果解析"""

from framework.action_parser import parse_actions, normalize_action
from framework.quality_gate import (assess_search, assess_doc_spec,
                                     extract_keywords)
from framework.web_search import parse_search_payload


class TestActionParser:
    def test_valid_open_app(self):
        actions = parse_actions([{"action": "open_app", "app": "微信"}])
        assert len(actions) == 1
        assert actions[0]["action"] == "open_app"
        assert actions[0]["app"] == "微信"

    def test_illegal_action_dropped(self):
        actions = parse_actions([{"action": "fly_to_moon"}])
        assert len(actions) == 1
        assert actions[0]["action"] == "show_bubble"

    def test_missing_required_param_dropped(self):
        assert normalize_action({"action": "open_app"}) is None

    def test_param_alias_text(self):
        actions = parse_actions([{"action": "show_bubble", "content": "你好"}])
        assert actions[0]["text"] == "你好"

    def test_max_12_actions(self):
        raw = [{"action": "show_bubble", "text": str(i)} for i in range(20)]
        actions = parse_actions(raw)
        assert len(actions) <= 12

    def test_path_quotes_stripped_so_spaced_path_survives(self):
        """带空格的路径被引号包住是常态，进 path_guard 前必须剥掉。

        不剥的后果不是「格式问题」，是「文件不存在」——错在引号，不在文件，
        而报错信息会把用户引到错误的方向（去怀疑路径写错了）。
        """
        p = "C:/Users/Wang zhi de/Desktop/主要项目/笔记.md"
        actions = parse_actions([{"action": "read_doc",
                                  "path": '"%s"' % p}])
        assert actions[0]["path"] == p

    def test_path_brackets_and_curly_quotes_stripped(self):
        for wrapped in ("「D:/销售 数据.csv」", "“D:/销售 数据.csv”",
                        "‘D:/销售 数据.csv’"):
            actions = parse_actions([{"action": "analyze_data",
                                      "path": wrapped}])
            assert actions[0]["path"] == "D:/销售 数据.csv", wrapped

    def test_paths_array_quotes_stripped(self):
        actions = parse_actions([{
            "action": "read_doc",
            "paths": ['"a b.md"', "「c.md」", "d.md"],
        }])
        assert actions[0]["paths"] == ["a b.md", "c.md", "d.md"]

    def test_read_doc_paths_without_single_path(self):
        """批量读：paths 数组能替代 path，否则整条动作会被必填校验剔掉。"""
        actions = parse_actions([{
            "action": "read_doc",
            "paths": ["D:/资料/a.txt", "D:/资料/b.docx"],
        }])
        assert len(actions) == 1
        assert actions[0]["action"] == "read_doc"
        assert actions[0]["paths"] == ["D:/资料/a.txt", "D:/资料/b.docx"]
        assert "path" not in actions[0]

    def test_show_plan_steps_string_split(self):
        actions = parse_actions([{
            "action": "show_plan",
            "steps": "1. 搜集资料；2. 整理内容；3. 产出报告"
        }])
        assert actions[0]["action"] == "show_plan"
        assert len(actions[0]["steps"]) == 3


class TestQualityGate:
    def test_extract_keywords(self):
        kws = extract_keywords("新能源汽车 2025 销量")
        assert "新能" in kws or "汽车" in kws
        assert "2025" in kws

    def test_search_empty(self):
        assert assess_search("AI", [])["ok"] is False

    def test_search_zero_overlap(self):
        results = [{"title": "天气预报", "content": "今天晴，气温 26 度"}]
        gate = assess_search("新能源汽车市场", results)
        assert gate["ok"] is False

    def test_search_ok(self):
        results = [
            {"title": "新能源汽车销量",
             "content": "2025 年新能源汽车销量同比增长 30%，"
                        "渗透率首次突破五成"},
            {"title": "新能源汽车市场",
             "content": "新能源汽车市场规模持续扩大，出口量创新高"},
        ]
        gate = assess_search("新能源汽车", results)
        assert gate["ok"] is True

    def test_doc_ppt_too_thin(self):
        spec = {"slides": [{"title": "只有一页", "bullets": ["短"]}]}
        assert assess_doc_spec("ppt", spec)["ok"] is False

    def test_doc_ppt_ok(self):
        spec = {"slides": [
            {"title": "第一页", "bullets": ["这是第一条完整要点内容", "这是第二条完整要点内容"]},
            {"title": "第二页", "bullets": ["这是第三条完整要点内容", "这是第四条完整要点内容"]},
        ]}
        assert assess_doc_spec("ppt", spec)["ok"] is True
        pdf_spec = {"sections": [
            {"heading": "摘要", "paragraphs": ["第一段完整内容", "第二段完整内容"]},
            {"heading": "计划", "paragraphs": ["第三段完整内容"]},
        ]}
        assert assess_doc_spec("pdf", pdf_spec)["ok"] is True
        assert assess_doc_spec(
            "pdf", {"sections": pdf_spec["sections"][:1]})["ok"] is False


class TestSearchParser:
    def test_parse_tools_format(self):
        data = {"choices": [{"message": {"tools": [
            {"search_result": [{"title": "T", "content": "C", "link": "L"}]}
        ]}}]}
        results = parse_search_payload(data)
        assert len(results) == 1
        assert results[0]["title"] == "T"

    def test_parse_empty(self):
        assert parse_search_payload({}) == []