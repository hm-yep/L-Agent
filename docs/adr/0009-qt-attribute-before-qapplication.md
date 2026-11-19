# 0009 · 进程级 Qt 属性必须在 QApplication 之前设，降级文案必须按原因分支

- 状态：已采纳（2026-11-18）
- 相关：`main.py`、`ui/workspace_window.py`、`tests/test_workspace_webview.py`、`tools/check_contracts.py`、[ADR 0007](0007-cross-end-shared-grant-channel.md)

## 背景

工作台第 8 页「网页调试台」用 `QWebEngineView` 内嵌本地网页，代码里是**懒加载**：
切到那一页才 `from PyQt5.QtWebEngineWidgets import QWebEngineView`，import 失败就降级成一张说明卡。

2026-11-18 的真机截图显示这张卡永远在，内容写着「这个环境没有安装 PyQtWebEngine，请执行 pip install PyQtWebEngine 后重启」。
但同一台机器上：

- `pip show PyQtWebEngine` → 5.15.6（Qt 5.15.2，C:\python\python.exe 3.13.6），包是装着的；
- 把那句 import 放在 `QApplication(sys.argv)` **之后**跑 → `ImportError: QtWebEngineWidgets must be imported or Qt.AA_ShareOpenGLContexts must be set before a QCoreApplication instance is created`。

所以这不是依赖问题，是**初始化顺序**问题：PyQt5 只靠 `AA_ShareOpenGLContexts` 这个进程级属性判断 WebEngine 的初始化时机，而 `main.py` 从来没设过它。懒加载又必然发生在 `QApplication` 之后，于是这条分支在**每一次正常启动**（`python main.py`、`启动助手.bat`）里都会走到——功能对用户从来没存在过。

值得单独记一笔的是：仓库里存着一张 2026-11-17 的截图 `output/grant_ui/workspace_web_dark.png`，里面第 8 页**确实**渲染着网页。它没有说谎，但它什么也没证明——那张图是抓图脚本先 `import` 了 WebEngine、之后才建 `QApplication` 得到的，正好是这个属性唯一不需要被设好的顺序。**验证方式本身不会失败，等于没有验证。**

## 决策

### 1. 进程级属性只在唯一启动入口设，且排在 `QApplication` 之前

`main.py` 里 `QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)` 紧跟在 `app = QApplication(sys.argv)` 前面（当前 449 行 / 451 行）。工作台、预览窗都不许自己补设——属性是进程级的，设第二次没用，还会让人误以为「哪里都能设」。

### 2. 降级文案按**原因**分支，不许共用一句

`_try_init_web_view()` 的 `except ImportError` 里先用 `importlib.util.find_spec("PyQt5.QtWebEngineWidgets")` 判一次包在不在：

- 真不在 → 才喊 `pip install PyQtWebEngine`；
- 在但起不来 → 写「PyQtWebEngine 是装着的，但内嵌浏览器没能初始化：<异常原话>」，说明本版要靠 `python main.py` 启动、属性要在 QApplication 之前设好，并给出临时绕行（浏览器直接打开）。

界面文案是一条**可失败项**：包装着却喊人装包，就是对着用户说谎，而且会把人引去重装依赖、永远查不到顺序问题。

### 3. 槽里的构造一律包异常

`QWebEngineView(...)` 的整个构造过程被 `try` 包住，失败只写占位文案 + 记日志。理由和授权卡那条一样：PyQt5 在信号槽里遇到未捕获异常会**直接 abort 整个进程**（退出码 127，不留 traceback）。

### 4. 顺序型缺陷只能由顺序型验证拦

- `tests/test_workspace_webview.py` 里两条用例**开子进程**跑真实顺序：先建 `QApplication` 再 import——不设属性那条断言真的 `IMPORTERROR`，设了的那条断言真的拿到视图；
- 一条静态用例读 `main.py` 的行号，断言那行**真的调用**排在 `app = QApplication(` 之前。搜的字符串是 `QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts`，不是裸的 `AA_ShareOpenGLContexts`——后者在 selftest 的说明文字里也出现，只搜名字的话把那行代码删了测试照样绿；
- 启动冒烟 `--selftest` 新增一项「网页台可内嵌」：此刻 `QApplication` 已经存在，正好复现工作台的懒加载时机；真没装包的环境记 SKIP，不算失败（它是可选依赖，不是顺序错了）；
- 契约层同样搜真调用、同样要求 `pip install PyQtWebEngine` 这句话在工作台源码里**只出现一次**（只许待在「真没装」那条分支）。

UI 的另外几条分支用假模块驱动（往 `sys.modules` 里塞一个 `QWidget` 子类），这样「包装着却初始化失败」「创建时抛异常」「服务没启动」三种形态都能在同一个 pytest 进程里跑，不用装第二套依赖。

## 为什么这样设计

可选依赖的失败必须区分「没装」和「装了但起不来」，因为这两种情况的**修法完全相反**：前者补依赖，后者改启动顺序。把它们合并成一句提示，等于把能自查的缺陷变成只能重装环境的猜测。

另一个原因更一般：一个只在非主流启动路径下失效的功能，happy-path 测试和手工截图都可能长期全绿。要拦住它，验证必须继承**缺陷的形状**——顺序 bug 就用顺序测试。

## 代价与边界

- 两条子进程用例各要起一次 Python + Qt，全套 pytest 多花几秒；本机真没装 PyQtWebEngine 时它们 SKIP（SKIP 只允许发生在这一条上，且理由写进用例名）。
- 离屏 Qt（CI 与 `--selftest`）验的是「能不能 import、能不能构造视图」，**不验渲染**。渲染由一张真窗口截图作证：`output/grant_ui/workspace_web_after_fix_screen.png`（2560×1494，页面顶部大标题「凌零 · 智能体服务」、四个能力徽章都绿），契约里有一条要求这张图存在且不小于 50 KB。抓取必须用 `primaryScreen().grabWindow(winId())`——`QWidget.grab()` 抓不到 Chromium 的合成层，会得到一片纯白，那是一张**会说谎的证据**。
- 截图由一次性探针产生（按 `main.py` 的顺序设属性、建 `QApplication`，再切到第 8 页），跑完即删；产品路径的顺序本身由上面的行号用例与契约钉住，不靠这张图。
- `AA_ShareOpenGLContexts` 是进程级属性，对所有 Qt 窗口生效；本项目只此一处设，不存在互相覆盖。
- 通过懒加载保住「没装 WebEngine 也能启动整个桌宠」这条性质不变：包缺失依旧降级，不再谎报原因。

## 验证

```
python -m pytest -q                                  # 533 passed / 1 skipped
python -m pytest -q tests/test_workspace_webview.py  # 8 passed（0 skipped）
python tools/check_contracts.py                       # 96/96 通过
python main.py --selftest                             # 36/36 通过
```

真窗口实跑（2026-11-18，Windows 平台，非离屏）：按 `main.py` 的顺序启动后切到第 8 页，打印
`VIEW_CLASS QWebEngineView` / `VIEW_IS_WEBENGINE True` / `PLACEHOLDER_HIDDEN True` /
`FINAL_URL http://127.0.0.1:8000/` / `FINAL_TITLE 凌零 · 智能体服务`，截图纯白像素占比 0.000。
同一次运行里 `QWidget.grab()` 得到的却是整片纯白——差别只在抓取方式，这正是旧截图为什么会「证明」一个坏功能可用的同类陷阱。

负向验证（已于 2026-11-18 实跑）：删掉 `main.py` 里那行 `QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)`，
`test_main_py_sets_the_attribute_before_creating_qapplication` 与契约「WebEngine 属性设在 QApplication 之前」同时变红（94/96）；
恢复后重新全绿。故意先让检查失败一次，再宣布它有效。
