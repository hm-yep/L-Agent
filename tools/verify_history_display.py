# -*- coding: utf-8 -*-
"""历史记录面板真实字体渲染验证（防"半行字被裁"回归）

与离屏测试不同：本脚本用【真实 Windows 显示字体】跑（不设 offscreen），
控件只离屏渲染到内存位图（绝不 show，桌面不会弹任何窗口）。

验证三件事：
1. QSS 的 19px 字体在正文标签上真实生效（上一版裁字的根因就是按默认字体量高）；
2. 逐条真实渲染：把正文标签按"算出的条目高度"渲染成位图，扫描最深色像素行，
   断言文字底边落在气泡内边距之上 —— 即任何一条都不会被裁掉半行；
3. 行内几何：正文标签固定宽 + 头像在条目内放得下（不产生横向挤压）。

运行: python tools/verify_history_display.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontInfo, QImage
from PyQt5.QtWidgets import QApplication, QLabel

app = QApplication([])          # 真实平台；全程不 show()，屏幕无感

from ui import input_dialog as hd
from ui.input_dialog import (HistoryPanel, CONTENT_LABEL_W, CONTENT_TEXT_W,
                             FOLD_TEXT_LEN, PANEL_WIDTH)

qss_path = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "ui", "style.qss")
with open(qss_path, encoding="utf-8") as f:
    _qss = f.read()

passed, failed = 0, 0


def check(name, cond, detail=""):
    global passed, failed
    passed += bool(cond)
    failed += not cond
    print(f"  [{'PASS]' if cond else 'FAIL]'} {name}"
          + (f"  {detail}" if detail and not cond else ""))


class FakeMem:
    def __init__(self, pairs):
        self._pairs = pairs

    def get_pairs(self):
        return self._pairs


long_cn = ("这是一段用于验证真实渲染高度的中文长回复。" * 12) \
    + "结尾混排 English words and 数字 12345 检查中西文混行。"
long_code = ("def plan(steps):\n"
             "    for i, s in enumerate(steps, 1):\n"
             "        print(f'第{i}步: {s}')  # 注释混排说明\n"
             "    return 'done'\n") * 6
short = "短回复。"
# 边界压力样本：折叠阈值两侧、多换行、无空格长串（最坏换行情况）
boundary_140 = "正好一百四十字的回复不用折叠。" * 10 \
    + "补零补齐到一百四十字整补零补齐到一百四"          # len==140
boundary_141 = boundary_140 + "整加一"                    # len>140 折叠
many_newlines = "\n".join(f"第{i}行短句" for i in range(1, 41))[:139] \
    + "补"
long_ascii = "a" * 60 + "_" + "b" * 78                    # 139 字符无空格
texts = [
    ("pet", long_cn),
    ("pet", long_code),
    ("user", long_cn),
    ("user", "用户也很长的消息：" + "慢慢输入内容测试用户侧的换行与高度。" * 10),
    ("pet", short),
    ("pet", boundary_140),
    ("pet", boundary_141),
    ("user", many_newlines),
    ("pet", long_ascii),
]

panel = HistoryPanel()
now = 1758000000.0
panel.bind_memory(FakeMem([
    {"user": "问题", "reply": t, "ts": now, "new_session": False}
    for _role, t in texts
]))


def _find_row(text):
    for i in range(panel._list.count()):
        item = panel._list.item(i)
        if item.data(Qt.UserRole) != text:
            continue
        w = panel._list.itemWidget(item)
        if w is None:
            continue
        label = next((lb for lb in w.findChildren(QLabel)
                      if lb.objectName().startswith("historyText")
                      and lb.text().startswith(text[:6])), None)
        if label is not None:
            return item, label
    return None, None


def _make_probe(text: str, role: str, height: int = None) -> QLabel:
    """构造与产品正文标签同款（QSS/固定宽/顶对齐）的探针标签"""
    probe = QLabel(text[:FOLD_TEXT_LEN] + "……"
                   if len(text) > FOLD_TEXT_LEN else text)
    probe.setObjectName("historyTextUser" if role == "user"
                        else "historyTextPet")
    probe.setWordWrap(True)
    probe.setAlignment(Qt.AlignLeft | Qt.AlignTop)
    probe.setStyleSheet(_qss)
    probe.ensurePolished()
    probe.setFixedWidth(CONTENT_LABEL_W)
    if height:
        probe.resize(CONTENT_LABEL_W, height)
    return probe


def _true_text_bottom(text: str, role: str) -> int:
    """用独立探针标签真实渲染文本，返回最深"文字色"像素的【逻辑 y 坐标】。
    顶对齐保证文字从顶部内边距开始画，底边即真实文字高度；
    grab 出的位图是设备像素，按 devicePixelRatio 归一化回逻辑像素。
    文字色 #3a2d20 是气泡里唯一的深色（边框/背景都是浅色）。"""
    probe = _make_probe(text, role, height=2000)   # 给足高度让文字完整铺开
    img = probe.grab().toImage().convertToFormat(QImage.Format_ARGB32)
    dpr = probe.devicePixelRatioF() or 1.0
    bottom = -1
    for y in range(img.height() - 1, -1, -1):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y)
            if c.red() < 120 and c.green() < 120 and c.blue() < 120:
                bottom = y
                break
        if bottom >= 0:
            break
    return bottom / dpr


print("\n一、QSS 字体真实生效（上一版裁字根因）")
item, label = _find_row(long_cn)
label.ensurePolished()
px = QFontInfo(label.font()).pixelSize()
check("正文标签字体 = 19px（真实平台）", px == 19, f"pixelSize={px}")

print("\n二、逐条真实渲染：文字底边必须落在气泡内边距之上")
for role, text in texts:
    probe = _make_probe(text, role)
    content_h = hd.HistoryPanel._content_height(probe, CONTENT_TEXT_W)
    true_bottom = _true_text_bottom(text, role)   # 含顶部内边距10的真实底边
    # 内容区限额 = content_h - 底部保护 10；文字自身从顶部 10px 处开始画，
    # 故文字底边(true_bottom) 与内容区底(content_h-10) 的关系即是否裁字
    limit = content_h - 10
    folded = len(text) > FOLD_TEXT_LEN
    check(f"[{role}] len={len(text)} 折叠={folded} 文字底边 {true_bottom}"
          f" ≤ 内容区底 {limit}", 0 <= true_bottom <= limit,
          f"内容高 {content_h}")

print("\n三、行内几何（固定宽标签不横向挤压）")
avail = PANEL_WIDTH - 72          # 条目内可用宽
check("用户行放得下（边距12+正文716+间距8+头像28 ≤ 条目宽）",
      12 + CONTENT_LABEL_W + 8 + 28 <= avail,
      f"{12 + CONTENT_LABEL_W + 8 + 28} vs {avail}")
check("正文换行宽度与高度计算同源", CONTENT_TEXT_W == CONTENT_LABEL_W - 28)

print("\n============================================================")
print(f"结果: {passed} 通过 / {failed} 失败")
print("============================================================")
sys.exit(0 if failed == 0 else 1)
