# -*- coding: utf-8 -*-
"""LoRA 提示词格式的单一真源：训练与推理必须用同一份模板。

为什么值得单独一个模块：微调最怕「训的是 A、跑的是 B」。训练脚本
（finetune/train_lora.py）与产品推理（framework/lora_engine.py）原来
各写一遍模板，只要有一处改了格式（多一个空格、少一段标题），微调就白做了
——而且不会报错，只会让效果莫名其妙地差。

本模块只依赖标准库：训练脚本 import 它、产品侧也 import 它，两边跑在同一个
仓库里，格式只有这一份。**不要往这里加第三方依赖**（训练脚本在没有 torch
的机器上也要能 import）。

格式（与历史训练脚本逐字一致；改这里等于让所有历史权重失配）：

    训练文本：
        ### 指令：{instruction}
        ### 输入：{input}
        ### 回答：{output}

    推理提示（同一模板，末尾留白让模型续写）：
        ### 指令：{instruction}
        ### 输入：{input}
        ### 回答：
"""

TRAIN_TEMPLATE = "### 指令：{instruction}\n### 输入：{input}\n### 回答：{output}"
PROMPT_TEMPLATE = "### 指令：{instruction}\n### 输入：{input}\n### 回答："

# 指令前缀：训练集 31 条统一用「用户说：」
#
# 这里曾经按「像不像提问」猜「用户问：/用户说：」，结果与训练集的标注对不上
# ——训练集里「在吗？」「我的画图工具在哪里？」「你今天都干了啥？」三条带问号
# 却标的是「用户说：」，而「你是谁？介绍一下自己。」标的是「用户问：」。规则
# 猜出来的前缀因此有 4 条与训练分布相反，等于又一次「训的是 A、跑的是 B」。
# 前缀本身不携带语义（模型要理解的是冒号后面那句话），统一成主前缀最稳。
SAY_PREFIX = "用户说："


def build_training_text(instruction: str, input_text: str = "",
                        output: str = "") -> str:
    """训练样本文本：训练脚本拼样本时只应走这里。"""
    return TRAIN_TEMPLATE.format(instruction=instruction or "",
                                 input=input_text or "",
                                 output=output or "")


def build_prompt(instruction: str, input_text: str = "") -> str:
    """推理提示：与训练文本同一模板，去掉回答段。"""
    return PROMPT_TEMPLATE.format(instruction=instruction or "",
                                  input=input_text or "")


def instruction_for(user_text: str, input_text: str = "") -> str:
    """把用户这句话变成训练集同款的指令。

    前缀恒为 `SAY_PREFIX`，不再猜测问句——猜测的规则与训练集标注无法对齐，
    见模块里 SAY_PREFIX 处的说明。
    """
    t = (user_text or "").strip()
    if not t:
        return ""
    return SAY_PREFIX + t


def build_user_prompt(user_text: str, input_text: str = "") -> str:
    """产品推理入口：从一句用户输入直接得到可送进模型的提示。"""
    return build_prompt(instruction_for(user_text, input_text), input_text)
