"""PDF tools: merge, split by page ranges, images -> single PDF."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

PAGE_SIZES = {
    "original": None,
    "A4": (1240, 1754),      # 150 dpi portrait
    "Letter": (1275, 1650),  # 150 dpi portrait
}


def _open_reader(path: str | os.PathLike) -> PdfReader:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在：{path.name}")
    try:
        reader = PdfReader(str(path))
    except PdfReadError as exc:
        raise ValueError(f"无法读取 PDF（文件可能已损坏）：{path.name}") from exc
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                raise ValueError
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"PDF 已加密，需要密码：{path.name}") from exc
    return reader


def page_count(path: str | os.PathLike) -> int:
    return len(_open_reader(path).pages)


def merge_pdfs(paths: Iterable[str | os.PathLike], output: str | os.PathLike) -> int:
    """Merge PDFs in the given order into *output*. Returns total page count."""
    paths = [Path(p) for p in paths]
    if len(paths) < 1:
        raise ValueError("请至少选择一个 PDF 文件")
    output = Path(output)
    if any(p.resolve() == output.resolve() for p in paths if p.exists()):
        raise ValueError("输出文件不能与输入文件相同")
    writer = PdfWriter()
    for p in paths:
        reader = _open_reader(p)
        for page in reader.pages:
            writer.add_page(page)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "wb") as fh:
        writer.write(fh)
    return len(writer.pages)


def parse_page_ranges(spec: str, total: int) -> list[tuple[int, int]]:
    """Parse '1-3, 5, 8-' into [(1,3), (5,5), (8,total)] (1-based, inclusive).

    Supported tokens: 'N', 'A-B', 'A-' (to end), '-B' (from start). Separators: comma/semicolon/space.
    """
    if total <= 0:
        raise ValueError("PDF 没有页面")
    tokens = [t for t in re.split(r"[,\s;，；]+", (spec or "").strip()) if t]
    if not tokens:
        raise ValueError("请输入页码范围，例如：1-3, 5, 8-")
    ranges: list[tuple[int, int]] = []
    for tok in tokens:
        m = re.fullmatch(r"(\d*)\s*[-~～—]\s*(\d*)", tok)
        try:
            if m:
                a = int(m.group(1)) if m.group(1) else 1
                b = int(m.group(2)) if m.group(2) else total
            else:
                a = b = int(tok)
        except ValueError:
            raise ValueError(f"无法识别的页码：{tok!r}") from None
        if a < 1 or b < 1:
            raise ValueError(f"页码必须从 1 开始：{tok!r}")
        if a > b:
            raise ValueError(f"起始页大于结束页：{tok!r}")
        if b > total:
            raise ValueError(f"页码 {tok!r} 超出范围（共 {total} 页）")
        ranges.append((a, b))
    return ranges


def split_pdf(src: str | os.PathLike, out_dir: str | os.PathLike, spec: str = "",
              each_page: bool = False) -> list[Path]:
    """Split *src* by page ranges (one output file per range) or into single pages."""
    src = Path(src)
    reader = _open_reader(src)
    total = len(reader.pages)
    ranges = [(i, i) for i in range(1, total + 1)] if each_page else parse_page_ranges(spec, total)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    width = len(str(total))
    outputs: list[Path] = []
    for a, b in ranges:
        writer = PdfWriter()
        for i in range(a - 1, b):
            writer.add_page(reader.pages[i])
        label = f"p{str(a).zfill(width)}" if a == b else f"p{str(a).zfill(width)}-{str(b).zfill(width)}"
        dst = out_dir / f"{src.stem}_{label}.pdf"
        with open(dst, "wb") as fh:
            writer.write(fh)
        outputs.append(dst)
    return outputs


def _fit_on_page(img: Image.Image, page: tuple[int, int], margin: int) -> Image.Image:
    pw, ph = page
    if img.width > img.height and pw < ph:   # landscape image -> landscape page
        pw, ph = ph, pw
    canvas = Image.new("RGB", (pw, ph), "white")
    box_w, box_h = pw - 2 * margin, ph - 2 * margin
    scale = min(box_w / img.width, box_h / img.height, 1.0)   # shrink to fit, never upscale
    new = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.Resampling.LANCZOS)
    canvas.paste(new, ((pw - new.width) // 2, (ph - new.height) // 2))
    return canvas


def images_to_pdf(images: Iterable[str | os.PathLike], output: str | os.PathLike,
                  page_size: str = "original", margin: int = 40) -> int:
    """Combine images (in order) into one PDF. Returns page count."""
    images = [Path(p) for p in images]
    if not images:
        raise ValueError("请至少选择一张图片")
    if page_size not in PAGE_SIZES:
        raise ValueError(f"不支持的页面尺寸：{page_size}")
    pages: list[Image.Image] = []
    for p in images:
        try:
            with Image.open(p) as im:
                im = ImageOps.exif_transpose(im)
                if im.mode in ("RGBA", "LA", "P"):
                    im = im.convert("RGBA")
                    bg = Image.new("RGB", im.size, "white")
                    bg.paste(im, mask=im.getchannel("A"))
                    im = bg
                else:
                    im = im.convert("RGB")
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"无法读取图片：{p.name}") from exc
        size = PAGE_SIZES[page_size]
        pages.append(_fit_on_page(im, size, margin) if size else im)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(output, "PDF", save_all=True, append_images=pages[1:], resolution=150.0)
    return len(pages)
