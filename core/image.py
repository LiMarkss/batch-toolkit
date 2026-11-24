"""Image batch processing: resize, compress, convert format, text watermark."""
from __future__ import annotations

import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

from PIL import Image, ImageDraw, ImageFont, ImageOps

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
FORMATS = ("KEEP", "JPG", "PNG", "WEBP")
POSITIONS = ("bottom-right", "bottom-left", "top-right", "top-left", "center", "tile")

_PIL_FORMAT = {"JPG": "JPEG", "PNG": "PNG", "WEBP": "WEBP"}
_EXT_FOR = {"JPG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
_FORMAT_FROM_EXT = {".jpg": "JPG", ".jpeg": "JPG", ".png": "PNG", ".webp": "WEBP"}

# Fonts that can render Chinese on Windows; first existing one wins.
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "DejaVuSans-Bold.ttf",
]


@dataclass
class ImageOptions:
    max_width: int = 0               # 0 = unlimited
    max_height: int = 0              # 0 = unlimited
    quality: int = 85                # 1-100 (JPG / WEBP)
    out_format: str = "KEEP"         # KEEP | JPG | PNG | WEBP
    watermark_text: str = ""
    watermark_position: str = "bottom-right"
    watermark_opacity: int = 60      # percent 0-100
    watermark_size: int = 5          # font size as % of the shorter image side
    watermark_color: str = "#FFFFFF"
    name_suffix: str = ""            # appended to output stem, e.g. "_web"
    overwrite: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "ImageOptions":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (data or {}).items() if k in known})

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ImageResult:
    src: Path
    dst: Path | None
    ok: bool
    error: str = ""
    src_bytes: int = 0
    dst_bytes: int = 0
    size: tuple[int, int] | None = None


def is_image(path: str | os.PathLike) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def collect_images(folder: str | os.PathLike, recursive: bool = False) -> list[Path]:
    folder = Path(folder)
    it = folder.rglob("*") if recursive else folder.iterdir()
    return sorted((p for p in it if p.is_file() and is_image(p)), key=lambda p: str(p).lower())


def load_font(size: int) -> ImageFont.ImageFont:
    for cand in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(cand, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default(size=size)


def resize_to_fit(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """Shrink keeping aspect ratio so that width<=max_w and height<=max_h (0 = no limit)."""
    w, h = img.size
    scale = 1.0
    if max_w and w > max_w:
        scale = min(scale, max_w / w)
    if max_h and h > max_h:
        scale = min(scale, max_h / h)
    if scale >= 1.0:
        return img
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return img.resize(new_size, Image.Resampling.LANCZOS)


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except (ValueError, IndexError):
        return 255, 255, 255


def add_text_watermark(img: Image.Image, text: str, position: str = "bottom-right",
                       opacity: int = 60, size_pct: int = 5, color: str = "#FFFFFF") -> Image.Image:
    """Return a new RGBA image with *text* drawn on it (with a subtle shadow)."""
    if not text:
        return img
    base = img.convert("RGBA")
    w, h = base.size
    font_size = max(10, int(min(w, h) * max(1, size_pct) / 100))
    font = load_font(font_size)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    alpha = int(255 * max(0, min(100, opacity)) / 100)
    rgb = _hex_to_rgb(color)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    tw, th = right - left, bottom - top
    margin = max(8, int(min(w, h) * 0.03))
    shadow = max(1, font_size // 18)

    def draw_at(x: int, y: int) -> None:
        draw.text((x - left + shadow, y - top + shadow), text, font=font, fill=(0, 0, 0, alpha // 2))
        draw.text((x - left, y - top), text, font=font, fill=(*rgb, alpha))

    if position == "tile":
        step_x, step_y = tw + margin * 4, th + margin * 4
        for row, y in enumerate(range(-th, h + th, step_y)):
            offset = (step_x // 2) if row % 2 else 0
            for x in range(-tw + offset, w + tw, step_x):
                draw_at(x, y)
    else:
        positions = {
            "top-left": (margin, margin),
            "top-right": (w - tw - margin, margin),
            "bottom-left": (margin, h - th - margin),
            "bottom-right": (w - tw - margin, h - th - margin),
            "center": ((w - tw) // 2, (h - th) // 2),
        }
        draw_at(*positions.get(position, positions["bottom-right"]))
    return Image.alpha_composite(base, layer)


def apply_options(img: Image.Image, opts: ImageOptions) -> Image.Image:
    """Apply orientation fix, resize and watermark (no saving). Used for live preview too."""
    img = ImageOps.exif_transpose(img)
    img = resize_to_fit(img, int(opts.max_width or 0), int(opts.max_height or 0))
    if opts.watermark_text.strip():
        img = add_text_watermark(img, opts.watermark_text.strip(), opts.watermark_position,
                                 int(opts.watermark_opacity), int(opts.watermark_size), opts.watermark_color)
    return img


def output_format_for(src: Path, opts: ImageOptions) -> str:
    if opts.out_format and opts.out_format.upper() != "KEEP":
        return opts.out_format.upper()
    return _FORMAT_FROM_EXT.get(src.suffix.lower(), "PNG")


def output_path_for(src: Path, out_dir: Path, opts: ImageOptions) -> Path:
    fmt = output_format_for(src, opts)
    ext = _EXT_FOR[fmt]
    if fmt == "JPG" and src.suffix.lower() in (".jpg", ".jpeg"):
        ext = src.suffix.lower()
    candidate = out_dir / f"{src.stem}{opts.name_suffix}{ext}"
    if opts.overwrite:
        return candidate
    n = 1
    while candidate.exists():
        candidate = out_dir / f"{src.stem}{opts.name_suffix} ({n}){ext}"
        n += 1
    return candidate


def _prepare_for_format(img: Image.Image, fmt: str) -> Image.Image:
    if fmt == "JPG":
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.getchannel("A"))
            return bg
        return img.convert("RGB") if img.mode != "RGB" else img
    if fmt == "WEBP" and img.mode not in ("RGB", "RGBA"):
        return img.convert("RGBA")
    if fmt == "PNG" and img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
        return img.convert("RGBA")
    return img


def process_image(src: str | os.PathLike, out_dir: str | os.PathLike, opts: ImageOptions) -> ImageResult:
    """Process one image into *out_dir*. Errors are captured in the result, never raised."""
    src = Path(src)
    out_dir = Path(out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        if src.resolve().parent == out_dir.resolve() and not opts.name_suffix and opts.overwrite:
            raise ValueError("输出目录与源目录相同且未设置后缀，会覆盖原图")
        src_bytes = src.stat().st_size
        with Image.open(src) as im:
            im.load()
            exif = im.info.get("exif")
            img = apply_options(im, opts)
        fmt = output_format_for(src, opts)
        img = _prepare_for_format(img, fmt)
        dst = output_path_for(src, out_dir, opts)
        save_kwargs: dict = {}
        q = max(1, min(100, int(opts.quality)))
        if fmt == "JPG":
            save_kwargs = {"quality": q, "optimize": True, "progressive": True}
            if exif:
                save_kwargs["exif"] = exif
        elif fmt == "WEBP":
            save_kwargs = {"quality": q, "method": 4}
        elif fmt == "PNG":
            save_kwargs = {"optimize": True}
        img.save(dst, _PIL_FORMAT[fmt], **save_kwargs)
        return ImageResult(src, dst, True, "", src_bytes, dst.stat().st_size, img.size)
    except Exception as exc:  # noqa: BLE001 - we report every failure per file
        return ImageResult(src, None, False, friendly_error(exc))


def friendly_error(exc: Exception) -> str:
    from PIL import UnidentifiedImageError

    if isinstance(exc, UnidentifiedImageError):
        return "无法识别的图片格式或文件已损坏"
    if isinstance(exc, PermissionError):
        return "没有权限读写该文件（是否被其他程序占用？）"
    if isinstance(exc, FileNotFoundError):
        return "文件不存在"
    return str(exc) or exc.__class__.__name__


ProgressCB = Callable[[int, int, ImageResult], None]


def batch_process(files: Iterable[str | os.PathLike], out_dir: str | os.PathLike, opts: ImageOptions,
                  progress: ProgressCB | None = None,
                  cancel: threading.Event | None = None) -> list[ImageResult]:
    """Process many images. Calls progress(done, total, result) after each file."""
    files = [Path(f) for f in files]
    total = len(files)
    results: list[ImageResult] = []
    for i, f in enumerate(files, 1):
        if cancel is not None and cancel.is_set():
            break
        res = process_image(f, out_dir, opts)
        results.append(res)
        if progress:
            progress(i, total, res)
    return results


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"
