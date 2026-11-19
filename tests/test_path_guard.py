# -*- coding: utf-8 -*-
"""单元测试：本机文件读取边界 framework/path_guard.py

覆盖：三态判定 allow / grant / deny 各自该落在哪、格式表逐条放行、敏感项**给了
授权也不读**、../ 与符号链接逃逸不会继承根内的放行、授权按目录段而不是字符串前缀
匹配、file 档授权不顺手放开父目录、LINGLING_ALLOWED_PATHS 追加根、空路径 / 不存在 /
超大文件边界，以及 reason 不回显绝对路径。

判据为什么从「两态」改成「三态」：老版本对白名单外的路径一律拒，等于顺手把用户
放在 D:\\、E:\\ 自己的资料也挡死了。现在的口径是 **默认不读，但要问一次**：
不在根里 ≠ 能读，必须先拿到 grant=="grant" 的显式确认。所以这一版把断言写成
「没授权时绝不 allow」，比「一律拒」更强：它同时钉住了逃逸、兄弟目录、父目录
三种类越权路径，而不是只钉住一条返回值。

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
from framework.grants import GRANT_FILE, GRANT_TREE, GrantStore


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


@pytest.mark.parametrize("suffix", [".exe", ".dll", ".bin", ".so", ".7z", ".png"])
def test_reject_extensions_not_in_the_format_table(tmp_path, suffix):
    """不在格式表里的后缀（可执行/图片/私有压缩格式）直接 deny，连问都不问。"""
    f = tmp_path / ("payload" + suffix)
    f.write_bytes(b"MZ\x00\x01")
    verdict, normalized, reason = path_guard.check_read(str(f))
    assert verdict == "deny"
    assert normalized is None
    assert "格式" in reason

    # deny 是终态：给了整棵树的授权也读不出来
    g = GrantStore()
    g.add(GRANT_TREE, tmp_path)
    assert path_guard.check_read(str(f), grants=g)[0] == "deny"


@pytest.mark.parametrize("suffix", [".bat", ".cmd", ".ps1", ".sh"])
def test_scripts_are_readable_as_text(tmp_path, suffix):
    """脚本按纯文本读（用户会让我看他的 .bat 启动脚本）；内容是二进制时由
    file_reader 的 NUL 嗅探拦下，不是在路径层一刀切。"""
    f = tmp_path / ("start" + suffix)
    f.write_text("@echo off\r\necho hi\r\n", encoding="utf-8")
    assert path_guard.check_read(str(f))[0] == "allow"
    assert path_guard.kind_of(f) == "text"


def test_dotdot_escape_never_inherits_the_inside_grant(controlled):
    """用 ../ 从白名单根逃到外面：既不能 allow，也不能继承根内的放行。

    这条比老版本的「返回 False」更硬：先证明根内的真文件确实 allow，
    再证明指向同一个外部文件的逃逸写法只能拿到 grant。
    """
    sandbox, outside = controlled
    (sandbox / "inside.txt").write_text("ok", encoding="utf-8")
    victim = outside / "outside.txt"
    victim.write_text("secret", encoding="utf-8")

    assert path_guard.check_read(str(sandbox / "inside.txt"))[0] == "allow"

    escape = str(sandbox / ".." / "outside" / "outside.txt")
    assert Path(escape).resolve() == victim.resolve()
    verdict, normalized, reason = path_guard.check_read(escape)
    assert verdict == "grant"
    assert normalized == str(victim.resolve())   # 归一化到真实路径，不做字符串把戏
    assert "授权" in reason

    # 根内 tree 授权绝不外溢：给了 sandbox 整棵树，逃逸路径仍然要再问一次
    g = GrantStore()
    g.add(GRANT_TREE, sandbox)
    assert path_guard.check_read(escape, grants=g)[0] == "grant"
    assert path_guard.check_read(str(victim), grants=g)[0] == "grant"
    # 老接口在没有授权的前提下不能放行
    assert path_guard.validate_read_path(escape)[0] is False


@pytest.mark.parametrize("name", [
    "user_settings.json", "id_rsa.txt", "id_ed25519.txt",
    "server.pem.txt", "prod.env.json", "my_secrets.json",
    "token.txt", "password.txt", "credentials.json",
])
def test_sensitive_filenames_denied_even_with_grant(tmp_path, name):
    """命中敏感文件名模式的文件：后缀合法、给了整树授权、追加了环境变量根，也一律 deny。

    这一条是整个授权设计的底线——「用户点了允许」不能变成「什么都能读」。
    """
    f = tmp_path / name
    f.write_text("x", encoding="utf-8")
    verdict, normalized, reason = path_guard.check_read(str(f))
    assert verdict == "deny"
    assert normalized is None
    assert "敏感" in reason, reason

    g = GrantStore()
    g.add(GRANT_FILE, f)
    g.add(GRANT_TREE, tmp_path)
    assert path_guard.check_read(str(f), grants=g)[0] == "deny"
    assert path_guard.validate_read_path(str(f)) == (False, None, reason)


def test_dotenv_is_rejected(tmp_path):
    """.env 没有白名单后缀，同样必须拒绝（后缀检查先于敏感检查也拦得住）。"""
    f = tmp_path / ".env"
    f.write_text("KEY=secret", encoding="utf-8")
    verdict, _, reason = path_guard.check_read(str(f))
    assert verdict == "deny"
    assert reason


def test_path_outside_roots_requires_grant(controlled):
    """白名单外的普通文件：不是 allow，也不是终态 deny，而是「要问一次」。"""
    _sandbox, outside = controlled
    victim = outside / "outside.txt"
    victim.write_text("secret", encoding="utf-8")
    assert victim.is_absolute()

    verdict, normalized, reason = path_guard.check_read(str(victim))
    assert verdict == "grant"
    assert normalized == str(victim.resolve())
    assert "授权" in reason
    # 老接口（不弹确认的调用方，比如网页入库）在没有授权时仍然不放行
    ok, path, reason2 = path_guard.validate_read_path(str(victim))
    assert ok is False
    assert path == normalized
    assert reason2 == reason

    # 用户点「只读这一个」后，这一个文件 allow，但目录本身仍然要再问
    g = GrantStore()
    g.add(GRANT_FILE, victim)
    assert path_guard.check_read(str(victim), grants=g)[0] == "allow"
    assert path_guard.check_read(str(outside), grants=g)[0] == "grant"

    # 点「完全访问这个文件夹」后，整棵子树 allow
    g2 = GrantStore()
    g2.add(GRANT_TREE, outside)
    assert path_guard.check_read(str(victim), grants=g2)[0] == "allow"
    assert path_guard.check_read(str(outside), grants=g2)[0] == "allow"


def test_tree_grant_does_not_leak_to_sibling_dir(controlled):
    """授权 D:\\…\\foo 不能顺手放开 foo2：按路径段比对，不做字符串前缀匹配。"""
    _sandbox, outside = controlled
    inner = outside / "foo"
    sibling = outside / "foo2"
    inner.mkdir()
    sibling.mkdir()
    (inner / "a.txt").write_text("a", encoding="utf-8")
    (sibling / "b.txt").write_text("b", encoding="utf-8")

    g = GrantStore()
    g.add(GRANT_TREE, inner)
    assert path_guard.check_read(str(inner / "a.txt"), grants=g)[0] == "allow"
    assert path_guard.check_read(str(sibling / "b.txt"), grants=g)[0] == "grant"
    assert path_guard.check_read(str(sibling), grants=g)[0] == "grant"
    assert g.covers(sibling) == ""


def test_python_install_dir_is_not_a_default_root():
    """默认白名单不含 Python 安装目录：用真实存在的标准库文件做越界样本。"""
    stdlib_file = json.__file__
    assert Path(stdlib_file).is_absolute()
    verdict, normalized, reason = path_guard.check_read(stdlib_file)
    assert verdict != "allow"
    assert path_guard.validate_read_path(stdlib_file)[0] is False
    assert reason
    assert Path(tempfile.gettempdir()).resolve() not in Path(stdlib_file).parents


def test_env_var_adds_allowed_dir(controlled, monkeypatch):
    """LINGLING_ALLOWED_PATHS 追加目录后，原本「要问一次」的目录永久放行。"""
    _sandbox, outside = controlled
    f = outside / "note.txt"
    f.write_text("hello", encoding="utf-8")

    assert path_guard.check_read(str(f))[0] == "grant"
    assert outside.resolve() not in path_guard.allowed_roots()

    monkeypatch.setenv("LINGLING_ALLOWED_PATHS", str(outside))
    assert outside.resolve() in path_guard.allowed_roots()
    verdict, normalized, reason = path_guard.check_read(str(f))
    assert verdict == "allow", reason
    assert normalized == str(f.resolve())
    # 永久口子不需要授权记录，也不受 grants 影响
    assert path_guard.check_read(str(f), grants=GrantStore())[0] == "allow"


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


def test_directory_inside_root_is_listable(tmp_path):
    """目录不再是「这不是一个文件」直接拒：根内的目录 kind=="dir"，可以列一层。"""
    d = tmp_path / "notes"
    d.mkdir()
    verdict, normalized, reason = path_guard.check_read(str(d))
    assert verdict == "allow", reason
    assert path_guard.kind_of(Path(normalized)) == "dir"
    # 目录名带合法后缀也一样按目录算，不被后缀误判成文本
    df = tmp_path / "folder.txt"
    df.mkdir()
    assert path_guard.kind_of(df) == "dir"


def test_directory_outside_root_needs_its_own_grant(controlled):
    """白名单外的目录要单独授权；只给里面某个文件的 file 档，不放开目录本身。"""
    _sandbox, outside = controlled
    f = outside / "a.txt"
    f.write_text("a", encoding="utf-8")
    assert path_guard.check_read(str(outside))[0] == "grant"
    g = GrantStore()
    g.add(GRANT_FILE, f)
    assert path_guard.check_read(str(outside), grants=g)[0] == "grant"


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


def test_reason_never_contains_absolute_path(controlled, tmp_path):
    """给用户/模型看的 reason 是中文短句，绝不能把本机绝对路径回显出去。

    allow 的 reason 必须是空串；deny 与 grant 必须给出一句能念出来的话。
    """
    sandbox, outside = controlled
    exe = tmp_path / "payload.exe"
    exe.write_bytes(b"MZ")
    secret = tmp_path / "user_settings.json"
    secret.write_text("{}", encoding="utf-8")
    victim = outside / "outside.txt"
    victim.write_text("x", encoding="utf-8")
    blocked = [                      # deny 或 grant，两者都必须有 reason
        str(exe), str(secret), str(outside / "missing.txt"), "",
        json.__file__, str(victim),
    ]
    for raw in blocked:
        verdict, _, reason = path_guard.check_read(raw)
        assert verdict in ("deny", "grant"), raw
        assert reason, raw
        assert str(tmp_path) not in reason
        assert str(outside) not in reason
        assert str(json.__file__) not in reason
        assert "/" not in reason and "\\" not in reason, reason

    # 对照：白名单内正常放行时不产生任何文案（sandbox 才是本用例的根）
    good = sandbox / "ok.txt"
    good.write_text("x", encoding="utf-8")
    assert path_guard.check_read(str(good)) == ("allow", str(good.resolve()), "")


def test_symlink_pointing_outside_cannot_inherit_the_root(controlled):
    """符号链接 resolve 后必须仍在白名单内：指向根外的链接只能拿到 grant。"""
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
    assert path_guard.check_read(str(inner))[0] == "allow"
    verdict, normalized, reason = path_guard.check_read(str(link))
    assert verdict == "grant"
    assert normalized == str(target.resolve())   # 报的是真实目标，不是链接本身
    assert "授权" in reason

    # 给了 sandbox 整棵树，也读不到指向外面的链接
    g = GrantStore()
    g.add(GRANT_TREE, sandbox)
    assert path_guard.check_read(str(link), grants=g)[0] == "grant"
    assert path_guard.check_read(str(inner), grants=g)[0] == "allow"
    assert path_guard.validate_read_path(str(link))[0] is False


def test_allowed_roots_and_is_sensitive():
    """allowed_roots 含临时目录；is_sensitive 认敏感目录、放过普通路径。"""
    roots = path_guard.allowed_roots()
    assert isinstance(roots, list) and roots
    assert all(isinstance(r, Path) for r in roots)
    assert Path(tempfile.gettempdir()).resolve() in roots

    assert path_guard.is_sensitive(Path("/home/user/.ssh/id_rsa")) is True
    assert path_guard.is_sensitive(Path("/home/user/project/.git/config")) is True
    assert path_guard.is_sensitive(Path("/home/user/project/notes.txt")) is False


def test_sensitive_dir_denied_even_with_tree_grant(controlled):
    """整树授权落进 .ssh 这类敏感目录时，仍然是 deny——授权不覆盖敏感判定。"""
    _sandbox, outside = controlled
    ssh = outside / ".ssh"
    ssh.mkdir()
    key = ssh / "server_key.txt"
    key.write_text("secret", encoding="utf-8")

    g = GrantStore()
    g.add(GRANT_TREE, outside)
    assert path_guard.check_read(str(outside), grants=g)[0] == "allow"
    assert path_guard.check_read(str(key), grants=g)[0] == "deny"
    # 目录本身名字就命中敏感表，连列一层都不给
    assert path_guard.check_read(str(ssh), grants=g)[0] == "deny"


def test_kind_of_follows_the_format_tables(tmp_path):
    """判据只有一张表：kind_of 的归类必须和分组常量一致，界面文案才不会各说各话。"""
    assert path_guard.ALLOWED_EXTS == (path_guard.TEXT_EXTS
                                       | path_guard.DOC_EXTS
                                       | path_guard.ARCHIVE_EXTS)
    expect = {
        "a.txt": "text", "b.cpp": "text", "c.ps1": "text", "d.md": "text",
        "e.docx": "office", "f.pptx": "office", "g.xlsx": "office",
        "h.doc": "legacy", "i.xls": "legacy", "j.ppt": "legacy",
        "k.pdf": "pdf", "l.zip": "archive", "m.tar.gz": "archive",
        "n.exe": "unknown", "o.png": "unknown",
    }
    for name, kind in expect.items():
        f = tmp_path / name
        f.write_bytes(b"x")
        assert path_guard.kind_of(f) == kind, name
    d = tmp_path / "a_dir"
    d.mkdir()
    assert path_guard.kind_of(d) == "dir"
    # 没后缀的日常文本名（Dockerfile / README）按文本试读
    for name in ("Dockerfile", "README", ".gitignore"):
        f = tmp_path / name
        f.write_text("x", encoding="utf-8")
        assert path_guard.kind_of(f) == "text", name


def test_advertised_formats_are_actually_readable(tmp_path):
    """文案里念出来的每个后缀，判据必须真认——不然又是一处「说了做不到」。"""
    advertised = path_guard.advertised_exts()
    assert advertised <= path_guard.ALLOWED_EXTS, advertised - path_guard.ALLOWED_EXTS
    sentence = path_guard.describe_formats()
    for ext in sorted(advertised):
        assert ext.lstrip(".") in sentence, ext
        f = tmp_path / ("probe" + ext)
        f.write_bytes(b"x")
        assert path_guard.check_read(str(f))[0] == "allow", ext
    # 「文件夹本身」这条也要能念出来，并且 kind_of 真的按目录算
    assert "文件夹" in sentence
    d = tmp_path / "probe_dir"
    d.mkdir()
    assert path_guard.kind_of(d) == "dir"
