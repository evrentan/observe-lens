#!/usr/bin/env python3
"""observe-lens kube_ro: guarded read-only passthrough for kubectl, helm and
flux. Only allows read verbs; refuses anything that could change state or
reveal secrets."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ol_common as ol

AREA = "cd"

INSTALL_HINTS = {
    "kubectl": "install kubectl: macOS 'brew install kubectl', Linux distro "
               "package 'kubectl' (or kubernetes-client), Windows "
               "'winget search kubectl' or the official installer",
    "helm": "install helm: macOS 'brew install helm', Linux see "
            "helm.sh docs, Windows 'winget search helm'",
    "flux": "install flux: macOS 'brew install fluxcd/tap/flux', Linux see "
            "fluxcd.io install docs, Windows 'winget search flux'",
}

_KUBECTL_VERBS = {"get", "describe", "logs", "events", "top", "explain",
                  "api-resources", "api-versions", "version", "cluster-info",
                  "rollout", "auth", "config"}
_KUBECTL_BANNED_VERBS = {"exec", "cp", "port-forward", "proxy", "debug",
                       "attach", "apply", "create", "delete", "edit",
                       "patch", "replace", "scale", "cordon", "uncordon",
                       "drain", "taint", "label", "annotate", "set", "run",
                       "expose", "autoscale", "reconcile", "diff"}
_KUBECTL_ROLLOUT_SUB = {"status", "history"}
_KUBECTL_AUTH_SUB = {"can-i"}
_KUBECTL_CONFIG_SUB = {"get-contexts", "current-context"}
_HELM_VERBS = {"list", "ls", "status", "history", "hist", "version", "show",
               "get"}
_HELM_SHOW_SUB = {"chart", "readme", "crds"}
_HELM_GET_SUB = {"notes", "metadata"}
_FLUX_VERBS = {"get", "tree", "stats", "logs", "version", "check", "events",
               "trace"}
_FLUX_BANNED = {"export", "reconcile", "suspend", "resume", "create",
               "delete", "install", "uninstall", "build", "diff", "push",
               "pull", "bootstrap", "source", "image"}
_WATCH_FLAGS = {"-w", "--watch", "--watch-only", "-f", "--follow"}
_SECRET_RE = {"secret", "secrets"}


def _has_flag(args, names):
    return any(a in names or any(a.startswith(n + "=") for n in names
                                if n.startswith("--"))
               for a in args)


_VALUE_FLAGS = {
    "kubectl": {"-n", "--namespace", "--context", "--kubeconfig",
                "--cluster", "--user", "--request-timeout", "-o",
                "--output", "-l", "--selector", "-v", "--v"},
    "helm": {"-n", "--namespace", "--kube-context", "--kubeconfig",
             "-o", "--output", "-l", "--selector"},
    "flux": {"-n", "--namespace", "--context", "--kubeconfig",
             "--timeout"},
}
_BAD_FLAGS = {
    "kubectl": {"--server", "-s", "--token", "--username", "--password",
                "--insecure-skip-tls-verify", "--as", "--as-group",
                "--as-uid", "--client-key", "--client-certificate"},
    "helm": {"--kube-apiserver", "--kube-token", "--kube-as-user",
             "--kube-as-group", "--kube-insecure-skip-tls-verify",
             "--kube-ca-file"},
    "flux": {"--server", "--token", "--as", "--as-group", "--as-uid",
             "--insecure-skip-tls-verify"},
}


def _flag_matches(arg, bad):
    """arg is a banned flag in '--flag', '--flag=v' or '-sX' forms."""
    for f in bad:
        if arg == f or arg.startswith(f + "="):
            return f
    # short-option combined form: -s<value>
    for f in bad:
        if len(f) == 2 and f[0] == "-" and arg.startswith(f) and len(arg) > 2:
            return f
    return None


def _kubectl_verbosity(args, i):
    """Return (skip, err) for -v/--v flags; None,None when not one."""
    a = args[i]
    m = re.match(r"^(-v|--v)(?:=(\d+))?$", a)
    val = m.group(2) if m else None
    skip = 1
    if m and val is None:
        if i + 1 < len(args) and args[i + 1].isdigit():
            val, skip = args[i + 1], 2
        else:
            val = "2"  # bare -v is fine
    elif m is None:
        m2 = re.match(r"^-v(\d+)$", a)
        if m2:
            val = m2.group(1)
    if val is None:
        return None, None
    if int(val) > 5:
        return None, "verbosity -v above 5 can log credentials"
    return skip, None


def _scan_flag(tool, a, args, i, bad, value_flags):
    """Return (skip_count, error) for one flag token."""
    hit = _flag_matches(a, bad)
    if hit:
        return 0, "flag '%s' is not allowed (%s)" % (a, hit)
    if tool == "kubectl":
        skip, err = _kubectl_verbosity(args, i)
        if skip is not None:
            return skip, err
        if err:
            return 0, err
    if a in value_flags:
        return 2, None
    return 1, None


def _scan(tool, args):
    """Split args into (verb, positionals after verb, error). Positionals
    are non-flag tokens whose preceding flag does not take a value."""
    value_flags = _VALUE_FLAGS.get(tool, set())
    bad = _BAD_FLAGS.get(tool, set())
    positionals = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-"):
            skip, err = _scan_flag(tool, a, args, i, bad, value_flags)
            if err:
                return None, None, err
            i += skip
        else:
            positionals.append(a)
            i += 1
    if not positionals:
        return None, None, "no verb given"
    return positionals[0], positionals[1:], None


def _looks_secret(arg):
    """True if arg names a secrets resource, incl. comma lists like
    'all,secrets' or 'pods,secret/x'."""
    for piece in arg.lower().split(","):
        base = piece.split("/")[0].split(".")[0]
        if base in _SECRET_RE:
            return True
    return False


def _kubectl_subcmd_gate(verb, rest):
    if verb == "rollout" and (not rest or rest[0] not in _KUBECTL_ROLLOUT_SUB):
        return "only 'rollout status' and 'rollout history' are allowed"
    if verb == "auth" and (not rest or rest[0] not in _KUBECTL_AUTH_SUB):
        return "only 'auth can-i' is allowed"
    if verb == "config" and (not rest or rest[0] not in _KUBECTL_CONFIG_SUB):
        return ("only 'config get-contexts' and 'current-context' "
                "are allowed (view can expose credentials)")
    if verb == "cluster-info" and any(
            a == "dump" or a.startswith("dump=") for a in rest):
        return "'cluster-info dump' is not allowed"
    return None


def _decide_kubectl(req):
    verb, rest, args = req.verb, req.rest, req.args
    if _has_flag(args, {"--raw"}):
        return False, "--raw can expose credentials"
    if verb in _KUBECTL_BANNED_VERBS:
        return False, "kubectl %s changes state or runs code" % verb
    if verb not in _KUBECTL_VERBS:
        return False, "kubectl verb '%s' is not an allowed read" % verb
    if verb == "logs" and _has_flag(args[1:], _WATCH_FLAGS):
        return False, "log following (-f/--follow) is not allowed"
    if _has_flag(args[1:], {"-w", "--watch", "--watch-only"}):
        return False, "watch flags are not allowed"
    gate = _kubectl_subcmd_gate(verb, rest)
    if gate:
        return False, gate
    for a in rest:
        if _looks_secret(a):
            return False, "reading secrets is not allowed"
    return True, "ok"


def _helm_output_flag(args):
    for i, a in enumerate(args):
        val = None
        if a in ("-o", "--output") and i + 1 < len(args):
            val = args[i + 1]
        elif a.startswith("--output="):
            val = a.split("=", 1)[1]
        elif a.startswith("-o=") or (a.startswith("-o") and len(a) > 2):
            val = a.split("=", 1)[-1] if "=" in a else a[2:]
        if val and val.lower() in ("json", "yaml"):
            return val
    return None


def _decide_helm(req):
    verb, rest, args = req.verb, req.rest, req.args
    if verb not in _HELM_VERBS:
        return False, "helm verb '%s' is not an allowed read" % verb
    sub = rest[0] if rest else None
    if verb == "show" and sub not in _HELM_SHOW_SUB:
        return False, "only 'helm show chart|readme|crds' are allowed"
    if verb == "get" and sub not in _HELM_GET_SUB:
        return False, ("helm get %s can expose values/manifests with "
                       "credentials; only notes|metadata allowed" % sub)
    if verb == "status" and _helm_output_flag(args):
        return False, ("helm status -o %s includes user-supplied release "
                       "values" % _helm_output_flag(args))
    if _has_flag(args[1:], {"-w", "--watch"}):
        return False, "watch flags are not allowed"
    return True, "ok"


def _decide_flux(req):
    verb, args = req.verb, req.args
    if verb in _FLUX_BANNED or verb not in _FLUX_VERBS:
        return False, "flux verb '%s' is not an allowed read" % verb
    if _has_flag(args[1:], {"-f", "--follow", "-w", "--watch"}):
        return False, "watch/follow flags are not allowed"
    return True, "ok"


def decide(tool, args):
    """Return (ok, reason). Pure decision, no execution."""
    if not args:
        return False, "no arguments"
    verb, rest, err = _scan(tool, args)
    if err:
        return False, err
    import types
    req = types.SimpleNamespace(verb=verb, rest=rest, args=args)
    if tool == "kubectl":
        return _decide_kubectl(req)
    if tool == "helm":
        return _decide_helm(req)
    if tool == "flux":
        return _decide_flux(req)
    return False, "unknown tool '%s' (kubectl, helm or flux)" % tool


def _inject_instance_args(tool, inst, args):
    out = list(args)
    ctx = inst.get("context")
    kubeconfig = inst.get("kubeconfig")
    ns = inst.get("namespace")
    if kubeconfig and not any(a == "--kubeconfig"
                              or a.startswith("--kubeconfig=")
                              for a in out):
        out += ["--kubeconfig", os.path.expanduser(kubeconfig)]
    ctxflag = "--context" if tool != "helm" else "--kube-context"
    if ctx and not any(a == ctxflag or a.startswith(ctxflag + "=")
                     for a in out):
        out += [ctxflag, ctx]
    if ns and tool in ("kubectl", "helm"):
        if not any(a in ("-n", "--namespace", "-A", "--all-namespaces")
                   or a.startswith("--namespace=") for a in out):
            out += ["-n", ns]
    return out


def main():
    ap = argparse.ArgumentParser(
        prog="kube_ro.py",
        description="guarded read-only kubectl/helm/flux")
    ap.add_argument("--instance")
    ap.add_argument("--ro-timeout", type=int, default=60)
    ap.add_argument("tool", choices=["kubectl", "helm", "flux"])
    ap.add_argument("args", nargs=argparse.REMAINDER)
    ns = ap.parse_args()
    args = ns.args
    if args and args[0] == "--":
        args = args[1:]
    inst = None
    if ns.instance:
        cfg = ol.load_config(AREA)
        _, inst = ol.pick_instance(AREA, cfg, ns.instance)
    ok, reason = decide(ns.tool, args)
    if not ok:
        ol.die("refused: %s" % reason, ol.EX_GUARD)
    cli = shutil.which(ns.tool)
    if not cli:
        ol.die("%s not found on PATH — %s" % (ns.tool,
                                              INSTALL_HINTS[ns.tool]),
               ol.EX_CONFIG)
    if inst is not None:
        args = _inject_instance_args(ns.tool, inst, args)
    try:
        proc = subprocess.run([cli] + args, timeout=ns.ro_timeout,
                              capture_output=True, text=True,
                              errors="replace")
        sys.stdout.write(ol.redact(proc.stdout or ""))
        sys.stderr.write(ol.redact(proc.stderr or ""))
        sys.exit(proc.returncode)
    except subprocess.TimeoutExpired:
        ol.die("%s timed out after %ds" % (ns.tool, ns.ro_timeout), ol.EX_HTTP)


if __name__ == "__main__":
    try:
        main()
    except ol.OlError as e:
        ol.die(str(e), e.code)
    except KeyboardInterrupt:
        sys.exit(130)
