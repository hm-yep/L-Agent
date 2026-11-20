# Changelog

本文件按 Keep a Changelog 风格维护，每个版本条目按实际变更类型分段（如「新增 / 变更 / 隐私 / 验证」），只记录与当前仓库状态对应的变更。

## [2026-11-20] · 工作台底栏收敛：网页台只留一个输入框

> 用户指出两处：「网页调试台」那一页有两个输入框（网页自己那条 + 工作台底部命令栏），同一屏不知道该往哪打字；窗口最底部常驻的七个指标芯片与模型状态文字要求删掉。

### 变更

- `ui/workspace_window.py`：
  - 命令栏按页 key 判显隐——「网页调试台」页收起（`switch_page()` 里 `PAGE_DEFS[index][0] != "web"`），别的页照旧。走 key 不走页序号：页序号写死过一次就会悄悄点到别人的页。删的是工作台那条重复的输入框，不动网页里的——这一页的职责就是浏览器那份网页的镜像，网页调试台没了输入框就不叫调试台。
  - 底部状态栏删掉七个指标芯片（轮次 / 事件 / 失败率 / token / 偏好 / 生效规则 / 哈希链）与模型状态文字，只留右侧那一句操作反馈。这条反馈是交互闭环的一部分（开关翻成功、授权卡收回、交付登记完都要在这儿说一声），不能一起删。
  - `_status_full` 照旧算同一份内容，只是不再常驻显示；菜单「复制状态栏文本」改名「复制运行状态摘要」，要发出去时复制一份即可。
  - 删除 `_set_chip` 与 `_chips`（已无控件可更新）。
- `ui/theme.py`：删掉已无人使用的 `QLabel#statChip` 三条样式；`switchToggle`（关于页真按钮）样式不受影响。
- `docs/adr/0011-workbench-bottom-bar.md`：新增这条决策的记录；ADR 0005 补记 `statChip` 样式已随之撤掉。

### 验证

- `tests/test_workspace_window.py` 新增 2 项：`test_web_page_shows_only_the_web_input`（网页台页命令栏真的隐藏、网页 HTML 里确实有输入框、切回对话页命令栏回来）、`test_status_bar_keeps_only_feedback`（窗口里没有 statChip、状态栏里没有那七个数字与模型状态、复制摘要的内容还在）。
- `pytest -q`：609 passed / 1 skipped（共收集 610）；`python tools/check_contracts.py`：111/111；`docs/QUICK_START.md` 断言 8 第 3 步补上「这一页底下不再有工作台命令栏」。文档里的 pytest 数字同步到 609/610（三份文档口径由契约检查对着真实收集结果钉死）。

## [2026-11-20] · LoRA 真训一次：从「接线完成」到「训练集内 96.8%」

> 用户一句话：「你帮我把这个 Lora 模块和我的这个现在的模块连接起来。」
> 查下来接线确实做完了（provider 路由、设置界面、格式单一真源都在），
> 但 `finetune/output` 里没有任何权重——那条链路从没跑过一次真实推理。

### 新增

- **`tools/eval_lora.py`**：真实权重上的质量评测，走主链路的判定函数
  （`AIWorker._request_lora`），不另写一套标准。输出可用率 / 动作全对 / 参数全对 /
  平均耗时，支持 `--data` 换评测集与 `--json` 落盘。
- **`finetune/data/holdout.jsonl`**：10 条**未参与训练**的同义改写指令，
  用来量泛化缺口（训练集内 96.8% vs holdout 60%，两个数字必须并列看）。

### 变更

- **`finetune/train_lora.py`**：适配 transformers 5.x（`warmup_ratio` → `warmup_steps`、
  `Trainer(tokenizer=)` → `processing_class`、`torch_dtype` → `dtype`）。三处任一没改，
  脚本一启动就 TypeError——原清单锁的是 4.x，与实际环境 5.19 不符。
- **`finetune/config.yaml`**：训练步数 6 → 32 → **160**（batch 2 × 累积 4 × 40 轮），
  `max_length` 512 → 192（实测样本 max 160 token）。32 步时 trainer loss 2.10、
  动作全对 12.9%；160 步时 loss 0.60、动作全对 96.8%。
- **`framework/lora_format.py`**：取消「用户问：/用户说：」的猜测，前缀恒定。
  核对标注后发现规则把 4 条猜反（带问号却标「说」的句子推理端会发成「问」），
  等于又一次「训的是 A、跑的是 B」。
- **`finetune/inference.py`**：默认改贪心解码，与产品侧一致（原来是 temperature=0.7
  采样，命令行调提示词会被误导）；`--temperature` 显式开启；加载走缓存优先逻辑。
- **`requirements-finetune.txt`**：版本口径对齐实际验证组合
  （torch 2.7 / transformers 5.19 / peft 0.21 / accelerate 1.15）。

### 修复

- **缓存优先加载**：新增 `lora_engine.from_pretrained_cached()`，先
  `local_files_only=True` 再联网。本机 huggingface.co 被代理拦（502），
  `from_pretrained` 会连「权重已在本地缓存」都加载失败。

### 验证

| 口径 | 可用 | 动作全对 | 参数全对 | 平均耗时 |
|---|---|---|---|---|
| 训练集 31 条（32 步旧配置） | 38.7% | 12.9% | 12.9% | 10.90 s |
| 训练集 31 条（160 步） | 100% | 96.8% | 96.8% | 2.12 s |
| holdout 10 条（未参与训练） | 90% | 60.0% | 50.0% | 3.80 s |

- 训练：RTX 5070 Laptop，160 步 155 秒，适配器 8.7 MB（可训练参数 0.141%）。
- 单测新增 3 项（缓存优先的调用顺序、训练脚本必须走同一份加载逻辑），
  本地微调相关用例 25 → 28；文档 pytest 数字同步 580 passed / 1 skipped。

## [2026-11-19] · LoRA 微调接进主链路：本地模型成了第一个 provider

> 用户一句话：「你把它这个 LoRA 未接入的问题解决一下。」查下来 README 的能力对照表里那行
> 「LoRA 微调 | 独立训练脚本 | 未接入产品推理链路」是实话：`finetune/` 训得出权重，
> 但产品侧没有任何一条代码路径能用上它。

### 新增

- **`framework/lora_format.py`**：LoRA 提示词格式的**单一真源**。训练脚本
  （`finetune/train_lora.py`）、命令行推理（`finetune/inference.py`）、产品推理
  （`framework/lora_engine.py`）全部从这一个模块取模板，模板字面量只允许出现在这一个文件里。
  微调最常见的失败是「训的是 A、跑的是 B」，两处各写一遍格式迟早漂，而且不会报错。
- **`framework/lora_engine.py`**：本地运行时（基座 + PEFT 适配器）。懒加载、线程安全、
  可用性如实报告（缺依赖 / 缺适配器 / 就绪三档），适配器判据是目录里有 `adapter_config.json`。
- **`framework/ai_engine.py` 的 provider 路由**：`_request_model()` 的顺序是
  **本地微调 → 云端模型 → 离线规则兜底**。本地返回的仍是同一套动作列表，
  下游（动作解析 / 计划确认 / 撤销账本 / 质量门）一行都没改。
- **设置界面「本地微调模型（LoRA，选填）」分组**：开关、基座模型、适配器路径、设备四栏，
  外加「测试加载」按钮（真加载一次，把结果说清楚）；配置写进本机 `user_settings.json`。
- **`docs/adr/0010-lora-as-main-chain-provider.md`**：为什么接在 `ai_engine` 而不是
  `core`，以及能力边界写在明处的三条。
- **`tools/verify_lora_runtime.py`**（手动运行的真机验证脚本）：不联网、不下大模型——
  现场造一个**极小的随机基座** + **真 PEFT 适配器**（产出真实 `adapter_config.json`），
  走完「加载 → 真续写 → 主链路协议校验」。本机实测：适配器能加载、GPU 上真续写、
  主链路认它的输出（随机权重吐的是乱码，被判成人话包成 `show_bubble`）。

### 变更

- **`framework/config.py`**：新增 `LORA_ENABLED` / `LORA_BASE_MODEL` / `LORA_ADAPTER_PATH` /
  `LORA_DEVICE` / `LORA_MAX_NEW_TOKENS` / `LORA_TEMPERATURE` / `LORA_INFERENCE_TIMEOUT`，
  默认**关闭**（产品行为不该默认变），落盘键 `lora_enabled` 等四个。
- **`framework/core.py`**：`set_lora_config()`（改配置 + 落盘 + 关开关时释放权重）与
  `lora_status_text()`。
- **`finetune/train_lora.py`**：样本文本改走共享模板；**修掉一个真缺陷**——原来
  `enc["labels"] = enc["input_ids"].copy()` 让提示部分也参与 loss，等于让模型连提示一起背，
  而推理时只喂提示；现在提示段掩成 `-100`，只有回答部分参与训练。
- **`finetune/inference.py`**：同样走共享模板；修掉「decode 时把提示也带出来」的旧毛病
  （只 decode 新生成的那段）。
- **`.gitignore`**：新增 `/finetune/output/`——适配器权重是几十上百 MB 的二进制，不进仓库；
  训练数据仍入库以便复现。

### 边界（如实，不夸大）

- 本地通路**只吃当前这一句用户输入**：长期记忆 / 检索资料 / 人设提示词不进本地模型。
- 吐了**残缺 JSON** 或动作不在 32 个动作池里 → **退给云端**，云端没配 Key 再退离线规则；
  确实是段人话就当回答收下。不会把半截 JSON 显示给用户。
- 缺依赖 / 缺权重 / 加载失败一律如实降级并说明原因，**不假装「本地模型答了」**。
- `torch / transformers / peft` 只在 `requirements-finetune.txt` 里，主链路顶层不 import 它们；
  没装这些包的机器完全不受影响。

### 验证

- `pytest -q`：**577 passed / 1 skipped**（较上一版 552 增 25 项，新增
  `tests/test_lora_engine.py`）；`tools/check_contracts.py`：**111/111**（新增 8 条：
  格式单一真源、主链路 provider 接线、不合协议退云端、重依赖不进主链路、
  开关与路径落盘、设置界面真控件、真机验证脚本在）；`tools/test_agent_features.py`：157 通过 / 0 失败；
  `tools/eval_tasks.py`：38/38；`main.py --selftest`：36/36。
- 文档：README 能力对照表把 LoRA 从「独立训练脚本」改成「桌面主链路（可选 provider）」并写明边界；
  新增「本地微调模型（LoRA）」一节（装依赖 → 训练 → 开开关）；架构说明的 Mermaid 图补上
  `lora_engine` 节点与连线；QUICK_START 扩到 14 条断言（新增第 14 条手验步骤）。
- 三份文档的 pytest 条数与契约条数由契约脚本对着真实收集结果核，写错就红。

## [2026-11-18] · 执行轨迹收进设置页：工程面板不该占用户的导航位

> 用户发来截图：左侧导航第三项就是「执行轨迹」，整页是轮次 / 事件 / 动作 / 失败 / token 的表格，
> 原话是「这个界面不太适合直接显示到前端界面」，要求把它藏进设置里。

### 变更

- **导航 8 项收成 7 项**：`HIDDEN_IN_NAV = ("trace",)`。页面本身没删，仍在 `QStackedWidget` 里
  占着原下标（`_nav_buttons` 用 `None` 补齐对齐，否则 `switch_page(3)` 会点到别人的按钮）。
- **关于页新增入口**：「执行轨迹（工程面板）」卡片给一句人话摘要（已记录几轮 / 失败率，
  或「还没有记录」/「子系统当前关闭」）+ 一个「打开执行轨迹」按钮；时间线导出仍在那页里。
- **跳页不再写死下标**：新增 `page_index(key)`。顺手修掉一个真 bug——「关于 → 关于与状态」
  菜单项写的是 `switch_page(5)`，点开的一直是「本机访问」页，而且不会报错。
- **切到隐藏页时整组导航取消选中**：互斥组要先 `setExclusive(False)` 才能全不选，
  否则顶栏写着「执行轨迹」、左边却还亮着上一项。

### 验证

- 新增 3 条单测：7 个页签且切到轨迹页无高亮（回到普通页恢复）、关于页按钮真能跳、
  菜单「关于与状态」打开的是关于页。
- 契约 101 → **103**：`执行轨迹不占导航位、入口收进关于页`、`工作台跳页不写死下标`。
- 文档：README / 产品功能介绍 / QUICK_START / 面试手册统一为「八页、导航七项」；
  顺手把「第 8 页内嵌网页台」「第 7 页本机访问」这类页码写法改成按名字说——
  页序早就漂了（网页台其实是第 7 页、关于页是第 8 页），数字一旦写进文档就会静静说谎。

## [2026-11-18] · 偏好从「认关键词」升级到「听得懂话」：每 3 轮让模型归纳一次

> 用户问：它到底怎么知道我的偏好？我说「我是做财务的，别跟我讲实现细节」，它记不住。
> 查下来：规则层确实是自动的，但只认 6 类固定句式，开放式偏好一条都学不到——
> 不是要用户手动指定，是「自动」的覆盖面太窄。

### 新增

- **`PreferenceStore.observe_batch()`**：每 3 轮把最近 8 条对话交给模型抽开放式偏好，
  与规则层写入同一个 `preferences.json`；`source` 字段区分「规则命中 / 模型归纳」。
- **原话引用校验**（`parse_llm_preferences`）：模型返回的每条偏好必须附一句用户原话，
  比对前去空白逐字匹配；查无实据、少于 4 字、key 不合法、value 超 120 字的一律丢弃。
  这是这一层唯一的防幻觉手段——宁可漏，不能编。
- **异步 + 单飞**：`Framework._maybe_llm_preferences()` 在后台线程跑，`_pref_llm_busy`
  保证同一时刻最多一个在途请求；没配 API Key 时连轮次计数都不走。
- **两个能力开关**：`PREFERENCE_LLM`（自动归纳）与 `PREFERENCE_PANEL`（界面是否显示）。

### 变更

- **偏好页默认只显示一句说明**：偏好继续在后台学、继续影响回复，但不在界面上摊开；
  用户去「关于与设置」打开「在界面上显示偏好」才看到明细与删除按钮（用户明确要求）。
- 偏好表格新增「来源」列（`规则命中` / `模型归纳`）；老落盘文件没有该字段时按规则命中显示。
- 关于页开关从 5 个变 7 个；`CAPABILITY_KEYS` 同步增加两个落盘键。
- 规则层与模型层共用 `_merge_items()` 合并逻辑，两套「改主意」语义不会再分叉。

### 验证

- `pytest -q`：549 passed / 1 skipped（新增 16 条：引用校验 8、合并与冲突 2、
  异步调度 2、UI 显隐 1、提示词去英文 key 1、层级去重 1、异常吞掉 1）；
  `tools/check_contracts.py`：96/96。
- 规则层原有 56 条用例全绿，证明抽公共合并函数没有改变旧行为。

## [2026-11-18] · 工作台第 8 页内嵌网页台：从来没生效过的那次启动

> 用户发来截图：第 8 页写着「这个环境没有安装 PyQtWebEngine，请执行 pip install」。查下去发现包装着（5.15.6），这句话是假话，而这个功能在**每一次正常启动**里都没生效过。

### 修复

- **`AA_ShareOpenGLContexts` 没在 `QApplication` 之前设**：PyQt5 只靠这个进程级属性判断 WebEngine 的初始化时机，而工作台是懒加载——`from PyQt5.QtWebEngineWidgets import QWebEngineView` 必然发生在 `QApplication` 之后，于是每次都 `ImportError`，每次都降级成说明卡。`main.py` 现在在 `app = QApplication(sys.argv)` 之前设好该属性（449 行 / 451 行）。
- **降级文案不分原因**：`_try_init_web_view()` 的 `except ImportError` 现在先用 `importlib.util.find_spec` 判包在不在——真没装才喊 `pip install PyQtWebEngine`；装着却起不来的话如实写「PyQtWebEngine 是装着的，但内嵌浏览器没能初始化：<异常原话>」并给出临时绕行。把两种修法完全相反的情况合并成一句，等于把能自查的缺陷变成让人重装环境的猜测。
- **视图构造可能把进程干掉**：`QWebEngineView(...)` 的整个构造过程包进 `try`，失败只写占位文案 + 记日志。信号槽里的未捕获异常会被 PyQt5 直接 abort（退出码 127、不打 traceback），授权卡那条已经踩过一次。

### 新增

- `tests/test_workspace_webview.py`（8 项）：两条**开子进程按真实启动顺序**跑（先建 `QApplication` 再 import，不设属性真的 `IMPORTERROR`、设了真的起得来），一条断言 `main.py` 里那行**真的调用**排在 `QApplication(` 之前（搜的是 `QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts`，不是裸名字——只搜名字的话把代码删了测试照样绿），另外四条用假模块驱动三种降级形态与「网页台没起来时那个按钮真的把服务拉起」。
- `main.py --selftest` 新增一项「网页台可内嵌」：此刻 `QApplication` 已存在，正好复现懒加载时机；真没装包记 SKIP，不算失败。
- [ADR 0009](docs/adr/0009-qt-attribute-before-qapplication.md)：进程级 Qt 属性的设置时机、降级文案必须按原因分支、以及「顺序 bug 只能由顺序测试拦」。
- `docs/QUICK_START.md` 断言 8 把「七个页签」改成实际的八个，并写清第 8 页要看到真网页（顶部大标题「凌零 · 智能体服务」），不是一段喊人装包的说明文字。

### 变更

- `tools/check_contracts.py` 新增 5 条：属性排在 `QApplication` 之前、降级文案分开两种原因（`pip install PyQtWebEngine` 在工作台源码里只许出现一次）、创建失败不许漏异常回 Qt 槽、启动冒烟里必须有「网页台可内嵌」这一项、第 8 页必须有真窗口截图证据（存在且 > 50 KB）；ADR 齐备那条扩到 0007/0008/0009。

### 验证

- `pytest -q`：533 passed / 1 skipped（共 534 条，与真实收集一致）；`tools/check_contracts.py`：96/96；`tools/test_agent_features.py`：157 通过 / 0 失败；`tools/eval_tasks.py`：38/38；`main.py --selftest`：36/36（离屏跳过 2 项布局几何断言）。上一条目里的 525 / 91 / 35 是当时口径，已被本条目取代。
- 真窗口实跑（Windows 平台，非离屏）：切到第 8 页打印 `VIEW_CLASS QWebEngineView` / `PLACEHOLDER_HIDDEN True` / `FINAL_URL http://127.0.0.1:8000/` / `FINAL_TITLE 凌零 · 智能体服务`，截图存为 `output/grant_ui/workspace_web_after_fix_screen.png`（纯白像素占比 0.000）。同一次运行里改用 `QWidget.grab()` 抓出来的是整片纯白——Chromium 的合成层抓不到，那种图会说谎，所以抓取必须用 `primaryScreen().grabWindow(winId())`，并把这张证据截图本身做成一条契约。
- 负向实跑：删掉 `main.py` 里那行属性调用后，`test_main_py_sets_the_attribute_before_creating_qapplication` 与契约「WebEngine 属性设在 QApplication 之前」同时变红（94/96），恢复后全绿。
- 关于旧截图的教训：仓库里 `output/grant_ui/workspace_web_dark.png`（11-17）确实拍到第 8 页有网页，它没说谎，但什么也没证明——抓图脚本先 import 了 WebEngine 再建 `QApplication`，正好是这个属性唯一不必被设好的顺序。**验证方式本身不会失败，等于没有验证。**
- 仍未处理：审计清单第二~六类（网页端第二个 `GrantService()` 静默 store、`core.request_doc_edit` 绕过 `check_read`、`.gitignore`/`.dockerignore`/Dockerfile `--host 0.0.0.0`、零演示媒体与 15 个未推送提交）按约定保持原样。

## [2026-11-18] · 跨端授权链路：真实执行里断掉的四条

> 用户点出：514 项测试全绿，但桌面 ⇄ 网页这条授权链路真跑起来是断的。这一轮只修第一类问题（链路真断），其余审计条目按约定不动。

### 修复

- **网页点「允许」被自己人改写成「拒绝」**：授权卡 `closeEvent` 对没答过的卡片补发一次 `deny`，而 `GrantService.answer()` 之后的卡片关闭也照发，于是网页刚落的 `file` 档被后来的 `deny` 覆盖、暂存的桌面动作链整条被丢。现在 `answer()` 返回 `newly`（这条请求是不是第一次被真正回答），`Framework.answer_grant` 只在 `newly` 时广播；`mark_resolved()` 有了调用方，已决卡片关闭不再补发拒绝。
- **两条并发请求只有一张卡**：`GrantCard` 原来是单例复用，第二条请求把第一条覆盖掉，第一条永远答不了。现在 `ui/grant_card.py::popup(info, pet_x, pet_y, cascade=)` 一条请求一张卡，位置按 `cascade` 横向错开、放不下时向下错，`_grant_cards` 按 gid 记账，`shutdown()` 全部收掉。
- **恢复队列不认请求归属**：原来只有一个 `self._grant_queue`，任何一条请求答复都会把它当成自己的队列恢复。现在改为 `self._grant_queues[gid]`，`_apply_grant_resolution` 只 pop 自己那份；`queue is None`（网页发起、桌面没暂存过）时**一步都不动桌面链路**；拒/超时候只丢自己那一步（`_note_plan_failure` + 归还其余），不再整条清空。
- **点一下授权按钮桌宠就消失**：`_on_grant_resolved` 里用 `info.get("timed_out")` 读 `GrantService.get()` 的返回值，而它给的是 `PendingGrant` 对象不是字典 → `AttributeError`；PyQt 对槽函数里的未捕获异常直接 abort 整个进程（退出码 127、连 traceback 都不打）。现在按属性读 `info.timed_out`，并把槽包成「兜住异常也要如实播报」的外壳。

### 变更

- `ui/grant_card.py`：多卡级联定位；`framework/core.py`：新增 `_open_grant_card` / `_close_grant_card` / `_requeue_grant_actions` / `_apply_grant_resolution`。
- `tools/check_contracts.py` 文档口径升级：`pytest` 数字不再只把三份文档互相比对（一起写错照样全绿，514 就是这么过期了 11 项），改为真跑一次 `pytest --collect-only` 拿客观条数，要求文档里 `passed + skipped` 与之相等；契约条数改为拿本次真实条数直接核三份文档与 README 目录树的「CI 契约检查（N 项）」，这条不计入条数、写错就红。

### 验证

- 新增 `tests/test_grant_chain.py`（11 项）：拿真实 `Framework` 进程 + 真实 `GrantCard` + 真实回环锚点跑链路，不再喂打桩应答。含「网页允许不被卡片关闭改写」「两张卡都能答（逆序回答且两个文件都真被读）」「网页请求不动桌面队列」「答过期 gid 吃不掉别人的队列」「槽里抛异常不 abort 进程」「`PendingGrant` 不许当字典读」等。
- `pytest -q`：525 passed / 1 skipped（共 526 条，与真实收集一致）；`tools/check_contracts.py`：91/91；`tools/test_agent_features.py`：157 通过 / 0 失败；`tools/eval_tasks.py`：38/38；`main.py --selftest`：35/35（离屏跳过 2 项布局几何断言）。
- README、产品功能介绍、docs/QUICK_START 的测试数字全部改为上面的实测值，并把 README 里自相矛盾的「CI 契约检查（53 项）」「87/87 通过 | 共 85 项」收敛成一个真实条数；断言 11 增加第 7 步（两张卡 + 关卡后网页点允许）。
- 设计记录：[ADR 0007](docs/adr/0007-cross-end-shared-grant-channel.md) 补「四条真断与由此定下的两条不变量」。

## [2026-11-17] · 桌面工作台与网页调试台合并为同一张界面

> 用户点出：项目里本来就有网页版，但一直和桌面版分治——两套视觉、两个入口、连服务启动都是两份实现。这一轮把已有网页台并进工作台，不重建、不另抄一份前端。

### 新增

- `ui/workspace_window.py`：第 8 页「网页调试台」用 QWebEngineView 内嵌 `http://127.0.0.1:8000/`，加载的就是浏览器访问的那一个 URL。`api/static/index.html` 不再另做简化版；浏览器里的会话、本机文件授权（GrantAnchor 回环）与工作台完全共用。
- `framework/api_service.py`：本地服务的唯一启动出处（`API_BASE` / `api_alive` / `start_api_in_thread`）。`python main.py --api` 与工作台内嵌页都从它取，端口占用时复用已在跑的那份，不再出现两份启动逻辑。
- 网页端支持 `data-theme`：默认深色与桌面基调一致，工作台换肤时通过 JS 注入同步（QtWebEngine 下用 `setAttribute` 而非 `dataset.*` 赋值，已实测）。

### 变更

- `api/static/index.html`：样式表整体换成 `ui/theme.py` 同令牌的配色（深色为默认、浅色可切），次要按钮改成线框，顶部新增「切换主题」；视觉与工作台对齐，不再是浅蓝白另一套语言。
- 内嵌浏览器懒加载：只有切到网页台时才创建 QWebEngineView；没装 PyQtWebEngine 或没起服务时给明确的降级说明与真能点的「启动本地服务」按钮（点了才在后台起，定时重试不阻塞 Qt），不偷偷拉进程、不让整个工作台起不来。
- `requirements.txt` 增加 `PyQtWebEngine`；API-only 镜像（`requirements-api.txt`）保持不含 Qt。

### 验证

- `pytest -q`：514 passed / 1 skipped；契约 87/87（新增「同一 URL 同一启动出处」「内嵌页跟着换肤」两条）；网页脚本 `node --check` 通过；本地服务实测 `/` 返回新样式页。
- 实窗截图 `output/grant_ui/workspace_web_dark.png`：工作台第 8 页内嵌网页成功，深色视觉与工作台一致。浅色主题用 JS 读回 `data-theme=light` 与 `body` 背景 `rgb(246,247,249)` 验证生效（QtWebEngine 离屏合成在换肤后抓不到新帧，属截图时序，不是功能问题）。

## [2026-11-17] · 图标统一为线框风格

> 用户反馈授权卡上那把黄色小锁是「廉价图标」，要只有边框的简约高级感。工作台导航早就是线框矢量，但浮层卡、面板按钮和网页调试台还留着 emoji，这一轮把剩下这些可见控件全部收进同一套描边图标。

### 变更

- `ui/icons.py`：新增 `unlock` / `timer` / `clipboard` / `check-circle` / `check` / `play` / `x` / `folder` / `file-text` / `robot` 十个线框图标，统一 24×24、`fill=none`、1.8px 描边、2x 超采样；新增 `pixmap()` / `icon_label()` 给 QLabel 用。
- `ui/grant_card.py` / `ui/plan_card.py`：标题与倒计时的 emoji 换成线框图标，按钮文字不再带 `▶` / `✕` 前缀，图标与文字分行对齐；计划完成时标题图标切到 `check-circle`。
- `ui/pet_window.py` / `ui/ui_manager.py` / `ui/input_dialog.py`：工作流模板、运行 / 完成 / 删除 / 清理 / 打开 / 记录按钮及设置标题、记录面板关闭键全部改为线框图标；`⚠️` 提示前缀改为「注意：」。
- `api/static/index.html`：与桌面端同口径的内联 SVG symbol 精灵；授权头、麦克风、读文件 / 多智能体 / 入库 / 新会话按钮改线框，状态文案不再用彩色 emoji 前缀。

### 验证

- `tools/check_contracts.py` 新增「浮层与面板图标是线框矢量，不再用彩色 emoji」契约，总数 84 → 85；`pytest -q` 514 passed / 1 skipped，网页脚本 `node --check` 通过。

## [2026-11-17] · F4：读完能直接作用（list_dir / edit_doc / 批量预算），工作台第 7 页跟上

> 用户点出的痛点：F1–F3 上了授权之后，界面上一处看不见、也撤不掉；右键「修改文件（PPT/Word）」只有 UI 入口，模型发不出这个动作；同时「读任意路径」加了目录和压缩包之后，一次读多份会把资料缓冲撑爆，note 却什么都不说。这一轮把这三件事接上，并把动作数一次改齐到代码实际的 **32**。

### 新增

- `framework/action_registry.py` / `framework/action_parser.py` / `framework/core.py`：`list_dir`（列一层目录）与 `edit_doc`（模型可达的 PPT / Word 改写）进入动作全集与执行分发；`edit_doc` 的 `instruction` 兼容 `content` / `要求` / `怎么改` 等模型写法。动作数 **30 → 32**，连带 `effect_spec`、`PROMPT_ACTION_POOL`、`tests/test_action_registry.py`、契约检查与 README / 产品功能介绍 / QUICK_START / 架构说明 / ADR 0001 / ADR 0004 全部同步，不留「远端写 27、简历写 26+」这类别处口径。
- `framework/file_reader.read_many()` + `Framework._do_batch_read()`：`read_doc` 可传 `paths` 数组一次读多份，**每份截 600 字、最多 4 份**，去重、坏路径单独记失败，超出的份数在 note 与气泡里明写「还剩 K 份没读」；每份单独进一条资料缓冲，避免单条 800 字上限把汇总偷偷截断。批量里只要有一份要授权或终态拒绝就整批停下，不留「偷偷读了能读的」这种半截成功。
- `framework/effect_journal.py`：`RESTORABLE = {"edit_doc"}` 新的可还原分类。`edit_doc` 顺序固定为校验 → 三态判据 → **先 `copy2` 落副本** → 记 `file_edit_item` → 才允许提取全文并发给模型；副本失败就停下并如实说明。输出仍是 `_修改` 新文件，原件只读。`restorable` 在契约上满足 `reversible=True`，区别在逆操作来自副本而不是世界快照差集，见 [ADR 0008](docs/adr/0008-restorable-write-actions.md)。
- 工作台第 7 页「本机访问」：表格列档位 / 路径 / 放开时间，展示 `describe_formats()` 生成的格式说明、最近读取证据卡与「全部撤销」；撤完判据只看空授权记录，同一路径必须重新弹卡。左侧导航分组计数增加「本机访问：放开 N 条」且本身可点。对话页气泡附读取证据卡：列了几项、读了几页、跳过什么、截到多少字、批量共几份实读几份。
- `docs/adr/0007-cross-end-shared-grant-channel.md`、`docs/adr/0008-restorable-write-actions.md`：跨端通道与写动作可逆性分类的决策记录。

### 变更

- `README.md` / [产品功能介绍.md](产品功能介绍.md) / [docs/QUICK_START.md](docs/QUICK_START.md)：pytest 数字统一到 509；QUICK_START 由 9 条断言扩到 **12 条**（10 = 桌面授权三按钮 / file 不溢出 / 重启失效；11 = 浏览器 tree 落到桌面锚点；12 = 本机访问页可见可撤 + 证据卡）。命令栏占位改成带真实路径的例子。
- `ui/theme.py` / `ui/icons.py`：第 7 页表格、空态、格式说明、证据卡与 `shield` 图标并入深浅两套 QSS 令牌，避免新控件在浅色主题下被裁切或长得像只读文字。
- **决定：读取记录不进「我的交付」页**——读文件没有产出文件，进交付页会让那一页开始说谎；读取证据统一放「本机访问」。
- 产品功能介绍里两处硬编码格式清单改为引用 `describe_formats()` 的当前判据表，格式文案只有一个出处：`path_guard` 的表 → 提示词 / 气泡 / 工作台 / 文档。

### 验证

- `pytest -q`：514 passed / 1 skipped（新增 `read_many` 预算 / 去重 / 坏路径、`paths` 解析、证据卡宽度与不裁切用例）；`tools/check_contracts.py`：87/87；`tools/test_agent_features.py`：157/157；`tools/eval_tasks.py`：38/38（修掉评测 C20 漏统计 `restorable` 新桶导致的假红，并加契约钉死）；`main.py --selftest`：35/37（离屏跳过 2 项几何断言）；网页前端 `node --check` 通过。
- 实窗（`QT_QPA_PLATFORM=windows`）截图 6 张逐张核对：`output/grant_ui/` 下深色/浅色 × 对话页（读取证据卡）与本机访问页，以及授权卡两主题；自动断言 14 项宽高均不裁切。截图过程发现并修掉两个真实故障：连续刷新对话时旧气泡/证据卡以 `deleteLater()` 滞留造成叠影（改为先 `hide()` + `setParent(None)`），以及证据卡在水平布局里被挤成窄列（改为按字体度量定宽定高）。浮层卡在两套主题下都保持半透明深底，这是贴桌面时要压住壁纸的有意设计，不是漏换肤。

## [2026-11-17] · F3：网页端与桌宠共用一条授权通道（不分治）

> 前置核实：`main.py --api` 虽在同一进程跑 uvicorn，但 API 并不持有 `Framework`；README 与 Docker 还支持独立 `uvicorn api.server:app`，那是另一个没有 Qt 的进程。因此「共享单例」不成立，必须靠桌面锚点 + 回环通道实现不分治，这条结论直接决定了设计。

### 新增

- `framework/grant_anchor.py`：桌面进程起只监听 `127.0.0.1` 的回环小服务（标准库，无新依赖），把端口与**一次性令牌**写进本机发现文件 `grant_anchor.json`（不含任何路径）；网页端 `ask` / `answer` / `state` / `revoke` 四件事全部转交桌面那份 `GrantService`。令牌用 `secrets.compare_digest` 比对，每次启动重生成。
- `api/server.py`：`ChatRequest` / `AgentRunRequest` 加 `read_path`；`ChatResponse` / `AgentRunResponse` 加 `pending_grant` 与 `grant_note`；新增 `POST /grant/{gid}`，只收 `file` / `tree` / `deny`，**继承 app 级 `require_access`**（配令牌必须带令牌，未配只接受回环），不是匿名可达的后门。自持模式下超时按拒的请求通过下一次响应的 `grant_note` 明说。
- `api/static/index.html`：读本机文件按钮 + 授权确认条，三个按钮与桌面卡一一对应；`sendMsg` / `runAgent` 处理 `pending_grant` 并支持答完自动重试；`describeSearchRefs()` 渲染本地文件读取证据卡。
- `tests/test_api_grant.py`：7 项跨端验收，含杀手级场景——浏览器点「允许整个文件夹」后桌面读同目录另一个文件不再弹卡。

### 变更

- 找不到锚点（桌宠没开 / Docker 里没有 Qt）时，网页端用**同一个 `GrantService` 类**自持：判定与状态机完全一致，授权只在网页端生效；绝不伪造「已授权」。设计取舍见 [ADR 0007](docs/adr/0007-cross-end-shared-grant-channel.md)。
- 120 秒未答按**拒绝**处理并标记 `timed_out`，下一次响应说明是超时按拒，不伪装成用户主动拒绝。

## [2026-11-17] · F2：桌面授权确认回路（三态判定接到真实按钮）

> 上一版只交付了判据与读取层，用户还点不到确认卡。这一版把 `ask_grant()` / `answer_grant()` 做成唯一入口，卡片、动作队列、超时与撤销全部接到同一份状态机上。

### 新增

- `ui/grant_card.py`：三个按钮各带一行后果说明——「只读这一个」（只放这一份，同目录另一份还要再问）/「允许整个文件夹（本次会话）」（整棵子树）/「拒绝」；卡片明写「本次会话有效，重启失效」并有 120 秒倒计时，超时显示「已超时，按拒绝处理」。直接关卡等于拒绝。
- `Framework.ask_grant()` / `answer_grant()` / `grant_state()` / `revoke_grants()` / `read_log()`：桌面卡与网页确认条都只走这两个入口；`grant` 时暂存 `_grant_queue`、停看门狗、立即返回不卡 Qt，用户答完恢复重跑。
- `Framework._do_read_doc()` / `_do_analyze_data()` / `_do_list_dir()` / `_do_edit_doc()` 全部改走 `grant_service.check()` 三态；提示词与气泡里的格式清单改为 `describe_formats()` 动态生成。

### 变更

- 授权仍只有 `file` / `tree` 两档、只活在进程内存；敏感判定永远先于授权，`.ssh` / `.env` / 凭据即使点了允许也不读。
- `tools/check_contracts.py`：新增三态、卡片三按钮、超时按拒、撤销真清空等防退化契约；全量 87/87 通过。

## [2026-11-17] · 本机文件读取改成「三态判定 + 会话内授权」，并收出统一读取层

> 需求是「要能读常用那些文件，任意路径的文件夹、文件，并直接作用在上面」，同时明确不分治：桌面与网页共用一条授权通道。这一步先做**判据与读取层**（用户还点不到确认卡片，桌面接线在下一步）。原来的两态判定（在根里＝读，不在＝拒）唯一的放开方式是把白名单改宽，那等于拆边界；所以判据改成三态，把「要不要放开这个路径」交给用户、并且限定在本次运行里。

### 新增

- `framework/grants.py`：会话内授权记录，只有两档——`file`（这一个文件；目录则只允许列它一层）与 `tree`（这个目录及整棵子树，即用户说的「完全访问」）。只增不持久化，`clear()` 一次全撤；归一化用 `normcase(realpath(abspath()))`，Windows 上换大小写、换正反斜杠、写相对路径都不会要求用户点两次；包含判定按路径段比对（`is_within`），不用字符串 `startswith`，否则 `D:\foo2` 会被判进 `D:\foo` 的子树。
- `framework/file_reader.py`：统一读取层，把「一个路径」变成 `{ok, kind, text, note}`。分派到目录列举 / 纯文本与代码 / docx·pptx·xlsx / 文本型 PDF / 压缩包 / 老格式 `.doc|.xls`（借本机 WPS·Office COM 转文本）。三条硬约定：**永不抛异常**（损坏、加密、被占用、缺依赖都变成中文短句）、**不许空手报成功**（`ok=True` 而正文为空一律改判失败，空目录除外）、**截断与跳过必须写进 `note`**（只列前 200 项、只读前 40 页、只顺带读 12 个文本成员，用户看到的和实际进上下文的一致）。
- `docs/adr/0006-session-path-grants.md`：为什么是三态而不是把白名单改宽、为什么敏感判定必须排在授权之前、为什么授权绝不落盘（`user_settings.json` 自己在白名单根下、又在敏感名单里——用一份可能被读走的文件保存读走文件的通行证），以及代价与边界。

### 变更

- `framework/path_guard.py`：`check_read(raw, grants=None)` 返回三态，判定顺序固定为 存在性 → 敏感 → 格式表 → 大小 → 根目录 → 授权记录；老接口 `validate_read_path()` 语义收窄为「只认静态白名单」（`grant` 仍返回 `ok=False`），网页入库与单测这些不弹确认的调用方不会因为引入授权就默认放行。格式表按用途拆成 `TEXT_EXTS`（新增 py / cpp / h / go / rs / java / ts / vue / sh / sql / ipynb 等代码与配置后缀）+ `OFFICE_EXTS` + `LEGACY_EXTS` + `PDF_EXTS` + `ARCHIVE_EXTS`，无后缀的 `Dockerfile` / `README` / `.gitignore` 走 `TEXT_NAMES`；能力文案 `describe_formats()` 从表生成，不再各处手写一句「目前支持 txt/md/csv」。
- **修掉一个真实漏洞**：`is_sensitive()` 原先只检查 `parts[:-1]`，目录名本身是 `.ssh` 时不算敏感，列一层就能把私钥文件名与大小抄走；现在检查全部分段。
- 压缩包成员只在内存里读、不落地解包：成员名先归一化并跳过 `..` 与绝对路径（zip slip），再过一次敏感表与二进制后缀表；单个 gz（非 tar）解开直读。图片与扫描型 PDF 明确不属于这一层——无文字层时返回「要读得走视觉模型」，不硬解也不静默返回空串。
- `requirements.txt`：补上从未声明却已被使用的 `pypdf`（本机 PDF 文字层抽取），并显式列出老格式依赖 `pywin32`（`sys_platform == "win32"`；缺它时读取层如实降级）。
- `tools/check_contracts.py`：新增 10 条本机文件读取契约（59 → 69）——格式判据只有一张表且文案是表的子集、三态判定只有一个实现、**敏感判定源码位置先于根目录与授权**、敏感分段检查覆盖全部分段、授权只有两档且不落盘（AST 检查只 import `os`、不含 `open(`）、包含判定按路径段、空手而归不算成功、读取层不抛异常、判据层不拖起 Qt、PDF/老格式依赖已声明。
- 文档：README 目录结构与安全说明改写为三态判定口径并挂上 ADR 0006；三份文档数字统一到 493 / 69。

### 隐私

- 授权记录只存在于进程内存，重启即失效，不写 `user_settings.json`、不写 `data/`；`reason` 文案一律不含绝对路径，避免把本机目录结构回显给模型或日志。
- 新增用例全部在 `tmp_path` 里现造文件；PDF 用例用标准库手搓两份真 PDF（有文字层 / 无文字层）而不是打桩，COM 路径在用例里于「起进程之前」就返回，不会拉起他的 WPS。

### 验证

- `pytest -q`：493 passed / 1 skipped（较上一版记录的 434 增 59 项：`tests/test_grants.py` 19 项、`tests/test_file_reader.py` 29 项、`tests/test_path_guard.py` 由 35 项扩到 46 项（其中 1 项在本机不能建符号链接时跳过））。重写而不是放宽：老的 6 条「白名单外一律拒」断言换成更强的三态不变量——没授权时根外**绝不 allow**、`..` 逃逸与指向根外的符号链接不继承根内放行、`file` 档不放开父目录、`tree` 档不溢出到兄弟目录、给了整树仍然读不到 `.ssh` 与 `user_settings.json`。
- `tools/check_contracts.py`：69/69；`tools/test_agent_features.py`：157 通过 / 0 失败；`tools/eval_tasks.py`：38/38；`main.py --selftest`：35/35；`tools/test_settings_persistence.py`：17/17。
- 本轮为判据与读取层交付，桌面气泡／网页确认条的接线在下一步（F2/F3），因此 README 的功能表暂未宣称「任意路径可读」。

## [2026-11-17] · 深色专业工具风重构 + 关于页五个开关真的能点

> 两件事连着做的：先把前端样式收到一个出处（`ui/theme.py` 设计令牌），再处理用户指出的「这几个按钮是摆设」——关于页那五行「当前开关」原本是**写成按钮样式的只读 `QLabel`**。第二件事比换控件难：光换成 `QPushButton` 只是把长相的谎撤掉，还要让翻牌真的改变程序行为，而本项目里「一个能力开没开」有两种读法（有的调用点每次读 `config.X`，有的只看子系统对象在不在场）。

### 新增

- `ui/theme.py`：全仓唯一的样式出处。DARK / LIGHT 两套调色板 + `SP`（4px 间距阶梯）/ `RD`（圆角）/ `DIM`（尺寸）/ `SZ`（字号阶梯 micro 11 → title 17）/ `WT`（字重），一份带 `__TOKEN__` 占位符的 `TEMPLATE` 由 `_sub()` 替换；`set_theme()` / `install(app)` 负责 `app.setStyleSheet()` 与全局字体安装，换肤时工作台、桌宠浮层、预览窗一起变。
- `framework/core.py`：`CAPABILITY_STORES` + `_open_capability()` + `set_capability(attr, on)`。开 = 当场 `importlib` 建起对应 Store（建不起来只降级为 `None`，不拖垮主链路）；关 = 当场把对象撤掉；随后落盘，**落盘失败则 flag 与对象一起回滚**并如实提示「没写进配置文件」。
- `framework/config.py`：`CAPABILITY_KEYS`（5 个开关的属性名 → JSON 键名，落盘与读回共用同一张表）；`save_user_settings()` 改为返回 `bool`；`apply_user_settings()` 按表把 5 个键读回来。
- `docs/adr/0005-runtime-capability-switches.md`：为什么「关掉」实现为「把子系统对象撤场」而不是在每个调用点补 flag 判断，以及这样做的代价与边界。

### 变更

- `ui/workspace_window.py`：关于页「当前开关」五行由只读 `QLabel#statChip` 改为 `QPushButton#switchToggle`（可勾选、手型光标、悬停/按下变色、开启绿 / 关闭红），点一下即翻 `config` + 建/撤子系统 + 写 `%APPDATA%\DesktopPet\user_settings.json`，右下角状态栏回报结果；信号连 `clicked` 而非 `toggled`（程序重绘时 `setChecked` 不该被当成用户操作存盘）。开关行只建一次、刷新时原地重绘——先前用 `deleteLater()` 重建会让旧按钮在事件循环前留在对象树里，切两次页攒出 10 个点得着却看不见的按钮。删掉一份从未被调用的重复 `_refresh_status`。
- 五套互不相认的配色（`_QSSTEMPLATE`、`ui/style.qss`、预览台内联三段、气泡、卡片）合并进 `theme.TEMPLATE`；`ui/pet_window.py`、`ui/plan_card.py`、`ui/input_dialog.py`、`ui/preview_window.py`、`ui/ui_manager.py` 全部改为读令牌，`main.py` 走 `theme.install(app)`。
- 关于页卡片副标题与 `OPERATION_ROWS` 改为如实描述可点语义（「点开就开启、点关就关闭，立即生效并写进本机配置」），使用说明补上「关于页的五个能力开关」这一处可点区域。
- `tools/check_contracts.py`：新增 6 条开关链路契约（属性名真实存在且可落盘、按钮而非只读标签、连 `clicked`、会建/撤对象、失败回滚、启动读回），并把「关于页操作说明」那条改成读 `OPERATION_ROWS` 这个唯一出处，两条字体契约改到重构后的真实出处（53 → 59；重构后曾自行变红 2 条，属检查口径没跟着改）。
- 文档：README 桌面章节新增「关于页五个开关是可点控件」条目；`docs/QUICK_START.md` 扩为 9 条断言（断言 9 = 7 步手动验证这五个开关，含「关掉事件轨迹后轨迹不再增长」与「反复切页不攒重复按钮」）；三份文档数字统一到 434 / 59。

### 隐私

- 五个开关只写本机 `%APPDATA%\DesktopPet\user_settings.json` 里的 5 个布尔键（沿用原子写 + `chmod 600`），不写聊天内容；相关测试把 `_user_settings_path` 或 `APPDATA` 指到临时目录，不碰真实配置与 `data/`。

### 验证

- `pytest -q`：434 passed / 1 skipped（较上一版记录的 422 增 12 项。开关 5 项：可按按钮而非只读标签、点击翻 config 并落盘、不重启即改变行为、存盘失败整体回滚、下次启动读回同样的值；其余为样式与渲染用例：样式只有一个出处、无硬编码字号、气泡宽度实测不裁切、气泡纯文本与换行高度、模型标记拍平、裸 QWidget 窗口走窗口底色、卡片标题不被短路径压碎。）
- `tools/check_contracts.py`：59/59；`tools/test_agent_features.py`：157 通过 / 0 失败；`tools/eval_tasks.py`：38/38；`main.py --selftest`：35/35；`tools/test_settings_persistence.py`：15/15（新增第 2.6 节子进程临时 `APPDATA` 的落盘—读回环）。
- 实窗（`QT_QPA_PLATFORM=windows`）截图 20 张逐张核对深浅色六种页面与浮层，无文字裁切；真实点击两个开关后 `user_settings.json` 与状态栏文案一致。

## [2026-11-17] · UI 控件与真实能力逐项对齐（清掉剩余摆设）

> 用户再次指出「前端 UI 界面和项目上有的功能要符合好，有些按钮都是摆设」。全量核对桌面工作台与网页调试台约 100 个可点击控件后，把剩余的「点了没反应 / 名不副实 / 默认必失败」控件逐个处置：能接真实能力的接上，接不上的删掉或置灰并写明原因。

### 变更

- 桌面工作台 `ui/workspace_window.py`：
  - 「检查更新」原本没有任何 `clicked` 接线，tooltip 还自认没有在线更新通道；改为「查看更新」，点击打开 GitHub Releases 页（真实可达），文案如实说明开源项目需手动拉取更新。
  - 「新建对话」原本只切页 + 聚焦输入框，不清历史；新增 `MemorySystem.clear_history()`（只清对话记录，保留长期记忆 / 偏好），按钮接它并 `history_changed.emit()` 同步桌宠历史面板，清空前有确认框，避免误触。
  - 「文件 → 退出程序」发出的 `quit_requested` 全仓库无人接收；在 `ui/pet_window.py` 补接 `quit_app`（真退出桌宠与托盘）。
  - 关于页还停留在旧版界面：删掉「加号按钮 / @ 引用 / 分支 / 访问级别 / 模型」等已不存在的控件描述，改为逐项说明当前真实按钮；顶部铃铛 tooltip 改为「查看执行轨迹」。
  - 删除死信号 `context_requested` 与死方法 `_quick`。
- 预览组件调试台 `ui/preview_window.py` / `ui/input_dialog.py`：预览用的独立 InputDialog 里「历史记录」「工作台」两条信号没有接收者，新增 `set_aux_buttons_visible(False)` 在预览窗隐藏，只留真接通的输入 + 发送。
- 网页调试台 `api/static/index.html`：新增页首能力状态条，加载时读 `/health`，按 `llm_key_configured` / `rag_available` / `search_key_configured` / `voice_available` 置灰不可用控件并写明缺什么；新增「重新检测」与「🧹 新会话」（接真实 `DELETE /sessions/{sid}`）；修中文输入法回车误发、入库 `source` 默认值与后端不一致。
- 后端 `api/server.py`：`GET /` 静态页免令牌（否则 Docker 场景下页首令牌框永远不可达，其余接口照常鉴权）；`/health` 增加 `llm_key_configured` / `voice_available`；Whisper 依赖改用 `find_spec` 真探测，缺依赖返回明确 503 而不是含糊 500；`/agent/run` 无 Key 时回报可诊断文案。
- 文档与契约：README 新增「网页调试台的按钮可用性」对照表并修正工作台布局描述；三份文档的 pytest / 契约数字同步；`tools/check_contracts.py` 新增 4 条防摆设回归契约（49 → 53）。

### 验证

- `pytest -q`：422 passed / 1 skipped（新增 14 项覆盖新建对话、更新按钮、退出菜单、关于页文案、预览窗按钮、health 能力字段、首页免令牌、语音 503、/voice/chat、无 Key 文案与调试台门禁）。
- `tools/check_contracts.py`：53/53；`main.py --selftest`：35/35 通过。

## [2026-11-15] · 命令栏移除摆设控件（只留真接通的输入 + 发送）

> 用户质疑「你做的功能和 UI 按钮链接好了吗，别做了功能但 UI 是摆设」。核实：主功能（输入 → `fw.send_command` → 气泡回显）真实接通；但底部命令栏抄参考图的一排小控件（分支 / 访问级别 / 模型 / sparkle / 麦克风 / 添加上下文）在本项目里没有对应能力，点了没反应，是纯摆设——模型下拉框里的「Hy4 / GLM-4.6V」甚至和项目实际用的 DeepSeek 对不上。

### 变更

- `ui/workspace_window.py`：命令栏由「两层（大输入框 + 一排小控件）」收缩为「单层（输入框 + 圆形发送按钮）」，移除分支 / 访问级别 / 模型 / sparkle / mic / plus 六个空壳控件；占位文案去掉本项目没有的「@ 添加上下文，/ 调用技能」假话术，改为如实描述真实交互。删除 `_add_context`，`_focus_command` 改为选中现有文本而非塞「@ 」。
- 移除无用的 `QComboBox` 导入与 `cmdCombo` / `chip` 两条 QSS 规则。

### 验证

- `test_workspace_window.py` 24 passed；契约 49/49；离屏渲染确认命令栏只剩输入框 + 发送按钮、无残留空壳。

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
