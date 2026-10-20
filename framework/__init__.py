# -*- coding: utf-8 -*-
"""framework 包初始化

注意：这里【不要】导入 framework.core。
否则任何 `from framework.xxx import yyy` 都会连锁导入 PyQt5，
导致 API 服务在无 GUI 环境（Docker / Linux 服务器 / CI）无法运行。

桌面版请显式导入：from framework.core import Framework
"""

__all__ = []