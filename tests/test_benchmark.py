# -*- coding: utf-8 -*-
"""单元测试：带扰动的能力评测（含学习前后对照）。

隔离原则：所有 JSON / 历史产物都写进 tmp_path；用 monkeypatch 拦截任何对
真实 data/ 的写入，验证评测全程不写脏项目数据。真断言，禁止 assert True。
"""

import builtins
import json
import os
import random
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_ROOT, "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

import benchmark


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# 1. 任务集规模与字段
# ---------------------------------------------------------------------------
def test_task_count_and_required_fields():
    tasks = benchmark.TASKS
    assert len(tasks) >= 30
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)), "任务 id 必须唯一"
    required = {"id", "group", "utterance", "perturbation",
                "expected_action", "fixture"}
    for t in tasks:
        assert required.issubset(t.keys())
        assert isinstance(t["fixture"], dict)
        assert isinstance(t["fixture"]["raw_actions"], list)
        assert t["expected_action"]
        assert t["group"] and t["utterance"]


# ---------------------------------------------------------------------------
# 2. 扰动类型覆盖 >= 8 类
# ---------------------------------------------------------------------------
def test_perturbation_coverage_at_least_eight():
    kinds = {t["perturbation"] for t in benchmark.TASKS}
    assert len(kinds) >= 8
    assert len(benchmark.PERTURBATIONS) >= 8
    assert kinds.issubset(set(benchmark.PERTURBATIONS))
    # 每类至少有一条真实任务（不是空声明）
    for k in kinds:
        assert any(t["perturbation"] == k for t in benchmark.TASKS)


# ---------------------------------------------------------------------------
# 3. 固定种子两次结果完全一致（含学习态与对照）
# ---------------------------------------------------------------------------
def test_fixed_seed_is_deterministic():
    a = benchmark.run_suite(42, learned=False)
    b = benchmark.run_suite(42, learned=False)
    assert _dump(a) == _dump(b)
    c = benchmark.run_suite(42, learned=True)
    d = benchmark.run_suite(42, learned=True)
    assert _dump(c) == _dump(d)
    assert _dump(benchmark.compare(42)) == _dump(benchmark.compare(42))
    # 不同种子不应撞出完全一样的模拟成本
    assert _dump(benchmark.run_suite(7, False)) != _dump(a)


# ---------------------------------------------------------------------------
# 4. 学习后完成率严格大于学习前，且无回归
# ---------------------------------------------------------------------------
def test_learning_improves_completion_rate():
    cmp = benchmark.compare(42)
    assert cmp["after_rate"] > cmp["before_rate"]
    assert cmp["after"]["passed"] > cmp["before"]["passed"]
    assert cmp["delta_pp"] > 0
    # 规则只做增强：学习前办成的事，学习后不该被弄砸
    before_ok = {c["id"]: c["ok"] for c in cmp["before"]["cases"]}
    after_ok = {c["id"]: c["ok"] for c in cmp["after"]["cases"]}
    for cid, was_ok in before_ok.items():
        if was_ok:
            assert after_ok[cid] is True, "学习让本可完成的任务 %s 回归了" % cid
    # 敢公布失败率：学习后也不是满分
    assert cmp["after"]["passed"] < cmp["after"]["total"]


# ---------------------------------------------------------------------------
# 5. 被救回任务 id 非空且确实是「前败后成」
# ---------------------------------------------------------------------------
def test_rescued_ids_nonempty_and_real():
    cmp = benchmark.compare(42)
    rescued = cmp["rescued_ids"]
    assert len(rescued) >= 4
    assert len(set(rescued)) == len(rescued)
    before_ok = {c["id"]: c["ok"] for c in cmp["before"]["cases"]}
    after_ok = {c["id"]: c["ok"] for c in cmp["after"]["cases"]}
    for cid in rescued:
        assert before_ok[cid] is False
        assert after_ok[cid] is True
    # 救回数恰等于完成数增量（无抵消）
    assert len(rescued) == cmp["after"]["passed"] - cmp["before"]["passed"]


# ---------------------------------------------------------------------------
# 6. 失败原因分类计数与总数自洽
# ---------------------------------------------------------------------------
def test_failure_categories_self_consistent():
    for suite in (benchmark.run_suite(42, False),
                  benchmark.run_suite(42, True)):
        cats = suite["failure_categories"]
        assert sum(cats.values()) == suite["total"] - suite["passed"]
        assert set(cats).issubset(set(benchmark.ALL_FAILURE_CATEGORIES))
        for c in suite["cases"]:
            if c["ok"]:
                assert c["failure_reason"] == ""
            else:
                assert c["failure_reason"] in benchmark.ALL_FAILURE_CATEGORIES
                assert cats[c["failure_reason"]] >= 1


# ---------------------------------------------------------------------------
# 7. run_case 返回字段齐全
# ---------------------------------------------------------------------------
def test_run_case_result_shape():
    rng = random.Random(42)
    with benchmark.LearningAdapter(learned=False) as adapter:
        r = benchmark.run_case(benchmark.TASKS[0], adapter, rng)
    required = {"id", "ok", "expected_action", "actual_action", "retries",
                "quality_passed", "skipped", "failure_reason",
                "latency_ms", "tokens", "rule_ids"}
    assert required.issubset(r.keys())
    assert isinstance(r["ok"], bool)
    assert isinstance(r["latency_ms"], int) and r["latency_ms"] > 0
    assert isinstance(r["tokens"], int)
    assert isinstance(r["rule_ids"], list)
    assert r["rule_ids"] == []          # learned=False 不应用任何规则


# ---------------------------------------------------------------------------
# 8. --json 输出结构与内容正确
# ---------------------------------------------------------------------------
def test_json_output_structure(tmp_path):
    out = tmp_path / "benchmark_out.json"
    rc = benchmark.main(["--json", str(out), "--threshold", "0.8"])
    assert rc == 0
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    for key in ("seed", "before_rate", "after_rate", "delta_pp",
                "rescued_ids", "failure_categories_before",
                "failure_categories_after", "before", "after", "meta",
                "exit_ok", "threshold"):
        assert key in data
    assert data["exit_ok"] is True
    assert data["before"]["total"] == len(data["before"]["cases"]) >= 30
    assert len(data["before"]["cases"]) == len(data["after"]["cases"])
    assert data["after_rate"] > data["before_rate"]
    assert data["meta"]["tasks"] >= 30
    assert data["meta"]["perturbation_types"] >= 8
    # 逐条明细也写进去了，且字段齐全
    first = data["before"]["cases"][0]
    assert {"id", "ok", "actual_action", "failure_reason"}.issubset(first)


# ---------------------------------------------------------------------------
# 9. 退出码语义：阈值达标放行，不达标卡口
# ---------------------------------------------------------------------------
def test_exit_code_semantics():
    assert benchmark.main([]) == 0                        # 默认阈值可通过
    assert benchmark.main(["--threshold", "0.5"]) == 0    # 低阈值放行
    assert benchmark.main(["--threshold", "0.99"]) == 1   # 达不到则卡口
    assert benchmark.main(["--threshold", "1.5"]) == 1    # 非法参数


# ---------------------------------------------------------------------------
# 10. 全程不写脏真实 data/（monkeypatch 拦截写入）
# ---------------------------------------------------------------------------
def test_no_dirty_data_directory(monkeypatch, tmp_path):
    real_data = os.path.abspath(os.path.join(_ROOT, "data"))
    dirty = []
    real_open = builtins.open
    real_makedirs = os.makedirs

    def _under(path):
        try:
            return (os.path.commonpath(
                [os.path.abspath(os.fspath(path)), real_data]) == real_data)
        except (ValueError, OSError, TypeError):
            return False

    def spy_open(file, *args, **kwargs):
        mode = str(args[0] if args else kwargs.get("mode", "r"))
        if any(c in mode for c in ("w", "a", "x", "+")) and _under(file):
            dirty.append(("open", str(file)))
        return real_open(file, *args, **kwargs)

    def spy_makedirs(name, *args, **kwargs):
        if _under(name):
            dirty.append(("makedirs", str(name)))
        return real_makedirs(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(os, "makedirs", spy_makedirs)
    report = benchmark.run_suite(42, learned=True)
    assert report["total"] >= 30
    assert dirty == [], dirty


# ---------------------------------------------------------------------------
# 11. --append-history 追加结构正确、目录自动创建（路径重定向到 tmp）
# ---------------------------------------------------------------------------
def test_append_history_row_structure(monkeypatch, tmp_path):
    hist = tmp_path / "benchmark" / "history.jsonl"
    monkeypatch.setattr(benchmark, "HISTORY_PATH", str(hist))
    assert benchmark.main(["--append-history", "--threshold", "0.8"]) == 0
    assert hist.is_file()
    lines = [x for x in hist.read_text(encoding="utf-8").splitlines()
             if x.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])
    for key in ("ts", "seed", "before_rate", "after_rate", "delta",
                "metrics"):
        assert key in row
    assert row["seed"] == 42
    assert row["delta"] == round(row["after_rate"] - row["before_rate"], 4)
    assert "before" in row["metrics"] and "after" in row["metrics"]
    assert "rescued_ids" in row["metrics"]
    # 再跑一次验证是「追加」而非覆盖
    assert benchmark.main(["--append-history", "--threshold", "0.8"]) == 0
    lines2 = [x for x in hist.read_text(encoding="utf-8").splitlines()
              if x.strip()]
    assert len(lines2) == 2
