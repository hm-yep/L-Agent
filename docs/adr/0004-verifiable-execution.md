# ADR 0004：从「每轮首尾快照」升级为「事件溯源」的可验证执行

- 状态：已接受
- 日期：2026-11-15
- 相关代码：`framework/trajectory.py`、`framework/effect_spec.py`、`framework/plan_graph.py`、`framework/core.py`（`_begin_turn` / `_review_effects` / `_run_next_action` / `_traj_end` / `rollback_to` / `_plan_replan` / `_do_show_plan`）、`tools/trace_view.py`、`tests/test_adaptive_integration.py`
- 相关 ADR：[0001 撤销按世界快照求差集](0001-undo-by-world-diff.md)、[0003 偏好与自我进化](0003-preference-and-evolution.md)

## 背景

[ADR 0001](0001-undo-by-world-diff.md) 用「开工拍快照、收口求差集」解决了一轮之内「到底改了什么」，判据只有 `effect_journal.py` 一份，离线可单测。但它只保留「上一轮」：`_last_undo` 撤销一次后即销账，没有跨轮历史。

升级的动因是四个首尾快照回答不了的问题：

1. 「第三轮到底干了什么」——没有按轮归档的事件，只有一份会被覆盖的账本；
2. 「回到三轮之前」——`undo_last` 只知道上一轮，无法指定目标轮；
3. 「凭什么相信这次撤销是对的」——除了「撤完再求一次差集自证」，没有可回放的哈希链与前后状态指纹；
4. 「失败 / 质量门 / 撤销发生过几次」——`framework/evolution.py`（见 [ADR 0003](0003-preference-and-evolution.md)）需要结构化的问题事件做归纳，快照里没有这些事件。

换句话说，L1「可验证执行」要的不只是「能撤销」，还要「能回放、能指认、能回滚到任意轮、能证明」。这四件事都要求把一轮内的过程记下来，而不只是记首尾两个状态。

## 决策

### 1. append-only 事件流 + 哈希链

`framework/trajectory.py` 把 `data/trajectory.jsonl` 作为 append-only 事件流，一行一个事件。事件字段为 `turn_id` / `seq` / `event_id` / `ts` / `kind` / `source` / `action` / `payload` / `pre_hash` / `post_hash` / `duration_ms` / `tokens` / `parent_id` / `ok` / `note`（实现里另有 `schema` 与 `payload_digest`）。`KINDS` 是十类：`turn_start` / `action` / `action_result` / `plan` / `failure` / `quality` / `undo` / `preference` / `rule` / `turn_end`。

链有两层，不要混为一谈：

- **事件链**：`event_id` 形如 `{turn_id}#{seq}`，`parent_id` 指向上一条事件的 `event_id`；
- **状态链**：`turn_start` 记 `pre_hash`，`turn_end` 记 `post_hash`，两者都是 `world_digest()` 对五面世界状态（音量 / 待办 / 提醒 / 交付 / 长期记忆）用 `effect_journal.normalize` 归一后求的指纹；`chain_ok()` 校验「上一条显式 `post_hash` 与下一条显式 `pre_hash` 接得上」，只校验显式给了哈希的事件，很多不改世界的事件允许缺省。

`TrajectoryStore` 提供 `start_turn` / `append` / `end_turn` / `events` / `turns` / `rollback_ledgers` / `chain_ok` / `summarize` / `clear`；读侧容忍半行损坏（坏行跳过），写侧加锁，JSONL 追加不改写历史。

接线在 `framework/core.py`：`_begin_turn` 开轮并记 `pre_hash`，`_review_effects` 用 try/finally 收轮写 `post_hash` 与 `undo_ledger`，`_run_next_action` 每个动作写 `action` 事件；`_note_plan_failure` / `_handle_search_quality` / `_doc_quality_followup` / `_do_undo_last` / `_on_ai_failed` 分别写 `failure` / `quality` / `undo` 事件；`_traj_end` 顺带触发规则归纳。

### 2. 任意轮回滚

每轮的撤销账本随 `turn_end` 事件一起落盘，于是回滚不再受「只存上一轮」限制。`TrajectoryStore.rollback_ledgers(turn_id)` 从目标轮的 `turn_start` 起，收集其后所有 `turn_end` 的账本并逆序返回；`core.rollback_to(turn_id)` 逐轮调 `effect_journal.undo_ops` + `apply_ops` 执行回退，并写一条 `undo` 事件。动作 `undo_last` 支持 `to` 参数指定目标轮，也可直接调用 `fw.rollback_to(turn_id)`。

这里复用了 [ADR 0001](0001-undo-by-world-diff.md) 的判据：账本仍是世界快照求差集得到的，不需要为每个动作手写 undo；事件溯源只是把「只留一份」变成「按轮留全部」。

### 3. 副作用契约与「撤销是对合」的证明

`framework/effect_spec.py` 给 30 个动作各写一份显式副作用契约：`observables` / `reversible` / `idempotent` / `inverse` / `pre` / `post` / `reason`。`validate_specs()` 与 `action_registry` / `effect_journal` / `REQUIRED_PARAMS` 交叉校验，新增动作漏写契约会在契约检查里失败；`check_pre` / `check_post` 是运行时断言，默认只记录不阻断（`EFFECT_SPEC_STRICT=False`）。

`involution_check()` 是属性测试，不是示例：用 `random.Random(seed)` 的固定种子生成随机世界与随机可撤动作序列，用纯假手执行 `apply_ops(inverse_ops(diff(before, after)), api)`，逐组用 `world_digest` 校验回退后的状态哈希与 `before` 一致，返回 `{"trials", "passed", "failed", "seed"}`；`failed == 0` 就是「撤销是对合」的可复现证据。它不碰真手、不联网、不调模型，因此能进单测与契约检查。

### 4. 计划 DAG 与增量重算

`framework/plan_graph.py` 把线性计划编译成 DAG：默认顺序依赖保证拓扑序等于原顺序，「基于 / 据此 / 汇总」这类词额外加数据依赖。`validate()` 静态校验无环、动作合法、不可逆动作需确认；`topological_order()` 是稳定的 Kahn（同层按原序）；`affected_subgraph()` 只返回失败节点及其传递后继。

`_do_show_plan` 编译依赖图并写 `plan` 事件；`_plan_replan` 用 `affected_subgraph()` 只调整受影响子图，不再整轮推倒。这把「重规划」从「把剩余步骤重问一遍模型」收敛成「按依赖图定位受影响范围」，也让失败事件有了可指认的节点。

### 5. 时间线与脱敏

`tools/trace_view.py` 把事件流渲染成单文件可交互时间线 HTML：按轮分组、哈希链、失败高亮、「只看失败」过滤、深浅色自适应、HTML 转义、无 CDN。撤销账本只显示数量、不显示明细——回滚需要账本原文，展示层不复制它。命令：

```bash
python tools/trace_view.py --trace data/trajectory.jsonl --out timeline.html
```

## 后果与代价

- **多三个落盘文件**：`data/trajectory.jsonl`、`data/preferences.json`、`data/rules.json`。`data/` 整体由 `.gitignore` 排除；三者都可用 `LINGLING_DATA_DIR` 覆盖 `framework.utils.app_data_dir()` 隔离到临时目录，`tests/conftest.py`、`tools/test_agent_features.py` 与 CI 都设了它，测试不写脏真实用户数据。
- **每轮多几次状态读取**：开轮、收轮、每个动作都要读一次五面世界状态求哈希并追加事件；这些调用都在 try/except 内，任一模块异常只降级、不影响原有动作链，代价主要是本地磁盘追加与几次 JSON 读写。
- **隐私要脱敏**：`sanitize()` 丢掉敏感键（`api_key` / `token` / `password` / `secret` / `authorization` / `raw_text` / `user_text` / `message` / `content` / `headers`），路径只留 basename，字符串截断到 240 字；用户原话只以哈希出现，时间线不展示撤销账本明细。事件流里存的是结构化字段，不是对话原文。
- **可验证的边界**：哈希链校验的是「显式给了哈希的事件之间前后接得上」，不是对整条事件流做密码学防篡改；`EFFECT_SPEC_STRICT` 默认关闭，运行时契约违约只记录不阻断。把这两条写清楚，比笼统地说「可验证」更有用。

## 可验证方式

- `python tools/trace_view.py --trace data/trajectory.jsonl --out timeline.html`：按轮查看事件、哈希链与失败高亮；
- `python tools/check_contracts.py`：41 项契约检查（含「30 个动作的副作用契约完备」「轨迹/偏好/进化/计划图模块可用」「主链路接入 token」「三份文档的 pytest 数字一致」），实测 41/41 通过；
- `pytest -q`：401 passed / 1 skipped，其中 `tests/test_adaptive_integration.py` 覆盖端到端接线 8 项；
- `python tools/benchmark.py`：学习前后对照，见 [ADR 0003](0003-preference-and-evolution.md)；
- `python main.py --selftest`：启动冒烟 35/35 通过。

## 参考

- 实现：`framework/trajectory.py`、`framework/effect_spec.py`、`framework/plan_graph.py`
- 接线：`framework/core.py` 的 `_begin_turn` / `_review_effects` / `_run_next_action` / `_traj_end` / `rollback_to` / `_plan_replan` / `_do_show_plan`
- 验证：`tools/trace_view.py`、`tools/check_contracts.py`、`tests/test_adaptive_integration.py`
