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
from framework.lora_engine import from_pretrained_cached   # noqa: E402

try:
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer
except ImportError as exc:  # noqa: BLE001 - 启动即给出可执行的安装指引
    sys.stderr.write(
        "[LoRA] 缺少推理依赖：{0}\n"
        "请先安装：pip install -r requirements-finetune.txt\n".format(exc)
    )
    raise SystemExit(2)


def load(model_path: str, lora_path: str = None, device: str = "auto"):
    """加载基座 + 适配器。

    走 from_pretrained_cached：先吃本地缓存，缓存缺失才联网——本机
    huggingface.co 被挡时，裸 from_pretrained 会连缓存齐全的情况都失败。
    """
    tokenizer = from_pretrained_cached(AutoTokenizer, model_path,
                                       trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = from_pretrained_cached(AutoModelForCausalLM, model_path,
                                   trust_remote_code=True)
    if device == "auto":
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(device)
    if lora_path:
        model = PeftModel.from_pretrained(model, lora_path)
    model.eval()
    return model, tokenizer


def generate(model, tokenizer, prompt: str, max_new_tokens: int = 256,
             temperature: float = 0.0):
    """续写并只返回新生成的那段。

    默认 temperature=0（贪心），与产品侧 framework/lora_engine.py 一致：
    命令行试出来的结果要和用户在桌面上拿到的一样，否则调提示词等于自欺。
    想要多样性显式传 --temperature。
    """
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    kwargs = {"max_new_tokens": max_new_tokens}
    if temperature and temperature > 0:
        kwargs.update(do_sample=True, temperature=temperature, top_p=0.9)
    else:
        kwargs.update(do_sample=False)
    out = model.generate(**inputs, **kwargs)
    # 只 decode 新生成的那段：连提示一起 decode 出来是旧脚本的老毛病，
    # 看起来像模型把自己的提问又说了一遍
    new_tokens = out[0][inputs["input_ids"].shape[-1]:]
    return tokenizer.decode(new_tokens, skip_special_tokens=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="Qwen/Qwen2.5-1.5B-Instruct")
    p.add_argument("--lora", default="finetune/output")
    p.add_argument("--device", default="auto")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--prompt", default="介绍一下凌零")
    args = p.parse_args()

    model, tok = load(args.base, args.lora, args.device)
    prompt = lora_format.build_user_prompt(args.prompt)
    print(prompt)
    print(generate(model, tok, prompt, temperature=args.temperature))
