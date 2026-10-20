# -*- coding: utf-8 -*-
"""以真实素材帧为底，补齐素材包缺失或过薄的动画。

素材包一般只提供 待机/跑/跳，而框架需要六个姿态。这里不改画风地"加装饰"：
以真实素材帧为底（保证角色本身 100% 一致），叠加手绘符号或几何形变。

    think      歪头（角度逐帧变化）+ 思考泡泡问号
    searching  左右张望箭头 + 身体轻微平移
    talk       上下弹跳 + 飘出的音符
    jump       挤压/拉伸补帧：素材只有 Jump_Up / Fall_Down 两张真实帧，
               绕脚底线做 squash & stretch 合成 8 帧完整跳跃弧
               （下蹲蓄力 -> 起跳拉伸 -> 腾空 -> 下落 -> 落地压缩 -> 恢复）
               帧内只做形变不做位移——跳跃的位移由 PetWindow.move_to 完成

配色取自 Segel 骑士素材的描边色（深灰蓝 #484A58），换素材时改 OUT 即可。

用法（先跑 tools/prepare_character_frames.py 装好 idle/jump）:
    python tools/make_derived_poses.py
    python tools/make_derived_poses.py --character knight --frames 6
    python tools/make_derived_poses.py --only jump     # 只重做 jump 补帧
"""

import argparse
import glob
import math
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAR_ROOT = os.path.join(ROOT, "assets", "characters")
FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

# 主画布与规范（与 prepare_character_frames.py 保持一致）
MASTER = 512
FEET_Y = 464                    # 512 * 0.90625

# 配色
OUT = (72, 74, 88)              # 描边（骑士素材的深灰蓝）
WHITE = (255, 255, 255, 235)
BLUE = (96, 106, 200)           # 思考问号
RED = (224, 92, 92)             # 音符


def font(name_candidates, size):
    """按候选顺序找一个存在的字体；音符等符号必须用 seguisym 才有字形"""
    for name in name_candidates:
        try:
            return ImageFont.truetype(os.path.join(FONT_DIR, name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def squash_stretch(im, sy):
    """绕脚底线的纵向挤压/拉伸，水平按半体积守恒反向补偿。

    底边与水平中心锚定在原位，故帧间脚底/中心零漂移——这是把
    两张真实帧撑成完整跳跃弧而不会让角色"踩空"的关键。
    """
    if abs(sy - 1.0) < 0.01:
        return im.copy()
    bbox = im.split()[3].getbbox()
    if bbox is None:
        return im.copy()
    body = im.crop(bbox)
    sx = min(1.06, max(0.94, sy ** -0.6))
    w = max(1, int(body.width * sx))
    h = max(1, int(body.height * sy))
    body = body.resize((w, h), Image.BICUBIC)
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    cx = (bbox[0] + bbox[2]) // 2
    out.alpha_composite(body, (cx - w // 2, bbox[3] - h))
    return out


# think 歪头角度（度）：负值朝一侧歪，幅度逐帧摆动，配合泡泡浮动保证 6 帧全不同
THINK_TILT = [-6, -4, -5, -7, -5, -4]


def make_think(idle, count):
    """歪头（角度逐帧变化）+ 泡泡问号"""
    f_q = font(["segoeuib.ttf", "segoeui.ttf", "arialbd.ttf"], 64)
    for i in range(count):
        tilt = THINK_TILT[i % len(THINK_TILT)]
        base = idle[0].rotate(tilt, center=(MASTER // 2, FEET_Y),
                              resample=Image.BICUBIC)
        d = ImageDraw.Draw(base)
        bob = int(4 * math.sin(i * math.pi / 3))      # 上下浮动
        d.ellipse([318, 118 + bob, 346, 146 + bob], fill=WHITE, outline=OUT, width=5)
        d.ellipse([352, 74 + bob, 412, 134 + bob], fill=WHITE, outline=OUT, width=6)
        d.text((366, 78 + bob), "?", font=f_q, fill=BLUE,
               stroke_width=3, stroke_fill=WHITE)
        yield base


def make_searching(idle, count):
    """左右张望箭头 + 身体轻移"""
    for i in range(count):
        shift = -6 if i % 3 == 0 else (6 if i % 3 == 2 else 0)
        base = idle[i % len(idle)].transform(
            (MASTER, MASTER), Image.AFFINE, (1, 0, shift, 0, 1, 0),
            resample=Image.BICUBIC)
        d = ImageDraw.Draw(base)
        side = -1 if (i // 3) % 2 == 0 else 1         # 前半朝左看，后半朝右看
        ax, ay = MASTER // 2 + side * 74, 96
        d.line([ax - side * 34, ay, ax + side * 10, ay], fill=OUT, width=10)
        d.polygon([(ax + side * 10, ay - 16), (ax + side * 10, ay + 16),
                   (ax + side * 30, ay)], fill=OUT)
        yield base


def make_talk(idle, count):
    """上下弹跳 + 音符飘出"""
    f_note = font(["seguisym.ttf", "segoeui.ttf"], 56)
    f_note_s = font(["seguisym.ttf", "segoeui.ttf"], 40)
    for i in range(count):
        dy = int(-7 * abs(math.sin(i / count * 2 * math.pi)))
        base = idle[i % len(idle)].transform(
            (MASTER, MASTER), Image.AFFINE, (1, 0, 0, 0, 1, dy),
            resample=Image.BICUBIC)
        d = ImageDraw.Draw(base)
        rise = i * 6                                  # 音符逐帧上飘
        d.text((356, 132 - rise), "\u266A" if i % 2 == 0 else "\u266B",
               font=f_note, fill=RED, stroke_width=3, stroke_fill=WHITE)
        if i >= 2:
            d.text((394, 118 - rise), "\u266A", font=f_note_s, fill=RED,
                   stroke_width=3, stroke_fill=WHITE)
        yield base


# 跳跃弧的挤压/拉伸表：(源帧, 纵向缩放)。源帧来自素材包仅有的两张真实帧：
# jump_src[0] = Jump_Up，jump_src[1] = Fall_Down。14fps 下一跳 560ms ≈ 8 帧。
JUMP_CYCLE = [
    (0, 0.94),    # 下蹲蓄力
    (0, 1.06),    # 起跳拉伸
    (0, 1.00),    # 上升
    (1, 1.03),    # 顶端
    (1, 1.05),    # 下落拉伸
    (1, 0.93),    # 落地压缩
    (0, 0.97),    # 缓冲恢复
    (0, 1.02),    # 回到腾空
]


def make_jump(jump_src, count):
    """挤压/拉伸补帧：两张真实帧撑成完整跳跃弧，脚底锚定不动"""
    for i in range(count):
        src_idx, sy = JUMP_CYCLE[i % len(JUMP_CYCLE)]
        src = jump_src[src_idx % len(jump_src)]
        yield squash_stretch(src, sy)


BUILDERS = {"think": make_think, "searching": make_searching,
            "talk": make_talk, "jump": make_jump}

# builder 需要的非 idle 源姿态（缺省以 idle 为底）；jump 以已装好的 jump 帧为底
SOURCE_POSE = {"jump": "jump"}

# 各姿态默认帧数（--frames 可整体覆盖）
DEFAULT_FRAMES = {"jump": 8, "idle": 6, "walk": 6,
                  "think": 6, "searching": 6, "talk": 6}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--character", default=None,
                    help="角色目录名（默认自动找第一个含 idle 的目录）")
    ap.add_argument("--frames", type=int, default=None,
                    help="每组帧数（默认按 DEFAULT_FRAMES，jump=8 其余 6）")
    ap.add_argument("--only", action="append", default=[],
                    choices=list(BUILDERS), help="只生成指定姿态，可重复")
    args = ap.parse_args()

    # ---- 定位角色目录 ----
    if args.character:
        char_dir = os.path.join(CHAR_ROOT, args.character)
    else:
        # 兼容两种布局：<CHAR_ROOT>/<角色>/idle 或 <CHAR_ROOT>/idle（扁平）
        candidates = [d for d in glob.glob(os.path.join(CHAR_ROOT, "*"))
                      if os.path.isdir(os.path.join(d, "idle"))]
        if candidates:
            char_dir = candidates[0]
        elif os.path.isdir(os.path.join(CHAR_ROOT, "idle")):
            char_dir = CHAR_ROOT
        else:
            print("找不到含 idle/ 的角色目录，请先跑 prepare_character_frames.py")
            return 1
    print(f"角色目录: {char_dir}")

    # ---- 先把所有源帧读进内存（jump 的源目录就是输出目录，必须先读后清）----
    sources = {}
    for pose in (args.only or list(BUILDERS)):
        src_dir = os.path.join(char_dir, SOURCE_POSE.get(pose, "idle"))
        files = sorted(glob.glob(os.path.join(src_dir, "*.png")))
        if not files:
            print(f"{pose}: 源目录 {os.path.relpath(src_dir, ROOT)} 没有帧，跳过")
            continue
        if pose == "jump" and len(files) > 4:
            print(f"{pose}: 检测到 {len(files)} 帧——jump 目录里已是补帧产物，"
                  f"重复补帧会叠加形变。请先重跑 prepare_character_frames.py "
                  f"恢复原始帧再补。")
            return 1
        sources[pose] = [Image.open(p).convert("RGBA") for p in files]
    if not sources:
        print("没有任何可用的源帧")
        return 1
    print(f"以 {', '.join(f'{p}x{len(v)}' for p, v in sources.items())} 为底\n")

    # ---- 清空输出目录并重建 ----
    for pose, src_frames in sources.items():
        outdir = os.path.join(char_dir, pose)
        os.makedirs(outdir, exist_ok=True)
        for old in glob.glob(os.path.join(outdir, "*.png")):
            os.remove(old)
        n = args.frames or DEFAULT_FRAMES.get(pose, 6)
        count = 0
        for i, frame in enumerate(BUILDERS[pose](src_frames, n)):
            frame.save(os.path.join(outdir, f"{pose}_{i:03d}.png"))
            count += 1
        print(f"  {pose:10} 生成 {count} 帧 -> {os.path.relpath(outdir, ROOT)}")

    print("\n完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
