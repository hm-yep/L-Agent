# -*- coding: utf-8 -*-
"""系统控制器：查找 + 启动应用（队友B负责）

对上层的唯一接口：
    find_app(app_name) -> AppInfo | None      查找应用（配合 SearchEngine）
    launch_app(app_name) -> (bool, str)       启动应用（白名单安全拦截）

开发模式（USE_REAL_SYSTEM=False）不执行真实启动，只返回模拟结果。
"""

import os
import subprocess

from framework import config
from framework.search_engine import SearchEngine, AppInfo
from framework.utils import logger

# "虚拟应用"别名：没有对应 exe，用系统默认方式打开
# （os.startfile 直接支持 URL 和 shell 命名空间 CLSID）
_VIRTUAL_APPS = {
    "浏览器":   "https://www.baidu.com",
    "browser":  "https://www.baidu.com",
    "上网":     "https://www.baidu.com",
    "此电脑":   "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}",
    "我的电脑": "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}",
    "回收站":   "::{645FF040-5081-101B-9F80-08002B30309D}",
    "控制面板": "::{21EC2020-3AEA-1069-A2DD-08002B30309D}",
}


class SystemController:
    """系统控制器：应用查找 + 白名单安全启动"""

    def __init__(self):
        self.search_engine = SearchEngine()
        # 保存最近一次查找结果，供"打开"动作复用（避免重复搜索）
        self._last_found = {}

    # ------------------------------------------------------------
    # 查找应用
    # ------------------------------------------------------------
    def find_app(self, app_name: str):
        """
        查找应用（先做白名单解析拿 exe 提示，再交给搜索引擎）。
        ALLOW_ALL_APPS=True 时不限制：不在白名单的也按名字直接搜。
        :return: AppInfo 或 None
        """
        name, exe_names = self.resolve_whitelist(app_name)
        if name is None:
            if not config.ALLOW_ALL_APPS:
                logger.info(f"[System] {app_name} 不在白名单内")
                return None
            name, exe_names = app_name.strip(), None   # 按名字直接搜
        info = self.search_engine.search(name, exe_names)
        if info:
            self._last_found[name] = info
        return info

    def resolve_whitelist(self, app_name: str):
        """
        白名单解析：把用户的说法映射到 (标准名, exe列表)。
        支持精确匹配和子串模糊匹配。
        :return: (标准名, exe列表) 或 (None, None) 表示不在白名单
        """
        key = app_name.strip().lower()

        # 1. 精确匹配
        if key in config.APP_WHITELIST:
            return key, config.APP_WHITELIST[key]

        # 2. 模糊匹配：白名单键包含用户输入 或 用户输入包含白名单键。
        #    反向包含（用户输入 ⊂ 白名单键）容易误命中短名，
        #    如 "ea" ⊂ "steam"，只对 3 字以上的输入启用该方向
        for wl_key, exes in config.APP_WHITELIST.items():
            if wl_key in key:
                return wl_key, exes
            if len(key) >= 3 and key in wl_key:
                return wl_key, exes

        return None, None

    def is_whitelisted(self, app_name: str) -> bool:
        """判断应用是否允许打开（放行模式下全部允许）"""
        if config.ALLOW_ALL_APPS:
            return True
        name, _ = self.resolve_whitelist(app_name)
        return name is not None

    # ------------------------------------------------------------
    # 启动应用
    # ------------------------------------------------------------
    def launch_app(self, app_name: str):
        """
        启动应用（白名单关闭时拒绝；ALLOW_ALL_APPS=True 时全部放行）。
        :return: (成功与否, 提示消息)
        """
        # ---- 安全拦截（仅白名单模式）----
        name, exe_names = self.resolve_whitelist(app_name)
        if name is None:
            if not config.ALLOW_ALL_APPS:
                logger.warning(f"[System] 安全拦截: {app_name} 不在白名单")
                return False, f"{app_name} 不在安全白名单内，拒绝打开"
            name, exe_names = app_name.strip(), None   # 按名字直接找

        # ---- 虚拟应用（浏览器/此电脑等）：无 exe，直接系统默认方式打开 ----
        virtual = _VIRTUAL_APPS.get(name.lower())
        if virtual is not None:
            try:
                os.startfile(virtual)  # noqa: S606
                logger.info(f"[System] 虚拟应用打开成功: {name} <- {virtual}")
                return True, f"已打开{name}"
            except OSError as e:
                logger.error(f"[System] 虚拟应用打开失败: {name} ({e})")
                return False, f"打不开{name}"

        if not config.USE_REAL_SYSTEM:
            return self._mock_launch(name)

        # ---- 优先复用查找缓存，否则现场查找 ----
        info = self._last_found.get(name)
        if info is None or not os.path.exists(info.path):
            info = self.find_app(name)

        path = info.path if info else None
        if path is None:
            # 尝试内置应用（系统自带，无需磁盘路径）
            for exe in exe_names or []:
                if exe.lower() in config.BUILTIN_APPS:
                    return self._start_builtin(config.BUILTIN_APPS[exe.lower()])
            return False, f"未找到 {name}，请检查是否已安装"

        return self._launch_path(name, path, exe_names)

    def _launch_path(self, name: str, path: str, exe_names):
        """通过路径启动应用"""
        # 防线：有些路径来源给的是安装目录（注册表 InstallLocation）。
        # 把文件夹丢给 os.startfile 只会打开资源管理器，先尝试解析出 exe
        if os.path.isdir(path):
            exe = self.search_engine._exe_in_dir(path, exe_names)
            if not exe:
                return False, f"未找到 {name} 的可执行文件"
            path = exe
        if not path.lower().endswith(".exe"):
            return self._start_shell(path, name)

        try:
            # 先尝试内置方式（某些系统应用必须用 shell 协议）
            if os.path.basename(path).lower() in config.BUILTIN_APPS:
                ok, msg = self._start_builtin(
                    config.BUILTIN_APPS[os.path.basename(path).lower()]
                )
                if ok:
                    return ok, msg

            # DETACHED_PROCESS：让应用独立于助手运行
            subprocess.Popen(
                [path],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
            logger.info(f"[System] 启动成功: {name} <- {path}")
            return True, f"已启动 {name}"
        except OSError as e:
            logger.error(f"[System] 启动失败: {path} ({e})")
            # 回退：尝试内置方式
            for exe in exe_names or []:
                if exe.lower() in config.BUILTIN_APPS:
                    return self._start_builtin(config.BUILTIN_APPS[exe.lower()])
            return False, f"打不开 {name}"

    def _start_builtin(self, shell_verb: str):
        """通过 shell 内置名启动系统应用（calc / notepad / mspaint ...）"""
        try:
            subprocess.Popen(
                shell_verb,
                shell=True,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            logger.info(f"[System] 内置启动成功: {shell_verb}")
            return True, f"已启动 {shell_verb}"
        except OSError as e:
            logger.error(f"[System] 内置启动失败: {shell_verb} ({e})")
            return False, "打不开该应用"

    def _start_shell(self, path: str, name: str):
        """用系统默认方式打开（快捷方式 lnk 等）"""
        try:
            os.startfile(path)  # noqa: S606
            logger.info(f"[System] shell 启动成功: {name} <- {path}")
            return True, f"已启动 {name}"
        except OSError as e:
            logger.error(f"[System] shell 启动失败: {path} ({e})")
            return False, f"打不开 {name}"

    # ------------------------------------------------------------
    # 模拟模式
    # ------------------------------------------------------------
    @staticmethod
    def _mock_launch(name: str):
        """开发模式：模拟启动，不真正打开任何程序"""
        logger.info(f"[System][模拟] 启动 {name} 成功")
        return True, f"[模拟] 已启动 {name}"
