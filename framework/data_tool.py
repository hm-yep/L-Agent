# -*- coding: utf-8 -*-
"""数据分析工具（analyze_data 动作的执行体）

用 pandas 把用户本机的表格（csv / tsv / xlsx）读成 DataFrame，产出两样东西：
1. 统计摘要——规模、列型、缺失值、数值列分布、类别列频次、可选分组聚合、
   相关性最强的一对数值列；
2. 图表——柱状 / 折线 / 饼图，PNG 存到数据目录，由卡片交付给用户。

摘要是要进【检索资料】缓冲给模型看的，所以这里给"结论式人话"而不是
原始 head()：模型拿到合计、极值、分组名次，才能直接答"哪个月最高、差多少"。

判定与截断都保持保守：列太多/太宽时只报最有信息量的前几项，
绝不让一段摘要吃掉整个上下文预算。
"""

import os
import re
import time

import matplotlib
matplotlib.use("Agg")          # 无界面后端：不跟 Qt 主线程抢 GUI 线程
import matplotlib.pyplot as plt   # noqa: E402  必须先设后端再 import pyplot
import pandas as pd                # noqa: E402

from framework import config
from framework.utils import app_data_dir, logger

# 中文字体按可用性依次回退，找不到就退回默认字体（中文会变方框但不影响出图）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "PingFang SC",
                                   "Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# 图表类型的中英文写法都收（模型经常直接写"饼图"）
_CHART_WORDS = {
    "bar": ("bar", "column", "柱", "条形", "直方"),
    "line": ("line", "trend", "折线", "曲线", "趋势", "走势"),
    "pie": ("pie", "饼", "圆饼", "扇形", "占比"),
}
# 模型/用户这样写就表示"不要图"，只给文字结论
_CHART_OFF = ("none", "off", "false", "skip", "不要", "不用", "不需",
              "别画", "不画", "无图", "只要文字")


def normalize_chart_kind(raw, default: str = "bar") -> str:
    """把"饼图 / 折线 / bar"之类统一成 bar / line / pie。"""
    s = str(raw or "").strip().lower()
    if not s:
        return default
    for kind, words in _CHART_WORDS.items():
        if any(w in s for w in words):
            return kind
    return default


def chart_wanted(raw) -> bool:
    """是否要出图：缺省出图，命中否定词才只给文字结论。"""
    s = str(raw or "").strip().lower()
    if not s:
        return True
    return not any(w in s for w in _CHART_OFF)


def load_table(path: str) -> pd.DataFrame:
    """读表格；文件/格式不支持时抛 ValueError（中文信息，直接给用户看）。"""
    if not path or not os.path.exists(path):
        raise ValueError("这份文件不存在，确认一下完整路径？")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".tsv", ".txt"):
        last_err = None
        for enc in ("utf-8-sig", "gbk"):
            try:
                if ext == ".tsv":
                    return pd.read_csv(path, sep="\t", encoding=enc,
                                       nrows=config.DATA_MAX_ROWS)
                # engine=python + sep=None：自动嗅探逗号/分号/制表符
                return pd.read_csv(path, engine="python", encoding=enc,
                                   nrows=config.DATA_MAX_ROWS)
            except (UnicodeDecodeError, pd.errors.ParserError) as e:
                last_err = e
        raise ValueError(f"这个文本表格我解析不了（{type(last_err).__name__}），"
                         "另存成标准 csv 或 xlsx 再试？")
    if ext in (".xlsx", ".xlsm"):
        return pd.read_excel(path, nrows=config.DATA_MAX_ROWS)
    raise ValueError("我目前能分析 csv / tsv / xlsx；"
                     "老格式 .xls 请先另存为 xlsx，数据库导出也请先存成文件。")


def _numeric_cols(df: pd.DataFrame) -> list:
    return [c for c in df.columns
            if pd.api.types.is_numeric_dtype(df[c])
            and not pd.api.types.is_bool_dtype(df[c])]


def _category_cols(df: pd.DataFrame) -> list:
    return [c for c in df.columns
            if not pd.api.types.is_numeric_dtype(df[c])
            and not pd.api.types.is_datetime64_any_dtype(df[c])]


def _match_col(df: pd.DataFrame, name: str):
    """把用户/模型口述的列名对到真实列名：精确 → 忽略大小写 → 互相包含。"""
    want = str(name or "").strip().strip("\"“”'")
    if not want:
        return None
    for c in df.columns:
        if str(c) == want:
            return c
    low = want.lower()
    for c in df.columns:
        if str(c).strip().lower() == low:
            return c
    for c in df.columns:
        s = str(c).strip().lower()
        if low in s or (len(s) >= 2 and s in low):
            return c
    return None


def _fmt(v) -> str:
    """数字读数统一格式化：大数加千分位，小数保留 2 位，长整数不显示 .0。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f:                      # NaN
        return "—"
    if abs(f - round(f)) < 1e-9 and abs(f) < 1e15:
        return f"{int(round(f)):,}"
    return f"{f:,.2f}"


def summarize(df: pd.DataFrame, group_by: str = "") -> str:
    """生成结论式统计摘要（纯文本逐行，长度受 config.DATA_SUMMARY_MAX 约束）。"""
    if df is None or df.empty:
        raise ValueError("这份表格是空的，没有可分析的行列。")
    lines = [f"数据规模：{len(df)} 行 × {len(df.columns)} 列"]
    num, cat = _numeric_cols(df), _category_cols(df)
    lines.append(f"数值列 {len(num)} 个："
                 + ("、".join(str(c) for c in num[:10]) or "无")
                 + f"；类别列 {len(cat)} 个："
                 + ("、".join(str(c) for c in cat[:10]) or "无"))

    missing = {c: int(df[c].isna().sum()) for c in df.columns}
    missing = {c: n for c, n in missing.items() if n}
    if missing:
        worst = sorted(missing.items(), key=lambda kv: -kv[1])[:5]
        lines.append("缺失：" + "、".join(
            f"{c} 空 {n} 个（占 {n / len(df):.0%}）" for c, n in worst))
    else:
        lines.append("缺失：无空值，数据完整")

    if num:
        for c in num[:6]:
            s = df[c]
            lines.append(f"{c}：合计 {_fmt(s.sum())}，均值 {_fmt(s.mean())}，"
                         f"中位数 {_fmt(s.median())}，最小 {_fmt(s.min())}，"
                         f"最大 {_fmt(s.max())}，标准差 {_fmt(s.std())}")
        if len(num) > 6:
            lines.append(f"（另有 {len(num) - 6} 个数值列未展开）")

    shown = 0
    for c in cat:
        if shown >= 3:
            break
        vc = df[c].value_counts()
        if not len(vc) or len(vc) == len(df):
            continue      # 每行一个值的列（编号、姓名）报频次没有信息量
        shown += 1
        lines.append(f"{c}：{len(vc)} 个取值，最多"
                     + "、".join(f"{k}({v})" for k, v in vc.head(3).items())
                     + f"，最少 {vc.index[-1]}({vc.iloc[-1]})")

    if len(num) >= 2:
        corr = df[num].corr(numeric_only=True)
        best = None
        for i, a in enumerate(corr.columns):
            for b in corr.columns[i + 1:]:
                r = corr.at[a, b]
                if r == r and (best is None or abs(r) > abs(best[2])):
                    best = (a, b, r)
        if best:
            a, b, r = best
            rel = "正相关" if r > 0 else "负相关"
            strength = ("强" if abs(r) >= 0.7 else
                        "中等" if abs(r) >= 0.4 else "弱")
            lines.append(f"相关性：{a} 与 {b} {strength}{rel}（r={r:.2f}）")

    if group_by:
        col = _match_col(df, group_by)
        if col is None:
            lines.append(f"分组列「{group_by}」没找到，可用列："
                         + "、".join(str(c) for c in df.columns[:12]))
        else:
            g = df.groupby(col, dropna=True)
            if num:
                key = num[0]
                agg = g[key].sum().sort_values(ascending=False)
                rank = agg.head(5)
                top = "、".join(f"{k}:{_fmt(v)}" for k, v in rank.items())
                lines.append(f"按 {col} 分组的 {key} 合计"
                             f"（前 {len(rank)} 名）：{top}")
                total = agg.sum()
                if len(agg) > 1:
                    share = f"{agg.iloc[0] / total:.0%}" if total else "—"
                    lines.append(f"{col} 分组共 {len(agg)} 组，"
                                 f"第一 {agg.index[0]} 占 {share}")
            else:
                vc = g.size().sort_values(ascending=False)
                lines.append(f"按 {col} 分组的行数（前 5）："
                             + "、".join(f"{k}:{v}"
                                        for k, v in vc.head(5).items()))
    return fit("\n".join(lines))


def fit(text: str) -> str:
    """按整行裁剪到长度上限（保证进【检索资料】缓冲时不被硬截断）。"""
    limit = config.DATA_SUMMARY_MAX
    if len(text) <= limit:
        return text
    kept, used, rows = [], 0, text.split("\n")
    for line in rows:
        if used + len(line) + 1 > limit - 12:
            break
        kept.append(line)
        used += len(line) + 1
    kept.append(f"（余 {len(rows) - len(kept)} 行统计略）")
    return "\n".join(kept)


def render_chart(df: pd.DataFrame, path: str, x_col: str = "",
                 y_col: str = "", kind: str = "bar", title: str = "") -> str:
    """画一张图并返回 PNG 路径；列选不出来时自己回退到"第一个类别列的频次"。"""
    kind = normalize_chart_kind(kind)
    x = _match_col(df, x_col)
    if x is None:
        cands = _category_cols(df) or list(df.columns)
        x = cands[0]
    y = _match_col(df, y_col)
    if y is not None and y == x:
        y = None

    if y is None:
        data = df[x].value_counts().sort_index()
        xs, ys, ylabel = data.index.astype(str), data.values, f"{x} 计数"
    elif pd.api.types.is_numeric_dtype(df[y]):
        pair = df[[x, y]].dropna()
        if pair.empty:
            raise ValueError(f"{x} 和 {y} 没有同时有值的行，画不出来。")
        agg = pair.groupby(x, dropna=False)[y].sum().sort_index()
        xs, ys, ylabel = agg.index.astype(str), agg.values, f"{y} 合计"
    else:
        data = df[x].value_counts().sort_index()
        xs, ys, ylabel = data.index.astype(str), data.values, f"{x} 计数"

    xs, ys = list(map(str, xs)), list(ys)
    if kind == "pie" and len(xs) > 8:      # 饼图切片太多不可读，取前 8
        order = sorted(range(len(ys)), key=lambda i: -float(ys[i]))[:8]
        xs = [xs[i] for i in order]
        ys = [ys[i] for i in order]
    if len(xs) == 1:
        raise ValueError(f"「{x}」只有一个取值，出图没有信息量。")

    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=140)
    if kind == "pie":
        ax.pie([float(v) for v in ys], labels=xs, autopct="%1.0f%%",
               startangle=90, counterclock=False)
        ax.set_title(title or f"{x} 占比")
    else:
        if kind == "line":
            ax.plot(xs, [float(v) for v in ys], marker="o")
        else:
            ax.bar(xs, [float(v) for v in ys], color="#4C79A6")
        ax.set_xlabel(str(x))
        ax.set_ylabel(ylabel)
        ax.set_title(title or f"{x} → {ylabel}")
        ax.grid(axis="y", alpha=0.3)
        if len(xs) > 8:
            for tick in ax.get_xticklabels():
                tick.set_rotation(35)
                tick.set_ha("right")
    fig.tight_layout()

    out_dir = os.path.join(app_data_dir(), "analysis")
    os.makedirs(out_dir, exist_ok=True)
    stem = re.sub(r"[^\w\-]+", "_", os.path.splitext(os.path.basename(path))[0])
    png = os.path.join(out_dir,
                       f"{stem}_{time.strftime('%m%d_%H%M%S')}"
                       f"{time.time_ns() // 1000 % 1000:03d}.png")
    fig.savefig(png, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"[Data] 图表已生成: {png}")
    return png


def analyze(path: str, group_by: str = "", x_col: str = "", y_col: str = "",
            chart: str = "bar") -> dict:
    """读表 → 统计摘要 → 出图。返回 {"summary", "chart_path", "rows", "cols"}。

    :param chart: bar / line / pie；填"不要/none"则只给文字结论。
    """
    df = load_table(path)
    summary = summarize(df, group_by)
    chart_path = ""
    if chart_wanted(chart):
        try:
            chart_path = render_chart(df, path, x_col, y_col,
                                      normalize_chart_kind(chart))
        except Exception as e:                       # 出图失败不该拖垮统计结论
            logger.warning(f"[Data] 图表生成失败: {e}")
    return {"summary": summary, "chart_path": chart_path,
            "rows": len(df), "cols": len(df.columns)}
