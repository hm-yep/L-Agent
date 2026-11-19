# ADR 0005：能力开关运行期可翻、关掉就把子系统撤场、状态必须落盘

- 状态：已接受
- 日期：2026-11-17
- 相关代码：`framework/config.py`（`CAPABILITY_KEYS` / `apply_user_settings` / `save_user_settings`）、`framework/core.py`（`CAPABILITY_STORES` / `_open_capability` / `set_capability`）、`ui/workspace_window.py`（`SWITCH_ROWS` / `_build_switch_rows` / `_switch_row` / `_paint_switch` / `_toggle_switch`）、`ui/theme.py`（`QPushButton#switchToggle`）、`tests/test_workspace_window.py`、`tools/check_contracts.py`、`tools/test_settings_persistence.py`、`docs/QUICK_START.md` 断言 9
- 相关 ADR：[0001 撤销按世界快照求差集](0001-undo-by-world-diff.md)、[0003 偏好与自我进化](0003-preference-and-evolution.md)、[0004 可验证执行](0004-verifiable-execution.md)

## 背景

关于页有一张「当前开关」卡片，列着事件轨迹 / 偏好学习 / 自我进化 / 计划依赖图 / 契约严格模式五行。它原本的形态是：每行右侧一个带边框圆角的小 `QLabel`，写着「开启」或「关闭」。用户第一句话是「这几个按钮是摆设」。

这个指控有两层，缺层都会留下同一个缺陷：

1. **长相在撒谎**。带边框的胶囊 + 与可点按钮一致的配色，在界面上的读法就是「这是个能点的东西」，而它是一块只读文本。affordance（看起来能不能点）与 effect（点了有没有用）必须一起对，只对一半仍然是摆设。
2. **接了线也可能是摆设**。这五个开关背后是 `framework/config.py` 的布尔量，把 `QLabel` 换成 `QPushButton` 只解决第一层。第二层的坑是：一个能力「开」与「关」在代码里并不只有一种读法——调用点有的每次读 `config.X`（`PLAN_DAG_ENABLED`、`EFFECT_SPEC_STRICT` 都是这种），有的只看对象在不在场（`_traj_event` 只判 `self.trajectory is None`，`web_search` 的偏好闸门只判 `self.prefs is not None`）。如果翻牌只改 `config.X`，那么第二类调用点根本不看这个牌子，关掉的开关还在跑——界面显示「关闭」而程序仍在记录轨迹，这比不接线更糟。

还有一个和 [ADR 0002](0002-skill-replay-zero-llm.md) 同源的约束：界面说的话必须能被机械检查。卡片副标题写着「立即生效，并写进本机 user_settings.json，下次启动读回同样的值」，这三句（立即生效 / 写盘 / 读回）任何一句做不到，这句话就是新的摆设。

## 决策与理由

### 1. 开关只收「调用点每次重读」的能力，共 5 个

`ui/workspace_window.py` 的 `SWITCH_ROWS` 是一张确定表：`(显示名, config 属性名, 这行小字)`。入表判据两条，缺一不可：

- **调用点每次都重读 `getattr(config, X)`**，不是 import 时抓一次值。满足这条才敢说「立即生效、不用重启」。
- **翻动它不会造成不可逆后果**。所以 `USE_REAL_AI`、API Key、`ALLOW_ALL_APPS` 这类不进这张卡：前者在设置面板里有各自的真实控件与更重的确认语义，后两者是安全边界，不该做成关于页上一点就翻的胶囊。

不在表里的 config 项一律没有开关，而不是「先给个按钮再说」。

### 2. 关掉 = 把子系统对象当场撤场，而不是给每个调用点补 flag 判断

`framework/core.py` 的 `CAPABILITY_STORES` 声明了三个开关背后要实例化的子系统：

```
TRAJECTORY_ENABLED  → self.trajectory  (framework.trajectory.TrajectoryStore)
PREFERENCE_LEARNING → self.prefs       (framework.preference.PreferenceStore)
EVOLUTION_ENABLED   → self.rules       (framework.evolution.RuleStore)
```

`set_capability(attr, on)` 的语义是：开 → `_open_capability()` 当场 `importlib` 建对象（建不起来只置 `None` 降级，不拖垮主链路）；关 → `setattr(self, slot, None)` 当场把对象撤掉。另两个开关（计划依赖图、契约严格模式）不建对象，只控制调用点分支，`CAPABILITY_STORES.get(attr)` 拿不到槽位就只翻 flag。

**为什么不选另一条路**（在每个调用点补 `and config.X`）：

- 调用点数量是会长大的，漏改一处就出现「显示关闭、实际在跑」，而且这种缺陷只在行为层，界面上看不出来；
- 「对象不在场」是一个可证的收敛判据——测试只要断言 `fw.trajectory is None` 与 `fw._traj_event(...) is None`，不需要穷举调用点；
- 它顺着本项目已有的写法：这些子系统在构造期本来就允许是 `None`（能力缺失时的降级路径），运行期关闭复用同一条降级路径，不新增分支语义。

代价要如实说：这条决定让「`self.X is not None`」与「`config.对应开关为真`」两者等价，等价性是靠 `set_capability` 单点维护的。以后谁新增子系统对象在别处赋值，就得同时保证它不与 flag 分离——`tools/check_contracts.py` 里那 6 条开关契约守的就是这条链的接线，而不是等价性本身。

### 3. 必须落盘，而且「有没有存上」要能返回

`config.save_user_settings()` 原来无返回值（只写文件）。现在返回 `bool`，`set_capability` 据此决定怎么向用户交代：

- 写成功 → 「已开启（已存盘，下次启动同样）」，状态栏绿色；
- 写失败 → **整个动作退回原样**：`config` flag 复原、子系统对象也复原（连带的哨兵对象都要恢复，否则界面显示关闭而对象还在场），并显示「开关已改但没写进配置文件（看 logs）」。

第 2 条是这次唯一要求「失败也要正确」的地方。开关这类控件最容易出的假话不是「没反应」，而是「反应了但没记住」：用户点完以为关了，下次启动又自己开着。所以 `apply_user_settings()` 用同一张 `CAPABILITY_KEYS` 表把 5 个键读回来，落盘与读回共用一份键名，不允许两边各写各的字符串。

### 4. 连 `clicked`，不连 `toggled`

`_paint_switch()` 每次刷新都会 `setChecked(on)`——程序自己改状态。如果接线接在 `toggled` 上，一次重绘就会被当成用户操作写盘，翻页即改配置。接 `clicked`（只由真实点击发出）把「重绘」和「操作」分开。

### 5. 开关行只建一次，刷新时原地改文字

第一版实现用 `takeAt()` + `deleteLater()` 重建五行。这在 Qt 的 object tree 上有坑：`deleteLater` 要等事件循环跑一轮才真正销毁，`findChildren(QPushButton)` 期间仍能数到旧按钮，测试直接断出「10 个按钮」。改成 `_build_switch_rows()` 建一次、`_refresh_about()` 只调 `_paint_switch()`：既没有「看不见但点得着」的重复控件，也让按钮状态永远从 `config` 现读，而不是上一次点击的记忆。

### 6. 可点的样子由 `ui/theme.py` 给，只读标签不许冒充控件

读只读的胶囊 `QLabel#statChip` 与真按钮 `QPushButton#switchToggle` 是两套样式：后者有实心底、`hover`/`pressed` 变色、`min-width: 62px`、手型光标，开启=绿（`role="good"`）/关闭=红（`role="bad"`）。样式仍只由 `ui/theme.py` 下发（全仓只允许它调 `setStyleSheet`，这条已有测试钉住）。

## 怎么证明

| 层次 | 手段 | 断言 |
|---|---|---|
| 静态接线 | `tools/check_contracts.py`（CI 步骤，59/59） | 5 行的属性名都是真实 config 属性且都在 `CAPABILITY_KEYS` 里；界面用 `setObjectName("switchToggle")` + `setCheckable(True)` 且没有 `QLabel("开启")`；连的是 `clicked` 不是 `toggled`；`set_capability` 里同时有 `_open_capability`、`save_user_settings(**{key: bool(on)})` 与失败回滚；`apply_user_settings` 按表读回 |
| 单元行为 | `tests/test_workspace_window.py` 6 项 | `test_switches_are_buttons_not_readonly_chips`（是按钮不是只读标签）、`test_clicking_a_switch_flips_config_and_persists`（翻 config + JSON 落盘 + 状态栏文案，写盘走 `tmp_path` 的假 `_user_settings_path`，绝不碰真实 `%APPDATA%`）、`test_switch_changes_behavior_without_restart`（开→对象在场；关→`fw.trajectory is None` 且 `_traj_event()` 当场返回 `None`）、`test_switch_rolls_back_when_saving_fails`（存不上就 flag 与对象一起回滚）、`test_capability_flags_reload_from_settings_file`（存—读回环） |
| 跨进程启动读回 | `tools/test_settings_persistence.py` 第 2.6 节 | 用临时 `APPDATA` 起子进程写 5 个键，再 `apply_user_settings()`，逐个比对 `getattr(config, attr)` |
| 真实窗口手动 | `docs/QUICK_START.md` 断言 9 | 7 步：点是按钮 → 状态栏出现「已存盘」→ `%APPDATA%\DesktopPet\user_settings.json` 里有对应键 → 切页/重启后仍是该状态 → 关掉事件轨迹后轨迹不再增长 → 开回来恢复；并检查反复切页不攒重复按钮 |

回归口径：`pytest -q` 434 passed / 1 skipped、契约 59/59、集成自检 157/0、能力评测 38/38、启动冒烟 35/35（2026-11-17 实测）。

## 后果

- 「关」的含义从此有了边界：关掉只是**停止新增**，不删已有数据。`trajectory.jsonl`、已学到的偏好、已归纳的规则都留在盘上，重新打开继续用；要清数据得走各自页面上的操作，开关不承担删除语义。
- 关掉「事件轨迹」会影响 [ADR 0004](0004-verifiable-execution.md) 的一条能力：**「回滚到任意轮」不可用**（`rollback_to` 读的是 `turn_end` 事件里的撤销账本，`self.trajectory is None` 时如实返回「轨迹未启用」）。但 [ADR 0001](0001-undo-by-world-diff.md) 的「撤销上一轮」不受影响——世界快照求差集只由 `config.UNDO_ENABLED` 控制，走的是内存里的 `_world_before` / `_last_undo`。这是有意的：撤销是安全阀，不该被一个演示类开关顺手关掉。
- 界面文案成了可失败项。卡片副标题那三句「立即生效 / 写进本机配置 / 下次启动读回」现在都有对应断言，改文案时若砍掉能力，契约或用例会红——这是想要的耦合方向。
- 五个开关是白名单而不是通用反射。以后新增能力开关要先过第 1 节那两条判据（调用点每次重读、翻动不可逆风险低），过不了就不该有按钮；这条判据优先于「界面上多一个开关看起来更完整」。
