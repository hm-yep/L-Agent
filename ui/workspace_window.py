# -*- coding: utf-8 -*-
"""凌零 · 工作台：对标 WorkBuddy 深色风格的标准主窗口。

桌宠本体是紧凑的无边框角色（尺寸由 config.PET_SIZE 决定），适合常驻桌面；但它不适合读长回复、
看偏好/规则/轨迹表、翻交付清单。这里补一个真正的 QMainWindow，
外观对标 WorkBuddy：顶部菜单栏 + 左侧品牌导航边栏（含版本、分组、
底部更新区）+ 主区顶部标题栏（标题 + 一句页面说明 + 一个带文字的刷新按钮）
+ 主区底部全局命令栏。界面只保留看得懂、点了有反馈的控件。

  * 标准窗口边框 + 系统最小化/最大化/关闭按钮，双击标题栏也能最大化；
  * 显眼的最大化 / 还原按钮，首次打开默认最大化；
  * 记住窗口尺寸、最大化状态、深浅色主题（默认深色，贴近截图风格）；
  * 关闭只隐藏窗口、不退出程序（托盘常驻），下次点开立刻回来；
  * 左侧导航七个页面，数据全部来自 Framework 的真实 Store，不放假数据。

数据口径：
  对话  -> fw.memory.history（信号 history_changed 自动刷新）
  偏好  -> fw.prefs.all()
  规则  -> fw.rules.all()
  轨迹  -> fw.trajectory.turns()/events()/summarize()
  交付  -> fw.tasks.deliveries
所有新能力都可能因降级而为 None，页面要给出友好提示而不是崩掉。
"""

import importlib.util
import os
import sys
import time
from pathlib import Path

from PyQt5.QtCore import (
    Qt, QSettings, QRect, QSize, QTimer, QUrl, pyqtSignal)
from PyQt5.QtGui import QDesktopServices, QFontMetrics

from .icons import icon
from . import theme
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenuBar, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QStatusBar,
    QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from framework import config
from framework.path_guard import describe_formats
from framework.utils import app_data_dir, logger, plain_text

VERSION = "v1.0.0"
# 开源项目没有在线更新通道，导航底部的按钮直接打开 releases 页看新版，
# 而不是假装「检查更新」后毫无反应。
REPO_RELEASES_URL = "https://github.com/hm-yep/L-Agent/releases"


PAGE_DEFS = [
    ("chat", "对话", "在这里直接吩咐凌零；回复与桌宠气泡同步"),
    ("prefs", "我的偏好", "聊着聊着自动记下的喜好，改主意会覆盖旧值"),
    ("rules", "学到的规则", "使用中遇到的问题归纳出的调整，连续失效会自动停用"),
    ("trace", "执行轨迹", "每轮的状态哈希链、动作与失败；可生成时间线"),
    ("deliveries", "我的交付", "生成过的文档与图表，双击打开"),
    ("grants", "本机访问", "本次会话放开了哪些路径、读过什么；可一键全部撤销"),
    ("web", "网页调试台", "和浏览器访问的是同一份网页：同一套会话、授权与视觉"),
    ("about", "关于与设置", "七个能力开关（可直接点）、数据位置与操作说明"),
]

# 导航项使用矢量线框图标名（见 ui/icons.py），不再用廉价的系统 emoji 字符
_PAGE_ICONS = ["chat", "prefs", "rules", "trace", "deliveries", "shield",
               "globe", "about"]

# 不进左侧导航的页面：页面本身还在（栈里仍占位），只是不给它按钮。
# 执行轨迹是工程面板——状态哈希链、token、耗时、失败率，排障和面试讲故事时有用，
# 但摆在主界面上会让整个产品看起来像调试器。入口收在「关于与设置」里。
HIDDEN_IN_NAV = ("trace",)


def page_index(key: str) -> int:
    """按 key 找页面下标。

    页面顺序会变（第 8 页就是后来插进去的），所以任何「跳到某页」的地方
    都该问这里，而不是写死 5 这种数字——写死的数字不会报错，只会静静地打开别的页。
    """
    for i, page in enumerate(PAGE_DEFS):
        if page[0] == key:
            return i
    return 0

# 「按钮都做什么」的唯一出处：关于页的卡片和完整使用说明对话框都读这份，
# 改按钮时只改这里，界面与说明书不会各说一套。
OPERATION_ROWS = (
    "新建对话：清空当前对话记录（会先确认），长期记忆与偏好保留",
    "刷新本页：重新读取本机数据，刷新当前页的表格与统计",
    "主题：切深色 / 浅色，工作台与桌宠浮层一起换肤",
    "最大化：铺满 / 还原窗口；回桌宠：隐藏本窗口但不退出程序",
    "查看更新：打开 GitHub Releases 页，开源项目需手动拉取代码",
    "圆形发送按钮（或回车）：把指令交给凌零执行",
    "关于页七个开关：点开就开启、点关就关闭，立即生效并写进本机配置",
    "文件 → 退出程序：真正退出桌宠与托盘（点 X 只是隐藏）",
    "授权卡三个按钮：「只读这一个」只放开当前文件；「允许整个文件夹（本次会话）」"
    "放开整棵子树；「拒绝」不读。三者都只在本次运行有效，重启后失效",
    "本机访问页：看本次放开了哪些路径、最近实际读了什么；「全部撤销」后同一路径会重新弹卡",
    "网页调试台：工作台里直接内嵌浏览器里那份网页，同一份会话/授权/视觉；"
    "没起服务时点「启动本地服务」，端口占用则复用已在跑的那份",
    "关于页「打开执行轨迹」：工程面板（事件、动作、失败、状态哈希链），"
    "不占左侧导航，需要时从关于页进；那一页可生成并打开时间线网页",
)

# 关于页「当前开关」的五个按钮：(显示名, framework.config 属性名, 这一行的小字)
# 属性名必须是 config 里真实存在、且 core.py 调用点每次都重读的开关——
# 按钮点了要能改变程序行为，否则又成了摆设。
SWITCH_ROWS = (
    ("事件轨迹", "TRAJECTORY_ENABLED",
     "记录每轮动作与失败；执行轨迹页和顶部统计的数据就来自这里"),
    ("偏好学习", "PREFERENCE_LEARNING",
     "从你说的话里自动发现偏好，写进「我的偏好」并注入后续对话"),
    ("偏好自动归纳", "PREFERENCE_LLM",
     "每 3 轮把最近对话交给模型抽开放式偏好；没配 API Key 时自动跳过"),
    ("在界面上显示偏好", "PREFERENCE_PANEL",
     "默认只在后台学习、不展示；打开后「我的偏好」页才显示明细与删除按钮"),
    ("自我进化", "EVOLUTION_ENABLED",
     "从失败里归纳规则并在下次自动调整，规则见「学到的规则」页"),
    ("计划依赖图", "PLAN_DAG_ENABLED",
     "多步任务先编译成依赖图做静态校验，出错只重算受影响的那几步"),
    ("契约严格模式", "EFFECT_SPEC_STRICT",
     "动作没通过副作用前置校验就拒绝执行（默认只记录，不拦）"),
)


def _cell(text) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
    return item


def _icon_palette(name: str):
    """该主题下的图标取色：正文区用 TEXT，导航区用 NAV_TEXT。"""
    p = theme.palette(name)
    return p["TEXT"], p["NAV_TEXT"]


def _repolish(widget):
    """改了 role 这类动态属性后必须重新 polish，QSS 才会按新属性取色。"""
    st = widget.style()
    st.unpolish(widget)
    st.polish(widget)


class WorkspaceWindow(QMainWindow):
    """可最大化的主工作台。关闭 = 隐藏，程序继续留在托盘。"""

    workspace_closed = pyqtSignal()
    settings_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, fw, parent=None, settings_path: str = None):
        super().__init__(parent)
        self.fw = fw
        self.setWindowTitle("工作台")
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
        """菜单栏只放不重复、且点了一定看得到结果的操作。

        之前这里还放了「打开工作台」（在已经打开的工作台里点它毫无变化）、
        「最小化」（系统标题栏本来就有）等冗余项，现在全部收掉。
        """
        bar = self.menuBar()
        bar.clear()

        file_menu = bar.addMenu("文件(&F)")
        act = file_menu.addAction("新建对话（清空当前聊天）")
        act.triggered.connect(self._new_conversation)
        act = file_menu.addAction("退出程序")
        act.triggered.connect(lambda: self._emit_or_notify(
            self.quit_requested, "正在退出凌零…", "退出请求未接线：请用桌宠右键菜单退出"))

        edit_menu = bar.addMenu("编辑(&E)")
        act = edit_menu.addAction("切换深色 / 浅色主题")
        act.triggered.connect(self.toggle_theme)
        act = edit_menu.addAction("复制状态栏文本")
        act.triggered.connect(self._copy_status)

        win_menu = bar.addMenu("窗口(&W)")
        act = win_menu.addAction("最大化 / 还原")
        act.triggered.connect(self.toggle_maximize)
        act = win_menu.addAction("回到桌宠（隐藏本窗口）")
        act.triggered.connect(self.hide_to_pet)

        help_menu = bar.addMenu("帮助(&H)")
        act = help_menu.addAction("打开使用文档")
        act.triggered.connect(self._open_docs)
        act = help_menu.addAction("查看 GitHub 更新页")
        act.triggered.connect(self._open_repo)

        about_menu = bar.addMenu("关于(&A)")
        act = about_menu.addAction("关于与状态")
        act.triggered.connect(lambda: self.switch_page(page_index("about")))

    # ------------------------------------------------------------
    # 统一反馈：任何按钮操作都要在状态栏右侧留下一句看得见的结果
    # ------------------------------------------------------------
    def _notify(self, message: str, ok: bool = True):
        self._feedback.setText(("✓  " if ok else "!  ") + message)
        self._feedback.setProperty("role", "good" if ok else "bad")
        _repolish(self._feedback)
        self._feedback_timer.start(6000)

    def _confirm(self, title: str, message: str, detail: str = "") -> bool:
        """不可逆操作统一确认；单独成方法便于离屏测试替换。"""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(message)
        if detail:
            box.setInformativeText(detail)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        return box.exec_() == QMessageBox.Yes

    def _emit_or_notify(self, signal, ok_text: str, empty_text: str):
        """信号没人接收时如实提示，而不是让按钮点了像死了一样。"""
        try:
            connected = self.receivers(signal) > 0
        except Exception:      # noqa: BLE001 个别 PyQt 版本签名不同，退化为直接发射
            connected = True
        if not connected:
            self._notify(empty_text, ok=False)
            return
        signal.emit()
        self._notify(ok_text)

    def _refresh_page_with_feedback(self):
        self.refresh_current()
        self._notify("已刷新「%s」" % PAGE_DEFS[self._stack.currentIndex()][1])

    def _copy_status(self):
        try:
            QApplication.clipboard().setText(self._status_full)
            self._notify("状态栏文本已复制到剪贴板")
        except Exception as e:      # noqa: BLE001
            self._notify("复制失败：%s" % e, ok=False)

    def _open_docs(self):
        try:
            root = Path(__file__).resolve().parents[1]
            target = root / "docs" / "QUICK_START.md"
            if target.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
                self._notify("已在默认程序打开使用文档")
            else:
                self._notify("没找到 docs/QUICK_START.md，可能被打包时省略了",
                             ok=False)
        except Exception as e:      # noqa: BLE001
            logger.warning(f"[Workspace] 打开文档失败: {e}")
            self._notify("打开文档失败：%s" % e, ok=False)

    def _open_repo(self):
        """开源项目没有在线更新器：如实打开 releases 页让用户自己看版本。"""
        try:
            QDesktopServices.openUrl(QUrl(REPO_RELEASES_URL))
            self._notify("已在浏览器打开 GitHub Releases 更新页")
        except Exception as e:      # noqa: BLE001
            logger.warning(f"[Workspace] 打开项目更新页失败: {e}")
            self._notify("打开更新页失败：%s" % e, ok=False)

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
        sp = theme.SP
        hv.setContentsMargins(sp["l"], sp["m"], sp["l"], sp["m"])
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
        nav.setFixedWidth(theme.DIM["nav_w"])
        v = QVBoxLayout(nav)
        v.setContentsMargins(theme.SP["m"], theme.SP["l"], theme.SP["m"],
                             theme.SP["m"])
        v.setSpacing(theme.SP["xs"])

        brand = QLabel("凌零 · 工作台")
        brand.setObjectName("brand")
        sub = QLabel("桌面 AI 数字员工  %s" % VERSION)
        sub.setObjectName("brandSub")
        v.addWidget(brand)
        v.addWidget(sub)
        v.addSpacing(theme.SP["s"])

        new_conv = QPushButton("  新建对话")
        new_conv.setObjectName("newConv")
        new_conv.setIcon(icon("plus", theme.T("ACCENT_TEXT"), 16))
        new_conv.setIconSize(QSize(16, 16))
        new_conv.setCursor(Qt.PointingHandCursor)
        new_conv.clicked.connect(self._new_conversation)
        self._icon_buttons[new_conv] = ("plus", "accent")
        v.addWidget(new_conv)
        v.addSpacing(theme.SP["s"])

        group = QButtonGroup(self)
        group.setExclusive(True)
        self._nav_group = group
        # 隐藏页（见 HIDDEN_IN_NAV）没有按钮，但下标要和 PAGE_DEFS 对齐，
        # 否则 switch_page(3) 会点到别人的按钮上。
        self._nav_buttons = [None] * len(PAGE_DEFS)
        _text_color, nav_color = _icon_palette(self._theme)
        for i, (key, title, _hint) in enumerate(PAGE_DEFS):
            if key in HIDDEN_IN_NAV:
                continue
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
            self._nav_buttons[i] = btn
        v.addSpacing(theme.SP["m"])

        # 分组区：任务 / 交付（动态计数）
        cap = QLabel("本机数据")
        cap.setObjectName("navGroupTitle")
        v.addWidget(cap)
        self._nav_groups = QLabel("")
        self._nav_groups.setObjectName("navGroupBody")
        self._nav_groups.setWordWrap(True)
        v.addWidget(self._nav_groups)
        # 分组计数本身也要能点：本机访问放开了几条，点进去就是那一页
        self._nav_grants_btn = QPushButton("  本机访问：放开 0 条")
        self._nav_grants_btn.setObjectName("navGroupLink")
        self._nav_grants_btn.setCursor(Qt.PointingHandCursor)
        self._nav_grants_btn.clicked.connect(
            lambda: self.switch_page(page_index("grants")))
        v.addWidget(self._nav_grants_btn)
        v.addStretch(1)

        v.addWidget(self._build_nav_foot())
        return nav

    def _build_nav_foot(self) -> QWidget:
        """导航底部只留版本与更新入口。

        主题 / 最大化 / 回桌宠原来挤在这里，232px 宽的边栏放不下三个带文字
        按钮，「最大化」会被截成「最大」；它们已挪到顶栏，操作区只留一处。
        """
        foot = QWidget()
        foot.setObjectName("navFoot")
        h = QHBoxLayout(foot)
        h.setContentsMargins(0, theme.SP["s"], 0, 0)
        h.setSpacing(theme.SP["s"])
        note = QLabel("凌零 %s · 开源" % VERSION)
        note.setObjectName("navGroupTitle")
        h.addWidget(note)
        h.addStretch(1)
        upd_btn = QPushButton("查看更新")
        upd_btn.setObjectName("updateBtn")
        upd_btn.setCursor(Qt.PointingHandCursor)
        upd_btn.setToolTip(
            "打开 GitHub Releases 查看最新版本；开源项目需手动拉取代码更新")
        upd_btn.clicked.connect(self._open_repo)
        h.addWidget(upd_btn)
        return foot

    # ---------------- 主区顶部标题栏：说清这一页是干嘛的 ----------------
    def _build_topbar(self) -> QWidget:
        """标题 + 页面说明 + 本页唯一操作；窗口级操作（主题/最大化/回桌宠）也在这里。

        原来这些按钮藏在 232px 的导航底部，文字被截断；顶栏横向空间充裕，
        全部带文字后一眼看得懂点了会干什么。
        """
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setFixedHeight(theme.DIM["topbar_h"])
        h = QHBoxLayout(bar)
        h.setContentsMargins(theme.SP["xl"], 0, theme.SP["l"], 0)
        h.setSpacing(theme.SP["s"])

        self._title = QLabel("对话")
        self._title.setObjectName("pageTitle")
        h.addWidget(self._title)
        self._page_hint = QLabel("")
        self._page_hint.setObjectName("pageSubtitle")
        self._page_hint.setWordWrap(False)
        h.addWidget(self._page_hint, 1)

        color = theme.T("TEXT2", self._theme)
        refresh = self._top_btn(h, "刷新本页", self._refresh_page_with_feedback,
                                "重新读取本机数据，刷新当前页的表格与统计")
        refresh.setIcon(icon("refresh", color, 15))
        self._icon_buttons[refresh] = ("refresh", "muted")

        self._theme_btn = self._top_btn(h, "主题", self.toggle_theme,
                                        "切换深色 / 浅色主题（全局同步换肤）")
        self._maximize_btn = self._top_btn(h, "最大化", self.toggle_maximize,
                                           "最大化 / 还原窗口")
        self._foot_pet = self._top_btn(h, "回桌宠", self.hide_to_pet,
                                       "隐藏工作台，回到桌面角色（不退出程序）")
        self._theme_icon_name = "moon" if self._theme == "dark" else "sun"
        self._theme_btn.setIcon(icon(self._theme_icon_name, color, 15))
        self._icon_buttons[self._theme_btn] = (self._theme_icon_name, "muted")
        self._maximize_btn.setIcon(icon("maximize", color, 15))
        self._icon_buttons[self._maximize_btn] = ("maximize", "muted")
        self._foot_pet.setIcon(icon("arrow-left", color, 15))
        self._icon_buttons[self._foot_pet] = ("arrow-left", "muted")
        return bar

    def _top_btn(self, row: QHBoxLayout, text: str, slot,
                 tip: str) -> QPushButton:
        btn = QPushButton("  " + text)
        btn.setObjectName("topButton")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tip)
        btn.setIconSize(QSize(15, 15))
        btn.clicked.connect(slot)
        row.addWidget(btn)
        return btn

    # ---------------- 主区底部全局命令栏 ----------------
    def _build_command_bar(self) -> QWidget:
        """单层命令栏：选文件按钮 + 大输入框 + 圆形发送按钮。

        之前这里抄了参考图的一排小控件（分支 / 访问级别 / 模型 / 麦克风 /
        添加上下文），但它们在本项目里没有对应的真实能力，点了没反应、
        是纯摆设，所以删了。「选文件」是例外：它真能接上——弹系统文件框，
        把「读一下 "路径"」填进输入框，回车后走的是原有链路。手打路径
        （尤其是带空格的）太麻烦，这个按钮解决的就是这一件事。
        """
        bar = QWidget()
        bar.setObjectName("commandBar")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(theme.SP["l"], 0, theme.SP["l"], theme.SP["m"])
        outer.setSpacing(0)

        composer = QWidget()
        composer.setObjectName("composer")
        cv = QHBoxLayout(composer)
        cv.setContentsMargins(theme.SP["m"], theme.SP["xs"], theme.SP["xs"],
                              theme.SP["xs"])
        cv.setSpacing(theme.SP["s"])

        # 「选择文件」：弹系统文件框，把指令填进输入框。
        # 只填路径不读文件——回车后仍走 模型 → path_guard → 授权卡，
        # 选文件不是绕过授权链的后门（真绕过去，「点同意」就白点了）。
        pick = QPushButton()
        pick.setObjectName("pickFile")
        pick.setIcon(icon("folder", theme.T("TEXT2", self._theme), 16))
        pick.setIconSize(QSize(16, 16))
        pick.setFixedSize(30, 30)
        pick.setCursor(Qt.PointingHandCursor)
        pick.setToolTip("选择本机文件（自动填好指令，回车发送）")
        pick.clicked.connect(self._pick_file)
        self._icon_buttons[pick] = ("folder", "muted")
        cv.addWidget(pick, 0, Qt.AlignVCenter)

        # 输入框：占位文案如实描述真实交互，不再写 @ / / 这类本项目没有的前缀语法
        self._chat_input = QLineEdit()
        self._chat_input.setObjectName("commandInput")
        self._chat_input.setPlaceholderText(
            "今天帮你做些什么？点左边文件夹选文件，或直接说一句话；回车发送")
        self._chat_input.returnPressed.connect(self._send)
        self._chat_input.setFixedHeight(theme.DIM["composer_h"])
        cv.addWidget(self._chat_input, 1)

        # 圆形发送按钮（右下角圆形向上箭头）
        send = QPushButton()
        send.setObjectName("sendRound")
        send.setIcon(icon("arrow-up", theme.T("ACCENT_TEXT", self._theme), 18))
        send.setIconSize(QSize(18, 18))
        send.setFixedSize(34, 34)
        send.setCursor(Qt.PointingHandCursor)
        send.setToolTip("发送（回车同效）")
        send.clicked.connect(self._send)
        self._icon_buttons[send] = ("arrow-up", "accent")
        cv.addWidget(send, 0, Qt.AlignVCenter)

        outer.addWidget(composer)
        return bar

    _CHIP_DEFS = (("turns", "轮次"), ("events", "事件"), ("fail", "失败率"),
                  ("tokens", "token"), ("prefs", "偏好"), ("rules", "规则"),
                  ("chain", "哈希链"))

    def _build_status(self) -> QStatusBar:
        """左侧指标芯片 + 中间模型状态 + 右侧操作反馈。

        原来七个数字挤成一条长文本，窗口一窄就被裁掉、也没法一眼扫到；
        拆成芯片后每个指标各自成块，异常值还能单独标色。
        """
        self._status = QStatusBar(self)
        self._status.setObjectName("statusBar")
        self._status.setSizeGripEnabled(False)

        chips = QWidget()
        ch = QHBoxLayout(chips)
        ch.setContentsMargins(theme.SP["xs"], 0, 0, 0)
        ch.setSpacing(theme.SP["xs"])
        self._chips = {}
        for key, label in self._CHIP_DEFS:
            chip = QLabel("%s —" % label)
            chip.setObjectName("statChip")
            ch.addWidget(chip)
            self._chips[key] = chip
        self._status.addWidget(chips)

        self._status_label = QLabel("就绪")
        self._status_label.setProperty("role", "muted")
        self._status.addWidget(self._status_label, 1)

        self._feedback = QLabel("")
        self._feedback.setObjectName("feedbackLabel")
        self._status.addPermanentWidget(self._feedback)
        self._feedback_timer = QTimer(self)
        self._feedback_timer.setSingleShot(True)
        self._feedback_timer.timeout.connect(self._feedback.clear)
        self._status_full = "就绪"
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
        h.setContentsMargins(0, theme.SP["s"], 0, 0)
        h.setSpacing(theme.SP["s"])
        for b in buttons:
            h.addWidget(b)
        h.addStretch(1)
        return row

    def _card(self, title: str, desc: str = "") -> tuple:
        """一块设置/信息卡片：返回 (卡片控件, 往里追加内容的竖向布局)。"""
        card = QWidget()
        card.setObjectName("card")
        v = QVBoxLayout(card)
        v.setContentsMargins(theme.SP["l"], theme.SP["m"], theme.SP["l"],
                             theme.SP["m"])
        v.setSpacing(theme.SP["xs"])
        t = QLabel(title)
        t.setObjectName("cardTitle")
        v.addWidget(t)
        if desc:
            d = QLabel(desc)
            d.setObjectName("cardDesc")
            d.setWordWrap(True)
            v.addWidget(d)
        return card, v

    def _page_shell(self, hint: str):
        """页面说明统一显示在顶栏副标题，页面里只放数据和操作。

        hint 仍由 PAGE_DEFS 传入，但不再在页内重复渲染一行同样的文字。
        """
        page = QWidget()
        page.setObjectName("pageCard")
        v = QVBoxLayout(page)
        v.setContentsMargins(theme.SP["xl"], theme.SP["l"], theme.SP["xl"],
                             theme.SP["l"])
        v.setSpacing(theme.SP["m"])
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
        s = theme.SP["s"]
        self._chat_layout.setContentsMargins(s, s, s, s)
        self._chat_layout.setSpacing(theme.SP["m"])
        self._chat_layout.addStretch(1)
        self._chat_scroll.setWidget(self._chat_list)
        self._bubble_rows = []
        v.addWidget(self._chat_scroll, 1)
        return page, self._refresh_chat

    def _send(self):
        text = self._chat_input.text().strip()
        if not text:
            self._notify("先在底部写一句话再发送", ok=False)
            return
        self._chat_input.clear()
        self.fw.send_command(text)
        self.switch_page(0)
        self._refresh_chat()
        self._notify("已发送指令，正在等凌零回复")

    def _pick_file(self):
        """弹系统文件框，把「读一下 "路径"」填进输入框（不自动发送）。

        不自动发送是有意的：用户选完文件往往还要补一句「总结要点」，
        直接发出去等于替他做了决定；而且读文件要过授权卡，节奏应该由人掌握。
        """
        from . import file_picker

        path = file_picker.pick_file(self)
        text = file_picker.compose_command(path) if path else ""
        if not text:
            return
        self._chat_input.setText(text)
        self._chat_input.setFocus()
        self._chat_input.setCursorPosition(len(text))
        self._notify("已选好文件，回车发送（可先在后面补一句要求）")

    def _confirm_new_conversation(self) -> bool:
        """清空对话记录前确认一次；单独成方法便于离屏测试替换。"""
        box = QMessageBox(self)
        box.setWindowTitle("新建对话")
        box.setText("清空当前对话记录，开始新一轮？")
        box.setInformativeText(
            "只清对话气泡与历史面板；长期记忆、学到的偏好和规则都保留。")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        return box.exec_() == QMessageBox.Yes

    def _new_conversation(self):
        """真的开一轮新对话：清掉历史并同步桌宠的历史面板。"""
        memory = getattr(self.fw, "memory", None)
        if memory is None or not getattr(memory, "history", None):
            self.switch_page(0)
            self._focus_command()
            self._notify("当前没有对话记录，可以直接开始")
            return
        if not self._confirm_new_conversation():
            self.switch_page(0)
            self._focus_command()
            self._notify("已取消，聊天记录未改动")
            return
        memory.clear_history()      # 只清对话，不删长期记忆
        try:
            self.fw.history_changed.emit()   # 桌宠历史面板 + 本窗口气泡
        except Exception:      # noqa: BLE001 信号异常时至少刷新本窗口
            self._refresh_chat()
        self.switch_page(0)
        self._focus_command()
        self._notify("已开始新对话：聊天记录清空，长期记忆与偏好保留")

    def _focus_command(self):
        self._chat_input.setFocus()
        self._chat_input.selectAll()

    def _refresh_chat(self):
        # 清空旧气泡（保留末尾的 stretch 弹簧）
        while self._chat_layout.count() > 1:
            item = self._chat_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                # 只 deleteLater 不够：事件循环处理前它仍挂在父控件上按旧几何
                # 绘制，切页/连续刷新时会看到新旧气泡和证据卡叠在一起。
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._bubble_rows = []
        history = list(getattr(self.fw.memory, "history", []) or [])[-60:]
        if not history:
            empty = QLabel("还没有对话。在底部命令栏输入一句话，或点桌宠直接说。")
            empty.setObjectName("emptyState")
            empty.setAlignment(Qt.AlignCenter)
            empty.setWordWrap(True)
            self._chat_layout.insertWidget(0, empty)
        else:
            last_day = None
            for item in history:
                ts = item.get("ts") or 0
                day = time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else None
                if day and day != last_day:
                    self._insert_day_separator(day)
                    last_day = day
                self._add_bubble(item.get("role", ""),
                                 str(item.get("content", "")), ts,
                                 item.get("meta") or {})
        # 最近一次本机读取的证据卡：只说「读完了」用户无从判断读了什么
        log = self._read_log()
        if log:
            self._insert_evidence_card(log[-1])
        # 滚动到底部
        QTimer.singleShot(0, self._scroll_chat_to_bottom)

    def _insert_day_separator(self, day: str):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(theme.SP["s"])
        for _ in range(2):
            line = QFrame()
            line.setObjectName("hline")
            line.setFrameShape(QFrame.HLine)
            line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            h.addWidget(line)
        lab = QLabel(day)
        lab.setObjectName("bubbleMeta")
        h.insertWidget(1, lab)
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, row)

    def _add_bubble(self, role: str, content: str, ts: float = 0,
                    meta: dict = None):
        is_me = role == "user"
        align = Qt.AlignRight if is_me else Qt.AlignLeft
        who = "你" if is_me else "凌零"
        # 回复出自哪边（本地模型 / 云端模型 / 离线规则）：标出来，
        # 否则用户分不清这句是本机跑的还是云端跑的
        provider = ""
        if not is_me and isinstance(meta, dict):
            provider = str(meta.get("provider") or "")
        stamp = ("  ·  " + time.strftime("%m-%d %H:%M", time.localtime(ts))
                 if ts else "")
        meta_text = who + (("  ·  " + provider) if provider else "") + stamp
        meta_label = QLabel(meta_text)
        meta_label.setObjectName("bubbleMeta")
        # 自己打的字原样显示；模型回复里的 <p> 是我们自己的排版约定，转成纯文本
        text = content if is_me else plain_text(content)
        bubble = QLabel(text)
        bubble.setTextFormat(Qt.PlainText)
        bubble.setObjectName("bubbleMe" if is_me else "bubbleAi")
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._bubble_rows.append((bubble, text))
        self._fit_bubble(bubble, content)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        col.addWidget(meta_label, 0, align)
        col.addWidget(bubble, 0, align)
        container = QWidget()
        container.setLayout(col)
        # 插到末尾弹簧之前
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, container)

    def _insert_evidence_card(self, ev: dict):
        """对话页里附一张卡：列了几项、读了几页、跳过什么、截断到多少字。"""
        lines = ["读取证据 · %s" % ev.get("name", "本机文件")]
        lines += self._evidence_lines([ev])
        body = "\n".join(lines)
        lab = QLabel(body)
        lab.setObjectName("grantEvidenceCard")
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # 和气泡同理：wordWrap 的 QLabel 在水平布局里 minimumSizeHint 很小，
        # 只给 maximumWidth 会被挤成一条窄列。这里按字体度量把宽高算死。
        fm = QFontMetrics(lab.font())
        width = min(620, self._bubble_max_w())
        pad_x = theme.DIM["bubble_pad_x"]
        need_h = fm.boundingRect(
            QRect(0, 0, max(80, width - pad_x * 2), 16777215),
            Qt.TextWordWrap | Qt.AlignLeft, body).height()
        lab.setFixedSize(width, need_h + theme.DIM["bubble_pad_y"])
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(lab, 0, Qt.AlignLeft)
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, row)

    def _fit_bubble(self, bubble: QLabel, text: str):
        """气泡宽和高都自己算死：宽度按文本实测收缩，最多占可视宽的 ratio。

        只写 setMaximumWidth + addStretch 不够：自动换行的 QLabel 在水平
        布局里 minimumSizeHint 很小，会被挤成一条窄列，长句反而更难读。
        高度也不能指望 heightForWidth——它传不过容器这层嵌套布局，实测
        两行的句子只给一行高，第二行被裁掉。
        """
        fm = QFontMetrics(bubble.font())
        pad_x = theme.DIM["bubble_pad_x"]
        text_x = theme.DIM["bubble_text_x"]
        natural = fm.boundingRect(
            QRect(0, 0, 16777215, 16777215),
            Qt.TextWordWrap | Qt.AlignLeft, text).width() + pad_x
        width = max(theme.SP["xxl"], min(natural, self._bubble_max_w()))
        inner = max(20, width - text_x)
        lines = fm.boundingRect(
            QRect(0, 0, inner, 16777215),
            Qt.TextWordWrap | Qt.AlignLeft, text).height()
        bubble.setFixedSize(width, lines + theme.DIM["bubble_pad_y"])

    def _bubble_max_w(self) -> int:
        base = self._chat_scroll.viewport().width() or self.width()
        floor = min(theme.DIM["bubble_min_w"], max(200, base - 40))
        return max(int(base * theme.DIM["bubble_max_ratio"]), floor)

    def _refit_bubbles(self):
        for bubble, text in getattr(self, "_bubble_rows", []):
            self._fit_bubble(bubble, text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._refit_bubbles)

    def _scroll_chat_to_bottom(self):
        bar = self._chat_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ---------------- 页面：偏好 ----------------
    def _build_prefs_page(self, hint):
        page, v = self._page_shell(hint)

        # 明细整块放进容器：开关关掉时一并藏起来，只留一句「去哪开」。
        # 偏好本身照常学习、照常影响回复，只是不摆在你眼前。
        self._prefs_body = QWidget()
        bv = QVBoxLayout(self._prefs_body)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(8)
        self._prefs_table = self._table(
            ["偏好", "当前值", "来源", "置信度", "命中", "证据数"])
        # 六列等宽会把「当前值」挤成省略号；让它独占剩余宽度，其余按内容收
        hh = self._prefs_table.horizontalHeader()
        for col in (0, 2, 3, 4, 5):
            hh.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        bv.addWidget(self._prefs_table, 1)
        bv.addWidget(self._button_row(
            self._ghost("删除选中", self._delete_pref, "trash"),
            self._ghost("清空全部偏好", self._clear_prefs, "x-circle")))
        v.addWidget(self._prefs_body, 1)

        self._prefs_hidden = QLabel(
            "偏好仍在后台自动学习，只是默认不在界面上展示。\n"
            "想查看、删除或清空，去「关于与设置」打开「在界面上显示偏好」。")
        self._prefs_hidden.setObjectName("grantEmpty")
        self._prefs_hidden.setWordWrap(True)
        self._prefs_hidden.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        v.addWidget(self._prefs_hidden, 1)
        return page, self._refresh_prefs

    def _selected_key(self, table, col=0):
        rows = table.selectionModel().selectedRows() if table.selectionModel() else []
        if not rows:
            return None
        item = table.item(rows[0].row(), col)
        return item.text() if item else None

    def _refresh_prefs(self):
        show = bool(getattr(config, "PREFERENCE_PANEL", False))
        self._prefs_body.setVisible(show)
        self._prefs_hidden.setVisible(not show)
        if not show:
            return
        store = getattr(self.fw, "prefs", None)
        items = store.all() if store is not None else []
        self._prefs_table.setRowCount(len(items))
        for i, p in enumerate(items):
            # 旧的落盘文件没有 source 字段，一律当关键词命中（规则层先存在）
            source = "模型归纳" if p.get("source") == "llm" else "规则命中"
            values = [p.get("key", ""), p.get("value", ""), source,
                      p.get("confidence", ""), p.get("hits", 0),
                      len(p.get("evidence") or [])]
            for j, val in enumerate(values):
                self._prefs_table.setItem(i, j, _cell(val))
        self._set_empty_hint(self._prefs_table, len(items),
                             "还没有学到偏好。说一句「我喜欢深色 PPT」试试。")

    def _delete_pref(self):
        key = self._selected_key(self._prefs_table)
        store = getattr(self.fw, "prefs", None)
        if not key or store is None:
            self._notify("请先在表格里选中一行偏好", ok=False)
            return
        before = len(store.all())
        store.forget(key)
        self._refresh_prefs()
        removed = before - len(store.all())
        self._notify("已删除偏好「%s」" % key if removed
                     else "没有删掉任何偏好，请刷新后重试", ok=bool(removed))

    def _clear_prefs(self):
        store = getattr(self.fw, "prefs", None)
        items = store.all() if store is not None else []
        if not items:
            self._notify("偏好本来就是空的，无需清空")
            return
        if not self._confirm(
                "清空偏好", "确定删除全部 %d 条偏好？" % len(items),
                "只影响学到的偏好，不动对话记录和规则；此操作不可撤销。"):
            self._notify("已取消，未清空偏好")
            return
        store.clear()
        self._refresh_prefs()
        self._notify("已清空 %d 条偏好" % len(items))

    # ---------------- 页面：规则 ----------------
    def _build_rules_page(self, hint):
        page, v = self._page_shell(hint)
        self._rules_table = self._table(
            ["规则族", "状态", "置信度", "触发", "成功", "失败"])
        v.addWidget(self._rules_table, 1)
        v.addWidget(self._button_row(
            self._ghost("删除选中规则", self._delete_rule, "trash")))
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
        if not family or store is None:
            self._notify("请先在表格里选中一行规则", ok=False)
            return
        before = len(store.all())
        for r in list(store.all()):
            if r.get("family") == family:
                store.forget(r.get("id"))
        self._refresh_rules()
        removed = before - len(store.all())
        self._notify("已删除规则族「%s」（%d 条）" % (family, removed)
                     if removed else "没有删掉任何规则，请刷新后重试",
                     ok=bool(removed))

    # ---------------- 页面：轨迹 ----------------
    def _build_trace_page(self, hint):
        page, v = self._page_shell(hint)
        self._trace_table = self._table(
            ["轮次", "事件", "动作", "失败", "tokens", "耗时(ms)"])
        self._trace_table.itemSelectionChanged.connect(self._show_turn_detail)
        self._detail_view = QTextBrowser()
        self._detail_view.setObjectName("detailView")
        # 可拖分割条：表格里轮次多时把详情压小，反之放大，不用二选一
        split = QSplitter(Qt.Vertical)
        split.setObjectName("traceSplit")
        split.addWidget(self._trace_table)
        split.addWidget(self._detail_view)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([240, 330])
        v.addWidget(split, 1)
        v.addWidget(self._button_row(
            self._ghost("生成并打开时间线", self._generate_and_open_timeline,
                        "clock")))
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
            self._detail_view.setPlainText("轨迹子系统未启用：可在设置里打开「事件轨迹」。")
            return
        turn_id = self._selected_key(self._trace_table)
        if not turn_id:
            s = traj.summarize()
            if not s["turns"]:
                self._detail_view.setPlainText(
                    "还没有执行轨迹。发一句指令就会产生第一轮。")
                return
            self._detail_view.setPlainText(
                "共 %d 轮 / %d 事件 / 失败率 %.1f%% / token %d\n哈希链完整：%s"
                "\n\n点上面任意一行看这一轮的事件明细。"
                % (s["turns"], s["events"], s["failure_rate"] * 100,
                   s["tokens"], "是" if s["chain_ok"] else "否"))
            return
        events = traj.events(turn_id=turn_id)
        if not events:
            self._detail_view.setPlainText("这一轮没有记录到事件。")
            return
        lines = ["轮次 %s" % turn_id]
        for e in events:
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

    def _generate_and_open_timeline(self):
        """一个按钮做完「生成 + 打开」，并给出可见结果。

        以前拆成两个按钮：「生成时间线」只写文件、返回值被 PyQt 丢弃，
        点了像没反应；「打开时间线」失败时又完全静默。现在合并并反馈。
        """
        path = self.generate_timeline()
        if not path:
            self._notify("时间线生成失败：轨迹子系统未启用或渲染出错，详见日志",
                         ok=False)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        self._notify("已生成并打开时间线：%s" % path)

    def _open_timeline(self):
        """保留给外部调用的兼容入口，界面上不再单独放这个按钮。"""
        self._generate_and_open_timeline()

    # ---------------- 页面：交付 ----------------
    def _build_deliveries_page(self, hint):
        page, v = self._page_shell(hint)
        self._delivery_list = QListWidget()
        self._delivery_list.setObjectName("deliveryList")
        self._delivery_list.itemDoubleClicked.connect(self._open_delivery)
        v.addWidget(self._delivery_list, 1)
        v.addWidget(self._button_row(
            self._ghost("打开选中文件", self._open_delivery, "open")))
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
        if not items or row < 0:
            self._notify("请先在列表里选中一份交付", ok=False)
            return
        idx = len(items) - 1 - row
        if not 0 <= idx < len(items):
            self._notify("这条交付记录已失效，请点顶栏「刷新本页」", ok=False)
            return
        d = items[idx]
        path = d.get("path", "")
        if path and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            self._notify("已打开：%s" % d.get("name", path))
        else:
            self._notify("文件已不在原位置：%s" % (path or "未记录路径"),
                         ok=False)

    # ---------------- 页面：本机访问（本次会话授权） ----------------
    def _build_grants_page(self, hint):
        """本次放开了哪些路径、实际读了什么、支持什么格式，全在一页可撤。"""
        page, v = self._page_shell(hint)

        self._grants_empty = QLabel("")
        self._grants_empty.setObjectName("grantEmpty")
        self._grants_empty.setWordWrap(True)
        v.addWidget(self._grants_empty)
        self._grants_table = self._table(("档位", "放开的路径", "放开时间"))
        v.addWidget(self._grants_table, 2)

        cap = QLabel("最近实际读了什么")
        cap.setObjectName("cardTitle")
        v.addWidget(cap)
        self._grants_evidence = QLabel("")
        self._grants_evidence.setObjectName("grantEvidenceCard")
        self._grants_evidence.setWordWrap(True)
        v.addWidget(self._grants_evidence, 1)

        # 格式文案唯一出处就是判据表，这里不再手写第二份
        self._grants_formats = QLabel("支持格式：" + describe_formats())
        self._grants_formats.setObjectName("grantFormats")
        self._grants_formats.setWordWrap(True)
        v.addWidget(self._grants_formats)

        v.addWidget(self._button_row(
            self._ghost("全部撤销", self._revoke_all_grants, "trash")))
        return page, self._refresh_grants

    @staticmethod
    def _evidence_lines(entries) -> list:
        """把读取证据压成人话：列了几项、读了几页、跳过什么、截断到多少字。"""
        lines = []
        for e in entries:
            bits = []
            if e.get("kind"):
                bits.append("类型 " + str(e["kind"]))
            if e.get("kind") == "batch":
                bits.append("批量共 %s 份，实读 %s 份"
                            % (e.get("total", "?"), e.get("listed", "?")))
                if e.get("limit"):
                    bits.append("每份最多 %s 字" % e["limit"])
            elif e.get("total") is not None:
                bits.append("目录共 %s 项，列了 %s 项"
                            % (e["total"], e.get("listed", "?")))
            if e.get("pages") is not None:
                bits.append("读了 %s 页" % e["pages"])
            if e.get("chars") is not None:
                bits.append("截到 %s 字" % e["chars"])
            if e.get("skipped"):
                bits.append("跳过 " + str(e["skipped"])[:60])
            if e.get("note"):
                bits.append(str(e["note"])[:80])
            if e.get("error"):
                bits.append("失败：" + str(e["error"])[:80])
            lines.append("• %s：%s" % (e.get("name", "本机文件"),
                                       "，".join(bits) or "已读入上下文"))
        return lines

    def _read_log(self) -> list:
        try:
            return list(self.fw.read_log() or [])
        except Exception:      # noqa: BLE001 老版本 Framework 可能没有这个方法
            return []

    def _grant_entries(self) -> list:
        try:
            return list((self.fw.grant_state() or {}).get("grants") or [])
        except Exception:
            return []

    def _refresh_grants(self):
        grants = self._grant_entries()
        self._grants_table.setRowCount(len(grants))
        for row, g in enumerate(grants):
            level = "整个文件夹" if g.get("level") == "tree" else "仅这一个"
            ts = g.get("granted_at") or 0
            when = time.strftime("%m-%d %H:%M:%S", time.localtime(ts)) \
                if ts else "刚刚"
            for col, text in enumerate((level, g.get("path", ""), when)):
                self._grants_table.setItem(row, col, _cell(text))
        self._grants_empty.setText(
            "当前只放开默认目录（桌面 / 文档 / 下载），根外路径仍要逐次确认。"
            if not grants else
            "本次会话已放开 %d 条；全部只在本次运行有效，重启后失效。" % len(grants))
        log = self._read_log()
        self._grants_evidence.setText(
            "\n".join(self._evidence_lines(log[-5:][::-1]))
            if log else "还没有读过本机文件。")

    def _revoke_all_grants(self):
        """撤销后判据只看空的授权记录：同一路径必须重新弹卡。"""
        try:
            cleared = int(self.fw.revoke_grants() or 0)
        except Exception as e:      # noqa: BLE001
            self._notify("撤销失败：%s" % e, ok=False)
            return
        self._refresh_grants()
        self._refresh_status()
        self._notify("已撤销 %d 条本机访问授权；再读同一路径会重新弹卡" % cleared)

    # ---------------- 页面：网页调试台（内嵌浏览器里那份网页）----------------
    def _build_web_page(self, hint):
        """工作台里的网页台与浏览器访问的是同一个 URL、同一份后端。

        这里只搭骨架：真正的 QWebEngineView 在第一次切到这一页时才创建。
        两个原因：离线测试环境不该为一个不看的页面拉起浏览器内核；
        没装 PyQtWebEngine 时要能降级成说明卡，而不是让整个工作台起不来。
        """
        from framework.api_service import API_BASE

        page, v = self._page_shell(hint)
        self._web_base = API_BASE

        self._web_host = QWidget(page)
        self._web_host.setObjectName("webHost")
        host_layout = QVBoxLayout(self._web_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self._web_host, 1)

        self._web_placeholder = QLabel("")
        self._web_placeholder.setObjectName("webPlaceholder")
        self._web_placeholder.setWordWrap(True)
        host_layout.addWidget(self._web_placeholder)

        self._web_start_btn = self._ghost(
            "启动本地服务", self._start_web_service, "play")
        self._web_start_btn.hide()
        host_layout.addWidget(self._web_start_btn)

        self._web_view = None
        self._web_ready = False
        self._web_retries = 0
        # 重试不能挂在 QTimer.singleShot 上：它不归任何窗口管，窗口关了还会
        # 继续回调到已销毁的控件上。测试里这条最长会追 10 次、跨 12 秒，
        # 打完正好落在别的用例中间，Windows 上直接报堆损坏（0xc0000374）。
        # 挂成自己的子定时器，随窗口一起消失；detach() 里再主动停一次。
        self._web_timer = QTimer(self)
        self._web_timer.setSingleShot(True)
        self._web_timer.timeout.connect(self._retry_web_view)
        return page, self._refresh_web

    def _start_web_service(self):
        """用户明确点了才起本地服务；不偷偷在切页时拉起后台进程。"""
        from framework.api_service import api_alive, start_api_in_thread

        self._web_start_btn.hide()
        self._web_placeholder.setText("正在启动本地网页台……")
        start_api_in_thread()
        self._web_retries = 0
        self._web_timer.start(1200)

    def _retry_web_view(self):
        """后台线程起服务需要时间，用定时器等非阻塞重试，不卡 Qt。"""
        from framework.api_service import api_alive

        self._web_retries += 1
        if api_alive():
            self._web_ready = False
            self._try_init_web_view()
            return
        if self._web_retries >= 10:
            self._web_placeholder.setText(
                "本地网页台没能启动。请检查 8000 端口是否被占用，"
                "或手动运行 python main.py --api 后刷新本页。")
            self._web_start_btn.show()
            return
        self._web_timer.start(1200)

    def _try_init_web_view(self):
        """懒加载内嵌浏览器：只在切到网页台时尝试，失败留说明不崩。"""
        if self._web_ready:
            return
        try:
            from PyQt5.QtWebEngineWidgets import QWebEngineView
        except ImportError as exc:
            self._web_ready = True
            # 这条分支有两种完全不同的原因，文案不能共用一句：
            # 真没装才让人 pip install；装了但起不来还喊装包，就是假话。
            if importlib.util.find_spec("PyQt5.QtWebEngineWidgets") is None:
                self._web_placeholder.setText(
                    "这个环境没有安装 PyQtWebEngine，工作台里无法内嵌网页台。\n"
                    "请执行 pip install PyQtWebEngine 后重启，"
                    "或直接用浏览器打开 %s/ 。" % self._web_base)
            else:
                self._web_placeholder.setText(
                    "PyQtWebEngine 是装着的，但内嵌浏览器没能初始化：\n%s\n"
                    "这一版要求用 python main.py（或启动助手.bat）启动，"
                    "启动时要在 QApplication 之前设好 AA_ShareOpenGLContexts；"
                    "临时可以先用浏览器打开 %s/ 。"
                    % (exc, self._web_base))
            logger.warning("[Workspace] 内嵌网页台降级: %s" % exc)
            return

        # 没起服务不自动启动：给一个真能点的按钮，点了再走启动 + 重试
        from framework.api_service import api_alive
        if not api_alive():
            self._web_placeholder.setText(
                "本地网页台还没启动。点下面的按钮在后台起服务，"
                "或者先在命令行运行 python main.py --api。")
            self._web_start_btn.show()
            return

        self._web_ready = True
        from PyQt5.QtCore import QUrl
        try:
            view = QWebEngineView(self._web_host)
            view.setObjectName("webView")
            view.urlChanged.connect(self._on_web_url_changed)
            self._web_host.layout().addWidget(view)
            view.setUrl(QUrl(self._web_base + "/"))
        except Exception as exc:      # noqa: BLE001
            # 这个函数是页面切换信号里调的，槽里的未捕获异常会被 PyQt 直接
            # abort 掉整个进程（授权卡那条踩过一次，同样的坑不踩第二次）。
            logger.warning("[Workspace] 内嵌浏览器创建失败: %s" % exc)
            self._web_placeholder.setText(
                "内嵌浏览器创建失败：%s\n可以先用浏览器打开 %s/ 。"
                % (exc, self._web_base))
            return
        self._web_view = view
        self._web_timer.stop()
        self._web_placeholder.hide()
        self._notify("网页台已内嵌：与浏览器访问的是同一份网页")

    def _on_web_url_changed(self, _url=None):
        """网页每次导航后把当前主题带过去，避免工作台深色/网页浅色打架。"""
        self._sync_web_theme()

    def _sync_web_theme(self):
        view = getattr(self, "_web_view", None)
        if view is None:
            return
        try:
            # QtWebEngine 里 dataset.xxx 赋值不可靠，用 setAttribute 直写
            view.page().runJavaScript(
                "document.documentElement.setAttribute('data-theme','%s');"
                % self._theme)
        except Exception as e:      # noqa: BLE001 页面还没加载完不算错
            logger.debug("[Workspace] 网页主题同步跳过: %s" % e)

    def _refresh_web(self):
        """每次切到网页台都走这里：先懒加载，再刷新状态提示。"""
        self._try_init_web_view()
        if self._web_view is not None:
            self._web_placeholder.setText("")
            self._web_start_btn.hide()
            self._sync_web_theme()
        elif not self._web_placeholder.text():
            self._web_placeholder.setText(
                "正在准备网页台……若长时间没反应，点「刷新本页」重试。")

    # ---------------- 页面：关于 ----------------
    def _build_about_page(self, hint):
        page, v = self._page_shell(hint)
        scroll = QScrollArea()
        scroll.setObjectName("aboutScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 0, theme.SP["s"], 0)
        bv.setSpacing(theme.SP["m"])

        # 开关卡：五个能力直接在这里翻，不用去翻 config 文件再重启
        card, cv = self._card("当前开关",
                              "点按钮就翻这个能力：立即生效，并写进本机 user_settings.json，"
                              "下次启动读回同样的值。")
        self._build_switch_rows(cv)
        bv.addWidget(card)

        card, cv = self._card("界面操作",
                              "左边七个页签切页面，顶栏按钮都带文字，"
                              "底部输入框回车发送。每个按钮点完都会在状态栏右侧留一句结果。"
                              "本机访问页能撤销本次会话放开的路径。"
                              "「执行轨迹」是工程面板，不占导航，入口在下面那张卡里。")
        for line in OPERATION_ROWS:
            lab = QLabel("·  " + line)
            lab.setObjectName("cardDesc")
            lab.setWordWrap(True)
            cv.addWidget(lab)
        bv.addWidget(card)

        card, cv = self._card("执行轨迹（工程面板）",
                              "每轮的事件、动作、失败与状态哈希链都记在这里，还能导出一份时间线网页。"
                              "排障和面试讲实现时有用，日常操作不需要看，所以没放进左侧导航。")
        self._trace_summary_label = QLabel("")
        self._trace_summary_label.setObjectName("cardDesc")
        self._trace_summary_label.setWordWrap(True)
        cv.addWidget(self._trace_summary_label)
        cv.addWidget(self._button_row(
            self._ghost("打开执行轨迹",
                        lambda: self.switch_page(page_index("trace")),
                        "trace")))
        bv.addWidget(card)

        card, cv = self._card("数据位置",
                              "轨迹、偏好、规则保存在下面这个目录，均不含用户原话明文；"
                              "可用环境变量 LINGLING_DATA_DIR 换成别的目录做隔离。")
        self._data_dir_label = QLabel("")
        self._data_dir_label.setObjectName("cardDesc")
        self._data_dir_label.setProperty("role", "mono")
        self._data_dir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._data_dir_label.setWordWrap(True)
        cv.addWidget(self._data_dir_label)
        bv.addWidget(card)

        bv.addStretch(1)
        scroll.setWidget(body)
        v.addWidget(scroll, 1)
        v.addWidget(self._button_row(
            self._ghost("打开设置窗口", self._open_settings, "settings"),
            self._ghost("查看完整使用说明", self._show_help, "about")))
        self._refresh_about()
        return page, self._refresh_about

    def _build_switch_rows(self, box):
        """七行开关只建一次；刷新时改按钮的文字和状态，不重建控件。

        重建会把旧按钮留在对象树里（deleteLater 要等事件循环），
        翻页几次就攒出一堆点得着却看不见的重复按钮。
        """
        self._switch_btns = {}
        self._switch_names = {}
        for name, attr, desc in SWITCH_ROWS:
            self._switch_names[attr] = name
            box.addWidget(self._switch_row(name, attr, desc))

    def _switch_row(self, name: str, attr: str, desc: str) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(theme.SP["m"])
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        lab = QLabel(name)
        lab.setObjectName("switchName")
        d = QLabel(desc)
        d.setObjectName("cardDesc")
        d.setWordWrap(True)
        col.addWidget(lab)
        col.addWidget(d)
        btn = QPushButton()
        btn.setObjectName("switchToggle")
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        # 连在 clicked 而不是 toggled：程序自己 setChecked 时不该被当成用户操作存盘
        btn.clicked.connect(
            lambda _=False, a=attr: self._toggle_switch(a))
        self._switch_btns[attr] = btn
        self._paint_switch(attr)
        h.addLayout(col, 1)
        h.addWidget(btn, 0, Qt.AlignVCenter)
        return row

    def _paint_switch(self, attr: str):
        """按钮长相跟着 config 走：读的是真值，不是上一次点击的记忆。"""
        btn = self._switch_btns[attr]
        on = bool(getattr(config, attr))
        btn.setChecked(on)
        btn.setText("开启" if on else "关闭")
        btn.setProperty("role", "good" if on else "bad")
        btn.setToolTip("点击%s「%s」" % ("关闭" if on else "开启",
                                        self._switch_names[attr]))
        _repolish(btn)

    def _toggle_switch(self, attr: str):
        want = not bool(getattr(config, attr))
        ok, note = self.fw.set_capability(attr, want)
        self._paint_switch(attr)
        if attr == "PREFERENCE_PANEL":
            # 开关一翻，偏好页当场跟着显隐，不用等切页才生效
            self._refresh_prefs()
        self._notify("%s：%s" % (self._switch_names[attr], note), ok=ok)
        self._refresh_status()

    def help_text(self) -> str:
        """完整使用说明：只在对话框里展示，不再占满整页当文字墙。"""
        rows = "\n".join("  " + line for line in OPERATION_ROWS)
        return (
            "凌零 · 桌面 AI 数字员工 %s\n\n"
            "这个窗口能点的地方：左侧七个页签（含「本机访问」）、顶栏带文字按钮、"
            "底部输入框（回车发送）、关于页的七个能力开关。"
            "每个页面顶部写了一句「这页是干嘛的」，"
            "每个按钮点完都会在右下角状态栏留一句结果（绿色=成功，红色=没做成）。\n\n"
            "左侧导航\n"
            "  对话：看聊天记录，底部输入框回车发送\n"
            "  我的偏好 / 学到的规则：看凌零记下的东西，可删除\n"
            "  我的交付：双击或点「打开选中文件」打开生成过的文档\n"
            "  本机访问：本次放开了哪些路径、最近实际读了什么；「全部撤销」"
            "后同一路径会重新弹卡。授权只在本次运行有效，重启后失效\n"
            "  关于与设置：七个能力开关（可直接点）、执行轨迹入口、数据位置与本说明\n"
            "  执行轨迹不在导航里：它是工程面板（事件 / 动作 / 失败 / 状态哈希链），"
            "从「关于与设置」里点「打开执行轨迹」进，那一页能生成并打开时间线网页\n\n"
            "按钮都做什么\n%s\n\n"
            "清空 / 删除类操作会先弹确认框，取消也会给提示\n\n"
            "没有的功能（所以界面上也没有按钮）\n"
            "  文件引用前缀、分支选择、访问级别、模型下拉、语音按钮："
            "本项目没有对应能力，不放空壳控件\n"
            "  语音、选文件、技能、撤销等走桌宠右键菜单，或直接用自然语言说\n\n"
            "隐私\n"
            "  聊天记录只写在本机数据目录；仓库里不含你的对话内容，"
            "data/ 已在 .gitignore 中。\n"
            % (VERSION, rows))

    def _show_help(self):
        box = QMessageBox(self)
        box.setWindowTitle("使用说明")
        box.setTextFormat(Qt.PlainText)
        box.setText(self.help_text())
        box.setStandardButtons(QMessageBox.Ok)
        box.exec_()
        self._notify("已显示使用说明")

    def _open_settings(self):
        self._emit_or_notify(
            self.settings_requested, "已请求打开设置窗口",
            "设置窗口没有接线：请从桌宠右键菜单或托盘「显示设置」打开")

    def _refresh_about(self):
        for attr in self._switch_btns:
            self._paint_switch(attr)
        try:
            self._data_dir_label.setText(app_data_dir())
        except Exception:      # noqa: BLE001 路径拿不到不影响本页其余内容
            self._data_dir_label.setText("（数据目录读取失败）")
        # 轨迹摘要放在这里说人话：不打开那一页也知道有没有东西可看
        try:
            traj = getattr(self.fw, "trajectory", None)
            if traj is None:
                self._trace_summary_label.setText(
                    "轨迹子系统当前关闭；在下面开关里打开「事件轨迹」后才会记录。")
            else:
                s = traj.summarize()
                self._trace_summary_label.setText(
                    "本机已记录 %d 轮 / %d 个事件，失败率 %.1f%%。"
                    % (s["turns"], s["events"], s["failure_rate"] * 100)
                    if s["turns"] else "还没有记录：发一句指令就会产生第一轮。")
        except Exception as e:      # noqa: BLE001 摘要读不到不影响开关与路径
            logger.warning(f"[Workspace] 轨迹摘要读取失败: {e}")
            self._trace_summary_label.setText("轨迹摘要暂时读不到，点进去看即可。")

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
        self._feedback_timer.stop()
        # 网页台的重试链也得停：窗口都关了还在等「服务起来没有」没有意义，
        # 回调打在一个已 hide/待销毁的窗口上只会出事。
        self._web_timer.stop()

    def switch_page(self, index: int):
        index = max(0, min(index, len(PAGE_DEFS) - 1))
        self._stack.setCurrentIndex(index)
        btn = self._nav_buttons[index]
        if btn is not None:
            btn.setChecked(True)
        else:
            # 隐藏页没有自己的按钮：整组取消选中，否则顶栏写着「执行轨迹」，
            # 左边却还亮着上一次那一项。互斥组要先关掉互斥才能全不选。
            self._nav_group.setExclusive(False)
            for b in self._nav_buttons:
                if b is not None:
                    b.setChecked(False)
            self._nav_group.setExclusive(True)
        self._title.setText(PAGE_DEFS[index][1])
        self._page_hint.setText(PAGE_DEFS[index][2])
        self.refresh_current()

    def refresh_current(self):
        key = PAGE_DEFS[self._stack.currentIndex()][0]
        try:
            self._page_refresh[key]()
        except Exception as e:      # noqa: BLE001 单个页面异常不拖垮整个窗口
            logger.warning(f"[Workspace] 刷新 {key} 页失败: {e}")
        self._refresh_status()

    def _set_chip(self, key: str, text: str, role: str = ""):
        chip = self._chips[key]
        chip.setText(text)
        if chip.property("role") != role:
            chip.setProperty("role", role)
            _repolish(chip)

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

        fail = s["failure_rate"] * 100
        self._set_chip("turns", "轮次 %d" % s["turns"])
        self._set_chip("events", "事件 %d" % s["events"])
        self._set_chip("fail", "失败率 %.1f%%" % fail,
                       "bad" if fail >= 20 else ("warn" if fail > 0 else ""))
        self._set_chip("tokens", "token %d" % s["tokens"])
        self._set_chip("prefs", "偏好 %d" % n_prefs)
        self._set_chip("rules", "生效规则 %d" % n_rules)
        self._set_chip("chain", "哈希链 %s" % ("完整" if s["chain_ok"] else "断开"),
                       "good" if s["chain_ok"] else "bad")

        ai = (self.fw.ai_status_text()
              if hasattr(self.fw, "ai_status_text") else "")
        self._status_label.setText(ai)
        self._status_full = "%s  ·  %s" % (
            "  ·  ".join(c.text() for c in self._chips.values()), ai)
        n_grants = len(self._grant_entries())
        self._nav_grants_btn.setText("  本机访问：放开 %d 条" % n_grants)
        self._nav_groups.setText(
            "任务：待办 %d 项\n交付：已生成 %d 份\n偏好：%d 条 · 生效规则：%d 条"
            "\n本机访问：放开 %d 条"
            % (n_todos, n_deliv, n_prefs, n_rules, n_grants))

    def toggle_theme(self):
        self._apply_theme("dark" if self._theme == "light" else "light")
        self._refresh_chat()
        self._refresh_about()
        self.save_state()
        self._notify("已切换到%s主题（工作台与桌宠同步换肤）" %
                     ("浅色" if self._theme == "light" else "深色"))

    def _apply_theme(self, name: str):
        """主题只有一个来源：ui/theme.py。这里只记下选择并触发全局换肤。"""
        self._theme = theme.set_theme(
            name if name in ("light", "dark") else "dark")
        self._recolor_icons()
        self._sync_web_theme()      # 工作台换肤时内嵌网页台一起换
        if hasattr(self, "_theme_btn"):
            self._theme_icon_name = "moon" if self._theme == "dark" else "sun"
            self._theme_btn.setIcon(
                icon(self._theme_icon_name, theme.T("TEXT2", self._theme), 15))
            self._icon_buttons[self._theme_btn] = (
                self._theme_icon_name, "muted")

    def _recolor_icons(self):
        """矢量图标不吃 QSS，主题切换后要按新的令牌色重画一遍。"""
        text_color, nav_color = _icon_palette(self._theme)
        muted = theme.T("TEXT2", self._theme)
        accent = theme.T("ACCENT_TEXT", self._theme)
        for btn, (name, kind) in list(self._icon_buttons.items()):
            color = {"nav": nav_color, "accent": accent, "muted": muted,
                     "white": accent}.get(kind, text_color)
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
        self._notify("窗口已%s" % ("最大化" if self._max_state else "还原"))

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
