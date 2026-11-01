#!/usr/bin/env python3
"""Build tests/fixtures/guard_golden.json: record exact outputs of every
guard function on the existing test inputs plus seeded generated cases.
Run before and after refactors; tests/test_golden.py replays it."""
from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))


def load_module(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ol = load_module("ol_gold", "common/_ol_common.py")
pl = load_module("pl_gold",
                 "skills/observe-lens-pipelines/scripts/pipelines.py")
kr = load_module("kr_gold", "skills/observe-lens-cd/scripts/kube_ro.py")
cd = load_module("cd_gold", "skills/observe-lens-cd/scripts/cd.py")
tl = load_module("tl_gold",
                 "skills/observe-lens-telemetry/scripts/telemetry.py")
tu = load_module("tu_gold", "tests/test_units.py")


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------

def _collect_static():
    corpus = {"sql": [], "mongo": [], "decide": [], "allow": [],
              "redact": [], "normalize": [], "time": []}
    corpus["sql"] += tu.TestSqlGuard.ACCEPT + tu.TestSqlGuard.REFUSE
    corpus["mongo"] += tu.TestMongoGuard.ACCEPT + tu.TestMongoGuard.REFUSE
    corpus["redact"] += [c[0] for c in tu.TestRedact.CASES]
    corpus["redact"] += ["commit " + "3f2a9c1" * 5, "token_count=5",
                         "plain text with no secrets at all"]
    corpus["normalize"] += [
        "api/v1/x", "api/v1/../x", "job/a/%2e%2e/build",
        "%252e%252e/x", "a\\b", "x%00y", "api/v1/app?refresh=hard"]
    corpus["time"] += ["now", "now-15m", "now-2d", "2026-01-15T09:40:00Z",
                       "1768470000", "1768470000000", "bad", "12x"]
    return corpus


_SQL_TOKS = ["select", "*", "from", "t", "where", "id", "=", "'A-1'",
             ";", "-- c\n", "/* x */", "limit", "10", "with", "c",
             "as", "(", ")", "explain", "analyze", "show", "tables",
             "for", "update", "delete", "drop", "insert", "\\",
             "$tag$", "body $tag$", "/*!x*/", "values", "table",
             "describe", "pg_sleep", "(", "1", ")", "dblink", "into"]
_MONGO_TOKS = ["db", ".", "orders", "find", "(", ")", "{", "}", ":",
               "1", "'a'", "limit", "5", "sort", "aggregate", "[",
               "]"  , "$match", "ObjectId", "ISODate", "$out", "eval",
               "deleteMany", "forEach", ";", "`", "=>", "[", "x",
               "stats", "getCollection", "getIndexes", "new", "Date"]
_KUBE_TOKS = {
    "kubectl": ["get", "pods", "-n", "ns", "secrets", "-w", "--watch",
                "logs", "-f", "--follow", "--server", "https://x",
                "-s", "-v=9", "-v", "9", "--v=6", "--context", "x",
                "delete", "exec", "--raw", "rollout", "status",
                "history", "describe", "secret/x", "all,secrets",
                "--token", "t", "--as", "u", "top", "api-resources",
                "version", "auth", "can-i", "config", "get-contexts",
                "view", "--as-group", "g", "-l", "app=x"],
    "helm": ["list", "-A", "status", "r", "-o", "json", "yaml",
             "table", "--output=yaml", "history", "get", "values",
             "manifest", "notes", "metadata", "all", "hooks", "show",
             "chart", "readme", "crds", "values", "template",
             "install", "upgrade", "uninstall", "rollback",
             "--kube-context", "x", "--kube-token", "t", "-n", "ns",
             "--kube-apiserver", "s", "version", "-w", "--watch"],
    "flux": ["get", "kustomizations", "-A", "tree", "stats", "logs",
             "version", "check", "events", "trace", "export",
             "reconcile", "suspend", "resume", "create", "delete",
             "--server", "s", "--token", "t", "--as", "u", "-f",
             "--follow", "-w", "--watch", "ks", "x", "--as-group",
             "g", "--as-uid", "u", "--insecure-skip-tls-verify"],
}
_ALLOW_PATHS = ["api/v1/applications", "api/v1/applications/x?refresh=y",
                "api/v1/app?Refresh=hard", "api/v1/app?refresh%3Dhard",
                "job/x/api/json", "job/x/1/consoleText", "job/a/build/x",
                "job/a/%2e%2e/x", "api/v1/../x", "api/admin",
                "api/datasources/proxy/uid/a/loki/api/v1/q",
                "api/v1/admin/tsdb/snapshot", "loki/api/v1/query",
                "flush", "shutdown", "api/traces/t", "ready",
                "x%00y", "api/v2/a", "job/x/1/artifact/z.zip",
                "job/x/12/wfapi/describe", "scriptText", "wfapi/x"]
_TOK_PARTS = ["gh" + "p_", "A" * 30, "gl" + "sa_", "b" * 22,
              "xox" + "b-", "tok-" * 6, "AKIA", "Z" * 16,
              "eyJ" + "x" * 12, ".", "y" * 12, "Bearer ", "Basic ",
              "password=", "token:", "-----BEGIN ", "PRIVATE KEY-----",
              "-----END ", "://u:p@", "MII", "text", " ", ";"]
_NORM_PARTS = ["api/", "v1/", "..", "%2e%2e", "%252e%252e", "a", "x",
               "?refresh=1", "%00", "\\", "%3F", "job/", "build",
               "%2f", "api%2f", "//", "%252f", "?x=1", "&y=2"]


def _collect_sql(rng, corpus, n=200):
    for _ in range(n):
        corpus["sql"].append(" ".join(rng.choice(_SQL_TOKS)
                                      for _ in range(rng.randint(1, 14))))


def _collect_mongo(rng, corpus, n=200):
    for _ in range(n):
        corpus["mongo"].append("".join(rng.choice(_MONGO_TOKS)
                                       for _ in range(rng.randint(2, 18))))


def _collect_decide(rng, corpus, n=80):
    for tool, toks in _KUBE_TOKS.items():
        for _ in range(n):
            corpus["decide"].append(
                {"tool": tool,
                 "args": [rng.choice(toks)
                          for _ in range(rng.randint(1, 8))]})


def _collect_allow(corpus):
    for prov in ("grafana", "prometheus", "loki", "tempo"):
        for p in _ALLOW_PATHS:
            corpus["allow"].append({"f": "telemetry", "args": [prov, p]})
    for p in _ALLOW_PATHS:
        corpus["allow"].append({"f": "jenkins", "args": [p]})
        corpus["allow"].append({"f": "cd", "args": [p]})


def _collect_redact(rng, corpus, n=200):
    for _ in range(n):
        corpus["redact"].append("".join(rng.choice(_TOK_PARTS)
                                        for _ in range(rng.randint(1, 8))))


def _collect_normalize(rng, corpus, n=200):
    for _ in range(n):
        corpus["normalize"].append(
            "".join(rng.choice(_NORM_PARTS)
                    for _ in range(rng.randint(1, 9))))


def collect():
    corpus = _collect_static()
    rng = random.Random(20260115)
    _collect_sql(rng, corpus)
    _collect_mongo(rng, corpus)
    _collect_decide(rng, corpus)
    _collect_allow(corpus)
    _collect_redact(rng, corpus)
    _collect_normalize(rng, corpus)
    return corpus


# ---------------------------------------------------------------------------
# recording
# ---------------------------------------------------------------------------

def rec(fn, *args):
    try:
        return {"v": fn(*args)}
    except Exception as e:  # noqa: BLE001 - record every outcome
        return {"e": "%s: %s" % (type(e).__name__, e)}


def main():
    corpus = collect()
    # freeze 'now' for parse_time
    from datetime import datetime as _dt, timezone as _tz

    class FrozenDT(_dt):
        @classmethod
        def now(cls, tz=None):
            base = _dt(2026, 1, 15, 9, 40, 0, tzinfo=_tz.utc)
            return base.astimezone(tz) if tz else base
    ol.datetime = FrozenDT

    out = []
    for s in corpus["sql"]:
        for d in ("postgres", "mysql"):
            out.append(["check_sql", [s, d], rec(ol.check_sql, s, d)])
            out.append(["sql_add_limit", [s, 100, d],
                        rec(ol.sql_add_limit, s, 100, d)])
            out.append(["_sql_strip", [s, d], rec(ol._sql_strip, s, d)])
    for e in corpus["mongo"]:
        out.append(["check_mongo", [e], rec(ol.check_mongo, e)])
        out.append(["mongo_add_limit", [e, 100],
                    rec(ol.mongo_add_limit, e, 100)])
    for t in corpus["redact"]:
        out.append(["redact", [t], rec(ol.redact, t)])
    for p in corpus["normalize"]:
        out.append(["normalize_api_path", [p],
                    rec(ol.normalize_api_path, p)])
    for d in corpus["decide"]:
        out.append(["kube_decide", [d["tool"], d["args"]],
                    rec(kr.decide, d["tool"], d["args"])])
    for a in corpus["allow"]:
        fn = {"jenkins": pl.jenkins_get_allowed,
              "cd": cd.cd_get_allowed,
              "telemetry": tl.telemetry_get_allowed}[a["f"]]
        out.append(["%s_allowed" % a["f"], a["args"], rec(fn, *a["args"])])
    for t in corpus["time"]:
        out.append(["parse_time", [t], rec(ol.parse_time, t)])

    dst = ROOT / "tests" / "fixtures" / "guard_golden.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    import base64 as _b64

    def enc(v):
        return _b64.b64encode(json.dumps(v).encode("utf-8")
                              ).decode("ascii")

    encoded = [{"fn": name, "in_b64": [enc(a) for a in args],
                "out_b64": enc(result)} for name, args, result in out]
    dst.write_text(json.dumps(encoded, indent=0) + "\n", encoding="utf-8")
    counts = {}
    for name, _, _ in out:
        counts[name] = counts.get(name, 0) + 1
    print("wrote %s: %d cases (%d bytes)" % (dst, len(out),
                                             dst.stat().st_size))
    for k in sorted(counts):
        print("  %-22s %d" % (k, counts[k]))


if __name__ == "__main__":
    main()
