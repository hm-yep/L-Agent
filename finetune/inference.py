# -*- coding: utf-8 -*-
"""LoRA 推理：加载基座 + LoRA 权重"""

import argparse
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


def load(model_path: str, lora_path: str = None):
    tokenizer = AutoTokenizer.from_pretrained(model_path,
                                               trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        model_path, trust_remote_code=True, device_map="auto")
    if lora_path:
        model = PeftModel.from_pretrained(model, lora_path)
    return model, tokenizer


def generate(model, tokenizer, prompt: str, max_new_tokens: int = 256):
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    out = model.generate(**inputs, max_new_tokens=max_new_tokens,
                         temperature=0.7, do_sample=True)
    return tokenizer.decode(out[0], skip_special_tokens=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="Qwen/Qwen2.5-1.5B-Instruct")
    p.add_argument("--lora", default="finetune/output")
    p.add_argument("--prompt", default="### 指令：介绍一下凌零\n### 回答：")
    args = p.parse_args()

    model, tok = load(args.base, args.lora)
    print(generate(model, tok, args.prompt))