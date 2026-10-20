# -*- coding: utf-8 -*-
"""动画管理（增强版）

七种动画状态：idle(待机) / walk(走路) / jump(跳跃) / attack(挥剑) / think(思考) /
searching(寻找) / talk(说话)。

动画细节（全部程序化绘制，不依赖任何外部素材）：
- 尾巴：贝塞尔曲线摆动，走路/跳跃时摆幅加大
- 耳朵：待机时偶尔抖动，思考时下垂
- 身体：呼吸缩放 + 挤压拉伸（squash & stretch）
- 眼睛：眨眼、瞳孔看向移动方向、思考时斜视
- 待机：循环播放"呼吸 -> 伸懒腰 -> 打哈欠"随机小动作
- 粒子特效：跳跃落地尘土、走路脚步尘土、说话音符、开心爱心、思考汗滴
- 情绪表情：开心/爱心/生气/汗/困/惊叹/星星，叠加在头顶

若 assets/characters/<姿态>/*.png 存在，优先按 PNG 序列帧播放
（要求素材朝右、脚底对齐画布约 90.6% 高度线，512x512 主尺寸）；
其次若 assets/ 下存在对应 GIF（idle.gif 等），自动用 QMovie 播放；
都没有则回退到内置程序化绘制（橘猫团子）。

UI 层用法:
    sm = SpriteManager()
    sm.set_pose("walk")           切换状态
    sm.advance()                  推进一帧（窗口定时器驱动）
    pm = sm.current_pixmap()      取当前帧（已按朝向翻转）
    sm.draw_overlay(p, size)      绘制粒子与情绪（实时层，紧跟在帧之后）
    sm.set_direction(-1)          设置朝向
    sm.set_emotion("happy")       叠加情绪表情（自动消失）
"""

import glob
import math
import os
import random

from PyQt5.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer
from PyQt5.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap, QPolygonF, QRadialGradient, QTransform,
)

from framework import config
from framework.utils import logger, resource_path

# 动画状态定义：状态名 -> (帧率fps, 帧数)（帧数仅程序化回退用，序列帧动态读取）
POSES = {
    "idle":      (8, 16),    # 待机：呼吸 + 伸懒腰 + 打哈欠 + 眨眼
    "walk":      (12, 10),   # 走路：腿部交替 + 身体摇摆 + 尾巴摆动
    "jump":      (14, 10),   # 跳跃：下蹲 -> 腾空 -> 落地 + 尘土
    "attack":    (12, 8),    # 挥剑：蓄力 -> 挥砍 -> 收剑（打开应用成功庆祝）
    "think":     (6, 12),    # 思考：歪头 + 下垂耳朵 + 问号
    "searching": (10, 12),   # 寻找：四处张望 + 尾巴警觉竖起
    "talk":      (10, 8),    # 说话：嘴巴张合 + 音符飘出
}

# 情绪表情：(文字/符号, 颜色, 显示时长ms)
EMOTIONS = {
    "happy":   ("♪", QColor(255, 150, 80), 1600),
    "love":    ("♥", QColor(255, 90, 130), 1600),
    "angry":   ("#", QColor(230, 80, 60), 1600),
    "sweat":   ("💧", QColor(90, 160, 255), 1600),
    "sleep":   ("Z", QColor(120, 130, 180), 2200),
    "exclaim": ("!", QColor(255, 190, 60), 1400),
    "star":    ("★", QColor(255, 210, 70), 1600),
}

# 情绪表演：符号之外的身体动作。以当前真实帧为底做整体位移，
# 每项 [(dx, dy), ...] 为一个表演循环（128px 基准像素，负 dy = 向上），
# 由 advance() 按当前姿态帧率推进，set_emotion 时从头播放。
PERFORMANCES = {
    "happy":   [(0, 0), (-8, -5), (-10, -7), (-8, -5), (0, 0),
                (8, -5), (10, -7), (8, -5)],                       # 左右蹦跳
    "angry":   [(0, 0), (-3, 0), (3, 1), (-3, 0), (3, 1),
                (-3, 0), (3, 0), (0, 0)],                          # 气得发抖
    "love":    [(0, 0), (0, -5), (0, -8), (0, -5), (0, 0),
                (0, -5), (0, -8), (0, -5)],                        # 原地雀跃
    "sweat":   [(0, 0), (4, 1), (6, 1), (4, 1), (0, 0),
                (-4, 1), (-6, 1), (-4, 1)],                        # 尴尬地挪来挪去
    "sleep":   [(0, 0), (2, 1), (4, 2), (4, 2), (2, 1),
                (0, 0), (-2, 1), (-4, 2)],                         # 打瞌睡轻轻晃
    "exclaim": [(0, -9), (0, -9), (0, -4), (0, 0), (0, 0),
                (0, -3), (0, 0), (0, 0)],                          # 吓一跳后回神
    "star":    [(0, -4), (0, -8), (0, -4), (0, 0),
                (0, -4), (0, -8), (0, -4), (0, 0)],                # 兴奋连跳
}


class SpriteManager(QObject):
    """动画管理器：程序化绘制 + 粒子特效 + 情绪表情 + 可选GIF"""

    # ---- 配色（一只圆滚滚的橘猫团子）----
    BODY_COLOR = QColor(255, 178, 92)
    BODY_DARK = QColor(232, 150, 60)
    BELLY_COLOR = QColor(255, 238, 210)
    EAR_INNER = QColor(255, 170, 170)
    EYE_COLOR = QColor(62, 46, 36)
    NOSE_COLOR = QColor(240, 130, 120)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pose = "idle"
        self._frame_index = 0
        self._direction = 1               # 1朝右 -1朝左
        self._size = config.PET_SIZE      # 当前渲染尺寸
        self._speed = 1.0                 # 播放速度倍率

        self._cache = {}                  # (pose,size) -> [QPixmap,...]
        self._seq = {}                    # pose -> [QPixmap,...] PNG序列帧原图（assets/characters）
        self._use_seq = False             # 当前姿态是否使用 PNG 序列帧
        self._movie = None                # QMovie（GIF模式）
        self._use_gif = False

        # 粒子系统
        self._particles = []
        # 情绪表情
        self._emotion = None
        self._emotion_timer = QTimer(self)
        self._emotion_timer.setSingleShot(True)
        self._emotion_timer.timeout.connect(self.clear_emotion)
        self._perf_tick = 0               # 情绪表演的循环帧号

        # 待机小动作随机种子（每次进入 idle 时随机一个循环起点）
        self._idle_variant = random.randint(0, 3)

    # ============================================================
    # 对外接口
    # ============================================================
    @property
    def pose(self):
        return self._pose

    @property
    def frame_index(self):
        return self._frame_index

    def set_pose(self, pose: str):
        """切换动画状态（优先级：PNG序列帧 > GIF > 程序化绘制）"""
        if pose not in POSES:
            pose = "idle"
        if pose == self._pose and (pose, self._size) in self._cache:
            return
        self._pose = pose
        self._frame_index = 0
        self._use_gif = False
        if pose == "idle":
            self._idle_variant = random.randint(0, 3)

        # 1) PNG 序列帧资源（assets/characters/<pose>/*.png，朝右、脚底对齐）
        seq_paths = self._sequence_paths(pose)
        if seq_paths:
            self._use_seq = True
            if pose not in self._seq:
                self._seq[pose] = [QPixmap(p) for p in seq_paths]
            return
        self._use_seq = False

        # 2) GIF 资源
        gif_path = resource_path(os.path.join("assets", f"{pose}.gif"))
        if os.path.exists(gif_path):
            self._load_gif(gif_path)
            return
        # 3) 程序化绘制
        if (pose, self._size) not in self._cache:
            self._cache[(pose, self._size)] = self._build_frames(pose)

    def _sequence_paths(self, pose: str):
        """assets/characters/<pose>/ 下的 PNG 序列帧列表（无资源返回 None）"""
        d = resource_path(os.path.join("assets", "characters", pose))
        if not os.path.isdir(d):
            return None
        paths = sorted(glob.glob(os.path.join(d, "*.png")))
        return paths or None

    def set_direction(self, direction: int):
        """设置朝向：1 右 / -1 左"""
        if direction in (1, -1):
            self._direction = direction

    def set_size(self, size: int):
        """设置渲染尺寸（预览页面可调）"""
        size = max(48, min(320, int(size)))
        if size != self._size:
            self._size = size
            self._cache.clear()          # 尺寸变化需重建缓存
            self.set_pose(self._pose)

    @property
    def size(self):
        return self._size

    def set_speed(self, speed: float):
        """播放速度倍率（预览页面可调）"""
        self._speed = max(0.2, min(3.0, speed))

    # ------------------------------------------------------------
    # 帧推进与特效
    # ------------------------------------------------------------
    def advance(self):
        """推进到下一帧 + 更新粒子（由窗口 QTimer 驱动）"""
        if self._use_seq and self._pose in self._seq:
            n = len(self._seq[self._pose])
            self._frame_index = (self._frame_index + 1) % n
            self._spawn_particles()
        elif self._use_gif and self._movie:
            self._movie.jumpToNextFrame()
        else:
            self._frame_index = (self._frame_index + 1) % POSES[self._pose][1]
            self._spawn_particles()
        self._perf_tick += 1
        self._update_particles()

    def current_pixmap(self) -> QPixmap:
        """获取当前帧图像（已按朝向翻转）"""
        if self._use_seq and self._pose in self._seq:
            key = (self._pose, self._size)
            if key not in self._cache:
                # 序列帧原图(512)平滑缩放到渲染尺寸并缓存
                self._cache[key] = [
                    pm.scaled(self._size, self._size,
                              Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                    for pm in self._seq[self._pose]
                ]
            frames = self._cache[key]
            pm = frames[self._frame_index % len(frames)]
        elif self._use_gif and self._movie:
            pm = self._movie.currentPixmap()
        else:
            key = (self._pose, self._size)
            if key not in self._cache:
                self._cache[key] = self._build_frames(self._pose)
            frames = self._cache[key]
            pm = frames[self._frame_index % len(frames)]

        if self._direction == -1:
            return pm.transformed(QTransform().scale(-1.0, 1.0))
        return pm

    def frame_interval_ms(self) -> int:
        """当前状态的帧间隔（毫秒，含速度倍率）"""
        fps = POSES[self._pose][0]
        return max(20, int(1000 / (fps * self._speed)))

    def pose_duration_ms(self, pose: str = None) -> int:
        """播完一轮姿态所需毫秒（序列帧按实际帧数算，供单次表演后回 idle）"""
        pose = pose or self._pose
        if self._use_seq and pose in self._seq:
            n = len(self._seq[pose])
        else:
            n = POSES.get(pose, (8, 16))[1]
        return n * max(20, int(1000 / (POSES.get(pose, (8, 16))[0] * self._speed)))

    # ------------------------------------------------------------
    # 情绪表情
    # ------------------------------------------------------------
    def set_emotion(self, kind: str, duration_ms: int = None):
        """叠加情绪表情（自动消失 + 配套的身体表演与粒子）"""
        if kind not in EMOTIONS:
            return
        self._emotion = kind
        self._perf_tick = 0               # 表演从头开始
        self._spawn_emotion_burst(kind)
        _, _, default = EMOTIONS[kind]
        self._emotion_timer.start(duration_ms or default)

    def performance_offset(self):
        """当前情绪表演的身体偏移 (dx, dy)，128px 基准像素。无情绪返回 (0, 0)。"""
        cycle = PERFORMANCES.get(self._emotion)
        if not cycle:
            return 0, 0
        return cycle[self._perf_tick % len(cycle)]

    def _spawn_emotion_burst(self, kind: str):
        """情绪出现瞬间喷发一簇配套粒子（与该情绪的动作呼应）"""
        def add(kind_, x, y, vx, vy, r, color=None, decay=0.035):
            self._particles.append({"kind": kind_, "x": x, "y": y,
                                    "vx": vx, "vy": vy, "r": r,
                                    "life": 1.0, "decay": decay})
            if color:
                self._particles[-1]["color"] = color

        if kind == "happy":       # 音符 + 金星齐飞
            for i in range(5):
                add("note", random.uniform(55, 85), random.uniform(30, 50),
                    random.uniform(-0.4, 0.4), -0.5, random.uniform(6, 9))
            add("star", 95, 35, 0.2, -0.4, 5)
            add("star", 40, 45, -0.2, -0.4, 4)
        elif kind == "love":      # 爱心冒泡
            for i in range(6):
                add("heart", random.uniform(45, 95), random.uniform(35, 60),
                    random.uniform(-0.3, 0.3), -0.4, random.uniform(4, 7))
        elif kind == "angry":     # 红色怒气符号蹦出
            for i in range(6):
                add("star", random.uniform(45, 95), random.uniform(25, 50),
                    random.uniform(-0.5, 0.5), -0.3, random.uniform(4, 6),
                    color=(230, 80, 60))
        elif kind == "sweat":     # 大颗汗滴滚落
            for i in range(3):
                add("sweat", random.uniform(60, 95), random.uniform(25, 40),
                    random.uniform(-0.2, 0.2), 0.3, random.uniform(4, 6),
                    decay=0.02)
        elif kind == "sleep":     # Z 字上飘
            for i in range(4):
                add("zzz", 85 + i * 4, 40 - i * 6, 0.05, -0.25,
                    7 + i * 2, decay=0.018)
        elif kind == "exclaim":   # 黄星一闪
            for i in range(3):
                add("star", random.uniform(70, 95), random.uniform(28, 42),
                    random.uniform(-0.3, 0.3), -0.3, random.uniform(3, 5),
                    color=(255, 190, 60), decay=0.05)
        elif kind == "star":      # 金星环绕
            for i in range(7):
                ang = i * math.pi * 2 / 7
                add("star", 64 + 26 * math.cos(ang), 40 + 14 * math.sin(ang),
                    0.25 * math.cos(ang + math.pi / 2), -0.2,
                    random.uniform(3.5, 5.5), decay=0.025)

    def clear_emotion(self):
        self._emotion = None
        self._emotion_timer.stop()

    # ------------------------------------------------------------
    # 粒子系统
    # ------------------------------------------------------------
    def _spawn_particles(self):
        """根据当前状态与帧号生成粒子"""
        pose, i = self._pose, self._frame_index
        if pose == "jump" and i in (0, 9):
            # 起跳与落地：两侧尘土
            for side in (-1, 1):
                for _ in range(4):
                    self._particles.append({
                        "kind": "dust",
                        "x": 64 + side * random.randint(18, 30),
                        "y": 112 + random.randint(-4, 4),
                        "vx": side * random.uniform(0.4, 1.4),
                        "vy": -random.uniform(0.2, 0.9),
                        "r": random.uniform(2.5, 5.5),
                        "life": 1.0, "decay": random.uniform(0.05, 0.09),
                    })
            if i == 0:
                for _ in range(5):      # 腾空时头顶小星星
                    self._particles.append({
                        "kind": "star",
                        "x": 64 + random.randint(-26, 26),
                        "y": 34 + random.randint(-14, 14),
                        "vx": random.uniform(-0.5, 0.5),
                        "vy": -random.uniform(0.3, 0.9),
                        "r": random.uniform(2.0, 3.6),
                        "life": 1.0, "decay": random.uniform(0.04, 0.07),
                    })
        elif pose == "walk" and i in (2, 7):
            self._particles.append({
                "kind": "dust",
                "x": 64 - self._direction * 16 + random.randint(-6, 6),
                "y": 114,
                "vx": -self._direction * random.uniform(0.2, 0.7),
                "vy": -random.uniform(0.1, 0.4),
                "r": random.uniform(2.0, 3.4),
                "life": 1.0, "decay": random.uniform(0.07, 0.12),
            })
        elif pose == "talk" and i % 3 == 0:
            self._particles.append({
                "kind": "note",
                "x": 92 + random.randint(-6, 10),
                "y": 44 + random.randint(-6, 6),
                "vx": random.uniform(0.2, 0.7),
                "vy": -random.uniform(0.4, 0.8),
                "r": random.uniform(7, 10),
                "life": 1.0, "decay": 0.035,
            })
        elif pose == "idle" and i == 0 and random.random() < 0.45:
            self._particles.append({
                "kind": "heart",
                "x": 64 + random.randint(-30, 30),
                "y": 40,
                "vx": random.uniform(-0.3, 0.3),
                "vy": -random.uniform(0.4, 0.7),
                "r": random.uniform(5, 8),
                "life": 1.0, "decay": 0.028,
            })
        elif pose == "think" and i == 6 and random.random() < 0.5:
            self._particles.append({
                "kind": "sweat",
                "x": 96, "y": 34,
                "vx": random.uniform(0.1, 0.4), "vy": 0.1,
                "r": random.uniform(3, 4.5),
                "life": 1.0, "decay": 0.05,
            })

    def _update_particles(self):
        """更新粒子位置与生命周期"""
        alive = []
        for pt in self._particles:
            pt["x"] += pt["vx"]
            pt["y"] += pt["vy"]
            if pt["kind"] in ("heart", "note", "star", "zzz"):
                pt["vy"] -= 0.01          # 上升加速（浮力感）
            elif pt["kind"] in ("dust", "sweat"):
                pt["vy"] += 0.03          # 重力
            pt["life"] -= pt["decay"]
            if pt["life"] > 0:
                alive.append(pt)
        self._particles = alive[:120]     # 上限保护

    def draw_overlay(self, painter: QPainter, size: int):
        """
        在角色帧之上绘制实时层（粒子 + 情绪表情）。
        :param size: 当前绘制区域边长（与窗口尺寸一致）
        """
        scale = size / 128.0
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        # ---- 粒子 ----
        for pt in self._particles:
            x, y = pt["x"] * scale, pt["y"] * scale
            r = pt["r"] * scale
            alpha = int(230 * max(pt["life"], 0))
            painter.setPen(Qt.NoPen)
            if pt["kind"] == "dust":
                painter.setBrush(QColor(200, 175, 145, alpha))
                painter.drawEllipse(QPointF(x, y), r, r * 0.62)
            elif pt["kind"] == "star":
                c = pt.get("color", (255, 220, 90))
                self._draw_star(painter, x, y, r, QColor(*c, alpha))
            elif pt["kind"] == "heart":
                c = pt.get("color", (255, 110, 150))
                self._draw_heart(painter, x, y, r, QColor(*c, alpha))
            elif pt["kind"] == "note":
                painter.setPen(QPen(QColor(255, 130, 130, alpha), 2 * max(scale, .6)))
                painter.setFont(QFont("Segoe UI Symbol", int(r)))
                painter.drawText(QPointF(x, y), "♪")
            elif pt["kind"] == "zzz":
                painter.setPen(QPen(QColor(255, 255, 255, alpha * 3 // 4),
                                    max(1.5, 2 * scale)))
                painter.setFont(QFont("Segoe UI Symbol", int(r)))
                painter.drawText(QPointF(x, y), "Z")
            elif pt["kind"] == "sweat":
                painter.setBrush(QColor(120, 180, 255, alpha))
                painter.drawEllipse(QPointF(x, y), r * 0.7, r)

        # ---- 情绪表情 ----
        if self._emotion:
            text, color, _ = EMOTIONS[self._emotion]
            font = QFont("Segoe UI Emoji", int(22 * scale))
            painter.setFont(font)
            # 呼吸式上下浮动
            bob = 3 * math.sin(self._frame_index * 0.9)
            x = size * 0.70
            y = (26 + bob) * scale
            painter.setPen(QPen(QColor(255, 255, 255, 200), max(2.0, 3 * scale)))
            painter.drawText(QPointF(x, y), text)          # 白描边
            painter.setPen(color)
            painter.drawText(QPointF(x, y), text)
        painter.restore()

    @staticmethod
    def _draw_star(p, cx, cy, r, color):
        """五角星"""
        path = QPainterPath()
        for i in range(10):
            ang = math.pi / 2 + i * math.pi / 5
            rad = r if i % 2 == 0 else r * 0.45
            x, y = cx + rad * math.cos(ang), cy - rad * math.sin(ang)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.closeSubpath()
        p.setBrush(color)
        p.drawPath(path)

    @staticmethod
    def _draw_heart(p, cx, cy, r, color):
        """爱心"""
        path = QPainterPath()
        path.moveTo(cx, cy + r * 0.9)
        path.cubicTo(cx - r * 1.6, cy - r * 0.2, cx - r * 0.5, cy - r * 1.2, cx, cy - r * 0.35)
        path.cubicTo(cx + r * 0.5, cy - r * 1.2, cx + r * 1.6, cy - r * 0.2, cx, cy + r * 0.9)
        p.setBrush(color)
        p.drawPath(path)

    # ============================================================
    # GIF 模式
    # ============================================================
    def _load_gif(self, path: str):
        from PyQt5.QtGui import QMovie
        self._movie = QMovie(path)
        self._movie.setScaledSize(QSize(self._size, self._size))
        self._movie.start()
        self._use_gif = True
        logger.info(f"[Sprite] 使用GIF资源: {path}")

    # ============================================================
    # 程序化绘制
    # ============================================================
    def _build_frames(self, pose: str) -> list:
        """为某状态生成全部帧的 QPixmap 列表"""
        _, count = POSES[pose]
        frames = []
        for i in range(count):
            pm = QPixmap(self._size, self._size)
            pm.fill(Qt.transparent)
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.scale(self._size / 128.0, self._size / 128.0)   # 统一用128坐标系
            self._draw_pet(painter, pose, i, count)
            painter.end()
            frames.append(pm)
        logger.info(f"[Sprite] 生成 {pose} 动画 {count} 帧 @{self._size}px")
        return frames

    # ------------------------------------------------------------
    # 单帧绘制：橘猫团子
    # ------------------------------------------------------------
    def _draw_pet(self, p: QPainter, pose: str, i: int, n: int):
        t = i / max(n, 1)
        cx, cy = 64.0, 68.0

        # ===== 姿态参数 =====
        bounce = 0.0          # 整体上下位移
        squash = 1.0          # 纵向压缩（配合横向拉伸）
        tilt = 0.0            # 头部倾斜（弧度）
        leg_phase = 0.0       # 腿部摆动
        tail_amp = 0.35       # 尾巴摆幅
        ear_droop = 0.0       # 耳朵下垂
        eye_state = "open"    # open / blink / happy / look
        mouth = "smile"       # smile / open / flat / o
        body_y = cy

        if pose == "idle":
            # 呼吸 + 随机小动作（伸懒腰 / 打哈欠）
            v = (self._idle_variant + i // 4) % 4
            breath = math.sin(t * 2 * math.pi)
            bounce = 3.0 * breath
            squash = 1.0 + 0.03 * breath
            if v == 1 and i % 4 in (0, 1):        # 伸懒腰
                squash = 1.0 + 0.10 * math.sin(i * math.pi)
                tilt = 0.08
            elif v == 2 and i % 4 == 2:           # 打哈欠
                mouth = "o"
                eye_state = "happy"
            tail_amp = 0.25 + 0.1 * breath
            if i in (n - 2, n - 1) or i in (6, 7):
                eye_state = "blink"

        elif pose == "walk":
            walk = math.sin(t * 4 * math.pi)
            bounce = 4.5 * abs(walk)
            squash = 1.0 + 0.05 * math.sin(t * 8 * math.pi)
            tilt = 0.05 * walk
            leg_phase = t * 2 * math.pi
            tail_amp = 0.6
            eye_state = "look"

        elif pose == "jump":
            if i <= 2:                            # 下蹲蓄力
                bounce = 8 + 6 * (i / 2)
                squash = 0.78 - 0.04 * i
                eye_state = "happy"
            elif i <= 7:                          # 腾空
                k = (i - 3) / 4
                bounce = -30 * math.sin((k + 0.12) * math.pi)
                squash = 1.10 - 0.04 * k
                tail_amp = 0.9
                eye_state = "happy"
            else:                                 # 落地缓冲
                bounce = 6 * (1 - (i - 8) / 2)
                squash = 0.88 + 0.04 * (i - 8)
                eye_state = "blink" if i == 8 else "open"
            mouth = "o" if i in (3, 4, 5) else "smile"

        elif pose == "think":
            bounce = 2.0 * math.sin(t * 2 * math.pi)
            tilt = 0.14
            ear_droop = 6.0
            tail_amp = 0.15
            eye_state = "look"
            mouth = "flat"

        elif pose == "searching":
            look = math.sin(t * 2 * math.pi)
            tilt = 0.06 * look
            bounce = 2.2 * math.sin(t * 4 * math.pi)
            tail_amp = 0.75                        # 警觉竖尾
            eye_state = "look"
            mouth = "flat"

        elif pose == "talk":
            bounce = 3.0 * math.sin(t * 4 * math.pi)
            squash = 1.0 + 0.04 * math.sin(t * 4 * math.pi)
            mouth = "open" if i % 2 == 0 else "smile"
            tail_amp = 0.45
            eye_state = "happy" if i % 4 < 2 else "open"

        body_y = cy + bounce

        # ===== 影子（先画，位于角色下方）=====
        p.save()
        p.setPen(Qt.NoPen)
        shrink = 1.0 if bounce <= 0 else 1.25
        shadow_alpha = 45 if bounce >= 0 else 22
        p.setBrush(QColor(60, 40, 20, shadow_alpha))
        sw = 40 / shrink
        p.drawEllipse(QPointF(cx + math.sin(t * 6) * 2, 116), sw, 7 / shrink)
        p.restore()

        # ===== 尾巴（画在身体后面）=====
        self._draw_tail(p, cx, body_y, tail_amp, t, pose)

        # ===== 身体坐标系 =====
        p.save()
        p.translate(cx, body_y)
        p.rotate(math.degrees(tilt))
        p.scale(squash, 2.0 - squash)

        # ---- 后腿（走路时交替）----
        if pose == "walk":
            for side, off in ((-1, 0.0), (1, math.pi)):
                swing = 10 * math.sin(leg_phase + off)
                self._draw_leg(p, side * 15, 30, swing, back=True)
        elif pose == "jump":
            for side in (-1, 1):
                self._draw_leg(p, side * 14, 30, -6 if i > 2 else 4, back=True)

        # ---- 耳朵 ----
        self._draw_ear(p, -1, ear_droop)          # 左耳
        self._draw_ear(p, 1, ear_droop)           # 右耳

        # ---- 身体（径向渐变）----
        grad = QRadialGradient(QPointF(-10, -18), 58)
        grad.setColorAt(0, QColor(255, 196, 120))
        grad.setColorAt(0.55, self.BODY_COLOR)
        grad.setColorAt(1, self.BODY_DARK)
        p.setBrush(QBrush(grad))
        p.setPen(QPen(self.BODY_DARK.darker(115), 2))
        p.drawEllipse(QPointF(0, 0), 38, 36)

        # ---- 肚皮 ----
        p.setPen(Qt.NoPen)
        p.setBrush(self.BELLY_COLOR)
        p.drawEllipse(QPointF(0, 13), 21, 16)

        # ---- 前爪（走路/跳跃时摆动）----
        if pose == "walk":
            self._draw_leg(p, -12, 28, 10 * math.sin(leg_phase + math.pi), back=False)
            self._draw_leg(p, 12, 28, 10 * math.sin(leg_phase), back=False)
        elif pose == "jump" and i > 2:
            self._draw_leg(p, -13, 26, -10, back=False)
            self._draw_leg(p, 13, 26, -10, back=False)
        else:
            self._draw_leg(p, -13, 30, 0, back=False)
            self._draw_leg(p, 13, 30, 0, back=False)

        # ---- 条纹（橘猫花纹）----
        p.setPen(QPen(QColor(230, 140, 55, 120), 3.5, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(-30, -14), QPointF(-24, -6))
        p.drawLine(QPointF(-33, -4), QPointF(-27, 4))
        p.drawLine(QPointF(30, -14), QPointF(24, -6))
        p.drawLine(QPointF(33, -4), QPointF(27, 4))

        # ---- 眼睛 ----
        look_dx = 0.0
        if eye_state == "look":
            look_dx = 2.5 * math.sin(t * 2 * math.pi)
        self._draw_eyes(p, eye_state, look_dx)

        # ---- 腮红 ----
        p.setBrush(QColor(255, 120, 110, 110))
        p.drawEllipse(QPointF(-25, 3), 7, 4)
        p.drawEllipse(QPointF(25, 3), 7, 4)

        # ---- 鼻子 ----
        p.setBrush(self.NOSE_COLOR)
        path = QPainterPath()
        path.moveTo(0, 8); path.lineTo(-4, 5); path.lineTo(4, 5)
        path.closeSubpath()
        p.drawPath(path)

        # ---- 嘴巴 ----
        self._draw_mouth(p, mouth)

        # ---- 胡须 ----
        p.setPen(QPen(QColor(90, 70, 55, 140), 1.5, Qt.SolidLine, Qt.RoundCap))
        for dx in (-1, 1):
            y0 = 6
            p.drawLine(QPointF(dx * 30, y0), QPointF(dx * 44, y0 - 4))
            p.drawLine(QPointF(dx * 30, y0 + 4), QPointF(dx * 44, y0 + 7))
        p.restore()

        # ===== 状态装饰（问号/张望箭头）=====
        if pose == "think":
            self._draw_thought(p, i)
        elif pose == "searching":
            self._draw_look_marks(p, t)

    # ------------------------------------------------------------
    # 部件绘制
    # ------------------------------------------------------------
    def _draw_tail(self, p, cx, cy, amp, t, pose):
        """尾巴：二次贝塞尔曲线摆动"""
        base_x, base_y = cx - 34, cy + 6
        swing = 26 * amp * math.sin(t * 4 * math.pi)
        # 寻找时尾巴竖得更直
        lift = 26 if pose == "searching" else 10
        ctrl = QPointF(base_x - 26, base_y + lift - 18 + swing * 0.3)
        end = QPointF(base_x - 18 - abs(swing) * 0.4,
                      base_y - lift - 14 + swing)
        path = QPainterPath()
        path.moveTo(base_x, base_y)
        path.quadTo(ctrl, end)
        p.setPen(QPen(self.BODY_DARK, 9, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        # 尾巴尖
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 240, 214))
        p.drawEllipse(end, 5.5, 5.5)

    def _draw_ear(self, p, side: int, droop: float):
        """耳朵：side=-1 左 1 右；droop 下垂量"""
        x = side * 20
        tip = -48 + droop
        outer = QPainterPath()
        outer.moveTo(x - side * 10, -22)
        outer.lineTo(x + side * 2, tip)
        outer.lineTo(x + side * 14, -24)
        outer.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(self.BODY_DARK)
        p.drawPath(outer)
        inner = QPainterPath()
        inner.moveTo(x - side * 5, -25)
        inner.lineTo(x + side * 2, tip + 7)
        inner.lineTo(x + side * 9, -26)
        inner.closeSubpath()
        p.setBrush(self.EAR_INNER)
        p.drawPath(inner)

    def _draw_leg(self, p, x: int, y: int, swing: float, back: bool):
        """腿/爪子：椭圆 + 摆动"""
        color = self.BODY_DARK.darker(105) if back else self.BODY_COLOR
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(x + swing * 0.5, y), 8, 9)
        p.setBrush(QColor(255, 240, 214))
        p.drawEllipse(QPointF(x + swing * 0.5, y + 3), 5.5, 4)

    def _draw_eyes(self, p, state: str, look_dx: float):
        """眼睛：open/blink/happy/look"""
        p.setPen(Qt.NoPen)
        p.setBrush(self.EYE_COLOR)
        if state == "blink":
            p.setPen(QPen(self.EYE_COLOR, 3, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(-19, -8), QPointF(-8, -8))
            p.drawLine(QPointF(8, -8), QPointF(19, -8))
            p.setPen(Qt.NoPen)
            return
        if state == "happy":
            # 闭眼弯弯（^ ^）
            p.setPen(QPen(self.EYE_COLOR, 3, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(-20, -16, 12, 12, 0, 180 * 16)
            p.drawArc(8, -16, 12, 12, 0, 180 * 16)
            p.setPen(Qt.NoPen)
            return
        # 睁眼：眼白 + 瞳孔 + 高光
        for ex in (-13.5, 13.5):
            p.setBrush(QColor(255, 252, 248))
            p.drawEllipse(QPointF(ex, -8), 7.5, 9)
            p.setBrush(self.EYE_COLOR)
            p.drawEllipse(QPointF(ex + look_dx, -7), 4.6, 6)
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(QPointF(ex + look_dx - 1.6, -10.5), 1.9, 2.4)

    def _draw_mouth(self, p, mouth: str):
        """嘴巴：smile / open / flat / o"""
        p.setPen(QPen(self.EYE_COLOR, 2.2, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        if mouth == "open":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(160, 70, 70))
            p.drawEllipse(QPointF(0, 15), 6.5, 7)
            p.setBrush(QColor(255, 150, 160))
            p.drawEllipse(QPointF(0, 18), 4.5, 2.5)     # 舌头
        elif mouth == "o":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(150, 70, 70))
            p.drawEllipse(QPointF(0, 15), 5, 6.5)
        elif mouth == "flat":
            p.setPen(QPen(self.EYE_COLOR, 2.2, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(-4, 14), QPointF(5, 13))
        else:  # smile：w 形猫嘴
            p.setPen(QPen(self.EYE_COLOR, 2.2, Qt.SolidLine, Qt.RoundCap))
            path = QPainterPath()
            path.moveTo(-9, 12)
            path.quadTo(-4.5, 17, 0, 13)
            path.quadTo(4.5, 17, 9, 12)
            p.drawPath(path)

    # ------------------------------------------------------------
    # 状态装饰
    # ------------------------------------------------------------
    @staticmethod
    def _draw_thought(p: QPainter, i: int):
        """思考：头顶冒泡 + 问号"""
        bob = (i % 4) * -2
        alpha = 150 + int(90 * abs(math.sin(i)))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 200))
        p.drawEllipse(QPointF(100, 26 + bob), 9, 7)
        p.drawEllipse(QPointF(112, 18 + bob), 12, 10)
        p.setPen(QPen(QColor(90, 90, 200, alpha), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(100, 26 + bob), 9, 7)
        p.drawEllipse(QPointF(112, 18 + bob), 12, 10)
        p.setPen(QPen(QColor(80, 80, 190, alpha), 3.2))
        p.setFont(QFont("Segoe UI", 14, QFont.Bold))
        p.drawText(QPointF(106, 22 + bob), "?")

    @staticmethod
    def _draw_look_marks(p: QPainter, t: float):
        """寻找：头顶左右张望的箭头"""
        x = 64 + 34 * math.sin(t * 2 * math.pi)
        p.setPen(QPen(QColor(90, 90, 110, 170), 2.6, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(int(x - 8), 16, int(x + 8), 16)
        p.drawLine(int(x + 8), 16, int(x + 3), 11)
        p.drawLine(int(x + 8), 16, int(x + 3), 21)
