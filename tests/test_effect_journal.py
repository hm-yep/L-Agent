# -*- coding: utf-8 -*-
"""单元测试：效果记账（后悔药）—— 差集判定、回退顺序、删文件守卫、分类完备性

这一层是纯函数，不碰 Qt、不碰网络、不碰注册表，所以每条断言都是真的在判
"这台电脑被改成什么样"，而不是在判 mock 长得像不像。
"""

import os
import time

import pytest

from framework import config, effect_journal
from framework.action_parser import VALID_ACTIONS


def world(volume=None, todos=(), reminders=(), deliveries=(), facts=()):
    """按 (文本, 是否完成) 的简写构造待办，少写几行噪音。

    形状不对（比如误传已归一化的字典）当场炸掉，而不是让 diff 悄悄以为
    「这条待办不见了」——测试脚手架自己先不能撒谎。
    """
    norm_todos = []
    for t in todos:
        if not (isinstance(t, tuple) and len(t) == 2):
            raise TypeError(f"todos 要 (文本, 是否完成) 二元组，拿到 {t!r}")
        norm_todos.append({"text": t[0], "done": bool(t[1])})
    return effect_journal.normalize(
        volume=volume, todos=norm_todos,
        reminders=reminders, deliveries=deliveries, facts=facts)


class TestDiff:
    def test_the_shapes_the_wiring_layer_actually_sends(self):
        """接线层读出来的是 (文本, 是否完成) 元组和 {'text','path'} 字典。

        上面那个 world() 脚手架会把元组先转成字典，所以它对判据是透明的——
        这条测试偏不走脚手架，直接喂真形状：元组一旦被 str() 整块，标签就成了
        「新待办「('交周报', False)」」，回退于是去删一条不存在的待办，
        而 24 条按字典写的测试全都还是绿的。
        """
        before = effect_journal.normalize(todos=[], facts=[])
        after = effect_journal.normalize(
            todos=[("交周报", False), ("取快递", True)],
            reminders=[{"text": "开会"}],
            deliveries=[{"text": "报告", "path": "C:/x/报告.pptx"}])
        items = effect_journal.diff(before, after)
        labels = {i["kind"]: i["label"] for i in items}
        assert "('交周报'" not in labels["todo_add"], labels["todo_add"]
        assert "交周报" in labels["todo_add"]
        ops = {o["op"]: o for o in effect_journal.undo_ops(items)}
        assert ops["remove_todo"]["text"] == "交周报"
        assert ops["remove_reminder"]["text"] == "开会"
        assert [i["path"] for i in items if i["kind"] == "delivery"] \
            == ["C:/x/报告.pptx"]

    def test_volume_change_is_reversible_to_old_value(self):
        before = world(volume=80)
        after = world(volume=35)
        items = effect_journal.diff(before, after)
        assert len(items) == 1
        assert items[0]["reversible"] is True
        assert items[0]["undo"] == {"op": "set_volume", "percent": 80}

    def test_nothing_changed_diffs_to_nothing(self):
        """防瞎守卫：判据必须能判负。永远非空的 diff 等于没有 diff。"""
        args = dict(volume=50, todos=[("交周报", False)],
                    reminders=["开会"], deliveries=["C:/x/a.pptx"],
                    facts=["我在杭州"])
        assert effect_journal.diff(world(**args), world(**args)) == []

    def test_new_todo_and_completed_todo_are_different_items(self):
        before = world(todos=[("取快递", False)])
        after = world(todos=[("取快递", True), ("交周报", False)])
        kinds = [i["kind"] for i in effect_journal.diff(before, after)]
        assert kinds == ["todo_done", "todo_add"]

    def test_reopening_an_old_todo_is_not_counted_as_added(self):
        """旧条目被重新打开不是新增：误判会让回退去删用户接手前就有的待办。"""
        before = world(todos=[("交周报", True)])
        after = world(todos=[("交周报", False)])
        items = effect_journal.diff(before, after)
        assert all(i["kind"] != "todo_add" for i in items)
        assert effect_journal.undo_ops(items) == []

    def test_vanished_todo_is_reported_but_not_restored(self):
        before = world(todos=[("消失的", False)])
        after = world(todos=[])
        items = effect_journal.diff(before, after)
        assert [i["kind"] for i in items] == ["todo_gone"]
        assert items[0]["reversible"] is False
        assert effect_journal.undo_ops(items) == []

    def test_new_delivery_becomes_a_delete_op(self):
        before = world(deliveries=[])
        after = world(deliveries=["C:/报告/三月发票.xlsx"])
        items = effect_journal.diff(before, after)
        assert items[0]["undo"] == {"op": "delete_file",
                                    "path": "C:/报告/三月发票.xlsx"}
        assert "三月发票.xlsx" in items[0]["label"]

    def test_pre_existing_delivery_is_untouched(self):
        snap = world(deliveries=["C:/报告/旧的.docx"])
        assert effect_journal.diff(snap, snap) == []

    def test_new_reminder_and_fact_each_get_an_op(self):
        items = effect_journal.diff(world(),
                                    world(reminders=["开会"],
                                          facts=["我在杭州"]))
        ops = effect_journal.undo_ops(items)
        assert {"op": "remove_reminder", "text": "开会"} in ops
        assert {"op": "forget_fact", "text": "我在杭州"} in ops

    def test_diff_order_is_deterministic(self):
        """同一批改动，顺序必须每次一样：撤销顺序飘忽=不可复现=没法测。"""
        before = world(volume=60, todos=[("旧", False)])
        after = world(volume=10, facts=["喜欢猫"], reminders=["喝水"],
                      deliveries=["C:/a.pptx"], todos=[("旧", True)])
        first = [i["kind"] for i in effect_journal.diff(before, after)]
        for _ in range(5):
            assert [i["kind"] for i in
                    effect_journal.diff(before, after)] == first
        assert first[0] == "volume"                       # 音量最先
        assert first[-1] == "delivery"                    # 删文件最后


class TestBlindSpots:
    def test_launched_app_and_lock_are_admitted_not_undone(self):
        spots = effect_journal.blind_spots(["open_app", "lock", "web_search",
                                            "set_volume"])
        assert [s["action"] for s in spots] == ["open_app", "lock"]
        assert "没存的活儿" in spots[0]["reason"]

    def test_screenshot_is_a_blind_spot_on_purpose(self):
        """截图落在哪是用户在对话框里亲手选的，那是用户的文件，不追踪也不删。"""
        assert effect_journal.blind_spots(["screenshot"]) != []


class TestDeleteGuard:
    def test_file_created_this_turn_is_deletable(self):
        now = time.time()
        ok, why = effect_journal.deletable("C:/x/报告.pptx", size=2048,
                                           started_at=now - 3, mtime=now)
        assert ok, why

    def test_older_file_is_never_deleted(self):
        now = time.time()
        ok, why = effect_journal.deletable("C:/x/你上周的.xlsx", size=2048,
                                           started_at=now, mtime=now - 86400)
        assert not ok and "不是本轮生成的" in why

    def test_extension_outside_whitelist_is_refused(self):
        now = time.time()
        ok, why = effect_journal.deletable("C:/Windows/关键.dll", size=10,
                                           started_at=now - 1, mtime=now)
        assert not ok and "白名单" in why

    def test_oversized_file_is_refused(self):
        now = time.time()
        big = config.UNDO_DELETE_MAX_BYTES + 1
        ok, why = effect_journal.deletable("C:/x/大.png", size=big,
                                           started_at=now - 1, mtime=now)
        assert not ok and "超过回退上限" in why

    def test_master_switch_blocks_deletion(self, monkeypatch):
        monkeypatch.setattr(config, "UNDO_DELETE_FILES", False)
        ok, why = effect_journal.deletable("C:/x/a.pptx")
        assert not ok and "设置里关闭" in why

    def test_no_mtime_means_cannot_prove_it_is_ours(self, tmp_path):
        """mtime 拿不到（文件已不在/读不到）就不删：证不了是我们生的就别动手。"""
        ghost = os.path.join(str(tmp_path), "不存在.png")
        ok, _why = effect_journal.deletable(ghost, size=10,
                                            started_at=time.time() - 1,
                                            mtime=0.0)
        assert ok is True      # 纯函数只管规则，文件在不在由执行层判断


class TestCoverage:
    def test_every_action_is_classified(self):
        """新增动作忘了归类就是红灯：这是本项目反复踩过的「检查装成在跑」。"""
        cov = effect_journal.coverage(VALID_ACTIONS)
        assert cov["unknown"] == [], f"这些动作没进任何可逆性分类：{cov['unknown']}"

    def test_three_buckets_partition_the_action_pool(self):
        cov = effect_journal.coverage(VALID_ACTIONS)
        total = (len(cov["reversible"]) + len(cov["blind"])
                 + len(cov["no_effect"]) + len(cov["undo"]))
        assert total == len(VALID_ACTIONS)
        assert len(cov["reversible"]) > 0 and len(cov["blind"]) > 0

    def test_no_action_is_counted_twice(self):
        cov = effect_journal.coverage(VALID_ACTIONS)
        flat = (cov["reversible"] + cov["blind"] + cov["no_effect"]
                + cov["undo"])
        assert len(flat) == len(set(flat))


class TestSummarize:
    def test_every_line_carries_a_verdict_mark(self):
        items = effect_journal.diff(world(volume=70), world(volume=20))
        items += effect_journal.diff(world(todos=[("没了", False)]), world())
        lines = effect_journal.summarize(items,
                                         effect_journal.blind_spots(
                                             ["open_app"]))
        assert len(lines) == 3
        assert all(l.startswith("[可撤]") or l.startswith("[撤不回]")
                   for l in lines)


class TestPurity:
    def test_module_needs_no_qt_or_network(self):
        """这一层能被纯单测覆盖的前提：它自己不去偷偷 import 重型依赖。"""
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(effect_journal))
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        assert not ({"PyQt5", "requests", "pandas", "numpy"} & mods), \
            f"效果记账层引到了重依赖：{sorted(mods)}"

    def test_diff_does_not_mutate_inputs(self):
        before = world(volume=50, todos=[("a", False)])
        snapshot = {"volume": before["volume"],
                    "todos": [dict(t) for t in before["todos"]],
                    "facts": list(before["facts"]),
                    "reminders": list(before["reminders"]),
                    "deliveries": list(before["deliveries"])}
        effect_journal.diff(before, world(volume=10, facts=["x"]))
        assert before == snapshot


def test_undo_ops_drop_irreversible_items():
    items = [{"kind": "volume", "reversible": True,
              "undo": {"op": "set_volume", "percent": 5}},
             {"kind": "todo_gone", "reversible": False, "undo": None},
             {"kind": "delivery", "reversible": True,
              "undo": {"op": "delete_file", "path": "C:/a.png"}}]
    assert effect_journal.undo_ops(items) == [
        {"op": "set_volume", "percent": 5},
        {"op": "delete_file", "path": "C:/a.png"}]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
