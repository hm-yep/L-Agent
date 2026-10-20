# -*- coding: utf-8 -*-
"""数据分析（analyze_data / data_tool）单测：读数、统计、出图、动作参数容错"""

import os

import pandas as pd
import pytest

from framework import config, data_tool
from framework.action_parser import normalize_action, parse_actions


@pytest.fixture
def sales_csv(tmp_path):
    p = tmp_path / "月度销售.csv"
    pd.DataFrame({
        "月份": ["1月", "2月", "3月", "4月", "5月", "6月"],
        "区域": ["华东", "华北", "华东", "华南", "华北", "华东"],
        "销售额": [120.5, 88.0, 150.0, 99.9, 130.0, 210.0],
        "成本": [80.0, 60.0, 95.0, 70.0, 100.0, 140.0],
        "退货数": [3.0, 1.0, 0.0, 2.0, float("nan"), 5.0],
    }).to_csv(p, index=False, encoding="utf-8-sig")
    return str(p)


@pytest.fixture
def png_dir(tmp_path, monkeypatch):
    """把图表输出改到临时目录，测试不往项目 data/ 里堆文件"""
    out = tmp_path / "analysis"
    out.mkdir()
    monkeypatch.setattr(data_tool, "app_data_dir", lambda: str(out))
    return str(out)


class TestLoad:
    def test_read_csv(self, sales_csv):
        df = data_tool.load_table(sales_csv)
        assert len(df) == 6 and "销售额" in df.columns

    def test_read_xlsx(self, tmp_path):
        p = tmp_path / "成绩.xlsx"
        pd.DataFrame({"姓名": ["张三", "李四"], "绩点": [3.8, 2.9]}).to_excel(
            p, index=False)
        assert len(data_tool.load_table(str(p))) == 2

    def test_gbk_csv(self, tmp_path):
        p = tmp_path / "gbk.csv"
        p.write_bytes("城市,销量\n杭州,10\n".encode("gbk"))
        assert list(data_tool.load_table(str(p))["城市"]) == ["杭州"]

    def test_unsupported_ext(self, tmp_path):
        p = tmp_path / "a.doc"
        p.write_text("x")
        with pytest.raises(ValueError):
            data_tool.load_table(str(p))

    def test_missing_file(self, tmp_path):
        with pytest.raises(ValueError):
            data_tool.load_table(str(tmp_path / "没有这个.csv"))

    def test_empty_table(self):
        with pytest.raises(ValueError):
            data_tool.summarize(pd.DataFrame())


class TestSummarize:
    def test_key_numbers(self, sales_csv):
        text = data_tool.summarize(data_tool.load_table(sales_csv))
        assert "6 行 × 5 列" in text
        assert "798.4" in text                 # 销售额合计
        assert "退货数 空 1 个" in text          # 缺失值
        assert "销售额 与 成本" in text          # 相关性配对

    def test_group_by(self, sales_csv):
        text = data_tool.summarize(
            data_tool.load_table(sales_csv), group_by="区域")
        assert "华东:480.5" in text
        assert "第一 华东 占 60%" in text

    def test_group_by_unknown_col(self, sales_csv):
        text = data_tool.summarize(
            data_tool.load_table(sales_csv), group_by="渠道")
        assert "没找到" in text and "可用列" in text

    def test_group_by_zero_total(self, tmp_path):
        # 正负抵消到 0 时不能除零，占比直接给"不可比"
        p = tmp_path / "对冲.csv"
        pd.DataFrame({"区域": ["A", "B"],
                      "净额": [100.0, -100.0]}).to_csv(p, index=False)
        text = data_tool.summarize(data_tool.load_table(str(p)),
                                   group_by="区域")
        assert "占 —" in text

    def test_match_col_fuzzy(self, sales_csv):
        df = data_tool.load_table(sales_csv)
        assert data_tool._match_col(df, "销售额") == "销售额"
        assert data_tool._match_col(df, " 销售 ") in ("销售额", "退货数")
        assert data_tool._match_col(df, "完全不存在的列") is None

    def test_fit_limits_length(self):
        df = pd.DataFrame({f"数值列{i}": [1.5, 2.5, 3.5] for i in range(40)})
        text = data_tool.summarize(df)
        assert len(text) <= config.DATA_SUMMARY_MAX
        assert text.endswith("行统计略）") or "未展开" in text


class TestChart:
    def test_chinese_kind_normalizes(self):
        for raw, want in (("饼图", "pie"), ("折线", "line"),
                          ("bar", "bar"), ("", "bar")):
            assert data_tool.normalize_chart_kind(raw) == want

    def test_chart_off_words(self):
        assert data_tool.chart_wanted("") is True
        assert data_tool.chart_wanted("不要") is False
        assert data_tool.chart_wanted("none") is False
        assert data_tool.chart_wanted("饼图") is True

    def test_render_and_off(self, sales_csv, png_dir):
        res = data_tool.analyze(sales_csv, x_col="月份", y_col="销售额",
                                chart="柱状图")
        assert os.path.isfile(res["chart_path"])
        assert res["chart_path"].startswith(png_dir)
        assert data_tool.analyze(sales_csv, chart="不要")["chart_path"] == ""

    def test_chart_failure_keeps_summary(self, sales_csv, png_dir):
        # 只有一个取值的列画不出图，但统计结论必须照常交付
        p = os.path.join(png_dir, "..", "单值.csv")
        pd.DataFrame({"a": ["x", "x"], "b": [1, 2]}).to_csv(p, index=False)
        res = data_tool.analyze(p, x_col="a")
        assert res["chart_path"] == ""
        assert "2 行 × 2 列" in res["summary"]


class TestActionContract:
    def test_chinese_param_aliases(self):
        a = normalize_action({"action": "analyze_data", "文件": "D:/s.csv",
                              "图表": "饼图", "分组": "区域"})
        assert a == {"action": "analyze_data", "path": "D:/s.csv",
                     "group_by": "区域", "x_col": "", "y_col": "",
                     "chart": "饼图"}

    def test_english_param_aliases(self):
        a = normalize_action({"action": "analyze_data", "path": "D:/s.csv",
                              "x": "月份", "y": "销售额", "chart": "line"})
        assert (a["x_col"], a["y_col"], a["chart"]) == (
            "月份", "销售额", "line")

    def test_missing_path_dropped(self):
        assert normalize_action({"action": "analyze_data"}) is None

    def test_in_action_pool(self):
        acts = parse_actions([{"action": "show_bubble", "text": "我来算算"},
                              {"action": "analyze_data",
                               "path": "D:/s.csv"}])
        assert [a["action"] for a in acts] == ["show_bubble", "analyze_data"]
