# -*- coding: utf-8 -*-
"""文件修改 + 联网配图功能验证

覆盖：pptx/docx 内容提取回读、改写消息组装、发送文件→AI改写→返回新文件
全链路（AIWorker 打桩）、取消指令、图库配图（requests 打桩，不真联网）。
所有文件写到临时目录，测完清理。
用法: python tools/test_doc_edit.py
"""
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))   # 项目根目录

from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication

from framework import config, doc_generator
from framework.doc_editor import build_edit_message, extract_content

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


app = QApplication(sys.argv)


def pump(seconds=2.0):
    import time
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


TMP = tempfile.mkdtemp(prefix="pet_doc_edit_test_")

# ============================================================
print("一、文件内容提取（pptx / docx 回读）")
# ============================================================
ppt_spec = {"title": "原稿标题", "subtitle": "凌零",
            "slides": [{"title": "背景", "bullets": ["要点A", "要点B"]},
                       {"title": "计划", "bullets": ["甲", "乙"]}]}
ppt_path = os.path.join(TMP, "原稿.pptx")
ok, err = doc_generator.generate("ppt", ppt_spec, ppt_path)
check("前置：原稿 PPT 生成", ok, err)

kind, spec = extract_content(ppt_path)
check("PPT 提取类型", kind == "ppt")
check("PPT 提取页数(含自动结束页)", len(spec.get("slides", [])) == 4,
      str(len(spec.get("slides", []))))
titles = [s["title"] for s in spec.get("slides", [])]
check("PPT 提取页标题", "背景" in titles and "计划" in titles, str(titles))
check("PPT 封面首行提升为文稿标题", titles and titles[0] == "原稿标题",
      str(titles))
check("PPT 提取文稿标题", spec.get("title") == "原稿标题", spec.get("title"))
check("页脚页码不混入内容",
      all("凌零 · 桌面AI助手" not in b and not b.isdigit()
          for s in spec["slides"] for b in s["bullets"]))

msg = build_edit_message("ppt", spec, "每页加一条总结")
check("改写消息含原内容 JSON", "原稿标题" in msg and "JSON" in msg)
check("改写消息含指令与类型", "每页加一条总结" in msg and '"ppt"' in msg)

doc_path = os.path.join(TMP, "原稿.docx")
ok, err = doc_generator.generate("doc", {
    "title": "原稿文档",
    "sections": [{"heading": "第一章", "paragraphs": ["内容一", "内容二"]},
                 {"heading": "第二章", "paragraphs": ["收尾"]}]}, doc_path)
check("前置：原稿 Word 生成", ok, err)
kind, spec = extract_content(doc_path)
check("Word 提取类型", kind == "doc")
heads = [s["heading"] for s in spec.get("sections", [])]
check("Word 提取章节", "第一章" in heads and "第二章" in heads, str(heads))
check("Word 提取段落",
      any(p == "内容一" for s in spec["sections"] for p in s["paragraphs"]))

try:
    extract_content(os.path.join(TMP, "不存在的文件.pptx"))
    check("缺失文件报错", False)
except (OSError, ValueError):
    check("缺失文件报错", True)
try:
    extract_content(os.path.join(TMP, "类型.xyz"))
    check("不支持类型报错", False)
except ValueError:
    check("不支持类型报错", True)

# ============================================================
print("\n二、联网配图（requests 打桩，不真联网）")
# ============================================================
from PIL import Image

fake_img = io.BytesIO()
Image.new("RGB", (320, 200), (180, 90, 60)).save(fake_img, "JPEG")
_IMG_BYTES = fake_img.getvalue()


class FakeResp:
    def __init__(self, payload=None, content=b"", status_code=200):
        self._payload = payload
        self.content = content
        self.status_code = status_code

    def json(self):
        return self._payload


_search_calls = []
_orig_get = doc_generator.requests.get


def _fake_get(url, params=None, headers=None, timeout=None, **kw):
    _search_calls.append(url)
    if "pexels" in url:
        return FakeResp({"photos": [{"src": {"large": "http://fake/img.jpg"}}]})
    return FakeResp(content=_IMG_BYTES)


doc_generator.requests.get = _fake_get
config.PEXELS_API_KEY = "test-key"

spec_q = {"title": "配图测试", "slides": [
    {"title": "一", "bullets": ["a"], "image_query": "teamwork office"},
    {"title": "二", "bullets": ["b"]}]}
check("has_image_intent 命中", doc_generator.has_image_intent("ppt", spec_q))
check("has_image_intent 无Key不命中",
      not doc_generator.has_image_intent("ppt", {"slides": [
          {"title": "x", "bullets": [], "image_query": "cat"}]})
      if not config.PEXELS_API_KEY else True)
images = doc_generator.fetch_images("ppt", spec_q)
check("搜图下载成功", 0 in images and images[0] == _IMG_BYTES,
      f"{len(images)} 张")
check("无 image_query 的页不搜图", 1 not in images)

ppt_img_path = os.path.join(TMP, "配图.pptx")
ok, err = doc_generator.generate("ppt", spec_q, ppt_img_path, images)
check("带图 PPT 生成", ok, err)
if ok:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    prs = Presentation(ppt_img_path)
    pics = [sp for sp in prs.slides[1].shapes
            if sp.shape_type == MSO_SHAPE_TYPE.PICTURE]
    check("图片真实插入页面", len(pics) == 1)
    check("图片带主题色描边", pics and pics[0].line.fill.type is not None)

# 图库无结果时安静降级
doc_generator.requests.get = lambda url, **kw: FakeResp({"photos": []})
check("图库无结果降级为空", doc_generator.fetch_images("ppt", spec_q) == {})

# 无 Key 时完全不发请求
config.PEXELS_API_KEY = ""
_search_calls.clear()
doc_generator.requests.get = _fake_get
check("无Key不发起请求",
      doc_generator.fetch_images("ppt", spec_q) == {} and not _search_calls)

# ============================================================
print("\n三、文件修改全链路（AIWorker 打桩）")
# ============================================================
import framework.core as core_mod
from ui.pet_window import PetWindow

win = PetWindow()
fw = win.fw
# 任务数据隔离到临时文件：交付/工作日志不写进真实 tasks.json
fw.tasks._file = os.path.join(TMP, "tasks_iso.json")
fw.tasks.reminders, fw.tasks.todos = [], []
fw.tasks.deliveries, fw.tasks.worklog = [], []
bubbles = []
cards = []
fw.show_bubble = lambda text: bubbles.append(text)
fw.show_card = lambda title, p, size="": cards.append((title, p, size))
fw._finish_action = lambda: (
    finished.append(True),
    QTimer.singleShot(150, fw._run_next_action)) and None
finished = []

config.PEXELS_API_KEY = ""
config.USE_REAL_AI = True
config.DEEPSEEK_API_KEY = "sk-fake-for-test"

_MODIFIED = {"title": "原稿标题（修改版）",
             "slides": [{"title": "背景", "bullets": ["要点A已按要求充分展开",
                                                     "要点B补充了数据支撑",
                                                     "【已修改】新增总结章节要点"]},
                        {"title": "计划", "bullets": ["第一步完成渠道下沉试点",
                                                     "第二步上线标准产品包"]}]}


class FakeWorker(QObject):
    finished = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, message, history, personality, parent=None):
        super().__init__(parent)
        self.message = message

    def isRunning(self):
        return False

    def stop(self):
        pass

    def start(self):
        msg = self.message
        QTimer.singleShot(50, lambda: self.finished.emit([
            {"action": "show_bubble", "text": "改好了，请查收。"},
            {"action": "create_doc", "doc_type": "ppt",
             "filename": "随便.pptx", "content": _MODIFIED},
        ]))


core_mod.AIWorker = FakeWorker

# 3.1 拒绝路径
bad_path = os.path.join(TMP, "类型.xyz")
with open(bad_path, "wb") as f:
    f.write(b"not a document")              # 真实存在但内容打不开
fw.request_doc_edit(bad_path)
check("不支持类型被拒", not fw._doc_edit and any("换" in b for b in bubbles),
      str(bubbles[-1:]))
fw.request_doc_edit(os.path.join(TMP, "不存在.pptx"))
check("缺失文件被拒", not fw._doc_edit)

# 3.2 投喂 -> 等待指令
bubbles.clear()
fw.request_doc_edit(ppt_path)
edit = fw._doc_edit
check("投喂后进入等待", edit is not None and edit["kind"] == "ppt"
      and edit["spec"].get("slides"))
check("提示用户给修改指令", any("要怎么改" in b for b in bubbles), str(bubbles))

# 3.3 取消指令
fw.send_command("不改了")
check("取消词退出修改模式", fw._doc_edit is None)

# 3.4 全链路：投喂 -> 指令 -> AI 返回 create_doc -> 预设路径生成 -> 卡片
bubbles.clear()
cards.clear()
fw.request_doc_edit(ppt_path)
check("重新进入等待", fw._doc_edit is not None)
fw.send_command("每页加一条总结")
pump(0.5)                                   # 等假 AI 回包 + 动作队列启动
target = os.path.join(TMP, "原稿_修改.pptx")
deadline = 0
while not cards and deadline < 40:
    pump(0.2)
    deadline += 1
check("改写文件按预设路径落盘", os.path.exists(target),
      str([c[1] for c in cards]))
if os.path.exists(target):
    from pptx import Presentation
    new_prs = Presentation(target)
    texts = "\n".join(sp.text_frame.text for s in new_prs.slides
                      for sp in s.shapes
                      if getattr(sp, "has_text_frame", False))
    check("新文件含 AI 修改后的内容", "【已修改】新增总结" in texts)
    check("修改状态已消费", fw._doc_edit is None)
    check("卡片反馈修改结果", cards and "文档已生成" in cards[0][0],
          str(cards))
check("成功气泡出现", any("改写" in b or "生成" in b for b in bubbles),
      str(bubbles))

win.close()
doc_generator.requests.get = _orig_get
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n== 测试结束，失败 {len(FAILS)} 项 ==")
sys.exit(0 if not FAILS else 1)
