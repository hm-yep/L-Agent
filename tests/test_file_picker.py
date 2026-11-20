# -*- coding: utf-8 -*-
"""单元测试：文件选择入口的纯逻辑（过滤器 / 指令拼装）

只测不弹窗的部分。picker 本身要开模态框，UI 接线在
test_workspace_window.py 里用 monkeypatch 替换掉。
"""

import os

from framework import path_guard
from ui import file_picker


class TestComposeCommand:
    def test_table_routes_to_analyze(self):
        """表格类要拼成「分析一下」，否则 analyze_data 永远走不到。"""
        for name in ("销量.csv", "报表.xlsx", "导出.tsv", "预算.xlsm"):
            assert file_picker.compose_command(name).startswith("分析一下"), name

    def test_document_routes_to_read(self):
        for name in ("笔记.md", "报告.pdf", "方案.docx", "代码.py", "数据.json"):
            assert file_picker.compose_command(name).startswith("读一下"), name

    def test_old_xls_is_read_not_analyze(self):
        """老格式 .xls 不给 analyze：data_tool 明确拒绝它，要求另存 xlsx。"""
        assert file_picker.compose_command("旧表.xls").startswith("读一下")

    def test_space_in_path_survives(self):
        """Windows 用户名带空格：引号包住整条路径，一个字都不许丢。"""
        p = "C:/Users/Wang zhi de/Desktop/主要项目/笔记.md"
        cmd = file_picker.compose_command(p)
        assert p in cmd
        assert cmd.endswith('"')

    def test_quotes_already_wrapped_are_not_doubled(self):
        p = 'C:/a b/c.md'
        cmd = file_picker.compose_command('"%s"' % p)
        assert cmd.count('"') == 2, cmd
        assert cmd == '读一下 "C:/a b/c.md"'

    def test_empty_input_gives_empty_command(self):
        """用户取消选择时不能拼出「读一下 ""」这种空指令。"""
        assert file_picker.compose_command("") == ""
        assert file_picker.compose_command("   ") == ""
        assert file_picker.compose_command(None) == ""

    def test_case_insensitive_extension(self):
        assert file_picker.compose_command("数据.CSV").startswith("分析一下")


class TestFileFilter:
    def test_only_guard_allowed_exts(self):
        """过滤器里的后缀必须是 path_guard 真允许的那批，不能自己另写一份。"""
        import re
        flt = file_picker.file_filter()
        allow = set(path_guard.advertised_exts())
        listed = {m.lstrip("*").lower() for m in re.findall(r"\*\.[a-z0-9]+", flt)}
        assert listed, flt
        assert listed <= allow, listed - allow

    def test_no_all_files_escape_hatch(self):
        """不提供「所有文件」：允许之外的后缀选了也过不了 path_guard。"""
        assert "所有文件" not in file_picker.file_filter()
        assert "*.exe" not in file_picker.file_filter()

    def test_has_document_and_table_groups(self):
        flt = file_picker.file_filter()
        assert "文档" in flt and "表格" in flt

    def test_every_allowed_ext_appears(self):
        flt = file_picker.file_filter()
        for ext in path_guard.advertised_exts():
            assert "*" + ext in flt, "过滤器漏了允许的后缀: " + ext
