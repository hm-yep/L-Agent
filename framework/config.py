# -*- coding: utf-8 -*-
"""全局配置：白名单 + 锚点 + 开关 + 性格参数"""

import json
import os
import shutil

# ============================================================
# 开关（开发模式用，队友开发时使用模拟数据，不依赖真实API和真实系统调用）
# ============================================================
USE_REAL_AI = False          # True: 调用 DeepSeek API / False: 使用模拟AI
USE_REAL_SYSTEM = True       # True: 真实查找/启动应用 / False: 使用模拟数据
                             # （False 时启动应用只是"假装成功"，气泡说搞定但不会真打开）

# 注意：用户在设置界面填写有效的 API Key 后，程序会自动切换为 USE_REAL_AI=True

# DeepSeek API 配置（用户可在设置界面修改并持久化）
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"
AI_TIMEOUT = 90              # AI 请求超时（秒；长回答/慢网关需要充裕时间）
AI_MAX_RETRIES = 2           # 请求失败自动重试次数（网络抖动/限流/截断）
AI_MAX_TOKENS = 1024         # 单次回复最大 token（被截断时会自动放大重试）

# 可选择的模型种类（设置界面下拉框 + 可手动输入）
DEEPSEEK_MODELS = [
    "deepseek-chat",          # 通用对话（V3）
    "deepseek-reasoner",      # 深度推理（R1，适合数学/难题）
    "deepseek-coder",         # 代码专用
]
USER_SETTINGS_FILE = "user_settings.json"

# ============================================================
# 安全白名单：只允许打开白名单内的应用
# 键为识别名（支持模糊匹配），值为 exe 名称列表
# ============================================================
# 是否放行白名单之外的任意程序。
# 默认关闭（安全默认）：open_app 只允许 APP_WHITELIST 命中的程序。
# 确实需要"搜到什么就开什么"时，显式设置环境变量 LINGLING_ALLOW_ALL_APPS=1，
# 或在设置界面开启；白名单表同时保留，作为"应用名 -> exe 文件名"的搜索提示。
ALLOW_ALL_APPS = os.environ.get("LINGLING_ALLOW_ALL_APPS", "0").strip() \
    in ("1", "true", "yes", "on")

APP_WHITELIST = {
    "微信":     ["wechat.exe", "weixin.exe"],
    "weixin":   ["wechat.exe", "weixin.exe"],
    "wechat":   ["wechat.exe", "weixin.exe"],
    "qq":       ["qq.exe"],
    "扣扣":     ["qq.exe"],
    "记事本":   ["notepad.exe"],
    "notepad":  ["notepad.exe"],
    "计算器":   ["calc.exe", "calculator.exe"],
    "calculator": ["calc.exe", "calculator.exe"],
    "画图":     ["mspaint.exe"],
    "paint":    ["mspaint.exe"],
    "浏览器":   ["chrome.exe", "msedge.exe", "firefox.exe"],
    "browser":  ["chrome.exe", "msedge.exe", "firefox.exe"],
    "chrome":   ["chrome.exe"],
    "edge":     ["msedge.exe"],
    "vscode":   ["code.exe"],
    "vs code":  ["code.exe"],
    "代码":     ["code.exe"],
    "typora":   ["typora.exe"],
    "word":     ["winword.exe"],
    "excel":    ["excel.exe"],
    "powerpoint": ["powerpnt.exe"],
    "ppt":      ["powerpnt.exe"],
    "cmd":      ["cmd.exe"],
    "命令行":   ["cmd.exe"],
    "任务管理器": ["taskmgr.exe"],
    "资源管理器": ["explorer.exe"],
    "文件资源管理器": ["explorer.exe"],
    "steam":    ["steam.exe"],
    "网易云":   ["cloudmusic.exe"],
    "网易云音乐": ["cloudmusic.exe"],
    # ---- 09-11 扩充：优先按本机实际安装的程序核对过 exe 名 ----
    "豆包":     ["doubao.exe"],
    "doubao":   ["doubao.exe"],
    "哔哩哔哩": ["哔哩哔哩.exe", "bilibili.exe"],
    "bilibili": ["哔哩哔哩.exe", "bilibili.exe"],
    "b站":      ["哔哩哔哩.exe", "bilibili.exe"],
    "抖音":     ["douyin.exe"],
    "douyin":   ["douyin.exe"],
    "迅雷":     ["thunder.exe"],
    "thunder":  ["thunder.exe"],
    "剪映":     ["jianyingpro.exe"],
    "飞书":     ["feishu.exe", "lark.exe"],
    "钉钉":     ["dingtalk.exe"],
    "腾讯会议": ["wemeetapp.exe"],
    "qq音乐":   ["qqmusic.exe"],
}

# 内置应用：无需查找磁盘路径，直接通过 shell 启动
BUILTIN_APPS = {
    "calc.exe": "calc",            # 计算器
    "notepad.exe": "notepad",      # 记事本
    "mspaint.exe": "mspaint",      # 画图
    "cmd.exe": "cmd",              # 命令行
    "taskmgr.exe": "taskmgr",      # 任务管理器
    "explorer.exe": "explorer",    # 资源管理器
    "control.exe": "control",      # 控制面板
}

# ============================================================
# 本地搜索范围
# ============================================================
SEARCH_DIRS = [
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData\Microsoft\Windows\Start Menu",
]
# 部分应用（如豆包）装在 %LOCALAPPDATA%，也纳入扫描
_local_appdata = os.environ.get("LOCALAPPDATA")
if _local_appdata:
    SEARCH_DIRS.append(_local_appdata)

def user_desktop() -> str:
    """当前用户桌面路径"""
    return os.path.join(os.path.expanduser("~"), "Desktop")


def all_desktop_dirs() -> list:
    """所有桌面目录（当前用户 + 公用桌面，支持重定向）。

    Steam 等游戏平台装给所有用户时，快捷方式落在公用桌面
    C:\\Users\\Public\\Desktop；桌面被 OneDrive 重定向时真实路径
    以注册表 Shell Folders 为准。
    """
    dirs = []
    try:
        import winreg
        for hive, sub_key, value_name in (
            (winreg.HKEY_CURRENT_USER,
             r"Software\Microsoft\Windows\CurrentVersion\Explorer"
             r"\Shell Folders", "Desktop"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"Software\Microsoft\Windows\CurrentVersion\Explorer"
             r"\Shell Folders", "Common Desktop"),
        ):
            with winreg.OpenKey(hive, sub_key) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
                if value and os.path.isdir(value) and value not in dirs:
                    dirs.append(value)
    except OSError:
        pass
    for d in (user_desktop(), r"C:\Users\Public\Desktop"):
        if os.path.isdir(d) and d not in dirs:
            dirs.append(d)
    return dirs

# 注册表卸载信息位置（用于查找已安装应用）
REGISTRY_KEYS = [
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
]

# ============================================================
# 屏幕锚点（移动路线用）
# ============================================================
ANCHOR_MARGIN = 60           # 距屏幕边缘的边距

# ============================================================
# 性格系统
# ============================================================
PERSONALITIES = {
    "活泼": {
        "move_speed": 1.6,        # 移动速度倍率
        "action_rate": 1.5,       # 动作频繁度倍率
        "chat_style": "你性情豪爽，像冲锋在前的年轻骑士，回复干脆有力、行动果断，回复控制在40字以内。",
    },
    "沉稳": {
        "move_speed": 0.7,
        "action_rate": 0.7,
        "chat_style": "你像久经沙场的老骑士，话少而精炼，言出必行，回复简洁平静，一般不超过25字。",
    },
    "毒舌": {
        "move_speed": 1.0,
        "action_rate": 1.0,
        "chat_style": "你像爱吐槽的骑士副官，嘴上不饶人但关键时刻必定出手相助，回复带幽默和调侃，回复控制在45字以内。",
    },
}
DEFAULT_PERSONALITY = "活泼"

# ============================================================
# 主动互动
# ============================================================
IDLE_TIMEOUT_MS = 2 * 60 * 1000   # 两分钟无操作触发主动搭话
PROACTIVE_ENABLED = True          # 主动互动开关（设置中可改）
AUTOSTART_ENABLED = False         # 开机自启（设置中可改）

# ============================================================
# 电源操作（关机 / 重启 / 锁屏）
# ============================================================
ALLOW_POWER_ACTIONS = True     # 总开关：False 时拒绝关机/重启/锁屏动作
POWER_ACTION_DELAY = 60        # 关机/重启默认延时（秒），期间可取消
POWER_REPLY_MS = 3000          # 关机前确认气泡停留时长（毫秒）

# ============================================================
# 文档生成（AI 调用本机工具制作 PPT / Word / Excel）
# ============================================================
ALLOW_DOC_GENERATION = True    # 总开关：False 时拒绝 create_doc 动作（设置中可改）
DOC_MAX_SLIDES = 30            # 单份 PPT 最大页数（防模型输出失控）
DOC_MAX_ROWS = 500             # 单个表格最大数据行数
DOC_MAX_TEXT = 2000            # 单个段落/要点/单元格最大字符数（超出截断）

# 联网配图（Pexels 免费图库，Key 可在设置中填写；不填则退回纯排版）
PEXELS_API_KEY = ""            # 用户的 Pexels API Key（持久化到用户设置）
PEXELS_API_URL = "https://api.pexels.com/v1/search"
DOC_IMAGE_TIMEOUT = 10         # 搜图/下载单次超时（秒）
DOC_IMAGE_MAX = 6              # 单份文档最多配图张数（控制等待时间）

# 文件修改（用户把 pptx/docx 发给凌零，AI 改写后返回新文件）
DOC_EDIT_CANCEL_WORDS = ("取消修改", "不用改了", "不改了", "算了")

# ============================================================
# 屏幕视觉理解（多模态）：凌零截图后交给视觉大模型"看"屏幕再回答
# 视觉接口走 OpenAI 兼容的图片消息格式，默认用智谱免费的 glm-4.6v-flash。
# Key 不填则该功能不可用（其余功能不受影响）。
# 设置里清空地址/模型 = 回到下面的出厂默认（DEFAULT 常量兜底）。
# ============================================================
VISION_URL_DEFAULT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
VISION_MODEL_DEFAULT = "glm-4.6v-flash"   # 智谱免费视觉推理模型
VISION_KEY_DEFAULT = ""                   # 视觉模型 Key 需用户自填
VISION_API_URL = VISION_URL_DEFAULT
VISION_MODEL = VISION_MODEL_DEFAULT
VISION_API_KEY = VISION_KEY_DEFAULT
VISION_TIMEOUT = 60               # 视觉请求超时（秒）
VISION_MAX_TOKENS = 800           # 屏幕描述长度上限

# ============================================================
# 联网检索（智能体调研能力）：搜索结果作为"检索资料"注入后续对话，
# 支撑"调研 -> 出报告/PPT"的端到端任务。默认智谱 web-search-pro
# （open.bigmodel.cn，与视觉模型同一把 Key 也能用）。
# Key 不填则搜索功能不可用（其余功能不受影响）。
# ============================================================
WEBSEARCH_URL_DEFAULT = "https://open.bigmodel.cn/api/paas/v4/tools"
WEBSEARCH_KEY_DEFAULT = ""                # 搜索 Key 需用户自填
WEBSEARCH_API_URL = WEBSEARCH_URL_DEFAULT
WEBSEARCH_API_KEY = WEBSEARCH_KEY_DEFAULT
WEBSEARCH_TIMEOUT = 30            # 搜索请求超时（秒）
WEBSEARCH_MAX_RESULTS = 6         # 单次搜索最多采纳的结果条数
WEBSEARCH_SNIPPET_MAX = 400       # 每条摘要注入上下文的长度上限

# 阅读本地文档（read_doc 动作）注入上下文的字符上限
READ_DOC_TEXT_MAX = 6000
# "检索资料"缓冲：最多保留条数 / 单条长度（超出丢最老、截断）
REFERENCE_NOTES_MAX = 6
REFERENCE_NOTE_MAX = 800

# 数据分析（analyze_data 动作）：单次读表行数上限 / 统计摘要长度上限
# 上限刻意小于 REFERENCE_NOTE_MAX，留出「【数据分析 · 文件名】」前缀，
# 保证摘要整段进缓冲不被截断
DATA_MAX_ROWS = 200000
DATA_SUMMARY_MAX = 740

# ============================================================
# 任务台：定时提醒 + 待办清单（JSON 持久化，重启不丢）
# ============================================================
TASKS_FILE = "tasks.json"
REMINDER_DEFAULT_MINUTES = 30     # 用户没说多久时默认提醒间隔（分钟）
REMINDER_MIN_SEC = 5              # 提醒最短间隔（秒，"20秒后"这类短提醒可用）
REMINDER_MAX_SEC = 604800         # 提醒最远时限（7 天，秒）
REMINDER_POLL_SEC = 5             # 到期提醒轮询间隔（秒，短提醒需要秒级响应）
REMINDER_JUMP_TIMES = 6           # 提醒到点时助手连跳次数（引起注意）
REMINDER_TEXT_MAX = 200           # 提醒/待办内容长度上限（超出截断）
MAX_TODOS = 100                   # 待办条数上限
MAX_DELIVERIES = 100              # 交付记录条数上限（超出丢最老）
MAX_WORKLOG = 300                 # 工作日志条数上限（超出丢最老）

# 任务计划确认流（show_plan 出计划 -> 用户确认 -> 逐步执行）
PLAN_MAX_ROUNDS = 15              # 确认后自动推进的最大轮数（安全阀，防死循环）
PLAN_MAX_REPLANS = 2              # 步骤受阻时自动重规划的次数上限（防循环重试）
PLAN_CANCEL_WORDS = ("取消计划", "放弃计划", "不要计划", "停止计划",
                     "计划取消", "别做了", "先不做了")
PLAN_CONFIRM_WORDS = ("确认", "开始执行", "执行吧", "开始吧", "没问题",
                      "可以开始", "按计划", "同意", "就这样", "就这么办",
                      "计划可以", "可以了")

# ============================================================
# 后悔药：一轮任务的效果记账与逐项回退（纯确定性，撤销过程不调模型）
# ============================================================
UNDO_ENABLED = True             # 总开关：False 时不记账，undo_last 如实说没记
UNDO_DELETE_FILES = True        # 允许回退时删掉本轮自己生成的文档/图表
UNDO_DELETE_MAX_BYTES = 50 * 1024 * 1024   # 只删不超过这个大小的本轮新文件
UNDO_FILE_EXTS = (".pptx", ".docx", ".xlsx", ".png", ".jpg", ".jpeg", ".html")
UNDO_MAX_ITEMS = 20             # 一次最多回退几项（超出交还用户手工处理）
# 短指令直连：这几句话不经过模型当场执行（API 欠费时照样能撤销、能存技能）
SHORT_CMD_MAX_LEN = 14          # 超过这个长度就不算"一句指令"，交回模型当任务
UNDO_WORDS = ("撤销", "撤回", "恢复原样", "undo")
SKILL_SAVE_WORDS = ("存成技能", "记成技能", "学会这个", "记住这套",
                    "以后就这么做", "存下来")
SKILL_LIST_WORDS = ("技能列表", "你会什么技能", "有哪些技能", "学过什么")

# ============================================================
# 技能层：把做成过的动作序列存成可重放技能（一次学会，下次零规划）
# ============================================================
SKILLS_FILE = "skills.json"
MAX_SKILLS = 30                 # 技能条数上限（超出丢最老的）
SKILL_MIN_STEPS = 2             # 少于此步数不提议存技能（一步的事不值得记）
SKILL_MAX_STEPS = 12            # 单个技能最多几步（超出说明这一轮太杂，不是技能）
SKILL_NAME_MAX = 24             # 技能名长度上限
SKILL_OFFER_COOLDOWN_SEC = 180  # 两次「要存成技能吗」的最小间隔（不烦人）
SKILL_SLOT_MAX = 4              # 单个技能最多几个槽位（超出说明抽象得不好）
# 一句话直放的长度门限：比短指令宽，因为技能名可能就是当初那句长任务。
# 再宽就危险了——长句子是该让模型读的任务，不是能逐字对上号的命令。
SKILL_REPLAY_MAX_LEN = 40

# ============================================================
# 工作流模板：一键触发的多步任务编排（智能体"工作流编排"能力）。
# run_workflow 动作 / 右键菜单都能启动；command 会作为一条新任务
# 走完整链路：AI 拟计划 -> 计划卡确认 -> 调工具逐步执行 -> 交付。
# ============================================================
WORKFLOW_TEMPLATES = [
    {
        "name": "每日科技早报",
        "desc": "联网搜集今日科技要闻，生成一份网页早报",
        "command": ("执行工作流「每日科技早报」：先联网搜索今天的科技新闻"
                    "（行业动态、新品发布、行业政策各搜一次），"
                    "再基于检索资料生成一份 html 网页早报，"
                    "分「要闻速览」「详情解读」两节。"),
    },
    {
        "name": "市场调研简报",
        "desc": "联网调研主题现状，产出带数据表的网页简报",
        "command": ("执行工作流「市场调研简报」：先联网搜索该主题的市场规模、"
                    "主要玩家与最新动态，再基于检索资料生成一份 html 调研简报，"
                    "关键数据用表格呈现。"),
    },
    {
        "name": "周报整理",
        "desc": "根据今日工作日志整理一份 Word 周报",
        "command": ("执行工作流「周报整理」：先播报今天的工作日志，"
                    "再据此生成一份 Word 周报，分「今日进展」「成果与数据」"
                    "「待跟进事项」三节。"),
    },
    {
        "name": "会议准备清单",
        "desc": "搜集会议主题背景资料，列出会前待办",
        "command": ("执行工作流「会议准备清单」：联网搜索会议主题的背景资料，"
                    "整理成一份会议要点卡片，再把需要提前准备的事项"
                    "逐条加进待办清单。"),
    },
]

# ============================================================
# 记忆系统
# ============================================================
MEMORY_MAX_TURNS = 20             # 保存最近20轮对话
MEMORY_FILE = "memory.json"
MAX_FACTS = 50                    # 长期记忆条数上限（超出丢最老）
FACT_TEXT_MAX = 200               # 单条长期记忆长度上限（超出截断）

# ============================================================
# 音效（语音包：400 Sounds Pack / assets/sounds）
# ============================================================
SOUND_ENABLED = True          # 音效总开关（设置中可改）
SOUND_VOLUME = 0.6            # 音量 0.0 ~ 1.0

# ============================================================
# API 服务安全（环境变量配置；默认值是"只服务本机"的最小暴露面）
# ============================================================
# 设置后所有请求必须带 X-API-Key: <token>（或 Authorization: Bearer <token>）。
# 未设置时服务只接受回环地址（127.0.0.1 / ::1）的请求，避免 0.0.0.0 暴露
# 时把免鉴权的 /chat、/agent/run 直接放到局域网上。
API_AUTH_TOKEN = os.environ.get("LINGLING_API_TOKEN", "")
API_ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get(
        "LINGLING_API_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000").split(",")
    if o.strip()
]
API_MAX_MESSAGE_CHARS = 4000        # 单条对话/任务文本长度上限
API_MAX_TASK_CHARS = 2000           # /agent/run 任务描述长度上限
API_MAX_UPLOAD_BYTES = 20 * 1024 * 1024   # 上传文件 20 MB 上限
API_SESSION_TTL_SEC = 3600          # 会话空闲过期时间
API_MAX_SESSIONS = 512              # 会话表上限（防伪造 sid 无限增长）

# ============================================================
# 用户设置持久化（%APPDATA%\DesktopPet\user_settings.json）
# 设置界面填写的 API Key / 接口地址 / 模型 / 性格 / 音效 都会存到这里。
# 固定放用户目录（不放 exe 旁边）：exe 被挪到别的文件夹、直接在压缩包里
# 双击运行时位置都会变，只有 %APPDATA% 每台电脑都稳定，key 才不会丢。
# ============================================================
SETTINGS_APP_DIR = "DesktopPet"   # 沿用旧目录名：老用户的数据/Key 直接继承


def _user_settings_path() -> str:
    base = os.path.join(
        os.environ.get("APPDATA") or os.path.expanduser("~"),
        SETTINGS_APP_DIR)
    try:
        os.makedirs(base, exist_ok=True)   # 全新用户也要能落盘
    except OSError:
        pass
    path = os.path.join(base, USER_SETTINGS_FILE)
    if not os.path.exists(path):
        # 兼容迁移：老版本把设置存在程序目录 data/ 下，首次运行时搬过来
        from framework.utils import app_data_dir
        legacy = os.path.join(app_data_dir(), USER_SETTINGS_FILE)
        if os.path.abspath(legacy) != os.path.abspath(path) \
                and os.path.exists(legacy):
            try:
                os.makedirs(base, exist_ok=True)
                shutil.copyfile(legacy, path)
            except OSError:
                pass
    return path


def save_user_settings(**kwargs) -> None:
    """增量保存用户设置到 JSON（原子写 + 收紧权限）。

    这个文件里含用户填写的 API Key，因此：
      * 先写临时文件再 os.replace，避免半写导致设置损坏；
      * POSIX 下 chmod 600，仅属主可读写；
      * Windows 的 ACL 不能靠 chmod 保证，需要更强隔离时请改用
        环境变量（DEEPSEEK_API_KEY 等），不要落盘。
    """
    data = load_user_settings()
    data.update(kwargs)
    path = _user_settings_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as e:
        from framework.utils import logger
        logger.warning(f"[Config] 用户设置保存失败: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass


def load_user_settings() -> dict:
    """读取已保存的用户设置"""
    path = _user_settings_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def apply_user_settings() -> dict:
    """程序启动时载入用户设置到内存配置"""
    data = load_user_settings()
    global DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
    global USE_REAL_AI, PROACTIVE_ENABLED, AUTOSTART_ENABLED
    global SOUND_ENABLED, SOUND_VOLUME, ALLOW_DOC_GENERATION, PEXELS_API_KEY
    global VISION_API_KEY, VISION_MODEL, VISION_API_URL
    global WEBSEARCH_API_KEY, WEBSEARCH_API_URL

    DEEPSEEK_API_KEY = data.get("api_key", DEEPSEEK_API_KEY)
    DEEPSEEK_BASE_URL = data.get("base_url", DEEPSEEK_BASE_URL)
    DEEPSEEK_MODEL = data.get("model", DEEPSEEK_MODEL)
    PROACTIVE_ENABLED = data.get("proactive", PROACTIVE_ENABLED)
    AUTOSTART_ENABLED = data.get("autostart", AUTOSTART_ENABLED)
    SOUND_ENABLED = data.get("sound_enabled", SOUND_ENABLED)
    SOUND_VOLUME = data.get("sound_volume", SOUND_VOLUME)
    ALLOW_DOC_GENERATION = bool(
        data.get("doc_enabled", ALLOW_DOC_GENERATION))
    PEXELS_API_KEY = str(data.get("pexels_key") or PEXELS_API_KEY)
    VISION_API_KEY = str(data.get("vision_key") or VISION_KEY_DEFAULT)
    VISION_MODEL = str(data.get("vision_model") or "").strip() \
        or VISION_MODEL_DEFAULT
    VISION_API_URL = str(data.get("vision_url") or "").strip() \
        or VISION_URL_DEFAULT
    WEBSEARCH_API_KEY = str(data.get("websearch_key") or WEBSEARCH_KEY_DEFAULT)
    WEBSEARCH_API_URL = str(data.get("websearch_url") or "").strip() \
        or WEBSEARCH_URL_DEFAULT

    # 有 Key 就自动启用真实 AI（无需手改代码）
    use_real = data.get("use_real_ai", bool(DEEPSEEK_API_KEY))
    USE_REAL_AI = bool(use_real and DEEPSEEK_API_KEY)
    return data


# ============================================================
# UI 参数
# ============================================================
PET_SIZE = 185                    # 角色尺寸（像素），09-12 由 160 再放大到 185
BUBBLE_DURATION = 3500            # 气泡最短显示时长（毫秒），也是基础时长
BUBBLE_MS_PER_CHAR = 180          # 气泡按字数追加的阅读时长（毫秒/字）
BUBBLE_DURATION_MAX = 30000       # 气泡最长显示时长（毫秒），防止长回复赖着不走
SCREENSHOT_REPLY_MS = 2500        # 截图前确认回复停留时长（毫秒），看清即可开抓
CARD_DURATION = 6500              # 卡片自动关闭时长（毫秒）
INPUT_FOLLOW_OFFSET = (-40, -150) # 输入框相对角色的偏移

# 日志目录
LOG_DIR = "logs"
