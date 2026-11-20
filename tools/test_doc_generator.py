# -*- coding: utf-8 -*-
"""文档生成功能验证（PPT / Word / Excel / PDF / HTML）

覆盖：动作解析、离线模拟、真实生成+回读、规模防御、总开关拦截、
后台线程执行链。所有文件都写到临时目录，测完清理。
用法: python tools/test_doc_generator.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))   # 项目根目录

from PyQt5.QtWidgets import QApplication

from framework import config
from framework.action_parser import parse_actions
from framework.ai_engine import is_doc_request, mock_ai

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


app = QApplication(sys.argv)


def pump(seconds=2.0):
    """转事件循环若干真实时间（QTimer/QThread 回调需要真实时间）"""
    import time
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


TMP = tempfile.mkdtemp(prefix="pet_doc_test_")

# ============================================================
print("一、动作解析：create_doc 进白名单，类型归一化")
# ============================================================
out = parse_actions([{"action": "create_doc", "doc_type": "PPT",
                      "filename": "计划.pptx",
                      "content": {"title": "t",
                                  "slides": [{"title": "s", "bullets": ["b"]}]}}])
check("PPT 类型归一化为 ppt", len(out) == 1 and out[0]["doc_type"] == "ppt",
      str(out))
check("filename 透传", out and out[0].get("filename") == "计划.pptx")
check("content 保留 dict",
      out and isinstance(out[0].get("content"), dict)
      and out[0]["content"].get("title") == "t")

out = parse_actions([{"action": "create_doc", "type": "表格",
                      "filename": "x"}])
check("别名 type/表格 -> xlsx，缺 content 补空 dict",
      len(out) == 1 and out[0]["doc_type"] == "xlsx"
      and out[0]["content"] == {})

out = parse_actions([{"action": "create_doc", "doc_type": "PDF",
                      "filename": "报告.pdf", "content": {"title": "报告"}}])
check("PDF 类型归一化并保留", len(out) == 1
      and out[0]["doc_type"] == "pdf" and out[0]["filename"] == "报告.pdf",
      str(out))

out = parse_actions([{"action": "create_doc", "doc_type": "txt",
                      "content": {"a": 1}}])
check("不认识的类型被剔除",
      all(a.get("action") != "create_doc" for a in out), str(out))

out = parse_actions([{"action": "create_doc", "content": {"a": 1}}])
check("缺 doc_type 被剔除",
      all(a.get("action") != "create_doc" for a in out), str(out))

out = parse_actions([{"action": "create_doc", "doc_type": "word",
                      "content": {"title": "d",
                                  "sections": [{"heading": "h",
                                                "paragraphs": ["p"]}]}}])
check("word -> doc", len(out) == 1 and out[0]["doc_type"] == "doc")

# content 不是 dict（比如模型给了字符串）-> 置空，交给执行层拒绝
out = parse_actions([{"action": "create_doc", "doc_type": "ppt",
                      "content": "正文"}])
check("非 dict content 置空", out and out[0]["content"] == {})

# ============================================================
print("\n二、离线模拟与意图判定")
# ============================================================
for text, dtype in (("帮我做个PPT", "ppt"), ("生成幻灯片", "ppt"),
                    ("做个Excel表格", "xlsx"), ("整理个名单", "xlsx"),
                    ("生成PDF报告并保存到桌面", "pdf"),
                    ("写个word文档", "doc"), ("帮我写份报告", "doc")):
    acts = [a["action"] for a in mock_ai(text, "活泼")]
    doc = [a for a in mock_ai(text, "活泼") if a["action"] == "create_doc"]
    ok = "create_doc" in acts and doc and doc[0]["doc_type"] == dtype
    check(f"「{text}」-> {dtype}", ok, str(acts))

acts = [a["action"] for a in mock_ai("打开word", "活泼")]
check("「打开word」不误触文档生成", "open_app" in acts
      and "create_doc" not in acts, str(acts))

check("is_doc_request 命中", is_doc_request("做个PPT介绍项目")
      and is_doc_request("生成一份PDF"))
check("is_doc_request 不命中闲聊", not is_doc_request("今天天气怎么样"))
pdf_mock = [a for a in mock_ai("生成PDF并保存到桌面", "活泼")
            if a["action"] == "create_doc"]
check("离线 PDF 带桌面保存位置", pdf_mock
      and pdf_mock[0].get("location") == "desktop")

# ============================================================
print("\n三、真实生成 + 回读（临时目录）")
# ============================================================
from framework.doc_generator import generate

path = os.path.join(TMP, "测试.pptx")
ok, err = generate("ppt", {"title": "项目汇报", "subtitle": "凌零出品",
                           "slides": [{"title": "背景", "bullets": ["要点一", "要点二"]},
                                      {"title": "计划", "bullets": ["甲", "乙", "丙"]}]},
                   path)
check("PPT 生成成功", ok and os.path.exists(path), err)
if ok:
    from pptx import Presentation
    prs = Presentation(path)
    # 新母版用空白版式+文本框：取每页第一个非空文本框当"标题"
    def slide_title(s):
        for sp in s.shapes:
            if getattr(sp, "has_text_frame", False) \
                    and sp.text_frame.text.strip():
                return sp.text_frame.text.strip()
        return ""
    def slide_text(s):
        return "\n".join(sp.text_frame.text for sp in s.shapes
                         if getattr(sp, "has_text_frame", False))
    titles = [slide_title(s) for s in prs.slides]
    check("PPT 页数=封面+2内容+结束页", len(prs.slides) == 4, str(titles))
    check("PPT 标题正确", titles[0] == "项目汇报" and titles[1] == "背景")
    check("PPT 要点写入", "要点一" in slide_text(prs.slides[1])
          and "要点二" in slide_text(prs.slides[1]))

path = os.path.join(TMP, "测试.docx")
ok, err = generate("doc", {"title": "项目简报",
                           "sections": [
                               {"heading": "背景", "paragraphs": ["第一段", "第二段"],
                                "table": {"headers": ["名", "值"],
                                          "rows": [["a", "1"], ["b", "2"]]}},
                               {"heading": "结论", "paragraphs": ["完成"]}]},
                   path)
check("Word 生成成功", ok and os.path.exists(path), err)
if ok:
    import docx as docx_mod
    d = docx_mod.Document(path)
    heads = [p.text for p in d.paragraphs if p.style.name.startswith("Heading")]
    check("Word 章节标题", "背景" in heads and "结论" in heads, str(heads))
    check("Word 正文段落", any(p.text == "第二段" for p in d.paragraphs))
    t = d.tables[0]
    check("Word 内嵌表格", t.rows[1].cells[0].text == "a"
          and t.rows[2].cells[1].text == "2")

path = os.path.join(TMP, "中文报告.pdf")
ok, err = generate("pdf", {
    "title": "项目交付报告", "subtitle": "凌零出品",
    "sections": [
        {"heading": "执行摘要",
         "paragraphs": ["中文段落与 English 混排，特殊字符 <安全>。",
                        "这一段用于验证 PDF 保留可复制的文字层。"],
         "table": {"headers": ["事项", "状态"],
                   "rows": [["功能开发", "已完成"], ["测试验收", "进行中"]]}},
        {"heading": "后续计划", "paragraphs": ["继续完成交付验证。"]},
    ]}, path)
check("PDF 生成成功", ok and os.path.exists(path), err)
if ok:
    from pypdf import PdfReader
    reader = PdfReader(path)
    pdf_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    compact_pdf_text = "".join(pdf_text.split())
    check("PDF 有页面且文件非空", len(reader.pages) >= 1
          and os.path.getsize(path) > 1000)
    check("PDF 中文标题与章节可回读",
          all(s in compact_pdf_text for s in
              ("项目交付报告", "凌零出品", "执行摘要", "后续计划")), pdf_text)
    check("PDF 中英文段落与特殊字符可回读",
          "中文段落与English混排" in compact_pdf_text
          and "<安全>" in compact_pdf_text)
    check("PDF 中文表格可回读",
          all(s in compact_pdf_text for s in
              ("事项", "状态", "功能开发", "已完成", "测试验收", "进行中")))

path = os.path.join(TMP, "测试.xlsx")
ok, err = generate("xlsx", {"sheets": [{"name": "名单",
                                        "headers": ["姓名", "进度"],
                                        "rows": [["凌零", "100%"],
                                                 ["骑士", "60%"]]}]},
                   path)
check("Excel 生成成功", ok and os.path.exists(path), err)
if ok:
    from openpyxl import load_workbook
    wb = load_workbook(path)
    ws = wb["名单"]
    check("Excel 表头/数据", ws["A1"].value == "姓名"
          and ws["B2"].value == "100%" and ws["A3"].value == "骑士")

# 网页报告（html）
from framework.doc_generator import normalize_doc_type
check("html 类型别名归一", normalize_doc_type("网页") == "html"
      and normalize_doc_type("html") == "html")
path = os.path.join(TMP, "报告.html")
ok, err = generate("html", {"title": "市场调研", "subtitle": "凌零出品",
                            "sections": [
                                {"heading": "摘要",
                                 "paragraphs": ["行业增长 15%", "<风险>要转义"]},
                                {"heading": "数据",
                                 "paragraphs": ["见下表"],
                                 "table": {"headers": ["指标", "值"],
                                           "rows": [["增速", "15%"]]}}]},
                   path)
check("网页报告生成成功", ok and os.path.exists(path), err)
if ok:
    with open(path, encoding="utf-8") as f:
        html_text = f.read()
    check("网页含标题与章节", "市场调研" in html_text
          and "摘要" in html_text and "行业增长 15%" in html_text)
    check("网页表格与转义", "<th>指标</th>" in html_text
          and "&lt;风险&gt;" in html_text)
ok, err = generate("html", {"title": "空"}, os.path.join(TMP, "空.html"))
check("网页缺 sections 报错", not ok and "sections" in err, err)

# 失败路径
ok, err = generate("ppt", {"title": "空"}, os.path.join(TMP, "空.pptx"))
check("缺 slides 内容报错", not ok and "slides" in err, err)
ok, err = generate("pdf", {"title": "空"}, os.path.join(TMP, "x.pdf"))
check("PDF 缺 sections 报错", not ok and "sections" in err, err)
ok, err = generate("txt", {"a": 1}, os.path.join(TMP, "x.txt"))
check("不支持的类型报错", not ok, err)
ok, err = generate("doc", {"sections": []}, os.path.join(TMP, "y.docx"))
check("缺 sections 报错", not ok, err)

# ============================================================
print("\n三·五、PPT 多模板 + 原生表格（新增能力）")
# ============================================================
from framework.doc_generator import (generate as _gen, PPT_TEMPLATES,
                                     resolve_template)

check("模板库五套", len(PPT_TEMPLATES) == 5, str(list(PPT_TEMPLATES)))
check("模板别名命中", resolve_template({"template": "翡翠绿"})["cover"]
      == "band" and resolve_template({"template": "purple"})["cover"]
      == "ribbon")
check("模板关键词兜底",
      resolve_template({"title": "自然与环保"})["cover"] == "band")
check("未知模板回默认", resolve_template({"template": "花里胡哨"})["cover"]
      == "bar" and resolve_template({})["cover"] == "bar")

path = os.path.join(TMP, "模板表格.pptx")
ok, err = _gen("ppt", {"title": "季度经营汇报", "template": "暖橙",
                       "slides": [
                           {"title": "业务概览", "bullets": ["增长", "降本"]},
                           {"title": "季度数据",
                            "table": {"headers": ["指标", "Q1", "Q2"],
                                      "rows": [["营收", 120, 158],
                                               ["新客户", 300, 420],
                                               ["续费率", "82%", "88%"]]}},
                       ]}, path)
check("带模板+表格 PPT 生成", ok and os.path.exists(path), err)
if ok:
    from pptx import Presentation as _Prs
    prs2 = _Prs(path)
    check("页数=封面+2内容+结束页", len(prs2.slides) == 4,
          str(len(prs2.slides)))
    slide2 = list(prs2.slides)[2]
    gfs = [sp for sp in slide2.shapes if sp.has_table]
    check("内容页含原生表格", bool(gfs))
    if gfs:
        tbl = gfs[0].table
        check("表格行列正确", len(tbl.rows) == 4 and len(tbl.columns) == 3)
        check("表头/数据写入", tbl.cell(0, 0).text == "指标"
              and tbl.cell(1, 1).text == "120")
        check("表头填充主题色",
              str(tbl.cell(0, 0).fill.fore_color.rgb) == "D9661F",
              str(tbl.cell(0, 0).fill.fore_color.rgb))
    closing = list(prs2.slides)[3]
    ctexts = "\n".join(sp.text_frame.text for sp in closing.shapes
                       if getattr(sp, "has_text_frame", False))
    check("自动生成结束页", "谢谢观看" in ctexts)

path = os.path.join(TMP, "大表裁剪.pptx")
ok, err = _gen("ppt", {"title": "大表", "slides": [
    {"title": "数据", "table": {"headers": ["列"] * 3,
                                "rows": [[str(i), "x", "y"]
                                         for i in range(20)]}}]}, path)
check("表格行数被裁剪", ok and len(
    [sp for sp in _Prs(path).slides[1].shapes if sp.has_table][0].table.rows
) == 1 + 10, err)

for name in PPT_TEMPLATES:
    p2 = os.path.join(TMP, f"模板_{name}.pptx")
    ok, err = _gen("ppt", {"title": f"{name}测试", "template": name,
                           "slides": [{"title": "页", "bullets": ["要点"]}]},
                   p2)
    check(f"模板{name}渲染成功", ok, err)

# ============================================================
print("\n四、规模防御（超出上限被裁剪）")
# ============================================================
many = {"title": "多页", "slides": [{"title": f"第{i}页", "bullets": ["x"]}
                                    for i in range(40)]}
path = os.path.join(TMP, "多页.pptx")
ok, err = generate("ppt", many, path)
from pptx import Presentation
check("PPT 页数被裁到上限",
      ok and len(Presentation(path).slides) == 2 + config.DOC_MAX_SLIDES, err)

long_text = "长" * (config.DOC_MAX_TEXT + 500)
path = os.path.join(TMP, "长文.docx")
ok, err = generate("doc", {"title": "长文", "sections": [
    {"heading": "h", "paragraphs": [long_text]}]}, path)
import docx as docx_mod
paras = [p.text for p in docx_mod.Document(path).paragraphs]
check("超长段落被截断",
      ok and any(config.DOC_MAX_TEXT - 2 <= len(p) <= config.DOC_MAX_TEXT
                 for p in paras), err)

rows = {"sheets": [{"name": "大表", "headers": ["A"],
                    "rows": [[str(i)] for i in range(config.DOC_MAX_ROWS + 300)]}]}
path = os.path.join(TMP, "大表.xlsx")
ok, err = generate("xlsx", rows, path)
from openpyxl import load_workbook
check("Excel 行数被裁到上限",
      ok and load_workbook(path)["大表"].max_row == 1 + config.DOC_MAX_ROWS, err)

# ============================================================
print("\n五、Framework 集成：开关拦截 + 后台线程执行链（对话框打桩）")
# ============================================================
from PyQt5.QtWidgets import QFileDialog
from ui.pet_window import PetWindow

win = PetWindow()
fw = win.fw
# 任务数据隔离到临时文件：交付/工作日志不写进真实 tasks.json
fw.tasks._file = os.path.join(TMP, "tasks_iso.json")
fw.tasks.reminders, fw.tasks.todos = [], []
fw.tasks.deliveries, fw.tasks.worklog = [], []

bubbles = []
cards = []
fw.show_bubble = lambda text: bubbles.append(text)
fw.show_card = lambda title, path_, size="", icon="": \
    cards.append((title, path_, size, icon))
finished = []
fw._finish_action = lambda: finished.append(True)
_real_use_ai = config.USE_REAL_AI
config.USE_REAL_AI = False   # 质量门纠正轮走离线降级分支，测试不联网

# 5.1 总开关关闭时拒绝（不会弹保存对话框）
config.ALLOW_DOC_GENERATION = False
fw._do_create_doc({"doc_type": "ppt",
                   "content": {"title": "t",
                               "slides": [{"title": "s", "bullets": ["b"]}]}})
check("开关关闭时拒绝并提示", any("禁用" in b for b in bubbles), str(bubbles))
check("开关关闭时不进入生成", not fw._doc_worker)
config.ALLOW_DOC_GENERATION = True

# 5.2 正常执行链：对话框打桩 -> 后台线程真实生成 -> 卡片反馈
bubbles.clear()
cards.clear()
target = os.path.join(TMP, "集成测试.pptx")
QFileDialog.getSaveFileName = staticmethod(
    lambda *a, **kw: (target, "演示文稿 (*.pptx)"))
fw._do_create_doc({"doc_type": "ppt", "filename": "集成测试.pptx",
                   "content": {"title": "集成测试",
                               "slides": [
                                   {"title": "第一页",
                                    "bullets": ["行业规模近三年年均增长 15%"]},
                                   {"title": "第二页",
                                    "bullets": ["第一阶段完成渠道下沉与代理体系建设"]}]}})
check("生成期间后台线程在跑", fw._doc_worker is not None)
deadline = 0
while not cards and deadline < 30:
    pump(0.2)
    deadline += 1
check("后台线程完成并反馈卡片",
      len(cards) == 1 and cards[0][0] == "文档已生成"
      and cards[0][3] == "file-text"
      and cards[0][1] == target, str(cards))
check("文件真实落盘", os.path.exists(target) and os.path.getsize(target) > 0)
check("成功气泡与完成回调", any("生成" in b for b in bubbles) and finished)
check("busy 复位", not fw._busy and fw._doc_worker is None and finished)

# 5.3 用户取消保存
bubbles.clear()
QFileDialog.getSaveFileName = staticmethod(lambda *a, **kw: ("", ""))
fw._do_create_doc({"doc_type": "ppt", "content": {
    "title": "t", "slides": [{"title": "s", "bullets": ["b"]}]}})
pump(0.3)
check("取消保存时礼貌提示", any("先不生成" in b for b in bubbles), str(bubbles))

# ============================================================
print("\n六、用户样式定制（style）与保存位置（location）")
# ============================================================
from framework.doc_generator import (parse_color, resolve_style,
                                     resolve_location, unique_path)

check("颜色解析：中文/十六进制/非法",
      parse_color("红色") == (0xD9, 0x30, 0x2B)
      and parse_color("#FF0000") == (255, 0, 0)
      and parse_color("ff0000") == (255, 0, 0)
      and parse_color("不存在的颜色") is None)
st = resolve_style({"style": {"title_color": "红色", "title_size": 36,
                              "body_size": 16, "line_spacing": 1.5,
                              "font": "宋体", "accent_color": "#22CC66"}})
check("样式解析：字段齐全且越界丢弃",
      st.get("title_size") == 36.0 and st.get("line_spacing") == 1.5
      and st.get("accent_color") == (0x22, 0xCC, 0x66)
      and st.get("font") == "宋体"
      and resolve_style({"style": {"title_size": 999}}) == {}
      and resolve_style({"style": "不是dict"}) == {})

spec = {"title": "样式测试", "style": {"title_color": "红色",
        "title_size": 36, "body_size": 20, "line_spacing": 1.5},
        "slides": [{"title": "页面", "bullets": ["要点一", "要点二"]}]}
path = os.path.join(TMP, "样式.pptx")
ok, err = generate("ppt", spec, path)
check("带样式的 PPT 生成成功", ok and os.path.exists(path), err)
if ok:
    from pptx import Presentation
    from pptx.util import Pt as _Pt
    prs = Presentation(path)
    cover_run = None
    for shp in prs.slides[0].shapes:
        if shp.has_text_frame:
            for p in shp.text_frame.paragraphs:
                for r in p.runs:
                    if r.text == "样式测试":
                        cover_run = r
    check("封面标题应用用户颜色与字号",
          cover_run is not None
          and str(cover_run.font.color.rgb) == "D9302B"
          and cover_run.font.size == _Pt(57))
    head_ok = body_ok = False
    for shp in prs.slides[1].shapes:
        if not shp.has_text_frame:
            continue
        for p in shp.text_frame.paragraphs:
            for r in p.runs:
                if r.text == "页面":
                    head_ok = (str(r.font.color.rgb) == "D9302B"
                               and r.font.size == _Pt(36))
                elif r.text == "要点一":
                    body_ok = (r.font.size == _Pt(20)
                               and p.line_spacing == 1.5)
    check("内容页标题/正文字号行距生效", head_ok and body_ok)

path = os.path.join(TMP, "样式.html")
ok, err = generate("html", {"title": "网页", "style": {
    "title_color": "#D9302B", "body_size": 18, "line_spacing": 2.0},
    "sections": [{"heading": "背景", "paragraphs": ["内容"]}]}, path)
html_style_ok = False
if ok:
    with open(path, encoding="utf-8") as f:
        html_text = f.read()
    html_style_ok = ("#D9302B" in html_text and "font-size: 18px" in html_text
                     and "line-height: 2.0" in html_text)
check("HTML 样式注入 CSS", html_style_ok, err)

path = os.path.join(TMP, "样式.docx")
ok, err = generate("doc", {"title": "文档", "style": {
    "body_size": 14, "line_spacing": 1.5, "title_color": "蓝色"},
    "sections": [{"heading": "背景", "paragraphs": ["内容"]}]}, path)
doc_style_ok = False
if ok:
    import docx as _docx
    d = _docx.Document(path)
    doc_style_ok = (d.styles["Title"].font.color.rgb is not None
                    and d.styles["Normal"].font.size is not None)
check("Word 样式生效（标题色/正文字号）", doc_style_ok, err)

path = os.path.join(TMP, "样式.xlsx")
ok, err = generate("xlsx", {"style": {"accent_color": "绿色"},
                            "sheets": [{"name": "表", "headers": ["A"],
                                        "rows": [[1]]}]}, path)
check("Excel 表头自定义色生成成功", ok and os.path.exists(path), err)

dd = resolve_location("desktop")
check("保存位置解析：桌面有效/非法为空",
      bool(dd) and os.path.isdir(dd) and resolve_location("乱写") == "")
probe = os.path.join(TMP, "占用.txt")
with open(probe, "w") as f:
    f.write("x")
check("重名不覆盖（追加时间戳）",
      unique_path(probe) != probe
      and unique_path(os.path.join(TMP, "从没存在_9x8y.txt"))
      == os.path.join(TMP, "从没存在_9x8y.txt"))

# 端到端：带 location 的 create_doc 不弹对话框、直接存到指定目录
import framework.doc_generator as _dg
_real_resolve = _dg.resolve_location
_dg.resolve_location = lambda v: TMP          # 指到临时目录，不写真实桌面
calls = []
QFileDialog.getSaveFileName = staticmethod(
    lambda *a, **kw: (calls.append(1), ("", ""))[1])
bubbles.clear()
fw._do_create_doc({"doc_type": "html", "filename": "免弹窗.html",
                   "location": "desktop",
                   "content": {"title": "免弹窗",
                               "sections": [
                                   {"heading": "摘要",
                                    "paragraphs": ["本页由指定位置流程直接生成。",
                                                   "不再弹出另存为对话框。"]},
                                   {"heading": "说明",
                                    "paragraphs": ["重名文件自动加时间戳，",
                                                   "绝不覆盖已有成果。"]}]}})
pump(0.3)
deadline = 0
while not cards and deadline < 30:
    pump(0.2)
    deadline += 1
check("指定位置时不再弹保存对话框", calls == [], str(calls))
check("直接保存到指定目录并反馈卡片",
      any("文档已生成" in c[0] for c in cards)
      and os.path.exists(os.path.join(TMP, "免弹窗.html")),
      str(cards))

# PDF 全链路：指定桌面位置（打桩到临时目录）-> 后台生成 -> 交付登记 -> 回读
cards.clear()
fw._do_create_doc({"doc_type": "pdf", "filename": "桌面报告.pdf",
                   "location": "desktop",
                   "content": {"title": "桌面 PDF 验收",
                               "sections": [
                                   {"heading": "生成结果",
                                    "paragraphs": ["PDF 已直接保存到指定目录。",
                                                   "整个过程不弹出保存对话框。"]},
                                   {"heading": "交付检查",
                                    "paragraphs": ["交付清单记录文件路径和 PDF 类型。"]}]}})
deadline = 0
while not cards and deadline < 30:
    pump(0.2)
    deadline += 1
pdf_target = os.path.join(TMP, "桌面报告.pdf")
check("PDF 指定位置保存不弹对话框", calls == []
      and os.path.exists(pdf_target), str(cards))
check("PDF 全链路反馈与交付登记",
      any(c[1] == pdf_target for c in cards)
      and any(d.get("path") == pdf_target and d.get("kind") == "pdf"
              for d in fw.tasks.deliveries), str(fw.tasks.deliveries))
if os.path.exists(pdf_target):
    from pypdf import PdfReader
    delivered_text = "".join(
        page.extract_text() or "" for page in PdfReader(pdf_target).pages)
    check("桌面 PDF 成品可回读", "桌面 PDF 验收" in delivered_text
          and "交付清单记录文件路径" in delivered_text)
_dg.resolve_location = _real_resolve

config.USE_REAL_AI = _real_use_ai

win.close()

shutil.rmtree(TMP, ignore_errors=True)

print(f"\n== 测试结束，失败 {len(FAILS)} 项 ==")
sys.exit(0 if not FAILS else 1)
