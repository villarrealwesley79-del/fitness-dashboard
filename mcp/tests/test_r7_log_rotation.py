"""Reviewer round 7: try to exceed the fd-level log cap (mcp/server.py rotate_log).

Every probe runs in a subprocess because rotate_log rewires fds 1 and 2.
Run: MCP_DIR=<worktree>/mcp <venv>/bin/python -m pytest -q <this file>
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

MCP_DIR = os.environ.get("MCP_DIR", str(Path(__file__).resolve().parents[1]))

PRELUDE = r"""
import json, os, resource, sys, threading, time
sys.path.insert(0, sys.argv[1])
log, cap, result = sys.argv[2], int(sys.argv[3]), sys.argv[4]
fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
os.dup2(fd, 1); os.dup2(fd, 2); os.close(fd)
import server
def done(**kw):
    kw.setdefault("fd2_size", os.fstat(2).st_size)
    kw.setdefault("log_exists", os.path.exists(log))
    with open(result, "w") as f: json.dump(kw, f)
"""


def probe(tmp_path, body, cap=20_000, log_name="mcp.log"):
    log, result = tmp_path / log_name, tmp_path / "result.json"
    out = subprocess.run([sys.executable, "-c", PRELUDE + body, MCP_DIR, str(log), str(cap), str(result)],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, (out.stdout[-800:], out.stderr[-800:], log.read_text()[-800:] if log.exists() else "")
    return log, json.loads(result.read_text())


def test_emfile_during_rotation_must_not_disable_the_cap(tmp_path):
    """A full fd table (e.g. ~250 held anonymous connections under launchd's 256 maxfiles) at the
    moment of rotation must not turn rotation off for the life of the process."""
    cap = 20_000
    log, r = probe(tmp_path, cap=cap, body=r"""
os.write(2, b"x" * (cap + 1))
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (64, hard))
held = []
try:
    while True: held.append(os.open("/dev/null", os.O_RDONLY))
except OSError: pass
try:
    server.rotate_log(log, cap); first = "ok"
except OSError as e:
    first = "OSError errno %d" % e.errno
for h in held: os.close(h)
# the attack ends; the process keeps logging and the thread keeps ticking (OSError is swallowed there)
for _ in range(10):
    os.write(2, b"y" * (cap // 2))
    try: server.rotate_log(log, cap)
    except OSError: pass
done(first=first)
""")
    assert r["log_exists"], r
    assert r["fd2_size"] <= 2 * cap, r


def test_rotation_moves_old_file_and_points_both_fds_at_a_fresh_private_file(tmp_path):
    log, r = probe(tmp_path, r"""
os.write(2, b"old-stderr\n"); os.write(1, b"old-stdout\n"); os.write(2, b"x" * cap)
rotated = server.rotate_log(log, cap)
os.write(2, b"new-stderr\n"); os.write(1, b"new-stdout\n")
print("print-after", flush=True)
done(rotated=rotated)
""")
    old, new = (tmp_path / "mcp.log.1").read_bytes(), log.read_bytes()
    assert r["rotated"] is True
    assert old.startswith(b"old-stderr\nold-stdout\n") and b"new" not in old
    assert new == b"new-stderr\nnew-stdout\nprint-after\n"
    assert log.stat().st_mode & 0o777 == 0o600


def test_concurrent_writers_lose_or_split_no_line_across_one_rotation(tmp_path):
    """Owner-relevant lines written while rotating land intact in either file."""
    log, r = probe(tmp_path, r"""
import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
lg = logging.getLogger("owner")
stop = False
def writer(tag):
    i = 0
    while not stop:
        lg.error("%s-%06d-%s", tag, i, "p" * 120); i += 1
    writers_n[tag] = i
writers_n = {}
ts = [threading.Thread(target=writer, args=(t,)) for t in ("a", "b", "c")]
for t in ts: t.start()
rot = 0
deadline = time.time() + 3
while time.time() < deadline and rot < 1:
    rot += server.rotate_log(log, cap)
stop = True
for t in ts: t.join()
done(rot=rot, n=writers_n)
""", cap=200_000)
    assert r["rot"] == 1, r
    import re
    lines = (tmp_path / "mcp.log.1").read_text().splitlines() + log.read_text().splitlines()
    good = [l for l in lines if re.fullmatch(r"[abc]-\d{6}-p{120}", l)]
    seen = {(l[0], int(l[2:8])) for l in good}
    total = sum(r["n"].values())
    # macOS dup2 is not atomic against a concurrent write(2): a writer can get EBADF, and logging's
    # handleError then adds "--- Logging error ---" noise. The in-flight record is lost (not retried).
    # None may be duplicated or split, and at most 5% may be lost (a few in-flight records at one rotation).
    assert len(seen) == len(good) and total - len(good) <= 0.05 * total, (len(seen), len(good), r["n"])
    noise = [l for l in lines if not re.fullmatch(r"[abc]-\d{6}-p{120}", l)]
    assert all(l.startswith(("--- Logging error ---", "Traceback", "  ", "OSError", "Call stack", "Message", "Arguments")) for l in noise), noise[:5]
    print("noise lines from the dup2 race:", len(noise))


def test_symlink_at_log_path_rotates_the_link_and_stays_bounded(tmp_path):
    target = tmp_path / "real-target.log"
    target.write_bytes(b"")
    (tmp_path / "link.log").symlink_to(target)
    log, r = probe(tmp_path, r"""
rots = 0
for _ in range(6):
    os.write(2, b"z" * (cap + 1))
    rots += server.rotate_log(log, cap)
done(rots=rots, link_is_symlink=os.path.islink(log), dot1_is_symlink=os.path.islink(log + ".1"))
""", log_name="link.log")
    # the owner's target is left behind (abandoned, not deleted or written past the cap); later rotations are normal
    assert r["rots"] == 6 and not r["link_is_symlink"] and not r["dot1_is_symlink"], r
    assert target.exists() and target.stat().st_size == 20_001
    assert log.stat().st_size == 0 and (tmp_path / "link.log.1").stat().st_size == 20_001


def test_unicode_log_path(tmp_path):
    log, r = probe(tmp_path, r"""
os.write(2, b"u" * (cap + 1))
done(rot=server.rotate_log(log, cap))
""", log_name="fitness-mcp-éñ日本.log")
    assert r["rot"] is True and r["fd2_size"] == 0 and r["log_exists"]


def test_rotation_never_renames_when_fd2_is_not_a_file(tmp_path):
    """stderr on /dev/null or a pipe (plist without StandardErrorPath): nothing is renamed."""
    other = tmp_path / "other.log"
    other.write_bytes(b"x" * 50_000)
    code = (f"import os,sys; sys.path.insert(0,{MCP_DIR!r}); import server; "
            f"print(server.rotate_log({str(other)!r}, 10))")
    out = subprocess.run([sys.executable, "-c", code], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, timeout=60)
    assert out.stdout.strip() == "False" and other.stat().st_size == 50_000
    assert not (tmp_path / "other.log.1").exists()
