# -*- coding: utf-8 -*-
"""本地 LoRA 推理运行时：让微调权重真正参与产品推理。

定位（边界写在最前面，别往大了说）：
  * 这是**主链路的一个 provider**，不是旁路脚本：framework/ai_engine.py 在
    「本地微调」开启时先问这里要答案，拿到合乎动作协议的结果才继续走；
  * 只处理「一句话 -> 动作序列」这类短指令——训练集 31 条样本覆盖的就是
    这一类。上下文（长期记忆 / 检索资料 / 人设提示词）不进本地模型，
    它拿到的只有当前这一句用户输入；
  * 缺依赖、缺权重、加载失败——一律如实降级，绝不假装「本地模型答了」。
    调用方拿到的要么是动作列表，要么是异常。

依赖是可选的（requirements-finetune.txt：torch / transformers / peft）。
这三个包很重，所以**全部在函数里懒加载**：import 本模块不会拉起 torch，
判据层、契约检查、单元测试都不会被拖下水。
"""

import importlib.util
import os
import threading
import time

from framework import lora_format
from framework.utils import logger

# 本地微调通路需要的第三方包（缺一个都跑不起来）
REQUIRED_MODULES = ("torch", "transformers", "peft")
# PEFT 适配器目录的标志文件：有它才说明这确实是训练出来的适配器
ADAPTER_MARKER = "adapter_config.json"

_lock = threading.Lock()
_runtime = None


class LoraUnavailable(RuntimeError):
    """本地 LoRA 不可用：原因要能直接给人看（缺包 / 缺权重 / 加载失败）。"""


# ------------------------------------------------------------
# 配置读取（延迟 import，测试可以先换掉 framework.config）
# ------------------------------------------------------------
def _cfg(name: str, default=None):
    from framework import config
    return getattr(config, name, default)


def _resolve(path: str) -> str:
    """相对路径按当前工作目录解析（适配器默认在仓库的 finetune/output）。"""
    return os.path.abspath(os.path.expanduser(path or ""))


def _missing_modules() -> list:
    """缺哪些依赖（只查存在性，不 import——import torch 要好几秒）。"""
    missing = []
    for name in REQUIRED_MODULES:
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ValueError):
            missing.append(name)
    return missing


def adapter_exists(adapter_path: str) -> bool:
    """适配器路径是不是一个真的 PEFT 适配器目录。"""
    p = _resolve(adapter_path)
    return os.path.isdir(p) and os.path.exists(os.path.join(p, ADAPTER_MARKER))


def from_pretrained_cached(loader, name: str, **kwargs):
    """先纯本地加载，失败再交给 loader 自己去联网。

    为什么需要这一步：`from_pretrained` 默认会先向 huggingface.co 发元数据
    请求校验缓存，网络被墙/被代理拦（本项目开发机上就是 502）时，**即使
    权重已经完整落在本地缓存里也会直接抛错**。先试 `local_files_only=True`
    能让「缓存齐全」这条常见路径零网络；缓存缺失时再回落到原来的联网行为，
    首次下载不受影响。
    """
    try:
        return loader.from_pretrained(name, local_files_only=True, **kwargs)
    except Exception as e:      # noqa: BLE001 缓存不全就走联网，原因只记日志
        logger.info(f"[LoRA] 本地缓存不可直接用（{e}），改为联网加载 {name}")
        return loader.from_pretrained(name, **kwargs)


def status(base_model: str = None, adapter_path: str = None,
           device: str = None) -> dict:
    """本地微调通路的状态（不加载模型、不 import torch，可以随便调）。"""
    base = base_model or _cfg("LORA_BASE_MODEL", "")
    adapter = adapter_path or _cfg("LORA_ADAPTER_PATH", "")
    dev = device or _cfg("LORA_DEVICE", "auto")
    info = {
        "enabled": bool(_cfg("LORA_ENABLED", False)),
        "base_model": base,
        "adapter": adapter,
        "device": dev,
        "loaded": _runtime is not None,
        "ready": False,
        "reason": "",
    }
    missing = _missing_modules()
    if missing:
        info["reason"] = ("缺少依赖 %s（pip install -r requirements-finetune.txt）"
                          % "、".join(missing))
        return info
    if not adapter_exists(adapter):
        info["reason"] = ("适配器权重不存在或不是 PEFT 目录：%s"
                          "（先跑 python finetune/train_lora.py）" % adapter)
        return info
    info["ready"] = True
    info["reason"] = "就绪（首次推理要加载权重，可能要几十秒）"
    return info


class LoraRuntime:
    """一次加载、多次推理：基座模型 + PEFT 适配器。"""

    def __init__(self, base_model: str, adapter_path: str, device: str = "auto"):
        self.base_model = base_model
        self.adapter_path = adapter_path
        self.device = device or "auto"
        self.model = None
        self.tokenizer = None
        self._load_lock = threading.Lock()

    # -- 加载 ----------------------------------------------------
    def _resolve_device(self, torch_mod) -> str:
        want = (self.device or "auto").lower()
        if want == "auto":
            try:
                return "cuda" if torch_mod.cuda.is_available() else "cpu"
            except Exception:       # noqa: BLE001 显卡探测失败就当 CPU 用
                return "cpu"
        return want

    def load(self):
        """加载基座 + 适配器；失败抛 LoraUnavailable（原因可直接展示）。"""
        if self.model is not None:
            return self.model, self.tokenizer
        with self._load_lock:
            if self.model is not None:      # 另一个线程已经加载好了
                return self.model, self.tokenizer
            missing = _missing_modules()
            if missing:
                raise LoraUnavailable(
                    "缺少依赖：%s（pip install -r requirements-finetune.txt）"
                    % "、".join(missing))
            if not adapter_exists(self.adapter_path):
                raise LoraUnavailable(
                    "适配器权重不存在或不是 PEFT 目录：%s"
                    % self.adapter_path)
            try:
                import torch
                from peft import PeftModel
                from transformers import AutoModelForCausalLM, AutoTokenizer
            except ImportError as e:
                raise LoraUnavailable("依赖导入失败：%s" % e) from e

            device = self._resolve_device(torch)
            try:
                tokenizer = from_pretrained_cached(
                    AutoTokenizer, self.base_model, trust_remote_code=True)
                if tokenizer.pad_token is None:
                    tokenizer.pad_token = tokenizer.eos_token
                base = from_pretrained_cached(
                    AutoModelForCausalLM, self.base_model, trust_remote_code=True)
                base = base.to(device)
                model = PeftModel.from_pretrained(
                    base, _resolve(self.adapter_path))
                model.eval()
            except Exception as e:      # noqa: BLE001 加载失败要说人话
                raise LoraUnavailable(
                    "加载基座/适配器失败：%s" % e) from e

            self.model, self.tokenizer = model, tokenizer
            logger.info(f"[LoRA] 已加载 基座={self.base_model} "
                        f"适配器={self.adapter_path} 设备={device}")
            return model, tokenizer

    # -- 推理 ----------------------------------------------------
    def generate(self, prompt: str, max_new_tokens: int = None) -> str:
        """按提示续写，返回**新生成**的那段文本（不含提示本身）。

        这里不 import torch：transformers 的 generate 内部自带 no_grad，
        多一次 import 就多几秒冷启动。
        """
        model, tokenizer = self.load()
        limit = int(max_new_tokens or _cfg("LORA_MAX_NEW_TOKENS", 256) or 256)
        temperature = float(_cfg("LORA_TEMPERATURE", 0.0) or 0.0)
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        gen_kwargs = {"max_new_tokens": limit}
        if temperature > 0:
            gen_kwargs.update(do_sample=True, temperature=temperature, top_p=0.9)
        else:
            # 小模型要吐结构化 JSON，贪心解码比采样更稳
            gen_kwargs.update(do_sample=False)
        started = time.time()
        out = model.generate(**inputs, **gen_kwargs)
        # 只取新生成的部分：历史脚本把提示也一起 decode 出来了
        new_tokens = out[0][inputs["input_ids"].shape[-1]:]
        text = tokenizer.decode(new_tokens, skip_special_tokens=True)
        budget = int(_cfg("LORA_INFERENCE_TIMEOUT", 120) or 120)
        elapsed = time.time() - started
        if elapsed > budget:
            logger.warning(f"[LoRA] 本地推理耗时 {elapsed:.1f} 秒，"
                           f"超过预算 {budget} 秒")
        return text

    def unload(self):
        """释放权重（关掉开关时腾出内存）。"""
        self.model = None
        self.tokenizer = None


# ------------------------------------------------------------
# 模块级入口：主链路只跟这几个函数打交道
# ------------------------------------------------------------
def get_runtime(base_model: str = None, adapter_path: str = None,
                device: str = None) -> LoraRuntime:
    """取（必要时重建）运行时；配置变了就换掉旧的。"""
    global _runtime
    base = base_model or _cfg("LORA_BASE_MODEL", "")
    adapter = adapter_path or _cfg("LORA_ADAPTER_PATH", "")
    dev = device or _cfg("LORA_DEVICE", "auto")
    with _lock:
        if _runtime is None or _runtime.base_model != base \
                or _runtime.adapter_path != adapter \
                or _runtime.device != dev:
            if _runtime is not None:
                _runtime.unload()
            _runtime = LoraRuntime(base, adapter, dev)
        return _runtime


def unload_runtime():
    """把运行时整个撤掉（关开关用）。"""
    global _runtime
    with _lock:
        if _runtime is not None:
            _runtime.unload()
            _runtime = None


def generate_for(user_text: str, input_text: str = "",
                 base_model: str = None, adapter_path: str = None,
                 device: str = None, max_new_tokens: int = None) -> str:
    """主链路入口：一句用户输入 -> 模型续写出来的文本。

    提示词格式走 framework/lora_format.py（与训练脚本同一份模板）。
    """
    prompt = lora_format.build_user_prompt(user_text, input_text)
    rt = get_runtime(base_model, adapter_path, device)
    return rt.generate(prompt, max_new_tokens=max_new_tokens)


def test_load(base_model: str = None, adapter_path: str = None,
              device: str = None) -> tuple:
    """设置界面的「测试加载」：真加载一次，把结果如实回报。"""
    try:
        rt = get_runtime(base_model, adapter_path, device)
        started = time.time()
        rt.load()
        return True, "加载成功（%.1f 秒）· %s" % (time.time() - started,
                                              rt.base_model)
    except LoraUnavailable as e:
        return False, str(e)
    except Exception as e:      # noqa: BLE001
        return False, "加载失败：%s" % e
