# ADR 0003：用「结构化偏好 + 证据门槛规则归纳」替代逐事询问与全量历史塞进提示词

- 状态：已接受
- 日期：2026-11-15
- 相关代码：`framework/preference.py`、`framework/evolution.py`、`framework/trajectory.py`、`framework/core.py`（`send_command` / `_learn_preferences` / `_adjust_action` / `_traj_end` / `_evolve`）、`framework/ai_engine.py`（`prefs_text`）、`framework/config.py`、`tools/benchmark.py`、`tests/test_adaptive_integration.py`
- 相关 ADR：[0001 撤销按世界快照求差集](0001-undo-by-world-diff.md)、[0002 技能重放零模型调用](0002-skill-replay-zero-llm.md)、[0004 可验证执行](0004-verifiable-execution.md)

## 背景

需求原话是「它应该能从交流里发现我喜欢什么、遇到问题能自己进化」。拆成工程约束有两条：

1. **偏好要自动发现、且要作用到行为**。不能要求用户显式说「记住我喜欢深色 PPT」才算；从普通一句话里抽出来之后，文档样式、回答详略、联网开关、语气等要真的跟着变，而不是只往日志里写一行。
2. **重复出现的失败要能被归纳成调整**。搜索零重合、文档太薄、路径读不出、用户撤销、动作超时这类问题再次出现时，系统下次执行前应有对应调整，而不是每轮从头试错。

继承两条既有约束：桌面版默认 `USE_REAL_AI=False`，离线、无模型、API 欠费时主链路仍要跑（[ADR 0002](0002-skill-replay-zero-llm.md)）；用户原话不写进落盘文件。因此偏好抽取必须是纯本地确定性规则，规则归纳只能消费事件流里的结构化字段，不能把整段对话交给模型做总结。第三条约束来自评审：「越用越懂你」这类说法需要能给出可复现的对照数据，否则只是形容词。

## 备选方案

### 方案 A：每件事都问用户，或让用户显式声明偏好

给六个维度做一张设置表，或在每次可能影响结果的动作前弹一次确认。

**失败模式**：一是打断成本，Plan-and-Execute 的卖点是一句话端到端，逐步询问把链路退回到向导；二是覆盖面，用户只会填自己想得起来的那几项，说不出来的习惯很难被发现；三是保鲜，偏好会改主意，问一次的结果很快过期，而没有机制判定哪条已失效；四是「问了但没用」的风险，若询问结果只落设置、没有接到执行前的动作调整上，等于多了一次点击。

### 方案 B：把历史对话全量塞进系统提示词，让模型自己领悟

不做抽取，把最近 N 轮对话原文拼进 system prompt，由模型自行判断偏好与上次失败的教训。

**失败模式**：长度与噪声不可控，N 取小了记不住、取大了挤占上下文且把无关闲聊也塞进去；不可验证，无法断言「哪条偏好生效了」「这次调整由哪次失败触发」，出问题只能重读 prompt 猜；隐私上等于把用户原话复制进每次请求的上下文，和「原话不落盘」的边界不一致；改主意之后旧偏好与新偏好同时留在 prompt 里互相打架，模型按哪条执行取决于拼接顺序；还把「能不能用」绑在了模型与 Key 上，离线链路直接失效。

### 方案 C：结构化偏好 + 证据门槛的规则归纳（采用）

偏好侧：纯规则从一句话里抽六个维度的当前决定，落盘只存命中短语与原话哈希，行为侧从「当前落盘状态」实时读取。进化侧：从事件流的 failure / quality / undo 事件按族归纳规则，达到证据门槛才生成，按后验置信度打分并自动淘汰。

**代价与失败模式**：纯规则只认规则表里的说法，换个说法就漏抽，覆盖面不如语义模型；规则归纳依赖事件流完整，若轨迹被关闭或事件降级丢失，就学不到东西；证据门槛取 2 条仍可能把偶发两次同类噪声误当成规律，因此必须配自动淘汰；规则作用在动作执行前，若长期不验证成效，可能把一次性的环境故障固化成长期策略。这些代价用「可验证、可关闭、可淘汰」来对冲，而不是靠调模型参数。

## 决策

### 1. 偏好：纯规则抽取，落盘只存证据指纹

`framework/preference.py` 的 `extract_preferences(text)` 是纯函数，支持 6 个维度：`doc_theme` / `verbosity` / `tone` / `web_search` / `language` / `reminder_style`。显式喜好与否定分开处理（「我喜欢深色 PPT」→ 正向主题、「简洁点」→ `verbosity=concise`、「以后别联网」→ `web_search=False`），正向主题色需带设计/偏好语境，避免「查一下绿色能源」被误抓；置信度分层（显式喜好 / 「以后都」0.9，否定与「…点…些」0.7，模糊词 0.5），非偏好句不误抓。

`PreferenceStore` 把当前决定落进 `data/preferences.json`：同一维度只保留一条，同值重复累加 `hits` 与 `evidence`，改主意覆盖 `value` 但保留 `evidence` 全历史与 `supersedes` 链指针；`evidence` 只存 `text_digest`（原话哈希）、命中的控制短语（≤20 字）、轮次与时间戳，上限 `MAX_PREFERENCES=40`，超出按 `updated_at` 丢最老。

行为侧不是「只存不用」：

- `as_prompt()` 输出标签化的一行（如「文档配色：深色」），由 `ai_engine.AIWorker` 的 `prefs_text` 动态块追加到系统提示词之后，`SYSTEM_PROMPT` 常量本身逐字不变；
- `doc_style()` 作为 `_do_create_doc` 的默认样式注入，用户当次显式 `style` 优先；
- `verbosity()` / `allow_web_search()` 驱动回答详略与联网开关，`_adjust_action` 在动作执行前应用：`web_search` 被偏好关闭时会跳过并回一句「按你的偏好，这次不联网，我用已有资料来答」。

`send_command` 每轮自动调用 `_learn_preferences(text)`，用户不必说「记住」。

### 2. 规则：从失败事件归纳，2 条独立证据才生成

`framework/evolution.py` 从轨迹里的 `failure` / `quality` / `undo` 事件归纳 5 个规则族：`search_too_broad` / `doc_too_thin` / `path_unreadable` / `user_undid` / `action_timeout`。`_traj_end` 收轮时顺带触发 `_evolve`，把最近事件喂给 `RuleStore.learn()`。

闸门按重要性排：

- **证据门槛**：同族同模式至少 2 条独立 `event_id` 才生成规则，单次失败只进 `pending`；`learn` 幂等，同一批事件重复喂入不会把一条证据算两遍。
- **置信度**：`confidence = (1 + s) / (2 + s + f)`，即 Beta(1,1) 后验均值，成功 `s` 与失败 `f` 都计入，避免一条规则只靠「被生成过」就长期高置信。
- **自动淘汰**：`failures >= 3 且 successes == 0` 时 `enabled=False` 并写 `disabled_reason`，被停用规则仍可查询与 `forget`，但不再出现在 `apply()` 里。
- **上限**：`MAX_RULES=24`，超出按 `(enabled, confidence, updated_at)` 丢最弱的。

`RuleStore.apply(context)` 返回 `search_suffix` / `min_slides` / `min_bullet_len` / `skip_action` / `require_confirm` / `retry_budget` / `rule_ids` / `note`，由 `_adjust_action` 在执行前应用：换词重搜、跳过坏路径、执行前确认、降低重试预算；`min_slides` 会抬高文档质量门的门槛。

### 3. 降级边界

`framework/config.py` 提供 `TRAJECTORY_ENABLED` / `PREFERENCE_LEARNING` / `PREFERENCE_NOTIFY` / `EVOLUTION_ENABLED` / `PLAN_DAG_ENABLED` / `EFFECT_SPEC_STRICT` 六个开关；`EFFECT_SPEC_STRICT` 默认 False，契约前置校验不通过时只记录不阻断。三个落盘文件都走惰性 `app_data_dir()`，可用 `LINGLING_DATA_DIR` 覆盖。主链路里所有新增调用都包在 try/except 内，任一子系统初始化或运行异常只写日志、不影响原有动作链。

## 后果

- **隐私**：用户原话不写进 `preferences.json` / `rules.json` / `trajectory.jsonl`，只以哈希或 basename 形式出现；`as_prompt()` 输出的是标签化描述，不含证据原文；时间线展示层对撤销账本只显示数量。
- **证据门槛防偶发误学**：2 条独立事件是生成规则的必要条件，配合幂等 `learn`，一次性的失败不会被放大成长期策略。
- **自动淘汰防规则膨胀**：连续失败且毫无成功成效的规则自动停用，`MAX_RULES` 与 `MAX_PREFERENCES` 给文件规模设了上界，避免「越用越臃肿」。
- **覆盖面有限是有意的取舍**：纯规则抽取不是通用语义理解，5 个规则族也不覆盖所有失败类型。把「可验证」放在「看起来更聪明」前面，代价是漏抽与漏归纳。
- **开关可降级**：关掉 `EVOLUTION_ENABLED` / `PREFERENCE_LEARNING` 后，主链路回到原来的静态行为；`tools/check_contracts.py` 把接线 token 钉成检查项，防止「文档写了、代码没接上」。

## 可验证方式

`python tools/benchmark.py`：32 条任务 / 10 类扰动、固定种子（默认 42）、不联网不调模型，`LearningAdapter` 做「学习前 / 学习后」两态对照，输出完成率、提升百分点、被救回任务 id 与失败原因分类。2026-11-15 实测：学习前 56.25%（18/32）→ 学习后 81.25%（26/32），提升 25.00 个百分点，8 条任务被真实规则/偏好救回（C19~C26），仍如实保留 6 条硬失败样本（缺参、恶意动作、计划冲突、无法换词的零重合资料）。可用 `--json` / `--append-history` / `--threshold` / `--seed` 调口径，阈值默认 0.80，退出码 0=放行、1=卡口。这是离线确定性链路的能力抽样，不等同于联网或真实大模型表现；与 pytest 单测互补，用来量化「进化是否真的有用」。

端到端接线由 `tests/test_adaptive_integration.py` 的 8 项测试和 `tools/check_contracts.py` 的「可验证执行（L1/L2）」检查组覆盖；契约检查共 34 项，实测 34/34 通过。

## 参考

- 实现：`framework/preference.py`、`framework/evolution.py`、`framework/trajectory.py`
- 接线：`framework/core.py` 的 `send_command` / `_learn_preferences` / `_adjust_action` / `_traj_end` / `_evolve`、`framework/ai_engine.py` 的 `prefs_text`
- 验证：`tools/benchmark.py`、`tests/test_adaptive_integration.py`、`tools/check_contracts.py`
