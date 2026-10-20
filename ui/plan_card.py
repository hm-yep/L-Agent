# -*- coding: utf-8 -*-
"""常驻任务计划卡：贴在助手左右两侧的深色圆角卡片

三个状态：
- 待确认：标题 + 步骤列表 + 「▶ 开始执行」「✕ 取消」按钮
- 执行中：每完成一步打一个勾（✔ 绿色 + 删除线），当前步 ▶ 高亮，可「放弃」
- 已完成：整卡打勾，停留几秒后自动关闭

卡片随助手移动跟随（pet_window._sync_followers 调 follow），截图时会
被一起藏起来（pet_window._on_screenshot_hide）。

结构说明：外层窗口只做透明圆角承载，真正的底色画在内层普通 QWidget
面板上（与 BubbleWidget 同款结构）——样式表背景画在自定义 QWidget
子类/顶层上不可靠，会整卡透明只剩文字。
"""

import html

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from framework import config

_WIDTH = 1080         # 卡片宽度：约历史记录面板（560）的两倍
_AUTO_CLOSE_MS = 8000  # 完成后停留时长

_STYLE = """
#planPanel {
    background: rgba(24, 28, 42, 242);
    border: 1px solid rgba(110, 180, 255, 110);
    border-radius: 18px;
}
#planPanel QLabel {
    color: #e8f2ff; font-size: 22px; background: transparent;
    border: none; font-family: "Microsoft YaHei";
}
#planPanel QLabel#planTitle {
    font-size: 26px; font-weight: bold; color: #cfe6ff;
}
#planPanel QPushButton {
    color: #eaf4ff; background: rgba(80, 140, 220, 200);
    border: none; border-radius: 10px; padding: 10px 24px;
    font-size: 18px; font-family: "Microsoft YaHei";
}
#planPanel QPushButton:hover { background: rgba(100, 160, 240, 230); }
#planPanel QPushButton#planQuit {
    color: #9fb6c9; background: transparent; padding: 4px 12px;
    font-size: 16px;
}
#planPanel QPushButton#planQuit:hover { color: #ff9d9d; }
"""


class PlanCard(QWidget):
    """任务计划常驻卡（无边框置顶小窗）"""

    confirm_requested = pyqtSignal()      # 用户点了「开始执行」
    cancel_requested = pyqtSignal()       # 用户点了「取消/放弃」

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # 内层面板：真正承载深色圆角底（顶层窗口只做透明承载）
        self._panel = QWidget(self)
        self._panel.setObjectName("planPanel")
        self.setStyleSheet(_STYLE)

        self._side = 1                     # 1 = 助手右侧, -1 = 左侧
        self._steps = []
        self._done = 0
        self._status = "pending"

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(12)

        self._title = QLabel("📋 任务计划（待确认）")
        self._title.setObjectName("planTitle")
        self._title.setTextFormat(Qt.RichText)
        layout.addWidget(self._title)

        self._steps_label = QLabel()
        self._steps_label.setTextFormat(Qt.RichText)
        self._steps_label.setWordWrap(True)
        layout.addWidget(self._steps_label)

        self._btn_start = QPushButton("▶ 开始执行")
        self._btn_cancel = QPushButton("✕ 取消")
        self._btn_start.clicked.connect(self.confirm_requested.emit)
        self._btn_cancel.clicked.connect(self.cancel_requested.emit)
        self._btn_row = QHBoxLayout()
        self._btn_row.setSpacing(14)
        self._btn_row.addWidget(self._btn_start)
        self._btn_row.addWidget(self._btn_cancel)
        self._btn_row.addStretch()
        layout.addLayout(self._btn_row)

        self._btn_quit = QPushButton("✕ 放弃计划")
        self._btn_quit.setObjectName("planQuit")
        self._btn_quit.setCursor(Qt.PointingHandCursor)
        self._btn_quit.clicked.connect(self.cancel_requested.emit)
        self._btn_quit.hide()
        row2 = QHBoxLayout()
        row2.addStretch()
        row2.addWidget(self._btn_quit)
        layout.addLayout(row2)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._panel)

    # ------------------------------------------------------------
    # 对外接口（Framework 调用）
    # ------------------------------------------------------------
    def popup(self, steps, pet_x: int, pet_y: int):
        """亮卡：按步骤列表渲染待确认态，并贴到助手侧面"""
        self._steps = [str(s) for s in steps]
        self._done = 0
        self._status = "pending"
        self._title.setText("📋 任务计划（待确认）")
        self._btn_start.show()
        self._btn_cancel.show()
        self._btn_quit.hide()
        self._refresh_steps()
        screen = QApplication.primaryScreen().availableGeometry()
        self.setFixedWidth(min(_WIDTH, screen.width() - 48))
        self.show()
        self.adjustSize()
        need = pet_x + config.PET_SIZE + 10 + self.width()
        self._side = 1 if need <= screen.right() else -1
        self.follow(pet_x, pet_y)

    def update_progress(self, done: int):
        """执行中：已完成 done 步，逐格打勾"""
        if self._status != "done":
            self._done = max(self._done, int(done))
            self._status = "running"
            self._title.setText(f"📋 任务计划（{self._done}"
                                f"/{len(self._steps)}）")
            self._refresh_steps()

    def update_steps(self, steps):
        """动态重规划：换上调整后的剩余步骤，保持执行中状态"""
        self._steps = [str(s) for s in steps]
        self._done = 0
        if self._status != "done":
            self._status = "running"
            self._title.setText(f"📋 任务计划（重规划，剩 {len(self._steps)} 步）")
            self._refresh_steps()
        self.adjustSize()

    def mark_done(self):
        """全部完成：整卡打勾，停留几秒自动关闭"""
        self._done = len(self._steps)
        self._status = "done"
        self._title.setText("✅ 计划完成")
        self._btn_start.hide()
        self._btn_cancel.hide()
        self._btn_quit.hide()
        self._refresh_steps()
        QTimer.singleShot(_AUTO_CLOSE_MS, self._auto_close)

    def follow(self, pet_x: int, pet_y: int):
        """跟随助手（保持弹出时选定的左右侧，窄屏兜底收进屏幕内）"""
        if not self.isVisible():
            return
        screen = QApplication.primaryScreen().availableGeometry()
        if self._side > 0:
            x = pet_x + config.PET_SIZE + 10
            if x + self.width() > screen.right():
                x = pet_x - self.width() - 10
        else:
            x = pet_x - self.width() - 10
            if x < screen.left():
                x = pet_x + config.PET_SIZE + 10
        x = max(screen.left() + 4, min(x, screen.right() - self.width() - 4))
        y = pet_y - 24
        y = max(screen.top() + 8,
                min(y, screen.bottom() - self.height() - 8))
        self.move(x, y)

    # ------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------
    def _auto_close(self):
        if self._status == "done":
            self.close()

    def _refresh_steps(self):
        parts = []
        for i, step in enumerate(self._steps):
            text = html.escape(step)
            if i < self._done or self._status == "done":
                parts.append(
                    f'<div style="margin:8px 0;">'
                    f'<span style="color:#6ee7a0; font-weight:bold;">✔</span> '
                    f'<span style="color:#8fa3b8; '
                    f'text-decoration:line-through;">{text}</span></div>')
            elif self._status == "running" and i == self._done:
                parts.append(
                    f'<div style="margin:8px 0;">'
                    f'<span style="color:#ffd166;">▶</span> '
                    f'<b style="color:#ffffff;">{text}</b></div>')
            else:
                parts.append(
                    f'<div style="margin:8px 0;">'
                    f'<span style="color:#7f93a8;">○</span> {text}</div>')
        self._steps_label.setText("".join(parts))
