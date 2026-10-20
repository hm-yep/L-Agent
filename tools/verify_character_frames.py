# -*- coding: utf-8 -*-
"""校验 assets/characters/ 下的序列帧是否符合 sprite_manager 的播放约定。

检查项：
    1. 六个姿态目录是否齐全
    2. 每帧尺寸是否一致、是否 512x512
    3. 脚底基准线是否漂移（帧间抖动会让角色在桌面上"跳帧"）
    4. 水平中心是否漂移
    5. 是否存在重复帧（只有一个唯一帧 = 根本没在动）
    6. 是否存在近乎空白的帧（裁剪/缩放算错时会露出来）

用法:
    python tools/verify_character_frames.py
    python tools/verify_character_frames.py --render     # 附带离屏渲染一张总览图

退出码 0 = 全部通过，1 = 有问题。
"""

import argparse
import glob
import hashlib
import os
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAR_ROOT = os.path.join(ROOT, "assets", "characters")

REQUIRED_POSES = ["idle", "walk", "jump", "attack", "think", "searching", "talk"]
MASTER = 512
FEET_Y = int(MASTER * 0.90625)          # 464

# 容差（静止基准姿态的全局默认值）
FEET_TOLERANCE = 4                      # 脚底允许的像素漂移
CENTER_TOLERANCE = 8                    # 中心允许的像素漂移
MIN_OPAQUE = 300                        # 单帧最少不透明像素

# 表现性动作按设计摆幅放宽（缺省键回落到上面的全局值）。放宽的均为有意的
# 表现性动作，不是对齐缺陷：
#   walk      原素材 Run 本身的身体前后倾，中心实测 248~261（漂 13px）
#   searching 补画的 ±6px 张望摆动，中心实测 243~255（漂 12px）
#   talk      补画的 ±7px 说话弹跳，脚底实测 463~469（漂 6px）
POSE_TOLERANCE = {
    "walk":      {"center": 14},
    "searching": {"center": 14},
    "talk":      {"feet": 8},
    "attack":    {"center": 48},   # 挥剑弓步前冲，中心实测 220~264
}


def opaque_pixels(im) -> int:
    """alpha > 40 的像素数（用直方图，避开 Pillow 版本的 API 差异）"""
    hist = im.split()[3].histogram()
    return sum(hist[41:])


def check_pose(pose, chatty=True):
    """返回 (是否有问题, 统计信息 dict)"""
    d = os.path.join(CHAR_ROOT, pose)
    files = sorted(glob.glob(os.path.join(d, "*.png")))
    issues = []
    if not files:
        return ["目录不存在或没有 png"], {}

    tol = {"feet": FEET_TOLERANCE, "center": CENTER_TOLERANCE}
    tol.update(POSE_TOLERANCE.get(pose, {}))

    feet, centers, hashes, opaque = [], [], set(), []
    sizes = set()
    for f in files:
        im = Image.open(f).convert("RGBA")
        sizes.add(im.size)
        bbox = im.split()[3].getbbox()
        if bbox is None:
            issues.append(f"空帧 {os.path.basename(f)}")
            continue
        feet.append(bbox[3])
        centers.append((bbox[0] + bbox[2]) // 2)
        opaque.append(opaque_pixels(im))
        hashes.add(hashlib.md5(im.tobytes()).hexdigest())

    if len(sizes) > 1:
        issues.append(f"尺寸不一致 {sorted(sizes)}")
    if feet and (max(feet) - min(feet)) > tol["feet"]:
        issues.append(f"脚底漂移 {min(feet)}~{max(feet)}")
    if centers and (max(centers) - min(centers)) > tol["center"]:
        issues.append(f"中心漂移 {min(centers)}~{max(centers)}")
    if len(hashes) < 2:
        issues.append("只有一个唯一帧，没有动画")
    if opaque and min(opaque) < MIN_OPAQUE:
        issues.append(f"有帧近乎空白（最小 {min(opaque)} px）")

    stats = {
        "frames": len(files),
        "size": sorted(sizes)[0] if len(sizes) == 1 else "不一致",
        "feet": f"{min(feet)}~{max(feet)}" if feet else "-",
        "center": f"{min(centers)}~{max(centers)}" if centers else "-",
        "distinct": len(hashes),
        "min_opaque": min(opaque) if opaque else 0,
    }
    return issues, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", action="store_true",
                    help="额外离屏渲染一张六姿态总览图")
    args = ap.parse_args()

    header = (f"{'pose':<11}{'frames':>7}{'size':>11}{'feet_y':>10}"
              f"{'center_x':>11}{'distinct':>9}{'min_px':>8}")
    print(f"检查目录: {CHAR_ROOT}")
    print(header)
    print("-" * len(header))

    failed = 0
    for pose in REQUIRED_POSES:
        issues, st = check_pose(pose)
        if not st:
            print(f"{pose:<11}  {issues[0]}")
            failed += 1
            continue
        line = (f"{pose:<11}{st['frames']:>7}{str(st['size']):>11}"
                f"{st['feet']:>10}{st['center']:>11}"
                f"{st['distinct']:>9}{st['min_opaque']:>8}")
        if issues:
            failed += 1
            line += "   <-- " + "; ".join(issues)
        print(line)

    print()
    # 脚底是否落在规范线上（只对有脚的姿态有意义）
    print(f"规范：脚底应对齐 y={FEET_Y}（{MASTER}x{MASTER} 画布的 "
          f"{FEET_Y/MASTER*100:.2f}% 高度线）")

    if args.render:
        try:
            render_sheet()
        except Exception as exc:                    # noqa: BLE001
            print(f"离屏渲染跳过: {exc}")

    print()
    print("RESULT:", "ALL PASS" if failed == 0 else f"{failed} 个姿态有问题")
    return 1 if failed else 0


def render_sheet():
    """走真实代码路径渲染一张总览图，便于人工目视"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, ROOT)
    from PyQt5.QtGui import QPainter
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)   # noqa: F841
    from ui.sprite_manager import SpriteManager

    sm = SpriteManager()
    sm.set_size(128)
    cells = []
    for pose in REQUIRED_POSES:
        sm.set_pose(pose)
        for _ in range(12):
            sm.advance()
            img = sm.current_pixmap().toImage()
            painter = QPainter(img)
            sm.draw_overlay(painter, img.width())
            painter.end()
            cells.append((pose, img))

    cell, cols = 140, 12
    sheet = Image.new("RGBA", (cell * cols, cell * len(REQUIRED_POSES) + 20),
                      (250, 250, 252, 255))
    for idx, (pose, img) in enumerate(cells):
        row, col = idx // cols, idx % cols
        tmp = os.path.join(ROOT, "data", f"_cell_{idx}.png")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        img.save(tmp)
        with Image.open(tmp) as im:
            sheet.alpha_composite(im.convert("RGBA"),
                                  (col * cell + 6, row * cell + 4))
        os.remove(tmp)
    out = os.path.join(ROOT, "data", "character_render_sheet.png")
    sheet.convert("RGB").save(out)
    print(f"\n总览图已生成: {out}")
    print("（行序 = idle/walk/jump/think/searching/talk，每行 12 帧 = 循环两轮）")


if __name__ == "__main__":
    sys.exit(main())
