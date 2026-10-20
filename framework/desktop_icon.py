# -*- coding: utf-8 -*-
"""桌面图标定位（纯标准库 ctypes，无需额外依赖）

读取 Windows 桌面 ListView 中每个图标的名称与屏幕坐标，
用于让助手"走到那个软件那里去"再打开它。

    from framework.desktop_icon import desktop_icon_position
    pos = desktop_icon_position("微信")     # -> (17, 654) 或 None
    icons = all_desktop_icons()             # -> {"微信": (17, 654), ...}

实现方式：
    找到桌面 ListView 句柄 -> 跨进程读取条目文本(LVM_GETITEMW)
    -> 读取图标坐标(LVM_GETITEMPOSITION) -> 加上窗口左上角得到屏幕坐标。
读取失败（权限/系统版本差异）时静默返回 None，调用方自行降级。
"""

import ctypes
import ctypes.wintypes as wt

import logging

logger = logging.getLogger("desktop_pet")

# ListView 消息
LVM_GETITEMCOUNT = 0x1004
LVM_GETITEMW = 0x104B
LVM_GETITEMPOSITION = 0x1010
LVIF_TEXT = 0x0001
PROCESS_VM_OP = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_QUERY_INFO = 0x0400
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class LVITEMW(ctypes.Structure):
    _fields_ = [
        ("mask", wt.UINT), ("iItem", ctypes.c_int), ("iSubItem", ctypes.c_int),
        ("state", wt.UINT), ("stateMask", wt.UINT), ("pszText", ctypes.c_void_p),
        ("cchTextMax", ctypes.c_int), ("iImage", ctypes.c_int),
        ("lParam", wt.LPARAM), ("iIndent", ctypes.c_int), ("iGroupId", ctypes.c_int),
        ("cColumns", wt.UINT), ("puColumns", ctypes.c_void_p),
        ("piColFmt", ctypes.c_void_p), ("iGroup", ctypes.c_int),
    ]


def _find_desktop_listview():
    """定位桌面图标所在的 SysListView32 句柄"""
    user32 = ctypes.windll.user32
    progman = user32.FindWindowW("Progman", None)
    shell_view = user32.FindWindowExW(progman, 0, "SHELLDLL_DefView", None)

    if not shell_view:
        # Windows 多桌面：向 Progman 发消息让系统生成 WorkerW 容器
        result = wt.DWORD()
        user32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0, 200,
                                   ctypes.byref(result))

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        workers = []

        def _cb(hwnd, _lparam):
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if cls.value == "WorkerW":
                workers.append(hwnd)
            return True

        user32.EnumWindows(EnumWindowsProc(_cb), 0)
        for worker in workers:
            shell_view = user32.FindWindowExW(worker, 0, "SHELLDLL_DefView", None)
            if shell_view:
                break

    if not shell_view:
        return None
    return user32.FindWindowExW(shell_view, 0, "SysListView32", None)


def all_desktop_icons() -> dict:
    """获取桌面所有图标的屏幕坐标 {名称: (x, y)}"""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    lv = _find_desktop_listview()
    if not lv:
        return {}

    icons = {}
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(lv, ctypes.byref(pid))
    access = PROCESS_VM_OP | PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_QUERY_INFO
    ph = kernel32.OpenProcess(access, False, pid.value)
    if not ph:
        return {}

    remote = []
    try:
        rect = wt.RECT()
        user32.GetWindowRect(lv, ctypes.byref(rect))

        text_size = 520
        item_size = ctypes.sizeof(LVITEMW)
        point_size = ctypes.sizeof(POINT)

        r_text = kernel32.VirtualAllocEx(ph, None, text_size,
                                         MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
        r_item = kernel32.VirtualAllocEx(ph, None, item_size,
                                         MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
        r_point = kernel32.VirtualAllocEx(ph, None, point_size,
                                          MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
        remote = [r_text, r_item, r_point]
        if not all(remote):
            return {}

        count = user32.SendMessageW(lv, LVM_GETITEMCOUNT, 0, 0)
        local_text = ctypes.create_unicode_buffer(260)
        local_point = POINT()
        read = wt.DWORD()

        for i in range(count):
            item = LVITEMW()
            item.mask = LVIF_TEXT
            item.iItem = i
            item.iSubItem = 0
            item.pszText = r_text
            item.cchTextMax = 260

            kernel32.WriteProcessMemory(ph, r_item, ctypes.byref(item),
                                        item_size, None)
            user32.SendMessageW(lv, LVM_GETITEMW, i, r_item)
            kernel32.ReadProcessMemory(ph, r_text, ctypes.byref(local_text),
                                       text_size, ctypes.byref(read))
            name = local_text.value
            if not name:
                continue

            user32.SendMessageW(lv, LVM_GETITEMPOSITION, i, r_point)
            kernel32.ReadProcessMemory(ph, r_point, ctypes.byref(local_point),
                                       point_size, ctypes.byref(read))
            icons[name] = (local_point.x + rect.left, local_point.y + rect.top)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[DesktopIcon] 读取桌面图标失败: {e}")
        return {}
    finally:
        for addr in remote:
            if addr:
                kernel32.VirtualFreeEx(ph, addr, 0, MEM_RELEASE)
        kernel32.CloseHandle(ph)

    return icons


def desktop_icon_position(keyword: str):
    """
    按名称模糊匹配桌面图标，返回其屏幕坐标 (x, y)。
    匹配规则：相等 / 图标名包含关键字 / 关键字包含图标名（取第一个命中）。
    """
    if not keyword:
        return None
    key = keyword.strip().lower()
    icons = all_desktop_icons()
    if not icons:
        return None

    # 1. 精确匹配
    for name, pos in icons.items():
        if name.strip().lower() == key:
            return pos
    # 2. 图标名包含关键字（如 "微信 - 快捷方式"）
    for name, pos in icons.items():
        low = name.strip().lower()
        # 去掉常见的快捷方式后缀
        for suffix in (" - 快捷方式", ".lnk", " - shortcut"):
            low = low.replace(suffix.lower(), "")
        if key in low:
            return pos
    # 3. 关键字包含图标名
    for name, pos in icons.items():
        low = name.strip().lower()
        if len(low) >= 2 and low in key:
            return pos
    return None
