#!/usr/bin/env python3
"""observe-lens shared helpers: config, secrets, HTTP, time, SQL/Mongo guards.

This file is the single source of truth at common/_ol_common.py.
Copies live in each area skill under scripts/_ol_common.py and must stay
byte-identical (tools/sync_common.py --check verifies this).

Python 3.11+ standard library only. macOS, Linux and Windows.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# Exit codes
EX_OK = 0
EX_CONFIG = 2
EX_AUTH = 3
EX_HTTP = 4
EX_GUARD = 5
EX_USAGE = 64

AREAS = ("pipelines", "cd", "db", "telemetry")

# Secret keys each provider/engine prompts for in `config add`.
PROVIDER_SECRET_KEYS = {
    "jenkins": ["username", "token"],
    "github": ["token"],
    "argocd": ["token"],
    "grafana": ["token"],
    "prometheus": [],
    "loki": [],
    "tempo": [],
    "kubernetes": [],
    "postgres": [],
    "mysql": [],
    "mongodb": [],
}

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[@-_]")


class OlError(Exception):
    def __init__(self, msg, code=EX_CONFIG):
        super().__init__(msg)
        self.code = code


def die(msg, code=EX_CONFIG):
    print("error: %s" % msg, file=sys.stderr)
    sys.exit(code)


def strip_ansi(text):
    return ANSI_RE.sub("", text)


# ---------------------------------------------------------------------------
# Secret masking — applied to everything the scripts print or save.
# ---------------------------------------------------------------------------

_REDACT_RULES = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?"
                r"-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "[REDACTED:private-key]"),
    (re.compile(r"(?i)://([^/\s:@]+):([^/\s@]+)@"),
     r"://\1:[REDACTED:password]@"),
    (re.compile(r"github_pat_\w{20,}", re.ASCII), "[REDACTED:github]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED:github]"),
    (re.compile(r"gl(sa|c)_\w{20,}", re.ASCII), "[REDACTED:grafana]"),
    (re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"), "[REDACTED:slack]"),
    (re.compile(r"(AKIA|ASIA)[0-9A-Z]{16}"), "[REDACTED:aws-key]"),
    (re.compile(r"eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}", re.ASCII), "[REDACTED:jwt]"),
    (re.compile(r"(?i)(bearer)\s+[a-z0-9._~+/=-]{16,}"),
     r"\1 [REDACTED:bearer]"),
    (re.compile(r"(?i)basic\s+[a-z0-9+/=]{16,}"), "[REDACTED:basic]"),
    (re.compile(r'(?i)(?<![a-z0-9])([\w-]*?(?:password|passwd|'
                r'pwd|secret|token|api[_-]?key|access[_-]?key|'
                r'client[_-]?secret))(?!\w)'
                r'(\s*["\x27]?\s*[:=]\s*["\x27]?)([^\s"\x27,;&]{4,})',
                re.ASCII),
     r"\1\2[REDACTED:value]"),
]


def redact(text):
    """Mask high-confidence secret patterns as [REDACTED:<kind>]."""
    if not text:
        return text
    for rx, repl in _REDACT_RULES:
        text = rx.sub(repl, text)
    return text


_PROD_ENVS = {"prod", "production", "prd", "live"}


def needs_confirm(inst):
    """True when this instance should be treated as production."""
    if "confirm" in inst:
        return bool(inst["confirm"])
    return str(inst.get("environment") or "").lower() in _PROD_ENVS


_LOCALHOSTS = {"localhost", "127.0.0.1", "::1"}


def warn_http_creds(area, name, inst):
    """Warn when a credential would be sent over plain http."""
    url = str(inst.get("url") or "")
    if not url.startswith("http://"):
        return
    host = urllib.parse.urlsplit(url).hostname or ""
    if host.lower() in _LOCALHOSTS:
        return
    for key in _secret_keys_for(area, inst):
        if resolve_secret(area, name, inst, key):
            print("warning: '%s' sends credentials over plain http; "
                  "use https" % name, file=sys.stderr)
            return


def note_if_prod(name, inst):
    if needs_confirm(inst):
        print("note: instance '%s' is production (confirm=yes)" % name,
              file=sys.stderr)


def normalize_api_path(path):
    """Percent-decode repeatedly (max 3 rounds) until stable. Returns the
    decoded path string, or None when it contains a backslash, NUL, or a
    '.'/'..' segment — callers must treat None as refused."""
    prev = path
    for _ in range(3):
        dec = urllib.parse.unquote(prev)
        if dec == prev:
            break
        prev = dec
    if "\x00" in prev or "\\" in prev:
        return None
    if any(seg in (".", "..")
           for seg in urllib.parse.urlsplit(prev).path.split("/")):
        return None
    return prev


def safe_dest(area, dest):
    """Resolve --dest: relative paths land in the area temp dir; the result
    must stay inside it. Returns the resolved path string."""
    base = tmp_dir(area).resolve()
    p = Path(dest)
    if not p.is_absolute():
        p = base / p
    rp = p.resolve()
    if rp != base and base not in rp.parents:
        raise OlError("refused: --dest must stay inside %s" % base, EX_GUARD)
    return str(rp)


# ---------------------------------------------------------------------------
# Config root and files
# ---------------------------------------------------------------------------

def config_root():
    env = os.environ.get("OBSERVE_LENS_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "observe-lens"
    return Path.home() / ".config" / "observe-lens"


def area_dir(area):
    return config_root() / area


def config_path(area):
    return area_dir(area) / "config.json"


def secrets_dir(area):
    return area_dir(area) / "secrets"


def secrets_path(area, name):
    return secrets_dir(area) / ("%s.json" % name)


def knowledge_dir(area):
    return area_dir(area) / "knowledge"


def _check_private_dir(p):
    """POSIX: refuse symlinks or foreign ownership; tighten perms."""
    st = os.lstat(p)
    if stat.S_ISLNK(st.st_mode):
        raise OlError("refused: %s is a symlink" % p)
    if st.st_uid != os.getuid():
        raise OlError("refused: %s is owned by another user" % p)
    if st.st_mode & 0o077:
        os.chmod(p, 0o700)


def tmp_dir(area):
    if os.name == "nt":
        d = Path(tempfile.gettempdir()) / "observe-lens" / area
        d.mkdir(parents=True, exist_ok=True)
        return d
    base = Path(tempfile.gettempdir()) / ("observe-lens-%d" % os.getuid())
    if base.exists():
        _check_private_dir(base)
    else:
        base.mkdir(0o700)
    d = base / area
    if d.exists():
        _check_private_dir(d)
    else:
        d.mkdir(0o700)
    return d


def load_config(area):
    p = config_path(area)
    if not p.exists():
        raise OlError(
            "no config for area '%s'. Expected %s — run 'config init' then "
            "'config add' in your own terminal." % (area, p))
    try:
        with open(p, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise OlError("invalid config %s: %s" % (p, e))
    if not isinstance(cfg, dict) or not isinstance(cfg.get("instances"), dict):
        raise OlError("invalid config %s: missing 'instances' object" % p)
    return cfg


def pick_instance(area, cfg, name=None):
    insts = cfg.get("instances") or {}
    if name:
        if name not in insts:
            raise OlError("no instance named '%s'. Configured: %s"
                          % (name, ", ".join(sorted(insts)) or "(none)"))
        return name, insts[name]
    env_name = os.environ.get("OBSERVE_LENS_%s_INSTANCE" % area.upper())
    if env_name:
        if env_name not in insts:
            raise OlError("OBSERVE_LENS_%s_INSTANCE='%s' not in config. "
                          "Configured: %s" % (area.upper(), env_name,
                                              ", ".join(sorted(insts))))
        return env_name, insts[env_name]
    default = cfg.get("default")
    if default:
        if default not in insts:
            raise OlError("default instance '%s' not in config. Configured: %s"
                          % (default, ", ".join(sorted(insts))))
        return default, insts[default]
    if len(insts) == 1:
        n = next(iter(insts))
        return n, insts[n]
    raise OlError("multiple instances configured, pick one with --instance. "
                  "Configured: %s" % (", ".join(sorted(insts)) or "(none)"))


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

def _env_key(area, inst_name, key):
    norm = re.sub(r"[^A-Za-z0-9]", "_", inst_name).upper()
    return "OBSERVE_LENS_%s_%s_%s" % (area.upper(), norm,
                                    re.sub(r"[^A-Za-z0-9]", "_", key).upper())


def _secret_file_permissions_ok(path):
    if os.name == "nt":
        return True
    try:
        st = path.stat()
    except OSError:
        return True
    return not (st.st_mode & (stat.S_IRWXG | stat.S_IRWXO))


def warn_secret_permissions(area, name=None):
    """Warn on POSIX when the secrets folder or a secrets file is accessible
    by group/others. Silent on Windows."""
    if os.name == "nt":
        return
    paths = [secrets_dir(area)]
    if name:
        paths.append(secrets_path(area, name))
    for p in paths:
        if not p.exists():
            continue
        if not _secret_file_permissions_ok(p):
            print("warning: %s is group/world accessible — fix with: chmod 700 %s"
                  % (p, p), file=sys.stderr)


def secret_source(area, inst_name, inst, key):
    """Where the secret for key would come from: env|secret_env|command|file|none."""
    if os.environ.get(_env_key(area, inst_name, key)):
        return "env"
    se = inst.get("secret_env") or {}
    if key in se and os.environ.get(se[key]):
        return "secret_env"
    if key in (inst.get("secret_command") or {}):
        return "command"
    sf = secrets_path(area, inst_name)
    if sf.exists():
        try:
            with open(sf, "r", encoding="utf-8") as f:
                if key in json.load(f):
                    return "file"
        except (OSError, json.JSONDecodeError):
            pass
    return "none"


from collections import namedtuple as _nt

SecretRef = _nt("SecretRef", "area inst_name inst")


def _secret_from_env(ref, key):
    return os.environ.get(_env_key(ref.area, ref.inst_name, key))


def _secret_from_secret_env(ref, key):
    env = (ref.inst.get("secret_env") or {}).get(key)
    return os.environ.get(env) if env else None


def _secret_from_command(ref, key):
    argv = (ref.inst.get("secret_command") or {}).get(key)
    if not argv:
        return None
    if isinstance(argv, str):
        argv = [argv]
    try:
        out = subprocess.run(argv, capture_output=True, text=True,
                             timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode == 0 and out.stdout.strip():
        return out.stdout.strip()
    return None


def _secret_from_file(ref, key):
    sf = secrets_path(ref.area, ref.inst_name)
    if not sf.exists():
        return None
    try:
        with open(sf, "r", encoding="utf-8") as f:
            return json.load(f).get(key) or None
    except (OSError, json.JSONDecodeError):
        return None


_SECRET_RESOLVERS = (_secret_from_env, _secret_from_secret_env,
                     _secret_from_command, _secret_from_file)


def resolve_secret(area, inst_name, inst, key):
    """Resolve secret for key; None when absent. Never raises on missing."""
    ref = SecretRef(area, inst_name, inst)
    for resolver in _SECRET_RESOLVERS:
        v = resolver(ref, key)
        if v:
            return v
    return None


# ---------------------------------------------------------------------------
# HTTP (GET only)
# ---------------------------------------------------------------------------

def tls_context(inst):
    if inst.get("verify_tls") is False:
        raise OlError("verify_tls: false is not supported: it lets anyone on "
                      "the network read the credentials sent to this "
                      "instance. Remove it and set \"ca_file\" to your CA "
                      "bundle (PEM) instead.")
    ca_file = inst.get("ca_file")
    if ca_file:
        path = os.path.expanduser(ca_file)
        if not os.path.isfile(path):
            raise OlError("ca_file not found: %s" % path)
        return ssl.create_default_context(cafile=path)
    return ssl.create_default_context()


def _sanitize_url(url):
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path,
                                    "", ""))


def http_get(url, headers=None, timeout=30, context=None,
             max_bytes=64 * 1024 * 1024):
    """GET url. headers: dict; an 'Authorization' header is added with
    add_unredirected_header so redirects to other hosts cannot leak it.
    Returns (status, body_bytes, headers_dict). Raises OlError on failure."""
    req = urllib.request.Request(url, method="GET")
    for k, v in (headers or {}).items():
        if k.lower() == "authorization":
            req.add_unredirected_header(k, v)
        else:
            req.add_header(k, v)
    handlers = []
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read(max_bytes), dict(r.headers.items())
    except urllib.error.HTTPError as e:
        try:
            body = e.read(4096).decode("utf-8", "replace")[:300]
        except Exception:
            body = ""
        hint = ""
        if e.code in (401, 403):
            hint = " — check credentials ('config list' shows the secret source)"
        elif e.code == 404:
            hint = " — check the URL path and resource name"
        raise OlError("HTTP %s %s%s\n%s" % (e.code, _sanitize_url(url),
                                            hint, body.strip()),
                      EX_AUTH if e.code in (401, 403) else EX_HTTP)
    except OSError as e:
        raise OlError("cannot reach %s: %s" % (_sanitize_url(url), e), EX_HTTP)


def get_json(url, headers=None, timeout=30, context=None):
    _, body, _ = http_get(url, headers=headers, timeout=timeout,
                          context=context)
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except json.JSONDecodeError as e:
        raise OlError("invalid JSON from %s: %s" % (_sanitize_url(url), e))


def basic_auth(username, password):
    return "Basic " + base64.b64encode(
        ("%s:%s" % (username, password)).encode("utf-8")).decode("ascii")


def bearer(token):
    return "Bearer %s" % token


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

_REL_RE = re.compile(r"^now(?:-(\d+)([smhd]))?$")


def parse_time(value):
    """Parse ISO 8601 (naive = UTC), epoch s/ms, now, now-15m. Returns epoch s."""
    if value is None:
        raise OlError("missing time value", EX_USAGE)
    v = str(value).strip()
    m = _REL_RE.match(v.lower())
    if m:
        now = datetime.now(timezone.utc).timestamp()
        if not m.group(1):
            return now
        n = int(m.group(1))
        unit = m.group(2)
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
        return now - n * mult
    if re.fullmatch(r"\d+(\.\d+)?", v):
        num = float(v)
        if num > 1e12:
            return num / 1000.0
        return num
    iso = v
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        raise OlError("cannot parse time '%s' (use ISO 8601, epoch or now-15m)"
                      % value, EX_USAGE)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def fmt_ts(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S")


def fmt_iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def iso_to_epoch(s):
    if s is None:
        return None
    s = str(s)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# ---------------------------------------------------------------------------
# SQL guard (postgres + mysql)
# ---------------------------------------------------------------------------

_SQL_BANNED_WORDS = [
    "insert", "update", "delete", "merge", "upsert", "truncate", "drop",
    "alter", "create", "grant", "revoke", "copy", "call", "do", "lock",
    "vacuum", "into", "setval", "nextval", "set_config",
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir",
    "pg_switch_wal", "pg_rotate_logfile", "pg_create_restore_point",
    "pg_promote", "pg_notify", "load_file", "sleep", "pg_sleep",
    "get_lock", "release_lock", "release_all_locks",
    "benchmark",
]
# word-prefix bans: catch dblink_exec, pg_advisory_lock, lo_import/lo_put etc.
_SQL_BANNED_PREFIX = ("dblink", "pg_advisory", "lo_")
_SQL_BANNED_PHRASES = [
    "for update", "for share", "for no key update", "for key share",
]
_SQL_ALLOWED_FIRST = {"select", "with", "show", "describe", "desc",
                      "explain", "table", "values"}


def _sql_blank(out, i, j, n):
    for k in range(i, min(j, n)):
        if out[k] != "\n":
            out[k] = " "


def _sql_skip_line_comment(text, i, n):
    j = text.find("\n", i)
    return n if j < 0 else j


def _sql_skip_block_comment(text, i, n):
    j = text.find("*/", i + 2)
    return n if j < 0 else j + 2


def _sql_skip_squote(text, i, n):
    j = i + 1
    while j < n:
        if text[j] == "'":
            if j + 1 < n and text[j + 1] == "'":
                j += 2
                continue
            break
        j += 1
    return j + 1


def _sql_skip_to(text, i, ch, n):
    j = text.find(ch, i + 1)
    return n if j < 0 else j + 1


def _sql_skip_dollar(text, i, n, dialect):
    if dialect != "postgres" or text[i] != "$" or i + 1 >= n:
        return None
    m = re.match(r"\$[A-Za-z_]\w*\$|\$\$", text[i:], re.ASCII)
    if not m:
        return None
    tag = m.group(0)
    j = text.find(tag, i + len(tag))
    return n if j < 0 else j + len(tag)


def _sql_strip(text, dialect="postgres"):
    """Length-preserving blank-out of comments, string literals, quoted
    identifiers and (postgres only) dollar-quoted strings. Indices in the
    result match the original; newlines are kept."""
    out = list(text)
    n = len(text)
    i = 0
    while i < n:
        two = text[i:i + 2]
        if two == "--":
            j = _sql_skip_line_comment(text, i, n)
        elif two == "/*":
            j = _sql_skip_block_comment(text, i, n)
        elif text[i] == "'":
            j = _sql_skip_squote(text, i, n)
        elif text[i] in ('"', "`"):
            j = _sql_skip_to(text, i, text[i], n)
        elif text[i] == "$":
            j = _sql_skip_dollar(text, i, n, dialect)
            if j is None:
                i += 1
                continue
        else:
            i += 1
            continue
        _sql_blank(out, i, j, n)
        i = j
    return "".join(out)


def _sql_words(clean):
    return re.findall(r"[a-zA-Z_]\w*", clean.lower(), re.ASCII)


def check_sql(statement, dialect="postgres"):
    """Validate a read-only SQL statement. Analysis happens on a stripped,
    index-preserving copy; the ORIGINAL statement is returned (with any
    trailing top-level ';' cut). Raises OlError(EX_GUARD) on refusal."""
    if "\\" in statement:
        raise OlError("refused: backslashes are not allowed (they can hide "
                      "code from the guard)", EX_GUARD)
    if "/*!" in statement:
        raise OlError("refused: MySQL executable comments are not allowed",
                      EX_GUARD)
    stripped = _sql_strip(statement, dialect)
    stmt = statement
    tail = stripped.rstrip()
    if tail.endswith(";"):
        # cut the ';' at the same index in the original
        stmt = statement[:len(tail) - 1]
        stripped = tail[:-1]
    if ";" in stripped:
        raise OlError("refused: only a single statement is allowed", EX_GUARD)
    words = _sql_words(stripped)
    if not words:
        raise OlError("refused: empty statement", EX_GUARD)
    first = words[0]
    if first not in _SQL_ALLOWED_FIRST:
        raise OlError("refused: statement must start with SELECT, WITH, SHOW, "
                      "DESCRIBE, EXPLAIN, TABLE or VALUES (got '%s')" % first,
                      EX_GUARD)
    lowered = stripped.lower()
    if first == "explain" and re.search(r"\banalyz", lowered):
        raise OlError("refused: EXPLAIN ANALYZE executes the statement",
                      EX_GUARD)
    for phrase in _SQL_BANNED_PHRASES:
        if re.search(r"\b%s\b" % re.escape(phrase).replace(r"\ ", r"\s+"),
                     lowered):
            raise OlError("refused: '%s' locks rows" % phrase, EX_GUARD)
    for w in sorted(set(words)):
        if w in _SQL_BANNED_WORDS or w.startswith(_SQL_BANNED_PREFIX):
            raise OlError("refused: '%s' is not a read-only keyword" % w,
                          EX_GUARD)
    return stmt.strip()


def _has_top_level_limit(stripped):
    """True if the statement has a top-level LIMIT/FETCH outside parentheses
    (literals already blanked)."""
    depth = 0
    for t in re.findall(r"\(|\)|[a-zA-Z_]\w*", stripped.lower(), re.ASCII):
        if t == "(":
            depth += 1
        elif t == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and t in ("limit", "fetch"):
            return True
    return False


def sql_add_limit(stmt, limit, dialect="postgres"):
    if limit is None:
        return stmt
    stripped = _sql_strip(stmt, dialect)
    if _has_top_level_limit(stripped):
        return stmt
    words = _sql_words(stripped)
    if words and words[0] in ("select", "with", "values", "table"):
        return "SELECT * FROM (\n%s\n) AS _ol LIMIT %d" % (stmt, int(limit))
    return stmt


# ---------------------------------------------------------------------------
# Mongo guard
# ---------------------------------------------------------------------------

_MONGO_METHODS = {"find", "findone", "countdocuments", "estimateddocumentcount",
                  "distinct", "aggregate", "getindexes", "stats"}
_MONGO_TOP_METHODS = {"getcollectionnames", "getcollectioninfos", "stats"}
_MONGO_CHAIN = {"limit", "sort", "skip", "project", "projection", "toarray",
                "count", "hint", "maxtimems", "batchsize", "explain"}
_MONGO_CTORS = {"objectid", "isodate", "date", "numberlong", "numberint",
                "numberdecimal", "uuid", "timestamp", "regexp", "bindata",
                "minkey", "maxkey"}
_MONGO_CALLABLE = (_MONGO_METHODS | _MONGO_TOP_METHODS | _MONGO_CHAIN
                   | _MONGO_CTORS | {"getcollection"})
_MONGO_BAD_TEXT = ["$out", "$merge", "$function", "$accumulator", "$where"]
_MONGO_BAD_SUBSTR = ["insert", "update", "delete", "remove", "replace",
                     "drop", "create", "rename", "bulkwrite", "eval",
                     "runcommand", "admincommand", "getsiblingdb", "getmongo",
                     "connect", "load", "require", "process"]
_MONGO_BAD_IDENT = {"function", "this", "globalthis", "constructor",
                    "__proto__", "prototype"}


def _refuse(msg):
    raise OlError("refused: %s" % msg, EX_GUARD)


def _mongo_scan_string(e, i):
    """Return (token_text, next_index); raises OlError on unterminated or
    backtick literals."""
    c = e[i]
    if c == "`":
        _refuse("backtick template literals are not allowed")
    j = i + 1
    n = len(e)
    while j < n:
        if e[j] == "\\":
            j += 2
            continue
        if e[j] == c:
            break
        j += 1
    if j >= n:
        _refuse("unterminated string literal")
    return e[i:j + 1], j + 1


def _mongo_tokens(e):
    """(kind, value) tokens; strings become ('str', whole)."""
    toks = []
    i, n = 0, len(e)
    while i < n:
        c = e[i]
        if c.isspace():
            i += 1
        elif c in ("'", '"', "`"):
            text, i = _mongo_scan_string(e, i)
            toks.append(("str", text))
        elif c.isdigit():
            m = re.match(r"\d+(\.\d+)?", e[i:])
            toks.append(("num", m.group(0)))
            i += m.end()
        else:
            m = re.match(r"[A-Za-z_$][\w$]*", e[i:], re.ASCII)
            if m:
                toks.append(("ident", m.group(0)))
                i += m.end()
            else:
                toks.append((c, c))
                i += 1
    return toks


def _mongo_check_text(e):
    if ";" in e:
        _refuse("only a single expression is allowed")
    low = e.lower()
    for bad in _MONGO_BAD_TEXT:
        if bad in low:
            _refuse("'%s' is not read-only" % bad)


def _mongo_check_ident(toks, idx):
    val = toks[idx][1]
    v = val.lower()
    if v in _MONGO_BAD_IDENT:
        _refuse("'%s' is not allowed" % val)
    for bad in _MONGO_BAD_SUBSTR:
        if bad in v:
            _refuse("'%s' contains '%s'" % (val, bad))
    if v == "new":
        nxt = toks[idx + 1] if idx + 1 < len(toks) else (None, None)
        if nxt[0] != "ident" or nxt[1].lower() not in _MONGO_CTORS:
            _refuse("'new' is only allowed before an allowed constructor")
    if (idx + 1 < len(toks) and toks[idx + 1][0] == "("
            and v not in _MONGO_CALLABLE):
        _refuse("call to '%s' is not allowed" % val)


def _mongo_check_token(toks, idx):
    kind, _ = toks[idx]
    if kind == "ident":
        _mongo_check_ident(toks, idx)
    elif kind == "(":
        prev = toks[idx - 1] if idx else (None, None)
        if prev[0] != "ident":
            _refuse("parentheses must follow a name")
    elif kind == "[":
        prev = toks[idx - 1] if idx else (None, None)
        if prev[0] in ("ident", ")", "]"):
            _refuse("computed member access is not allowed")
    elif kind == "=":
        _refuse("assignment is not allowed")


def _mongo_head_getcollection(head):
    """True when head is getCollection("<c>").<method>(...)."""
    return (len(head) >= 7 and head[1][0] == "(" and head[2][0] == "str"
            and head[3][0] == ")" and head[4][0] == "."
            and head[5][0] == "ident"
            and head[5][1].lower() in _MONGO_METHODS
            and head[6][0] == "(")


def _mongo_head_collection(head):
    """True when head is <coll>[.<coll>...].<method>(."""
    i = 0
    while i + 1 < len(head) and head[i][0] == "ident" and head[i + 1][0] == ".":
        i += 2
    return (i + 1 < len(head) and head[i][0] == "ident"
            and head[i][1].lower() in _MONGO_METHODS
            and head[i + 1][0] == "(")


def _mongo_check_head(toks):
    if toks[1][0] != ".":
        _refuse("expected db.<...>")
    head = toks[2:]
    if not head or head[0][0] != "ident":
        _refuse("expected a collection or method after db.")
    h0 = head[0][1].lower()
    if h0 in _MONGO_TOP_METHODS:
        if len(head) < 2 or head[1][0] != "(":
            _refuse("expected %s(...)" % head[0][1])
    elif h0 == "getcollection":
        if not _mongo_head_getcollection(head):
            _refuse("expected db.getCollection(\"<name>\").<method>(...)")
    elif not _mongo_head_collection(head):
        _refuse("expected db.<collection>.<method>(...)")


def check_mongo(expr):
    """Validate a read-only mongosh expression via tokenization. Returns the
    expression unchanged. Raises OlError(EX_GUARD) on refusal."""
    e = expr.strip()
    _mongo_check_text(e)
    toks = _mongo_tokens(e)
    dbs = [t for t in toks if t[0] == "ident" and t[1].lower() == "db"]
    if len(dbs) != 1 or toks[0] != ("ident", "db"):
        _refuse("expression must reference exactly the leading 'db'")
    for idx in range(len(toks)):
        _mongo_check_token(toks, idx)
    _mongo_check_head(toks)
    return e


def mongo_add_limit(expr, limit):
    """Append .limit(n) to a find() expression that has none."""
    if limit is None:
        return expr
    if ".find(" not in expr.replace(" ", ""):
        return expr
    if re.search(r"\.\s*limit\s*\(", expr):
        return expr
    return expr + ".limit(%d)" % int(limit)


# ---------------------------------------------------------------------------
# config subcommands (shared CLI for every area script)
# ---------------------------------------------------------------------------

def _secret_keys_for(area, inst):
    prov = (inst.get("provider") or inst.get("engine") or "").lower()
    keys = list(PROVIDER_SECRET_KEYS.get(prov, []))
    if area == "db":
        users = inst.get("users")
        if isinstance(users, str):
            users = [u.strip() for u in users.split(",")]
        users = users or ([inst["user"]] if inst.get("user") else [])
        keys = ["password_%s" % u for u in users]
    return keys


def cmd_config_path(area):
    root = config_root()
    print("config root : %s (%s)" % (root, "exists" if root.exists()
                                     else "missing"))
    ad = area_dir(area)
    print("area dir    : %s (%s)" % (ad, "exists" if ad.exists() else "missing"))
    for sub in ("config.json", "secrets", "knowledge"):
        p = ad / sub
        print("  %-12s: %s (%s)" % (sub, p, "exists" if p.exists()
                                    else "missing"))


def cmd_config_list(area):
    cfg = load_config(area)
    insts = cfg.get("instances") or {}
    if not insts:
        print("no instances configured — run 'config add' in your terminal")
        return
    default = cfg.get("default")
    for name in sorted(insts):
        inst = insts[name]
        prov = inst.get("provider") or inst.get("engine") or "?"
        url = inst.get("url") or inst.get("host") or "?"
        env = inst.get("environment") or "-"
        mark = " (default)" if name == default else ""
        sources = sorted({secret_source(area, name, inst, k)
                          for k in _secret_keys_for(area, inst)}) or ["none"]
        src = ",".join(s for s in sources if s != "none") or "none"
        conf = "yes" if needs_confirm(inst) else "no"
        print("%-20s %-10s %-40s env=%-10s secret=%s confirm=%s%s"
              % (name, prov, url, env, src, conf, mark))
    if cfg.get("tunnels"):
        print("tunnels: %s" % ", ".join(sorted(cfg["tunnels"])))


def _check_fields(name, inst, prov, required_fields):
    missing = [f for f in (required_fields or {}).get(prov, [])
               if not inst.get(f)]
    if missing:
        print("  %s (%s): missing fields %s" % (name, prov,
                                                ", ".join(missing)))
        return False
    return True


def _check_tls(name, inst):
    ok = True
    if inst.get("verify_tls") is False:
        print("  %s: verify_tls: false is not supported; remove it and "
              "set ca_file to your CA bundle" % name)
        ok = False
    ca_file = inst.get("ca_file")
    if ca_file and not os.path.isfile(os.path.expanduser(ca_file)):
        print("  %s: ca_file not found: %s" % (name, ca_file))
        ok = False
    return ok


def _warn_prod_like(name, inst):
    tokens = set(re.split(r"[^A-Za-z0-9]+",
                          ("%s %s" % (name, inst.get("url") or ""))
                          .lower()))
    if (tokens & _PROD_ENVS and "environment" not in inst
            and "confirm" not in inst):
        print("  warning: '%s' looks like production; set "
              "\"environment\" or \"confirm\"" % name)


def _check_secrets(area, name, inst, prov):
    ok = True
    for key in _secret_keys_for(area, inst):
        src = secret_source(area, name, inst, key)
        print("  %s: secret '%s' -> %s" % (name, key, src))
        if src == "none" and prov in ("jenkins", "github", "argocd",
                                      "grafana"):
            ok = False
    return ok


def _check_cli(name, prov, client_clis):
    ok = True
    cli = (client_clis or {}).get(prov)
    for c in [cli] if isinstance(cli, str) else (cli or []):
        found = shutil.which(c)
        print("  %s: cli '%s' %s" % (name, c, "found" if found
                                     else "NOT on PATH"))
        if not found:
            ok = False
    return ok


def _check_tunnel(name, inst, cfg):
    tn = inst.get("tunnel")
    if not tn:
        return True
    defined = tn in (cfg.get("tunnels") or {})
    print("  %s: tunnel '%s' %s" % (name, tn,
          "defined" if defined else "NOT defined in tunnels"))
    return defined


def _check_kube(name):
    for c in ("kubectl", "helm", "flux"):
        if shutil.which(c):
            print("  %s: cli '%s' found" % (name, c))
    if not any(shutil.which(c) for c in ("kubectl", "helm", "flux")):
        print("  %s: none of kubectl/helm/flux on PATH" % name)
        return False
    return True


def _check_instance(area, name, inst, cfg, required_fields, client_clis):
    prov = (inst.get("provider") or inst.get("engine") or "")
    ok = _check_fields(name, inst, prov, required_fields)
    ok = _check_tls(name, inst) and ok
    _warn_prod_like(name, inst)
    warn_http_creds(area, name, inst)
    ok = _check_secrets(area, name, inst, prov) and ok
    ok = _check_cli(name, prov, client_clis) and ok
    ok = _check_tunnel(name, inst, cfg) and ok
    if prov == "kubernetes":
        if inst.get("context"):
            print("  %s: context %s" % (name, inst["context"]))
        else:
            print("  %s: no context set (uses current)" % name)
        ok = _check_kube(name) and ok
    return ok


def cmd_config_check(area, instance=None, required_fields=None,
                     client_clis=None):
    """Offline sanity check. required_fields: {provider/engine: [fields]}.
    client_clis: {engine/provider: cli name or [names]}."""
    p = config_path(area)
    if not p.exists():
        print("config: missing %s" % p)
        return False
    try:
        cfg = load_config(area)
    except OlError as e:
        print("config: %s" % e)
        return False
    print("config: %s ok" % p)
    ok = True
    insts = cfg.get("instances") or {}
    for name in [instance] if instance else sorted(insts):
        if name not in insts:
            print("  %s: no such instance" % name)
            ok = False
            continue
        ok = _check_instance(area, name, insts[name], cfg,
                             required_fields, client_clis) and ok
    warn_secret_permissions(area, instance)
    return ok


def cmd_config_knowledge(area):
    kd = knowledge_dir(area)
    files = sorted(kd.glob("*.md")) if kd.exists() else []
    if not files:
        print("no knowledge notes in %s — run 'config init' for a template, "
              "then add your own notes" % kd)
        return
    for f in files:
        print("%s (%d bytes)" % (f, f.stat().st_size))


def _assets_dir(area):
    """The skill's assets/ dir. Works when this file lives in
    scripts/_ol_common.py (deployed) and at repo common/ (development)."""
    here = Path(__file__).resolve().parent.parent
    d = here / "assets"
    if d.exists():
        return d
    return here / "skills" / ("observe-lens-%s" % area) / "assets"


def _ensure_secrets_dir(area):
    """Create secrets/ with 0o700 on POSIX; tighten it if it already exists
    with group/other bits set."""
    sd = secrets_dir(area)
    if not sd.exists():
        sd.mkdir(parents=True)
        created = True
    else:
        created = False
    if os.name != "nt":
        mode = stat.S_IMODE(sd.stat().st_mode)
        if mode & 0o077:
            os.chmod(sd, 0o700)
    return created


def cmd_config_init(area):
    ad = area_dir(area)
    created = []
    ad.mkdir(parents=True, exist_ok=True)
    if _ensure_secrets_dir(area):
        created.append(str(secrets_dir(area)))
    cp = config_path(area)
    if not cp.exists():
        live = {"default": None, "instances": {}}
        if area == "db":
            live["tunnels"] = {}
        cp.write_text(json.dumps(live, indent=2) + "\n", encoding="utf-8")
        created.append(str(cp))
    example = ad / "config.example.json"
    if not example.exists():
        src = _assets_dir(area) / "config.example.json"
        if src.exists():
            shutil.copyfile(src, example)
            created.append(str(example))
    kd = knowledge_dir(area)
    kd.mkdir(exist_ok=True)
    readme = kd / "README.md"
    if not readme.exists():
        src = _assets_dir(area) / "knowledge-template.md"
        if src.exists():
            shutil.copyfile(src, readme)
        else:
            readme.write_text("# %s knowledge notes\n\n"
                              "Write notes about your own systems here.\n"
                              % area, encoding="utf-8")
        created.append(str(readme))
    if created:
        print("created:")
        for c in created:
            print("  %s" % c)
    else:
        print("nothing to create; %s already initialised" % ad)


def _config_add_parse(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="config add")
    ap.add_argument("name")
    ap.add_argument("--provider")
    ap.add_argument("--engine")
    ap.add_argument("--url")
    ap.add_argument("--set", dest="sets", action="append", default=[],
                    metavar="KEY=VALUE")
    ap.add_argument("--default", action="store_true")
    ap.add_argument("--no-secret", action="store_true")
    return ap.parse_args(argv)


def _config_add_set(inst, kv):
    if "=" not in kv:
        die("--set expects key=value, got '%s'" % kv, EX_USAGE)
    k, v = kv.split("=", 1)
    k = k.strip()
    try:
        inst[k] = json.loads(v)
    except json.JSONDecodeError:
        inst[k] = ([p.strip() for p in v.split(",")]
                   if ("," in v or k == "users") else v)


def _config_add_merge(area, args):
    cfg = {}
    cp = config_path(area)
    if cp.exists():
        try:
            cfg = json.loads(cp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            die("invalid config %s" % cp)
    cfg.setdefault("instances", {})
    inst = dict(cfg["instances"].get(args.name) or {})
    if args.provider:
        inst["provider"] = args.provider
    if args.engine:
        inst["engine"] = args.engine
    if args.url:
        inst["url"] = args.url
    for kv in args.sets:
        _config_add_set(inst, kv)
    cfg["instances"][args.name] = inst
    if args.default or not cfg.get("default"):
        cfg["default"] = args.name
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print("wrote %s" % cp)
    return inst


def _config_add_secrets(area, args, inst):
    import getpass
    secrets = {}
    sp = secrets_path(area, args.name)
    if sp.exists():
        try:
            secrets = json.loads(sp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            secrets = {}
    for key in _secret_keys_for(area, inst):
        prompt = "%s for instance '%s' (leave blank to keep/skip): " % (
            key, args.name)
        try:
            v = getpass.getpass(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if v:
            secrets[key] = v
    if secrets:
        _ensure_secrets_dir(area)
        if os.name != "nt":
            fd = os.open(str(sp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                         0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(secrets, indent=2) + "\n")
            os.chmod(sp, 0o600)
        else:
            tmp = sp.with_suffix(".tmp")
            tmp.write_text(json.dumps(secrets, indent=2) + "\n",
                           encoding="utf-8")
            os.replace(tmp, sp)
        print("wrote %s" % sp)


def cmd_config_add(area, argv):
    args = _config_add_parse(argv)
    prov = args.provider or args.engine
    if not prov:
        die("config add needs --provider (or --engine for db)", EX_USAGE)
    if prov not in PROVIDER_SECRET_KEYS:
        die("unknown provider/engine '%s'. Known: %s"
            % (prov, ", ".join(sorted(PROVIDER_SECRET_KEYS))), EX_USAGE)
    if not sys.stdin.isatty():
        die("config add is interactive; run this in your own terminal",
            EX_USAGE)
    inst = _config_add_merge(area, args)
    if args.no_secret:
        return
    keys = _secret_keys_for(area, inst)
    if not keys:
        print("no secret keys for provider '%s'; done" % prov)
        return
    _config_add_secrets(area, args, inst)


def _dispatch_first_positional(argv):
    """Return (index_of_first_positional, instance_value). Skips global
    options (value-taking and boolean) so 'config' is only recognised as
    the subcommand when it is the first positional."""
    value_opts = {"--instance", "--repo", "--user", "--timeout",
                  "--format", "--ds", "--ro-timeout"}
    inst = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--instance="):
            inst = a.split("=", 1)[1]
        elif a in value_opts:
            if a == "--instance" and i + 1 < len(argv):
                inst = argv[i + 1]
            i += 1
        elif not a.startswith("-"):
            return i, inst
        i += 1
    return None, inst


def dispatch_config(area, argv, required_fields=None, client_clis=None):
    """Run a `config ...` command if 'config' is the first positional after
    global options; returns an exit code, or None if argv is not a config
    command. A --instance given before 'config' is passed to config check."""
    i, inst = _dispatch_first_positional(argv)
    if i is None or argv[i] != "config":
        return None
    rest = list(argv[i:])
    if inst and "check" in rest and "--instance" not in rest:
        rest += ["--instance", inst]
    return config_main(area, rest, required_fields, client_clis)


def _config_check_cmd(area, rest, required_fields, client_clis):
    inst = None
    if "--instance" in rest:
        i = rest.index("--instance")
        inst = rest[i + 1] if i + 1 < len(rest) else None
    ok = cmd_config_check(area, inst, required_fields, client_clis)
    return EX_OK if ok else EX_CONFIG


def config_main(area, argv, required_fields=None, client_clis=None):
    """Handle `config <sub> ...`; returns exit code or None if argv is not a
    config command."""
    if not argv or argv[0] != "config":
        return None
    if len(argv) < 2:
        die("usage: config path|list|check|knowledge|init|add", EX_USAGE)
    sub, rest = argv[1], argv[2:]
    dispatch = {
        "path": lambda a, r: (cmd_config_path(a), EX_OK)[1],
        "list": lambda a, r: (cmd_config_list(a), EX_OK)[1],
        "knowledge": lambda a, r: (cmd_config_knowledge(a), EX_OK)[1],
        "init": lambda a, r: (cmd_config_init(a), EX_OK)[1],
        "add": lambda a, r: (cmd_config_add(a, r), EX_OK)[1],
        "check": lambda a, r: _config_check_cmd(a, r, required_fields,
                                                client_clis),
    }
    fn = dispatch.get(sub)
    if not fn:
        die("unknown config subcommand '%s'" % sub, EX_USAGE)
    return fn(area, rest)


def run_cli(argv, cwd=None, env=None, timeout=None):
    """Run a CLI arg list without a shell; return CompletedProcess."""
    return subprocess.run(argv, cwd=cwd, env=env, timeout=timeout)
