# -*- coding: utf-8 -*-
"""凌零 · 智能体 API 服务（RAG 子进程 + 与桌面版共享记忆/任务台）

与桌面版统一的部分：
  - 对话历史：共享 framework.memory_system.MemorySystem（data/memory.json）
  - 提醒待办：共享 framework.task_board.TaskBoard（data/tasks.json）
  - 知识库：共享 framework.rag_client（子进程）
  - 联网搜索：共享 framework.web_search
  - 人设：与桌面版一致的骑士凌零（输出格式不同：桌面走 JSON 动作，网页走纯文本）

安全口径（见 docs/QUICK_START.md 与 .env.example）：
  - 设置 LINGLING_API_TOKEN 后，所有请求必须带 X-API-Key（或 Bearer）；
  - 未设置令牌时只接受回环地址请求，避免 0.0.0.0 暴露免鉴权的 /chat、/agent/run；
  - CORS 用白名单，不再放行任意来源；
  - 服务端异常只记日志，不把异常原文回显给客户端；
  - 所有文本/上传/会话都有长度、大小、数量与空闲过期上限。
"""

import importlib.util
import json
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Optional

import requests
from fastapi import (Depends, FastAPI, File, Form, Header, HTTPException,
                     Request, UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from framework import config
config.apply_user_settings()

from framework.web_search import web_search
from framework.quality_gate import assess_search, extract_keywords
from framework.utils import logger
from framework import rag_client
from framework.memory_system import MemorySystem
from framework.task_board import TaskBoard


_GENERIC_500 = "处理失败，详见服务端日志"
_SID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


# ---------------- 访问控制 ----------------
def _client_host(request: Request) -> str:
    return (request.client.host if request.client else "") or ""


def require_access(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None),
):
    """全局依赖：有令牌就验令牌；没令牌就只允许本机访问。

    Starlette TestClient 的 client.host 是 "testclient"，把它一并视为本机，
    否则所有接口测试都得先伪造令牌（测试覆盖反而会下降）。
    """
    token = config.API_AUTH_TOKEN
    if token:
        supplied = x_api_key or ""
        if not supplied and authorization \
                and authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if not supplied or not secrets.compare_digest(supplied, token):
            raise HTTPException(401, "无效的访问令牌")
        return
    if _client_host(request) not in _LOOPBACK_HOSTS:
        raise HTTPException(
            403, "未配置 LINGLING_API_TOKEN：服务仅接受本机回环请求")


_origins = [o for o in config.API_ALLOWED_ORIGINS if o and o != "*"]
if not _origins:
    _origins = ["http://127.0.0.1:8000"]

app = FastAPI(title="凌零 · 智能体服务", version="1.3.0",
              dependencies=[Depends(require_access)])
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
)

# 与桌面版共享同一份 JSON 文件
_memory = MemorySystem()
_tasks = TaskBoard()

# 会话 sid -> (memory 起始索引, 最后访问时间)
_sessions: dict = {}

# RAG 是可选依赖，health 要如实反映，不能恒为 True
_RAG_AVAILABLE = importlib.util.find_spec("langchain_chroma") is not None


def _check_sid(sid: Optional[str]) -> str:
    if sid is not None and not _SID_RE.match(sid):
        raise HTTPException(400, "session_id 格式不合法")
    return sid or ""


def _check_language(language: str) -> str:
    if not re.fullmatch(r"[A-Za-z]{0,8}", language or ""):
        raise HTTPException(400, "language 参数不合法")
    return language


def _require_rag():
    if not _RAG_AVAILABLE:
        raise HTTPException(
            503, "知识库依赖未安装：pip install -r requirements-rag.txt")


def _read_upload_sync(file: UploadFile, limit: int) -> bytes:
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "上传文件过大")
    return data


async def _read_upload_async(file: UploadFile, limit: int) -> bytes:
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "上传文件过大")
    return data


def _prune_sessions():
    """清掉过期与超限会话，防止伪造 sid 让内存无限增长。"""
    now = time.time()
    for sid, (_, last_seen) in list(_sessions.items()):
        if now - last_seen > config.API_SESSION_TTL_SEC:
            _sessions.pop(sid, None)
    overflow = len(_sessions) - config.API_MAX_SESSIONS
    if overflow > 0:
        oldest = sorted(_sessions.items(), key=lambda kv: kv[1][1])[:overflow]
        for sid, _ in oldest:
            _sessions.pop(sid, None)


# ---------------- 请求/响应模型 ----------------
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1,
                         max_length=config.API_MAX_MESSAGE_CHARS)
    session_id: Optional[str] = Field(None, max_length=64)
    use_rag: bool = False
    use_search: bool = False
    top_k: int = Field(3, ge=1, le=10)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    references: list = []
    elapsed_ms: int = 0


class IngestRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=200_000)
    source: str = Field("manual", max_length=64)


class IngestResponse(BaseModel):
    ok: bool
    chunks: int
    total: int


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    count: int = Field(5, ge=1, le=10)


class VoiceResponse(BaseModel):
    text: str
    elapsed_ms: int = 0


class AgentRunRequest(BaseModel):
    task: str = Field(..., min_length=1,
                      max_length=config.API_MAX_TASK_CHARS)
    session_id: Optional[str] = Field(None, max_length=64)


class AgentRunResponse(BaseModel):
    session_id: str
    task: str
    steps: list
    results: list
    calls: int
    elapsed: float


class TodoItem(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)


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
        _require_rag()
        try:
            hits = rag_client.search(message, top_k=top_k)
            if hits:
                rag_context = rag_client.format_context(hits, max_chars=2000)
                references += [{"type": "rag", "source": h.get("source", ""),
                                "score": h.get("score", 0),
                                "snippet": (h.get("content") or "")[:120]}
                               for h in hits]
                logger.info(f"[API] RAG 命中 {len(hits)} 条")
        except HTTPException:
            raise
        except Exception as e:                  # noqa: BLE001
            logger.warning(f"[API] RAG 检索失败: {e}")
            references.append({"type": "rag_error",
                               "message": "知识库暂时不可用"})

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
        except Exception:                      # noqa: BLE001
            logger.exception("[API] 搜索失败")
            references.append({"type": "search_error",
                               "message": "联网检索暂时不可用"})

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
    _prune_sessions()
    now = time.time()
    rec = _sessions.get(sid)
    if rec is None:
        rec = (len(_memory.history), now)
    _sessions[sid] = (rec[0], now)
    return list(_memory.history[rec[0]:])


def _record(sid: str, user_text: str, reply: str):
    """写入共享 memory，桌面版也能看到。"""
    _memory.add("user", user_text)
    _memory.add("assistant", reply)
    if sid not in _sessions:
        _sessions[sid] = (max(0, len(_memory.history) - 2), time.time())
    else:
        start, _ = _sessions[sid]
        _sessions[sid] = (start, time.time())


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
            "rag_available": _RAG_AVAILABLE,
            "auth_required": bool(config.API_AUTH_TOKEN),
            "memory_count": len(_memory.history),
            "todos_count": len(_tasks.open_todos()),
            "search_key_configured": bool(config.WEBSEARCH_API_KEY)}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    t0 = time.time()
    _check_sid(req.session_id)
    sid = req.session_id or f"s_{int(time.time()*1000)}"
    history = _get_history(sid)

    try:
        reply, refs = agent_chat(req.message, history, req.use_rag,
                                 req.use_search, req.top_k)
    except HTTPException:
        raise
    except Exception:                          # noqa: BLE001
        logger.exception("[API] chat 失败")
        raise HTTPException(500, _GENERIC_500)

    _record(sid, req.message, reply)
    return ChatResponse(session_id=sid, reply=reply, references=refs,
                        elapsed_ms=int((time.time() - t0) * 1000))


@app.post("/ingest/text", response_model=IngestResponse)
async def ingest_text(req: IngestRequest):
    _require_rag()
    if not req.text.strip():
        raise HTTPException(400, "text 不能为空")
    try:
        n = rag_client.add_text(req.text, source=req.source)
        total = rag_client.count()
    except Exception:                          # noqa: BLE001
        logger.exception("[API] ingest_text 失败")
        raise HTTPException(500, "入库失败，详见服务端日志")
    return IngestResponse(ok=True, chunks=n, total=total)


@app.post("/ingest/file", response_model=IngestResponse)
async def ingest_file(file: UploadFile = File(...)):
    _require_rag()
    tmp_dir = Path("./data/uploads")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "").name  # 丢掉 ../ 与绝对路径，只留纯文件名
    if not safe_name:
        safe_name = "upload.bin"
    dst = (tmp_dir / safe_name).resolve()
    if dst.parent != tmp_dir.resolve():  # 兜底：解析后必须仍落在 uploads 内
        raise HTTPException(400, "非法文件名")
    data = await _read_upload_async(file, config.API_MAX_UPLOAD_BYTES)
    with open(dst, "wb") as f:
        f.write(data)
    try:
        n = rag_client.add_file(str(dst))
        total = rag_client.count()
    except Exception:                          # noqa: BLE001
        logger.exception("[API] ingest_file 失败")
        raise HTTPException(400, "入库失败，详见服务端日志")
    return IngestResponse(ok=True, chunks=n, total=total)


@app.post("/search")
async def search_endpoint(req: SearchRequest):
    try:
        results = web_search(req.query, count=req.count)
    except Exception:                          # noqa: BLE001
        logger.exception("[API] 搜索失败")
        raise HTTPException(502, "搜索服务暂时不可用，详见服务端日志")
    return {"query": req.query, "results": results}


@app.post("/voice", response_model=VoiceResponse)
def voice_to_text(
    file: UploadFile = File(...),
    language: str = Form("zh"),
):
    t0 = time.time()
    language = _check_language(language)
    logger.info("[API] 收到语音请求 /voice")
    try:
        from framework.voice_input import transcribe_bytes
    except ImportError:
        raise HTTPException(
            503, "语音依赖未安装：pip install -r requirements-voice.txt")

    suffix = Path(file.filename or "voice.wav").suffix or ".wav"
    data = _read_upload_sync(file, config.API_MAX_UPLOAD_BYTES)
    if not data:
        raise HTTPException(400, "音频文件为空")
    try:
        text = transcribe_bytes(data, suffix=suffix, language=language)
    except Exception:                          # noqa: BLE001
        logger.exception("[API] 语音识别失败")
        raise HTTPException(500, "语音识别失败，详见服务端日志")
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
    language = _check_language(language)
    _check_sid(session_id)
    logger.info("[API] 收到语音请求 /voice/chat")
    try:
        from framework.voice_input import transcribe_bytes
    except ImportError:
        raise HTTPException(
            503, "语音依赖未安装：pip install -r requirements-voice.txt")

    suffix = Path(file.filename or "voice.wav").suffix or ".wav"
    data = _read_upload_sync(file, config.API_MAX_UPLOAD_BYTES)
    if not data:
        raise HTTPException(400, "音频文件为空")
    try:
        text = transcribe_bytes(data, suffix=suffix, language=language)
    except Exception:                          # noqa: BLE001
        logger.exception("[API] 语音识别失败")
        raise HTTPException(500, "语音识别失败，详见服务端日志")
    if not text:
        raise HTTPException(400, "没有识别到语音内容")

    sid = session_id or f"s_{int(time.time()*1000)}"
    history = _get_history(sid)
    try:
        reply, refs = agent_chat(text, history, use_rag, use_search, 3)
    except HTTPException:
        raise
    except Exception:                          # noqa: BLE001
        logger.exception("[API] voice_chat 对话失败")
        raise HTTPException(500, _GENERIC_500)
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
    _check_sid(req.session_id)
    sid = req.session_id or f"s_{int(time.time()*1000)}"
    try:
        result = get_orchestrator().run(req.task)
    except Exception:                          # noqa: BLE001
        logger.exception("[API] 多智能体失败")
        raise HTTPException(500, "编排失败，详见服务端日志")
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
    items = _tasks.open_todos()
    return {"todos": [t["text"] for t in items], "total": len(items)}


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
    if index < 1:
        raise HTTPException(400, "index 必须 >= 1")
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
    _check_sid(sid)
    return {"session_id": sid, "history": _get_history(sid)}


@app.delete("/sessions/{sid}")
async def clear_session(sid: str):
    _check_sid(sid)
    _sessions.pop(sid, None)
    return {"ok": True}
