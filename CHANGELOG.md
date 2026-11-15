# Changelog

本文件按 Keep a Changelog 风格维护，每个版本条目按实际变更类型分段（如「新增 / 变更 / 隐私 / 验证」），只记录与当前仓库状态对应的变更。

## [2026-11-15] · 工作台界面对标 WorkBuddy 风格

> 本日第四版：把上一版的标准主窗口重做成 WorkBuddy 的深色界面格局，外观与交互更贴近主流桌面 AI 客户端。

### 变更

- `ui/workspace_window.py` 重写为 WorkBuddy 深色风格：顶部菜单栏（文件 / 编辑 / 窗口 / 帮助 / 关于）、左侧品牌导航边栏（品牌 + 版本号 + 分组计数 + 底部更新区）、主区顶部标题栏（页面标题 + 右侧图标）、主区底部全局命令栏（「今天帮你做些什么？」+ @ 上下文 / 分支 / 访问级别 / 模型 / 发送）；**默认深色主题**。
- 对话页去掉内部输入框，统一由底部全局命令栏发指令；菜单栏「退出程序」发射 `quit_requested`、「打开使用文档」打开 `docs/QUICK_START.md`、「关于」跳到关于页。
- 左侧导航分组随真实数据刷新（待办 / 交付 / 偏好 / 规则计数）；「◐」按钮在深色与浅色间切换。

### 验证

- `pytest -q`：404 passed / 1 skipped（新增 3 项 UI 测试：顶部菜单栏、全局命令栏、导航分组）。
- `tests/test_workspace_window.py`：20 passed；`tools/check_contracts.py`：41/41；`main.py --selftest`：35/35；`tools/test_agent_features.py`：157/157。

## [2026-11-15] · 可最大化的工作台窗口

> 本日第三版：补齐桌面形态的主窗口。上一版条目的测试数字为当时实测，本版数字见下方「验证」。

### 新增

- `ui/workspace_window.py`：标准 `QMainWindow` 工作台，六个页面（对话 / 我的偏好 / 学到的规则 / 执行轨迹 / 我的交付 / 关于与设置），数据全部读真实 Store。
- 最大化能力：系统标题栏 + 双击标题栏 + 工具栏「⛶ 最大化 / 还原」；首次打开默认最大化。
- 状态记忆：窗口尺寸、最大化状态、深浅色主题写入本机 `workspace.ini`；「◐ 深色」一键切换。
- 关闭语义：点 X / Esc 只隐藏窗口并发出 `workspace_closed`，程序继续留在托盘；再次点开立刻恢复。
- 「执行轨迹」页可一键调用 `tools/trace_view.py` 生成并打开单文件时间线 HTML。
- `tests/test_workspace_window.py`：17 项离屏 Qt 测试（最大化语义、六页真实数据、HTML 转义、状态持久化、关闭不退出、三处入口接线）。

### 变更

- `ui/pet_window.py`：右键菜单、托盘菜单、输入条三处新增「打开工作台」入口；截图前后一并隐藏/恢复工作台；退出时保存窗口状态并断开信号。
- `ui/input_dialog.py` / `ui/ui_manager.py`：新增 `workspace_requested` 信号与对应按钮/菜单项。
- `tools/check_contracts.py`：新增 7 项桌面 UI 接线校验（工作台存在、最大化/还原、状态记忆、六页数据、三处入口、截图联动）。

### 验证

- `pytest -q`：404 passed / 1 skipped。
- `python tools/check_contracts.py`：41/41 通过。
- `main.py --selftest`：35/35；`tools/test_agent_features.py`：157/157；`tools/eval_tasks.py`：38/38；`tools/benchmark.py`：+25.00pp。

## [2026-11-15] · 可验证执行与自适应

> 本日第二版：在第一版依赖拆分与口径修正之上，补齐「可验证执行 + 偏好学习 + 自我进化」。设计取舍与代价见 [ADR 0003](docs/adr/0003-preference-and-evolution.md)、[ADR 0004](docs/adr/0004-verifiable-execution.md)。

### 新增

- `framework/trajectory.py`：append-only 事件流 `data/trajectory.jsonl`，`KINDS` 十类事件、`TrajectoryStore`（`start_turn` / `append` / `end_turn` / `events` / `turns` / `rollback_ledgers` / `chain_ok` / `summarize` / `clear`）、`world_digest()` 五面状态指纹、`sanitize()` 脱敏；`pre_hash` / `post_hash` / `parent_id` 串成链，`chain_ok()` 校验前后哈希接得上。
- `framework/preference.py`：纯规则 `extract_preferences(text)` 从自然对话抽 6 类偏好（`doc_theme` / `verbosity` / `tone` / `web_search` / `language` / `reminder_style`），`PreferenceStore` 落盘 `preferences.json`（`evidence` 只存原话哈希、置信度分层、`hits` 与 `supersedes` 历史），`as_prompt()` / `doc_style()` / `verbosity()` / `allow_web_search()` / `forget()` / `clear()` 驱动行为。
- `framework/evolution.py`：从 `failure` / `quality` / `undo` 事件归纳 5 个规则族（`search_too_broad` / `doc_too_thin` / `path_unreadable` / `user_undid` / `action_timeout`），2 条独立事件才生成、`learn` 幂等、置信度取 Beta(1,1) 后验均值 `(1+s)/(2+s+f)`、`failures>=3 且 successes==0` 自动停用、`MAX_RULES` 上限；`RuleStore.apply(context)` 返回换词 / 跳过 / 确认 / 重试预算等调整项。
- `framework/effect_spec.py`：30 个动作的显式副作用契约（`observables` / `reversible` / `idempotent` / `inverse` / `pre` / `post` / `reason`）、`validate_specs()` 与 `action_registry` / `effect_journal` / `REQUIRED_PARAMS` 交叉校验、`check_pre` / `check_post` 运行时断言、`involution_check()` 用固定种子随机序列与纯假手证明「撤销是对合」。
- `framework/plan_graph.py`：线性计划编译成 DAG（默认顺序依赖 + 「基于 / 据此 / 汇总」数据依赖）、`validate()` 静态校验、`topological_order()` 稳定 Kahn、`affected_subgraph()` 只返回失败节点及其传递后继。
- `tools/benchmark.py`：32 条任务 / 10 类扰动、固定种子、不联网不调模型，`LearningAdapter` 做学习前后两态对照；支持 `--json` / `--append-history` / `--threshold` / `--seed`。
- `tools/trace_view.py`：事件流渲染成单文件可交互时间线 HTML（按轮分组、哈希链、失败高亮、只看失败过滤、深浅色自适应、HTML 转义、无 CDN）。
- `tests/test_adaptive_integration.py`：端到端接线测试 8 项。
- 配置开关：`TRAJECTORY_ENABLED` / `PREFERENCE_LEARNING` / `PREFERENCE_NOTIFY` / `EVOLUTION_ENABLED` / `PLAN_DAG_ENABLED` / `EFFECT_SPEC_STRICT`（默认 False，只记录不阻断）/ `MAX_PREFERENCES=40` / `MAX_RULES=24`。
- 文档：新增 [ADR 0003](docs/adr/0003-preference-and-evolution.md)、[ADR 0004](docs/adr/0004-verifiable-execution.md)；`README.md` 补「深度：从工具编排到可验证执行」三层章节与自验证命令；`docs/QUICK_START.md` 从 5 条断言扩到 7 条；[产品功能介绍.md](产品功能介绍.md) 补自适应章节；本文件新增本条目。

### 变更

- `framework/core.py` 接线：`_begin_turn` 记 `pre_hash`、`_review_effects` 用 try/finally 收轮写 `post_hash` 与撤销账本、`_run_next_action` 每个动作写 `action` 事件；`send_command` 自动调 `_learn_preferences(text)`，用户不必说「记住」；`ai_engine.AIWorker` 用 `prefs_text` 动态块把偏好追加到系统提示词之后（`SYSTEM_PROMPT` 常量逐字不变）。
- 偏好与规则在执行前生效：`_adjust_action` 应用 `web_search` 开关与学到的规则（换词 / 跳过 / 确认），并跑 `check_pre`；`_do_create_doc` 把 `prefs.doc_style()` 作为默认样式（当次显式 `style` 优先），`min_slides` 抬高文档质量门门槛；`_do_show_plan` 写 `plan` 事件，`_plan_replan` 用 `affected_subgraph()` 只调整受影响子图。
- 失败与收口事件：`_note_plan_failure` / `_handle_search_quality` / `_doc_quality_followup` / `_do_undo_last` / `_on_ai_failed` 分别写 `failure` / `quality` / `undo` 事件，`_traj_end` 顺带触发规则归纳。
- 撤销升级为任意轮回滚：`rollback_to(turn_id)` 从 `turn_end` 事件逆序取回撤销账本，逐轮执行 `effect_journal.apply_ops`；`undo_last` 动作支持 `to` 参数。
- 所有新能力带 try/except 降级，任一模块异常不影响原有动作链；`tools/check_contracts.py` 新增「可验证执行（L1/L2）」检查组，契约检查现为 34 项。
- 测试与评测表格按实测更新：`pytest -q` 384 passed / 1 skipped、契约检查 34/34、能力评测集 38/38、启动冒烟 35/35、集成自检 157 通过 / 0 失败，并新增扰动评测一行。

### 隐私

- 新增三个落盘文件 `data/trajectory.jsonl`、`data/preferences.json`、`data/rules.json`，均随 `data/` 由 `.gitignore` 排除。
- 用户原话不写进这三个文件：轨迹走 `sanitize()`（丢敏感键、路径只留 basename、超长截断），偏好证据只存 `text_digest`（原话哈希）与命中短语，`as_prompt()` 只输出标签化描述；时间线对撤销账本只显示数量、不显示明细。
- 测试隔离：`LINGLING_DATA_DIR` 可覆盖 `framework.utils.app_data_dir()`；`tests/conftest.py`、`tools/test_agent_features.py` 与 CI 都设了它，CI 用 `${{ runner.temp }}/lingling-data`，测试产物不写进真实用户 `data/`。

### 验证

- `pytest -q`：384 passed / 1 skipped（含 `tests/test_adaptive_integration.py` 的 8 项端到端接线测试）。
- `python tools/check_contracts.py`：34/34 通过。
- `python tools/benchmark.py`：学习前 56.25%（18/32）→ 学习后 81.25%（26/32），提升 25.00 个百分点，8 条任务被救回（C19~C26），仍如实保留 6 条硬失败；阈值 0.80、退出码 0（放行）。
- `python tools/test_agent_features.py`：157 通过 / 0 失败。
- `python tools/eval_tasks.py`：38/38 通过。
- `python main.py --selftest`：35/35 通过（离屏环境跳过 2 项依赖真实屏幕几何的布局断言）。

## [2026-11-15]

### 新增

- 依赖拆分：新增 `requirements-api.txt`（仅 API）、`requirements-rag.txt`、`requirements-voice.txt`、`requirements-finetune.txt`、`requirements-dev.txt`；桌面核心仍用 `requirements.txt`。
- `framework/action_registry.py`：动作契约单一真源，集中登记 30 个动作的名称、参数与可逆性分类。
- `tools/check_contracts.py`：CI 契约检查，把动作契约与撤销分类覆盖（未知项必须为 0）钉成可失败的检查项。
- `tools/eval_tasks.py`：端到端能力评测集。
- 文档：`README.md` 重写；[产品功能介绍.md](产品功能介绍.md) 口径修正；`PROGRESS.md` 全文归档到 `docs/archive/PROGRESS-2026-09-to-11.md`；新增 `CHANGELOG.md`、`docs/adr/0001-undo-by-world-diff.md`、`docs/adr/0002-skill-replay-zero-llm.md`、`docs/架构说明.md`、`docs/QUICK_START.md`。
- CI 全量回归：pytest 单测 + 契约检查 + 157 项集成自检 + 评测集。

### 修复

- 修正 finetune 相关依赖中 PyTorch 的包名，避免安装阶段解析失败。
- CI 改为按真实依赖安装并跑全量回归，不再只安装测试收集期的轻依赖。
- 状态复位统一：新输入打断在途请求时，集中复位推进标记与在途状态，避免计划停在半途。
- API 输入校验：请求体非空校验、上传文件名路径归一校验。

### 变更

- 项目名统一为「凌零 · 桌面 AI 数字员工」，废弃「皮皮」「桌宠」「桌面AI伙伴」等旧称谓。
- 能力边界如实标注：`framework/multi_agent.py` 的 Planner→Executor→Reviewer 仅 API `/agent/run` 可用、`framework/core.py` 零引用；RAG（LangChain + Chroma + BGE）仅 API 侧且 `use_rag` 默认 False；Whisper 仅网页调试台 `/voice`、`/voice/chat`；`finetune/` 的 LoRA 为 31 条样本的独立训练脚本，未接入产品推理链路。
- 测试口径区分：`pytest -q` 单测 / `python tools/test_agent_features.py` 157 通过 0 失败（需手动运行，不在 pytest 收集范围）/ `python main.py --selftest` 启动冒烟（35/35，离屏环境跳过 2 项依赖真实屏幕几何的布局断言，退出码不再恒为 1），不再混称「测试套件」；pytest 汇总数字见本文件最新条目。
- Docker 镜像明确只跑 API 服务（`uvicorn api.server:app`），不运行桌面 UI。
- [产品功能介绍.md](产品功能介绍.md) 第十节自评表权重合计修正为 100%，加权分按表实算为 88.68%（约 89%），并写明算法。

### 安全

- API 鉴权：访问令牌从 `LINGLING_API_TOKEN` 读取，未配置时只接受本机回环请求；CORS 白名单由 `LINGLING_API_ORIGINS` 配置。
- `framework/path_guard.py`：本机文件读取白名单，限制可读路径范围。
- 日志脱敏：运行日志不落明文密钥。
- Key 不入库：`DEEPSEEK_API_KEY` 仅从环境变量或设置界面读取；`data/` 运行时产物继续由 `.gitignore` 排除。
