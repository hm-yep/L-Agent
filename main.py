# -*- coding: utf-8 -*-
"""桌面AI助手 - 应用入口

用法:
    python main.py                 正常启动（桌面版）
    python main.py --api           桌面版 + 后台 API 服务一起启动
    python main.py --with-rag      启动时预热 RAG 引擎（可选）
    python main.py --selftest      自动化冒烟测试（运行数秒后自动退出）
    python main.py --selftest --api  自测时也启动 API 服务

新增模块（RAG / 语音 / 多智能体 / FastAPI）：
- 桌面版与 API 版共用 framework/ 目录
- API 服务在后台线程运行，不阻塞桌面 UI
- RAG/语音/多智能体均为懒加载，用到才初始化
"""

import sys

# ============================================================
# 子进程入口（必须在 PyQt5 导入之前）
# ============================================================
# 语音识别子进程（见 framework/voice_input.py）。打包成 exe 后，
# 子进程以 "<exe> --voice-worker" 方式拉起；必须放在 PyQt5 导入之前，
# 否则子进程一加载 Qt 就会重新触发原生库冲突（0xC0000005）。
if "--voice-worker" in sys.argv[1:]:
    from framework.voice_input import _worker_main
    _worker_main()
    raise SystemExit(0)

# RAG 子进程（见 framework/rag_worker.py）。同理，必须在 PyQt5
# 导入之前处理，避免子进程加载 Qt 与 PyTorch 冲突。
if "--rag-worker" in sys.argv[1:]:
    from framework.rag_worker import _worker_main as _rag_worker_main
    _rag_worker_main()
    raise SystemExit(0)

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

from framework import config
from framework.core import Framework
from framework.utils import logger
from ui.pet_window import PetWindow


# ============================================================
# 后台启动 FastAPI 服务
# ============================================================
def start_api_in_thread(host: str = "127.0.0.1", port: int = 8000):
    """在后台线程启动 FastAPI 服务，不阻塞桌面版 UI。

    - uvicorn 支持 reload=False 时在线程里跑
    - daemon=True：主程序退出时自动结束
    - 端口被占用时只记日志，不影响桌面版
    """
    import threading

    def _run():
        try:
            import uvicorn
            logger.info(f"[Main] API 服务启动中: http://{host}:{port}")
            uvicorn.run("api.server:app", host=host, port=port,
                        log_level="warning", reload=False)
        except ImportError as e:
            logger.error(f"[Main] API 依赖缺失，无法启动: {e}")
            logger.error("请先执行: pip install fastapi uvicorn")
        except OSError as e:
            logger.error(f"[Main] API 端口被占用或权限不足: {e}")
        except Exception as e:                       # noqa: BLE001
            logger.exception(f"[Main] API 服务异常: {e}")

    t = threading.Thread(target=_run, name="api-server", daemon=True)
    t.start()
    return t


# ============================================================
# 预热 RAG 引擎（首次加载嵌入模型较慢，提前初始化）
# ============================================================
def warm_up_rag():
    """后台线程预热 RAG，避免第一次提问时卡顿。失败只记日志。

    注意：走 rag_client 子进程预热，不在主进程加载模型，
    避免 PyQt5 + PyTorch 原生冲突。
    """
    import threading

    def _run():
        try:
            from framework import rag_client
            rag_client.warm_up()
            count = rag_client.count()
            logger.info(f"[Main] RAG 预热完成，知识库现有 {count} 块")
        except Exception as e:               # noqa: BLE001
            logger.warning(f"[Main] RAG 预热失败（不影响桌面版）: {e}")

    t = threading.Thread(target=_run, name="rag-warmup", daemon=True)
    t.start()
    return t


# ============================================================
# 自测：原有逻辑（不变）
# ============================================================
def run_selftest(window: PetWindow):
    """自动化冒烟测试：验证核心链路，完成后自动退出"""
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond)))
        logger.info(f"[SelfTest] {name}: {'PASS' if cond else 'FAIL'}"
                    + (f" ({detail})" if detail else ""))

    # T+0.5s: 验证窗口与单例
    QTimer.singleShot(500, lambda: check("窗口可见", window.isVisible()))
    QTimer.singleShot(500, lambda: check("Framework单例",
                                          Framework.instance() is window.fw))

    # T+1s: 测试气泡回调
    QTimer.singleShot(1000, lambda: window.fw.show_bubble("自测气泡"))
    QTimer.singleShot(1500, lambda: check("气泡显示", window.bubble.isVisible()))

    # T+2s: 测试卡片回调
    QTimer.singleShot(2000, lambda: window.fw.show_card("自测应用",
                                                        r"C:\\mock\\app.exe",
                                                        "1.2 MB"))
    QTimer.singleShot(2500, lambda: check("卡片显示", window.card.isVisible()))

    # T+3s: 测试指令链路（模拟AI -> 动作序列）
    QTimer.singleShot(3000, lambda: window.fw.send_command("你好"))
    QTimer.singleShot(5500, lambda: check(
        "AI动作执行(忙碌标记)", window.fw._busy or not window.fw._action_queue))

    # T+6s: 测试搜索引擎（模拟模式）
    def test_search():
        info = window.fw.system.find_app("记事本")
        check("搜索引擎(模拟)", info is not None)
    QTimer.singleShot(6000, test_search)

    # T+7s: 白名单拦截测试
    def test_whitelist():
        ok, _ = window.fw.system.launch_app("未知恶意软件")
        check("白名单拦截", ok is False)
    QTimer.singleShot(7000, test_whitelist)

    # T+7.5s: 音效系统测试
    def test_sound():
        check("音效文件可用", window.sound.available())
        window.sound.play("bubble")
        window.sound.play("card")
        check("音效播放无异常", True)
        window.sound.set_enabled(False)
        check("音效可关闭", not window.sound.enabled)
        window.sound.set_enabled(True)
        check("音效可开启", window.sound.enabled)
    QTimer.singleShot(7500, test_sound)

    # T+8s: 设置窗口
    def test_settings():
        import os
        from PyQt5.QtWidgets import QApplication
        # 离屏 Qt 没有真实屏幕几何，sizeHint / 窗口尺寸会失真。
        # 这类布局断言只在有桌面会话时有意义，跳过而不是记成 FAIL，
        # 否则 --selftest 在 CI 或无头环境永远返回非零退出码（历史遗留的 2 个假失败）。
        offscreen = os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen"
        screen = QApplication.primaryScreen().availableGeometry()
        window.show_settings()
        dlg = window.settings
        check("设置窗口已弹出", dlg.isVisible())
        panel = dlg._panel
        if offscreen:
            logger.info("[SelfTest] 设置面板布局几何检查: SKIP（离屏 Qt）")
        else:
            check("设置面板铺满窗口",
                  (panel.width(), panel.height()) == (dlg.width(), dlg.height()))
            check("设置内容没有被压窄",
                  panel.sizeHint().width() <= dlg.width()
                  and panel.sizeHint().height() <= dlg.height())
        check("设置窗口整窗在屏幕内",
              dlg.x() >= screen.left() and dlg.y() >= screen.top()
              and dlg.x() + dlg.width() <= screen.right() + 1
              and dlg.y() + dlg.height() <= screen.bottom() + 1,
              f"窗口 {dlg.geometry().getRect()} / 屏幕 {screen.getRect()}")
        dlg.close()
    QTimer.singleShot(8000, test_settings)

    # T+8.5s: 用户设置持久化
    def test_persist():
        data = config.load_user_settings()
        config.save_user_settings(**data)
        check("用户设置可读写(存APPDATA)", config.load_user_settings() == data)
    QTimer.singleShot(8500, test_persist)

    # T+8.7s: 文档生成器
    def test_docgen():
        import os
        import tempfile
        from framework.doc_generator import generate
        fd, path = tempfile.mkstemp(suffix=".pptx")
        os.close(fd)
        ok, _ = generate("ppt", {"title": "自测",
                                 "slides": [{"title": "第一页",
                                             "bullets": ["要点"]}]}, path)
        check("文档生成器可用", ok and os.path.getsize(path) > 0)
        try:
            from framework.doc_editor import extract_content
            kind, spec = extract_content(path)
            check("文件内容提取可用", kind == "ppt"
                  and len(spec.get("slides", [])) == 3)
        except Exception as e:                  # noqa: BLE001
            check("文件内容提取可用", False, str(e))
        try:
            os.remove(path)
        except OSError:
            pass
        fd, hpath = tempfile.mkstemp(suffix=".html")
        os.close(fd)
        ok, _ = generate("html", {"title": "自测报告",
                                  "sections": [{"heading": "摘要",
                                                "paragraphs": ["一切正常"]}]},
                         hpath)
        check("网页报告生成可用", ok and os.path.getsize(hpath) > 0)
        try:
            os.remove(hpath)
        except OSError:
            pass
    QTimer.singleShot(8700, test_docgen)

    # T+8.9s: 智能体新能力（原有）
    def test_agent():
        import os
        import tempfile
        import time as _time
        from framework.action_parser import parse_actions
        from framework.ai_engine import extract_remind
        from framework.task_board import TaskBoard

        acts = parse_actions([
            {"action": "show_plan", "steps": "第1步调研；第2步写稿"},
            {"action": "remind", "text": "开会", "seconds": "85"},
            {"action": "see_screen", "question": "屏幕上是什么"},
            {"action": "remember", "fact": "用户喜欢深色"},
            {"action": "todo_add", "text": "交报告"},
            {"action": "run_workflow", "name": "每日科技早报"},
            {"action": "run_workflow"},
        ])
        names = [a["action"] for a in acts]
        check("智能体新动作解析", names == ["show_plan", "remind", "see_screen",
                                            "remember", "todo_add",
                                            "run_workflow"],
              str(names))
        check("工作流动作参数/缺参剔除",
              acts[-1].get("name") == "每日科技早报"
              and all(a["action"] != "run_workflow" for a in acts[:-1]),
              str(acts[-1]))
        check("工作流模板已配置",
              len(config.WORKFLOW_TEMPLATES) >= 3
              and all(t.get("command") and t.get("desc")
                      for t in config.WORKFLOW_TEMPLATES))
        check("计划步骤自动拆解", bool(acts) and acts[0]["steps"]
              == ["调研", "写稿"], str(acts[0]["steps"] if acts else None))
        parsed = (acts[1].get("seconds"), acts[1].get("text")) \
            if len(acts) > 1 else (None, None)
        check("提醒秒数归一化", parsed == (85.0, "开会"), str(parsed))
        check("复合时长换算(1分25秒=85秒)",
              extract_remind("1分25秒后提醒我看电视") == (85.0, "看电视"),
              str(extract_remind("1分25秒后提醒我看电视")))
        acts2 = parse_actions([
            {"action": "web_search", "query": "新能源市场"},
            {"action": "read_doc", "path": "D:/报告.docx"},
            {"action": "work_log"},
        ])
        check("检索/读档/日志动作解析",
              [a["action"] for a in acts2]
              == ["web_search", "read_doc", "work_log"],
              str([a.get("action") for a in acts2]))

        fw = window.fw
        fw.memory.remember_fact("自测偏好条目")
        check("长期记忆可注入提示词",
              "自测偏好条目" in fw.memory.get_facts_text())

        board = TaskBoard()
        board._file = os.path.join(tempfile.gettempdir(),
                                   "pet_selftest_tasks.json")
        board.reminders, board.todos = [], []
        board.deliveries, board.worklog = [], []
        board.add_reminder("自测提醒", _time.time() - 1)
        board.add_todo("自测待办")
        check("到期提醒可取出", any(r["text"] == "自测提醒"
                                    for r in board.pop_due_reminders()))
        check("待办可添加完成", board.complete_todo(1) is not None
              and all(t["done"] for t in board.todos
                      if t["text"] == "自测待办"))
        board.add_delivery("自测交付.xlsx", "C:/自测交付.xlsx", "xlsx")
        check("交付记录可用", len(board.deliveries) == 1
              and board.remove_delivery(1) is not None)
        board.log_work("misc", "自测日志")
        check("工作日志可用", any(w["text"] == "自测日志"
                                  for w in board.today_worklog()))
        try:
            os.remove(board._file)
        except OSError:
            pass
        idx = next((i for i, f in enumerate(fw.memory.facts)
                    if f == "自测偏好条目"), -1)
        if idx >= 0:
            fw.memory.forget_fact(idx)
    QTimer.singleShot(8900, test_agent)

    # ============================================================
    # T+9.0s 验证 RAG / 语音 / 多智能体 / API 服务
    # ============================================================
    def test_new_modules():
        # --- RAG 客户端：只验证导入和接口，不真的联网下载嵌入模型 ---
        try:
            from framework import rag_client
            check("RAG 客户端可导入", True)
            check("RAG 客户端接口完整",
                  all(hasattr(rag_client, m) for m in
                      ("add_text", "add_file", "search",
                       "format_context", "count")))
        except Exception as e:               # noqa: BLE001
            check("RAG 客户端可导入", False, str(e))

        # --- RAG 子进程脚本：验证模块存在 ---
        try:
            from framework import rag_worker
            check("RAG 子进程模块可导入", hasattr(rag_worker, "_worker_main"))
        except Exception as e:               # noqa: BLE001
            check("RAG 子进程模块可导入", False, str(e))

        # --- 语音模块：验证导入和接口 ---
        try:
            from framework.voice_input import (transcribe_file,
                                                transcribe_bytes)
            check("语音模块可导入",
                  callable(transcribe_file) and callable(transcribe_bytes))
        except Exception as e:               # noqa: BLE001
            check("语音模块可导入", False, str(e))

        # --- 多智能体：验证导入和接口 ---
        try:
            from framework.multi_agent import (MultiAgentOrchestrator,
                                                get_orchestrator)
            orch = MultiAgentOrchestrator()
            check("多智能体可导入",
                  hasattr(orch, "run") and callable(get_orchestrator))
        except Exception as e:               # noqa: BLE001
            check("多智能体可导入", False, str(e))

        # --- API 模块：验证 app 与路由 ---
        try:
            from api.server import app
            routes = {r.path for r in app.routes}
            expect = {"/chat", "/ingest/text", "/ingest/file",
                      "/search", "/voice", "/voice/chat",
                      "/agent/run", "/health"}
            missing = expect - routes
            check("API 路由完整", not missing,
                  f"缺失: {missing}" if missing else "")
        except Exception as e:               # noqa: BLE001
            check("API 路由完整", False, str(e))

        # --- API 服务是否在后台跑（只有 --api 时才有意义）---
        if "--api" in sys.argv:
            import urllib.request
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:8000/health", timeout=3) as r:
                    check("API 服务健康检查", r.status == 200)
            except Exception as e:           # noqa: BLE001
                check("API 服务健康检查", False, str(e))
    QTimer.singleShot(9000, test_new_modules)

    # T+9.5s: 汇总并退出
    def report():
        passed = sum(1 for _, ok in results if ok)
        logger.info(f"[SelfTest] 完成: {passed}/{len(results)} 通过")
        for name, ok in results:
            print(f"  {'[PASS]' if ok else '[FAIL]'} {name}")
        window.quit_app()
        sys.exit(0 if passed == len(results) else 1)

    QTimer.singleShot(9500, report)


def install_crash_guards():
    """全局异常钩子：任何未捕获异常只记日志，绝不静默闪退。"""
    import traceback
    import threading

    def _hook(exc_type, exc_value, exc_tb):
        logger.error("未捕获异常: "
                     + "".join(traceback.format_exception(
                         exc_type, exc_value, exc_tb)))

    def _thread_hook(args):
        logger.error(f"线程异常: {args.exc_type.__name__}: {args.exc_value}")

    sys.excepthook = _hook
    threading.excepthook = _thread_hook
    logger.info("[Main] 全局异常钩子已安装（闪退保护生效）")


# ============================================================
# 主入口
# ============================================================
def main():
    install_crash_guards()

    # 载入用户设置
    from framework.config import apply_user_settings
    data = apply_user_settings()
    if data:
        logger.info(f"[Main] 已载入用户设置: model={data.get('model')}")

    # 解析命令行参数
    args = sys.argv[1:]
    enable_api = "--api" in args
    enable_rag = "--with-rag" in args
    enable_selftest = "--selftest" in args

    # 后台启动 API 服务（桌面版 + API 同时跑）
    if enable_api:
        start_api_in_thread(host="127.0.0.1", port=8000)

    # 预热 RAG（首次加载嵌入模型较慢，提前加载）。
    # selftest 只验证客户端接口是否可导入，不该真的拉起子进程加载模型：
    # 否则自检 9.5 秒收尾、主进程退出时，子进程写回管道会断开并打出
    # OSError traceback，让一次全绿的自检看起来像崩了。
    if enable_rag and not enable_selftest:
        warm_up_rag()

    # 高分屏文字锐利：这两个属性必须在 QApplication 实例化【之前】设置，
    # 否则 125%/150% 缩放下字体会发虚、控件偏小（见 ui/typography.py）。
    from ui.typography import enable_high_dpi, install_app_fonts
    enable_high_dpi()

    app = QApplication(sys.argv)
    install_app_fonts(app)                 # 全应用统一界面字体栈
    app.setQuitOnLastWindowClosed(False)   # 托盘常驻，关窗不退出

    # 应用图标
    import os
    from framework.utils import resource_path
    ico = resource_path(os.path.join("assets", "icon.ico"))
    if os.path.exists(ico):
        from PyQt5.QtGui import QIcon
        app.setWindowIcon(QIcon(ico))

    window = PetWindow()
    window.show()

    if enable_selftest:
        run_selftest(window)

    logger.info("[Main] 桌面AI助手已启动"
                + ("（含 API 服务）" if enable_api else "")
                + ("（RAG 预热）" if enable_rag else ""))
    sys.exit(app.exec_())


if __name__ == "__main__":
    # 若将来引入 multiprocessing 子进程，打包成 exe 后必须先调用
    # freeze_support，否则子进程会重新启动整个程序
    import multiprocessing
    multiprocessing.freeze_support()
    main()