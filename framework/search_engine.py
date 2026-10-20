# -*- coding: utf-8 -*-
"""本地文件搜索引擎（队友B负责）

负责在本地磁盘查找应用程序：
1. 常见安装目录扫描（Program Files / Program Files (x86) / 开始菜单 / 桌面）
2. Windows 注册表查询（卸载信息 + App Paths）
3. 系统环境变量 PATH 扫描
4. 开发模式（USE_REAL_SYSTEM=False）返回模拟数据
"""

import os
import winreg

from framework import config
from framework.utils import logger, human_size

# 扫描目录的最大深度，防止遍历过深拖慢搜索
MAX_SCAN_DEPTH = 4
# 常见程序安装目录（搜索时逐个扫描）：所有桌面目录都算。
# 延迟到首次搜索时初始化，避免模块导入期就查注册表
_COMMON_INSTALL_DIRS = None


def _common_install_dirs():
    global _COMMON_INSTALL_DIRS
    if _COMMON_INSTALL_DIRS is None:
        _COMMON_INSTALL_DIRS = config.all_desktop_dirs()
    return _COMMON_INSTALL_DIRS


class AppInfo:
    """应用信息（搜索结果）"""

    def __init__(self, name: str, path: str, size: int = 0):
        self.name = name          # 应用显示名，如 "微信"
        self.path = path          # 完整安装路径
        self.size = size          # 文件大小（字节）

    @property
    def size_text(self) -> str:
        return human_size(self.size) if self.size > 0 else "未知"

    def __repr__(self):
        return f"<AppInfo {self.name} {self.path}>"


class SearchEngine:
    """本地文件搜索引擎"""

    def __init__(self):
        # 常见安装目录缓存（首次搜索时构建，加速后续搜索）
        self._dir_cache = None

    # ------------------------------------------------------------
    # 对外主接口
    # ------------------------------------------------------------
    def search(self, app_name: str, exe_names=None):
        """
        搜索应用。
        :param app_name: 应用中文名（如 "微信"）
        :param exe_names: 白名单中该应用对应的 exe 文件名列表
        :return: AppInfo 或 None
        """
        if not config.USE_REAL_SYSTEM:
            return self._mock_search(app_name)

        logger.info(f"[SearchEngine] 开始搜索: {app_name} ({exe_names})")
        # 白名单给了 exe 提示就用；放行模式没有提示时，用"应用名.exe"
        # 作为定位提示（如 steam.exe），避免注册表/目录命中错误 exe
        targets = exe_names or [f"{app_name}.exe"]

        # 依次尝试五种搜索策略，命中即返回。
        # 桌面快捷方式放最前：用户指名"桌面上的程序"，且扫描极快
        for finder in (
            lambda: self.search_desktop_lnk(app_name),
            lambda: self.search_registry(app_name, targets),
            lambda: self.search_dirs(app_name, targets),
            lambda: self.search_path_env(targets),
            lambda: self.search_start_menu(app_name),
        ):
            result = finder()
            if not result:
                continue
            # 注册表的 InstallLocation 常是安装目录而非 exe（如新版微信）：
            # 进目录找真正的可执行文件；找不到则视为未命中，继续下一种策略
            if os.path.isdir(result.path):
                exe = self._exe_in_dir(result.path, targets)
                if not exe:
                    continue
                result = self._make_info(app_name, exe)
            logger.info(f"[SearchEngine] 找到: {result.path}")
            return result

        logger.info(f"[SearchEngine] 未找到: {app_name}")
        return None

    @staticmethod
    def _exe_in_dir(directory, exe_names, max_depth=3):
        """在安装目录里定位真正的可执行文件。

        注册表给的 InstallLocation 常是安装根目录，而真实 exe 可能藏在
        版本子目录里（新版微信 Weixin\\Weixin.exe、豆包 Application\\app\\Doubao.exe），
        所以按白名单 exe 名做深度有限的查找；找不到再按名字包含关系匹配，
        实在拿不准且一层内只有一个 exe 时才采用，避免选错。
        """
        targets = [e.lower() for e in (exe_names or [])]
        if targets:
            for root, dirs, files in os.walk(directory):
                if root[len(directory):].count(os.sep) >= max_depth:
                    dirs[:] = []
                    continue
                lowers = [f.lower() for f in files]
                for t in targets:
                    if t in lowers:
                        return os.path.join(root, t)
        try:
            entries = [f for f in os.listdir(directory)
                       if f.lower().endswith(".exe")]
        except OSError:
            return None
        for f in entries:
            low = f.lower()
            for exe in targets:
                if exe in low or low in exe:
                    return os.path.join(directory, f)
        if len(entries) == 1:
            return os.path.join(directory, entries[0])
        return None

    # ------------------------------------------------------------
    # 策略1：注册表查询
    # ------------------------------------------------------------
    def search_registry(self, app_name: str, exe_names):
        """查询注册表卸载信息与 App Paths，返回应用路径"""
        keys = [
            (winreg.HKEY_LOCAL_MACHINE, config.REGISTRY_KEYS[0]),
            (winreg.HKEY_LOCAL_MACHINE, config.REGISTRY_KEYS[1]),
            (winreg.HKEY_CURRENT_USER, config.REGISTRY_KEYS[0]),
            (winreg.HKEY_LOCAL_MACHINE, config.REGISTRY_KEYS[2]),
        ]
        for hive, sub_key in keys:
            result = self._query_registry(hive, sub_key, app_name, exe_names)
            if result:
                return result
        return None

    def _query_registry(self, hive, sub_key: str, app_name: str, exe_names):
        """遍历某个注册表键，按 DisplayName / 子键名匹配"""
        try:
            root = winreg.OpenKey(hive, sub_key)
        except OSError:
            return None

        with root:
            i = 0
            while True:
                try:
                    child = winreg.EnumKey(root, i)
                    i += 1
                except OSError:
                    break
                try:
                    item = winreg.OpenKey(root, child)
                    with item:
                        # 匹配 DisplayName
                        try:
                            display, _ = winreg.QueryValueEx(item, "DisplayName")
                            if app_name.lower() in display.lower() or \
                               display.lower() in app_name.lower():
                                path = self._get_reg_path(item)
                                if path and os.path.exists(path):
                                    return self._make_info(app_name, path)
                        except OSError:
                            pass
                        # 匹配 exe 名（App Paths 的子键就是 xxx.exe）
                        for exe in exe_names:
                            if child.lower() == exe.lower():
                                path = self._get_reg_path(item)
                                if path and os.path.exists(path):
                                    return self._make_info(exe, path)
                except OSError:
                    continue
        return None

    @staticmethod
    def _get_reg_path(item):
        """从注册表项中取 InstallLocation / DisplayIcon / 默认值作为路径"""
        for value_name in ("InstallLocation", "DisplayIcon", None):
            try:
                value, _ = winreg.QueryValueEx(item, value_name)
                if value:
                    # DisplayIcon 可能是 "path,0" 形式
                    path = str(value).split(",")[0].strip().strip('"')
                    if path.lower().endswith(".exe") and os.path.exists(path):
                        return path
                    if os.path.isdir(path):
                        return path
            except OSError:
                continue
        return None

    # ------------------------------------------------------------
    # 策略2：桌面快捷方式
    # ------------------------------------------------------------
    def search_desktop_lnk(self, app_name: str):
        """在所有桌面（用户桌面 + 公用桌面）查找名称匹配的快捷方式。

        Steam 等游戏平台的快捷方式常落在公用桌面；游戏快捷方式
        既可能是 .lnk 也可能是 .url（如 steam:// 协议快捷方式），
        os.startfile 两种都能按系统默认方式打开。
        匹配分三档：文件名相同 > 以应用名开头 > 包含应用名，
        避免短名（如 EA）被"Steam专注锁"这类无关快捷方式抢中。
        """
        key = app_name.lower()
        exact, prefix, contains = [], [], []
        for desktop in config.all_desktop_dirs():
            try:
                entries = os.listdir(desktop)
            except OSError:
                continue
            for f in entries:
                low = f.lower()
                if not low.endswith((".lnk", ".url")):
                    continue
                stem = os.path.splitext(low)[0]
                path = os.path.join(desktop, f)
                if stem == key:
                    exact.append(path)
                elif stem.startswith(key):
                    prefix.append(path)
                elif key in stem:
                    contains.append(path)
        for tier, candidates in (("同名", exact), ("前缀", prefix),
                                 ("包含", contains)):
            if candidates:
                logger.info(f"[SearchEngine] 桌面快捷方式命中（{tier}）: "
                            f"{candidates[0]}")
                return self._make_info(app_name, candidates[0])
        return None

    # ------------------------------------------------------------
    # 策略3：常见安装目录扫描
    # ------------------------------------------------------------
    def search_dirs(self, app_name: str, exe_names):
        """扫描常见安装目录查找 exe"""
        targets = exe_names or []
        for base_dir in config.SEARCH_DIRS + _common_install_dirs():
            if not os.path.isdir(base_dir):
                continue
            for root, dirs, files in os.walk(base_dir):
                # 限制扫描深度
                depth = root[len(base_dir):].count(os.sep)
                if depth >= MAX_SCAN_DEPTH:
                    dirs[:] = []
                    continue
                # 跳过明显无关的目录，加快速度
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    low = f.lower()
                    if low.endswith(".exe") and (
                        low in [t.lower() for t in targets]
                        or app_name.lower() in low
                    ):
                        path = os.path.join(root, f)
                        return self._make_info(app_name, path)
        return None

    # ------------------------------------------------------------
    # 策略4：系统环境变量 PATH
    # ------------------------------------------------------------
    def search_path_env(self, exe_names):
        """在 PATH 环境变量中查找系统应用（calc/notepad 等）"""
        for exe in exe_names or []:
            for dir_path in os.environ.get("PATH", "").split(os.pathsep):
                candidate = os.path.join(dir_path.strip('"'), exe)
                if os.path.isfile(candidate):
                    return self._make_info(exe, candidate)
        return None

    # ------------------------------------------------------------
    # 策略5：开始菜单快捷方式
    # ------------------------------------------------------------
    def search_start_menu(self, app_name: str):
        """在开始菜单中查找名称匹配的快捷方式（返回 lnk 路径）"""
        menus = [
            r"C:\ProgramData\Microsoft\Windows\Start Menu",
            os.path.join(os.environ.get("APPDATA", ""),
                         r"Microsoft\Windows\Start Menu"),
        ]
        key = app_name.lower()
        for menu in menus:
            if not os.path.isdir(menu):
                continue
            for root, dirs, files in os.walk(menu):
                for f in files:
                    if f.lower().endswith(".lnk") and key in f.lower():
                        path = os.path.join(root, f)
                        return self._make_info(app_name, path)
        return None

    # ------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------
    def _make_info(self, name: str, path: str) -> AppInfo:
        """根据路径构造 AppInfo（读取文件大小）"""
        size = 0
        try:
            if os.path.isfile(path):
                size = os.path.getsize(path)
        except OSError:
            pass
        return AppInfo(name=name, path=path, size=size)

    @staticmethod
    def _mock_search(app_name: str) -> AppInfo:
        """开发模式：返回模拟搜索结果，不依赖真实系统"""
        fake_paths = {
            "微信": r"C:\Program Files\Tencent\WeChat\WeChat.exe",
            "qq": r"C:\Program Files (x86)\Tencent\QQ\QQ.exe",
            "记事本": r"C:\Windows\System32\notepad.exe",
            "计算器": r"C:\Windows\System32\calc.exe",
            "画图": r"C:\Windows\System32\mspaint.exe",
        }
        path = fake_paths.get(app_name,
                             rf"C:\Program Files\Mock\{app_name}\{app_name}.exe")
        size = 12345678 if app_name not in fake_paths else 0
        try:
            if os.path.isfile(path):
                size = os.path.getsize(path)
        except OSError:
            pass
        logger.info(f"[SearchEngine][模拟] 找到: {app_name} -> {path}")
        return AppInfo(name=app_name, path=path, size=size)
