#!/usr/bin/env python3
"""生成上架用的图标和截图。

产出（都进仓库，市场按 screenshots.json 取图）：
    assets/catalog/icon-180.png       市场卡片图标（180×180）
    assets/catalog/screenshot-1.png   听你说话（蓝色声波）
    assets/catalog/screenshot-2.png   它在回答（橙色声波）

截图来源是 helper 的 --snapshot 诊断快照，这里裁到内容边界再补白边。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "catalog"
ICON_SIZE = 180
# 截图统一宽度，高度按内容裁
SHOT_WIDTH = 900
PAD = 24


def make_icon() -> Path:
    """从待机第一帧裁出角色，做方形容器。"""
    src = ROOT / "assets" / "pet" / "idle" / "idle_001.webp"
    img = Image.open(src).convert("RGBA")
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)

    # 正方形画布，角色居中留一点边
    side = max(img.width, img.height)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)

    # 缩到 180（卡片约 96px 显示，留 2x 余量）
    canvas = canvas.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    path = OUT / "icon-180.png"
    canvas.save(path, "PNG")
    return path


def make_shot(src: Path, dst: Path) -> Path:
    """裁掉透明/白边，按统一宽度缩放。"""
    img = Image.open(src).convert("RGBA")

    # 用 alpha 或非白像素求边界
    if img.getchannel("A").getbbox() and img.getchannel("A").getextrema()[0] < 255:
        bbox = img.getchannel("A").getbbox()
    else:
        white = Image.new("RGBA", img.size, (255, 255, 255, 255))
        diff = Image.alpha_composite(white, img).convert("L")
        from PIL import ImageChops

        bbox = ImageChops.difference(diff, Image.new("L", img.size, 255)).getbbox()
    if bbox:
        img = img.crop(bbox)

    # 补白边（市场是浅色卡片，给透明图加白底更像截图）
    canvas = Image.new("RGBA", (img.width + PAD * 2, img.height + PAD * 2), (255, 255, 255, 255))
    canvas.paste(img, (PAD, PAD), img)

    scale = SHOT_WIDTH / canvas.width
    canvas = canvas.resize((SHOT_WIDTH, max(1, round(canvas.height * scale))), Image.LANCZOS)
    canvas.convert("RGB").save(dst, "PNG")
    return dst


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    icon = make_icon()
    print(f"✅ 图标 {icon.relative_to(ROOT)}")

    pairs = [
        (ROOT / ".build" / "snap-exe.png", OUT / "screenshot-1.png"),
        (ROOT / ".build" / "snap-wave4.png", OUT / "screenshot-2.png"),
    ]
    for src, dst in pairs:
        if not src.exists():
            print(f"⚠️ 缺少 {src.name}，跳过（先跑 helper --snapshot 生成）")
            continue
        out = make_shot(src, dst)
        print(f"✅ 截图 {out.relative_to(ROOT)}  {Image.open(out).size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
