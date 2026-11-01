#!/usr/bin/env python3
"""observe-lens tunnel: start/check/stop SSH port forwards from the db
config's 'tunnels' map (ssh -N -L)."""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ol_common as ol

AREA = "db"


def _tunnels():
    cfg = ol.load_config(AREA)
    return cfg.get("tunnels") or {}


def _paths(name):
    d = ol.tmp_dir(AREA)
    return d / ("tunnel-%s.log" % name), d / ("tunnel-%s.pid" % name)


def _port_open(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except OSError:
        return False


def _argv(t):
    argv = ["ssh", "-N",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "BatchMode=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3"]
    if t.get("identity_file"):
        argv += ["-i", os.path.expanduser(t["identity_file"])]
    if t.get("port"):
        argv += ["-p", str(t["port"])]
    for f in t.get("forwards") or []:
        argv += ["-L", "%s:%s:%s" % (f["local_port"], f["remote_host"],
                                     f["remote_port"])]
    argv += list(t.get("ssh_args") or [])
    argv.append(t["ssh_host"])
    return argv


def _pick(name):
    tunnels = _tunnels()
    if name:
        if name not in tunnels:
            ol.die("no tunnel named '%s'. Configured: %s"
                   % (name, ", ".join(sorted(tunnels)) or "(none)"),
                   ol.EX_USAGE)
        return name, tunnels[name]
    if len(tunnels) == 1:
        n = next(iter(tunnels))
        return n, tunnels[n]
    ol.die("several tunnels configured, name one. Configured: %s"
           % (", ".join(sorted(tunnels)) or "(none)"), ol.EX_USAGE)


def cmd_status(name):
    tunnels = _tunnels()
    names = [name] if name else sorted(tunnels)
    if not names:
        print("no tunnels configured")
        return
    for n in names:
        t = tunnels.get(n)
        if not t:
            print("%s: not defined" % n)
            continue
        _, pidf = _paths(n)
        ports = [f.get("local_port") for f in t.get("forwards") or []]
        up = all(_port_open(p) for p in ports) if ports else False
        pid = pidf.read_text().strip() if pidf.exists() else "-"
        print("%s: %s (pid %s, forwards %s)"
              % (n, "up" if up else "down", pid,
                 ", ".join(str(p) for p in ports)))


def cmd_start(name):
    n, t = _pick(name)
    ports = [f.get("local_port") for f in t.get("forwards") or []]
    if ports and all(_port_open(p) for p in ports):
        print("%s: already up (ports %s accepting)" % (n, ports))
        return
    if not shutil.which("ssh"):
        ol.die("ssh not found on PATH — install an OpenSSH client (built in "
               "on macOS/Linux, Windows 10+ optional feature)")
    log, pidf = _paths(n)
    argv = _argv(t)
    logf = open(log, "ab")
    kwargs = {"stdout": logf, "stderr": subprocess.STDOUT,
              "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                   | subprocess.CREATE_NO_WINDOW)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **kwargs)
    pidf.write_text(str(proc.pid))
    if _tunnel_wait(proc, ports):
        print("%s: up (pid %d, log %s)" % (n, proc.pid, log))
        return
    _tunnel_fail(n, proc, log)


def _tunnel_wait(proc, ports):
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        if ports and all(_port_open(p) for p in ports):
            return True
        time.sleep(0.4)
    return False


def _tunnel_fail(n, proc, log):
    tail = ""
    try:
        tail = "\n".join(log.read_text(errors="replace").splitlines()[-15:])
    except OSError:
        pass
    low = tail.lower()
    if "publickey" in low or "permission denied" in low:
        hint = ("hint: SSH needs key auth — add your key to the server and "
                "ssh-agent (ssh-add / Add-SSHKey on Windows)")
    elif "address already in use" in low:
        hint = "hint: a local port is already in use; check 'tunnel.py status'"
    elif "timed out" in low or "timeout" in low:
        hint = "hint: host unreachable or ssh timed out"
    else:
        hint = ""
    try:
        proc.terminate()
    except OSError:
        pass
    ol.die("tunnel '%s' did not come up.\n%s\n%s" % (n, tail, hint).strip(),
           ol.EX_HTTP)


def _process_name(pid):
    """Executable name of a running pid, or '' if unknown or not running."""
    if os.name == "nt":
        argv = ["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"]
    else:
        argv = ["ps", "-p", str(pid), "-o", "comm="]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    line = (out.stdout or "").strip().splitlines()
    if out.returncode != 0 or not line:
        return ""
    first = line[0].split('","')[0].strip('"') if os.name == "nt" else line[0]
    return os.path.basename(first.strip()).lower()


def _is_ssh(pid):
    """Only kill pids that are still an ssh client, so a recycled pid
    belonging to an unrelated process is never signalled."""
    return _process_name(pid) in ("ssh", "ssh.exe")


def cmd_stop(name):
    n, _ = _pick(name)
    _, pidf = _paths(n)
    if not pidf.exists():
        print("%s: no pid file; not started by this tool" % n)
        return
    try:
        pid = int(pidf.read_text().strip())
    except ValueError:
        pidf.unlink()
        ol.die("corrupt pid file %s removed" % pidf)
    if not _is_ssh(pid):
        try:
            pidf.unlink()
        except OSError:
            pass
        print("%s: pid %d is not an ssh process — removed stale pid file "
              "(not killed)" % (n, pid))
        return
    try:
        os.kill(pid, signal.SIGTERM)
        print("%s: stopped (pid %d)" % (n, pid))
    except OSError as e:
        print("%s: pid %d not running (%s)" % (n, pid, e))
    try:
        pidf.unlink()
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser(prog="tunnel.py")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("status")
    p.add_argument("name", nargs="?")
    p = sub.add_parser("start")
    p.add_argument("name")
    p = sub.add_parser("stop")
    p.add_argument("name")
    args = ap.parse_args()
    if not args.cmd:
        ol.die("usage: tunnel.py status|start|stop [name]", ol.EX_USAGE)
    {"status": cmd_status, "start": cmd_start,
     "stop": cmd_stop}[args.cmd](args.name)


if __name__ == "__main__":
    try:
        main()
    except ol.OlError as e:
        ol.die(str(e), e.code)
    except KeyboardInterrupt:
        sys.exit(130)
