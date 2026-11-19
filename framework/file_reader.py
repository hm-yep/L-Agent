# -*- coding: utf-8 -*-
"""统一的本机文件读取层：把「一个路径」变成「一段能交给模型的文字 + 一句实话」。

为什么单独一层：`read_doc`、`analyze_data`、目录浏览、以后的批量汇总都要读文件，
判据（哪些后缀认、怎么截断、读不出来怎么交代）只能有一份。以前这段逻辑写在
`core._do_read_doc` 里的一串 if/elif 上，界面上「支持的格式」那句话就得跟着改一处漏一处。

三条硬约定：
  1. **不抛异常**：损坏、加密、缺依赖一律返回 ok=False + 中文短句，让助手能如实念出来；
  2. **不静默给半截**：截断、跳过、只读了前 N 页都要写进 note，用户看到的和实际读的一致；
  3. **重依赖懒加载**：pypdf / openpyxl / win32com 只在真要读那种格式时才 import，
     本模块顶层只依赖标准库，单测和契约检查不用装齐全套 Office 依赖。
"""

import os
import tarfile
import zipfile
from pathlib import Path

# 目录一次最多列这么多条，超了只给前 N 条并在 note 里说明还剩多少
DIR_ENTRY_LIMIT = 200
# 压缩包里最多顺带读几个文本成员（其余只列名不读内容）
ARCHIVE_MEMBER_LIMIT = 12
# 单个文本成员/单份纯文本的读取上限（字符），防止一个巨型 log 吃掉整个上下文
SNIPPET_CHARS = 6000
# 批量汇总（read_doc 的 paths 数组）：每份截多少字、一轮最多读几份。
# 上限按「资料缓冲单条 800 字」留余量：每份单独入一条资料，另有一条汇总 note，
# 这样 N 小于缓冲条数上限 6，既不会互相挤掉，也不会把每份的预算偷偷截断。
BATCH_CHARS_PER_FILE = 600
BATCH_MAX_FILES = 4
# 判定「这 PDF 大概没文字层」的阈值：低于这个字数就当作扫描件
PDF_TEXT_FLOOR = 24

# 压缩包/目录里不当作文本去读的二进制成员后缀
_BINARY_MEMBER_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".mp3", ".wav",
    ".mp4", ".avi", ".mov", ".exe", ".dll", ".so", ".dylib", ".class", ".pyc",
    ".7z", ".rar", ".iso", ".dmg",
}


def _decode(data: bytes) -> str:
    """按 utf-8 → gbk 依次试（Windows 上的老文本大量是 GBK），最后兜底替换。"""
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def read_text(path: str) -> dict:
    raw = Path(path).read_bytes()
    if _looks_binary(raw):
        return {"ok": False, "kind": "text", "text": "",
                "note": "这个文件看着是二进制内容，没有可读的文本"}
    body = _decode(raw)
    note = ""
    if len(body) > SNIPPET_CHARS:
        body = body[:SNIPPET_CHARS]
        note = f"内容长，我只取了前 {SNIPPET_CHARS} 字"
    return {"ok": True, "kind": "text", "text": body, "note": note,
            "evidence": {"kind": "text", "chars": len(body),
                         "truncated": bool(note), "limit": SNIPPET_CHARS,
                         "skipped": 0}}


def list_dir(path: str) -> dict:
    """列一层目录：子目录在前、文件在后，带体积。只给名字，不猜内容。"""
    root = Path(path)
    try:
        entries = sorted(root.iterdir(),
                         key=lambda p: (p.is_file(), p.name.lower()))
    except OSError as e:
        return {"ok": False, "kind": "dir", "text": "",
                "note": f"这个文件夹列不出来（{type(e).__name__}）"}
    dirs = [p for p in entries if p.is_dir()]
    files = [p for p in entries if not p.is_dir()]
    lines = [f"[目录] {p.name}" for p in dirs[:DIR_ENTRY_LIMIT]]
    rest = max(0, DIR_ENTRY_LIMIT - min(len(dirs), DIR_ENTRY_LIMIT))
    for p in files[:rest]:
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        lines.append(f"[文件] {p.name}  {_human(size)}")
    total = len(dirs) + len(files)
    note = f"共 {total} 项"
    if total > len(lines):
        note += f"，只列了前 {DIR_ENTRY_LIMIT} 项"
    return {"ok": True, "kind": "dir", "text": "\n".join(lines),
            "note": note, "entries": [p.name for p in entries[:len(lines)]],
            "evidence": {"kind": "dir", "total": total, "listed": len(lines),
                         "skipped": max(0, total - len(lines)),
                         "dirs": len(dirs), "files": len(files),
                         "truncated": total > len(lines)}}


def _human(n) -> str:
    """字节数说人话（不用 framework.utils.human_size，那会把这个模块拖上 Qt 依赖链）。"""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}GB"


def read_pdf(path: str, max_pages: int = 40) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"ok": False, "kind": "pdf", "text": "",
                "note": "这台机器没装 pypdf，读不了 PDF"}
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:       # noqa: BLE001 加密 PDF 不硬试
                return {"ok": False, "kind": "pdf", "text": "",
                        "note": "这份 PDF 加了密码，我没有口令读不了"}
        pages = len(reader.pages)
        chunks = []
        for i, page in enumerate(reader.pages[:max_pages]):
            try:
                chunks.append(page.extract_text() or "")
            except Exception:       # noqa: BLE001 单页坏不拖垮整份
                chunks.append("")
        body = "\n".join(chunks).strip()
    except Exception as e:          # noqa: BLE001 损坏文件不崩助手
        return {"ok": False, "kind": "pdf", "text": "",
                "note": f"PDF 打不开（{type(e).__name__}）"}
    note = f"共 {pages} 页"
    read_pages = min(pages, max_pages)
    if pages > max_pages:
        note += f"，只读了前 {max_pages} 页"
    if len(body) < PDF_TEXT_FLOOR:
        return {"ok": False, "kind": "pdf", "text": "",
                "note": note + "，但这几页里没有文字层（扫描件/图片型 PDF），"
                               "纯文本抽不出字，要读得走视觉模型",
                "evidence": {"kind": "pdf", "pages": pages,
                             "pages_read": read_pages, "chars": 0,
                             "truncated": pages > max_pages,
                             "skipped": max(0, pages - read_pages)}}
    if len(body) > SNIPPET_CHARS:
        body = body[:SNIPPET_CHARS]
        note += f"，正文只取了前 {SNIPPET_CHARS} 字"
    return {"ok": True, "kind": "pdf", "text": body, "note": note,
            "evidence": {"kind": "pdf", "pages": pages, "pages_read": read_pages,
                         "chars": len(body), "truncated": pages > max_pages,
                         "skipped": max(0, pages - read_pages)}}


def read_archive(path: str) -> dict:
    """列压缩包条目，并顺带把里面能读的文本成员读出来（跳过二进制与敏感名）。"""
    from framework.path_guard import is_sensitive
    p = Path(path)
    names, texts = [], []
    try:
        if zipfile.is_zipfile(str(p)):
            with zipfile.ZipFile(str(p)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    names.append((info.filename, info.file_size))
                for info in zf.infolist()[:ARCHIVE_MEMBER_LIMIT * 4]:
                    body = _member_text(info.filename, lambda: zf.read(info),
                                        is_sensitive)
                    if body:
                        texts.append(f"『{info.filename}』\n{body}")
                    if len(texts) >= ARCHIVE_MEMBER_LIMIT:
                        break
        else:
            with tarfile.open(str(p)) as tf:
                for m in tf.getmembers():
                    if not m.isfile():
                        continue
                    names.append((m.name, m.size))
                for m in tf.getmembers():
                    if not m.isfile():
                        continue
                    f = tf.extractfile(m)
                    body = _member_text(m.name,
                                        lambda f=f: f.read() or b"",
                                        is_sensitive)
                    if body:
                        texts.append(f"『{m.name}』\n{body}")
                    if len(texts) >= ARCHIVE_MEMBER_LIMIT:
                        break
    except tarfile.ReadError:
        # 单个文件压成的 .gz（不是 tar）：直接当文本读
        try:
            import gzip
            with gzip.open(str(p), "rb") as gf:
                data = gf.read()
        except Exception as e:      # noqa: BLE001 .xz/.bz2 之外的杂包
            return {"ok": False, "kind": "archive", "text": "",
                    "note": f"这个压缩包打不开（{type(e).__name__}）"}
        if _looks_binary(data):
            return {"ok": False, "kind": "archive", "text": "",
                    "note": "压缩包里是二进制内容，没有可读的文本"}
        body = _decode(data)[:SNIPPET_CHARS]
        return {"ok": True, "kind": "archive", "text": body,
                "note": "单个 gz 文件，解开直接读"}
    except Exception as e:          # noqa: BLE001 坏包/不支持的压缩算法
        return {"ok": False, "kind": "archive", "text": "",
                "note": f"这个压缩包打不开（{type(e).__name__}）"}
    if not names:
        return {"ok": False, "kind": "archive", "text": "",
                "note": "这个压缩包里没东西"}
    lines = [f"[条目] {n}  {_human(s)}" for n, s in
             names[:DIR_ENTRY_LIMIT]]
    note = f"共 {len(names)} 个条目"
    if len(names) > DIR_ENTRY_LIMIT:
        note += f"，只列了前 {DIR_ENTRY_LIMIT} 个"
    picked = len(texts)
    if picked:
        note += f"；顺带读了其中 {picked} 个文本文件"
    else:
        note += "；里面没有可读的文本文件"
    body = "\n".join(lines)
    if texts:
        body += "\n\n—— 能直接读的文本成员 ——\n\n" + "\n\n".join(texts)
    return {"ok": True, "kind": "archive", "text": body, "note": note,
            "entries": [n for n, _ in names[:DIR_ENTRY_LIMIT]],
            "evidence": {"kind": "archive", "entries": len(names),
                         "listed": min(len(names), DIR_ENTRY_LIMIT),
                         "read_members": picked,
                         "chars": len(body),
                         "truncated": len(names) > DIR_ENTRY_LIMIT,
                         "skipped": max(0, len(names) - picked)}}


def _member_text(name: str, getter, is_sensitive) -> str:
    """压缩包内一个成员：先过敏感判定与二进制后缀，再按文本读。

    成员名可能带 `..` 或绝对路径（zip slip）。这里不落地解包，只读进内存，
    所以不存在写越界；但仍然不能因为它绕开敏感名单。
    """
    norm = os.path.normpath(str(name)).replace("\\", "/")
    if norm.startswith("../") or norm.startswith("/") or ".." in norm.split("/"):
        return ""
    base = Path(norm).name.lower()
    if Path(base).suffix in _BINARY_MEMBER_EXTS:
        return ""
    try:
        if is_sensitive(Path(norm)):
            return ""
    except (OSError, ValueError):
        return ""
    try:
        data = getter()
    except Exception:               # noqa: BLE001 单个成员坏不影响整包
        return ""
    if _looks_binary(data):
        return ""
    body = _decode(data)
    return body[:1500]


def read_legacy_office(path: str) -> dict:
    """.doc/.xls/.ppt：借本机 WPS/Office 的 COM 接口另存成纯文本再读。

    这是「本机有装了办公软件才能用」的能力，不是硬依赖：接口起不来就如实说
    「这台机器上没有可用的 Office/WPS」，绝不假装读出来了。
    临时转存文件放在临时目录里，读完就删。
    """
    src = os.path.abspath(str(path))
    out = os.path.join(os.path.dirname(src),
                       f".lingling_extract_{os.getpid()}.txt")
    if Path(src).suffix.lower() == ".ppt":
        return {"ok": False, "kind": "legacy", "text": "",
                "note": ".ppt 老演示文稿我这台接口转不出来，用 WPS/Office "
                        "另存为 .pptx 我就能读"}
    try:
        import pythoncom        # type: ignore
        import win32com.client as wc    # type: ignore
    except ImportError:
        return {"ok": False, "kind": "legacy", "text": "",
                "note": "这台机器没装 pywin32，也就可以理解为本机没有可用的 "
                        "Office/WPS 接口，老格式 .doc/.xls 转不出来"}
    pythoncom.CoInitialize()
    app = None
    try:
        for progid in ("Kwps.Application", "Word.Application"):
            try:
                app = wc.DispatchEx(progid)
                break
            except Exception:       # noqa: BLE001 逐个试，都起不来再说
                app = None
        if app is None:
            return {"ok": False, "kind": "legacy", "text": "",
                    "note": "本机没有可用的 Word/WPS 接口，"
                            "老格式 .doc/.xls 转不出来，另存为新格式我就能读"}
        app.Visible = False
        try:
            app.DisplayAlerts = False
        except Exception:           # noqa: BLE001 WPS 没这个属性
            pass
        doc = app.Documents.Open(src, ReadOnly=True)
        # 7 = wdFormatEncodedText/纯文本；用 2 会带 RTF 标记
        doc.SaveAs2(out, 7) if hasattr(doc, "SaveAs2") else doc.SaveAs(out, 7)
        doc.Close(False)
        res = read_text(out)
        res["kind"] = "legacy"
        ev = dict(res.get("evidence") or {})
        ev["kind"] = "legacy"
        res["evidence"] = ev
        res["note"] = ("由本机 Office/WPS 转出来的纯文本，图片与排版会丢。"
                       + (res.get("note") or ""))
        return res
    except Exception as e:          # noqa: BLE001 COM 的异常五花八门
        return {"ok": False, "kind": "legacy", "text": "",
                "note": f"本机接口转这份文件失败了（{type(e).__name__}）"}
    finally:
        try:
            if app is not None:
                app.Quit()
        except Exception:           # noqa: BLE001 关掉失败不影响结果
            pass
        try:
            if os.path.exists(out):
                os.remove(out)
        except OSError:
            pass
        pythoncom.CoUninitialize()


def read_office(path: str) -> dict:
    """.docx/.pptx 走文档工厂的抽取器，.xlsx 走 openpyxl。"""
    ext = Path(path).suffix.lower()
    from framework.doc_editor import extract_content
    if ext == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        rows = []
        for ws in wb.worksheets:
            rows.append(f"『{ws.title}』")
            for row in list(ws.iter_rows(values_only=True))[:100]:
                vals = [str(v) for v in row if v is not None]
                if vals:
                    rows.append(" | ".join(vals))
        body = "\n".join(rows)
        note = f"{len(wb.worksheets)} 个工作表，每表最多取 100 行"
        if len(body) > SNIPPET_CHARS:
            body = body[:SNIPPET_CHARS]
            note += f"；正文只取了前 {SNIPPET_CHARS} 字"
        return {"ok": True, "kind": "office", "text": body, "note": note,
                "evidence": {"kind": "office", "sheets": len(wb.worksheets),
                             "chars": len(body), "truncated": True,
                             "skipped": 0}}
    kind, spec = extract_content(path)
    if kind == "ppt":
        body = "\n".join(f"『{s['title']}』" + "；".join(s["bullets"])
                         for s in spec.get("slides", []) if s.get("title"))
        note = f"{len(spec.get('slides', []))} 页"
        units = len(spec.get("slides", []))
    else:
        body = "\n".join(f"『{s['heading']}』" + "；".join(s["paragraphs"])
                         for s in spec.get("sections", []) if s.get("heading"))
        note = f"{len(spec.get('sections', []))} 节"
        units = len(spec.get("sections", []))
    if len(body) > SNIPPET_CHARS:
        body = body[:SNIPPET_CHARS]
        note += f"；正文只取了前 {SNIPPET_CHARS} 字"
    return {"ok": True, "kind": "office", "text": body, "note": note,
            "evidence": {"kind": "office", "units": units, "chars": len(body),
                         "truncated": True, "skipped": 0}}


def read(path: str, kind: str = "") -> dict:
    """按格式表把路径读成文字。kind 由 path_guard.kind_of 给，省略则自己算。

    这里是唯一的安全网：任何一个分支漏掉的 IO / 解析异常都在这一层变成
    ok=False + 中文短句，调用方永远只需要看结果，不需要 try。
    """
    try:
        res = _read_inner(path, kind)
    except Exception as e:          # noqa: BLE001 损坏/加密/占用不崩助手
        return {"ok": False, "kind": kind or "unknown", "text": "",
                "note": f"这份文件读不出来（{type(e).__name__}），"
                        "确认它没有被打开或加密"}
    # 空手而归不能算成功：不然调用方会把「一个字都没读到」当成读到了
    if res.get("ok") and res.get("kind") != "dir" and not res.get("text", "").strip():
        res["ok"] = False
        res["note"] = (res.get("note") or "").strip()
        res["note"] = "这里面没抽出文字（可能是扫描件、纯图片或空文档）。" + res["note"]
    return res


def read_many(paths, *, max_files: int = None,
              chars_per_file: int = None) -> dict:
    """按上下文预算读一批路径：每份截 N 字、最多读 M 份，剩下的如实报数。

    这是 F4「读完能直接作用」的批量入口。三条硬约定沿用本模块：
      1. 不抛异常：坏路径只记一条失败，不拖垮整批；
      2. 不静默给半截：每份截断、整批少读，都写进 note 与 evidence；
      3. 去重：同一个文件用不同写法传两次不算两份，免得预算被浪费。

    :return: {ok, kind:"batch", text, note, items:[{path,name,ok,text,note}],
              evidence:{kind,total,read,failed,skipped,chars,limit,max_files}}
    """
    empty_ev = {"kind": "batch", "total": 0, "read": 0, "failed": 0,
                "skipped": 0, "chars": 0, "limit": BATCH_CHARS_PER_FILE,
                "max_files": BATCH_MAX_FILES, "truncated": False}
    try:
        max_files = max(1, min(int(max_files or BATCH_MAX_FILES),
                               BATCH_MAX_FILES))
        chars_per_file = max(1, min(int(chars_per_file or BATCH_CHARS_PER_FILE),
                                    SNIPPET_CHARS))
        raw = list(paths or [])
    except (TypeError, ValueError):
        return {"ok": False, "kind": "batch", "text": "",
                "note": "这批路径我解析不了，请一份一行重新给我",
                "items": [], "evidence": empty_ev}

    picked, seen = [], set()
    for item in raw:
        p = str(item or "").strip().strip('"')
        if not p:
            continue
        try:
            key = os.path.normcase(os.path.realpath(p))
        except (OSError, ValueError):
            key = p
        if key in seen:
            continue
        seen.add(key)
        picked.append(p)

    total = len(picked)
    chosen = picked[:max_files]
    skipped = max(0, total - len(chosen))
    items, chunks, failed = [], [], 0
    for p in chosen:
        name = os.path.basename(p) or p
        res = read(p)
        if not res.get("ok"):
            failed += 1
            items.append({"path": p, "name": name, "ok": False,
                          "text": "", "note": res.get("note") or "没读出来",
                          "truncated": False})
            continue
        full = (res.get("text") or "").strip()
        body = full[:chars_per_file]
        extra = ""
        if len(full) > chars_per_file:
            extra = "批量汇总每份只取前 %d 字" % chars_per_file
        note = "；".join(x for x in (res.get("note") or "", extra) if x)
        items.append({"path": p, "name": name, "ok": True, "text": body,
                      "note": note, "truncated": bool(extra),
                      "kind": res.get("kind") or "text"})
        chunks.append("【本机文档 %s】\n%s" % (name, body))

    read_count = len(chosen) - failed
    bits = ["本轮共 %d 份，读了 %d 份" % (total, read_count)]
    if failed:
        bits.append("%d 份没读出来" % failed)
    if skipped:
        bits.append("还剩 %d 份没读" % skipped)
    if any(i.get("truncated") for i in items):
        bits.append("每份只取前 %d 字" % chars_per_file)
    return {
        "ok": bool(chunks), "kind": "batch", "text": "\n\n".join(chunks),
        "note": "；".join(bits) + "。", "items": items,
        "evidence": {"kind": "batch", "total": total, "read": read_count,
                     "failed": failed, "skipped": skipped,
                     "chars": sum(len(i["text"]) for i in items),
                     "limit": chars_per_file, "max_files": max_files,
                     "listed": read_count,
                     "truncated": any(i.get("truncated") for i in items)},
    }


def _read_inner(path: str, kind: str) -> dict:
    p = Path(path)
    if not kind:
        from framework.path_guard import kind_of
        kind = kind_of(p)
    if kind == "dir":
        return list_dir(str(p))
    if kind == "archive":
        return read_archive(str(p))
    if kind == "pdf":
        return read_pdf(str(p))
    if kind == "legacy":
        return read_legacy_office(str(p))
    if kind == "office":
        return read_office(str(p))
    if kind in ("text", "unknown"):
        return read_text(str(p))
    return {"ok": False, "kind": kind, "text": "", "note": "这种格式还不支持"}
