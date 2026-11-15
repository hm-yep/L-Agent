# -*- coding: utf-8 -*-
"""LoRA 微调数据层校验（只用标准库，不依赖 torch/transformers/peft）

目的：让"微调可复现"这件事能被 CI 真正验证——数据文件在、格式对、
动作全部落在 30 动作池内，并且项目自带的 prepare_data.convert 能跑通。
"""

import json
from pathlib import Path

import pytest

from framework.action_parser import VALID_ACTIONS

REPO = Path(__file__).resolve().parents[1]
TRAIN = REPO / "finetune" / "data" / "train.jsonl"


def _load_jsonl(path: Path):
    rows = []
    for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append((ln, json.loads(line)))
        except json.JSONDecodeError as e:  # noqa: BLE001
            pytest.fail(f"{path.name} 第 {ln} 行不是合法 JSON: {e}")
    return rows


def test_train_jsonl_exists_and_has_enough_samples():
    assert TRAIN.exists(), "finetune/data/train.jsonl 缺失，微调无法复现"
    rows = _load_jsonl(TRAIN)
    assert len(rows) >= 20, f"样本太少（{len(rows)}），不足以体现领域覆盖"


def test_train_jsonl_schema_and_actions():
    for ln, item in _load_jsonl(TRAIN):
        assert set(item) <= {"instruction", "input", "output"}, f"第{ln}行键异常"
        assert item.get("instruction", "").strip(), f"第{ln}行 instruction 为空"
        out = item.get("output", "").strip()
        assert out, f"第{ln}行 output 为空"
        arr = json.loads(out)
        assert isinstance(arr, list) and arr, f"第{ln}行 output 不是非空 JSON 数组"
        for act in arr:
            name = act.get("action")
            assert name in VALID_ACTIONS, f"第{ln}行动作 {name!r} 不在 30 动作池"


def test_convert_pipeline_roundtrip(tmp_path):
    """用项目自带的 convert() 把 raw.json 转成 JSONL，验证工具链真的能跑。"""
    from finetune.prepare_data import convert

    raw = tmp_path / "raw.json"
    dst = tmp_path / "out" / "train.jsonl"
    payload = [
        {"instruction": "打开记事本", "input": "",
         "output": '[{"action": "open_app", "app": "记事本"}]'},
        {"instruction": "随便聊聊", "output":
         '[{"action": "show_bubble", "text": "在的"}]'},
    ]
    raw.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    convert(str(raw), str(dst))

    rows = _load_jsonl(dst)
    assert len(rows) == 2
    for _, item in rows:
        assert set(item) == {"instruction", "input", "output"}  # convert 补齐了 input
