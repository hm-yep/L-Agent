# -*- coding: utf-8 -*-
"""RAG 引擎：基于 LangChain + Chroma 的本地知识库检索增强

使用新版 langchain-chroma / langchain-huggingface 适配 Chroma 1.x。
旧版 langchain_community 的 Chroma 适配器与 Chroma 1.x 不兼容，
会在 add_texts 时报 RuntimeError: '_type'。
"""

import os
import time

# 关闭 Chroma 遥测上报（必须在 import chromadb 之前设置）
os.environ["ANONYMIZED_TELEMETRY"] = "False"

from framework.utils import logger

_DEFAULT_EMBED = "BAAI/bge-small-zh-v1.5"
_DEFAULT_CHUNK = 500
_DEFAULT_OVERLAP = 50
_DEFAULT_TOP_K = 3


class RagEngine:
    """本地知识库：加文档、检索、拼上下文"""

    def __init__(self, persist_dir: str = "./data/chroma",
                 embed_model: str = _DEFAULT_EMBED,
                 collection: str = "lingling_kb"):
        self.persist_dir = persist_dir
        self.embed_model = embed_model
        self.collection = collection
        self._vs = None
        self._splitter = None

    def _ensure(self):
        if self._vs is not None:
            return
        try:
            from langchain_text_splitters import RecursiveCharacterTextSplitter
            # 新版包（必须）：适配 Chroma 1.x
            from langchain_huggingface import HuggingFaceEmbeddings
            from langchain_chroma import Chroma
        except ImportError as e:
            raise RuntimeError(
                "缺少新版 RAG 依赖，请执行：\n"
                "  pip install -U langchain-chroma langchain-huggingface"
            ) from e

        os.makedirs(self.persist_dir, exist_ok=True)
        logger.info(f"[RAG] 加载嵌入模型 {self.embed_model}（首次会下载）")
        embeddings = HuggingFaceEmbeddings(
            model_name=self.embed_model,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=_DEFAULT_CHUNK,
            chunk_overlap=_DEFAULT_OVERLAP,
            separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""],
        )
        self._vs = Chroma(
            collection_name=self.collection,
            embedding_function=embeddings,
            persist_directory=self.persist_dir,
        )
        logger.info("[RAG] 向量库已就绪")

    def add_text(self, text: str, source: str = "manual",
                 extra_meta: dict = None) -> int:
        self._ensure()
        text = (text or "").strip()
        if not text:
            return 0
        chunks = self._splitter.split_text(text)
        if not chunks:
            return 0
        metadatas = []
        now = time.time()
        for i, _ in enumerate(chunks):
            # Chroma 1.x 要求 metadata 值只能是 str/int/float/bool，
            # 空 dict/空列表会被拒。全部转字符串最稳。
            meta = {"source": str(source or ""),
                    "chunk": str(i),
                    "ts": str(now)}
            if extra_meta:
                for k, v in extra_meta.items():
                    meta[str(k)] = str(v) if v is not None else ""
            metadatas.append(meta)
        self._vs.add_texts(texts=chunks, metadatas=metadatas)
        logger.info(f"[RAG] 新增 {len(chunks)} 块（来源：{source}）")
        return len(chunks)

    def add_file(self, path: str) -> int:
        from framework.doc_editor import extract_content
        ext = os.path.splitext(path)[1].lower()
        if ext in (".docx", ".pptx"):
            kind, spec = extract_content(path)
            if kind == "ppt":
                body = "\n".join(
                    f"【{s.get('title', '')}】" + "；".join(s.get("bullets", []))
                    for s in spec.get("slides", []))
            else:
                body = "\n".join(
                    f"【{s.get('heading', '')}】" + "；".join(s.get('paragraphs', []))
                    for s in spec.get("sections", []))
            text = body
        elif ext in (".txt", ".md", ".csv", ".json", ".log", ".py"):
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        elif ext == ".xlsx":
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=True, data_only=True)
            lines = []
            for ws in wb.worksheets:
                lines.append(f"【{ws.title}】")
                for row in list(ws.iter_rows(values_only=True))[:200]:
                    vals = [str(v) for v in row if v is not None]
                    if vals:
                        lines.append(" | ".join(vals))
            text = "\n".join(lines)
        else:
            raise ValueError(f"不支持的文档格式: {ext}")
        return self.add_text(text, source=os.path.basename(path))

    def search(self, query: str, top_k: int = _DEFAULT_TOP_K) -> list:
        self._ensure()
        query = (query or "").strip()
        if not query:
            return []
        docs = self._vs.similarity_search_with_score(query, k=top_k)
        hits = []
        for doc, score in docs:
            hits.append({
                "content": doc.page_content,
                "source": str(doc.metadata.get("source", "")),
                "chunk": doc.metadata.get("chunk", "0"),
                "score": float(score),
            })
        return hits

    def format_context(self, hits: list, max_chars: int = 2000) -> str:
        if not hits:
            return ""
        parts = []
        used = 0
        for h in hits:
            piece = f"《{h['source']}》片段{h['chunk']}：{h['content']}"
            if used + len(piece) > max_chars:
                break
            parts.append(piece)
            used += len(piece)
        return "\n".join(parts)

    def count(self) -> int:
        self._ensure()
        try:
            return self._vs._collection.count()
        except Exception:
            return -1

    def reset(self):
        self._ensure()
        try:
            self._vs._client.delete_collection(self.collection)
        except Exception as e:
            logger.warning(f"[RAG] 清空失败: {e}")
        self._vs = None