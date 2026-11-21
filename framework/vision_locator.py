# -*- coding: utf-8 -*-
"""传统 CV 屏幕控件定位：为 screen_locate 动作提供「候选区域生成 + 模板匹配」。

为什么单独一层：
    本项目原来的屏幕理解只有 see_screen（截图交给视觉大模型）。那条路能回答
    "屏幕上写了什么"，但给不出**控件坐标**，没有 Key 时也完全不可用。
    工业视觉的定位引导通常是「粗定位 → 精确认」两段式，这里补上粗定位段：
    只用 OpenCV 的传统算子，不依赖任何深度学习权重。

算法（纯传统 CV，面试可讲）：
    1. 灰度 → 高斯去噪
    2. Canny 边缘
    3. 形态学闭运算，把断开的边缘连成控件外框
    4. 轮廓分析，取外接矩形
    5. 面积占比与宽高比过滤（去掉整屏和细碎噪点）
    6. 非极大值抑制（IoU 去重）
    有模板图时改走多尺度模板匹配（TM_CCOEFF_NORMED），定位更准。

边界：
    * cv2 / numpy 为可选依赖，缺失时 is_available() 返回 False，调用方如实降级；
    * 纯函数、不碰 Qt，无界面环境可直接单测；
    * 只读图片，不写用户文件，标注图由调用方指定输出路径。
"""

import os

from framework.utils import logger

# 可选依赖：缺了就整个模块不可用，由调用方给出人话提示
try:
    import cv2
    import numpy as np
except Exception:  # noqa: BLE001
    cv2 = None
    np = None

# 候选框过滤阈值：占全图面积的比例
_MIN_AREA_RATIO = 0.0004
_MAX_AREA_RATIO = 0.35
# 宽高比合理区间：UI 控件极少极端细长
_MIN_ASPECT = 0.08
_MAX_ASPECT = 12.0

# 模板匹配默认尺度：UI 元素在不同 DPI 下大概率落在这个区间
DEFAULT_SCALES = (0.6, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5)

# 检测参数默认值：工业视觉软件必须让人能调阈值，
# 所以这些不再是写死的常量，而是参数面板的初值。
DEFAULT_PARAMS = {
    "canny_low": 50,
    "canny_high": 150,
    "min_area_ratio": _MIN_AREA_RATIO,
    "max_area_ratio": _MAX_AREA_RATIO,
    "min_aspect": _MIN_ASPECT,
    "max_aspect": _MAX_ASPECT,
    "nms_iou": 0.45,
    "max_candidates": 8,
    "template_threshold": 0.55,
}


def _merge_params(params=None) -> dict:
    """把调用方传入的参数合并到默认值上，缺项用默认。"""
    merged = dict(DEFAULT_PARAMS)
    if params:
        merged.update({k: v for k, v in params.items() if v is not None})
    return merged


def is_available() -> bool:
    """OpenCV 与 numpy 是否可用。缺依赖时调用方应如实降级。"""
    return cv2 is not None and np is not None


def _read_image(path: str):
    """读图：Windows 下 cv2.imread 不支持中文路径，统一走 np.fromfile。"""
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        raise ValueError("图片为空或路径不可读")
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("图片解码失败")
    return img


def _iou(a, b) -> float:
    """两个 [x, y, w, h] 框的交并比。"""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _nms(candidates: list, max_results: int, iou_threshold: float = 0.45) -> list:
    """按得分降序做非极大值抑制，返回去重后的 Top-K。"""
    ordered = sorted(candidates, key=lambda c: c["score"], reverse=True)
    kept = []
    for cand in ordered:
        if all(_iou(cand["bbox"], k["bbox"]) <= iou_threshold for k in kept):
            kept.append(cand)
            if len(kept) >= max_results:
                break
    return kept


def generate_candidates(image, max_candidates: int = 8,
                        params: dict = None) -> list:
    """边缘 + 连通域生成控件候选框。

    :param image: BGR ndarray
    :param params: 可调参数（见 DEFAULT_PARAMS），缺项用默认
    :return: [{"bbox": [x, y, w, h], "score": float, "source": "edge"}]
    """
    p = _merge_params(params)
    height, width = image.shape[:2]
    total = float(height * width) or 1.0

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, int(p["canny_low"]), int(p["canny_high"]))

    # 闭运算把控件边框的断点接上，避免一个按钮被拆成四条边
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)

    # OpenCV 3 返回三元组、4 返回二元组，取倒数第二个都能拿到轮廓
    found = cv2.findContours(closed, cv2.RETR_EXTERNAL,
                             cv2.CHAIN_APPROX_SIMPLE)
    contours = found[-2] if len(found) >= 2 else []

    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < 8 or h < 8:
            continue
        area_ratio = (w * h) / total
        if not (p["min_area_ratio"] <= area_ratio <= p["max_area_ratio"]):
            continue
        aspect = w / float(h)
        if not (p["min_aspect"] <= aspect <= p["max_aspect"]):
            continue
        # 矩形度：轮廓面积 / 外接矩形面积，越接近 1 越像规则控件
        contour_area = float(cv2.contourArea(contour))
        fill = contour_area / float(w * h) if w * h else 0.0
        score = 0.65 * fill + 0.35 * min(
            1.0, area_ratio / max(p["max_area_ratio"], 1e-9))
        candidates.append({
            "bbox": [int(x), int(y), int(w), int(h)],
            "score": round(float(score), 4),
            "source": "edge",
        })

    logger.info(f"[VisionLocator] 边缘候选 {len(candidates)} 个")
    return _nms(candidates, max_candidates, p["nms_iou"])


def match_template(image, template, max_candidates: int = 8,
                   threshold: float = None,
                   scales=DEFAULT_SCALES, params: dict = None) -> list:
    """多尺度模板匹配，返回按相似度排序的候选框。

    :param image: 待搜索的 BGR ndarray
    :param template: 模板 BGR ndarray
    :return: [{"bbox": [x, y, w, h], "score": float, "source": "template"}]
    """
    p = _merge_params(params)
    if threshold is None:
        threshold = p["template_threshold"]
    img_h, img_w = image.shape[:2]
    candidates = []
    for scale in scales:
        tpl = cv2.resize(template, None, fx=scale, fy=scale)
        tpl_h, tpl_w = tpl.shape[:2]
        if tpl_h < 6 or tpl_w < 6 or tpl_h > img_h or tpl_w > img_w:
            continue
        result = cv2.matchTemplate(image, tpl, cv2.TM_CCOEFF_NORMED)
        # 只保留局部极大值，避免同一目标在阈值以上连成一片
        dilated = cv2.dilate(result, np.ones((5, 5), np.uint8))
        peaks = (result >= dilated - 1e-6) & (result >= threshold)
        ys, xs = np.where(peaks)
        # 纯色区域会让整片像素都成为「局部极大值」，先截到最有希望的
        # 一小批再做 NMS，避免几十万个候选拖慢主线程
        limit = max_candidates * 50
        if len(ys) > limit:
            scores = result[ys, xs]
            order = np.argsort(scores)[::-1][:limit]
            ys, xs = ys[order], xs[order]
        for y, x in zip(ys, xs):
            candidates.append({
                "bbox": [int(x), int(y), int(tpl_w), int(tpl_h)],
                "score": round(float(result[y, x]), 4),
                "source": "template",
            })

    logger.info(f"[VisionLocator] 模板候选 {len(candidates)} 个")
    return _nms(candidates, max_candidates, p["nms_iou"])


def locate(image_path: str, target: str = "", template_path: str = "",
           max_candidates: int = 8, params: dict = None) -> dict:
    """统一入口：有模板走模板匹配，没模板走边缘候选。

    :return: {"ok": bool, "method": str, "candidates": [...], "reason": str}
    """
    if not is_available():
        return {"ok": False, "method": "", "candidates": [],
                "reason": "缺少 OpenCV 依赖，装上 opencv-python 才能做视觉定位"}
    try:
        image = _read_image(image_path)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "method": "", "candidates": [],
                "reason": f"屏幕图片读取失败（{type(e).__name__}）"}

    if template_path:
        try:
            template = _read_image(template_path)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "method": "template", "candidates": [],
                    "reason": f"模板图片读取失败（{type(e).__name__}）"}
        candidates = match_template(image, template,
                                    max_candidates=max_candidates,
                                    params=params)
        method = "template"
    else:
        candidates = generate_candidates(image,
                                         max_candidates=max_candidates,
                                         params=params)
        method = "edge"

    return {"ok": True, "method": method, "candidates": candidates,
            "reason": "" if candidates else "没有找到可用的候选区域",
            "target": str(target or "")}


def annotate(image_path: str, candidates: list, out_path: str) -> bool:
    """把候选框画到截图上存成文件，供界面展示或人工核对。

    :return: 是否成功写出文件
    """
    if not is_available() or not candidates:
        return False
    try:
        image = _read_image(image_path)
        for index, cand in enumerate(candidates, 1):
            x, y, w, h = cand["bbox"]
            color = (0, 200, 120) if index == 1 else (0, 165, 255)
            cv2.rectangle(image, (x, y), (x + w, y + h), color, 2)
            cv2.putText(image, f"{index}: {cand['score']:.2f}",
                        (x, max(12, y - 6)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, color, 1, cv2.LINE_AA)
        ok, buffer = cv2.imencode(".jpg", image)
        if not ok:
            return False
        buffer.tofile(out_path)
        return os.path.exists(out_path)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[VisionLocator] 标注图写出失败: {type(e).__name__}")
        return False
