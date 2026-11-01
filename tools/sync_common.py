#!/usr/bin/env python3
"""Copy common/_ol_common.py into each area skill's scripts/ folder and the
area assets into examples/config/. --check verifies without writing."""
from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMMON = ROOT / "common" / "_ol_common.py"
AREAS = ("pipelines", "cd", "db", "telemetry")
ASSET_NAMES = ("config.example.json", "secrets.example.json",
               "knowledge-template.md")


def _sync(src, dst, check, problems):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if check:
        if not dst.exists() or not filecmp.cmp(src, dst, shallow=False):
            problems.append("drift: %s != %s" % (src, dst))
    else:
        shutil.copyfile(src, dst)
        print("copied %s -> %s" % (src, dst))


def main():
    check = "--check" in sys.argv
    problems = []
    for area in AREAS:
        skill = ROOT / "skills" / ("observe-lens-%s" % area)
        _sync(COMMON, skill / "scripts" / "_ol_common.py", check, problems)
        for name in ASSET_NAMES:
            src = skill / "assets" / name
            if src.exists():
                _sync(src, ROOT / "examples" / "config" / area / name,
                      check, problems)
    if problems:
        for p in problems:
            print(p)
        sys.exit(1)
    if check:
        print("all common copies and example configs in sync")


if __name__ == "__main__":
    main()
