# -*- coding: utf-8 -*-
"""pytest 全局隔离：所有落盘数据都进临时目录，绝不写脏真实 data/。

必须在任何 framework 模块被 import 之前设置 LINGLING_DATA_DIR——
framework.utils.app_data_dir() 会优先读它。轨迹、偏好、规则、记忆、
任务台、日志都随之落在临时目录里，测试运行完由系统清理。
"""

import os
import tempfile

_TEST_DATA_DIR = tempfile.mkdtemp(prefix="lingling_pytest_")
os.environ.setdefault("LINGLING_DATA_DIR", _TEST_DATA_DIR)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
