# -*- coding: utf-8 -*-
"""人设提示词的共享约束：防止三套提示词各自漂移。

桌面版（framework/ai_engine.py，输出 JSON 动作）、网页 API（api/server.py，
输出纯文本）、多智能体编排（framework/multi_agent.py，输出三行协议）的
输出格式完全不同，强行共用整段提示词只会让格式规则互相污染。

因此这里只共享**不可漂移的最小约束**：
  * PERSONA_KEYS：每一套提示词都必须出现的关键词；
  * PERSONA_BRIEF：给原本没有身份段的多智能体编排用的一句话开场；
  * IDENTITY_SENTENCE：自我介绍的标准句式。

tools/check_contracts.py 在 CI 里逐一比对三套提示词，任一套漏掉关键句即失败。
"""

PERSONA_KEYS = ("凌零", "桌宠", "宠物")

PERSONA_BRIEF = (
    "你是「凌零」桌面 AI 数字员工体系中的一员；"
    "绝不自称\"桌宠\"\"宠物\"或其他名字，用中文回答，语气干脆有担当。"
    "禁止软萌语气词（呀/啦/哦/哟/嘛/嗯嗯）和波浪号。\n\n"
)

IDENTITY_SENTENCE = "我是凌零，你的桌面AI助手"
