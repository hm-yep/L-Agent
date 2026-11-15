# Changelog

本文件按 Keep a Changelog 风格维护，每个版本条目按实际变更类型分段（如「新增 / 变更 / 隐私 / 验证」），只记录与当前仓库状态对应的变更。

## [2026-11-15] · 趋势跟踪真正落盘（修 history 路径 + CI 上传 artifact）

> 上轮「趋势跟踪」其实写了个寂寞：`--append-history` 写进 `docs/benchmark/history.jsonl`，但 CI 既没上传 artifact 也没 commit 回去，runner 一销毁历史就没了；且该目录本地不存在、也没进 git，路径还与其余报告的 `docs/eval/` 不一致。本轮修掉。

### 变更

- `tools/benchmark.py`：`HISTORY_PATH` 从 `docs/benchmark/history.jsonl` 统一改为 `docs/eval/history.jsonl`（与 benchmark.md / capability.md 同目录），同步更新 docstring 与 `--append-history` 帮助文本。
- `.github/workflows/tests.yml`：新增 `Upload eval artifacts` 步骤（`actions/upload-artifact@v4`），把 `history.jsonl` 及两套报告一并上传为 `eval-artifacts`，避免数据随一次性 runner 销毁。
- `docs/eval/README.md`：「趋势」一节更新路径与落盘机制说明（仓库提交种子历史作基线，趋势 = 基线 + 历次 artifact）。
- `tools/check_contracts.py`：新增 2 条契约（趋势历史路径统一到 `docs/eval/` / CI 上传 artifact 保住趋势历史），契约总数 47 → 49。

### 验证

- 实测 `--append-history` 落盘到 `docs/eval/history.jsonl`、退出码 0；pytest `test_benchmark.py` 11 passed；契约 49/49 全绿。

## [2026-11-15] · 评测深度落盘 + 进 CI 卡口（把 +25pp 变成看得见的东西）

> 本轮盘面重审发现：评测层真实存在且口径认真（扰动 + 学习前后对照），但「零落盘产物」——跑完只出 stdout，README/CI/简历三处都看不到那个最硬的数字。本次把深度落到盘上并收进 CI 回归卡口。

### 新增

- `tools/benchmark.py`：新增 `--md <path>` 落盘 markdown 报告（含学习前/后对照表、被救回 id、失败原因分类）；新增 `--min-delta <pp>` 回归卡口（默认 10pp，防未来改动把「进化」能力改坏——之前只有 `delta_pp > 0` 的弱卡口）。
- `tools/eval_tasks.py`：新增 `--md <path>` 落盘 markdown 报告（按组通过率、失败原因分类、结论）。
- `docs/eval/`：两套评测的 markdown 报告落盘位置（`benchmark.md` / `capability.md`），README 首屏引用。

### 变更

- `README.md`：首屏新增「评测：扰动下把事办成」区块，贴学习前/后对照表（56.25% → 81.25%，+25.00pp，8 条救回 / 6 条硬失败），并明确口径（离线确定性链路，真实大模型评测属下一阶段）。
- `.github/workflows/tests.yml`：两套评测加 `--json --md` 落盘；benchmark 加 `--min-delta 10` 回归卡口。
- `tools/check_contracts.py`：新增 3 条契约（min-delta 卡口存在 / 两套评测支持 markdown 落盘 / CI 落盘且带回归卡口），契约总数 44 → 47。

### 验证

- 实测 `--min-delta` 卡口：过高阈值退出码 1、正常阈值退出码 0；markdown 报告成功落盘；pytest 408 passed / 1 skipped、契约 47/47、集成自检 157/157 全绿。

## [2026-11-15] · 对话页改为自绘圆角气泡（抛弃 QTextBrowser）

> 本日第十版：用户明确「不要有棱角的框」。Qt 富文本（QTextBrowser）无论如何都做不了圆角（不支持 border-radius），上一版的 `<table>` 方案只能做出直角块。故将对话页整体从富文本改造为 `QScrollArea` + 自绘 `QLabel` 气泡，用 QSS 的 `border-radius` 实现真正的圆角。

### 变更

- `ui/workspace_window.py`：
  - 对话页 `_build_chat_page` 从 `QTextBrowser` 改为 `QScrollArea#chatScroll` + 垂直 `QVBoxLayout` 容器（`chatList`），每条消息一个 `QLabel` 气泡；
  - 新增 `_add_bubble` / `_scroll_chat_to_bottom`：用户消息右对齐蓝底白字（`bubbleMe`）、助手消息左对齐深灰底亮字（`bubbleAi`），均 `border-radius:14px` + 内边距 + 自动换行 + 可鼠标选中文本，最大宽 680px 自适应；
  - 移除 `QTextCursor` 导入（不再需要滚动光标）。
- `tests/test_workspace_window.py`：`test_chat_page_shows_real_history_and_escapes_html` 从检查 `toHtml()` 改为遍历气泡 QLabel 文本（QLabel 天然不执行 script，安全语义不变）。

### 验证

- 离屏渲染确认气泡四角圆润、无棱角；pytest 408 passed / 1 skipped、契约 44/44、集成自检 157/157 全绿。

## [2026-11-15] · 对话气泡改用 table 渲染，修复「窄条高亮」观感

> 本日第九版：用户反馈「字体搞得丑」。根因不是字号，而是气泡用了 Qt 富文本不支持的 `display:inline-block` + `border-radius` + `max-width`，被 Qt 静默丢弃，气泡退化成「紧贴文字的直角窄条」，看起来像选中高亮而不是气泡。

### 变更

- `ui/workspace_window.py` `_refresh_chat`：气泡由 `<span style="display:inline-block">` 改为 `<table align="left|right" cellpadding="10" bgcolor=...>`。Qt 富文本完整支持 table 的 `bgcolor`/`cellpadding`/`align`，能渲染出「宽度随内容、四周有内边距」的真气泡块；用户气泡右对齐蓝底白字、助手气泡左对齐深灰底亮字，字号 15px。

### 验证

- 离屏捕获原始 HTML 断言 9 项（对齐/底色/字色/内边距/字号/无撑满）全过；pytest 408 passed / 1 skipped、契约 44/44、集成自检 157/157 全绿。

## [2026-11-15] · 对话区文字与桌宠比例修正

> 本日第八版：用户反馈「右边字体难看、人物太大」。上一版字号收敛到 14px 后，对话气泡文字反而显得又小又淡、助手气泡底色与背景几乎同色导致气泡感消失；桌宠角色也被放大到 185px 偏离原始比例。

### 变更

- `ui/workspace_window.py`：
  - 对话气泡正文显式设为 15px + 行高 1.55，比通用正文大一档，阅读更舒适；
  - 深色主题配色微调：正文文字 `#E6E8EB → #ECEEF1`（更亮）、次要文字 `#8B9099 → #9AA1AB`、助手气泡底色 `#2A2C33 → #2E323A`（与背景拉开对比，气泡感恢复）；
  - 页面顶部提示语 `hintLabel` 从 13px 灰改为主文字色 + 正文档（14px），不再局促；
  - 空状态提示色由 `#8899aa` 调亮为 `#aab4bf`。
- `framework/config.py`：桌宠尺寸 `PET_SIZE` 185 → 160，恢复原始比例（注释原就记录「由 160 放大到 185」）。

### 验证

- pytest 408 passed / 1 skipped、契约 44/44、集成自检 157/157 全绿。

## [2026-11-15] · 工作台视觉对标 WorkBuddy 参考截图

> 本日第七版：用户提供 WorkBuddy 截图要求「对标这个设计，字体大小也参考这个」。据此把上一版偏大的字号按截图实测值调回精致尺度，并把底部命令栏重做成截图的两层结构。

### 变更

- `ui/typography.py`：字号阶梯按 WorkBuddy 实测重新锚定——正文 15→14、导航 14→13、页标题 21→17、品牌 24→15、辅助 13→12 / 角标 12→11，整体收敛到 11~17px；层级靠字重与颜色区分，不再靠放大。字体栈与高 DPI 保持不变（清晰度已由上一版解决，不需要靠加大字号）。
- `ui/workspace_window.py`：
  - 配色改为中性冷灰（背景 #18191C / 导航 #131417 / 选中态低饱和），去掉上一版偏亮的绿蓝大色块，贴近截图的克制风格。
  - 底部命令栏从单行平铺改为截图的两层结构：上方一个大圆角输入框（占位文案「今天帮你做些什么？ @ 添加上下文，/ 调用技能与指令」），下方一排小控件（+ / main 分支 / 允许完全访问 / 模型 / 麦克风 / 右下角圆形↑发送按钮）。
  - 导航项、品牌、表格、状态栏、提示文字字号统一下调，强调色收敛到一处。
- `ui/icons.py`：新增 git-branch / mic / arrow-up / chevron-down / sparkle 五个矢量图标，供命令栏使用。
- `tests/test_workspace_window.py`：字号测试从「越大越好」口径改为「对齐参考图」口径，断言正文 14 / 标题 17 / 品牌 15 / 导航 13，且最大字号不超过 17。

### 验证

- `pytest -q`：408 passed / 1 skipped；`tools/check_contracts.py`：44/44；`tools/test_agent_features.py`：157/157；评测集 38/38。
- 离屏渲染核查：两层命令栏结构、控件排布、圆形发送按钮位置均正常，无重叠 / 错位 / 图标缺失。

## [2026-11-15] · 统一字体系统：字体更大更清晰

> 本日第六版：按「字体太小看不清、视觉效果差」的反馈，把字体从散落设置改成一套统一的现代界面字体系统。

### 变更

- 新增 `ui/typography.py`：集中定义现代无衬线字体栈（西文 Segoe UI → 中文微软雅黑 UI → 等线 / 思源黑体 / 苹方兜底）、8 级字号阶梯、字重规范与等宽栈，并提供 `enable_high_dpi()` / `install_app_fonts()` / `apply_qss_tokens()`。
- `main.py`：QApplication 创建前开高 DPI 缩放与高 DPI 图标（解决 125%/150% 屏字体发虚），创建后装应用级统一字体，覆盖所有未显式设字体的窗口。
- `ui/workspace_window.py`：QSS 顶部加全局 `QWidget { font-family: ... }` 规则，几十个控件不再回退到宋体；字号全部改走阶梯占位符并整体调大（正文 14→15、命令栏→16、页标题 17→21、品牌 19→24），字重分层（标题 600 / 正文 400）。
- 移除 Qt 样式表不支持的 `line-height` / `letter-spacing`，避免无意义解析警告。

### 验证

- `pytest -q`：408 passed / 1 skipped；`tools/check_contracts.py`：44/44；`tools/test_agent_features.py`：157/157。
- 新增 4 项字体测试 + 3 项契约：断言统一字体栈已注入、无写死字号、字号阶梯可读、高 DPI 与全局字体生效。

## [2026-11-15] · 界面图标改为矢量线框

> 本日第五版：按「廉价图标不如没有」的反馈，把界面里靠系统 emoji 字符充当图标的地方全部替换掉。

### 变更

- 新增 `ui/icons.py`：程序化绘制的 Feather 风格 24×24 矢量线框图标集，`QSvgRenderer` + 2× 超采样渲染，随主题着色并按 (名称, 颜色, 尺寸) 缓存；不依赖系统 emoji 字体。
- `ui/workspace_window.py`：左侧导航（对话 / 偏好 / 规则 / 轨迹 / 交付 / 关于）、新建对话、标题栏、底部命令栏、各页操作按钮全部改用矢量图标，主题切换时统一重绘；移除 `💬⭐🧠🛰📦⚙⛶◐☀🔍🔔⟳＋▲` 等字符图标。
- `ui/input_dialog.py` / `ui/pet_window.py` / `ui/ui_manager.py`：工作台入口、右键菜单、设置标题去掉 emoji，改用矢量图标或纯文字（菜单里没有好图标时，纯文字优于廉价图标）。
- `tests/test_workspace_window.py` / `tools/check_contracts.py`：把原先断言 emoji 字符的检查改成断言矢量图标 / 干净文案，契约总数不变。

### 验证

- `pytest -q`：404 passed / 1 skipped；`tools/check_contracts.py`：41/41；`tools/test_agent_features.py`：157/157。
- 离屏渲染工作台截图人工核对：图标为统一细线风格，无缺失、无方块乱码、无错位。

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
