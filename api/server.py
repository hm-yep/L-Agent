# -*- coding: utf-8 -*-
"""凌零 · 智能体 API 服务（RAG 子进程 + 与桌面版共享记忆/任务台）

与桌面版统一的部分：
  - 对话历史：共享 framework.memory_system.MemorySystem（data/memory.json）
  - 提醒待办：共享 framework.task_board.TaskBoard（data/tasks.json）
  - 知识库：共享 framework.rag_client（子进程）
  - 联网搜索：共享 framework.web_search
  - 人设：与桌面版一致的骑士凌零（输出格式不同：桌面走 JSON 动作，网页走纯文本）
"""

import json
import sys
import time
from pathlib import Path
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from framework import config
config.apply_user_settings()

from framework.web_search import web_search
from framework.quality_gate import assess_search, extract_keywords
from framework.utils import logger
from framework import rag_client
from framework.memory_system import MemorySystem
from framework.task_board import TaskBoard


app = FastAPI(title="凌零 · 智能体服务", version="1.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# 与桌面版共享同一份 JSON 文件
_memory = MemorySystem()
_tasks = TaskBoard()

# 会话 sid -> 起始索引（用于从 memory 中截取该会话的上下文）
_sessions: dict = {}


# ---------------- 请求/响应模型 ----------------
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    use_rag: bool = False
    use_search: bool = False
    top_k: int = 3


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    references: list = []
    elapsed_ms: int = 0


class IngestRequest(BaseModel):
    text: str
    source: str = "manual"


class IngestResponse(BaseModel):
    ok: bool
    chunks: int
    total: int


class SearchRequest(BaseModel):
    query: str
    count: int = 5


class VoiceResponse(BaseModel):
    text: str
    elapsed_ms: int = 0


class AgentRunRequest(BaseModel):
    task: str
    session_id: Optional[str] = None


class AgentRunResponse(BaseModel):
    session_id: str
    task: str
    steps: list
    results: list
    calls: int
    elapsed: float


class TodoItem(BaseModel):
    text: str


# ---------------- LLM 调用 ----------------
SYSTEM_PROMPT = """你是"凌零"——桌面AI助手/数字员工，形象是一位身披银甲、佩剑的Q版小骑士。

【身份规则】
- 你的名字是"凌零"，身份是桌面AI助手/数字员工
- 绝不自称"桌宠""宠物"或任何其他名字，也不把自己说成玩具
- 用户问"你是谁/你叫什么"时，回答："我是凌零，你的桌面AI助手。查资料、做文档、定提醒、管待办，都可以吩咐。"
- 骑士只是外在形象和说话风格，自我认知永远是：凌零·桌面AI助手

【回答规则】
- 用中文回答，简洁准确，语气自然像一位可靠的骑士伙伴
- 干脆利落、言出必行、有担当（例如"遵命""这就去办""任务完成""交给我"）
- 可以幽默，但禁止软萌语气词（呀/啦/哦/哟/嘛/嗯嗯）和波浪号"~"，不撒娇、不卖萌、不用可爱表情
- 用户问你性别：回答"我是AI助手，没有生理性别。形象是骑士，性别不重要，办事才重要。"

【资料使用规则】
- 如果提供了【参考资料·知识库】或【参考资料·联网】，优先基于资料回答
- 【绝对不要】在回答里写"来源：《xxx》片段N："这类原始片段引用
- 需要提来源时，自然说"根据我的知识库"或"根据联网资料"即可
- 【不要】使用 Markdown 加粗（**）或任何 Markdown 语法，用纯文本
- 资料没覆盖的部分可以用你自己的知识补充，但不要编造数据"""


def _chat_url() -> str:
    base = config.DEEPSEEK_BASE_URL.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def call_deepseek(messages: list, temperature: float = 0.7) -> str:
    if not config.DEEPSEEK_API_KEY:
        raise HTTPException(500, "未配置 DEEPSEEK_API_KEY")
    url = _chat_url()
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
                 "Content-Type": "application/json"},
        json={"model": config.DEEPSEEK_MODEL,
              "messages": messages,
              "temperature": temperature,
              "max_tokens": 8192},
        timeout=120,
    )
    if resp.status_code != 200:
        raise HTTPException(502, f"LLM 请求失败: HTTP {resp.status_code}")
    body = resp.json()
    msg = ((body.get("choices") or [{}])[0].get("message") or {})
    content = str(msg.get("content") or "").strip()
    if not content:
        content = str(msg.get("reasoning_content") or "").strip()
    return content


def _build_search_query(message: str) -> str:
    kws = extract_keywords(message or "")
    q = " ".join(kws[:8]).strip()
    return q or (message or "").strip()


def agent_chat(message: str, history: list, use_rag: bool,
               use_search: bool, top_k: int = 3) -> tuple:
    """主 Agent 文字对话（含 RAG / 联网检索）。"""
    references = []

    rag_context = ""
    if use_rag:
        try:
            hits = rag_client.search(message, top_k=top_k)
            if hits:
                rag_context = rag_client.format_context(hits, max_chars=2000)
                references += [{"type": "rag", "source": h.get("source", ""),
                                "score": h.get("score", 0),
                                "snippet": (h.get("content") or "")[:120]}
                               for h in hits]
                logger.info(f"[API] RAG 命中 {len(hits)} 条")
        except Exception as e:                  # noqa: BLE001
            logger.warning(f"[API] RAG 检索失败: {e}")
            references.append({"type": "rag_error", "message": str(e)})

    search_context = ""
    if use_search:
        query = _build_search_query(message)
        try:
            logger.info(f"[API] 联网搜索 query={query!r}")
            results = web_search(query, count=5)
            gate = assess_search(query, results)
            if gate["ok"] and results:
                search_context = "\n".join(
                    f"{i}. {r['title']}（{r.get('media','')}）：{r['content']}"
                    for i, r in enumerate(results, 1))
                references += [{"type": "search", "title": r["title"],
                                "link": r["link"]}
                               for r in results[:5]]
                logger.info(f"[API] 搜索命中 {len(results)} 条资料")
            else:
                references.append({"type": "search_warn",
                                   "reason": gate.get("reason", "结果不相关")})
                logger.warning(f"[API] 搜索质量不达标: {gate.get('reason')}")
        except Exception as e:                  # noqa: BLE001
            logger.exception("[API] 搜索失败")
            references.append({"type": "search_error", "message": str(e)})

    user_prompt = message
    if rag_context:
        user_prompt += f"\n\n【参考资料·知识库】\n{rag_context}"
    if search_context:
        user_prompt += f"\n\n【参考资料·联网】\n{search_context}"

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += history[-10:]
    messages.append({"role": "user", "content": user_prompt})

    reply = call_deepseek(messages)
    return reply, references


def _get_history(sid: str) -> list:
    """从共享 MemorySystem 里取该会话的上下文。"""
    start = _sessions.get(sid)
    if start is None:
        # 新会话：起点 = 当前 memory 长度
        start = len(_memory.history)
        _sessions[sid] = start
    return list(_memory.history[start:])


def _record(sid: str, user_text: str, reply: str):
    """写入共享 memory，桌面版也能看到。"""
    _memory.add("user", user_text)
    _memory.add("assistant", reply)
    if sid not in _sessions:
        _sessions[sid] = max(0, len(_memory.history) - 2)


# ---------------- 路由 ----------------
@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "static" / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>凌零 API 已启动</h1><p>请访问 /docs</p>")


@app.get("/health")
async def health():
    return {"status": "ok", "ts": int(time.time()),
            "rag_enabled": True,
            "memory_count": len(_memory.history),
            "todos_count": len(_tasks.open_todos()),
            "search_key_configured": bool(config.WEBSEARCH_API_KEY)}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    t0 = time.time()
    sid = req.session_id or f"s_{int(time.time()*1000)}"
    history = _get_history(sid)

    try:
        reply, refs = agent_chat(req.message, history, req.use_rag,
                                 req.use_search, req.top_k)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[API] chat 失败")
        raise HTTPException(500, f"处理失败: {type(e).__name__}: {e}")

    _record(sid, req.message, reply)
    return ChatResponse(session_id=sid, reply=reply, references=refs,
                        elapsed_ms=int((time.time() - t0) * 1000))


@app.post("/ingest/text", response_model=IngestResponse)
async def ingest_text(req: IngestRequest):
    if not req.text.strip():
        raise HTTPException(400, "text 不能为空")
    try:
        n = rag_client.add_text(req.text, source=req.source)
        total = rag_client.count()
    except Exception as e:  # noqa: BLE001
        logger.exception("[API] ingest_text 失败")
        raise HTTPException(500, f"入库失败: {type(e).__name__}: {e}")
    return IngestResponse(ok=True, chunks=n, total=total)


@app.post("/ingest/file", response_model=IngestResponse)
async def ingest_file(file: UploadFile = File(...)):
    tmp_dir = Path("./data/uploads")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dst = tmp_dir / (file.filename or "upload.bin")
    with open(dst, "wb") as f:
        f.write(await file.read())
    try:
        n = rag_client.add_file(str(dst))
        total = rag_client.count()
    except Exception as e:  # noqa: BLE001
        logger.exception("[API] ingest_file 失败")
        raise HTTPException(400, f"入库失败: {type(e).__name__}: {e}")
    return IngestResponse(ok=True, chunks=n, total=total)


@app.post("/search")
async def search_endpoint(req: SearchRequest):
    try:
        results = web_search(req.query, count=req.count)
    except Exception as e:
        raise HTTPException(502, f"搜索失败: {e}")
    return {"query": req.query, "results": results}


@app.post("/voice", response_model=VoiceResponse)
def voice_to_text(
    file: UploadFile = File(...),
    language: str = Form("zh"),
):
    t0 = time.time()
    logger.info(f"[API] 收到语音请求 /voice（{file.filename}）")
    try:
        from framework.voice_input import transcribe_bytes
    except ImportError as e:
        raise HTTPException(500, f"语音模块不可用: {e}")

    suffix = Path(file.filename or "voice.wav").suffix or ".wav"
    data = file.file.read()
    if not data:
        raise HTTPException(400, "音频文件为空")
    try:
        text = transcribe_bytes(data, suffix=suffix, language=language)
    except Exception as e:
        logger.exception("[API] 语音识别失败")
        raise HTTPException(500, f"识别失败: {type(e).__name__}: {e}")
    return VoiceResponse(text=text,
                         elapsed_ms=int((time.time() - t0) * 1000))


@app.post("/voice/chat", response_model=ChatResponse)
def voice_chat(
    file: UploadFile = File(...),
    session_id: Optional[str] = Form(None),
    use_rag: bool = Form(False),
    use_search: bool = Form(False),
    language: str = Form("zh"),
):
    t0 = time.time()
    logger.info(f"[API] 收到语音请求 /voice/chat（{file.filename}）")
    try:
        from framework.voice_input import transcribe_bytes
    except ImportError as e:
        raise HTTPException(500, f"语音模块不可用: {e}")

    suffix = Path(file.filename or "voice.wav").suffix or ".wav"
    data = file.file.read()
    if not data:
        raise HTTPException(400, "音频文件为空")
    try:
        text = transcribe_bytes(data, suffix=suffix, language=language)
    except Exception as e:
        raise HTTPException(500, f"识别失败: {type(e).__name__}: {e}")
    if not text:
        raise HTTPException(400, "没有识别到语音内容")

    sid = session_id or f"s_{int(time.time()*1000)}"
    history = _get_history(sid)
    reply, refs = agent_chat(text, history, use_rag, use_search, 3)
    _record(sid, f"[语音] {text}", reply)

    return ChatResponse(
        session_id=sid,
        reply=f"[识别] {text}\n\n{reply}",
        references=refs,
        elapsed_ms=int((time.time() - t0) * 1000),
    )


@app.post("/agent/run", response_model=AgentRunResponse)
async def agent_run(req: AgentRunRequest):
    from framework.multi_agent import get_orchestrator
    t0 = time.time()
    sid = req.session_id or f"s_{int(time.time()*1000)}"
    try:
        result = get_orchestrator().run(req.task)
    except Exception as e:
        logger.exception("[API] 多智能体失败")
        raise HTTPException(500, f"编排失败: {type(e).__name__}: {e}")
    _memory.add("user", f"[多智能体] {req.task}")
    _memory.add("assistant",
                json.dumps(result.get("results", []),
                           ensure_ascii=False)[:500])
    return AgentRunResponse(
        session_id=sid, task=result["task"], steps=result["steps"],
        results=result["results"], calls=result["calls"],
        elapsed=round(time.time() - t0, 1),
    )


# ---------------- 任务台（提醒 + 待办，共享桌面版数据） ----------------
@app.get("/tasks/todos")
async def list_todos():
    """查看当前未完成待办"""
    return {"todos": [t["text"] for t in _tasks.open_todos()],
            "total": len(_tasks.open_todos())}


@app.post("/tasks/todos")
async def add_todo(item: TodoItem):
    """新增待办（桌面版右键「我的待办」也能看到）"""
    if not item.text.strip():
        raise HTTPException(400, "text 不能为空")
    _tasks.add_todo(item.text.strip())
    _memory.add("user", f"[待办] {item.text.strip()}")
    return {"ok": True, "total": len(_tasks.open_todos())}


@app.delete("/tasks/todos/{index}")
async def delete_todo(index: int):
    """删除第 index 条待办（1 起始）"""
    item = _tasks.remove_todo(index)
    if item is None:
        raise HTTPException(404, f"待办 {index} 不存在")
    return {"ok": True, "removed": item["text"]}


@app.get("/tasks/reminders")
async def list_reminders():
    """查看当前提醒"""
    return {"reminders": _tasks.reminders,
            "total": len(_tasks.reminders)}


# ---------------- 会话管理 ----------------
@app.get("/sessions/{sid}")
async def get_session(sid: str):
    return {"session_id": sid, "history": _get_history(sid)}


@app.delete("/sessions/{sid}")
async def clear_session(sid: str):
    _sessions.pop(sid, None)
    return {"ok": True}