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
from PyQt5.QtWidgets import QApplication, QMainWindow  # noqa: E402

from framework.core import Framework  # noqa: E402
from ui.workspace_window import PAGE_DEFS, WorkspaceWindow  # noqa: E402


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
    assert w.windowTitle() == "凌零 · 工作台"
    assert w.minimumWidth() >= 900 and w.minimumHeight() >= 600


def test_six_pages_are_real(fw_and_window):
    _fw, w, _path = fw_and_window
    assert w._stack.count() == len(PAGE_DEFS) == 6
    for i, (_key, title, _hint) in enumerate(PAGE_DEFS):
        w.switch_page(i)
        assert w._title.text() == title
        assert w._nav_buttons[i].isChecked()
        assert w._stack.currentIndex() == i


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
    rendered = w._chat_view.toHtml()
    assert "遵命" in rendered
    assert "你好" in rendered
    assert "<script>alert" not in rendered, "用户输入必须转义，不能注入页面"


def test_sending_from_workspace_goes_to_framework(fw_and_window, monkeypatch):
    fw, w, _path = fw_and_window
    seen = []
    monkeypatch.setattr(fw, "send_command", lambda text: seen.append(text))
    w._chat_input.setText("加个待办：交周报")
    w._send()
    assert seen == ["加个待办：交周报"]
    assert w._chat_input.text() == ""


def test_preference_page_shows_learned_preference(fw_and_window):
    fw, w, _path = fw_and_window
    fw.prefs.observe("我喜欢深色 PPT")
    w.switch_page(1)
    assert w._prefs_table.rowCount() >= 1
    assert w._prefs_table.item(0, 0).text() == "doc_theme"
    assert w._prefs_table.item(0, 1).text()


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
    fw, w, path = fw_and_window
    light = w.styleSheet()
    w.toggle_theme()
    assert w._theme == "dark"
    assert w.styleSheet() != light
    assert "浅色" in w._theme_btn.text()

    w2 = WorkspaceWindow(fw, settings_path=path)
    try:
        assert w2._theme == "dark"
    finally:
        w2.detach()
        w2.hide()


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
    assert "⛶ 工作台" in inp
    tray = Path("ui/ui_manager.py").read_text(encoding="utf-8")
    assert "打开工作台" in tray


def test_detach_stops_timer_and_disconnects(fw_and_window):
    _fw, w, _path = fw_and_window
    assert w._status_timer.isActive()
    w.detach()
    assert not w._status_timer.isActive()
    w.detach()   # 幂等，不抛异常
