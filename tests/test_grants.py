# -*- coding: utf-8 -*-
"""单元测试：会话内路径授权 framework/grants.py

覆盖：Windows 大小写/分隔符归一化后只算一次授权、tree 档按路径段包含（兄弟目录
`foo2` 不落在 `foo` 子树里）、file 档不放开父目录、非法档位拒绝记录、clear 一次全撤、
展示清单与实际判据同源，以及最要紧的一条契约——**授权不落盘**：本模块不 import
任何序列化库、不调用 open()，重启必然回到默认拒绝。

不需要联网、不需要 Qt：纯标准库的字符串与集合判定。
"""

import ast
import inspect
import os
import sys
from pathlib import Path

import pytest

from framework import grants
from framework.grants import GRANT_FILE, GRANT_TREE, GrantStore, is_within


@pytest.fixture(autouse=True)
def fresh_store():
    """每个用例都用新建的 store，绝不用模块级单例，避免用例之间互相放开路径。"""
    return GrantStore()


def test_levels_are_exactly_two():
    """用户要的是「是否允许完全访问」两档，多出第三档就是自作主张。"""
    assert grants.LEVELS == (GRANT_FILE, GRANT_TREE)


@pytest.mark.parametrize("parent,child,expect", [
    (r"D:\foo", r"D:\foo", True),
    (r"D:\foo", r"D:\foo\a.txt", True),
    (r"D:\foo", r"D:\foo\bar\b.txt", True),
    (r"D:\foo", r"D:\foo2", False),          # 纯字符串前缀会误判成子树
    (r"D:\foo", r"D:\foo2\b.txt", False),
    (r"D:\foo", r"D:\foo\b.txt", True),
    (r"D:\foo", r"D:\bar", False),
    (r"D:\foo", r"C:\foo\a.txt", False),
])
def test_is_within_compares_path_segments(parent, child, expect):
    # 签名是 is_within(child_key, parent_key)：child 是否落在 parent 子树内
    assert is_within(grants._key(child), grants._key(parent)) is expect


def test_root_itself_is_within_itself():
    assert is_within(grants._key("D:\\"), grants._key("D:\\")) is True


def test_file_grant_covers_only_that_path(fresh_store, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    other = tmp_path / "b.txt"
    other.write_text("x", encoding="utf-8")

    assert fresh_store.covers(f) == ""
    fresh_store.add(GRANT_FILE, f)
    assert fresh_store.covers(f) == GRANT_FILE
    assert fresh_store.covers(other) == ""
    # file 档绝不顺手放开父目录
    assert fresh_store.covers(tmp_path) == ""


def test_tree_grant_covers_subtree(fresh_store, tmp_path):
    inner = tmp_path / "proj" / "src"
    inner.mkdir(parents=True)
    f = inner / "main.py"
    f.write_text("print(1)", encoding="utf-8")

    fresh_store.add(GRANT_TREE, tmp_path / "proj")
    assert fresh_store.covers(f) == GRANT_TREE
    assert fresh_store.covers(tmp_path / "proj") == GRANT_TREE
    assert fresh_store.covers(tmp_path) == ""          # 只往下放开，不往上


def test_case_and_separator_insensitive_on_windows(fresh_store, tmp_path):
    """同一个目录换种写法（大小写 / 正反斜杠 / 末尾分隔符）不能要求用户再点一次。"""
    d = tmp_path / "资料"
    d.mkdir()
    fresh_store.add(GRANT_TREE, d)
    variants = [str(d), str(d) + os.sep, str(d).replace("\\", "/"),
                str(d).lower(), str(d).upper()]
    for v in variants:
        got = fresh_store.covers(v)
        if os.name == "nt":
            assert got == GRANT_TREE, v
        else:
            assert got in (GRANT_TREE, ""), v


def test_relative_and_absolute_forms_are_the_same_grant(fresh_store, monkeypatch,
                                                        tmp_path):
    """相对路径与它的绝对形式必须命中同一条授权，否则换个写法就绕过判据。"""
    d = tmp_path / "notes"
    d.mkdir()
    (d / "a.txt").write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    fresh_store.add(GRANT_FILE, Path("notes/a.txt"))
    assert fresh_store.covers(d / "a.txt") == GRANT_FILE
    assert fresh_store.covers("notes/a.txt") == GRANT_FILE


def test_bad_level_is_not_recorded(fresh_store, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    assert fresh_store.add("always", f) is False
    assert fresh_store.add("", f) is False
    assert len(fresh_store) == 0
    assert fresh_store.covers(f) == ""


def test_clear_revokes_everything(fresh_store, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    fresh_store.add(GRANT_TREE, d)
    fresh_store.add(GRANT_FILE, tmp_path / "x.txt")
    assert len(fresh_store) == 2
    fresh_store.clear()
    assert len(fresh_store) == 0
    assert fresh_store.covers(d / "a.txt") == ""
    assert fresh_store.entries() == []


def test_entries_is_display_only(fresh_store, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    fresh_store.add(GRANT_TREE, d)
    fresh_store.add(GRANT_FILE, tmp_path / "x.txt")
    listed = fresh_store.entries()
    assert {e["level"] for e in listed} == {GRANT_FILE, GRANT_TREE}
    assert all(e["path"] for e in listed)
    assert len(listed) == len(fresh_store) == 2


def test_grants_never_touch_the_disk():
    """授权只活在本次进程里：这个模块不许 import 序列化库、不许 open()。

    为什么单独钉这一条：一旦有人为了「省一次点击」把授权写进 user_settings.json，
    就等于悄悄把一次性许可变成了永久口子，而那份 JSON 还会被 read_doc 读到。
    """
    src = inspect.getsource(sys.modules[grants.__name__])
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"os"}, imported
    assert "open(" not in src.replace(" ", "")
    assert not any(hasattr(grants, n) for n in ("json", "pickle", "shelve", "sqlite3"))


def test_no_qt_at_import_time():
    """判据层不能拖上 Qt：网页端与单测都要能单独 import。"""
    src = Path(grants.__file__).read_text(encoding="utf-8")
    assert "PySide" not in src and "QtCore" not in src
