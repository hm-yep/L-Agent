# -*- coding: utf-8 -*-
"""单元测试：从自然对话自动发现偏好，并让偏好真正作用到行为。

隔离原则：所有 Store 都注入 tmp_path，绝不写脏真实 data/；
改 MAX_PREFERENCES / app_data_dir 一律用 monkeypatch，跑完自动还原。
"""

import ast
import inspect
import json
import os

import pytest

import framework.preference as pref
from framework import config
from framework.preference import (
    PREFERENCE_KEYS, PreferenceStore, extract_preferences,
    parse_llm_preferences,
)


@pytest.fixture
def store(tmp_path):
    return PreferenceStore(path=str(tmp_path / "preferences.json"))


@pytest.fixture
def pref_path(tmp_path):
    return str(tmp_path / "preferences.json")


# ============================================================
# 一、纯函数 extract_preferences：显式喜好
# ============================================================
class TestExplicitLike:
    def test_i_like_dark_is_positive_high_confidence(self):
        assert extract_preferences("我喜欢深色 PPT") == [
            {"key": "doc_theme", "value": "深色", "polarity": "positive",
             "confidence": 0.9, "match": "深色"},
        ]

    def test_prefer_green_normalizes_to_emerald_theme(self):
        out = extract_preferences("我偏好绿色")
        assert len(out) == 1
        assert out[0]["key"] == "doc_theme"
        assert out[0]["value"] == "翡翠绿"
        assert out[0]["polarity"] == "positive"

    def test_ppt_use_dark_and_always_dark(self):
        for text in ("PPT 用深色的", "以后都用深色", "我喜欢深空蓝"):
            out = extract_preferences(text)
            assert len(out) == 1, text
            assert out[0]["key"] == "doc_theme", text
            assert out[0]["value"] in ("深色", "深空蓝"), text

    def test_multiple_preferences_in_one_sentence(self):
        out = extract_preferences("我喜欢深色，简洁点")
        assert {p["key"] for p in out} == {"doc_theme", "verbosity"}
        assert all(0 <= p["confidence"] <= 1 for p in out)

    def test_each_result_shape_is_complete(self):
        for text in ("我喜欢深空蓝", "简洁点", "别用红色", "用英文",
                     "可能安静点"):
            for p in extract_preferences(text):
                assert set(p) == {"key", "value", "polarity", "confidence",
                                  "match"}
                assert p["key"] in PREFERENCE_KEYS
                assert p["polarity"] in ("positive", "negative")
                assert 0 <= p["confidence"] <= 1
                assert isinstance(p["match"], str) and len(p["match"]) <= 20


# ============================================================
# 二、否定：转成对应 key 的反向 value
# ============================================================
class TestNegation:
    @pytest.mark.parametrize("text,key,value,polarity", [
        ("我不喜欢花哨的", "verbosity", "concise", "negative"),
        ("太长了", "verbosity", "concise", "negative"),
        ("简洁点", "verbosity", "concise", "negative"),
        ("别写太长", "verbosity", "concise", "negative"),
        ("别用红色", "doc_theme", "红色", "negative"),
        ("不要红色", "doc_theme", "红色", "negative"),
        ("不要联网", "web_search", False, "negative"),
        ("不用搜了", "web_search", False, "negative"),
        ("别查了", "web_search", False, "negative"),
        ("以后别联网", "web_search", False, "negative"),
        ("别太活泼", "tone", "formal", "negative"),
        ("提醒别太吵", "reminder_style", "quiet", "negative"),
        ("安静点", "reminder_style", "quiet", "negative"),
    ])
    def test_negative_phrase_becomes_reverse_value(self, text, key, value,
                                                   polarity):
        out = extract_preferences(text)
        assert len(out) == 1, text
        p = out[0]
        assert p["key"] == key, text
        assert p["value"] == value, text
        assert p["polarity"] == polarity, text

    def test_red_rejection_does_not_set_a_red_theme(self, store):
        store.observe("别用红色")
        assert store.doc_style() == {}          # 不设置红色主题

    def test_negative_overrides_earlier_positive_theme(self, store):
        store.observe("我喜欢深空蓝")
        store.observe("别用红色")
        assert store.get("doc_theme")["value"] == "红色"
        assert store.doc_style() == {}
        assert store.get("doc_theme")["supersedes"]["value"] == "深空蓝"


# ============================================================
# 三、非偏好句必须返回 []，不能误抓
# ============================================================
class TestNonPreference:
    @pytest.mark.parametrize("text", [
        "帮我做个 PPT", "查一下天气", "加个待办", "打开微信",
        "提醒我三点开会", "把音量调到30", "现在几点", "帮我写个周报",
        "查一下绿色能源价格",
    ])
    def test_task_instruction_returns_empty(self, text):
        assert extract_preferences(text) == []

    def test_observe_on_non_preference_changes_nothing(self, store):
        assert store.observe("帮我做个 PPT") == []
        assert store.all() == []
        assert store.as_prompt() == ""


# ============================================================
# 四、置信度分层
# ============================================================
class TestConfidence:
    def test_strong_markers_get_09(self):
        for text in ("我喜欢深色 PPT", "以后都用深色", "我偏好绿色"):
            assert extract_preferences(text)[0]["confidence"] == 0.9, text

    def test_point_some_and_negation_get_07(self):
        for text in ("简洁点", "详细些", "PPT 用深色的", "别太活泼"):
            assert extract_preferences(text)[0]["confidence"] == 0.7, text

    def test_vague_words_get_05(self):
        for text in ("可能简洁点", "感觉详细点好", "也许用英文"):
            out = extract_preferences(text)
            assert out and all(p["confidence"] == 0.5 for p in out), text


# ============================================================
# 五、去重 / 改主意 / 落盘结构
# ============================================================
class TestConflictAndHistory:
    def test_duplicate_sentence_does_not_add_item(self, store):
        first = store.observe("我喜欢深色")
        second = store.observe("我喜欢深色")
        assert len(first) == len(second) == 1
        assert len(store.all()) == 1
        item = store.get("doc_theme")
        assert item["hits"] == 2
        assert len(item["evidence"]) == 2
        assert item["supersedes"] is None

    def test_same_value_different_wording_still_accumulates(self, store):
        store.observe("我喜欢深色")
        store.observe("PPT 用深色的")
        assert len(store.all()) == 1
        assert store.get("doc_theme")["hits"] == 2
        assert len(store.get("doc_theme")["evidence"]) == 2

    def test_change_of_mind_keeps_evidence_and_supersedes(self, store):
        store.observe("我喜欢深空蓝")
        store.observe("我喜欢深空蓝")           # 重复
        changed = store.observe("以后都用翡翠绿")  # 改主意
        assert changed and changed[0]["key"] == "doc_theme"
        assert len(store.all()) == 1            # 覆盖而非新增条目
        item = store.get("doc_theme")
        assert item["value"] == "翡翠绿"
        assert len(item["evidence"]) == 3       # 全历史保留
        assert item["hits"] == 1                # 新值重新计数
        assert item["supersedes"]["value"] == "深空蓝"
        assert item["created_at"] <= item["updated_at"]

    def test_all_items_have_required_schema_fields(self, store):
        store.observe("我喜欢深色")
        store.observe("简洁点")
        required = {"key", "value", "polarity", "confidence", "evidence",
                    "hits", "created_at", "updated_at", "supersedes"}
        items = store.all()
        assert len(items) == 2
        for it in items:
            assert required.issubset(set(it))
            assert it["key"] in PREFERENCE_KEYS
            for ev in it["evidence"]:
                assert {"text_digest", "match", "turn_id", "ts"}.issubset(
                    set(ev))
                assert len(ev["match"]) <= 20

    def test_observe_returns_only_this_sentences_changes(self, store):
        out = store.observe("我喜欢深色，简洁点")
        assert {o["key"] for o in out} == {"doc_theme", "verbosity"}
        assert store.observe("查一下天气") == []


# ============================================================
# 六、偏好真正作用到行为
# ============================================================
class TestBehavior:
    def test_doc_style_fields_are_resolve_style_compatible(self, store):
        from framework.doc_generator import resolve_style

        store.observe("我喜欢深空蓝")
        style = store.doc_style()
        allowed = {"title_color", "accent_color", "title_size", "body_size",
                   "line_spacing", "font"}
        assert set(style).issubset(allowed)
        parsed = resolve_style({"style": style})
        assert parsed.get("accent_color") == (0x0F, 0x2B, 0x46)
        assert parsed.get("title_color") == (0x0F, 0x2B, 0x46)

    def test_all_six_themes_resolve_to_valid_colors(self, tmp_path):
        from framework.doc_generator import resolve_style

        phrases = {
            "我喜欢深色": "#0F2B46",
            "我喜欢深空蓝": "#0F2B46",
            "我喜欢翡翠绿": "#0E332A",
            "我喜欢暖橙": "#4A250F",
            "我喜欢绛紫": "#2C1A40",
            "我喜欢雅灰": "#25272A",
        }
        for i, (text, hex_color) in enumerate(phrases.items()):
            s = PreferenceStore(path=str(tmp_path / ("theme%d.json" % i)))
            s.observe(text)
            style = s.doc_style()
            assert style == {"title_color": hex_color,
                             "accent_color": hex_color}, text
            parsed = resolve_style({"style": style})
            assert parsed.get("accent_color") is not None, text
            r, g, b = parsed["accent_color"]
            assert all(0 <= c <= 255 for c in (r, g, b)), text

    def test_doc_style_empty_without_a_theme(self, store):
        assert store.doc_style() == {}
        store.observe("简洁点")
        assert store.doc_style() == {}

    def test_verbosity_follows_current_value(self, store):
        assert store.verbosity() == "normal"
        store.observe("简洁点")
        assert store.verbosity() == "concise"
        store.observe("详细点")
        assert store.verbosity() == "detailed"
        store.forget("verbosity")
        assert store.verbosity() == "normal"

    def test_allow_web_search_follows_current_value(self, store):
        assert store.allow_web_search() is True       # 无偏好默认允许
        store.observe("以后别联网")
        assert store.allow_web_search() is False
        store.observe("多查点资料")
        assert store.allow_web_search() is True
        store.forget("web_search")
        assert store.allow_web_search() is True

    def test_change_of_mind_changes_behavior_immediately(self, store):
        store.observe("我喜欢深空蓝")
        assert store.doc_style()["accent_color"] == "#0F2B46"
        store.observe("我偏好绿色")
        assert store.doc_style()["accent_color"] == "#0E332A"  # 立刻变
        store.observe("简洁点")
        assert store.verbosity() == "concise"
        store.observe("详细点")
        assert store.verbosity() == "detailed"

    def test_as_prompt_has_title_is_short_and_has_no_raw_text(self, store):
        store.observe("我喜欢深色PPT，但别把暗号Zebra9写进去")
        prompt = store.as_prompt()
        assert "用户偏好" in prompt
        assert len(prompt) <= 200
        assert "Zebra9" not in prompt
        assert "暗号" not in prompt
        assert "text_digest" not in prompt

    def test_as_prompt_is_empty_when_no_preferences(self, store):
        assert store.as_prompt() == ""

    def test_as_prompt_lists_all_dimensions_within_limit(self, store):
        for text in ("我喜欢深色", "简洁点", "活泼点", "以后别联网",
                     "用英文", "安静点"):
            store.observe(text)
        prompt = store.as_prompt()
        assert "用户偏好" in prompt
        assert len(prompt) <= 200
        for label in ("文档配色", "回答详略", "语气", "联网搜索",
                      "回答语言", "提醒方式"):
            assert label in prompt


# ============================================================
# 七、生命周期 / 持久化 / 隐私 / 上限
# ============================================================
class TestLifecycle:
    def test_forget_removes_only_that_key(self, store):
        store.observe("我喜欢深色")
        store.observe("简洁点")
        assert store.forget("doc_theme") is True
        assert store.forget("doc_theme") is False
        assert store.get("doc_theme") is None
        assert store.doc_style() == {}
        assert [i["key"] for i in store.all()] == ["verbosity"]

    def test_clear_resets_everything(self, store):
        store.observe("我喜欢深色")
        store.observe("简洁点")
        store.observe("以后别联网")
        store.clear()
        assert store.all() == []
        assert store.as_prompt() == ""
        assert store.verbosity() == "normal"
        assert store.allow_web_search() is True
        assert store.doc_style() == {}
        assert store.forget("doc_theme") is False

    def test_persistence_roundtrip(self, pref_path):
        s1 = PreferenceStore(path=pref_path)
        s1.observe("我喜欢深空蓝")
        s1.observe("以后别联网")
        s2 = PreferenceStore(path=pref_path)   # 模拟重启
        assert s2.get("doc_theme")["value"] == "深空蓝"
        assert s2.doc_style()["accent_color"] == "#0F2B46"
        assert s2.allow_web_search() is False

    def test_no_plaintext_original_on_disk(self, store, pref_path):
        store.observe("我喜欢深色PPT，但别把暗号Zebra9写进去",
                      source_turn="t_000042")
        content = open(pref_path, encoding="utf-8").read()
        assert "暗号Zebra9" not in content        # 原话/隐私片段绝不落盘
        assert "Zebra9" not in content
        assert "我喜欢深色PPT，但别把暗号Zebra9写进去" not in content
        data = json.loads(content)
        assert data["schema"] == 1
        ev = data["items"]["doc_theme"]["evidence"][0]
        assert len(ev["text_digest"]) == 16        # 只留哈希
        assert ev["turn_id"] == "t_000042"
        assert ev["match"] == "深色"               # 只留控制短语

    def test_max_preferences_cap_drops_oldest(self, store, monkeypatch):
        monkeypatch.setattr(config, "MAX_PREFERENCES", 3, raising=False)
        for text in ("我喜欢深色", "简洁点", "活泼点", "以后别联网"):
            store.observe(text)
        keys = {i["key"] for i in store.all()}
        assert len(keys) == 3
        assert "doc_theme" not in keys              # 最老的被丢
        assert "web_search" in keys                 # 最新的留下

    def test_injected_path_never_touches_real_data_dir(self, tmp_path,
                                                       monkeypatch):
        import framework.utils as utils

        def boom():
            raise AssertionError("path 注入时不应调用 app_data_dir")

        monkeypatch.setattr(utils, "app_data_dir", boom, raising=False)
        p = str(tmp_path / "injected.json")
        s = PreferenceStore(path=p)
        s.observe("我喜欢深色")
        assert os.path.exists(p)
        assert s.get("doc_theme")["value"] == "深色"


# ============================================================
# 八、鲁棒性：异常输入 / 损坏文件 / 无 Qt 依赖
# ============================================================
class TestRobustness:
    def test_abnormal_inputs_never_crash(self, store):
        for bad in (None, "", "   ", 123, 3.14, [], {}, b"abc",
                    "简洁点" * 5000):
            assert isinstance(extract_preferences(bad), list)
            assert isinstance(store.observe(bad), list)
        assert isinstance(store.as_prompt(), str)
        assert isinstance(store.doc_style(), dict)
        assert store.verbosity() in ("concise", "normal", "detailed")
        assert isinstance(store.allow_web_search(), bool)

    def test_corrupt_file_is_treated_as_empty(self, tmp_path):
        p = tmp_path / "broken.json"
        p.write_text("{这不是 json", encoding="utf-8")
        s = PreferenceStore(path=str(p))
        assert s.all() == []
        assert s.as_prompt() == ""
        s.observe("我喜欢深色")                    # 还能正常恢复写入
        assert s.get("doc_theme")["value"] == "深色"

    def test_module_stays_qt_free(self):
        tree = ast.parse(inspect.getsource(pref))
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        assert not ({"PyQt5", "requests", "pandas", "numpy"} & mods), \
            sorted(mods)


# ============================================================
# 七、大模型批量抽取：只收「有原话证据」的偏好
# ============================================================
class TestLLMBatch:
    """这一层的风险只有一个：模型编。所以每条都必须过原话引用校验。"""

    TURNS = [
        {"role": "user", "content": "我是做财务的，以后讲东西别给我讲实现细节"},
        {"role": "assistant", "content": "好，我记住了。"},
        {"role": "user", "content": "以后 PPT 都用深色"},
    ]

    def test_accepts_item_with_verbatim_quote(self):
        raw = json.dumps([{
            "key": "background", "value": "用户是做财务的",
            "evidence": "我是做财务的"}])
        out = parse_llm_preferences(raw, self.TURNS)
        assert len(out) == 1
        assert out[0]["key"] == "background"
        assert out[0]["confidence"] == 0.72

    def test_rejects_item_without_evidence(self):
        raw = json.dumps([{"key": "background", "value": "财务"}])
        assert parse_llm_preferences(raw, self.TURNS) == []

    def test_rejects_quote_not_in_conversation(self):
        raw = json.dumps([{
            "key": "hobby", "value": "喜欢户外",
            "evidence": "我周末常去爬山"}])       # 原话里根本没有这句
        assert parse_llm_preferences(raw, self.TURNS) == []

    def test_rejects_too_short_quote(self):
        raw = json.dumps([{
            "key": "finance", "value": "财务", "evidence": "财务"}])
        assert parse_llm_preferences(raw, self.TURNS) == []

    def test_rejects_bad_key_and_overlong_value(self):
        raw = json.dumps([
            {"key": "坏Key", "value": "财务", "evidence": "我是做财务的"},
            {"key": "ok_key", "value": "长" * 200, "evidence": "我是做财务的"},
        ])
        assert parse_llm_preferences(raw, self.TURNS) == []

    def test_tolerates_fenced_json(self):
        body = json.dumps([{
            "key": "background", "value": "财务",
            "evidence": "我是做财务的"}])
        assert len(parse_llm_preferences("```json\n" + body + "\n```",
                                         self.TURNS)) == 1

    def test_bad_json_returns_empty(self):
        assert parse_llm_preferences("不是 JSON", self.TURNS) == []
        assert parse_llm_preferences(None, self.TURNS) == []
        assert parse_llm_preferences("[]", self.TURNS) == []

    def test_quote_survives_whitespace_difference(self):
        """模型常把换行抹平，比对前双方都去空白，不该因此误杀。"""
        turns = [{"role": "user", "content": "我是做\n财务的"}]
        raw = json.dumps([{
            "key": "background", "value": "财务",
            "evidence": "我是做 财务的"}])
        assert len(parse_llm_preferences(raw, turns)) == 1

    def test_observe_batch_writes_source_llm(self, store):
        raw = json.dumps([{
            "key": "background", "value": "用户是做财务的",
            "evidence": "我是做财务的"}])
        changed = store.observe_batch(self.TURNS, lambda _t: raw)
        assert changed and changed[0]["source"] == "llm"
        assert store.get("background")["value"] == "用户是做财务的"

    def test_observe_batch_swallows_llm_errors(self, store):
        def boom(_turns):
            raise RuntimeError("网络断了")

        assert store.observe_batch(self.TURNS, boom) == []
        assert store.all() == []

    def test_observe_batch_empty_turns_is_noop(self, store):
        assert store.observe_batch([], lambda _t: "[]") == []
        assert store.observe_batch(self.TURNS, None) == []

    def test_second_batch_overrides_and_keeps_supersedes(self, store):
        first = json.dumps([{
            "key": "background", "value": "财务",
            "evidence": "我是做财务的"}])
        store.observe_batch(self.TURNS, lambda _t: first)
        turns2 = [{"role": "user", "content": "我转岗去做产品了"}]
        second = json.dumps([{
            "key": "background", "value": "产品",
            "evidence": "我转岗去做产品了"}])
        store.observe_batch(turns2, lambda _t: second)
        item = store.get("background")
        assert item["value"] == "产品"
        assert item["supersedes"]["value"] == "财务"

    def test_open_ended_preference_reaches_prompt_without_english_key(self,
                                                                      store):
        raw = json.dumps([{
            "key": "background", "value": "用户是做财务的，别讲实现细节",
            "evidence": "我是做财务的"}])
        store.observe_batch(self.TURNS, lambda _t: raw)
        prompt = store.as_prompt()
        assert "别讲实现细节" in prompt
        assert "background" not in prompt     # 英文 key 不该漏进系统提示词

    def test_rule_layer_and_llm_layer_share_one_merge(self, store):
        """同一维度两层都命中时，只留一条，来源以先写者为准。"""
        store.observe("以后 PPT 都用深色")      # rule 层
        raw = json.dumps([{
            "key": "doc_theme", "value": "深色",
            "evidence": "以后 PPT 都用深色"}])
        store.observe_batch(self.TURNS, lambda _t: raw)   # llm 层同值
        item = store.get("doc_theme")
        assert item["hits"] >= 2
        assert item["source"] == "rule"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
