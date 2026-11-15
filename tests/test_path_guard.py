# -*- coding: utf-8 -*-
"""单元测试：本机文件读取白名单 framework/path_guard.py

覆盖：白名单后缀放行、危险后缀拒绝、../ 与符号链接逃逸、敏感文件名、
白名单外绝对路径默认拒绝、LINGLING_ALLOWED_PATHS 追加放行、空路径 /
不存在 / 目录 / 超大文件边界、以及 reason 不回显绝对路径。

为什么不需要联网：本模块只依赖标准库（os / pathlib / tempfile），做的都是
本机路径的字符串与文件系统判定，没有任何网络调用、没有 Qt、没有 LLM。
所有被测文件都用 pytest 的 tmp_path 现造，读的是自己刚写出来的真实文件；
环境变量与 tempdir 的改动一律走 monkeypatch，测试结束自动恢复。
"""

import json
import os
import tempfile
import types
from pathlib import Path

import pytest

from framework import path_guard


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """每个用例都从干净的白名单环境变量基线出发，不污染全局状态。"""
    monkeypatch.delenv("LINGLING_ALLOWED_PATHS", raising=False)


@pytest.fixture
def controlled(tmp_path, monkeypatch):
    """把 tempdir 白名单收窄到 sandbox，让 outside 成为真正落在白名单外的目录。

    只替换 path_guard 模块内部的 tempfile 名字（不动全局 tempfile 模块），
    validate_read_path / allowed_roots / is_sensitive 仍是真实被测实现。
    """
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(
        path_guard, "tempfile",
        types.SimpleNamespace(gettempdir=lambda: str(sandbox)),
    )
    return sandbox, outside


@pytest.mark.parametrize("suffix", [".txt", ".csv", ".json", ".docx"])
def test_allowed_document_extensions_in_tempdir(tmp_path, suffix):
    """临时目录是默认白名单根，常见文档/文本后缀应放行并返回规范化路径。"""
    f = tmp_path / ("report" + suffix)
    f.write_text("hello, 凌零", encoding="utf-8")
    ok, normalized, reason = path_guard.validate_read_path(str(f))
    assert ok is True, reason
    assert normalized == str(f.resolve())
    assert reason == ""


def test_every_declared_allowed_ext_is_accepted(tmp_path):
    """契约表和实现不能漂移：ALLOWED_EXTS 里的每个后缀都得真能读。"""
    for suffix in path_guard.ALLOWED_EXTS:
        f = tmp_path / ("probe" + suffix)
        f.write_text("x", encoding="utf-8")
        ok, _, reason = path_guard.validate_read_path(str(f))
        assert ok is True, f"{suffix} 应被放行：{reason}"


@pytest.mark.parametrize("suffix", [".exe", ".bat", ".dll", ".bin"])
def test_reject_executable_and_binary_extensions(tmp_path, suffix):
    """非白名单后缀（可执行文件/二进制）必须拒绝，且不给规范化路径。"""
    f = tmp_path / ("payload" + suffix)
    f.write_bytes(b"MZ\x00\x01")
    ok, normalized, reason = path_guard.validate_read_path(str(f))
    assert ok is False
    assert normalized is None
    assert "格式" in reason


def test_reject_dotdot_escape(controlled):
    """用 ../ 从白名单根逃到外面，resolve 后必须落在根外并被拒绝。"""
    sandbox, outside = controlled
    (sandbox / "inside.txt").write_text("ok", encoding="utf-8")
    victim = outside / "outside.txt"
    victim.write_text("secret", encoding="utf-8")

    assert path_guard.validate_read_path(str(sandbox / "inside.txt"))[0] is True

    escape = str(sandbox / ".." / "outside" / "outside.txt")
    assert Path(escape).resolve() == victim.resolve()
    ok, normalized, reason = path_guard.validate_read_path(escape)
    assert ok is False
    assert normalized is None
    assert "允许" in reason


@pytest.mark.parametrize("name", [
    "user_settings.json", "id_rsa.txt", "id_ed25519.txt",
    "server.pem.txt", "prod.env.json", "my_secrets.json",
    "token.txt", "password.txt", "credentials.json",
])
def test_reject_sensitive_filenames(tmp_path, name):
    """命中敏感文件名模式的文件，即使后缀合法、落在白名单内也默认不读。"""
    f = tmp_path / name
    f.write_text("x", encoding="utf-8")
    ok, normalized, reason = path_guard.validate_read_path(str(f))
    assert ok is False
    assert normalized is None
    assert "敏感" in reason, reason


def test_dotenv_is_rejected(tmp_path):
    """.env 没有白名单后缀，同样必须拒绝（后缀检查先于敏感检查也拦得住）。"""
    f = tmp_path / ".env"
    f.write_text("KEY=secret", encoding="utf-8")
    ok, _, reason = path_guard.validate_read_path(str(f))
    assert ok is False
    assert reason


def test_reject_absolute_path_outside_whitelist(controlled):
    """白名单外的绝对路径（真实存在、后缀合法）默认拒绝。"""
    _sandbox, outside = controlled
    victim = outside / "outside.txt"
    victim.write_text("secret", encoding="utf-8")
    assert victim.is_absolute()
    ok, normalized, reason = path_guard.validate_read_path(str(victim))
    assert ok is False
    assert normalized is None
    assert "允许" in reason


def test_reject_python_install_dir_absolute_path():
    """默认白名单不含 Python 安装目录：用真实存在的标准库文件做越界样本。"""
    stdlib_file = json.__file__
    assert Path(stdlib_file).is_absolute()
    ok, normalized, reason = path_guard.validate_read_path(stdlib_file)
    assert ok is False
    assert normalized is None
    assert "允许" in reason


def test_env_var_adds_allowed_dir(controlled, monkeypatch):
    """LINGLING_ALLOWED_PATHS 追加目录后，原本被拒的目录必须放行。"""
    _sandbox, outside = controlled
    f = outside / "note.txt"
    f.write_text("hello", encoding="utf-8")

    assert path_guard.validate_read_path(str(f))[0] is False
    assert outside.resolve() not in path_guard.allowed_roots()

    monkeypatch.setenv("LINGLING_ALLOWED_PATHS", str(outside))
    assert outside.resolve() in path_guard.allowed_roots()
    ok, normalized, reason = path_guard.validate_read_path(str(f))
    assert ok is True, reason
    assert normalized == str(f.resolve())


def test_env_var_accepts_os_pathsep_separated_dirs(controlled, monkeypatch,
                                                   tmp_path):
    """os.pathsep 分隔的多个目录要全部追加，不是只认第一个。"""
    _sandbox, outside = controlled
    second = tmp_path / "second_root"
    second.mkdir()
    (outside / "a.txt").write_text("a", encoding="utf-8")
    (second / "b.txt").write_text("b", encoding="utf-8")

    monkeypatch.setenv("LINGLING_ALLOWED_PATHS",
                       os.pathsep.join([str(outside), str(second)]))
    roots = path_guard.allowed_roots()
    assert outside.resolve() in roots
    assert second.resolve() in roots
    assert path_guard.validate_read_path(str(outside / "a.txt"))[0] is True
    assert path_guard.validate_read_path(str(second / "b.txt"))[0] is True


@pytest.mark.parametrize("raw", [None, "", "   ", "\"\"", "''"])
def test_empty_path_rejected(raw):
    ok, normalized, reason = path_guard.validate_read_path(raw)
    assert ok is False
    assert normalized is None
    assert reason == "路径为空"


def test_nonexistent_path_rejected(tmp_path):
    missing = tmp_path / "no_such_file.txt"
    ok, normalized, reason = path_guard.validate_read_path(str(missing))
    assert ok is False
    assert normalized is None
    assert reason == "文件不存在"


def test_directory_is_rejected(tmp_path):
    d = tmp_path / "folder.txt"      # 目录名带合法后缀，确保不是被后缀拦下
    d.mkdir()
    ok, normalized, reason = path_guard.validate_read_path(str(d))
    assert ok is False
    assert normalized is None
    assert reason == "这不是一个文件"


def test_oversize_file_is_rejected_at_the_boundary(tmp_path, monkeypatch):
    """边界是真断言：恰好等于上限放行，多一个字节就拒绝。"""
    monkeypatch.setattr(path_guard, "MAX_READ_BYTES", 64)
    at_limit = tmp_path / "at_limit.txt"
    at_limit.write_bytes(b"a" * 64)
    ok, _, reason = path_guard.validate_read_path(str(at_limit))
    assert ok is True, reason

    over = tmp_path / "over_limit.txt"
    over.write_bytes(b"a" * 65)
    ok2, normalized2, reason2 = path_guard.validate_read_path(str(over))
    assert ok2 is False
    assert normalized2 is None
    assert "超过" in reason2


def test_reason_never_contains_absolute_path(tmp_path):
    """给用户听的 reason 是中文短句，绝不能把本机绝对路径回显出去。"""
    exe = tmp_path / "payload.exe"
    exe.write_bytes(b"MZ")
    secret = tmp_path / "user_settings.json"
    secret.write_text("{}", encoding="utf-8")
    cases = [
        str(exe),
        str(secret),
        str(tmp_path / "missing.txt"),
        str(tmp_path),
        "",
        json.__file__,
    ]
    for raw in cases:
        ok, _, reason = path_guard.validate_read_path(raw)
        assert ok is False, raw
        assert reason
        assert str(tmp_path) not in reason
        assert str(json.__file__) not in reason
        assert "/" not in reason and "\\" not in reason, reason


def test_symlink_pointing_outside_is_rejected(controlled):
    """符号链接 resolve 后必须仍在白名单内：指向根外的链接要拒。"""
    sandbox, outside = controlled
    (sandbox / "inside.txt").write_text("ok", encoding="utf-8")
    target = outside / "target.txt"
    target.write_text("secret", encoding="utf-8")
    link = sandbox / "link.txt"
    inner = sandbox / "inner_link.txt"
    try:
        os.symlink(str(target), str(link))
        os.symlink(str(sandbox / "inside.txt"), str(inner))
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"本机无法创建符号链接：{e}")

    # 对照：指向白名单内的链接应放行，证明不是"一律拒绝符号链接"
    assert path_guard.validate_read_path(str(inner))[0] is True
    ok, normalized, reason = path_guard.validate_read_path(str(link))
    assert ok is False
    assert normalized is None
    assert "允许" in reason


def test_allowed_roots_and_is_sensitive():
    """allowed_roots 含临时目录；is_sensitive 认敏感目录、放过普通路径。"""
    roots = path_guard.allowed_roots()
    assert isinstance(roots, list) and roots
    assert all(isinstance(r, Path) for r in roots)
    assert Path(tempfile.gettempdir()).resolve() in roots

    assert path_guard.is_sensitive(Path("/home/user/.ssh/id_rsa")) is True
    assert path_guard.is_sensitive(Path("/home/user/project/.git/config")) is True
    assert path_guard.is_sensitive(Path("/home/user/project/notes.txt")) is False
