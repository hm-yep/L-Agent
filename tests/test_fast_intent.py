# -*- coding: utf-8 -*-
"""单元测试：确定性短指令路由（撤销 / 存技能 / 技能列表）

这三条话必须**不进模型**就能执行：API 欠费时它们照样好用，而且"撤销"这种
事一旦让模型转述一遍就可能变成它口头答应、实际没动手。
风险也很具体：配置里的词写得长于门限、或者同时出现在两张表里，路由就会
静默失灵（话永远走模型那条路，测试还全绿）。所以这里测的是词表与判据是否
对得上，不测"有没有这段代码"。
"""

import pytest

from framework import config
from framework.core import Framework
from framework.utils import normalize_intent

LISTS = {"撤销": config.UNDO_WORDS,
         "存技能": config.SKILL_SAVE_WORDS,
         "技能列表": config.SKILL_LIST_WORDS}


class TestWordLists:
    @pytest.mark.parametrize("which", sorted(LISTS))
    def test_every_word_routes_to_itself(self, which):
        """配置里写的每个词，都得被自己的判据认出来"""
        for word in LISTS[which]:
            assert Framework._fast_intent(None, word, LISTS[which]), word

    @pytest.mark.parametrize("which", sorted(LISTS))
    def test_every_word_is_short_enough_to_reach_the_gate(self, which):
        """send_command 只在不长于门限时走快路径：超长词等于写了条死规则"""
        for word in LISTS[which]:
            assert len(normalize_intent(word)) <= config.SHORT_CMD_MAX_LEN, word

    def test_a_skill_name_can_always_be_spoken(self):
        """技能名最长 SKILL_NAME_MAX，必须落在直放门限里，否则存了也叫不动"""
        assert config.SKILL_NAME_MAX <= config.SKILL_REPLAY_MAX_LEN
        assert config.SHORT_CMD_MAX_LEN < config.SKILL_REPLAY_MAX_LEN

    def test_the_three_triggers_never_collide(self):
        """两张表重叠时先判的那张会偷走后面那条命令"""
        names = sorted(LISTS)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                assert not (set(LISTS[a]) & set(LISTS[b])), (a, b)


class TestMatcher:
    @pytest.mark.parametrize("text", [
        "撤销", "撤销！", " 撤 销 ", "UNDO", "undo.", "恢复原样。"])
    def test_punctuation_and_case_do_not_break_it(self, text):
        assert Framework._fast_intent(None, text, config.UNDO_WORDS)

    @pytest.mark.parametrize("text", [
        "今天天气不错", "帮我把音量调大一点", "写一份季度总结"])
    def test_ordinary_chatter_is_not_a_command(self, text):
        for words in LISTS.values():
            assert not Framework._fast_intent(None, text, words), text

    def test_empty_sentence_is_never_a_command(self):
        """空串规范化后仍是空：任何词都别想靠"包含空串"命中"""
        assert not Framework._fast_intent(None, "  ，。 ", config.UNDO_WORDS)

    def test_matcher_alone_needs_the_length_gate(self):
        """判据本身只看"有没有这个词"，长句靠 send_command 的长度门限挡住。

        这条测试盯的是那个门限确实存在：删掉它之后，一句本该交给模型的长任务
        会被当成撤销指令吃掉。门限本身在接线冒烟脚本里验，不在这里。
        """
        text = "帮我把刚才那条待办撤回，然后重新加一条明天上午的"
        assert len(text) > config.SHORT_CMD_MAX_LEN   # 门限挡住它
        assert Framework._fast_intent(None, text, config.UNDO_WORDS)

    def test_the_real_phrase_is_a_short_command(self):
        assert Framework._fast_intent(None, "存成技能", config.SKILL_SAVE_WORDS)
        assert not Framework._fast_intent(None, "存成技能", config.UNDO_WORDS)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
