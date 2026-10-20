# 凌零 · 桌面 AI 数字员工（Agent）

一个可服务化的桌面 AI Agent，支持自然语言 → 结构化计划 → 用户确认 →
自动执行 → 文件交付的端到端闭环。

![六姿态立绘总览](docs/preview/01_六姿态安装总览.png)

## ✨ 核心能力
- **自定义 Function Calling 协议**：27 动作池，覆盖系统控制、文档生成、
  数据分析、联网检索、长期记忆、任务规划六大类
- **Plan-and-Execute + Human-in-the-loop**：15 轮预算 + 2 次重规划上限
- **Quality Gate 软失败治理**：识别"API 成功但结果不可用"，自动换词重搜 /
  内容重写 / 降级
- **多模态视觉理解**：GLM-4.6V 看屏幕，截图 → 压缩 → 编码 → 注入上下文
- **RAG 检索增强**：LangChain + Chroma + BGE 中文嵌入，本地知识库问答
- **FastAPI 服务化**：/chat、/ingest/text、/ingest/file、/search、/voice、/agent/run
- **语音输入**：Whisper 语音转文字
- **多智能体编排**：Planner → Executor → Reviewer 三角色协作
- **LoRA 微调**：PEFT + Qwen2.5-1.5B，领域指令微调
- **工程化**：QThread、看门狗、退避重试、pytest 单元测试、Docker

## 🚀 快速开始

```bash
pip install -r requirements.txt

# 设置 API Key
# Windows:
set DEEPSEEK_API_KEY=你的Key
# Linux/macOS:
export DEEPSEEK_API_KEY=你的Key

# 桌面版启动
python main.py

# 桌面版 + API 服务一起启动（RAG 预热再加 --with-rag）
python main.py --api --with-rag

# 或单独启动 API 服务（服务未做鉴权，默认只监听本机）
uvicorn api.server:app --host 127.0.0.1 --port 8000
```

启动后浏览器打开 `http://127.0.0.1:8000/` 即为网页调试台，
Windows 下也可以直接双击 `启动助手.bat` / `启动API服务.bat`。

## 📁 目录结构

```
framework/   Agent 核心：动作解析、计划执行、质量门、RAG、系统控制
api/         FastAPI 服务层 + 网页调试台（api/static/index.html）
ui/          PyQt5 桌宠窗口、立绘与音效管理、计划卡片
assets/      立绘帧动画、音效、图标
tests/       pytest 单元测试
tools/       集成自检脚本与立绘处理工具
finetune/    LoRA 微调训练与推理脚本
docs/        立绘预览图
```

## ✅ 运行测试

```bash
pytest -q                              # 动作解析 / 质量门 / API / 数据分析
python tools/test_agent_features.py    # 动作池与执行链路集成自检
```

## 🔑 API Key

密钥只从环境变量或本机配置文件读取，仓库内不含任何真实密钥。
`data/`（向量库、日志、长期记忆）属于运行时产物，已在 `.gitignore` 中排除。

## 🐳 Docker

```bash
docker build -t lingling .
docker run -e DEEPSEEK_API_KEY=你的Key -p 8000:8000 lingling
```

## 📄 License

[MIT](LICENSE)
