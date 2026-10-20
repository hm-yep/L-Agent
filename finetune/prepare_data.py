# -*- coding: utf-8 -*-
"""数据准备：把原始对话整理成 Alpaca 格式"""

import argparse
import json
import os


def convert(raw_path: str, out_path: str):
    with open(raw_path, encoding="utf-8") as f:
        data = json.load(f)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for item in data:
            line = {
                "instruction": item.get("instruction", ""),
                "input": item.get("input", ""),
                "output": item.get("output", ""),
            }
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    print(f"✅ 已生成 {out_path}，共 {len(data)} 条")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--input", default="finetune/data/raw.json")
    p.add_argument("--output", default="finetune/data/train.jsonl")
    args = p.parse_args()
    convert(args.input, args.output)