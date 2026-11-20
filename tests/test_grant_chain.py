# -*- coding: utf-8 -*-
"""跨端授权链路验收：跑真的 Framework、真的授权卡、真的动作队列。

这个文件存在的原因：`tests/test_api_grant.py` 用一个假的 `desk_ask` 复刻了
ask_grant，全套测试从没执行过「桌面弹卡 → 网页回答 → 恢复动作链」这段真胶水，
于是有三个 bug 在 514 个绿灯里活了下来：

  1. 网页点「允许」后，桌面 `_on_grant_resolved` 先 `card.close()`，卡片的
     closeEvent 把它当成「用户点了拒绝」再发一次，允许被翻成拒绝、队列被丢；
  2. 卡片只有一个实例，第二个请求顶掉第一个，第一个再也无法被回答；
  3. 恢复队列时不认 gid，任何一条请求的回答都会消费掉全局队列。

所以这里的每一条都从 `Framework.instance()` 起步，走 `_anchor_ask` /
`_anchor_answer`（锚点在 HTTP 线程里调的就是这两个方法）和真 `GrantCard`，
断言的是用户会看到的结果，不是源码里的字符串。
"""

import time

import pytest
from PyQt5.QtWidgets import QApplication

import framework.path_guard as path_guard
from framework.core import Framework
from framework.grant_service import (
    DECISION_DENY, DECISION_FILE, DECISION_TREE,
)

_APP = None          # 必须持有引用：否则新建的 QApplication 被 GC，之后建 QWidget 会原生崩溃


def _pump(app, ms=1200):
    """让 Qt 定时器真的走一遍（动作链靠 singleShot 推进，不泵就停在原地）。"""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def _reset(fw, monkeypatch):
    """把授权相关状态清干净，并且绝不让测试写他本机 %APPDATA% 的数据。"""
    for store in (fw.trajectory, fw.prefs, fw.rules):
        if store is not None:
            store.clear()
    fw.memory.history = []
    fw.tasks.deliveries = []
    monkeypatch.setattr(fw.memory, "save", lambda *a, **k: None)
    monkeypatch.setattr(fw.tasks, "save", lambda *a, **k: None)
    # read_doc 读完等「跟进轮」，跟进轮要联网；这里换成「这一步做完了，继续下一个动作」
    monkeypatch.setattr(fw, "_search_followup", lambda: fw._finish_action())
    fw.grant_service._pending.clear()
    fw.grant_service.store.clear()
    fw.grant_service._granted_at.clear()
    fw.grant_service.timeout_sec = 120
    for card in fw._grant_cards.values():
        card.mark_resolved()
        card.close()
    fw._grant_cards.clear()
    fw._grant_queues.clear()
    fw._action_queue = []
    fw._turn_actions = []
    fw._reference_notes = []
    fw._read_log = []
    fw._turn_evidence = {}
    fw._busy = False
    fw._finish_done = False
    fw._plan = None
    fw._plan_done = 0


@pytest.fixture
def chain(tmp_path, monkeypatch):
    """真桌面：白名单清空（逼出 grant），判定/卡片/队列都是产品里那份。"""
    global _APP
    _APP = QApplication.instance() or QApplication([])
    fw = Framework.instance()
    _reset(fw, monkeypatch)
    monkeypatch.setattr(path_guard, "allowed_roots", lambda: [])

    events = []                      # 谁广播了 grant_resolved，广播了几次
    fw.grant_resolved.connect(lambda g, d: events.append((g, d)))
    bubbles = []
    fw.bubble_requested.connect(lambda t: bubbles.append(t))

    def make(name, body="第一段内容"):
        p = tmp_path / name
        p.write_text(body, encoding="utf-8")
        return str(p)

    yield fw, make, events, bubbles
    _reset(fw, monkeypatch)           # 单例会被别的测试模块接着用，还原干净


def _start_chain(fw, path, extra=None):
    """把 read_doc 放进真动作链并跑起来：走到 grant 就停下来等点头。"""
    action = {"action": "read_doc", "path": path}
    fw._action_queue = [action] + list(extra or [])
    fw._busy = True
    fw._run_next_action()
    return action


# ============================================================
# 1. bug 1：网页端答「允许」，桌面不能把它翻成「拒绝」
# ============================================================
def test_web_allow_survives_desktop_card_close(chain, monkeypatch):
    fw, make, events, bubbles = chain
    path = make("报告.txt")
    action = _start_chain(fw, path)

    assert len(fw._grant_cards) == 1, "grant 判定必须真弹出一张卡"
    gid = next(iter(fw._grant_cards))
    stashed = fw._grant_queues[gid]
    assert [a["action"] for a in stashed] == ["read_doc"], \
        "暂存那份必须是产品代码自己存的"
    assert stashed[0]["path"] == action["path"]

    # 锚点在 HTTP 线程里调的就是 _anchor_answer：网页确认条走这条
    res = fw._anchor_answer(gid, DECISION_FILE)
    assert res["ok"] and res["newly"]

    # ① 判据翻回来了：同一路径现在是 allow，不再弹卡
    assert fw.grant_service.check(path)[0] == "allow"
    # ② 只广播了一次，且是「允许」——close 卡片补发的那次拒绝必须没有
    assert events == [(gid, DECISION_FILE)], events
    # ③ 被暂存的动作真的重跑了：文件内容进了资料缓冲，证据卡也记上了
    assert any("报告.txt" in n for n in fw._reference_notes), fw._reference_notes
    assert fw._read_log and fw._read_log[-1]["name"] == "报告.txt"
    assert "好，这次会话里我就能读了" in "".join(bubbles), bubbles
    # ④ 卡片收掉了，登记表里不再挂着这条
    assert fw._grant_cards == {} and fw._grant_queues == {}
    assert fw.grant_service.get(gid) is None


def test_card_close_does_not_emit_second_decision(chain):
    """关掉已经答过的卡不算表态：这是 bug 1 的最小复现。"""
    fw, make, events, bubbles = chain
    path = make("a.txt")
    action = _start_chain(fw, path)
    gid = next(iter(fw._grant_cards))
    fw._grant_queues[gid] = [action]

    fw._anchor_answer(gid, DECISION_DENY)
    assert events == [(gid, DECISION_DENY)], events
    # 答完就收尾了：同一条 gid 再答一次不改判、不重复广播，也不会把拒绝翻成允许
    again = fw._anchor_answer(gid, DECISION_FILE)
    assert again["ok"] is False and again["newly"] is False, again
    assert events == [(gid, DECISION_DENY)], events
    assert fw.grant_service.check(path)[0] == "grant", "拒绝之后仍未放开"


# ============================================================
# 2. bug 2：两条请求各一张卡，谁也不许顶掉谁
# ============================================================
def test_two_requests_get_two_cards_and_both_answerable(chain):
    fw, make, events, bubbles = chain
    p1, p2 = make("一.txt"), make("二.txt")

    fw.ask_grant(p1, source="desktop", action={"action": "read_doc", "path": p1})
    fw.ask_grant(p2, source="desktop", action={"action": "read_doc", "path": p2})

    assert len(fw._grant_cards) == 2, "第二条请求不能顶掉还没回答的第一条"
    g1, g2 = list(fw._grant_cards)
    cards = fw._grant_cards
    assert cards[g1].isVisible() and cards[g2].isVisible()
    # 两张卡不能叠在同一个坐标上，否则前面那张永远点不到
    assert cards[g1].pos() != cards[g2].pos(), (cards[g1].pos(), cards[g2].pos())

    # 倒过来答（先答后弹的那张），两份暂存的工作都要还在、都要真的做完
    fw._anchor_answer(g2, DECISION_FILE)
    _pump(_APP)
    fw._anchor_answer(g1, DECISION_FILE)
    _pump(_APP)
    assert fw.grant_service.check(p1)[0] == "allow"
    assert fw.grant_service.check(p2)[0] == "allow"
    assert sorted(e["name"] for e in fw._read_log) == ["一.txt", "二.txt"], \
        fw._read_log
    assert fw._grant_queues == {} and fw._grant_cards == {}


# ============================================================
# 3. bug 3：恢复动作必须认 gid
# ============================================================
def test_web_request_without_stash_leaves_desktop_chain_alone(chain):
    """网页端发起的读取没有暂存桌面动作链，答它不许动桌面正在跑的链。"""
    fw, make, events, bubbles = chain
    path = make("只在网页要.txt")
    keep = {"action": "todo_add", "text": "别丢我这一步"}
    fw._action_queue = [keep]
    fw._busy = True

    res = fw._anchor_ask(path, source="web")
    assert res["verdict"] == "grant" and res["gid"]
    assert res["gid"] not in fw._grant_queues, "没有桌面动作链就不该凭空造一份"

    fw._anchor_answer(res["gid"], DECISION_DENY)
    assert fw._action_queue == [keep], "别人的拒绝不能清空桌面队列"
    assert fw._busy is True


def test_answering_stale_gid_cannot_consume_live_queue(chain):
    fw, make, events, bubbles = chain
    p_new, p_stale = make("新请求.txt"), make("旧请求.txt")
    act_new = {"action": "read_doc", "path": p_new}
    fw.ask_grant(p_new, source="desktop", action=act_new)
    gid_new = next(iter(fw._grant_cards))

    stale = "不存在的那个gid"
    res = fw.answer_grant(stale, DECISION_DENY)
    assert res["ok"] is False and res.get("newly") is False
    # 队列还在，而且新请求的卡还亮着
    assert fw._grant_queues[gid_new] == [act_new]
    assert fw._grant_cards[gid_new].isVisible()
    assert events == [], "答一条不存在的请求不该惊动任何人"


# ============================================================
# 4. 用户自己点按钮 / 直接关卡 / 超时：三种收尾都要诚实
# ============================================================
def test_deny_drops_only_that_action_and_keeps_the_rest(chain):
    fw, make, events, bubbles = chain
    path = make("不给读.txt")
    keep = {"action": "todo_add", "text": "后面这一步照常做"}
    _start_chain(fw, path, extra=[keep])
    gid = next(iter(fw._grant_cards))

    fw._on_grant_decided(gid, DECISION_DENY)      # 真点「拒绝」按钮走的路
    assert fw._action_queue == [keep], fw._action_queue
    assert fw.grant_service.check(path)[0] == "grant"  # 依然没放开
    assert any("不读" in b for b in bubbles), bubbles


def test_card_closed_without_click_is_one_deny(chain):
    """用户不点按钮直接关掉卡片 = 这次不读，但只能表态一次，且后面的动作要接着跑。"""
    fw, make, events, bubbles = chain
    path = make("随手关掉.txt")
    keep = {"action": "todo_add", "text": "关卡之后也要跑"}
    _start_chain(fw, path, extra=[keep])
    gid, card = next(iter(fw._grant_cards.items()))

    card.close()
    _APP.processEvents()
    assert events == [(gid, DECISION_DENY)], events
    assert fw._grant_cards == {}, "关掉的卡必须从登记里摘掉"
    # 关卡之后 keep 不能被吞掉。它有两种合法落点：还在队列里等着，或已经
    # 在同一轮事件循环里跑完。原先这里写死 `_action_queue == [keep]`，等于
    # 规定「closeEvent 之后不得继续推进队列」——那是时序细节，不是产品语义，
    # 全量跑时前面用例留下的 pending 定时器会让队列提前被消费，于是假红。
    ran = any("关卡之后也要跑" in b for b in bubbles)
    assert fw._action_queue == [keep] or ran, \
        "关掉卡片不能吞掉后面的动作：队列=%r 气泡=%r" % (fw._action_queue, bubbles)


def test_timeout_is_reported_as_timeout_not_as_user_refusal(chain):
    fw, make, events, bubbles = chain
    path = make("没人应答.txt")
    fw.grant_service.timeout_sec = 0
    action = {"action": "read_doc", "path": path}
    fw.ask_grant(path, source="desktop", action=action)
    gid = next(iter(fw._grant_cards))
    time.sleep(0.01)

    fw._grant_expire_tick()
    assert fw._grant_cards == {}, "超时的卡要收掉"
    assert any("等太久" in b and "按拒绝" in b for b in bubbles), bubbles
    assert fw._grant_queues == {}


# ============================================================
# 5. 反方向：桌面回答网页发起的请求，档位不越界
# ============================================================
def test_slot_exception_cannot_abort_process(chain, monkeypatch):
    """PyQt 对槽函数里的未捕获异常直接 abort 进程（连 traceback 都不留）。

    这一条钉的是「壳必须兜住」：授权收尾任何一步炸了，也只能老实说没读到，
    不许把整个桌宠带走。
    """
    fw, make, events, bubbles = chain
    monkeypatch.setattr(fw, "_apply_grant_resolution",
                        lambda gid, decision: 1 / 0)
    fw._on_grant_resolved("gid随便", DECISION_FILE)     # 不崩就是过
    assert any("没处理好" in b and "没读" in b for b in bubbles), bubbles


def test_pending_grant_is_not_read_as_dict(chain):
    """`grant_service.get()` 返回 PendingGrant 对象，不是字典。

    原代码写成 `info.get("timed_out")`：属性不存在 → 槽里抛异常 → 进程原生崩溃，
    真机上点授权卡任何一个按钮都会把桌宠弄没。
    """
    fw, make, events, bubbles = chain
    from framework.grant_service import PendingGrant
    path = make("对象不是字典.txt")
    r = fw.ask_grant(path, source="desktop",
                     action={"action": "read_doc", "path": path})
    info = fw.grant_service.get(r["gid"])
    assert isinstance(info, PendingGrant)
    assert not hasattr(info, "get"), "PendingGrant 一旦有 get()，这条就该改回字典口径"
    fw._anchor_answer(r["gid"], DECISION_TREE)      # 走真槽：不崩就是过
    assert fw.grant_service.check(path)[0] == "allow"


def test_desktop_answers_web_request_with_tree_level(chain):
    fw, make, events, bubbles = chain
    target = make("能被读.txt", "整文件夹放行")
    sibling = make("隔壁.txt", "同目录另一个文件")

    res = fw._anchor_ask(target, source="web")
    gid = res["gid"]
    assert fw._grant_cards[gid].isVisible(), "网页发起的请求也要在桌面亮卡"

    fw._on_grant_decided(gid, DECISION_TREE)
    assert fw.grant_service.check(target)[0] == "allow"
    assert fw.grant_service.check(sibling)[0] == "allow", "选了整文件夹就该覆盖同目录"
    assert events == [(gid, DECISION_TREE)], events
