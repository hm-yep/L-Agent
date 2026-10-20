# -*- coding: utf-8 -*-
"""LoRA 微调：基于 peft + transformers

    pip install peft transformers datasets accelerate pyyaml
    python finetune/train_lora.py
"""

import json
from pathlib import Path

import yaml
from datasets import Dataset
from peft import LoraConfig, TaskType, get_peft_model
from transformers import (AutoModelForCausalLM, AutoTokenizer,
                          TrainingArguments, Trainer)


def load_config(path: str = "finetune/config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_dataset(path: str, tokenizer, max_length: int) -> Dataset:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            text = (f"### 指令：{item['instruction']}\n"
                    f"### 输入：{item.get('input', '')}\n"
                    f"### 回答：{item['output']}")
            enc = tokenizer(text, truncation=True, max_length=max_length,
                            padding="max_length")
            enc["labels"] = enc["input_ids"].copy()
            records.append(enc)
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