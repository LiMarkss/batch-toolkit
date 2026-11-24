"""Generate demo data in samples/ (used by --demo / --screenshot mode and for manual testing).

    python make_samples.py
"""
from __future__ import annotations

import math
import os
import random
import shutil
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from core.image import load_font

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "samples"

PALETTES = [
    ((14, 165, 233), (99, 102, 241)),
    ((244, 114, 182), (251, 146, 60)),
    ((34, 197, 94), (14, 165, 233)),
    ((250, 204, 21), (239, 68, 68)),
    ((168, 85, 247), (59, 130, 246)),
    ((20, 184, 166), (132, 204, 22)),
]


def gradient(size, c1, c2, angle=35):
    w, h = size
    small = Image.new("RGB", (64, 64))
    px = small.load()
    a = math.radians(angle)
    for y in range(64):
        for x in range(64):
            t = (x * math.cos(a) + y * math.sin(a)) / (64 * (abs(math.cos(a)) + abs(math.sin(a))))
            px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    return small.resize((w, h), Image.Resampling.BICUBIC)


def product_photo(path: Path, title: str, subtitle: str, palette, size=(1600, 1067)):
    img = gradient(size, *palette)
    d = ImageDraw.Draw(img, "RGBA")
    w, h = size
    rnd = random.Random(title)
    for _ in range(14):
        r = rnd.randint(40, 260)
        x, y = rnd.randint(-r, w), rnd.randint(-r, h)
        d.ellipse([x, y, x + r, y + r], fill=(255, 255, 255, rnd.randint(18, 50)))
    # "product" card
    cw, ch = int(w * 0.42), int(h * 0.55)
    x0, y0 = (w - cw) // 2, (h - ch) // 2 - 30
    d.rounded_rectangle([x0 + 12, y0 + 16, x0 + cw + 12, y0 + ch + 16], 40, fill=(0, 0, 0, 50))
    d.rounded_rectangle([x0, y0, x0 + cw, y0 + ch], 40, fill=(255, 255, 255, 235))
    d.ellipse([x0 + cw // 2 - 110, y0 + 60, x0 + cw // 2 + 110, y0 + 280], fill=(*palette[0], 255))
    f1, f2 = load_font(64), load_font(36)
    tb = d.textbbox((0, 0), title, font=f1)
    d.text((x0 + (cw - (tb[2] - tb[0])) // 2, y0 + 320), title, font=f1, fill=(30, 41, 59))
    sb = d.textbbox((0, 0), subtitle, font=f2)
    d.text((x0 + (cw - (sb[2] - sb[0])) // 2, y0 + 410), subtitle, font=f2, fill=(100, 116, 139))
    img.save(path, quality=88)


def pdf_doc(path: Path, title: str, pages: int, color):
    imgs = []
    f_title, f_body, f_small = load_font(54), load_font(26), load_font(22)
    for p in range(1, pages + 1):
        page = Image.new("RGB", (1240, 1754), "white")
        d = ImageDraw.Draw(page)
        d.rectangle([0, 0, 1240, 170], fill=color)
        d.text((80, 55), title, font=f_title, fill="white")
        for k in range(16):
            y = 260 + k * 70
            d.rounded_rectangle([80, y, 1160 - (k * 53) % 380, y + 22], 8, fill=(226, 232, 240))
        d.text((80, 1660), f"BatchToolkit 示例文档 · 第 {p} / {pages} 页", font=f_small, fill=(100, 116, 139))
        d.text((1000, 1660), f"Page {p}", font=f_body, fill=color)
        imgs.append(page)
    imgs[0].save(path, "PDF", save_all=True, append_images=imgs[1:], resolution=150.0)


def main():
    if SAMPLES.exists():
        shutil.rmtree(SAMPLES)
    (SAMPLES / "rename").mkdir(parents=True)
    (SAMPLES / "images").mkdir()
    (SAMPLES / "pdfs").mkdir()

    # 1) messy file names for the rename demo
    messy = [
        "IMG_20240501_093012.jpg", "IMG_20240501_101544.jpg", "DSC_0042.JPG", "DSC_0043.JPG",
        "微信图片_20240502153011.jpg", "Screenshot 2024-05-03 at 10.15.22.png", "截图(1).png",
        "新建 文本文档.txt", "photo final (2).jpg", "photo final (3).jpg",
    ]
    for i, name in enumerate(messy):
        p = SAMPLES / "rename" / name
        if p.suffix.lower() in (".jpg", ".png"):
            gradient((96, 64), *PALETTES[i % len(PALETTES)]).save(p)
        else:
            p.write_text("BatchToolkit demo file\n", encoding="utf-8")
        ts = time.mktime(datetime(2024, 5, 1 + i // 3, 9 + i, 0).timetuple())
        os.utime(p, (ts, ts))

    # 2) product photos for the image demo
    products = [("Smart Watch", "智能手表 · 新品"), ("Headphones", "降噪耳机 · 热卖"), ("Coffee Mug", "陶瓷马克杯"),
                ("Sneakers", "轻量跑鞋 · 2024"), ("Backpack", "通勤双肩包"), ("Desk Lamp", "护眼台灯")]
    for i, (t, s) in enumerate(products, 1):
        product_photo(SAMPLES / "images" / f"product_{i:02d}.jpg", t, s, PALETTES[i - 1])

    # 3) PDFs for the PDF demo
    pdf_doc(SAMPLES / "pdfs" / "合同_第1部分.pdf", "Contract · Part 1", 3, (37, 99, 235))
    pdf_doc(SAMPLES / "pdfs" / "合同_第2部分.pdf", "Contract · Part 2", 2, (79, 70, 229))
    pdf_doc(SAMPLES / "pdfs" / "Invoice_2024-05.pdf", "Invoice 2024-05", 1, (5, 150, 105))
    pdf_doc(SAMPLES / "pdfs" / "Report_Q2.pdf", "Quarterly Report Q2", 6, (234, 88, 12))
    print("samples generated in", SAMPLES)


if __name__ == "__main__":
    main()
