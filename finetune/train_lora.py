# -*- coding: utf-8 -*-
"""LoRA 微调：基于 peft + transformers

    pip install peft transformers datasets accelerate pyyaml
    python finetune/train_lora.py

两件事要跟产品侧对齐（都写在这一处的注释里，别只改一边）：
  * 样本文本走 framework/lora_format.py——那是提示词格式的单一真源，
    产品推理（framework/lora_engine.py）用的是同一份模板。微调最怕
    「训的是 A、跑的是 B」，两处各写一遍格式迟早会漂。
  * 只有**回答部分**参与 loss（提示部分掩成 -100）。历史脚本把整段
    都算进 loss，等于让模型连提示一起背，推理时却只喂提示——
    训练目标与推理用法对不上。
"""

import json
import sys
from pathlib import Path

# 让脚本能从仓库根目录 import framework.lora_format（直接跑本文件时
# sys.path[0] 是 finetune/，拿不到仓库根）
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from framework import lora_format    # noqa: E402  提示词格式单一真源

try:
    import yaml
    from datasets import Dataset
    from peft import LoraConfig, TaskType, get_peft_model
    from transformers import (AutoModelForCausalLM, AutoTokenizer,
                              TrainingArguments, Trainer)
except ImportError as exc:  # noqa: BLE001 - 启动即给出可执行的安装指引，而非堆栈
    sys.stderr.write(
        "[LoRA] 缺少训练依赖，无法启动微调：{0}\n"
        "请先安装：pip install peft transformers datasets accelerate pyyaml torch\n"
        "提示：数据层校验不依赖这些库，可直接运行 "
        "pytest tests/test_finetune_data.py\n".format(exc)
    )
    raise SystemExit(2)


def load_config(path: str = "finetune/config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_dataset(path: str, tokenizer, max_length: int) -> Dataset:
    """拼训练样本：提示部分不算 loss，只有回答部分参与训练。

    提示文本走 framework/lora_format.py（与产品推理同一份模板）；
    labels 里提示段与补齐段一律掩成 -100，模型只学「怎么答」。
    """
    records = []
    pad_id = tokenizer.pad_token_id
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            prompt = lora_format.build_prompt(item["instruction"],
                                              item.get("input", ""))
            answer = item["output"]
            prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
            answer_ids = tokenizer(answer, add_special_tokens=False)["input_ids"]
            if tokenizer.eos_token_id is not None:
                answer_ids = answer_ids + [tokenizer.eos_token_id]
            ids = prompt_ids + answer_ids
            labels = [-100] * len(prompt_ids) + answer_ids
            real_len = min(len(ids), max_length)
            ids = ids[:real_len]
            labels = labels[:real_len]
            attention = [1] * real_len + [0] * (max_length - real_len)
            ids = ids + [pad_id] * (max_length - real_len)
            labels = labels + [-100] * (max_length - real_len)
            records.append({"input_ids": ids,
                            "attention_mask": attention,
                            "labels": labels})
    return Dataset.from_list(records)


def main():
    cfg = load_config()
    print(f"[LoRA] 基座模型: {cfg['base_model']}")

    tokenizer = AutoTokenizer.from_pretrained(cfg["base_model"],
                                               trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        cfg["base_model"], trust_remote_code=True,
        device_map="auto")

    lora_cfg = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=cfg["lora_r"],
        lora_alpha=cfg["lora_alpha"],
        lora_dropout=cfg["lora_dropout"],
        target_modules=cfg["target_modules"],
    )
    model = get_peft_model(model, lora_cfg)
    model.print_trainable_parameters()

    dataset = build_dataset(cfg["data_path"], tokenizer,
                            cfg["max_length"])

    Path(cfg["output_dir"]).mkdir(parents=True, exist_ok=True)

    args = TrainingArguments(
        output_dir=cfg["output_dir"],
        num_train_epochs=cfg["num_epochs"],
        per_device_train_batch_size=cfg["batch_size"],
        gradient_accumulation_steps=cfg["grad_accum"],
        learning_rate=cfg["learning_rate"],
        warmup_ratio=cfg["warmup_ratio"],
        logging_steps=cfg["logging_steps"],
        save_steps=cfg["save_steps"],
        save_total_limit=2,
        fp16=True,
        report_to="none",
    )

    trainer = Trainer(model=model, args=args, train_dataset=dataset,
                      tokenizer=tokenizer)
    trainer.train()
    trainer.save_model(cfg["output_dir"])
    tokenizer.save_pretrained(cfg["output_dir"])
    print(f"✅ LoRA 权重已保存到 {cfg['output_dir']}")


if __name__ == "__main__":
    main()