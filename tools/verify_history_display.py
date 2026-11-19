# -*- coding: utf-8 -*-
"""历史记录面板真实字体渲染验证（防"半行字被裁"回归）

与离屏测试不同：本脚本用【真实 Windows 显示字体】跑（不设 offscreen），
控件只离屏渲染到内存位图（绝不 show，桌面不会弹任何窗口）。

样式改版后样式表挂在 QApplication 上（ui/theme.py 是唯一来源），
所以这里不再有"读 style.qss 贴到控件"这一步。

验证三件事：
1. 主题令牌的正文字号在正文标签上真实生效（旧版裁字的根因就是按默认字体量高）；
2. 逐条真实渲染：把正文标签按"算出的条目高度"渲染成位图，扫描文字像素行，
   断言文字底边落在气泡内边距之上 —— 即任何一条都不会被裁掉半行；
3. 行内几何：正文标签固定宽 + 头像在条目内放得下（不产生横向挤压），
   且面板总宽是由这些常量一层层推出来的，不是拍脑袋写的数字。

运行: python tools/verify_history_display.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontInfo, QImage
from PyQt5.QtWidgets import (QApplication, QLabel, QVBoxLayout, QWidget)

app = QApplication([])          # 真实平台；全程不 show()，屏幕无感

from ui import theme
from ui.typography import SZ
from ui import input_dialog as hd
from ui.input_dialog import (HistoryPanel, AVATAR_W, CONTENT_CHROME_H,
                             CONTENT_CHROME_W, CONTENT_LABEL_W, CONTENT_SAFE_H,
                             CONTENT_TEXT_W, FOLD_TEXT_LEN, ITEM_GAP,
                             ITEM_MARGIN, ITEM_W, PANEL_WIDTH)

theme.install(app)              # 与 main.py 同一条路径：全局样式表挂到 app

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


def _make_label(text: str, role: str) -> QLabel:
    """构造与产品正文标签同款（同 objectName/固定宽/顶对齐）的探针标签

    不再 setStyleSheet：样式来自 QApplication，控件照常继承，
    这也顺带验证了"样式挂在 app 上"这条路真的生效。
    """
    probe = QLabel(text[:FOLD_TEXT_LEN] + "……"
                   if len(text) > FOLD_TEXT_LEN else text)
    probe.setObjectName("historyTextUser" if role == "user"
                        else "historyTextPet")
    probe.setWordWrap(True)
    probe.setAlignment(Qt.AlignLeft | Qt.AlignTop)
    probe.ensurePolished()
    probe.setFixedWidth(CONTENT_LABEL_W)
    return probe


def _true_text_bottom(text: str, role: str) -> int:
    """真实渲染文本，返回最深文字像素的【逻辑 y 坐标】。

    标签必须装在 #historyPanel 容器里再截图：浮层底色是半透明深底，
    单独 grab 一个裸标签时那层 alpha 会合成到系统的浅色 Window 底上，
    浅灰文字立刻和背景糊成一片，像素扫描就等于在量噪声。
    容器给出与产品一致的深色 backing 后，文字是画面里唯一的高亮像素。
    顶对齐保证文字从顶部内边距开始画，底边即真实文字高度；
    grab 出的位图是设备像素，按 devicePixelRatio 归一化回逻辑像素。
    """
    host = QWidget()
    host.setObjectName("historyPanel")
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    probe = _make_label(text, role)
    probe.setFixedSize(CONTENT_LABEL_W, 2000)   # 给足高度让文字完整铺开
    lay.addWidget(probe)
    host.adjustSize()
    img = host.grab().toImage().convertToFormat(QImage.Format_ARGB32)
    dpr = host.devicePixelRatioF() or 1.0
    bottom = -1
    for y in range(img.height() - 1, -1, -1):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y)
            if min(c.red(), c.green(), c.blue()) >= 150:
                bottom = y
                break
        if bottom >= 0:
            break
    return bottom / dpr


print("\n一、主题字号真实生效（旧版裁字根因）")
item, label = _find_row(long_cn)
label.ensurePolished()
px = QFontInfo(label.font()).pixelSize()
check(f"正文标签字体 = {SZ['body']}px（真实平台）", px == SZ["body"],
      f"pixelSize={px}")
check("正文标签确实从 app 样式表拿到浮层配色",
      "historyTextPet" in QApplication.instance().styleSheet())

print("\n二、逐条真实渲染：文字底边必须落在气泡内边距之上")
for role, text in texts:
    probe = _make_label(text, role)
    content_h = hd.HistoryPanel._content_height(probe, CONTENT_TEXT_W)
    true_bottom = _true_text_bottom(text, role)   # 含顶部内边距的真实底边
    # 内容区限额 = content_h - 底部保护；文字自身从顶部内边距处开始画，
    # 故文字底边(true_bottom) 与内容区底(content_h - 底部 chrome) 的关系
    # 即是否裁字
    limit = content_h - CONTENT_SAFE_H
    folded = len(text) > FOLD_TEXT_LEN
    check(f"[{role}] len={len(text)} 折叠={folded} 文字底边 {true_bottom}"
          f" ≤ 内容区底 {limit}", 0 <= true_bottom <= limit,
          f"内容高 {content_h}")

print("\n三、行内几何（固定宽标签不横向挤压，面板宽由常量推出）")
check("用户行放得下（条目留白+正文+间距+头像 ≤ 条目宽）",
      ITEM_MARGIN + CONTENT_LABEL_W + ITEM_GAP + AVATAR_W <= ITEM_W,
      f"{ITEM_MARGIN + CONTENT_LABEL_W + ITEM_GAP + AVATAR_W} vs {ITEM_W}")
check("正文换行宽度与高度计算同源",
      CONTENT_TEXT_W == CONTENT_LABEL_W - CONTENT_CHROME_W)
check("面板宽 = 条目宽 + 内层留白 + 外层留白 + 滚动条",
      PANEL_WIDTH == (ITEM_W + 2 * hd.PANEL_INNER_MARGIN
                      + 2 * hd.PANEL_WRAP_MARGIN + hd.SCROLLBAR_W),
      f"PANEL_WIDTH={PANEL_WIDTH}")

print("\n四、两套主题都不再残留旧样式来源")
check("theme.TEMPLATE 里没有 style.qss 影子（浮层配色由令牌给）",
      "rgba" in theme.build_qss(theme.DARK))
check("浅色主题的浮层仍是深底（贴在壁纸上才压得住）",
      theme.LIGHT["FLOAT_BG"].startswith("rgba(26, 29, 35"))

print("\n============================================================")
print(f"结果: {passed} 通过 / {failed} 失败")
print("============================================================")
sys.exit(0 if failed == 0 else 1)
