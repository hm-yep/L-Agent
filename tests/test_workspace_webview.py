# -*- coding: utf-8 -*-
"""工作台第 8 页内嵌网页台的启动时机与降级文案。

真机事故（2026-11-18 用户截图）：第 8 页永远显示「这个环境没有安装
PyQtWebEngine，请执行 pip install」，但那台机器上包是装着的（PyQtWebEngine
5.15.6 / Qt 5.15.2）。真因不在依赖，在**顺序**：PyQt 要求
`Qt.AA_ShareOpenGLContexts` 在 QApplication 实例化之前设好，否则
`from PyQt5.QtWebEngineWidgets import ...` 直接抛 ImportError——
而工作台是懒加载，import 一定发生在 QApplication 之后，所以正常启动
（python main.py / 启动助手.bat）**每一次**都只能降级。
上一轮的实窗截图之所以是好的，是因为那个抓图脚本先 import 了 WebEngine
再建 QApplication：验证方式本身不会失败，等于没验证。

口径：真包只在子进程里验（本进程可能已经有 QApplication，那时真包必然导入失败，
拿它判断依赖在不在＝自己造一个假红）；界面分支用塞进 sys.modules 的假模块驱动，
所以这些用例在任何机器上都必须真的执行，不许靠 skip 蒙过去。
全部离屏 Qt、不联网、不起服务、不调模型。
"""

import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

from framework.core import Framework  # noqa: E402
from ui.workspace_window import WorkspaceWindow  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MODULE = "PyQt5.QtWebEngineWidgets"

# 必须留住 QApplication 引用，写成 `QApplication.instance() or QApplication([])`
# 不赋值会让新建的 app 被立刻 GC，之后构造 QWidget 原生崩溃（退出码 127）。
_APP = None


def _run_child(script: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-c", script], cwd=str(ROOT),
                          env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)


def _real_package_present() -> bool:
    proc = _run_child(
        "import importlib.util\n"
        "print('YES' if importlib.util.find_spec(%r) else 'NO')\n" % MODULE)
    return "YES" in (proc.stdout or "")


_REAL_WEBENGINE = _real_package_present()
needs_real_webengine = pytest.mark.skipif(
    not _REAL_WEBENGINE,
    reason="这台机器没装 PyQtWebEngine，只能在子进程里验真包，跳过而不是判红")


class _FakeView(QWidget):
    """够用的 QWebEngineView 替身：得是真 QWidget，因为产品代码会 addWidget 它。"""

    built = []

    def __init__(self, parent=None):
        super().__init__(parent)
        _FakeView.built.append(self)
        self.url = None
        self.connected = []

    @property
    def urlChanged(self):
        outer = self

        class _Sig:
            def connect(self, fn):
                outer.connected.append(fn)

        return _Sig()

    def setUrl(self, url):
        self.url = url

    def page(self):
        raise RuntimeError("假视图没有真页面")


@pytest.fixture
def window(tmp_path):
    global _APP
    _APP = QApplication.instance() or QApplication([])
    _FakeView.built = []
    w = WorkspaceWindow(Framework.instance(),
                        settings_path=str(tmp_path / "web.ini"))
    yield w
    w.detach()
    w.hide()


def _install_fake_module(monkeypatch, view_cls):
    """把 PyQt5.QtWebEngineWidgets 换成假模块：本进程里真包未必能导入。"""
    fake = types.ModuleType(MODULE)
    fake.QWebEngineView = view_cls
    monkeypatch.setitem(sys.modules, MODULE, fake)
    return fake


def _block_import(monkeypatch, spec_value):
    """让 `from PyQt5.QtWebEngineWidgets import ...` 抛 ImportError，
    同时把 find_spec 的结果换成指定值，用来分开两条降级文案。"""
    monkeypatch.setitem(sys.modules, MODULE, None)
    monkeypatch.setattr("importlib.util.find_spec",
                        lambda name, *a, **k: spec_value)


@needs_real_webengine
def test_import_after_qapplication_without_attribute_really_fails():
    """先证明这个故障类是真的：不设属性、QApplication 之后再 import 必须抛。

    这条红了说明本机 PyQt 版本已经不要求这个顺序了，那 main.py 里那行
    就该删掉——它拦的是「文档说装了就能用、实际永远降级」这件事。
    """
    proc = _run_child(
        "from PyQt5.QtWidgets import QApplication\n"
        "app = QApplication([])\n"
        "try:\n"
        "    from PyQt5.QtWebEngineWidgets import QWebEngineView\n"
        "except ImportError as e:\n"
        "    print('IMPORTERROR::', e)\n"
        "else:\n"
        "    print('UNEXPECTED-OK')\n")
    out = (proc.stdout or "") + (proc.stderr or "")
    assert "IMPORTERROR::" in out, out[-400:]
    assert "AA_ShareOpenGLContexts" in out, out[-400:]


@needs_real_webengine
def test_attribute_before_qapplication_lets_the_late_import_work():
    """再证明修法成立：按 main.py 的顺序设好属性，懒加载就能真的建出视图。"""
    proc = _run_child(
        "from PyQt5.QtCore import QCoreApplication, Qt\n"
        "from PyQt5.QtWidgets import QApplication\n"
        "QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)\n"
        "app = QApplication([])\n"
        "from PyQt5.QtWebEngineWidgets import QWebEngineView\n"
        "view = QWebEngineView()\n"
        "print('VIEW-OK', view is not None)\n")
    out = (proc.stdout or "") + (proc.stderr or "")
    assert "VIEW-OK True" in out, out[-400:]


def test_main_py_sets_the_attribute_before_creating_qapplication():
    """产品代码必须真的按这个顺序走——上面两条只证明规则，不证明它被遵守。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8").splitlines()

    def first_line(needle):
        for i, line in enumerate(src, 1):
            if needle in line and not line.strip().startswith("#"):
                return i
        return None

    # 认的是那行真的调用，不是提到过这个名字——selftest 的说明文字里也有它，
    # 只搜名字的话把代码删了测试照样绿。
    attr_line = first_line("QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts")
    app_line = first_line("app = QApplication(sys.argv)")
    assert attr_line, "main.py 里没设 AA_ShareOpenGLContexts，第 8 页必然永远降级"
    assert app_line, "main.py 里找不到 QApplication 创建点"
    assert attr_line < app_line, (
        "AA_ShareOpenGLContexts 设在第 %d 行、QApplication 在第 %d 行："
        "这个属性晚一步就失效" % (attr_line, app_line))


def test_embed_view_really_gets_built_and_placeholder_retired(window, monkeypatch):
    """一切就绪时必须真的内嵌：视图建出来、URL 发出去、说明卡让位。"""
    _install_fake_module(monkeypatch, _FakeView)
    monkeypatch.setattr("framework.api_service.api_alive", lambda: True)
    events = []
    window._notify = events.append
    window._try_init_web_view()
    assert window._web_view is not None
    assert window._web_view.url is not None, "没 setUrl 就是白屏"
    assert window._web_placeholder.isHidden() or not window._web_placeholder.text()
    assert any("网页台已内嵌" in str(e) for e in events), events


def test_fallback_copy_does_not_blame_a_missing_package_that_is_installed(
        window, monkeypatch):
    """包装着却起不来时，界面不许喊「请 pip install」——那是假话。"""
    _block_import(monkeypatch, object())
    window._try_init_web_view()
    text = window._web_placeholder.text()
    assert "没有安装" not in text, text
    assert "装着" in text and "AA_ShareOpenGLContexts" in text, text
    assert window._web_view is None


def test_fallback_copy_still_says_install_when_really_absent(window, monkeypatch):
    _block_import(monkeypatch, None)
    window._try_init_web_view()
    text = window._web_placeholder.text()
    assert "没有安装 PyQtWebEngine" in text, text
    assert window._web_view is None


def test_view_construction_failure_does_not_escape_the_slot(window, monkeypatch):
    """建视图失败也不能把异常漏回 Qt 槽：槽里未捕获异常 = 整个进程被 abort。"""

    class Boom:
        def __init__(self, *_a, **_k):
            raise RuntimeError("Qt WebEngine 起不来")

    _install_fake_module(monkeypatch, Boom)
    monkeypatch.setattr("framework.api_service.api_alive", lambda: True)
    window._try_init_web_view()          # 不抛异常就是过
    assert window._web_view is None
    assert "创建失败" in window._web_placeholder.text()


def test_service_not_running_gives_a_button_that_really_starts_it(window,
                                                                  monkeypatch):
    """没起服务时不许只留一句话：说明要写清、按钮点了要真的去起服务。"""
    _install_fake_module(monkeypatch, _FakeView)
    monkeypatch.setattr("framework.api_service.api_alive", lambda: False)
    window._try_init_web_view()
    assert window._web_view is None
    assert "还没启动" in window._web_placeholder.text()
    assert not window._web_start_btn.isHidden(), "按钮被藏着 = 说明卡成了死路"

    calls = []
    monkeypatch.setattr("framework.api_service.start_api_in_thread",
                        lambda *a, **k: calls.append((a, k)))
    window._web_start_btn.click()
    assert calls, "「启动本地服务」没连到真启动函数＝摆设"
    assert "正在启动" in window._web_placeholder.text()
