"""BatchToolkit - 批量文件处理工具箱 (tkinter GUI).

Run:  python app.py                 normal mode
      python app.py --demo          preload sample data from samples/
      python app.py --screenshot DIR   demo mode + capture every tab into DIR, then exit
"""
from __future__ import annotations

import argparse
import ctypes
import os
import queue
import sys
import tempfile
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

try:
    import ctypes.wintypes  # noqa: F401  (Windows window-geometry helpers)
except (ImportError, ValueError):
    pass

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from core import __version__
from core import image as imgcore
from core import pdf as pdfcore
from core import rename as rn
from core.icon import make_icon_image
from core.settings import app_data_dir, load_settings, save_settings

APP_TITLE = "批量文件处理工具箱 BatchToolkit"
UI_FONT = "Microsoft YaHei UI"
ACCENT = "#2563eb"
ACCENT_DARK = "#1e40af"
MUTED = "#64748b"
DANGER = "#dc2626"
SUCCESS = "#16a34a"


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def enable_dpi_awareness() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:  # noqa: BLE001
                pass


def safe_int(var: tk.Variable, default: int = 0) -> int:
    try:
        return int(float(var.get()))
    except (tk.TclError, ValueError):
        return default


def open_in_explorer(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        messagebox.showerror("无法打开", str(exc))


# ============================================================================ widgets

class FileList(ttk.Frame):
    """A Treeview-based file list with add / reorder (buttons + drag) / remove / clear."""

    def __init__(self, master, app: "BatchToolkitApp", columns: list[tuple[str, str, int, str]],
                 row_values: Callable[[Path], tuple], filetypes: list[tuple[str, str]],
                 accept: Callable[[Path], bool], allow_folder: bool = False, reorder: bool = True,
                 on_change: Callable[[], None] | None = None, height: int = 8):
        super().__init__(master)
        self.app = app
        self.row_values = row_values
        self.filetypes = filetypes
        self.accept = accept
        self.on_change = on_change
        self._drag_item = None

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="＋ 添加文件", command=self.add_dialog).pack(side="left")
        if allow_folder:
            ttk.Button(bar, text="＋ 添加文件夹", command=self.add_folder_dialog).pack(side="left", padx=(6, 0))
        if reorder:
            ttk.Button(bar, text="↑ 上移", width=6, command=lambda: self.move(-1)).pack(side="left", padx=(12, 0))
            ttk.Button(bar, text="↓ 下移", width=6, command=lambda: self.move(1)).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="清空", width=5, command=self.clear).pack(side="right")
        ttk.Button(bar, text="移除", width=5, command=self.remove_selected).pack(side="right", padx=(0, 6))

        body = ttk.Frame(self)
        self.count_label = ttk.Label(self, text="", foreground=MUTED)
        self.count_label.pack(side="bottom", anchor="w", pady=(6, 0))
        body.pack(fill="both", expand=True)
        ids = [c[0] for c in columns]
        self.tree = ttk.Treeview(body, columns=ids, show="headings", height=height, selectmode="extended")
        for cid, heading, width, anchor in columns:
            self.tree.heading(cid, text=heading, anchor="w" if anchor == "w" else "center")
            self.tree.column(cid, width=width, minwidth=40, anchor=anchor,
                             stretch=(cid == ids[1] if len(ids) > 1 else True))
        sb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        if reorder:
            self.tree.bind("<ButtonPress-1>", self._drag_start, add="+")
            self.tree.bind("<B1-Motion>", self._drag_motion, add="+")
            self.tree.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag_item", None), add="+")
        self._refresh_count()

    # data
    @property
    def paths(self) -> list[Path]:
        return [Path(i) for i in self.tree.get_children()]

    def add_paths(self, paths) -> int:
        added = 0
        for p in paths:
            p = Path(p)
            if not p.is_file() or not self.accept(p) or self.tree.exists(str(p)):
                continue
            try:
                values = self.row_values(p)
            except Exception as exc:  # noqa: BLE001
                self.app.log(f"无法读取 {p.name}：{exc}", "error")
                continue
            self.tree.insert("", "end", iid=str(p), values=values)
            added += 1
        self._renumber()
        self._changed()
        return added

    def add_dialog(self):
        files = filedialog.askopenfilenames(parent=self, filetypes=self.filetypes,
                                            initialdir=self.app.last_dir())
        if files:
            self.app.remember_dir(Path(files[0]).parent)
            n = self.add_paths(files)
            self.app.log(f"已添加 {n} 个文件")

    def add_folder_dialog(self):
        folder = filedialog.askdirectory(parent=self, initialdir=self.app.last_dir())
        if folder:
            self.app.remember_dir(Path(folder))
            files = sorted((p for p in Path(folder).iterdir() if p.is_file()), key=lambda p: rn.natural_key(p.name))
            n = self.add_paths(files)
            self.app.log(f"已从文件夹添加 {n} 个文件：{folder}")

    def remove_selected(self):
        for item in self.tree.selection():
            self.tree.delete(item)
        self._renumber()
        self._changed()

    def clear(self):
        self.tree.delete(*self.tree.get_children())
        self._changed()

    def move(self, delta: int):
        sel = list(self.tree.selection())
        if not sel:
            return
        items = list(self.tree.get_children())
        order = sorted(sel, key=items.index, reverse=delta > 0)
        for item in order:
            idx = self.tree.index(item)
            new = max(0, min(len(items) - 1, idx + delta))
            self.tree.move(item, "", new)
        self._renumber()
        self._changed()

    def _drag_start(self, event):
        self._drag_item = self.tree.identify_row(event.y) or None

    def _drag_motion(self, event):
        if not self._drag_item:
            return
        target = self.tree.identify_row(event.y)
        if target and target != self._drag_item:
            self.tree.move(self._drag_item, "", self.tree.index(target))
            self._renumber()
            self._changed()

    def _renumber(self):
        cols = self.tree["columns"]
        if cols and cols[0] == "idx":
            for n, item in enumerate(self.tree.get_children(), 1):
                self.tree.set(item, "idx", n)

    def _refresh_count(self):
        n = len(self.tree.get_children())
        self.count_label.configure(text=f"共 {n} 个文件  ·  支持拖动排序 / Delete 键删除" if n else "列表为空，点击「添加文件」开始")

    def _changed(self):
        self._refresh_count()
        if self.on_change:
            self.on_change()


def labeled_scale(parent, var: tk.IntVar, frm: int, to: int, unit: str = "") -> ttk.Frame:
    """A ttk.Scale bound to *var* with a live numeric label."""
    f = ttk.Frame(parent)
    lbl = ttk.Label(f, width=5, anchor="e")
    scale = ttk.Scale(f, from_=frm, to=to, orient="horizontal", variable=var)
    var.trace_add("write", lambda *_: lbl.configure(text=f"{safe_int(var)}{unit}"))
    scale.pack(side="left", fill="x", expand=True)
    lbl.pack(side="left", padx=(8, 0))
    lbl.configure(text=f"{safe_int(var)}{unit}")
    return f


# ============================================================================ tab: rename

NUMBERING_LABELS = {"不编号 None": "none", "序号在前 Prefix": "prefix",
                    "序号在后 Suffix": "suffix", "仅保留序号 Replace": "replace"}
SORT_LABELS = {"按名称 Name": "name", "按修改时间 Modified": "mtime"}
DATE_SRC_LABELS = {"今天 Today": "today", "修改日期 Modified": "modified"}


def _label_for(mapping: dict, value: str) -> str:
    return next((k for k, v in mapping.items() if v == value), next(iter(mapping)))


class RenameTab(ttk.Frame):
    def __init__(self, master, app: "BatchToolkitApp"):
        super().__init__(master, padding=14)
        self.app = app
        self._job = None
        self.plan: rn.RenamePlan | None = None
        self.log_dir = app_data_dir() / "rename_logs"

        s = app.settings.get("rename", {})
        rule = rn.RenameRule.from_dict(s.get("rule", {}))
        self.v_folder = tk.StringVar(value=s.get("folder", ""))
        self.v_ext = tk.StringVar(value=rule.extensions)
        self.v_sort = tk.StringVar(value=_label_for(SORT_LABELS, rule.sort_by))
        self.v_prefix = tk.StringVar(value=rule.prefix)
        self.v_suffix = tk.StringVar(value=rule.suffix)
        self.v_find = tk.StringVar(value=rule.find)
        self.v_replace = tk.StringVar(value=rule.replace)
        self.v_regex = tk.BooleanVar(value=rule.use_regex)
        self.v_case = tk.BooleanVar(value=rule.case_sensitive)
        self.v_numbering = tk.StringVar(value=_label_for(NUMBERING_LABELS, rule.numbering))
        self.v_start = tk.StringVar(value=str(rule.start))
        self.v_step = tk.StringVar(value=str(rule.step))
        self.v_padding = tk.StringVar(value=str(rule.padding))
        self.v_sep = tk.StringVar(value=rule.separator)
        self.v_date = tk.BooleanVar(value=rule.date_prefix)
        self.v_date_fmt = tk.StringVar(value=rule.date_format)
        self.v_date_src = tk.StringVar(value=_label_for(DATE_SRC_LABELS, rule.date_source))
        self.v_lower = tk.BooleanVar(value=rule.lowercase_ext)

        self._build()
        for v in (self.v_folder, self.v_ext, self.v_sort, self.v_prefix, self.v_suffix, self.v_find, self.v_replace,
                  self.v_regex, self.v_case, self.v_numbering, self.v_start, self.v_step, self.v_padding, self.v_sep,
                  self.v_date, self.v_date_fmt, self.v_date_src, self.v_lower):
            v.trace_add("write", lambda *_: self.schedule_preview())
        self.after(100, self.refresh_preview)

    # ------------------------------------------------------------------ layout
    def _build(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        left = ttk.Frame(self, width=380)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 14))

        # source
        g = ttk.LabelFrame(left, text=" 1  选择文件 Source ", padding=(12, 8))
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        ttk.Label(g, text="文件夹").grid(row=0, column=0, sticky="w")
        ttk.Entry(g, textvariable=self.v_folder).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(g, text="浏览…", command=self.browse).grid(row=0, column=2)
        ttk.Label(g, text="扩展名").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(g, textvariable=self.v_ext, width=12).grid(row=1, column=1, sticky="w", padx=6, pady=(8, 0))
        ttk.Combobox(g, textvariable=self.v_sort, values=list(SORT_LABELS), state="readonly", width=16
                     ).grid(row=1, column=1, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Label(g, text="例：jpg, png（留空 = 全部文件）", foreground=MUTED,
                  font=(UI_FONT, 8)).grid(row=2, column=1, columnspan=2, sticky="w", padx=6)

        # text
        g = ttk.LabelFrame(left, text=" 2  文本规则 Text ", padding=(12, 8))
        g.pack(fill="x", pady=(10, 0))
        for c in (1, 3):
            g.columnconfigure(c, weight=1)
        ttk.Label(g, text="前缀").grid(row=0, column=0, sticky="w")
        ttk.Entry(g, textvariable=self.v_prefix, width=10).grid(row=0, column=1, sticky="ew", padx=(6, 10))
        ttk.Label(g, text="后缀").grid(row=0, column=2, sticky="w")
        ttk.Entry(g, textvariable=self.v_suffix, width=10).grid(row=0, column=3, sticky="ew", padx=(6, 0))
        ttk.Label(g, text="查找").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(g, textvariable=self.v_find, width=10).grid(row=1, column=1, sticky="ew", padx=(6, 10), pady=(8, 0))
        ttk.Label(g, text="替换").grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(g, textvariable=self.v_replace, width=10).grid(row=1, column=3, sticky="ew", padx=(6, 0), pady=(8, 0))
        ttk.Checkbutton(g, text="正则 Regex", variable=self.v_regex).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(g, text="区分大小写", variable=self.v_case).grid(row=2, column=2, columnspan=2, sticky="w", pady=(6, 0))

        # numbering
        g = ttk.LabelFrame(left, text=" 3  序号 Numbering ", padding=(12, 8))
        g.pack(fill="x", pady=(10, 0))
        ttk.Combobox(g, textvariable=self.v_numbering, values=list(NUMBERING_LABELS), state="readonly", width=18
                     ).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(g, text="分隔符").grid(row=0, column=4, sticky="e", padx=(10, 4))
        ttk.Entry(g, textvariable=self.v_sep, width=4).grid(row=0, column=5, sticky="w")
        for col, (label, var, lo, hi) in enumerate((("起始", self.v_start, 0, 99999), ("步长", self.v_step, 1, 1000),
                                                     ("位数", self.v_padding, 1, 8))):
            ttk.Label(g, text=label).grid(row=1, column=col * 2, sticky="w", pady=(8, 0), padx=(0 if col == 0 else 8, 4))
            ttk.Spinbox(g, textvariable=var, from_=lo, to=hi, width=5).grid(row=1, column=col * 2 + 1, sticky="w", pady=(8, 0))

        # date & misc
        g = ttk.LabelFrame(left, text=" 4  日期与其他 Date & More ", padding=(12, 8))
        g.pack(fill="x", pady=(10, 0))
        g.columnconfigure(2, weight=1)
        ttk.Checkbutton(g, text="添加日期前缀", variable=self.v_date).grid(row=0, column=0, sticky="w")
        ttk.Entry(g, textvariable=self.v_date_fmt, width=10).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Combobox(g, textvariable=self.v_date_src, values=list(DATE_SRC_LABELS), state="readonly", width=16
                     ).grid(row=0, column=2, sticky="e")
        ttk.Checkbutton(g, text="扩展名转小写 (.JPG → .jpg)", variable=self.v_lower
                        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # right: preview
        right = ttk.Frame(self)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        top = ttk.Frame(right)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(top, text="实时预览 Live Preview", font=(UI_FONT, 11, "bold")).pack(side="left")
        self.summary = ttk.Label(top, text="", foreground=MUTED)
        self.summary.pack(side="right")

        body = ttk.Frame(right)
        body.grid(row=1, column=0, sticky="nsew")
        cols = ("idx", "old", "arrow", "new", "status")
        self.tree = ttk.Treeview(body, columns=cols, show="headings", selectmode="browse")
        for cid, text, w, anchor, stretch in (("idx", "#", 40, "center", False), ("old", "原文件名 Old name", 200, "w", True),
                                              ("arrow", "", 28, "center", False), ("new", "新文件名 New name", 200, "w", True),
                                              ("status", "状态 Status", 130, "w", False)):
            self.tree.heading(cid, text=text, anchor="center" if anchor == "center" else "w")
            self.tree.column(cid, width=w, minwidth=28, anchor=anchor, stretch=stretch)
        self.tree.tag_configure("conflict", foreground=DANGER, background="#fef2f2")
        self.tree.tag_configure("unchanged", foreground="#94a3b8")
        self.tree.tag_configure("ok", foreground="#0f172a")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        btns = ttk.Frame(right)
        btns.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(btns, text="⟳ 刷新预览", command=self.refresh_preview).pack(side="left")
        ttk.Button(btns, text="打开文件夹", command=self.open_folder).pack(side="left", padx=(8, 0))
        self.btn_apply = ttk.Button(btns, text="✔ 执行重命名 Apply", style="Accent.TButton", command=self.apply)
        self.btn_apply.pack(side="right")
        self.btn_undo = ttk.Button(btns, text="↶ 撤销上次 Undo", command=self.undo)
        self.btn_undo.pack(side="right", padx=(0, 8))
        self._update_undo_state()

    # ------------------------------------------------------------------ logic
    def rule(self) -> rn.RenameRule:
        return rn.RenameRule(
            prefix=self.v_prefix.get(), suffix=self.v_suffix.get(), find=self.v_find.get(),
            replace=self.v_replace.get(), use_regex=self.v_regex.get(), case_sensitive=self.v_case.get(),
            numbering=NUMBERING_LABELS.get(self.v_numbering.get(), "none"),
            start=safe_int(self.v_start, 1), step=safe_int(self.v_step, 1) or 1, padding=safe_int(self.v_padding, 3),
            separator=self.v_sep.get(), date_prefix=self.v_date.get(), date_format=self.v_date_fmt.get() or "%Y%m%d",
            date_source=DATE_SRC_LABELS.get(self.v_date_src.get(), "today"), extensions=self.v_ext.get(),
            lowercase_ext=self.v_lower.get(), sort_by=SORT_LABELS.get(self.v_sort.get(), "name"),
        )

    def browse(self):
        folder = filedialog.askdirectory(parent=self, initialdir=self.v_folder.get() or self.app.last_dir())
        if folder:
            self.v_folder.set(folder)
            self.app.remember_dir(Path(folder))

    def open_folder(self):
        folder = Path(self.v_folder.get())
        if folder.is_dir():
            open_in_explorer(folder)

    def schedule_preview(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(220, self.refresh_preview)

    def refresh_preview(self):
        self._job = None
        self.tree.delete(*self.tree.get_children())
        self.plan = None
        folder = self.v_folder.get().strip()
        if not folder:
            self.summary.configure(text="请选择要重命名的文件夹", foreground=MUTED)
            return
        try:
            self.plan = rn.plan_renames(folder, self.rule())
        except (ValueError, FileNotFoundError, OSError) as exc:
            self.summary.configure(text=f"⚠ {exc}", foreground=DANGER)
            return
        for n, item in enumerate(self.plan.items, 1):
            tag = {"ok": "ok", "unchanged": "unchanged"}.get(item.status, "conflict")
            status = rn.STATUS_LABELS[item.status]
            self.tree.insert("", "end", values=(n, item.old, "→", item.new, status), tags=(tag,))
        stats = rn.summarize(self.plan.items)
        conflicts = stats[rn.DUPLICATE] + stats[rn.EXISTS] + stats[rn.INVALID]
        text = f"共 {len(self.plan.items)} 个文件 · {stats[rn.OK]} 个将被重命名"
        if conflicts:
            text += f" · {conflicts} 个冲突"
        self.summary.configure(text=text, foreground=DANGER if conflicts else SUCCESS)

    def apply(self):
        self.refresh_preview()
        if not self.plan:
            return
        if self.plan.has_conflicts:
            messagebox.showwarning("存在冲突", f"有 {len(self.plan.conflicts)} 个文件名冲突（红色行），请调整规则后再执行。",
                                   parent=self)
            return
        n = len(self.plan.ok_items)
        if n == 0:
            messagebox.showinfo("无需重命名", "没有文件名会发生变化。", parent=self)
            return
        if not self.app.confirm("确认重命名", f"即将重命名 {n} 个文件。\n操作完成后可点击「撤销上次」恢复。是否继续？"):
            return
        try:
            log = rn.apply_renames(self.plan, self.log_dir)
        except Exception as exc:  # noqa: BLE001
            self.app.report_error("重命名", exc)
            return
        self.app.log(f"重命名完成：{n} 个文件（撤销日志：{log.name}）", "success")
        self.app.set_status(f"已重命名 {n} 个文件")
        self.refresh_preview()
        self._update_undo_state()

    def undo(self):
        log = rn.latest_log(self.log_dir)
        if not log:
            messagebox.showinfo("没有可撤销的操作", "尚未执行过重命名，或已全部撤销。", parent=self)
            return
        try:
            data = rn.read_log(log)
        except Exception as exc:  # noqa: BLE001
            self.app.report_error("读取撤销日志", exc)
            return
        if not self.app.confirm("确认撤销", f"将恢复 {len(data['renames'])} 个文件的原名称：\n{data['folder']}"):
            return
        try:
            n = rn.undo_renames(log)
        except Exception as exc:  # noqa: BLE001
            self.app.report_error("撤销", exc)
            return
        self.app.log(f"已撤销：恢复 {n} 个文件名", "success")
        self.app.set_status(f"已撤销 {n} 个重命名")
        self.refresh_preview()
        self._update_undo_state()

    def _update_undo_state(self):
        self.btn_undo.state(["!disabled"] if rn.latest_log(self.log_dir) else ["disabled"])

    def export_settings(self) -> dict:
        return {"folder": self.v_folder.get(), "rule": self.rule().to_dict()}


# ============================================================================ tab: images

POSITION_LABELS = {"右下 Bottom-right": "bottom-right", "左下 Bottom-left": "bottom-left",
                   "右上 Top-right": "top-right", "左上 Top-left": "top-left",
                   "居中 Center": "center", "平铺 Tile": "tile"}
FORMAT_LABELS = {"保持原格式 Keep": "KEEP", "JPG": "JPG", "PNG": "PNG", "WEBP": "WEBP"}


def _image_row(p: Path) -> tuple:
    with Image.open(p) as im:
        w, h = im.size
    return (p.name, f"{w}×{h}", imgcore.human_size(p.stat().st_size))


class ImageTab(ttk.Frame):
    def __init__(self, master, app: "BatchToolkitApp"):
        super().__init__(master, padding=14)
        self.app = app
        self.cancel_event = threading.Event()
        self._preview_job = None
        self._photo = None

        s = app.settings.get("image", {})
        o = imgcore.ImageOptions.from_dict(s.get("options", {}))
        self.v_w = tk.StringVar(value=str(o.max_width))
        self.v_h = tk.StringVar(value=str(o.max_height))
        self.v_quality = tk.IntVar(value=o.quality)
        self.v_format = tk.StringVar(value=_label_for(FORMAT_LABELS, o.out_format))
        self.v_wm = tk.StringVar(value=o.watermark_text)
        self.v_pos = tk.StringVar(value=_label_for(POSITION_LABELS, o.watermark_position))
        self.v_opacity = tk.IntVar(value=o.watermark_opacity)
        self.v_wmsize = tk.IntVar(value=o.watermark_size)
        self.v_suffix = tk.StringVar(value=o.name_suffix)
        self.v_out = tk.StringVar(value=s.get("out_dir", ""))
        self._build()
        for v in (self.v_w, self.v_h, self.v_wm, self.v_pos, self.v_opacity, self.v_wmsize, self.v_format):
            v.trace_add("write", lambda *_: self.schedule_preview())

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        left = ttk.Frame(self)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        left.rowconfigure(0, weight=3)
        left.rowconfigure(1, weight=2)
        left.columnconfigure(0, weight=1)

        g = ttk.LabelFrame(left, text=" 待处理图片 Input images ", padding=(12, 8))
        g.grid(row=0, column=0, sticky="nsew")
        exts = " ".join(f"*{e}" for e in sorted(imgcore.IMAGE_EXTS))
        self.files = FileList(g, self.app, [("name", "文件名 Name", 220, "w"), ("dims", "尺寸 Size", 100, "center"),
                                            ("bytes", "大小", 80, "e")],
                              _image_row, [("图片 Images", exts), ("所有文件", "*.*")], imgcore.is_image,
                              allow_folder=True, reorder=False, on_change=self.schedule_preview, height=5)
        self.files.pack(fill="both", expand=True)
        self.files.tree.bind("<<TreeviewSelect>>", lambda e: self.schedule_preview())

        g = ttk.LabelFrame(left, text=" 效果预览 Preview ", padding=(12, 8))
        g.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.preview = tk.Label(g, bg="#f1f5f9", text="选择图片后在此预览处理效果", fg=MUTED, font=(UI_FONT, 9))
        self.preview.pack(fill="both", expand=True)
        self.preview_info = ttk.Label(g, text="", foreground=MUTED)
        self.preview_info.pack(anchor="w", pady=(6, 0))
        self.preview.bind("<Configure>", lambda e: self.schedule_preview())

        right = ttk.Frame(self, width=360)
        right.grid(row=0, column=1, sticky="nsew")

        g = ttk.LabelFrame(right, text=" 尺寸与压缩 Resize & Compress ", padding=(12, 8))
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        ttk.Label(g, text="最大宽×高").grid(row=0, column=0, sticky="w")
        wh = ttk.Frame(g)
        wh.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Spinbox(wh, textvariable=self.v_w, from_=0, to=20000, increment=100, width=6).pack(side="left")
        ttk.Label(wh, text=" × ").pack(side="left")
        ttk.Spinbox(wh, textvariable=self.v_h, from_=0, to=20000, increment=100, width=6).pack(side="left")
        ttk.Label(g, text="单位 px · 0 = 不限制 · 等比缩放，不放大", foreground=MUTED, font=(UI_FONT, 8)
                  ).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(g, text="压缩质量").grid(row=2, column=0, sticky="w", pady=(6, 0))
        labeled_scale(g, self.v_quality, 10, 100).grid(row=2, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Label(g, text="输出格式").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(g, textvariable=self.v_format, values=list(FORMAT_LABELS), state="readonly", width=16
                     ).grid(row=3, column=1, sticky="w", padx=6, pady=(6, 0))

        g = ttk.LabelFrame(right, text=" 文字水印 Watermark ", padding=(12, 8))
        g.pack(fill="x", pady=(8, 0))
        g.columnconfigure(1, weight=1)
        ttk.Label(g, text="水印文字").grid(row=0, column=0, sticky="w")
        ttk.Entry(g, textvariable=self.v_wm).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(g, text="位置").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(g, textvariable=self.v_pos, values=list(POSITION_LABELS), state="readonly", width=16
                     ).grid(row=1, column=1, sticky="w", padx=6, pady=(6, 0))
        ttk.Label(g, text="不透明度").grid(row=2, column=0, sticky="w", pady=(4, 0))
        labeled_scale(g, self.v_opacity, 5, 100, "%").grid(row=2, column=1, sticky="ew", padx=6, pady=(4, 0))
        ttk.Label(g, text="字号").grid(row=3, column=0, sticky="w", pady=(4, 0))
        labeled_scale(g, self.v_wmsize, 1, 20, "%").grid(row=3, column=1, sticky="ew", padx=6, pady=(4, 0))

        g = ttk.LabelFrame(right, text=" 输出 Output ", padding=(12, 8))
        g.pack(fill="x", pady=(8, 0))
        g.columnconfigure(1, weight=1)
        ttk.Label(g, text="输出到").grid(row=0, column=0, sticky="w")
        ttk.Entry(g, textvariable=self.v_out).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(g, text="浏览…", command=self.browse_out).grid(row=0, column=2)
        ttk.Label(g, text="文件名后缀").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(g, textvariable=self.v_suffix, width=8).grid(row=1, column=1, sticky="w", padx=6, pady=(6, 0))
        ttk.Label(g, text="输出目录留空 = 源目录/output", foreground=MUTED, font=(UI_FONT, 8)
                  ).grid(row=1, column=1, columnspan=2, sticky="e", pady=(6, 0))

        g = ttk.Frame(right)
        g.pack(fill="x", pady=(10, 0))
        self.progress = ttk.Progressbar(g, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        self.progress_label = ttk.Label(g, text="就绪 Ready", foreground=MUTED)
        self.progress_label.pack(anchor="w", pady=(4, 8))
        row = ttk.Frame(g)
        row.pack(fill="x")
        self.btn_start = ttk.Button(row, text="▶ 开始处理 Start", style="Accent.TButton", command=self.start)
        self.btn_start.pack(side="right")
        self.btn_cancel = ttk.Button(row, text="■ 取消", command=self.cancel_event.set, state="disabled")
        self.btn_cancel.pack(side="right", padx=(0, 8))
        ttk.Button(row, text="打开输出文件夹", command=self.open_out).pack(side="left")

    def options(self) -> imgcore.ImageOptions:
        return imgcore.ImageOptions(
            max_width=safe_int(self.v_w), max_height=safe_int(self.v_h), quality=safe_int(self.v_quality, 85),
            out_format=FORMAT_LABELS.get(self.v_format.get(), "KEEP"), watermark_text=self.v_wm.get(),
            watermark_position=POSITION_LABELS.get(self.v_pos.get(), "bottom-right"),
            watermark_opacity=safe_int(self.v_opacity, 60), watermark_size=safe_int(self.v_wmsize, 5),
            name_suffix=self.v_suffix.get(),
        )

    def output_dir(self) -> Path | None:
        out = self.v_out.get().strip()
        if out:
            return Path(out)
        paths = self.files.paths
        return paths[0].parent / "output" if paths else None

    def browse_out(self):
        folder = filedialog.askdirectory(parent=self, initialdir=self.v_out.get() or self.app.last_dir())
        if folder:
            self.v_out.set(folder)

    def open_out(self):
        out = self.output_dir()
        if out and out.is_dir():
            open_in_explorer(out)
        else:
            messagebox.showinfo("提示", "输出文件夹尚未创建，处理完成后再打开。", parent=self)

    def schedule_preview(self):
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(250, self.render_preview)

    def render_preview(self):
        self._preview_job = None
        sel = self.files.tree.selection()
        items = self.files.tree.get_children()
        target = sel[0] if sel else (items[0] if items else None)
        if not target:
            self.preview.configure(image="", text="选择图片后在此预览处理效果")
            self.preview_info.configure(text="")
            return
        box_w = max(self.preview.winfo_width() - 16, 120)
        box_h = max(self.preview.winfo_height() - 16, 90)
        try:
            opts = self.options()
            with Image.open(target) as im:
                im.load()
                src_size = im.size
                out = imgcore.apply_options(im, opts)
            final_size = out.size
            thumb = out.convert("RGBA")
            thumb.thumbnail((box_w, box_h), Image.Resampling.LANCZOS)
            self._photo = ImageTk.PhotoImage(thumb)
            self.preview.configure(image=self._photo, text="")
            fmt = imgcore.output_format_for(Path(target), opts)
            self.preview_info.configure(
                text=f"{Path(target).name}   {src_size[0]}×{src_size[1]}  →  {final_size[0]}×{final_size[1]}  ·  {fmt}")
        except Exception as exc:  # noqa: BLE001
            self.preview.configure(image="", text=f"无法预览：{exc}")

    def start(self):
        files = self.files.paths
        if not files:
            messagebox.showinfo("没有图片", "请先添加要处理的图片。", parent=self)
            return
        out_dir = self.output_dir()
        opts = self.options()
        self.cancel_event.clear()
        self.btn_start.state(["disabled"])
        self.btn_cancel.state(["!disabled"])
        self.progress.configure(value=0, maximum=len(files))
        self.app.log(f"开始处理 {len(files)} 张图片 → {out_dir}")

        def work(report):
            return imgcore.batch_process(files, out_dir, opts, progress=lambda d, t, r: report(d, t, r),
                                         cancel=self.cancel_event)

        def on_progress(done, total, res):
            self.progress.configure(value=done)
            self.progress_label.configure(text=f"{done}/{total}  {res.src.name}")
            if res.ok:
                self.app.log(f"  ✓ {res.src.name} → {res.dst.name}  ({imgcore.human_size(res.src_bytes)} → "
                             f"{imgcore.human_size(res.dst_bytes)})")
            else:
                self.app.log(f"  ✗ {res.src.name}：{res.error}", "error")

        def on_done(results):
            self.btn_start.state(["!disabled"])
            self.btn_cancel.state(["disabled"])
            ok = [r for r in results if r.ok]
            failed = len(results) - len(ok)
            src_total = sum(r.src_bytes for r in ok)
            dst_total = sum(r.dst_bytes for r in ok)
            saved = (1 - dst_total / src_total) * 100 if src_total else 0
            cancelled = len(results) < len(files)
            msg = (f"{'已取消' if cancelled else '处理完成'}：成功 {len(ok)}，失败 {failed}，"
                   f"{imgcore.human_size(src_total)} → {imgcore.human_size(dst_total)}（体积减少 {saved:.0f}%）")
            self.progress_label.configure(text=msg)
            self.app.log(msg, "error" if failed else "success")
            self.app.set_status(msg)

        def on_error(_exc):
            self.btn_start.state(["!disabled"])
            self.btn_cancel.state(["disabled"])

        self.app.run_task("图片处理", work, on_done=on_done, on_progress=on_progress, on_error=on_error)

    def export_settings(self) -> dict:
        return {"options": self.options().to_dict(), "out_dir": self.v_out.get()}


# ============================================================================ tab: pdf

def _pdf_row(p: Path) -> tuple:
    return ("", p.name, pdfcore.page_count(p), imgcore.human_size(p.stat().st_size))


def _img_row_idx(p: Path) -> tuple:
    return ("",) + _image_row(p)


class PdfTab(ttk.Frame):
    def __init__(self, master, app: "BatchToolkitApp"):
        super().__init__(master, padding=14)
        self.app = app
        s = app.settings.get("pdf", {})
        self.v_merge_out = tk.StringVar(value=s.get("merge_out", ""))
        self.v_split_src = tk.StringVar()
        self.v_split_ranges = tk.StringVar(value=s.get("split_ranges", "1-3, 5, 8-"))
        self.v_split_each = tk.BooleanVar(value=False)
        self.v_split_out = tk.StringVar(value=s.get("split_out", ""))
        self.v_img_out = tk.StringVar(value=s.get("img_out", ""))
        self.v_page = tk.StringVar(value=s.get("page_size", "A4"))
        self._build()

    def _build(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        self.merge_tab = ttk.Frame(self.nb, padding=12)
        self.split_tab = ttk.Frame(self.nb, padding=12)
        self.img_tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(self.merge_tab, text="  合并 Merge  ")
        self.nb.add(self.split_tab, text="  拆分 Split  ")
        self.nb.add(self.img_tab, text="  图片转 PDF Images → PDF  ")
        self._build_merge()
        self._build_split()
        self._build_images()

    @staticmethod
    def _hint(parent, text):
        return ttk.Label(parent, text=text, foreground=MUTED)

    def _output_row(self, parent, label, var, command):
        row = ttk.Frame(parent)
        row.columnconfigure(1, weight=1)
        ttk.Label(row, text=label).grid(row=0, column=0, sticky="w")
        ttk.Entry(row, textvariable=var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(row, text="浏览…", command=command).grid(row=0, column=2)
        return row

    # merge -------------------------------------------------------------------
    def _build_merge(self):
        f = self.merge_tab
        self._hint(f, "添加多个 PDF，通过 ↑↓ 按钮或拖动调整顺序，然后合并为一个文件。").pack(anchor="w", pady=(0, 8))
        self.merge_list = FileList(f, self.app, [("idx", "#", 44, "center"), ("name", "文件名 File", 360, "w"),
                                                 ("pages", "页数 Pages", 90, "center"), ("bytes", "大小 Size", 90, "e")],
                                   _pdf_row, [("PDF", "*.pdf")], lambda p: p.suffix.lower() == ".pdf",
                                   on_change=self._merge_changed, height=6)
        bottom = ttk.Frame(f)
        bottom.pack(side="bottom", fill="x", pady=(10, 0))
        self.merge_list.pack(fill="both", expand=True)
        self._output_row(bottom, "保存为", self.v_merge_out, self._browse_merge_out).pack(side="left", fill="x", expand=True)
        ttk.Button(bottom, text="合并 PDF  Merge", style="Accent.TButton", command=self.merge
                   ).pack(side="right", padx=(12, 0))

    def _merge_changed(self):
        total = 0
        for item in self.merge_list.tree.get_children():
            try:
                total += int(self.merge_list.tree.set(item, "pages"))
            except ValueError:
                pass
        n = len(self.merge_list.paths)
        if n:
            self.merge_list.count_label.configure(text=f"共 {n} 个文件，合计 {total} 页  ·  支持拖动排序 / Delete 键删除")

    def _browse_merge_out(self):
        f = filedialog.asksaveasfilename(parent=self, defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
                                         initialfile="merged.pdf", initialdir=self.app.last_dir())
        if f:
            self.v_merge_out.set(f)

    def merge(self):
        paths = self.merge_list.paths
        if len(paths) < 2:
            messagebox.showinfo("提示", "请至少添加两个 PDF 文件。", parent=self)
            return
        out = self.v_merge_out.get().strip() or str(paths[0].parent / "merged.pdf")
        self.v_merge_out.set(out)
        self.app.run_task("合并 PDF", lambda report: pdfcore.merge_pdfs(paths, out),
                          on_done=lambda pages: self._done(f"合并完成：{len(paths)} 个文件，共 {pages} 页 → {out}"))

    # split -------------------------------------------------------------------
    def _build_split(self):
        f = self.split_tab
        f.columnconfigure(1, weight=1)
        self._hint(f, "按页码范围拆分 PDF，每个范围生成一个新文件；也可以每页拆成一个文件。"
                   ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        ttk.Label(f, text="源文件").grid(row=1, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.v_split_src).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Button(f, text="选择 PDF…", command=self._browse_split_src).grid(row=1, column=2)
        self.split_info = ttk.Label(f, text="", foreground=MUTED)
        self.split_info.grid(row=2, column=1, sticky="w", padx=8, pady=(4, 0))
        ttk.Label(f, text="页码范围").grid(row=3, column=0, sticky="w", pady=(14, 0))
        self.range_entry = ttk.Entry(f, textvariable=self.v_split_ranges)
        self.range_entry.grid(row=3, column=1, sticky="ew", padx=8, pady=(14, 0))
        ttk.Checkbutton(f, text="每页一个文件", variable=self.v_split_each,
                        command=self._toggle_each).grid(row=3, column=2, sticky="w", pady=(14, 0))
        self._hint(f, "格式：1-3, 5, 8-   （8- 表示第 8 页到最后；-4 表示第 1 到第 4 页）"
                   ).grid(row=4, column=1, sticky="w", padx=8, pady=(4, 0))
        self.split_preview = ttk.Label(f, text="", foreground=ACCENT)
        self.split_preview.grid(row=5, column=1, sticky="w", padx=8, pady=(10, 0))
        ttk.Label(f, text="输出到").grid(row=6, column=0, sticky="w", pady=(14, 0))
        ttk.Entry(f, textvariable=self.v_split_out).grid(row=6, column=1, sticky="ew", padx=8, pady=(14, 0))
        ttk.Button(f, text="浏览…", command=self._browse_split_out).grid(row=6, column=2, pady=(14, 0))
        self._hint(f, "留空则输出到源文件所在目录下的「split」文件夹").grid(row=7, column=1, sticky="w", padx=8, pady=(4, 0))
        ttk.Button(f, text="✂ 拆分 PDF Split", style="Accent.TButton", command=self.split
                   ).grid(row=8, column=2, sticky="e", pady=(24, 0))
        for v in (self.v_split_src, self.v_split_ranges, self.v_split_each):
            v.trace_add("write", lambda *_: self._update_split_preview())

    def _toggle_each(self):
        self.range_entry.state(["disabled"] if self.v_split_each.get() else ["!disabled"])

    def _browse_split_src(self):
        f = filedialog.askopenfilename(parent=self, filetypes=[("PDF", "*.pdf")], initialdir=self.app.last_dir())
        if f:
            self.v_split_src.set(f)
            self.app.remember_dir(Path(f).parent)

    def _browse_split_out(self):
        folder = filedialog.askdirectory(parent=self, initialdir=self.app.last_dir())
        if folder:
            self.v_split_out.set(folder)

    def _update_split_preview(self):
        src = self.v_split_src.get().strip()
        if not src or not Path(src).is_file():
            self.split_info.configure(text="")
            self.split_preview.configure(text="")
            return
        try:
            total = pdfcore.page_count(src)
        except Exception as exc:  # noqa: BLE001
            self.split_info.configure(text=f"⚠ {exc}", foreground=DANGER)
            return
        self.split_info.configure(text=f"共 {total} 页 · {imgcore.human_size(Path(src).stat().st_size)}",
                                  foreground=MUTED)
        if self.v_split_each.get():
            self.split_preview.configure(text=f"将生成 {total} 个单页 PDF", foreground=ACCENT)
            return
        try:
            ranges = pdfcore.parse_page_ranges(self.v_split_ranges.get(), total)
            parts = ", ".join(f"第{a}页" if a == b else f"第{a}–{b}页" for a, b in ranges)
            self.split_preview.configure(text=f"将生成 {len(ranges)} 个文件：{parts}", foreground=ACCENT)
        except ValueError as exc:
            self.split_preview.configure(text=f"⚠ {exc}", foreground=DANGER)

    def split(self):
        src = self.v_split_src.get().strip()
        if not src:
            messagebox.showinfo("提示", "请先选择要拆分的 PDF。", parent=self)
            return
        out = self.v_split_out.get().strip() or str(Path(src).parent / "split")
        spec, each = self.v_split_ranges.get(), self.v_split_each.get()
        self.app.run_task("拆分 PDF", lambda report: pdfcore.split_pdf(src, out, spec, each_page=each),
                          on_done=lambda outs: self._done(f"拆分完成：生成 {len(outs)} 个文件 → {out}"))

    # images -> pdf -----------------------------------------------------------
    def _build_images(self):
        f = self.img_tab
        self._hint(f, "把多张图片（扫描件、照片、截图）按顺序合成一个 PDF。").pack(anchor="w", pady=(0, 8))
        exts = " ".join(f"*{e}" for e in sorted(imgcore.IMAGE_EXTS))
        self.img_list = FileList(f, self.app, [("idx", "#", 44, "center"), ("name", "文件名 File", 320, "w"),
                                               ("dims", "尺寸 Size", 110, "center"), ("bytes", "大小", 90, "e")],
                                 _img_row_idx, [("图片 Images", exts)], imgcore.is_image, allow_folder=True, height=6)
        bottom = ttk.Frame(f)
        bottom.pack(side="bottom", fill="x", pady=(10, 0))
        self.img_list.pack(fill="both", expand=True)
        ttk.Label(bottom, text="页面").pack(side="left")
        ttk.Combobox(bottom, textvariable=self.v_page, values=list(pdfcore.PAGE_SIZES), state="readonly", width=9
                     ).pack(side="left", padx=(8, 14))
        self._output_row(bottom, "保存为", self.v_img_out, self._browse_img_out).pack(side="left", fill="x", expand=True)
        ttk.Button(bottom, text="生成 PDF  Create", style="Accent.TButton", command=self.images_to_pdf
                   ).pack(side="right", padx=(12, 0))

    def _browse_img_out(self):
        f = filedialog.asksaveasfilename(parent=self, defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
                                         initialfile="images.pdf", initialdir=self.app.last_dir())
        if f:
            self.v_img_out.set(f)

    def images_to_pdf(self):
        paths = self.img_list.paths
        if not paths:
            messagebox.showinfo("提示", "请先添加图片。", parent=self)
            return
        out = self.v_img_out.get().strip() or str(paths[0].parent / "images.pdf")
        self.v_img_out.set(out)
        page = self.v_page.get()
        self.app.run_task("图片转 PDF", lambda report: pdfcore.images_to_pdf(paths, out, page_size=page),
                          on_done=lambda n: self._done(f"已生成 PDF：{n} 页 → {out}"))

    def _done(self, msg: str):
        self.app.log(msg, "success")
        self.app.set_status(msg)

    def export_settings(self) -> dict:
        return {"merge_out": self.v_merge_out.get(), "split_ranges": self.v_split_ranges.get(),
                "split_out": self.v_split_out.get(), "img_out": self.v_img_out.get(), "page_size": self.v_page.get()}


# ============================================================================ main window

class BatchToolkitApp(tk.Tk):
    def __init__(self, demo: bool = False, settings_path: Path | None = None):
        super().__init__()
        self.demo = demo
        self.settings_path = settings_path
        self.settings = {} if demo else load_settings(settings_path)
        self.busy = False
        self.withdraw()
        self.title(APP_TITLE)
        self._setup_theme()
        self._icon = ImageTk.PhotoImage(make_icon_image(64))
        self.iconphoto(True, self._icon)
        self.geometry(self.settings.get("geometry", "1240x860"))
        self.minsize(1060, 720)
        self.report_callback_exception = self._on_tk_error
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.deiconify()
        self.log(f"BatchToolkit v{__version__} 已启动 · 设置文件：{settings_path or app_data_dir() / 'settings.json'}")

    # theme ------------------------------------------------------------------
    def _setup_theme(self):
        style = ttk.Style(self)
        try:
            import sv_ttk
            sv_ttk.set_theme("light", self)
        except Exception:  # noqa: BLE001 - fall back to a clean built-in theme
            style.theme_use("clam")
            style.configure("Accent.TButton", background=ACCENT, foreground="white")
            style.map("Accent.TButton", background=[("active", ACCENT_DARK), ("disabled", "#93c5fd")])
        families = set(tkfont.families(self))
        family = UI_FONT if UI_FONT in families else ("Microsoft YaHei" if "Microsoft YaHei" in families else None)
        if family:
            for name in tkfont.names(self):
                f = tkfont.nametofont(name, self)
                if name.startswith("SunValley") or name in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                                                             "TkHeadingFont", "TkCaptionFont", "TkTooltipFont"):
                    bold = "Semibold" in str(f.actual("family")) or "Strong" in name or "Title" in name or "Subtitle" in name
                    f.configure(family=family, weight="bold" if bold else "normal")
            if "SunValleyBodyFont" in tkfont.names(self):
                tkfont.nametofont("SunValleyBodyFont", self).configure(size=-13)
        style.configure("Treeview", rowheight=30)
        style.configure("TLabelframe.Label", font=(UI_FONT, 10, "bold"), foreground="#1e293b")
        style.configure("TNotebook.Tab", padding=(16, 6))

    # layout -----------------------------------------------------------------
    def _build(self):
        header = tk.Frame(self, bg=ACCENT, height=72)
        header.pack(fill="x")
        header.pack_propagate(False)
        self._header_icon = ImageTk.PhotoImage(make_icon_image(44))
        tk.Label(header, image=self._header_icon, bg=ACCENT).pack(side="left", padx=(18, 12))
        titles = tk.Frame(header, bg=ACCENT)
        titles.pack(side="left")
        tk.Label(titles, text="批量文件处理工具箱", font=(UI_FONT, 16, "bold"), fg="white", bg=ACCENT
                 ).pack(anchor="w")
        tk.Label(titles, text="BatchToolkit  ·  批量重命名  ·  图片压缩/水印  ·  PDF 合并拆分", font=(UI_FONT, 9),
                 fg="#dbeafe", bg=ACCENT).pack(anchor="w")
        tk.Label(header, text=f"v{__version__}", font=(UI_FONT, 9), fg="#bfdbfe", bg=ACCENT).pack(side="right", padx=18)

        status = ttk.Frame(self, padding=(14, 4))
        status.pack(side="bottom", fill="x")
        self.status_label = ttk.Label(status, text="就绪 Ready", foreground=MUTED)
        self.status_label.pack(side="left")
        ttk.Label(status, text="设置自动保存 · Settings are remembered", foreground="#94a3b8").pack(side="right")

        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, padx=12, pady=(10, 0))

        self.nb = ttk.Notebook(paned)
        self.rename_tab = RenameTab(self.nb, self)
        self.image_tab = ImageTab(self.nb, self)
        self.pdf_tab = PdfTab(self.nb, self)
        self.nb.add(self.rename_tab, text="  批量重命名 Rename  ")
        self.nb.add(self.image_tab, text="  图片批量处理 Images  ")
        self.nb.add(self.pdf_tab, text="  PDF 工具 PDF Tools  ")
        paned.add(self.nb, weight=5)
        try:
            self.nb.select(int(self.settings.get("tab", 0)))
        except (tk.TclError, ValueError):
            pass

        logf = ttk.LabelFrame(paned, text=" 运行日志 Log ", padding=(8, 4))
        paned.add(logf, weight=1)
        self.log_text = tk.Text(logf, height=4, wrap="word", relief="flat", bg="#f8fafc", fg="#334155",
                                font=(UI_FONT, 9), padx=8, pady=6, state="disabled", borderwidth=0)
        sb = ttk.Scrollbar(logf, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.log_text.tag_configure("time", foreground="#94a3b8")
        self.log_text.tag_configure("success", foreground=SUCCESS)
        self.log_text.tag_configure("error", foreground=DANGER)
        self.log_text.tag_configure("info", foreground="#334155")

    # helpers ----------------------------------------------------------------
    def log(self, msg: str, level: str = "info"):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", datetime.now().strftime("%H:%M:%S  "), "time")
        self.log_text.insert("end", msg + "\n", level)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def set_status(self, msg: str):
        self.status_label.configure(text=msg)

    def last_dir(self) -> str:
        return self.settings.get("last_dir", str(Path.home()))

    def remember_dir(self, path: Path):
        self.settings["last_dir"] = str(path)

    def confirm(self, title: str, msg: str) -> bool:
        return messagebox.askyesno(title, msg, parent=self)

    def report_error(self, action: str, exc: BaseException):
        msg = str(exc) or exc.__class__.__name__
        if isinstance(exc, PermissionError):
            msg = f"没有权限访问文件（可能被其他程序占用）：{exc.filename or ''}"
        self.log(f"{action}失败：{msg}", "error")
        self.set_status(f"{action}失败")
        if not self.demo:
            messagebox.showerror("出错了", f"{action}失败：\n\n{msg}", parent=self)

    def _on_tk_error(self, exc_type, exc, tb):
        traceback.print_exception(exc_type, exc, tb)
        self.report_error("操作", exc)

    def run_task(self, name: str, func: Callable, on_done: Callable | None = None,
                 on_progress: Callable | None = None, on_error: Callable | None = None):
        """Run func(report) in a worker thread; marshal progress/results back to the UI thread."""
        if self.busy:
            messagebox.showinfo("请稍候", "另一个任务正在运行，请等待完成。", parent=self)
            if on_error:
                on_error(None)
            return
        self.busy = True
        self.set_status(f"{name}进行中…")
        q: queue.Queue = queue.Queue()

        def worker():
            try:
                res = func(lambda *a: q.put(("progress", a)))
                q.put(("done", res))
            except Exception as exc:  # noqa: BLE001
                q.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

        def poll():
            try:
                while True:
                    kind, payload = q.get_nowait()
                    if kind == "progress":
                        if on_progress:
                            on_progress(*payload)
                    elif kind == "done":
                        self.busy = False
                        if on_done:
                            on_done(payload)
                        return
                    else:
                        self.busy = False
                        self.report_error(name, payload)
                        if on_error:
                            on_error(payload)
                        return
            except queue.Empty:
                pass
            self.after(60, poll)

        poll()

    def collect_settings(self) -> dict:
        data = dict(self.settings)
        data.update({
            "geometry": self.geometry(),
            "tab": self.nb.index(self.nb.select()),
            "rename": self.rename_tab.export_settings(),
            "image": self.image_tab.export_settings(),
            "pdf": self.pdf_tab.export_settings(),
        })
        return data

    def on_close(self):
        if not self.demo:
            try:
                save_settings(self.collect_settings(), self.settings_path)
            except OSError:
                pass
        self.destroy()

    # demo -------------------------------------------------------------------
    def load_demo(self, samples: Path):
        out_root = Path(tempfile.gettempdir()).resolve() / "BatchToolkit_demo_output"
        import shutil
        shutil.rmtree(out_root, ignore_errors=True)   # fresh output for every demo run
        r = self.rename_tab
        r.v_folder.set(str(samples / "rename"))
        r.v_ext.set("jpg, png")
        r.v_prefix.set("Trip_")
        r.v_numbering.set(_label_for(NUMBERING_LABELS, "replace"))
        r.v_padding.set("3")
        r.v_date.set(True)
        r.v_date_src.set(_label_for(DATE_SRC_LABELS, "modified"))
        r.v_lower.set(True)

        i = self.image_tab
        i.files.add_paths(imgcore.collect_images(samples / "images"))
        i.v_w.set("1200")
        i.v_h.set("1200")
        i.v_quality.set(80)
        i.v_format.set("WEBP")
        i.v_wm.set("© BatchToolkit 样图")
        i.v_opacity.set(70)
        i.v_wmsize.set(6)
        i.v_suffix.set("_web")
        i.v_out.set(str(out_root / "images"))
        kids = i.files.tree.get_children()
        if kids:
            i.files.tree.selection_set(kids[0])

        p = self.pdf_tab
        pdfs = sorted((samples / "pdfs").glob("*.pdf"), key=lambda x: x.name)
        order = [x for x in pdfs if "合同" in x.name] + [x for x in pdfs if "合同" not in x.name]
        p.merge_list.add_paths(order)
        p.v_merge_out.set(str(out_root / "合同_合并版.pdf"))
        p.v_split_src.set(str(samples / "pdfs" / "Report_Q2.pdf"))
        p.v_split_ranges.set("1-2, 3, 5-")
        p.v_split_out.set(str(out_root / "split"))
        p.img_list.add_paths(imgcore.collect_images(samples / "images"))
        p.v_img_out.set(str(out_root / "产品图册.pdf"))
        self.log(f"演示模式：已从 {samples} 载入示例数据")


# ============================================================================ screenshots

def window_bbox(win: tk.Tk) -> tuple[int, int, int, int]:
    """Screen bbox of the whole window incl. title bar (DWM extended frame, no shadow)."""
    win.update_idletasks()
    if sys.platform == "win32":
        try:
            hwnd = int(win.wm_frame(), 16)
            rect = ctypes.wintypes.RECT()
            DWMWA_EXTENDED_FRAME_BOUNDS = 9
            res = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                ctypes.wintypes.HWND(hwnd), ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
                ctypes.byref(rect), ctypes.sizeof(rect))
            if res == 0:
                return rect.left, rect.top, rect.right, rect.bottom
        except Exception:  # noqa: BLE001
            pass
    x, y = win.winfo_rootx(), win.winfo_rooty()
    return x, y, x + win.winfo_width(), y + win.winfo_height()


def _printwindow_capture(win: tk.Tk) -> Image.Image:
    """Render the top-level window (incl. title bar) via Win32 PrintWindow.

    Works even when the window is covered or the session has no visible screen
    (e.g. a sandboxed desktop where screen BitBlt / ImageGrab fails).
    """
    import ctypes.wintypes as wt

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    hwnd = int(win.wm_frame(), 16)
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    PW_RENDERFULLCONTENT = 2
    if not user32.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT):
        user32.PrintWindow(hwnd, mdc, 0)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    data = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mdc, bmp, 0, h, data, ctypes.byref(bih), 0)
    gdi32.SelectObject(mdc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(hwnd, hdc)
    img = Image.frombuffer("RGB", (w, h), data, "raw", "BGRX", 0, 1)

    # crop the invisible resize borders (rendered black) using the visible frame bounds
    x0, y0, x1, y1 = window_bbox(win)
    crop = (x0 - rect.left, y0 - rect.top, w - (rect.right - x1), h - (rect.bottom - y1))
    if crop == (0, 0, w, h):   # DWM unavailable: fall back to the system frame metrics
        border = user32.GetSystemMetrics(32) + user32.GetSystemMetrics(92)  # SM_CXSIZEFRAME + SM_CXPADDEDBORDER
        crop = (border, 0, w - border, h - border)
    return img.crop(crop)


def capture_window(win: tk.Tk) -> Image.Image:
    win.update()
    if sys.platform == "win32":
        try:
            img = _printwindow_capture(win)
            if img.getextrema() != ((0, 0), (0, 0), (0, 0)):
                return img
        except Exception as exc:  # noqa: BLE001
            print("PrintWindow failed:", exc, flush=True)
    from PIL import ImageGrab
    return ImageGrab.grab(bbox=window_bbox(win), all_screens=True)


def run_screenshot_mode(app: BatchToolkitApp, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    app.geometry("1240x860+40+30")
    app.attributes("-topmost", True)
    app.lift()
    app.focus_force()
    shots: list[tuple[str, Callable[[], None], int]] = [
        ("01_rename.png", lambda: app.nb.select(0), 900),
        ("02_images.png", lambda: (app.nb.select(1), app.image_tab.start()), 3500),
        ("03_pdf_merge.png", lambda: (app.nb.select(2), app.pdf_tab.nb.select(0), app.pdf_tab.merge()), 2000),
        ("04_pdf_split.png", lambda: (app.pdf_tab.nb.select(1), app.pdf_tab.split()), 2000),
        ("05_pdf_images.png", lambda: (app.pdf_tab.nb.select(2), app.pdf_tab.images_to_pdf()), 3000),
    ]

    def step(i=0):
        if i >= len(shots):
            app.destroy()
            return
        name, action, wait = shots[i]
        action()

        def grab():
            img = capture_window(app)
            img.save(out_dir / name)
            print("saved", out_dir / name, img.size, flush=True)
            app.after(200, lambda: step(i + 1))

        app.after(wait, grab)

    app.after(1200, step)
    app.after(90_000, app.destroy)   # safety net: never hang in automation


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--demo", action="store_true", help="preload sample data from samples/")
    parser.add_argument("--screenshot", metavar="DIR", help="demo mode: capture every tab into DIR and exit")
    parser.add_argument("--samples", metavar="DIR", help="samples folder (default: ./samples)")
    args = parser.parse_args(argv)

    enable_dpi_awareness()
    demo = bool(args.demo or args.screenshot)
    app = BatchToolkitApp(demo=demo)
    if demo:
        samples = Path(args.samples) if args.samples else base_dir() / "samples"
        if samples.is_dir():
            app.load_demo(samples)
        else:
            app.log(f"未找到示例文件夹：{samples}（请先运行 python make_samples.py）", "error")
    if args.screenshot:
        run_screenshot_mode(app, Path(args.screenshot))
    app.mainloop()


if __name__ == "__main__":
    main()
