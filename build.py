"""Build a single-file Windows executable with PyInstaller.

    python build.py            # -> dist/BatchToolkit.exe
    python build.py --clean    # remove build/ and dist/ first
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ICON = ROOT / "assets" / "icon.ico"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true", help="delete build/ and dist/ before building")
    args = parser.parse_args()

    if args.clean:
        for d in ("build", "dist"):
            shutil.rmtree(ROOT / d, ignore_errors=True)

    sys.path.insert(0, str(ROOT))
    from core.icon import save_ico

    ICON.parent.mkdir(exist_ok=True)
    save_ico(str(ICON))

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--onefile", "--windowed",
        "--name", "BatchToolkit",
        "--icon", str(ICON),
        "--collect-data", "sv_ttk",
        "--exclude-module", "pytest",
        # keep the exe small: these get pulled in only because they are installed globally
        "--exclude-module", "numpy",
        "--exclude-module", "lxml",
        "--exclude-module", "cryptography",
        "--exclude-module", "matplotlib",
        "--exclude-module", "pandas",
        "--exclude-module", "IPython",
        "--exclude-module", "PIL.ImageQt",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        str(ROOT / "app.py"),
    ]
    print(" ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    exe = ROOT / "dist" / "BatchToolkit.exe"
    if result.returncode == 0 and exe.exists():
        print(f"\nOK -> {exe}  ({exe.stat().st_size / 1024 / 1024:.1f} MB)")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
