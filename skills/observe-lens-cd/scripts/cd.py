#!/usr/bin/env python3
"""observe-lens cd: read-only Argo CD client."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ol_common as ol

AREA = "cd"
REQUIRED = {"argocd": ["url"]}


def ctx_for(area, name, inst, timeout=None):
    token = ol.resolve_secret(area, name, inst, "token")
    if not token:
        raise ol.OlError("argocd needs a 'token' secret — see "
                         "references/setup.md (argocd account generate-token)")
    return {
        "base": (inst.get("url") or "").rstrip("/"),
        "auth": ol.bearer(token),
        "timeout": timeout or inst.get("timeout") or 60,
        "ctx": ol.tls_context(inst),
    }


def api(ctx, path, params=None):
    url = "%s/%s" % (ctx["base"], path.lstrip("/"))
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return ol.get_json(url, headers={"Authorization": ctx["auth"]},
                       timeout=ctx["timeout"], context=ctx["ctx"])


def _rev(app):
    st = app.get("status") or {}
    sync = st.get("sync") or {}
    revs = sync.get("revisions") or ([sync.get("revision")]
                                     if sync.get("revision") else [])
    return ", ".join(r[:12] for r in revs if r)


def cd_get_allowed(path):
    """Raw get: only api/v1/ paths, never a refresh query parameter."""
    norm = ol.normalize_api_path(path)
    if norm is None:
        return False
    parts = urllib.parse.urlsplit(norm)
    if not parts.path.lstrip("/").startswith("api/v1/"):
        return False
    q = urllib.parse.parse_qs(parts.query)
    return not any(k.lower() == "refresh" for k in q)


def c_whoami(call):
    data = api(call.ctx, "/api/v1/session/userinfo")
    print("%s (%s)" % (data.get("username") or data.get("iss")
                       or json.dumps(data)[:80], call.ctx["base"]))


def c_apps(call):
    args, ctx = call.args, call.ctx
    params = {}
    if args.project:
        params["projects"] = args.project
    if args.selector:
        params["selector"] = args.selector
    data = api(ctx, "/api/v1/applications", params)
    flt = (args.filter or "").lower()
    rows = []
    for a in data.get("items") or []:
        md = a.get("metadata") or {}
        st = a.get("status") or {}
        name = md.get("name") or ""
        if flt and flt not in name.lower():
            continue
        rows.append((name, (st.get("sync") or {}).get("status"),
                     (st.get("health") or {}).get("status"), _rev(a)))
    for name, sync, health, rev in sorted(rows):
        print("%-30s %-10s %-12s %s" % (name, sync, health, rev))
    if not rows:
        print("(no applications)")
    if getattr(args, "json", False):
        print(json.dumps(data.get("items") or [], indent=2))


def _c_app_status(st, data):
    sync = st.get("sync") or {}
    health = st.get("health") or {}
    print("sync:      %s -> %s" % (sync.get("status"), _rev(data)))
    print("health:    %s %s" % (health.get("status"),
                               ol.redact(health.get("message") or "")))
    images = (st.get("summary") or {}).get("images") or []
    if images:
        print("images:    %s" % ", ".join(images))
    op = st.get("operationState") or {}
    if op:
        print("operation: %s %s -> %s %s"
              % (op.get("phase"), op.get("startedAt"),
                 op.get("finishedAt"),
                 ol.redact(op.get("message") or "")[:200]))
    for cond in st.get("conditions") or []:
        print("condition: %s %s" % (cond.get("type"),
                                  ol.redact(cond.get("message") or "")[:200]))
    for r in st.get("resources") or []:
        h = (r.get("health") or {}).get("status")
        s = r.get("status")
        if (h and h != "Healthy") or (s and s != "Synced"):
            print("resource:  %s/%s %s %s %s"
                  % (r.get("kind"), r.get("name"), r.get("namespace"),
                     s or "-", h or "-"))


def _c_app_detail(data):
    st = data.get("status") or {}
    spec = data.get("spec") or {}
    print("name:      %s" % (data.get("metadata") or {}).get("name"))
    print("project:   %s" % spec.get("project"))
    srcs = spec.get("sources") or ([spec["source"]]
                                   if spec.get("source") else [])
    for src in srcs:
        print("source:    %s path=%s target=%s"
              % (src.get("repoURL"), src.get("path") or src.get("chart"),
                 src.get("targetRevision")))
    _c_app_status(st, data)


def c_app(call):
    args, ctx = call.args, call.ctx
    params = {}
    if args.app_namespace:
        params["appNamespace"] = args.app_namespace
    data = api(ctx, "/api/v1/applications/%s"
               % urllib.parse.quote(args.name, safe=""), params)
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2))
        return
    _c_app_detail(data)


def c_history(call):
    args, ctx = call.args, call.ctx
    data = api(ctx, "/api/v1/applications/%s"
               % urllib.parse.quote(args.name, safe=""))
    hist = (data.get("status") or {}).get("history") or []
    for h in reversed(hist):
        print("%-4s %-14s start=%s deployed=%s" % (
            h.get("id"), (h.get("revision") or "")[:12],
            h.get("deployStartedAt"), h.get("deployedAt")))
    if not hist:
        print("(no history)")


def c_resources(call):
    args, ctx = call.args, call.ctx
    data = api(ctx, "/api/v1/applications/%s/resource-tree"
               % urllib.parse.quote(args.name, safe=""))
    nodes = data.get("nodes") or []
    shown = 0
    for n in nodes:
        h = (n.get("health") or {}).get("status")
        if args.unhealthy and h in (None, "Healthy"):
            continue
        shown += 1
        print("%-15s %-30s ns=%-15s health=%s %s" % (
            n.get("kind"), n.get("name"), n.get("namespace") or "-",
            h or "-", (n.get("health") or {}).get("message") or ""))
    if not shown:
        print("(no resources%s)" % (" unhealthy" if args.unhealthy
                                    else ""))


def c_events(call):
    args, ctx = call.args, call.ctx
    params = {"fieldSelector": "involvedObject.name=%s" % args.name}
    data = api(ctx, "/api/v1/applications/%s/events"
               % urllib.parse.quote(args.name, safe=""), params)
    items = data.get("items") or []
    for e in items[-30:]:
        print("%s %-8s %s" % (e.get("lastTimestamp")
                              or e.get("eventTime") or "",
                              e.get("type") or "",
                              ol.redact(e.get("message") or "")[:200]))
    if not items:
        print("(no events)")


def c_get(call):
    args, ctx = call.args, call.ctx
    p = args.path.lstrip("/")
    if p.startswith("http"):
        ol.die("get takes a path relative to the Argo CD base URL",
               ol.EX_USAGE)
    if not cd_get_allowed(p):
        ol.die("refused: get only allows api/v1/ paths without a "
               "'refresh' query parameter", ol.EX_GUARD)
    print(ol.redact(json.dumps(api(ctx, p), indent=2)))


_CD_CMDS = {"whoami": c_whoami, "apps": c_apps, "app": c_app,
            "history": c_history, "resources": c_resources,
            "events": c_events, "get": c_get}


def cmd(args, ctx):
    fn = _CD_CMDS.get(args.cmd)
    if not fn:
        ol.die("unknown subcommand '%s'" % args.cmd, ol.EX_USAGE)
    import types
    fn(types.SimpleNamespace(args=args, ctx=ctx))


def build_parser():
    gp = argparse.ArgumentParser(add_help=False)
    gp.add_argument("--instance", default=argparse.SUPPRESS)
    gp.add_argument("--prod", action="store_true",
                    default=argparse.SUPPRESS,
                    help="required for production instances (reads only; "
                         "guards still apply)")
    gp.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    ap = argparse.ArgumentParser(prog="cd.py", parents=[gp],
                                 description="read-only Argo CD client")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("whoami", parents=[gp])
    p = sub.add_parser("apps", parents=[gp])
    p.add_argument("--project")
    p.add_argument("--selector")
    p.add_argument("--filter")
    p = sub.add_parser("app", parents=[gp])
    p.add_argument("name")
    p.add_argument("--app-namespace")
    p = sub.add_parser("history", parents=[gp])
    p.add_argument("name")
    p = sub.add_parser("resources", parents=[gp])
    p.add_argument("name")
    p.add_argument("--unhealthy", action="store_true")
    p = sub.add_parser("events", parents=[gp])
    p.add_argument("name")
    p = sub.add_parser("get", parents=[gp])
    p.add_argument("path")
    return ap


def main():
    args = build_parser().parse_args()
    if not args.cmd:
        ol.die("no subcommand; see --help", ol.EX_USAGE)
    cfg = ol.load_config(AREA)
    name, inst = ol.pick_instance(AREA, cfg, getattr(args, "instance", None))
    ol.require_prod_flag(name, inst, args)
    ol.warn_http_creds(AREA, name, inst)
    if (inst.get("provider") or "").lower() != "argocd":
        ol.die("provider '%s' is not argocd; use kube_ro.py for "
               "kubectl/helm/flux" % inst.get("provider"))
    ctx = ctx_for(AREA, name, inst)
    cmd(args, ctx)


if __name__ == "__main__":
    rc = ol.dispatch_config(AREA, sys.argv[1:], required_fields=REQUIRED)
    if rc is not None:
        sys.exit(rc)
    try:
        main()
    except ol.OlError as e:
        ol.die(str(e), e.code)
    except KeyboardInterrupt:
        sys.exit(130)
