# -*- coding: utf-8 -*-
"""真实权重上的 LoRA 质量评测（手动运行；需要 torch/transformers/peft + 已训好的适配器）

为什么需要它：`tools/verify_lora_runtime.py` 证明的是**链路通**（随机小模型
能加载、能续写、协议能判），它证明不了**答得好不好**。这个脚本在真实基座 +
真实适配器上把样本跑一遍，给出三个数字：

  * 可用率   —— 输出没有退给云端（即本地通路真的接住了这一轮）
  * 动作对   —— 除 show_bubble 外的动作序列与样本期望完全一致
  * 参数对   —— 第一个动作的关键参数（app / path / seconds 等）也一致

**口径要说清**：默认评的是训练集本身，数字必然偏高（模型见过这些句子），
所以它只回答「有没有学进去」，不回答「泛化得多好」。要测泛化就传一份
训练时没见过的 `--data`，两个数字一起看才有意义。

    python tools/eval_lora.py
    python tools/eval_lora.py --data finetune/data/holdout.jsonl
    python tools/eval_lora.py --adapter finetune/output --limit 10
"""

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 判定动作是否合法用的是主链路那份 normalize_action，不另写一套标准
from framework.action_parser import normalize_action   # noqa: E402

# 说话类动作不参与比对：每条样本都会带一句 show_bubble，用它判对错没有意义
_CHATTER = {"show_bubble"}
# 参数比对只看这些能一眼看出对错的键（动作池里真在用的参数名，
# 漏一个就等于那一类动作的参数错了也算对）
_PARAM_KEYS = ("app", "path", "seconds", "volume", "index", "count", "text",
               "content", "fact", "query", "title", "name", "skill",
               "workflow", "file", "keyword", "x", "y")


def _expected_actions(row: dict) -> list:
    out = json.loads(row["output"])
    if isinstance(out, dict):
        out = [out]
    return [a for a in out if isinstance(a, dict)]


def _real_actions(row: dict) -> list:
    return [a for a in row["actions"] if isinstance(a, dict)]


def _names(actions: list) -> list:
    return [a.get("action") for a in actions
            if a.get("action") and a.get("action") not in _CHATTER]


def _first_params(actions: list) -> dict:
    for a in actions:
        if a.get("action") in _CHATTER:
            continue
        return {k: a[k] for k in _PARAM_KEYS if k in a}
    return {}


def evaluate(data_path: str, adapter: str, limit: int = 0,
             base_model: str = None) -> dict:
    """逐条跑真实推理，返回汇总与明细。"""
    from framework import config, lora_engine

    if base_model:
        config.LORA_BASE_MODEL = base_model
    config.LORA_ADAPTER_PATH = adapter
    config.LORA_ENABLED = True

    rows = [json.loads(line) for line in
            Path(data_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    if limit:
        rows = rows[:limit]

    # 判定用的 worker 只构造一次：QThread 对象反复新建没必要，且没 start
    # 就丢弃容易留 Qt 警告。每轮只换 message。
    from framework.ai_engine import AIWorker
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])   # noqa: F841 必须持有引用
    worker = AIWorker("", [], "活泼")

    detail = []
    for i, row in enumerate(rows, 1):
        text = row.get("instruction") or row.get("input") or ""
        started = time.time()
        try:
            raw = lora_engine.generate_for(text)
            error = ""
        except Exception as exc:        # noqa: BLE001 单条失败不该中断整轮
            raw, error = "", str(exc)
        elapsed = time.time() - started

        # 用主链路的判定逻辑把原始输出变成动作列表（None = 会退给云端）
        actions = None
        if raw:
            try:
                worker.message = text
                actions = AIWorker._request_lora(worker)
            except Exception as exc:    # noqa: BLE001
                error = error or str(exc)
        detail.append({"instruction": text, "raw": raw, "actions": actions or [],
                        "available": actions is not None, "elapsed": elapsed,
                        "error": error})
        print("  %2d/%d  %-34s %s  %.1fs"
              % (i, len(rows), text[:32], "可用" if actions else "退云端", elapsed),
              flush=True)

    total = len(detail)
    usable = sum(1 for d in detail if d["available"])
    action_ok = param_ok = 0
    per_row = []
    for row, d in zip(rows, detail):
        exp = _expected_actions(row)
        got = _real_actions(d)
        same = _names(exp) == _names(got)
        params = same and _first_params(exp) == _first_params(got)
        action_ok += 1 if same else 0
        param_ok += 1 if params else 0
        per_row.append({"instruction": d["instruction"],
                        "expected": _names(exp), "got": _names(got),
                        "action_ok": same, "param_ok": params,
                        "available": d["available"]})

    avg = sum(d["elapsed"] for d in detail) / total if total else 0.0
    summary = {
        "data": data_path,
        "adapter": adapter,
        "total": total,
        "usable": usable,
        "usable_rate": round(usable / total, 4) if total else 0.0,
        "action_ok": action_ok,
        "action_rate": round(action_ok / total, 4) if total else 0.0,
        "param_ok": param_ok,
        "param_rate": round(param_ok / total, 4) if total else 0.0,
        "avg_seconds": round(avg, 2),
        "rows": per_row,
        "raw": detail,
    }
    return summary


def _print_summary(s: dict) -> None:
    print("\n" + "=" * 56)
    print("样本 %d 条 · 可用 %d (%.1f%%) · 动作全对 %d (%.1f%%) "
          "· 参数全对 %d (%.1f%%) · 平均 %.2f 秒/条"
          % (s["total"], s["usable"], s["usable_rate"] * 100,
             s["action_ok"], s["action_rate"] * 100,
             s["param_ok"], s["param_rate"] * 100, s["avg_seconds"]))
    bad = [r for r in s["rows"] if not r["action_ok"]]
    if bad:
        print("-" * 56)
        print("动作不一致的 %d 条：" % len(bad))
        for r in bad:
            print("  指令: %s" % r["instruction"][:40])
            print("    期望 %s / 实得 %s%s"
                  % (r["expected"], r["got"],
                     "" if r["available"] else "（本地没接住，会退云端）"))
    print("=" * 56)
    print("口径：默认评训练集本身，数字偏高；要测泛化请用 --data 传未参与训练的指令。")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="finetune/data/train.jsonl")
    p.add_argument("--adapter", default="finetune/output")
    p.add_argument("--base", default=None, help="覆盖基座模型（默认读配置）")
    p.add_argument("--limit", type=int, default=0, help="只跑前 N 条")
    p.add_argument("--json", default="", help="把完整结果写到这个文件")
    args = p.parse_args()

    try:
        import torch  # noqa: F401
        import peft   # noqa: F401
        import transformers  # noqa: F401
    except ImportError as exc:  # noqa: BLE001
        sys.stderr.write("[LoRA] 缺少真实依赖，无法评测：%s\n"
                         "请先安装：pip install -r requirements-finetune.txt\n" % exc)
        return 2

    from framework import lora_engine
    info = lora_engine.status(adapter_path=args.adapter, base_model=args.base)
    print("适配器: %s" % args.adapter)
    print("状态  : ready=%s %s" % (info["ready"], info["reason"]))
    if not info["ready"]:
        return 3

    summary = evaluate(args.data, args.adapter, args.limit, args.base)
    _print_summary(summary)
    if args.json:
        Path(args.json).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print("完整结果已写入 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
