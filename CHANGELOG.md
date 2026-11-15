# Changelog

本文件按 Keep a Changelog 风格维护，每个版本条目分「新增 / 修复 / 变更 / 安全」四段，只记录与当前仓库状态对应的变更。

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
- 测试口径区分：`pytest -q` 200 passed / 1 skipped / `python tools/test_agent_features.py` 157 通过 0 失败（需手动运行，不在 pytest 收集范围）/ `python main.py --selftest` 启动冒烟（35/35，离屏环境跳过 2 项依赖真实屏幕几何的布局断言，退出码不再恒为 1），不再混称「测试套件」。
- Docker 镜像明确只跑 API 服务（`uvicorn api.server:app`），不运行桌面 UI。
- [产品功能介绍.md](产品功能介绍.md) 第十节自评表权重合计修正为 100%，加权分按表实算为 88.68%（约 89%），并写明算法。

### 安全

- API 鉴权：访问令牌从 `LINGLING_API_TOKEN` 读取，未配置时只接受本机回环请求；CORS 白名单由 `LINGLING_API_ORIGINS` 配置。
- `framework/path_guard.py`：本机文件读取白名单，限制可读路径范围。
- 日志脱敏：运行日志不落明文密钥。
- Key 不入库：`DEEPSEEK_API_KEY` 仅从环境变量或设置界面读取；`data/` 运行时产物继续由 `.gitignore` 排除。
