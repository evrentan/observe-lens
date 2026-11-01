from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import pytest
from pathlib import Path

import helpers

ol = helpers.common()


class EnvGuard:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch, tmp_path):
        for k in [k for k in os.environ if k.startswith("OBSERVE_LENS_") or k == "XDG_CONFIG_HOME"]:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("OBSERVE_LENS_CONFIG_DIR", str(tmp_path))
        self._mp = monkeypatch
        self.tmp = tmp_path


class TestConfigRoot(EnvGuard):
    def test_env_override(self):
        assert ol.config_root() == self.tmp

    def test_xdg(self):
        self._mp.delenv("OBSERVE_LENS_CONFIG_DIR", raising=False)
        self._mp.setenv("XDG_CONFIG_HOME", "/tmp/xdg-test")
        assert ol.config_root() == Path("/tmp/xdg-test") / "observe-lens"

    def test_home_default(self):
        self._mp.delenv("OBSERVE_LENS_CONFIG_DIR", raising=False)
        assert ol.config_root() == Path.home() / ".config" / "observe-lens"


class TestInstancePick(EnvGuard):
    CFG = {"default": "b",
           "instances": {"a": {"provider": "jenkins", "url": "x"},
                         "b": {"provider": "github", "url": "y"}}}

    def test_flag(self):
        n, _ = ol.pick_instance("pipelines", self.CFG, "a")
        assert n == "a"

    def test_env(self):
        self._mp.setenv("OBSERVE_LENS_PIPELINES_INSTANCE", "a")
        n, _ = ol.pick_instance("pipelines", self.CFG)
        assert n == "a"

    def test_default(self):
        n, _ = ol.pick_instance("pipelines", self.CFG)
        assert n == "b"

    def test_single(self):
        cfg = {"instances": {"only": {"provider": "jenkins"}}}
        n, _ = ol.pick_instance("pipelines", cfg)
        assert n == "only"

    def test_ambiguous(self):
        cfg = {"instances": {"a": {}, "b": {}}}
        with pytest.raises(ol.OlError):
            ol.pick_instance("pipelines", cfg)


class TestSecrets(EnvGuard):
    def test_env_naming_and_order(self):
        inst = {"secret_env": {"token": "MY_TOK"},
                "secret_command": {"token": ["false"]}}
        # (1) standard env wins
        self._mp.setenv("OBSERVE_LENS_PIPELINES_CI_MAIN_TOKEN", "e1")
        self._mp.setenv("MY_TOK", "e2")
        assert ol.resolve_secret("pipelines", "ci-main", inst, "token") == "e1"
        # (2) secret_env next
        self._mp.delenv("OBSERVE_LENS_PIPELINES_CI_MAIN_TOKEN", raising=False)
        assert ol.resolve_secret("pipelines", "ci-main", inst, "token") == "e2"
        # (4) file last
        self._mp.delenv("MY_TOK", raising=False)
        sd = ol.secrets_dir("pipelines")
        sd.mkdir(parents=True)
        (sd / "ci-main.json").write_text(json.dumps({"token": "file1"}))
        assert ol.resolve_secret("pipelines", "ci-main", inst, "token") == "file1"
        # non-alnum instance name normalisation
        self._mp.setenv("OBSERVE_LENS_PIPELINES_CI_MAIN2_TOKEN", "x")
        assert ol.resolve_secret("pipelines", "ci.main2", {}, "token") == "x"

    def test_secret_command(self):
        inst = {"secret_command": {"token": [
            os.environ.get("PYTHON", "python3"), "-c", "print('cmd-tok')"]}}
        assert ol.resolve_secret("pipelines", "x", inst, "token") == "cmd-tok"

    @pytest.mark.skipif(os.name == "nt", reason="POSIX only")
    def test_permission_warning(self):
        import io, contextlib
        sd = ol.secrets_dir("db")
        sd.mkdir(parents=True)
        f = sd / "i.json"
        f.write_text("{}")
        os.chmod(f, 0o644)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ol.warn_secret_permissions("db", "i")
        assert "chmod" in err.getvalue()
        os.chmod(f, 0o600)


class TestTime(EnvGuard):
    def test_iso(self):
        assert abs(ol.parse_time("2026-01-15T09:40:00Z") - 1768470000.0) < 1
        # naive = UTC
        assert ol.parse_time("2026-01-15T09:40:00") == ol.parse_time("2026-01-15T09:40:00Z")
        # offset
        assert ol.parse_time("2026-01-15T10:40:00+01:00") == ol.parse_time("2026-01-15T09:40:00Z")

    def test_epoch(self):
        assert ol.parse_time("1768470000") == 1768470000.0
        assert ol.parse_time("1768470000000") == 1768470000.0

    def test_relative(self):
        import time
        assert abs(ol.parse_time("now") - time.time()) < 5
        assert abs(ol.parse_time("now-15m") - (time.time() - 900)) < 5
        assert abs(ol.parse_time("now-2d") - (time.time() - 2 * 86400)) < 5

    def test_fmt(self):
        assert ol.fmt_ts(1768470000) == "2026-01-15 09:40:00"


class TestSqlGuard(EnvGuard):
    ACCEPT = [
        "select * from t",
        "select 1",
        "SELECT a, b FROM t WHERE x = 'drop table x'",
        "select 'drop table x'",
        "select 'it''s fine' from t",
        'select "weird col" from t',
        "with c as (select 1) select * from c",
        "show tables",
        "describe orders",
        "desc orders",
        "explain select * from t",
        "explain (costs off) select * from t",
        "table orders",
        "values (1,2)",
        "select * from t;   ",
        "-- a comment mentioning delete\nselect 1",
        "/* insert into x */ select 1",
        "select $$not a dollar$$ from t",
        "select * from t limit 10",
        "select * from t where d = 'a;b'",
    ]
    REFUSE = [
        "drop table t",
        "insert into t values (1)",
        "update t set a=1",
        "delete from t",
        "merge into t using s",
        "truncate t",
        "alter table t add column x int",
        "create table t (a int)",
        "grant select on t to u",
        "revoke all on t from u",
        "copy t to stdout",
        "call my_proc()",
        "do $$ begin end $$",
        "lock table t",
        "vacuum t",
        "select * into newt from t",
        "select * from t into outfile '/tmp/x'",
        "select * from t for update",
        "select * from t for share",
        "select * from t for no key update",
        "select * from t for key share",
        "select setval('seq', 1)",
        "select nextval('seq')",
        "select set_config('x','y',false)",
        "select pg_terminate_backend(1)",
        "select pg_cancel_backend(1)",
        "select pg_reload_conf()",
        "select lo_import('/etc/passwd')",
        "select lo_export(1,'/tmp/x')",
        "select pg_read_file('/etc/passwd')",
        "select pg_read_binary_file('/x')",
        "select pg_ls_dir('.')",
        "select * from dblink('x','select 1') t(a int)",
        "select load_file('/etc/passwd')",
        "select sleep(10)",
        "select pg_sleep(10)",
        "select * from t; drop table t",
        "select 1; select 2",
        "explain analyze select * from t",
        "explain (analyze, costs off) select * from t",
        "with d as (delete from t returning *) select * from d",
        "select * from t \\! ls",
        "with x as (update t set a=1) select 1",
        "select /*!40101 1+1*/",
        "select * from dblink_exec('x','y') t(a int)",
        "select 'a\\'' , 1",
        "select pg_switch_wal()",
        "select pg_rotate_logfile()",
        "select pg_create_restore_point('x')",
        "select pg_promote()",
        "select pg_advisory_lock(1)",
        "select lo_put(1,0,'x')",
        "select get_lock('x', 5)",
        "select release_lock('x')",
        "select release_all_locks()",
        "select benchmark(10, md5('x'))",
        "select pg_notify('c','m')",
    ]

    def test_accept(self):
        for s in self.ACCEPT:
            try:
                ol.check_sql(s)
            except ol.OlError as e:
                self.fail("accepted-statement refused: %r (%s)" % (s, e))

    def test_refuse(self):
        for s in self.REFUSE:
            with pytest.raises(ol.OlError):
                ol.check_sql(s)

    def test_limit_wrapping(self):
        assert ol.sql_add_limit(ol.check_sql("select * from t"), 5) == "SELECT * FROM (\nselect * from t\n) AS _ol LIMIT 5"
        assert ol.sql_add_limit(ol.check_sql("select * from t limit 10"), 5) == "select * from t limit 10"
        assert ol.sql_add_limit(ol.check_sql("show tables"), 5) == "show tables"
        # limit inside a subquery doesn't count
        assert "LIMIT 5" in ol.sql_add_limit(ol.check_sql(
                          "select * from (select * from t limit 1) x"), 5)


    def test_literal_preserved_and_dialect(self):
        stmt = "select * from t where id = 'A-123'"
        assert ol.check_sql(stmt) == stmt
        # dollar-quoting is postgres-only
        ol.check_sql("select $a$ delete $a$", "postgres")
        with pytest.raises(ol.OlError):
            ol.check_sql("select $a$ delete $a$", "mysql")

    def test_trailing_semicolon_and_comment(self):
        stmt = ol.check_sql("select 1; -- c")
        assert stmt == "select 1"
        assert ol.sql_add_limit(stmt, 7) == "SELECT * FROM (\nselect 1\n) AS _ol LIMIT 7"


class TestMongoGuard(EnvGuard):
    ACCEPT = [
        "db.orders.find({orderId: 'A'})",
        "db.orders.findOne({_id: 1})",
        "db.orders.find({}).sort({ts: -1}).limit(5)",
        "db.orders.countDocuments({status: 'X'})",
        "db.orders.estimatedDocumentCount()",
        "db.orders.distinct('status')",
        "db.orders.aggregate([{$match: {a: 1}}])",
        "db.orders.getIndexes()",
        "db.stats()",
        "db.getCollectionNames()",
        "db.getCollectionInfos()",
        'db.getCollection("orders").find({})',
        'db.x.find({a: ObjectId("65a1f2e3d4c5b6a7e8f9a0b1")})',
        'db.x.find({d: {$gte: ISODate("2026-01-01")}}).sort({d: -1}).limit(5)',
        'db.x.find({a: new Date("2026-01-01")})',
    ]
    REFUSE = [
        "db.orders.insert({a: 1})",
        "db.orders.update({}, {$set: {a: 1}})",
        "db.orders.deleteMany({})",
        "db.orders.remove({})",
        "db.orders.replaceOne({}, {})",
        "db.orders.drop()",
        "db.createCollection('x')",
        "db.orders.renameCollection('y')",
        "db.orders.bulkWrite([])",
        "db.orders.aggregate([{$out: 'x'}])",
        "db.orders.aggregate([{$merge: {into: 'x'}}])",
        "db.orders.find({$where: 'this.a'})",
        "db.eval('x')",
        "db.runCommand({ping: 1})",
        "db.adminCommand({x: 1})",
        "db.getSiblingDB('other').orders.find({})",
        "db.getMongo()",
        "connect('mongodb://x')",
        "db.orders.find({}); db.orders.drop()",
        "db.orders.find({}).forEach(function(d){print(d)})",
        "db.orders.find().mapReduce(function(){},function(){},'out')",
        "db.x.find(db.y.deleteMany({}))",
        'db.x.find()["constructor"]',
        "db.x.find({a: `tpl`})",
        "db.x.find({a: 1}).forEach(d => print(d))",
        "db.orders.find({a: 1})['x']",
        "db.x.find(({}))",
    ]

    def test_accept(self):
        for e in self.ACCEPT:
            try:
                ol.check_mongo(e)
            except ol.OlError as ex:
                self.fail("mongo expr refused: %r (%s)" % (e, ex))

    def test_refuse(self):
        for e in self.REFUSE:
            with pytest.raises(ol.OlError):
                ol.check_mongo(e)

    def test_limit(self):
        assert ol.mongo_add_limit("db.orders.find({})", 10) == "db.orders.find({}).limit(10)"
        assert ol.mongo_add_limit("db.orders.find({}).limit(3)", 10) == "db.orders.find({}).limit(3)"
        assert ol.mongo_add_limit("db.orders.countDocuments()", 5) == "db.orders.countDocuments()"


class TestKubeRo(EnvGuard):
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.k = helpers.kube_ro()

    def _assert_ok(self, tool, *args):
        ok, reason = self.k.decide(tool, list(args))
        assert ok, "%s %r refused: %s" % (tool, args, reason)

    def _assert_refused(self, tool, *args):
        ok, _ = self.k.decide(tool, list(args))
        assert not ok

    def test_kubectl_allow(self):
        for a in [("get", "pods", "-n", "x"), ("describe", "pod", "p", "-n", "x"),
                  ("logs", "p", "-n", "x"), ("events", "-n", "x"),
                  ("top", "pods"), ("explain", "pods"),
                  ("api-resources",), ("api-versions",), ("version",),
                  ("cluster-info",), ("rollout", "status", "deploy/x"),
                  ("rollout", "history", "deploy/x"), ("auth", "can-i", "get", "pods"),
                  ("config", "get-contexts"), ("config", "current-context"),
                  ("-n", "ns", "get", "pods"),
                  ("--context", "x", "get", "pods", "-n", "y")]:
            self._assert_ok("kubectl", *a)

    def test_kubectl_refuse(self):
        for a in [("get", "secrets"), ("get", "secret/x"),
                  ("get", "secrets.v1", "-n", "x"), ("get", "pods", "-w"),
                  ("logs", "p", "-f"), ("logs", "p", "--follow"),
                  ("config", "view"), ("config", "view", "--raw"),
                  ("exec", "p", "--", "ls"), ("cp", "p:/x", "y"),
                  ("port-forward", "p", "80"), ("proxy",), ("debug", "p"),
                  ("attach", "p"), ("delete", "pod", "p"),
                  ("cluster-info", "dump"), ("rollout", "restart", "deploy/x"),
                  ("apply", "-f", "x.yaml"), ("scale", "deploy/x", "--replicas=2"),
                  ("get", "pods", "--raw", "/api/v1/x"),
                  ("get", "all,secrets"), ("get", "pods,secret/x"),
                  ("get", "--server", "https://evil", "pods"),
                  ("get", "pods", "-v=9"), ("get", "pods", "-v", "9"),
                  ("get", "pods", "--v=6"), ("get", "pods", "-v9"),
                  ("--context", "x", "delete", "pod", "a"),
                  ("-n", "ns", "exec", "p", "--", "ls"),
                  ("get", "pods", "--token", "x"),
                  ("get", "pods", "--as", "admin"),
                  ("get", "pods", "--insecure-skip-tls-verify"),
                  ("get", "pods", "-shttps://evil")]:
            self._assert_refused("kubectl", *a)

    def test_helm(self):
        for a in [("list", "-A"), ("ls",), ("status", "r", "-n", "x"),
                  ("history", "r", "-n", "x"), ("hist", "r"), ("version",),
                  ("show", "chart", "repo/x"), ("show", "readme", "repo/x"),
                  ("get", "notes", "r"), ("get", "metadata", "r"),
                  ("--kube-context", "x", "status", "r")]:
            self._assert_ok("helm", *a)
        for a in [("get", "values", "r"), ("get", "manifest", "r"),
                  ("get", "all", "r"), ("get", "hooks", "r"),
                  ("template", "repo/x"), ("install", "x", "y"),
                  ("upgrade", "x", "y"), ("uninstall", "x"), ("rollback", "x", "1"),
                  ("show", "values", "repo/x"),
                  ("status", "r", "-o", "json"),
                  ("status", "r", "--output=yaml"),
                  ("status", "r", "-ojson"),
                  ("--kube-context", "x", "get", "secrets", "r")]:
            self._assert_refused("helm", *a)

    def test_flux(self):
        for a in [("get", "kustomizations", "-A"), ("tree", "kustomization", "x"),
                  ("stats",), ("logs",), ("version",), ("check",),
                  ("events",), ("trace", "kustomization", "x")]:
            self._assert_ok("flux", *a)
        for a in [("export", "kustomization", "x"), ("reconcile", "ks", "x"),
                  ("suspend", "ks", "x"), ("resume", "ks", "x"),
                  ("create", "source", "git", "x"), ("delete", "ks", "x"),
                  ("logs", "--follow"),
                  ("get", "ks", "--server", "https://evil"),
                  ("get", "ks", "--token", "x")]:
            self._assert_refused("flux", *a)


class TestUrlParsers(EnvGuard):
    def test_jenkins_job_path(self):
        p = helpers.pipelines()
        assert p.jenkins_job_path("TEAM/api") == "job/TEAM/job/api"
        assert p.jenkins_job_path("job/TEAM/job/api") == "job/TEAM/job/api"
        inst = {"url": "https://jenkins.example.com"}
        job, n = p.jenkins_parse_job_run(
            inst, "https://jenkins.example.com/view/All/job/TEAM/job/api/812/")
        assert (job, n) == ("job/TEAM/job/api", 812)
        job, n = p.jenkins_parse_job_run(
            inst, "https://jenkins.example.com/job/TEAM/job/api/812")
        assert (job, n) == ("job/TEAM/job/api", 812)
        job, n = p.jenkins_parse_job_run(inst, "TEAM/api")
        assert (job, n) == ("TEAM/api", None)

    def test_github_run_url(self):
        p = helpers.pipelines()
        repo, wf, rid, jid = p.gh_parse_run(
            None, "https://github.com/acme/shop/actions/runs/123/job/456")
        assert (repo, rid, jid) == ("acme/shop", "123", "456")
        repo, wf, rid, jid = p.gh_parse_run(
            "ci.yml", "https://github.com/acme/shop/actions/runs/123")
        assert (repo, rid) == ("acme/shop", "123")
        repo, wf, rid, jid = p.gh_parse_run("ci.yml", "999")
        assert (repo, wf, rid, jid) == (None, "ci.yml", "999", None)


class TestLogSummary(EnvGuard):
    def test_summary(self):
        import subprocess
        f = self.tmp / "log.txt"
        f.write_text("start\n[INFO] building\nERROR: thing broke\n" "Caused by: java.lang.RuntimeException\n" "exit code 1\ndone\n")
        out = subprocess.run(
            [sys.executable, str(helpers.ROOT / "skills/observe-lens-pipelines/scripts/log_summary.py"),
             str(f)], capture_output=True, text=True)
        assert out.returncode == 0
        assert "ERROR" in out.stdout
        assert "3|" in out.stdout


class TestTestReport(EnvGuard):
    def test_junit(self):
        tr = helpers.test_report()
        d = self.tmp
        (d / "junit.xml").write_text(
            '<testsuite tests="2">' '<testcase classname="T" name="ok"/>' '<testcase classname="T" name="bad">' '<failure message="boom">at T.bad(T.java:1)</failure>' '</testcase></testsuite>')
        tests = tr.summarize(str(d))
        assert len(tests) == 2
        assert [t for t in tests if t["status"] == "failure" ][0]["name"] == "T.bad"

    def test_allure_and_zip(self):
        import zipfile
        tr = helpers.test_report()
        d = self.tmp
        z = d / "r.zip"
        with zipfile.ZipFile(z, "w") as zz:
            zz.writestr("ab-result.json", json.dumps(
                {"name": "t1", "status": "broken",
                 "statusDetails": {"message": "npe",
                                   "trace": "at X.y(X.java:2)"}}))
            zz.writestr("cd-result.json", json.dumps(
                {"name": "t2", "status": "passed"}))
        tests = tr.summarize(str(z))
        assert len(tests) == 2
        assert [t for t in tests if t["status"] == "broken"
                ][0]["message"] == "npe"


class TestTraceTree(EnvGuard):
    OTLP = {"batches": [
        {"resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "api"}}]},
         "scopeSpans": [{"spans": [
             {"spanId": "aaaaaaaaaaaaaaaa", "name": "GET /orders",
              "startTimeUnixNano": "1000", "endTimeUnixNano": "300000000",
              "status": {"code": "STATUS_CODE_OK"}, "attributes": []},
             {"spanId": "bbbbbbbbbbbbbbbb",
              "parentSpanId": "aaaaaaaaaaaaaaaa", "name": "payments",
              "startTimeUnixNano": "50000000", "endTimeUnixNano": "250000000",
              "status": {"code": "STATUS_CODE_ERROR"},
              "attributes": [
                  {"key": "http.status_code",
                   "value": {"intValue": 500}},
                  {"key": "exception.message",
                   "value": {"stringValue": "conn refused"}}]}]}]}]}

    def test_tree(self):
        t = helpers.telemetry()
        spans = t.flatten_spans(self.OTLP)
        assert len(spans) == 2
        lines = t.render_trace_tree(spans)
        assert any("GET /orders" in l for l in lines)
        assert any("payments" in l and "ERROR" in l for l in lines)
        err = t.render_trace_tree(spans, errors_only=True)
        assert not any("GET /orders" in l for l in err)
        assert any("http.status_code=500" in l for l in err)


class TestArgParse(EnvGuard):
    """Global options must work before AND after the subcommand."""

    def test_pipelines(self):
        p = helpers.pipelines().build_parser()
        for argv in (["--repo", "a/b", "--instance", "x", "runs", "ci.yml"],
                     ["runs", "ci.yml", "--repo", "a/b", "--instance", "x"]):
            a = p.parse_args(argv)
            assert a.repo == "a/b"
            assert a.instance == "x"
            assert a.workflow == "ci.yml"

    def test_db(self):
        p = helpers.db().build_parser()
        for argv in (["--user", "u", "--instance", "x", "query", "select 1"],
                     ["query", "select 1", "--user", "u", "--instance", "x"]):
            a = p.parse_args(argv)
            assert a.user == "u"
            assert a.instance == "x"
            assert a.sql == "select 1"

    def test_cd(self):
        p = helpers.cd().build_parser()
        for argv in (["--instance", "x", "app", "api", "--json"],
                     ["app", "api", "--instance", "x", "--json"]):
            a = p.parse_args(argv)
            assert a.instance == "x"
            assert a.json

    def test_telemetry(self):
        p = helpers.telemetry().build_parser()
        for argv in (["--ds", "u1", "logs", "{x=''}", "--from", "now-5m"],
                     ["logs", "{x=''}", "--from", "now-5m", "--ds", "u1"]):
            a = p.parse_args(argv)
            assert a.ds == "u1"
            assert a.cmd == "logs"


class TestConfigDispatch(EnvGuard):
    def test_config_only_first_positional(self):
        assert ol.dispatch_config("cd", ["app", "config"]) is None
        assert ol.dispatch_config("db",
                                             ["columns", "config"]) is None
        assert ol.dispatch_config("pipelines",
                                             ["run", "config", "3"]) is None
        # 'config' as first positional runs (config path needs no files)
        assert ol.dispatch_config("cd", ["config", "path"]) == 0
        assert ol.dispatch_config("cd", ["--instance", "ci", "config", "path"]) == 0


class TestPluginManifests(EnvGuard):
    def test_validate_plugins_clean(self):
        vs = helpers.load_module(
            "validate_skills_mod", "tools/validate_skills.py")
        problems, versions = [], set()
        for skill_dir in sorted(p for p in vs.SKILLS.iterdir() if p.is_dir()):
            vs._validate_skill(skill_dir, problems, versions)
        assert len(versions) == 1
        vs.validate_plugins(problems, versions)
        assert problems == []

    def test_validate_plugins_catches_drift(self):
        vs = helpers.load_module(
            "validate_skills_mod", "tools/validate_skills.py")
        problems = []
        vs.validate_plugins(problems, {"9.9.9"})
        assert any("version" in p for p in problems)


class TestRedact(EnvGuard):
    CASES = [
        ("token " + "gh" + "p_" + "A" * 22 + " is bad", "gh" + "p_",
         "[REDACTED:github]"),
        ("x " + "github" + "_pat_" + "a" * 25 + " y", "github" + "_pat_",
         "[REDACTED:github]"),
        ("gl" + "sa_" + "b" * 25, "gl" + "sa_", "[REDACTED:grafana]"),
        ("gl" + "c_" + "b" * 25, "gl" + "c_", "[REDACTED:grafana]"),
        ("xox" + "b-1234567890-abc", "xox" + "b-", "[REDACTED:slack]"),
        ("AKIA" + "IOSFODNN7EXAMPLE", "AKIA", "[REDACTED:aws-key]"),
        ("h Authorization: Bearer " + "abc" * 8, "abc" * 8,
         "Bearer [REDACTED:bearer]"),
        ("Basic " + "dXNlcjpw" + "YXNzMTIzNDU2", "dXNlcjpwYXNzMTIzNDU2",
         "[REDACTED:basic]"),
        ("dsn https://user:s3cr3tPW@db.internal/db", "s3cr3tPW",
         "://user:[REDACTED:password]@"),
        ("cfg token=abcd1234 x", "abcd1234", "token=[REDACTED:value]"),
        ("password: 'hunter22'", "hunter22", "[REDACTED:value]"),
        ("eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJ" + "zdWIiOiIxIn0"
         + "." + "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
         "eyJ" + "hbGci", "[REDACTED:jwt]"),
        ("PGPASSWORD=abcd1234", "abcd1234", "PGPASSWORD=[REDACTED:value]"),
        ("DB_PASSWORD=x1234", "x1234", "DB_PASSWORD=[REDACTED:value]"),
        ("GITHUB_TOKEN=abcd1234", "abcd1234",
         "GITHUB_TOKEN=[REDACTED:value]"),
        ("AWS_SECRET_ACCESS_KEY=abcd1234", "abcd1234",
         "AWS_SECRET_ACCESS_KEY=[REDACTED:value]"),
        ("MYSQL_PWD=abcd1234", "abcd1234", "MYSQL_PWD=[REDACTED:value]"),
    ]

    def test_patterns(self):
        for text, needle, repl in self.CASES:
            out = ol.redact(text)
            assert repl in out
            assert needle not in out

    def test_private_key_block(self):
        text = ("pre\n-----BEGIN " + "RSA PRIVATE KEY-----\nMIIxyz\n" "-----END " + "RSA PRIVATE KEY-----\npost")
        out = ol.redact(text)
        assert "[REDACTED:private-key]" in out
        assert "post" in out
        assert "MIIxyz" not in out

    def test_normal_text_untouched(self):
        for text in ["commit 3f2a9c1d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b",
                     "id 550e8400-e29b-41d4-a716-446655440000",
                     "token_count=5",
                     "select * from t where status = 'FAILED'",
                     "requests_total{app='api'}"]:
            assert ol.redact(text) == text


class TestNeedsConfirm(EnvGuard):
    def test_needs_confirm(self):
        assert ol.needs_confirm({"environment": "prod"})
        assert ol.needs_confirm({"environment": "Production"})
        assert ol.needs_confirm({"environment": "prd"})
        assert ol.needs_confirm({"environment": "live"})
        assert not ol.needs_confirm({"environment": "dev"})
        assert not ol.needs_confirm({})
        assert ol.needs_confirm({"confirm": True,
                                          "environment": "dev"})
        assert not ol.needs_confirm({"confirm": False,
                                           "environment": "prod"})

    def _write(self, area, cfg):
        d = self.tmp / area
        d.mkdir(parents=True)
        (d / "config.json").write_text(json.dumps(cfg))

    def test_config_list_marks_confirm(self):
        import contextlib, io
        self._write("cd", {"default": "p",
                           "instances": {"p": {"provider": "argocd",
                                               "url": "x",
                                               "environment": "prod"}}})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ol.cmd_config_list("cd")
        assert "confirm=yes" in out.getvalue()

    def test_config_check_prod_warning(self):
        import contextlib, io
        self._write("cd", {"default": "argo-prod",
                           "instances": {"argo-prod": {
                               "provider": "argocd",
                               "url": "https://argocd.example.com"}}})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ol.cmd_config_check("cd")
        assert "looks like production" in out.getvalue()
        # environment set -> no warning
        cfg = json.loads((self.tmp / "cd" / "config.json").read_text())
        cfg["instances"]["argo-prod"]["environment"] = "prod"
        (self.tmp / "cd" / "config.json").write_text(
            json.dumps(cfg))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ol.cmd_config_check("cd")
        assert "looks like production" not in out.getvalue()


class TestGetAllowlists(EnvGuard):
    def test_jenkins(self):
        p = helpers.pipelines()
        for ok in ["job/x/api/json", "job/x/1/api/xml",
                   "job/x/1/consoleText",
                   "job/x/1/logText/progressiveText",
                   "job/x/1/artifact/report.zip",
                   "job/x/12/wfapi/describe"]:
            assert p.jenkins_get_allowed(ok)
        for bad in ["job/x/build?token=abc", "job/x/buildWithParameters",
                    "job/x/rebuild", "job/x/replay", "job/x/stop",
                    "job/x/doDelete", "scriptText", "safeRestart",
                    "job/x/api/json/../../scriptText",
                    "job/a/%2e%2e/build/api/json",
                    "job/a/build/api/json", "job/a/1/stop/api/json",
                    "job/a/1/rebuild/api/json", "safeRestart/api/json"]:
            assert not p.jenkins_get_allowed(bad)
        # a job literally named 'build' is fine
        assert p.jenkins_get_allowed("job/build/api/json")
        assert p.jenkins_get_allowed(
            "job/team/job/api/1/api/json")

    def test_cd(self):
        m = helpers.cd()
        for ok in ["api/v1/applications", "api/v1/applications/x",
                   "api/v1/applications/x/events?x=1"]:
            assert m.cd_get_allowed(ok)
        for bad in ["api/v1/applications/x?refresh=hard",
                    "api/v1/applications/x?refresh=normal",
                    "api/v1/applications/x?Refresh=hard",
                    "api/v1/applications/x?refresh%3Dhard",
                    "api/v2/applications", "api/version/../x",
                    "api/v1/%2e%2e/admin",
                    "account/settings"]:
            assert not m.cd_get_allowed(bad)

    def test_telemetry(self):
        m = helpers.telemetry()
        A = m.telemetry_get_allowed
        # grafana
        for ok in ["api/user", "api/datasources", "api/search?q=x",
                   "api/datasources/proxy/uid/a/loki/api/v1/query_range",
                   "api/datasources/proxy/uid/a/api/v1/query",
                   "api/datasources/proxy/uid/a/api/traces/t1"]:
            assert A("grafana", ok)
        # non-proxy api/ paths are all allowed per spec (GET-only reads)
        assert A("grafana", "api/admin/settings")
        for bad in ["api/datasources/proxy/uid/a/" "api/v1/admin/tsdb/snapshot",
                    "api/datasources/proxy/uid/a/flush",
                    "api/datasources/proxy/uid/a/ingester/shutdown",
                    "api/datasources/proxy/uid/a/shutdown",
                    "admin/orgs"]:
            assert not A("grafana", bad)
        # prometheus
        assert A("prometheus", "api/v1/query")
        assert not A("prometheus", "api/v1/admin/tsdb/snapshot")
        assert not A("prometheus", "api/v2/query")
        # loki
        assert A("loki", "loki/api/v1/query_range")
        assert not A("loki", "loki/api/v1/../flush")
        assert not A("loki", "loki/api/v1/%2e%2e/%2e%2e/flush")
        assert not A("loki", "loki/api/v1/%252e%252e/flush")
        assert not A("loki", "loki\\api\\v1\\flush")
        assert not A("loki", "flush")
        # tempo
        assert A("tempo", "api/traces/abc")
        assert A("tempo", "ready")
        assert not A("tempo", "api/v1/admin")
        assert not A("tempo", "shutdown")


class TestWindowCap(EnvGuard):
    def _args(self, frm=None, to=None, wide=False):
        import types
        return types.SimpleNamespace(
            **{"from": frm, "to": to, "wide": wide})

    def test_cap(self):
        m = helpers.telemetry()
        frm, to = m._window(self._args("now-1h"))
        assert to - frm < 7200
        # scripts load their own _ol_common copy; use its OlError class
        with pytest.raises(m.ol.OlError):
            m._window(self._args("now-2d"))
        frm, to = m._window(self._args("now-2d", wide=True))
        with pytest.raises(m.ol.OlError):
            m._window(self._args("now-8d"))
        with pytest.raises(m.ol.OlError):
            m._window(self._args("now-8d", wide=True))


class TestSafeDest(EnvGuard):
    def test_dest(self):
        base = ol.tmp_dir("pipelines").resolve()
        ok = Path(ol.safe_dest("pipelines", "report.zip"))
        assert str(ok).startswith(str(base))
        with pytest.raises(ol.OlError):
            ol.safe_dest("pipelines", "../x")
        with pytest.raises(ol.OlError):
            ol.safe_dest("pipelines", "/etc/passwd")


class TestZipLimits(EnvGuard):
    def test_big_member_skipped(self):
        import zipfile as zf
        tr = helpers.test_report()
        d = self.tmp
        z = d / "big.zip"
        with zf.ZipFile(z, "w") as zz:
            zz.writestr("a-result.json", json.dumps(
                {"name": "t", "status": "passed"}))
        # fake a huge member list
        real_zf = zf.ZipFile
        class FakeZip(real_zf):
            def infolist(self):
                infos = super().infolist()
                for i in infos:
                    i.file_size = 60 * 1024 * 1024
                return infos
        self._mp.setattr(tr.zipfile, "ZipFile", FakeZip)
        tests = tr.summarize(str(z))
        assert tests == []


class TestTmpDirPerms(EnvGuard):
    @pytest.mark.skipif(os.name == "nt", reason="POSIX only")
    def test_tmp_dir_0700(self):
        base = Path(tempfile.gettempdir()) / ("observe-lens-%d" % os.getuid())
        if base.exists():
            import shutil
            shutil.rmtree(base)
        d = ol.tmp_dir("pipelines")
        for p in (base, d):
            mode = stat.S_IMODE(os.lstat(p).st_mode)
            assert mode == 0o700

    @pytest.mark.skipif(os.name == "nt", reason="POSIX only")
    def test_symlink_base_refused(self):
        base = Path(tempfile.gettempdir()) / ("observe-lens-%d" % os.getuid())
        import shutil
        if base.exists() and not base.is_symlink():
            shutil.rmtree(base)
        if base.exists():
            base.unlink()
        target = self.tmp / "real"
        target.mkdir()
        base.symlink_to(target)
        try:
            with pytest.raises(ol.OlError):
                ol.tmp_dir("pipelines")
        finally:
            base.unlink()

    @pytest.mark.skipif(os.name == "nt", reason="POSIX only")
    def test_foreign_owner_refused(self):
        # uses self._mp (monkeypatch)
        base = Path(tempfile.gettempdir()) / ("observe-lens-%d" % os.getuid())
        if base.exists():
            import shutil
            shutil.rmtree(base)
        base.mkdir(0o700)
        try:
            real_lstat = os.lstat

            def fake_lstat(p, **_kw):
                st = real_lstat(p)
                if Path(p) == base:
                    return os.stat_result(
                        (st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
                         st.st_uid + 9999, st.st_gid, st.st_size,
                         st.st_atime, st.st_mtime, st.st_ctime))
                return st
            self._mp.setattr(os, "lstat", fake_lstat)
            with pytest.raises(ol.OlError):
                ol.tmp_dir("pipelines")
            self._mp.setattr(os, "lstat", real_lstat)
        finally:
            import shutil
            shutil.rmtree(base)


class TestPlainHttpWarning(EnvGuard):
    def test_warn(self):
        import contextlib, io
        name = "ci"
        inst = {"provider": "jenkins",
                "url": "http://jenkins.internal:8080"}
        self._mp.setenv("OBSERVE_LENS_PIPELINES_CI_TOKEN", "x")
        self._mp.setenv("OBSERVE_LENS_PIPELINES_CI_USERNAME", "u")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ol.warn_http_creds("pipelines", name, inst)
        assert "plain http" in err.getvalue()
        # https -> silent
        inst["url"] = "https://jenkins.internal:8080"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ol.warn_http_creds("pipelines", name, inst)
        assert err.getvalue() == ""
        # localhost http -> silent
        inst["url"] = "http://127.0.0.1:8080"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ol.warn_http_creds("pipelines", name, inst)
        assert err.getvalue() == ""
        # no credential resolved -> silent
        self._mp.delenv("OBSERVE_LENS_PIPELINES_CI_TOKEN", raising=False)
        self._mp.delenv("OBSERVE_LENS_PIPELINES_CI_USERNAME", raising=False)
        inst["url"] = "http://jenkins.internal:8080"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ol.warn_http_creds("pipelines", name, inst)
        assert err.getvalue() == ""


class TestConfigInit(EnvGuard):
    def test_init_writes_empty_config(self):
        ol.cmd_config_init("db")
        cfg = json.loads(ol.config_path("db").read_text())
        assert cfg == {"default": None, "instances": {}, "tunnels": {}}
        assert (ol.area_dir("db") / "config.example.json").exists()
        ol.cmd_config_init("cd")
        cfg = json.loads(ol.config_path("cd").read_text())
        assert "tunnels" not in cfg
        assert cfg["instances"] == {}

    @pytest.mark.skipif(os.name == "nt", reason="POSIX only")
    def test_secrets_dir_perms(self):
        ol.cmd_config_init("db")
        sd = ol.secrets_dir("db")
        assert stat.S_IMODE(os.stat(sd).st_mode) == 0o700

    @pytest.mark.skipif(os.name == "nt", reason="POSIX only")
    def test_ensure_secrets_dir_tightens(self):
        sd = ol.secrets_dir("db")
        sd.mkdir(parents=True)
        os.chmod(sd, 0o755)
        assert not ol._ensure_secrets_dir("db")
        assert stat.S_IMODE(os.stat(sd).st_mode) == 0o700


class TestTlsContext(EnvGuard):
    def test_default_verifies(self):
        import ssl
        ctx = ol.tls_context({})
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname

    def test_verify_tls_false_refused(self):
        with pytest.raises(ol.OlError) as cm:
            ol.tls_context({"verify_tls": False})
        assert "ca_file" in str(cm.value)

    def test_missing_ca_file_refused(self):
        with pytest.raises(ol.OlError):
            ol.tls_context({"ca_file": os.path.join(str(self.tmp), "nope.pem")})

    def test_no_unverified_context_in_source(self):
        src = (Path(__file__).resolve().parent.parent / "common" / "_ol_common.py").read_text(encoding="utf-8")
        assert "_create_unverified" + "_context" not in src

    def _check_with(self, extra, capsys):
        inst = {"provider": "prometheus", "url": "https://prom.example.com"}
        inst.update(extra)
        p = ol.config_path("telemetry")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"instances": {"p1": inst}}), encoding="utf-8")
        ok = ol.cmd_config_check("telemetry", None, {"prometheus": ["url"]}, {})
        return ok, capsys.readouterr().out

    def test_config_check_fails_on_verify_tls_false(self, capsys):
        ok, out = self._check_with({"verify_tls": False}, capsys)
        assert not ok
        assert "not supported" in out

    def test_config_check_fails_on_missing_ca_file(self, capsys):
        ok, out = self._check_with({"ca_file": str(self.tmp / "nope.pem")}, capsys)
        assert not ok
        assert "ca_file not found" in out

    def test_config_check_passes_with_existing_ca_file(self, capsys):
        ca = self.tmp / "ca.pem"
        ca.write_text("placeholder", encoding="utf-8")
        ok, _ = self._check_with({"ca_file": str(ca)}, capsys)
        assert ok
