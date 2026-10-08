#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Package blender/viewar into dist/viewar-<version>.zip, ready for
Edit > Preferences > Get Extensions > Install from Disk.

    python3 build.py          build the zip
    python3 build.py --hook   same, but reads a Claude Code hook payload on
                              stdin and only builds if the edited file is
                              inside blender/viewar/
"""

import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "blender" / "viewar"
DIST = ROOT / "dist"


def version():
    manifest = (SOURCE / "blender_manifest.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', manifest, re.MULTILINE)
    if match is None:
        sys.exit("build.py: no version in blender_manifest.toml")
    return match.group(1)


def included(path):
    rel = path.relative_to(SOURCE)
    return path.is_file() and not any(
        part.startswith(".") or part == "__pycache__" for part in rel.parts
    ) and path.suffix != ".pyc"


def build():
    DIST.mkdir(exist_ok=True)
    for old in DIST.glob("viewar-*.zip"):
        old.unlink()
    target = DIST / f"viewar-{version()}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(p for p in SOURCE.rglob("*") if included(p)):
            zf.write(path, path.relative_to(SOURCE).as_posix())
    return target


def edited_addon_file():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return False
    file_path = (payload.get("tool_input") or {}).get("file_path") or ""
    try:
        Path(file_path).resolve().relative_to(SOURCE)
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    if "--hook" in sys.argv and not edited_addon_file():
        sys.exit(0)
    print(f"Built {build().relative_to(ROOT)}")
