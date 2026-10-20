# -*- coding: utf-8 -*-
"""文档生成：AI 提供"结构化内容"，本地渲染成 PPT / Word / Excel 文件

    from framework.doc_generator import generate, normalize_doc_type
    ok, err = generate("ppt", spec, r"C:\\Users\\me\\Desktop\\汇报.pptx")

spec 结构约定（由大模型按 SYSTEM_PROMPT 产出、action_parser 清洗）：
- ppt:  {"title": str, "subtitle"?: str,
         "slides": [{"title": str, "bullets": [str, ...],
                     "image_query"?: str}, ...]}
- doc:  {"title": str,
         "sections": [{"heading": str, "paragraphs": [str, ...],
                       "table"?: {"headers": [str], "rows": [[...], ...]}}]}
- xlsx: {"sheets": [{"name": str, "headers": [str], "rows": [[...], ...]}, ...]}

设计：
- 只写用户在"另存为"对话框里选定的路径，不读用户任何其它文件；
- 版式为内置美化母版（主题配色 + 装饰 + 页码），不依赖外部模板文件；
- 联网配图：slides 带 image_query 且用户填了 Pexels Key 时，fetch_images()
  先把图下好，generate() 再把图插到页面右侧；任一环节失败都退回纯排版；
- 对模型输出做规模防御（config.DOC_MAX_*），防止输出失控撑爆文件；
- 纯函数无 UI 依赖，任何异常都以 (False, 原因) 返回，由调用者决定如何反馈。
"""

import os
import re
import time
from io import BytesIO

import requests

from framework import config
from framework.utils import logger

# ============================================================
# PPT 模板库：五套主题（配色 + 封面装饰风格）
# 模型可在 content.template 里点名；没给就按内容关键词猜或用默认
# ============================================================
PPT_TEMPLATES = {
    "深空蓝": {
        "aliases": ("深空蓝", "深蓝", "蓝", "blue", "ocean", "tech", "科技"),
        "dark": (0x0F, 0x2B, 0x46), "theme": (0x1B, 0x6B, 0x9A),
        "light": (0xEE, 0xF4, 0xF9), "accent": (0x35, 0xC3, 0xF2),
        "tint": (0xDD, 0xEC, 0xF7), "text": (0x24, 0x2C, 0x33),
        "gray": (0x8A, 0x9A, 0xA2),
        "cover": "bar",                        # 左侧竖色条
    },
    "翡翠绿": {
        "aliases": ("翡翠绿", "绿", "青绿", "green", "forest", "自然"),
        "dark": (0x0E, 0x33, 0x2A), "theme": (0x17, 0x7A, 0x5E),
        "light": (0xEC, 0xF6, 0xF1), "accent": (0x35, 0xD0, 0x9A),
        "tint": (0xDA, 0xF0, 0xE5), "text": (0x1F, 0x33, 0x2C),
        "gray": (0x84, 0x9B, 0x92),
        "cover": "band",                       # 底部大色带
    },
    "暖橙": {
        "aliases": ("暖橙", "橙", "橘", "orange", "sunset", "活力", "暖色"),
        "dark": (0x4A, 0x25, 0x0F), "theme": (0xD9, 0x66, 0x1F),
        "light": (0xFC, 0xF3, 0xEA), "accent": (0xF7, 0xA3, 0x3B),
        "tint": (0xFA, 0xE3, 0xCC), "text": (0x3A, 0x2A, 0x1E),
        "gray": (0xA8, 0x94, 0x84),
        "cover": "circle",                     # 右下大圆
    },
    "绛紫": {
        "aliases": ("绛紫", "紫", "purple", "royal", "典雅", "高端"),
        "dark": (0x2C, 0x1A, 0x40), "theme": (0x6B, 0x3F, 0xA0),
        "light": (0xF4, 0xF0, 0xFA), "accent": (0xB0, 0x8A, 0xE8),
        "tint": (0xE7, 0xDD, 0xF5), "text": (0x2E, 0x26, 0x3A),
        "gray": (0x9A, 0x91, 0xA8),
        "cover": "ribbon",                     # 斜向色带
    },
    "雅灰": {
        "aliases": ("雅灰", "灰", "极简", "gray", "grey", "minimal", "简约"),
        "dark": (0x25, 0x27, 0x2A), "theme": (0x4E, 0x56, 0x5E),
        "light": (0xF4, 0xF5, 0xF6), "accent": (0x9A, 0xA7, 0xB3),
        "tint": (0xE4, 0xE8, 0xEC), "text": (0x2B, 0x2E, 0x32),
        "gray": (0x9A, 0xA1, 0xA8),
        "cover": "frame",                      # 细线框
    },
}
_PPT_FONT = "微软雅黑"
# PPT 页面里单张表格的规模上限（防模型输出失控撑爆版面）
_PPT_TABLE_MAX_ROWS = 10
_PPT_TABLE_MAX_COLS = 8


def resolve_template(spec: dict) -> dict:
    """按 spec.template（中英文别名都认）选模板，认不出按标题关键词猜，
    再认不出回默认深空蓝"""
    name = _clean(spec.get("template"), 30).lower() if isinstance(spec, dict) \
        else ""
    if name:
        for tpl in PPT_TEMPLATES.values():
            if any(name == a.lower() or name in a.lower()
                   for a in tpl["aliases"] if a):
                return tpl
    # 内容关键词兜底：标题里带了"自然/环保"这类词时配同气质的模板
    text = _clean(spec.get("title"), 100).lower() if isinstance(spec, dict) \
        else ""
    if text:
        for tpl in PPT_TEMPLATES.values():
            for keyword in tpl["aliases"]:
                if keyword and keyword in text:
                    return tpl
    return PPT_TEMPLATES["深空蓝"]


DOC_TYPE_EXTS = {"ppt": ".pptx", "doc": ".docx", "xlsx": ".xlsx",
                 "html": ".html"}

# Word / Excel 共用的主题色（沿用默认深空蓝色系）
_DARK = (0x0F, 0x2B, 0x46)     # 标题深色
_THEME = (0x1B, 0x6B, 0x9A)    # 主题色


# ============================================================
# 用户样式定制：create_doc 带 style 字段时按用户要求改颜色/字号/
# 行距/字体；location 字段指定保存位置（桌面/文档/下载）
# ============================================================
_NAMED_COLORS = {
    "红": (0xD9, 0x30, 0x2B), "大红": (0xC0, 0x1F, 0x1F),
    "粉": (0xE8, 0x6A, 0x8E), "橙": (0xE0, 0x7B, 0x28),
    "黄": (0xD9, 0xA5, 0x21), "金": (0xC9, 0xA0, 0x4E),
    "绿": (0x2E, 0x8B, 0x57), "青": (0x17, 0x9C, 0x9C),
    "蓝": (0x22, 0x5E, 0xA6), "深蓝": (0x14, 0x36, 0x6E),
    "天蓝": (0x42, 0x9E, 0xD9), "紫": (0x7B, 0x4F, 0xA8),
    "灰": (0x6E, 0x76, 0x7E), "黑": (0x1A, 0x1A, 0x1A),
    "棕": (0x7A, 0x51, 0x33), "白": (0xFA, 0xFA, 0xFA),
}


def parse_color(value):
    """'红'/'红色'/'#FF0000'/'ff0000' -> (r, g, b)；认不出返回 None"""
    v = _clean(value, 20).strip().lower()
    if not v:
        return None
    hex_v = v.lstrip("#")
    if re.fullmatch(r"[0-9a-f]{6}", hex_v):
        return tuple(int(hex_v[i:i + 2], 16) for i in (0, 2, 4))
    if re.fullmatch(r"[0-9a-f]{3}", hex_v):
        return tuple(int(c * 2, 16) for c in hex_v)
    v2 = v.rstrip("色").rstrip("颜色") or v
    for name, rgb in _NAMED_COLORS.items():
        if name in v2 or v2 in name:
            return rgb
    return None


def _css_hex(rgb) -> str:
    return "#%02X%02X%02X" % rgb


def resolve_style(spec: dict) -> dict:
    """解析 spec.style（模型按用户样式需求产出）-> 规范化样式 dict。

    识别字段：title_color / accent_color（中文色名或 # 十六进制）、
    title_size / body_size（字号数字）、line_spacing（倍数）、font（字体名）。
    只返回能识别的字段，没给的字段不覆盖内置样式。
    """
    st = spec.get("style") if isinstance(spec, dict) else None
    if not isinstance(st, dict):
        return {}
    out = {}
    for key in ("title_color", "accent_color"):
        rgb = parse_color(st.get(key))
        if rgb:
            out[key] = rgb
    for key, lo, hi in (("title_size", 8, 96), ("body_size", 8, 72)):
        try:
            num = float(st.get(key))
            if lo <= num <= hi:
                out[key] = num
        except (TypeError, ValueError):
            pass
    try:
        ls = float(st.get("line_spacing"))
        if 1.0 <= ls <= 3.0:
            out["line_spacing"] = ls
    except (TypeError, ValueError):
        pass
    font = _clean(st.get("font"), 20)
    if font and len(font) >= 2:
        out["font"] = font
    return out


def apply_palette_style(palette: dict, style: dict) -> dict:
    """把解析出的样式覆盖进 PPT 调色板（原对象就地修改并返回）"""
    if not style:
        return palette
    if style.get("accent_color"):
        palette["accent"] = style["accent_color"]
        palette["theme"] = style["accent_color"]
    if style.get("title_color"):
        palette["heading"] = style["title_color"]     # 内容页标题
        palette["cover_title"] = style["title_color"]  # 封面大标题
    if style.get("title_size"):
        palette["heading_size"] = style["title_size"]
        palette["cover_size"] = round(style["title_size"] * 44 / 28)
    if style.get("body_size"):
        palette["body_size"] = style["body_size"]
    if style.get("line_spacing"):
        palette["line_spacing"] = style["line_spacing"]
    if style.get("font"):
        palette["font"] = style["font"]
    return palette


# 保存位置：location 字段 -> 实际目录（Windows 已知文件夹，兼容 OneDrive 重定向）
_LOCATION_CSIDL = {"desktop": 0x10, "documents": 0x05, "downloads": 0x0C}
_LOCATION_ALIASES = {
    "desktop": ("desktop", "桌面"),
    "documents": ("documents", "document", "文档", "我的文档"),
    "downloads": ("downloads", "download", "下载"),
}


def resolve_location(value) -> str:
    """'desktop/桌面'、'documents/文档'、'downloads/下载' -> 实际目录；
    认不出返回空串（调用方退回另存为对话框）"""
    v = _clean(value, 20).strip().lower()
    if not v:
        return ""
    key = next((k for k, aliases in _LOCATION_ALIASES.items()
                if any(a in v for a in aliases)), "")
    if not key:
        return ""
    try:  # Windows API 取真实目录（桌面可能被 OneDrive 重定向）
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(
                None, _LOCATION_CSIDL[key], None, 0, buf) == 0 and buf.value:
            return buf.value
    except Exception:  # noqa: BLE001 非 Windows / API 失败走兜底
        pass
    name = {"desktop": "Desktop", "documents": "Documents",
            "downloads": "Downloads"}[key]
    p = os.path.join(os.path.expanduser("~"), name)
    return p if os.path.isdir(p) else os.path.expanduser("~")


def unique_path(path: str) -> str:
    """同名文件已存在时追加时间戳，绝不静默覆盖用户的旧文件"""
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    return f"{root}_{time.strftime('%H%M%S')}{ext}"


def normalize_doc_type(value) -> str:
    """把模型给的各种叫法归一成 ppt / doc / xlsx / html，未知返回空串"""
    v = str(value or "").strip().lower()
    if v in ("ppt", "pptx", "幻灯片", "演示文稿", "slides"):
        return "ppt"
    if v in ("doc", "docx", "word", "文档", "文章", "报告"):
        return "doc"
    if v in ("xlsx", "excel", "表格", "电子表格", "sheet"):
        return "xlsx"
    if v in ("html", "htm", "网页", "网页报告", "报告页", "web"):
        return "html"
    return ""


def _clean(value, limit: int = None) -> str:
    """模型输出的任意值 -> 安全的单段文本（None 归空、截断超长）"""
    if value is None:
        return ""
    text = str(value).strip()
    if limit is not None and len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _str_list(value, limit=None) -> list:
    """容错地把 str / list / 其它 转成非空文本行列表"""
    if isinstance(value, str):
        items = [ln.strip() for ln in value.replace("\r", "").split("\n")]
    elif isinstance(value, (list, tuple)):
        items = [_clean(v, limit) for v in value]
    else:
        items = [_clean(value, limit)] if value not in (None, "") else []
    return [x for x in items if x]


def generate(doc_type: str, spec: dict, save_path: str, images: dict = None):
    """
    生成文档。:param spec: 结构化内容; :param save_path: 用户选定的保存路径;
    :param images: {页序号: 图片字节}（可选，来自 fetch_images）
    :return: (成功与否, 失败原因)
    """
    kind = normalize_doc_type(doc_type)
    if not kind:
        return False, f"不支持的文档类型: {doc_type}"
    if not isinstance(spec, dict) or not spec:
        return False, "文档内容为空"
    if not save_path:
        return False, "未指定保存路径"
    try:
        if kind == "ppt":
            _gen_ppt(spec, save_path, images or {})
        elif kind == "doc":
            _gen_doc(spec, save_path)
        elif kind == "html":
            _gen_html(spec, save_path)
        else:
            _gen_xlsx(spec, save_path)
    except Exception as e:                      # 生成失败不让助手崩掉
        logger.error(f"[Doc] 生成失败 {save_path}: {type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {e}"
    logger.info(f"[Doc] 已生成 {kind}: {save_path}")
    return True, ""


# ------------------------------------------------------------
# 方案B：联网配图（Pexels 免费图库）
# ------------------------------------------------------------
def has_image_intent(doc_type: str, spec: dict) -> bool:
    """这轮生成会不会真的去搜图（用于提示用户"正在找图"）"""
    if normalize_doc_type(doc_type) != "ppt" or not config.PEXELS_API_KEY:
        return False
    slides = spec.get("slides") if isinstance(spec, dict) else None
    return isinstance(slides, list) and any(
        isinstance(s, dict) and _clean(s.get("image_query"))
        for s in slides[: config.DOC_MAX_SLIDES])


def fetch_images(doc_type: str, spec: dict) -> dict:
    """PPT 页面带 image_query 且已配置图库 Key 时联网搜图下载。
    :return: {页序号: 图片字节}；任何失败都安静返回已有部分/空 dict（退回纯排版）"""
    if normalize_doc_type(doc_type) != "ppt" or not config.PEXELS_API_KEY:
        return {}
    slides = spec.get("slides")
    if not isinstance(slides, list):
        return {}
    images = {}
    for idx, item in enumerate(slides[: config.DOC_MAX_SLIDES]):
        if len(images) >= config.DOC_IMAGE_MAX:
            break
        if not isinstance(item, dict):
            continue
        query = _clean(item.get("image_query"), 60)
        if not query:
            continue
        data = _download_image(query)
        if data:
            images[idx] = data
    if images:
        logger.info(f"[Doc] 图库配图: 成功 {len(images)} 张")
    return images


def _download_image(query: str):
    """搜一张横版图并下载字节；失败返回 None（调用方降级）"""
    try:
        resp = requests.get(
            config.PEXELS_API_URL,
            params={"query": query, "per_page": 1, "orientation": "landscape"},
            headers={"Authorization": config.PEXELS_API_KEY},
            timeout=config.DOC_IMAGE_TIMEOUT)
        if resp.status_code != 200:
            logger.warning(f"[Doc] 图库搜索失败: HTTP {resp.status_code}")
            return None
        photos = resp.json().get("photos") or []
        if not photos:
            logger.info(f"[Doc] 图库无结果: {query}")
            return None
        url = (photos[0].get("src") or {}).get("large")
        if not url:
            return None
        img = requests.get(url, timeout=config.DOC_IMAGE_TIMEOUT)
        if img.status_code != 200 or not img.content:
            return None
        return img.content
    except Exception as e:                      # noqa: BLE001 网络问题一律降级
        logger.warning(f"[Doc] 图片下载失败: {type(e).__name__}: {e}")
        return None


def _crop_to_fill(data: bytes, w_in: float, h_in: float) -> bytes:
    """居中裁剪到目标宽高比（插到页面上不变形），JPEG 字节"""
    try:
        from PIL import Image
        im = Image.open(BytesIO(data)).convert("RGB")
        target = w_in / h_in
        w, h = im.size
        if w / h > target:                      # 过宽：裁左右
            new_w = int(h * target)
            x0 = (w - new_w) // 2
            im = im.crop((x0, 0, x0 + new_w, h))
        else:                                   # 过高：裁上下
            new_h = int(w / target)
            y0 = (h - new_h) // 2
            im = im.crop((0, y0, w, y0 + new_h))
        buf = BytesIO()
        im.save(buf, "JPEG", quality=88)
        return buf.getvalue()
    except Exception:                           # 图片坏了解析不动：放弃配图
        return b""


# ------------------------------------------------------------
# PPT（多模板美化母版：五套主题 × 封面风格 + 原生表格 + 结束页）
# ------------------------------------------------------------
def _ppt_rect(slide, x, y, w, h, rgb, line_rgb=None, line_w=1.0,
              no_fill=False):
    """实心/描边矩形装饰（python-pptx 形状默认带主题样式，全部显式覆盖）"""
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches, Pt
    rect = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE,
                                  Inches(x), Inches(y), Inches(w), Inches(h))
    if no_fill:
        rect.fill.background()
    else:
        rect.fill.solid()
        rect.fill.fore_color.rgb = RGBColor(*rgb)
    if line_rgb:
        rect.line.color.rgb = RGBColor(*line_rgb)
        rect.line.width = Pt(line_w)
    else:
        rect.line.fill.background()
    rect.shadow.inherit = False
    return rect


def _ppt_circle(slide, x, y, d, rgb, line_rgb=None, no_fill=False, text="",
                text_size=12, text_rgb=(255, 255, 255)):
    """圆片装饰（可作页码徽章）"""
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches, Pt
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    ring = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y),
                                  Inches(d), Inches(d))
    if no_fill:
        ring.fill.background()
    else:
        ring.fill.solid()
        ring.fill.fore_color.rgb = RGBColor(*rgb)
    if line_rgb:
        ring.line.color.rgb = RGBColor(*line_rgb)
        ring.line.width = Pt(1.5)
    else:
        ring.line.fill.background()
    ring.shadow.inherit = False
    if text:
        tf = ring.text_frame
        tf.word_wrap = False
        tf.margin_left = tf.margin_right = 0
        tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        run = p.add_run()
        run.text = str(text)
        run.font.size = Pt(text_size)
        run.font.name = _PPT_FONT
        run.font.bold = True
        run.font.color.rgb = RGBColor(*text_rgb)
    return ring


def _ppt_text(slide, x, y, w, h, text, size, rgb, bold=False,
              align=None, line_spacing=None, font=None):
    """放一个文本框（显式字体，避免继承默认样式）"""
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    if align == "center":
        p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = text
    f = run.font
    f.size = Pt(size)
    f.bold = bold
    f.name = font or _PPT_FONT
    f.color.rgb = RGBColor(*rgb)
    if line_spacing:
        p.line_spacing = line_spacing
    return box


def _fill_bg(slide, w, h, rgb):
    _ppt_rect(slide, 0, 0, w, h, rgb)


def _ppt_cover(prs, spec, P, W, H):
    """封面：深底大标题，按模板换装饰风格"""
    title = _clean(spec.get("title"), 100) or "未命名演示文稿"
    subtitle = _clean(spec.get("subtitle"), 200) or "由凌零为您生成"
    slide = prs.slides.add_slide(prs.slide_layouts[6])   # 空白版式
    _fill_bg(slide, W, H, P["dark"])
    style = P["cover"]

    if style == "bar":
        _ppt_rect(slide, 0, 0, 0.26, H, P["accent"])
        _ppt_rect(slide, 0.95, 4.5, 3.4, 0.07, P["accent"])
        _ppt_text(slide, 0.95, 2.3, 11.6, 1.9, title, P.get("cover_size", 44), P.get("cover_title", (255, 255, 255)),
                  bold=True, font=P.get("font"))
        _ppt_text(slide, 1.0, 4.75, 11, 0.7, subtitle, 18, P["tint"])
    elif style == "band":
        _ppt_text(slide, 0.95, 2.0, 11.6, 1.9, title, P.get("cover_size", 44), P.get("cover_title", (255, 255, 255)),
                  bold=True, font=P.get("font"))
        _ppt_rect(slide, 0, H - 1.75, W, 1.75, P["theme"])
        _ppt_rect(slide, 0, H - 1.75, W, 0.06, P["accent"])
        _ppt_text(slide, 1.0, H - 1.35, 11, 0.7, subtitle, 18,
                  (255, 255, 255))
    elif style == "circle":
        _ppt_circle(slide, W - 4.6, H - 3.9, 5.6, P["theme"])
        _ppt_circle(slide, W - 5.4, H - 1.6, 2.2, P["accent"])
        _ppt_text(slide, 0.95, 2.15, 8.2, 2.4, title, P.get("cover_size", 44), P.get("cover_title", (255, 255, 255)),
                  bold=True, font=P.get("font"))
        _ppt_text(slide, 1.0, 4.6, 8, 0.7, subtitle, 18, P["tint"])
    elif style == "ribbon":
        band = _ppt_rect(slide, -2.5, 4.4, 19, 1.5, P["theme"])
        band.rotation = 352
        thin = _ppt_rect(slide, -2.5, 4.2, 19, 0.12, P["accent"])
        thin.rotation = 352
        _ppt_text(slide, 0.95, 2.2, 11.6, 1.9, title, P.get("cover_size", 44), P.get("cover_title", (255, 255, 255)),
                  bold=True, font=P.get("font"))
        _ppt_text(slide, 1.0, 4.62, 8.5, 0.7, subtitle, 18, (255, 255, 255))
    else:                                                # frame 极简细框
        _ppt_rect(slide, 0.45, 0.45, W - 0.9, H - 0.9, P["accent"],
                  line_rgb=P["accent"], line_w=1.2, no_fill=True)
        _ppt_rect(slide, 0.45, 0.45, 1.5, 1.5, P["accent"])
        _ppt_text(slide, 1.2, 2.5, 11, 1.9, title, P.get("cover_size", 44), P.get("cover_title", (255, 255, 255)),
                  bold=True, font=P.get("font"))
        _ppt_text(slide, 1.25, 4.55, 10.5, 0.7, subtitle, 18, P["tint"])

    _ppt_text(slide, 0.95, 6.92, 6, 0.4, "凌零 · 桌面AI助手", 10, P["tint"])
    return slide


def _ppt_content_page(prs, item, idx, P, W, H, images):
    """内容页：彩色底 + 白色圆角内容卡 + 标题区 + 要点/配图/表格组合

    底色用模板浅色调（不再是白纸），四角加装饰圆，内容装在白色圆角
    卡片里——既不刺眼，也和封面深底形成"彩色边框"的层次感。
    """
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Pt as _Pt, Inches

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _fill_bg(slide, W, H, P["tint"])
    # 装饰圆（先画，被内容卡压在下面，只露出边角）
    _ppt_circle(slide, W - 1.7, -1.5, 3.6, P["accent"])
    _ppt_circle(slide, -1.4, H - 1.9, 2.8, P["theme"])
    _ppt_rect(slide, 0, 0, W, 0.09, P["theme"])          # 顶部细条
    # 白色圆角内容卡
    card = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.55), Inches(1.52),
        Inches(W - 1.1), Inches(5.33))
    from pptx.dml.color import RGBColor
    card.fill.solid()
    card.fill.fore_color.rgb = RGBColor(255, 255, 255)
    card.line.color.rgb = RGBColor(*P["accent"])
    card.line.width = _Pt(1.0)
    card.shadow.inherit = False
    try:
        card.adjustments[0] = 0.045                       # 圆角小一点
    except (IndexError, ValueError):
        pass
    # 标题区（在彩色底上）
    _ppt_rect(slide, 0.9, 0.52, 0.2, 0.6, P["theme"])
    _ppt_text(slide, 1.25, 0.36, 10.6, 0.9,
              _clean(item.get("title"), 100) or " ",
              P.get("heading_size", 28), P.get("heading", P["dark"]),
              bold=True, font=P.get("font"))

    bullets = _str_list(item.get("bullets"), config.DOC_MAX_TEXT)[:20]
    table = item.get("table") if isinstance(item.get("table"), dict) else None
    img_data = images.get(idx)

    if bullets and not table:
        rows_n = len(bullets) or 1
        base = P.get("body_size", 18)          # 用户定的正文字号（默认自适应）
        size = base if rows_n <= 6 else (max(base - 2, 12)
                                         if rows_n <= 10 else max(base - 4, 12))
        space = 13 if rows_n <= 8 else 8
        if img_data:
            pic = _crop_to_fill(img_data, 5.6, 4.3)
            if pic:
                _bullets_box(slide, bullets, 1.15, 2.0, 4.9, 4.4,
                             size=max(size - 1, 13), space=space, P=P)
                picture = slide.shapes.add_picture(
                    BytesIO(pic), Inches(6.5), Inches(1.95),
                    Inches(5.6), Inches(4.3))
                picture.line.color.rgb = RGBColor(*P["theme"])
                picture.line.width = _Pt(1.5)
            else:
                _bullets_box(slide, bullets, 1.15, 2.0, W - 2.3, 4.4,
                             size=size, space=space, P=P)
        else:
            _bullets_box(slide, bullets, 1.15, 2.0, W - 2.3, 4.4,
                         size=size, space=space, P=P)
    elif table:
        headers = _str_list(table.get("headers"))[:_PPT_TABLE_MAX_COLS]
        rows_raw = table.get("rows")
        rows = [r for r in (rows_raw or [])
                if isinstance(r, (list, tuple))][:12]
        if headers and rows:
            if bullets:
                # 要点 + 表格上下排：要点区收紧，表格行数也收敛
                _bullets_box(slide, bullets[:4], 1.15, 1.85, W - 2.3, 1.55,
                             size=max(P.get("body_size", 18) - 4, 12),
                             space=4, P=P)
                _ppt_table(slide, table, P, 1.15, 3.6, W - 2.3,
                           max_rows=6, compact=True)
            else:
                _ppt_table(slide, table, P, 1.15, 1.95, W - 2.3,
                           max_rows=_PPT_TABLE_MAX_ROWS)
        elif bullets:
            _bullets_box(slide, bullets, 1.15, 2.0, W - 2.3, 4.4,
                         size=P.get("body_size", 18), space=13, P=P)
        else:
            _ppt_text(slide, 1.15, 3.4, W - 2.3, 1.0, "（本页无内容）", 16,
                      P["theme"], align="center")
    elif not bullets and not img_data:
        _ppt_text(slide, 1.15, 3.4, W - 2.3, 1.0, "（本页无要点）", 16,
                  P["theme"], align="center")

    # 页脚 + 页码徽章（在彩色底上，卡片外）
    _ppt_text(slide, 0.95, 7.0, 5, 0.35, "凌零 · 桌面AI助手", 9, P["gray"])
    _ppt_circle(slide, 12.35, 6.92, 0.42, P["theme"],
                text=str(idx + 1), text_size=11)
    return slide


def _ppt_table(slide, table, P, x, y, w, max_rows=10, compact=False):
    """原生 PPT 表格：主题色表头 + 隔行浅色填充（好看且可在 PPT 里继续编辑）"""
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.util import Inches, Pt

    headers = _str_list(table.get("headers"))[:_PPT_TABLE_MAX_COLS]
    rows_raw = table.get("rows")
    rows = [r for r in (rows_raw or [])
            if isinstance(r, (list, tuple))][:max_rows]
    if not headers or not rows:
        return None
    ncols, nrows = len(headers), 1 + len(rows)
    row_h = 0.34 if compact else (0.44 if nrows <= 6 else 0.4)
    head_h = 0.4 if compact else 0.46
    graphic = slide.shapes.add_table(nrows, ncols, Inches(x), Inches(y),
                                     Inches(w),
                                     Inches(head_h + row_h * nrows))
    tbl = graphic.table
    # 列宽按内容长度加权（首列保底，避免窄列挤成竖排字）
    weights = []
    for j in range(ncols):
        cells = [len(str(headers[j]))] + [
            len(str(r[j])) for r in rows if j < len(r)]
        weights.append(max(max(cells) * 0.16, 1.6))
    total = sum(weights)
    for j, wt in enumerate(weights):
        tbl.columns[j].width = Inches(w * wt / total)
    for i in range(nrows):
        tbl.rows[i].height = Inches(0.46 if (i == 0 and not compact)
                                    else row_h)
    for j, head in enumerate(headers):
        _ppt_cell(tbl.cell(0, j), _clean(head, 60), size=13, bold=True,
                  rgb=(255, 255, 255), fill=P["theme"])
    for i, row in enumerate(rows, start=1):
        fill = P["tint"] if i % 2 == 0 else (255, 255, 255)
        for j in range(ncols):
            v = row[j] if j < len(row) else ""
            _ppt_cell(tbl.cell(i, j), _clean(v, 60), size=12,
                      rgb=P["text"], fill=fill)
    return graphic


def _ppt_cell(cell, text, size, rgb, fill, bold=False):
    """表格单元格：字体/颜色/填充/垂直居中一次设置到位"""
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt
    from pptx.enum.text import MSO_ANCHOR
    cell.fill.solid()
    cell.fill.fore_color.rgb = RGBColor(*fill)
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    cell.margin_left = cell.margin_right = Inches(0.06)
    cell.margin_top = cell.margin_bottom = Inches(0.02)
    tf = cell.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = _PPT_FONT
    run.font.color.rgb = RGBColor(*rgb)


def _ppt_closing(prs, spec, P, W, H):
    """结束页：深底"谢谢观看"，回扣封面风格"""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _fill_bg(slide, W, H, P["dark"])
    if P["cover"] == "circle":
        _ppt_circle(slide, W - 4.2, H - 4.4, 5.2, P["theme"])
    if P["cover"] == "bar":
        _ppt_rect(slide, 0, 0, 0.26, H, P["accent"])
    _ppt_rect(slide, W / 2 - 1.6, 2.85, 3.2, 0.06, P["accent"])
    _ppt_text(slide, 1.6, 3.15, W - 3.2, 1.4, "谢谢观看",
              P.get("cover_size", 46), (255, 255, 255),
              bold=True, align="center", font=P.get("font"))
    _ppt_text(slide, 1.6, 4.6, W - 3.2, 0.6,
              _clean(spec.get("title"), 60) or "", 15, P["tint"],
              align="center")
    _ppt_text(slide, 1.6, 6.85, W - 3.2, 0.4, "凌零 · 桌面AI助手", 10,
              P["tint"], align="center")
    return slide


def _gen_ppt(spec: dict, path: str, images: dict):
    from pptx import Presentation
    from pptx.util import Inches

    slides = spec.get("slides")
    if isinstance(slides, dict):
        slides = [slides]
    if not isinstance(slides, list) or not slides:
        raise ValueError("缺少 slides 内容")
    slides = [s for s in slides[: config.DOC_MAX_SLIDES]
              if isinstance(s, dict)]
    if not slides:
        raise ValueError("缺少 slides 内容")

    P = resolve_template(spec)
    apply_palette_style(P, resolve_style(spec))   # 用户样式覆盖默认主题
    prs = Presentation()
    prs.slide_width = Inches(13.333)            # 16:9
    prs.slide_height = Inches(7.5)
    W, H = 13.333, 7.5

    _ppt_cover(prs, spec, P, W, H)
    for idx, item in enumerate(slides):
        _ppt_content_page(prs, item, idx, P, W, H, images)
    _ppt_closing(prs, spec, P, W, H)
    prs.save(path)


def _bullets_box(slide, bullets, x, y, w, h, size=17, space=12, P=None):
    """要点列表文本框：彩色圆点 + 深色正文（配图/通栏/表格版式共用）"""
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt
    pal = P or resolve_template({})
    font = pal.get("font") or _PPT_FONT
    ls = pal.get("line_spacing")           # 用户要求的行距（倍数）
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    for i, line in enumerate(bullets):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        dot = p.add_run()
        dot.text = "▪  "
        dot.font.size = Pt(size)
        dot.font.name = font
        dot.font.color.rgb = RGBColor(*pal["accent"])
        dot.font.bold = True
        run = p.add_run()
        run.text = line
        run.font.size = Pt(size)
        run.font.name = font
        run.font.color.rgb = RGBColor(*pal["text"])
        p.space_after = Pt(space)
        if ls:
            p.line_spacing = ls


# ------------------------------------------------------------
# Word（标题/章节主题色）
# ------------------------------------------------------------
def _gen_doc(spec: dict, path: str):
    import docx
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    sections = spec.get("sections")
    if isinstance(sections, dict):
        sections = [sections]
    if not isinstance(sections, list) or not sections:
        raise ValueError("缺少 sections 内容")
    sections = sections[: config.DOC_MAX_SLIDES]

    style = resolve_style(spec)
    document = docx.Document()
    try:    # 母版样式上标题色（用户指定颜色优先，个别模板缺样式时忽略）
        document.styles["Title"].font.color.rgb = RGBColor(
            *(style.get("title_color") or _DARK))
        document.styles["Heading 1"].font.color.rgb = RGBColor(
            *(style.get("accent_color") or _THEME))
    except (KeyError, AttributeError):
        pass
    if style.get("body_size") or style.get("line_spacing") \
            or style.get("font"):
        try:    # 正文样式：字号 / 行距 / 字体（含中文 eastAsia 字体名）
            normal = document.styles["Normal"]
            if style.get("body_size"):
                normal.font.size = Pt(style["body_size"])
            if style.get("font"):
                normal.font.name = style["font"]
                rpr = normal.element.get_or_add_rPr()
                rpr.get_or_add_rFonts().set(qn("w:eastAsia"), style["font"])
            if style.get("line_spacing"):
                normal.paragraph_format.line_spacing = style["line_spacing"]
        except (KeyError, AttributeError):
            pass
    document.add_heading(_clean(spec.get("title"), 100) or "未命名文档", 0)
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        document.add_heading(_clean(sec.get("heading"), 100) or " ", level=1)
        for para in _str_list(sec.get("paragraphs"), config.DOC_MAX_TEXT):
            document.add_paragraph(para)
        table = sec.get("table")
        if isinstance(table, dict):
            headers = _str_list(table.get("headers"))[:20]
            rows = table.get("rows")
            if headers and isinstance(rows, list):
                rows = [r for r in rows if isinstance(r, (list, tuple))]
                rows = rows[: config.DOC_MAX_ROWS]
                t = document.add_table(rows=1 + len(rows), cols=len(headers))
                t.style = "Table Grid"
                for j, h in enumerate(headers):
                    t.rows[0].cells[j].text = _clean(h)
                    for run in t.rows[0].cells[j].paragraphs[0].runs:
                        run.font.bold = True
                for i, row in enumerate(rows, start=1):
                    for j in range(len(headers)):
                        cell = row[j] if j < len(row) else ""
                        t.rows[i].cells[j].text = _clean(cell, config.DOC_MAX_TEXT)
    document.save(path)


# ------------------------------------------------------------
# Excel（主题色表头 + 冻结首行）
# ------------------------------------------------------------
def _sheet_name(name, index: int) -> str:
    """Excel 工作表名限制：31 字符且不含 :\\/?*[]"""
    clean = _clean(name, 31) or f"Sheet{index + 1}"
    for ch in ':\\/?*[]':
        clean = clean.replace(ch, " ")
    return clean.strip() or f"Sheet{index + 1}"


def _gen_xlsx(spec: dict, path: str):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    sheets = spec.get("sheets")
    if isinstance(sheets, dict):
        sheets = [sheets]
    if not isinstance(sheets, list) or not sheets:
        raise ValueError("缺少 sheets 内容")
    sheets = sheets[:10]

    wb = Workbook()
    wb.remove(wb.active)
    header_font = Font(bold=True, color="FFFFFF")
    header_rgb = (resolve_style(spec).get("accent_color")
                  or resolve_style(spec).get("title_color") or _THEME)
    header_fill = PatternFill("solid", fgColor=_css_hex(header_rgb).lstrip("#"))
    for idx, item in enumerate(sheets):
        if not isinstance(item, dict):
            continue
        ws = wb.create_sheet(_sheet_name(item.get("name"), idx))
        headers = _str_list(item.get("headers"))[:50]
        if headers:
            ws.append(headers)
            for cell in ws[1]:
                cell.font = header_font
                cell.fill = header_fill
        rows = item.get("rows")
        if isinstance(rows, list):
            for row in rows[: config.DOC_MAX_ROWS]:
                if isinstance(row, (list, tuple)):
                    ws.append([_clean(v, config.DOC_MAX_TEXT)
                               for v in row[:len(headers) or 50]])
        if headers:
            for j, h in enumerate(headers, start=1):
                width = max([len(str(h))] + [len(str(r[j - 1])) for r in rows
                                             if isinstance(r, (list, tuple))
                                             and j - 1 < len(r)][:200] or [8])
                ws.column_dimensions[
                    ws.cell(row=1, column=j).column_letter].width = \
                    min(max(width * 1.8, 8), 40)
            ws.freeze_panes = "A2"
    if not wb.sheetnames:
        raise ValueError("没有可用的工作表")
    wb.save(path)


# ------------------------------------------------------------
# 网页报告（HTML）：单文件、内联样式，生成后浏览器直接打开即可演示
# ------------------------------------------------------------
_HTML_CSS = """
body { font-family: "Microsoft YaHei", sans-serif; margin: 0;
       background: #f2f6fa; color: #26303a; }
.wrap { max-width: 900px; margin: 0 auto; padding: 0 24px 48px; }
header { background: linear-gradient(135deg, #0F2B46 0%, #1B6B9A 100%);
         color: #fff; padding: 42px 24px 34px; text-align: center; }
header h1 { margin: 0 0 10px; font-size: 30px; letter-spacing: 1px; }
header p { margin: 0; opacity: .85; font-size: 15px; }
section { background: #fff; border-radius: 14px; padding: 26px 30px;
          margin-top: 22px; box-shadow: 0 2px 10px rgba(15,43,70,.08); }
section h2 { margin: 0 0 14px; font-size: 20px; color: #1B6B9A;
             border-left: 4px solid #1B6B9A; padding-left: 12px; }
section li { margin: 7px 0; line-height: 1.7; font-size: 15px; }
table { border-collapse: collapse; width: 100%; margin-top: 14px;
        font-size: 14px; }
th { background: #1B6B9A; color: #fff; padding: 9px 10px; text-align: left; }
td { border-bottom: 1px solid #e3ecf3; padding: 8px 10px; }
tr:nth-child(even) td { background: #eef4f9; }
footer { text-align: center; color: #8aa0b2; font-size: 13px;
         margin-top: 34px; }
"""


def _gen_html(spec: dict, path: str):
    """生成网页报告：sections 分卡片，每节可带段落/要点/表格"""
    import html as _html

    title = _clean(spec.get("title"), 120) or "调研报告"
    subtitle = _clean(spec.get("subtitle"), 160)
    sections = spec.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("html 需要 sections 内容")
    sections = sections[: config.DOC_MAX_SLIDES]

    # 用户样式 -> CSS 覆盖块（标题色/强调色/字号/行距/字体）
    style = resolve_style(spec)
    css = _HTML_CSS
    if style:
        rules = []
        if style.get("title_color"):
            c = _css_hex(style["title_color"])
            rules.append(f"header h1, section h2 {{ color: {c}; }}")
        if style.get("accent_color"):
            c = _css_hex(style["accent_color"])
            rules.append(f"section h2 {{ border-left-color: {c}; }}"
                         f"th {{ background: {c}; }}")
        if style.get("body_size"):
            rules.append(f"section li {{ font-size: {style['body_size']:.0f}px; }}")
        if style.get("line_spacing"):
            rules.append(f"section li {{ line-height: {style['line_spacing']}; }}")
        if style.get("font"):
            fam = style["font"].replace('"', "").replace("<", "")
            rules.append(f'body, th, td {{ font-family: "{fam}", sans-serif; }}')
        css += "\n" + "\n".join(rules)

    out = ["<!DOCTYPE html><html lang=\"zh\"><head><meta charset=\"utf-8\">",
           "<meta name=\"viewport\" content=\"width=device-width,",
           "initial-scale=1\"><title>",
           _html.escape(title), "</title><style>", css,
           "</style></head><body>",
           "<header><h1>", _html.escape(title), "</h1>",
           f"<p>{_html.escape(subtitle or '凌零 · 桌面AI助手 出品')}</p></header>",
           "<div class=\"wrap\">"]

    def _table_html(tb) -> str:
        if not isinstance(tb, dict):
            return ""
        headers = _str_list(tb.get("headers"), 60)[:8]
        rows = tb.get("rows") if isinstance(tb.get("rows"), list) else []
        if not headers:
            return ""
        buf = ["<table><tr>"]
        buf += [f"<th>{_html.escape(h)}</th>" for h in headers]
        buf.append("</tr>")
        for row in rows[: config.DOC_MAX_ROWS]:
            if not isinstance(row, (list, tuple)):
                continue
            cells = [_html.escape(_clean(v, config.DOC_MAX_TEXT))
                     for v in row[:len(headers)]]
            cells += [""] * (len(headers) - len(cells))
            buf.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells)
                       + "</tr>")
        buf.append("</table>")
        return "".join(buf)

    for sec in sections:
        if not isinstance(sec, dict):
            continue
        heading = _clean(sec.get("heading"), 100)
        paras = _str_list(sec.get("paragraphs"), config.DOC_MAX_TEXT)
        if not heading and not paras:
            continue
        out.append("<section>")
        if heading:
            out.append(f"<h2>{_html.escape(heading)}</h2>")
        for p in paras:
            out.append(f"<li>{_html.escape(p)}</li>")
        out.append(_table_html(sec.get("table")))
        out.append("</section>")
    out.append("</div><footer>凌零 · 桌面AI助手 为你生成</footer></body></html>")

    if len(out) <= 8:                     # 只有头尾，没有任何实际章节
        raise ValueError("html 章节内容为空")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(out))
