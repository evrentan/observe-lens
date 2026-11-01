#!/usr/bin/env python3
"""Summarise JUnit XML, Allure result or Allure report test data from a file,
folder or zip: totals plus failed/broken tests with message and first frame."""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ol_common as _ol

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


def _zip_members(z):
    """Return (data, skipped) honouring the 50MB member / 200MB total caps."""
    data = {}
    total = 0
    skipped = []
    for info in z.infolist():
        if info.filename.endswith("/"):
            continue
        over_member = info.file_size > 50 * 1024 * 1024
        over_total = total + info.file_size > 200 * 1024 * 1024
        if over_member or over_total:
            skipped.append((info.filename, info.file_size))
            continue
        data[info.filename] = z.read(info.filename)
        total += info.file_size
    return data, skipped


def _iter_files(path):
    """Yield (name, reader_callable) for file/folder/zip inputs."""
    p = Path(path)
    if p.is_dir():
        for f in sorted(p.rglob("*")):
            if f.is_file():
                yield str(f), lambda f=f: f.read_bytes()
    elif zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as z:
            data, skipped = _zip_members(z)
        for n, size in skipped:
            print("note: skipped %s (%d bytes; zip member/total cap)"
                  % (n, size), file=sys.stderr)
        for n in data:
            yield n, lambda n=n, d=data: d[n]
    elif p.is_file():
        yield str(p), lambda: p.read_bytes()
    else:
        print("error: no such file or folder %s" % path, file=sys.stderr)
        sys.exit(2)


def _junit_case(tc):
    res = None
    for tag in ("failure", "error"):
        for el in tc.findall(tag):
            res = (tag, el.get("message") or "", (el.text or ""))
    skipped = tc.find("skipped")
    if res:
        status = res[0]
    elif skipped is not None:
        status = "skipped"
    else:
        status = "passed"
    return {
        "name": _ol.redact("%s.%s" % (tc.get("classname") or "",
                                      tc.get("name"))),
        "status": status,
        "message": _ol.redact(res[1] if res else (
            skipped.get("message") or "" if skipped is not None else "")),
        "detail": _ol.redact(res[2] if res else ""),
    }


def _parse_junit(data):
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    return [_junit_case(tc) for tc in root.iter("testcase")]


def _allure_first_frame(trace):
    for line in trace.splitlines():
        if line.strip().startswith("at ") or "File " in line:
            return line.strip()
    return ""


def _allure_failed_step(obj):
    def walk(s):
        for st in s.get("steps") or []:
            yield st
            yield from walk(st)
    return next((s.get("name") for s in walk(obj)
                 if s.get("status") == "failed"), None)


def _parse_allure_result(data):
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict) or "status" not in obj:
        return None
    status = (obj.get("status") or "").lower()
    detail = obj.get("statusDetails") or {}
    first_frame = _allure_first_frame(detail.get("trace") or "")
    failed_step = _allure_failed_step(obj)
    return {
        "name": _ol.redact(obj.get("fullName") or obj.get("name") or "?"),
        "status": status,
        "message": _ol.redact(detail.get("message") or ""),
        "detail": _ol.redact(first_frame or (failed_step or "")),
    }


def summarize(path):
    tests = []
    for name, read in _iter_files(path):
        lname = name.lower()
        try:
            data = read()
        except Exception:
            continue
        if lname.endswith(".xml"):
            tests.extend(_parse_junit(data))
        elif lname.endswith("-result.json") or (
                "/data/test-cases/" in lname.replace("\\", "/")
                and lname.endswith(".json")):
            r = _parse_allure_result(data)
            if r:
                tests.append(r)
    return tests


def main():
    ap = argparse.ArgumentParser(prog="test_report.py")
    ap.add_argument("path", help="file, folder or zip")
    ap.add_argument("--all", action="store_true",
                    help="list every test, not only failures")
    args = ap.parse_args()
    tests = summarize(args.path)
    if not tests:
        print("no test results found in %s" % args.path)
        return
    total = len(tests)
    by = {}
    for t in tests:
        by[t["status"]] = by.get(t["status"], 0) + 1
    parts = ", ".join("%s=%d" % (k, v) for k, v in sorted(by.items()))
    print("%d tests: %s" % (total, parts))
    bad = [t for t in tests if t["status"] not in ("passed", "skipped")]
    if args.all:
        bad = tests
    for t in bad[:50]:
        print("\n%s [%s]" % (t["name"], t["status"]))
        if t.get("message"):
            print("  %s" % str(t["message"]).splitlines()[0][:300])
        if t.get("detail"):
            print("  %s" % str(t["detail"]).splitlines()[0][:300])
    if len(bad) > 50:
        print("\n... and %d more" % (len(bad) - 50))


if __name__ == "__main__":
    main()
