# -*- coding: utf-8 -*-
"""聊天输入条 + 聊天记录面板（两个独立窗口）

交互约定：
- InputDialog：单击角色弹出，紧凑输入条，始终跟随角色（毛玻璃半透明）
  * 回车或点「发送」提交指令；输入为空时回车直接关闭不执行任何操作
  * Esc / 失焦关闭；左侧「记录」按钮请求打开聊天记录面板
- HistoryPanel：独立的聊天记录窗口（与输入条分离）
  * 按会话分组，超过 10 分钟算新会话并显示分隔标题
  * 双击某条记录可把内容填回输入条重新发送
  * 每条记录带「复制」「删除」按钮；删除按轮删除（问+答一起删）
  * 顶部「清空」清空并持久化；关闭仅隐藏，数据不受影响
  * 超长回复默认折叠（只显示开头一段），点「展开全文」看完整内容
"""

import time

from PyQt5.QtCore import QPointF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QGraphicsDropShadowEffect, QApplication,
)

from framework import config
from framework.utils import resource_path

# ------------------------------------------------------------
# 程序化绘制的头像（不依赖系统 emoji 字体，避免"👤"显示不出来的问题）
# ------------------------------------------------------------
_AVATAR_CACHE = {}


def _avatar(kind: str, size: int = 22) -> QPixmap:
    """kind: 'user' 绿色人像 / 'pet' 橘色猫脸"""
    key = (kind, size)
    if key in _AVATAR_CACHE:
        return _AVATAR_CACHE[key]

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)

    if kind == "user":
        # 绿色圆底 + 白色人像（头 + 肩）
        p.setBrush(QColor(107, 143, 90))
        p.drawEllipse(0, 0, size, size)
        p.setBrush(QColor(255, 255, 255))
        c = size / 2
        p.drawEllipse(QPointF(c, size * 0.38), size * 0.17, size * 0.17)
        p.drawEllipse(QPointF(c, size * 1.02), size * 0.30, size * 0.26)
    else:
        # 橘色圆底 + 猫耳 + 眼睛 + 鼻子
        p.setBrush(QColor(255, 178, 92))
        p.drawEllipse(0, 0, size, size)
        p.setBrush(QColor(232, 150, 60))
        for sx in (-1, 1):
            ear = QPainterPath()
            ear.moveTo(size * (0.5 + sx * 0.32), size * 0.24)
            ear.lineTo(size * (0.5 + sx * 0.44), size * 0.02)
            ear.lineTo(size * (0.5 + sx * 0.12), size * 0.12)
            ear.closeSubpath()
            p.drawPath(ear)
        p.setBrush(QColor(62, 46, 36))
        eye_y = size * 0.48
        p.drawEllipse(QPointF(size * 0.34, eye_y), size * 0.06, size * 0.07)
        p.drawEllipse(QPointF(size * 0.66, eye_y), size * 0.06, size * 0.07)
        p.setBrush(QColor(240, 130, 120))
        p.drawEllipse(QPointF(size * 0.5, size * 0.66), size * 0.05, size * 0.04)

    p.end()
    _AVATAR_CACHE[key] = pm
    return pm


# 输入条尺寸 / 输入行高度
INPUT_WIDTH = 610
INPUT_ROW_HEIGHT = 52
# 记录面板尺寸（09-19 再扩大：长宽约为最初的 1.5 倍，字体同步放大，
# 长回复/代码完整显示，靠列表滚动查看）
PANEL_WIDTH = 840
PANEL_HEIGHT = 960
# 超过这个字数的回复默认折叠，只显示开头 + 「展开全文」按钮
FOLD_TEXT_LEN = 140
# 正文标签固定宽度：条目可用宽 756（面板 840 - 外边距 72 - 行内边距 12），
# 用户行还要让出头像 28 + 间距 8，取 716 各方都放得下。
# 固定宽度让换行宽度变成确定值（QSS 内边距 26 + 边框 2 占 28 → 正文 688），
# 高度按同一条宽度计算，彻底消除"算的高度比实际渲染矮 → 半行字被裁"的问题。
CONTENT_LABEL_W = 716
CONTENT_TEXT_W = CONTENT_LABEL_W - 28


def _load_qss(widget: QWidget):
    try:
        with open(resource_path("ui/style.qss"), encoding="utf-8") as f:
            widget.setStyleSheet(f.read())
    except OSError:
        pass


# ============================================================
# 输入条
# ============================================================
class InputDialog(QWidget):
    """跟随角色的毛玻璃输入条（记录面板由此分离，点「记录」打开）"""

    # 用户按回车提交了非空指令
    command_submitted = pyqtSignal(str)
    # 用户请求打开聊天记录面板
    history_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._init_ui()
        _load_qss(self)

    def _init_ui(self):
        self._panel = QWidget(self)
        self._panel.setObjectName("inputPanel")
        row = QHBoxLayout(self._panel)
        row.setContentsMargins(14, 8, 10, 8)
        row.setSpacing(8)

        # 「历史记录」按钮：打开聊天记录面板（与输入框等高，竖边加长）
        self._history_btn = QPushButton("历史记录", self._panel)
        self._history_btn.setObjectName("barButton")
        self._history_btn.setCursor(Qt.PointingHandCursor)
        self._history_btn.setFocusPolicy(Qt.NoFocus)   # 不抢焦点，避免输入条失焦自关
        self._history_btn.setFixedHeight(INPUT_ROW_HEIGHT)
        self._history_btn.clicked.connect(self.history_requested.emit)
        row.addWidget(self._history_btn)

        # 输入框
        self._edit = QLineEdit(self._panel)
        self._edit.setObjectName("inputEdit")
        self._edit.setPlaceholderText("想让我做什么？回车发送～")
        self._edit.setFixedHeight(INPUT_ROW_HEIGHT)
        self._edit.returnPressed.connect(self._on_submit)
        row.addWidget(self._edit, 1)

        # 发送按钮
        self._send_btn = QPushButton("发送", self._panel)
        self._send_btn.setObjectName("sendButton")
        self._send_btn.setCursor(Qt.PointingHandCursor)
        self._send_btn.setFocusPolicy(Qt.NoFocus)
        self._send_btn.setFixedHeight(INPUT_ROW_HEIGHT)   # 与输入框等高，竖边加长
        self._send_btn.clicked.connect(self._on_submit)
        row.addWidget(self._send_btn)

        wrapper = QVBoxLayout(self)
        wrapper.setContentsMargins(8, 8, 8, 8)
        wrapper.addWidget(self._panel)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(80, 60, 40, 90))
        self._panel.setGraphicsEffect(shadow)

        self.setFixedSize(INPUT_WIDTH, 84)

    # ============================================================
    # 显示 / 跟随
    # ============================================================
    def popup_at(self, pet_x: int, pet_y: int):
        """在角色上方弹出并聚焦"""
        self._place(pet_x, pet_y)
        self._edit.clear()
        self.show()
        self.raise_()
        self._edit.setFocus()

    def set_text(self, text: str):
        """填入文本并定位光标（供记录面板「重新发送」使用）"""
        self._edit.setText(text)
        self._edit.setFocus()
        self._edit.setCursorPosition(len(text))

    def follow(self, pet_x: int, pet_y: int):
        if self.isVisible():
            self._place(pet_x, pet_y)

    def _place(self, pet_x: int, pet_y: int):
        """锚定在角色上方（超出屏幕上/右边界时自动内收）"""
        screen = QApplication.primaryScreen().availableGeometry()
        x = pet_x + config.PET_SIZE // 2 - self.width() // 2
        x = max(screen.left() + 4,
                min(x, screen.right() - self.width() - 4))
        y = pet_y - self.height() - 10
        if y < screen.top() + 4:                 # 上方放不下就放下方
            y = pet_y + config.PET_SIZE + 10
        self.move(x, y)

    # ============================================================
    # 提交
    # ============================================================
    def _on_submit(self):
        text = self._edit.text().strip()
        if not text:
            self.hide()
            return
        self.hide()
        self.command_submitted.emit(text)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.hide()


# ============================================================
# 聊天记录面板（独立窗口）
# ============================================================
class HistoryPanel(QWidget):
    """独立于输入条的聊天记录窗口，随时开关、跟随角色"""

    # 双击某条记录 -> 请求把内容填回输入条
    resend = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._memory = None
        self._pet_pos = None
        self._expanded = set()      # 已展开的长回复 (pair_index, role)，刷新不丢
        self._init_ui()
        _load_qss(self)

    def _init_ui(self):
        self._panel = QWidget(self)
        self._panel.setObjectName("historyPanel")
        outer = QVBoxLayout(self._panel)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        # ---- 标题行 ----
        head = QHBoxLayout()
        title = QLabel("💬 聊天记录")
        title.setObjectName("historyTitle")
        head.addWidget(title)
        head.addStretch()
        self._count_label = QLabel("共 0 条")
        self._count_label.setObjectName("historyCount")
        head.addWidget(self._count_label)
        self._clear_btn = QPushButton("清空")
        self._clear_btn.setObjectName("historyClear")
        self._clear_btn.setCursor(Qt.PointingHandCursor)
        self._clear_btn.clicked.connect(self._clear_history)
        head.addWidget(self._clear_btn)
        close = QPushButton("✕")
        close.setObjectName("historyClose")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.hide)
        head.addWidget(close)
        outer.addLayout(head)

        # ---- 记录列表 ----
        self._list = QListWidget(self._panel)
        self._list.setObjectName("historyList")
        self._list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self._list.itemDoubleClicked.connect(self._on_item_picked)
        outer.addWidget(self._list, 1)

        # ---- 底部提示 ----
        tip = QLabel("双击可重新发送 · 长回复点「展开全文」 · 每条可复制 / 删除")
        tip.setObjectName("historyTip")
        tip.setAlignment(Qt.AlignCenter)
        outer.addWidget(tip)

        wrapper = QVBoxLayout(self)
        wrapper.setContentsMargins(8, 8, 8, 8)
        wrapper.addWidget(self._panel)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(80, 60, 40, 100))
        self._panel.setGraphicsEffect(shadow)

        self.setFixedSize(PANEL_WIDTH, PANEL_HEIGHT)

    # ============================================================
    # 显示 / 跟随（默认停在角色侧面，不挡输入条）
    # ============================================================
    def popup_at(self, pet_x: int, pet_y: int):
        self._place(pet_x, pet_y)
        self.show()
        self.raise_()
        self._list.scrollToBottom()

    def follow(self, pet_x: int, pet_y: int):
        if self.isVisible():
            self._place(pet_x, pet_y)

    def _place(self, pet_x: int, pet_y: int):
        """锚定在角色侧面（右侧优先，放不下换左侧，上下内收）"""
        self._pet_pos = (pet_x, pet_y)
        screen = QApplication.primaryScreen().availableGeometry()
        x = pet_x + config.PET_SIZE + 14
        if x + self.width() > screen.right() - 4:
            x = pet_x - self.width() - 14
        x = max(screen.left() + 4, min(x, screen.right() - self.width() - 4))
        y = pet_y + config.PET_SIZE // 2 - self.height() // 2
        y = max(screen.top() + 4, min(y, screen.bottom() - self.height() - 4))
        self.move(x, y)

    # ============================================================
    # 历史记录
    # ============================================================
    def bind_memory(self, memory):
        """绑定记忆系统，用于读取对话记录"""
        self._memory = memory
        self.refresh_history()

    def refresh_history(self):
        """刷新历史记录列表（数据来自记忆系统，重启后仍在）"""
        pairs = self._memory.get_pairs() if self._memory else []
        self._count_label.setText(f"共 {len(pairs)} 条")
        self._list.clear()
        for idx, p in enumerate(pairs):
            if p["new_session"]:
                self._add_session_header(p["ts"])
            self._add_item("user", p["user"], p["ts"], idx)
            if p["reply"]:
                self._add_item("pet", p["reply"], p["ts"], idx)
        if self.isVisible():
            self._list.scrollToBottom()

    def _add_session_header(self, ts: float):
        item = QListWidgetItem(self._list)
        item.setFlags(Qt.NoItemFlags)
        label = QLabel(self._fmt_time(ts, full=True) + " · 新的对话")
        label.setObjectName("sessionHeader")
        item.setSizeHint(QSize(PANEL_WIDTH - 72, 36))
        self._list.addItem(item)
        self._list.setItemWidget(item, label)

    def _add_item(self, role: str, text: str, ts: float, pair_index: int,
                  row: int = None):
        """添加（或在 row 处替换）一条记录。

        超过 FOLD_TEXT_LEN 的内容默认折叠：只显示开头一段 + 「展开全文」按钮，
        点开后本次会话内保持展开（刷新/新消息不打断）。
        """
        item = QListWidgetItem(self._list)
        item.setData(Qt.UserRole, text)
        item.setToolTip(text[:200] + ("…" if len(text) > 200 else ""))

        w = QWidget()
        w.setObjectName("historyItem")
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(8)
        if role != "user":
            h.addStretch()                       # 凌零的靠右，形成对话感

        box = QVBoxLayout()
        box.setSpacing(2)
        top = QHBoxLayout()
        name = QLabel("我" if role == "user" else "凌零")
        name.setObjectName("historyNameUser" if role == "user"
                           else "historyNamePet")
        t = QLabel(self._fmt_time(ts))
        t.setObjectName("historyTime")
        top.addWidget(name)
        top.addStretch()
        top.addWidget(t)

        # 「复制」「删除」按钮（删除按轮删除：问+答一起删）
        copy_btn = QPushButton("复制")
        copy_btn.setObjectName("historyItemBtn")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.setFocusPolicy(Qt.NoFocus)
        copy_btn.clicked.connect(
            lambda _=False, txt=text, b=copy_btn: self._copy_text(txt, b))
        top.addWidget(copy_btn)
        del_btn = QPushButton("删除")
        del_btn.setObjectName("historyItemBtn")
        del_btn.setProperty("role", "delete")
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setFocusPolicy(Qt.NoFocus)
        del_btn.clicked.connect(
            lambda _=False, i=pair_index: self._delete_pair(i))
        top.addWidget(del_btn)
        box.addLayout(top)

        # 长内容折叠：默认只显示开头，点按钮展开/收起（存完整原文可随时复原）
        full = text or "…"
        expanded = (pair_index, role) in self._expanded
        folded = len(full) > FOLD_TEXT_LEN and not expanded
        content = QLabel(full[:FOLD_TEXT_LEN] + "……" if folded else full)
        content.setObjectName("historyTextUser" if role == "user"
                              else "historyTextPet")
        content.setWordWrap(True)
        content.setAlignment(Qt.AlignLeft | Qt.AlignTop)   # 文字贴顶，底距可预期
        content.setFixedWidth(CONTENT_LABEL_W)   # 换行宽度确定，高度才算得准
        box.addWidget(content)

        has_fold_btn = len(full) > FOLD_TEXT_LEN
        if has_fold_btn:
            fold_btn = QPushButton("▴ 收起" if expanded else "▸ 展开全文")
            fold_btn.setObjectName("historyItemBtn")
            fold_btn.setCursor(Qt.PointingHandCursor)
            fold_btn.setFocusPolicy(Qt.NoFocus)
            fold_btn.clicked.connect(
                lambda _=False: self._toggle_fold(role, text, ts,
                                                  pair_index, item))
            fold_row = QHBoxLayout()
            fold_row.addWidget(fold_btn)
            fold_row.addStretch()
            box.addLayout(fold_row)

        h.addLayout(box)
        if role == "user":
            icon = QLabel()
            icon.setPixmap(_avatar("user", 28))
            icon.setFixedSize(28, 28)
            h.addWidget(icon, 0, Qt.AlignTop)

        # 条目宽度 = 面板 840 - 面板内边距等 72；总高 = 名称行 30
        # + 气泡真实高度 + （展开按钮一行）+ 条目上下留白 20
        item_w = PANEL_WIDTH - 72
        height = 30 + self._content_height(content, CONTENT_TEXT_W) + 20
        if has_fold_btn:
            height += 26
        item.setSizeHint(QSize(item_w, height))
        if row is None:
            self._list.addItem(item)
        else:
            self._list.insertItem(row, item)
        self._list.setItemWidget(item, w)

    def _toggle_fold(self, role: str, text: str, ts: float,
                     pair_index: int, item: QListWidgetItem):
        """展开/收起一条长回复（延迟到点击事件结束后再重建该行）"""
        key = (pair_index, role)
        if key in self._expanded:
            self._expanded.discard(key)
        else:
            self._expanded.add(key)
        QTimer.singleShot(
            0, lambda: self._replace_row(item, role, text, ts, pair_index))

    def _replace_row(self, item: QListWidgetItem, role: str, text: str,
                     ts: float, pair_index: int):
        """原位重建一条记录（高度随展开/收起变化），行号失效则整体刷新"""
        row = self._list.row(item)
        if row < 0:
            self.refresh_history()
            return
        self._list.takeItem(row)
        self._add_item(role, text, ts, pair_index, row=row)
        new_item = self._list.item(row)
        if new_item is not None:
            self._list.scrollToItem(new_item)

    @staticmethod
    def _content_height(content: QLabel, text_width: int) -> int:
        """
        计算内容气泡的真实排版高度（不封顶，长代码也能完整显示）。

        三重防裁字：
        1. ensurePolished 让 QSS 字体（19px）先生效，按默认字体量必然偏矮；
        2. QLabel 自身 heightForWidth 与字体度量 boundingRect 两种算法取较大值
          （前者是 Qt 排版引擎的真实答案，后者覆盖前者拿不到的情形）；
        3. 在内边距之外再加 10px 安全余量——宁可底部多留白，绝不裁掉半行字。
        """
        content.ensurePolished()
        fm = content.fontMetrics()
        h_fm = fm.boundingRect(0, 0, max(60, text_width), 1000000,
                               Qt.TextWordWrap, content.text()).height()
        h_hw = content.heightForWidth(max(60, text_width))
        h = max(h_fm, h_hw or 0)
        if h <= 0:                           # 兜底估算
            t = content.text()
            chars = max(1, text_width // 10)  # 半角字符宽约 10px（19px 字号）
            h = max(1, -(-len(t) // chars)) * 27
        # QSS 气泡内边距与边框 20px + 安全余量 10px
        return h + 30

    @staticmethod
    def _fmt_time(ts, full: bool = False) -> str:
        if not ts:
            return ""
        lt = time.localtime(ts)
        return (time.strftime("%m-%d %H:%M", lt) if full
                else time.strftime("%H:%M", lt))

    def _on_item_picked(self, item):
        """双击记录：请求把内容填回输入条"""
        text = item.data(Qt.UserRole)
        if text:
            self.resend.emit(text)

    def _copy_text(self, text: str, btn: QPushButton):
        """复制该条记录内容到剪贴板，按钮短暂变为「已复制」"""
        QApplication.clipboard().setText(text)
        btn.setText("已复制")
        btn.setEnabled(False)
        QTimer.singleShot(1200, lambda: self._restore_copy_btn(btn))

    @staticmethod
    def _restore_copy_btn(btn: QPushButton):
        try:
            btn.setText("复制")
            btn.setEnabled(True)
        except RuntimeError:
            pass    # 列表已刷新/面板已关闭，按钮控件已销毁

    def _delete_pair(self, pair_index: int):
        """删除第 pair_index 轮对话（问+答一起），并刷新列表"""
        if self._memory and self._memory.delete_pair(pair_index):
            self.refresh_history()

    def _clear_history(self):
        """清空保存的记录"""
        if self._memory:
            self._memory.clear()
            self.refresh_history()
