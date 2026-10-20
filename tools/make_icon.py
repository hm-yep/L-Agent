# -*- coding: utf-8 -*-
"""应用图标生成：骑士助手（icon.ico / icon.png）

从 assets/characters/ 的骑士序列帧中取一帧，放在圆形徽章底上：
- 底：暖奶油色纵向渐变 + 应用主题橙色环（与 UI 配色一致）
- 角色：整身骑士（含剑），自动求解不超出圆形内切区的最大缩放
- 产出：assets/icon.png（512 主图）+ assets/icon.ico（7 档尺寸）

设计依据（数值分析结论，见 tools/ 同目录会话记录）：
- 骑士为银灰铠甲 + 深描边（#303030/#a0a0a0/#d0d0d0 占 74%），
  浅底上深描边轮廓对比最强，深底会吞掉描边 -> 选浅奶油底
- attack 姿态剑横挥，包围盒 345x373 近正方形，最适合圆形构图

用法:
    python tools/make_icon.py                     # 默认 attack_003
    python tools/make_icon.py --pose idle --frame 0
    python tools/make_icon.py --dry-run           # 只打印求解结果不写文件
"""

import argparse
import glob
import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER = 512            # 主画布边长（与 icon.png 历史尺寸一致）
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48),
             (64, 64), (128, 128), (256, 256)]   # 与旧 icon.ico 保持一致

# 圆形徽章几何
CX = CY = MASTER // 2
R_OUTER = 250           # 外圆半径
RING = 12               # 橙色环宽
R_INNER = R_OUTER - RING
INSET = 12              # 角色须距内圆边缘的最小留白

# 配色（与 ui/ 预览页、输入条的橙色主题一致）
RING_COLOR = (224, 123, 57)       # #e07b39
RING_EDGE = (166, 82, 30)         # 深一圈描边
GRAD_TOP = (255, 249, 238)        # 内圆顶部 #fff9ee
GRAD_BOTTOM = (255, 224, 174)     # 内圆底部 #ffe0ae
SHADOW = (120, 90, 60, 64)        # 脚下投影


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _disc_mask(r):
    m = Image.new("L", (MASTER, MASTER), 0)
    ImageDraw.Draw(m).ellipse(
        [CX - r, CY - r, CX + r, CY + r], fill=255)
    return m


def make_badge():
    """画圆形徽章底（返回 RGBA 图，角色稍后叠加）"""
    im = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))

    # 内圆：纵向渐变，裁进圆形蒙版
    grad = Image.new("RGBA", (MASTER, MASTER))
    gd = ImageDraw.Draw(grad)
    y0, y1 = CY - R_INNER, CY + R_INNER
    for y in range(y0, y1 + 1):
        t = (y - y0) / (y1 - y0)
        gd.line([(0, y), (MASTER, y)],
                fill=lerp(GRAD_TOP, GRAD_BOTTOM, t) + (255,))
    im.paste(grad, (0, 0), _disc_mask(R_INNER))

    # 橙色环（外圆减内圆）
    ring = Image.new("RGBA", (MASTER, MASTER), RING_COLOR + (255,))
    mask = _disc_mask(R_OUTER)
    ImageDraw.Draw(mask).ellipse(
        [CX - R_INNER, CY - R_INNER, CX + R_INNER, CY + R_INNER], fill=0)
    im.paste(ring, (0, 0), mask)

    d = ImageDraw.Draw(im)
    # 最外圈细描边
    d.ellipse([CX - R_OUTER + 1, CY - R_OUTER + 1,
               CX + R_OUTER - 1, CY + R_OUTER - 1],
              outline=RING_EDGE + (255,), width=3)
    return im


def draw_shadow(im, scaled, pos):
    """在角色实际落点画脚下投影（须在角色叠加之前画）"""
    x0, y0 = pos
    a = scaled.getchannel("A")
    w, h = a.size
    px = a.load()
    feet_row = [x for x in range(w) if px[x, h - 1] > 8] or \
               [x for x in range(w) if px[x, h - 2] > 8]
    if feet_row:
        cx = x0 + (feet_row[0] + feet_row[-1]) / 2
        rx = max(36, int((feet_row[-1] - feet_row[0]) * 0.85))
    else:
        cx, rx = CX, 60
    d = ImageDraw.Draw(im)
    fy = y0 + h - 8
    d.ellipse([cx - rx, fy - 13, cx + rx, fy + 13], fill=SHADOW)


def circle_violations(alpha, box_x0, box_y0, r_limit):
    """检查角色不透明像素是否都在内切圆内（留 INSET 边距）。

    返回违例行列表 [(y, x0, x1, 允许范围), ...]
    """
    w, h = alpha.size
    px = alpha.load()
    bad = []
    for y in range(h):
        xs = [x for x in range(w) if px[x, y] > 8]
        if not xs:
            continue
        Y = box_y0 + y
        dy2 = r_limit ** 2 - (Y - CY) ** 2
        if dy2 <= 0:
            bad.append((Y, box_x0 + xs[0], box_x0 + xs[-1], "出圆"))
            continue
        half = dy2 ** 0.5 - INSET
        lo, hi = CX - half, CX + half
        if box_x0 + xs[0] < lo or box_x0 + xs[-1] > hi:
            bad.append((Y, box_x0 + xs[0], box_x0 + xs[-1], (lo, hi)))
    return bad


def solve_placement(knight):
    """搜索不越圆的最大缩放与垂直位置（返回缩放后的图与粘贴坐标）"""
    # 裁到并集包围盒再缩放，避免透明边参与计算
    bbox = knight.getchannel("A").getbbox()
    knight = knight.crop(bbox)
    best = None
    for scale in [x / 100 for x in range(120, 54, -2)]:
        nw, nh = max(1, int(knight.width * scale)), max(1, int(knight.height * scale))
        scaled = knight.resize((nw, nh), Image.LANCZOS)
        for ycenter in range(CY - 14, CY + 15, 4):
            x0 = int(CX - nw / 2)
            y0 = int(ycenter - nh / 2)
            if y0 + nh < CY + 60:       # 脚至少到圆心下方
                continue
            bad = circle_violations(scaled.getchannel("A"), x0, y0, R_INNER)
            if not bad:
                if best is None or nw > best[0].width:
                    best = (scaled, x0, y0)
                break
        if best:
            break
    if best is None:
        raise SystemExit("!! 求解失败：任何缩放都无法放入圆内（检查素材）")
    return best


def build(frame_path):
    badge = make_badge()
    knight = Image.open(frame_path).convert("RGBA")
    scaled, x0, y0 = solve_placement(knight)
    draw_shadow(badge, scaled, (x0, y0))
    badge.alpha_composite(scaled, (x0, y0))
    return badge, scaled, (x0, y0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pose", default="attack")
    ap.add_argument("--frame", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    d = os.path.join(ROOT, "assets", "characters", args.pose)
    paths = sorted(glob.glob(os.path.join(d, "*.png")))
    if not paths:
        raise SystemExit(f"!! 找不到姿态目录: {d}")
    frame = paths[args.frame % len(paths)]
    print(f"素材帧: {os.path.relpath(frame, ROOT)}")

    im, scaled, (x0, y0) = build(frame)
    sw, sh = scaled.size
    print(f"角色缩放: {sw}x{sh}  粘贴位置: ({x0},{y0})  "
          f"占内圆直径比例: {sh / (2 * R_INNER) * 100:.0f}%")

    # 数值自检：圆形外必须全透明
    a = im.getchannel("A")
    w, h = im.size
    px = a.load()
    outside = 0
    for y in range(h):
        for x in range(w):
            if px[x, y] > 8:
                if (x - CX) ** 2 + (y - CY) ** 2 > (R_OUTER + 1) ** 2:
                    outside += 1
    print(f"圆外不透明像素: {outside} (应为 0)")

    if args.dry_run:
        print("[dry-run] 未写入文件")
        return

    png_path = os.path.join(ROOT, "assets", "icon.png")
    ico_path = os.path.join(ROOT, "assets", "icon.ico")
    im.save(png_path)
    im.save(ico_path, sizes=ICO_SIZES)
    print(f"已写入: {os.path.relpath(png_path, ROOT)}  {im.size}")
    print(f"已写入: {os.path.relpath(ico_path, ROOT)}  尺寸档: {[s[0] for s in ICO_SIZES]}")


if __name__ == "__main__":
    sys.exit(main())
