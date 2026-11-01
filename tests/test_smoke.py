"""End-to-end smoke tests: local http.server mocks for Jenkins, GitHub,
Argo CD and Grafana; each script run as a subprocess."""
from __future__ import annotations

import http.server
import json
import os
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
