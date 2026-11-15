# -*- coding: utf-8 -*-
"""单元测试：技能库（一次学会）—— 抽槽、拒收、重放确定性、持久化

这里最要紧的一条是 TestReplayDeterminism：技能的全部卖点是"下次别再让我
说一遍"，而这句话只有在**重放完全不进模型**时才是真的。所以我不测"看起来
像存下来了"，直接测同一份技能 + 同一组槽值跑 20 遍逐字节相等，并且跑的过程
里 AI 层被整体拦死（一旦有代码偷偷去调模型，这条就红）。
"""

import json
import os

import pytest

from framework import config, skill_store
from framework.skill_store import SkillStore


@pytest.fixture
def store(tmp_path):
    return SkillStore(path=str(tmp_path / "skills.json"))


class TestSlotting:
    def test_path_app_topic_each_get_their_own_slot(self):
        skill = skill_store.abstract([
            {"action": "analyze_data", "path": "D:/下载/三月发票.csv"},
            {"action": "open_app", "app": "微信"},
            {"action": "web_search", "query": "竞品动态"},
        ])
        assert skill["steps"] == [
            {"action": "analyze_data", "path": "{路径}"},
            {"action": "open_app", "app": "{应用}"},
            {"action": "web_search", "query": "{主题}"},
        ]
        assert skill["slots"] == ["路径", "应用", "主题"]
        assert skill["values"]["路径"] == "D:/下载/三月发票.csv"

    def test_the_same_literal_shares_one_slot(self):
        skill = skill_store.abstract([
            {"action": "read_doc", "path": "C:/a/排班表.xlsx"},
            {"action": "analyze_data", "path": "C:/a/排班表.xlsx"},
        ])
        assert skill["steps"][0]["path"] == skill["steps"][1]["path"] == "{路径}"
        assert skill["slots"] == ["路径"]

    def test_two_different_paths_become_two_slots(self):
        skill = skill_store.abstract([
            {"action": "read_doc", "path": "C:/a.xlsx"},
            {"action": "analyze_data", "path": "D:/b.csv"},
        ])
        assert skill["slots"] == ["路径", "路径2"]

    def test_a_value_that_is_only_a_date_becomes_a_date_slot(self):
        skill = skill_store.abstract(
            [{"action": "web_search", "query": "三月"}])
        assert skill["steps"][0]["query"] == "{日期}"

    def test_a_date_inside_a_longer_topic_stays_a_topic_slot(self):
        """"三月的发票"抽成日期会把"发票"丢掉——那种值该走主题。"""
        skill = skill_store.abstract(
            [{"action": "web_search", "query": "三月的发票"}])
        assert skill["steps"][0]["query"] == "{主题}"

    def test_non_string_params_are_kept_verbatim(self):
        skill = skill_store.abstract(
            [{"action": "set_volume", "volume": 30}])
        assert skill["steps"] == [{"action": "set_volume", "volume": 30}]
        assert skill["slots"] == []

    def test_theatre_actions_are_dropped_from_a_skill(self):
        """think / show_bubble 是表演，进技能只会让重放慢得像卡了。"""
        skill = skill_store.abstract([
            {"action": "think"},
            {"action": "show_bubble", "text": "好的"},
            {"action": "todo_add", "text": "交周报"},
        ])
        assert skill["steps"] == [{"action": "todo_add", "text": "{主题}"}]

    def test_consecutive_duplicates_collapse(self):
        skill = skill_store.abstract([
            {"action": "set_volume", "volume": 20},
            {"action": "set_volume", "volume": 20},
        ])
        assert len(skill["steps"]) == 1


class TestRefusal:
    def test_generated_document_is_never_saved_as_a_skill(self):
        """create_doc 的内容是上次替用户写的，重放＝把旧报告再发一遍。"""
        ok, why = skill_store.skillable([
            {"action": "web_search", "query": "科技新闻"},
            {"action": "create_doc", "doc_type": "ppt", "content": {"x": 1}},
        ])
        assert not ok and "原样重放" in why

    def test_power_actions_are_never_saved(self):
        for action in ("shutdown", "restart", "lock"):
            ok, why = skill_store.skillable(
                [{"action": action}, {"action": "todo_add", "text": "x"}])
            assert not ok and "真机器" in why

    def test_single_step_is_not_worth_a_skill(self):
        ok, why = skill_store.skillable(
            [{"action": "set_volume", "volume": 10}])
        assert not ok and "就一步" in why

    def test_a_messy_turn_is_refused(self):
        steps = [{"action": "todo_add", "text": f"第{i}件"}
                 for i in range(config.SKILL_MAX_STEPS + 1)]
        ok, why = skill_store.skillable(steps)
        assert not ok and "太杂" in why

    def test_unknown_action_is_refused_not_guessed(self):
        ok, why = skill_store.skillable([
            {"action": "todo_add", "text": "a"},
            {"action": "some_new_action", "x": 1},
        ])
        assert not ok and "不可靠" in why

    def test_store_add_returns_the_reason(self, store):
        item, why = store.add("做个报告", [
            {"action": "create_doc", "doc_type": "doc"}])
        assert item is None and why


class TestFill:
    def test_round_trip_rebuilds_the_original_steps(self):
        original = [
            {"action": "analyze_data", "path": "D:/x/发票.csv"},
            {"action": "remind", "text": "交税", "seconds": 60},
        ]
        skill = skill_store.abstract(original)
        steps, missing = skill_store.fill(
            skill, {"路径": "D:/x/发票.csv", "主题": "交税", "seconds": 60})
        assert missing == []
        assert steps[0] == {"action": "analyze_data", "path": "D:/x/发票.csv"}
        # remind 的 text 抽成了主题槽，seconds 是数字原样保留
        assert steps[1] == {"action": "remind", "text": "交税", "seconds": 60}

    def test_missing_slot_refuses_the_whole_skill(self):
        """缺一个槽就整条不跑：静默少跑一步比不跑更难被发现。"""
        skill = {"steps": [{"action": "read_doc", "path": "{路径}"},
                           {"action": "todo_add", "text": "{主题}"}]}
        steps, missing = skill_store.fill(skill, {"主题": "交周报"})
        assert steps == [] and missing == ["路径"]

    def test_empty_value_counts_as_missing(self):
        skill = {"steps": [{"action": "web_search", "query": "{主题}"}]}
        steps, missing = skill_store.fill(skill, {"主题": "   "})
        assert steps == [] and missing == ["主题"]

    def test_previous_value_is_not_used_as_a_default(self):
        """values 只能用来提示"上次是这个"，不许当默认值自动填进去。"""
        skill = skill_store.abstract(
            [{"action": "read_doc", "path": "C:/上周.xlsx"},
             {"action": "todo_add", "text": "看一眼"}])
        steps, missing = skill_store.fill(skill, {})
        assert steps == [] and "路径" in missing
        assert skill["values"]["路径"] == "C:/上周.xlsx"   # 只作为提示存在


class TestMatchValues:
    def test_path_app_date_topic_are_mined_from_a_sentence(self):
        got = skill_store.match_values(
            "打开微信，查一下「竞品动态」，再看 D:/报表/三月.csv，明天提醒我",
            ["应用", "主题", "路径", "日期"])
        assert got["应用"] == "微信"
        assert got["主题"] == "竞品动态"
        assert got["路径"] == "D:/报表/三月.csv"
        assert got["日期"] == "明天"

    def test_nothing_is_invented_when_the_sentence_has_no_path(self):
        got = skill_store.match_values("再查一次竞品动态", ["路径", "主题"])
        assert "路径" not in got and got["主题"] == "竞品动态"

    def test_second_slot_of_the_same_kind_is_left_missing(self):
        """一句话里抠不出两份路径：宁可问，也不把同一个值塞进两个槽。"""
        got = skill_store.match_values("分析 C:/a.csv", ["路径", "路径2"])
        assert got.get("路径") == "C:/a.csv" and "路径2" not in got


class TestReplayDeterminism:
    def test_same_skill_same_values_is_byte_identical_twenty_times(self):
        skill = skill_store.abstract([
            {"action": "web_search", "query": "三月销量"},
            {"action": "analyze_data", "path": "D:/数据/q1.csv"},
            {"action": "todo_add", "text": "写周报"},
        ])
        values = {"主题": "四月销量", "路径": "D:/数据/q2.csv"}
        first = json.dumps(skill_store.fill(skill, values), ensure_ascii=False)
        for _ in range(19):
            again = json.dumps(skill_store.fill(skill, values),
                               ensure_ascii=False)
            assert again == first

    def test_filling_touches_no_ai_layer(self, monkeypatch):
        """重放路径上只要有人碰到 AI 层，这条立即红。

        只看 import 列表不够（运行时也可能 import），所以临时插一个 meta_path
        拦截器：抽槽 + 填槽这段时间里，谁 import AI 层谁就被拦住并报错。
        """
        import sys

        blocked = ("framework.ai_engine", "framework.multi_agent")

        class _Blocker:
            def find_spec(self, fullname, path=None, target=None):
                if fullname.startswith(blocked):
                    raise AssertionError(f"技能重放里出现了 AI 层：{fullname}")
                return None

        for name in list(sys.modules):
            if name.startswith(blocked):
                monkeypatch.delitem(sys.modules, name, raising=False)
        finder = _Blocker()
        monkeypatch.setattr(sys, "meta_path", [finder] + list(sys.meta_path))

        skill = skill_store.abstract([
            {"action": "read_doc", "path": "C:/a.txt"},
            {"action": "todo_add", "text": "看这份"}])
        steps, missing = skill_store.fill(skill, {"路径": "C:/b.txt",
                                                 "主题": "看那份"})
        assert steps and missing == []
        assert not any(n.startswith(blocked) for n in sys.modules)


class TestStore:
    def test_learned_skill_survives_a_restart(self, tmp_path):
        path = str(tmp_path / "skills.json")
        s1 = SkillStore(path=path)
        item, why = s1.add("每周一查竞品", [
            {"action": "web_search", "query": "竞品动态"},
            {"action": "todo_add", "text": "整理成要点"},
        ])
        assert item and not why
        s2 = SkillStore(path=path)
        assert [s["name"] for s in s2.all()] == [item["name"]]
        assert s2.all()[0]["steps"][0]["query"] == "{主题}"

    def test_same_trajectory_is_not_learned_twice(self, store):
        steps = [{"action": "web_search", "query": "竞品"},
                 {"action": "todo_add", "text": "整理"}]
        first, _ = store.add("查竞品", steps)
        second, why = store.add("再查竞品", steps)
        assert first and second is None and "已经会了" in why
        assert len(store.all()) == 1

    def test_corrupt_file_is_treated_as_empty_not_fatal(self, tmp_path):
        path = tmp_path / "skills.json"
        path.write_text("{这不是 json", encoding="utf-8")
        store = SkillStore(path=str(path))
        assert store.all() == []

    def test_name_collision_gets_a_suffix(self, store):
        """两件事被用户用同一句话说出来：骨架不同就该都留下，名字自动加序号。"""
        a, _ = store.add("查竞品动态", [
            {"action": "web_search", "query": "竞品"},
            {"action": "todo_add", "text": "整理"}])
        b, _ = store.add("查竞品动态", [
            {"action": "web_search", "query": "竞品"},
            {"action": "todo_add", "text": "整理"},
            {"action": "remember", "fact": "要点"}])
        assert a and b
        assert a["name"] != b["name"] and b["name"].startswith(a["name"])

    def test_get_matches_the_longest_name(self, store):
        store.add("查竞品", [{"action": "web_search", "query": "竞品"},
                             {"action": "todo_add", "text": "a"}])
        store.add("查竞品并记待办", [{"action": "web_search", "query": "对手"},
                                     {"action": "remember", "fact": "b"}])
        hit = store.get("帮我查竞品并记待办")
        assert hit and "并记待办" in hit["name"]

    def test_remove_and_mark_used(self, store):
        item, _ = store.add("查竞品", [
            {"action": "web_search", "query": "竞品"},
            {"action": "todo_add", "text": "整理"}])
        store.mark_used(item)
        assert SkillStore(path=store.path).all()[0]["used"] == 1
        assert store.remove(item["name"]) is not None
        assert store.all() == []

    def test_catalog_lists_names_and_required_slots(self, store):
        item, _ = store.add("查一下竞品动态并把要点记成待办",
                            [{"action": "web_search", "query": "竞品"},
                             {"action": "todo_add", "text": "整理"}])
        text = store.catalog_text()
        assert "run_skill" in text and "主题" in text
        assert item["name"] in text        # 清单里念的就是下次能叫得出口的那个名字

    def test_empty_catalog_is_empty_string(self, store):
        assert store.catalog_text() == ""

    def test_too_many_slots_means_it_is_not_a_routine(self, store):
        """要现填的参数比 SKILL_SLOT_MAX 还多：这不是流程，是即兴发挥，拒收。"""
        n = config.SKILL_SLOT_MAX + 1
        item, why = store.add("查六个话题",
                              [{"action": "web_search", "query": f"话题{i}"}
                               for i in range(n)])
        assert item is None and "槽位" in why
        assert store.all() == []

    def test_catalog_text_is_injected_into_the_ai_prompt(self):
        """学过的技能必须真的进提示词，否则 run_skill 只是有个动作名而已。"""
        from types import SimpleNamespace

        from framework.ai_engine import AIWorker

        catalog = "\n\n【已学会的技能】- 调音量·开程序（需要：应用）"
        base = dict(facts="", notes="", personality="活泼",
                    message="把音量调小点开微信", history=[])
        hit = AIWorker._messages(
            SimpleNamespace(**base, skills=catalog))[0]["content"]
        assert "调音量·开程序" in hit
        # 没有技能时不注入：不许留一节空清单骗模型去挑不存在的名儿
        miss = AIWorker._messages(
            SimpleNamespace(**base, skills=""))[0]["content"]
        assert "调音量·开程序" not in miss

    def test_cap_drops_the_oldest(self, store, monkeypatch):
        monkeypatch.setattr(config, "MAX_SKILLS", 3)
        extra = [{"action": "todo_add", "text": "任务"},
                 {"action": "remember", "fact": "要点"},
                 {"action": "set_volume", "level": 30},
                 {"action": "screenshot", "region": "full"},
                 {"action": "open_app", "app": "记事本"}]
        for i in range(1, 6):
            item, why = store.add(f"第{i}次查资料",
                                  [{"action": "web_search", "query": "话题"}]
                                  + extra[:i])
            assert item, why            # 骨架步数不同，才算 5 条不同的技能
        names = [s["name"] for s in store.all()]
        assert len(names) == 3 and "第1次查资料" not in names


def test_module_stays_qt_free():
    """技能层必须能被纯单测覆盖：不许 import Qt / requests / pandas。"""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(skill_store))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert not ({"PyQt5", "requests", "pandas", "numpy"} & mods), sorted(mods)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
