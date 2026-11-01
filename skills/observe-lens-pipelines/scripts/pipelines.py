#!/usr/bin/env python3
"""observe-lens pipelines: read-only CI client for Jenkins and GitHub Actions."""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ol_common as ol

AREA = "pipelines"

REQUIRED = {
    "jenkins": ["url"],
    "github": ["url"],
}
GH_API_VERSION = "2022-11-28"


# ---------------------------------------------------------------------------
# Jenkins helpers
# ---------------------------------------------------------------------------

def jenkins_base(inst):
    return (inst.get("url") or "").rstrip("/")


def jenkins_auth(area, name, inst):
    user = ol.resolve_secret(area, name, inst, "username")
    token = ol.resolve_secret(area, name, inst, "token")
    if not user or not token:
        raise ol.OlError(
            "jenkins needs username+token secrets — see references/setup.md")
    return ol.basic_auth(user, token)


def jenkins_job_path(job):
    """'FOLDER/JOB' -> 'job/FOLDER/job/JOB'; 'job/F/job/J' kept as-is."""
    job = job.strip().strip("/")
    parts = job.split("/")
    if parts[0] == "job":
        return "/".join("job/%s" % urllib.parse.quote(p, safe="")
                        for p in parts[1::2])
    return "/".join("job/%s" % urllib.parse.quote(p, safe="") for p in parts)


def jenkins_parse_job_run(inst, arg):
    """Return (job_path, run_number_or_None) for a job name or build URL."""
    base = jenkins_base(inst)
    a = arg.strip()
    if a.startswith("http://") or a.startswith("https://"):
        if base and a.startswith(base):
            a = a[len(base):]
        else:
            a = urllib.parse.urlsplit(a).path
        a = re.sub(r"^/+", "", a)
        a = re.sub(r"^view/[^/]+/", "", a)
        a = a.strip("/")
        m = re.match(r"(.*?)/(\d+)(?:/.*)?$", a)
        if m and "/job/" in "/" + m.group(1) + "/":
            return m.group(1), int(m.group(2))
        return a or None, None
    m = re.match(r"(.+?)[/:](\d+)$", a)
    if m and not a.startswith("job/"):
        return m.group(1), int(m.group(2))
    if a.startswith("job/"):
        m = re.match(r"(.+)/(\d+)$", a)
        if m:
            return m.group(1), int(m.group(2))
    return a, None


def jenkins_api(ctx, path, params=None):
    url = "%s/%sapi/json" % (ctx["base"], (path.strip("/") + "/") if path else "")
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return ol.get_json(url, headers={"Authorization": ctx["auth"]},
                       timeout=ctx["timeout"], context=ctx["ctx"])


def jenkins_get(ctx, path):
    url = "%s/%s" % (ctx["base"], path.lstrip("/"))
    _, body, hdrs = ol.http_get(url, headers={"Authorization": ctx["auth"]},
                                timeout=ctx["timeout"], context=ctx["ctx"])
    return body, hdrs


def _jenkins_commit_action(a):
    rev = ((a.get("lastBuiltRevision") or {}).get("SHA1")
           or (a.get("buildsByBranchName") or {}))
    if isinstance(rev, str) and rev:
        return rev
    if isinstance(rev, dict):
        for v in rev.values():
            sha = (v.get("revision") or {}).get("SHA1")
            if sha:
                return sha
    return None


def _jenkins_commit_changeset(cs):
    items = cs.get("items") or []
    return items[0].get("commitId") if items else None


def _jenkins_commit(run):
    for a in run.get("actions") or []:
        sha = _jenkins_commit_action(a)
        if sha:
            return sha
    for cs in run.get("changeSets") or []:
        sha = _jenkins_commit_changeset(cs)
        if sha:
            return sha
    return _jenkins_commit_changeset(run.get("changeSet") or {})


def _jenkins_action_causes(a):
    causes = [c.get("shortDescription") or ""
              for c in a.get("causes") or []]
    upstream = next(
        ("%s #%s" % (c["upstreamProject"], c.get("upstreamBuild"))
         for c in a.get("causes") or [] if c.get("upstreamProject")),
        None)
    return causes, upstream


def _jenkins_causes_params(actions):
    causes = []
    upstream = None
    params = {}
    for a in actions or []:
        cs, up = _jenkins_action_causes(a)
        causes.extend(cs)
        upstream = upstream or up
        if isinstance(a.get("parameters"), list):
            for p in a["parameters"]:
                if p.get("name") is not None:
                    params[p["name"]] = ol.redact(str(p.get("value")))
    return causes, upstream, params


def jenkins_run_summary(ctx, job_path, n):
    data = jenkins_api(ctx, "%s/%d" % (job_path, n))
    causes, upstream, params = _jenkins_causes_params(data.get("actions"))
    out = {
        "job": data.get("fullDisplayName") or data.get("displayName"),
        "number": data.get("number"),
        "result": data.get("result") or ("BUILDING" if data.get("building")
                                         else None),
        "building": data.get("building"),
        "url": data.get("url"),
        "started": ol.fmt_ts((data.get("timestamp") or 0) / 1000.0)
        if data.get("timestamp") else None,
        "duration_s": round((data.get("duration") or 0) / 1000.0, 1),
        "causes": [c for c in causes if c],
        "upstream": upstream,
        "parameters": params,
        "commit": _jenkins_commit(data),
    }
    return data, out


_JENKINS_ACTION_SEGMENTS = {
    "build", "buildWithParameters", "rebuild", "replay", "stop", "term",
    "kill", "doDelete", "delete", "doWipeOutWorkspace", "scriptText",
    "script", "safeRestart", "restart", "exit", "quietDown",
    "cancelQuietDown", "configSubmit", "polling", "toggleLogKeep",
    "submitDescription", "input", "proceedEmpty", "abort", "enable",
    "disable"}


def jenkins_get_allowed(path):
    """Raw get is only allowed for read endpoints."""
    norm = ol.normalize_api_path(path)
    if norm is None:
        return False
    p = urllib.parse.urlsplit(norm).path
    segs = p.split("/")
    for i, seg in enumerate(segs):
        if (seg in _JENKINS_ACTION_SEGMENTS
                and (i == 0 or segs[i - 1] != "job")):
            return False
    return (p.endswith("api/json") or p.endswith("api/xml")
            or p.endswith("consoleText")
            or p.endswith("logText/progressiveText")
            or "/artifact/" in p or "/wfapi/" in p)


def j_whoami(call):
    ctx = call.ctx
    data = jenkins_api(ctx, "me")
    print("%s (%s)" % (data.get("fullName") or data.get("id"),
                       ctx["base"]))


def j_runs(call):
    args, ctx = call.args, call.ctx
    job_path, _ = jenkins_parse_job_run(ctx["inst"], args.job)
    jp = jenkins_job_path(job_path)
    data = jenkins_api(ctx, jp)
    builds = (data.get("builds") or [])[: args.limit]
    for b in builds:
        print("#%-6s %-10s %s" % (b.get("number"), "", b.get("url")))
    if getattr(args, "json", False):
        print(json.dumps(builds, indent=2))


def _jenkins_job_and_n(args, ctx, allow_missing_n=False):
    job_path, url_n = jenkins_parse_job_run(ctx["inst"], args.job)
    n = getattr(args, "number", None) or url_n
    if n is not None:
        n = int(n)
    if n is None and not allow_missing_n:
        ol.die("give a build number or paste a build URL", ol.EX_USAGE)
    return jenkins_job_path(job_path), n


def j_run(call):
    args, ctx = call.args, call.ctx
    jp, n = _jenkins_job_and_n(args, ctx)
    data, out = jenkins_run_summary(ctx, jp, n)
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2))
        return
    for k, v in out.items():
        if v not in (None, {}, []):
            print("%-12s %s" % (k + ":", v))


def j_params(call):
    args, ctx = call.args, call.ctx
    jp, n = _jenkins_job_and_n(args, ctx, allow_missing_n=True)
    if n is None:
        data = jenkins_api(ctx, jp)
        n = (data.get("lastCompletedBuild") or {}).get("number")
        if n is None:
            ol.die("no completed build to read parameters from")
    _, out = jenkins_run_summary(ctx, jp, n)
    if not out["parameters"]:
        print("(no parameters recorded on #%s)" % n)
    for k, v in out["parameters"].items():
        print("%s = %s" % (k, v))


def j_artifacts(call):
    args, ctx = call.args, call.ctx
    jp, n = _jenkins_job_and_n(args, ctx)
    data, _ = jenkins_run_summary(ctx, jp, n)
    arts = data.get("artifacts") or []
    if not arts:
        print("(no artifacts)")
    for a in arts:
        print("%-60s %s" % (a.get("relativePath"), a.get("fileName")))


def j_artifact(call):
    args, ctx = call.args, call.ctx
    jp, n = _jenkins_job_and_n(args, ctx)
    dest = (ol.safe_dest(AREA, args.dest) if args.dest
            else str(ol.tmp_dir(AREA) / Path(args.path).name))
    body, _ = jenkins_get(ctx, "%s/%d/artifact/%s" % (jp, n, args.path))
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_bytes(body)
    print("saved %s (%d bytes)" % (dest, len(body)))


def j_log(call):
    args, ctx = call.args, call.ctx
    jp, n = _jenkins_job_and_n(args, ctx)
    body, _ = jenkins_get(ctx, "%s/%d/consoleText" % (jp, n))
    text = ol.redact(ol.strip_ansi(body.decode("utf-8", "replace")))
    dest = ol.tmp_dir(AREA) / ("jenkins-%s-%s.log"
                               % (jp.replace("/", "_"), n))
    dest.write_text(text, encoding="utf-8")
    lines = len(text.splitlines())
    print("%s (%d lines)" % (dest, lines))


def j_get(call):
    args, ctx = call.args, call.ctx
    p = args.path.lstrip("/")
    if p.startswith("http"):
        ol.die("get takes a path relative to the Jenkins base URL",
               ol.EX_USAGE)
    if not jenkins_get_allowed(p):
        ol.die("refused: '%s' is not a read endpoint (api/json, api/xml, "
               "consoleText, logText/progressiveText, /artifact/ or "
               "/wfapi/ only)" % p, ol.EX_GUARD)
    body, _ = jenkins_get(ctx, p)
    sys.stdout.buffer.write(body)


_JENKINS_CMDS = {
    "whoami": j_whoami, "runs": j_runs, "run": j_run, "params": j_params,
    "artifacts": j_artifacts, "artifact": j_artifact, "log": j_log,
    "get": j_get}


def cmd_jenkins(args, ctx):
    fn = _JENKINS_CMDS.get(args.cmd)
    if not fn:
        ol.die("unknown subcommand '%s'" % args.cmd, ol.EX_USAGE)
    import types
    fn(types.SimpleNamespace(args=args, ctx=ctx))


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def gh_base(inst):
    return (inst.get("url") or "https://api.github.com").rstrip("/")


def gh_repo(inst, args):
    repo = getattr(args, "repo", None) or inst.get("repo")
    if not repo:
        raise ol.OlError("no repository — set 'repo' in the instance or pass "
                         "--repo owner/name")
    return repo


def gh_headers(ctx):
    return {
        "Authorization": ctx["auth"],
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GH_API_VERSION,
    }


def gh_api(ctx, path_or_url, params=None):
    if path_or_url.startswith("http"):
        url = path_or_url
    else:
        url = "%s/%s" % (ctx["base"], path_or_url.lstrip("/"))
    if params:
        sep = "&" if "?" in url else "?"
        url += sep + urllib.parse.urlencode(params)
    return ol.get_json(url, headers=gh_headers(ctx), timeout=ctx["timeout"],
                       context=ctx["ctx"])


GH_RUN_URL_RE = re.compile(
    r"https?://[^/]+/([^/]+/[^/]+)/actions/runs/(\d+)(?:/job/(\d+))?")


def gh_parse_run(args_wf, args_id):
    """Return (repo_or_None, workflow, run_id, job_id)."""
    job_id = None
    rid = args_id
    m = GH_RUN_URL_RE.match(args_id or "")
    repo = None
    wf = args_wf
    if m:
        repo = m.group(1)
        rid = m.group(2)
        job_id = m.group(3)
    else:
        m = GH_RUN_URL_RE.match(args_wf or "")
        if m:
            repo = m.group(1)
            rid = m.group(2)
            job_id = m.group(3)
            wf = None
    return repo, wf, rid, job_id


def gh_run_summary(ctx, repo, run_id, attempt=None):
    suffix = "/attempts/%d" % attempt if attempt else ""
    run = gh_api(ctx, "/repos/%s/actions/runs/%s%s" % (repo, run_id, suffix))
    jobs = gh_api(ctx, "/repos/%s/actions/runs/%s%s/jobs"
                  % (repo, run_id, suffix), {"per_page": 100})
    out = {
        "id": run.get("id"),
        "name": run.get("name"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "branch": run.get("head_branch"),
        "commit": run.get("head_sha"),
        "event": run.get("event"),
        "attempt": run.get("run_attempt"),
        "url": run.get("html_url"),
        "started": run.get("run_started_at") or run.get("created_at"),
        "jobs": [],
    }
    for j in (jobs.get("jobs") or []):
        failed_steps = [s.get("name") for s in (j.get("steps") or [])
                        if s.get("conclusion") == "failure"]
        out["jobs"].append({
            "id": j.get("id"), "name": j.get("name"),
            "conclusion": j.get("conclusion") or j.get("status"),
            "failed_steps": failed_steps,
        })
    return run, jobs, out


def gh_whoami(call):
    args, ctx = call.args, call.ctx
    try:
        data = gh_api(ctx, "/user")
        print("%s (%s)" % (data.get("login"), ctx["base"]))
        return
    except ol.OlError:
        repo = ctx["inst"].get("repo") or getattr(args, "repo", None)
        if not repo:
            raise
        data = gh_api(ctx, "/repos/%s" % repo)
        print("token can read %s (fine-grained token, no /user access)"
              % data.get("full_name"))


def gh_runs(call):
    args, ctx = call.args, call.ctx
    repo = gh_repo(ctx["inst"], args)
    if args.workflow == "all":
        path = "/repos/%s/actions/runs" % repo
    else:
        path = "/repos/%s/actions/workflows/%s/runs" % (
            repo, urllib.parse.quote(str(args.workflow), safe=""))
    params = {"per_page": args.limit}
    if getattr(args, "branch", None):
        params["branch"] = args.branch
    if getattr(args, "status", None):
        params["status"] = args.status
    data = gh_api(ctx, path, params)
    runs = data.get("workflow_runs") or []
    for r in runs:
        print("%-12s %-12s %-10s %-20s %s" % (
            r.get("id"), r.get("status"), r.get("conclusion") or "-",
            (r.get("head_branch") or "")[:20], r.get("display_title")
            or r.get("name") or ""))
    if getattr(args, "json", False):
        print(json.dumps(runs, indent=2))


def _gh_dispatch_block(text):
    """Extract the workflow_dispatch inputs block (best effort)."""
    lines = text.splitlines()
    keep, in_wd = [], False
    for line in lines:
        if re.match(r"^\s*workflow_dispatch:", line):
            in_wd = True
            keep.append(line)
            continue
        if in_wd:
            if re.match(r"^\S", line) or (
                    re.match(r"^  \S", line)
                    and "inputs:" not in line
                    and not line.startswith("   ")):
                if re.match(r"^\s{0,2}\S", line) and \
                        "inputs:" not in line and \
                        "workflow_dispatch" not in line:
                    in_wd = False
                    continue
            keep.append(line)
    return keep


def gh_params(call):
    args, ctx = call.args, call.ctx
    repo = gh_repo(ctx["inst"], args)
    wf = urllib.parse.quote(str(args.workflow), safe="")
    data = gh_api(ctx, "/repos/%s/actions/workflows/%s" % (repo, wf))
    fpath = data.get("path")
    if not fpath:
        return
    content = gh_api(ctx, "/repos/%s/contents/%s" % (repo, fpath))
    import base64 as b64
    try:
        text = b64.b64decode(content.get("content") or b""
                             ).decode("utf-8", "replace")
    except Exception:
        text = ""
    keep = _gh_dispatch_block(text)
    if keep:
        print("\n".join(keep))
    else:
        print("(no workflow_dispatch block found in %s)" % fpath)


def gh_run_cmd(call):
    args, ctx = call.args, call.ctx
    repo, rid = call.repo, call.rid
    attempt = getattr(args, "attempt", None)
    _, _, out = gh_run_summary(ctx, repo, rid, attempt)
    if getattr(args, "json", False):
        print(json.dumps(out, indent=2))
        return
    for k in ("id", "name", "status", "conclusion", "branch",
              "commit", "event", "attempt", "started", "url"):
        if out.get(k) is not None:
            print("%-10s %s" % (k + ":", out[k]))
    for j in out["jobs"]:
        print("job %-12s %-10s %s%s" % (
            j["id"], j["conclusion"], j["name"],
            " failed steps: %s" % ", ".join(j["failed_steps"])
            if j["failed_steps"] else ""))


def gh_artifacts(call):
    ctx, repo, rid = call.ctx, call.repo, call.rid
    data = gh_api(ctx, "/repos/%s/actions/runs/%s/artifacts"
                  % (repo, rid), {"per_page": 100})
    arts = data.get("artifacts") or []
    if not arts:
        print("(no artifacts)")
    for a in arts:
        print("%-40s %10d bytes  %s" % (
            a.get("name"), a.get("size_in_bytes") or 0,
            "expired" if a.get("expired") else a.get("id")))


def gh_artifact(call):
    args, ctx = call.args, call.ctx
    repo, rid = call.repo, call.rid
    data = gh_api(ctx, "/repos/%s/actions/runs/%s/artifacts"
                  % (repo, rid), {"per_page": 100})
    match = [a for a in (data.get("artifacts") or [])
             if a.get("name") == args.path
             or str(a.get("id")) == str(args.path)]
    if not match:
        ol.die("no artifact named '%s' on run %s" % (args.path, rid))
    a = match[0]
    if a.get("expired"):
        ol.die("artifact '%s' is expired" % a["name"])
    _, body, _ = ol.http_get(
        a["archive_download_url"], headers=gh_headers(ctx),
        timeout=ctx["timeout"], context=ctx["ctx"])
    dest = (ol.safe_dest(AREA, args.dest) if args.dest
            else str(ol.tmp_dir(AREA) / (a["name"] + ".zip")))
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_bytes(body)
    print("saved %s (%d bytes)" % (dest, len(body)))


def _gh_log_pick_named(args, jlist, rid):
    pick = [j for j in jlist if str(j.get("id")) == str(args.gh_job)
            or j.get("name") == args.gh_job]
    if not pick:
        ol.die("no job '%s' on run %s" % (args.gh_job, rid))
    return pick


def _gh_log_jobs(args, rid, jobs, job_id):
    jlist = jobs.get("jobs") or []
    if getattr(args, "gh_job", None):
        return _gh_log_pick_named(args, jlist, rid)
    if job_id and job_id != "-":
        return [j for j in jlist if str(j.get("id")) == job_id]
    pick = [j for j in jlist if j.get("conclusion") == "failure"]
    return pick or jlist


def _gh_log_text(ctx, repo, job, keep_ts):
    _, body, _ = ol.http_get(
        "%s/repos/%s/actions/jobs/%s/logs" % (ctx["base"], repo, job["id"]),
        headers=gh_headers(ctx), timeout=ctx["timeout"], context=ctx["ctx"])
    text = body.decode("utf-8", "replace")
    if not keep_ts:
        text = "\n".join(
            re.sub(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z ?",
                   "", ln) for ln in text.splitlines())
    return "===== job: %s =====\n%s" % (
        job.get("name"), ol.redact(ol.strip_ansi(text)))


def gh_log(call):
    args, ctx = call.args, call.ctx
    repo, rid = call.repo, call.rid
    job_id = call.job_id
    _, jobs, _ = gh_run_summary(ctx, repo, rid)
    pick = _gh_log_jobs(args, rid, jobs, job_id)
    dest = ol.tmp_dir(AREA) / ("gh-%s-%s.log" % (repo.replace("/", "_"),
                                                 rid))
    keep = getattr(args, "keep_timestamps", False)
    out_text = "\n".join(_gh_log_text(ctx, repo, j, keep) for j in pick)
    dest.write_text(out_text, encoding="utf-8")
    print("%s (%d lines)" % (dest, len(out_text.splitlines())))


def gh_get(call):
    args, ctx = call.args, call.ctx
    p = args.path.lstrip("/")
    if p.startswith("http"):
        ol.die("get takes a path relative to the API base URL", ol.EX_USAGE)
    print(json.dumps(gh_api(ctx, p), indent=2))


def cmd_github(args, ctx):
    import types
    c = args.cmd
    call = types.SimpleNamespace(args=args, ctx=ctx)
    if c == "whoami":
        return gh_whoami(call)
    if c == "runs":
        return gh_runs(call)
    if c == "params":
        return gh_params(call)
    if c == "get":
        return gh_get(call)
    if c in ("run", "log", "artifacts", "artifact"):
        repo_u, _, rid, job_id = gh_parse_run(args.workflow, args.id)
        call.repo = repo_u or gh_repo(ctx["inst"], args)
        call.rid = rid
        call.job_id = job_id
        if rid is None:
            ol.die("give a run id or paste a run URL", ol.EX_USAGE)
        if c == "run":
            return gh_run_cmd(call)
        if c == "artifacts":
            return gh_artifacts(call)
        if c == "artifact":
            return gh_artifact(call)
        return gh_log(call)
    ol.die("unknown subcommand '%s'" % c, ol.EX_USAGE)


def build_parser():
    gp = argparse.ArgumentParser(add_help=False)
    gp.add_argument("--instance", default=argparse.SUPPRESS)
    gp.add_argument("--repo", help="owner/name (github)",
                    default=argparse.SUPPRESS)
    gp.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    gp.add_argument("--timeout", type=int, default=argparse.SUPPRESS)
    ap = argparse.ArgumentParser(prog="pipelines.py", parents=[gp],
                                 description="read-only CI client")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("whoami", parents=[gp])
    p = sub.add_parser("runs", parents=[gp])
    p.add_argument("workflow", help="job path or workflow file/id or 'all'")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--branch")
    p.add_argument("--status")
    p = sub.add_parser("run", parents=[gp])
    p.add_argument("job", help="job path/URL or workflow or run URL")
    p.add_argument("id", nargs="?", help="run number/id (github)")
    p.add_argument("--attempt", type=int)
    p = sub.add_parser("log", parents=[gp])
    p.add_argument("job")
    p.add_argument("id", nargs="?")
    p.add_argument("--job", dest="gh_job", help="github job name or id")
    p.add_argument("--keep-timestamps", action="store_true")
    p = sub.add_parser("params", parents=[gp])
    p.add_argument("job", help="job path or workflow")
    p.add_argument("id", nargs="?")
    p = sub.add_parser("artifacts", parents=[gp])
    p.add_argument("job")
    p.add_argument("id", nargs="?")
    p = sub.add_parser("artifact", parents=[gp])
    p.add_argument("job")
    p.add_argument("id", nargs="?")
    p.add_argument("path", help="artifact relative path or name")
    p.add_argument("--dest")
    p = sub.add_parser("get", parents=[gp])
    p.add_argument("path")
    return ap


def main():
    args = build_parser().parse_args()
    if args.cmd is None:
        ol.die("no subcommand; see --help", ol.EX_USAGE)
    return args


if __name__ == "__main__":
    rc = ol.dispatch_config(AREA, sys.argv[1:], required_fields=REQUIRED)
    if rc is not None:
        sys.exit(rc)
    args = main()
    try:
        cfg = ol.load_config(AREA)
        name, inst = ol.pick_instance(AREA, cfg, getattr(args, "instance", None))
        ol.note_if_prod(name, inst)
        ol.warn_http_creds(AREA, name, inst)
        prov = (inst.get("provider") or "").lower()
        timeout = getattr(args, "timeout", None) or inst.get("timeout") or 60
        context = ol.tls_context(inst)
        if prov == "jenkins":
            ctx = {"base": jenkins_base(inst), "inst": inst,
                   "auth": jenkins_auth(AREA, name, inst),
                   "timeout": timeout, "ctx": context}
            # unify arg names: jenkins job = workflow|job, number = id
            args.job = getattr(args, "workflow", None) or getattr(
                args, "job", None)
            args.number = getattr(args, "id", None)
            cmd_jenkins(args, ctx)
        elif prov == "github":
            token = ol.resolve_secret(AREA, name, inst, "token")
            if not token:
                raise ol.OlError(
                    "github needs a 'token' secret — see references/setup.md")
            ctx = {"base": gh_base(inst), "inst": inst,
                   "auth": ol.bearer(token), "timeout": timeout,
                   "ctx": context}
            args.workflow = getattr(args, "workflow", None) or getattr(
                args, "job", None)
            cmd_github(args, ctx)
        else:
            ol.die("unknown provider '%s' (expected jenkins or github)"
                   % prov)
    except ol.OlError as e:
        sys.exit(ol.die(str(e), e.code))
    except KeyboardInterrupt:
        sys.exit(130)
