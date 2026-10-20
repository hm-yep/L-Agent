# -*- coding: utf-8 -*-
"""把第三方精灵图素材预处理成助手可直接播放的 PNG 序列帧。

输出会被 ui/sprite_manager.py 自动识别（优先级：序列帧 > GIF > 程序化绘制）。

【输出规范】—— sprite_manager 的约定，改这里必须同步改那边
    assets/characters/<姿态>/<姿态>_000.png ...
    - 全部帧朝右（框架用水平翻转表示朝左）
    - 统一 512x512 主画布
    - 脚底对齐画布 90.6% 高度线、水平居中
      （该比例取自旧版程序化橘猫，粒子特效锚点依赖它，不要随意改）

【为什么统一包围盒】
    逐帧各自裁剪会让角色在播放时抖动。这里取所有用到动画的联合包围盒一次裁掉，
    保证帧间零漂移。

用法:
    # 用内置预设（OpenGameArt 的 Segel 系列）
    python tools/prepare_character_frames.py knight --src "D:/素材/CHIBI KNIGHT-PNG"

    # 自定义映射（把素材子目录映射到姿态名）
    python tools/prepare_character_frames.py mychar --src "D:/素材/xxx" \
        --pose idle=01-Idle --pose walk=02-Run --pose jump=04-Jump

    --flip / --no-flip   素材朝左时加 --flip（默认按预设）
    --dry-run            只打印计算结果，不写文件
"""

import argparse
import glob
import os
import shutil
import sys

try:
    from PIL import Image
except ImportError:
    print("需要 Pillow：python -m pip install Pillow")
    sys.exit(1)

# 项目根目录（本脚本在 tools/ 下）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.path.join(ROOT, "assets", "characters")

# ---- 输出规范常量 ----
MASTER = 512                      # 主画布边长
FEET_RATIO = 0.90625              # 脚底高度线（= 旧橘猫 116/128）
HEAD_RATIO = 0.15625              # 头顶高度线（= 旧橘猫 20/128）

# ---- 已知素材包预设 ----
# pose -> (素材子目录, 是否需要水平翻转)
PRESETS = {
    "knight": {
        "src_name": "CHIBI KNIGHT-PNG",
        "flip": True,             # 原素材朝左
        "poses": {
            "idle": "01-Idle_",
            "walk": "02-Run_",
            "jump": "04-Jump_",
            "attack": "03-Attack_",   # 挥剑庆祝（打开应用成功等场景）
        },
        # jump 素材只有 Jump_Up / Fall_Down 各一帧，重复拼成 4 帧
        "jump_expand": ["Jump_Up", "Fall_Down"],
    },
    "heroguy": {
        "src_name": "Hero-Guy-PNG",
        "flip": True,
        "poses": {
            "idle": os.path.join("_Mode-Sword", "01-Idle"),
            "walk": os.path.join("_Mode-Sword", "02-Run"),
            "jump": os.path.join("_Mode-Sword", "05-Jump"),
        },
    },
}


def load_frames(paths, flip: bool):
    """读入并（可选）水平翻转"""
    out = []
    for p in paths:
        im = Image.open(p).convert("RGBA")
        if flip:
            im = im.transpose(Image.FLIP_LEFT_RIGHT)
        out.append(im)
    return out


def union_bbox(images):
    """多帧 alpha 通道的联合包围盒，空帧会被跳过"""
    boxes = [im.split()[3].getbbox() for im in images]
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        raise ValueError("所有帧都是空的，检查素材路径")
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def normalize(im, box, scale, out_w):
    """裁剪到 box -> 等比缩放 -> 贴到 512 画布（脚底对齐、水平居中）"""
    crop = im.crop(box)
    new_h = max(1, int(crop.height * scale))
    crop = crop.resize((out_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    canvas.alpha_composite(crop, ((MASTER - out_w) // 2,
                                  int(MASTER * FEET_RATIO) - new_h))
    return canvas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="角色名，决定输出目录 assets/characters/<名字>/")
    ap.add_argument("--src", required=True, help="素材包解压根目录")
    ap.add_argument("--pose", action="append", default=[],
                    metavar="姿态=子目录", help="自定义姿态映射，可重复")
    ap.add_argument("--flip", dest="flip", action="store_true", default=None)
    ap.add_argument("--no-flip", dest="flip", action="store_false")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # ---- 解析姿态映射 ----
    preset = PRESETS.get(args.name, {})
    if args.pose:
        poses = {}
        for item in args.pose:
            if "=" not in item:
                ap.error(f"--pose 需要 姿态=子目录 形式，收到 {item!r}")
            k, v = item.split("=", 1)
            poses[k.strip()] = v.strip()
        expand = None
    else:
        poses = preset.get("poses")
        expand = preset.get("jump_expand")
        if not poses:
            ap.error(f"没有 {args.name!r} 的内置预设，请用 --pose 指定映射")

    flip = preset.get("flip", False) if args.flip is None else args.flip

    # ---- 素材根目录：兼容「解压后多一层同名子目录」----
    src = args.src
    inner = os.path.join(src, preset.get("src_name", ""))
    if preset.get("src_name") and os.path.isdir(inner):
        src = inner

    # ---- 读取各姿态帧 ----
    seqs = {}
    for pose, sub in poses.items():
        d = os.path.join(src, sub)
        files = sorted(glob.glob(os.path.join(d, "*.png")))
        if not files:
            print(f"  [跳过] {pose}: {d} 下没有 png")
            continue
        seqs[pose] = files
        print(f"  {pose:10} {len(files):2} 帧  <- {sub}")

    if not seqs:
        print("没有找到任何帧，退出")
        return 1

    # ---- jump 帧数补足 ----
    if expand and "jump" in seqs:
        groups = [[f for f in seqs["jump"] if tag in os.path.basename(f)]
                  for tag in expand]
        seqs["jump"] = [f for g in groups for f in g] * 2
        print(f"  jump 用 {expand} 重复拼接 -> {len(seqs['jump'])} 帧")

    # ---- 联合包围盒 + 缩放系数 ----
    all_ims = [load_frames(v, flip) for v in seqs.values()]
    box = union_bbox([im for group in all_ims for im in group])
    bw, bh = box[2] - box[0], box[3] - box[1]
    scale = (MASTER * (FEET_RATIO - HEAD_RATIO)) / bh
    out_w = max(1, int(bw * scale))

    print()
    print(f"  联合包围盒 {box}  角色原始尺寸 {bw}x{bh}")
    print(f"  缩放系数 {scale:.4f} -> 输出角色 {out_w}x{int(bh*scale)}"
          f"  水平居中于 {MASTER} 画布，脚底 y={int(MASTER*FEET_RATIO)}")

    if args.dry_run:
        print("\n  [dry-run] 未写入任何文件")
        return 0

    # ---- 写出 ----
    print()
    for pose, ims in zip(seqs.keys(), all_ims):
        outdir = os.path.join(OUT_ROOT, pose)
        os.makedirs(outdir, exist_ok=True)
        for old in glob.glob(os.path.join(outdir, "*.png")):
            os.remove(old)
        for i, im in enumerate(ims):
            normalize(im, box, scale, out_w).save(
                os.path.join(outdir, f"{pose}_{i:03d}.png"))
        print(f"  已安装 {pose}: {len(ims)} 帧 -> assets/characters/{pose}/")

    print()
    print("完成。注意：think / searching / talk 若素材包没有，")
    print("      用 tools/make_derived_poses.py 以 idle 帧为底补画。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
