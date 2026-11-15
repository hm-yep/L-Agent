# -*- coding: utf-8 -*-
"""本机文件读取白名单：给 read_doc / analyze_data 这类"模型给路径就读"的动作设边界。

背景：这两个动作的 path 完全由大模型输出决定，读到的内容会写进【检索资料】
缓冲并随下一轮请求发给第三方 LLM。没有边界时，一句被诱导的指令就能让助手
读走 .ssh 私钥、.env、浏览器凭据或 user_settings.json 里的 API Key。

这里做的是**默认拒绝**（deny by default）：
  1. 只允许常见文档后缀与纯文本文件；
  2. 只允许几个明确的根目录，且解析后的真实路径必须仍在根内（防符号链接/.. 逃逸）；
  3. 拒绝任何落在敏感目录或命中敏感文件名的路径；
  4. 限制单文件大小，避免把整个硬盘读进上下文。

用户若要放开某个目录，用环境变量 LINGLING_ALLOWED_PATHS 追加（os.pathsep 分隔）。
这个模块只依赖标准库，不 import Qt / requests / 文档工厂，方便单测与契约检查。
"""

import os
import tempfile
from pathlib import Path

# 允许读取的后缀（与 _do_read_doc / data_tool.analyze 的支持范围对齐）
ALLOWED_EXTS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".jsonl", ".log", ".py",
    ".docx", ".pptx", ".xlsx",
}

# 路径中包含这些目录名即拒绝（大小写不敏感）。
# 注意：不能笼统拒绝 "appdata"——Windows 的临时目录本身就在 AppData\Local\Temp 下，
# 而临时目录是白名单根之一（测试和用户拖进来的临时文件都走这里）。
_DENIED_DIR_PARTS = {
    ".ssh", ".aws", ".git", ".gnupg", ".kube", ".docker",
    "windows", "system32", "desktoppet", "user data",
}

# 文件名命中这些模式即拒绝
_DENIED_FILE_PARTS = (
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".pem", ".key",
    ".pfx", ".p12", ".env", "credentials", "user_settings.json",
    "ntuser.dat", "sam", "secrets", "token", "password",
)

MAX_READ_BYTES = 20 * 1024 * 1024      # 单文件 20 MB 上限


def _home_candidates() -> list:
    """用户常用目录（兼容 OneDrive 重定向与中文目录名）"""
    home = Path.home()
    names = ("Desktop", "Documents", "Downloads",
             "桌面", "文档", "下载", "OneDrive/Desktop", "OneDrive/文档")
    return [home / n for n in names]


def allowed_roots() -> list:
    """当前允许读取的根目录（每次调用重新计算，便于测试与运行时改环境变量）"""
    roots = []

    def add(p):
        try:
            resolved = Path(p).expanduser().resolve()
            if resolved.is_dir() and resolved not in roots:
                roots.append(resolved)
        except (OSError, ValueError):
            pass

    for p in _home_candidates():
        add(p)
    add(tempfile.gettempdir())
    try:
        from framework.utils import app_data_dir
        add(app_data_dir())
    except (ImportError, OSError):
        pass
    for raw in os.environ.get("LINGLING_ALLOWED_PATHS", "").split(os.pathsep):
        if raw.strip():
            add(raw.strip())
    return roots


def is_sensitive(path: Path) -> bool:
    parts = [p.lower() for p in path.parts]
    if any(part in _DENIED_DIR_PARTS for part in parts[:-1]):
        return True
    name = path.name.lower()
    return any(token in name for token in _DENIED_FILE_PARTS)


def validate_read_path(raw) -> tuple:
    """校验一条模型给出的路径。

    :return: (ok, normalized_path_or_None, reason)
             reason 是可以直接念给用户听的中文短句，**不含绝对路径**，
             避免把本机目录结构回显给模型/日志。
    """
    raw = str(raw or "").strip().strip("\"'“”")
    if not raw:
        return False, None, "路径为空"

    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            # 相对路径按"项目工作目录下的文件"解释，仍要落在白名单根内
            path = Path.cwd() / path
        path = path.resolve()
    except (OSError, ValueError):
        return False, None, "路径无法解析"

    if not path.exists():
        return False, None, "文件不存在"
    if not path.is_file():
        return False, None, "这不是一个文件"
    if path.suffix.lower() not in ALLOWED_EXTS:
        return False, None, "这种格式不在允许读取的范围内"
    if is_sensitive(path):
        return False, None, "这个文件可能包含敏感信息，默认不读取"
    try:
        if path.stat().st_size > MAX_READ_BYTES:
            return False, None, "文件超过 20 MB 上限"
    except OSError:
        return False, None, "文件属性读不到"

    for root in allowed_roots():
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return True, str(path), ""

    return False, None, "文件不在允许的目录内（可用 LINGLING_ALLOWED_PATHS 追加）"
