# -*- coding: utf-8 -*-
"""工作台主窗口测试：可最大化、真实数据、关闭不退出、状态可记住。

全部在离屏 Qt 下跑，不联网、不调模型、不打开系统文件管理器。
数据目录由 tests/conftest.py 的 LINGLING_DATA_DIR 隔离。
"""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QCloseEvent  # noqa: E402
from PyQt5.QtWidgets import (QApplication, QLabel, QLineEdit, QMainWindow,
                             QPushButton)  # noqa: E402

from framework.core import Framework  # noqa: E402
from ui import icons  # noqa: E402
from ui.grant_card import GrantCard  # noqa: E402
from ui.workspace_window import (HIDDEN_IN_NAV, OPERATION_ROWS, PAGE_DEFS,  # noqa: E402
                                 WorkspaceWindow, page_index)


# 必须保留 QApplication 引用：写成 `QApplication.instance() or QApplication([])`
# 而不赋值，新建的 app 会被立刻 GC，之后构造 QWidget 会原生崩溃（退出码 127）。
_APP = None


@pytest.fixture
def fw_and_window(tmp_path):
    global _APP
    _APP = QApplication.instance() or QApplication([])
    fw = Framework.instance()
    for store in (fw.trajectory, fw.prefs, fw.rules):
        if store is not None:
            store.clear()
    fw.memory.history = []
    fw.tasks.deliveries = []
    path = str(tmp_path / "workspace.ini")
    w = WorkspaceWindow(fw, settings_path=path)
    yield fw, w, path
    w.detach()
    w.hide()


def test_is_a_standard_resizable_main_window(fw_and_window):
    """不是无边框小面板：要能靠系统按钮/双击标题栏最大化。"""
    _fw, w, _path = fw_and_window
    assert isinstance(w, QMainWindow)
    assert not w.windowFlags() & Qt.FramelessWindowHint
    # 系统标题栏只写「工作台」：品牌名在窗口内左侧导航顶部（brand 标签）
    # 已经有了，标题栏再重复一遍，在任务栏和标题栏之间会显得啰嗦。
    assert w.windowTitle() == "工作台"
    assert w.minimumWidth() >= 900 and w.minimumHeight() >= 600


def test_eight_pages_are_real(fw_and_window):
    _fw, w, _path = fw_and_window
    assert w._stack.count() == len(PAGE_DEFS) == 8
    for i, (key, title, _hint) in enumerate(PAGE_DEFS):
        w.switch_page(i)
        assert w._title.text() == title
        assert w._stack.currentIndex() == i
        if key in HIDDEN_IN_NAV:
            assert w._nav_buttons[i] is None   # 工程面板不占导航位
        else:
            assert w._nav_buttons[i].isChecked()


def test_trace_page_hidden_from_nav_but_reachable(fw_and_window):
    """工程面板不进左侧导航：7 个页签，切过去时不许留一个「亮着却不对应」的项。"""
    _fw, w, _path = fw_and_window
    buttons = [b for b in w._nav_buttons if b is not None]
    assert len(buttons) == 7
    assert "执行轨迹" not in [b.text().strip() for b in buttons]

    w.switch_page(page_index("trace"))
    assert w._title.text() == "执行轨迹"
    assert not any(b.isChecked() for b in buttons)

    w.switch_page(page_index("chat"))
    assert w._nav_buttons[page_index("chat")].isChecked()


def test_trace_entry_lives_in_about_page(fw_and_window):
    """入口收在关于页：那张卡上有摘要，按钮真的能跳到轨迹页。"""
    _fw, w, _path = fw_and_window
    w.switch_page(page_index("about"))
    assert w._trace_summary_label.text()          # 不进去也知道里面有没有东西
    btn = next(b for b in w.findChildren(QPushButton)
               if "打开执行轨迹" in b.text())
    btn.click()
    assert w._stack.currentIndex() == page_index("trace")
    assert w._title.text() == "执行轨迹"


def test_about_menu_opens_about_page(fw_and_window):
    """菜单「关于与状态」曾经写死 switch_page(5)，打开的是本机访问页。"""
    _fw, w, _path = fw_and_window
    action = next(a for m in w.menuBar().actions()
                  for a in m.menu().actions() if a.text() == "关于与状态")
    action.trigger()
    assert w._stack.currentIndex() == page_index("about")
    assert w._title.text() == "关于与设置"


def test_first_open_maximizes(fw_and_window, monkeypatch):
    """点开就铺满屏幕，符合「打开然后界面最大化」的预期。"""
    _fw, w, _path = fw_and_window
    calls = []
    monkeypatch.setattr(w, "showMaximized", lambda: calls.append("max"))
    monkeypatch.setattr(w, "showNormal", lambda: calls.append("normal"))
    w.open_workspace()
    assert "max" in calls
    assert w._max_state is True
    assert "还原" in w._maximize_btn.text()


def test_toggle_maximize_is_reversible(fw_and_window):
    _fw, w, _path = fw_and_window
    w._apply_maximize(True)
    assert w._max_state is True and "还原" in w._maximize_btn.text()
    w.toggle_maximize()
    assert w._max_state is False and "最大化" in w._maximize_btn.text()
    w.toggle_maximize()
    assert w._max_state is True


def test_chat_page_shows_real_history_and_escapes_html(fw_and_window):
    fw, w, _path = fw_and_window
    fw.memory.add("user", "你好<script>alert(1)</script>")
    fw.memory.add("assistant", "遵命，这就去办")
    w._refresh_chat()
    # 气泡是 QLabel，天然不执行 script，只需确认文本原样渲染、不丢内容
    texts = [b.text() for b in w._chat_list.findChildren(QLabel)
             if b.objectName() in ("bubbleMe", "bubbleAi")]
    joined = "\n".join(texts)
    assert "遵命" in joined
    assert "你好" in joined
    assert "<script>alert(1)</script>" in joined, "用户输入应原样显示为文本"


def test_sending_from_workspace_goes_to_framework(fw_and_window, monkeypatch):
    fw, w, _path = fw_and_window
    seen = []
    monkeypatch.setattr(fw, "send_command", lambda text: seen.append(text))
    w._chat_input.setText("加个待办：交周报")
    w._send()
    assert seen == ["加个待办：交周报"]
    assert w._chat_input.text() == ""


def test_preference_page_is_hidden_until_switch_turns_it_on(
        fw_and_window, monkeypatch):
    """偏好默认只在后台学，界面不摊开；用户主动打开开关才显示明细。

    这是产品决策：偏好影响回复，但不需要天天摆在眼前。
    """
    from framework import config
    fw, w, _path = fw_and_window
    fw.prefs.observe("我喜欢深色 PPT")

    monkeypatch.setattr(config, "PREFERENCE_PANEL", False)
    w.switch_page(1)
    assert w._prefs_body.isHidden()
    assert not w._prefs_hidden.isHidden()
    assert "在界面上显示偏好" in w._prefs_hidden.text()

    monkeypatch.setattr(config, "PREFERENCE_PANEL", True)
    w._refresh_prefs()
    assert not w._prefs_body.isHidden()
    assert w._prefs_hidden.isHidden()
    assert w._prefs_table.rowCount() >= 1
    assert w._prefs_table.item(0, 0).text() == "doc_theme"
    assert w._prefs_table.item(0, 1).text()
    # 来源列要能区分「规则命中」与「模型归纳」
    assert w._prefs_table.item(0, 2).text() in ("规则命中", "模型归纳")


def test_rules_page_shows_real_rules(fw_and_window):
    fw, w, _path = fw_and_window
    fw.trajectory.start_turn(pre_hash="a")
    for _ in range(2):
        fw.trajectory.append(
            "quality", action="web_search", source="system", ok=False,
            note="资料与查询关键词零重合")
    fw.trajectory.end_turn(post_hash="b", undo_ledger=None)
    fw.rules.learn(fw.trajectory.events())
    assert fw.rules.all(), "应先真的归纳出规则"
    w.switch_page(2)
    assert w._rules_table.rowCount() >= 1
    assert w._rules_table.item(0, 0).text()


def test_trace_page_shows_real_turns(fw_and_window):
    fw, w, _path = fw_and_window
    fw.trajectory.start_turn(pre_hash="a")
    fw.trajectory.append("action", action="todo_add", source="model")
    fw.trajectory.end_turn(post_hash="b", undo_ledger=None)
    w.switch_page(3)
    assert w._trace_table.rowCount() >= 1
    assert "t_" in w._trace_table.item(0, 0).text()
    assert "哈希链" in w._detail_view.toPlainText() or "轮次" in \
        w._detail_view.toPlainText()


def test_timeline_can_be_generated_from_the_window(fw_and_window, tmp_path):
    fw, w, _path = fw_and_window
    fw.trajectory.start_turn(pre_hash="a")
    fw.trajectory.append("preference", source="user",
                         payload={"key": "doc_theme", "value": "深色"})
    fw.trajectory.end_turn(post_hash="b", undo_ledger={"items": [], "spots": []})
    w._timeline_path = str(tmp_path / "timeline.html")
    path = w.generate_timeline()
    assert path and os.path.exists(path)
    body = Path(path).read_text(encoding="utf-8")
    assert "时间线" in body
    assert "doc_theme" in body


def test_delivery_page_shows_real_files(fw_and_window, tmp_path):
    fw, w, _path = fw_and_window
    fake = tmp_path / "报告.pptx"
    fake.write_bytes(b"x")
    fw.tasks.add_delivery("报告.pptx", str(fake), "ppt")
    w.switch_page(4)
    assert w._delivery_list.count() >= 1
    assert "报告.pptx" in w._delivery_list.item(0).text()


def test_theme_toggle_changes_stylesheet_and_is_remembered(fw_and_window):
    from ui import theme
    fw, w, path = fw_and_window
    app = QApplication.instance()
    init_theme = w._theme
    before = app.styleSheet()
    w.toggle_theme()
    assert w._theme != init_theme          # 主题真的翻转了
    assert app.styleSheet() != before      # 全局样式表真的换了
    # 样式表挂 QApplication 上，工作台与桌宠侧浮层才会一起换肤
    assert theme.get_theme() == w._theme
    assert w.styleSheet() == ""
    assert not w._theme_btn.icon().isNull()  # 主题按钮用矢量图标，不是字符
    assert w._icon_buttons[w._theme_btn][0] == (
        "sun" if w._theme == "light" else "moon")

    w2 = WorkspaceWindow(fw, settings_path=path)
    try:
        assert w2._theme == w._theme       # 记住了上一次切换后的主题
    finally:
        w2.detach()
        w2.hide()
    w._apply_theme(init_theme)


def test_top_menu_bar_exists(fw_and_window):
    """对标 WorkBuddy：顶部有 文件/编辑/窗口/帮助/关于 菜单栏。"""
    _fw, w, _path = fw_and_window
    bar = w.menuBar()
    joined = "".join(a.text() for a in bar.actions())
    for name in ("文件", "编辑", "窗口", "帮助", "关于"):
        assert name in joined


def test_global_command_bar_present(fw_and_window):
    """底部全局命令栏：占位文案、回车/发送把指令交给框架。"""
    _fw, w, _path = fw_and_window
    assert isinstance(w._chat_input, QLineEdit)
    assert "今天帮你做些什么" in w._chat_input.placeholderText()
    seen = []
    w.fw.send_command = lambda t: seen.append(t)
    w._chat_input.setText("加个待办：交周报")
    w._send()
    assert seen == ["加个待办：交周报"]
    assert w._chat_input.text() == ""


def test_nav_groups_reflect_live_counts(fw_and_window):
    """左侧导航分组随真实数据刷新（任务 / 交付 / 偏好 / 规则）。"""
    fw, w, _path = fw_and_window
    fw.tasks.add_delivery("报告.pptx", __file__, "ppt")
    fw.prefs.observe("我喜欢深色 PPT")
    w._refresh_status()
    body = w._nav_groups.text()
    assert "待办" in body and "交付" in body
    assert "1 份" in body or "1份" in body
    assert "偏好" in body


def test_geometry_and_maximize_state_are_saved(fw_and_window):
    fw, w, path = fw_and_window
    w._apply_maximize(True)
    w.save_state()
    from PyQt5.QtCore import QSettings
    s = QSettings(path, QSettings.IniFormat)
    assert str(s.value("maximized")).lower() == "true"
    assert s.value("theme") in ("light", "dark")
    assert int(s.value("width")) >= 900


def test_close_hides_but_does_not_quit(fw_and_window):
    """点 X 只回到桌宠，程序继续留在托盘。"""
    _fw, w, _path = fw_and_window
    w.show()
    closed = []
    w.workspace_closed.connect(lambda: closed.append(True))
    event = QCloseEvent()
    w.closeEvent(event)
    assert not event.isAccepted(), "close 要被忽略，窗口只是隐藏"
    assert not w.isVisible()
    assert closed == [True]
    assert QApplication.instance() is not None


def test_hide_to_pet_and_esc(fw_and_window):
    from PyQt5.QtGui import QKeyEvent
    from PyQt5.QtCore import QEvent
    _fw, w, _path = fw_and_window
    w.show()
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    w.keyPressEvent(ev)
    assert not w.isVisible()
    w.hide_to_pet()
    assert not w.isVisible()


def test_three_entry_points_are_wired():
    """输入条 / 托盘 / 右键菜单三处都要能点开工作台。"""
    from ui.input_dialog import InputDialog
    from ui.ui_manager import TrayIcon

    assert hasattr(InputDialog, "workspace_requested")
    assert hasattr(TrayIcon, "workspace_requested")
    pet = Path("ui/pet_window.py").read_text(encoding="utf-8")
    assert "open_workspace" in pet
    assert pet.count("workspace_requested.connect") >= 2
    inp = Path("ui/input_dialog.py").read_text(encoding="utf-8")
    assert "工作台" in inp
    tray = Path("ui/ui_manager.py").read_text(encoding="utf-8")
    assert "打开工作台" in tray


def test_detach_stops_timer_and_disconnects(fw_and_window):
    _fw, w, _path = fw_and_window
    assert w._status_timer.isActive()
    w.detach()
    assert not w._status_timer.isActive()
    w.detach()   # 幂等，不抛异常


def test_workspace_qss_has_unified_font_stack(fw_and_window):
    """所有控件统一现代字体栈，不能回退到 Qt 默认的宋体。"""
    _fw, w, _path = fw_and_window
    qss = QApplication.instance().styleSheet()
    assert "font-family" in qss
    assert "Segoe UI" in qss and "Microsoft YaHei" in qss
    # 不能有未替换的占位符，也不能再有写死的微软雅黑单字体
    assert "__FONT_STACK__" not in qss
    assert '"Microsoft YaHei", sans-serif' not in qss


def test_font_sizes_match_workbuddy_reference(fw_and_window):
    """字号阶梯对标 WorkBuddy 截图：精致克制、层级分明，不靠一味放大。

    已开高 DPI + 现代字体栈，13~17px 就足够锐利清晰；上一轮把正文调到
    15 / 标题 21 / 品牌 24 反而偏大，这里按参考图实测值重新锚定。
    """
    from ui.typography import SZ
    assert SZ["body"] == 14, "正文对齐参考图 14px"
    assert SZ["title"] == 17, "页面标题对齐参考图 17px"
    assert SZ["brand"] == 15, "品牌对齐参考图 15px"
    assert SZ["small"] == 13, "导航项对齐参考图 13px"
    # 层级：标题 > 正文 > 辅助；字号区间整体收敛在 11~17，不再出现 21/24
    assert SZ["title"] > SZ["body"] > SZ["small"] > SZ["caption"]
    assert max(SZ.values()) <= 17, "不应再有比参考图标题更大的字号"
    _fw, w, _path = fw_and_window
    qss = QApplication.instance().styleSheet()
    assert "font-size: %dpx" % SZ["body"] in qss
    assert "font-size: %dpx" % SZ["title"] in qss


def test_no_hardcoded_font_sizes_left_in_qss():
    """样式模板里 font-size 只能写阶梯占位符，成品 QSS 里不能残留未替换占位符。"""
    import re
    from ui import theme
    hard = re.findall(r"font-size:\s*\d+px", theme.TEMPLATE)
    assert hard == [], "模板里写死了字号: %s" % hard
    for name in ("dark", "light"):
        qss = theme.build_qss(theme.PALETTES[name])
        assert "__" not in qss, "%s 主题有未替换的占位符" % name


def test_bubble_width_is_measured_not_left_to_the_layout(fw_and_window):
    """长回复的气泡宽度按字体度量算死：既不被布局挤成窄列，也不越过上限。"""
    from PyQt5.QtWidgets import QLabel
    fw, w, _path = fw_and_window
    fw.memory.add("assistant", "这是一条很长的回复，" * 40)
    fw.memory.add("user", "短句")
    w._refresh_chat()
    bubbles = [b for b in w._chat_list.findChildren(QLabel)
               if b.objectName() in ("bubbleMe", "bubbleAi")]
    assert len(bubbles) == 2
    ai_bubble, me_bubble = bubbles
    cap = w._bubble_max_w()
    assert ai_bubble.width() <= cap, "气泡不能宽过可视区的固定比例"
    assert ai_bubble.width() >= 300, "长文本要铺到上限附近，不能被挤成窄列"
    assert me_bubble.width() < ai_bubble.width(), "短消息的气泡应跟着收缩"


def test_bubble_text_is_plain_and_never_clipped(fw_and_window):
    """模型回复常带 <p> 标签：气泡只显示纯文本，宽度也不能把标签字符算进去。

    这条同时钉住两个真实故障：富文本标签让测量偏大，以及 padding 之和取小
    了让「你好」这种短句被裁掉最后一个字。
    """
    from PyQt5.QtCore import QRect, Qt
    from PyQt5.QtGui import QFontMetrics
    from PyQt5.QtWidgets import QLabel
    fw, w, _path = fw_and_window
    fw.memory.add("assistant", "<p>我在，有何吩咐？</p>")
    fw.memory.add("user", "你好")
    w._refresh_chat()
    bubbles = [b for b in w._chat_list.findChildren(QLabel)
               if b.objectName() in ("bubbleMe", "bubbleAi")]
    assert [b.text() for b in bubbles] == ["我在，有何吩咐？", "你好"]
    for b in bubbles:
        fm = QFontMetrics(b.font())
        need_w = fm.boundingRect(
            QRect(0, 0, 16777215, 16777215),
            Qt.TextWordWrap | Qt.AlignLeft, b.text()).width()
        assert b.contentsRect().width() >= need_w, "%r 内容区被裁切" % b.text()


def test_wrapped_bubble_gets_enough_height(fw_and_window):
    """两行的长句必须给两行的高：heightForWidth 传不过嵌套布局，实测会被裁掉第二行。"""
    from PyQt5.QtCore import QRect, Qt
    from PyQt5.QtGui import QFontMetrics
    from PyQt5.QtWidgets import QLabel
    fw, w, _path = fw_and_window
    fw.memory.add("assistant", "这是一条会被自动换行的回复，" * 12)
    w._refresh_chat()
    b = [x for x in w._chat_list.findChildren(QLabel)
         if x.objectName() == "bubbleAi"][0]
    fm = QFontMetrics(b.font())
    need_h = fm.boundingRect(
        QRect(0, 0, b.contentsRect().width(), 16777215),
        Qt.TextWordWrap | Qt.AlignLeft, b.text()).height()
    assert need_h > fm.height(), "样例文本确实换行了，否则这条用例没测到东西"
    assert b.contentsRect().height() >= need_h, "长回复的高度不够，最后一行被裁"


def test_read_evidence_card_is_measured_not_squeezed(fw_and_window):
    """读取证据卡和气泡同理：只给 maximumWidth 会被水平布局挤成窄列。"""
    from PyQt5.QtCore import QRect, Qt
    from PyQt5.QtGui import QFontMetrics
    from PyQt5.QtWidgets import QLabel
    fw, w, _path = fw_and_window
    fw._read_log = [{
        "kind": "batch", "name": "批量 7 份", "total": 7, "listed": 4,
        "skipped": 3, "chars": 2310, "limit": 600,
        "note": "还剩 3 份没读", "path": "D:/验收资料/甲.txt",
    }]
    w._refresh_chat()
    cards = [x for x in w._chat_list.findChildren(QLabel)
             if x.objectName() == "grantEvidenceCard"]
    assert len(cards) == 1
    card = cards[0]
    assert card.width() >= 400, "证据卡被挤成窄列，长句读不完整"
    assert "还剩 3 份没读" in card.text()
    fm = QFontMetrics(card.font())
    need_h = fm.boundingRect(
        QRect(0, 0, card.contentsRect().width(), 16777215),
        Qt.TextWordWrap | Qt.AlignLeft, card.text()).height()
    assert card.contentsRect().height() >= need_h, "证据卡最后一行被裁"


def test_plain_text_flattens_model_markup():
    """工作台气泡、桌宠气泡、历史面板共用这一份：块级标签换行、内联标签删除。"""
    from framework.utils import plain_text
    assert plain_text("<p>我在，有何吩咐？</p>") == "我在，有何吩咐？"
    assert plain_text("<p>甲</p><p>乙</p>") == "甲\n\n乙"
    assert plain_text("a <b>粗</b> b") == "a 粗 b"
    assert plain_text("<p>如果 a &lt; b</p>") == "如果 a < b"
    # 完全没有标签的文本一律原样保留，连 & 也不动
    assert plain_text("没有标签的句子") == "没有标签的句子"
    assert plain_text("Q&A 与 5 < 8") == "Q&A 与 5 < 8"


def test_app_font_and_high_dpi_helpers():
    """字体工具：应用字体含界面字体栈，高 DPI 属性可设置。"""
    from PyQt5.QtCore import Qt
    from ui.typography import app_font, enable_high_dpi, install_app_fonts
    f = app_font()
    fams = f.families()
    assert "Microsoft YaHei UI" in fams or "Segoe UI" in fams
    enable_high_dpi()   # 不应抛异常
    app = QApplication.instance()
    install_app_fonts(app)
    assert app.font().families() == f.families()


# ---------------- UI 控件必须真接到项目已有的能力 ----------------
def test_new_conversation_clears_history_but_keeps_long_term_memory(
        fw_and_window, monkeypatch):
    fw, w, _path = fw_and_window
    try:
        fw.memory.add_pair("你好", "在的")
        fw.memory.remember_fact("我叫小明")
        fw.memory.preferences["city"] = "北京"
        monkeypatch.setattr(w, "_confirm_new_conversation", lambda: True)
        w._new_conversation()
        assert fw.memory.history == []
        assert fw.memory.facts == ["我叫小明"]
        assert fw.memory.preferences["city"] == "北京"
        assert w._stack.currentIndex() == 0
        assert any("还没有对话" in b.text()
                   for b in w._chat_list.findChildren(QLabel))
    finally:
        fw.memory.clear()


def test_new_conversation_respects_cancel(fw_and_window, monkeypatch):
    fw, w, _path = fw_and_window
    try:
        fw.memory.add_pair("别删", "收到")
        monkeypatch.setattr(w, "_confirm_new_conversation", lambda: False)
        w._new_conversation()
        assert len(fw.memory.history) == 2
    finally:
        fw.memory.clear()


def test_clear_history_is_separate_from_clear_all():
    """「新建对话」不能误用会把长期记忆一起删掉的 clear()。"""
    from pathlib import Path
    src = Path("framework/memory_system.py").read_text(encoding="utf-8")
    ws = Path("ui/workspace_window.py").read_text(encoding="utf-8")
    assert "def clear_history(self)" in src
    assert "memory.clear_history()" in ws
    assert "memory.clear()" not in ws


def test_update_button_opens_real_releases_page(fw_and_window, monkeypatch):
    from ui import workspace_window as ws_mod
    _fw, w, _path = fw_and_window
    opened = []

    class FakeDesktop:
        @staticmethod
        def openUrl(url):
            opened.append(url.toString())
            return True

    monkeypatch.setattr(ws_mod, "QDesktopServices", FakeDesktop)
    btn = w.findChild(QPushButton, "updateBtn")
    assert btn is not None and "查看更新" in btn.text()
    btn.click()
    assert opened and "github.com/hm-yep/L-Agent/releases" in opened[0]


def test_file_menu_exit_is_wired_to_real_quit(fw_and_window):
    _fw, w, _path = fw_and_window
    from pathlib import Path
    pet = Path("ui/pet_window.py").read_text(encoding="utf-8")
    assert "quit_requested.connect(self.quit_app)" in pet
    called = []
    w.quit_requested.connect(lambda: called.append(True))
    file_action = next(a for a in w.menuBar().actions()
                       if a.text().startswith("文件"))
    exit_action = next(a for a in file_action.menu().actions()
                       if "退出程序" in a.text())
    exit_action.trigger()
    assert called == [True]


def test_about_page_describes_only_real_controls(fw_and_window):
    _fw, w, _path = fw_and_window
    w.switch_page(7)
    text = w.help_text()
    assert "新建对话" in text and "查看更新" in text
    assert "退出程序" in text
    assert "加号按钮" not in text
    assert "分支 / 访问级别" not in text
    assert "@ 引用桌面文件" not in text
    # 说明书不再当整页正文，页面本身要给出真开关与数据目录
    assert "七个能力开关" in text


def test_switches_are_buttons_not_readonly_chips(fw_and_window):
    """「当前开关」必须是可按的控件。

    早先它长成胶囊样子的 QLabel：看着像按钮、点了没反应，等于摆设。
    """
    from PyQt5.QtWidgets import QLabel
    from ui.workspace_window import SWITCH_ROWS
    _fw, w, _path = fw_and_window
    w.switch_page(7)
    btns = [b for b in w.findChildren(QPushButton)
            if b.objectName() == "switchToggle"]
    assert len(btns) == len(SWITCH_ROWS) == 7
    for b in btns:
        assert b.isCheckable() and b.isEnabled()
        assert b.text() in ("开启", "关闭")
        assert b.cursor().shape() == Qt.PointingHandCursor
    # 顶栏统计可以继续用只读 statChip，但「开启/关闭」不许再用只读标签
    assert not [c for c in w.findChildren(QLabel)
                if c.objectName() == "statChip"
                and c.text() in ("开启", "关闭")]


def test_clicking_a_switch_flips_config_and_persists(
        fw_and_window, tmp_path, monkeypatch):
    """点一下：内存标志真的变、真的写进 user_settings.json、按钮文字跟着变。

    _user_settings_path 指向临时目录——测试绝不写用户真实 %APPDATA%。
    """
    import json
    from framework import config as cfg
    from ui.workspace_window import SWITCH_ROWS
    fw, w, _path = fw_and_window
    monkeypatch.setattr(cfg, "_user_settings_path",
                        lambda: str(tmp_path / "user_settings.json"))
    idx = [name for name, _attr, _d in SWITCH_ROWS].index("契约严格模式")
    btn = [b for b in w.findChildren(QPushButton)
           if b.objectName() == "switchToggle"][idx]
    monkeypatch.setattr(cfg, "EFFECT_SPEC_STRICT", False)
    w._refresh_about()
    btn = [b for b in w.findChildren(QPushButton)
           if b.objectName() == "switchToggle"][idx]
    assert btn.isChecked() is False and btn.text() == "关闭"

    btn.click()
    assert cfg.EFFECT_SPEC_STRICT is True
    assert btn.text() == "开启"
    assert "契约严格模式" in w._feedback.text() \
        and "已开启" in w._feedback.text(), "点了要在状态栏留一句结果"
    saved = json.loads(
        (tmp_path / "user_settings.json").read_text(encoding="utf-8"))
    assert saved["effect_spec_strict"] is True

    btn.click()
    assert cfg.EFFECT_SPEC_STRICT is False
    assert btn.text() == "关闭"


def test_switch_changes_behavior_without_restart(
        fw_and_window, tmp_path, monkeypatch):
    """开关两头都要真的管用：打开当场建子系统，关掉当场撤掉。

    调用点有的只看 config.X，有的只看对象在不在（_traj_event、联网偏好的闸门）。
    只翻标志不撤对象，「关闭」这一半就还是摆设。
    """
    from framework import config as cfg
    fw, _w, _path = fw_and_window
    monkeypatch.setattr(cfg, "_user_settings_path",
                        lambda: str(tmp_path / "user_settings.json"))
    old_store, old_flag = fw.trajectory, cfg.TRAJECTORY_ENABLED
    try:
        fw.trajectory = None
        monkeypatch.setattr(cfg, "TRAJECTORY_ENABLED", False)
        ok, _note = fw.set_capability("TRAJECTORY_ENABLED", True)
        assert ok is True
        assert fw.trajectory is not None, "开关打开后没把轨迹子系统建起来"

        ok, _note = fw.set_capability("TRAJECTORY_ENABLED", False)
        assert ok is True
        assert fw.trajectory is None, "关掉后对象还在，写轨迹的代码会继续跑"
        assert fw._traj_event("turn_start", text="x") is None
    finally:
        fw.trajectory, cfg.TRAJECTORY_ENABLED = old_store, old_flag


def test_switch_rolls_back_when_saving_fails(
        fw_and_window, tmp_path, monkeypatch):
    """写盘失败要说「没存上」并把标志退回原值，不能报假成功。"""
    from framework import config as cfg
    fw, _w, _path = fw_and_window
    monkeypatch.setattr(cfg, "PLAN_DAG_ENABLED", True)
    monkeypatch.setattr(cfg, "save_user_settings", lambda **kw: False)
    ok, note = fw.set_capability("PLAN_DAG_ENABLED", False)
    assert ok is False
    assert cfg.PLAN_DAG_ENABLED is True, "没存上还留着新值，界面就在骗人"
    assert "没写进" in note

    # 带子系统的开关也一样：写盘失败要把撤掉的对象放回去
    # Framework 是单例，轨迹对象换过就要还原，否则污染后面的用例
    sentinel = object()
    real_store, real_flag = fw.trajectory, cfg.TRAJECTORY_ENABLED
    fw.trajectory = sentinel
    monkeypatch.setattr(cfg, "TRAJECTORY_ENABLED", True)
    try:
        ok, _note = fw.set_capability("TRAJECTORY_ENABLED", False)
        assert ok is False
        assert fw.trajectory is sentinel, "没存上却把轨迹对象撤了，等于偷偷改了行为"
        assert cfg.TRAJECTORY_ENABLED is True
    finally:
        fw.trajectory, cfg.TRAJECTORY_ENABLED = real_store, real_flag


def test_capability_flags_reload_from_settings_file(tmp_path, monkeypatch):
    """下次启动读回同样的值：apply_user_settings() 要认这五个键。"""
    from framework import config as cfg
    monkeypatch.setattr(cfg, "_user_settings_path",
                        lambda: str(tmp_path / "user_settings.json"))
    for name in ("USE_REAL_AI", "DEEPSEEK_API_KEY"):
        monkeypatch.setattr(cfg, name, getattr(cfg, name))  # 测完还原
    cfg.save_user_settings(
        trajectory_enabled=False, preference_learning=False,
        evolution_enabled=False, plan_dag_enabled=False,
        effect_spec_strict=True)
    for attr in cfg.CAPABILITY_KEYS:
        monkeypatch.setattr(cfg, attr, not getattr(cfg, attr))
    cfg.apply_user_settings()
    assert cfg.TRAJECTORY_ENABLED is False
    assert cfg.PREFERENCE_LEARNING is False
    assert cfg.EVOLUTION_ENABLED is False
    assert cfg.PLAN_DAG_ENABLED is False
    assert cfg.EFFECT_SPEC_STRICT is True


def test_preview_input_has_no_unwired_buttons(fw_and_window):
    from pathlib import Path
    from ui.input_dialog import InputDialog
    assert "set_aux_buttons_visible(False)" in Path(
        "ui/preview_window.py").read_text(encoding="utf-8")
    dlg = InputDialog()
    assert dlg._history_btn.isVisibleTo(dlg)
    dlg.set_aux_buttons_visible(False)
    assert not dlg._history_btn.isVisibleTo(dlg)
    assert not dlg._workspace_btn.isVisibleTo(dlg)


def test_info_card_title_is_never_crushed_by_a_short_path():
    """卡片宽度要同时容得下标题行。

    上一版只按路径算宽：路径短、文件名长时，157px 的标题被压成 17px 一条缝。
    """
    from ui.ui_manager import InfoCardWidget
    card = InfoCardWidget()
    title = "周报_2026-11-17.docx"
    card.show_card(title, r"C:\Users\me\Desktop\周报_2026-11-17.docx", "24 KB")
    card._title.ensurePolished()
    need = card._title.fontMetrics().horizontalAdvance(title)
    assert card._title.contentsRect().width() >= need, (
        "标题需要 %dpx，只给到 %dpx" % (
            need, card._title.contentsRect().width()))
    card.close()


def test_bare_qwidget_windows_are_named_for_the_window_background():
    """预览台是裸 QWidget 顶层窗口：不在样式表里点名，它退回系统浅灰底，
    深色控件配浅字（复选框文字、滑块旁标签）会直接看不见。"""
    from pathlib import Path
    from ui import theme
    qss = theme.build_qss(theme.DARK)
    assert "QWidget#previewWindow" in qss
    src = Path("ui/preview_window.py").read_text(encoding="utf-8")
    assert 'setObjectName("previewWindow")' in src


def test_styles_have_one_source_only():
    """样式表只允许挂在 QApplication 上（ui/theme.py 拼出来的那一套）。

    控件自己 setStyleSheet 就是又开一套调色板：改版前仓库里同时存在
    _QSSTEMPLATE、style.qss、预览台内联三段，谁也不认谁，改一处花一处。
    """
    import re
    from pathlib import Path
    offenders = {p.name for p in Path("ui").glob("*.py")
                 if re.search(r"\.setStyleSheet\(",
                              p.read_text(encoding="utf-8"))}
    assert offenders == {"theme.py"}, "这些控件还在自己写样式: %s" % offenders


def test_grants_page_has_honest_empty_state_and_is_navigable(fw_and_window):
    """第 7 页空态不说假话，左侧计数按钮点进去就是它。"""
    fw, w, _path = fw_and_window
    fw.revoke_grants()
    idx = next(i for i, p in enumerate(PAGE_DEFS) if p[0] == "grants")
    w.switch_page(idx)
    assert w._stack.currentIndex() == idx
    assert "当前只放开默认目录" in w._grants_empty.text()
    assert "txt/md" in w._grants_formats.text()
    w._nav_grants_btn.click()
    assert w._stack.currentIndex() == idx


def test_revoke_button_really_clears_and_forces_reask(
        fw_and_window, tmp_path, monkeypatch):
    """「全部撤销」不是摆设：撤完同一路径必须重新走授权。"""
    from framework import path_guard
    fw, w, _path = fw_and_window
    monkeypatch.setattr(path_guard, "allowed_roots", lambda: [])
    target = tmp_path / "资料.txt"
    target.write_text("x", encoding="utf-8")
    fw.revoke_grants()
    fw.grant_service.store.add("tree", str(tmp_path))

    idx = next(i for i, p in enumerate(PAGE_DEFS) if p[0] == "grants")
    w.switch_page(idx)
    assert w._grants_table.rowCount() == 1
    w._revoke_all_grants()
    assert w._grants_table.rowCount() == 0
    assert fw.grant_service.check(str(target))[0] == "grant"


def test_chat_page_shows_read_evidence_card(fw_and_window, monkeypatch):
    """不能只说「读完了」：页数/截断/跳过要能在对话页看见。"""
    fw, w, _path = fw_and_window
    monkeypatch.setattr(w, "_read_log", lambda: [{
        "name": "报告.pdf", "kind": "pdf", "pages": 3, "chars": 1200,
        "note": "只读了前 3 页", "skipped": ["第 4 页起"],
    }])
    w._refresh_chat()
    cards = [lab.text() for lab in w._chat_list.findChildren(QLabel)
             if lab.objectName() == "grantEvidenceCard"]
    assert cards
    assert "读了 3 页" in cards[0] and "截到 1200 字" in cards[0]
    assert "跳过" in cards[0]


def test_float_cards_use_stroke_icons_only():
    """浮层卡不能回退到彩色 emoji：图标统一由 ui/icons.py 线框渲染。"""
    root = Path(__file__).resolve().parent.parent
    grant_src = (root / "ui" / "grant_card.py").read_text(encoding="utf-8")
    pet_src = (root / "ui" / "pet_window.py").read_text(encoding="utf-8")
    for ch in ("🔓", "⏱", "🔐"):
        assert ch not in grant_src
    for ch in ("🚀", "🗑", "🧹", "📂", "📄"):
        assert ch not in pet_src

    for name in ("unlock", "timer", "clipboard", "check-circle", "robot",
                 "folder", "file-text", "trash", "x-circle", "play", "x"):
        assert not icons.icon(name, "#E8EBF0", 17).isNull(), name

    card = GrantCard()
    assert not card._title_icon.pixmap().isNull()
    assert not card._countdown_icon.pixmap().isNull()


def test_operation_rows_and_placeholder_expose_new_capability(fw_and_window):
    fw, w, _path = fw_and_window
    joined = "".join(OPERATION_ROWS)
    assert "只读这一个" in joined and "允许整个文件夹" in joined
    assert "重启后失效" in joined and "本机访问" in joined
    assert "D:/资料/报告.pdf" in w._chat_input.placeholderText()


def test_web_page_embeds_same_url_as_browser(fw_and_window):
    """网页台不是另做一套：地址就是浏览器访问的那一个。"""
    from framework.api_service import API_BASE

    fw, w, _path = fw_and_window
    idx = next(i for i, p in enumerate(PAGE_DEFS) if p[0] == "web")
    assert w._web_base == API_BASE
    assert w._page_widgets["web"] is w._stack.widget(idx)
    # 构造窗口时不该为一个没看的页面拉起浏览器内核或后台服务
    assert w._web_view is None and w._web_ready is False


def test_web_page_degrades_without_webengine(fw_and_window, monkeypatch):
    """没装 PyQtWebEngine 时给说明，不让整个工作台起不来。"""
    import builtins
    fw, w, _path = fw_and_window
    w.switch_page(next(i for i, p in enumerate(PAGE_DEFS) if p[0] == "web"))
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "PyQt5.QtWebEngineWidgets":
            raise ImportError("simulated missing PyQtWebEngine")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    # 重置懒加载标记，强制再走一次失败路径
    w._web_ready = False
    w._refresh_web()
    assert w._web_view is None
    assert "PyQtWebEngine" in w._web_placeholder.text()
    assert w._stack.count() == len(PAGE_DEFS)   # 其余页面不受影响


def test_web_page_start_button_is_wired(fw_and_window):
    """没起服务时给的是真能点的启动按钮，不是一句空话。"""
    fw, w, _path = fw_and_window
    idx = next(i for i, p in enumerate(PAGE_DEFS) if p[0] == "web")
    w.switch_page(idx)
    # 探测服务是否起来的网络调用打桩：当作没起
    import framework.api_service as api_service
    real_alive = api_service.api_alive
    api_service.api_alive = lambda *a, **k: False
    try:
        w._web_ready = False
        w._refresh_web()
        # WebEngine 在本机可用时，服务没起应显示启动按钮
        if w._web_view is None and "PyQtWebEngine" not in w._web_placeholder.text():
            assert w._web_start_btn.isVisible()
            assert w._web_start_btn.text().strip() == "启动本地服务"
    finally:
        api_service.api_alive = real_alive


def test_web_page_theme_follows_workspace(fw_and_window):
    """工作台换肤时把主题带给内嵌网页，避免两套颜色打架。"""
    fw, w, _path = fw_and_window
    calls = []
    w._web_view = type("FakeView", (), {
        "page": lambda self: type("FakePage", (), {
            "runJavaScript": staticmethod(lambda js: calls.append(js))
        })(),
    })()
    w._apply_theme("light")
    assert calls and "setAttribute('data-theme','light')" in calls[-1]
    w._apply_theme("dark")
    assert "setAttribute('data-theme','dark')" in calls[-1]


def test_llm_preference_extraction_is_skipped_without_api_key(
        fw_and_window, monkeypatch):
    """没配 Key 时连轮次计数都不该走，更不许偷偷发请求。"""
    from framework import config
    fw, _w, _path = fw_and_window
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(fw, "_pref_turn_seq", 0)
    fw._maybe_llm_preferences()
    assert fw._pref_turn_seq == 0


def test_llm_preference_extraction_counts_turns_and_skips_when_busy(
        fw_and_window, monkeypatch):
    """每 3 轮才投一次；已有请求在途时不许再起一个。"""
    from framework import config
    fw, _w, _path = fw_and_window
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(config, "PREFERENCE_LLM", True)
    monkeypatch.setattr(config, "PREFERENCE_LLM_EVERY", 3)
    monkeypatch.setattr(fw, "_pref_turn_seq", 0)
    monkeypatch.setattr(fw, "_pref_llm_busy", True)   # 假装有一个在途
    for _ in range(3):
        fw._maybe_llm_preferences()
    assert fw._pref_turn_seq == 3
    assert fw._pref_llm_busy is True       # 没有被第二次启动顶掉
