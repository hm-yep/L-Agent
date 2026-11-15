"""任务台：定时提醒 + 待办清单 + 交付记录 + 工作日志（JSON 持久化）

- 提醒：[{"text", "at"}]，at 为 unix 时间戳（秒级精度，"20 秒后"也能设）；
  到期由 Framework 的轮询定时器取出并播报（到点即从列表删除）。
  持久化后重启也不丢——关机错过的提醒下次启动会补播一次。
- 待办：[{"text", "done", "ts"}]，支持添加 / 按序号完成 / 列出 / 清理已完成。
- 交付：[{"name", "path", "kind", "ts"}]，凌零每次生成文档（PPT/Word/
  Excel/网页）都会记一笔，右键「我的交付」面板可回看、打开。
- 工作日志：[{"ts", "kind", "text"}]，凌零替用户做过的事按时间流水记录
  （完成计划/生成交付/设提醒/记待办……），支撑"数字员工干了啥"的汇报。

所有数据存 app_data_dir/tasks.json，每次变更立即落盘。
"""

import json
import os
import time

from framework import config
from framework.utils import app_data_dir, logger

MAX_REMINDERS = 50


def format_duration(seconds: float) -> str:
    """把秒数格式化成中文时长（"45 秒" / "1 分 25 秒" / "2 小时 5 分钟"）"""
    seconds = int(round(max(0.0, float(seconds))))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {sec} 秒" if sec else f"{minutes} 分钟"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} 小时 {minutes} 分钟" if minutes else f"{hours} 小时"
    days, hours = divmod(hours, 24)
    return f"{days} 天 {hours} 小时" if hours else f"{days} 天"


class TaskBoard:
    """提醒 + 待办（线程约束：只在主线程读写，无锁）"""

    def __init__(self):
        self._file = os.path.join(app_data_dir(), config.TASKS_FILE)
        self.reminders = []          # [{"text": str, "at": float}]
        self.todos = []              # [{"text": str, "done": bool, "ts": float}]
        self.deliveries = []         # [{"name","path","kind","ts"}]
        self.worklog = []            # [{"ts": float, "kind": str, "text": str}]
        self.load()

    # ------------------------------------------------------------
    # 定时提醒
    # ------------------------------------------------------------
    def add_reminder(self, text: str, at_ts: float) -> dict:
        """新增提醒，at_ts 为触发时刻（unix 秒）"""
        item = {"text": text[:config.REMINDER_TEXT_MAX], "at": float(at_ts)}
        self.reminders.append(item)
        self.reminders.sort(key=lambda r: r["at"])
        if len(self.reminders) > MAX_REMINDERS:
            self.reminders = self.reminders[-MAX_REMINDERS:]
        self.save()
        logger.info(f"[Tasks] 新增提醒: {item['text']} @ {item['at']}")
        return item

    def pop_due_reminders(self, now: float = None) -> list:
        """取出所有到期的提醒（并从列表移除），没到期的不动"""
        now = now if now is not None else time.time()
        due = [r for r in self.reminders if r["at"] <= now]
        if due:
            self.reminders = [r for r in self.reminders if r["at"] > now]
            self.save()
        return due

    def reminder_count(self) -> int:
        return len(self.reminders)

    def remove_reminder(self, index: int) -> dict | None:
        """删除第 index 条提醒（1 起始，按触发时间排序的顺序）；越界返回 None"""
        if not 1 <= index <= len(self.reminders):
            return None
        item = self.reminders.pop(index - 1)
        self.save()
        logger.info(f"[Tasks] 删除提醒: {item['text']}")
        return item

    # ------------------------------------------------------------
    # 待办清单
    # ------------------------------------------------------------
    def add_todo(self, text: str) -> dict:
        """新增待办；同一内容且未完成的不再重复添加"""
        text = text[:config.REMINDER_TEXT_MAX]
        for t in self.todos:
            if not t["done"] and t["text"] == text:
                return t
        item = {"text": text, "done": False, "ts": time.time()}
        self.todos.append(item)
        if len(self.todos) > config.MAX_TODOS:
            # 先清已完成，还不够再丢最老的
            self.todos = [t for t in self.todos if not t["done"]] \
                or self.todos[-config.MAX_TODOS:]
        self.save()
        logger.info(f"[Tasks] 新增待办: {text}")
        return item

    def open_todos(self) -> list:
        """未完成的待办（按加入顺序）"""
        return [t for t in self.todos if not t["done"]]

    def complete_todo(self, index: int) -> dict | None:
        """完成第 index 条未完成待办（1 起始）；越界返回 None"""
        open_items = self.open_todos()
        if not 1 <= index <= len(open_items):
            return None
        item = open_items[index - 1]
        item["done"] = True
        self.save()
        logger.info(f"[Tasks] 完成待办: {item['text']}")
        return item

    def remove_todo(self, index: int) -> dict | None:
        """删除第 index 条待办（1 起始，按当前列表顺序）；越界返回 None"""
        if not 1 <= index <= len(self.todos):
            return None
        item = self.todos.pop(index - 1)
        self.save()
        logger.info(f"[Tasks] 删除待办: {item['text']}")
        return item

    # ---- 按内容回退（撤销这一步用；序号会随列表变动，只有内容靠得住）----
    def _todo_index(self, text: str, done: bool) -> int:
        """最后一条 (内容, 完成状态) 匹配的待办序号（1 起始），没有返回 0。

        从后往前找：回退要撤的是最靠近的那一次改动，不是历史里的同名老条目。
        """
        for i in range(len(self.todos) - 1, -1, -1):
            if self.todos[i]["text"] == text and bool(self.todos[i]["done"]) == done:
                return i + 1
        return 0

    def reopen_todo(self, text: str) -> dict | None:
        """把最近一条同名的已完成待办改回未完成（回退「划掉」这一步）"""
        index = self._todo_index(text, True)
        if not index:
            return None
        item = self.todos[index - 1]
        item["done"] = False
        self.save()
        logger.info(f"[Tasks] 重新打开待办: {text}")
        return item

    def remove_todo_by_text(self, text: str) -> dict | None:
        """删掉最近一条同名的未完成待办（回退「新加待办」这一步）"""
        index = self._todo_index(text, False)
        return self.remove_todo(index) if index else None

    def remove_reminder_by_text(self, text: str) -> dict | None:
        """删掉最近一条同名的提醒（回退「设提醒」这一步）"""
        for i in range(len(self.reminders) - 1, -1, -1):
            if self.reminders[i].get("text") == text:
                return self.remove_reminder(i + 1)
        return None

    def remove_delivery_by_path(self, path: str) -> dict | None:
        """删掉指向这个路径的交付记录（回退「生成文件」这一步，只删记录）"""
        for i in range(len(self.deliveries) - 1, -1, -1):
            if self.deliveries[i].get("path") == path:
                return self.remove_delivery(i + 1)
        return None

    def clear_done(self):
        """清掉已完成条目"""
        self.todos = [t for t in self.todos if not t["done"]]
        self.save()

    # ------------------------------------------------------------
    # 交付记录（凌零生成过的文件）
    # ------------------------------------------------------------
    def add_delivery(self, name: str, path: str, kind: str) -> dict:
        """记录一次交付（生成文档成功后调用），同名重复路径不重复记"""
        for d in self.deliveries:
            if d.get("path") == path:
                return d
        item = {"name": (name or os.path.basename(path))[:120],
                "path": path, "kind": (kind or "")[:20], "ts": time.time()}
        self.deliveries.append(item)
        if len(self.deliveries) > config.MAX_DELIVERIES:
            self.deliveries = self.deliveries[-config.MAX_DELIVERIES:]
        self.save()
        return item

    def remove_delivery(self, index: int) -> dict | None:
        """删除第 index 条交付记录（1 起始，只删记录不动文件）；越界返回 None"""
        if not 1 <= index <= len(self.deliveries):
            return None
        item = self.deliveries.pop(index - 1)
        self.save()
        return item

    # ------------------------------------------------------------
    # 工作日志（凌零替用户做过的事，数字员工汇报用）
    # ------------------------------------------------------------
    def log_work(self, kind: str, text: str):
        """追加一条工作日志（文本超长截断，超量丢最老）"""
        self.worklog.append({"ts": time.time(),
                             "kind": (kind or "misc")[:20],
                             "text": (text or "").strip()
                             [:config.REMINDER_TEXT_MAX]})
        if len(self.worklog) > config.MAX_WORKLOG:
            self.worklog = self.worklog[-config.MAX_WORKLOG:]
        self.save()

    def today_worklog(self, now: float = None) -> list:
        """今天（本地时区）的日志条目"""
        lt = time.localtime(now if now is not None else time.time())
        day_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                                 0, 0, 0, 0, 0, -1))
        return [w for w in self.worklog if w.get("ts", 0) >= day_start]

    # ------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------
    def save(self):
        try:
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump({"reminders": self.reminders, "todos": self.todos,
                           "deliveries": self.deliveries,
                           "worklog": self.worklog},
                          f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning(f"[Tasks] 保存失败: {e}")

    def load(self):
        if not os.path.exists(self._file):
            return
        try:
            with open(self._file, encoding="utf-8") as f:
                data = json.load(f)
            self.reminders = [r for r in data.get("reminders", [])
                              if isinstance(r, dict) and r.get("text")]
            self.todos = [t for t in data.get("todos", [])
                          if isinstance(t, dict) and t.get("text")]
            self.deliveries = [d for d in data.get("deliveries", [])
                               if isinstance(d, dict) and d.get("path")]
            self.worklog = [w for w in data.get("worklog", [])
                            if isinstance(w, dict) and w.get("text")]
            logger.info(f"[Tasks] 已载入 {len(self.reminders)} 条提醒、"
                        f"{len(self.todos)} 条待办、"
                        f"{len(self.deliveries)} 条交付、"
                        f"{len(self.worklog)} 条日志")
        except (OSError, ValueError) as e:
            logger.warning(f"[Tasks] 加载失败: {e}")
            self.reminders, self.todos = [], []
            self.deliveries, self.worklog = [], []
