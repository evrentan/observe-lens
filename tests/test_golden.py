"""Replay tests/fixtures/guard_golden.json: every recorded guard output
must be identical after refactors. Regenerate with
tools/make_guard_golden.py."""
from __future__ import annotations

import json
from datetime import datetime as _dt, timezone as _tz
from pathlib import Path

import helpers

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "guard_golden.json"


class TestGolden:
    def test_replay(self, monkeypatch):
        ol = helpers.common()
        pl = helpers.pipelines()
        kr = helpers.kube_ro()
        cd = helpers.cd()
        tl = helpers.telemetry()

        class FrozenDT(_dt):
            @classmethod
            def now(cls, tz=None):
                base = _dt(2026, 1, 15, 9, 40, 0, tzinfo=_tz.utc)
                return base.astimezone(tz) if tz else base
        monkeypatch.setattr(ol, "datetime", FrozenDT)

        fns = {
            "check_sql": ol.check_sql,
            "sql_add_limit": ol.sql_add_limit,
            "_sql_strip": ol._sql_strip,
            "check_mongo": ol.check_mongo,
            "mongo_add_limit": ol.mongo_add_limit,
            "redact": ol.redact,
            "normalize_api_path": ol.normalize_api_path,
            "kube_decide": kr.decide,
            "jenkins_allowed": pl.jenkins_get_allowed,
            "cd_allowed": cd.cd_get_allowed,
            "telemetry_allowed": tl.telemetry_get_allowed,
            "parse_time": ol.parse_time,
        }
        import base64

        def dec(v):
            return json.loads(base64.b64decode(v).decode("utf-8"))

        cases = [(c["fn"], [dec(a) for a in c["in_b64"]], dec(c["out_b64"]))
                 for c in json.loads(FIXTURE.read_text())]
        bad = 0
        for name, args, want in cases:
            try:
                v = fns[name](*args)
                if isinstance(v, tuple):
                    v = list(v)
                got = {"v": v}
            except Exception as e:  # noqa: BLE001
                got = {"e": "%s: %s" % (type(e).__name__, e)}
            if got != want:
                bad += 1
                if bad <= 10:
                    print("\nMISMATCH %s %r\n  want %r\n  got  %r"
                          % (name, args, want, got))
        assert bad == 0
