# -*- coding: utf-8 -*-
"""本地 API 服务的单一启动出处：桌面 --api 与工作台内嵌网页台共用。

历史状态：main.py 里有一份 start_api_in_thread，工作台要内嵌网页台时
若再抄一份，就会出现「命令行起的能连、工作台起的连的不是同一个」这种
分治。这里把启动、健康探测与默认地址收敛到一个模块：

  - API_HOST / API_PORT：唯一地址口径；
  - api_alive()：只做一次回环探测，不抛异常；
  - start_api_in_thread()：后台线程起 uvicorn，端口占用只记日志。

本模块只用标准库，不 import PyQt5 / framework.core，因此既能被桌面端
也能被纯 API 进程安全引用。
"""

import threading
import urllib.request

API_HOST = "127.0.0.1"
API_PORT = 8000
API_BASE = "http://%s:%d" % (API_HOST, API_PORT)

_SERVER_THREAD = None


def api_alive(timeout: float = 1.5) -> bool:
    """探测本地网页台是否已经在跑；任何异常都算「没起」，不往外抛。"""
    try:
        with urllib.request.urlopen(API_BASE + "/health",
                                    timeout=timeout) as resp:
            return getattr(resp, "status", 200) == 200
    except Exception:      # noqa: BLE001 网络/权限/服务端未起都归为不可用
        return False


def start_api_in_thread(host: str = API_HOST, port: int = API_PORT):
    """在后台线程启动 FastAPI 服务，不阻塞桌面 UI；重复调用只起一次。

    返回线程对象。端口已被占用时不报错也不另起一份，正好复用已经在跑的
    那个服务（浏览器与工作台因此连的是同一份会话与授权记录）。
    """
    global _SERVER_THREAD
    if api_alive():
        return _SERVER_THREAD

    def _run():
        try:
            import uvicorn
            from framework.utils import logger
            logger.info("[API] 本地网页台启动中: http://%s:%d" % (host, port))
            uvicorn.run("api.server:app", host=host, port=port,
                        log_level="warning", reload=False)
        except ImportError as e:
            from framework.utils import logger
            logger.error("[API] 依赖缺失，无法启动网页台: %s" % e)
            logger.error("请先执行: pip install -r requirements-api.txt")
        except OSError as e:
            from framework.utils import logger
            logger.error("[API] 端口被占用或权限不足: %s" % e)
        except Exception as e:      # noqa: BLE001
            from framework.utils import logger
            logger.exception("[API] 服务异常: %s" % e)

    t = threading.Thread(target=_run, name="api-server", daemon=True)
    t.start()
    _SERVER_THREAD = t
    return t
