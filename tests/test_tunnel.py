from __future__ import annotations

import json
import subprocess
import sys
import time

import helpers


def _tunnel():
    return helpers.load_module("tunnel_mod",
                               "skills/observe-lens-db/scripts/tunnel.py")


def _write_tunnel_config(tmp_path):
    db = tmp_path / "db"
    db.mkdir(parents=True, exist_ok=True)
    (db / "config.json").write_text(json.dumps({
        "instances": {},
        "tunnels": {"t1": {"ssh_host": "bastion-dev", "forwards": []}},
    }), encoding="utf-8")


def _sleeper():
    return subprocess.Popen([sys.executable, "-c",
                             "import time; time.sleep(60)"])


def _wait_exit(proc, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.1)
    return False


def test_process_name_of_self_is_not_ssh():
    import os
    t = _tunnel()
    assert t._process_name(os.getpid())
    assert not t._is_ssh(os.getpid())


def test_stop_refuses_non_ssh_pid(tmp_path, capsys):
    t = _tunnel()
    _write_tunnel_config(tmp_path)
    proc = _sleeper()
    try:
        _, pidf = t._paths("t1")
        pidf.write_text(str(proc.pid))
        t.cmd_stop("t1")
        assert "not an ssh process" in capsys.readouterr().out
        assert not pidf.exists()
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait()


def test_stop_kills_ssh_pid(tmp_path, monkeypatch, capsys):
    t = _tunnel()
    _write_tunnel_config(tmp_path)
    monkeypatch.setattr(t, "_is_ssh", lambda pid: True)
    proc = _sleeper()
    try:
        _, pidf = t._paths("t1")
        pidf.write_text(str(proc.pid))
        t.cmd_stop("t1")
        assert "stopped" in capsys.readouterr().out
        assert not pidf.exists()
        assert _wait_exit(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def test_stop_without_pid_file(tmp_path, capsys):
    t = _tunnel()
    _write_tunnel_config(tmp_path)
    t.cmd_stop("t1")
    assert "no pid file" in capsys.readouterr().out
