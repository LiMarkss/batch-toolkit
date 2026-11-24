from core.icon import make_icon_image, save_ico
from core.settings import load_settings, save_settings


def test_settings_roundtrip(tmp_path):
    path = tmp_path / "sub" / "settings.json"
    data = {"rename": {"prefix": "旅行_"}, "geometry": "1100x720"}
    save_settings(data, path)
    assert load_settings(path) == data


def test_settings_missing_or_corrupted(tmp_path):
    assert load_settings(tmp_path / "missing.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_settings(bad) == {}
    bad.write_text("[1, 2]", encoding="utf-8")
    assert load_settings(bad) == {}


def test_icon_generation(tmp_path):
    img = make_icon_image(64)
    assert img.size == (64, 64) and img.mode == "RGBA"
    ico = tmp_path / "app.ico"
    save_ico(str(ico))
    assert ico.stat().st_size > 1000
