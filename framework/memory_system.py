# -*- coding: utf-8 -*-
"""记忆系统：对话记忆 + 用户偏好

- 记录最近 20 轮对话，调用 AI 时作为上下文传入
- 从对话中提取用户偏好（城市、兴趣等）
- 所有记忆保存为 JSON 文件，重启后保留
"""

import json
import os
import re
import time

# 同一会话的时间间隔阈值（秒），超过则视为新的一轮对话
SESSION_GAP_SEC = 10 * 60

from framework import config
from framework.utils import app_data_dir, logger


class MemorySystem:
    """对话记忆系统（JSON 持久化）"""

    def __init__(self):
        self.history = []          # [{"role","content"}, ...]
        self.preferences = {}      # {"city": "...", "interests": [...]}
        self.facts = []            # 长期记忆：用户明确让记住的事实（字符串）
        self._file = os.path.join(app_data_dir(), config.MEMORY_FILE)
        self.load()

    # ------------------------------------------------------------
    # 对话历史
    # ------------------------------------------------------------
    def add(self, role: str, content: str):
        """新增一条对话记录（带时间戳），超出上限时裁剪"""
        self.history.append({
            "role": role, "content": content, "ts": time.time()
        })
        self._trim()
        self._extract_preferences(content)
        self.save()

    def add_pair(self, user_text: str, reply_text: str):
        """成对记录一轮对话"""
        now = time.time()
        self.history.append(
            {"role": "user", "content": user_text, "ts": now}
        )
        self.history.append(
            {"role": "assistant", "content": reply_text, "ts": now + 0.1}
        )
        self._trim()
        self._extract_preferences(user_text)
        self.save()

    # ------------------------------------------------------------
    # 会话记录（供输入框下方的历史折叠面板使用）
    # ------------------------------------------------------------
    def get_pairs(self) -> list:
        """
        整理成一轮轮对话：
        [{"user": 问, "reply": 答, "ts": 时间戳, "new_session": 是否新会话}, ...]
        """
        pairs = []
        pending = None
        last_ts = None
        for item in self.history:
            if item.get("role") == "user":
                pending = {"user": item.get("content", ""),
                           "ts": item.get("ts", 0)}
            elif item.get("role") == "assistant" and pending is not None:
                ts = item.get("ts", 0) or pending["ts"]
                pairs.append({
                    "user": pending["user"],
                    "reply": item.get("content", ""),
                    "ts": ts,
                    # 与上一条间隔超过阈值就划分为新会话
                    "new_session": (last_ts is None
                                    or (ts - last_ts) > SESSION_GAP_SEC),
                })
                last_ts = ts
                pending = None
        if pending is not None:      # 只问未答的也算一条
            pairs.append({
                "user": pending["user"], "reply": "",
                "ts": pending["ts"],
                "new_session": (last_ts is None
                                or (pending["ts"] - last_ts) > SESSION_GAP_SEC),
            })
        return pairs

    def delete_pair(self, index: int) -> bool:
        """
        删除第 index 轮对话（用户消息与其回答一起删除，0 起始）。
        索引与 get_pairs() 返回的顺序一致，成功返回 True。
        """
        seen = -1
        for i, item in enumerate(self.history):
            if item.get("role") == "user":
                seen += 1
                if seen == index:
                    end = i + 1
                    if (end < len(self.history)
                            and self.history[end].get("role") == "assistant"):
                        end += 1
                    del self.history[i:end]
                    self.save()
                    logger.info(f"[Memory] 已删除第 {index + 1} 轮对话")
                    return True
        return False

    def turn_count(self) -> int:
        """对话轮数"""
        return len(self.get_pairs())

    def get_context(self, max_turns: int = None) -> list:
        """获取对话上下文（供 AI 调用）"""
        n = (max_turns or config.MEMORY_MAX_TURNS) * 2
        return list(self.history[-n:])

    def clear(self):
        """清空记忆"""
        self.history.clear()
        self.preferences.clear()
        self.facts.clear()
        self.save()
        logger.info("[Memory] 记忆已清空")

    def _trim(self):
        """只保留最近 MEMORY_MAX_TURNS 轮（1轮=user+assistant两条）"""
        max_items = config.MEMORY_MAX_TURNS * 2
        if len(self.history) > max_items:
            self.history = self.history[-max_items:]

    # ------------------------------------------------------------
    # 长期记忆（用户明确要求记住的事实，跨会话注入 AI 提示词）
    # ------------------------------------------------------------
    def remember_fact(self, text: str) -> bool:
        """
        记住一条事实（去重、截断、上限 MAX_FACTS 条）。
        :return: 是否真的新增了（False 表示重复或为空）
        """
        text = (text or "").strip()
        if not text:
            return False
        if len(text) > config.FACT_TEXT_MAX:
            text = text[:config.FACT_TEXT_MAX]
        if any(f.lower() == text.lower() for f in self.facts):
            return False
        self.facts.append(text)
        if len(self.facts) > config.MAX_FACTS:
            self.facts = self.facts[-config.MAX_FACTS:]
        self.save()
        logger.info(f"[Memory] 长期记忆 +1: {text}")
        return True

    def forget_fact(self, index: int) -> str | None:
        """删除第 index 条长期记忆（0 起始），返回被删内容；越界返回 None"""
        if 0 <= index < len(self.facts):
            text = self.facts.pop(index)
            self.save()
            logger.info(f"[Memory] 长期记忆 -1: {text}")
            return text
        return None

    def get_facts_text(self) -> str:
        """长期记忆注入 AI 提示词用；没有时返回空串"""
        return "\n".join(f"- {f}" for f in self.facts)

    # ------------------------------------------------------------
    # 偏好提取
    # ------------------------------------------------------------
    def _extract_preferences(self, text: str):
        """从用户输入中提取偏好信息（城市/兴趣）"""
        if not text:
            return
        m = re.search(r"(我在|我在用|我在玩|我在看|我喜欢|我爱)([\w\u4e00-\u9fa5]{1,10})",
                      text)
        if m:
            key, value = m.group(1).strip("我在用看欢喜爱"), m.group(2)
            if key in ("city", "城市") or "天气" in text:
                self.preferences["city"] = value
            else:
                interests = self.preferences.setdefault("interests", [])
                if value not in interests:
                    interests.append(value)
                    self.preferences["interests"] = interests[-10:]

    # ------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------
    def save(self):
        """保存到 JSON 文件"""
        try:
            data = {"history": self.history, "preferences": self.preferences,
                    "facts": self.facts}
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning(f"[Memory] 保存失败: {e}")

    def load(self):
        """从 JSON 文件加载（重启后保留）"""
        if not os.path.exists(self._file):
            return
        try:
            with open(self._file, encoding="utf-8") as f:
                data = json.load(f)
            self.history = data.get("history", [])
            self.preferences = data.get("preferences", {})
            self.facts = [str(f) for f in data.get("facts", [])
                          if str(f).strip()]
            self._trim()
            logger.info(f"[Memory] 已加载 {len(self.history)} 条历史记录、"
                        f"{len(self.facts)} 条长期记忆")
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"[Memory] 加载失败: {e}")
            self.history = []
