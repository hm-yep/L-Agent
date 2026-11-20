# -*- coding: utf-8 -*-
"""凌零唯一的设计令牌源：颜色 / 间距 / 圆角 / 层级 + 一份全局样式表。

为什么要有这个文件：历史上样式分三处各写各的——工作台自己一套
``_QSSTEMPLATE``、桌宠侧一套 ``style.qss``（而且从没全局生效）、预览窗
第三套。结果是同一个产品出现两种语言（深蓝黑 vs 米黄橙），滚动条还是
Qt 默认粗灰条。这里把「一个界面元素该用什么颜色、多大圆角、隔多远」
收敛成一份令牌，所有窗口只读令牌，不再自己写十六进制。

用法：
    from ui import theme
    theme.install(app)                 # main.py 建好 QApplication 后
    theme.set_theme("light")           # 任意处切主题，全体窗口同步换肤
    theme.T("ACCENT")                  # 取当前主题的某个色值
    theme.build_qss(theme.DARK)        # 需要离线拼样式时

深色是默认基调（专业工具风），浅色保留但同样由令牌驱动。桌宠浮层用
``FLOAT_*`` 半透明深底 + 浅字：贴在任意壁纸上都有足够对比度，也不会在
浅色主题下变成一块刺眼的白卡片。
"""

from __future__ import annotations

from .typography import (FONT_STACK, MONO_STACK, SZ, WT, apply_qss_tokens,
                          qss_tokens)

# ------------------------------------------------------------
# 主题令牌
# ------------------------------------------------------------
DARK = dict(
    # 表面层级：底 -> 卡片 -> 抬升 -> 更高，一共四档，不再随手加灰
    # 整体朝中性近黑收敛（对标 TraeCode 深色工具风），去掉偏蓝的底色
    BG="#0F1115", SURFACE="#17191D", SURFACE2="#1E2024", ELEVATED="#24272E",
    BORDER="#26292F", BORDER_STRONG="#343840",
    # 文字三档：主文 / 次要 / 说明
    TEXT="#E6E6E6", TEXT2="#A0A4AB", TEXT3="#6B6F76",
    # 强调色只留一处克制的蓝（代码 / 链接 / 主操作）
    ACCENT="#4F8CFF", ACCENT_HOVER="#6BA0FF", ACCENT_TEXT="#FFFFFF",
    ACCENT_SOFT="#1B2433", ACCENT_LINE="#2E4368",
    GOOD="#4ADE80", WARN="#E0A03C", BAD="#F87171", INFO="#4F8CFF",
    HOVER="#20242A", PRESS="#262B32", SELECTED="#1E2A40",
    # 左侧导航比主区更暗，形成「外壳 / 内容」的层次
    NAV="#0C0E11", NAV_TEXT="#B9C0CB", NAV_ACTIVE="#191D23",
    # 聊天气泡：我方=强调色低饱和底，对方=表面2
    BUBBLE_ME="#1E2A40", BUBBLE_ME_TEXT="#DCE8FF", BUBBLE_ME_BORDER="#2F4874",
    BUBBLE_AI="#1E2024", BUBBLE_AI_TEXT="#E6E6E6", BUBBLE_AI_BORDER="#26292F",
    CODE_BG="#131519",
    # 浮层（桌宠侧气泡/输入条/历史面板/计划卡）
    FLOAT_BG="rgba(26, 29, 35, 238)", FLOAT_BG2="rgba(32, 36, 44, 242)",
    FLOAT_BORDER="rgba(255, 255, 255, 30)", FLOAT_TEXT="#E8EBF0",
    FLOAT_MUTED="#A2AAB7",
    SCROLL="#3A414C", SCROLL_HOVER="#4C5563", SCROLL_BG="transparent",
    SHADOW="rgba(0, 0, 0, 100)",
    TABLE_ALT="#1C1F25", TABLE_HEAD="#232830",
)

LIGHT = dict(
    BG="#F6F7F9", SURFACE="#FFFFFF", SURFACE2="#F2F4F7", ELEVATED="#E9ECF1",
    BORDER="#DFE3E9", BORDER_STRONG="#C8CED8",
    TEXT="#1B1F26", TEXT2="#5A6371", TEXT3="#8A93A1",
    ACCENT="#2F6FED", ACCENT_HOVER="#2862D2", ACCENT_TEXT="#FFFFFF",
    ACCENT_SOFT="#EAF1FF", ACCENT_LINE="#C3D6FB",
    GOOD="#0F7B58", WARN="#9A6A12", BAD="#B42318", INFO="#2F6FED",
    HOVER="#EEF1F5", PRESS="#E4E8EF", SELECTED="#DCE8FF",
    NAV="#F0F2F6", NAV_TEXT="#4A5260", NAV_ACTIVE="#E4E8EF",
    BUBBLE_ME="#2F6FED", BUBBLE_ME_TEXT="#FFFFFF", BUBBLE_ME_BORDER="#2862D2",
    BUBBLE_AI="#F0F2F5", BUBBLE_AI_TEXT="#1B1F26", BUBBLE_AI_BORDER="#DFE3E9",
    CODE_BG="#F2F4F7",
    # 浅色主题下浮层仍是半透明深底：桌宠贴在桌面上，深底才压得住壁纸
    FLOAT_BG="rgba(26, 29, 35, 235)", FLOAT_BG2="rgba(32, 36, 44, 238)",
    FLOAT_BORDER="rgba(255, 255, 255, 30)", FLOAT_TEXT="#E8EBF0",
    FLOAT_MUTED="#A2AAB7",
    SCROLL="#C2C9D3", SCROLL_HOVER="#A8B2C0", SCROLL_BG="transparent",
    SHADOW="rgba(0, 0, 0, 40)",
    TABLE_ALT="#FAFBFC", TABLE_HEAD="#F0F2F6",
)

PALETTES = {"dark": DARK, "light": LIGHT}

# 间距阶梯：4 的倍数，控件之间的距离只允许取这几档
SP = dict(xs=4, s=8, m=12, l=16, xl=20, xxl=28)
# 圆角阶梯
RD = dict(s=6, m=8, l=12, xl=14, pill=999)
# 常用尺寸
DIM = dict(
    nav_w=232,            # 左侧导航宽
    topbar_h=52,          # 顶栏高
    composer_h=44,        # 输入框高
    btn_h=32,             # 常规按钮高
    bubble_max_ratio=0.68,  # 气泡最多占可视宽的 68%
    bubble_min_w=280,
    bubble_text_x=28,       # 文本区比气泡窄这么多：QSS padding 13+13 + border 1+1（实测）
    bubble_pad_x=38,        # 算宽度时占位 = 上面 28 + QLabel 自身 8 + 余量 2（实测 sizeHint）
    bubble_pad_y=20,        # 算高度时占位 = padding 9+9 + border 1+1（实测单行 14px 高 40）
    float_bubble_text_w=430,  # 桌宠头顶气泡文本区最大宽，超了自动换行
    card_path_w=360,        # 信息卡片里路径一行的限宽
    plan_card_w=560,        # 任务计划卡宽：与历史面板同一量级，不再横贯半屏
    grant_card_w=430,       # 授权卡宽：三个按钮竖排 + 后果说明，窄了文字会换行
)

# ------------------------------------------------------------
# 全局样式表模板（占位符替换，避免 f-string 把 QSS 花括号当语法）
# ------------------------------------------------------------
TEMPLATE = """
/* ===== 基础 ===== */
QWidget { font-family: __FONT_STACK__; color: __TEXT__; }
/* 顶层窗口底色。预览台是裸 QWidget 窗口，不在 QMainWindow/QDialog 里，
   不点名就会退回系统浅灰，深色控件贴在浅色底上标签直接看不见。 */
QMainWindow, QDialog, QWidget#central,
QWidget#previewWindow { background: __BG__; }
QToolTip {
    background: __ELEVATED__; color: __TEXT__; border: 1px solid __BORDER_STRONG__;
    padding: 6px 9px; border-radius: __RD_S__px; font-size: __FS_CAPTION__px;
}

/* ===== 菜单 ===== */
QMenuBar { background: __SURFACE__; color: __TEXT__;
           border-bottom: 1px solid __BORDER__; font-size: __FS_SMALL__px; }
QMenuBar::item { padding: 5px 11px; border-radius: __RD_S__px; background: transparent; }
QMenuBar::item:selected { background: __ACCENT_SOFT__; }
QMenu { background: __SURFACE__; color: __TEXT__; border: 1px solid __BORDER__;
        border-radius: __RD_M__px; padding: 6px; font-size: __FS_SMALL__px; }
QMenu::item { padding: 7px 26px 7px 14px; border-radius: __RD_S__px; }
QMenu::item:selected { background: __ACCENT_SOFT__; color: __TEXT__; }
QMenu::item:disabled { color: __TEXT3__; }
QMenu::separator { height: 1px; background: __BORDER__; margin: 5px 8px; }

/* ===== 滚动条：细、随主题、无箭头 ===== */
QScrollBar:vertical { background: __SCROLL_BG__; width: 11px; margin: 3px 2px 3px 0; }
QScrollBar:horizontal { background: __SCROLL_BG__; height: 11px; margin: 0 3px 2px 3px; }
QScrollBar::handle { background: __SCROLL__; border-radius: 5px; min-height: 30px; min-width: 30px; }
QScrollBar::handle:hover { background: __SCROLL_HOVER__; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QAbstractScrollArea::corner { background: transparent; border: none; }

/* ===== 文本 ===== */
QLabel { background: transparent; color: __TEXT__; font-size: __FS_BODY__px; }
QLabel[role="muted"] { color: __TEXT2__; font-size: __FS_CAPTION__px; }
QLabel[role="mono"] { font-family: __MONO_STACK__; font-size: __FS_SMALL__px; }

/* ===== 输入 ===== */
QLineEdit, QTextEdit, QPlainTextEdit {
    background: __SURFACE__; color: __TEXT__; border: 1px solid __BORDER__;
    border-radius: __RD_M__px; padding: 7px 10px; font-size: __FS_BODY__px;
    selection-background-color: __SELECTED__; selection-color: __TEXT__;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus { border: 1px solid __ACCENT_LINE__; }
QLineEdit:disabled, QTextEdit:disabled { color: __TEXT3__; background: __SURFACE2__; }

/* ===== 按钮 ===== */
QPushButton {
    background: __SURFACE2__; color: __TEXT__; border: 1px solid __BORDER__;
    border-radius: __RD_M__px; padding: 7px 14px; font-size: __FS_SMALL__px;
}
QPushButton:hover { background: __HOVER__; border-color: __BORDER_STRONG__; }
QPushButton:pressed { background: __PRESS__; }
QPushButton:disabled { color: __TEXT3__; background: __SURFACE__; border-color: __BORDER__; }
QPushButton:checked { background: __ACCENT_SOFT__; border-color: __ACCENT_LINE__; color: __TEXT__; }
QPushButton[role="primary"] {
    background: __ACCENT__; color: __ACCENT_TEXT__; border: none; font-weight: __WT_MEDIUM__;
}
QPushButton[role="primary"]:hover { background: __ACCENT_HOVER__; }
QPushButton[role="danger"] { background: transparent; color: __BAD__; border: 1px solid __BORDER__; }
QPushButton[role="danger"]:hover { background: rgba(229, 100, 92, 30); }
QToolButton {
    background: transparent; color: __TEXT2__; border: none;
    border-radius: __RD_S__px; padding: 4px;
}
QToolButton:hover { background: __HOVER__; color: __TEXT__; }

/* ===== 勾选 / 下拉 ===== */
QCheckBox, QRadioButton { color: __TEXT__; font-size: __FS_SMALL__px; spacing: 8px; background: transparent; }
QCheckBox::indicator, QRadioButton::indicator { width: 15px; height: 15px; }
QCheckBox::indicator { border: 1px solid __BORDER_STRONG__; border-radius: 4px; background: __SURFACE__; }
QCheckBox::indicator:checked { background: __ACCENT__; border-color: __ACCENT__; }
QRadioButton::indicator { border-radius: 8px; border: 1px solid __BORDER_STRONG__; background: __SURFACE__; }
QRadioButton::indicator:checked { background: __ACCENT__; border-color: __ACCENT__; }
QComboBox {
    background: __SURFACE__; color: __TEXT__; border: 1px solid __BORDER__;
    border-radius: __RD_M__px; padding: 6px 10px; font-size: __FS_SMALL__px;
}
QComboBox:hover { border-color: __BORDER_STRONG__; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: __SURFACE__; color: __TEXT__; border: 1px solid __BORDER__;
    selection-background-color: __ACCENT_SOFT__; selection-color: __TEXT__;
    outline: none; padding: 4px;
}

/* ===== 列表 / 表格 ===== */
QListWidget, QListView {
    background: __SURFACE__; color: __TEXT__; border: 1px solid __BORDER__;
    border-radius: __RD_M__px; padding: 5px; font-size: __FS_SMALL__px; outline: none;
}
QListWidget::item, QListView::item { border-radius: __RD_S__px; padding: 7px 9px; }
QListWidget::item:hover, QListView::item:hover { background: __HOVER__; }
QListWidget::item:selected, QListView::item:selected {
    background: __SELECTED__; color: __TEXT__; border: 1px solid __ACCENT_LINE__;
}
QTableWidget, QTableView {
    background: __SURFACE__; color: __TEXT__; border: 1px solid __BORDER__;
    border-radius: __RD_M__px; gridline-color: __BORDER__;
    font-size: __FS_SMALL__px; alternate-background-color: __TABLE_ALT__;
    selection-background-color: __SELECTED__; selection-color: __TEXT__;
    outline: none;
}
QTableWidget::item, QTableView::item { padding: 5px 7px; border: none; }
QTableWidget::item:hover, QTableView::item:hover { background: __HOVER__; }
QTableWidget::item:selected, QTableView::item:selected {
    background: __SELECTED__;
    border: 1px solid __ACCENT_LINE__;
    color: __TEXT__;
}
QHeaderView::section {
    background: __TABLE_HEAD__; color: __TEXT2__; border: none;
    border-bottom: 1px solid __BORDER__; padding: 8px 7px;
    font-size: __FS_CAPTION__px; font-weight: __WT_MEDIUM__;
}
QTableView QTableCornerButton::section { background: __TABLE_HEAD__; border: none; }

/* ===== 分组 / 选项卡 / 分割 ===== */
QGroupBox {
    border: 1px solid __BORDER__; border-radius: __RD_L__px;
    margin-top: 12px; padding: 14px 12px 12px; font-size: __FS_SMALL__px;
    color: __TEXT__; background: transparent;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: __TEXT2__; }
QTabWidget::pane { border: 1px solid __BORDER__; border-radius: __RD_M__px; top: -1px; }
QTabBar::tab {
    background: transparent; color: __TEXT2__; border: none;
    padding: 7px 14px; border-radius: __RD_S__px; font-size: __FS_SMALL__px;
}
QTabBar::tab:selected { background: __ACCENT_SOFT__; color: __TEXT__; }
QSplitter::handle { background: __BORDER__; }
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical { height: 1px; }
QProgressBar {
    background: __SURFACE2__; border: none; border-radius: 4px;
    height: 8px; text-align: center; color: __TEXT2__; font-size: __FS_MICRO__px;
}
QProgressBar::chunk { background: __ACCENT__; border-radius: 4px; }
QScrollBar#qt_scrollarea_hcontainer, QScrollBar#qt_scrollarea_vcontainer { background: transparent; }
QSlider::groove:horizontal {
    height: 4px; background: __SURFACE2__; border-radius: 2px;
}
QSlider::sub-page:horizontal { background: __ACCENT__; border-radius: 2px; }
QSlider::add-page:horizontal { background: __SURFACE2__; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 14px; margin: -5px 0; border-radius: 7px; background: __TEXT__;
}
QSlider::handle:horizontal:hover { background: __ACCENT_HOVER__; }
QLabel#statusChip {
    color: __TEXT2__; background: __SURFACE2__; border: 1px solid __BORDER__;
    border-radius: __RD_M__px; padding: 6px 10px; font-size: __FS_SMALL__px;
}

/* ===== 状态栏 ===== */
QStatusBar { background: __SURFACE__; color: __TEXT2__;
             border-top: 1px solid __BORDER__; font-size: __FS_CAPTION__px; }QStatusBar::item { border: none; }
QStatusBar QLabel { color: __TEXT2__; font-size: __FS_CAPTION__px; background: transparent; }

/* ===== 滚动区视口保持透明，避免整块死黑 ===== */
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }

/* ============================================================
   工作台（objectName 由 workspace_window.py 指定）
   ============================================================ */
QWidget#navPanel { background: __NAV__; border: none; }
QLabel#brand { color: __NAV_TEXT__; font-size: __FS_BRAND__px;
               font-weight: __WT_BOLD__; }
QLabel#brandSub { color: __TEXT3__; font-size: __FS_MICRO__px; }
QPushButton#navButton {
    color: __NAV_TEXT__; background: transparent; border: none;
    border-radius: __RD_M__px; padding: 8px 11px; text-align: left;
    font-size: __FS_SMALL__px; font-weight: __WT_REGULAR__;
}
QPushButton#navButton:hover { background: __NAV_ACTIVE__; }
QPushButton#navButton:checked { background: __NAV_ACTIVE__; color: __TEXT__;
                                font-weight: __WT_MEDIUM__; }
QLabel#navGroupTitle { color: __TEXT3__; font-size: __FS_MICRO__px;
                       padding: 10px 6px 3px; letter-spacing: 1px; }
QWidget#topBar { background: __SURFACE__; border-bottom: 1px solid __BORDER__; }
QLabel#pageTitle { color: __TEXT__; font-size: __FS_TITLE__px; font-weight: __WT_SEMIBOLD__; }
QLabel#pageSubtitle { color: __TEXT2__; font-size: __FS_SMALL__px; }
QWidget#pageCard { background: __SURFACE__; border: 1px solid __BORDER__;
                   border-radius: __RD_L__px; }
QWidget#commandBar { background: __BG__; }
QWidget#composer { background: __SURFACE__; border: 1px solid __BORDER__;
                   border-radius: __RD_XL__px; }
QWidget#composer:focus-within { border: 1px solid __ACCENT_LINE__; }
QLineEdit#commandInput { background: transparent; border: none; padding: 0 4px;
                         font-size: __FS_BODY__px; }
QPushButton#sendRound { background: __ACCENT__; color: __ACCENT_TEXT__; border: none;
                        border-radius: 17px; }
QPushButton#sendRound:hover { background: __ACCENT_HOVER__; }
QPushButton#sendRound:disabled { background: __BORDER__; color: __TEXT3__; }
/* 命令栏里的「选文件」：无底色，只在悬停时浮出，避免和发送键抢注意力 */
QPushButton#pickFile { background: transparent; border: none;
                       border-radius: __RD_M__px; }
QPushButton#pickFile:hover { background: __SURFACE2__; }
QPushButton#pickFile:pressed { background: __PRESS__; }

/* 卡片式分区：设置页 / 空状态 */
QWidget#card { background: __SURFACE2__; border: 1px solid __BORDER__;
               border-radius: __RD_L__px; }
QLabel#cardTitle { color: __TEXT__; font-size: __FS_LEAD__px; font-weight: __WT_SEMIBOLD__; }
QLabel#cardDesc { color: __TEXT2__; font-size: __FS_SMALL__px; }
QLabel#emptyState { color: __TEXT3__; font-size: __FS_BODY__px; }
QFrame#hline { background: __BORDER__; max-height: 1px; border: none; }

/* 关于页五个能力开关：点了真会翻 config 并落盘，所以要长成能按的样子
   （实心底 + 悬停变色 + 手型光标），不能跟普通说明文字一个长相 */
QLabel#switchName {
    color: __TEXT__; font-size: __FS_SMALL__px; font-weight: __WT_MEDIUM__;
}
QPushButton#switchToggle {
    background: __ELEVATED__; color: __TEXT2__; border: 1px solid __BORDER_STRONG__;
    border-radius: __RD_S__px; min-width: 62px; padding: 5px 0;
    font-size: __FS_CAPTION__px; font-weight: __WT_MEDIUM__;
}
QPushButton#switchToggle:hover {
    background: __HOVER__; color: __TEXT__; border-color: __ACCENT_LINE__;
}
QPushButton#switchToggle:pressed { background: __PRESS__; }
QPushButton#switchToggle[role="good"] { color: __GOOD__; border-color: __ACCENT_LINE__; }
QPushButton#switchToggle[role="bad"] { color: __BAD__; }
QPushButton#switchToggle[role="bad"]:hover { border-color: __BORDER_STRONG__; }

/* 工作台里其余仍在使用的控件名 */
QPushButton#newConv {
    background: __ACCENT_SOFT__; color: __TEXT__; border: 1px solid __ACCENT_LINE__;
    border-radius: __RD_M__px; padding: 8px 11px; text-align: left;
    font-size: __FS_SMALL__px; font-weight: __WT_MEDIUM__;
}
QPushButton#newConv:hover { background: __HOVER__; }
QLabel#navGroupBody { color: __TEXT2__; font-size: __FS_CAPTION__px; padding: 0 6px; }
/* 本机访问的分组计数是可点的入口，要长得像按钮 */
QPushButton#navGroupLink {
    color: __TEXT2__; background: __ACCENT_SOFT__; border: 1px solid __BORDER__;
    border-radius: __RD_S__px; padding: 5px 8px; text-align: left;
    font-size: __FS_CAPTION__px; font-weight: __WT_MEDIUM__;
}
QPushButton#navGroupLink:hover { background: __HOVER__; color: __TEXT__; }
/* 本机访问页：空态、读取证据卡、格式说明 */
QLabel#grantEmpty { color: __TEXT2__; font-size: __FS_SMALL__px; padding: 4px 0; }
QLabel#grantFormats { color: __TEXT3__; font-size: __FS_CAPTION__px; padding: 6px 0; }
QLabel#grantEvidenceCard {
    color: __TEXT2__; background: __SURFACE2__; border: 1px solid __BORDER__;
    border-radius: __RD_M__px; padding: 8px 10px; font-size: __FS_CAPTION__px;
}
QWidget#navFoot { background: __NAV__; border-top: 1px solid __BORDER__; }
QPushButton#updateBtn {
    background: transparent; color: __TEXT2__; border: 1px solid __BORDER__;
    border-radius: __RD_S__px; padding: 4px 9px; font-size: __FS_MICRO__px;
}
QPushButton#updateBtn:hover { background: __HOVER__; color: __TEXT__; }
/* 顶栏操作按钮：一律带文字，避免「纯图标看不懂点的是什么」 */
QPushButton#topButton {
    background: transparent; color: __TEXT2__; border: 1px solid __BORDER__;
    border-radius: __RD_M__px; padding: 5px 10px; font-size: __FS_CAPTION__px;
}
QPushButton#topButton:hover {
    background: __HOVER__; color: __TEXT__; border-color: __BORDER_STRONG__;
}
QPushButton#ghostButton {
    background: transparent; color: __TEXT__; border: 1px solid __BORDER__;
    border-radius: __RD_M__px; padding: 6px 13px; font-size: __FS_SMALL__px;
}
QPushButton#ghostButton:hover { background: __HOVER__; border-color: __BORDER_STRONG__; }
QTextBrowser#detailView {
    background: __SURFACE2__; color: __TEXT__; border: 1px solid __BORDER__;
    border-radius: __RD_M__px; padding: 10px 12px; font-size: __FS_SMALL__px;
}
QScrollArea#chatScroll { background: transparent; border: none; }
QWidget#chatList { background: transparent; }
QLabel#feedbackLabel {
    color: __TEXT2__; background: transparent; padding: 2px 10px;
    border-radius: __RD_S__px; font-size: __FS_CAPTION__px;
}
QLabel#feedbackLabel[role="good"] { color: __GOOD__; background: __ACCENT_SOFT__; }
QLabel#feedbackLabel[role="bad"] { color: __BAD__; background: rgba(229, 100, 92, 34); }

/* 聊天气泡 */
QLabel#bubbleMe {
    background: __BUBBLE_ME__; color: __BUBBLE_ME_TEXT__;
    border: 1px solid __BUBBLE_ME_BORDER__;
    border-top-right-radius: __RD_S__px; border-radius: __RD_XL__px;
    padding: 9px 13px; font-size: __FS_BODY__px;
}
QLabel#bubbleAi {
    background: __BUBBLE_AI__; color: __BUBBLE_AI_TEXT__;
    border: 1px solid __BUBBLE_AI_BORDER__;
    border-top-left-radius: __RD_S__px; border-radius: __RD_XL__px;
    padding: 9px 13px; font-size: __FS_BODY__px;
}
QLabel#bubbleMeta { color: __TEXT3__; font-size: __FS_MICRO__px; }
/* 系统事件（文档已生成 / 提醒触发等）：弱化通栏提示，不占气泡、不抢对话注意力 */
QLabel#systemNote {
    color: __TEXT3__; font-size: __FS_CAPTION__px;
    background: __SURFACE2__; border: 1px solid __BORDER__;
    border-radius: __RD_S__px; padding: 4px 10px;
}

/* ============================================================
   桌宠侧浮层（objectName 沿用 ui_manager / input_dialog / plan_card）
   统一为半透明深底 + 浅字，贴在任意壁纸上都读得清
   ============================================================ */
QWidget#inputPanel, QWidget#historyPanel, QWidget#bubblePanel,
QWidget#cardPanel, QWidget#planCard, QWidget#grantCard, QWidget#taskPanel, QWidget#settingsPanel {
    background: __FLOAT_BG__; border: 1px solid __FLOAT_BORDER__;
    border-radius: __RD_L__px;
}
QLabel#bubbleLabel { background: transparent; color: __FLOAT_TEXT__;
                     font-size: __FS_BODY__px; }
QLabel#cardTitleFloat, QLabel#historyTitle, QLabel#planTitle {
    color: __FLOAT_TEXT__; font-size: __FS_LEAD__px; font-weight: __WT_SEMIBOLD__;
    background: transparent;
}
QLabel#cardPath, QLabel#cardSize { color: __FLOAT_MUTED__;
                                   font-family: __MONO_STACK__; font-size: __FS_CAPTION__px;
                                   background: transparent; }
QLineEdit#inputEdit {
    background: rgba(255, 255, 255, 18); color: __FLOAT_TEXT__;
    border: 1px solid __FLOAT_BORDER__; border-radius: __RD_M__px;
    padding: 0 10px; font-size: __FS_BODY__px;
    selection-background-color: __SELECTED__;
}
QLineEdit#inputEdit:focus { border: 1px solid __ACCENT__; background: rgba(255, 255, 255, 28); }
QPushButton#sendButton {
    background: __ACCENT__; color: __ACCENT_TEXT__; border: none;
    border-radius: __RD_M__px; padding: 0 14px; font-size: __FS_SMALL__px;
    font-weight: __WT_MEDIUM__;
}
QPushButton#sendButton:hover { background: __ACCENT_HOVER__; }
QPushButton#barButton, QPushButton#miniButton, QPushButton#historyClear {
    background: rgba(255, 255, 255, 16); color: __FLOAT_TEXT__;
    border: 1px solid __FLOAT_BORDER__; border-radius: __RD_M__px;
    padding: 0 11px; font-size: __FS_SMALL__px;
}
QPushButton#barButton:hover, QPushButton#miniButton:hover,
QPushButton#historyClear:hover { background: rgba(255, 255, 255, 30); }
QPushButton#exitButton { background: transparent; color: __BAD__;
                         border: 1px solid __BAD__; border-radius: __RD_M__px;
                         padding: 0 14px; font-size: __FS_SMALL__px; }
QPushButton#exitButton:hover { background: rgba(229, 100, 92, 40); }
QPushButton#historyClose, QPushButton#cardClose, QPushButton#settingsClose {
    background: transparent; border: none; color: __FLOAT_MUTED__;
    border-radius: __RD_S__px; padding: 2px 8px; font-size: __FS_LEAD__px;
}
QPushButton#historyClose:hover, QPushButton#cardClose:hover,
QPushButton#settingsClose:hover { background: rgba(229, 100, 92, 46); color: #FFD9D6; }
QLabel#historyTip, QLabel#historyTime, QLabel#historyCount {
    color: __FLOAT_MUTED__; font-size: __FS_CAPTION__px; background: transparent;
}
QLabel#historyNameUser { color: #8FD3A8; font-size: __FS_SMALL__px;
                          font-weight: __WT_SEMIBOLD__; background: transparent; }
QLabel#historyNamePet { color: #9CC2FF; font-size: __FS_SMALL__px;
                         font-weight: __WT_SEMIBOLD__; background: transparent; }
QLabel#historyTextUser, QLabel#historyTextPet {
    color: __FLOAT_TEXT__; font-size: __FS_BODY__px;
    background: rgba(255, 255, 255, 16);
    border: 1px solid rgba(255, 255, 255, 20);
    border-radius: __RD_M__px; padding: 8px 12px;
}
QLabel#historyTextPet { background: rgba(79, 140, 255, 34); border-color: rgba(79, 140, 255, 60); }
QLabel#sessionHeader { color: __FLOAT_MUTED__; font-size: __FS_CAPTION__px;
                        background: transparent; padding: 4px 2px; }
QPushButton#historyItemBtn { background: transparent; border: none;
                              color: __FLOAT_MUTED__; border-radius: __RD_S__px;
                              padding: 1px 7px; font-size: __FS_CAPTION__px; }
QPushButton#historyItemBtn:hover { background: rgba(255, 255, 255, 26); color: __FLOAT_TEXT__; }
QListWidget#historyList { background: transparent; border: none; padding: 0; }
QListWidget#historyList::item { background: transparent; border: none;
                                 padding: 0; margin-bottom: 6px; }
QListWidget#historyList::item:selected { background: transparent; }
QWidget#historyItem { background: transparent; }
QLabel#historyIcon { background: transparent; }
QScrollArea#settingsScroll { background: transparent; border: none; }
QLabel#settingsTitle { color: __FLOAT_TEXT__; font-size: __FS_LEAD__px;
                        font-weight: __WT_SEMIBOLD__; background: transparent; }
QPushButton#settingsButton { background: __ACCENT__; color: __ACCENT_TEXT__;
                             border: none; border-radius: __RD_M__px;
                             padding: 7px 16px; font-weight: __WT_MEDIUM__; }
QPushButton#settingsButton:hover { background: __ACCENT_HOVER__; }
QLabel#planSteps { color: __FLOAT_TEXT__; font-size: __FS_BODY__px;
                   background: transparent; }
QPushButton#planQuit { background: transparent; color: __FLOAT_MUTED__;
                       border: none; padding: 4px 10px;
                       font-size: __FS_SMALL__px; }
QPushButton#planQuit:hover { color: __BAD__; }
/* ===== 授权浮层卡（读本机文件前的那次点头）===== */
QLabel#grantTitle { color: __FLOAT_TEXT__; font-size: __FS_LEAD__px;
                    font-weight: __WT_SEMIBOLD__; background: transparent; }
QLabel#grantPath { color: __FLOAT_TEXT__; font-family: __MONO_STACK__;
                   font-size: __FS_SMALL__px; background: transparent; }
QLabel#grantHint { color: __FLOAT_MUTED__; font-size: __FS_BODY__px;
                   background: transparent; }
QLabel#grantNote { color: __FLOAT_MUTED__; font-size: __FS_CAPTION__px;
                   background: transparent; padding: 1px 2px; }
QLabel#grantCountdown { color: __WARN__; font-size: __FS_CAPTION__px;
                        background: transparent; padding-top: 2px; }
QPushButton#grantFile, QPushButton#grantTree {
    background: rgba(255, 255, 255, 16); color: __FLOAT_TEXT__;
    border: 1px solid __FLOAT_BORDER__; border-radius: __RD_M__px;
    padding: 6px 12px; font-size: __FS_SMALL__px; text-align: left;
}
QPushButton#grantFile:hover, QPushButton#grantTree:hover {
    background: rgba(255, 255, 255, 30); }
QPushButton#grantTree { border-color: __ACCENT__; color: __ACCENT_TEXT__; }
QPushButton#grantTree:hover { background: __ACCENT_HOVER__; }
QPushButton#grantDeny { background: transparent; color: __FLOAT_MUTED__;
                        border: 1px solid __FLOAT_BORDER__;
                        border-radius: __RD_M__px; padding: 6px 12px;
                        font-size: __FS_SMALL__px; text-align: left; }
QPushButton#grantDeny:hover { color: __BAD__; border-color: __BAD__; }
/* 浮层里的 QLabel 默认继承全局深色文字会看不见，统一改成浮层文字色 */
QWidget#bubblePanel QLabel, QWidget#cardPanel QLabel, QWidget#historyPanel QLabel,
QWidget#inputPanel QLabel, QWidget#planCard QLabel, QWidget#grantCard QLabel,
QWidget#taskPanel QLabel, QWidget#settingsPanel QLabel {
    color: __FLOAT_TEXT__; background: transparent; }
"""


def _sub(p: dict, tpl: str) -> str:
    out = tpl
    for key, val in p.items():
        out = out.replace("__%s__" % key.upper(), str(val))
    for key, val in qss_tokens().items():
        out = out.replace(key, val)
    for key, val in WT.items():
        out = out.replace("__WT_%s__" % key.upper(), str(val))
    for key, val in SZ.items():
        out = out.replace("__FS_%s__" % key.upper(), str(val))
    for key, val in RD.items():
        out = out.replace("__RD_%s__" % key.upper(), str(val))
    return out


# ------------------------------------------------------------
# 当前主题状态 + 换肤回调
# ------------------------------------------------------------
_current = "dark"
_callbacks = []


def get_theme() -> str:
    return _current


def palette(name: str = None) -> dict:
    return PALETTES[name or _current]


def T(key: str, name: str = None) -> str:
    """取当前主题的一个令牌值，例如 theme.T("ACCENT")。"""
    return str(palette(name)[key])


def Q(key: str, name: str = None):
    """令牌值转 QColor，给 QPainter 自绘用。

    QSS 认 rgba(r, g, b, a) 这种写法，QColor 在 Qt5 里不一定认，浮层背景
    又都是半透明的，所以这里自己解析一次。
    """
    from PyQt5.QtGui import QColor
    val = T(key, name).strip()
    if val.startswith("rgba("):
        r, g, b, a = (int(float(x)) for x in val[5:-1].split(","))
        return QColor(r, g, b, a)
    if val.startswith("rgb("):
        r, g, b = (int(float(x)) for x in val[4:-1].split(","))
        return QColor(r, g, b)
    return QColor(val)


def build_qss(p: dict) -> str:
    return _sub(p, TEMPLATE)


def on_theme_change(cb) -> None:
    """注册回调：cb(theme_name, palette)。窗口用它刷新非 QSS 的自绘内容。"""
    if cb not in _callbacks:
        _callbacks.append(cb)


def set_theme(name: str, app=None) -> str:
    """切主题并把全局样式表推给所有窗口；桌宠侧与工作台同步换肤。"""
    global _current
    if name not in PALETTES:
        name = "dark"
    _current = name
    p = PALETTES[name]
    if app is None:
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(build_qss(p))
    for cb in list(_callbacks):
        try:
            cb(name, p)
        except Exception:
            pass
    return name


def install(app, name: str = None) -> str:
    """main.py 建好 QApplication 后调用一次：装字体 + 装全局样式表。"""
    from .typography import install_app_fonts
    install_app_fonts(app)
    return set_theme(name or _current, app)
