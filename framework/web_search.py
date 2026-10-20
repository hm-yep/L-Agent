import json
import re
import uuid
from typing import Optional

import requests

from framework import config
from framework.utils import logger


def _cfg(name, default=None):
    return getattr(config, name, default)


def web_search(query: str, key: Optional[str] = None, url: Optional[str] = None,
               count: Optional[int] = None) -> list:
    key = (key if key is not None else _cfg("WEBSEARCH_API_KEY", "") or "").strip()
    if not key:
        raise RuntimeError("未配置搜索 Key")

    query = str(query or "").strip()
    if not query:
        raise RuntimeError("搜索词为空")

    api_url = url or _cfg("WEBSEARCH_API_URL", "")
    if not api_url:
        raise RuntimeError("未配置搜索 URL")

    timeout = _cfg("WEBSEARCH_TIMEOUT", 10)
    payload = {
        "request_id": uuid.uuid4().hex,
        "tool": "web-search-pro",
        "messages": [{"role": "user", "content": query}],
        "stream": False,
    }

    try:
        resp = requests.post(
            api_url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"搜索请求失败: {e}") from e

    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

    try:
        data = resp.json()
    except ValueError as e:
        raise RuntimeError(f"搜索返回非 JSON: {resp.text[:200]}") from e

    results = parse_search_payload(data)
    default_limit = int(_cfg("WEBSEARCH_MAX_RESULTS", 10))
    if count is None:
        limit = default_limit
    else:
        try:
            limit = int(count)
        except (TypeError, ValueError):
            limit = default_limit
    limit = max(0, limit)
    return results[:limit]


def _try_load_json(raw: str):
    try:
        return json.loads(raw)
    except ValueError:
        pass
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except ValueError:
            pass
    return None


def parse_search_payload(data: dict) -> list:
    try:
        choices = data.get("choices") or [{}]
        if not isinstance(choices, list):
            choices = [choices]
        msg = (choices[0] or {}).get("message") or {}
        if not isinstance(msg, dict):
            return []

        items = []
        tools = msg.get("tools") or []
        if not isinstance(tools, list):
            tools = [tools]

        for tool in tools:
            if not isinstance(tool, dict):
                continue
            got = tool.get("search_result") or []
            if isinstance(got, dict):
                got = [got]
            if isinstance(got, list) and got:
                items = got
                break

        if not items:
            raw = str(msg.get("content") or "").strip()
            if raw:
                inner = _try_load_json(raw)
                if isinstance(inner, dict):
                    items = inner.get("search_result") or inner.get("results") or []
                elif isinstance(inner, list):
                    items = inner

        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            items = []

        return [_norm_result(it) for it in items if isinstance(it, dict)]
    except (ValueError, AttributeError, TypeError) as e:
        logger.warning(f"[Search] 返回解析失败: {type(e).__name__}: {e}")
        return []


def _norm_result(item: dict) -> dict:
    snippet = str(item.get("content") or "").strip()
    max_len = max(1, int(_cfg("WEBSEARCH_SNIPPET_MAX", 500)))
    if len(snippet) > max_len:
        snippet = snippet[:max_len - 1] + "…"

    return {
        "title": str(item.get("title") or "").strip()[:120],
        "link": str(item.get("link") or item.get("url") or "").strip(),
        "content": snippet,
        "media": str(item.get("media") or item.get("source") or "").strip(),
    }


def format_notes(query: str, results: list) -> str:
    lines = [f"搜索「{query}」查到："]
    for i, r in enumerate(results or [], 1):
        if not isinstance(r, dict):
            continue
        media = r.get("media") or ""
        src = f"（{media}）" if media else ""
        snippet = r.get("content") or "（无摘要）"
        lines.append(f"{i}. {r.get('title', '')}{src}：{snippet}")
    return "\n".join(lines)