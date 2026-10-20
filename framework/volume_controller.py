# -*- coding: utf-8 -*-
"""Windows 音量控制（纯 ctypes COM，无第三方依赖）

    from framework.volume_controller import set_system_volume, set_app_volume
    set_system_volume(50)               # 系统主音量调到 50%
    set_app_volume("微信", 30)          # 只调某个应用（音量合成器里的那条会话）

实现路径：
    系统音量  CoCreateInstance(IMMDeviceEnumerator) -> 默认输出设备
              -> IAudioEndpointVolume.SetMasterVolumeLevelScalar
    应用音量  同设备 -> IAudioSessionManager2 -> 枚举声音会话
              -> IAudioSessionControl2(取进程ID) 按进程名匹配
              -> ISimpleAudioVolume.SetMasterVolume（只影响该应用，不动系统音量）

注意：Windows 只为"正在运行且出过声"的应用保留声音会话；
     应用不在会话列表时 set_app_volume 返回空列表，由调用方提示用户。
"""

import ctypes
import os
import uuid
from ctypes import POINTER, byref, c_float, c_int, c_uint, c_void_p
from ctypes import HRESULT, WINFUNCTYPE

from framework import config
from framework.utils import logger


class GUID(ctypes.Structure):
    """COM GUID（Python 3.14 起标准库移除了 ctypes.GUID / wintypes.GUID）"""
    _fields_ = [("data1", ctypes.c_ulong), ("data2", ctypes.c_ushort),
                ("data3", ctypes.c_ushort), ("data4", ctypes.c_ubyte * 8)]

    def __init__(self, guid_str: str = None):
        super().__init__()
        if guid_str:
            u = uuid.UUID(guid_str)
            self.data1 = u.time_low
            self.data2 = u.time_mid
            self.data3 = u.time_hi_version
            self.data4 = (ctypes.c_ubyte * 8)(*u.bytes[8:])

# ------------------------------------------------------------
# COM 常量
# ------------------------------------------------------------
CLSCTX_ALL = 0x17
COINIT_APARTMENTTHREADED = 0x2
RPC_E_CHANGED_MODE = -2147417850
E_RENDER = 0                 # eRender（输出设备）
E_MULTIMEDIA = 1             # eMultimedia 角色
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IAudioEndpointVolume = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
IID_IAudioSessionManager2 = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
IID_ISimpleAudioVolume = GUID("{87CE5498-68D6-44E5-9215-6DA47EF883D8}")

_ole32 = ctypes.oledll.ole32
_kernel32 = ctypes.windll.kernel32
_user32 = ctypes.windll.user32



def _release(iface: int):
    """安全调用 IUnknown::Release"""
    if iface:
        try:
            _method(iface, 2, HRESULT)(iface)
        except Exception:  # noqa: BLE001
            pass

def _ok(hr) -> bool:
    return hr is not None and hr >= 0


def _method(iface: int, index: int, restype, *argtypes):
    """取 COM 接口虚表第 index 个方法（WINFUNCTYPE 已含 self）"""
    vtbl = ctypes.cast(iface, POINTER(c_void_p)).contents.value
    fn = ctypes.cast(vtbl, POINTER(c_void_p))[index]
    return WINFUNCTYPE(restype, c_void_p, *argtypes)(fn)


def _co_init():
    """确保本线程 COM 已初始化（Qt 通常已按 STA 初始化，重复调用无害）"""
    hr = _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    if hr == RPC_E_CHANGED_MODE:
        pass                    # 已按其他模式初始化，直接沿用


def _default_device() -> int:
    """默认音频输出设备 IMMDevice 指针"""
    enumerator = c_void_p()
    if not _ok(_ole32.CoCreateInstance(
            byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
            byref(IID_IMMDeviceEnumerator), byref(enumerator))):
        return 0
    device = c_void_p()
    # GetDefaultAudioEndpoint(eRender=0, eMultimedia=1, &device)
    hr = _method(enumerator.value, 4, HRESULT,
                 c_int, c_int, POINTER(c_void_p))(enumerator, E_RENDER,
                                                  E_MULTIMEDIA,
                                                  byref(device))
    _release(enumerator.value)       # Release
    if not _ok(hr):
        return 0
    return device.value or 0


def _endpoint_volume():
    """IAudioEndpointVolume 指针（系统主音量）"""
    device = _default_device()
    if not device:
        return 0
    endpoint = c_void_p()
    # device->Activate(&IID_IAudioEndpointVolume, CLSCTX_ALL, NULL, &ep)
    # 虚表序号为实测值：IMMDevice::Activate 在槽位 3
    hr = _method(device, 3, HRESULT,
                 POINTER(GUID), c_uint, c_void_p,
                 POINTER(c_void_p))(device, byref(IID_IAudioEndpointVolume),
                                    CLSCTX_ALL, None, byref(endpoint))
    _release(device)                 # Release
    if not _ok(hr):
        return 0
    return endpoint.value or 0


# ============================================================
# 系统主音量
# ============================================================
def get_system_volume() -> int:
    """读系统主音量 0~100；读取失败返回 -1"""
    try:
        _co_init()
        ep = _endpoint_volume()
        if not ep:
            return -1
        vol = c_float()
        hr = _method(ep, 9, HRESULT, POINTER(c_float))(ep, byref(vol))
        _release(ep)                 # Release
        if not _ok(hr):
            return -1
        return int(round(vol.value * 100))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Volume] 读取系统音量失败: {e}")
        return -1


def set_system_volume(percent: int) -> int:
    """
    设置系统主音量 0~100（顺带解除静音），返回实际设置的整数百分比。
    失败返回 -1。
    """
    percent = max(0, min(100, int(percent)))
    try:
        _co_init()
        ep = _endpoint_volume()
        if not ep:
            return -1
        hr = _method(ep, 7, HRESULT, c_float, c_void_p)(
            ep, c_float(percent / 100.0), None)
        _method(ep, 14, HRESULT, c_int, c_void_p)(ep, 0, None)   # SetMute(False)
        _release(ep)                 # Release
        if not _ok(hr):
            logger.warning(f"[Volume] 设置系统音量失败 hr={hr}")
            return -1
        logger.info(f"[Volume] 系统音量已设为 {percent}")
        return percent
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Volume] 设置系统音量异常: {e}")
        return -1


# ============================================================
# 单应用音量（音量合成器会话）
# ============================================================
def _process_image_name(pid: int) -> str:
    """按 PID 取进程 exe 文件名（小写，仅文件名部分）；失败返回空串"""
    try:
        h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                  False, pid)
        if not h:
            return ""
        buf = ctypes.create_unicode_buffer(512)
        size = c_uint(512)
        ok = _kernel32.QueryFullProcessImageNameW(h, 0, buf, byref(size))
        _kernel32.CloseHandle(h)
        if not ok:
            return ""
        return os.path.basename(buf.value).lower()
    except Exception:  # noqa: BLE001
        return ""


def _candidate_keys(app: str) -> list:
    """应用关键词 -> 可匹配的进程名（白名单映射 + 关键词本身）"""
    keys = {app.strip().lower()}
    for exes in config.APP_WHITELIST.get(app, []):
        keys.add(exes.lower())
        keys.add(os.path.splitext(exes)[0].lower())
    return [k for k in keys if k]


def _session_matches(app: str, low_name: str) -> bool:
    """进程名是否命中应用关键词（精确/包含均算）"""
    for key in _candidate_keys(app):
        if key == low_name or key + ".exe" == low_name:
            return True
        if len(key) >= 2 and (key in low_name or low_name in key):
            return True
    return False


def set_app_volume(app: str, percent: int) -> list:
    """
    只调某个应用的声音会话音量 0~100（不动系统主音量）。
    :param app: 应用名（中文/英文均可，走 APP_WHITELIST 映射 + 进程名匹配）
    :return: 命中的进程名列表（如 ['chrome.exe']）；没找到会话返回 []
    """
    percent = max(0, min(100, int(percent)))
    matched = []
    try:
        _co_init()
        device = _default_device()
        if not device:
            return []
        manager = c_void_p()
        # device->Activate(&IID_IAudioSessionManager2, CLSCTX_ALL, NULL, &mgr)
        hr = _method(device, 3, HRESULT,
                     POINTER(GUID), c_uint, c_void_p,
                     POINTER(c_void_p))(device, byref(IID_IAudioSessionManager2),
                                        CLSCTX_ALL, None, byref(manager))
        _release(device)
        if not _ok(hr) or not manager.value:
            logger.warning("[Volume] 取声音会话管理器失败")
            return []

        enum = c_void_p()
        # manager->GetSessionEnumerator(&enum)
        hr = _method(manager.value, 5, HRESULT, POINTER(c_void_p))(
            manager, byref(enum))
        if not _ok(hr) or not enum.value:
            return []

        count = c_int()
        _method(enum.value, 3, HRESULT, POINTER(c_int))(enum, byref(count))

        for i in range(count.value):
            ctrl = c_void_p()
            hr = _method(enum.value, 4, HRESULT, c_int,
                         POINTER(c_void_p))(enum, i, byref(ctrl))
            if not _ok(hr) or not ctrl.value:
                continue

            # QI 出 IAudioSessionControl2 取进程 ID
            pid = c_uint()
            hr = _method(ctrl.value, 14, HRESULT, POINTER(c_uint))(
                ctrl, byref(pid))
            if not _ok(hr):
                _release(ctrl.value)
                continue
            # 系统提示音会话跳过（S_OK=是系统会话；普通会话返回 S_FALSE(1)，
            # 不能用 _ok() 判断，否则会把所有会话都跳过）
            if _method(ctrl.value, 15, HRESULT)(ctrl) == 0:
                _release(ctrl.value)
                continue

            name = _process_image_name(pid.value)
            if name and _session_matches(app, name):
                simple = c_void_p()
                hr = _method(ctrl.value, 0, HRESULT,
                             POINTER(GUID), POINTER(c_void_p))(
                    ctrl, byref(IID_ISimpleAudioVolume), byref(simple))
                if _ok(hr) and simple.value:
                    hr2 = _method(simple.value, 3, HRESULT, c_float,
                                  c_void_p)(simple, c_float(percent / 100.0),
                                            None)
                    if _ok(hr2):
                        matched.append(name)
                        logger.info(f"[Volume] {name} 音量已设为 {percent}")
                    _release(simple.value)
            _release(ctrl.value)

        _release(enum.value)
        _release(manager.value)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Volume] 调节应用音量异常: {e}")
    return matched
