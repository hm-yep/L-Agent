# -*- coding: utf-8 -*-
"""导出工具契约：OpenAI Function Calling / MCP / 提示词三种格式。

用法：
    python tools/schema_export.py                      # 三种格式全打出来
    python tools/schema_export.py --format mcp         # 只打 MCP
    python tools/schema_export.py --format all --out data/tool_schema.json

导出内容完全来自 framework.action_registry 与 framework.effect_spec，
这里不定义任何动作行为，只负责落盘。
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from framework import tool_schema  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 Agent 工具契约")
    parser.add_argument("--format", default="all",
                        choices=("openai", "mcp", "prompt", "all"),
                        help="导出格式，默认全部")
    parser.add_argument("--out", default="",
                        help="输出文件路径；不填则打印到标准输出")
    args = parser.parse_args()

    if args.format == "prompt":
        text = tool_schema.export_prompt_text()
    else:
        payload = tool_schema.export_all()
        if args.format != "all":
            payload = payload[args.format]
        text = tool_schema.dumps(payload)

    if args.out:
        target = os.path.abspath(args.out)
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print("已导出到 %s" % target)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
