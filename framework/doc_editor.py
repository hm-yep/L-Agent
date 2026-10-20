# -*- coding: utf-8 -*-
"""文档读取与改写：用户把 pptx/docx 发给凌零，AI 修改后返回新文件

    from framework.doc_editor import extract_content, build_edit_message
    kind, spec = extract_content(r"C:\\me\\报告.pptx")

- extract_content(path): 读取文件文字内容，转成与 create_doc 兼容的 spec dict；
- build_edit_message(kind, spec, instruction): 组装发给大模型的改写任务消息。

边界说明：提取的是"文字内容"（标题/要点/段落/表格）。原文件里的图片、
动画、母版等设计元素不会带到新文件——新文件由凌零的内置美化母版重新
排版输出（*_修改.pptx / *_修改.docx），这是"内容改写"而非"原地编辑"。
"""

import json
import os

from framework import config
from framework.doc_generator import _clean


def extract_content(path: str):
    """
    读取文档文字内容。
    :return: (kind, spec) —— kind 为 "ppt"/"doc"，spec 与 create_doc 的 content 同构
    :raises ValueError: 类型不支持 / 文件打不开 / 内容为空
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".pptx", ".docx"):
        raise ValueError(f"不支持的文件类型: {ext}")
    try:
        spec = _extract_pptx(path) if ext == ".pptx" else _extract_docx(path)
    except ValueError:
        raise
    except Exception as e:                      # 损坏/加密等一律归一为 ValueError
        raise ValueError(f"文件打不开: {type(e).__name__}: {e}") from e
    return ("ppt" if ext == ".pptx" else "doc"), spec


def _extract_pptx(path: str) -> dict:
    from pptx import Presentation

    prs = Presentation(path)
    slides = []
    deck_title = (prs.core_properties.title or "").strip()
    for slide in prs.slides:
        title = ""
        bullets = []
        shapes = list(slide.shapes)
        for shape in shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            is_title = shape == slide.shapes.title
            text = shape.text_frame.text.strip()
            if not text:
                continue
            if text == "凌零 · 桌面AI助手" or text.isdigit():
                continue                                # 页脚/页码不是内容
            if is_title and not title:
                title = text
                continue
            # 没有标题占位符的页（如凌零自产文件的封面）：
            # 首个文本框的首行提升为标题，其余进要点
            if not title and not bullets:
                lines = [ln.strip()
                         for ln in text.replace("\r", "").split("\n")]
                lines = [ln for ln in lines if ln]
                if lines:
                    title = _clean(lines[0], 100)
                    bullets.extend(_clean(ln, config.DOC_MAX_TEXT)
                                   for ln in lines[1:])
                    continue
            for para in shape.text_frame.paragraphs:
                line = para.text.strip()
                if line and line != title:
                    bullets.append(_clean(line, config.DOC_MAX_TEXT))
        if not title and not bullets:
            continue                                  # 空页跳过
        slides.append({"title": _clean(title, 100)
                               or f"第{len(slides) + 1}页",
                       "bullets": bullets[:20]})
        if not deck_title and title:
            deck_title = title
    slides = slides[: config.DOC_MAX_SLIDES]
    if not slides:
        raise ValueError("PPT 里没有可编辑的文字内容")
    return {"title": _clean(deck_title, 100) or os.path.splitext(
                os.path.basename(path))[0],
            "slides": slides}


def _extract_docx(path: str) -> dict:
    import docx as docx_mod

    d = docx_mod.Document(path)
    sections = []
    deck_title = (d.core_properties.title or "").strip()
    cur = None

    def _flush():
        nonlocal cur
        if cur and (cur["paragraphs"] or cur.get("table")):
            sections.append(cur)
        cur = None

    for para in d.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name if para.style is not None else "") or ""
        if style.startswith("Heading") or style == "Title":
            _flush()
            if not deck_title or style == "Title":
                if style == "Title" and not deck_title:
                    deck_title = text
            cur = {"heading": _clean(text, 100), "paragraphs": []}
        else:
            if cur is None:
                cur = {"heading": "正文", "paragraphs": []}
            if len(cur["paragraphs"]) < 80:
                cur["paragraphs"].append(_clean(text, config.DOC_MAX_TEXT))
    _flush()

    # 表格：按出现顺序各自挂成独立章节（v1 不还原本义在文中的位置）
    for i, table in enumerate(d.tables[:10]):
        headers = [cell.text.strip() for cell in table.rows[0].cells] \
            if table.rows else []
        rows = [[cell.text.strip() for cell in row.cells]
                for row in table.rows[1:]]
        rows = rows[: config.DOC_MAX_ROWS]
        if headers or rows:
            sections.append({"heading": _clean(table.rows and
                                               headers and headers[0]
                                               or f"表格{i + 1}", 100),
                             "paragraphs": [],
                             "table": {"headers": headers or ["列1"],
                                       "rows": rows}})
    if not deck_title:
        deck_title = os.path.splitext(os.path.basename(path))[0]
    sections = sections[: config.DOC_MAX_SLIDES] or [
        {"heading": "正文", "paragraphs": []}]
    if not any(s.get("paragraphs") or s.get("table") for s in sections):
        raise ValueError("Word 里没有可编辑的文字内容")
    return {"title": _clean(deck_title, 100), "sections": sections}


def build_edit_message(kind: str, spec: dict, instruction: str) -> str:
    """组装文件改写任务的用户消息（系统提示词里已有 create_doc 的格式约定）"""
    type_name = {"ppt": "PPT 演示文稿", "doc": "Word 文档"}.get(kind, kind)
    return (
        f"【文档修改任务】用户把一份{type_name}交给你修改。\n"
        f"文件当前内容 JSON：\n{json.dumps(spec, ensure_ascii=False)}\n\n"
        f"用户的修改要求：{instruction}\n\n"
        f"请按用户要求改写这份内容，返回带 create_doc 动作的 JSON 数组"
        f"（动作池第 15 条），doc_type 填 \"{kind}\"，content 为修改后的"
        f"完整内容——保持原有的顶层结构（{kind} 对应的键），不要自作主张"
        f"换成别的文档类型。若某页/某节适合配图，可给 image_query"
        f"（2~4 个英文搜索词）。filename 只填文件名。"
    )
