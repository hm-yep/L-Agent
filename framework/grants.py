# -*- coding: utf-8 -*-
"""会话内路径授权：白名单外的路径要用户点一次头，而且这次点头只在本次运行里有效。

为什么需要这一层：`path_guard` 的默认拒绝既挡住了密钥和系统目录，也一并挡住了
用户自己放在 D:\\、E:\\ 里的资料。直接放开成「什么路径都能读」等于把边界拆掉，
所以这里做的是**逐级放大的一次性许可**：

  file  —— 只允许这一个文件（或这一个目录本身列一层）；
  tree  —— 允许这个目录及其整棵子树（用户说的「完全访问」）。

两档都只活在本次进程里，不落盘：重启后回到默认拒绝，因为「上次给过」不等于
「这次还要给」。命中判据只在这里算一次，桌面气泡和网页确认条共用同一个
`Framework.answer_grant` 入口，前端不分治。

敏感项永远不认（见 `path_guard.is_sensitive`）：用户点了「允许整个文件夹」，
`.ssh`、`.env`、凭据文件仍然读不到。本模块只依赖标准库，不 import Qt / 文档库。
"""

import os


GRANT_FILE = "file"
GRANT_TREE = "tree"
LEVELS = (GRANT_FILE, GRANT_TREE)


def _key(path) -> str:
    """归一化成一个可比较的键：绝对路径 + 真实解析（跟符号链接/.. 逃逸对齐）。

    Windows 上大小写不敏感，所以统一 normcase——否则 `D:\\资料` 与 `d:\\资料`
    会被当成两个目录，用户点过一次还要再点一次。
    """
    try:
        return os.path.normcase(os.path.realpath(os.path.abspath(str(path))))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(str(path)))


def is_within(child_key: str, parent_key: str) -> bool:
    """child 是否等于 parent 或落在 parent 子树内（按路径段比对，不做前缀字符串匹配）。

    纯字符串 startswith 会把 `D:\\foo2` 判进 `D:\\foo` 的子树，那是越权。
    """
    if child_key == parent_key:
        return True
    return child_key.startswith(
        parent_key.rstrip(os.sep) + os.sep)


class GrantStore:
    """本次运行内的授权集合。只增不持久化，`clear()` 一次全撤。"""

    def __init__(self):
        self._file = set()      # 归一化后的文件键
        self._tree = set()      # 归一化后的目录键
        self._shown = {}        # 键 → 展示用原始路径（给确认列表看，不影响判据）

    def add(self, level: str, path) -> bool:
        if level not in LEVELS:
            return False
        k = _key(path)
        if level == GRANT_FILE:
            self._file.add(k)
        else:
            self._tree.add(k)
        self._shown[k] = str(path)
        return True

    def covers(self, path) -> str:
        """这条路径已被允许吗？返回命中的级别（file/tree），没命中返回空串。"""
        k = _key(path)
        if k in self._file:
            return GRANT_FILE
        for parent in self._tree:
            if is_within(k, parent):
                return GRANT_TREE
        return ""

    def clear(self):
        self._file.clear()
        self._tree.clear()
        self._shown.clear()

    def entries(self) -> list:
        """当前授权清单（展示用）。判据一律走 covers()，不要拿这份去比对。"""
        out = [{"level": GRANT_FILE, "path": self._shown.get(k, k)}
               for k in sorted(self._file)]
        out += [{"level": GRANT_TREE, "path": self._shown.get(k, k)}
                for k in sorted(self._tree)]
        return out

    def __len__(self) -> int:
        return len(self._file) + len(self._tree)
