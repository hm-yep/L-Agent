# -*- coding: utf-8 -*-
"""基座加载顺序：先吃本地缓存，失败再联网。

单独成文件而不是塞进 `test_lora_engine.py`：那个文件会拉起 QApplication 与
Framework 单例，而这里测的是纯加载顺序，不需要任何 Qt 东西。混在一起时
一旦失败，还得分清是「加载逻辑坏了」还是「窗口构造崩了」。
"""

from pathlib import Path

from framework import lora_engine

REPO = Path(__file__).resolve().parents[1]
TRAIN_SCRIPT = REPO / "finetune" / "train_lora.py"


class _FakeLoader:
    """假的分词器/模型加载器：记录每次调用收到的参数。"""

    def __init__(self, cache_ok=True):
        self.calls = []
        self.cache_ok = cache_ok

    def from_pretrained(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if kwargs.get("local_files_only") and not self.cache_ok:
            raise OSError("本地缓存里没有这份权重")
        return "loaded:%s" % name


def test_cached_loader_tries_local_cache_first():
    """缓存齐全时零网络：只发一次 local_files_only 的调用。"""
    loader = _FakeLoader()
    out = lora_engine.from_pretrained_cached(
        loader, "Qwen/Qwen2.5-1.5B-Instruct", trust_remote_code=True)
    assert out == "loaded:Qwen/Qwen2.5-1.5B-Instruct"
    assert loader.calls == [("Qwen/Qwen2.5-1.5B-Instruct",
                             {"local_files_only": True,
                              "trust_remote_code": True})]


def test_cached_loader_falls_back_to_network():
    """缓存缺失时才联网，且原参数一个不丢。"""
    loader = _FakeLoader(cache_ok=False)
    out = lora_engine.from_pretrained_cached(
        loader, "Qwen/Qwen2.5-1.5B-Instruct", trust_remote_code=True)
    assert out == "loaded:Qwen/Qwen2.5-1.5B-Instruct"
    flags = [c[1].get("local_files_only") for c in loader.calls]
    assert flags == [True, None], "应当先纯本地、失败后再联网"
    assert loader.calls[-1][1]["trust_remote_code"] is True


def test_train_script_loads_base_through_cached_loader():
    """训练脚本也走同一份加载逻辑：本机 hf.co 被挡时不该起不来。"""
    src = TRAIN_SCRIPT.read_text(encoding="utf-8")
    assert "from_pretrained_cached" in src
    assert "AutoModelForCausalLM.from_pretrained(" not in src
    assert "AutoTokenizer.from_pretrained(" not in src
