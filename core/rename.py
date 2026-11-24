"""Batch rename: rule-based name generation, preview planning, apply and undo.

Workflow::

    rule = RenameRule(prefix="trip_", numbering="replace", padding=3)
    plan = plan_renames(folder, rule)          # live preview, no disk changes
    log  = apply_renames(plan, log_dir)        # performs renames, writes JSON log
    undo_renames(log)                          # restores original names
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

# Characters that are not allowed in Windows file names.
INVALID_CHARS = set('<>:"/\\|?*')
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

NUMBERING_MODES = ("none", "prefix", "suffix", "replace")

# Plan item statuses
OK = "ok"
UNCHANGED = "unchanged"
DUPLICATE = "duplicate"
EXISTS = "exists"
INVALID = "invalid"

STATUS_LABELS = {
    OK: "✓ 可重命名",
    UNCHANGED: "— 名称未变",
    DUPLICATE: "✗ 冲突：目标重名",
    EXISTS: "✗ 冲突：文件已存在",
    INVALID: "✗ 非法文件名",
}


@dataclass
class RenameRule:
    prefix: str = ""
    suffix: str = ""
    find: str = ""
    replace: str = ""
    use_regex: bool = False
    case_sensitive: bool = True
    numbering: str = "none"          # none | prefix | suffix | replace
    start: int = 1
    step: int = 1
    padding: int = 3
    separator: str = "_"
    date_prefix: bool = False
    date_format: str = "%Y%m%d"
    date_source: str = "today"       # today | modified
    extensions: str = ""             # e.g. "jpg, png" (empty = all files)
    lowercase_ext: bool = False
    sort_by: str = "name"            # name | mtime

    @classmethod
    def from_dict(cls, data: dict) -> "RenameRule":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PlanItem:
    old: str
    new: str
    status: str = OK
    message: str = ""

    @property
    def will_rename(self) -> bool:
        return self.status == OK


@dataclass
class RenamePlan:
    folder: Path
    items: list[PlanItem] = field(default_factory=list)

    @property
    def ok_items(self) -> list[PlanItem]:
        return [i for i in self.items if i.status == OK]

    @property
    def conflicts(self) -> list[PlanItem]:
        return [i for i in self.items if i.status in (DUPLICATE, EXISTS, INVALID)]

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)


# --------------------------------------------------------------------------- helpers

def parse_extensions(text: str) -> set[str]:
    """'jpg, .PNG; txt' -> {'.jpg', '.png', '.txt'}"""
    parts = re.split(r"[\s,;，；|]+", (text or "").strip())
    return {("." + p.lstrip(".")).lower() for p in parts if p.strip(".")}


def natural_key(name: str):
    """Sort key so that 'img2' < 'img10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def list_files(folder: str | os.PathLike, extensions: str = "", sort_by: str = "name") -> list[Path]:
    """List regular files in *folder* (non-recursive), filtered and sorted."""
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"文件夹不存在：{folder}")
    exts = parse_extensions(extensions)
    files = [
        p for p in folder.iterdir()
        if p.is_file() and not p.name.startswith(".") and (not exts or p.suffix.lower() in exts)
    ]
    if sort_by == "mtime":
        files.sort(key=lambda p: (p.stat().st_mtime, natural_key(p.name)))
    else:
        files.sort(key=lambda p: natural_key(p.name))
    return files


def validate_filename(name: str) -> str:
    """Return an error message ('' when the name is valid on Windows)."""
    if not name or not name.strip():
        return "文件名为空"
    bad = sorted({c for c in name if c in INVALID_CHARS or ord(c) < 32})
    if bad:
        return "包含非法字符：" + " ".join(repr(c) for c in bad)
    if name.endswith((" ", ".")):
        return "文件名不能以空格或点结尾"
    if name.split(".")[0].upper() in RESERVED_NAMES:
        return "Windows 保留名称"
    if len(name) > 255:
        return "文件名过长"
    return ""


def build_new_name(filename: str, index: int, rule: RenameRule, date_value: datetime | None = None) -> str:
    """Compute the new name for *filename*; *index* is 0-based position in the list."""
    stem, ext = os.path.splitext(filename)

    # 1) find / replace on the stem
    if rule.find:
        flags = 0 if rule.case_sensitive else re.IGNORECASE
        pattern = rule.find if rule.use_regex else re.escape(rule.find)
        repl = rule.replace if rule.use_regex else (lambda _m: rule.replace)
        try:
            stem = re.sub(pattern, repl, stem, flags=flags)
        except re.error as exc:
            raise ValueError(f"正则表达式错误：{exc}") from exc

    # 2) numbering
    sep = rule.separator
    number = ""
    if rule.numbering in ("prefix", "suffix", "replace"):
        n = int(rule.start) + index * int(rule.step)
        number = str(n).zfill(max(int(rule.padding), 0))

    if rule.numbering == "replace":
        body = f"{rule.prefix}{number}{rule.suffix}"
    else:
        body = f"{rule.prefix}{stem}{rule.suffix}"
        if rule.numbering == "prefix":
            body = f"{number}{sep}{body}"
        elif rule.numbering == "suffix":
            body = f"{body}{sep}{number}"

    # 3) date prefix
    if rule.date_prefix:
        d = date_value or datetime.now()
        body = f"{d.strftime(rule.date_format)}{sep}{body}"

    if rule.lowercase_ext:
        ext = ext.lower()
    return body + ext


def plan_renames(folder: str | os.PathLike, rule: RenameRule, now: datetime | None = None) -> RenamePlan:
    """Build a preview of all renames, detecting conflicts. Never touches the disk."""
    folder = Path(folder)
    files = list_files(folder, rule.extensions, rule.sort_by)
    plan = RenamePlan(folder=folder)

    for idx, path in enumerate(files):
        if rule.date_source == "modified":
            date_value = datetime.fromtimestamp(path.stat().st_mtime)
        else:
            date_value = now or datetime.now()
        new = build_new_name(path.name, idx, rule, date_value)
        plan.items.append(PlanItem(old=path.name, new=new))

    _detect_conflicts(plan)
    return plan


def _detect_conflicts(plan: RenamePlan) -> None:
    """Mark invalid names, duplicates and collisions with untouched files (case-insensitive)."""
    for item in plan.items:
        if item.new == item.old:
            item.status, item.message = UNCHANGED, ""
            continue
        err = validate_filename(item.new)
        if err:
            item.status, item.message = INVALID, err

    # Files that will keep their name (not renamed, or not in the list at all) are "fixed".
    moving = {i.old.lower() for i in plan.items if i.status == OK}
    on_disk = {p.name.lower() for p in plan.folder.iterdir()} if plan.folder.is_dir() else set()
    fixed = on_disk - moving

    counts: dict[str, int] = {}
    for item in plan.items:
        key = item.new.lower() if item.status in (OK, UNCHANGED) else None
        if key:
            counts[key] = counts.get(key, 0) + 1

    for item in plan.items:
        if item.status != OK:
            continue
        key = item.new.lower()
        if counts.get(key, 0) > 1:
            item.status, item.message = DUPLICATE, "多个文件将得到同一名称"
        elif key in fixed and key != item.old.lower():
            item.status, item.message = EXISTS, "文件夹中已存在同名文件"


# --------------------------------------------------------------------------- apply / undo

def _two_phase_rename(folder: Path, pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Rename via temporary names so chains/swaps (a->b, b->a) work. Rolls back on error."""
    token = uuid.uuid4().hex[:8]
    staged: list[tuple[str, str, str]] = []   # (old, tmp, new)
    done: list[tuple[str, str]] = []
    try:
        for i, (old, new) in enumerate(pairs):
            tmp = f".~bt_{token}_{i}.tmp"
            os.rename(folder / old, folder / tmp)
            staged.append((old, tmp, new))
        for old, tmp, new in staged:
            if (folder / new).exists():
                raise FileExistsError(f"目标已存在：{new}")
            os.rename(folder / tmp, folder / new)
            done.append((old, new))
    except Exception:
        # roll back everything we touched
        for old, new in reversed(done):
            try:
                os.rename(folder / new, folder / old)
            except OSError:
                pass
        finished_old = {o for o, _ in done}
        for old, tmp, _new in staged:
            if old in finished_old:
                continue
            try:
                os.rename(folder / tmp, folder / old)
            except OSError:
                pass
        raise
    return done


def apply_renames(plan: RenamePlan, log_dir: str | os.PathLike | None = None) -> Path | None:
    """Execute all OK items of *plan*. Returns the path of the JSON undo log (or None)."""
    if plan.has_conflicts:
        raise ValueError(f"存在 {len(plan.conflicts)} 个冲突，请先修改规则后再执行")
    pairs = [(i.old, i.new) for i in plan.ok_items]
    if not pairs:
        return None
    done = _two_phase_rename(plan.folder, pairs)

    log_dir = Path(log_dir) if log_dir else plan.folder
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = log_dir / f"rename_log_{stamp}.json"
    payload = {
        "app": "BatchToolkit",
        "created": datetime.now().isoformat(timespec="seconds"),
        "folder": str(plan.folder.resolve()),
        "renames": [{"old": o, "new": n} for o, n in done],
    }
    log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return log_path


def read_log(log_path: str | os.PathLike) -> dict:
    data = json.loads(Path(log_path).read_text(encoding="utf-8"))
    if "folder" not in data or "renames" not in data:
        raise ValueError("无效的重命名日志文件")
    return data


def undo_renames(log_path: str | os.PathLike, delete_log: bool = True) -> int:
    """Revert the renames recorded in *log_path*. Returns number of files restored."""
    data = read_log(log_path)
    folder = Path(data["folder"])
    if not folder.is_dir():
        raise FileNotFoundError(f"原文件夹不存在：{folder}")
    pairs = []
    for entry in data["renames"]:
        old, new = entry["old"], entry["new"]
        if not (folder / new).exists():
            raise FileNotFoundError(f"无法撤销：找不到文件 {new}（可能已被移动或修改）")
        pairs.append((new, old))
    current = {n.lower() for n, _ in pairs}
    for _new, old in pairs:
        if (folder / old).exists() and old.lower() not in current:
            raise FileExistsError(f"无法撤销：{old} 已被其他文件占用")
    done = _two_phase_rename(folder, pairs)
    if delete_log:
        try:
            Path(log_path).unlink()
        except OSError:
            pass
    return len(done)


def latest_log(log_dir: str | os.PathLike) -> Path | None:
    log_dir = Path(log_dir)
    if not log_dir.is_dir():
        return None
    logs = sorted(log_dir.glob("rename_log_*.json"))
    return logs[-1] if logs else None


def summarize(items: Iterable[PlanItem]) -> dict[str, int]:
    out = {OK: 0, UNCHANGED: 0, DUPLICATE: 0, EXISTS: 0, INVALID: 0}
    for i in items:
        out[i.status] = out.get(i.status, 0) + 1
    return out
