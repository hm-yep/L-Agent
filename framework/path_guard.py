# -*- coding: utf-8 -*-
"""本机文件读取白名单：给 read_doc / analyze_data 这类"模型给路径就读"的动作设边界。

背景：这两个动作的 path 完全由大模型输出决定，读到的内容会写进【检索资料】
缓冲并随下一轮请求发给第三方 LLM。没有边界时，一句被诱导的指令就能让助手
读走 .ssh 私钥、.env、浏览器凭据或 user_settings.json 里的 API Key。

这里做的是**默认拒绝**（deny by default）：
  1. 只允许表里列出的后缀（文档 / 纯文本与代码 / 表格 / 压缩包）；
  2. 只允许几个明确的根目录，且解析后的真实路径必须仍在根内（防符号链接/.. 逃逸）；
  3. 拒绝任何落在敏感目录或命中敏感文件名的路径——**这一条不给授权**；
  4. 限制单文件大小，避免把整个硬盘读进上下文。

不在根目录里的路径不是「一律拒」，而是**要问一次**：`check_read()` 返回三态
`allow` / `grant` / `deny`，`grant` 表示「格式认得、不是敏感项，但需要你授权」，
由界面弹一次确认，答应用户的话记进 `framework.grants.GrantStore`（只本次运行有效）。
永久放开的口子仍然是环境变量 `LINGLING_ALLOWED_PATHS`（os.pathsep 分隔）。
这个模块只依赖标准库，不 import Qt / requests / 文档工厂，方便单测与契约检查。
"""

import os
import tempfile
from pathlib import Path

# ---- 格式表：分成几组，判据只看这张表，界面文案也从这里生成 ----------------
# 纯文本与代码：靠后缀认出「应该按文本读」，内容再过一次二进制嗅探（file_reader）
TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl",
    ".log", ".ini", ".cfg", ".conf", ".toml", ".yaml", ".yml", ".xml",
    ".html", ".htm", ".css", ".sql", ".sh", ".bat", ".cmd", ".ps1",
    ".py", ".pyi", ".ipynb", ".js", ".ts", ".jsx", ".tsx", ".vue",
    ".c", ".h", ".cpp", ".cxx", ".cc", ".hpp", ".java", ".kt", ".go",
    ".rs", ".rb", ".php", ".swift", ".m", ".mm", ".lua", ".pl", ".r",
    ".gradle", ".cmake", ".mk", ".make",
}
# 没有后缀但天天见的文本文件名（`splitext` 对 `.gitignore` 这类点开头文件给不出后缀）
TEXT_NAMES = {
    "dockerfile", "makefile", "readme", "license", "changelog", "notes",
    ".gitignore", ".dockerignore", ".editorconfig", ".env.example",
    ".babelrc", ".eslintrc", ".pylintrc",
}
# Office 三件套（python-docx / python-pptx / openpyxl）
OFFICE_EXTS = {".docx", ".pptx", ".xlsx"}
# 老格式：靠本机 WPS/Office 的 COM 接口转文本，机器上没装就如实说不支持
LEGACY_EXTS = {".doc", ".xls", ".ppt"}
# 文本型 PDF（pypdf 抽文本层；扫描型没文本层，交给视觉而不是硬解）
PDF_EXTS = {".pdf"}
# 压缩包：标准库能开的（列条目 + 读里面的文本成员，成员再过一次本表与敏感判定）
ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz"}
# 图片不进这张表：那是视觉模型的活（see_screen / 后续 read_image），不是文本抽取

DOC_EXTS = OFFICE_EXTS | LEGACY_EXTS | PDF_EXTS

# 允许读取的后缀全集（旧名字保留：契约与单测按这张表逐个验「写了就真能读」）
ALLOWED_EXTS = TEXT_EXTS | DOC_EXTS | ARCHIVE_EXTS


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
    """路径里任何一段命中敏感目录名，或末段文件名命中敏感词，就不读。

    目录名要检查**全部**分段（含最后一段）：列目录也是一个读取动作，
    只看 parts[:-1] 会让 `~/.ssh` 这种「敏感目录本身」被放行。
    """
    parts = [p.lower() for p in path.parts]
    if any(part in _DENIED_DIR_PARTS for part in parts):
        return True
    name = path.name.lower()
    return any(token in name for token in _DENIED_FILE_PARTS)


def kind_of(path: Path) -> str:
    """这条路径按什么方式读：dir / archive / pdf / legacy / office / text / unknown。"""
    if path.is_dir():
        return "dir"
    name = path.name.lower()
    ext = path.suffix.lower()
    if ext in ARCHIVE_EXTS:
        return "archive"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in LEGACY_EXTS:
        return "legacy"
    if ext in OFFICE_EXTS:
        return "office"
    if ext in TEXT_EXTS or name in TEXT_NAMES or not ext:
        # 没后缀的按文本试一次，内容里若有 NUL 字节，file_reader 会拒
        return "text"
    return "unknown"


def _format_ok(path: Path) -> bool:
    return kind_of(path) != "unknown"


def check_read(raw, grants=None) -> tuple:
    """三态判定一条路径能不能读、要不要先问用户。

    :param grants: `framework.grants.GrantStore` 或 None（None＝只认静态白名单）
    :return: (verdict, path_str, reason)
             verdict 是 allow / grant / deny 之一；
             reason 是能直接念给用户听的中文短句，**不含绝对路径**，
             避免把本机目录结构回显给模型或日志。
    """
    raw = str(raw or "").strip().strip("\"'“”")
    if not raw:
        return "deny", None, "路径为空"
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
    except (OSError, ValueError):
        return "deny", None, "路径无法解析"

    if not path.exists():
        return "deny", None, "文件不存在"
    # 敏感判定放在最前面，且永远不给授权口子：点了「允许整个文件夹」也读不到私钥
    if is_sensitive(path):
        return "deny", None, "这个文件可能包含敏感信息，即使授权我也不读"
    if not _format_ok(path):
        return "deny", None, "这种格式我还读不了"
    if path.is_file():
        try:
            if path.stat().st_size > MAX_READ_BYTES:
                return "deny", None, "文件超过 20 MB 上限"
        except OSError:
            return "deny", None, "文件属性读不到"

    for root in allowed_roots():
        if _same_or_within(path, root):
            return "allow", str(path), ""
    if grants is not None and grants.covers(path):
        return "allow", str(path), ""
    return "grant", str(path), "这个路径不在我平常能读的目录里，要授权我才读"


def validate_read_path(raw) -> tuple:
    """只要静态白名单通过才算 ok 的老接口（网页入库、单测与「不授权」的场景用）。

    需要「问用户一次」这条路的调用方请直接用 `check_read`，
    把 verdict=="grant" 当成一次确认请求，而不是失败。
    """
    verdict, path, reason = check_read(raw, grants=None)
    if verdict == "allow":
        return True, path, ""
    if verdict == "grant":
        return False, path, reason
    return False, None, reason


def _same_or_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# 能力文案：气泡、输入框占位、提示词里的「支持什么格式」都从这一句生成。
# 写成样本清单（而不是把整张表倒出来）是为了念得顺口；单测与契约逐条核对
# 这里列出的每个后缀都真的在判据表里，防止「文案说有 .pages，判据其实不认」。
FORMAT_GROUPS = (
    ("纯文本与代码", (".txt", ".md", ".py", ".cpp", ".json", ".csv")),
    ("文档", (".docx", ".pptx", ".xlsx", ".pdf")),
    ("老格式", (".doc", ".xls")),
    ("压缩包", (".zip", ".tar.gz")),
    ("文件夹本身（列一层）", ()),
)


def describe_formats(sep: str = "；") -> str:
    """一句话说明支持哪些格式。判据表改了，这句话不需要另行维护。"""
    bits = []
    for name, samples in FORMAT_GROUPS:
        if samples:
            heads = "/".join(s.lstrip(".") for s in samples)
            bits.append(f"{name}（{heads}）")
        else:
            bits.append(name)
    return sep.join(bits)


def advertised_exts() -> set:
    """文案里承诺过的后缀集合（`tar.gz` 记成 `.gz`），供契约与单测对照判据表。

    注意：`Path(".txt").suffix` 是空串（点开头的名字整段都算 stem），
    所以补一个假文件名前缀再 splitext。
    """
    out = set()
    for _, samples in FORMAT_GROUPS:
        for s in samples:
            out.add(os.path.splitext("a" + s)[1].lower())
    return out
