# -*- coding: utf-8 -*-
"""LoRA 推理：加载基座 + LoRA 权重

这是**命令行**入口，方便单独试一条样本。产品里的那条路是
framework/lora_engine.py（主链路 provider），两者用同一份提示词模板
（framework/lora_format.py），提示词格式只有这一处。

    python finetune/inference.py --prompt "帮我打开微信"
"""

import argparse
import sys
from pathlib import Path

# 让脚本能从仓库根目录 import framework.lora_format
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from framework import lora_format    # noqa: E402  提示词格式单一真源

try:
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer
except ImportError as exc:  # noqa: BLE001 - 启动即给出可执行的安装指引
    sys.stderr.write(
        "[LoRA] 缺少推理依赖：{0}\n"
        "请先安装：pip install -r requirements-finetune.txt\n".format(exc)
    )
    raise SystemExit(2)


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
    # 只 decode 新生成的那段：连提示一起 decode 出来是旧脚本的老毛病，
    # 看起来像模型把自己的提问又说了一遍
    new_tokens = out[0][inputs["input_ids"].shape[-1]:]
    return tokenizer.decode(new_tokens, skip_special_tokens=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="Qwen/Qwen2.5-1.5B-Instruct")
    p.add_argument("--lora", default="finetune/output")
    p.add_argument("--prompt", default="介绍一下凌零")
    args = p.parse_args()

    model, tok = load(args.base, args.lora)
    prompt = lora_format.build_user_prompt(args.prompt)
    print(prompt)
    print(generate(model, tok, prompt))
