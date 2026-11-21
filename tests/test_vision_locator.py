# -*- coding: utf-8 -*-
"""传统 CV 视觉定位模块的单元测试。

只测纯函数层：不拉起 Qt、不截图、不调任何模型。
OpenCV 是可选依赖，缺了就整组跳过，不让主测试套件变红。
"""

import pytest

pytest.importorskip("cv2")
pytest.importorskip("numpy")

import cv2
import numpy as np

from framework import vision_locator as vl


def _make_canvas(width=640, height=480):
    """浅灰画布上画两个深色矩形，模拟按钮。"""
    img = np.full((height, width, 3), 235, dtype=np.uint8)
    cv2.rectangle(img, (120, 90), (340, 170), (40, 60, 200), -1)
    # 给按钮加独一无二的纹理：纯色块会在任意纯色区域匹配到 1.0，
    # 那样模板匹配测的就不是「定位」而是「找同色」
    cv2.line(img, (140, 120), (320, 120), (255, 255, 255), 3)
    cv2.line(img, (140, 145), (260, 145), (255, 255, 255), 2)
    cv2.circle(img, (300, 145), 10, (255, 255, 255), -1)
    cv2.rectangle(img, (420, 300), (560, 340), (60, 60, 60), -1)
    return img


def _save(img, path):
    """中文路径安全保存（cv2.imwrite 不支持中文路径）。"""
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    buf.tofile(str(path))


def test_iou_basic():
    assert vl._iou([0, 0, 10, 10], [0, 0, 10, 10]) == pytest.approx(1.0)
    assert vl._iou([0, 0, 10, 10], [20, 20, 10, 10]) == 0.0
    assert 0 < vl._iou([0, 0, 10, 10], [5, 5, 10, 10]) < 1


def test_nms_deduplicates_overlapping_boxes():
    candidates = [
        {"bbox": [10, 10, 50, 50], "score": 0.9, "source": "edge"},
        {"bbox": [12, 12, 50, 50], "score": 0.8, "source": "edge"},
        {"bbox": [200, 200, 50, 50], "score": 0.7, "source": "edge"},
    ]
    kept = vl._nms(candidates, max_results=5, iou_threshold=0.3)
    assert len(kept) == 2
    assert kept[0]["score"] == 0.9


def test_read_image_supports_chinese_path(tmp_path):
    folder = tmp_path / "中文目录"
    folder.mkdir()
    path = folder / "屏幕截图.jpg"
    _save(_make_canvas(), path)

    img = vl._read_image(str(path))
    assert img.shape[0] == 480
    assert img.shape[1] == 640


def test_generate_candidates_finds_rectangles(tmp_path):
    path = tmp_path / "screen.jpg"
    _save(_make_canvas(), path)

    result = vl.locate(str(path), target="按钮")
    assert result["ok"] is True
    assert result["method"] == "edge"
    assert result["candidates"]
    for cand in result["candidates"]:
        x, y, w, h = cand["bbox"]
        assert w > 0 and h > 0
        assert 0.0 <= cand["score"] <= 1.0


def test_template_match_locates_exact_crop(tmp_path):
    canvas = _make_canvas()
    template = canvas[90:170, 120:340]
    screen_path = tmp_path / "screen.jpg"
    template_path = tmp_path / "template.jpg"
    _save(canvas, screen_path)
    _save(template, template_path)

    result = vl.locate(str(screen_path), target="按钮",
                       template_path=str(template_path))
    assert result["ok"] is True
    assert result["method"] == "template"
    assert result["candidates"]

    top = result["candidates"][0]
    assert top["score"] > 0.9
    x, y, w, h = top["bbox"]
    # JPEG 压缩与多尺度搜索允许 1~3 像素误差
    assert abs(x - 120) <= 3
    assert abs(y - 90) <= 3
    assert abs(w - 220) <= 3
    assert abs(h - 80) <= 3


def test_annotate_writes_output(tmp_path):
    canvas = _make_canvas()
    screen_path = tmp_path / "screen.jpg"
    _save(canvas, screen_path)

    candidates = [{"bbox": [120, 90, 220, 80], "score": 0.9,
                   "source": "edge"}]
    out_path = tmp_path / "标注结果.jpg"
    assert vl.annotate(str(screen_path), candidates, str(out_path)) is True
    assert out_path.exists()
    assert out_path.stat().st_size > 0


def test_annotate_without_candidates_returns_false(tmp_path):
    canvas = _make_canvas()
    screen_path = tmp_path / "screen.jpg"
    _save(canvas, screen_path)
    assert vl.annotate(str(screen_path), [], str(tmp_path / "out.jpg")) is False


def test_locate_reports_unreadable_file(tmp_path):
    result = vl.locate(str(tmp_path / "不存在.jpg"), target="按钮")
    assert result["ok"] is False
    assert result["candidates"] == []
    assert result["reason"]


def test_is_available_true_when_cv2_imported():
    assert vl.is_available() is True
