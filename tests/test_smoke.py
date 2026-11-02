"""End-to-end smoke tests: local http.server mocks for Jenkins, GitHub,
Argo CD and Grafana; each script run as a subprocess."""
from __future__ import annotations

import http.server
import json
import os
import pytest
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills"

# recorded request headers (Authorization on redirect must NOT leak)
SEEN_AUTH_ON_BLOB = []
REQUEST_COUNT = [0]


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def _text(self, text, code=200):
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def _redirect(self, url):
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def do_GET(self):
        REQUEST_COUNT[0] += 1
        p = urllib.parse.urlsplit(self.path).path
        r = (self._jenkins(p) or self._github(p) or self._argo(p)
             or self._grafana(p))
        if r is None:
            return self._json({"error": "not mocked: %s" % p}, 404)
        return r

    def _jenkins(self, p):
        if p == "/me/api/json":
            return self._json({"fullName": "ci-user", "id": "ci-user"})
        if p == "/job/TEAM/job/api/api/json":
            return self._json({"builds": [{"number": 812},
                                          {"number": 811}]})
        if p == "/job/TEAM/job/api/812/api/json":
            return self._json({
                "number": 812, "result": "FAILURE", "building": False,
                "timestamp": 1768470000000, "duration": 61000,
                "url": "https://jenkins.example.com/job/TEAM/job/api/812/",
                "actions": [{"causes": [{"shortDescription":
                                         "Started by user dev"}]},
                            {"parameters": [{"name": "ENV",
                                             "value": "dev"}]}],
                "changeSets": [{"items": [{"commitId": "3f2a9c1"}]}],
                "artifacts": [{"relativePath": "report.zip",
                               "fileName": "report.zip"}]})
        if p == "/job/TEAM/job/api/812/consoleText":
            return self._text("line1\nERROR: broken\nline3\n")
        if p == "/job/TEAM/job/api/812/artifact/report.zip":
            return self._text("PK-fake-artifact")
        return None

    def _github(self, p):
        if p == "/user":
            return self._json({"login": "octo"})
        if p == "/repos/acme/shop/actions/runs":
            return self._json({"workflow_runs": [
                {"id": 123, "status": "completed", "conclusion": "failure",
                 "head_branch": "main", "display_title": "ci"}]})
        if p == "/repos/acme/shop/actions/runs/123":
            return self._json({"id": 123, "name": "ci", "status": "completed",
                               "conclusion": "failure", "head_branch": "main",
                               "head_sha": "3f2a9c1", "run_attempt": 1,
                               "html_url": "https://github.com/x"})
        if p == "/repos/acme/shop/actions/runs/123/jobs":
            return self._json({"jobs": [
                {"id": 900, "name": "test", "conclusion": "failure",
                 "steps": [{"name": "pytest", "conclusion": "failure"}]}]})
        if p == "/repos/acme/shop/actions/jobs/900/logs":
            # simulate GitHub's cross-host redirect to a blob store
            return self._redirect("/blob-storage/joblog.txt")
        if p == "/blob-storage/joblog.txt":
            auth = self.headers.get("Authorization")
            SEEN_AUTH_ON_BLOB.append(auth)
            return self._text("2026-01-15T09:41:07.000Z failed step\n")
        if p == "/repos/acme/shop/actions/runs/123/artifacts":
            return self._json({"artifacts": []})
        return None

    def _argo(self, p):
        if p == "/api/v1/session/userinfo":
            return self._json({"username": "investigator"})
        if p == "/api/v1/applications":
            return self._json({"items": [
                {"metadata": {"name": "shop-api"},
                 "status": {"sync": {"status": "Synced",
                                     "revision": "abc123def456"},
                            "health": {"status": "Healthy"}}}]})
        if p == "/api/v1/applications/shop-api":
            return self._json({
                "metadata": {"name": "shop-api"},
                "spec": {"project": "shop",
                         "source": {"repoURL": "https://git.example.com/cfg",
                                    "path": "api",
                                    "targetRevision": "main"}},
                "status": {
                    "sync": {"status": "Synced", "revision": "abc123def456"},
                    "health": {"status": "Healthy"},
                    "summary": {"images": ["api:1.42.0"]},
                    "history": [{"id": 3, "revision": "abc123def456",
                                 "deployedAt": "2026-01-15T09:46:02Z"}],
                    "resources": [{"kind": "Pod", "name": "api-1",
                                   "namespace": "shop",
                                   "status": "Synced",
                                   "health": {"status": "Healthy"}}]}})
        if p == "/api/v1/applications/shop-api/events":
            return self._json({"items": []})
        return None

    def _grafana(self, p):
        if p == "/api/health":
            return self._json({"database": "ok"})
        if p == "/api/user":
            return self._json({"login": "svc-observe", "orgId": 1})
        if p == "/api/datasources":
            return self._json([
                {"uid": "lok1", "type": "loki", "name": "Loki",
                 "isDefault": True}])
        if p == "/api/search":
            return self._json([{"title": "API overview",
                                "url": "/d/api-overview"}])
        if p == "/api/datasources/proxy/uid/lok1/loki/api/v1/query_range":
            return self._json({"status": "success", "data": {
                "resultType": "streams", "result": [{
                    "stream": {"namespace": "shop", "app": "api"},
                    "values": [["1768470072000000000",
                                "{\"message\": \"HTTP 500 calling payments\"}"]]}]}})
        if p == "/api/datasources/proxy/uid/lok1/loki/api/v1/labels":
            return self._json({"status": "success",
                               "data": ["namespace", "app"]})
        if p == "/api/prometheus/grafana/api/v1/alerts":
            return self._json({"status": "success", "data": {"alerts": [
                {"state": "firing", "labels": {"alertname": "HighErrors"},
                 "annotations": {"summary": "many 5xx"}}]}})
        if p == "/api/alertmanager/grafana/api/v2/alerts":
            return self._json([])
        return None


class SmokeBase:
    server = None

    @classmethod
    def setup_class(cls):
        cls.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()
        cls.cfg = tempfile.TemporaryDirectory()

    @classmethod
    def teardown_class(cls):
        cls.server.shutdown()
        cls.cfg.cleanup()

    def run_script(self, script, *argv, env=None):
        e = dict(os.environ)
        e["OBSERVE_LENS_CONFIG_DIR"] = self.cfg.name
        if env:
            e.update(env)
        return subprocess.run(
            [sys.executable, str(SCRIPTS / script)] + [str(a) for a in argv],
            capture_output=True, text=True, env=e, timeout=60)

    def write_cfg(self, area, cfg):
        d = Path(self.cfg.name) / area
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text(json.dumps(cfg))


class TestJenkins(SmokeBase):
    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls.write_cfg(cls, "pipelines", {
            "default": "ci",
            "instances": {"ci": {"provider": "jenkins", "url": cls.base}}})
        cls.env = {"OBSERVE_LENS_PIPELINES_CI_USERNAME": "u",
                   "OBSERVE_LENS_PIPELINES_CI_TOKEN": "t"}

    def test_whoami(self):
        r = self.run_script("observe-lens-pipelines/scripts/pipelines.py",
                            "whoami", env=self.env)
        assert r.returncode == 0
        assert "ci-user" in r.stdout

    def test_run(self):
        r = self.run_script("observe-lens-pipelines/scripts/pipelines.py",
                            "run", "TEAM/api", "812", env=self.env)
        assert r.returncode == 0
        assert "FAILURE" in r.stdout
        assert "3f2a9c1" in r.stdout
        assert "ENV" in r.stdout

    def test_run_url_input(self):
        r = self.run_script(
            "observe-lens-pipelines/scripts/pipelines.py",
            "run", "%s/view/All/job/TEAM/job/api/812/" % self.base, "",
            env=self.env)
        assert r.returncode == 0
        assert "812" in r.stdout

    def test_log(self):
        r = self.run_script("observe-lens-pipelines/scripts/pipelines.py",
                            "log", "TEAM/api", "812", env=self.env)
        assert r.returncode == 0
        assert "3 lines" in r.stdout


class TestGithub(SmokeBase):
    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls.write_cfg(cls, "pipelines", {
            "default": "gh",
            "instances": {"gh": {"provider": "github", "url": cls.base,
                                 "repo": "acme/shop",
                                 "web_url": "http://127.0.0.1:%d" % cls.port}}})
        cls.env = {"OBSERVE_LENS_PIPELINES_GH_TOKEN": "tok"}

    def test_whoami(self):
        r = self.run_script("observe-lens-pipelines/scripts/pipelines.py",
                            "whoami", env=self.env)
        assert r.returncode == 0
        assert "octo" in r.stdout

    def test_run(self):
        r = self.run_script("observe-lens-pipelines/scripts/pipelines.py",
                            "run", "ci.yml", "123", env=self.env)
        assert r.returncode == 0
        assert "failure" in r.stdout
        assert "pytest" in r.stdout

    def test_log_redirect_no_auth_leak(self):
        r = self.run_script("observe-lens-pipelines/scripts/pipelines.py",
                            "log", "ci.yml", "123", env=self.env)
        assert r.returncode == 0
        assert "failed step" in open(
            r.stdout.split()[0], encoding="utf-8").read()
        assert SEEN_AUTH_ON_BLOB
        assert all(a is None for a in SEEN_AUTH_ON_BLOB)


class TestArgo(SmokeBase):
    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls.write_cfg(cls, "cd", {
            "default": "argo",
            "instances": {"argo": {"provider": "argocd", "url": cls.base}}})
        cls.env = {"OBSERVE_LENS_CD_ARGO_TOKEN": "t"}

    def test_whoami(self):
        r = self.run_script("observe-lens-cd/scripts/cd.py", "whoami",
                            env=self.env)
        assert r.returncode == 0
        assert "investigator" in r.stdout

    def test_app(self):
        r = self.run_script("observe-lens-cd/scripts/cd.py", "app",
                            "shop-api", env=self.env)
        assert r.returncode == 0
        assert "api:1.42.0" in r.stdout
        assert "abc123def456" in r.stdout

    def test_apps(self):
        r = self.run_script("observe-lens-cd/scripts/cd.py", "apps",
                            env=self.env)
        assert "shop-api" in r.stdout


class TestGrafana(SmokeBase):
    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls.write_cfg(cls, "telemetry", {
            "default": "gf",
            "instances": {"gf": {"provider": "grafana", "url": cls.base,
                                 "org_id": 1}}})
        cls.env = {"OBSERVE_LENS_TELEMETRY_GF_TOKEN": "t"}

    def test_whoami(self):
        r = self.run_script("observe-lens-telemetry/scripts/telemetry.py",
                            "whoami", env=self.env)
        assert r.returncode == 0
        assert "svc-observe" in r.stdout

    def test_logs(self):
        r = self.run_script("observe-lens-telemetry/scripts/telemetry.py",
                            "logs", '{namespace="shop"} |= "error"',
                            "--from", "now-1h", env=self.env)
        assert r.returncode == 0
        assert "HTTP 500 calling payments" in r.stdout
        assert "shop/api" in r.stdout

    def test_labels(self):
        r = self.run_script("observe-lens-telemetry/scripts/telemetry.py",
                            "labels", env=self.env)
        assert r.returncode == 0
        assert "namespace" in r.stdout

    def test_alerts(self):
        r = self.run_script("observe-lens-telemetry/scripts/telemetry.py",
                            "alerts", "--state", "firing", env=self.env)
        assert r.returncode == 0
        assert "HighErrors" in r.stdout


class TestConfigListNoSecrets(SmokeBase):
    def test_no_secret_values(self):
        self.write_cfg("cd", {
            "default": "a",
            "instances": {"a": {"provider": "argocd",
                                "url": "https://argocd.example.com"}}})
        sd = Path(self.cfg.name) / "cd" / "secrets"
        sd.mkdir(parents=True)
        (sd / "a.json").write_text(json.dumps({"token": "SUPERSECRET123"}))
        r = self.run_script("observe-lens-cd/scripts/cd.py", "config", "list")
        assert r.returncode == 0
        assert "SUPERSECRET123" not in r.stdout
        assert "SUPERSECRET123" not in r.stderr
        assert "file" in r.stdout


class TestProdFlag(SmokeBase):
    """Every area script must refuse prod instances without --prod, before
    any network/client call."""

    def _cfg_prod(self, area, inst):
        d = Path(self.cfg.name) / area
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text(json.dumps(
            {"default": "p", "instances": {"p": inst}}))

    def _run(self, script, *argv, env=None):
        return self.run_script(script, *argv, env=env)

    def test_pipelines_refuses_without_flag(self):
        base = "http://127.0.0.1:%d" % self.port
        self._cfg_prod("pipelines",
                       {"provider": "jenkins", "url": base,
                        "environment": "prod"})
        n0 = REQUEST_COUNT[0]
        r = self._run("observe-lens-pipelines/scripts/pipelines.py",
                      "--instance", "p", "whoami",
                      env={"OBSERVE_LENS_PIPELINES_P_USERNAME": "u",
                           "OBSERVE_LENS_PIPELINES_P_TOKEN": "t"})
        assert r.returncode == 5
        assert "--prod" in r.stderr
        assert REQUEST_COUNT[0] == n0  # no request reached the server
        r = self._run("observe-lens-pipelines/scripts/pipelines.py",
                      "--instance", "p", "--prod", "whoami",
                      env={"OBSERVE_LENS_PIPELINES_P_USERNAME": "u",
                           "OBSERVE_LENS_PIPELINES_P_TOKEN": "t"})
        assert r.returncode == 0
        assert "ci-user" in r.stdout

    def test_pipelines_nonprod_ok_without_flag(self):
        base = "http://127.0.0.1:%d" % self.port
        self._cfg_prod("pipelines",
                       {"provider": "jenkins", "url": base})
        r = self._run("observe-lens-pipelines/scripts/pipelines.py",
                      "--instance", "p", "whoami",
                      env={"OBSERVE_LENS_PIPELINES_P_USERNAME": "u",
                           "OBSERVE_LENS_PIPELINES_P_TOKEN": "t"})
        assert r.returncode == 0

    def test_cd_refuses_without_flag(self):
        base = "http://127.0.0.1:%d" % self.port
        self._cfg_prod("cd", {"provider": "argocd", "url": base,
                              "environment": "prod"})
        n0 = REQUEST_COUNT[0]
        r = self._run("observe-lens-cd/scripts/cd.py",
                      "--instance", "p", "whoami",
                      env={"OBSERVE_LENS_CD_P_TOKEN": "t"})
        assert r.returncode == 5
        assert "--prod" in r.stderr
        assert REQUEST_COUNT[0] == n0
        r = self._run("observe-lens-cd/scripts/cd.py",
                      "--instance", "p", "--prod", "whoami",
                      env={"OBSERVE_LENS_CD_P_TOKEN": "t"})
        assert r.returncode == 0
        assert "investigator" in r.stdout

    def test_telemetry_refuses_without_flag(self):
        base = "http://127.0.0.1:%d" % self.port
        self._cfg_prod("telemetry",
                       {"provider": "grafana", "url": base,
                        "environment": "prod"})
        n0 = REQUEST_COUNT[0]
        r = self._run("observe-lens-telemetry/scripts/telemetry.py",
                      "--instance", "p", "whoami",
                      env={"OBSERVE_LENS_TELEMETRY_P_TOKEN": "t"})
        assert r.returncode == 5
        assert "--prod" in r.stderr
        assert REQUEST_COUNT[0] == n0
        r = self._run("observe-lens-telemetry/scripts/telemetry.py",
                      "--instance", "p", "--prod", "whoami",
                      env={"OBSERVE_LENS_TELEMETRY_P_TOKEN": "t"})
        assert r.returncode == 0

    def test_config_subcommands_unaffected(self):
        base = "http://127.0.0.1:%d" % self.port
        self._cfg_prod("pipelines",
                       {"provider": "jenkins", "url": base,
                        "environment": "prod"})
        r = self._run("observe-lens-pipelines/scripts/pipelines.py",
                      "config", "list")
        assert r.returncode == 0
        r = self._run("observe-lens-pipelines/scripts/pipelines.py",
                      "config", "check", "--instance", "p")
        # check runs (missing secrets -> rc 2 is fine); no --prod refusal
        assert r.returncode != 5
        assert "is production" not in r.stderr

    def _fake_psql(self, tmp_path, body):
        fake = tmp_path / "bin"
        fake.mkdir()
        marker = tmp_path / "psql-called"
        if "%s" in body:
            body = body % marker
        (fake / "psql").write_text("#!/bin/sh\n" + body)
        (fake / "psql").chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join([str(fake), env.get("PATH", "")])
        env["OBSERVE_LENS_CONFIG_DIR"] = self.cfg.name
        return env, marker

    def test_db_refuses_without_flag(self):
        # refusal happens before any client lookup; no fake needed
        self._cfg_prod("db", {"engine": "postgres", "host": "x",
                              "database": "d", "user": "u",
                              "environment": "prod"})
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "observe-lens-db/scripts/db.py"),
             "--instance", "p", "ping"],
            capture_output=True, text=True,
            env=dict(os.environ, OBSERVE_LENS_CONFIG_DIR=self.cfg.name),
            timeout=60)
        assert r.returncode == 5
        assert "--prod" in r.stderr
        # --prod permits the run but never relaxes the SQL guard
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "observe-lens-db/scripts/db.py"),
             "--instance", "p", "--prod", "query", "delete from t"],
            capture_output=True, text=True,
            env=dict(os.environ, OBSERVE_LENS_CONFIG_DIR=self.cfg.name),
            timeout=60)
        assert r.returncode == 5
        assert "refused" in r.stderr

    @pytest.mark.skipif(sys.platform == "win32",
                        reason="POSIX shell fake client")
    def test_db_prod_read_with_flag(self, tmp_path):
        env, marker = self._fake_psql(
            tmp_path, "echo called >> %s\necho OK\n")
        self._cfg_prod("db", {"engine": "postgres", "host": "x",
                              "database": "d", "user": "u",
                              "environment": "prod"})
        # without --prod the client is never invoked
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "observe-lens-db/scripts/db.py"),
             "--instance", "p", "ping"],
            capture_output=True, text=True, env=env, timeout=60)
        assert r.returncode == 5
        assert not marker.exists()
        # read on prod with --prod works through the fake client
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "observe-lens-db/scripts/db.py"),
             "--instance", "p", "--prod", "query", "select 1"],
            capture_output=True, text=True, env=env, timeout=60)
        assert r.returncode == 0
        assert marker.exists()

    @pytest.mark.skipif(sys.platform == "win32",
                        reason="POSIX shell fake client")
    def test_db_columns_exits_zero(self, tmp_path):
        env, _ = self._fake_psql(tmp_path, "echo col1\n")
        self._cfg_prod("db", {"engine": "postgres", "host": "x",
                              "database": "d", "user": "u"})
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "observe-lens-db/scripts/db.py"),
             "--instance", "p", "columns", "users"],
            capture_output=True, text=True, env=env, timeout=60)
        assert r.returncode == 0

    def _no_cli_env(self, tmp_path):
        """PATH with nothing on it: kube_ro dies EX_CONFIG before exec."""
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        return dict(os.environ, PATH=str(empty),
                    OBSERVE_LENS_CONFIG_DIR=self.cfg.name)

    def test_kube_ro_refuses_prod_without_flag(self, tmp_path):
        self._cfg_prod("cd", {"provider": "kubernetes",
                              "environment": "prod"})
        r = self._run("observe-lens-cd/scripts/kube_ro.py",
                      "--instance", "p", "kubectl", "get", "pods")
        assert r.returncode == 5
        assert "--prod" in r.stderr
        # --prod permits the run but never relaxes decide()
        r = self._run("observe-lens-cd/scripts/kube_ro.py",
                      "--instance", "p", "--prod", "kubectl",
                      "delete", "pod", "x")
        assert r.returncode == 5
        assert "refused" in (r.stderr + r.stdout)
        # no --instance: unchanged behaviour (no config instance, no check).
        # PATH has no kubectl -> script exits fast, never runs a real CLI.
        r = subprocess.run(
            [sys.executable,
             str(SCRIPTS / "observe-lens-cd/scripts/kube_ro.py"),
             "kubectl", "get", "pods"],
            capture_output=True, text=True, timeout=60,
            env=self._no_cli_env(tmp_path))
        assert "is production; ask the user" not in r.stderr

    def test_kube_ro_prod_flag_after_tool(self, tmp_path):
        self._cfg_prod("cd", {"provider": "kubernetes",
                              "environment": "prod"})
        env = self._no_cli_env(tmp_path)
        script = str(SCRIPTS / "observe-lens-cd/scripts/kube_ro.py")
        # --prod after the tool name is pulled out of REMAINDER args:
        # the prod refusal is not raised (kubectl itself may be absent)
        r = subprocess.run([sys.executable, script, "--instance", "p",
                            "kubectl", "get", "pods", "--prod"],
                           capture_output=True, text=True, timeout=60,
                           env=env)
        assert "is production; ask the user" not in r.stderr
        # and it never relaxes decide(): write still refused
        r = subprocess.run([sys.executable, script, "--instance", "p",
                            "kubectl", "delete", "pod", "x", "--prod"],
                           capture_output=True, text=True, timeout=60,
                           env=env)
        assert r.returncode == 5
        assert "refused" in (r.stderr + r.stdout)
