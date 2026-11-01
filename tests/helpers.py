"""Shared test helpers: load scripts as modules by path."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_module(name, relpath):
    path = ROOT / relpath
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def common():
    return load_module("_ol_common_test", "common/_ol_common.py")


def pipelines():
    common()  # ensure _ol_common importable dir is on path handled inside
    return load_module("pipelines_mod",
                       "skills/observe-lens-pipelines/scripts/pipelines.py")


def kube_ro():
    common()
    return load_module("kube_ro_mod",
                       "skills/observe-lens-cd/scripts/kube_ro.py")


def telemetry():
    common()
    return load_module("telemetry_mod",
                       "skills/observe-lens-telemetry/scripts/telemetry.py")


def test_report():
    return load_module("test_report_mod",
                       "skills/observe-lens-pipelines/scripts/test_report.py")


def cd():
    common()
    return load_module("cd_mod", "skills/observe-lens-cd/scripts/cd.py")


def db():
    common()
    return load_module("db_mod", "skills/observe-lens-db/scripts/db.py")
