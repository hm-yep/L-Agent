# -*- coding: utf-8 -*-
"""本机文件选择：把手打路径换成点一下选。

桌宠浮动输入条与工作台命令栏都要这个入口。两边各写一遍必然走样——过滤器
不一致、动作用词不一致、带空格的路径一边加引号一边不加，最后表现成「同一个
功能在两个窗口里行为不同」。所以过滤器生成与指令拼装只放这一份。

**这里不直接调用 read_doc。** 本模块只做一件事：把「动词 + 路径」写进输入框，
用户回车后仍然走 模型 → action_parser → path_guard → 授权卡 那条原路。
选文件不是绕过授权链的后门——真绕过去，「用户点同意」这一步就没有存在意义了，
而那是这个项目里少数几个自己做对的地方。

为什么用「分析一下」还是「读一下」：表格交给 analyze_data（data_tool 支持
csv / tsv / xlsx / xlsm），其余交给 read_doc。老格式 .xls 故意不归到表格组，
因为 data_tool 明确拒绝它、要求另存为 xlsx，用它当入口等于把用户送进死路。
"""

import os

from framework import path_guard

# 表格类：走 analyze_data
_TABLE_EXTS = (".csv", ".tsv", ".xlsx", ".xlsm")

# 弹窗下拉的分组（顺序即展示顺序），每组只取 path_guard 真允许的后缀
_GROUPS = (
    ("文档", (".docx", ".doc", ".pdf", ".pptx", ".md", ".txt")),
    ("表格", (".csv", ".tsv", ".xlsx", ".xlsm", ".xls")),
    ("代码与数据", (".py", ".cpp", ".json")),
    ("压缩包", (".zip", ".gz")),
)


def allowed_exts() -> set:
    """path_guard 真允许读的后缀。过滤器以此为准，不在别处另写一份。"""
    return set(path_guard.advertised_exts())


def file_filter() -> str:
    """QFileDialog 的过滤器串。

    不提供「所有文件 (*)」：允许之外的后缀选了也过不了 path_guard，
    让用户先选到、再被拒，比一开始就选不到更难受。
    """
    allow = allowed_exts()
    parts = []
    for title, exts in _GROUPS:
        keep = [e for e in exts if e in allow]
        if keep:
            parts.append("%s (%s)" % (title, " ".join("*" + e for e in keep)))
    # 兜底：path_guard 允许、但还没归组的新后缀不要漏在过滤器外面
    grouped = {e for _t, exts in _GROUPS for e in exts}
    rest = sorted(allow - grouped)
    if rest:
        parts.append("其他 (%s)" % " ".join("*" + e for e in rest))
    return ";;".join(parts)


def compose_command(path: str) -> str:
    """把选中的文件拼成一句可以直接发的指令。

    带空格的路径必须用引号包住（Windows 用户名里有空格极常见），
    解析器侧会把引号剥掉，见 action_parser._clean_path。
    """
    p = str(path or "").strip().strip('"')
    if not p:
        return ""
    ext = os.path.splitext(p)[1].lower()
    verb = "分析一下" if ext in _TABLE_EXTS else "读一下"
    return '%s "%s"' % (verb, p)


def pick_file(parent=None, start_dir: str = "") -> str:
    """弹系统文件选择框，返回选中的绝对路径；取消返回空串。

    起始目录默认落在 path_guard 的第一个允许根，用户不必从「此电脑」一层层点。
    Qt 只在真正要弹窗时才导入，所以 file_filter / compose_command 可以在没有
    QApplication 的环境里单测。
    """
    from PyQt5.QtWidgets import QFileDialog

    if not start_dir:
        roots = path_guard.allowed_roots()
        start_dir = str(roots[0]) if roots else ""
    path, _ = QFileDialog.getOpenFileName(
        parent, "选择要交给凌零的文件", start_dir, file_filter())
    return path or ""
