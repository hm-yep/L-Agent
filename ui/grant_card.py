# -*- coding: utf-8 -*-
"""授权浮层卡：读本机文件前要用户点的那一下头。

三个按钮对应三种**后果不同**的选择，所以每个按钮下面都写清楚它意味着什么：

  只读这一个          → 只放行这一个文件；同目录的另一个文件下次还会再问
  允许整个文件夹      → 本次会话里这个文件夹（含子目录）都能读
  拒绝                → 这次不读，我继续做别的

两条硬约定：
  1. 授权只在**本次会话**有效，重启即失效——卡片上明写；
  2. 超时（默认 120 秒）按**拒绝**处理，卡片上有倒计时，超时后 Framework
     会如实说「等太久，按拒绝处理了」，不装作用户点的拒绝。

结构与 `ui/plan_card.py` 同款：顶层窗口只做透明圆角承载，底色画在内层
普通 QWidget 面板上（样式表画在顶层 QWidget 上不可靠，会整卡透明只剩字）。
"""

import html

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from framework.grant_service import (DECISION_DENY, DECISION_FILE,
                                    DECISION_TREE)
from . import theme
from .icons import icon_label

_WIDTH = theme.DIM["grant_card_w"]

# 三个按钮各自的说明（文案与 ADR 0006 的两档授权一致，改这里即可）
_BUTTON_ROWS = (
    (DECISION_FILE, "只读这一个",
     "只放行这一个文件；同一个文件夹里的别的文件，下次还会再问一次"),
    (DECISION_TREE, "允许整个文件夹（本次会话）",
     "本次会话里这个文件夹和它下面的子目录都能读，不用再问"),
    (DECISION_DENY, "拒绝",
     "这次不读，我接着做别的事"),
)


class GrantCard(QWidget):
    """授权确认浮层卡（无边框置顶小窗）。"""

    decided = pyqtSignal(str, str)      # 用户点了某个按钮 (gid, decision)

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._panel = QWidget(self)
        self._panel.setObjectName("grantCard")

        self._side = 1
        self._gid = ""
        self._resolved = False
        self._cascade = 0
        self._deadline = 0.0

        sp = theme.SP
        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(sp["xl"], sp["l"], sp["xl"], sp["l"])
        layout.setSpacing(sp["s"])

        # 图标与标题分行布局：emoji 不用，统一走 ui/icons.py 的线框矢量图
        self._title_icon = icon_label(
            "unlock", theme.T("FLOAT_TEXT"), 17, "grantIcon")
        self._title = QLabel("要授权读一个文件")
        self._title.setObjectName("grantTitle")
        title_row = QHBoxLayout()
        title_row.setSpacing(theme.SP["s"])
        title_row.addWidget(self._title_icon)
        title_row.addWidget(self._title, 1)
        layout.addLayout(title_row)

        self._path = QLabel("")
        self._path.setObjectName("grantPath")
        self._path.setTextFormat(Qt.RichText)
        self._path.setWordWrap(True)
        layout.addWidget(self._path)

        self._reason = QLabel("")
        self._reason.setObjectName("grantHint")
        self._reason.setWordWrap(True)
        layout.addWidget(self._reason)

        layout.addSpacing(sp["xs"])

        # 三个按钮竖排：每个按钮下面一行说明，窄窗口下文字也不会被截断
        self._buttons = {}
        for decision, label, desc in _BUTTON_ROWS:
            btn = QPushButton(label)
            btn.setObjectName("grantFile" if decision == DECISION_FILE
                              else ("grantTree" if decision == DECISION_TREE
                                    else "grantDeny"))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(theme.DIM["btn_h"])
            btn.clicked.connect(
                lambda _checked=False, d=decision: self._decide(d))
            layout.addWidget(btn)
            self._buttons[decision] = btn

            note = QLabel(desc)
            note.setObjectName("grantNote")
            note.setWordWrap(True)
            layout.addWidget(note)
            layout.addSpacing(sp["xs"])

        self._foot = QLabel("")
        self._foot.setObjectName("grantNote")
        self._foot.setWordWrap(True)
        layout.addWidget(self._foot)

        self._countdown_icon = icon_label(
            "timer", theme.T("FLOAT_TEXT"), 13, "grantIcon")
        self._countdown = QLabel("")
        self._countdown.setObjectName("grantCountdown")
        count_row = QHBoxLayout()
        count_row.setSpacing(theme.SP["xs"])
        count_row.addWidget(self._countdown_icon)
        count_row.addWidget(self._countdown, 1)
        layout.addLayout(count_row)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._panel)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh_countdown)

    # ------------------------------------------------------------
    # 对外接口（Framework 调用）
    # ------------------------------------------------------------
    def popup(self, info: dict, pet_x: int, pet_y: int, cascade: int = 0):
        """亮卡：显示要授权的路径与三个选项，贴到助手侧面。

        :param cascade: 第几张（0 起）。同时挂着多条请求时按卡宽横向错开，
                        否则后弹的卡叠在前一张上，前面那条就再也点不到了。
        """
        self._gid = str(info.get("gid") or "")
        self._resolved = False
        self._cascade = max(0, int(cascade))
        path = str(info.get("path") or "")
        name = str(info.get("name") or "")
        parent = str(info.get("dir") or "")
        reason = str(info.get("reason") or "这个路径不在我平常能读的目录里")

        self._title.setText("要授权读一个文件")
        shown = html.escape(name or path)
        if parent:
            shown += "<br/>" + html.escape(parent)
        self._path.setText(shown)
        self._reason.setText(html.escape(reason) + "。")
        self._foot.setText(
            "授权只在本次会话有效，重启后失效；"
            "密钥、凭据这类文件点哪个都不读。")

        self._deadline = 0.0
        try:
            import time
            self._deadline = time.time() + float(info.get("expires_in") or 120)
        except (TypeError, ValueError):
            self._deadline = 0.0

        screen = QApplication.primaryScreen().availableGeometry()
        self.setFixedWidth(min(_WIDTH, screen.width() - 48))
        self.show()
        self.adjustSize()
        need = pet_x + 160 + 10 + self.width()
        self._side = 1 if need <= screen.right() else -1
        self.follow(pet_x, pet_y)
        self._timer.start()
        self._refresh_countdown()

    def mark_resolved(self):
        """外部（网页端）已经答了：标成已决，别再当成用户点的拒绝。"""
        self._resolved = True
        self._timer.stop()

    def follow(self, pet_x: int, pet_y: int):
        """跟随助手（保持弹出时选定的左右侧，窄屏兜底收进屏幕内）。"""
        if not self.isVisible():
            return
        screen = QApplication.primaryScreen().availableGeometry()
        if self._side > 0:
            x = pet_x + 160 + 10
            if x + self.width() > screen.right():
                x = pet_x - self.width() - 10
        else:
            x = pet_x - self.width() - 10
            if x < screen.left():
                x = pet_x + 160 + 10
        # 多条请求同时待决：横向错开一整张卡的宽度；右边放不下就换到助手左侧
        # 并逐张往下错开，让每张卡的三个按钮都露得出来（全叠成一个点等于只有一张能用）
        x += self._cascade * (self.width() + 12)
        drop = 0
        if x > screen.right() - self.width() - 4:
            x = pet_x - self.width() - 10
            drop = self._cascade * 46
        x = max(screen.left() + 4, min(x, screen.right() - self.width() - 4))
        y = pet_y - 24 + drop
        y = max(screen.top() + 8,
                min(y, screen.bottom() - self.height() - 8))
        self.move(x, y)

    # ------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------
    def _decide(self, decision: str):
        if self._resolved:
            return
        self._resolved = True
        self._timer.stop()
        self.decided.emit(self._gid, decision)
        self.close()

    def _refresh_countdown(self):
        """倒计时：告诉用户「不点会按拒绝处理」，别让人以为可以无限等。"""
        if not self._deadline:
            self._countdown.setText("")
            return
        import time
        left = int(round(self._deadline - time.time()))
        if left <= 0:
            self._countdown.setText("已超时，按拒绝处理")
            self._timer.stop()
            return
        self._countdown.setText(f"还剩 {left} 秒不点就按拒绝处理")

    def closeEvent(self, event):  # noqa: D102
        # 直接关掉卡片等于「这次不读」；已答过的不再重复表态
        if not self._resolved and self._gid:
            self._resolved = True
            self.decided.emit(self._gid, DECISION_DENY)
        self._timer.stop()
        super().closeEvent(event)
