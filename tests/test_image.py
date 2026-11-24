import threading

import pytest
from PIL import Image

from core.image import (
    ImageOptions, add_text_watermark, apply_options, batch_process, collect_images, human_size,
    output_path_for, process_image, resize_to_fit,
)


def make_image(path, size=(800, 600), color=(30, 120, 200), mode="RGB"):
    img = Image.new(mode, size, color if mode == "RGB" else (*color, 128))
    img.save(path)
    return path


def test_resize_keeps_ratio():
    img = Image.new("RGB", (2000, 1000))
    out = resize_to_fit(img, 1000, 0)
    assert out.size == (1000, 500)
    out = resize_to_fit(img, 1000, 300)
    assert out.size == (600, 300)


def test_resize_never_upscales():
    img = Image.new("RGB", (100, 50))
    assert resize_to_fit(img, 1000, 1000).size == (100, 50)
    assert resize_to_fit(img, 0, 0).size == (100, 50)


def test_watermark_changes_pixels():
    img = Image.new("RGB", (400, 300), (0, 0, 0))
    out = add_text_watermark(img, "© 测试 Demo", position="center", opacity=100, size_pct=10)
    assert out.size == img.size
    assert out.convert("RGB").getbbox() is not None  # something non-black was drawn
    assert out.convert("L").getextrema()[1] > 200


@pytest.mark.parametrize("position", ["bottom-right", "bottom-left", "top-left", "top-right", "center", "tile"])
def test_watermark_positions(position):
    img = Image.new("RGB", (300, 200), (0, 0, 0))
    out = add_text_watermark(img, "WM", position=position, opacity=100)
    assert out.convert("L").getextrema()[1] > 100


def test_convert_png_to_jpg_with_resize_and_quality(tmp_path):
    src = make_image(tmp_path / "a.png", (1600, 1200), mode="RGBA")
    out_dir = tmp_path / "out"
    res = process_image(src, out_dir, ImageOptions(max_width=800, out_format="JPG", quality=70))
    assert res.ok, res.error
    assert res.dst.suffix == ".jpg"
    with Image.open(res.dst) as im:
        assert im.format == "JPEG"
        assert im.size == (800, 600)
        assert im.mode == "RGB"


@pytest.mark.parametrize("fmt,pil", [("PNG", "PNG"), ("WEBP", "WEBP"), ("JPG", "JPEG")])
def test_format_conversion(tmp_path, fmt, pil):
    src = make_image(tmp_path / "a.bmp", (120, 80))
    res = process_image(src, tmp_path / "out", ImageOptions(out_format=fmt))
    assert res.ok, res.error
    with Image.open(res.dst) as im:
        assert im.format == pil


def test_lower_quality_gives_smaller_file(tmp_path):
    # noisy image so that JPEG quality matters
    import random

    random.seed(1)
    img = Image.new("RGB", (400, 400))
    img.putdata([(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)) for _ in range(400 * 400)])
    src = tmp_path / "noise.png"
    img.save(src)
    hi = process_image(src, tmp_path / "hi", ImageOptions(out_format="JPG", quality=95))
    lo = process_image(src, tmp_path / "lo", ImageOptions(out_format="JPG", quality=30))
    assert lo.dst_bytes < hi.dst_bytes


def test_output_name_does_not_overwrite(tmp_path):
    src = make_image(tmp_path / "a.jpg", (50, 50))
    out = tmp_path / "out"
    r1 = process_image(src, out, ImageOptions(name_suffix="_web"))
    r2 = process_image(src, out, ImageOptions(name_suffix="_web"))
    assert r1.dst.name == "a_web.jpg"
    assert r2.dst.name == "a_web (1).jpg"


def test_output_path_keep_format(tmp_path):
    assert output_path_for(tmp_path / "x.webp", tmp_path, ImageOptions()).suffix == ".webp"
    assert output_path_for(tmp_path / "x.bmp", tmp_path, ImageOptions()).suffix == ".png"


def test_corrupted_file_reports_error(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    res = process_image(bad, tmp_path / "out", ImageOptions())
    assert not res.ok
    assert "图片" in res.error


def test_batch_process_progress_and_cancel(tmp_path):
    files = [make_image(tmp_path / f"{i}.png", (60, 40)) for i in range(5)]
    calls = []
    results = batch_process(files, tmp_path / "out", ImageOptions(out_format="JPG"),
                            progress=lambda d, t, r: calls.append((d, t, r.ok)))
    assert len(results) == 5 and all(r.ok for r in results)
    assert calls[-1] == (5, 5, True)

    cancel = threading.Event()

    def stop_after_two(done, total, res):
        if done == 2:
            cancel.set()

    results = batch_process(files, tmp_path / "out2", ImageOptions(), progress=stop_after_two, cancel=cancel)
    assert len(results) == 2


def test_apply_options_watermark_and_resize():
    img = Image.new("RGB", (1000, 500), (0, 0, 0))
    out = apply_options(img, ImageOptions(max_width=500, watermark_text="Hi", watermark_opacity=100))
    assert out.size == (500, 250)
    assert out.convert("L").getextrema()[1] > 100


def test_collect_images(tmp_path):
    make_image(tmp_path / "a.jpg", (10, 10))
    make_image(tmp_path / "b.png", (10, 10))
    (tmp_path / "c.txt").write_text("x")
    assert [p.name for p in collect_images(tmp_path)] == ["a.jpg", "b.png"]


def test_human_size():
    assert human_size(512) == "512 B"
    assert human_size(2048) == "2.0 KB"
