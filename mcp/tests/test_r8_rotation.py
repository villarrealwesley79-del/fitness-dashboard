"""Reviewer round 8: the EMFILE-safe rotate_log (mcp/server.py) — written from the requirements.

Requirements checked:
  * a full fd table at rotation time keeps the cap (truncate in place) and never loses the log path;
  * truncation happens only when no fd is free: one free fd is enough to rotate normally;
  * with free fds, rotation loses no line: <log>.1 and <log> hold a contiguous run of lines;
  * the 1 s thread bounds the file to cap + one interval of writes, with or without free fds.

Every probe runs in a subprocess because rotate_log rewires fds 1 and 2.
Run: MCP_DIR=<tree>/mcp <venv>/bin/python -m pytest -q -p no:cacheprovider <this file>
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

MCP_DIR = os.environ.get("MCP_DIR", str(Path(__file__).resolve().parents[1]))

PRELUDE = r"""
import json, os, resource, sys, threading, time
sys.path.insert(0, sys.argv[1])
log, cap, result = sys.argv[2], int(sys.argv[3]), sys.argv[4]
fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
os.dup2(fd, 1); os.dup2(fd, 2); os.close(fd)
import server
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (256, hard))
def fill(leave=0):
    held = []
    try:
        while True: held.append(os.open("/dev/null", os.O_RDONLY))
    except OSError: pass
    for _ in range(leave): os.close(held.pop())
    return held
def free(held):
    for h in held: os.close(h)
def done(**kw):
    kw.setdefault("fd2_size", os.fstat(2).st_size)
    kw.setdefault("log_exists", os.path.exists(log))
    kw.setdefault("same", os.path.exists(log) and os.path.samestat(os.fstat(2), os.stat(log)))
    with open(result, "w") as f: json.dump(kw, f)
"""


def probe(tmp_path, body, cap=20_000):
    log, result = tmp_path / "mcp.log", tmp_path / "result.json"
    out = subprocess.run([sys.executable, "-c", PRELUDE + body, MCP_DIR, str(log), str(cap), str(result)],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, (out.stdout[-800:], out.stderr[-800:], log.read_text()[-800:] if log.exists() else "")
    return log, json.loads(result.read_text())


def test_full_fd_table_truncates_in_place_keeps_path_and_previous_dot1_then_rotation_resumes(tmp_path):
    (tmp_path / "mcp.log.1").write_bytes(b"PREVIOUS-ROTATION\n")
    log, r = probe(tmp_path, r"""
os.write(2, b"x" * (cap + 1))
held = fill()
first = server.rotate_log(log, cap)
during = dict(size=os.fstat(2).st_size, exists=os.path.exists(log), new=os.path.exists(log + ".new"))
free(held)
os.write(2, b"after-truncate\n" + b"y" * cap)
second = server.rotate_log(log, cap)
os.write(2, b"fresh\n")
done(first=first, during=during, second=second)
""")
    assert r["first"] is False and r["during"]["size"] == 0 and r["during"]["exists"] and not r["during"]["new"], r
    assert r["second"] is True and r["log_exists"] and r["same"], r
    assert (tmp_path / "mcp.log.1").read_bytes().startswith(b"after-truncate\n")  # normal rotation resumed
    assert log.read_bytes() == b"fresh\n" and log.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "mcp.log.new").exists()


def test_previous_dot1_survives_a_truncation(tmp_path):
    (tmp_path / "mcp.log.1").write_bytes(b"PREVIOUS-ROTATION\n")
    log, r = probe(tmp_path, r"""
os.write(2, b"x" * (cap + 1))
held = fill()
first = server.rotate_log(log, cap)
free(held)
done(first=first)
""")
    assert r["first"] is False and r["fd2_size"] == 0 and r["same"], r
    assert (tmp_path / "mcp.log.1").read_bytes() == b"PREVIOUS-ROTATION\n"


def test_one_free_fd_is_enough_to_rotate_without_losing_a_line(tmp_path):
    log, r = probe(tmp_path, r"""
os.write(2, b"OWNER-LINE redirect_uri refused\n" + b"x" * cap)
held = fill(leave=1)
rotated = server.rotate_log(log, cap)
os.write(2, b"next\n")
free(held)
done(rotated=rotated)
""")
    assert r["rotated"] is True, r
    assert (tmp_path / "mcp.log.1").read_bytes().startswith(b"OWNER-LINE redirect_uri refused\n")
    assert log.read_bytes() == b"next\n"


def test_stale_dot_new_from_a_crash_is_reused_not_leaked(tmp_path):
    (tmp_path / "mcp.log.new").write_bytes(b"STALE")
    log, r = probe(tmp_path, r"""
os.write(2, b"x" * (cap + 1))
rotated = server.rotate_log(log, cap)
os.write(2, b"fresh\n")
done(rotated=rotated)
""")
    assert r["rotated"] is True and log.read_bytes() == b"fresh\n" and not (tmp_path / "mcp.log.new").exists()


THREAD_RUN = r"""
full_fds = sys.argv[5] == "emfile"
rate, secs, line_len = 400_000, 6.0, 100   # 400 KB/s of numbered lines for 6 s
threading.Thread(target=server.rotate_log_forever, args=(log, cap), daemon=True).start()   # default 1 s interval
held = fill() if full_fds else []
stop, max_size, missing, samples, n = False, 0, 0, 0, 0
def sampler():
    global max_size, missing, samples
    while not stop:
        max_size = max(max_size, os.fstat(2).st_size)
        missing += not os.path.exists(log); samples += 1
        time.sleep(0.0005)
t = threading.Thread(target=sampler); t.start()
start = time.time()
while time.time() - start < secs:
    batch = b"".join(b"L%09d " % (n + i) + b"p" * (line_len - 12) + b"\n" for i in range(400))
    os.write(2, batch); n += 400
    time.sleep(max(0, (n * line_len / rate) - (time.time() - start)))
stop = True; t.join()
free(held)
done(max_size=max_size, missing=missing, samples=samples, written=n, rate=rate)
"""


def thread_probe(tmp_path, mode, cap):
    log, result = tmp_path / "mcp.log", tmp_path / "result.json"
    out = subprocess.run([sys.executable, "-c", PRELUDE + THREAD_RUN, MCP_DIR, str(log), str(cap), str(result), mode],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    return log, json.loads(result.read_text())


def numbers(path):
    return [int(m) for m in re.findall(rb"^L(\d{9}) ", path.read_bytes(), re.M)] if path.exists() else []


def test_thread_bounds_log_with_a_full_fd_table(tmp_path):
    cap = 300_000
    log, r = thread_probe(tmp_path, "emfile", cap)
    one_interval = r["rate"] * 1.0
    assert r["missing"] == 0 and r["samples"] > 1000, r
    assert r["max_size"] <= cap + one_interval * 1.25, r  # 25% slack for scheduling jitter
    print("emfile", r)


def test_thread_with_free_fds_rotates_and_keeps_a_contiguous_run_of_lines(tmp_path):
    cap = 300_000
    log, r = thread_probe(tmp_path, "normal", cap)
    assert r["max_size"] <= cap + r["rate"] * 1.25, r
    old, new = numbers(tmp_path / "mcp.log.1"), numbers(log)
    assert old, len(old)  # the last rotation may land just before the writer stops, leaving the new file empty
    run = old + new
    assert run == list(range(run[0], run[0] + len(run))), "a line went missing across rotation"
    assert run[-1] == r["written"] - 1
    print("normal", r, "lines kept", len(run))

