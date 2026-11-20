# -*- coding: utf-8 -*-
"""质量门：工具"成功返回"不等于"结果可用"（软失败检测与自我纠正的依据）

硬失败（请求报错、文件不存在）走既有重规划；这里拦截**软失败**：
请求成功但结果对不上题 / 内容过于单薄。全部本地启发式判定
（确定性、可离线单测、不额外花网络调用），不达标时由 Framework
注入【质量预警】或带着质量原因触发自我纠正（换词重搜 / 内容重写）。

判定刻意保守：只有"明显不达标"才拦截（如检索结果与查询关键词
零重合、文档全篇没有要点），避免把相关资料误杀造成纠正死循环。
"""

import re


def extract_keywords(text: str) -> list:
    """查询关键词：ASCII 词（≥2 位，小写）+ 中文 2-gram（去重保序）。

    "新能源汽车市场 2025 销量" -> [新能, 能源, 源汽, 汽车, 车市, 市场,
    2025, 销量]（顺序按出现先后）。"""
    words = []
    for w in re.findall(r"[A-Za-z0-9]{2,}", text or ""):
        lw = w.lower()
        if lw not in words:
            words.append(lw)
    for run in re.findall(r"[\u4e00-\u9fa5]{2,}", text or ""):
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg not in words:
                words.append(bg)
    return words[:24]


def assess_search(query: str, results: list) -> dict:
    """体检联网搜索结果（成功返回但可能对不上题的软失败）。

    :param query: 搜索词
    :param results: [{"title","content",...}, ...]
    :return: {"ok": 是否达标, "score": 0~1 相关度, "reason": 不达标原因}
    """
    results = [r for r in (results or []) if isinstance(r, dict)]
    if not results:
        return {"ok": False, "score": 0.0, "reason": "没有可用结果"}
    texts = [f"{r.get('title') or ''} {r.get('content') or ''}"
             for r in results]
    total_len = sum(len(t.strip()) for t in texts)
    if total_len < 60:
        return {"ok": False, "score": 0.0, "reason": "资料内容几乎为空"}
    titles = {r.get("title") or "" for r in results}
    if len(results) >= 3 and len(titles) == 1:
        return {"ok": False, "score": 0.0, "reason": "结果全部重复"}
    kws = extract_keywords(query)
    if not kws:
        return {"ok": True, "score": 0.5, "reason": ""}
    joined = " ".join(texts).lower()
    hit_kw = sum(1 for k in kws if k in joined)
    covered = sum(1 for t in texts if any(k in t.lower() for k in kws))
    score = round(0.5 * hit_kw / len(kws) + 0.5 * covered / len(results), 2)
    if hit_kw == 0 and covered == 0:
        return {"ok": False, "score": score,
                "reason": f"资料与查询关键词零重合（命中 0/{len(kws)}）"}
    return {"ok": True, "score": score, "reason": ""}


def assess_doc_spec(kind: str, spec: dict) -> dict:
    """体检文档内容（渲染前拦截"太单薄"的软失败）。

    只拦明显的偷懒（页数/章节/数据过少、要点干瘪），不吹毛求疵：
    通过则返回 ok=True，渲染照旧；不达标由执行层决定纠正方式。
    """
    if not isinstance(spec, dict):
        return {"ok": False, "score": 0.0, "reason": "内容结构为空"}
    if kind == "ppt":
        slides = spec.get("slides")
        slides = [s for s in slides if isinstance(s, dict)] \
            if isinstance(slides, list) else []
        if len(slides) < 2:
            return {"ok": False, "score": 0.0, "reason": "正文不足 2 页"}
        bullets = [str(b) for s in slides for b in (s.get("bullets") or [])
                   if str(b).strip()]
        if not bullets:
            return {"ok": False, "score": 0.0, "reason": "全篇没有要点"}
        thin = sum(1 for b in bullets if len(b.strip()) < 6)
        if thin / len(bullets) > 0.6:
            return {"ok": False, "score": 0.2,
                    "reason": f"要点过于干瘪（{thin}/{len(bullets)}"
                              " 条不足 6 字）"}
    elif kind in ("doc", "pdf", "html"):
        secs = spec.get("sections")
        secs = [s for s in secs if isinstance(s, dict)] \
            if isinstance(secs, list) else []
        if len(secs) < 2:
            return {"ok": False, "score": 0.0, "reason": "章节不足 2 个"}
        paras = sum(len([p for p in (s.get("paragraphs") or [])
                         if str(p).strip()]) for s in secs)
        if paras < 3:
            return {"ok": False, "score": 0.0,
                    "reason": f"全篇只有 {paras} 段内容"}
    elif kind == "xlsx":
        sheets = spec.get("sheets")
        sheets = [s for s in sheets if isinstance(s, dict)] \
            if isinstance(sheets, list) else []
        rows = sum(len(s.get("rows") or []) for s in sheets)
        if rows < 2:
            return {"ok": False, "score": 0.0, "reason": "几乎没有数据行"}
    return {"ok": True, "score": 1.0, "reason": ""}
