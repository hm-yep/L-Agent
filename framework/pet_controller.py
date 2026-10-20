# -*- coding: utf-8 -*-
"""宠物控制器：移动 + 搜索路线

负责桌面角色的所有移动逻辑，UI 层通过 Framework 调用本模块：

    move_to(x, y, duration, on_done)   平滑移动到指定位置（缓动插值）
    search_route(on_found)             四角搜索路线（模拟四处翻找）
    go_taskbar(on_done)                移动到任务栏/托盘位置（模拟跑过去）
    jump(on_done)                      原地跳跃庆祝
    wander()                           待机时随机散步（性格影响）
    get_anchor(name)                   获取屏幕锚点坐标
    stop()                             立即停止移动

移动速度受性格（活泼/沉稳/毒舌）影响。
"""

import random

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from framework import config
from framework.utils import logger

# 移动刷新间隔（毫秒），约 33fps
TICK_MS = 30
# 搜索路线中每个锚点停留时间（毫秒），模拟"四处张望"
SEARCH_PAUSE_MS = 700
# 搜索路线锚点顺序：左上 -> 右上 -> 左下 -> 右下
SEARCH_ANCHORS = ["top_left", "top_right", "bottom_left", "bottom_right"]


class PetController(QObject):
    """桌面角色移动控制器（纯逻辑层，不持有窗口引用）"""

    # 信号：位置更新 (x, y, direction)  direction: 1右 -1左 0不变
    position_changed = pyqtSignal(int, int, int)
    # 信号：一段移动完成
    move_finished = pyqtSignal(str)          # 携带本次移动的 tag
    # 信号：搜索路线完成
    search_finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._x = 200                        # 当前位置
        self._y = 200
        self._moving = False                 # 是否正在移动
        self._jumping = False

        # 移动插值参数
        self._target = (self._x, self._y)
        self._start = (self._x, self._y)
        self._duration = 0
        self._elapsed = 0
        self._tag = ""
        self._on_done = None                 # 移动完成回调

        # 搜索路线状态
        self._search_steps = []              # 待执行的步骤队列
        self._searching = False

        # 定时器：移动 tick
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

        # 定时器：暂停等待（搜索路线中锚点停留）
        self._pause_timer = QTimer(self)
        self._pause_timer.setSingleShot(True)
        self._pause_timer.timeout.connect(self._after_pause)

        # 定时器：主动散步
        self._wander_timer = QTimer(self)
        self._wander_timer.setSingleShot(True)
        self._wander_timer.timeout.connect(self._do_wander)

        self._personality = config.DEFAULT_PERSONALITY

    # ============================================================
    # 基础状态
    # ============================================================
    @property
    def position(self):
        return (self._x, self._y)

    @property
    def is_moving(self):
        return self._moving

    def set_position(self, x: int, y: int):
        """直接设置位置（用户拖拽后同步）"""
        self._x, self._y = x, y
        self._target = (x, y)
        self.position_changed.emit(x, y, 0)

    def set_personality(self, personality: str):
        """切换性格，影响移动速度与散步频率"""
        if personality in config.PERSONALITIES:
            self._personality = personality

    def _speed(self) -> float:
        """当前性格的移动速度倍率"""
        return config.PERSONALITIES.get(
            self._personality, {"move_speed": 1.0}
        )["move_speed"]

    # ============================================================
    # 锚点
    # ============================================================
    def get_anchor(self, name: str):
        """
        获取屏幕锚点坐标。
        支持：top_left / top_right / bottom_left / bottom_right /
             center / taskbar / tray / random
        """
        try:
            from PyQt5.QtWidgets import QApplication
            screen = QApplication.primaryScreen().availableGeometry()
        except Exception:
            screen = type("S", (), {"left": 0, "top": 0,
                                    "right": 1920, "bottom": 1080})()

        m = config.ANCHOR_MARGIN
        pet = config.PET_SIZE
        anchors = {
            "top_left":     (screen.left() + m, screen.top() + m),
            "top_right":    (screen.right() - m - pet, screen.top() + m),
            "bottom_left":  (screen.left() + m, screen.bottom() - m - pet),
            "bottom_right": (screen.right() - m - pet, screen.bottom() - m - pet),
            "center":       ((screen.left() + screen.right()) // 2 - pet // 2,
                             (screen.top() + screen.bottom()) // 2 - pet // 2),
            "taskbar":      ((screen.left() + screen.right()) // 2 - pet // 2,
                             screen.bottom() - pet - 8),
            "tray":         (screen.right() - pet - 220, screen.bottom() - pet - 8),
        }
        if name == "random":
            name = random.choice(list(anchors.keys()))
        return anchors.get(name, anchors["center"])

    # ============================================================
    # 核心：平滑移动
    # ============================================================
    def move_to(self, x: int, y: int, duration: int = 2000,
                tag: str = "", on_done=None):
        """
        平滑移动到指定位置（缓动 ease-in-out 插值）。
        :param duration: 移动时长（毫秒），会按性格速度自动缩放
        :param on_done: 完成回调
        """
        duration = max(200, int(duration / max(self._speed(), 0.1)))
        self._start = (self._x, self._y)
        self._target = (int(x), int(y))
        self._duration = duration
        self._elapsed = 0
        self._tag = tag
        self._on_done = on_done
        self._moving = True
        self._timer.start()
        logger.info(f"[Pet] move_to {self._target} ({duration}ms) tag={tag}")

    def _tick(self):
        """移动插值 tick"""
        if not self._moving:
            self._timer.stop()
            return

        self._elapsed += TICK_MS
        t = min(1.0, self._elapsed / self._duration)
        # ease-in-out：先加速后减速，移动更自然
        k = t * t * (3 - 2 * t)

        sx, sy = self._start
        tx, ty = self._target
        new_x = int(sx + (tx - sx) * k)
        new_y = int(sy + (ty - sy) * k)

        # 计算水平方向（用于动画翻转朝向）
        direction = 0
        if new_x > self._x + 1:
            direction = 1
        elif new_x < self._x - 1:
            direction = -1

        self._x, self._y = new_x, new_y
        self.position_changed.emit(new_x, new_y, direction)

        if t >= 1.0:
            self._moving = False
            self._timer.stop()
            self.move_finished.emit(self._tag)
            if self._on_done:
                cb = self._on_done
                self._on_done = None
                cb()

    def stop(self):
        """立即停止移动/搜索"""
        self._moving = False
        self._searching = False
        self._search_steps.clear()
        self._timer.stop()
        self._pause_timer.stop()
        self._wander_timer.stop()

    # ============================================================
    # 搜索路线：四角翻找动画路线
    # ============================================================
    def search_route(self, on_visit=None, on_done=None):
        """
        搜索路线：依次在四个屏幕角锚点间移动，模拟四处翻找。
        :param on_visit: 每到达一个锚点的回调 (anchor_name)
        :param on_done:  全部完成的回调
        """
        if self._searching:
            return
        self._searching = True
        self._on_visit = on_visit
        self._on_search_done = on_done

        # 生成步骤序列：起点 -> 角1 -> 角2 -> 角3 -> 角4
        self._search_steps = list(SEARCH_ANCHORS)
        logger.info("[Pet] 搜索路线开始: " + " -> ".join(self._search_steps))
        self._next_search_step()

    def _next_search_step(self):
        """执行搜索路线的下一步"""
        if not self._searching or not self._search_steps:
            # 路线结束
            self._searching = False
            self.search_finished.emit()
            if self._on_search_done:
                cb = self._on_search_done
                self._on_search_done = None
                cb()
            return

        anchor_name = self._search_steps.pop(0)
        x, y = self.get_anchor(anchor_name)
        self.move_to(
            x, y,
            duration=self._route_duration(anchor_name),
            tag=f"search:{anchor_name}",
            on_done=lambda a=anchor_name: self._arrived_anchor(a),
        )

    def _route_duration(self, target: str) -> int:
        """根据目标位置估算移动时长（距离越远走得越久）"""
        tx, ty = self.get_anchor(target)
        dist = ((tx - self._x) ** 2 + (ty - self._y) ** 2) ** 0.5
        return int(600 + dist * 0.6)

    def _arrived_anchor(self, anchor_name: str):
        """到达一个搜索锚点：停留片刻模拟张望，然后继续"""
        if self._on_visit:
            self._on_visit(anchor_name)
        self._pause_timer.start(SEARCH_PAUSE_MS)

    def _after_pause(self):
        """锚点停留结束，继续下一步"""
        self._next_search_step()

    # ============================================================
    # 跑向任务栏（打开应用场景）
    # ============================================================
    def go_taskbar(self, on_done=None):
        """移动到任务栏中央位置，模拟'跑过去打开应用'"""
        x, y = self.get_anchor("taskbar")
        self.move_to(x, y, duration=2500, tag="taskbar", on_done=on_done)

    def go_tray(self, on_done=None):
        """移动到系统托盘附近"""
        x, y = self.get_anchor("tray")
        self.move_to(x, y, duration=2500, tag="tray", on_done=on_done)

    def go_center(self, on_done=None):
        """移动到屏幕中央（主动互动场景）"""
        x, y = self.get_anchor("center")
        self.move_to(x, y, duration=2800, tag="center", on_done=on_done)

    # ============================================================
    # 跳跃
    # ============================================================
    def jump(self, on_done=None):
        """原地跳跃（快速上移再回落），庆祝动作"""
        if self._jumping:
            if on_done:
                on_done()
            return
        self._jumping = True
        base_y = self._y
        peak = base_y - config.PET_SIZE // 2

        def _up():
            self.move_to(self._x, peak, duration=280, tag="jump_up", on_done=_down)

        def _down():
            self.move_to(self._x, base_y, duration=280, tag="jump_down",
                         on_done=_finish)

        def _finish():
            self._jumping = False
            if on_done:
                on_done()

        _up()

    @property
    def is_jumping(self):
        return self._jumping

    # ============================================================
    # 待机散步（性格影响频率）
    # ============================================================
    def start_wander(self):
        """开启待机散步模式：每隔随机时间小幅移动"""
        self._schedule_wander()

    def stop_wander(self):
        self._wander_timer.stop()

    def _schedule_wander(self):
        """按性格安排下次散步时间（活泼更频繁）"""
        rate = config.PERSONALITIES.get(
            self._personality, {"action_rate": 1.0}
        )["action_rate"]
        interval = int(random.uniform(12000, 25000) / max(rate, 0.1))
        self._wander_timer.start(interval)

    def _do_wander(self):
        """随机小幅散步"""
        if self._moving or self._searching or self._jumping:
            self._schedule_wander()
            return
        dx = random.randint(-150, 150)
        dy = random.randint(-60, 60)
        x = max(0, self._x + dx)
        y = max(0, self._y + dy)
        # 散步点限制在屏幕内
        try:
            from PyQt5.QtWidgets import QApplication
            screen = QApplication.primaryScreen().availableGeometry()
            x = max(screen.left() + 10, min(x, screen.right() - config.PET_SIZE - 10))
            y = max(screen.top() + 10, min(y, screen.bottom() - config.PET_SIZE - 10))
        except Exception:
            pass
        self.move_to(x, y, duration=1600, tag="wander")
        self._schedule_wander()
