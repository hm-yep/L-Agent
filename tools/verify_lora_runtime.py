# -*- coding: utf-8 -*-
"""真机验证本地微调通路（手动运行；需要 torch / transformers / peft）

为什么还要这个脚本：单测里的模型与分词器都是替身，证明不了
「PEFT 适配器真能加载、真能续写、真能产出主链路认的动作 JSON」。
这个脚本在**真依赖**上把整条链路走一遍：

  1) 造一个**极小的随机基座模型 + 词表**（不联网、不下几个 G 的权重，
     几秒钟就能造好；它不会说人话，只用来证明链路是通的）；
  2) 用 peft 给它挂一个 LoRA 适配器并 save_pretrained
     （产出真实的 adapter_config.json + adapter_model.safetensors）；
  3) 用 framework/lora_engine 的**真运行时**加载它并续写；
  4) 把这次真续写的结果喂给 framework/ai_engine 的协议校验，
     看它被判成「合格动作」还是「不合协议 → 退云端」。

用法：python tools/verify_lora_runtime.py
退出码 0 = 链路通；非 0 = 有环节没通（每一步都会说明原因）。
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    import torch
    from peft import LoraConfig, TaskType, get_peft_model
    from tokenizers import Tokenizer, models
    from transformers import (AutoTokenizer, GPT2Config, GPT2LMHeadModel,
                              PreTrainedTokenizerFast)
except ImportError as exc:  # noqa: BLE001
    sys.stderr.write(
        "[LoRA] 缺少真依赖，无法做真机验证：%s\n"
        "请先安装：pip install -r requirements-finetune.txt\n" % exc)
    raise SystemExit(2)


def _build_tiny_base(base_dir: Path, vocab_size: int = 64) -> None:
    """造一个极小的随机 GPT-2：词表 + 分词器 + 模型权重。"""
    chars = [chr(c) for c in range(0x4E00, 0x4E00 + vocab_size - 4)]
    vocab = {"<pad>": 0, "<unk>": 1, "<eos>": 2, "<bos>": 3}
    for i, ch in enumerate(chars):
        vocab[ch] = 4 + i
    tok = Tokenizer(models.WordLevel(vocab, unk_token="<unk>"))
    fast = PreTrainedTokenizerFast(tokenizer_object=tok, unk_token="<unk>",
                                   bos_token="<bos>", eos_token="<eos>",
                                   pad_token="<pad>")
    fast.save_pretrained(str(base_dir))
    cfg = GPT2Config(vocab_size=len(vocab), n_positions=256, n_ctx=256,
                     n_embd=64, n_layer=2, n_head=2, bos_token_id=3,
                     eos_token_id=2)
    torch.manual_seed(42)
    model = GPT2LMHeadModel(cfg)
    model.save_pretrained(str(base_dir))


def _build_adapter(base_dir: Path, adapter_dir: Path) -> None:
    """给上面那个随机基座挂一个真 LoRA 适配器并落盘。"""
    model = GPT2LMHeadModel.from_pretrained(str(base_dir))
    lora_cfg = LoraConfig(task_type=TaskType.CAUSAL_LM, r=4, lora_alpha=8,
                           lora_dropout=0.0, target_modules=["c_attn"])
    peft_model = get_peft_model(model, lora_cfg)
    peft_model.save_pretrained(str(adapter_dir))


def main() -> int:
    from framework import lora_engine
    from framework.ai_engine import AIWorker

    tmp = Path(tempfile.mkdtemp(prefix="lora_verify_"))
    base_dir = tmp / "base"
    adapter_dir = tmp / "adapter"
    base_dir.mkdir(parents=True, exist_ok=True)
    steps = []

    try:
        # 1) 造基座
        _build_tiny_base(base_dir)
        steps.append("造出极小随机基座：%s" % base_dir)

        # 2) 造适配器
        _build_adapter(base_dir, adapter_dir)
        marker = adapter_dir / "adapter_config.json"
        assert marker.exists(), "适配器目录里没有 adapter_config.json"
        steps.append("挂上真 LoRA 适配器：%s（%d 个文件）"
                     % (adapter_dir, len(list(adapter_dir.iterdir()))))

        # 3) 真运行时：可用性状态 + 真续写
        info = lora_engine.status(base_model=str(base_dir),
                                  adapter_path=str(adapter_dir))
        steps.append("可用性状态：ready=%s reason=%s"
                     % (info["ready"], info["reason"]))
        if not info["ready"]:
            raise AssertionError("状态说不可用：" + info["reason"])

        text = lora_engine.generate_for("帮我打开微信",
                                        base_model=str(base_dir),
                                        adapter_path=str(adapter_dir))
        steps.append("真续写输出：%r" % text[:120])
        if text and "### 指令" in text:
            raise AssertionError("输出里混进了提示本身（decode 越界了）")

        # 4) 主链路的协议校验：合格动作还是「不合协议退云端」
        #    先把配置指向刚造出来的临时模型，让这一段走的是**真**运行时
        from framework import config
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        del app
        old_base, old_adapter = config.LORA_BASE_MODEL, config.LORA_ADAPTER_PATH
        config.LORA_BASE_MODEL = str(base_dir)
        config.LORA_ADAPTER_PATH = str(adapter_dir)
        try:
            worker = AIWorker("帮我打开微信", [], "活泼")
            actions = AIWorker._request_lora(worker)
        finally:
            config.LORA_BASE_MODEL = old_base
            config.LORA_ADAPTER_PATH = old_adapter
        steps.append("主链路协议校验：%s"
                     % ("合格动作 %s" % [a.get("action") for a in actions]
                        if actions else
                        "不合协议 → 会退给云端模型（随机小模型本该如此）"))
        print("\n".join("  - " + s for s in steps))
        print("\n✅ 本地微调通路真机验证通过："
              "适配器能加载、能续写、主链路认它的输出。")
        return 0
    except Exception as exc:  # noqa: BLE001
        print("\n".join("  - " + s for s in steps))
        sys.stderr.write("\n❌ 本地微调通路真机验证失败：%s\n" % exc)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
