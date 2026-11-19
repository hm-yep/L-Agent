# -*- coding: utf-8 -*-
"""本地微调（LoRA）接入产品推理链路的单测

**不装 torch/transformers/peft 也能跑**：运行时用假的模型/分词器替身，
依赖探测用 monkeypatch 换掉。守住四件事：

  * 提示词格式只有一处——训练脚本与产品推理共用 framework/lora_format.py；
  * 本地通路不可用（缺依赖/缺权重/加载失败）时如实降级，不假装答了；
  * 本地答得不合动作协议时退给云端，而不是拿废动作糊弄；
  * 开关与路径落盘，下次启动读回同样的值。
"""

import json
from pathlib import Path

import pytest

from framework import config as cfg
from framework import lora_engine, lora_format

REPO = Path(__file__).resolve().parents[1]
TRAIN_SCRIPT = REPO / "finetune" / "train_lora.py"
INFER_SCRIPT = REPO / "finetune" / "inference.py"

# 必须保留 QApplication 引用：写成 `QApplication.instance() or QApplication([])`
# 而不赋值，新建的 app 会被立刻 GC，之后构造 QWidget 会原生崩溃。
# （与 tests/test_workspace_window.py 同一条经验）
_APP = None


def _ensure_app():
    global _APP
    from PyQt5.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    return _APP


# ============================================================
# 一、提示词格式：单一真源
# ============================================================
def test_prompt_template_is_byte_identical_to_training_history():
    """模板必须与历史训练脚本逐字一致（改了它，所有历史权重失配）。"""
    prompt = lora_format.build_prompt("用户说：帮我打开微信。")
    assert prompt == ("### 指令：用户说：帮我打开微信。\n"
                      "### 输入：\n"
                      "### 回答：")
    text = lora_format.build_training_text("用户说：帮我打开微信。", "",
                                           '[{"action": "open_app"}]')
    assert text == ("### 指令：用户说：帮我打开微信。\n"
                    "### 输入：\n"
                    "### 回答：[{\"action\": \"open_app\"}]")


def test_instruction_prefix_follows_the_training_corpus():
    """训练集 30 条用「用户说：」、1 条用「用户问：」，推理时要认得这两种。"""
    assert lora_format.instruction_for("帮我打开微信") == "用户说：帮我打开微信"
    assert lora_format.instruction_for("你是谁？") == "用户问：你是谁？"
    assert lora_format.instruction_for("介绍一下自己") == "用户问：介绍一下自己"
    assert lora_format.instruction_for("") == ""


def test_training_corpus_prefixes_are_all_covered():
    """训练集里出现过的前缀，推理必须都能生成——不然有一半样本的格式对不上。"""
    rows = [json.loads(ln) for ln in
            (REPO / "finetune" / "data" / "train.jsonl")
            .read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows, "训练集空了"
    for item in rows:
        prefix = item["instruction"].split("：")[0] + "："
        assert prefix in (lora_format.SAY_PREFIX, lora_format.ASK_PREFIX), \
            "训练集出现了推理端不会生成的指令前缀：" + prefix


def test_train_script_uses_shared_format_instead_of_own_template():
    """训练脚本必须用共享模板；模板字面量不许再出现第二份。"""
    src = TRAIN_SCRIPT.read_text(encoding="utf-8")
    assert "from framework import lora_format" in src
    assert "lora_format.build_prompt(" in src
    assert "### 指令：" not in src, \
        "训练脚本里还留着一份自己的模板，格式迟早会跟产品侧漂"


def test_inference_script_uses_shared_format():
    """命令行推理脚本同样走共享模板（否则它和产品跑的不是一回事）。"""
    src = INFER_SCRIPT.read_text(encoding="utf-8")
    assert "from framework import lora_format" in src
    assert "lora_format.build_user_prompt(" in src


def test_train_script_masks_prompt_tokens_from_loss():
    """只有回答部分参与 loss：提示段必须是 -100。"""
    src = TRAIN_SCRIPT.read_text(encoding="utf-8")
    assert "[-100] * len(prompt_ids)" in src
    assert "enc[\"labels\"] = enc[\"input_ids\"].copy()" not in src, \
        "提示部分还在参与 loss，训练目标与推理用法对不上"


# ============================================================
# 二、运行时：可用性状态与诚实降级
# ============================================================
def test_status_reports_missing_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(lora_engine, "_missing_modules", lambda: ["torch"])
    info = lora_engine.status(base_model="m", adapter_path=str(tmp_path))
    assert info["ready"] is False
    assert "torch" in info["reason"]
    assert "requirements-finetune" in info["reason"]


def test_status_reports_missing_adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(lora_engine, "_missing_modules", lambda: [])
    info = lora_engine.status(base_model="m", adapter_path=str(tmp_path))
    assert info["ready"] is False
    assert "适配器" in info["reason"]


def test_status_is_ready_when_adapter_exists(tmp_path, monkeypatch):
    adapter = tmp_path / "output"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(lora_engine, "_missing_modules", lambda: [])
    info = lora_engine.status(base_model="m", adapter_path=str(adapter))
    assert info["ready"] is True
    assert info["base_model"] == "m"


def test_adapter_exists_needs_the_peft_marker(tmp_path):
    """光有目录不算数：得是训练出来的 PEFT 适配器。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    assert lora_engine.adapter_exists(str(empty)) is False
    real = tmp_path / "real"
    real.mkdir()
    (real / "adapter_config.json").write_text("{}", encoding="utf-8")
    assert lora_engine.adapter_exists(str(real)) is True


def test_load_failure_reason_is_readable(tmp_path, monkeypatch):
    """加载失败要给出能直接给人看的原因，不是堆栈。"""
    monkeypatch.setattr(lora_engine, "_missing_modules", lambda: ["peft"])
    rt = lora_engine.LoraRuntime("m", str(tmp_path))
    with pytest.raises(lora_engine.LoraUnavailable) as exc:
        rt.load()
    assert "缺少依赖" in str(exc.value)


def test_test_load_reports_reason_instead_of_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(lora_engine, "_missing_modules", lambda: ["peft"])
    ok, msg = lora_engine.test_load("m", str(tmp_path), "cpu")
    assert ok is False
    assert "缺少依赖" in msg


# ============================================================
# 三、运行时：推理行为（用替身，不装 torch）
# ============================================================
class _FakeIds(list):
    @property
    def shape(self):
        return (len(self),)


class _FakeBatch(dict):
    def to(self, _device):
        return self


class _FakeTokenizer:
    def __init__(self):
        self.seen = []

    def __call__(self, text, return_tensors=None, add_special_tokens=True):
        self.seen.append(text)
        return _FakeBatch(input_ids=_FakeIds([1, 2, 3]),
                          attention_mask=_FakeIds([1, 1, 1]))

    def decode(self, ids, skip_special_tokens=True):
        return "新token:" + ",".join(str(i) for i in ids)


class _FakeModel:
    device = "cpu"

    def __init__(self):
        self.kwargs = None

    def eval(self):
        return self

    def generate(self, **kwargs):
        self.kwargs = kwargs
        # 3 个提示 token + 2 个新 token
        return [[1, 2, 3, 41, 42]]


def test_generate_returns_only_new_tokens(tmp_path, monkeypatch):
    """只 decode 新生成的部分：把提示也 decode 出来是旧脚本的老毛病。"""
    tok = _FakeTokenizer()
    model = _FakeModel()
    rt = lora_engine.LoraRuntime("m", str(tmp_path))
    monkeypatch.setattr(rt, "load", lambda: (model, tok))
    text = rt.generate("### 指令：用户说：帮我打开微信。\n### 输入：\n### 回答：")
    assert text == "新token:41,42"
    assert "指令" not in text


def test_generate_for_sends_the_shared_prompt(tmp_path, monkeypatch):
    """产品推理送进模型的提示，必须是共享模板拼出来的那一份。"""
    tok = _FakeTokenizer()
    model = _FakeModel()
    rt = lora_engine.LoraRuntime("m", str(tmp_path))
    monkeypatch.setattr(rt, "load", lambda: (model, tok))
    monkeypatch.setattr(lora_engine, "get_runtime", lambda *a, **k: rt)
    lora_engine.generate_for("帮我打开微信")
    assert tok.seen[-1] == ("### 指令：用户说：帮我打开微信\n"
                            "### 输入：\n"
                            "### 回答：")


def test_generate_uses_greedy_decoding_by_default(tmp_path, monkeypatch):
    """小模型吐结构化 JSON，贪心比采样稳。"""
    tok = _FakeTokenizer()
    model = _FakeModel()
    rt = lora_engine.LoraRuntime("m", str(tmp_path))
    monkeypatch.setattr(rt, "load", lambda: (model, tok))
    monkeypatch.setattr(cfg, "LORA_TEMPERATURE", 0.0)
    rt.generate("x")
    assert model.kwargs["do_sample"] is False


# ============================================================
# 四、主链路路由：本地优先，答不好就退
# ============================================================
def _worker():
    from framework.ai_engine import AIWorker
    _ensure_app()
    return AIWorker("帮我打开微信", [], "活泼")


def test_local_provider_wins_when_enabled(monkeypatch):
    """开了本地微调：本地先答，云端这一步根本不该被调用。"""
    monkeypatch.setattr(cfg, "LORA_ENABLED", True)
    monkeypatch.setattr(
        lora_engine, "generate_for",
        lambda *a, **k: '[{"action": "show_bubble", "text": "遵命。"}, '
                        '{"action": "open_app", "app": "微信"}]')

    def _boom(*a, **k):
        raise AssertionError("本地通路答得上来，不该去调云端")

    monkeypatch.setattr("framework.ai_engine.AIWorker._request_deepseek", _boom)
    actions = _worker()._request_model()
    assert [a["action"] for a in actions] == ["show_bubble", "open_app"]


def test_local_garbage_falls_back_to_cloud(monkeypatch):
    """本地吐了非法动作（不在动作池里）就退给云端，不拿废动作糊弄。"""
    monkeypatch.setattr(cfg, "LORA_ENABLED", True)
    monkeypatch.setattr(lora_engine, "generate_for",
                        lambda *a, **k: '[{"action": "not_a_real_action"}]')
    monkeypatch.setattr("framework.ai_engine.AIWorker._request_deepseek",
                        lambda self: [{"action": "show_bubble", "text": "云端"}])
    actions = _worker()._request_model()
    assert actions == [{"action": "show_bubble", "text": "云端"}]


def test_local_broken_json_falls_back_to_cloud(monkeypatch):
    """本地输出根本不是 JSON，也退给云端。"""
    monkeypatch.setattr(cfg, "LORA_ENABLED", True)
    monkeypatch.setattr(lora_engine, "generate_for",
                        lambda *a, **k: "{这不是 JSON")
    monkeypatch.setattr("framework.ai_engine.AIWorker._request_deepseek",
                        lambda self: [{"action": "show_bubble", "text": "云端"}])
    assert _worker()._request_model() == \
        [{"action": "show_bubble", "text": "云端"}]


def test_local_prose_is_accepted_as_an_answer(monkeypatch):
    """确实是一段人话就当回答收下（不是 JSON 不等于不可用）。"""
    monkeypatch.setattr(cfg, "LORA_ENABLED", True)
    monkeypatch.setattr(lora_engine, "generate_for",
                        lambda *a, **k: "我是凌零，在的。")
    monkeypatch.setattr("framework.ai_engine.AIWorker._request_deepseek",
                        lambda self: [{"action": "show_bubble", "text": "云端"}])
    actions = _worker()._request_model()
    assert actions == [{"action": "show_bubble", "text": "我是凌零，在的。"}]


def test_local_crash_falls_back_to_cloud(monkeypatch):
    """本地通路抛异常不许拖垮主链路，退给云端继续。"""
    monkeypatch.setattr(cfg, "LORA_ENABLED", True)

    def _boom(*a, **k):
        raise RuntimeError("CUDA 炸了")

    monkeypatch.setattr(lora_engine, "generate_for", _boom)
    monkeypatch.setattr("framework.ai_engine.AIWorker._request_deepseek",
                        lambda self: [{"action": "show_bubble", "text": "云端"}])
    assert _worker()._request_model() == \
        [{"action": "show_bubble", "text": "云端"}]


def test_cloud_is_used_when_local_is_off(monkeypatch):
    """没开本地微调：还是原来的云端通路。"""
    monkeypatch.setattr(cfg, "LORA_ENABLED", False)

    def _boom(*a, **k):
        raise AssertionError("没开本地微调，不该去问本地")

    monkeypatch.setattr(lora_engine, "generate_for", _boom)
    monkeypatch.setattr("framework.ai_engine.AIWorker._request_deepseek",
                        lambda self: [{"action": "show_bubble", "text": "云端"}])
    assert _worker()._request_model() == \
        [{"action": "show_bubble", "text": "云端"}]


# ============================================================
# 五、配置：默认关、落盘、下次启动读回
# ============================================================
def test_lora_is_off_by_default():
    """默认关闭：没装 torch 的机器不该被本地通路拽住。"""
    src = (REPO / "framework" / "config.py").read_text(encoding="utf-8")
    assert "LORA_ENABLED = False" in src


def test_lora_settings_roundtrip(tmp_path, monkeypatch):
    """存—读回环：开关、基座、适配器路径、设备都要读回同样的值。"""
    monkeypatch.setattr(cfg, "_user_settings_path",
                        lambda: str(tmp_path / "user_settings.json"))
    monkeypatch.setattr(cfg, "LORA_ENABLED", False)
    monkeypatch.setattr(cfg, "LORA_BASE_MODEL", "x")
    monkeypatch.setattr(cfg, "LORA_ADAPTER_PATH", "y")
    monkeypatch.setattr(cfg, "LORA_DEVICE", "z")
    cfg.save_user_settings(lora_enabled=True, lora_base_model="Qwen/Qwen2.5",
                           lora_adapter_path="finetune/output",
                           lora_device="cpu")
    cfg.apply_user_settings()
    assert cfg.LORA_ENABLED is True
    assert cfg.LORA_BASE_MODEL == "Qwen/Qwen2.5"
    assert cfg.LORA_ADAPTER_PATH == "finetune/output"
    assert cfg.LORA_DEVICE == "cpu"


def test_settings_dialog_has_real_lora_controls():
    """设置界面里那几个是真控件：能勾、能填、存盘时真把值发出去。"""
    from ui.ui_manager import SettingsDialog
    _ensure_app()
    dlg = SettingsDialog()
    assert dlg._lora_enabled.isCheckable()
    assert dlg._lora_base.text() == cfg.LORA_BASE_MODEL
    assert dlg._lora_adapter.text() == cfg.LORA_ADAPTER_PATH
    assert dlg._lora_device.currentText() == cfg.LORA_DEVICE

    seen = {}

    def _record(enabled, base, adapter, device):
        seen.update(enabled=enabled, base=base, adapter=adapter, device=device)

    dlg.lora_config_changed.connect(_record)
    dlg._lora_enabled.setChecked(True)
    dlg._lora_base.setText("Qwen/Qwen2.5")
    dlg._lora_adapter.setText("finetune/output")
    dlg._lora_device.setCurrentText("cpu")
    dlg._save()
    assert seen.get("enabled") is True
    assert seen.get("base") == "Qwen/Qwen2.5"
    assert seen.get("adapter") == "finetune/output"
    assert seen.get("device") == "cpu"
    dlg.close()


def test_framework_set_lora_config_persists(tmp_path, monkeypatch):
    """主链路入口：改配置 + 落盘 + 状态文案。"""
    from framework.core import Framework
    _ensure_app()
    monkeypatch.setattr(cfg, "_user_settings_path",
                        lambda: str(tmp_path / "user_settings.json"))
    fw = Framework.instance()
    fw.set_lora_config(True, "Qwen/Qwen2.5", "finetune/output", "cpu")
    assert cfg.LORA_ENABLED is True
    assert cfg.LORA_BASE_MODEL == "Qwen/Qwen2.5"
    assert "已启用" in fw.lora_status_text()
    saved = json.loads((tmp_path / "user_settings.json").read_text(
        encoding="utf-8"))
    assert saved["lora_enabled"] is True
    assert saved["lora_adapter_path"] == "finetune/output"
    fw.set_lora_config(False, None, None, None)
    assert cfg.LORA_ENABLED is False
    assert "未启用" in fw.lora_status_text()
