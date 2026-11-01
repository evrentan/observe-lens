#!/usr/bin/env python3
"""observe-lens db: read-only database client for postgres, mysql (MariaDB)
and mongodb, through psql/mysql/mongosh with a statement guard."""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ol_common as ol

AREA = "db"
REQUIRED = {
    "postgres": ["host", "database"],
    "mysql": ["host", "database"],
    "mongodb": ["host"],
}
CLIENTS = {"postgres": "psql", "mysql": "mysql", "mongodb": "mongosh"}

INSTALL_HINTS = {
    "postgres": "install psql: macOS 'brew install libpq' (then add its bin "
                "to PATH), Linux distro package 'postgresql-client', Windows "
                "run 'winget search postgresql' or use the official installer",
    "mysql": "install the mysql client: macOS 'brew install mysql-client', "
             "Linux 'mysql-client' or 'mariadb-client', Windows run "
             "'winget search mysql' or use the official installer",
    "mongodb": "install mongosh: macOS 'brew install mongosh', Linux see "
               "MongoDB's mongosh install page, Windows run "
               "'winget search mongosh' or use the official installer",
}


def _cli(inst):
    engine = (inst.get("engine") or "").lower()
    if inst.get("client"):
        client = shutil.which(os.path.expanduser(inst["client"]))
        if not client:
            raise ol.OlError("configured client not found or not executable: "
                             "%s" % inst["client"])
        return client
    cli = shutil.which(CLIENTS[engine])
    if not cli:
        raise ol.OlError("%s not found on PATH — %s"
                         % (CLIENTS[engine], INSTALL_HINTS[engine]))
    return cli


def _pick_user(inst, want):
    users = inst.get("users")
    if isinstance(users, str):
        users = [u.strip() for u in users.split(",")]
    users = users or ([inst["user"]] if inst.get("user") else [])
    if want:
        if want not in users:
            raise ol.OlError("user '%s' not in instance users %s"
                             % (want, users), ol.EX_USAGE)
        return want
    if users:
        return users[0]
    return inst.get("user")


def _password(area, name, inst, user):
    if not user:
        return None
    return ol.resolve_secret(area, name, inst, "password_%s" % user)


def _run(argv, env, timeout=120):
    try:
        proc = subprocess.run(argv, env=env, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ol.OlError("query timed out after %ds" % timeout, ol.EX_HTTP)
    except OSError as e:
        raise ol.OlError("cannot run %s: %s" % (argv[0], e.strerror or e),
                         ol.EX_CONFIG)
    if proc.returncode != 0:
        err = ol.redact((proc.stderr or proc.stdout or "").strip()[:500])
        raise ol.OlError("database CLI failed: %s" % err, ol.EX_HTTP)
    return proc.stdout


# ---------------------------------------------------------------------------
# postgres
# ---------------------------------------------------------------------------

def pg_env(area, name, inst, user):
    env = dict(os.environ)
    pw = _password(area, name, inst, user)
    if pw:
        env["PGPASSWORD"] = pw
    env["PGOPTIONS"] = ("-c default_transaction_read_only=on "
                        "-c statement_timeout=60000")
    if inst.get("sslmode"):
        env["PGSSLMODE"] = inst["sslmode"]
    env["PGCONNECT_TIMEOUT"] = "10"
    env["PGAPPNAME"] = "observe-lens"
    return env


def pg_query(area, name, inst, stmt, user, fmt):
    cli = _cli(inst)
    argv = [cli, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-P", "pager=off",
            "-h", str(inst.get("host")), "-p", str(inst.get("port") or 5432),
            "-U", str(user), "-d", str(inst.get("database"))]
    if fmt in ("csv", "json"):
        argv.append("--csv")
    argv += ["-c", stmt]
    return _run(argv, pg_env(area, name, inst, user))


def csv_to_json(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    print(ol.redact(json.dumps(rows, indent=2)))


# ---------------------------------------------------------------------------
# mysql
# ---------------------------------------------------------------------------

def mysql_query(area, name, inst, stmt, user, fmt):
    cli = _cli(inst)
    pw = _password(area, name, inst, user)
    tmp = None
    try:
        argv = [cli]
        if pw:
            fd, tmp = tempfile.mkstemp(prefix="ol-mysql-", suffix=".cnf")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("[client]\npassword=%s\n" % pw)
            if os.name != "nt":
                os.chmod(tmp, 0o600)
            argv.append("--defaults-extra-file=%s" % tmp)
        argv += ["-h", str(inst.get("host")), "-P",
                 str(inst.get("port") or 3306), "-u", str(user),
                 "-D", str(inst.get("database")), "--batch"]
        if fmt == "table":
            argv.append("--table")
        full = ("SET SESSION TRANSACTION READ ONLY; "
                "START TRANSACTION READ ONLY; %s; ROLLBACK;" % stmt)
        argv += ["-e", full]
        env = dict(os.environ)
        env["MYSQL_PS1"] = "observe-lens"
        return _run(argv, env)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# mongodb
# ---------------------------------------------------------------------------

_MONGO_JS = """
const uri = process.env.OL_MONGO_URI;
const dbName = process.env.OL_MONGO_DB;
const expr = process.env.OL_MONGO_EXPR;
const fieldsOnly = process.env.OL_MONGO_FIELDS === '1';
db = connect(uri).getSiblingDB(dbName);
const result = eval(expr);
let out = result;
if (result !== null && typeof result === 'object' &&
    typeof result.toArray === 'function') {
  out = result.toArray();
}
if (fieldsOnly) {
  const seen = {};
  const typeOf = (v) => {
    if (v === null) return 'null';
    if (Array.isArray(v)) return 'array';
    if (v instanceof Date) return 'date';
    if (v instanceof ObjectId) return 'objectId';
    return typeof v;
  };
  const walk = (v, path) => {
    const t = typeOf(v);
    if (!path) {
      if (t === 'object') {
        for (const k of Object.keys(v)) walk(v[k], k);
      }
      return;
    }
    seen[path] = seen[path] || {};
    seen[path][t] = (seen[path][t] || 0) + 1;
    if (t === 'object') {
      for (const k of Object.keys(v)) walk(v[k], path + '.' + k);
    } else if (t === 'array') {
      for (const item of v) walk(item, path + '[]');
    }
  };
  (out || []).forEach((d) => walk(d, ''));
  print(JSON.stringify(seen));
} else {
  print(EJSON.stringify(out, null, 2, {relaxed: true}));
}
"""


def mongo_uri(area, name, inst, user, dbname):
    scheme = "mongodb+srv" if inst.get("srv") else "mongodb"
    auth = ""
    if user:
        pw = _password(area, name, inst, user)
        auth = urllib.parse.quote(str(user), safe="")
        if pw:
            auth += ":" + urllib.parse.quote(pw, safe="")
        auth += "@"
    host = inst.get("host")
    port = inst.get("port")
    netloc = "%s:%s" % (host, port) if port else str(host)
    q = {}
    if inst.get("auth_source"):
        q["authSource"] = inst["auth_source"]
    if inst.get("tls") is True:
        q["tls"] = "true"
    elif inst.get("tls") is False:
        q["tls"] = "false"
    for k, v in (inst.get("options") or {}).items():
        q[k] = str(v)
    qs = ("?" + urllib.parse.urlencode(q)) + "" if q else ""
    return "%s://%s%s/%s%s" % (scheme, auth, netloc, dbname or "", qs)


def mongo_eval(area, name, inst, expr, user, dbname, fields=False):
    cli = _cli(inst)
    fd, js = tempfile.mkstemp(prefix="ol-mongo-", suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_MONGO_JS)
        env = dict(os.environ)
        env["OL_MONGO_URI"] = mongo_uri(area, name, inst, user,
                                        dbname or inst.get("database"))
        env["OL_MONGO_DB"] = dbname or inst.get("database") or "test"
        env["OL_MONGO_EXPR"] = expr
        if fields:
            env["OL_MONGO_FIELDS"] = "1"
        return _run([cli, "--nodb", "--quiet", "--file", js], env)
    finally:
        try:
            os.unlink(js)
        except OSError:
            pass


# ---------------------------------------------------------------------------

def _emit(text):
    sys.stdout.write(ol.redact(text))


def _print_sql(area, name, inst, engine, stmt, user, fmt):
    if engine == "postgres":
        out = pg_query(area, name, inst, stmt, user, fmt)
        if fmt == "json":
            csv_to_json(out)
        else:
            _emit(out)
    else:
        _emit(mysql_query(area, name, inst, stmt, user, fmt))


def build_parser():
    gp = argparse.ArgumentParser(add_help=False)
    gp.add_argument("--instance", default=argparse.SUPPRESS)
    gp.add_argument("--user", default=argparse.SUPPRESS)
    gp.add_argument("--format", choices=["table", "csv", "json"],
                    default=argparse.SUPPRESS)
    gp.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                    help="alias for --format json")
    ap = argparse.ArgumentParser(prog="db.py", parents=[gp],
                                 description="read-only database client")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("ping", parents=[gp])
    p = sub.add_parser("tables", parents=[gp])
    p.add_argument("--schema")
    p.add_argument("--filter")
    p = sub.add_parser("columns", parents=[gp])
    p.add_argument("table")
    p.add_argument("--schema")
    p = sub.add_parser("query", parents=[gp])
    p.add_argument("sql")
    p.add_argument("--limit", type=int, default=100)
    p = sub.add_parser("mongo", parents=[gp])
    p.add_argument("expression")
    p.add_argument("--database")
    p.add_argument("--limit", type=int, default=100)
    return ap


def main():
    args = build_parser().parse_args()
    if not args.cmd:
        ol.die("no subcommand; see --help", ol.EX_USAGE)
    fmt = "json" if getattr(args, "json", False) else getattr(
        args, "format", "table")
    cfg = ol.load_config(AREA)
    name, inst = ol.pick_instance(AREA, cfg, getattr(args, "instance", None))
    ol.note_if_prod(name, inst)
    ol.warn_http_creds(AREA, name, inst)
    engine = (inst.get("engine") or "").lower()
    if engine not in CLIENTS:
        ol.die("unknown engine '%s' (postgres, mysql or mongodb)" % engine)
    user = _pick_user(inst, getattr(args, "user", None))
    dispatch = {
        "query": _cmd_query, "mongo": _cmd_mongo, "ping": _cmd_ping,
        "tables": _cmd_tables, "columns": _cmd_columns}
    fn = dispatch.get(args.cmd)
    if not fn:
        ol.die("unknown subcommand '%s'" % args.cmd, ol.EX_USAGE)
    import types
    fn(types.SimpleNamespace(args=args, name=name, inst=inst,
                             engine=engine, user=user, fmt=fmt))


def _cmd_query(call):
    args, name, inst = call.args, call.name, call.inst
    engine, user, fmt = call.engine, call.user, call.fmt
    if engine == "mongodb":
        ol.die("query is for postgres/mysql; use 'mongo' for mongodb",
               ol.EX_USAGE)
    clean = ol.check_sql(args.sql, engine)
    stmt = ol.sql_add_limit(clean, args.limit, engine)
    _print_sql(AREA, name, inst, engine, stmt, user, fmt)


def _cmd_mongo(call):
    args, name, inst = call.args, call.name, call.inst
    engine, user = call.engine, call.user
    if engine != "mongodb":
        ol.die("mongo is for mongodb instances; use 'query' for SQL",
               ol.EX_USAGE)
    expr = ol.mongo_add_limit(ol.check_mongo(args.expression), args.limit)
    _emit(mongo_eval(AREA, name, inst, expr, user, args.database))


def _cmd_ping(call):
    name, inst = call.name, call.inst
    engine, user = call.engine, call.user
    if engine == "postgres":
        _emit(pg_query(
            AREA, name, inst,
            "select current_user, current_database(), version(), now()",
            user, "table"))
        _emit(pg_query(AREA, name, inst,
                       "show transaction_read_only", user, "table"))
    elif engine == "mysql":
        _emit(mysql_query(
            AREA, name, inst,
            "select current_user(), database(), version(), now()",
            user, "table"))
    else:
        _emit(mongo_eval(AREA, name, inst, "db.runCommand({ping: 1})",
                         user, inst.get("database")))


def _cmd_tables(call):
    args, name, inst = call.args, call.name, call.inst
    engine, user, fmt = call.engine, call.user, call.fmt
    if engine == "mongodb":
        out = mongo_eval(AREA, name, inst, "db.getCollectionNames()",
                         user, inst.get("database"))
        try:
            names = json.loads(out)
        except json.JSONDecodeError:
            _emit(out)
            return
        if args.filter:
            names = [x for x in names
                     if args.filter.lower() in x.lower()]
        for x in names:
            print(ol.redact(x))
        return
    schema = args.schema or ("public" if engine == "postgres"
                             else inst.get("database"))
    stmt = ("select table_schema, table_name from "
            "information_schema.tables where table_schema = '%s' "
            "order by table_name" % schema.replace("'", "''"))
    if args.filter:
        stmt = ("select * from (%s) t where table_name like '%%%s%%'"
                % (stmt, args.filter.replace("'", "''")))
    _print_sql(AREA, name, inst, engine, stmt, user, fmt)


def _cmd_columns_mongo(call):
    args, name, inst, user = call.args, call.name, call.inst, call.user
    import re as _re
    if not _re.fullmatch(r"[\w.\-]+", args.table, _re.ASCII):
        ol.die("invalid collection name %r" % args.table, ol.EX_USAGE)
    expr = ('db.getCollection("%s").aggregate('
            '[{$sample: {size: 20}}])' % args.table)
    out = mongo_eval(AREA, name, inst, expr, user,
                     inst.get("database"), fields=True)
    try:
        seen = json.loads(out)
    except json.JSONDecodeError:
        _emit(out)
        return
    for path in sorted(seen):
        types = seen[path]
        n = sum(types.values())
        print("%-50s %-25s (%d)" % (ol.redact(path),
                                    ", ".join(sorted(types)), n))
    if not seen:
        print("(collection empty or no documents sampled)")


def _cmd_columns(call):
    args, name, inst = call.args, call.name, call.inst
    engine, user, fmt = call.engine, call.user, call.fmt
    if engine == "mongodb":
        return _cmd_columns_mongo(call)
    schema = args.schema or ("public" if engine == "postgres"
                             else inst.get("database"))
    stmt = ("select column_name, data_type, is_nullable from "
            "information_schema.columns where table_schema = '%s' and "
            "table_name = '%s' order by ordinal_position"
            % (schema.replace("'", "''"),
               args.table.replace("'", "''")))
    _print_sql(AREA, name, inst, engine, stmt, user, fmt)


    ol.die("unknown subcommand '%s'" % args.cmd, ol.EX_USAGE)


if __name__ == "__main__":
    rc = ol.dispatch_config(AREA, sys.argv[1:], required_fields=REQUIRED,
                            client_clis=CLIENTS)
    if rc is not None:
        sys.exit(rc)
    try:
        main()
    except ol.OlError as e:
        ol.die(str(e), e.code)
    except KeyboardInterrupt:
        sys.exit(130)
