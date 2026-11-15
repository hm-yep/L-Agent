# -*- coding: utf-8 -*-
"""程序化绘制的矢量线框图标（Feather 风格，MIT），不依赖系统 emoji 字体。

用过 emoji / 特殊符号（💬 ⭐ 🧠 🛰 📦 ⚙ ⛶ ◐ ← ⟳ ＋ ▲）的地方
在 Windows 上常常渲染得廉价、不一致。这里改用 24x24 统一描边的 SVG 线框，
按主题文字色渲染，并 @2x 输出保证高分屏清晰；主题切换时整体重新着色。
"""

from PyQt5.QtCore import QByteArray, QSize
from PyQt5.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer

# 每个图标是 24x24 viewBox 内的 SVG 子元素（fill=none + stroke，统一描边）。
_PATHS = {
    # ---- 左侧导航 ----
    "chat": '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 '
            '8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 '
            '8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 '
            '1 8 8v.5z"/>',
    "prefs": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" '
             'y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" '
             'x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" '
             'y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/>'
             '<line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" '
             'y2="16"/>',
    "rules": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "trace": '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    "deliveries": '<line x1="16.5" y1="9.4" x2="7.5" y2="4.21"/>'
                  '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4'
                  'A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4'
                  'A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 '
                  '20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
    "about": '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" '
             'y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
    # ---- 标题栏 / 命令栏 ----
    "search": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" '
              'y2="16.65"/>',
    "refresh": '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 '
               '7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 '
               '4.36A9 9 0 0 0 20.49 15"/>',
    "bell": '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>'
            '<path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
    "maximize": '<path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3'
                'a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" '
            'y2="12"/>',
    "send": '<line x1="22" y1="2" x2="11" y2="13"/>'
            '<polygon points="22 2 15 22 11 13 2 9 22 2"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" '
             'y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 '
             '15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    "arrow-left": '<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 '
                  '5 12 12 5"/>',
    "sun": '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/>'
           '<line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" '
           'x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>'
           '<line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" '
           'y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>'
           '<line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
    "moon": '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    # ---- 页面内操作 ----
    "trash": '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7'
             'a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
             '<line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" '
             'x2="14" y2="17"/>',
    "x-circle": '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" '
                'y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>',
    "open": '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
            '<polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" '
            'y2="3"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 '
                '.33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 '
                '0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 '
                '1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1'
                '-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 '
                '0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 '
                '0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 '
                '0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 '
                '1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 '
                '2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 '
                '1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
}

_CACHE = {}


def icon(name: str, color: str, size: int = 18) -> QIcon:
    """按 (名称, 颜色, 尺寸) 渲染一个矢量线框图标，结果缓存复用。

    color 为十六进制（如 '#E8EEF4'）。以 2x 分辨率渲染再降回逻辑尺寸，
    保证高分屏下不糊。
    """
    key = (name, color, size)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    body = _PATHS.get(name)
    if body is None:
        return QIcon()

    px = size * 2          # 2x 超采样
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 24 24" fill="none" stroke="%s" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">%s</svg>'
        % (px, px, color, body)
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))

    img = QImage(px, px, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()

    pix = QPixmap.fromImage(img)
    pix.setDevicePixelRatio(2)
    ic = QIcon(pix)
    _CACHE[key] = ic
    return ic
