# ADR 0002：技能层只收确定性动作，重放必须零模型调用

- 状态：已接受
- 日期：2026-11-15
- 相关代码：`framework/skill_store.py`、`framework/core.py`（`_offer_skill` / `_do_save_skill` / `_do_run_skill` / `_run_skill_named` / `_replay_skill`）、`tests/test_skill_store.py`、`tools/test_agent_features.py`
- 相关 ADR：[0001 撤销按世界快照求差集](0001-undo-by-world-diff.md)

## 背景

「一次学会」的流程是：用户做成一件事 → 说「存成技能」→ 程序把这一轮真实执行过的动作序列抽成骨架存下来 → 下次说技能名，直接把步骤插进动作队列执行，不再重新出计划。

这条链路要成立，必须先回答「什么样的动作序列值得被存下来并原样重放」。如果把任何一轮轨迹都存成技能，重放就会变成「把上次的模型输出再播一遍」：上次生成的文档内容、上次的计划卡、上次顺手说的话都会被当成固定流程复读。那不叫学会，那叫复读。

还有一个和 [ADR 0001](0001-undo-by-world-diff.md) 同源的约束：撤销与技能这两条快路径都承诺在离线、无模型、API 欠费时可用。所以「重放有没有偷偷问模型」不能只是设计意图，必须能被机械地证明。

## 决策与理由

### 1. 只收 10 个确定性安全动作

`SKILL_SAFE` 是一张白名单，只有 10 个动作：

```
set_volume, open_app, web_search, read_doc, analyze_data,
todo_add, todo_done, remind, remember, screenshot
```

判据是「参数完全由用户这次说的话决定」。这些动作的结果是可复现的：给同样的路径就读同样的文件，给同样的文本就加同样的待办。于是同一条技能 + 同一组槽位值，重放出来的动作序列是一样的，技能才配叫「固定流程」。

表演性动作（`THEATRE`：think / idle / jump / move_to / show_bubble / show_card / search_app / todo_list / work_log / see_screen）不进技能，因为它们只改姿态和界面，重放只会让流程变慢，没有业务含义。会动真机器的 `shutdown` / `restart` / `lock` 明确拒收：重放一次就动一次真机器。`show_plan` / `run_workflow` / `cancel_shutdown` 也拒收——计划是当时那件事的计划，工作流会另起一整轮任务，取消关机只在有排程时才有意义。

白名单是「默认拒绝」的：不在表里的动作一律不收，而不是「先收了再说」。

### 2. 拒收 `create_doc`

`create_doc` 被单列进 `SKILL_REFUSED`，理由写在表里：

> 那份文档的内容是我上次替你写的，原样重放等于把旧报告再发一遍。

文档工厂的输入（`doc_type` 之外的章节、要点、表格数据）来自模型当时基于资料缓冲生成的内容，不由用户这次的话决定。把它抽成技能、下次重放，等于把一份旧报告换个名字再生成一次，用户会误以为拿到的是新东西。同理，凡是「内容由模型产出」的动作都不适合进确定性重放。

拒收时不是静默跳过：`skills.add()` 返回拒绝原因，气泡如实说「这个存不成技能：……」，用户知道为什么没学会。

### 3. 缺任一槽位整条拒跑

抽槽靠一张确定表 `SLOT_BY_KEY`（path / file / save_path / filename → 路径，app → 应用，query / text / fact / title / name → 主题），不靠模型判断「看起来像不像」。重放时 `match_values()` 用每类槽的正则从用户新说的这句话里抠值，**抠不到就不给，不猜**。

`fill(skill, values)` 返回 `(steps, missing)`。只要 `missing` 非空，整条技能不跑，`_run_skill_named` 回一句「「X」还差 主题，我不拿上次的值瞎补——告诉我再跑」。这里刻意不做的两件事：

- 不用上次存下来的值补空缺（那就把「新任务」静默变成了「旧任务」）；
- 不跑半截（跑一半才说参数不够，世界已经被改了一部分，比整条不跑更难收拾）。

`_replay_skill` 还要求这句话里逐字叫得出技能名；名字对不上或槽抠不齐，就交回正常流程让模型去问，而不是硬走快路径。另外 `SKILL_SLOT_MAX` 会拦下槽位过多的轨迹——要现填的参数多到这份上，说明流程根本不固定，不该存成技能。

### 4. 重放必须零模型调用

理由有三条，按重要性排：

1. **可复现性**：技能是「固定流程」的承诺。一旦重放经过模型，同一技能 + 同一组槽位值可能产出不同步骤，承诺就不成立了，也没法对「学会了什么」做断言。
2. **可用性**：桌面版默认 `USE_REAL_AI=False`，且真实模型可能欠费、断网、超时。撤销与技能是产品承诺的离线可用路径，重放若依赖模型，这个承诺就是空的。
3. **可证明性**：零模型调用是一条能被机械检查的性质。纯本地代码路径可以用 import 拦截器和假件验证「确实没碰 AI 层」；一旦路径里混入模型调用，就只能靠口头声明「应该没调」。

实现上，重放是纯本地的：`skills.get(name)` → `match_values()` → `fill()` → 把 `steps` 插进 `_action_queue`，全程不启动 AIWorker。技能层模块本身也不许 import Qt / requests / pandas 等重依赖，保证它能被纯单测覆盖。

## 怎么证明

用两种手段，一个管「静态 import 与运行期 import」，一个管「整条链路」。

### import 拦截器（`tests/test_skill_store.py`）

`test_filling_touches_no_ai_layer` 不只扫源码的 import 列表（运行时也可能动态 import），而是临时往 `sys.meta_path` 头部插一个 finder：

```python
blocked = ("framework.ai_engine", "framework.multi_agent")

class _Blocker:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(blocked):
            raise AssertionError(f"技能重放里出现了 AI 层：{fullname}")
        return None
```

它先把 `sys.modules` 里已加载的 AI 层模块删掉，再在抽槽 + 填槽这段区间里装拦截器：谁 import `framework.ai_engine` 或 `framework.multi_agent`，当场抛 `AssertionError`。断言覆盖 `abstract()` + `fill()` 全过程，而不只是模块加载时刻。另有 AST 检查确认 `skill_store` 源码顶层没有 import Qt / requests / pandas。

### 一调用就 raise 的假 AIWorker（`tools/test_agent_features.py`）

集成自检把 `framework.core` 里的 `AIWorker` 整个换掉：

```python
_saved_ai = core_mod.AIWorker

def _boom(*a, **k):
    raise AssertionError("快路径请了模型")

core_mod.AIWorker = _boom
```

在这个假件生效期间跑完整重放（以及撤销、短指令快路径）：动作照样执行、待办照样增删、结果照样落盘。只要链路上有任何一处去请模型，假件立刻抛异常，自检红。自检第九节同时断言「每个动作的可逆性都归了类（没有未知项）」和「撤销动作自己不再记账」，与 [ADR 0001](0001-undo-by-world-diff.md) 的分类账共用同一份 `coverage(VALID_ACTIONS)`。

这两个手段合起来覆盖的是不同层次：拦截器证明技能层内部不碰 AI 层，假 AIWorker 证明接线层（core.py 的快路径）也不碰模型。`tools/test_agent_features.py` 需手动运行，结果 157 通过 / 0 失败，不在 pytest 收集范围内；CI 会把它和 pytest 单测、契约检查、评测集一起跑。

## 后果

- 技能覆盖面是有限的：只有 10 个动作能进技能，含 `create_doc` 的流程存不下来，这是有意的取舍，不是待补的缺口。
- 重放与撤销天然兼容：`run_skill` 展开出来的动作造成的效果落在 ADR 0001 的五个可观测面里，撤销读的是世界快照而不是动作清单，所以不需要为技能重放写任何特殊回退逻辑。
- 快路径的词表（撤销 / 存技能 / 技能列表 / 技能直放）必须不撞车、门限自洽，由 `test_fast_intent.py` 钉住；否则一句普通闲聊可能被误判成技能重放。
- 技能名取「那件事的原话」，不是「存成技能」这句，用户下次得用同一句话叫它——这是重放零模型的代价：没有模型来帮忙做模糊匹配。
