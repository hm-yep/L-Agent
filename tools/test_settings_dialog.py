# -*- coding: utf-8 -*-
"""设置窗口修复验证（真实 DPI + 真实屏幕，覆盖：尺寸/铺满/夹紧/滚动/拖动/圆角）

用法: python tools/test_settings_dialog.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))   # 项目根目录

from PyQt5.QtCore import Qt, QEvent, QRect, QPointF
from PyQt5.QtGui import QColor, QMouseEvent, QPainter, QPixmap
from PyQt5.QtWidgets import (QApplication, QWidget, QPushButton, QLabel,
                             QComboBox, QCheckBox, QLineEdit, QAbstractButton)

from ui.ui_manager import SettingsDialog

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


app = QApplication(sys.argv)
screen = app.primaryScreen().availableGeometry()
print(f"屏幕可用区域: {screen.getRect()}  DPI: "
      f"{app.primaryScreen().logicalDotsPerInch()}\n")

dlg = SettingsDialog()
panel = dlg._panel

# ============================================================
print("一、面板必须铺满整个窗口（原来面板 301x605 漂在 360x620 的窗口里）")
# ============================================================
dlg.show()
app.processEvents()
check("面板尺寸 == 窗口尺寸", panel.size() == dlg.size(),
      f"panel={panel.width()}x{panel.height()} dialog={dlg.width()}x{dlg.height()}")
check("面板无偏移", panel.mapTo(dlg, panel.rect().topLeft()) == dlg.rect().topLeft())
check("窗口按内容定尺寸（高度贴合内容）",
      dlg.height() == panel.sizeHint().height(),
      f"窗口高 {dlg.height()} / 内容需要 {panel.sizeHint().height()}")

# ============================================================
print("\n二、面板内所有控件都在窗口可见范围内、没有被压窄")
# ============================================================
overflow, squeezed = [], []
for w in panel.findChildren(QWidget):
    if isinstance(w, (QLineEdit, QComboBox)) or w.layout() is not None:
        continue
    if not isinstance(w, (QLabel, QAbstractButton, QCheckBox)):
        continue
    if not w.isVisible():
        continue
    tl = w.mapTo(dlg, w.rect().topLeft())
    r, b = tl.x() + w.width(), tl.y() + w.height()
    if tl.x() < 0 or tl.y() < 0 or r > dlg.width() or b > dlg.height():
        overflow.append(f"{w.__class__.__name__}({tl.x()},{tl.y()},{r},{b})")
    if w.width() < w.minimumSizeHint().width():
        squeezed.append(f"{w.__class__.__name__} {w.width()}<"
                        f"{w.minimumSizeHint().width()}")
check("无控件越出窗口边界", not overflow, "；".join(overflow))
check("无控件被压窄", not squeezed, "；".join(squeezed))

# ============================================================
print("\n三、从右下角助手处弹出：窗口必须完整落在屏幕内")
# ============================================================
pet = QRect(screen.right() - 60 - 185, screen.bottom() - 60 - 185, 185, 185)
dlg.show_near(pet)
app.processEvents()
g = dlg.geometry()
print(f"     助手 {pet.getRect()} -> 设置窗口 {g.getRect()}")
over_r = max(0, g.right() - screen.right())
over_b = max(0, g.bottom() - screen.bottom())
check("右侧不越界", over_r == 0, f"越界 {over_r}px")
check("底部不越界", over_b == 0, f"越界 {over_b}px")
check("左侧不越界", g.left() >= screen.left())
check("顶部不越界", g.top() >= screen.top())

# 极端锚点：屏幕四角 + 屏幕外
worst = []
for name, anchor in (
    ("左上", QRect(screen.left(), screen.top(), 185, 185)),
    ("右上", QRect(screen.right() - 185, screen.top(), 185, 185)),
    ("左下", QRect(screen.left(), screen.bottom() - 185, 185, 185)),
    ("右下", QRect(screen.right() - 185, screen.bottom() - 185, 185, 185)),
    ("屏幕外", QRect(screen.right() + 500, screen.bottom() + 500, 185, 185)),
):
    dlg.show_near(anchor)
    app.processEvents()
    g = dlg.geometry()
    if (g.left() < screen.left() or g.top() < screen.top()
            or g.right() > screen.right() or g.bottom() > screen.bottom()):
        worst.append(f"{name}{g.getRect()}")
check("五个极端锚点都完整可见", not worst, "；".join(worst))

# ============================================================
print("\n四、渲染：圆角外是透明的，只有面板本体是不透明的")
# ============================================================
dlg.show_near(pet)
app.processEvents()
pm = QPixmap(dlg.size())
pm.fill(Qt.transparent)
p = QPainter(pm)
dlg.render(p)
p.end()
img = pm.toImage()
corner_a = img.pixelColor(1, 1).alpha()
body_a = img.pixelColor(dlg.width() // 2, dlg.height() // 2).alpha()
check("窗口四角完全透明（面板圆角外没有多余的底）", corner_a == 0,
      f"左上角 alpha={corner_a}")
check("面板本体不透明", body_a == 255, f"中心 alpha={body_a}")
# 面板自身的底色采样：取内容边距之外的纯底像素（曾因滚动区透明规则
# 连带命中面板，把 #fffaf2 底色清没了，整页变透明）
px = img.pixelColor(10, dlg.height() // 2)
check("面板奶油底色还在（#fffaf2）",
      (px.red(), px.green(), px.blue(), px.alpha()) == (255, 250, 242, 255),
      f"实际 rgba={px.red()},{px.green()},{px.blue()},{px.alpha()}")
# 再垫一层底色出图，肉眼看圆角与留白
backdrop = QColor(120, 120, 130)
pm2 = QPixmap(dlg.size())
pm2.fill(backdrop)
p = QPainter(pm2)
dlg.render(p)
p.end()
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_after.png")
pm2.save(out)
print(f"     渲染图已存 {out}")

# ============================================================
print("\n五、矮屏幕（1280x600）：高度夹紧 + 可滚动到底部的按钮")
# ============================================================
fake = QRect(0, 0, 1280, 600)
real_helper = dlg._available_screen
dlg._available_screen = lambda: fake
dlg._fit_to_content()
app.processEvents()
check("高度被夹到屏幕的 88%", dlg.height() <= int(600 * 0.88) + 1,
      f"窗口高 {dlg.height()}")
bar = dlg._scroll.verticalScrollBar()
app.processEvents()
check("出现竖直滚动条", bar.isVisible() and bar.maximum() > 0,
      f"visible={bar.isVisible()} max={bar.maximum()}")
save_btn = [b for b in panel.findChildren(QPushButton) if b.text() == "保存"]
if save_btn:
    btn = save_btn[0]
    bar.setValue(bar.maximum())
    app.processEvents()
    tl = btn.mapTo(dlg, btn.rect().topLeft())
    check("滚到底后「保存」按钮可见",
          tl.y() >= 0 and tl.y() + btn.height() <= dlg.height(),
          f"按钮 y={tl.y()}..{tl.y() + btn.height()} 窗口高 {dlg.height()}")
else:
    check("找到「保存」按钮", False)
dlg._available_screen = real_helper

# ============================================================
print("\n六、面板空白处可拖动整窗（原来只有窗口透明边能拖）")
# ============================================================
dlg._available_screen = lambda: screen
dlg._fit_to_content()
dlg.move(screen.left() + 400, screen.top() + 300)
app.processEvents()
start = dlg.pos()
local = QPointF(panel.width() / 2, 8)          # 面板顶部空白处
glob = QPointF(dlg.x() + local.x(), dlg.y() + local.y())
app.sendEvent(panel, QMouseEvent(QEvent.MouseButtonPress, local, glob,
                                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
moved = QPointF(glob.x() - 120, glob.y() - 90)
app.sendEvent(panel, QMouseEvent(QEvent.MouseMove, local, moved,
                                Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
app.processEvents()
delta = dlg.pos() - start
check("按住面板可拖动窗口", (delta.x(), delta.y()) == (-120, -90),
      f"位移 {delta.x()},{delta.y()}")
app.sendEvent(panel, QMouseEvent(QEvent.MouseButtonRelease, local, moved,
                                Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
app.processEvents()

# 拖到屏幕外应贴边停住
dlg.move(screen.left() + 400, screen.top() + 300)
app.processEvents()
local = QPointF(panel.width() / 2, 8)
glob = QPointF(dlg.x() + local.x(), dlg.y() + local.y())
app.sendEvent(panel, QMouseEvent(QEvent.MouseButtonPress, local, glob,
                                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
far = QPointF(glob.x() + 5000, glob.y() + 5000)
app.sendEvent(panel, QMouseEvent(QEvent.MouseMove, local, far,
                                Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
app.processEvents()
g = dlg.geometry()
check("拖出屏幕会贴边停住（窗口不会丢）",
      g.right() <= screen.right() and g.bottom() <= screen.bottom(),
      g.getRect())
app.sendEvent(panel, QMouseEvent(QEvent.MouseButtonRelease, local, far,
                                Qt.LeftButton, Qt.NoButton, Qt.NoModifier))

# ============================================================
print("\n七、状态文案变长时窗口跟着长高")
# ============================================================
dlg.move(screen.left() + 200, screen.top() + 100)
dlg.show()
app.processEvents()
h0 = dlg.height()
dlg.set_ai_status("✗ 连接失败：请求超时，请检查网络与接口地址是否正确（这是一段刻意写长的状态文案，"
                  "用来确认设置窗口会自动长高而不是把底部按钮顶掉）")
app.processEvents()
h1 = dlg.height()
check("窗口随状态文案变长而长高", h1 >= h0, f"{h0} -> {h1}")
over = [w for w in panel.findChildren(QWidget)
        if isinstance(w, (QLabel, QAbstractButton, QCheckBox)) and w.isVisible()
        and w.mapTo(dlg, w.rect().topLeft()).y() + w.height() > dlg.height()]
check("长文案下仍无控件被顶出窗口", not over,
      "；".join(w.__class__.__name__ for w in over))

dlg.close()
print()
if FAILS:
    print(f"共 {len(FAILS)} 项失败: {FAILS}")
    sys.exit(1)
print("ALL TESTS PASS")
