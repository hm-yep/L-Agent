# -*- coding: utf-8 -*-
"""项目级契约检查（CI 第一步，不联网、不调模型、不需要 API Key）。

它守的是"跨文件的一致性"，这是单元测试守不住的地方：
  * 动作池 / 必填参数 / 撤销分类 / 技能白名单 是否与契约表一致；
  * 每个动作是否都有执行分支、是否都写进了系统提示词；
  * 三套人设提示词有没有各自漂移；
  * 依赖清单有没有复发 PyTorch 这类错误包名；
  * API 层有没有把鉴权、CORS、异常回显的护栏拆掉。

用法：python tools/check_contracts.py     （失败返回非零退出码）
"""

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

RESULTS = []


def check(name: str, ok: bool, detail: str = ""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail and not ok else ""))


def const_from_source(path: Path, name: str):
    """从源码里静态取一个字符串常量的值，不 import 模块（避免拉起 Qt）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == name for t in node.targets):
            return ast.literal_eval(node.value)
    return None


DOC_NUMBER_FILES = (ROOT / "README.md", ROOT / "产品功能介绍.md",
                    ROOT / "docs" / "QUICK_START.md")


def pytest_collected():
    """真实跑一次 pytest 收集，拿「这个项目一共多少条用例」这个客观数字。

    只做收集、不执行用例，约一秒；拿不到时返回 (None, 原因) 让契约红掉，
    而不是悄悄跳过——跳过就等于文档没人管了。
    """
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q",
             "-p", "no:cacheprovider"],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300)
    except Exception as exc:
        return None, "跑不起来 pytest --collect-only：%s" % exc
    out = (proc.stdout or "") + (proc.stderr or "")
    m = re.search(r"(\d+) tests? collected", out)
    if not m:
        return None, "收集输出里没有条数（退出码 %s）：%s" % (
            proc.returncode, out.strip()[-200:])
    return int(m.group(1)), ""


def docs_with_stale_contract_count(total: int) -> list:
    """返回契约条数写得和本次真实条数不一致的文档，含 README 的目录树注释。"""
    stale = []
    expect = "%d/%d 通过" % (total, total)
    for p in DOC_NUMBER_FILES:
        text = p.read_text(encoding="utf-8")
        if expect not in text:
            stale.append(str(p.relative_to(ROOT)))
        if p.name == "README.md":
            m = re.search(r"CI 契约检查（(\d+) 项）", text)
            if not m or int(m.group(1)) != total:
                stale.append("README.md 目录树「CI 契约检查（N 项）」")
    return stale


def dispatch_names(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_execute_action")
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare) and node.comparators \
                and isinstance(node.comparators[0], ast.Constant) \
                and isinstance(node.comparators[0].value, str):
            names.add(node.comparators[0].value)
    return names


def main() -> int:
    from framework import action_registry
    from framework.prompt_blocks import PERSONA_KEYS

    print("=== 动作契约 ===")
    problems = action_registry.validate()
    check("动作池/参数/撤销分类/技能白名单一致", not problems,
          "; ".join(problems))
    check("动作分类完备（unknown 必须为 0）",
          not any("没被 effect_journal 归类" in p for p in problems))

    actions = action_registry.all_actions()
    core_path = ROOT / "framework" / "core.py"
    missing_dispatch = actions - dispatch_names(core_path)
    check(f"执行分发覆盖全部 {len(actions)} 个动作",
          not missing_dispatch, f"缺 {sorted(missing_dispatch)}")

    pool = const_from_source(ROOT / "framework" / "ai_engine.py",
                             "PROMPT_ACTION_POOL") or ""
    missing_prompt = sorted(a for a in actions if a not in pool)
    check(f"系统提示词动作池覆盖全部 {len(actions)} 个动作",
          not missing_prompt, f"缺 {missing_prompt}")

    print("=== 人设一致性 ===")
    # 比对的是**运行时真正发给模型的文本**，不是源码字面量：
    # 多智能体的提示词由共享块拼接而成，源码里当然搜不到人设关键词。
    from framework import multi_agent
    persona_texts = {
        "桌面版": const_from_source(ROOT / "framework" / "ai_engine.py",
                                  "PROMPT_IDENTITY_AND_FORMAT") or "",
        "网页 API": const_from_source(ROOT / "api" / "server.py",
                                     "SYSTEM_PROMPT") or "",
        "多智能体": "\n".join(
            getattr(multi_agent, n) for n in
            ("PLANNER_PROMPT", "EXECUTOR_PROMPT",
             "EXECUTOR_REWRITE_PROMPT", "REVIEWER_PROMPT")),
    }
    for label, text in persona_texts.items():
        missing = [k for k in PERSONA_KEYS if k not in text]
        check(f"{label}提示词含统一人设关键句", not missing,
              f"缺 {missing}")
    check("多智能体引用共享人设块",
          "PERSONA_BRIEF" in (ROOT / "framework" / "multi_agent.py")
          .read_text(encoding="utf-8"))

    print("=== 依赖与构建 ===")
    reqs = sorted(ROOT.glob("requirements*.txt"))
    check("依赖清单已拆分", len(reqs) >= 4, f"找到 {len(reqs)} 个")
    bad_pkg = []
    for f in reqs:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "-r")) and "PyTorch" in line:
                bad_pkg.append(f"{f.name}: {line}")
    check("无 PyTorch 占位包错误（正确包名是 torch）", not bad_pkg,
          "; ".join(bad_pkg))
    finetune = (ROOT / "requirements-finetune.txt").read_text(encoding="utf-8")
    check("torch 只在可选微调组里", "torch>=" in finetune)
    api_lines = [l.strip() for l in
                 (ROOT / "requirements-api.txt").read_text(encoding="utf-8")
                 .splitlines()
                 if l.strip() and not l.strip().startswith(("#", "-r"))]
    check("API 镜像不含 Qt / torch",
          not any("PyQt5" in l or "torch" in l for l in api_lines))

    print("=== API 安全护栏 ===")
    server = (ROOT / "api" / "server.py").read_text(encoding="utf-8")
    check("无通配 CORS", 'allow_origins=["*"]' not in server)
    check("全局访问依赖已挂载",
          "dependencies=[Depends(require_access)]" in server)
    check("不把异常原文回显给客户端",
          'f"处理失败: {type(e).__name__}' not in server
          and "type(e).__name__}: {e}" not in server)
    check("请求有长度/范围校验", "Field(" in server and "ge=1" in server)
    check("会话有过期与上限", "_prune_sessions" in server
          and "API_MAX_SESSIONS" in server)

    print("=== 本机安全默认值 ===")
    config_src = (ROOT / "framework" / "config.py").read_text(encoding="utf-8")
    check("应用白名单默认开启（非硬编码放行）",
          "LINGLING_ALLOW_ALL_APPS" in config_src
          and "ALLOW_ALL_APPS = True" not in config_src)
    utils_src = (ROOT / "framework" / "utils.py").read_text(encoding="utf-8")
    check("日志有脱敏与轮转",
          "redact_for_log" in utils_src and "RotatingFileHandler" in utils_src)
    guard = ROOT / "framework" / "path_guard.py"
    core_src = core_path.read_text(encoding="utf-8")
    check("读文件动作走会话授权判据（grant_service.check 三态）",
          guard.exists()
          and "grant_service.check(" in core_src
          and "verdict == \"grant\"" in core_src
          and "ask_grant(" in core_src,
          "不能再回到两态白名单，也不能绕过授权卡片直接读")

    print("=== 本机文件读取：三态判定与会话内授权 ===")
    guard_src = guard.read_text(encoding="utf-8")
    reader_src = (ROOT / "framework" / "file_reader.py").read_text(encoding="utf-8")
    grants_src = (ROOT / "framework" / "grants.py").read_text(encoding="utf-8")
    req_src = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    from framework import path_guard as pg
    check("格式判据只有一张表，能力文案是表的子集",
          pg.advertised_exts() <= pg.ALLOWED_EXTS
          and "ALLOWED_EXTS = TEXT_EXTS | DOC_EXTS | ARCHIVE_EXTS" in guard_src,
          f"文案多出的后缀：{pg.advertised_exts() - pg.ALLOWED_EXTS}")
    check("路径判定是三态且只有一个实现（老接口只做包装）",
          "def check_read(raw, grants=None)" in guard_src
          and '"grant", str(path)' in guard_src
          and "verdict, path, reason = check_read(raw, grants=None)" in guard_src)
    check("敏感判定先于白名单与授权：给了授权也读不到私钥",
          0 < guard_src.index("if is_sensitive(path)")
          < guard_src.index("for root in allowed_roots()")
          < guard_src.index("grants.covers(path)"))
    check("敏感目录名检查全部分段（含最后一段，防列目录绕过）",
          "if any(part in _DENIED_DIR_PARTS for part in parts):" in guard_src)
    check("会话内授权只有两档且不落盘",
          'GRANT_FILE = "file"' in grants_src and 'GRANT_TREE = "tree"' in grants_src
          and "open(" not in grants_src.replace(" ", "")
          and not any(m in grants_src for m in ("import json", "import pickle")),
          "授权一旦写盘就从一次性许可变成永久口子")
    check("授权包含判定按路径段，不用字符串前缀",
          'parent_key.rstrip(os.sep) + os.sep' in grants_src)
    check("读取层空手而归不算成功",
          'not res.get("text", "").strip()' in reader_src)
    check("读取层永不抛异常给动作循环",
          "except Exception as e:"
          in reader_src.split("def read(path")[1][:1200])
    check("重依赖一律在函数里懒加载（判据层不拖起 Qt）",
          not any(l.startswith(("import PySide", "import PyQt", "from pypdf",
                                "import pypdf", "from openpyxl", "import win32com"))
                  for l in (guard_src + reader_src + grants_src).splitlines()))
    check("PDF / 老格式所需依赖已在清单声明",
          "pypdf>=" in req_src and "pywin32>=" in req_src)

    print("=== 可验证执行（L1/L2）===")
    from framework import effect_spec
    spec_problems = effect_spec.validate_specs()
    check("32 个动作的副作用契约完备", not spec_problems,
          "; ".join(spec_problems[:3]))
    from framework import evolution, plan_graph, preference, trajectory
    check("轨迹/偏好/进化/计划图模块可用",
          all([hasattr(trajectory, "TrajectoryStore"),
               hasattr(preference, "PreferenceStore"),
               hasattr(evolution, "RuleStore"),
               hasattr(plan_graph, "compile_plan")]))
    core_src = core_path.read_text(encoding="utf-8")
    for token in ("_learn_preferences(text)", "self.prefs.doc_style()",
                  "rollback_to(", "_adjust_action(action)", "_traj_end()",
                  "affected_subgraph", "prefs_text"):
        check(f"主链路接入 {token}", token in core_src)
    bench_src = (ROOT / "tools" / "benchmark.py").read_text(encoding="utf-8")
    check("扰动评测含学习前后对照",
          "class LearningAdapter" in bench_src and "def compare(" in bench_src)
    check("扰动评测带 min-delta 回归卡口",
          "min-delta" in bench_src and "MIN_DELTA_PP" in bench_src)
    eval_src = (ROOT / "tools" / "eval_tasks.py").read_text(encoding="utf-8")
    check("两套评测都支持 markdown 落盘",
          "--md" in bench_src and "--md" in eval_src
          and "_write_markdown" in bench_src and "_write_markdown" in eval_src)
    ci_src = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    check("CI 落盘评测产物并带回归卡口",
          "docs/eval/benchmark.md" in ci_src
          and "docs/eval/capability.md" in ci_src
          and "--min-delta" in ci_src)
    check("趋势历史路径统一到 docs/eval/",
          "docs/eval/history.jsonl" in bench_src
          and "docs/benchmark/history.jsonl" not in bench_src)
    check("CI 上传评测 artifact 保住趋势历史",
          "actions/upload-artifact" in ci_src
          and "docs/eval/history.jsonl" in ci_src)
    trace_src = (ROOT / "tools" / "trace_view.py").read_text(encoding="utf-8")
    check("时间线渲染对撤销账本脱敏",
          "undo_ledger" in trace_src and "html.escape" in trace_src)

    print("=== 桌面 UI ===")
    ws_path = ROOT / "ui" / "workspace_window.py"
    check("可最大化工作台存在", ws_path.exists())
    ws_src = ws_path.read_text(encoding="utf-8") if ws_path.exists() else ""
    check("工作台支持最大化/还原",
          "showMaximized" in ws_src and "toggle_maximize" in ws_src)
    check("工作台记住尺寸与深浅色主题",
          "save_state" in ws_src and "_apply_theme" in ws_src)
    check("工作台八个页面读真实数据（含本机访问与内嵌网页台）",
          all(k in ws_src for k in
              ("_refresh_prefs", "_refresh_rules", "_refresh_trace",
               "_refresh_deliveries", "_refresh_chat", "_refresh_grants",
               "_refresh_web")))
    # 执行轨迹是工程面板（哈希链 / token / 失败率），不该占用户界面的导航位。
    # 它必须仍然能进，只是入口收进关于页——所以三件事一起查：导航跳过、
    # 关于页给入口、跳页不靠写死下标。
    check("执行轨迹不占导航位、入口收进关于页",
          'HIDDEN_IN_NAV = ("trace",)' in ws_src
          and "if key in HIDDEN_IN_NAV:" in ws_src
          and 'page_index("trace")' in ws_src
          and "打开执行轨迹" in ws_src)
    check("工作台跳页不写死下标（改页序不会静默开到别的页）",
          "switch_page(5)" not in ws_src
          and "def page_index(" in ws_src
          and ws_src.count("page_index(") >= 4)
    pet_src = (ROOT / "ui" / "pet_window.py").read_text(encoding="utf-8")
    check("桌宠右键/托盘/输入条三处入口已接线",
          "open_workspace" in pet_src
          and pet_src.count("workspace_requested.connect") >= 2)
    inp_src = (ROOT / "ui" / "input_dialog.py").read_text(encoding="utf-8")
    tray_src = (ROOT / "ui" / "ui_manager.py").read_text(encoding="utf-8")
    check("输入条与托盘都有工作台入口",
          "_workspace_btn" in inp_src and '"  工作台"' in inp_src
          and "打开工作台" in tray_src)
    check("截图时工作台一并隐藏", "_workspace_was_visible" in pet_src)

    # 字体系统：统一字体栈 + 高 DPI + 字号阶梯，避免宋体回退与字号过小
    typo_path = ROOT / "ui" / "typography.py"
    check("统一字体系统模块存在", typo_path.exists())
    typo_src = typo_path.read_text(encoding="utf-8") if typo_path.exists() else ""
    theme_src = (ROOT / "ui" / "theme.py").read_text(encoding="utf-8")
    main_src = (ROOT / "main.py").read_text(encoding="utf-8")
    api_service_src = (ROOT / "framework" / "api_service.py").read_text(
        encoding="utf-8")
    # 样式表搬进 ui/theme.py 之后，装字体这一步由 theme.install() 统一做，
    # 入口只负责调它——检查要跟着改口径，不然红了的是检查自己。
    check("入口装高 DPI 并走统一换肤（换肤里装全局字体）",
          "enable_high_dpi" in main_src and "theme.install(app)" in main_src
          and "install_app_fonts(app)" in theme_src
          and "AA_EnableHighDpiScaling" in typo_src)
    check("字体栈为现代无衬线且字号走阶梯",
          "Segoe UI" in typo_src and "Microsoft YaHei" in typo_src
          and "__FONT_STACK__" in theme_src
          and "font-family: __FONT_STACK__" in theme_src)

    print("=== UI 与真实能力对齐（防摆设回归） ===")
    mem_src = (ROOT / "framework" / "memory_system.py").read_text(
        encoding="utf-8")
    html_src = (ROOT / "api" / "static" / "index.html").read_text(
        encoding="utf-8")
    srv_src = (ROOT / "api" / "server.py").read_text(encoding="utf-8")
    check("工作台「新建对话」真清历史且保留长期记忆",
          "def clear_history" in mem_src
          and "memory.clear_history()" in ws_src
          and "memory.clear()" not in ws_src)
    check("更新按钮与退出菜单都有真实接线",
          "_open_repo" in ws_src and "upd_btn.clicked.connect" in ws_src
          and "quit_requested.connect(self.quit_app)" in pet_src)
    # 关于页的操作说明来自模块级 OPERATION_ROWS（唯一出处），不是 _refresh_about 里
    # 的文案——检查口径要跟着走，否则查的是已经不存在的旧结构。
    op_rows = const_from_source(ws_path, "OPERATION_ROWS") or ()
    check("操作说明只描述真实存在的控件",
          "加号按钮" not in ws_src and "分支 / 访问级别" not in "".join(op_rows)
          and any("圆形发送按钮" in line for line in op_rows)
          and "for line in OPERATION_ROWS" in ws_src)
    # 五个开关：界面 → Framework.set_capability → config 翻牌 + 落盘，一条链断在哪都算摆设
    sw_rows = const_from_source(ws_path, "SWITCH_ROWS") or ()
    cap_keys = const_from_source(ROOT / "framework" / "config.py",
                                 "CAPABILITY_KEYS") or {}
    from framework import config as cfg_mod
    check("关于页有七个可点开关且都对应真实 config 属性",
          len(sw_rows) == 7 and all(hasattr(cfg_mod, a) for _, a, _ in sw_rows),
          "行=%d" % len(sw_rows))
    check("每个开关都能落盘（在 CAPABILITY_KEYS 里）",
          all(a in cap_keys for _, a, _ in sw_rows),
          "; ".join(a for _, a, _ in sw_rows if a not in cap_keys))
    check("开关长成能按的样子而非只读标签",
          'setObjectName("switchToggle")' in ws_src
          and "btn.setCheckable(True)" in ws_src
          and 'QLabel("开启")' not in ws_src and 'QLabel("关闭")' not in ws_src
          and 'QPushButton#switchToggle:hover' in theme_src)
    check("点击真的翻开关（走 clicked 不走 toggled）",
          "btn.clicked.connect" in ws_src and "btn.toggled.connect" not in ws_src
          and "self.fw.set_capability(attr, want)" in ws_src)
    check("翻开关会建/撤子系统并落盘，失败会回滚",
          "def set_capability" in core_src and "_open_capability" in core_src
          and 'config.save_user_settings(**{key: bool(on)})' in core_src
          and "setattr(self, slot, None)" in core_src
          and "setattr(config, attr, old_flag)" in core_src)
    check("开关状态下次启动读回，且「有没有存上」可判定",
          "for attr, key in CAPABILITY_KEYS.items()" in config_src
          and "def save_user_settings(**kwargs) -> bool:" in config_src
          and "return False" in config_src.split(
              "def save_user_settings", 1)[1].split(
                  "def load_user_settings", 1)[0])

    print("=== 偏好：规则层 + 大模型层，默认不展示 ===")
    pref_src = (ROOT / "framework" / "preference.py").read_text(
        encoding="utf-8")
    check("偏好有规则层与大模型层两套入口，共用一处合并",
          "def observe(" in pref_src and "def observe_batch(" in pref_src
          and "_merge_items(" in pref_src)
    check("大模型抽取的每条偏好都要原话引用（防编造）",
          "def parse_llm_preferences" in pref_src
          and "needle not in corpus" in pref_src
          and "len(needle) < 4" in pref_src)
    check("偏好默认不在界面展示，由 PREFERENCE_PANEL 开关控制",
          "PREFERENCE_PANEL = False" in config_src
          and "PREFERENCE_PANEL" in ws_src
          and "self._prefs_body.setVisible(show)" in ws_src
          and "self._prefs_hidden.setVisible(not show)" in ws_src)
    check("偏好页来源列区分「规则命中 / 模型归纳」",
          "模型归纳" in ws_src and "规则命中" in ws_src)
    check("大模型抽取异步且单飞，没配 Key 不投递",
          "def _maybe_llm_preferences" in core_src
          and "self._pref_llm_busy" in core_src
          and '_pref_turn_seq % every' in core_src
          and 'getattr(config, "DEEPSEEK_API_KEY", "")' in core_src
          and "threading.Thread(target=_worker" in core_src)
    check("网页调试台按 /health 门禁且首页免令牌可达",
          "loadCapabilities" in html_src
          and "applyCaps" in html_src
          and "llm_key_configured" in srv_src
          and "_VOICE_AVAILABLE" in srv_src
          and 'request.scope.get("path", "") == "/"' in srv_src)

    print("=== F2/F3/F4：授权回路、跨端通道与可还原写动作 ===")
    card_src = (ROOT / "ui" / "grant_card.py").read_text(encoding="utf-8")
    plan_src = (ROOT / "ui" / "plan_card.py").read_text(encoding="utf-8")
    icons_src = (ROOT / "ui" / "icons.py").read_text(encoding="utf-8")
    svc_src = (ROOT / "framework" / "grant_service.py").read_text(encoding="utf-8")
    anchor_src = (ROOT / "framework" / "grant_anchor.py").read_text(encoding="utf-8")
    ai_src = (ROOT / "framework" / "ai_engine.py").read_text(encoding="utf-8")
    registry_src = (ROOT / "framework" / "action_registry.py").read_text(
        encoding="utf-8")
    reader_src2 = (ROOT / "framework" / "file_reader.py").read_text(encoding="utf-8")
    parser_src = (ROOT / "framework" / "action_parser.py").read_text(encoding="utf-8")
    journal_src = (ROOT / "framework" / "effect_journal.py").read_text(encoding="utf-8")
    check("授权卡三个按钮都接到 answer_grant",
          all(x in card_src for x in ("_BUTTON_ROWS", "DECISION_FILE",
                                      "DECISION_TREE", "DECISION_DENY",
                                      "btn.clicked.connect"))
          and "decided.connect(self._on_grant_decided)" in core_src
          and "self.answer_grant(gid, decision)" in core_src,
          "三个按钮必须各带后果说明，且都走 Framework 唯一入口")
    check("浮层与面板图标是线框矢量，不再用彩色 emoji",
          'fill="none" stroke="%s" stroke-width="1.8"' in icons_src
          and all(('"%s":' % n) in icons_src
                  for n in ("unlock", "timer", "clipboard", "check-circle",
                            "folder", "file-text", "trash", "x-circle"))
          and all(x in card_src for x in ("icon_label(", '"unlock"', '"timer"'))
          and all(x in plan_src for x in ("icon_label(", "setIcon(icon("))
          and "_icon_button(" in pet_src
          and 'class="ico"' in html_src
          and '<svg width="0" height="0"' in html_src
          and not any(ch in card_src for ch in ("🔓", "⏱", "🔐"))
          and not any(ch in pet_src for ch in ("🚀", "🗑", "🧹", "📂", "📄")))
    check("弹卡时暂存动作队列并立即还回控制权",
          'self._grant_queues[info["gid"]] = [action] + list(self._action_queue)'
          in core_src
          and "self._action_queue = []" in core_src
          and "self._busy = False" in core_src)
    check("一条请求一张卡，恢复队列认 gid 归属",
          "self._grant_cards = {}" in core_src
          and "self._grant_queues = {}" in core_src
          and "queue = self._grant_queues.pop(gid, None)" in core_src
          and "if queue is None:" in core_src
          and "self._grant_queue = []" not in core_src
          and "self._grant_gid" not in core_src)
    check("已答过的卡片关闭时不再补发拒绝（mark_resolved 已接线）",
          "def mark_resolved(self):" in card_src
          and "card.mark_resolved()" in core_src
          and 'if res.get("newly"):' in core_src
          and '"newly": True' in svc_src and '"newly": False' in svc_src)
    check("授权收尾槽兜住异常（槽里抛异常会 abort 整个进程）",
          "def _apply_grant_resolution(" in core_src
          and core_src.index("def _on_grant_resolved(")
          < core_src.index("def _apply_grant_resolution(")
          and "except Exception:" in core_src.split(
              "def _on_grant_resolved(")[1][:500]
          and "info.timed_out" in core_src
          and 'info.get("timed_out")' not in core_src)
    check("超时按拒且在下次响应里说明",
          "GRANT_TIMEOUT_SEC = 120" in svc_src
          and "grant.decision = DECISION_DENY" in svc_src
          and "grant.timed_out = True" in svc_src
          and "TIMEOUT_NOTE" in svc_src
          and "按拒绝处理了" in core_src
          and "_grant_notes" in srv_src)
    check("网页确认条三按钮与桌面卡一一对应",
          all(x in html_src for x in ("id=\"grantBar\"", "grantFile",
                                      "grantTree", "grantDeny"))
          and all(("answerGrant('%s')" % d) in html_src
                  for d in ("file", "tree", "deny")))
    check("/chat 与 /agent/run 都返回 pending_grant",
          "pending_grant: Optional[dict] = None" in srv_src
          and srv_src.count("pending_grant: Optional[dict] = None") >= 2
          and "pendingGrant" in html_src)
    check("/grant/{gid} 继承全局鉴权且只收三档",
          '@app.post("/grant/{gid}")' in srv_src
          and "dependencies=[Depends(require_access)]" in srv_src
          and "def require_access" in srv_src
          and "decision not in DECISIONS" in srv_src)
    check("跨端锚点只绑回环、令牌不过发现文件里的路径",
          'host: str = "127.0.0.1"' in anchor_src
          and "secrets.compare_digest" in anchor_src
          and '"token": self.token' in anchor_src
          and '"port": self.port' in anchor_src
          and 'ANCHOR_FILE = "grant_anchor.json"' in anchor_src)
    check("格式文案只有一个出处（判据表 → 提示词 / 工作台 / 文档）",
          "{READ_FORMATS}" in ai_src
          and '.replace("{READ_FORMATS}", describe_formats("、"))' in ai_src
          and "describe_formats()" in ws_src
          and "txt/md/csv" not in (ROOT / "产品功能介绍.md").read_text(
              encoding="utf-8"))
    check("工作台第 7 页本机访问存在且「全部撤销」真清空",
          "(\"grants\", \"本机访问\"" in ws_src
          and "_build_grants_page" in ws_src
          and "self.fw.revoke_grants()" in ws_src
          and "self.grant_service.revoke_all()" in core_src
          and "self.store.clear()" in svc_src)
    check("读取证据卡交代实际读了什么",
          all(x in ws_src for x in ("读取证据", "截到 %s 字", "跳过 ",
                                    "每份最多 %s 字", "批量共 %s 份")))
    check("批量读取带上下文预算与未读份数",
          all(x in reader_src2 for x in ("BATCH_MAX_FILES",
                                         "BATCH_CHARS_PER_FILE",
                                         "还剩 %d 份没读"))
          and "def _do_batch_read" in core_src
          and 'normalized["paths"] = paths[:12]' in parser_src
          and "{BATCH_MAX_FILES}" in ai_src)
    check("edit_doc 是可还原写动作：副本先落、原件只读",
          '"edit_doc":' in registry_src
          and "restorable" in registry_src
          and 'RESTORABLE = {"edit_doc"}' in journal_src
          and core_src.find("shutil.copy2(real, backup)") > 0
          and core_src.find("shutil.copy2(real, backup)")
          < core_src.find("self._send_doc_edit(instruction)")
          and 'f"{base}_修改{ext}"' in core_src)
    quick_src = (ROOT / "docs" / "QUICK_START.md").read_text(encoding="utf-8")
    check("ADR 0007/0008/0009 与 QUICK_START 13 条断言齐备",
          (ROOT / "docs" / "adr" / "0007-cross-end-shared-grant-channel.md").exists()
          and (ROOT / "docs" / "adr" / "0008-restorable-write-actions.md").exists()
          and (ROOT / "docs" / "adr"
               / "0009-qt-attribute-before-qapplication.md").exists()
          and "13 条断言" in quick_src
          and "断言 13：偏好默认不展示" in quick_src)
    check("能力评测的动作分类覆盖 restorable 新桶",
          'cov["restorable"]' in (ROOT / "tools" / "eval_tasks.py").read_text(
              encoding="utf-8"))
    check("网页台与桌面端合并：同一个 URL、同一份启动出处",
          '("web", "网页调试台"' in ws_src
          and "_build_web_page" in ws_src
          and "_try_init_web_view" in ws_src
          and "QWebEngineView" in ws_src
          and "self._web_base + \"/\"" in ws_src
          # 启动逻辑只有一个出处，命令行与内嵌页都从这里拿
          and "from framework.api_service import" in ws_src
          and "from framework.api_service import (API_BASE" in main_src
          and "start_api_in_thread" in api_service_src
          # 不是把网页另抄一份进 Qt，浏览器访问的仍是同一个路由返回的 HTML
          and 'async def index()' in srv_src
          and '@app.get("/", response_class=HTMLResponse)' in srv_src)
    check("内嵌网页台能跟着工作台换肤",
          "_sync_web_theme" in ws_src
          and "setAttribute('data-theme'" in ws_src
          and "data-theme=\"light\"" in html_src
          and "PyQtWebEngine" in (ROOT / "requirements.txt").read_text(
              encoding="utf-8"))
    # 2026-11-18 真机截图：那一页永远写着「没装 PyQtWebEngine，请 pip install」，
    # 可那台机器上包是装着的。真因是顺序——PyQt 要求 AA_ShareOpenGLContexts
    # 在 QApplication 实例化之前设好，而工作台是懒加载，import 一定在之后。
    main_lines = main_src.splitlines()

    def _first_code_line(needle):
        return next((i for i, ln in enumerate(main_lines)
                     if needle in ln and not ln.strip().startswith("#")), -1)

    # 认的是那行真的调用，不是提到过这个名字：selftest 的说明文字里也有它，
    # 只搜名字的话把代码删了契约照样绿。
    _attr_at = _first_code_line(
        "QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts")
    _app_at = _first_code_line("app = QApplication(")
    check("WebEngine 属性设在 QApplication 之前（否则网页台每次启动都降级）",
          _attr_at >= 0 and _app_at >= 0 and _attr_at < _app_at,
          "AA_ShareOpenGLContexts 第 %d 行 / QApplication 第 %d 行"
          % (_attr_at + 1, _app_at + 1))
    check("降级文案分开「真没装」与「装了但起不来」",
          "importlib.util" in ws_src
          and "find_spec(\"PyQt5.QtWebEngineWidgets\")" in ws_src
          # 喊人装包的那句只许出现在真没装那条分支里
          and ws_src.count("pip install PyQtWebEngine") == 1)
    check("内嵌浏览器创建失败不许把异常漏回 Qt 槽",
          "内嵌浏览器创建失败" in ws_src)
    check("启动冒烟里有「网页台可内嵌」这一项",
          "网页台可内嵌" in main_src
          and "QtWebEngineWidgets import QWebEngineView" in main_src)
    # 这一页栽过一次：仓库里的截图是「先 import WebEngine 再建 QApplication」
    # 抓出来的，那种顺序下缺陷不可能出现，等于没有验证。现在留一张真窗口截图，
    # 并要求它真的存在、真的有内容（不是几十 KB 的纯色图）。
    _web_shot = ROOT / "output" / "grant_ui" / "workspace_web_after_fix_screen.png"
    check("网页调试台页留了真窗口截图证据",
          _web_shot.exists() and _web_shot.stat().st_size > 50_000,
          "缺 output/grant_ui/workspace_web_after_fix_screen.png（或小于 50 KB）："
          "这一页要用真窗口里渲染出来的截图作证，不接受只跑通导入链路的图")
    check("面向用户的文档动作数统一为 32",
          all("30 个动作" not in p.read_text(encoding="utf-8")
              and "30 个工具动作" not in p.read_text(encoding="utf-8")
              for p in (ROOT / "README.md", ROOT / "产品功能介绍.md",
                        ROOT / "docs" / "架构说明.md")))

    print("=== 文档口径 ===")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    check("README 不再使用过时的 119 项数字", "119 项" not in readme)
    check("README 不再写「约 95%」自评", "约 95%" not in readme)
    # 三份文档只互相比对，挡不住「一起写错」：它们曾经同时写着 514 passed，
    # 真实早就是 525，契约照样全绿。现在把数字钉到一次真实的 pytest 收集结果上。
    collected, err = pytest_collected()
    phrases = set()
    sums = set()
    for p in DOC_NUMBER_FILES:
        m = re.search(r"(\d+) passed(?: / (\d+) skipped)?",
                      p.read_text(encoding="utf-8"))
        if m:
            phrases.add("%s passed / %s skipped" % (m.group(1), m.group(2) or 0))
            sums.add(int(m.group(1)) + int(m.group(2) or 0))
    check("三份文档的 pytest 数字写法一致", len(phrases) == 1,
          f"出现 {sorted(phrases)}")
    check("文档 pytest 数字对得上真实收集",
          collected is not None and len(sums) == 1 and sums == {collected},
          f"文档合计 {sorted(sums)} 条，真实收集 {collected} 条"
          + (f"，{err}" if err else ""))

    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    # 契约条数不进 RESULTS：否则每加一条检查都要同时把「N/N」改成 N+1，
    # 改漏一次就是文档在骗人。这里拿本次真实条数直接对三份文档。
    stale = docs_with_stale_contract_count(total)
    print("  [%s] 三份文档的契约条数写着真实条数 %d/%d（本条不计入条数）"
          % ("PASS" if not stale else "FAIL", total, total))
    print(f"\n结果: {passed}/{total} 通过")
    if passed != total:
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name}: {detail}")
        return 1
    if stale:
        print("  - 三份文档的契约条数过期：本次实际 %d 条，请把这几处改成 %d/%d 通过"
              % (total, total, total))
        for name in stale:
            print("    · %s" % name)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
