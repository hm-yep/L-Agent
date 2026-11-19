# -*- coding: utf-8 -*-
"""单元测试：统一文件读取层 framework/file_reader.py

覆盖的真实失败类别（不是喂理想数据）：
  纯文本编码（UTF-8 / GBK / BOM）、二进制内容嗅探、超长截断必须在 note 里说明；
  目录列举（子目录在前、体积说人话、条目上限）、空目录；
  压缩包（列条目 + 顺带读文本成员、跳过图片等二进制成员、拒绝 `..` 成员、
  拒绝敏感名成员、坏包不崩、单个 gz 直读）；
  PDF（真造一份有文字层的 PDF 读出正文；再造一份无文字层的，必须如实说「扫描件」）；
  老格式 .doc/.ppt 的接口缺失降级（不许假装读成功、不许起真 Office 进程）；
  以及贯穿全层的两条硬约定：**read() 永不抛异常**、**空结果不算成功**。

需要 pypdf 的用法例是纯本地解析，不联网。缺依赖用 importorskip 跳过而不是失败。
"""

import gzip
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from framework import file_reader as fr


# ---------------------------------------------------------------- PDF 造样 ----
def _build_pdf(page_texts) -> bytes:
    """手搓一个最小合法 PDF：每页一条 Helvetica 文本。不引入任何 PDF 写入库。

    为什么要真造而不用桩：无文字层（扫描件）这条判据的价值就在于「pypdf 真的
    抽不出字」，用假 PdfReader 只能测我自己的 if，测不到阈值定得对不对。
    """
    n = len(page_texts)
    objs = []                      # (编号, bytes)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objs.append((1, b"<< /Type /Catalog /Pages 2 0 R >>"))
    objs.append((2, f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode()))
    objs.append((3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    for i, text in enumerate(page_texts):
        objs.append((4 + 2 * i, (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {5 + 2 * i} 0 R >>").encode()))
        safe = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream = (f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET").encode()
        objs.append((5 + 2 * i,
                     b"<< /Length " + str(len(stream)).encode()
                     + b" >> stream\n" + stream + b"\nendstream"))

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num, body in sorted(objs):
        offsets[num] = len(out)
        out += f"{num} 0 obj ".encode() + body + b" endobj\n"
    xref_at = len(out)
    total = max(offsets) + 1
    out += f"xref\n0 {total}\n".encode()
    out += b"0000000000 65535 f \n"
    for num in range(1, total):
        out += f"{offsets.get(num, 0):010d} 00000 n \n".encode()
    out += (f"trailer << /Size {total} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(out)


# ------------------------------------------------------------------ 纯文本 ----
def test_reads_utf8_plain_text(tmp_path):
    f = tmp_path / "笔记.md"
    f.write_text("# 标题\n正文一段", encoding="utf-8")
    res = fr.read(str(f))
    assert res["ok"] is True, res["note"]
    assert res["kind"] == "text"
    assert "正文一段" in res["text"]
    assert res["note"] == ""


def test_reads_gbk_and_bom_without_mojibake(tmp_path):
    """Windows 上老文本一大半是 GBK；带 BOM 的 UTF-8 也不能把 \ufeff 喂给模型。"""
    gbk = tmp_path / "gbk.txt"
    gbk.write_bytes("实验要求：按时提交".encode("gbk"))
    res = fr.read(str(gbk))
    assert res["ok"] is True, res["note"]
    assert "按时提交" in res["text"]

    bom = tmp_path / "bom.txt"
    bom.write_bytes("开头没有乱码".encode("utf-8-sig"))
    res2 = fr.read(str(bom))
    assert res2["ok"] is True, res2["note"]
    assert res2["text"].startswith("开头")


def test_binary_content_is_refused_with_a_plain_sentence(tmp_path):
    """后缀写着 .txt 但内容是二进制：按嗅探拒绝，不能把乱码塞进上下文。"""
    f = tmp_path / "fake.txt"
    f.write_bytes(b"MZ\x00\x01\x00\x00" + bytes(range(256)))
    res = fr.read(str(f))
    assert res["ok"] is False
    assert "二进制" in res["note"]
    assert res["text"] == ""


def test_long_text_is_truncated_but_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "SNIPPET_CHARS", 100)
    f = tmp_path / "long.txt"
    f.write_text("啊" * 500, encoding="utf-8")
    res = fr.read(str(f))
    assert res["ok"] is True
    assert len(res["text"]) == 100
    assert "100" in res["note"] and "只取了前" in res["note"]


def test_empty_file_is_not_reported_as_success(tmp_path):
    """空手而归不能算成功：否则助手会说「读完了」而资料缓冲里什么都没有。"""
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    res = fr.read(str(f))
    assert res["ok"] is False
    assert "没抽出文字" in res["note"]


def test_read_never_raises_on_missing_or_locked(tmp_path, monkeypatch):
    """路径不存在 / 底层抛 OSError，都必须变成 ok=False，而不是把异常抛给动作循环。"""
    res = fr.read(str(tmp_path / "nope.txt"), kind="text")
    assert res["ok"] is False
    assert "读不出来" in res["note"]

    def boom(*a, **kw):
        raise PermissionError("另一个程序正在使用此文件")
    monkeypatch.setattr(fr, "read_text", boom)
    res2 = fr.read(str(tmp_path / "x.txt"), kind="text")
    assert res2["ok"] is False
    assert "PermissionError" in res2["note"]


# -------------------------------------------------------------------- 目录 ----
def test_lists_directory_with_dirs_first(tmp_path):
    (tmp_path / "beta").mkdir()
    (tmp_path / "alpha").mkdir()
    (tmp_path / "z.txt").write_text("x", encoding="utf-8")
    (tmp_path / "big.txt").write_bytes(b"y" * 2048)
    res = fr.read(str(tmp_path))
    assert res["ok"] is True, res["note"]
    assert res["kind"] == "dir"
    lines = res["text"].splitlines()
    assert lines[0] == "[目录] alpha" and lines[1] == "[目录] beta"
    assert any(l.startswith("[文件] big.txt") and "2.0KB" in l for l in lines)
    assert "共 4 项" in res["note"]
    assert set(res["entries"]) == {"alpha", "beta", "z.txt", "big.txt"}


def test_empty_directory_is_a_valid_result(tmp_path):
    """空目录是真实答案，不是失败——这条和「空文本不算成功」正好相反。"""
    d = tmp_path / "empty_dir"
    d.mkdir()
    res = fr.read(str(d))
    assert res["ok"] is True
    assert res["text"] == ""
    assert "共 0 项" in res["note"]


def test_directory_entry_limit_is_honest(tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "DIR_ENTRY_LIMIT", 3)
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    res = fr.read(str(tmp_path))
    assert res["ok"] is True
    assert len(res["text"].splitlines()) == 3
    assert "只列了前 3 项" in res["note"]


def test_unreadable_directory_reports_failure(tmp_path, monkeypatch):
    d = tmp_path / "locked"
    d.mkdir()

    def boom(self):
        raise OSError("拒绝访问")
    monkeypatch.setattr(Path, "iterdir", boom)
    res = fr.list_dir(str(d))
    assert res["ok"] is False
    assert "列不出来" in res["note"]


# ------------------------------------------------------------------ 压缩包 ----
def _make_zip(path, members):
    with zipfile.ZipFile(str(path), "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_zip_lists_members_and_reads_text_ones(tmp_path):
    z = tmp_path / "材料.zip"
    _make_zip(z, {"说明.txt": "第一段内容",
                  "code/main.py": "print('hi')\n",
                  "logo.png": b"\x89PNG\x00\x01\x02"})
    res = fr.read(str(z))
    assert res["ok"] is True, res["note"]
    assert res["kind"] == "archive"
    assert "[条目] 说明.txt" in res["text"]
    assert "[条目] logo.png" in res["text"]        # 二进制成员照样列名
    assert "第一段内容" in res["text"]
    assert "print('hi')" in res["text"]
    assert "顺带读了其中 2 个文本文件" in res["note"]


def test_zip_skips_path_traversal_and_sensitive_members(tmp_path):
    """zip slip 与包内密钥：成员只读进内存不解包，但也不能绕过敏感名单。"""
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(str(z), "w") as zf:
        zf.writestr("../escape.txt", "越界内容")
        zf.writestr("/abs/path.txt", "绝对路径成员")
        zf.writestr("conf/user_settings.json", '{"api_key": "sk-fake"}')
        zf.writestr("keys/id_rsa", "PRIVATE KEY")
        zf.writestr("ok/readme.md", "正常内容")
    res = fr.read(str(z))
    assert res["ok"] is True, res["note"]
    assert "越界内容" not in res["text"]
    assert "绝对路径成员" not in res["text"]
    assert "sk-fake" not in res["text"]
    assert "PRIVATE KEY" not in res["text"]
    assert "正常内容" in res["text"]


def test_zip_member_limit_and_note(tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "ARCHIVE_MEMBER_LIMIT", 2)
    z = tmp_path / "many.zip"
    _make_zip(z, {f"m{i}.txt": f"内容{i}" for i in range(6)})
    res = fr.read(str(z))
    assert res["ok"] is True
    assert "顺带读了其中 2 个文本文件" in res["note"]


def test_binary_only_zip_says_there_is_nothing_to_read(tmp_path):
    z = tmp_path / "pics.zip"
    _make_zip(z, {"a.png": b"\x89PNG\x00", "b.jpg": b"\xff\xd8\xff"})
    res = fr.read(str(z))
    assert res["ok"] is True                 # 列出条目本身就是结果
    assert "里面没有可读的文本文件" in res["note"]


def test_empty_zip_is_not_success(tmp_path):
    z = tmp_path / "none.zip"
    _make_zip(z, {})
    res = fr.read(str(z))
    assert res["ok"] is False
    assert "没东西" in res["note"]


def test_corrupt_archive_does_not_raise(tmp_path):
    z = tmp_path / "broken.zip"
    z.write_bytes(b"this is not a zip file at all")
    res = fr.read(str(z))
    assert res["ok"] is False
    assert "打不开" in res["note"]


def test_tar_archive_reads_members(tmp_path):
    t = tmp_path / "pack.tar.gz"
    with tarfile.open(str(t), "w:gz") as tf:
        for name, body in (("notes.md", "# 打包说明"), ("bin.dat", "\x00\x01")):
            data = body.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    res = fr.read(str(t))
    assert res["ok"] is True, res["note"]
    assert "打包说明" in res["text"]
    assert "notes.md" in res["text"]


def test_plain_gz_file_is_unwrapped_and_read(tmp_path):
    g = tmp_path / "log.gz"
    with gzip.open(str(g), "wb") as gf:
        gf.write("第一行日志\n第二行日志".encode("utf-8"))
    res = fr.read(str(g))
    assert res["ok"] is True, res["note"]
    assert "第二行日志" in res["text"]
    assert "gz" in res["note"]


# -------------------------------------------------------------------- PDF ----
def test_pdf_with_text_layer_is_read(tmp_path):
    pytest.importorskip("pypdf")
    f = tmp_path / "说明.pdf"
    f.write_bytes(_build_pdf(["Hello LingLing page one",
                              "second page body text"]))
    res = fr.read_pdf(str(f))
    assert res["ok"] is True, res["note"]
    assert "Hello LingLing" in res["text"]
    assert "second page" in res["text"]
    assert "共 2 页" in res["note"]


def test_scanned_pdf_without_text_layer_is_reported_honestly(tmp_path):
    """无文字层的 PDF：绝不能返回 ok=True + 空串，要指出去走视觉模型。"""
    pytest.importorskip("pypdf")
    f = tmp_path / "scan.pdf"
    f.write_bytes(_build_pdf(["", ""]))
    res = fr.read_pdf(str(f))
    assert res["ok"] is False
    assert "没有文字层" in res["note"]
    assert "视觉" in res["note"]


def test_pdf_page_limit_is_reported(tmp_path, monkeypatch):
    pytest.importorskip("pypdf")
    monkeypatch.setattr(fr, "PDF_TEXT_FLOOR", 1)
    f = tmp_path / "many.pdf"
    f.write_bytes(_build_pdf([f"page {i} text body" for i in range(4)]))
    res = fr.read_pdf(str(f), max_pages=2)
    assert res["ok"] is True, res["note"]
    assert "共 4 页" in res["note"] and "只读了前 2 页" in res["note"]
    assert "page 3" not in res["text"]


def test_non_pdf_bytes_do_not_raise(tmp_path):
    pytest.importorskip("pypdf")
    f = tmp_path / "fake.pdf"
    f.write_bytes(b"%PDF-1.4 this is garbage")
    res = fr.read(str(f), kind="pdf")
    assert res["ok"] is False
    assert "PDF" in res["note"]


def test_missing_pypdf_degrades_to_a_plain_sentence(tmp_path, monkeypatch):
    """没装 pypdf 的机器：如实说读不了，而不是 ImportError 掀掉整轮对话。"""
    import sys
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setitem(sys.modules, "pypdf", None)
    res = fr.read_pdf(str(f))
    assert res["ok"] is False
    assert "pypdf" in res["note"]


# ------------------------------------------------------- 老格式 / Office ----
def test_ppt_legacy_is_refused_before_touching_com(tmp_path):
    """.ppt 的接口转不出可靠文本，直接要用户另存为 .pptx，不去起 Office。"""
    f = tmp_path / "旧演示.ppt"
    f.write_bytes(b"\xd0\xcf\x11\xe0")
    res = fr.read_legacy_office(str(f))
    assert res["ok"] is False
    assert ".pptx" in res["note"]


def test_legacy_without_pywin32_degrades(tmp_path, monkeypatch):
    import sys
    f = tmp_path / "旧文档.doc"
    f.write_bytes(b"\xd0\xcf\x11\xe0")
    monkeypatch.setitem(sys.modules, "pythoncom", None)
    res = fr.read_legacy_office(str(f))
    assert res["ok"] is False
    assert "没装 pywin32" in res["note"]


def test_docx_round_trip(tmp_path):
    """真造一份 docx 再读回来：走的是文档工厂的抽取器，不是我自己写的解析。"""
    docx = pytest.importorskip("docx")
    f = tmp_path / "报告.docx"
    d = docx.Document()
    d.add_heading("第一章 概述", level=1)
    d.add_paragraph("这是正文段落，应当被抽出来。")
    d.save(str(f))
    res = fr.read(str(f))
    assert res["ok"] is True, res["note"]
    assert res["kind"] == "office"
    assert "第一章 概述" in res["text"]
    assert "应当被抽出来" in res["text"]


def test_xlsx_round_trip(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    f = tmp_path / "成绩.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "期中"
    ws.append(["姓名", "分数"])
    ws.append(["张三", 91])
    wb.save(str(f))
    res = fr.read(str(f))
    assert res["ok"] is True, res["note"]
    assert "张三 | 91" in res["text"]
    assert "1 个工作表" in res["note"]


def test_image_is_not_this_layers_job(tmp_path):
    """图片不进文本读取层：判据里就不认，避免把 base64 塞给模型。"""
    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    from framework.path_guard import check_read, kind_of
    assert kind_of(png) == "unknown"
    assert check_read(str(png))[0] == "deny"


# ------------------------------------------------------------ 批量读取预算 ----
def test_read_many_budget_and_unread_count(tmp_path):
    """批量汇总：每份截 N 字、最多 M 份，剩下的必须在 note 里报数。"""
    paths = []
    for i in range(fr.BATCH_MAX_FILES + 3):
        p = tmp_path / ("doc%d.txt" % i)
        p.write_text("第%d份" % i + "内容" * 1000, encoding="utf-8")
        paths.append(str(p))
    res = fr.read_many(paths)
    assert res["ok"] is True, res["note"]
    ev = res["evidence"]
    assert ev["total"] == fr.BATCH_MAX_FILES + 3
    assert ev["read"] == fr.BATCH_MAX_FILES
    assert ev["skipped"] == 3
    assert "还剩 3 份没读" in res["note"]
    assert len(res["items"]) == fr.BATCH_MAX_FILES
    assert all(len(i["text"]) <= fr.BATCH_CHARS_PER_FILE
               for i in res["items"] if i["ok"])
    assert any(i["truncated"] for i in res["items"])


def test_read_many_dedups_and_reports_failures(tmp_path):
    good = tmp_path / "good.txt"
    good.write_text("能读到的内容", encoding="utf-8")
    res = fr.read_many([str(good), str(good),
                        str(tmp_path / "不存在.txt")])
    assert res["evidence"]["total"] == 2      # 同一路径换写法也只算一份
    assert res["evidence"]["failed"] == 1
    assert res["evidence"]["read"] == 1
    assert "没读出来" in res["note"]


def test_read_many_never_throws_on_bad_input():
    res = fr.read_many(None)
    assert res["ok"] is False
    assert res["evidence"]["total"] == 0
    assert res["note"]


# ------------------------------------------------------------ 顶层不抛异常 ----
def test_no_qt_or_doc_library_at_module_top():
    """判据与读取层顶层只准 import 标准库：重依赖一律在函数里懒加载。

    为什么钉这条：网页端、契约检查、单测都要能在没装 Office 依赖的机器上
    import 这两个模块；顶层一 import PySide6，整条链路就跟着起 Qt。
    """
    import ast
    from framework import grants
    for mod in (fr, grants, __import__("framework.path_guard",
                                       fromlist=["path_guard"])):
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        top = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    top.add(node.module.split(".")[0])
        assert not (top & {"PySide6", "PyQt5", "requests", "pypdf",
                           "openpyxl", "docx", "pptx", "win32com",
                           "pythoncom"}), (mod.__name__, top)
