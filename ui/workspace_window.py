# -*- coding: utf-8 -*-
"""凌零 · 工作台：对标 WorkBuddy 深色风格的标准主窗口。

桌宠本体是 140px 的无边框角色，适合常驻桌面；但它不适合读长回复、
看偏好/规则/轨迹表、翻交付清单。这里补一个真正的 QMainWindow，
外观对标 WorkBuddy：顶部菜单栏 + 左侧品牌导航边栏（含版本、分组、
底部更新区）+ 主区顶部标题栏（右侧图标）+ 主区底部全局命令栏。

  * 标准窗口边框 + 系统最小化/最大化/关闭按钮，双击标题栏也能最大化；
  * 显眼的最大化 / 还原按钮，首次打开默认最大化；
  * 记住窗口尺寸、最大化状态、深浅色主题（默认深色，贴近截图风格）；
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

from PyQt5.QtCore import Qt, QSettings, QSize, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices

from .icons import icon
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QComboBox, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenuBar, QPushButton, QScrollArea,
    QStackedWidget, QStatusBar, QTableWidget, QTableWidgetItem,
    QTextBrowser, QVBoxLayout, QWidget,
)

from framework import config
from framework.utils import app_data_dir, logger

VERSION = "v1.0.0"

# ------------------------------------------------------------
# 主题：用占位符替换生成 QSS，避免 f-string 把 QSS 的花括号当占位符
# ------------------------------------------------------------
_LIGHT = dict(
    BG="#F7F8FA", CARD="#FFFFFF", BORDER="#E5E7EB", TEXT="#1F2329",
    MUTED="#8A9099", NAV="#F9FAFB", NAV_TEXT="#3A4048",
    NAV_ACTIVE="#EEF0F3", ACCENT="#2F6FED", ACCENT_SOFT="#EDF3FF",
    GOOD="#0F6E56", BAD="#B42318", ROW_ALT="#F7F8FA", INPUT="#FFFFFF",
    BUBBLE_ME="#2F6FED", BUBBLE_ME_TEXT="#FFFFFF", BUBBLE_AI="#F0F2F5",
)
# 中性冷灰，贴近 WorkBuddy 截图：导航近黑、主区稍亮、选中态低饱和，
# 强调色只保留一处克制的蓝，去掉原来偏亮的绿/蓝大色块。
_DARK = dict(
    BG="#18191C", CARD="#202126", BORDER="#2E3036", TEXT="#ECEEF1",
    MUTED="#9AA1AB", NAV="#131417", NAV_TEXT="#C4C9D1",
    NAV_ACTIVE="#2A2C32", ACCENT="#4C8DFF", ACCENT_SOFT="#262A33",
    GOOD="#5DCAA5", BAD="#F09595", ROW_ALT="#1E1F24", INPUT="#202126",
    BUBBLE_ME="#2F6FED", BUBBLE_ME_TEXT="#FFFFFF", BUBBLE_AI="#2E323A",
)

_QSS_TEMPLATE = """
/* 全局字体栈：所有未单独指定字体的控件都继承现代无衬线界面字体，
   避免 Windows 下回退到宋体导致发虚、带衬线、字号不一。 */
QWidget { font-family: __FONT_STACK__; }
QMainWindow, QWidget#central { background: __BG__; }
QMenuBar { background: __CARD__; color: __TEXT__;
           border-bottom: 1px solid __BORDER__; font-size: __FS_SMALL__px; }
QMenuBar::item { padding: 5px 12px; border-radius: 6px; }
QMenuBar::item:selected { background: __ACCENT_SOFT__; }
QMenu { background: __CARD__; color: __TEXT__; border: 1px solid __BORDER__;
        font-size: __FS_SMALL__px; }
QMenu::item { padding: 8px 24px 8px 16px; }
QMenu::item:selected { background: __ACCENT__; color: #FFFFFF; }
QWidget#navPanel { background: __NAV__; border: none; }
QLabel#brand { color: __NAV_TEXT__; font-size: __FS_BRAND__px;
               font-weight: __WT_BOLD__; padding: 2px 2px; }
QLabel#brandSub { color: __MUTED__; font-size: __FS_MICRO__px; padding-bottom: 8px; }
QPushButton#newConv { background: __ACCENT_SOFT__; color: __TEXT__; border: 1px solid __BORDER__;
                      border-radius: 8px; padding: 9px 12px; font-size: __FS_SMALL__px;
                      font-weight: __WT_MEDIUM__; text-align: left; }
QPushButton#newConv:hover { background: __NAV_ACTIVE__; }
QPushButton#navButton {
    color: __NAV_TEXT__; background: transparent; border: none;
    border-radius: 7px; padding: 9px 12px; text-align: left;
    font-size: __FS_SMALL__px; font-weight: __WT_REGULAR__;
}
QPushButton#navButton:hover { background: __NAV_ACTIVE__; }
QPushButton#navButton:checked { background: __NAV_ACTIVE__; color: __TEXT__;
                                font-weight: __WT_MEDIUM__; }
QLabel#navGroupTitle { color: __MUTED__; font-size: __FS_MICRO__px;
                       padding: 10px 6px 2px; }
QLabel#navGroupBody { color: __MUTED__; font-size: __FS_MICRO__px; padding: 0 6px; }
QWidget#navFoot { background: __NAV__; border: none; }
QPushButton#navFootBtn {
    color: __NAV_TEXT__; background: transparent; border: none;
    border-radius: 7px; padding: 7px; font-size: __FS_SMALL__px;
}
QPushButton#navFootBtn:hover { background: __NAV_ACTIVE__; color: __TEXT__; }
QPushButton#updateBtn {
    background: transparent; color: __MUTED__; border: 1px solid __BORDER__;
    border-radius: 6px; padding: 5px 9px; font-size: __FS_MICRO__px;
}
QPushButton#updateBtn:hover { background: __NAV_ACTIVE__; color: __TEXT__; }
QWidget#topBar { background: __CARD__; border-bottom: 1px solid __BORDER__; }
QLabel#pageTitle { color: __TEXT__; font-size: __FS_TITLE__px;
                   font-weight: __WT_SEMIBOLD__; }
QPushButton#topIcon { background: transparent; color: __MUTED__; border: none;
                      border-radius: 7px; padding: 7px 9px; font-size: __FS_SMALL__px; }
QPushButton#topIcon:hover { background: __NAV_ACTIVE__; color: __TEXT__; }
QWidget#pageCard { background: __CARD__; border: 1px solid __BORDER__;
                   border-radius: 12px; }
QTextBrowser#detailView {
    background: __BG__; color: __TEXT__; border: 1px solid __BORDER__;
    border-radius: 9px; font-size: __FS_BODY__px; padding: 10px;
}
/* 对话页：滚动容器 + 自绘圆角气泡（QTextBrowser 富文本做不了圆角） */
QScrollArea#chatScroll { background: __BG__; border: none; }
QWidget#chatList { background: __BG__; }
QLabel#chatEmpty {
    color: __MUTED__; background: transparent; border: none;
    font-size: __FS_BODY__px; padding: 24px;
}
QLabel#bubbleMe {
    background: __BUBBLE_ME__; color: __BUBBLE_ME_TEXT__;
    border: none; border-radius: 14px;
    padding: 10px 14px; font-size: __FS_BODY__px;
}
QLabel#bubbleAi {
    background: __BUBBLE_AI__; color: __TEXT__;
    border: none; border-radius: 14px;
    padding: 10px 14px; font-size: __FS_BODY__px;
}
/* 底部命令栏：参考图的「大圆角输入框 + 下方控制条」两层结构 */
QWidget#commandBar { background: __BG__; }
QWidget#composer {
    background: __CARD__; border: 1px solid __BORDER__;
    border-radius: 14px;
}
QLineEdit#commandInput {
    background: transparent; color: __TEXT__; border: none;
    padding: 2px 4px; font-size: __FS_BODY__px;
}
QComboBox#cmdCombo {
    background: transparent; color: __MUTED__; border: none;
    border-radius: 6px; padding: 5px 8px; font-size: __FS_MICRO__px;
}
QComboBox#cmdCombo:hover { background: __NAV_ACTIVE__; color: __TEXT__; }
QComboBox#cmdCombo QAbstractItemView { background: __CARD__; color: __TEXT__;
                                       font-size: __FS_SMALL__px; }
QPushButton#chip {
    background: transparent; color: __MUTED__; border: none;
    border-radius: 6px; padding: 5px 8px; font-size: __FS_MICRO__px;
}
QPushButton#chip:hover { background: __NAV_ACTIVE__; color: __TEXT__; }
QPushButton#sendRound {
    background: __ACCENT__; color: #FFFFFF; border: none;
    border-radius: 16px; font-size: __FS_BODY__px;
}
QPushButton#sendRound:hover { background: __ACCENT__; }
QPushButton#sendRound:disabled { background: __BORDER__; color: __MUTED__; }
QPushButton#primaryButton {
    background: __ACCENT__; color: #FFFFFF; border: none;
    border-radius: 7px; padding: 8px 16px; font-size: __FS_SMALL__px;
    font-weight: __WT_MEDIUM__;
}
QPushButton#primaryButton:hover { background: __NAV_ACTIVE__; }
QPushButton#ghostButton {
    background: transparent; color: __TEXT__; border: 1px solid __BORDER__;
    border-radius: 7px; padding: 7px 14px; font-size: __FS_SMALL__px;
}
QPushButton#ghostButton:hover { background: __ACCENT_SOFT__; }
QTableWidget#dataTable {
    background: __CARD__; color: __TEXT__; border: 1px solid __BORDER__;
    border-radius: 9px; gridline-color: __BORDER__; font-size: __FS_SMALL__px;
    alternate-background-color: __ROW_ALT__;
}
QTableWidget#dataTable QHeaderView::section {
    background: __ACCENT_SOFT__; color: __TEXT__; border: none;
    border-bottom: 1px solid __BORDER__; padding: 9px 6px;
    font-size: __FS_SMALL__px; font-weight: __WT_MEDIUM__;
}
QListWidget#deliveryList {
    background: __CARD__; color: __TEXT__; border: 1px solid __BORDER__;
    border-radius: 9px; font-size: __FS_SMALL__px; padding: 6px;
}
QLabel#hintLabel { color: __TEXT__; font-size: __FS_BODY__px; }
QStatusBar#statusBar { background: __CARD__; color: __MUTED__;
                       border-top: 1px solid __BORDER__; font-size: __FS_MICRO__px; }
"""


def _qss(palette: dict) -> str:
    from .typography import apply_qss_tokens, WT
    out = _QSS_TEMPLATE
    for key, value in palette.items():
        out = out.replace("__%s__" % key, value)
    # 字号 + 字体栈 token
    out = apply_qss_tokens(out)
    # 字重 token
    for key, value in WT.items():
        out = out.replace("__WT_%s__" % key.upper(), str(value))
    return out


PAGE_DEFS = [
    ("chat", "对话", "在这里直接吩咐凌零；回复与桌宠气泡同步"),
    ("prefs", "我的偏好", "聊着聊着自动记下的喜好，改主意会覆盖旧值"),
    ("rules", "学到的规则", "使用中遇到的问题归纳出的调整，连续失效会自动停用"),
    ("trace", "执行轨迹", "每轮的状态哈希链、动作与失败；可生成时间线"),
    ("deliveries", "我的交付", "生成过的文档与图表，双击打开"),
    ("about", "关于与设置", "窗口操作、开关状态与快捷入口"),
]

# 导航项使用矢量线框图标名（见 ui/icons.py），不再用廉价的系统 emoji 字符
_PAGE_ICONS = ["chat", "prefs", "rules", "trace", "deliveries", "about"]


def _cell(text) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
    return item


def _icon_palette(theme: str):
    """返回该主题下按钮/文字与导航栏图标应使用的颜色。"""
    p = _DARK if theme == "dark" else _LIGHT
    return p["TEXT"], p["NAV_TEXT"]


class WorkspaceWindow(QMainWindow):
    """可最大化的主工作台。关闭 = 隐藏，程序继续留在托盘。"""

    workspace_closed = pyqtSignal()
    settings_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    context_requested = pyqtSignal()

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
        self._theme = self._settings.value("theme", "dark")
        if self._theme not in ("light", "dark"):
            self._theme = "dark"
        self._max_state = False
        self._first_open = True
        self._timeline_path = os.path.join(app_data_dir(), "timeline.html")
        # 记录「按钮 -> 图标名」，主题切换时统一重新着色（矢量图标随主题变色）
        self._icon_buttons = {}

        self._build_menu()
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
    # 顶部菜单栏（对标 WorkBuddy 的 文件/编辑/窗口/帮助/关于）
    # ------------------------------------------------------------
    def _build_menu(self):
        bar = self.menuBar()
        bar.clear()

        file_menu = bar.addMenu("文件(&F)")
        act = file_menu.addAction("打开工作台")
        act.triggered.connect(self.open_workspace)
        act = file_menu.addAction("退出程序")
        act.triggered.connect(self.quit_requested.emit)

        edit_menu = bar.addMenu("编辑(&E)")
        act = edit_menu.addAction("切换深色 / 浅色主题")
        act.triggered.connect(self.toggle_theme)
        act = edit_menu.addAction("复制当前页状态到剪贴板")
        act.triggered.connect(self._copy_status)

        win_menu = bar.addMenu("窗口(&W)")
        act = win_menu.addAction("最大化 / 还原")
        act.triggered.connect(self.toggle_maximize)
        act = win_menu.addAction("最小化")
        act.triggered.connect(self.showMinimized)
        act = win_menu.addAction("回到桌宠")
        act.triggered.connect(self.hide_to_pet)

        help_menu = bar.addMenu("帮助(&H)")
        act = help_menu.addAction("打开使用文档")
        act.triggered.connect(self._open_docs)

        about_menu = bar.addMenu("关于(&A)")
        act = about_menu.addAction("凌零 · 关于与设置")
        act.triggered.connect(lambda: self.switch_page(5))

    def _copy_status(self):
        try:
            QApplication.clipboard().setText(self._status_label.text())
        except Exception:      # noqa: BLE001 剪贴板不可用不致命
            pass

    def _open_docs(self):
        try:
            root = Path(__file__).resolve().parents[1]
            target = root / "docs" / "QUICK_START.md"
            if target.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        except Exception as e:      # noqa: BLE001
            logger.warning(f"[Workspace] 打开文档失败: {e}")

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
        rv.addWidget(self._build_topbar())

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

        rv.addWidget(self._build_command_bar())
        rv.addWidget(self._build_status())
        root.addWidget(right, 1)
        self.setCentralWidget(central)

    # ---------------- 左侧导航边栏（对标 WorkBuddy） ----------------
    def _build_nav(self) -> QWidget:
        nav = QWidget()
        nav.setObjectName("navPanel")
        nav.setFixedWidth(236)
        v = QVBoxLayout(nav)
        v.setContentsMargins(16, 18, 14, 14)
        v.setSpacing(6)

        brand = QLabel("凌零 · 工作台")
        brand.setObjectName("brand")
        sub = QLabel("桌面 AI 数字员工  %s" % VERSION)
        sub.setObjectName("brandSub")
        v.addWidget(brand)
        v.addWidget(sub)

        new_conv = QPushButton("  新建对话")
        new_conv.setObjectName("newConv")
        new_conv.setIcon(icon("plus", "#FFFFFF", 16))
        new_conv.setIconSize(QSize(16, 16))
        new_conv.setCursor(Qt.PointingHandCursor)
        new_conv.clicked.connect(self._new_conversation)
        self._icon_buttons[new_conv] = ("plus", "white")
        v.addWidget(new_conv)
        v.addSpacing(4)

        group = QButtonGroup(self)
        group.setExclusive(True)
        self._nav_buttons = []
        text_color, nav_color = _icon_palette(self._theme)
        for i, (_key, title, _hint) in enumerate(PAGE_DEFS):
            btn = QPushButton("  " + title)
            btn.setObjectName("navButton")
            btn.setIcon(icon(_PAGE_ICONS[i], nav_color, 18))
            btn.setIconSize(QSize(18, 18))
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _checked, idx=i: self.switch_page(idx))
            self._icon_buttons[btn] = (_PAGE_ICONS[i], "nav")
            group.addButton(btn)
            v.addWidget(btn)
            self._nav_buttons.append(btn)
        v.addSpacing(6)

        # 分组区：任务 / 交付（动态计数）
        self._nav_groups = QLabel("")
        self._nav_groups.setObjectName("navGroupBody")
        self._nav_groups.setWordWrap(True)
        v.addWidget(self._nav_groups)
        v.addStretch(1)

        v.addWidget(self._build_nav_foot())
        return nav

    def _build_nav_foot(self) -> QWidget:
        foot = QWidget()
        foot.setObjectName("navFoot")
        v = QVBoxLayout(foot)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(8)

        top = QWidget()
        th = QHBoxLayout(top)
        th.setContentsMargins(0, 0, 0, 0)
        th.setSpacing(8)
        self._theme_btn = self._foot_btn("", self.toggle_theme, "切换深色/浅色")
        self._maximize_btn = self._foot_btn("  最大化", self.toggle_maximize,
                                           "最大化/还原")
        self._foot_pet = self._foot_btn("", self.hide_to_pet, "回到桌宠")
        text_color, _ = _icon_palette(self._theme)
        self._theme_btn.setIcon(icon("moon" if self._theme == "dark" else "sun",
                                     text_color, 17))
        self._theme_btn.setIconSize(QSize(17, 17))
        self._maximize_btn.setIcon(icon("maximize", text_color, 17))
        self._maximize_btn.setIconSize(QSize(17, 17))
        self._foot_pet.setIcon(icon("arrow-left", text_color, 17))
        self._foot_pet.setIconSize(QSize(17, 17))
        self._icon_buttons[self._theme_btn] = ("theme", "theme")
        self._icon_buttons[self._maximize_btn] = ("maximize", "normal")
        self._icon_buttons[self._foot_pet] = ("arrow-left", "normal")
        th.addWidget(self._theme_btn)
        th.addWidget(self._maximize_btn)
        th.addWidget(self._foot_pet)
        v.addWidget(top)

        update = QWidget()
        uh = QHBoxLayout(update)
        uh.setContentsMargins(0, 0, 0, 0)
        uh.setSpacing(8)
        note = QLabel("凌零 %s · 开源" % VERSION)
        note.setObjectName("navGroupTitle")
        uh.addWidget(note)
        upd_btn = QPushButton("检查更新")
        upd_btn.setObjectName("updateBtn")
        upd_btn.setCursor(Qt.PointingHandCursor)
        upd_btn.setToolTip("本项目开源，没有在线更新通道；拉取最新代码即可")
        uh.addWidget(upd_btn)
        v.addWidget(update)
        return foot

    def _foot_btn(self, text, slot, tip) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("navFootBtn")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tip)
        btn.clicked.connect(slot)
        return btn

    # ---------------- 主区顶部标题栏（对标 WorkBuddy） ----------------
    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setFixedHeight(56)
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 8, 16, 8)
        h.setSpacing(8)

        self._title = QLabel("对话")
        self._title.setObjectName("pageTitle")
        h.addWidget(self._title)
        h.addStretch(1)

        text_color, _ = _icon_palette(self._theme)
        for name, tip, slot in (
            ("search", "聚焦命令栏", self._focus_command),
            ("refresh", "刷新当前页", self.refresh_current),
            ("bell", "查看执行状态", lambda: self.switch_page(3)),
            ("maximize", "最大化 / 还原", self.toggle_maximize),
        ):
            btn = QPushButton()
            btn.setObjectName("topIcon")
            btn.setIcon(icon(name, text_color, 16))
            btn.setIconSize(QSize(16, 16))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            self._icon_buttons[btn] = (name, "normal")
            h.addWidget(btn)
        return bar

    # ---------------- 主区底部全局命令栏（对标 WorkBuddy） ----------------
    def _build_command_bar(self) -> QWidget:
        """参考图的两层结构：上方大圆角输入框，下方一排小控件。"""
        bar = QWidget()
        bar.setObjectName("commandBar")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(16, 6, 16, 12)
        outer.setSpacing(0)

        text_color, _ = _icon_palette(self._theme)
        composer = QWidget()
        composer.setObjectName("composer")
        cv = QVBoxLayout(composer)
        cv.setContentsMargins(14, 12, 12, 8)
        cv.setSpacing(8)

        # 上层：占位文案输入框（占位文案与参考图一致）
        self._chat_input = QLineEdit()
        self._chat_input.setObjectName("commandInput")
        self._chat_input.setPlaceholderText(
            "今天帮你做些什么？  @ 添加上下文，/ 调用技能与指令")
        self._chat_input.returnPressed.connect(self._send)
        self._chat_input.setFixedHeight(30)
        cv.addWidget(self._chat_input)

        # 下层：一排小控件
        ctrl = QHBoxLayout()
        ctrl.setContentsMargins(0, 0, 0, 0)
        ctrl.setSpacing(4)

        def chip_btn(icon_name, tip, slot=None, width=30):
            b = QPushButton()
            b.setObjectName("chip")
            b.setIcon(icon(icon_name, text_color, 16))
            b.setIconSize(QSize(16, 16))
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.setFixedWidth(width)
            if slot:
                b.clicked.connect(slot)
            self._icon_buttons[b] = (icon_name, "normal")
            return b

        ctrl.addWidget(chip_btn("plus", "添加上下文（@ 引用桌面文件 / 资料）",
                                self._add_context))

        def combo(items, tip, width):
            c = QComboBox()
            c.setObjectName("cmdCombo")
            c.addItems(items)
            c.setToolTip(tip)
            c.setFixedWidth(width)
            return c

        branch = combo(["main"], "当前分支（本项目单分支）", 62)
        ctrl.addWidget(branch)
        access = combo(["允许完全访问", "受限模式"],
                       "访问级别（界面展示，由本机权限决定）", 106)
        ctrl.addWidget(access)
        ctrl.addStretch(1)
        # 模型带一个 sparkle 前缀图标感：用 icon 名做 chip + combo
        spark = chip_btn("sparkle", "模型由设置 / API Key 决定", width=26)
        ctrl.addWidget(spark)
        model = combo(["Hy4", "GLM-4.6V", "离线规则"],
                      "展示当前可用模型；实际模型由设置 / API Key 决定", 78)
        ctrl.addWidget(model)
        ctrl.addWidget(chip_btn("mic", "语音输入（需本机麦克风权限）", width=28))
        # 圆形发送按钮（参考图：右下角圆形向上箭头）
        send = QPushButton()
        send.setObjectName("sendRound")
        send.setIcon(icon("arrow-up", "#FFFFFF", 18))
        send.setIconSize(QSize(18, 18))
        send.setFixedSize(32, 32)
        send.setCursor(Qt.PointingHandCursor)
        send.setToolTip("发送（回车同效）")
        send.clicked.connect(self._send)
        self._icon_buttons[send] = ("arrow-up", "white")
        ctrl.addWidget(send)

        cv.addLayout(ctrl)
        outer.addWidget(composer)
        return bar

    def _build_status(self) -> QStatusBar:
        self._status = QStatusBar(self)
        self._status.setObjectName("statusBar")
        self._status_label = QLabel("就绪")
        self._status.addWidget(self._status_label)
        return self._status

    def _ghost(self, text, slot, icon_name=None) -> QPushButton:
        btn = QPushButton("  " + text if icon_name else text)
        btn.setObjectName("ghostButton")
        if icon_name:
            text_color, _ = _icon_palette(self._theme)
            btn.setIcon(icon(icon_name, text_color, 15))
            btn.setIconSize(QSize(15, 15))
            self._icon_buttons[btn] = (icon_name, "normal")
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
        # 用 QScrollArea + 自绘 QLabel 气泡，才能真正做出圆角
        # （QTextBrowser 富文本不支持 border-radius / display:inline-block）。
        self._chat_scroll = QScrollArea()
        self._chat_scroll.setObjectName("chatScroll")
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._chat_list = QWidget()
        self._chat_list.setObjectName("chatList")
        self._chat_layout = QVBoxLayout(self._chat_list)
        self._chat_layout.setContentsMargins(6, 6, 6, 6)
        self._chat_layout.setSpacing(8)
        self._chat_layout.addStretch(1)
        self._chat_scroll.setWidget(self._chat_list)
        v.addWidget(self._chat_scroll, 1)
        return page, self._refresh_chat

    def _send(self):
        text = self._chat_input.text().strip()
        if not text:
            return
        self._chat_input.clear()
        self.fw.send_command(text)
        self.switch_page(0)
        self._refresh_chat()

    def _quick(self, text):
        self.fw.send_command(text)
        self._refresh_chat()

    def _new_conversation(self):
        self.switch_page(0)
        self._focus_command()

    def _focus_command(self):
        self._chat_input.setFocus()
        self._chat_input.setText("@ ")

    def _add_context(self):
        self._chat_input.setFocus()
        cur = self._chat_input.text()
        if "@" not in cur:
            self._chat_input.setText((cur + " @ ").strip())

    def _refresh_chat(self):
        # 清空旧气泡（保留末尾的 stretch 弹簧）
        while self._chat_layout.count() > 1:
            item = self._chat_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        history = list(getattr(self.fw.memory, "history", []) or [])[-60:]
        if not history:
            empty = QLabel("还没有对话。在底部命令栏输入一句话，或点桌宠直接说。")
            empty.setObjectName("chatEmpty")
            empty.setAlignment(Qt.AlignCenter)
            empty.setWordWrap(True)
            self._chat_layout.insertWidget(0, empty)
            return
        for item in history:
            role = item.get("role", "")
            content = str(item.get("content", ""))
            self._add_bubble(role, content)
        # 滚动到底部
        QTimer.singleShot(0, self._scroll_chat_to_bottom)

    def _add_bubble(self, role: str, content: str):
        is_me = role == "user"
        bubble = QLabel(content)
        bubble.setObjectName("bubbleMe" if is_me else "bubbleAi")
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble.setMaximumWidth(680)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if is_me:
            row.addStretch(1)
            row.addWidget(bubble, 0, Qt.AlignRight)
        else:
            row.addWidget(bubble, 0, Qt.AlignLeft)
            row.addStretch(1)
        container = QWidget()
        container.setLayout(row)
        # 插到末尾弹簧之前
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, container)

    def _scroll_chat_to_bottom(self):
        bar = self._chat_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ---------------- 页面：偏好 ----------------
    def _build_prefs_page(self, hint):
        page, v = self._page_shell(hint)
        self._prefs_table = self._table(
            ["偏好", "当前值", "置信度", "命中", "证据数"])
        v.addWidget(self._prefs_table, 1)
        v.addWidget(self._button_row(
            self._ghost("刷新", self._refresh_prefs, "refresh"),
            self._ghost("删除选中", self._delete_pref, "trash"),
            self._ghost("清空全部", self._clear_prefs, "x-circle")))
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
            self._ghost("刷新", self._refresh_rules, "refresh"),
            self._ghost("删除选中", self._delete_rule, "trash")))
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
            self._ghost("刷新", self._refresh_trace, "refresh"),
            self._ghost("生成时间线", self.generate_timeline, "clock"),
            self._ghost("打开时间线", self._open_timeline, "open")))
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
            self._ghost("刷新", self._refresh_deliveries, "refresh"),
            self._ghost("打开选中", self._open_delivery, "open")))
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
            self._ghost("打开设置窗口", self.settings_requested.emit, "settings"),
            self._ghost("刷新状态", self._refresh_status, "refresh")))
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
            "<h2>凌零 · 桌面 AI 数字员工 %s</h2>"
            "<p>这个窗口对标 WorkBuddy 的深色主窗口：顶部菜单栏、左侧品牌导航、"
            "主区顶部标题栏与底部全局命令栏。可以拖动边框调整大小、双击标题栏最大化、"
            "点右上角或这里的「最大化」按钮铺满屏幕。关闭只会隐藏窗口，"
            "桌宠与托盘继续常驻；从桌宠右键菜单、托盘菜单或输入条的「工作台」"
            "按钮都能再打开。</p>"
            "<h3>窗口操作</h3><ul>"
            "<li>最大化 / 还原按钮：切换铺满与窗口模式（状态会被记住）</li>"
            "<li>主题按钮：切换深色 / 浅色（会被记住，默认深色）</li>"
            "<li>回到桌宠按钮：隐藏窗口，回到桌面角色</li>"
            "<li>Esc：关闭窗口（不退出程序）</li></ul>"
            "<h3>底部命令栏</h3><ul>"
            "<li>加号按钮添加上下文、@ 引用桌面文件、/ 调用技能与指令</li>"
            "<li>回车或「发送」把指令交给桌宠执行，自动切到对话页查看</li>"
            "<li>分支 / 访问级别 / 模型为界面展示项，实际行为由本机设置与 Key 决定</li></ul>"
            "<h3>当前开关</h3><ul>%s</ul>"
            "<h3>数据位置</h3><p>轨迹、偏好、规则保存在本机数据目录，"
            "均不含用户原话明文；可用环境变量 LINGLING_DATA_DIR 隔离。</p>" % (VERSION, rows))

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
        n_todos = len(getattr(getattr(self.fw, "tasks", None), "todos", []) or [])
        n_deliv = len(getattr(getattr(self.fw, "tasks", None), "deliveries", []) or [])
        self._status_label.setText(
            "轮次 %d  ·  事件 %d  ·  失败率 %.1f%%  ·  token %d  ·  "
            "偏好 %d  ·  生效规则 %d  ·  哈希链 %s  ·  %s"
            % (s["turns"], s["events"], s["failure_rate"] * 100, s["tokens"],
               n_prefs, n_rules, "完整" if s["chain_ok"] else "断开",
               self.fw.ai_status_text()
               if hasattr(self.fw, "ai_status_text") else ""))
        self._nav_groups.setText(
            "任务：待办 %d 项\n交付：已生成 %d 份\n偏好：%d 条 · 生效规则：%d 条"
            % (n_todos, n_deliv, n_prefs, n_rules))

    def toggle_theme(self):
        self._apply_theme("dark" if self._theme == "light" else "light")
        self._refresh_chat()
        self._refresh_about()
        self.save_state()

    def _apply_theme(self, theme: str):
        self._theme = theme if theme in ("light", "dark") else "dark"
        self.setStyleSheet(_qss(_DARK if self._theme == "dark" else _LIGHT))
        # 矢量图标随主题重新着色；主题按钮在月亮/太阳之间切换
        self._recolor_icons()
        if hasattr(self, "_theme_btn"):
            name = "moon" if self._theme == "dark" else "sun"
            self._theme_btn.setIcon(icon(name, _icon_palette(self._theme)[0], 17))
            self._icon_buttons[self._theme_btn] = (name, "theme")

    def _recolor_icons(self):
        """主题切换后，把所有非纯白图标重绘成新主题的文字色。"""
        text_color, nav_color = _icon_palette(self._theme)
        for btn, (name, kind) in list(self._icon_buttons.items()):
            if kind == "white":
                color = "#FFFFFF"
            elif kind == "nav":
                color = nav_color
            elif kind == "theme":
                color = text_color
            else:
                color = text_color
            btn.setIcon(icon(name, color, btn.iconSize().width() or 16))

    def _apply_maximize(self, on: bool):
        self._max_state = bool(on)
        if on:
            self.showMaximized()
        else:
            self.showNormal()
        self._maximize_btn.setText("  还原" if on else "  最大化")

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
