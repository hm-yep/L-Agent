# -*- coding: utf-8 -*-
"""统一字体系统：一套字体栈 + 清晰字号阶梯 + 高 DPI 设置。

背景：历史上界面各控件只写 font-size、不写 font-family，Windows 下会
回退到 Qt 默认字体（中文常是宋体，发虚带衬线），字号也是 11~19 乱用。
本模块把「字体栈 / 字号 / 字重」集中定义，所有窗口统一引用，避免字体
不统一、发虚、偏小的问题。

字体栈优先用现代无衬线体：
  * 西文/数字：Segoe UI（Windows 系统 UI 字体，比 Arial 清爽）；
  * 中文：微软雅黑 UI（界面专用字面，比普通雅黑更利落）→ 等线 →
    思源黑体 → 苹方，最后 sans-serif 兜底。
跨平台（macOS / Linux）也能平滑降级，不依赖某一台机器的特定字体。

用法：
    from ui.typography import install_app_fonts, FONT_STACK, SZ, WT
    install_app_fonts(app)                    # main.py 里建好 QApplication 后调用
    # 或在 QSS 里用占位符替换：
    qss = QSS_TEMPLATE.replace("__FONT_STACK__", FONT_STACK)
                      .replace("__FS_BODY__", str(SZ["body"]))
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

# ---- 字体栈（Qt QSS 的 font-family 值）----------------------------------
FONT_STACK = (
    '"Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", '
    '"DengXian", "Source Han Sans SC", "Noto Sans CJK SC", '
    '"PingFang SC", "Hiragino Sans GB", sans-serif'
)
# 等宽栈：仅给代码/哈希/时间戳等需要对齐的场景使用
MONO_STACK = (
    '"Cascadia Mono", "Cascadia Code", "Consolas", '
    '"SF Mono", "Menlo", monospace'
)

# ---- 字号阶梯（px，整体比旧版调大，保证可读）---------------------------
# 取值思路：正文 15 起步，辅助信息不低于 12；标题拉开层级。
SZ = {
    "micro": 12,    # 极次要：角标、分组标题
    "caption": 13,  # 辅助说明、时间戳、状态栏
    "small": 14,    # 表格单元格、次级正文
    "body": 15,     # 正文、输入框、导航项（默认档）
    "lead": 16,     # 强调正文、主按钮
    "subtitle": 18,  # 区块小标题、品牌副标题
    "title": 21,    # 页面标题
    "brand": 24,    # 左侧品牌主标题
}

# ---- 字重（QSS font-weight 用数字，Qt5 支持 100~900）--------------------
WT = {
    "regular": 400,
    "medium": 500,
    "semibold": 600,
    "bold": 700,
}

# 应用级默认字号（桌宠等未显式设字号的窗口会继承）
_APP_POINT_SIZE = 10


def enable_high_dpi() -> None:
    """在 QApplication 创建【之前】调用，让高分屏文字清晰不发虚。

    Qt5 默认可能不开缩放，导致 125%/150% 屏上文字模糊、控件偏小。
    这两个属性必须在 QApplication 实例化前设置才生效。
    """
    from PyQt5.QtWidgets import QApplication
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)


def app_font(point_size: int = None) -> QFont:
    """构造应用级默认 QFont（界面字体栈，带抗锯齿提示）。"""
    f = QFont()
    families = [
        "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei",
        "DengXian", "Source Han Sans SC", "Noto Sans CJK SC",
        "PingFang SC", "sans-serif",
    ]
    f.setFamilies(families)
    f.setPointSize(point_size or _APP_POINT_SIZE)
    f.setStyleStrategy(QFont.PreferAntialias)
    return f


def install_app_fonts(app) -> None:
    """给 QApplication 装统一默认字体（字号由 QSS 按控件再细化）。"""
    app.setFont(app_font())


def qss_tokens() -> dict:
    """返回 QSS 占位符 -> 值的映射，供各窗口 _qss() 统一替换。"""
    return {
        "__FONT_STACK__": FONT_STACK,
        "__MONO_STACK__": MONO_STACK,
        **{"__FS_%s__" % k.upper(): str(v) for k, v in SZ.items()},
    }


def apply_qss_tokens(qss: str) -> str:
    """把字体 tokens 替换进 QSS 文本。"""
    for key, value in qss_tokens().items():
        qss = qss.replace(key, value)
    return qss
