# -*- coding: utf-8 -*-
"""音效管理

语音包来源：400 Sounds Pack（WAV）。已挑选的音效放在 assets/sounds/ 下。

音效别名：
    bubble     气泡音（UI/pop_1~4 随机）—— 卡片弹出、气泡出现时播放
    card       抽卡音（Card and Board/card_draw_1~3 随机）—— 拉出信息卡片
    click      轻点击（UI/click_double_on）—— 提交指令、点击按钮
    whoosh     呼啸（Other/whoosh_1）—— 角色跳跃/移动起点

用法:
    sfx = SoundManager()
    sfx.play("bubble")
    sfx.play("card")            # 拉出卡片：抽卡音 + 气泡音叠加
    sfx.set_enabled(False)
    sfx.set_volume(0.5)
"""

import os
import random

from PyQt5.QtCore import QUrl
from PyQt5.QtMultimedia import QSoundEffect

from framework import config
from framework.utils import logger, resource_path

# 音效别名 -> 候选文件（每次随机挑一个，避免重复听腻）
SOUND_MAP = {
    "bubble": ["pop_1.wav", "pop_2.wav", "pop_3.wav", "pop_4.wav"],
    "card":   ["card_draw_1.wav", "card_draw_2.wav", "card_draw_3.wav"],
    "click":  ["click_double_on.wav"],
    "whoosh": ["whoosh_1.wav"],
}


class SoundManager:
    """音效管理器：QSoundEffect 低延迟播放（WAV）"""

    def __init__(self, enabled: bool = None, volume: float = None):
        self._enabled = config.SOUND_ENABLED if enabled is None else enabled
        self._volume = config.SOUND_VOLUME if volume is None else volume
        # 缓存已加载的音效，避免重复读盘
        self._effects = {}
        self._missing = set()

    # ------------------------------------------------------------
    # 状态控制
    # ------------------------------------------------------------
    @property
    def enabled(self):
        return self._enabled

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        config.SOUND_ENABLED = self._enabled
        logger.info(f"[Sound] 音效开关: {self._enabled}")

    def set_volume(self, volume: float):
        """音量 0.0 ~ 1.0"""
        self._volume = max(0.0, min(1.0, float(volume)))
        config.SOUND_VOLUME = self._volume
        for effect in self._effects.values():
            effect.setVolume(self._volume)

    @property
    def volume(self):
        return self._volume

    # ------------------------------------------------------------
    # 播放
    # ------------------------------------------------------------
    def play(self, alias: str):
        """
        播放音效。支持组合别名：
            "card"   -> 抽卡音 + 气泡音（拉出卡片时的"气泡音"效果）
        """
        if not self._enabled:
            return

        # 组合音效：卡片抽出 = 卡片滑动声 + 气泡声，两者间隔 90ms 更有层次
        if alias == "card":
            self._play_once("card")
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(90, lambda: self._play_once("bubble"))
            return
        self._play_once(alias)

    def _play_once(self, alias: str):
        """播放单个音效"""
        names = SOUND_MAP.get(alias)
        if not names:
            return
        path = self._resolve(random.choice(names))
        if path is None:
            return

        effect = self._effects.get(path)
        if effect is None:
            effect = QSoundEffect()
            effect.setSource(QUrl.fromLocalFile(path))
            effect.setVolume(self._volume)
            self._effects[path] = effect
        effect.play()

    # ------------------------------------------------------------
    def _resolve(self, filename: str):
        """定位音效文件，缺失时只提示一次"""
        path = resource_path(os.path.join("assets", "sounds", filename))
        if not os.path.exists(path):
            if path not in self._missing:
                self._missing.add(path)
                logger.warning(f"[Sound] 音效文件缺失: {path}")
            return None
        return path

    def available(self) -> bool:
        """语音包是否可用"""
        return self._resolve("pop_1.wav") is not None
