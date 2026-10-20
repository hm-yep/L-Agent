# -*- coding: utf-8 -*-
"""助手预览页面（前端组件调试台）

独立的角色查看/调试窗口，方便前端调试与展示：
- 舞台区：实时渲染角色，可切换背景（透明棋盘格 / 天空草地 / 深色）
- 七种动画状态一键切换，可自动轮播
- 朝向 / 尺寸 / 播放速度实时调节
- 情绪表情叠加（开心/爱心/生气/汗/困/惊叹/星星）
- 气泡、信息卡片、输入框三大组件的真实效果预览
"""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QSlider, QCheckBox, QLineEdit, QGroupBox, QButtonGroup,
)

from framework import config
from framework.utils import logger
from ui.sprite_manager import SpriteManager, POSES, EMOTIONS
from ui.sound_manager import SoundManager
from ui.ui_manager import BubbleWidget, InfoCardWidget
from ui.input_dialog import InputDialog

# 舞台背景方案
BACKGROUNDS = {
    "透明棋盘格": "checker",
    "天空草地": "sky",
    "深色夜幕": "dark",
}

POSE_LABELS = {
    "idle": "待机 idle",
    "walk": "走路 walk",
    "jump": "跳跃 jump",
    "attack": "挥剑 attack",
    "think": "思考 think",
    "searching": "寻找 searching",
    "talk": "说话 talk",
}

EMOTION_LABELS = {
    "happy": "开心 ♪", "love": "爱心 ♥", "angry": "生气 #",
    "sweat": "汗滴 💧", "sleep": "犯困 Z", "exclaim": "惊叹 !",
    "star": "星星 ★",
}


class StageArea(QWidget):
    """角色渲染舞台"""

    def __init__(self, sprite: SpriteManager, parent=None):
        super().__init__(parent)
        self.sprite = sprite
        self.bg = "checker"
        self.setMinimumSize(360, 360)

        # 帧定时
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._restart()
        self._fps_count = 0
        self._fps = 0
        self._fps_timer = QTimer(self)
        self._fps_timer.timeout.connect(self._calc_fps)
        self._fps_timer.start(1000)

    def _restart(self):
        self._timer.start(self.sprite.frame_interval_ms())

    def _tick(self):
        self.sprite.advance()
        self._fps_count += 1
        if self._timer.interval() != self.sprite.frame_interval_ms():
            self._restart()
        self.update()

    def _calc_fps(self):
        self._fps = self._fps_count
        self._fps_count = 0

    @property
    def fps(self):
        return self._fps

    def set_background(self, bg: str):
        self.bg = bg
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # ---- 背景 ----
        if self.bg == "checker":
            # 棋盘格：直观体现"透明区域"
            cell = 16
            for y in range(0, h, cell):
                for x in range(0, w, cell):
                    c = QColor(235, 235, 240) if (x // cell + y // cell) % 2 == 0 \
                        else QColor(205, 205, 212)
                    p.fillRect(x, y, cell, cell, c)
        elif self.bg == "sky":
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0, QColor(140, 205, 255))
            grad.setColorAt(1, QColor(225, 245, 255))
            p.fillRect(0, 0, w, h, grad)
            p.fillRect(0, int(h * 0.78), w, int(h * 0.22), QColor(150, 210, 130))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 200))
            for cx, cy, r in ((60, 60, 26), (95, 66, 32), (130, 62, 24)):
                p.drawEllipse(cx, cy, r, r)
        else:
            p.fillRect(0, 0, w, h, QColor(38, 40, 52))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 240, 220))
            for i, (cx, cy) in enumerate(((80, 60), (200, 90), (300, 50), (140, 120))):
                p.drawEllipse(cx, cy, 3 + i % 2, 3 + i % 2)

        # ---- 角色（居中，情绪表演会带动身体位移）----
        size = self.sprite.size
        dx, dy = self.sprite.performance_offset()
        x = (w - size) // 2 + int(dx * size / 128.0)
        y = int(h * 0.68) - size // 2 + int(dy * size / 128.0)
        p.drawPixmap(x, y, self.sprite.current_pixmap())

        # ---- 实时层（粒子 + 情绪）----
        p.save()
        p.translate(x, y)
        self.sprite.draw_overlay(p, size)
        p.restore()
        p.end()


class PreviewWindow(QWidget):
    """助手预览页面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("助手预览 · 组件调试台")
        self.resize(880, 560)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        # 预览用的独立动画实例（不影响桌面上的角色）
        self.sprite = SpriteManager(self)
        self.sprite.set_pose("idle")

        # 用于组件预览的实例（同样带音效）
        self.sound = SoundManager()
        self.bubble = BubbleWidget(sound=self.sound)
        self.card = InfoCardWidget(sound=self.sound)
        self.input_box = InputDialog()
        self.input_box.command_submitted.connect(self._on_preview_command)

        self._cycling = False
        self._cycle_index = 0
        self._cycle_timer = QTimer(self)
        self._cycle_timer.timeout.connect(self._cycle_next)

        self._init_ui()
        self._apply_style()
        logger.info("[Preview] 预览页面已打开")

    # ============================================================
    # 界面
    # ============================================================
    def _init_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # ---- 左侧：舞台 ----
        left = QVBoxLayout()
        self.stage = StageArea(self.sprite, self)
        left.addWidget(self.stage, 1)

        # 舞台底部：状态信息
        self.status = QLabel("状态: idle | 帧: 0 | FPS: 0 | 粒子: 0")
        self.status.setObjectName("previewStatus")
        left.addWidget(self.status)

        # 实时刷新状态栏
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(300)

        root.addLayout(left, 3)

        # ---- 右侧：控制面板 ----
        right = QVBoxLayout()
        right.setSpacing(10)

        # 动画状态
        gb_pose = QGroupBox("动画状态")
        grid = QGridLayout(gb_pose)
        self._pose_group = QButtonGroup(self)
        self._pose_group.setExclusive(True)
        for idx, pose in enumerate(POSES.keys()):
            btn = QPushButton(POSE_LABELS[pose])
            btn.setCheckable(True)
            btn.setObjectName("poseButton")
            btn.clicked.connect(lambda _, p=pose: self._set_pose(p))
            self._pose_group.addButton(btn)
            grid.addWidget(btn, idx // 2, idx % 2)
        self._pose_group.buttons()[0].setChecked(True)
        auto = QCheckBox("自动轮播七种状态")
        auto.stateChanged.connect(self._toggle_cycle)
        grid.addWidget(auto, len(POSES) // 2 + 1, 0, 1, 2)
        right.addWidget(gb_pose)

        # 朝向 / 速度
        gb_move = QGroupBox("朝向与速度")
        mv = QHBoxLayout(gb_move)
        left_btn = QPushButton("← 朝左")
        right_btn = QPushButton("朝右 →")
        left_btn.clicked.connect(lambda: self.sprite.set_direction(-1))
        right_btn.clicked.connect(lambda: self.sprite.set_direction(1))
        mv.addWidget(left_btn)
        mv.addWidget(right_btn)
        mv.addWidget(QLabel("速度"))
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(3, 25)
        self.speed_slider.setValue(10)
        self.speed_slider.valueChanged.connect(
            lambda v: self.sprite.set_speed(v / 10.0)
        )
        mv.addWidget(self.speed_slider)
        right.addWidget(gb_move)

        # 尺寸
        gb_size = QGroupBox("角色尺寸")
        sz = QHBoxLayout(gb_size)
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(64, 256)
        self.size_slider.setValue(config.PET_SIZE)
        self.size_label = QLabel(f"{config.PET_SIZE} px")
        self.size_slider.valueChanged.connect(self._change_size)
        sz.addWidget(self.size_slider)
        sz.addWidget(self.size_label)
        right.addWidget(gb_size)

        # 情绪表情
        gb_emo = QGroupBox("情绪表情")
        eg = QGridLayout(gb_emo)
        for idx, (key, label) in enumerate(EMOTION_LABELS.items()):
            btn = QPushButton(label)
            btn.setObjectName("emotionButton")
            btn.clicked.connect(lambda _, k=key: self.sprite.set_emotion(k))
            eg.addWidget(btn, idx // 4, idx % 4)
        right.addWidget(gb_emo)

        # 组件预览
        gb_comp = QGroupBox("组件预览")
        cv = QVBoxLayout(gb_comp)
        row_b = QHBoxLayout()
        self.bubble_edit = QLineEdit()
        self.bubble_edit.setPlaceholderText("气泡文字...")
        self.bubble_edit.setText("找到啦！要我帮你打开吗？")
        show_b = QPushButton("显示气泡")
        show_b.clicked.connect(self._preview_bubble)
        row_b.addWidget(self.bubble_edit)
        row_b.addWidget(show_b)
        cv.addLayout(row_b)

        row_c = QHBoxLayout()
        show_c = QPushButton("显示信息卡片")
        show_c.clicked.connect(self._preview_card)
        show_i = QPushButton("显示输入框")
        show_i.clicked.connect(self._preview_input)
        hide_all = QPushButton("隐藏全部")
        hide_all.clicked.connect(self._hide_all)
        row_c.addWidget(show_c)
        row_c.addWidget(show_i)
        row_c.addWidget(hide_all)
        cv.addLayout(row_c)
        right.addWidget(gb_comp)

        # 背景
        gb_bg = QGroupBox("舞台背景")
        bg = QHBoxLayout(gb_bg)
        for label in BACKGROUNDS:
            btn = QPushButton(label)
            btn.setObjectName("bgButton")
            btn.clicked.connect(lambda _, l=label: self.stage.set_background(BACKGROUNDS[l]))
            bg.addWidget(btn)
        right.addWidget(gb_bg)

        right.addStretch(1)
        root.addLayout(right, 2)

    def _apply_style(self):
        """预览页面样式（独立于 style.qss，避免影响主程序）"""
        self.setStyleSheet("""
            QWidget { font-family: "Microsoft YaHei"; color: #4a3b2a; }
            QGroupBox {
                border: 1px solid #e8cfa5; border-radius: 10px;
                margin-top: 12px; padding: 10px 8px 8px 8px;
                background-color: #fffaf2;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 12px; padding: 0 6px;
                color: #e07b39; font-weight: bold;
            }
            QPushButton {
                background-color: #ffe8c8; border: 1px solid #f0c987;
                border-radius: 8px; padding: 6px 10px;
            }
            QPushButton:hover { background-color: #ffd9a0; }
            QPushButton:checked { background-color: #ffb25c; color: white; font-weight: bold; }
            #previewStatus {
                background-color: #fffaf2; border: 1px solid #e8cfa5;
                border-radius: 8px; padding: 6px 10px; color: #6b5d4d;
            }
            QLineEdit {
                border: 1px solid #e0c9a0; border-radius: 8px;
                padding: 6px 10px; background-color: white;
            }
            QSlider::groove:horizontal {
                height: 4px; background: #f0dcc0; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 14px; margin: -5px 0; border-radius: 7px;
                background: #ffb25c;
            }
        """)

    # ============================================================
    # 交互
    # ============================================================
    def _set_pose(self, pose: str):
        self.sprite.set_pose(pose)
        self.stage._restart()

    def _toggle_cycle(self, state: int):
        """自动轮播七种状态"""
        self._cycling = bool(state)
        if self._cycling:
            self._cycle_timer.start(2200)
        else:
            self._cycle_timer.stop()

    def _cycle_next(self):
        keys = list(POSES.keys())
        self._cycle_index = (self._cycle_index + 1) % len(keys)
        pose = keys[self._cycle_index]
        self._set_pose(pose)
        # 同步按钮选中态
        for idx, key in enumerate(keys):
            if key == pose:
                self._pose_group.buttons()[idx].setChecked(True)

    def _change_size(self, value: int):
        self.sprite.set_size(value)
        self.size_label.setText(f"{value} px")
        self.stage._restart()

    # ------------------------------------------------------------
    # 组件预览
    # ------------------------------------------------------------
    def _stage_center(self):
        """舞台中心在屏幕上的坐标"""
        return self.stage.mapToGlobal(self.stage.rect().center())

    def _preview_bubble(self):
        text = self.bubble_edit.text().strip() or "（空）"
        c = self._stage_center()
        self.bubble.show_text(text, c.x() - 60, c.y() - 120)

    def _preview_card(self):
        c = self._stage_center()
        self.card.show_card(
            "微信 WeChat",
            r"C:\Program Files\Tencent\WeChat\WeChat.exe",
            "128.4 MB",
            near_x=c.x(), near_y=c.y() - 40,
        )

    def _preview_input(self):
        c = self._stage_center()
        self.input_box.popup_at(c.x() - 40, c.y() - 100)

    def _on_preview_command(self, text: str):
        """预览输入框提交：直接以气泡形式回显"""
        c = self._stage_center()
        self.bubble.show_text(f"收到指令：{text}", c.x() - 60, c.y() - 120)

    def _hide_all(self):
        self.bubble.hide()
        self.card.close()
        self.input_box.hide()

    # ------------------------------------------------------------
    def _refresh_status(self):
        self.status.setText(
            f"状态: {self.sprite.pose} | 帧: {self.sprite.frame_index} "
            f"| FPS: {self.stage.fps} | 粒子: {len(self.sprite._particles)}"
        )

    def closeEvent(self, event):
        self._hide_all()
        self._cycle_timer.stop()
        super().closeEvent(event)
