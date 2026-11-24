"""Programmatically drawn app icon (no binary assets needed)."""
from __future__ import annotations

from PIL import Image, ImageDraw


def make_icon_image(size: int = 256) -> Image.Image:
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(s * 0.22)
    # rounded square with a vertical gradient (blue -> indigo)
    grad = Image.new("RGBA", (s, s))
    gd = ImageDraw.Draw(grad)
    top, bottom = (37, 99, 235), (79, 70, 229)
    for y in range(s):
        t = y / max(1, s - 1)
        gd.line([(0, y), (s, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=255)
    img.paste(grad, (0, 0), mask)
    # three stacked "documents"
    w, h = int(s * 0.42), int(s * 0.52)
    for i, alpha in enumerate((110, 170, 255)):
        x0 = int(s * 0.20) + i * int(s * 0.07)
        y0 = int(s * 0.18) + i * int(s * 0.06)
        d.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=int(s * 0.04), fill=(255, 255, 255, alpha))
    # lines on the front document
    x0 = int(s * 0.20) + 2 * int(s * 0.07)
    y0 = int(s * 0.18) + 2 * int(s * 0.06)
    for k in range(4):
        yy = y0 + int(h * (0.22 + k * 0.17))
        d.rounded_rectangle([x0 + int(w * 0.15), yy, x0 + int(w * (0.85 if k % 2 == 0 else 0.6)), yy + max(2, s // 40)],
                            radius=s // 80 + 1, fill=(79, 70, 229, 255))
    # lightning bolt badge (batch = fast)
    cx, cy, br = int(s * 0.74), int(s * 0.74), int(s * 0.17)
    d.ellipse([cx - br, cy - br, cx + br, cy + br], fill=(245, 158, 11, 255), outline=(255, 255, 255, 255),
              width=max(2, s // 64))
    bolt = [(cx + br * 0.15, cy - br * 0.65), (cx - br * 0.40, cy + br * 0.10), (cx - br * 0.02, cy + br * 0.10),
            (cx - br * 0.18, cy + br * 0.68), (cx + br * 0.42, cy - br * 0.12), (cx + br * 0.03, cy - br * 0.12)]
    d.polygon(bolt, fill=(255, 255, 255, 255))
    return img


def save_ico(path: str) -> None:
    img = make_icon_image(256)
    img.save(path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
