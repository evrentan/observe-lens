#!/usr/bin/env python3
"""observe-lens telemetry: read-only client for Grafana (Loki/Prometheus/
Tempo/alerts through the datasource proxy) and direct prometheus/loki/tempo."""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ol_common as ol

AREA = "telemetry"
REQUIRED = {"grafana": ["url"], "prometheus": ["url"], "loki": ["url"],
            "tempo": ["url"]}

DS_TYPE_FOR = {"logs": "loki", "metrics": "prometheus", "traces": "tempo"}


def make_ctx(area, name, inst):
    prov = (inst.get("provider") or "").lower()
    headers = {}
    token = ol.resolve_secret(area, name, inst, "token")
    user = ol.resolve_secret(area, name, inst, "username")
    pw = ol.resolve_secret(area, name, inst, "password")
    if token:
        headers["Authorization"] = ol.bearer(token)
    elif user and pw:
        headers["Authorization"] = ol.basic_auth(user, pw)
    if prov == "grafana" and inst.get("org_id"):
        headers["X-Grafana-Org-Id"] = str(inst["org_id"])
    if prov != "grafana" and inst.get("tenant"):
        headers["X-Scope-OrgID"] = str(inst["tenant"])
    return {
        "prov": prov, "inst": inst,
        "base": (inst.get("url") or "").rstrip("/"),
        "headers": headers,
        "timeout": inst.get("timeout") or 60,
        "ctx": ol.tls_context(inst),
    }


def api(ctx, path, params=None, raw=False):
    url = path if str(path).startswith("http") else (
        "%s/%s" % (ctx["base"], str(path).lstrip("/")))
    if params:
        sep = "&" if "?" in url else "?"
        url += sep + urllib.parse.urlencode(params)
    if raw:
        _, body, _ = ol.http_get(url, headers=ctx["headers"],
                                 timeout=ctx["timeout"], context=ctx["ctx"])
        return body
    return ol.get_json(url, headers=ctx["headers"], timeout=ctx["timeout"],
                       context=ctx["ctx"])


# ---------------------------------------------------------------------------
# datasource selection (grafana proxy)
# ---------------------------------------------------------------------------

def ds_uid(ctx, signal, explicit=None):
    if explicit:
        return explicit, "given"
    inst = ctx["inst"]
    uids = inst.get("datasources") or {}
    if uids.get(signal):
        return uids[signal], "config"
    want = DS_TYPE_FOR[signal]
    try:
        dss = api(ctx, "/api/datasources")
    except ol.OlError:
        raise ol.OlError(
            "no datasource configured for '%s' and /api/datasources failed; "
            "set datasources.%s in the instance config" % (signal, signal))
    cands = [d for d in dss if d.get("type") == want]
    if not cands:
        raise ol.OlError("no %s datasource found in Grafana; set "
                         "datasources.%s" % (want, signal))
    pick = next((d for d in cands if d.get("isDefault")), cands[0])
    print("datasource auto-picked: %s (%s uid=%s)"
          % (pick.get("name"), want, pick.get("uid")), file=sys.stderr)
    return pick.get("uid"), "auto"


def ds_path(ctx, signal, path, ds=None):
    """Wrap a datasource API path in the Grafana proxy when needed."""
    if ctx["prov"] != "grafana":
        return path
    uid, _ = ds_uid(ctx, signal, ds)
    return "/api/datasources/proxy/uid/%s/%s" % (uid, path.lstrip("/"))


def sig_prefix(ctx, signal):
    if ctx["prov"] == "grafana":
        return {"logs": "loki/api/v1", "metrics": "api/v1",
                "traces": "api"}[signal]
    return {"loki": "loki/api/v1", "prometheus": "api/v1",
            "tempo": "api"}.get(ctx["prov"], "api/v1")


def sig_api(ctx, signal, path, params=None, ds=None):
    return api(ctx, ds_path(ctx, signal, "%s/%s"
                            % (sig_prefix(ctx, signal), path.lstrip("/")),
                            ds), params)


def _ns(ts):
    return int(ts) / 1e9


def _unwrap_line(line):
    """Unwrap an obvious JSON log line to its message field."""
    s = line.strip()
    if not s.startswith("{"):
        return line
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return line
    for k in ("message", "msg", "log", "error", "err"):
        if isinstance(obj.get(k), str):
            rest = {k2: v for k2, v in obj.items()
                    if k2 not in ("message", "msg", "log", "level", "time",
                                  "timestamp", "ts")}
            extra = " ".join("%s=%s" % (k2, v) for k2, v in rest.items()
                             if isinstance(v, (str, int, float)))
            return "%s%s" % (obj[k], (" | " + extra) if extra else "")
    return line


def _label_summary(labels):
    parts = []
    for k in ("namespace", "app", "pod", "container", "service",
              "service_name", "job", "level"):
        if k in labels:
            parts.append(str(labels[k]))
    return "/".join(parts) or ",".join(
        "%s=%s" % kv for kv in sorted(labels.items())[:3])


# ---------------------------------------------------------------------------
# spans / trace tree
# ---------------------------------------------------------------------------

def _b64(s):
    try:
        pad = "=" * (-len(s) % 4)
        return base64.b64decode(s + pad).hex()
    except Exception:
        return s


def _span_id(sid):
    if not sid:
        return ""
    s = str(sid)
    if all(c in "0123456789abcdefABCDEF" for c in s) and len(s) in (8, 16):
        return s.lower()
    return _b64(s)


def _attr_map(attrs):
    out = {}
    for a in attrs or []:
        v = a.get("value") or {}
        val = (v.get("stringValue") if v.get("stringValue") is not None else
               v.get("intValue") if v.get("intValue") is not None else
               v.get("doubleValue") if v.get("doubleValue") is not None else
               v.get("boolValue"))
        out[a.get("key")] = val
    return out


def _otlp_span(sp, svc):
    attrs = _attr_map(sp.get("attributes"))
    st = (sp.get("status") or {}).get("code")
    code = st if isinstance(st, int) else (
        {"STATUS_CODE_ERROR": 2, "STATUS_CODE_OK": 1}.get(st, 0))
    return {
        "id": _span_id(sp.get("spanId") or sp.get("spanID")),
        "parent": _span_id(sp.get("parentSpanId")
                           or sp.get("parentSpanID")),
        "name": sp.get("name") or "",
        "service": svc,
        "start": int(sp.get("startTimeUnixNano") or 0),
        "end": int(sp.get("endTimeUnixNano") or 0),
        "status": code,
        "attrs": attrs,
    }


def _resource_service(rs):
    res = rs.get("resource") or {}
    for a in res.get("attributes") or []:
        if a.get("key") == "service.name":
            return (a.get("value") or {}).get("stringValue") or ""
    return ""


def flatten_spans(trace_json):
    """Return list of span dicts: {id, parent, name, service, start_ns,
    end_ns, status, attrs}. Handles Tempo 'batches' and OTLP 'resourceSpans'."""
    spans = []
    groups = trace_json.get("batches") or trace_json.get("resourceSpans") or []
    for rs in groups:
        svc = _resource_service(rs)
        for ss in rs.get("scopeSpans") or rs.get("instrumentationLibrarySpans") or []:
            for sp in ss.get("spans") or []:
                spans.append(_otlp_span(sp, svc))
    return spans


def _span_line(s, depth):
    dur_ms = (s["end"] - s["start"]) / 1e6
    err = s["status"] == 2
    interesting = []
    for k in ("http.status_code", "http.response.status_code",
              "exception.message", "db.statement", "error"):
        if k in s["attrs"] and s["attrs"][k] not in (None, ""):
            val = ol.redact(str(s["attrs"][k]))
            interesting.append("%s=%s" % (
                k, val[:120] + "…" if len(val) > 120 else val))
    return "%s%s | %s | %.1fms%s%s" % (
        "  " * depth, s["service"] or "?", s["name"], dur_ms,
        " ERROR" if err else "",
        " | " + "; ".join(interesting) if interesting else "")


def _span_forest(spans):
    by_parent = {}
    roots = []
    ids = {s["id"] for s in spans}
    for s in spans:
        if s["parent"] and s["parent"] in ids:
            by_parent.setdefault(s["parent"], []).append(s)
        else:
            roots.append(s)
    for v in by_parent.values():
        v.sort(key=lambda s: s["start"])
    roots.sort(key=lambda s: s["start"])
    return roots, by_parent


def render_trace_tree(spans, errors_only=False, max_spans=200):
    """Indented span tree lines."""
    roots, by_parent = _span_forest(spans)
    lines = []
    count = 0

    def emit(s, depth):
        nonlocal count
        if count >= max_spans:
            return
        count += 1
        err = s["status"] == 2
        if not (errors_only and not err):
            lines.append(_span_line(s, depth))
        for ch in by_parent.get(s["id"], []):
            emit(ch, depth + 1)

    for r in roots:
        emit(r, 0)
    if len(spans) > count:
        lines.append("... (%d more spans, raise --max-spans)"
                     % (len(spans) - count))
    return lines


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------

def cmd_whoami(ctx):
    if ctx["prov"] == "grafana":
        try:
            api(ctx, "/api/health")
        except ol.OlError:
            pass
        try:
            u = api(ctx, "/api/user")
            print("%s (%s, org %s) on %s" % (
                u.get("login") or u.get("name"), u.get("email") or "-",
                u.get("orgId"), ctx["base"]))
        except ol.OlError:
            org = api(ctx, "/api/org")
            print("org '%s' on %s (service account token)"
                  % (org.get("name"), ctx["base"]))
        return
    ep = {"prometheus": "api/v1/status/buildinfo",
          "loki": "loki/api/v1/status/buildinfo",
          "tempo": "api/status/buildinfo"}
    try:
        data = api(ctx, ep.get(ctx["prov"], "api/v1/status/buildinfo"))
        print(json.dumps(data, indent=2)[:400])
    except ol.OlError:
        data = api(ctx, "ready" if ctx["prov"] == "tempo"
                   else "-/ready", raw=True)
        print(data.decode("utf-8", "replace")[:200])


def cmd_datasources(ctx):
    if ctx["prov"] != "grafana":
        print("provider '%s' queries its own API directly (no datasources)"
              % ctx["prov"])
        return
    for d in api(ctx, "/api/datasources"):
        print("%-10s %-12s %-20s %s%s" % (
            d.get("uid"), d.get("type"), d.get("name"), d.get("url"),
            " (default)" if d.get("isDefault") else ""))


def cmd_dashboards(ctx, args):
    if ctx["prov"] != "grafana":
        ol.die("dashboards needs a grafana provider instance", ol.EX_USAGE)
    data = api(ctx, "/api/search", {"query": args.text, "type": "dash-db"})
    for d in data:
        print("%s  %s%s" % (d.get("title"), ctx["base"], d.get("url")))
    if not data:
        print("(no dashboards matched)")


def cmd_logs(ctx, args):
    frm, to = _window(args)
    params = {"query": args.query, "limit": args.limit,
              "direction": args.direction}
    if frm:
        params["start"] = int(frm * 1e9)
    if to:
        params["end"] = int(to * 1e9)
    data = sig_api(ctx, "logs", "query_range", params, ds=args.ds)
    results = (data.get("data") or {}).get("result") or []
    n = 0
    for stream in results:
        labels = stream.get("stream") or {}
        who = _label_summary(labels)
        for ts, line in stream.get("values") or []:
            n += 1
            text = ol.redact(_unwrap_line(line))
            if not args.full and len(text) > 500:
                text = text[:500] + "…"
            print("%s | %s | %s" % (ol.fmt_ts(_ns(ts)), who, text))
    print("-- %d lines, %d streams (%s → %s)"
          % (n, len(results), ol.fmt_ts(frm) if frm else "?",
             ol.fmt_ts(to) if to else "now"))


def cmd_labels(ctx, args):
    frm = ol.parse_time("now-24h")
    to = ol.parse_time("now")
    params = {"start": int(frm * 1e9), "end": int(to * 1e9)}
    if args.name:
        data = sig_api(ctx, "logs", "label/%s/values" % args.name, params,
                       ds=args.ds)
    else:
        data = sig_api(ctx, "logs", "labels", params, ds=args.ds)
    for v in data.get("data") or []:
        print(v)


def cmd_metric(ctx, args):
    params = {"query": args.query}
    if getattr(args, "at", None):
        params["time"] = "%.3f" % ol.parse_time(args.at)
    data = sig_api(ctx, "metrics", "query", params, ds=args.ds)
    for r in (data.get("data") or {}).get("result") or []:
        ts, val = r.get("value") or (None, None)
        print("%s | %s | %s" % (
            ol.fmt_ts(float(ts)) if ts else "?",
            json.dumps(r.get("metric")), val))


def cmd_metric_range(ctx, args):
    params = {"query": args.query, "start": "%.3f" % ol.parse_time(getattr(args, "from")),
              "end": "%.3f" % ol.parse_time(args.to), "step": args.step}
    data = sig_api(ctx, "metrics", "query_range", params, ds=args.ds)
    for r in (data.get("data") or {}).get("result") or []:
        vals = [(float(t), float(v)) for t, v in (r.get("values") or [])]
        if not vals:
            continue
        mn = min(vals, key=lambda x: x[1])
        mx = max(vals, key=lambda x: x[1])
        print(json.dumps(r.get("metric")))
        print("  n=%d min=%g (%s) max=%g (%s) first=%g last=%g"
              % (len(vals), mn[1], ol.fmt_ts(mn[0]), mx[1],
                 ol.fmt_ts(mx[0]), vals[0][1], vals[-1][1]))


def cmd_traces(ctx, args):
    frm, to = _window(args)
    frm = frm or None
    to = to or None
    params = {"q": args.query, "limit": args.limit}
    if frm:
        params["start"] = int(frm)
    if to:
        params["end"] = int(to)
    data = sig_api(ctx, "traces", "search", params, ds=args.ds)
    traces = data.get("traces") or []
    for t in traces:
        print("%s | %s | %s | %s | %sms" % (
            t.get("traceID"), t.get("rootServiceName"),
            t.get("rootTraceName"),
            ol.fmt_ts(ol.iso_to_epoch(t.get("startTimeUnixNano") and
                                      int(t["startTimeUnixNano"]) / 1e9 or 0)
                      or (int(t.get("startTimeUnixNano") or 0) / 1e9)),
            t.get("durationMs")))
    if not traces:
        print("(no traces)")


def cmd_trace(ctx, args):
    data = sig_api(ctx, "traces", "traces/%s" % args.trace_id, ds=args.ds)
    spans = flatten_spans(data)
    if not spans:
        print("(trace has no spans — may be expired by retention)")
        return
    for line in render_trace_tree(spans, errors_only=args.errors_only,
                                  max_spans=args.max_spans):
        print(line)
    print("-- %d spans, %d error" % (
        len(spans), sum(1 for s in spans if s["status"] == 2)))


def _alerts_grafana(ctx):
    rows = []
    try:
        data = api(ctx, "/api/prometheus/grafana/api/v1/alerts")
        for al in (data.get("data") or {}).get("alerts") or []:
            rows.append((al.get("state"),
                         (al.get("labels") or {}).get("alertname"),
                         (al.get("annotations") or {}).get("summary")
                         or "", al.get("activeAt") or ""))
    except ol.OlError:
        pass
    try:
        am = api(ctx, "/api/alertmanager/grafana/api/v2/alerts")
        for al in am or []:
            labels = al.get("labels") or {}
            rows.append(((al.get("status") or {}).get("state"),
                         labels.get("alertname"),
                         (al.get("annotations") or {}).get("summary")
                         or "", al.get("startsAt") or ""))
    except ol.OlError:
        pass
    return rows


def _alerts_direct(ctx, args):
    data = api(ctx, ds_path(ctx, "metrics", "api/v1/alerts", args.ds))
    rows = []
    for al in (data.get("data") or {}).get("alerts") or []:
        rows.append((al.get("state"),
                     (al.get("labels") or {}).get("alertname"),
                     (al.get("annotations") or {}).get("summary") or "",
                     al.get("activeAt") or ""))
    return rows


def cmd_alerts(ctx, args):
    state = args.state or "firing"
    rows = (_alerts_grafana(ctx) if ctx["prov"] == "grafana"
            else _alerts_direct(ctx, args))
    seen = set()
    out = []
    for r in rows:
        key = (r[0], r[1])
        if key in seen:
            continue
        seen.add(key)
        if state != "all" and (r[0] or "").lower() != state:
            continue
        out.append(r)
    for st, nm, summary, at in sorted(out, key=lambda r: (r[0] or "",
                                                         r[1] or "")):
        print("%-8s %-30s %s %s" % (st, nm, ol.redact(summary)[:80], at))
    if not out:
        print("(no alerts%s)" % ("" if state == "all"
                                 else " in state %s" % state))


def cmd_link(ctx, args):
    if ctx["prov"] != "grafana":
        ol.die("link needs a grafana provider instance", ol.EX_USAGE)
    signal = {"logs": "logs", "metrics": "metrics",
              "traces": "traces"}[args.signal]
    uid, _ = ds_uid(ctx, signal, args.ds)
    frm = ol.parse_time(getattr(args, "from", None))
    to = ol.parse_time(getattr(args, "to", None))
    panes = {"a": {"datasource": uid, "queries": [
        {"refId": "A", "expr": args.query,
         "queryType": "range", "datasource": {"type": DS_TYPE_FOR[signal],
                                            "uid": uid}}],
        "range": {"from": str(int(frm)) if frm else "now-1h",
                  "to": str(int(to)) if to else "now"}}}
    url = ("%s/explore?schemaVersion=1&panes=%s&orgId=%s"
           % (ctx["base"],
              urllib.parse.quote(json.dumps(panes, separators=(",", ":"))),
              ctx["inst"].get("org_id") or 1))
    print(url)


def telemetry_get_allowed(prov, path):
    """Raw get is only allowed for read API paths per provider."""
    norm = ol.normalize_api_path(path)
    if norm is None:
        return False
    p = urllib.parse.urlsplit(norm).path.lstrip("/")
    if prov == "grafana":
        if p.startswith("api/datasources/proxy/uid/"):
            rest = p[len("api/datasources/proxy/uid/"):]
            rest = rest.split("/", 1)[1] if "/" in rest else ""
            return (rest.startswith("loki/api/v1/")
                    or (rest.startswith("api/v1/")
                        and not rest.startswith("api/v1/admin"))
                    or rest.startswith(("api/search", "api/v2/search",
                                        "api/traces/", "api/v2/traces/",
                                        "api/echo", "api/status")))
        return p.startswith("api/")
    if prov == "prometheus":
        return (p.startswith("api/v1/")
                and not p.startswith("api/v1/admin"))
    if prov == "loki":
        return p.startswith("loki/api/v1/")
    if prov == "tempo":
        return p.startswith(("api/search", "api/v2/search", "api/traces/",
                             "api/v2/traces/", "api/echo", "api/status",
                             "ready"))
    return False


def _window(args):
    """Return (from_ts, to_ts); refuse unbounded windows over 24h unless
    --wide, and always over 7 days."""
    import time
    frm = ol.parse_time(getattr(args, "from", None))
    to = (ol.parse_time(getattr(args, "to", None))
          if getattr(args, "to", None) else time.time())
    span = to - frm
    if span > 7 * 86400:
        raise ol.OlError(
            "refused: windows over 7 days are not allowed; narrow "
            "--from/--to", ol.EX_GUARD)
    if span > 86400 and not getattr(args, "wide", False):
        raise ol.OlError(
            "refused: window over 24h; narrow --from/--to or pass --wide",
            ol.EX_GUARD)
    return frm, to


def cmd_get(ctx, args):
    p = args.path.lstrip("/")
    if p.startswith("http"):
        ol.die("get takes a path relative to the instance base URL",
               ol.EX_USAGE)
    if not telemetry_get_allowed(ctx["prov"], p):
        ol.die("refused: '%s' is not an allowed read path for provider "
               "'%s'" % (p, ctx["prov"]), ol.EX_GUARD)
    try:
        data = api(ctx, p)
        print(ol.redact(json.dumps(data, indent=2)))
    except ol.OlError:
        body = api(ctx, p, raw=True)
        sys.stdout.write(ol.redact(body.decode("utf-8", "replace")))


def build_parser():
    gp = argparse.ArgumentParser(add_help=False)
    gp.add_argument("--instance", default=argparse.SUPPRESS)
    gp.add_argument("--prod", action="store_true",
                    default=argparse.SUPPRESS,
                    help="required for production instances (reads only; "
                         "guards still apply)")
    gp.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    gp.add_argument("--ds", help="datasource uid override (grafana)",
                    default=argparse.SUPPRESS)
    gp.add_argument("--wide", action="store_true",
                    default=argparse.SUPPRESS,
                    help="allow windows over 24h (still capped at 7d)")
    ap = argparse.ArgumentParser(prog="telemetry.py", parents=[gp])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("whoami", parents=[gp])
    sub.add_parser("datasources", parents=[gp])
    p = sub.add_parser("dashboards", parents=[gp])
    p.add_argument("text")
    p = sub.add_parser("logs", parents=[gp])
    p.add_argument("query")
    p.add_argument("--from", dest="from", default="now-1h")
    p.add_argument("--to", dest="to")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--direction", choices=["forward", "backward"],
                   default="backward")
    p.add_argument("--full", action="store_true")
    p = sub.add_parser("labels", parents=[gp])
    p.add_argument("name", nargs="?")
    p = sub.add_parser("metric", parents=[gp])
    p.add_argument("query")
    p.add_argument("--at")
    p = sub.add_parser("metric-range", parents=[gp])
    p.add_argument("query")
    p.add_argument("--from", dest="from", required=True)
    p.add_argument("--to", dest="to", required=True)
    p.add_argument("--step", default="1m")
    p = sub.add_parser("traces", parents=[gp])
    p.add_argument("query")
    p.add_argument("--from", dest="from", default="now-1h")
    p.add_argument("--to", dest="to")
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("trace", parents=[gp])
    p.add_argument("trace_id")
    p.add_argument("--errors-only", action="store_true")
    p.add_argument("--max-spans", type=int, default=200)
    p = sub.add_parser("alerts", parents=[gp])
    p.add_argument("--state", choices=["firing", "pending", "all"],
                   default="firing")
    p = sub.add_parser("link", parents=[gp])
    p.add_argument("signal", choices=["logs", "metrics", "traces"])
    p.add_argument("query")
    p.add_argument("--from", dest="from", default="now-1h")
    p.add_argument("--to", dest="to")
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
    if (inst.get("provider") or "").lower() not in REQUIRED:
        ol.die("unknown provider '%s' (grafana, prometheus, loki or tempo)"
               % inst.get("provider"))
    ctx = make_ctx(AREA, name, inst)
    if not hasattr(args, "ds"):
        args.ds = None
    dispatch = {
        "whoami": lambda: cmd_whoami(ctx),
        "datasources": lambda: cmd_datasources(ctx),
        "dashboards": lambda: cmd_dashboards(ctx, args),
        "logs": lambda: cmd_logs(ctx, args),
        "labels": lambda: cmd_labels(ctx, args),
        "metric": lambda: cmd_metric(ctx, args),
        "metric-range": lambda: cmd_metric_range(ctx, args),
        "traces": lambda: cmd_traces(ctx, args),
        "trace": lambda: cmd_trace(ctx, args),
        "alerts": lambda: cmd_alerts(ctx, args),
        "link": lambda: cmd_link(ctx, args),
        "get": lambda: cmd_get(ctx, args),
    }
    return dispatch[args.cmd]()


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
