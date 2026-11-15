# -*- coding: utf-8 -*-
"""凌零 · 工作台：可点开、可最大化、可记住尺寸的标准主窗口。

桌宠本体是 185px 的无边框角色，适合常驻桌面；但它不适合读长回复、
看偏好/规则/轨迹表、翻交付清单。这里补一个真正的 QMainWindow：

  * 标准窗口边框 + 系统最小化/最大化/关闭按钮，双击标题栏也能最大化；
  * 显眼的「⛶ 最大化 / 还原」按钮，首次打开默认最大化；
  * 记住窗口尺寸、最大化状态、深浅色主题；
  * 关闭只隐藏窗口、不退出程序（托盘常驻），下次点开立刻回来；
  * 左侧导航六个页面，数据全部来自 Framework 的真实 Store，不放假数据。

数据口径：
  对话  -> fw.memory.history（信号 history_changed 自动刷新）
  偏好  -> fw.prefs.all()
  规则  -> fw.rules.all()
  轨迹  -> fw.trajectory.turns()/events()/summarize()
  交付  -> fw.tasks.deliveries
所有新能力都可能因降级而为 None，页面要给出友好提示而不是崩掉。
"""

import html
import os
import sys
import time
from pathlib import Path

from PyQt5.QtCore import Qt, QSettings, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QTextCursor
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QPushButton, QStackedWidget, QStatusBar, QTableWidget,
    QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from framework import config
from framework.utils import app_data_dir, logger

# ------------------------------------------------------------
# 主题：用占位符替换生成 QSS，避免 f-string 把 QSS 的花括号当占位符
# ------------------------------------------------------------
_LIGHT = dict(
    BG="#F4F7FA", CARD="#FFFFFF", BORDER="#DCE6EE", TEXT="#26303A",
    MUTED="#6B7C8C", NAV="#0F2B46", NAV_TEXT="#E8F1F8",
    NAV_ACTIVE="#1B6B9A", ACCENT="#1B6B9A", ACCENT_SOFT="#E6F1FB",
    GOOD="#0F6E56", BAD="#A32D2D", ROW_ALT="#F7FAFC", INPUT="#FFFFFF",
    BUBBLE_ME="#1B6B9A", BUBBLE_ME_TEXT="#FFFFFF", BUBBLE_AI="#EDF3F8",
)
_DARK = dict(
    BG="#141A21", CARD="#1C2530", BORDER="#2C3A48", TEXT="#E8EEF4",
    MUTED="#9AAEC0", NAV="#0C141C", NAV_TEXT="#D7E3EE",
    NAV_ACTIVE="#1D9E75", ACCENT="#378ADD", ACCENT_SOFT="#1B3247",
    GOOD="#5DCAA5", BAD="#F09595", ROW_ALT="#202A36", INPUT="#161E27",
    BUBBLE_ME="#1D9E75", BUBBLE_ME_TEXT="#FFFFFF", BUBBLE_AI="#263242",
)

_QSS_TEMPLATE = """
QMainWindow, QWidget#central { background: __BG__; }
QWidget#navPanel { background: __NAV__; border: none; }
QLabel#brand { color: __NAV_TEXT__; font-size: 21px; font-weight: 500; padding: 4px 2px; }
QLabel#brandSub { color: __MUTED__; font-size: 12px; padding-bottom: 10px; }
QLabel#navStat { color: __NAV_TEXT__; font-size: 12px; line-height: 1.7; }
QPushButton#navButton {
    color: __NAV_TEXT__; background: transparent; border: none;
    border-radius: 8px; padding: 11px 14px; text-align: left;
    font-size: 14px; font-family: "Microsoft YaHei", sans-serif;
}
QPushButton#navButton:hover { background: __ACCENT_SOFT__; }
QPushButton#navButton:checked { background: __NAV_ACTIVE__; color: #FFFFFF; }
QWidget#toolBar { background: __CARD__; border-bottom: 1px solid __BORDER__; }
QLabel#pageTitle { color: __TEXT__; font-size: 17px; font-weight: 500; }
QLabel#hintLabel { color: __MUTED__; font-size: 12px; }
QPushButton#primaryButton {
    background: __ACCENT__; color: #FFFFFF; border: none;
    border-radius: 7px; padding: 8px 16px; font-size: 13px;
}
QPushButton#primaryButton:hover { background: __NAV_ACTIVE__; }
QPushButton#ghostButton {
    background: transparent; color: __TEXT__; border: 1px solid __BORDER__;
    border-radius: 7px; padding: 7px 14px; font-size: 13px;
}
QPushButton#ghostButton:hover { background: __ACCENT_SOFT__; }
QWidget#pageCard { background: __CARD__; border: 1px solid __BORDER__;
                   border-radius: 12px; }
QTextBrowser#chatView, QTextBrowser#detailView {
    background: __BG__; color: __TEXT__; border: 1px solid __BORDER__;
    border-radius: 9px; font-size: 14px; padding: 8px;
    font-family: "Microsoft YaHei", sans-serif;
}
QLineEdit#chatInput {
    background: __INPUT__; color: __TEXT__; border: 1px solid __BORDER__;
    border-radius: 9px; padding: 10px 14px; font-size: 14px;
}
QLineEdit#chatInput:focus { border: 1.5px solid __ACCENT__; }
QTableWidget#dataTable {
    background: __CARD__; color: __TEXT__; border: 1px solid __BORDER__;
    border-radius: 9px; gridline-color: __BORDER__; font-size: 13px;
    alternate-background-color: __ROW_ALT__;
}
QTableWidget#dataTable QHeaderView::section {
    background: __ACCENT_SOFT__; color: __TEXT__; border: none;
    border-bottom: 1px solid __BORDER__; padding: 8px 6px; font-size: 13px;
}
QListWidget#deliveryList {
    background: __CARD__; color: __TEXT__; border: 1px solid __BORDER__;
    border-radius: 9px; font-size: 14px; padding: 6px;
}
QStatusBar#statusBar { background: __CARD__; color: __MUTED__;
                       border-top: 1px solid __BORDER__; font-size: 12px; }
"""


def _qss(palette: dict) -> str:
    out = _QSS_TEMPLATE
    for key, value in palette.items():
        out = out.replace("__%s__" % key, value)
    return out


PAGE_DEFS = [
    ("chat", "对话", "在这里直接吩咐凌零；回复与桌宠气泡同步"),
    ("prefs", "我的偏好", "聊着聊着自动记下的喜好，改主意会覆盖旧值"),
    ("rules", "学到的规则", "使用中遇到的问题归纳出的调整，连续失效会自动停用"),
    ("trace", "执行轨迹", "每轮的状态哈希链、动作与失败；可生成时间线"),
    ("deliveries", "我的交付", "生成过的文档与图表，双击打开"),
    ("about", "关于与设置", "窗口操作、开关状态与快捷入口"),
]


def _cell(text) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
    return item


class WorkspaceWindow(QMainWindow):
    """可最大化的主工作台。关闭 = 隐藏，程序继续留在托盘。"""

    workspace_closed = pyqtSignal()
    settings_requested = pyqtSignal()

    def __init__(self, fw, parent=None, settings_path: str = None):
        super().__init__(parent)
        self.fw = fw
        self.setWindowTitle("凌零 · 工作台")
        # 标准窗口：保留系统标题栏与最小化/最大化/关闭按钮
        self.setWindowFlags(Qt.Window)
        self.setMinimumSize(960, 600)
        self.resize(1180, 780)

        self._settings = QSettings(
            settings_path or os.path.join(app_data_dir(), "workspace.ini"),
            QSettings.IniFormat)
        self._theme = self._settings.value("theme", "light")
        if self._theme not in ("light", "dark"):
            self._theme = "light"
        self._max_state = False
        self._first_open = True
        self._timeline_path = os.path.join(app_data_dir(), "timeline.html")

        self._build_ui()
        self._connect_framework()
        self._restore_state()
        self._apply_theme(self._theme)
        self.switch_page(0)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(4000)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

    # ------------------------------------------------------------
    # 构建界面
    # ------------------------------------------------------------
    def _build_ui(self):
        central = QWidget(self)
        central.setObjectName("central")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_nav())

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        rv.addWidget(self._build_toolbar())

        self._stack = QStackedWidget()
        self._stack.setObjectName("pageStack")
        self._page_widgets = {}
        self._page_refresh = {}
        for key, title, hint in PAGE_DEFS:
            widget, refresh = getattr(self, "_build_%s_page" % key)(hint)
            self._stack.addWidget(widget)
            self._page_widgets[key] = widget
            self._page_refresh[key] = refresh

        holder = QWidget()
        hv = QHBoxLayout(holder)
        hv.setContentsMargins(18, 16, 18, 16)
        hv.addWidget(self._stack)
        rv.addWidget(holder, 1)

        self._status = QStatusBar(self)
        self._status.setObjectName("statusBar")
        self._status_label = QLabel("就绪")
        self._status.addWidget(self._status_label)
        rv.addWidget(self._status)

        root.addWidget(right, 1)
        self.setCentralWidget(central)

    def _build_nav(self) -> QWidget:
        nav = QWidget()
        nav.setObjectName("navPanel")
        nav.setFixedWidth(210)
        v = QVBoxLayout(nav)
        v.setContentsMargins(18, 22, 14, 18)
        v.setSpacing(4)

        brand = QLabel("凌零 · 工作台")
        brand.setObjectName("brand")
        sub = QLabel("桌面 AI 数字员工")
        sub.setObjectName("brandSub")
        v.addWidget(brand)
        v.addWidget(sub)

        group = QButtonGroup(self)
        group.setExclusive(True)
        self._nav_buttons = []
        for i, (_key, title, _hint) in enumerate(PAGE_DEFS):
            btn = QPushButton(title)
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _checked, idx=i: self.switch_page(idx))
            group.addButton(btn)
            v.addWidget(btn)
            self._nav_buttons.append(btn)
        v.addStretch(1)

        self._nav_stat = QLabel("")
        self._nav_stat.setObjectName("navStat")
        self._nav_stat.setWordWrap(True)
        v.addWidget(self._nav_stat)
        return nav

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("toolBar")
        bar.setFixedHeight(62)
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 10, 18, 10)
        h.setSpacing(10)

        self._title = QLabel("对话")
        self._title.setObjectName("pageTitle")
        h.addWidget(self._title)
        h.addStretch(1)

        self._theme_btn = self._ghost("◐ 深色", self.toggle_theme)
        h.addWidget(self._theme_btn)
        h.addWidget(self._ghost("⟳ 刷新", self.refresh_current))
        self._maximize_btn = QPushButton("⛶ 最大化")
        self._maximize_btn.setObjectName("primaryButton")
        self._maximize_btn.setCursor(Qt.PointingHandCursor)
        self._maximize_btn.clicked.connect(self.toggle_maximize)
        h.addWidget(self._maximize_btn)
        h.addWidget(self._ghost("← 回到桌宠", self.hide_to_pet))
        return bar

    def _ghost(self, text, slot) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("ghostButton")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    def _button_row(self, *buttons) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 8, 0, 0)
        h.setSpacing(8)
        for b in buttons:
            h.addWidget(b)
        h.addStretch(1)
        return row

    def _page_shell(self, hint: str):
        page = QWidget()
        page.setObjectName("pageCard")
        v = QVBoxLayout(page)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(10)
        label = QLabel(hint)
        label.setObjectName("hintLabel")
        label.setWordWrap(True)
        v.addWidget(label)
        return page, v

    @staticmethod
    def _table(headers) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setObjectName("dataTable")
        t.setHorizontalHeaderLabels(headers)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setAlternatingRowColors(True)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        return t

    # ---------------- 页面：对话 ----------------
    def _build_chat_page(self, hint):
        page, v = self._page_shell(hint)
        self._chat_view = QTextBrowser()
        self._chat_view.setObjectName("chatView")
        self._chat_view.setOpenExternalLinks(False)
        v.addWidget(self._chat_view, 1)

        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self._chat_input = QLineEdit()
        self._chat_input.setObjectName("chatInput")
        self._chat_input.setPlaceholderText("想让我做什么？回车发送")
        self._chat_input.returnPressed.connect(self._send)
        h.addWidget(self._chat_input, 1)
        send = QPushButton("发送")
        send.setObjectName("primaryButton")
        send.clicked.connect(self._send)
        h.addWidget(send)
        h.addWidget(self._ghost("撤销", lambda: self._quick("撤销")))
        h.addWidget(self._ghost("技能列表", lambda: self._quick("技能列表")))
        v.addWidget(row)
        return page, self._refresh_chat

    def _send(self):
        text = self._chat_input.text().strip()
        if not text:
            return
        self._chat_input.clear()
        self.fw.send_command(text)
        self._refresh_chat()

    def _quick(self, text):
        self.fw.send_command(text)
        self._refresh_chat()

    def _refresh_chat(self):
        history = list(getattr(self.fw.memory, "history", []) or [])[-60:]
        if not history:
            self._chat_view.setHtml(
                '<div style="color:#8899aa;padding:24px;text-align:center;">'
                '还没有对话。在上面输入一句话，或点桌宠直接说。</div>')
            return
        p = _LIGHT if self._theme == "light" else _DARK
        parts = []
        for item in history:
            role = item.get("role", "")
            content = html.escape(str(item.get("content", "")))
            # 这里必须用 f-string：CSS 里的 "76%" 若走 % 格式化会被当成格式符
            if role == "user":
                parts.append(
                    f'<div style="text-align:right;margin:8px 0;">'
                    f'<span style="display:inline-block;max-width:76%;'
                    f'background:{p["BUBBLE_ME"]};color:{p["BUBBLE_ME_TEXT"]};'
                    f'padding:9px 13px;border-radius:12px 12px 3px 12px;'
                    f'text-align:left;">{content}</span></div>')
            else:
                parts.append(
                    f'<div style="text-align:left;margin:8px 0;">'
                    f'<span style="display:inline-block;max-width:76%;'
                    f'background:{p["BUBBLE_AI"]};color:{p["TEXT"]};'
                    f'padding:9px 13px;border-radius:12px 12px 12px 3px;">'
                    f'{content}</span></div>')
        self._chat_view.setHtml("".join(parts))
        self._chat_view.moveCursor(QTextCursor.End)

    # ---------------- 页面：偏好 ----------------
    def _build_prefs_page(self, hint):
        page, v = self._page_shell(hint)
        self._prefs_table = self._table(
            ["偏好", "当前值", "置信度", "命中", "证据数"])
        v.addWidget(self._prefs_table, 1)
        v.addWidget(self._button_row(
            self._ghost("⟳ 刷新", self._refresh_prefs),
            self._ghost("删除选中", self._delete_pref),
            self._ghost("清空全部", self._clear_prefs)))
        return page, self._refresh_prefs

    def _selected_key(self, table, col=0):
        rows = table.selectionModel().selectedRows() if table.selectionModel() else []
        if not rows:
            return None
        item = table.item(rows[0].row(), col)
        return item.text() if item else None

    def _refresh_prefs(self):
        store = getattr(self.fw, "prefs", None)
        items = store.all() if store is not None else []
        self._prefs_table.setRowCount(len(items))
        for i, p in enumerate(items):
            values = [p.get("key", ""), p.get("value", ""),
                      p.get("confidence", ""), p.get("hits", 0),
                      len(p.get("evidence") or [])]
            for j, val in enumerate(values):
                self._prefs_table.setItem(i, j, _cell(val))
        self._set_empty_hint(self._prefs_table, len(items),
                             "还没有学到偏好。说一句「我喜欢深色 PPT」试试。")

    def _delete_pref(self):
        key = self._selected_key(self._prefs_table)
        store = getattr(self.fw, "prefs", None)
        if key and store is not None:
            store.forget(key)
        self._refresh_prefs()

    def _clear_prefs(self):
        store = getattr(self.fw, "prefs", None)
        if store is not None:
            store.clear()
        self._refresh_prefs()

    # ---------------- 页面：规则 ----------------
    def _build_rules_page(self, hint):
        page, v = self._page_shell(hint)
        self._rules_table = self._table(
            ["规则族", "状态", "置信度", "触发", "成功", "失败"])
        v.addWidget(self._rules_table, 1)
        v.addWidget(self._button_row(
            self._ghost("⟳ 刷新", self._refresh_rules),
            self._ghost("删除选中", self._delete_rule)))
        return page, self._refresh_rules

    def _refresh_rules(self):
        store = getattr(self.fw, "rules", None)
        items = store.all() if store is not None else []
        self._rules_table.setRowCount(len(items))
        for i, r in enumerate(items):
            values = [r.get("family", ""),
                      "生效" if r.get("enabled") else "已停用",
                      r.get("confidence", ""), r.get("triggers", 0),
                      r.get("successes", 0), r.get("failures", 0)]
            for j, val in enumerate(values):
                self._rules_table.setItem(i, j, _cell(val))
        self._set_empty_hint(self._rules_table, len(items),
                             "还没有规则。同类问题出现两次后会自动归纳。")

    def _delete_rule(self):
        family = self._selected_key(self._rules_table)
        store = getattr(self.fw, "rules", None)
        if store is None:
            return
        for r in list(store.all()):
            if r.get("family") == family:
                store.forget(r.get("id"))
        self._refresh_rules()

    # ---------------- 页面：轨迹 ----------------
    def _build_trace_page(self, hint):
        page, v = self._page_shell(hint)
        self._trace_table = self._table(
            ["轮次", "事件", "动作", "失败", "tokens", "耗时(ms)"])
        self._trace_table.setMaximumHeight(260)
        self._trace_table.itemSelectionChanged.connect(self._show_turn_detail)
        v.addWidget(self._trace_table)
        self._detail_view = QTextBrowser()
        self._detail_view.setObjectName("detailView")
        v.addWidget(self._detail_view, 1)
        v.addWidget(self._button_row(
            self._ghost("⟳ 刷新", self._refresh_trace),
            self._ghost("生成时间线", self.generate_timeline),
            self._ghost("打开时间线", self._open_timeline)))
        return page, self._refresh_trace

    def _refresh_trace(self):
        traj = getattr(self.fw, "trajectory", None)
        turns = traj.turns() if traj is not None else []
        self._trace_table.setRowCount(len(turns))
        for i, t in enumerate(turns):
            values = [t.get("turn_id", ""), t.get("events", 0),
                      len(t.get("actions") or []), len(t.get("failures") or []),
                      t.get("tokens", 0), t.get("duration_ms", 0)]
            for j, val in enumerate(values):
                self._trace_table.setItem(i, j, _cell(val))
        self._set_empty_hint(self._trace_table, len(turns),
                             "还没有执行轨迹。发一句指令就会产生第一轮。")
        self._show_turn_detail()

    def _show_turn_detail(self):
        traj = getattr(self.fw, "trajectory", None)
        if traj is None:
            self._detail_view.setPlainText("轨迹子系统未启用。")
            return
        turn_id = self._selected_key(self._trace_table)
        if not turn_id:
            s = traj.summarize()
            self._detail_view.setPlainText(
                "共 %d 轮 / %d 事件 / 失败率 %.1f%% / token %d\n哈希链完整：%s"
                % (s["turns"], s["events"], s["failure_rate"] * 100,
                   s["tokens"], "是" if s["chain_ok"] else "否"))
            return
        lines = ["轮次 %s" % turn_id]
        for e in traj.events(turn_id=turn_id):
            lines.append(
                "  #%-2s %-12s %-10s %s"
                % (e.get("seq"), e.get("kind"), e.get("action") or "-",
                   (e.get("note") or "")[:80]))
        self._detail_view.setPlainText("\n".join(lines))

    def generate_timeline(self) -> str:
        """把当前事件流渲染成单文件 HTML；返回路径，失败返回空串。"""
        traj = getattr(self.fw, "trajectory", None)
        if traj is None:
            return ""
        try:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if root not in sys.path:
                sys.path.insert(0, root)
            from tools import trace_view   # tools 是隐式命名空间包
            text = trace_view.render(traj.events(), traj.summarize())
            Path(self._timeline_path).write_text(text, encoding="utf-8")
            return self._timeline_path
        except Exception as e:      # noqa: BLE001 生成失败不影响主界面
            logger.warning(f"[Workspace] 时间线生成失败: {e}")
            return ""

    def _open_timeline(self):
        path = self.generate_timeline()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ---------------- 页面：交付 ----------------
    def _build_deliveries_page(self, hint):
        page, v = self._page_shell(hint)
        self._delivery_list = QListWidget()
        self._delivery_list.setObjectName("deliveryList")
        self._delivery_list.itemDoubleClicked.connect(self._open_delivery)
        v.addWidget(self._delivery_list, 1)
        v.addWidget(self._button_row(
            self._ghost("⟳ 刷新", self._refresh_deliveries),
            self._ghost("打开选中", self._open_delivery)))
        return page, self._refresh_deliveries

    def _refresh_deliveries(self):
        self._delivery_list.clear()
        items = list(getattr(self.fw.tasks, "deliveries", []) or [])
        for d in reversed(items):
            ts = time.strftime("%m-%d %H:%M",
                               time.localtime(d.get("ts", time.time())))
            self._delivery_list.addItem(QListWidgetItem(
                "%s  ·  %s  ·  %s" % (d.get("name", ""),
                                      d.get("kind", ""), ts)))
        if not items:
            self._delivery_list.addItem(QListWidgetItem(
                "还没有交付物。让凌零做一份 PPT / Word / 表格就会出现在这里。"))

    def _open_delivery(self):
        row = self._delivery_list.currentRow()
        items = list(getattr(self.fw.tasks, "deliveries", []) or [])
        idx = len(items) - 1 - row
        if 0 <= idx < len(items):
            path = items[idx].get("path", "")
            if path and os.path.exists(path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ---------------- 页面：关于 ----------------
    def _build_about_page(self, hint):
        page, v = self._page_shell(hint)
        self._about_view = QTextBrowser()
        self._about_view.setObjectName("detailView")
        v.addWidget(self._about_view, 1)
        v.addWidget(self._button_row(
            self._ghost("打开设置窗口", self.settings_requested.emit),
            self._ghost("⟳ 刷新状态", self._refresh_status)))
        self._refresh_about()
        return page, self._refresh_about

    def _refresh_about(self):
        switches = [
            ("事件轨迹", config.TRAJECTORY_ENABLED),
            ("偏好学习", config.PREFERENCE_LEARNING),
            ("自我进化", config.EVOLUTION_ENABLED),
            ("计划依赖图", config.PLAN_DAG_ENABLED),
            ("契约严格模式", config.EFFECT_SPEC_STRICT),
        ]
        rows = "".join(
            "<li>%s：%s</li>" % (html.escape(k), "开启" if v else "关闭")
            for k, v in switches)
        self._about_view.setHtml(
            "<h2>凌零 · 桌面 AI 数字员工</h2>"
            "<p>这个窗口是标准主窗口：可以拖动边框调整大小、双击标题栏最大化、"
            "点右上角或这里的「⛶ 最大化」按钮铺满屏幕。关闭只会隐藏窗口，"
            "桌宠与托盘继续常驻；从桌宠右键菜单、托盘菜单或输入条的「工作台」"
            "按钮都能再打开。</p>"
            "<h3>窗口操作</h3><ul>"
            "<li>⛶ 最大化 / 还原：切换铺满与窗口模式（状态会被记住）</li>"
            "<li>◐ 深色 / 浅色：切换主题（会被记住）</li>"
            "<li>← 回到桌宠：隐藏窗口，回到桌面角色</li>"
            "<li>Esc：关闭窗口（不退出程序）</li></ul>"
            "<h3>当前开关</h3><ul>%s</ul>"
            "<h3>数据位置</h3><p>轨迹、偏好、规则保存在本机数据目录，"
            "均不含用户原话明文；可用环境变量 LINGLING_DATA_DIR 隔离。</p>" % rows)

    # ------------------------------------------------------------
    # 交互与状态
    # ------------------------------------------------------------
    def _set_empty_hint(self, table, count, hint):
        if count == 0 and hasattr(table, "setRowCount"):
            table.setRowCount(1)
            for col in range(table.columnCount()):
                table.setItem(0, col, _cell(hint if col == 0 else ""))

    def _connect_framework(self):
        # 用信号而不是 register_callback：后者是单值表，会覆盖桌宠的气泡回调
        try:
            self.fw.history_changed.connect(self._refresh_chat)
        except Exception as e:      # noqa: BLE001
            logger.warning(f"[Workspace] 对话信号连接失败: {e}")

    def detach(self):
        """测试/销毁前断开信号，避免多个窗口实例重复刷新。"""
        try:
            self.fw.history_changed.disconnect(self._refresh_chat)
        except (TypeError, RuntimeError):
            pass
        self._status_timer.stop()

    def switch_page(self, index: int):
        index = max(0, min(index, len(PAGE_DEFS) - 1))
        self._stack.setCurrentIndex(index)
        self._nav_buttons[index].setChecked(True)
        self._title.setText(PAGE_DEFS[index][1])
        self.refresh_current()

    def refresh_current(self):
        key = PAGE_DEFS[self._stack.currentIndex()][0]
        try:
            self._page_refresh[key]()
        except Exception as e:      # noqa: BLE001 单个页面异常不拖垮整个窗口
            logger.warning(f"[Workspace] 刷新 {key} 页失败: {e}")
        self._refresh_status()

    def _refresh_status(self):
        traj = getattr(self.fw, "trajectory", None)
        prefs = getattr(self.fw, "prefs", None)
        rules = getattr(self.fw, "rules", None)
        s = traj.summarize() if traj is not None else {
            "turns": 0, "events": 0, "failure_rate": 0.0, "tokens": 0,
            "chain_ok": True}
        n_prefs = len(prefs.all()) if prefs is not None else 0
        n_rules = len([r for r in rules.all() if r.get("enabled")]) \
            if rules is not None else 0
        self._status_label.setText(
            "轮次 %d  ·  事件 %d  ·  失败率 %.1f%%  ·  token %d  ·  "
            "偏好 %d  ·  生效规则 %d  ·  哈希链 %s  ·  %s"
            % (s["turns"], s["events"], s["failure_rate"] * 100, s["tokens"],
               n_prefs, n_rules, "完整" if s["chain_ok"] else "断开",
               self.fw.ai_status_text()
               if hasattr(self.fw, "ai_status_text") else ""))
        self._nav_stat.setText(
            "AI：%s\n偏好：%d 条\n生效规则：%d 条\n轨迹：%d 轮"
            % (self.fw.ai_status_text()
               if hasattr(self.fw, "ai_status_text") else "-",
               n_prefs, n_rules, s["turns"]))

    def toggle_theme(self):
        self._apply_theme("dark" if self._theme == "light" else "light")
        self._refresh_chat()
        self._refresh_about()
        self.save_state()

    def _apply_theme(self, theme: str):
        self._theme = theme if theme in ("light", "dark") else "light"
        self.setStyleSheet(_qss(_DARK if self._theme == "dark" else _LIGHT))
        self._theme_btn.setText("☀ 浅色" if self._theme == "dark" else "◐ 深色")

    def _apply_maximize(self, on: bool):
        self._max_state = bool(on)
        if on:
            self.showMaximized()
        else:
            self.showNormal()
        self._maximize_btn.setText("⛶ 还原" if on else "⛶ 最大化")

    def toggle_maximize(self):
        self._apply_maximize(not self._max_state)
        self.save_state()

    def open_workspace(self):
        """从桌宠/托盘点开：首次按记住的偏好自动最大化。"""
        self.show()
        if self._first_open:
            self._first_open = False
            self._apply_maximize(self._maximized_pref)
        self.raise_()
        self.activateWindow()
        self.refresh_current()

    def hide_to_pet(self):
        self.save_state()
        self.hide()
        self.workspace_closed.emit()

    def _restore_state(self):
        try:
            w = int(self._settings.value("width", 1180))
            h = int(self._settings.value("height", 780))
            self.resize(max(960, w), max(600, h))
        except (TypeError, ValueError):
            pass
        self._maximized_pref = str(
            self._settings.value("maximized", "true")).lower() == "true"

    def save_state(self):
        self._settings.setValue("width", self.width())
        self._settings.setValue("height", self.height())
        self._settings.setValue("maximized",
                                "true" if self._max_state else "false")
        self._settings.setValue("theme", self._theme)
        self._settings.sync()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide_to_pet()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        # 关闭窗口 ≠ 退出程序：保存状态后隐藏，桌宠与托盘继续常驻
        self.save_state()
        self.hide()
        self.workspace_closed.emit()
        event.ignore()
