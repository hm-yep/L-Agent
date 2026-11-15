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
import re
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
    check("read_doc / analyze_data 走文件白名单",
          guard.exists() and core_src.count("validate_read_path(") >= 2)

    print("=== 可验证执行（L1/L2）===")
    from framework import effect_spec
    spec_problems = effect_spec.validate_specs()
    check("30 个动作的副作用契约完备", not spec_problems,
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
    check("工作台六个页面读真实数据",
          all(k in ws_src for k in
              ("_refresh_prefs", "_refresh_rules", "_refresh_trace",
               "_refresh_deliveries", "_refresh_chat")))
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
    main_src = (ROOT / "main.py").read_text(encoding="utf-8")
    check("入口安装全局字体与高 DPI",
          "install_app_fonts" in main_src and "enable_high_dpi" in main_src
          and "AA_EnableHighDpiScaling" in typo_src)
    check("字体栈为现代无衬线且字号走阶梯",
          "Segoe UI" in typo_src and "Microsoft YaHei" in typo_src
          and "__FONT_STACK__" in ws_src
          and "font-family: __FONT_STACK__" in ws_src)

    print("=== 文档口径 ===")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    check("README 不再使用过时的 119 项数字", "119 项" not in readme)
    check("README 不再写「约 95%」自评", "约 95%" not in readme)
    # 三份面向不同读者的文档最容易各改一半，这里强制它们的测试数字一致
    doc_numbers = set()
    for p in (ROOT / "README.md", ROOT / "产品功能介绍.md",
              ROOT / "docs" / "QUICK_START.md"):
        m = re.search(r"(\d+) passed", p.read_text(encoding="utf-8"))
        if m:
            doc_numbers.add(m.group(1))
    check("三份文档的 pytest 数字一致", len(doc_numbers) == 1,
          f"出现 {sorted(doc_numbers)}")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n结果: {passed}/{total} 通过")
    if passed != total:
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
