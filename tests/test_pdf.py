import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from core.pdf import images_to_pdf, merge_pdfs, page_count, parse_page_ranges, split_pdf


def make_pdf(path, pages, width=200):
    """Create a PDF whose page widths encode (file, page) so we can verify order."""
    writer = PdfWriter()
    for i in range(pages):
        writer.add_blank_page(width=width + i, height=300)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def widths(path):
    return [round(float(p.mediabox.width)) for p in PdfReader(str(path)).pages]


def test_merge_keeps_order(tmp_path):
    a = make_pdf(tmp_path / "a.pdf", 2, width=100)
    b = make_pdf(tmp_path / "b.pdf", 3, width=300)
    out = tmp_path / "out" / "merged.pdf"
    assert merge_pdfs([b, a], out) == 5
    assert widths(out) == [300, 301, 302, 100, 101]


def test_merge_rejects_output_equal_input(tmp_path):
    a = make_pdf(tmp_path / "a.pdf", 1)
    with pytest.raises(ValueError):
        merge_pdfs([a], a)


def test_merge_empty_list(tmp_path):
    with pytest.raises(ValueError):
        merge_pdfs([], tmp_path / "x.pdf")


def test_merge_corrupted_file_friendly_error(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 garbage")
    with pytest.raises(ValueError):
        merge_pdfs([bad], tmp_path / "x.pdf")


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("1-3, 5, 8-", [(1, 3), (5, 5), (8, 10)]),
        ("-2；4", [(1, 2), (4, 4)]),
        ("10", [(10, 10)]),
        ("2~4", [(2, 4)]),
    ],
)
def test_parse_page_ranges(spec, expected):
    assert parse_page_ranges(spec, 10) == expected


@pytest.mark.parametrize("spec", ["", "0", "5-3", "11", "abc", "1-20"])
def test_parse_page_ranges_errors(spec):
    with pytest.raises(ValueError):
        parse_page_ranges(spec, 10)


def test_split_by_ranges(tmp_path):
    src = make_pdf(tmp_path / "doc.pdf", 6, width=100)
    outs = split_pdf(src, tmp_path / "parts", "1-2, 4, 5-")
    assert [p.name for p in outs] == ["doc_p1-2.pdf", "doc_p4.pdf", "doc_p5-6.pdf"]
    assert widths(outs[0]) == [100, 101]
    assert widths(outs[1]) == [103]
    assert widths(outs[2]) == [104, 105]


def test_split_each_page(tmp_path):
    src = make_pdf(tmp_path / "doc.pdf", 12)
    outs = split_pdf(src, tmp_path / "pages", each_page=True)
    assert len(outs) == 12
    assert outs[0].name == "doc_p01.pdf"
    assert all(page_count(p) == 1 for p in outs)


def test_images_to_pdf_original_size(tmp_path):
    imgs = []
    for i, (size, mode) in enumerate([((400, 300), "RGB"), ((300, 500), "RGBA"), ((200, 200), "L")]):
        p = tmp_path / f"{i}.png"
        Image.new(mode, size).save(p)
        imgs.append(p)
    out = tmp_path / "album.pdf"
    assert images_to_pdf(imgs, out) == 3
    assert page_count(out) == 3


def test_images_to_pdf_a4(tmp_path):
    p1 = tmp_path / "portrait.jpg"
    p2 = tmp_path / "landscape.jpg"
    Image.new("RGB", (600, 900), "red").save(p1)
    Image.new("RGB", (3000, 1000), "blue").save(p2)
    out = tmp_path / "a4.pdf"
    images_to_pdf([p1, p2], out, page_size="A4")
    pages = PdfReader(str(out)).pages
    w0, h0 = float(pages[0].mediabox.width), float(pages[0].mediabox.height)
    w1, h1 = float(pages[1].mediabox.width), float(pages[1].mediabox.height)
    assert h0 > w0            # portrait A4
    assert w1 > h1            # landscape image -> landscape page
    assert abs(w0 / h0 - 210 / 297) < 0.01


def test_images_to_pdf_errors(tmp_path):
    with pytest.raises(ValueError):
        images_to_pdf([], tmp_path / "x.pdf")
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"nope")
    with pytest.raises(ValueError):
        images_to_pdf([bad], tmp_path / "x.pdf")
