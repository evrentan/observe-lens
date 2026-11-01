#!/usr/bin/env python3
"""Summarise a saved CI log file: first errors, failed tests, build-tool
failure markers, exit codes and stack-trace heads, with line numbers."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ol_common as _ol

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

PATTERNS = [
    ("error", re.compile(r"\b(ERROR|FATAL|SEVERE)\b", re.I)),
    ("failed-test", re.compile(
        r"(FAILED|Failures?:\s*[1-9]|Tests run:.*Failures:\s*[1-9]|"
        r"\bFAIL\b.*\btest|\b\d+ failed\b)", re.I)),
    ("build-fail", re.compile(
        r"(BUILD FAILURE|npm ERR!|Gradle build daemon disappeared|"
        r"Compilation failure|error TS\d+|CS\d+: |panic:|"
        r"FAILURE: Build failed|Error: Process completed with exit code)")),
    ("exit-code", re.compile(
        r"(exit code [1-9]\d*|exit status [1-9]\d*|Exit Code: [1-9]\d*)",
        re.I)),
    ("stack", re.compile(
        r"(Traceback \(most recent call last\)|Exception in thread|"
        r"^\s*at [\w.$]+\([\w.:]+\)|Caused by:|panic: runtime error)")),
]


def _collect_hits(lines, pats):
    hits = {name: [] for name, _ in pats}
    for i, line in enumerate(lines, 1):
        for name, rx in pats:
            if rx.search(line):
                hits[name].append((i, line.rstrip()))
    return hits


def _print_group(name, lst, lines, args, shown):
    total = 0
    print("== %s (%d hits) ==" % (name, len(lst)))
    for i, line in lst[: args.max_hits]:
        if i in shown:
            continue
        shown.add(i)
        total += 1
        if total > args.max_hits:
            print("  ... (%d more)" % (len(lst) - args.max_hits))
            break
        if args.context:
            lo = max(1, i - args.context)
            hi = min(len(lines), i + args.context)
            for j in range(lo, hi + 1):
                mark = ">" if j == i else " "
                print("%s %6d| %s" % (mark, j, lines[j - 1].rstrip()))
        else:
            print("  %6d| %s" % (i, line.rstrip()[:300]))
    print()


def main():
    ap = argparse.ArgumentParser(prog="log_summary.py")
    ap.add_argument("file")
    ap.add_argument("--context", type=int, default=0)
    ap.add_argument("--grep", action="append", default=[],
                    help="extra regex to search for (repeatable)")
    ap.add_argument("--max-hits", type=int, default=40)
    args = ap.parse_args()
    path = Path(args.file)
    if not path.exists():
        print("error: no such file %s" % path, file=sys.stderr)
        sys.exit(2)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    lines = [_ol.redact(l) for l in lines]
    pats = PATTERNS + [("grep", re.compile(g, re.I)) for g in args.grep]
    hits = _collect_hits(lines, pats)
    shown = set()
    for name, _ in pats:
        if hits[name]:
            _print_group(name, hits[name], lines, args, shown)
    if not any(hits.values()):
        print("no error patterns matched (%d lines scanned)" % len(lines))
    else:
        print("%d lines scanned" % len(lines))


if __name__ == "__main__":
    main()
