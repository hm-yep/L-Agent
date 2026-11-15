# -*- coding: utf-8 -*-
"""Framework 总控（单例）

队友只需要记住六个关键操作：
    1. fw = Framework.instance()           初始化 Framework 单例
    2. fw.send_command(text)               传递用户输入
    3. fw.register_callback("show_bubble", cb)   注册气泡回调
       fw.register_callback("set_pose", cb)      注册动画回调
       fw.register_callback("show_card", cb)     注册卡片回调
    4. fw.get_anchor(name)                 获取屏幕锚点坐标 (x, y)
    5. fw.move_to(x, y, duration, on_done) 控制角色移动
    6. from framework.utils import resource_path  加载资源

除此之外，无需了解框架内部实现细节。
"""

from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal
import os
import re
import time

from framework import config
from framework import effect_journal, skill_store
from framework.action_parser import parse_actions
from framework.ai_engine import AIWorker
from framework.memory_system import MemorySystem
from framework.pet_controller import PetController
from framework.system_controller import SystemController
from framework.task_board import TaskBoard, format_duration
from framework.utils import logger, normalize_intent
from framework.web_search import web_search, format_notes as format_search_notes


class _SearchWorker(QThread):
    """联网搜索线程：网络往返不放主线程"""

    done = pyqtSignal(bool, str, object)   # (成功与否, 搜索词, 结果列表/错误文本)

    def __init__(self, query: str, count=None, parent=None):
        super().__init__(parent)
        self._query = query
        self._count = count

    def run(self):
        try:
            results = web_search(self._query, count=self._count)
            self.done.emit(bool(results), self._query, results)
        except Exception as e:                  # noqa: BLE001 网络/Key 问题
            logger.warning(f"[Search] 搜索失败: {e}")
            self.done.emit(False, self._query, str(e))


class _DocWorker(QThread):
    """文档生成线程：PPT/Word/Excel 的渲染可能上秒，放后台避免卡住 UI"""

    done = pyqtSignal(bool, str, str)      # (成功与否, 失败原因, 保存路径)

    def __init__(self, doc_type: str, content: dict, path: str, parent=None):
        super().__init__(parent)
        self._doc_type = doc_type
        self._content = content
        self._path = path

    def run(self):
        try:
            from framework import doc_generator
            # 联网配图（方案B）：有 image_query 且用户配了图库 Key 才会真的搜图，
            # 任何失败都返回空 dict，生成自动退回纯排版
            images = doc_generator.fetch_images(self._doc_type, self._content)
            ok, err = doc_generator.generate(self._doc_type, self._content,
                                             self._path, images)
        except Exception as e:  # noqa: BLE001 渲染库崩溃也不能带崩进程
            logger.exception(f"[Doc] 文档线程异常: {e}")
            ok, err = False, str(e)
        self.done.emit(ok, err, self._path)


def build_vision_messages(b64: str, question: str) -> dict:
    """OpenAI 兼容的图文消息体（视觉模型"看屏幕"用；模块级便于测试）"""
    return {
        "role": "user",
        "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text",
             "text": question or "描述一下这个屏幕上有什么"},
        ],
    }


_THINKING_CAPABLE = ("4.6v", "thinking")   # GLM 推理视觉模型支持开关思考模式


def build_vision_payload(model: str, messages: list) -> dict:
    """视觉请求体（模块级便于测试）。

    GLM 推理系列视觉模型默认开思考模式，"看屏幕"要的是快答，
    统一显式关闭；仅对已知支持该参数的模型族附加，其他供应商不带。
    """
    payload = {"model": model, "messages": messages,
               "max_tokens": config.VISION_MAX_TOKENS, "temperature": 0.6}
    if any(tag in (model or "").lower() for tag in _THINKING_CAPABLE):
        payload["thinking"] = {"type": "disabled"}
    return payload


# 看屏幕的重试与超时参数（模块级便于测试调整）
_VISION_ATTEMPT_DELAYS = (2.0, 4.0)   # 重试退避（秒）：连同首试共 1+2 次
_VISION_MIN_TIMEOUT = 15              # 单次请求超时下限（秒），配置异常时兜底


def vision_timeout_seconds() -> int:
    """单次视觉请求超时（秒）：配置被写成 0/负数/非数字时兜底到下限"""
    try:
        return max(_VISION_MIN_TIMEOUT, int(config.VISION_TIMEOUT or 0))
    except (TypeError, ValueError):
        return _VISION_MIN_TIMEOUT


class _VisionWorker(QThread):
    """屏幕视觉线程：截图交给视觉大模型"看"，网络往返不放主线程。

    免费视觉接口的网络抖动、限流（429）、网关 5xx 是常态：
    连接类失败自动退避重试，鉴权类 4xx 不重试直接给明确提示；
    run() 全程兜底——无论发生什么都保证 done 信号恰好发一次，
    绝不让界面停在"让我看看你的屏幕……"之后再无下文。
    """

    done = pyqtSignal(bool, str)           # (成功与否, 回答/失败原因)

    def __init__(self, image_path: str, question: str, parent=None):
        super().__init__(parent)
        self._image_path = image_path
        self._question = question
        self.attempts = 0                  # 实际尝试次数（日志/测试用）

    def run(self):
        try:
            ok, answer = self._ask()
        except Exception as e:  # noqa: BLE001 线程内绝不静默死亡
            logger.exception(f"[Vision] 视觉线程异常: {e}")
            ok, answer = False, f"看屏幕失败了（{type(e).__name__}）"
        self.done.emit(ok, answer)

    def _ask(self) -> tuple:
        import base64
        import requests
        try:
            try:
                with open(self._image_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
            except OSError as e:
                return False, f"屏幕图片读不出来了（{type(e).__name__}）"
            payload = build_vision_payload(
                config.VISION_MODEL,
                [build_vision_messages(b64, self._question)])
            timeout = vision_timeout_seconds()
            headers = {"Authorization": f"Bearer {config.VISION_API_KEY}",
                       "Content-Type": "application/json"}
            try:
                session = requests.Session()   # 复用连接：省握手，也更稳
            except Exception:  # noqa: BLE001 拿不到 Session 就退回一次性请求
                session = None
            last_err = ""
            for attempt in range(1 + len(_VISION_ATTEMPT_DELAYS)):
                self.attempts = attempt + 1
                if attempt:                    # 退避后再试
                    time.sleep(_VISION_ATTEMPT_DELAYS[attempt - 1])
                try:
                    if session is not None:
                        resp = session.post(
                            config.VISION_API_URL, headers=headers,
                            json=payload, timeout=timeout)
                    else:
                        resp = requests.post(
                            config.VISION_API_URL, headers=headers,
                            json=payload, timeout=timeout)
                    if resp.status_code == 200:
                        body = resp.json()
                        if body.get("error"):  # 网关 200 却带错误体
                            last_err = ("视觉模型返回错误（"
                                        + str(body["error"])[:80] + "）")
                            continue
                        msg = ((body.get("choices") or [{}])[0]
                               .get("message") or {})
                        content = str(msg.get("content") or "").strip()
                        if not content:
                            # 个别模型把回答放进思考字段，兜底取一次
                            content = str(
                                msg.get("reasoning_content") or "").strip()
                        if content:
                            return True, content
                        last_err = "视觉模型返回了空回答"
                        continue
                    if resp.status_code in (400, 401, 403):
                        return False, (f"视觉模型连接失败（HTTP "
                                       f"{resp.status_code}，请检查视觉Key）")
                    last_err = f"视觉模型连接失败（HTTP {resp.status_code}）"
                except ValueError as e:   # 响应体不是 JSON（含 JSONDecodeError）
                    last_err = f"视觉模型返回格式异常（{type(e).__name__}）"
                except requests.RequestException as e:
                    last_err = f"看屏幕时网络出了问题（{type(e).__name__}）"
                # 429 / 5xx / 408 / 网络异常：退避后重试
            return False, last_err
        finally:
            try:
                os.remove(self._image_path)
            except OSError:
                pass


class Framework(QObject):
    """框架总控：串联 AI 引擎 / 动作解析 / 宠物控制 / 系统控制 / 记忆"""

    _instance = None

    # ------------------------------------------------------------
    # 对 UI 层暴露的信号
    # ------------------------------------------------------------
    # 记忆更新后发出（输入框下方的历史记录面板据此刷新）
    history_changed = pyqtSignal()
    bubble_requested = pyqtSignal(str)          # 显示气泡 (text)
    card_requested = pyqtSignal(dict)           # 显示卡片 {title,path,size}
    pose_requested = pyqtSignal(str)            # 切换动画姿态 (pose)
    screenshot_hide = pyqtSignal()              # 截图前：隐藏所有助手窗口（不入镜）
    screenshot_restore = pyqtSignal()           # 截图后：恢复助手窗口显示

    @classmethod
    def instance(cls):
        """获取 Framework 单例"""
        if cls._instance is None:
            cls._instance = Framework()
        return cls._instance

    def __init__(self):
        super().__init__()
        if Framework._instance is not None:
            raise RuntimeError("Framework 是单例，请使用 Framework.instance()")

        # 子系统
        self.pet = PetController(self)              # 移动+搜索路线
        self.system = SystemController()            # 查找+启动应用
        self.memory = MemorySystem()                # 对话记忆
        self.personality = config.DEFAULT_PERSONALITY

        # 运行状态
        self._ai_worker = None
        self._doc_worker = None                     # 文档生成线程
        self._doc_edit = None                       # 待修改的文件（文件改写模式）
        self._vision_worker = None                  # 屏幕视觉线程
        self._vision_watchdog = None                # 看屏幕兜底闹钟
        self._zombie_workers = []                   # 超时弃置、可能仍存活的线程
        self._chain_watchdog = None                 # 动作链看门狗（防无声卡死）
        self._search_quality_retried = False        # 本话题搜索质量纠正次数
        self._doc_quality_retried = False           # 本话题文档质量纠正次数
        # 任务计划（确认流）：pending 等确认 / running 执行中
        self._plan = None                           # {"steps": [...], "status": str, "rounds": int}
        self._plan_done = 0                         # 计划已推进的步数
        self._plan_pending_queue = []               # 确认后要执行的后续动作
        self._plan_round_had_actions = False        # 上一轮模型是否给了实质动作
        self._plan_round_pending = False            # 已有一轮计划请求在途
        self._suppress_fallback = False             # 计划轮次不走意图兜底
        self.plan_card = None                       # 常驻计划卡（ui/plan_card.py，懒加载）
        self._busy = False                          # 正在执行动作序列
        self._finish_done = False                   # 当前动作是否已收尾（防重复推进）
        # 以前这个字段只在 _run_next_action 里动态创建，收尾处只能靠
        # getattr(self, "_finish_done", ...) 兜底；现在显式声明，状态一目了然。
        self._current_user_text = ""                # 当前这一轮的用户输入
        self._current_action_name = ""              # 正在执行的动作名
        self._reply_parts = []                      # 当前这一轮累积的回复文本
        self._action_queue = []
        self._current_search_text = ""
        # 检索资料缓冲：web_search / read_doc / analyze_data 的成果，注入后续 AI 轮次
        # （用户发起新话题时清空；计划执行轮次保留，让"调研->出报告"成链）
        self._reference_notes = []
        self._search_worker = None                  # 联网搜索线程
        # 动态重规划：步骤执行受阻时记下原因，本轮收口后让 AI 调整剩余步骤
        self._plan_fail_note = ""                   # 受阻原因（空=无受阻）
        self._plan_auto_plan = False                # 重规划轮在途，等 show_plan 自动接续
        # 工作流启动请求：本轮动作全部收尾后作为新任务交给 send_command
        self._workflow_start = ""
        # 效果记账：本轮开始前的世界快照 + 跑过的动作，收口时算差集（后悔药）
        self._world_before = None                   # 归一化五元组，None=本轮没记账
        self._turn_started_at = 0.0                 # 本轮开始时刻（删文件的门禁）
        self._turn_actions = []                     # 本轮跑过的动作名（含不可见后果）
        self._last_undo = None                      # {"items","spots"}，撤一次就清空
        self._effects_spoken = False                # 本轮是否已播报过差集
        # 技能层：上一轮的动作序列（用户说"存成技能"时拿它抽骨架）+ 技能库
        self._last_turn_actions = []
        self._last_turn_text = ""                   # 那轮的原话（技能名从这来）
        self._last_skill_offer_at = 0.0             # 提议保存的冷却，防碎碎念
        self.skills = skill_store.SkillStore()

        # ---- L1/L2 自适应子系统：惰性加载 + 失败降级 ----
        # 任何一个新模块出问题都只记日志、置 None，桌面版主链路照常运行。
        self.trajectory = None      # append-only 事件流（哈希链、任意轮回滚）
        self.prefs = None           # 自动学到的用户偏好
        self.rules = None           # 从失败里归纳出的调整规则
        self._current_turn_id = ""
        self._current_world_hash = None
        self._turn_journaled = False
        self._traj_ledger_written = False
        try:
            if config.TRAJECTORY_ENABLED:
                from framework.trajectory import TrajectoryStore
                self.trajectory = TrajectoryStore()
        except Exception as e:      # noqa: BLE001 新能力绝不拖垮桌面版
            logger.warning(f"[Framework] 轨迹子系统不可用（已降级）: {e}")
        try:
            if config.PREFERENCE_LEARNING:
                from framework.preference import PreferenceStore
                self.prefs = PreferenceStore()
        except Exception as e:      # noqa: BLE001
            logger.warning(f"[Framework] 偏好子系统不可用（已降级）: {e}")
        try:
            if config.EVOLUTION_ENABLED:
                from framework.evolution import RuleStore
                self.rules = RuleStore()
        except Exception as e:      # noqa: BLE001
            logger.warning(f"[Framework] 进化子系统不可用（已降级）: {e}")

        # 任务台：提醒 + 待办（JSON 持久化），定时轮询到期提醒
        self.tasks = TaskBoard()
        self._reminder_timer = QTimer(self)
        self._reminder_timer.setInterval(config.REMINDER_POLL_SEC * 1000)
        self._reminder_timer.timeout.connect(self._check_reminders)
        self._reminder_timer.start()

        # UI 回调注册表
        self._callbacks = {
            "show_bubble": self.bubble_requested.emit,
            "show_card": self.card_requested.emit,
            "set_pose": self.pose_requested.emit,
            "set_emotion": lambda kind: None,   # 情绪表情叠加（可选注册）
        }

        logger.info("[Framework] 初始化完成 "
                    f"(USE_REAL_AI={config.USE_REAL_AI}, "
                    f"USE_REAL_SYSTEM={config.USE_REAL_SYSTEM})")

    # ============================================================
    # 回调注册（接口约定）
    # ============================================================
    def register_callback(self, name: str, func):
        """注册回调: show_bubble / set_pose / show_card"""
        if name in self._callbacks:
            self._callbacks[name] = func

    # ============================================================
    # 便捷接口
    # ============================================================
    def get_anchor(self, name: str):
        """获取屏幕锚点坐标 (x, y)"""
        return self.pet.get_anchor(name)

    def move_to(self, x: int, y: int, duration: int = 2000, on_done=None):
        """控制角色移动到指定位置"""
        self.pet.move_to(x, y, duration=duration, on_done=on_done)

    def show_bubble(self, text: str):
        """直接显示气泡（UI 回调出错只记日志，不拖垮动作链）"""
        try:
            self._callbacks["show_bubble"](text)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Framework] 气泡回调失败: {e}")

    def set_pose(self, pose: str):
        """直接切换动画姿态: idle/walk/jump/think/searching/talk"""
        try:
            self._callbacks["set_pose"](pose)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Framework] 姿态回调失败: {e}")

    def show_card(self, title: str, path: str = "", size: str = ""):
        """直接显示信息卡片"""
        try:
            self._callbacks["show_card"](
                {"title": title, "path": path, "size": size}
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Framework] 卡片回调失败: {e}")

    def set_emotion(self, kind: str, duration_ms: int = None):
        """叠加情绪表情: happy/love/angry/sweat/sleep/exclaim/star"""
        try:
            self._callbacks["set_emotion"](kind)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Framework] 情绪回调失败: {e}")

    def ai_status_text(self) -> str:
        """当前 AI 状态文案（设置界面显示）"""
        if config.USE_REAL_AI and config.DEEPSEEK_API_KEY:
            key = config.DEEPSEEK_API_KEY
            masked = (f"{key[:6]}{'*' * max(0, len(key) - 10)}{key[-4:]}"
                      if len(key) > 12 else "***")
            return f"已连接 · {config.DEEPSEEK_MODEL} · {masked}"
        return "离线模式（未配置 API Key）"

    def set_ai_config(self, api_key: str = None, base_url: str = None,
                      model: str = None, use_real: bool = None):
        """
        应用用户在设置界面填写的 AI 配置（并持久化）。
        填写了有效 Key 会自动切换为真实 AI 模式（什么都能聊）。
        """
        if api_key is not None:
            config.DEEPSEEK_API_KEY = api_key.strip()
        if base_url is not None:
            config.DEEPSEEK_BASE_URL = base_url.strip()
        if model is not None:
            config.DEEPSEEK_MODEL = model.strip() or config.DEEPSEEK_MODEL

        if use_real is not None:
            config.USE_REAL_AI = bool(use_real and config.DEEPSEEK_API_KEY)
        elif api_key is not None:
            config.USE_REAL_AI = bool(config.DEEPSEEK_API_KEY)

        config.save_user_settings(
            api_key=config.DEEPSEEK_API_KEY,
            base_url=config.DEEPSEEK_BASE_URL,
            model=config.DEEPSEEK_MODEL,
            use_real_ai=config.USE_REAL_AI,
        )
        logger.info(
            f"[Framework] AI配置已更新: model={config.DEEPSEEK_MODEL} "
            f"base_url={config.DEEPSEEK_BASE_URL} use_real={config.USE_REAL_AI}"
        )

    def set_personality(self, personality: str):
        """切换性格（影响回复风格与移动速度）"""
        if personality in config.PERSONALITIES:
            self.personality = personality
            self.pet.set_personality(personality)
            self.memory.add("assistant", f"[系统] 性格已切换为{personality}")
            self.show_bubble(f"性格已切换为「{personality}」。")
            logger.info(f"[Framework] 性格切换: {personality}")

    # ============================================================
    # 可验证执行：轨迹 / 偏好 / 进化
    # 所有入口都带降级包裹——新增能力出问题，只记日志不影响原有动作链。
    # ============================================================
    def _traj_event(self, kind: str, **fields):
        t = self.trajectory
        if t is None:
            return None
        try:
            return t.append(kind, **fields)
        except Exception as e:      # noqa: BLE001
            logger.warning(f"[Framework] 轨迹写入失败（已忽略）: {e}")
            return None

    def _traj_start(self, text: str):
        t = self.trajectory
        if t is None:
            return
        try:
            from framework.trajectory import world_digest
            pre = world_digest(self._world_before) \
                if self._world_before is not None else None
            self._current_world_hash = pre
            self._current_turn_id = t.start_turn(
                source="user", pre_hash=pre,
                note=f"指令 {len(text or '')} 字（内容不落盘）")
        except Exception as e:      # noqa: BLE001
            logger.warning(f"[Framework] 开轮失败（已忽略）: {e}")

    def _traj_end(self, ok: bool = True):
        t = self.trajectory
        if t is None:
            return
        try:
            from framework.trajectory import world_digest
            post = world_digest(self._world_snapshot())
            ledger = None
            # 一轮只写一次账本：计划执行中 _review_effects 可能被调用多次
            if self._turn_journaled and not self._traj_ledger_written:
                ledger = self._last_undo
                self._traj_ledger_written = True
            t.end_turn(post_hash=post, undo_ledger=ledger, ok=ok)
            self._current_world_hash = post
        except Exception as e:      # noqa: BLE001
            logger.warning(f"[Framework] 收轮失败（已忽略）: {e}")
        self._evolve()

    def _evolve(self):
        """每轮收口：把最近的问题事件喂给规则库，让系统下次自动调整。"""
        if self.rules is None or self.trajectory is None \
                or not config.EVOLUTION_ENABLED:
            return
        try:
            changed = self.rules.learn(self.trajectory.events(limit=200)) or []
            for r in changed:
                self._traj_event(
                    "rule", source="system",
                    payload={"rule_id": r.get("id"),
                             "family": r.get("family"),
                             "confidence": r.get("confidence")},
                    note=f"归纳出规则 {r.get('family', '')}")
        except Exception as e:      # noqa: BLE001
            logger.warning(f"[Framework] 规则归纳失败（已忽略）: {e}")

    def _learn_preferences(self, text: str):
        """从用户这句自然话里自动发现偏好（不需要用户说「记住」）。"""
        if self.prefs is None or not config.PREFERENCE_LEARNING:
            return
        try:
            changed = self.prefs.observe(
                text, source_turn=self._current_turn_id) or []
            for p in changed:
                self._traj_event(
                    "preference", source="user",
                    payload={"key": p.get("key"), "value": p.get("value"),
                             "confidence": p.get("confidence")},
                    note=f"发现偏好 {p.get('key', '')}")
            if changed and config.PREFERENCE_NOTIFY:
                keys = "、".join(str(p.get("key")) for p in changed[:3])
                self.tasks.log_work("preference", f"自动记下偏好：{keys}")
        except Exception as e:      # noqa: BLE001
            logger.warning(f"[Framework] 偏好学习失败（已忽略）: {e}")

    def _adjust_action(self, action: dict):
        """执行前：应用偏好与进化规则，并做契约前置校验。返回 None = 跳过。"""
        name = action.get("action", "")
        if name == "web_search" and self.prefs is not None:
            try:
                if not self.prefs.allow_web_search():
                    self._traj_event("rule", action=name, source="system",
                                     note="按用户偏好跳过联网检索")
                    self.show_bubble("按你的偏好，这次不联网，我用已有资料来答。")
                    return None
            except Exception:       # noqa: BLE001
                pass
        ctx = {"action": name, "query": action.get("query", ""),
               "path": action.get("path", ""),
               "doc_type": action.get("doc_type", ""),
               "turn_id": self._current_turn_id}
        adj = {}
        if self.rules is not None and config.EVOLUTION_ENABLED:
            try:
                adj = self.rules.apply(ctx) or {}
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[Framework] 规则应用失败（已忽略）: {e}")
        if adj.get("skip_action"):
            self._traj_event("rule", action=name, source="system",
                             note=adj.get("note") or "规则建议跳过")
            return None
        if adj.get("search_suffix") and name == "web_search":
            action = dict(action)
            action["query"] = (str(action.get("query") or "") + " "
                               + str(adj["search_suffix"])).strip()
        if adj.get("rule_ids"):
            self._traj_event("rule", action=name, source="system",
                             payload={"rule_ids": adj["rule_ids"]},
                             note=adj.get("note") or "应用了学到的规则")
        try:
            from framework.effect_spec import check_pre
            violations = check_pre(action, {}) or []
            if violations:
                self._traj_event("failure", action=name, ok=False,
                                 note="；".join(map(str, violations))[:200])
                if config.EFFECT_SPEC_STRICT:
                    return None
        except Exception:           # noqa: BLE001
            pass
        return action

    def rollback_to(self, turn_id: str) -> dict:
        """回滚到任意轮：把目标轮之后的撤销账本逆序逐轮执行。

        账本存在 turn_end 事件里，所以不需要为每个动作手写 undo；
        这正是事件溯源比「只存上一轮快照」更深的地方。
        """
        if self.trajectory is None:
            return {"ok": False, "reason": "轨迹未启用"}
        ledgers = self.trajectory.rollback_ledgers(turn_id)
        if not ledgers:
            return {"ok": False, "reason": f"找不到轮次 {turn_id} 的账本",
                    "turns": 0}
        total_ops = 0
        for ledger in ledgers:
            ops = effect_journal.undo_ops(ledger.get("items") or [])
            total_ops += len(ops)
            try:
                effect_journal.apply_ops(ops, self._undo_api(),
                                         self._turn_started_at)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[Framework] 回滚部分操作失败: {e}")
        self._traj_event("undo", source="user",
                         payload={"target": turn_id, "ledgers": len(ledgers)},
                         note=f"回滚到 {turn_id}")
        return {"ok": True, "turns": len(ledgers), "ops": total_ops}

    # ============================================================
    # AI 线程的唯一出入口
    # ============================================================
    def _start_ai_worker(self, message: str, *, history=None,
                         facts=None, notes=None, skills=None):
        """创建并启动一轮 AI 请求。

        以前有 6 个调用点各自 new 一个 AIWorker 并重复 connect，而且只有
        「上一轮还在跑」这一条分支才断开旧连接；已 finished 的 QThread 因
        parent=self 会永久挂在 Framework 的子对象树上，随对话轮次线性累积。
        现在统一在这里：断开旧信号 → 可回收的旧线程 deleteLater → 建新线程。

        facts / notes / skills 传 None 表示「不传这个参数」，保留各调用点
        原本的上下文口径（例如文件改写轮刻意不带记忆与资料，避免串味）。
        """
        old = self._ai_worker
        if old is not None:
            for sig_name, slot in (("finished", self._on_ai_finished),
                                   ("failed", self._on_ai_failed)):
                try:
                    getattr(old, sig_name).disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
            try:
                running = old.isRunning()
            except RuntimeError:
                running = False
            if running:
                try:
                    old.stop()
                except RuntimeError:
                    pass
            else:
                try:
                    old.deleteLater()   # 不再让已结束的线程挂在父对象树上
                except RuntimeError:
                    pass

        kwargs = {"parent": self}
        # 自动学到的偏好作为动态块注入；只有非空时才传这个 kwarg，
        # 保证 157 项集成自检里那个「一调用就炸」的假 AIWorker 签名不受影响。
        if getattr(self, "prefs", None) is not None \
                and config.PREFERENCE_LEARNING:
            try:
                prefs_text = (self.prefs.as_prompt() or "").strip()
                if prefs_text:
                    kwargs["prefs_text"] = prefs_text
            except Exception:       # noqa: BLE001
                pass
        if facts is not None:
            kwargs["facts"] = facts
        if notes is not None:
            kwargs["notes"] = notes
        if skills is not None:
            kwargs["skills"] = skills
        worker = AIWorker(
            message,
            self.memory.get_context() if history is None else history,
            self.personality, **kwargs)
        worker.finished.connect(self._on_ai_finished)
        worker.failed.connect(self._on_ai_failed)
        self._ai_worker = worker
        worker.start()
        return worker

    # ============================================================
    # 主入口：处理用户输入
    # ============================================================
    def send_command(self, text: str):
        """
        传递用户输入（UI 输入框回车时调用）。
        流程: 记录记忆 → AI 生成动作序列 → 解析校验 → 依次执行
        """
        text = (text or "").strip()
        if not text:
            return
        # 上一轮请求还在路上：作废它，直接开新一轮（避免迟到的回复串台）
        if self._ai_worker is not None and self._ai_worker.isRunning():
            try:
                self._ai_worker.finished.disconnect(self._on_ai_finished)
                self._ai_worker.failed.disconnect(self._on_ai_failed)
            except TypeError:
                pass
            self._ai_worker.stop()
            # 在途的若是计划轮：清掉在途标记，否则后续的自动推进/「继续」
            # 会被这个残留标记永远挡住（计划看起来"卡死"）
            self._plan_round_pending = False
            self._suppress_fallback = False
            self._plan_auto_plan = False
            logger.info("[Framework] 新输入打断在途 AI 请求")
        # 看门狗：动作队列卡死（回调丢失）时自动解锁，避免永远"等人家忙完"
        if self._busy and not self._action_queue:
            logger.warning("[Framework] 检测到动作队列卡死，自动解锁")
            self._busy = False
        if self._busy:
            self.show_bubble("稍候，手头的事还未了结。")
            return

        # 文件修改模式：有等待改写的文件时，这句话当作修改指令（而非闲聊）
        if self._doc_edit is not None:
            if any(w in text for w in config.DOC_EDIT_CANCEL_WORDS):
                self._doc_edit = None
                self.show_bubble("好的，退出文件修改。")
                logger.info("[Framework] 文件修改: 用户取消")
                return
            self._send_doc_edit(text)
            return

        # 任务计划确认流：待确认 / 执行中的快捷控制
        ai_message = text
        if self._plan is not None:
            if self._plan.get("status") == "pending":
                if any(w in text for w in config.PLAN_CONFIRM_WORDS) \
                        and len(text) <= 20:
                    self.memory.add("user", text)
                    self.history_changed.emit()
                    self._confirm_plan()
                    return
                if any(w in text for w in config.PLAN_CANCEL_WORDS):
                    self.memory.add("user", text)
                    self.history_changed.emit()
                    self._cancel_plan()
                    return
                # 其余视为修改意见：把待确认计划注入上下文，让 AI 出修订版
                steps = self._plan.get("steps") or []
                ai_message = (
                    f"{text}\n\n（系统注：你刚给出如下待确认计划："
                    f"{'；'.join(steps)}。用户没有点「开始执行」，"
                    f"上面这句话是对计划的补充/修改意见——"
                    f"请吸收后返回修订版的 show_plan，仍然只返回计划等确认。）")
                logger.info("[Framework] 计划待确认: 收到修改意见，请求修订")
            else:                                   # running 执行中
                if any(w in text for w in config.PLAN_CANCEL_WORDS):
                    self.memory.add("user", text)
                    self.history_changed.emit()
                    self._cancel_plan()
                    return
                if text in ("继续", "继续执行", "接着执行", "继续吧", "继续干",
                            "接着干", "下一批", "go on"):
                    self.memory.add("user", text)
                    self.history_changed.emit()
                    self._plan_next_round()
                    return
                # 执行中来了一条新任务：先收尾当前计划（汇报进度）再走正常流程
                self._abandon_plan()

        # 确定性短指令：当场执行，不问模型（API 欠费时撤销和存技能照样好用）
        if self._plan is None and len(text) <= config.SHORT_CMD_MAX_LEN:
            if self._fast_intent(text, config.UNDO_WORDS):
                self._said(text)
                self._begin_turn(text, journal=False)
                self._learn_preferences(text)
                self._do_undo_last({})
                self._traj_end()
                return
            if self._fast_intent(text, config.SKILL_SAVE_WORDS):
                self._said(text)
                self._begin_turn(text, journal=False)
                self._learn_preferences(text)
                self._do_save_skill({})
                self._traj_end()
                return
            if self._fast_intent(text, config.SKILL_LIST_WORDS):
                self._said(text)
                self._show_skills()
                return
        # 技能直放：门限放宽（技能名可能就是当初那句长任务），但必须
        # 逐字叫得出名字且参数全抠得齐，差一个槽就交回模型去问
        if self._plan is None and len(text) <= config.SKILL_REPLAY_MAX_LEN:
            if self._replay_skill(text):
                self._said(text)
                return

        # 用户原话不落盘：这句话可能含姓名、路径、账号等隐私，
        # 只记录长度便于排查"这一轮有没有进来"，内容留在内存与聊天面板里。
        logger.info(f"[Framework] 用户输入: <{len(text)} 字，内容不落盘>")
        self._begin_turn(text)
        self._learn_preferences(text)   # 不用用户说「记住」，自己发现偏好
        self._reference_notes = []      # 新话题：清空上一话题的检索资料
        self._search_quality_retried = False   # 新话题：质量纠正次数清零
        self._doc_quality_retried = False
        self.memory.add("user", text)
        self.history_changed.emit()
        self.set_pose("think")

        # 启动 AI 线程（长期记忆一并注入，AI 回答时可自然运用）
        self._start_ai_worker(
            ai_message,
            facts=self.memory.get_facts_text(),
            notes=self._notes_text(), skills=self.skills.catalog_text())

    def _on_ai_finished(self, actions: list):
        """AI 返回动作序列（整体兜底：处理出错只结束本轮，绝不闪退）"""
        try:
            self._on_ai_finished_inner(actions)
        except Exception:  # noqa: BLE001
            logger.exception("[Framework] 处理 AI 回复时出错")
            self._traj_event("failure", source="model", ok=False,
                             note="处理 AI 回复时异常")
            self._clear_plan_transient()
            self._busy = False
            self._action_queue = []
            self.set_pose("idle")
            self.show_bubble("我刚才没接上话，再说一次试试。")

    def _on_ai_finished_inner(self, actions: list):
        self._plan_round_pending = False
        suppress = self._suppress_fallback
        self._suppress_fallback = False
        actions = parse_actions(actions if suppress
                                else self._ensure_tool_intent(actions))
        if self._doc_edit is not None:
            actions = self._apply_doc_edit_result(actions)
        actions = self._dedupe_reminds(actions)
        # 重规划轮没返回 show_plan：放行按原计划继续（预算照扣，防死循环）
        if self._plan_auto_plan and not any(
                a.get("action") == "show_plan" for a in actions):
            logger.warning("[Framework] 重规划轮未返回 show_plan，按原计划继续")
            self._plan_auto_plan = False
        # 计划执行轮：本轮是否给了实质动作（决定队列空后要不要自动推进下一步）
        self._plan_round_had_actions = any(
            a.get("action") not in ("show_bubble", "think", "idle")
            for a in actions)
        self._action_queue = actions
        self.history_changed.emit()      # AI 回复后刷新历史记录
        self._busy = True
        self._run_next_action()

    # 一轮回复里这类动作只保留第一个，防止模型重复动作导致互相冲突
    # （如两个 remind：换算口径不一还会设出两条提醒）
    _SINGLE_INSTANCE_ACTIONS = ("remind", "show_plan")

    # 这些是"说话/表演"类动作，不算计划进度（只有工具动作才打勾）
    _NO_PROGRESS_ACTIONS = {"show_bubble", "think", "idle", "jump"}

    def _dedupe_reminds(self, actions: list) -> list:
        out, seen = [], set()
        for a in actions:
            name = a.get("action")
            if name in self._SINGLE_INSTANCE_ACTIONS:
                if name in seen:
                    logger.warning(f"[Framework] 重复动作 {name} 已丢弃"
                                   f"（一轮只保留一个）: {a}")
                    continue
                seen.add(name)
            out.append(a)
        return out

    def _apply_doc_edit_result(self, actions: list) -> list:
        """文件修改模式：从 AI 返回里接管 create_doc，指定输出路径"""
        doc_actions = [a for a in actions if a.get("action") == "create_doc"]
        if not doc_actions:
            # 模型只回了话（比如追问细节）：保持等待状态，回复原样展示
            logger.info("[Framework] 文件修改: 模型未返回 create_doc，继续等待指令")
            return actions
        edit = self._doc_edit
        action = doc_actions[0]
        action["doc_type"] = edit["kind"]
        if not action.get("content"):
            # 模型没给内容：退回原文件内容直接重排，避免空手而归
            action["content"] = edit["spec"]
            logger.warning("[Framework] 文件修改: 模型未给 content，用原内容重排")
        base, ext = os.path.splitext(edit["path"])
        target, n = f"{base}_修改{ext}", 1
        while os.path.exists(target):
            target = f"{base}_修改{n}{ext}"
            n += 1
        action["save_path"] = target
        self._doc_edit = None                    # 待改文件已消费
        logger.info(f"[Framework] 文件修改: 输出到 {target}")
        return actions

    # 打开/查找类意图的关键词（与离线模拟 ai_engine.mock_ai 同一套规则）
    _OPEN_INTENT_WORDS = ("打开", "启动", "运行", "开一下")
    _FIND_INTENT_WORDS = ("找一下", "找找", "在哪", "查找", "搜索")
    # 截图关键词与离线模拟共用一份词表（含口语变体）
    from framework.ai_engine import _SHOT_WORDS as _SHOT_INTENT_WORDS

    def _ensure_tool_intent(self, actions: list) -> list:
        """意图兜底：模型嘴上说"马上打开"却没给工具动作时，
        按用户原话自动补上 open_app / search_app，保证操作真的执行。
        弱指令跟随的小模型经常只回确认语不带动作，这里托底。"""
        text = (self._current_user_text or "").strip()
        if not text or not isinstance(actions, list):
            return actions
        if any(a.get("action") in ("open_app", "search_app", "screenshot",
                                   "set_volume", "shutdown", "restart",
                                   "lock", "cancel_shutdown", "create_doc",
                                   "see_screen", "remember", "show_plan",
                                   "remind", "todo_add", "todo_done",
                                   "todo_list")
               for a in actions):
            return actions                      # 模型已给工具动作

        # ---- 多步任务兜底："先A，再B，最后C" -> 列计划逐步执行 ----
        from framework.ai_engine import split_multi_task
        steps = split_multi_task(text)
        if steps:
            actions.append({"action": "show_plan", "steps": steps})
            logger.info(f"[Framework] 意图兜底: 检测到多步任务，"
                        f"拆成 {len(steps)} 步计划待确认")
            return actions

        from framework.ai_engine import (_extract_app_name,
                                         _extract_volume_app,
                                         _extract_volume_number,
                                         _VISION_WORDS, _REMIND_WORDS,
                                         _TODO_ADD_WORDS, _TODO_LIST_WORDS,
                                         _TODO_DONE_WORDS, extract_remind)

        def _append(action_name: str):
            app = _extract_app_name(text)
            if app and len(app) <= 20:
                actions.append({"action": action_name, "app": app})
                logger.info(f"[Framework] 意图兜底: 模型未给工具动作，"
                            f"按原话补充 {action_name}({app})")

        # ---- 电源意图兜底（取消优先级最高）----
        from framework.ai_engine import (_POWER_SHUTDOWN_WORDS,
                                         _POWER_RESTART_WORDS,
                                         _POWER_LOCK_WORDS,
                                         _POWER_CANCEL_WORDS)
        if any(w in text for w in _POWER_CANCEL_WORDS):
            actions.append({"action": "cancel_shutdown"})
            logger.info("[Framework] 意图兜底: 补充 cancel_shutdown")
            return actions
        if any(w in text for w in _POWER_LOCK_WORDS):
            actions.append({"action": "lock"})
            logger.info("[Framework] 意图兜底: 补充 lock")
            return actions
        if any(w in text for w in _POWER_RESTART_WORDS):
            actions.append({"action": "restart"})
            logger.info("[Framework] 意图兜底: 补充 restart")
            return actions
        if any(w in text for w in _POWER_SHUTDOWN_WORDS):
            actions.append({"action": "shutdown"})
            logger.info("[Framework] 意图兜底: 补充 shutdown")
            return actions

        if any(w in text for w in self._SHOT_INTENT_WORDS):
            actions.append({"action": "screenshot"})
            logger.info("[Framework] 意图兜底: 模型未给工具动作，按原话补充 screenshot")
        elif "音量" in text:
            vol = _extract_volume_number(text)
            if vol is not None:
                action = {"action": "set_volume", "volume": vol}
                app = _extract_volume_app(text)
                if app:
                    action["app"] = app
                actions.append(action)
                logger.info(f"[Framework] 意图兜底: 补充 set_volume({vol}, "
                            f"app={app or '系统'})")
        elif any(w in text for w in self._OPEN_INTENT_WORDS):
            _append("open_app")
        elif any(w in text for w in self._FIND_INTENT_WORDS):
            _append("search_app")
        # ---- 屏幕视觉 / 提醒 / 待办兜底（在文档兜底之前，词更具体）----
        elif any(w in text for w in _VISION_WORDS):
            actions.append({"action": "see_screen", "question": text})
            logger.info("[Framework] 意图兜底: 补充 see_screen")
        elif any(w in text for w in _REMIND_WORDS):
            seconds, content = extract_remind(text)
            remind = {"action": "remind", "text": content}
            if seconds:
                remind["seconds"] = seconds
            actions.append(remind)
            logger.info(f"[Framework] 意图兜底: 补充 remind({content!r})")
        elif any(w in text for w in _TODO_DONE_WORDS):
            actions.append({"action": "todo_done"})
            logger.info("[Framework] 意图兜底: 补充 todo_done")
        elif any(w in text for w in _TODO_LIST_WORDS):
            actions.append({"action": "todo_list"})
            logger.info("[Framework] 意图兜底: 补充 todo_list")
        elif any(w in text for w in _TODO_ADD_WORDS):
            content = text
            for w in _TODO_ADD_WORDS:
                content = content.replace(w, " ")
            content = content.strip(" 　，。！？、的：:") or text
            actions.append({"action": "todo_add", "text": content})
            logger.info(f"[Framework] 意图兜底: 补充 todo_add({content!r})")
        else:
            # ---- 文档生成意图兜底（放最后，避免劫持"打开 word 文档"类指令）----
            # 弱模型只回确认语不给 create_doc 时补上动作骨架；
            # content 拿不到就交给执行层提示用户再描述
            low = text.lower()
            doc_type = ""
            if "ppt" in low or "幻灯片" in text or "演示文稿" in text:
                doc_type = "ppt"
            elif "excel" in low or "表格" in text or "名单" in text \
                    or "报表" in text:
                doc_type = "xlsx"
            elif "word" in low or "文档" in text or "报告" in text \
                    or "文章" in text:
                doc_type = "doc"
            if doc_type:
                actions.append({"action": "create_doc", "doc_type": doc_type,
                                "filename": "", "content": {}})
                logger.info(f"[Framework] 意图兜底: 补充 create_doc({doc_type})")
        return actions

    def _on_ai_failed(self, error: str):
        """AI 请求失败：明确告知用户并复位状态（不卡死、不留悬空状态）"""
        self._traj_event("failure", source="model", ok=False,
                         note=str(error)[:200])
        self._clear_plan_transient()
        self._busy = False
        self._action_queue = []
        self.show_bubble(f"{error}。可再试一次。")
        self.set_pose("idle")

    # ============================================================
    # 动作序列执行器
    # ============================================================
    def _run_next_action(self):
        """按顺序执行下一个动作（最外层兜底：动作链任何异常都不闪退）"""
        try:
            self._run_next_action_inner()
        except Exception:  # noqa: BLE001
            logger.exception("[Framework] 动作链异常（已兜底收尾）")
            self._action_queue = []
            self._busy = False
            self._review_effects(offer=False)
            self.set_pose("idle")
            self.show_bubble("出了点小状况，这一轮先到这里，可再吩咐。")
            self._announce_effects()

    def _run_next_action_inner(self):
        if not self._action_queue:
            # 一轮执行完毕：把本轮的回复整体记入历史（供折叠面板展示）
            if getattr(self, "_reply_parts", None) and self._current_user_text:
                self.memory.add("assistant", " ".join(self._reply_parts))
                self._reply_parts = []
                self.history_changed.emit()
            # 工作流启动请求：本轮收尾后作为新任务开跑（不与进行中的计划交叉）
            if self._workflow_start and (
                    self._plan is None
                    or self._plan.get("status") != "running"):
                command, self._workflow_start = self._workflow_start, ""
                self._busy = False
                logger.info(f"[Framework] 启动工作流: {command[:50]}")
                self.send_command(command)
                return
            # 任务计划执行中：全自动推进，直到所有步骤完成
            plan = self._plan
            if plan is not None and plan.get("status") == "running":
                steps = plan["steps"]
                done = min(self._plan_done, len(steps))
                if done >= len(steps):
                    self._complete_plan()
                    return
                fail_note = self._plan_fail_note
                if fail_note:
                    # 步骤受阻：预算内先让 AI 自动重规划，用尽则交还用户
                    if plan.get("replans", 0) < config.PLAN_MAX_REPLANS:
                        self._busy = False
                        QTimer.singleShot(600, self._plan_replan)
                        return
                    self._plan_fail_note = ""
                    self._busy = False
                    self._review_effects()
                    self.set_pose("idle")
                    self.show_bubble(
                        f"「{fail_note}」卡住了，计划推进到 "
                        f"{done}/{len(steps)} 步。说「继续」我再想办法，"
                        f"或直接吩咐别的事。")
                    self._announce_effects()
                    return
                if plan.get("rounds", 0) < config.PLAN_MAX_ROUNDS:
                    if not self._plan_round_had_actions:
                        # 本轮模型只说话没调工具：视为口头完成当前步骤，
                        # 推进一格继续下一轮（保证计划总能自动走完）
                        self._plan_done += 1
                        self._update_plan_card()
                        if self._plan_done >= len(steps):
                            self._complete_plan()
                            return
                    self._busy = False
                    QTimer.singleShot(600, self._plan_next_round)
                    return
                # 轮数安全阀用尽仍未完成：汇报进度交还用户
                self._busy = False
                self._review_effects()
                self.set_pose("idle")
                self.show_bubble(f"计划推进到 {done}/{len(steps)} 步，"
                                 f"说「继续」我接着干，或直接吩咐别的事。")
                self._announce_effects()
                return
            self._review_effects()
            self._busy = False
            self.set_pose("idle")
            self._announce_effects()
            return

        action = self._action_queue.pop(0)
        self._finish_done = False          # 本动作是否已收尾（防重复推进）
        action = self._adjust_action(action)
        if action is None:
            # 偏好或学到的规则建议跳过：如实记账，别把「没做」说成「做了」
            self._note_plan_failure("偏好/规则建议跳过该动作")
            self._finish_action()
            return
        self._current_action_name = action["action"]
        self._turn_actions.append(action)   # 轨迹：收口时抽技能、算撤不回项
        self._arm_chain_watchdog(action["action"])
        self._traj_event(
            "action", action=action["action"], source="model",
            pre_hash=self._current_world_hash,
            payload={"params": {k: v for k, v in action.items()
                                if k not in ("content", "style")}})
        # 只记动作名：完整 action 字典里可能有 path / fact / text 等用户数据
        logger.info(f"[Framework] 执行动作: {action['action']}")
        try:
            self._execute_action(action)
        except Exception:  # noqa: BLE001 单个动作出错不闪退、不停摆
            logger.exception(
                f"[Framework] 动作 {self._current_action_name} 执行异常，跳过")
            self._traj_event("failure", action=self._current_action_name,
                             source="system", ok=False,
                             note="动作执行异常，已跳过")
            self.show_bubble("这一步出了点小状况，我先跳过继续。")
            if not self._finish_done:
                self._finish_action()

    def _execute_action(self, action: dict):
        """分发执行单个动作（由 _run_next_action 的异常兜底包裹）"""
        name = action["action"]

        if name == "show_bubble":
            self.show_bubble(action["text"])
            self._reply_parts.append(action["text"])   # 记录本轮回复
            self.set_pose("talk")
            self._finish_action()

        elif name == "think":
            self.set_pose("think")
            self._finish_action()

        elif name == "idle":
            self.set_pose("idle")
            self._finish_action()

        elif name == "jump":
            self.set_pose("jump")
            self.pet.jump(on_done=lambda: (self.set_pose("idle"),
                                           self._finish_action()))

        elif name == "move_to":
            self.set_pose("walk")
            x, y = self.get_anchor(action["anchor"])
            self.pet.move_to(x, y, duration=2200, tag="action",
                             on_done=lambda: self._finish_action())

        elif name == "search_app":
            self._do_search(action["app"])

        elif name == "open_app":
            self._do_open(action["app"])

        elif name == "show_card":
            self.show_card(action.get("title", ""),
                           action.get("path", ""), action.get("size", ""))
            self._finish_action()

        elif name == "screenshot":
            self._do_screenshot()

        elif name == "set_volume":
            self._do_set_volume(action)

        # ---- 电源操作 ----
        elif name == "shutdown":
            self._do_power("shutdown", action.get("delay"))
        elif name == "restart":
            self._do_power("restart", action.get("delay"))
        elif name == "lock":
            self._do_lock()
        elif name == "cancel_shutdown":
            self._do_cancel_shutdown()

        # ---- 文档生成 ----
        elif name == "create_doc":
            self._do_create_doc(action)

        # ---- 屏幕视觉 / 长期记忆 / 任务规划 / 提醒待办 ----
        elif name == "see_screen":
            self._do_see_screen(action)
        elif name == "remember":
            self._do_remember(action)
        elif name == "show_plan":
            # 计划不直接执行：亮卡等用户确认（后续动作暂存，确认后再跑）
            self._do_show_plan(action)
            return
        elif name == "remind":
            self._do_remind(action)
        elif name == "todo_add":
            self._do_todo_add(action)
        elif name == "todo_done":
            self._do_todo_done(action)
        elif name == "todo_list":
            self._do_todo_list()

        # ---- 联网检索 / 读文档 / 数据分析 / 工作日志 / 工作流 ----
        elif name == "web_search":
            self._do_web_search(action)
        elif name == "read_doc":
            self._do_read_doc(action)
        elif name == "analyze_data":
            self._do_analyze_data(action)
        elif name == "work_log":
            self._do_work_log()
        elif name == "run_workflow":
            self._do_run_workflow(action)

        # ---- 后悔药 / 技能层 ----
        elif name == "undo_last":
            self._do_undo_last(action)
        elif name == "save_skill":
            self._do_save_skill(action)
        elif name == "run_skill":
            self._do_run_skill(action)

        else:
            self._finish_action()

    def _finish_action(self):
        """单个动作完成，继续下一个（计划执行中每个工具动作打一个勾；
        同一动作只收一次尾，防重复推进）"""
        self._stop_chain_watchdog()
        if getattr(self, "_finish_done", False):
            logger.warning("[Framework] 动作收尾重复调用，已忽略")
            return
        self._finish_done = True
        plan = self._plan
        if plan is not None and plan.get("status") == "running" \
                and self._plan_done < len(plan["steps"]) \
                and self._current_action_name not in self._NO_PROGRESS_ACTIONS:
            self._plan_done += 1
            self._update_plan_card()
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(150, self._run_next_action)

    # 动作链看门狗预算（秒）：到点还没收尾就强制收尾并说明原因，
    # 绝不让"遵命，这就去……"之后再无声息（慢动作单独放宽预算）
    _ACTION_WATCHDOG_BUDGETS = {
        "see_screen": 240, "create_doc": 300, "web_search": 90,
        "read_doc": 120, "screenshot": 90, "search_app": 60, "open_app": 90,
        "move_to": 40, "jump": 30, "work_log": 60, "analyze_data": 150,
    }
    _ACTION_WATCHDOG_DEFAULT = 90

    def _arm_chain_watchdog(self, name: str):
        """给当前动作布防看门狗；用户在弹窗里慢慢选位置的时间不算（先拆闸）"""
        seconds = self._ACTION_WATCHDOG_BUDGETS.get(
            name, self._ACTION_WATCHDOG_DEFAULT)
        wd = self._chain_watchdog
        if wd is None:
            wd = QTimer(self)
            wd.setSingleShot(True)
            wd.timeout.connect(self._on_chain_watchdog)
            self._chain_watchdog = wd
        wd.start(seconds * 1000)

    def _stop_chain_watchdog(self):
        if self._chain_watchdog is not None:
            self._chain_watchdog.stop()

    def _on_chain_watchdog(self):
        """动作卡死兜底：线程/网络万一无声挂住，也要给用户一个交代"""
        if not self._busy or getattr(self, "_finish_done", True):
            return                                    # 已正常收尾/不在执行链
        name = getattr(self, "_current_action_name", "") or "当前动作"
        logger.warning(f"[Framework] 动作 {name} 超时未收尾，看门狗强制收尾")
        self.set_emotion("sweat")
        self.show_bubble(f"「{name}」这步等太久没动静，我先跳过它继续往下走；"
                         "做得到的一定做完，做不到的会告诉你原因。")
        self._note_plan_failure(f"动作{name}等待超时")
        self._finish_action()

    # ============================================================
    # 效果记账与撤销（后悔药）：判据全在 effect_journal，这里只负责读数和执行
    # ============================================================
    def _said(self, text: str):
        """短指令也要进历史，否则折叠面板里看不见自己说过什么"""
        self.memory.add("user", text)
        self.history_changed.emit()

    def _fast_intent(self, text: str, words) -> bool:
        """这句话是不是那句短指令（去标点后逐字比，长句子不当指令）"""
        norm = normalize_intent(text)
        return bool(norm) and any(w.lower() in norm for w in words)

    def _begin_turn(self, text: str, journal: bool = True):
        """开一轮：记下世界此刻的样子，收口时好算差集。

        journal=False 给"这句话不改动世界"的快路径：没快照就没差集。
        """
        self._current_user_text = text
        self._reply_parts = []
        self._turn_actions = []
        self._effects_spoken = False
        # 上一轮的"这个动作已收尾"标记不能带进新一轮：快路径不经过动作队列，
        # 没人替它复位，残留的 True 会让本轮收尾被整个跳过（回复进不了历史）。
        self._finish_done = False
        self._turn_started_at = time.time()
        self._world_before = self._world_snapshot() \
            if journal and config.UNDO_ENABLED else None
        self._turn_journaled = bool(self._world_before is not None)
        self._traj_ledger_written = False
        self._traj_start(text)

    def _world_snapshot(self) -> dict:
        """读五个可观测面的当前值（音量/待办/提醒/交付/长期记忆）"""
        from framework import volume_controller as volume

        todos = [(t["text"], bool(t["done"])) for t in self.tasks.todos]
        reminders = [r["text"] for r in self.tasks.reminders]
        deliveries = [d["path"] for d in self.tasks.deliveries]
        try:
            vol = volume.get_system_volume()
        except Exception:  # noqa: BLE001 读不到就当不知道，绝不瞎猜一个值
            vol = None
        return effect_journal.normalize(
            volume=vol if vol is not None and vol >= 0 else None,
            todos=todos, reminders=reminders, deliveries=deliveries,
            facts=list(self.memory.facts))

    def _review_effects(self, offer: bool = True):
        """本轮动作跑完：算差集存好账（撤销用它），并把事件轨迹收口。"""
        try:
            self._review_effects_inner(offer)
        finally:
            # 所有早退路径（没记账 / 本轮本身就是撤销）也要留下 turn_end，
            # 否则哈希链断在这里，回放与任意轮回滚就少了一轮。
            self._traj_end()

    def _review_effects_inner(self, offer: bool = True):
        actions, self._turn_actions = self._turn_actions, []
        if actions:
            self._last_turn_actions = actions
            self._last_turn_text = self._current_user_text
        if self._world_before is None:
            return
        before, self._world_before = self._world_before, None
        names = [effect_journal.trajectory_name(a) for a in actions]
        if set(names) & effect_journal.UNDO_ITSELF:
            # 这一轮本身就是撤销：不再记账，否则"撤销"能把"撤销"撤回来
            self._last_undo = None
            return
        items = effect_journal.diff(before, self._world_snapshot())
        spots = effect_journal.blind_spots(names)
        self._last_undo = None
        if items or spots:
            # 撤不回的后果也要留账：只开了个程序这种"差集为空"的轮次，用户照样
            # 有权知道它撤不回，而不是收到一句含糊的"我没改动什么"。
            self._last_undo = {"items": items[:config.UNDO_MAX_ITEMS],
                               "spots": spots,
                               "before": before}
        if offer:
            self._offer_skill(actions)

    def _announce_effects(self):
        """收口时如实播报：几件能撤、几件撤不回（只在确实动了世界时开口）"""
        if self._effects_spoken or not self._last_undo:
            return
        self._effects_spoken = True
        undo = self._last_undo
        n, spots = len(undo["items"]), undo["spots"]
        if not spots:
            self.show_bubble(f"这一轮改了 {n} 件事，都能撤。"
                             "说「撤销」我退回去。")
        elif not n:
            self.show_bubble(f"这一轮做的事一件都撤不回，"
                             "说「撤销」时我会一条条讲为什么。")
        else:
            self.show_bubble(f"这一轮改了 {n} 件事能撤，另有 {len(spots)} 件撤不回"
                             "（说「撤销」时我会一条条讲）。")

    def _undo_api(self) -> dict:
        """给回退用的七只手（判据在 effect_journal，这里只对接真实子系统）"""
        from framework import volume_controller as volume

        def set_volume(percent):
            return volume.set_system_volume(int(percent)) >= 0

        def unlink(path):
            if os.path.exists(path):
                os.remove(path)
            return True

        return {
            "set_volume": set_volume,
            "forget_fact": self.memory.forget_fact_by_text,
            "remove_todo": self.tasks.remove_todo_by_text,
            "reopen_todo": self.tasks.reopen_todo,
            "remove_reminder": self.tasks.remove_reminder_by_text,
            "unlink": unlink,
            "drop_delivery": self.tasks.remove_delivery_by_path,
        }

    def _do_undo_last(self, action: dict):
        """undo_last 动作：把上一轮能撤的都退回去，撤不成的逐条说原因"""
        self._traj_event(
            "undo", action="undo_last", source="user",
            ok=bool(self._last_undo),
            note="用户请求撤销上一轮" if self._last_undo else "撤销请求但本轮无账")
        # 任意轮回滚：动作参数 to 给 turn_id（也可直接调用 fw.rollback_to）。
        # 账本存在 turn_end 事件里，不必为每个动作重写一套撤销逻辑。
        target = str((action or {}).get("to") or "").strip()
        if target and target not in ("last", "上一轮", "0"):
            res = self.rollback_to(target)
            if res.get("ok"):
                self.show_bubble(f"已回滚 {res['turns']} 轮"
                                 f"（共 {res['ops']} 项逆操作）。")
            else:
                self.show_bubble(f"回滚没成：{res.get('reason', '未知原因')}。")
            self._finish_action()
            return
        undo = self._last_undo
        if not undo:
            self.set_emotion("sweat")
            self.show_bubble("这一轮我没改动什么，或者已经撤过了——"
                             "没有第二次撤销可给你。")
            self._finish_action()
            return
        self._last_undo = None                 # 先销账：撤一半也不给重复撤
        items = undo["items"]
        effect_journal.apply_ops(effect_journal.undo_ops(items),
                                 self._undo_api(), self._turn_started_at)
        # 不拿"执行过了"冒充"撤成了"：撤完重读一次世界，还剩多少差集就是没撤成
        residue = effect_journal.diff(undo["before"], self._world_snapshot())
        stuck = {it["label"] for it in residue}
        done = [it["label"] for it in items
                if it.get("reversible") and it["label"] not in stuck]
        bad = [it["label"] for it in items if it["label"] in stuck]
        spots = [s["reason"] for s in undo["spots"]]
        self.tasks.log_work("undo", f"撤销了 {len(done)} 项，"
                                    f"{len(bad)} 项没撤成"
                                    + (f"，{len(spots)} 项本来就撤不回"
                                       if spots else ""))
        lines = [f"· 已退回：{x}" for x in done]
        lines += [f"· 没撤成：{x}" for x in bad]
        if done and not bad:
            self.set_emotion("happy")
        else:
            self.set_emotion("sweat")
        self.show_bubble(f"撤销：{len(done)} 项已退回"
                         + (f"，{len(bad)} 项没撤成" if bad else "")
                         + (f"，另有 {len(spots)} 项本来就撤不回" if spots else "")
                         + "。")
        self._reply_parts.append(
            f"（撤销：退回 {len(done)} 项 / 未成 {len(bad)} 项"
            + (f" / 撤不回 {len(spots)} 项" if spots else "") + "）")
        if lines or spots:
            self.show_card("↩️ 这次撤销的明细",
                           "\n".join(lines + [f"· 撤不回：{x}" for x in spots]),
                           f"退回 {len(done)} 项 / 没撤成 {len(bad)} 项 / "
                           f"本来就撤不回 {len(spots)} 项")
        self._finish_action()

    # ============================================================
    # 技能层：一次学会（存轨迹），下次一句话重放（重放零模型调用）
    # ============================================================
    def _offer_skill(self, actions):
        """这轮做成了一个可重放的流程：问一句要不要存（有冷却，不碎碎念）"""
        now = time.time()
        if now - self._last_skill_offer_at < config.SKILL_OFFER_COOLDOWN_SEC:
            return
        ok, _ = skill_store.skillable(actions)
        if not ok:
            return
        name = skill_store.label([a for a in actions])
        self._last_skill_offer_at = now
        self.show_bubble(f"这套动作（{name}）要我存成技能吗？"
                         "说「存成技能」，下次一句话就能重放，不再重新规划。")

    def _do_save_skill(self, action: dict):
        """save_skill 动作：把上一轮的动作序列抽成技能存下来

        名字取那件事的原话，不取「存成技能」这句：下次用户还得用同一句话说它。
        """
        item, why = self.skills.add(self._last_turn_text,
                                    self._last_turn_actions)
        if item:
            self._last_turn_actions = []
            self._last_turn_text = ""
            slots = f"，需要：{'、'.join(item['slots'])}" if item["slots"] else ""
            self.set_emotion("happy")
            self.tasks.log_work("skill", f"学会技能：{item['name']}")
            self.show_bubble(f"学会了：「{item['name']}」"
                             f"（{len(item['steps'])} 步{slots}）。"
                             "下次直接说名字就行。")
        else:
            self.set_emotion("sweat")
            self.show_bubble(f"这个存不成技能：{why}")
        self._reply_parts.append(f"（技能：{item['name'] if item else why}）")
        self._finish_action()

    def _do_run_skill(self, action: dict):
        """run_skill 动作：按名字重放技能。缺槽整条不跑，去问一句"""
        self._run_skill_named(action.get("skill") or action.get("name") or "",
                              self._current_user_text,
                              extra=action.get("values") or {})
        self._finish_action()

    def _run_skill_named(self, name: str, utterance: str,
                         extra=None, speak: bool = True) -> bool:
        """找到技能、抠出参数、把步骤插进队列 → 成没成（说没说由调用方决定）"""
        skill = self.skills.get(name)
        if not skill:
            if not speak:
                return False
            self.set_emotion("sweat")
            catalog = self.skills.catalog_text()
            self.show_bubble(f"没学过「{name}」这个技能。"
                             + ("我会的：" + catalog if catalog else ""))
            return False
        values = skill_store.match_values(utterance, skill["slots"])
        values.update(extra or {})
        steps, missing = skill_store.fill(skill, values)
        if not steps:
            if speak:
                self.set_emotion("sweat")
                self.show_bubble(f"「{skill['name']}」还差"
                                 f"{'、'.join(missing)}，我不拿上次的值瞎补——"
                                 f"告诉我再跑。")
            return False
        self.skills.mark_used(skill)
        self.set_emotion("happy")
        self.show_bubble(f"重放「{skill['name']}」：{len(steps)} 步，"
                         "这些步骤直接照当初的动作跑，一步都没再问模型。")
        self._action_queue[:0] = steps
        self.tasks.log_work("skill", f"重放技能：{skill['name']}")
        return True

    def _replay_skill(self, text: str) -> bool:
        """零模型调用直放：这句话里出现了技能名，参数也抠得全，就直接跑"""
        skill = self.skills.get(text)
        if not skill:
            return False
        norm = normalize_intent(text)
        if normalize_intent(skill["name"]) not in norm:
            return False
        if not self._run_skill_named(skill["name"], text, speak=False):
            return False                    # 抠不齐：交回正常流程，让模型去问
        steps, self._action_queue = self._action_queue[:], []
        self._begin_turn(text)
        self._action_queue = steps
        self._busy = True
        self._run_next_action()
        return True

    def _show_skills(self):
        """列出已学会的技能（不经过模型）"""
        items = self.skills.all()
        if not items:
            self.show_bubble("我还没学会任何技能。做成一件事后说「存成技能」就行。")
            return
        lines = [f"· {s['name']}（{len(s['steps'])} 步"
                 f"{'，需要' + '、'.join(s['slots']) if s['slots'] else ''}）"
                 for s in items]
        used = sum(int(s.get("used", 0)) for s in items)
        self.show_card("🎯 我学会的技能", "\n".join(lines),
                       f"共 {len(items)} 条，用过 {used} 次")

    # ============================================================
    # 搜索应用动作
    # ============================================================
    def _do_search(self, app_name: str):
        """搜索应用：搜索路线动画 + 真实/模拟查找 + 结果卡片"""
        self._current_search_text = app_name
        self.set_pose("searching")

        def on_route_done():
            # 路线走完，执行查找
            info = self.system.find_app(app_name)
            if info:
                self.show_bubble("找到了！")
                self.set_emotion("star")
                self.show_card(app_name, info.path, info.size_text)
                self.set_pose("jump")
                self.pet.jump(on_done=lambda: self._finish_action())
            else:
                self.set_emotion("sweat")
                self.show_bubble(f"未找到{app_name}，请检查是否已安装")
                self._note_plan_failure(f"没找到应用{app_name}")
                self._finish_action()

        self.pet.search_route(
            on_visit=lambda a: None,   # 每个锚点可扩展张望动画
            on_done=on_route_done,
        )

    # ============================================================
    # 打开应用动作
    # ============================================================
    def _do_open(self, app_name: str):
        """打开应用：走到该软件所在位置（桌面图标/任务栏）→ 启动 → 庆祝"""
        # 白名单预检
        if not self.system.is_whitelisted(app_name):
            self.set_emotion("angry")
            self.show_bubble(f"{app_name} 不在安全白名单内，不可打开")
            self._finish_action()
            return

        self.set_pose("walk")
        target = self._locate_app(app_name)
        if target is not None:
            # 桌上就有这个软件的图标：走过去指着它打开
            self.show_bubble(f"{app_name}就在桌面，这就去打开。")
            self.pet.move_to(target[0], target[1], duration=2600,
                             tag="goto_app",
                             on_done=lambda: self._launch_and_report(app_name))
        else:
            self.pet.go_taskbar(
                on_done=lambda: self._launch_and_report(app_name)
            )

    @staticmethod
    def _locate_app(app_name: str):
        """
        定位应用在屏幕上的位置（用于"走到那个软件那里"）。
        优先找桌面图标；找不到返回 None，降级为任务栏。
        """
        try:
            from framework.desktop_icon import desktop_icon_position
            pos = desktop_icon_position(app_name)
            if pos is None:
                return None
            # 站在图标正上方（避免挡住图标自身）
            x = pos[0] + 20
            y = max(0, pos[1] - int(config.PET_SIZE * 0.75))
            from PyQt5.QtWidgets import QApplication
            screen = QApplication.primaryScreen().availableGeometry()
            x = max(screen.left(), min(x, screen.right() - config.PET_SIZE))
            y = max(screen.top(), min(y, screen.bottom() - config.PET_SIZE))
            logger.info(f"[Framework] 桌面定位到 {app_name}: {pos} -> ({x},{y})")
            return (x, y)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Framework] 定位 {app_name} 失败: {e}")
            return None

    def _launch_and_report(self, app_name: str):
        """到达任务栏后启动应用并汇报结果"""
        self.show_bubble("正在打开...")
        ok, msg = self.system.launch_app(app_name)
        self.memory.add("assistant", msg)
        if ok:
            self.set_emotion("happy")
            self.set_pose("attack")           # 挥剑庆祝（UI 层播完自动回 idle）
            self.show_bubble("办妥了！")
            self._finish_action()
        else:
            self.set_emotion("sweat")
            self.show_bubble(msg)
            self._note_plan_failure(f"打不开{app_name}")
            self._finish_action()

    # ============================================================
    # 截图动作
    # ============================================================
    def _do_screenshot(self):
        """截图：先让用户看清确认回复 -> 隐藏助手窗口 -> 抓全屏 -> 另存为"""
        from PyQt5.QtCore import QTimer
        from PyQt5.QtWidgets import QApplication, QFileDialog

        # 确认回复停留固定 2.5 秒即开抓（截图前所有助手窗口会一起隐藏，气泡不入镜）
        delay = config.SCREENSHOT_REPLY_MS

        def capture():
            screen = QApplication.primaryScreen()
            pixmap = screen.grabWindow(0)      # 0 = 整个桌面
            self.screenshot_restore.emit()
            self.set_pose("idle")

            if pixmap.isNull():
                self.set_emotion("sweat")
                self.show_bubble("截图失败了，可愿再试一次？")
                self._finish_action()
                return

            default_name = time.strftime("截图_%Y%m%d_%H%M%S.png")
            self._stop_chain_watchdog()   # 用户慢慢选保存位置的时间不算超时
            path, _ = QFileDialog.getSaveFileName(
                None, "保存截图", default_name,
                "PNG 图片 (*.png);;JPEG 图片 (*.jpg);;所有文件 (*)")
            if not path:
                self.show_bubble("好吧，此次截图未保存。")
                self._finish_action()
                return

            if not path.lower().endswith((".png", ".jpg", ".jpeg")):
                path += ".png"
            if pixmap.save(path):
                self.set_emotion("star")
                self.show_bubble("截图已保存！")
                self.show_card("📸 截图已保存", path, "")
                logger.info(f"[Framework] 截图已保存: {path}")
            else:
                self.set_emotion("sweat")
                self.show_bubble("截图保存失败，换个保存位置试试？")
            self._finish_action()

        def begin():
            # 回复展示完毕：隐藏助手入镜窗口，再等它真正从屏幕消失
            self.screenshot_hide.emit()
            self.set_pose("think")
            QTimer.singleShot(350, capture)

        QTimer.singleShot(delay, begin)

    # ============================================================
    # 音量调节动作
    # ============================================================
    def _do_set_volume(self, action: dict):
        """调音量：没带 app 调系统总音量；带了 app 只调该应用（音量合成器）"""
        from framework import volume_controller as volume

        try:
            vol = int(float(str(action.get("volume", "")).strip().rstrip("%")))
        except ValueError:
            self.set_emotion("sweat")
            self.show_bubble("音量数值没读懂，请给我 0 到 100 的数字。")
            self._finish_action()
            return
        vol = max(0, min(100, vol))
        app = (action.get("app") or "").strip()

        if app:
            matched = volume.set_app_volume(app, vol)
            if matched:
                self.set_emotion("happy")
                self.show_bubble(f"已把{app}的声音调到{vol}，系统音量保持不变。")
            else:
                self.set_emotion("sweat")
                self.show_bubble(f"没找到{app}的声音会话——它得正在运行且"
                                 f"出过声才能单独调节。系统音量没有动。")
            self._finish_action()
            return

        actual = volume.set_system_volume(vol)
        if actual >= 0:
            self.set_emotion("happy")
            self.show_bubble(f"系统音量已调到{actual}。")
        else:
            self.set_emotion("sweat")
            self.show_bubble("系统音量调节失败，请手动试试。")
        self._finish_action()

    # ============================================================
    # 电源操作：关机 / 重启 / 锁屏 / 取消
    # ============================================================
    def _do_power(self, kind: str, delay=None):
        """关机或重启：先亮确认气泡，停留 POWER_REPLY_MS 再真正下单"""
        from PyQt5.QtCore import QTimer
        from framework import power_controller as power

        if not config.ALLOW_POWER_ACTIONS:
            self.set_emotion("angry")
            self.show_bubble("电源操作已在设置中禁用。")
            self._finish_action()
            return

        # delay 为空串/None 时交给 power_controller 用配置默认值
        if delay in ("", None):
            delay = None
        else:
            try:
                delay = int(float(str(delay).rstrip("秒s")))
            except ValueError:
                delay = None

        label = "关机" if kind == "shutdown" else "重启"
        self.set_pose("think")

        def fire():
            func = power.shutdown if kind == "shutdown" else power.restart
            ok, actual = func(delay)
            if ok:
                self.set_emotion("exclaim")
                self.show_bubble(
                    f"{label}已排定：{actual} 秒后执行。"
                    f"想反悔就说「取消关机」，我立刻撤销。"
                )
                self.memory.add("assistant",
                                f"[系统] {label}已排定 {actual} 秒后执行")
            else:
                self.set_emotion("sweat")
                self.show_bubble(f"{label}指令下达失败，请手动操作。")
            self._finish_action()

        # 留一点时间让用户看到确认语再执行
        QTimer.singleShot(config.POWER_REPLY_MS, fire)

    def _do_lock(self):
        """锁屏：立即执行"""
        from framework import power_controller as power

        if not config.ALLOW_POWER_ACTIONS:
            self.set_emotion("angry")
            self.show_bubble("电源操作已在设置中禁用。")
            self._finish_action()
            return

        if power.lock():
            self.set_emotion("happy")
            self.show_bubble("已锁定，回头见。")
        else:
            self.set_emotion("sweat")
            self.show_bubble("锁屏失败，试试 Win+L 吧。")
        self._finish_action()

    def _do_cancel_shutdown(self):
        """取消已排定的关机/重启"""
        from framework import power_controller as power

        power.cancel_shutdown()
        self.set_emotion("happy")
        self.show_bubble("已取消关机/重启。")
        self.memory.add("assistant", "[系统] 已取消排定的电源操作")
        self._finish_action()

    # ============================================================
    # 文件修改（用户发来 pptx/docx，AI 改写后返回新文件）
    # ============================================================
    def request_doc_edit(self, path: str):
        """右键菜单/拖拽投喂文件入口：读取内容，进入等待修改指令状态"""
        from framework import doc_editor

        if not config.ALLOW_DOC_GENERATION:
            self.set_emotion("angry")
            self.show_bubble("文档功能已在设置中禁用。")
            return
        path = (path or "").strip().strip('"')
        if not os.path.exists(path):
            self.set_emotion("sweat")
            self.show_bubble("这个文件不存在，是不是被移动或删除了？")
            return
        if self._doc_edit is not None:
            self.show_bubble("上一份还没改完呢，先告诉我要怎么改，"
                             "或说「不改了」换新文件。")
            return
        try:
            kind, spec = doc_editor.extract_content(path)
        except Exception as e:                  # noqa: BLE001 文件损坏/加密等
            logger.warning(f"[Framework] 文件读取失败 {path}: {e}")
            self.set_emotion("sweat")
            self.show_bubble("这份文件打不开（可能损坏或加了密），换个文件试试？")
            return
        if not spec or (kind == "ppt" and not spec.get("slides")) \
                or (kind == "doc" and not spec.get("sections")):
            self.show_bubble("这份文件里没有可编辑的文字内容。")
            return

        self._doc_edit = {"path": path, "kind": kind, "spec": spec}
        self.set_emotion("exclaim")
        count = len(spec.get("slides") or spec.get("sections") or [])
        type_name = "演示文稿" if kind == "ppt" else "文档"
        self.show_bubble(
            f"收到{type_name}「{os.path.basename(path)}」，共 {count} "
            f"{'页' if kind == 'ppt' else '个章节'}。要怎么改？直接告诉我，"
            f"比如「每页加一条总结」「语气正式一点」。说「不改了」可退出。")
        logger.info(f"[Framework] 文件修改: 进入等待状态 {path}")

    def _send_doc_edit(self, instruction: str):
        """把原文件内容 + 修改要求发给 AI，要求返回 create_doc 动作"""
        from framework import doc_editor

        if not config.USE_REAL_AI or not config.DEEPSEEK_API_KEY:
            self.show_bubble("修改文件需要联网的 AI 能力，"
                             "请先在设置里填好 API Key 再来找我。")
            self._doc_edit = None
            return
        edit = self._doc_edit
        message = doc_editor.build_edit_message(edit["kind"], edit["spec"],
                                                instruction)
        logger.info(f"[Framework] 文件修改: 发送指令 {instruction!r}")
        self._begin_turn(f"[文件修改] {instruction}")
        self.memory.add("user", f"[文件修改] "
                        f"{os.path.basename(edit['path'])}：{instruction}")
        self.history_changed.emit()
        self.set_pose("think")
        # 不带历史上下文：改写任务要求模型只盯着原文件内容，避免串味
        self._start_ai_worker(message, history=[])

    # ============================================================
    # 文档生成动作（PPT / Word / Excel）
    # ============================================================
    def _do_create_doc(self, action: dict):
        """文档生成：弹窗让用户选保存位置 -> 后台线程渲染 -> 卡片反馈路径"""
        from PyQt5.QtWidgets import QFileDialog
        from framework.doc_generator import DOC_TYPE_EXTS

        if not config.ALLOW_DOC_GENERATION:
            self.set_emotion("angry")
            self.show_bubble("文档生成已在设置中禁用。")
            self._note_plan_failure("文档生成已被禁用")
            self._finish_action()
            return

        kind = action.get("doc_type", "")
        content = action.get("content") or {}
        if kind not in DOC_TYPE_EXTS:
            self.set_emotion("sweat")
            self.show_bubble("这种文档类型我还不会做，请说 PPT、Word 或表格。")
            self._note_plan_failure(f"文档类型 {kind} 不支持")
            self._finish_action()
            return
        if not content:
            self.set_emotion("sweat")
            self.show_bubble("内容我还没拿到，请把要写的东西再描述一遍。")
            self._note_plan_failure("文档内容为空")
            self._finish_action()
            return
        # 质量门：渲染前体检内容（软失败自我纠正——单薄内容先纠正再产出）。
        # 学到的规则会抬高门槛：被「内容太单薄」坑过之后，标准真的会变严。
        from framework.quality_gate import assess_doc_spec
        gate = assess_doc_spec(kind, content)
        if self.rules is not None and config.EVOLUTION_ENABLED \
                and kind == "ppt" and gate.get("ok"):
            try:
                stricter = int((self.rules.apply(
                    {"action": "create_doc", "doc_type": kind}) or {})
                    .get("min_slides") or 0)
                slides = content.get("slides") if isinstance(content, dict) else None
                if stricter and isinstance(slides, list) \
                        and len(slides) < stricter:
                    gate = {"ok": False,
                            "reason": f"按学到的规则正文至少 {stricter} 页"
                                      f"（当前 {len(slides)} 页）"}
            except Exception as e:      # noqa: BLE001
                logger.warning(f"[Framework] 规则门槛读取失败（已忽略）: {e}")
        if not gate["ok"]:
            plan_running = (self._plan is not None
                            and self._plan.get("status") == "running")
            if plan_running:
                self.set_emotion("sweat")
                self.show_bubble(f"这版内容太单薄（{gate['reason']}），"
                                 "我重新规划，写一版更充实的。")
                self._note_plan_failure(
                    f"文档内容质量不达标（{gate['reason']}）")
                self._finish_action()
                return
            if config.USE_REAL_AI and not self._doc_quality_retried:
                self._doc_quality_followup(kind, gate["reason"])
                return
            # 降级：离线演示 / 纠正后仍不达标——带预警照常产出
            self.set_emotion("sweat")
            self.show_bubble(f"这版内容偏单薄（{gate['reason']}），"
                             "先做一版你看看，不满意随时让我重做。")

        ext = DOC_TYPE_EXTS[kind]
        # 样式优先级：自动学到的偏好是默认值，用户这一句的显式要求更高。
        # 说「我喜欢深色 PPT」之后，下一份 PPT 会自己变深色，无需重复交代。
        style = {}
        if self.prefs is not None and config.PREFERENCE_LEARNING:
            try:
                style.update(self.prefs.doc_style() or {})
            except Exception as e:      # noqa: BLE001
                logger.warning(f"[Framework] 偏好样式读取失败（已忽略）: {e}")
        if isinstance(action.get("style"), dict):
            style.update(action["style"])
        if style and isinstance(content, dict):
            content = dict(content)
            content["style"] = style
        # 文件改写流程会带预设输出路径（原名_修改.ext），不弹保存对话框；
        # 用户指定了保存位置（桌面/文档/下载）时也免弹窗，直接保存
        preset = action.get("save_path") or ""
        location_dir = ""
        if not preset:
            from framework.doc_generator import resolve_location
            location_dir = resolve_location(action.get("location"))
        if preset:
            path = preset
        elif location_dir:
            # 只取文件名部分：模型偶尔会把整条路径塞进 filename
            default_name = os.path.basename(action.get("filename") or "")
            default_name = "凌零文档_" + time.strftime("%Y%m%d_%H%M%S") \
                if not default_name else default_name
            if not default_name.lower().endswith(ext):
                default_name += ext
            from framework.doc_generator import unique_path
            path = unique_path(os.path.join(location_dir, default_name))
            loc_label = {"Desktop": "桌面"}.get(
                os.path.basename(location_dir), os.path.basename(location_dir))
        else:
            # 只取文件名部分：模型偶尔会把整条路径塞进 filename
            default_name = os.path.basename(action.get("filename") or "")
            default_name = "凌零文档_" + time.strftime("%Y%m%d_%H%M%S") \
                if not default_name else default_name
            if not default_name.lower().endswith(ext):
                default_name += ext
            type_label = {"ppt": "演示文稿 (*.pptx)",
                          "doc": "Word 文档 (*.docx)",
                          "xlsx": "Excel 表格 (*.xlsx)",
                          "html": "网页报告 (*.html)"}[kind]
            self._stop_chain_watchdog()   # 用户慢慢选保存位置的时间不算超时
            path, _ = QFileDialog.getSaveFileName(
                None, "保存文档", default_name, f"{type_label};;所有文件 (*)")
            if not path:
                self.show_bubble("好吧，此次先不生成了。")
                self._finish_action()
                return
            if not path.lower().endswith(ext):
                path += ext

        self.set_pose("think")
        from framework.doc_generator import has_image_intent
        if preset:
            self.show_bubble("正在按你的要求改写，请稍候……")
        elif has_image_intent(kind, content) and config.PEXELS_API_KEY:
            self.show_bubble("正在落笔并找配图，请稍候……")
        elif location_dir:
            self.show_bubble(f"正在落笔，做好后直接放到{loc_label}，"
                             "请稍候……")
        else:
            self.show_bubble("正在落笔，请稍候……")
        self._doc_worker = _DocWorker(kind, content, path)
        self._doc_worker.done.connect(self._on_doc_done)
        self._doc_worker.start()
        self._arm_chain_watchdog("create_doc")   # 生成期间重新布防

    def _doc_quality_followup(self, kind: str, reason: str):
        """文档软失败自我纠正：把质量体检结果发回模型，要求重写内容。
        模型返回新的 create_doc 动作后按正常链路执行（含样式/位置）。"""
        self._traj_event(
            "quality", action="create_doc", source="system", ok=False,
            payload={"doc_type": kind, "reason": str(reason)},
            note=f"文档内容不达标：{reason}")
        self._doc_quality_retried = True
        self._busy = True
        self._suppress_fallback = True       # 纠正轮不走关键词兜底
        self._stop_chain_watchdog()          # 旧动作被纠正轮接替
        kind_label = {"ppt": "PPT", "doc": "Word 文档",
                      "xlsx": "Excel 表格", "html": "网页报告"}.get(
                          kind, "文档")
        self.set_emotion("sweat")
        self.show_bubble(f"内容有点单薄（{reason}），我重新写一版更充实的。")
        message = (
            f"{self._current_user_text}\n\n"
            f"（系统提示：【质量纠错】你刚才要生成的{kind_label}内容"
            f"没有通过质量体检：{reason}。请重新返回 create_doc 动作"
            f"（doc_type 仍为 \"{kind}\"，filename/style/location 照旧），"
            "内容必须充实饱满：PPT 正文 6~10 页、每页 3~6 条 12~24 字的"
            "完整要点；文档分 3 个以上章节、每章 2 段以上；表格给足数据行。"
            "只返回动作，不要解释。）")
        logger.warning(f"[Framework] 文档质量纠错: {kind_label} {reason}")
        self._start_ai_worker(
            message,
            facts=self.memory.get_facts_text(), notes=self._notes_text())

    def _on_doc_done(self, ok: bool, err: str, path: str):
        """文档生成完毕：记录交付 + 卡片反馈保存路径；网页报告自动打开"""
        kind = self._doc_worker._doc_type if self._doc_worker else ""
        self._doc_worker = None
        if ok:
            self.set_emotion("star")
            try:
                size_kb = os.path.getsize(path) / 1024
                size_text = (f"{size_kb / 1024:.1f} MB"
                             if size_kb >= 1024 else f"{size_kb:.0f} KB")
            except OSError:
                size_text = ""
            kind_label = {"ppt": "PPT", "doc": "Word 文档",
                          "xlsx": "Excel 表格", "html": "网页报告"}.get(
                              kind, "文档")
            self.tasks.add_delivery(os.path.basename(path), path, kind)
            self.tasks.log_work("deliver",
                                f"生成了{kind_label}《"
                                f"{os.path.basename(path)}》")
            self.show_bubble(f"{kind_label}已生成，请查收！")
            self.show_card("📄 文档已生成", path, size_text)
            self.memory.add("assistant", f"[系统] 已生成文档: {path}")
            if kind == "html":
                try:            # 网页报告：默认浏览器直接打开验收
                    os.startfile(path)      # noqa: S606 Windows 专用
                except OSError as e:
                    logger.warning(f"[Doc] 自动打开网页失败: {e}")
        else:
            self.set_emotion("sweat")
            self.show_bubble(f"文档生成失败了（{err}）。可再试一次。")
            self._note_plan_failure(f"文档生成失败（{err}）")
        self._finish_action()

    # ============================================================
    # 屏幕视觉理解（多模态）：截图 -> 视觉模型 -> 气泡回答
    # ============================================================
    def _do_see_screen(self, action: dict):
        """看屏幕：隐藏助手窗口截图，交给视觉大模型"看"后回答"""
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QApplication

        question = (action.get("question") or "").strip()
        if not config.VISION_API_KEY:
            self.set_emotion("sweat")
            self.show_bubble("我还看不懂屏幕——需要视觉模型的 Key。"
                             "智谱官网（open.bigmodel.cn）可免费申请，"
                             "填到设置的「视觉Key」即可。")
            self._note_plan_failure("视觉模型 Key 未配置")
            self._finish_action()
            return
        self.set_pose("think")
        self.show_bubble("让我看看你的屏幕……")

        def capture():
            self.screenshot_hide.emit()

            def grab():
                screen = QApplication.primaryScreen()
                pixmap = screen.grabWindow(0)
                self.screenshot_restore.emit()
                self.set_pose("think")
                if pixmap.isNull():
                    self.set_emotion("sweat")
                    self.show_bubble("屏幕没抓下来，请再试一次。")
                    self._finish_action()
                    return
                # 压到 1600 宽再编码：视觉接口不必吃原生分辨率
                if pixmap.width() > 1600:
                    pixmap = pixmap.scaledToWidth(1600,
                                                  Qt.SmoothTransformation)
                import tempfile
                path = os.path.join(tempfile.gettempdir(),
                                    f"pet_screen_{int(time.time())}.jpg")
                if not pixmap.save(path, "JPG", 85):
                    self.set_emotion("sweat")
                    self.show_bubble("屏幕图片编码失败，请再试一次。")
                    self._finish_action()
                    return
                self._vision_worker = _VisionWorker(path, question)
                self._vision_worker.done.connect(self._on_vision_done)
                self._vision_worker.start()
                self._arm_vision_watchdog()

            # 等助手窗口真正从屏幕上消失再抓
            QTimer.singleShot(350, grab)

        # 让"让我看看"先展示片刻再抓屏
        QTimer.singleShot(1000, capture)

    def _on_vision_done(self, ok: bool, answer: str):
        """视觉模型返回：播报屏幕理解结果"""
        if self._vision_watchdog is not None:
            self._vision_watchdog.stop()
            self._vision_watchdog = None
        self._vision_worker = None
        if ok:
            self.set_emotion("star")
            self.show_bubble(answer)
            self._reply_parts.append(f"[看屏幕] {answer}")
        else:
            self.set_emotion("sweat")
            self.show_bubble(f"{answer}。可再试一次。")
            self._note_plan_failure(f"看屏幕失败（{answer}）")
        self._finish_action()

    def _arm_vision_watchdog(self):
        """看屏幕兜底闹钟：视觉线程万一卡死（如 DNS 解析不受 requests
        超时管束），到点强制收尾并给出反馈，绝不让助手无声卡住。"""
        seconds = (vision_timeout_seconds() * (1 + len(_VISION_ATTEMPT_DELAYS))
                   + sum(_VISION_ATTEMPT_DELAYS) + 15)
        watchdog = QTimer(self)
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(self._on_vision_watchdog)
        self._vision_watchdog = watchdog
        watchdog.start(seconds * 1000)
        logger.info(f"[Vision] 视觉请求看门狗已布防（{seconds}s）")

    def _on_vision_watchdog(self):
        if self._vision_watchdog is not None:
            self._vision_watchdog.stop()
            self._vision_watchdog = None
        worker = self._vision_worker
        if worker is None:              # 已正常返回，这是残留的闹钟
            return
        try:
            worker.done.disconnect(self._on_vision_done)
        except (TypeError, RuntimeError):   # 信号可能已断/对象已销毁
            pass
        # 线程可能还卡在系统调用里，留着引用防止 QThread 被 GC 时崩溃
        self._zombie_workers = [w for w in self._zombie_workers
                                if not w.isFinished()]
        self._zombie_workers.append(worker)
        self._vision_worker = None
        logger.warning("[Vision] 看屏幕等待超时，本次请求已放弃")
        self.set_emotion("sweat")
        self.show_bubble("等了好久都没等到视觉模型的回答，这次先算了，"
                         "过会儿网络顺畅些再让我看看？")
        self._note_plan_failure("看屏幕等待超时")
        self._finish_action()

    # ============================================================
    # 长期记忆（remember 动作写入，重启不丢，注入后续对话）
    # ============================================================
    def _do_remember(self, action: dict):
        """remember 动作：把用户告知的事实写入长期记忆"""
        fact = (action.get("fact") or "").strip()
        if not fact:
            self.set_emotion("sweat")
            self.show_bubble("你想让我记住什么呢？")
            self._finish_action()
            return
        if len(fact) > config.FACT_TEXT_MAX:
            fact = fact[:config.FACT_TEXT_MAX]
        self.memory.remember_fact(fact)
        self.set_emotion("star")
        self.show_bubble(f"记住了：{fact}")
        self._reply_parts.append(f"（已记入长期记忆：{fact}）")
        self._finish_action()

    # ============================================================
    # 任务规划（确认流）：show_plan 亮卡等确认 -> 逐步执行 -> 完成汇报
    # ============================================================
    def _do_show_plan(self, action: dict):
        """show_plan 动作：弹出计划卡（常驻在助手旁），暂停后续动作等确认。

        特例：重规划轮返回的计划（系统主动请求的）不走确认流，
        直接替换剩余步骤并自动继续执行——重规划必须保持无人值守。
        """
        steps = action.get("steps") or []
        if config.PLAN_DAG_ENABLED:
            try:
                from framework.plan_graph import (
                    compile_plan, validate as validate_plan, plan_summary)
                graph = compile_plan(list(steps))
                issues = validate_plan(graph)
                self._traj_event(
                    "plan", source="model", ok=not issues,
                    payload={"steps": [str(s)[:60] for s in steps],
                             "summary": plan_summary(graph),
                             "issues": issues[:5]},
                    note="计划已编译为依赖图并完成静态校验")
            except Exception as e:      # noqa: BLE001 DAG 只是增强，不能挡住计划
                logger.warning(f"[Framework] 计划图编译失败（已降级）: {e}")
        if self._plan_auto_plan and self._plan is not None \
                and self._plan.get("status") == "running":
            self._plan_auto_plan = False
            self._plan_fail_note = ""
            plan = self._plan
            if steps:                        # 空列表防御：保留原步骤
                plan["steps"] = steps
            self._plan_done = 0
            self._plan_pending_queue = list(self._action_queue)
            self._action_queue = []
            if self.plan_card is not None:
                self.plan_card.update_steps(plan["steps"])
            self.set_emotion("exclaim")
            self.show_bubble(f"现场有变，计划已调整（还剩 "
                             f"{len(plan['steps'])} 步），继续开工。")
            self._reply_parts.append("（计划已自动重规划）")
            if self._reply_parts and self._current_user_text:
                self.memory.add("assistant", " ".join(self._reply_parts))
                self._reply_parts = []
                self.history_changed.emit()
            logger.info(f"[Framework] 任务计划: 自动重规划 -> "
                        f"{len(plan['steps'])} 步")
            if self._plan_pending_queue:
                self._busy = True
                self._action_queue = self._plan_pending_queue
                self._plan_pending_queue = []
                self._run_next_action()
            else:
                self._busy = False
                self.set_pose("idle")
                QTimer.singleShot(600, self._plan_next_round)
            return
        # 上一个计划还没收尾就来了新计划：静默放下旧的
        if self._plan is not None:
            self._plan = None
            self._plan_done = 0
            self._plan_pending_queue = []
        self._plan = {"steps": steps, "status": "pending", "rounds": 0,
                      "replans": 0}
        self._plan_done = 0
        self._plan_pending_queue = list(self._action_queue)
        self._action_queue = []
        self.set_emotion("exclaim")
        px, py = self._pet_pos()
        self._ensure_plan_card().popup(steps, px, py)
        self.show_bubble(
            f"计划拟好了，共 {len(steps)} 步。没问题就点卡片上的"
            f"「开始执行」；要调整直接说。")
        self._reply_parts.append(f"（已给出 {len(steps)} 步计划，等用户确认）")
        # 本轮到此收尾：等用户确认后再继续（确认后恢复暂存的后续动作）
        if self._reply_parts and self._current_user_text:
            self.memory.add("assistant", " ".join(self._reply_parts))
            self._reply_parts = []
            self.history_changed.emit()
        self._busy = False
        self.set_pose("idle")

    # ---- 计划卡（懒加载 ui/plan_card.PlanCard）----
    def _ensure_plan_card(self):
        if self.plan_card is None:
            from ui.plan_card import PlanCard
            self.plan_card = PlanCard()
            self.plan_card.confirm_requested.connect(self._confirm_plan)
            self.plan_card.cancel_requested.connect(self._cancel_plan)
        return self.plan_card

    def _pet_pos(self) -> tuple:
        """助手当前屏幕位置 (x, y)（计划卡贴边用）"""
        try:
            return int(self.pet.position[0]), int(self.pet.position[1])
        except Exception:  # noqa: BLE001
            return (0, 0)

    def _update_plan_card(self):
        if self.plan_card is not None and self._plan is not None:
            self.plan_card.update_progress(min(self._plan_done,
                                               len(self._plan["steps"])))

    def _close_plan_card(self):
        if self.plan_card is not None:
            self.plan_card.close()
            self.plan_card = None

    # ---- 确认 / 取消 / 完成 ----
    def _confirm_plan(self):
        plan = self._plan
        if not plan or plan.get("status") != "pending" or self._busy:
            return
        plan["status"] = "running"
        plan["rounds"] = 0
        self._plan_done = 0
        self._plan_fail_note = ""
        self._plan_auto_plan = False
        self._begin_turn(f"[计划执行] {'；'.join(plan.get('steps') or [])[:30]}")
        self._update_plan_card()
        self.set_emotion("happy")
        self.show_bubble("好，按计划开干！")
        if self._plan_pending_queue:
            # 模型当时在计划后面跟了动作：先执行这部分
            self._busy = True
            self._action_queue = self._plan_pending_queue
            self._plan_pending_queue = []
            self._run_next_action()
        else:
            self._plan_next_round()      # 让 AI 从第一步开始执行

    def _clear_plan_transient(self):
        """统一清理一轮执行产生的瞬时标记。

        以前 _cancel_plan / _abandon_plan / _complete_plan / AI 失败兜底
        各写一遍复位，漏掉 _suppress_fallback 时，下一轮的普通指令会被
        当成「计划续轮」而不走意图兜底（说「打开微信」只回一句确认语）。
        所有退出路径统一调用这里；以后新增状态字段也只需改这一处。
        """
        self._plan_round_pending = False
        self._plan_round_had_actions = False
        self._suppress_fallback = False
        self._plan_auto_plan = False
        self._plan_fail_note = ""
        self._plan_done = 0
        self._plan_pending_queue = []

    def _cancel_plan(self):
        if self._plan is None:
            return
        self._clear_plan_transient()
        self._plan = None
        self._action_queue = []
        self._close_plan_card()
        self._busy = False
        self.set_pose("idle")
        self.show_bubble("好，计划取消了。")
        logger.info("[Framework] 任务计划: 用户取消")

    def _abandon_plan(self):
        """执行中来了一条新任务：静默收尾当前计划（汇报进度，不挡新事）"""
        plan = self._plan
        if plan is None:
            return
        done = min(self._plan_done, len(plan["steps"]))
        self._clear_plan_transient()
        self._plan = None
        self._close_plan_card()
        self._busy = False
        self._review_effects(offer=False)   # 半途搁置的计划也留一份可撤的账
        if plan.get("status") == "running" and done:
            self.show_bubble(f"先放下手头的计划（推进了 {done}/"
                             f"{len(plan['steps'])} 步），这就办新的事。")
        logger.info("[Framework] 任务计划: 因新指令搁置")

    def _complete_plan(self):
        plan = self._plan
        steps = plan["steps"] if plan else []
        self._clear_plan_transient()
        self._plan = None
        self.set_emotion("happy")
        self.show_bubble("计划执行完毕，全部落实！")
        self._reply_parts.append("（计划全部完成）")
        if plan is not None:
            self.tasks.log_work("plan", "完成了一个任务计划："
                                + "；".join(steps[:6]))
        if self._reply_parts and self._current_user_text:
            self.memory.add("assistant", " ".join(self._reply_parts))
            self._reply_parts = []
        self._busy = False
        self.set_pose("idle")
        self._review_effects()
        self._announce_effects()
        if self.plan_card is not None and steps:
            self.plan_card.mark_done()    # 整卡打勾，停留几秒自动关
        logger.info("[Framework] 任务计划: 全部完成")

    # ---- 计划执行循环 ----
    def _plan_next_round(self):
        """让 AI 继续推进计划的下一个未完成步骤（一轮一批动作）"""
        plan = self._plan
        if not plan or plan.get("status") != "running" or self._busy \
                or self._plan_round_pending:
            return
        self._plan_round_pending = True
        plan["rounds"] = plan.get("rounds", 0) + 1
        steps = plan["steps"]
        done = min(self._plan_done, len(steps))
        progress = "\n".join(f"{'✔' if i < done else '○'} {s}"
                             for i, s in enumerate(steps))
        message = (f"【计划执行】用户已在计划卡上确认，现在开始执行。计划进度：\n"
                   f"{progress}\n"
                   f"请直接执行下一个未完成步骤对应的工具动作（已完成打 ✔ 的步骤"
                   f"不要重复做）；本轮做 1~2 步即可，做完系统会自动让你继续，"
                   f"【不要】停下来询问用户要不要继续。")
        self._current_user_text = f"[计划执行] {done}/{len(steps)}"
        self._reply_parts = []
        self.memory.add("user", f"[计划执行] 继续推进计划"
                                f"（已完成 {done}/{len(steps)}）")
        self.history_changed.emit()
        self.set_pose("think")
        self._suppress_fallback = True   # 计划轮不走关键词兜底，避免误触发工具
        logger.info(f"[Framework] 任务计划: 第 {plan['rounds']} 轮执行"
                    f"（{done}/{len(steps)}）")
        self._start_ai_worker(
            message,
            facts=self.memory.get_facts_text(),
            notes=self._notes_text(), skills=self.skills.catalog_text())

    def _note_plan_failure(self, reason: str):
        """计划执行中某步受阻：记下原因，本轮收口后触发自动重规划"""
        # 失败是「自我进化」的原料：先落进轨迹，收口时由规则库归纳
        self._traj_event("failure", action=self._current_action_name,
                         source="system", ok=False, note=str(reason)[:200])
        plan = self._plan
        if plan is None or plan.get("status") != "running":
            return
        steps = plan.get("steps") or []
        step = steps[min(self._plan_done, len(steps) - 1)] if steps else ""
        self._plan_fail_note = f"步骤「{step}」{reason}"
        logger.warning(f"[Framework] 计划受阻: {self._plan_fail_note}")

    def _plan_replan(self):
        """动态重规划：把受阻原因和当前进度发给 AI，让它调整剩余步骤。

        AI 返回新的 show_plan 后不走确认卡（_do_show_plan 自动接续），
        按新计划继续无人值守执行；重规划次数受 PLAN_MAX_REPLANS 约束。
        """
        plan = self._plan
        if not plan or plan.get("status") != "running" or self._busy \
                or self._plan_round_pending:
            return
        reason = self._plan_fail_note or "执行受阻"
        self._plan_fail_note = ""
        plan["replans"] = plan.get("replans", 0) + 1
        self._plan_round_pending = True
        self._plan_auto_plan = True
        steps = plan["steps"]
        done = min(self._plan_done, len(steps))
        progress = "\n".join(f"{'✔' if i < done else '○'} {s}"
                             for i, s in enumerate(steps))
        # 增量重算：只把失败节点及其传递后继交给模型调整，
        # 而不是整轮推倒重写。这是计划依赖图真正被用上的地方。
        focus = ""
        if config.PLAN_DAG_ENABLED:
            try:
                from framework.plan_graph import compile_plan, affected_subgraph
                graph = compile_plan(list(steps))
                node_ids = [n["id"] for n in graph.get("nodes", [])]
                if node_ids:
                    affected = affected_subgraph(
                        graph, node_ids[min(done, len(node_ids) - 1)])
                    focus = ("\n系统已算出只需重算这几步（其余已完成的不要动）："
                             + "、".join(affected[:6]) + "。")
                    self._traj_event(
                        "plan", source="system",
                        payload={"affected": affected, "done": done},
                        note="增量重算：只调整受影响的子图")
            except Exception as e:      # noqa: BLE001
                logger.warning(f"[Framework] 受影响子图计算失败（已降级）: {e}")
        message = (f"【计划重规划】计划执行遇到问题，请你调整剩余步骤。{focus}\n"
                   f"原计划进度：\n{progress}\n"
                   f"遇到的问题：{reason}\n"
                   f"系统提示：只返回一个 show_plan 动作，steps 列出调整后"
                   f"【还没完成】的步骤（2~6 条：做不到的跳过，或换条路子），"
                   f"不要执行其他动作、不要询问用户；返回后系统会按新计划"
                   f"自动继续执行。")
        self._current_user_text = f"[计划重规划] {done}/{len(steps)}"
        self._reply_parts = []
        self.memory.add("user", f"[计划重规划] 因「{reason}」请求调整计划")
        self.history_changed.emit()
        self.set_pose("think")
        self._suppress_fallback = True   # 重规划轮不走关键词兜底
        logger.info(f"[Framework] 任务计划: 第 {plan['replans']} 次重规划"
                    f"（{done}/{len(steps)}，原因：{reason}）")
        self._start_ai_worker(
            message,
            facts=self.memory.get_facts_text(),
            notes=self._notes_text(), skills=self.skills.catalog_text())

    # ============================================================
    # 定时提醒 / 待办清单（任务台持久化，重启不丢）
    # ============================================================
    def _do_remind(self, action: dict):
        """remind 动作：登记一条定时提醒（seconds 秒级精确，兼容 minutes）"""
        text = (action.get("text") or "").strip()
        if not text:
            self.set_emotion("sweat")
            self.show_bubble("要提醒你什么事呢？")
            self._finish_action()
            return
        seconds = None
        try:
            seconds = float(action.get("seconds"))
        except (TypeError, ValueError):
            pass
        if seconds is None:
            try:                              # 兼容模型给 minutes 的旧口径
                seconds = float(action.get("minutes")) * 60.0
            except (TypeError, ValueError):
                seconds = None
        if seconds is None:
            seconds = config.REMINDER_DEFAULT_MINUTES * 60.0
        seconds = max(config.REMINDER_MIN_SEC,
                      min(seconds, float(config.REMINDER_MAX_SEC)))
        self.tasks.add_reminder(text, time.time() + seconds)
        self.set_emotion("exclaim")
        wait = format_duration(seconds)
        self.tasks.log_work("remind", f"设了提醒：{text}（{wait}后）")
        self.show_bubble(f"好，{wait}后提醒你：{text}")
        self._reply_parts.append(f"（已设提醒：{text}，{wait}后）")
        self._finish_action()

    def _do_todo_add(self, action: dict):
        """todo_add 动作：加一条待办"""
        text = (action.get("text") or "").strip()
        if not text:
            self.set_emotion("sweat")
            self.show_bubble("要加什么待办呢？")
            self._finish_action()
            return
        self.tasks.add_todo(text)
        self.tasks.log_work("todo", f"记了待办：{text}")
        n = len(self.tasks.open_todos())
        self.set_emotion("happy")
        self.show_bubble(f"已记入待办（共 {n} 件未完成）：{text}")
        self._reply_parts.append(f"（待办+1：{text}）")
        self._finish_action()

    def _do_todo_done(self, action: dict):
        """todo_done 动作：按序号（1 起）划掉一条待办"""
        index = action.get("index") or 0
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = 0
        item = self.tasks.complete_todo(index)
        if item:
            self.tasks.log_work("todo", f"办完了待办：{item['text']}")
            n = len(self.tasks.open_todos())
            self.set_emotion("happy")
            self.show_bubble(f"办完一件：「{item['text']}」，"
                             f"还剩 {n} 件待办。")
        else:
            left = self.tasks.open_todos()
            self.set_emotion("sweat")
            if left:
                self.show_bubble("没找到这条待办。现在手头的待办是：")
                self.show_card("📋 待办清单",
                               "\n".join(f"{i}. {t['text']}"
                                         for i, t in enumerate(left, 1)),
                               f"共 {len(left)} 件未完成")
            else:
                self.show_bubble("待办清单本来就是空的。")
        self._finish_action()

    def _do_todo_list(self):
        """todo_list 动作：展示待办卡片"""
        left = self.tasks.open_todos()
        if not left:
            self.show_bubble("待办清单是空的，很清净。")
        else:
            self.show_card("📋 待办清单",
                           "\n".join(f"{i}. {t['text']}"
                                     for i, t in enumerate(left, 1)),
                           f"共 {len(left)} 件未完成")
        self._finish_action()

    # ============================================================
    # 检索资料（web_search / read_doc / analyze_data 共用）：成果入缓冲，注入后续轮次
    # ============================================================
    def _notes_text(self) -> str:
        """把资料缓冲拼成注入提示词的文本块"""
        return "\n\n".join(self._reference_notes)

    def _add_note(self, text: str):
        """追加一条资料（超量丢最老、超长截断）"""
        if len(text) > config.REFERENCE_NOTE_MAX:
            text = text[:config.REFERENCE_NOTE_MAX - 1] + "…"
        self._reference_notes.append(text)
        if len(self._reference_notes) > config.REFERENCE_NOTES_MAX:
            self._reference_notes = \
                self._reference_notes[-config.REFERENCE_NOTES_MAX:]

    def _do_web_search(self, action: dict):
        """web_search 动作：后台线程联网搜索，结果存资料缓冲并播报"""
        query = (action.get("query") or "").strip()
        count = action.get("count")
        if not query:
            self.set_emotion("sweat")
            self.show_bubble("要搜什么词，再说一遍？")
            self._finish_action()
            return
        if not config.WEBSEARCH_API_KEY:
            self.set_emotion("sweat")
            self.show_bubble("我还没法联网查资料——需要搜索服务的 Key。"
                             "智谱开放平台（open.bigmodel.cn）的 Key 就能用，"
                             "填到设置的「搜索Key」即可。")
            self._note_plan_failure("搜索 Key 未配置")
            self._finish_action()
            return
        self.set_pose("searching")
        self.show_bubble(f"这就去联网查「{query}」，请稍候……")
        self._search_worker = _SearchWorker(query, count)
        self._search_worker.done.connect(self._on_search_done)
        self._search_worker.start()

    def _on_search_done(self, ok: bool, query: str, payload):
        """搜索完成：先过质量门（软失败自我纠正），结果入资料缓冲"""
        self._search_worker = None
        self.set_pose("idle")
        if ok and payload:
            from framework.quality_gate import assess_search
            gate = assess_search(query, payload)
            if not gate["ok"]:
                self._handle_search_quality(query, gate)
                return
            self._add_note(f"【联网搜索 {time.strftime('%H:%M')}】"
                           + format_search_notes(query, payload))
            self.tasks.log_work("search", f"联网搜索了「{query}」"
                                f"（{len(payload)} 条资料）")
            titles = "、".join(r["title"] for r in payload[:3] if r["title"])
            self.set_emotion("star")
            self.show_bubble(f"查到了 {len(payload)} 条资料：{titles}。"
                             if titles else f"查到了 {len(payload)} 条资料。"
                             )
            if self._plan is None or self._plan.get("status") != "running":
                # 单发搜索：资料到位后主动开一轮回答（计划模式交给下一轮注入）
                self._busy = True
                QTimer.singleShot(500, self._search_followup)
        else:
            self.set_emotion("sweat")
            self.show_bubble(f"没查到「{query}」的资料"
                             f"（{payload if not ok else '结果为空'}），"
                             "稍后再试或换个说法。")
            self._note_plan_failure(f"没查到「{query}」的资料")
        self._finish_action()

    def _handle_search_quality(self, query: str, gate: dict):
        """搜索软失败自我纠正：计划内交重规划；单发先换词重试一次，
        仍不达标则带【质量预警】降级使用（让模型谨慎引用并说明局限）"""
        if not gate.get("ok", True):
            self._traj_event(
                "quality", action="web_search", source="system", ok=False,
                payload={"query": str(query)[:80],
                         "reason": str(gate.get("reason", ""))},
                note=f"检索质量不达标：{gate.get('reason', '')}")
        plan_running = (self._plan is not None
                        and self._plan.get("status") == "running")
        if plan_running:
            self.set_emotion("sweat")
            self.show_bubble(f"搜回来的资料和「{query}」对不太上"
                             f"（{gate['reason']}），我换个思路重新查。")
            self._note_plan_failure(f"检索质量不达标（{gate['reason']}）")
            self._finish_action()
            return
        if not self._search_quality_retried:
            self._search_quality_retried = True
            from framework.quality_gate import extract_keywords
            retry = " ".join(extract_keywords(query)[:6]).strip()
            if retry and retry != query.strip():
                self.set_emotion("sweat")
                self.show_bubble(f"搜到的资料对不上题（{gate['reason']}），"
                                 f"换个关键词「{retry}」再查一次。")
                self._do_web_search({"action": "web_search", "query": retry})
                self._arm_chain_watchdog("web_search")   # 重试重新计时
                return        # 不收尾：重试自己的 _on_search_done 负责收尾
        # 降级：资料照收但带质量预警，模型会谨慎引用并说明局限
        self._add_note(f"【质量预警】刚才搜「{query}」的资料相关性差"
                       f"（{gate['reason']}），回答时请谨慎引用，"
                       "并如实说明资料可能不对题。")
        self.set_emotion("sweat")
        self.show_bubble(f"「{query}」的资料不太对题（{gate['reason']}），"
                         "我先按现有资料回答，仅供参考。")
        self._busy = True
        QTimer.singleShot(500, self._search_followup)
        self._finish_action()

    def _search_followup(self):
        """单发搜索/读文档/数据分析后的跟进轮：让 AI 基于【检索资料】
        回答用户最初的问题"""
        if self._busy and (self._action_queue
                           or (self._ai_worker
                               and self._ai_worker.isRunning())):
            return          # 用户已插话，交给新的流程
        self._busy = True
        self._current_user_text = self._current_user_text or ""
        self._suppress_fallback = True      # 别把资料回答再兜底成工具调用
        self._start_ai_worker(
            (f"{self._current_user_text}\n\n（系统提示：检索已完成，"
             f"资料已注入上下文，请基于【检索资料】直接回答上面的问题。）"),
            facts=self.memory.get_facts_text(), notes=self._notes_text())

    def _do_read_doc(self, action: dict):
        """read_doc 动作：读取本机文档文字内容，存入资料缓冲并跟进回答"""
        from framework.doc_editor import extract_content
        from framework.path_guard import validate_read_path

        # path 来自模型输出，读到的数据会随下一轮发给第三方 LLM。
        # 先过白名单：允许的目录、后缀、大小、敏感文件名，默认拒绝。
        ok, path, reason = validate_read_path(action.get("path"))
        if not ok:
            self.set_emotion("sweat")
            if reason == "文件不存在":
                self.show_bubble("这个文件不存在，确认一下完整路径？")
            else:
                self.show_bubble(f"这个文件我不便读取：{reason}。")
            self._note_plan_failure(f"文件路径被拒绝：{reason}")
            self._finish_action()
            return
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext in (".docx", ".pptx"):
                kind, spec = extract_content(path)
                if kind == "ppt":
                    body = "\n".join(
                        f"『{s['title']}』" + "；".join(s["bullets"])
                        for s in spec.get("slides", []) if s.get("title"))
                else:
                    body = "\n".join(
                        f"『{s['heading']}』" + "；".join(s["paragraphs"])
                        for s in spec.get("sections", []) if s.get("heading"))
            elif ext in (".txt", ".md", ".csv", ".json", ".log", ".py"):
                with open(path, encoding="utf-8", errors="replace") as f:
                    body = f.read()
            elif ext == ".xlsx":
                from openpyxl import load_workbook
                wb = load_workbook(path, read_only=True, data_only=True)
                rows = []
                for ws in wb.worksheets:
                    rows.append(f"『{ws.title}』")
                    for row in list(ws.iter_rows(values_only=True))[:100]:
                        vals = [str(v) for v in row if v is not None]
                        if vals:
                            rows.append(" | ".join(vals))
                body = "\n".join(rows)
            else:
                self.set_emotion("sweat")
                self.show_bubble("这种格式我还读不了，目前支持 txt/md/csv/"
                                 "json/docx/pptx/xlsx。")
                self._note_plan_failure(f"文档格式 {ext or '未知'} 不支持")
                self._finish_action()
                return
        except Exception as e:                  # noqa: BLE001 损坏/加密不崩助手
            self.set_emotion("sweat")
            self.show_bubble(f"文件读不出来（{type(e).__name__}），"
                             "确认它没有被占用或加密。")
            self._note_plan_failure(f"文件读取失败（{type(e).__name__}）")
            self._finish_action()
            return
        body = body.strip()[:config.READ_DOC_TEXT_MAX]
        if not body:
            self.set_emotion("sweat")
            self.show_bubble("文件里没读到文字内容。")
            self._note_plan_failure("文件里没有文字内容")
            self._finish_action()
            return
        name = os.path.basename(path)
        self._add_note(f"【本机文档 {name}】\n{body}")
        self.tasks.log_work("read_doc", f"阅读了《{name}》")
        self.set_emotion("star")
        self.show_bubble(f"读完了《{name}》（约 {len(body)} 字），"
                         "资料已记下，我这就给你讲讲。")
        self._busy = True
        QTimer.singleShot(500, self._search_followup)
        self._finish_action()

    def _do_analyze_data(self, action: dict):
        """analyze_data 动作：pandas 分析本机表格，结论进资料缓冲、图表走交付卡片"""
        from framework import data_tool
        from framework.path_guard import validate_read_path
        from framework.utils import human_size

        ok, path, reason = validate_read_path(action.get("path"))
        if not ok:
            self.set_emotion("sweat")
            if reason == "文件不存在":
                self.show_bubble("这个表格文件不存在，确认一下完整路径？")
            else:
                self.show_bubble(f"这份表格我不便读取：{reason}。")
            self._note_plan_failure(f"文件路径被拒绝：{reason}")
            self._finish_action()
            return
        try:
            res = data_tool.analyze(
                path, group_by=action.get("group_by", ""),
                x_col=action.get("x_col", ""), y_col=action.get("y_col", ""),
                chart=action.get("chart", ""))
        except Exception as e:                  # noqa: BLE001 坏文件不崩助手
            self.set_emotion("sweat")
            self.show_bubble(f"这份表格我算不出来：{e}")
            self._note_plan_failure(f"数据分析失败：{e}")
            self._finish_action()
            return

        name = os.path.basename(path)
        self._add_note(f"【数据分析 · {name}】\n{res['summary']}")
        self.tasks.log_work("analyze_data",
                            f"分析了《{name}》（{res['rows']} 行"
                            f"×{res['cols']} 列）")
        png = res.get("chart_path") or ""
        if png:
            self.tasks.add_delivery(os.path.basename(png), png, "png")
            try:
                size_text = human_size(os.path.getsize(png))
            except OSError:
                size_text = ""
            self.show_card("📊 分析图表已生成", png, size_text)
        self.set_emotion("star")
        self.show_bubble(f"算完了《{name}》（{res['rows']} 行 × "
                         f"{res['cols']} 列），"
                         + ("图也画好了，" if png else "") + "我讲讲结论。")
        self._busy = True
        QTimer.singleShot(500, self._search_followup)
        self._finish_action()

    def _do_work_log(self):
        """work_log 动作：播报今天替用户做过的事"""
        from framework.task_board import format_duration as _fmt
        items = self.tasks.today_worklog()
        if not items:
            self.show_bubble("今天还没开工，吩咐吧，做完都有据可查。")
        else:
            lines = []
            for w in items[-8:]:
                ts = time.localtime(w["ts"])
                lines.append(f"{ts.tm_hour:02d}:{ts.tm_min:02d} {w['text']}")
            self.set_emotion("star")
            self.show_card("📔 今天的工作日志",
                           "\n".join(lines), f"今日共 {len(items)} 件")
            self.tasks.log_work("report", "向用户汇报了今日工作")
        self._finish_action()

    # ============================================================
    # 工作流模板（一键多步编排：模板命令作为新任务走完整计划流）
    # ============================================================
    def _resolve_workflow(self, name: str):
        """按名称找工作流模板（全等 -> 互相包含），找不到返回 None"""
        name = (name or "").strip()
        if not name:
            return None
        for t in config.WORKFLOW_TEMPLATES:
            if t["name"] == name:
                return t
        for t in config.WORKFLOW_TEMPLATES:
            if name in t["name"] or t["name"] in name:
                return t
        return None

    def _do_run_workflow(self, action: dict):
        """run_workflow 动作：登记模板命令，本轮收尾后自动开新任务执行。

        不在本动作里直接 send_command（此刻 _busy=True 会被拒收），
        而是记到 _workflow_start，等动作队列清空后由收尾逻辑启动。
        """
        name = (action.get("name") or "").strip()
        tpl = self._resolve_workflow(name)
        if tpl is None:
            names = "、".join(t["name"] for t in config.WORKFLOW_TEMPLATES)
            self.set_emotion("sweat")
            self.show_bubble(f"没有叫「{name}」的工作流。现成的模板有：{names}。")
            self._finish_action()
            return
        self._workflow_start = tpl["command"]
        self.set_emotion("exclaim")
        self.show_bubble(f"好，工作流「{tpl['name']}」这就启动！")
        self._reply_parts.append(f"（启动工作流：{tpl['name']}）")
        self._finish_action()

    def _check_reminders(self):
        """轮询到期提醒：到点即删 + 播报 + 连跳引起注意
        （重启后错过的提醒会在这里补播一次）"""
        try:
            due = self.tasks.pop_due_reminders()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Framework] 提醒轮询失败: {e}")
            return
        for r in due:
            self.set_emotion("exclaim")
            self.show_bubble(f"⏰ 时间到！该去{r['text']}了！")
            self._alarm_jump_burst()
            self.memory.add("assistant", f"[系统] 提醒触发：{r['text']}")
            logger.info(f"[Framework] 提醒触发: {r['text']}")

    def _alarm_jump_burst(self, times: int = None):
        """提醒到点：连跳一串吸引注意（跳完回 idle，不打断后续指令）"""
        if times is None:
            times = config.REMINDER_JUMP_TIMES

        def _next(remaining):
            if remaining <= 0:
                self.set_pose("idle")
                return
            self.set_pose("jump")
            self.pet.jump(on_done=lambda: QTimer.singleShot(
                120, lambda: _next(remaining - 1)))

        _next(times)

    # ============================================================
    # 主动互动
    # ============================================================
    def proactive_chat(self):
        """两分钟无操作时主动搭话（UI 层的空闲计时器调用）"""
        if self._busy:
            return
        import random
        lines = [
            "发呆许久了，可有事吩咐？",
            "找应用、开程序，交给我便是。",
            "我还在此值守。无事的话，聊两句也行。",
        ]
        self.show_bubble(random.choice(lines))

    def shutdown(self):
        """退出前清理（任何一步失败都不阻断退出）。

        以前只收尾 pet / AI 线程 / 视觉线程，看门狗、文档线程、搜索线程、
        计划卡与所有信号连接都留着；这里统一遍历一遍，避免退出后残留
        QThread 对象或定时器回调打到已销毁的窗口上。
        """
        try:
            self.pet.stop()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Framework] 停止宠物控制器失败: {e}")

        for name in ("_reminder_timer", "_chain_watchdog", "_vision_watchdog"):
            try:
                timer = getattr(self, name, None)
                if timer is not None:
                    timer.stop()
            except RuntimeError as e:      # C++ 对象可能已销毁
                logger.warning(f"[Framework] 停止 {name} 失败: {e}")
        self._vision_watchdog = None

        # 所有后台线程：断信号 → 请求停止 → 限时等待 → 标记可回收
        for name in ("_ai_worker", "_doc_worker", "_search_worker",
                     "_vision_worker", "_chain_watchdog"):
            try:
                worker = getattr(self, name, None)
                if worker is None:
                    continue
                for sig_name in ("finished", "failed", "done", "timeout"):
                    sig = getattr(worker, sig_name, None)
                    if sig is not None:
                        try:
                            sig.disconnect()
                        except (TypeError, RuntimeError):
                            pass
                if hasattr(worker, "stop"):
                    worker.stop()
                if hasattr(worker, "isRunning") and worker.isRunning():
                    worker.wait(1500)
                if hasattr(worker, "deleteLater"):
                    worker.deleteLater()
                setattr(self, name, None)
            except RuntimeError as e:      # 线程 C++ 对象可能已销毁
                logger.warning(f"[Framework] 收尾 {name} 失败: {e}")
                setattr(self, name, None)

        try:
            self._zombie_workers.clear()
        except AttributeError:
            pass
        try:
            if self.plan_card is not None:
                self.plan_card.close()
                self.plan_card = None
        except RuntimeError as e:
            logger.warning(f"[Framework] 关闭计划卡失败: {e}")
        try:
            self.memory.save()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Framework] 保存记忆失败: {e}")
        logger.info("[Framework] 已关闭")
