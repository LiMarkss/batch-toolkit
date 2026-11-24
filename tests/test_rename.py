import json
import os
import time
from datetime import datetime

import pytest

from core.rename import (
    DUPLICATE, EXISTS, INVALID, OK, UNCHANGED, RenameRule, apply_renames, build_new_name,
    latest_log, list_files, parse_extensions, plan_renames, undo_renames, validate_filename,
)


def make_files(folder, names):
    for n in names:
        (folder / n).write_text(n, encoding="utf-8")


def names_in(folder):
    return sorted(p.name for p in folder.iterdir() if p.is_file())


def test_parse_extensions():
    assert parse_extensions("jpg, .PNG; txt") == {".jpg", ".png", ".txt"}
    assert parse_extensions("") == set()


def test_list_files_filters_and_natural_sort(tmp_path):
    make_files(tmp_path, ["img10.jpg", "img2.jpg", "img1.JPG", "notes.txt", ".hidden.jpg"])
    files = [p.name for p in list_files(tmp_path, "jpg")]
    assert files == ["img1.JPG", "img2.jpg", "img10.jpg"]


def test_list_files_missing_folder(tmp_path):
    with pytest.raises(FileNotFoundError):
        list_files(tmp_path / "nope")


@pytest.mark.parametrize(
    "rule,expected",
    [
        (RenameRule(prefix="A_", suffix="_z"), "A_photo_z.jpg"),
        (RenameRule(numbering="replace", start=7, padding=3), "007.jpg"),
        (RenameRule(numbering="prefix", start=1, padding=2), "01_photo.jpg"),
        (RenameRule(numbering="suffix", start=1, padding=4, separator="-"), "photo-0001.jpg"),
        (RenameRule(find="pho", replace="PHO"), "PHOto.jpg"),
        (RenameRule(find=r"\d+", replace="#", use_regex=True), "photo.jpg"),
        (RenameRule(find="PHOTO", replace="pic", case_sensitive=False), "pic.jpg"),
        (RenameRule(date_prefix=True), "20240501_photo.jpg"),
    ],
)
def test_build_new_name_rules(rule, expected):
    assert build_new_name("photo.jpg", 0, rule, datetime(2024, 5, 1)) == expected


def test_build_new_name_index_step_and_lower_ext():
    rule = RenameRule(prefix="trip_", numbering="replace", start=10, step=5, padding=3, lowercase_ext=True)
    assert build_new_name("DSC.JPG", 2, rule) == "trip_020.jpg"


def test_literal_replace_keeps_backslash_chars():
    rule = RenameRule(find="a", replace=r"\1")
    assert build_new_name("a.txt", 0, rule) == r"\1.txt"


def test_invalid_regex_raises_value_error():
    with pytest.raises(ValueError):
        build_new_name("a.txt", 0, RenameRule(find="(", use_regex=True))


def test_validate_filename():
    assert validate_filename("ok.txt") == ""
    assert validate_filename("bad:name.txt")
    assert validate_filename("CON.txt")
    assert validate_filename("trailing.")
    assert validate_filename("")


def test_plan_preview_does_not_touch_disk(tmp_path):
    make_files(tmp_path, ["b.jpg", "a.jpg"])
    plan = plan_renames(tmp_path, RenameRule(prefix="x_"))
    assert [(i.old, i.new, i.status) for i in plan.items] == [("a.jpg", "x_a.jpg", OK), ("b.jpg", "x_b.jpg", OK)]
    assert names_in(tmp_path) == ["a.jpg", "b.jpg"]


def test_plan_detects_duplicate_targets(tmp_path):
    make_files(tmp_path, ["a1.txt", "a2.txt"])
    plan = plan_renames(tmp_path, RenameRule(find=r"\d", replace="", use_regex=True))
    assert {i.status for i in plan.items} == {DUPLICATE}
    assert plan.has_conflicts


def test_plan_detects_existing_untouched_entry(tmp_path):
    make_files(tmp_path, ["a.jpg", "b.jpg"])
    (tmp_path / "x_a.jpg").mkdir()          # a folder with the target name is not part of the rename
    plan = plan_renames(tmp_path, RenameRule(prefix="x_"))
    statuses = {i.old: i.status for i in plan.items}
    assert statuses == {"a.jpg": EXISTS, "b.jpg": OK}


def test_plan_exists_conflict_with_filtered_out_file(tmp_path):
    make_files(tmp_path, ["a.jpg", "new.jpg.bak"])
    (tmp_path / "new.jpg").write_text("keep me")
    plan = plan_renames(tmp_path, RenameRule(find="a", replace="new", extensions="jpg"))
    items = {i.old: i for i in plan.items}
    # new.jpg is in the list and stays unchanged -> a.jpg -> new.jpg collides with it
    assert items["a.jpg"].status == DUPLICATE
    assert items["new.jpg"].status == UNCHANGED


def test_plan_invalid_name(tmp_path):
    make_files(tmp_path, ["a.txt"])
    plan = plan_renames(tmp_path, RenameRule(prefix="bad?"))
    assert plan.items[0].status == INVALID


def test_apply_and_undo_roundtrip(tmp_path):
    folder = tmp_path / "files"
    folder.mkdir()
    logs = tmp_path / "logs"
    make_files(folder, ["IMG_2.jpg", "IMG_1.jpg", "readme.txt"])
    plan = plan_renames(folder, RenameRule(prefix="trip_", numbering="replace", padding=3, extensions="jpg"))
    log = apply_renames(plan, logs)
    assert names_in(folder) == ["readme.txt", "trip_001.jpg", "trip_002.jpg"]
    assert (folder / "trip_001.jpg").read_text(encoding="utf-8") == "IMG_1.jpg"
    data = json.loads(log.read_text(encoding="utf-8"))
    assert len(data["renames"]) == 2
    assert latest_log(logs) == log

    restored = undo_renames(log)
    assert restored == 2
    assert names_in(folder) == ["IMG_1.jpg", "IMG_2.jpg", "readme.txt"]
    assert not log.exists()


def test_apply_handles_swap_chain(tmp_path):
    # a.txt -> b.txt while b.txt -> c.txt : requires two-phase rename
    make_files(tmp_path, ["1.txt", "2.txt", "3.txt"])
    rule = RenameRule(numbering="replace", start=2, padding=1)
    plan = plan_renames(tmp_path, rule)
    assert not plan.has_conflicts
    apply_renames(plan, tmp_path / "logs")
    assert (tmp_path / "2.txt").read_text(encoding="utf-8") == "1.txt"
    assert (tmp_path / "4.txt").read_text(encoding="utf-8") == "3.txt"


def test_apply_refuses_when_conflicts(tmp_path):
    make_files(tmp_path, ["a1.txt", "a2.txt"])
    plan = plan_renames(tmp_path, RenameRule(find=r"\d", replace="", use_regex=True))
    with pytest.raises(ValueError):
        apply_renames(plan, tmp_path / "logs")
    assert names_in(tmp_path) == ["a1.txt", "a2.txt"]


def test_case_only_rename(tmp_path):
    make_files(tmp_path, ["PHOTO.JPG"])
    plan = plan_renames(tmp_path, RenameRule(lowercase_ext=True))
    assert plan.items[0].status == OK
    apply_renames(plan, tmp_path / "logs")
    assert names_in(tmp_path) == ["PHOTO.jpg"]


def test_undo_fails_cleanly_if_file_missing(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    make_files(folder, ["a.txt"])
    log = apply_renames(plan_renames(folder, RenameRule(prefix="z_")), tmp_path / "logs")
    os.remove(folder / "z_a.txt")
    with pytest.raises(FileNotFoundError):
        undo_renames(log)


def test_date_from_modified_time(tmp_path):
    make_files(tmp_path, ["a.txt"])
    ts = time.mktime(datetime(2023, 1, 2, 12, 0).timetuple())
    os.utime(tmp_path / "a.txt", (ts, ts))
    plan = plan_renames(tmp_path, RenameRule(date_prefix=True, date_source="modified", date_format="%Y-%m-%d"))
    assert plan.items[0].new == "2023-01-02_a.txt"
