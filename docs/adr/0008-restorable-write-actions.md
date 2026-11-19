# 0008 · 写动作的可还原性分类（副本先落，原件只读）

- 状态：已采纳（2026-11-17）
- 相关：`framework/effect_spec.py`、`framework/effect_journal.py`、`framework/core.py`、`framework/action_registry.py`、`ui/pet_window.py`、[ADR 0001](0001-undo-by-world-diff.md)、[ADR 0004](0004-verifiable-execution.md)

## 背景

原来的撤销账本只分「可逆 / 无副作用 / 明确不可逆 / 撤销自身」四类。这个划分对读书写字的动作够用，但接上 `edit_doc` 之后就不够诚实了：

- 它**不是**「纯内存可逆」：`create_doc` 那种逆操作是 `delete_file`，而用户改的是自己的原始 PPT / Word，删掉原件不叫撤销，叫事故；
- 它**也不是**「明确不可逆」：只要动手前先把原件复制下来，就有确定的逆操作可走；
- 如果硬塞进 `reversible`，`effect_spec` 会期待一个纯内存逆操作，测试里就会把「能不能撤得回来」和「逆操作从哪来」混为一谈。

用户右键的「修改文件（PPT/Word）」此前只有 UI 入口，模型自己发不出这个动作——等于界面上摆着一个模型永远走不到的能力。F4 要让它成为模型可达的 `edit_doc`，同时必须给「这一类的副作用怎么收回来」一个明确分类。

## 决策

### 1. 在撤销账本里单列 `restorable`

`effect_journal.coverage()` 现在分五类：`reversible` / `restorable` / `blind`（明确不可逆）/ `no_effect` / `undo`。

- `reversible`：逆操作由世界快照差集直接算出（删掉新建文件、把音量调回原值）；
- `restorable`：**逆操作来自动手前亲手留下的副本**，不是从差集里现推；
- `blind`：不可逆，且当场明说「我不替你关窗口 / 不替你关电源」；
- `no_effect` / `undo`：不改动世界 / 撤销自身。

`RESTORABLE = {"edit_doc"}` 是这一类唯一的成员。`restorable` 在契约上仍满足 `reversible=True`——它确实撤得回来；区别在于逆操作的**来源**，所以单列一类而不是改 `reversible` 的语义。

### 2. 原件只读，写前先落副本

`core._do_edit_doc()` 的顺序被固定为：

```
校验 path / instruction → 三态路径授权 → 复制到 data/doc_backups/<原名>-<毫秒时间戳><后缀>
                        → 记入 _last_undo（file_edit_item）→ 才允许提取全文、发给模型
```

副本失败就**整个动作停下**，并如实说「没能给原件留好副本，先不改这个文件」。绝不出现「副本没落上但改写已经发出去」这种事后无法还原的局面。

改写结果继续走已有的 `_apply_doc_edit_result()`：输出 `原名_修改.ext` 新文件，原件一个字节都不动。副本只用于撤销账本，不是「覆盖回去就完事」的许可。

### 3. 模型可达，但参数不全就不发

`edit_doc` 进入 `VALID_ACTIONS` 与系统提示词；`instruction` 被解析器容错为 `content` / `要求` / `怎么改` 等写法。用户没说清「改哪个文件」时，模型应先问，不许猜路径。路径仍然走三态授权：根外路径弹授权卡，敏感项（`.env`、`user_settings.json`、凭据）点什么都不改。

### 4. 动作数一次改齐，而不是靠文档各自猜

代码里实际动作数是 **32**（30 个原有动作 + `list_dir` + `edit_doc`）。`effect_spec.expected_count`、动作注册表测试、契约检查、README / 产品功能介绍 / QUICK_START / 架构说明 / ADR 0001 / ADR 0004 全部统一到 32。远端 README 写 27、简历写 26+、面试手册另有一版，这些口径不在本仓库内；这次只保证本仓库「一处数字、处处相同」，避免下一个「说了做不到」。

## 为什么这样设计

- **可还原性来自证据，不来自承诺**：副本文件真实存在，撤销账本里记着它的路径，所以「能还原」是可检查的事实；只说「我会保留原件」不算。
- **分类服务于提示与验收**：`blind` 的动作会在气泡里当场说明撤不回；`restorable` 的动作在明细里可以被指认到副本。分类改了，测试与契约跟着红，而不是靠文案自我感觉良好。
- **不放宽原件只读这条硬规则**：即使有副本，`edit_doc` 也永不覆盖原件。副本只给「撤销」这条退路，不给「原地改」开绿灯。

## 代价与边界

- 每次改写都会在 `data/doc_backups/` 多留一份副本，随 `data/` 由 `.gitignore` 排除；目前没有自动清理策略，长时间大量改写会占空间。
- 副本只保住「文件内容」，保不住文件被其他程序同时打开时的锁状态或外部引用。
- 撤销 `edit_doc` 会把副本内容写回原路径；如果用户在两次操作之间手动改过原件，那次手动改动会被覆盖——撤销账本只保证「回到本轮动手前」，不保证合并用户后续编辑。

## 验证

- `tests/test_effect_spec.py` / `tests/test_effect_journal.py`：32 个动作契约完备；`edit_doc` 归到 `restorable` 且 `reversible=True`；五类分桶不重不漏。
- `tests/test_action_registry.py`：动作全集数量与必填参数一致。
- `tools/check_contracts.py`：`edit_doc` 在执行分发、系统提示词、副作用契约与 `RESTORABLE` 集合中都能找到；`core._do_edit_doc` 里 `copy2` 先于 `_send_doc_edit`，且副本失败有停下分支。
