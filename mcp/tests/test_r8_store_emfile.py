"""Reviewer round 8: does the OAuth store cap hold when the minute's check runs with a full fd table?

Requirement (acceptance 1): the store stays under its cap whatever an unauthenticated client sends.
An anonymous client can fill the fd table (connections that never finish a request head are not
counted by limit_concurrency and have no timeout), so the check must not read "no files" as "not full".

Every probe runs in a subprocess because it lowers RLIMIT_NOFILE and fills the fd table.
Run: MCP_DIR=<tree>/mcp <venv>/bin/python -m pytest -q -p no:cacheprovider <this file>
"""
import json
import os
import subprocess
from pathlib import Path
import sys

MCP_DIR = os.environ.get("MCP_DIR", str(Path(__file__).resolve().parents[1]))

PRELUDE = r"""
import json, logging, os, resource, sys, time, types
mcp, tmp, result = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path[:0] = [mcp, mcp + "/tests"]
logging.basicConfig(level=logging.WARNING, stream=open(os.path.join(tmp, "log.txt"), "w"), format="%(name)s %(message)s")
import server
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (256, hard))
def fill():
    held = []
    try:
        while True: held.append(os.open("/dev/null", os.O_RDONLY))
    except OSError: pass
    return held
def free(held):
    for h in held: os.close(h)
def count(root):
    return sum(len(n) for _, _, n in os.walk(root))
root = os.path.join(tmp, "store"); os.makedirs(root)
for i in range(6):
    open(os.path.join(root, "f%d" % i), "wb").write(b"x")
def done(**kw):
    with open(result, "w") as f: json.dump(kw, f)
"""


def probe(tmp_path, body):
    result = tmp_path / "result.json"
    out = subprocess.run([sys.executable, "-c", PRELUDE + body, MCP_DIR, str(tmp_path), str(result)],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, (out.stdout[-800:], out.stderr[-1500:])
    return json.loads(result.read_text()), (tmp_path / "log.txt").read_text()


def test_store_check_with_a_full_fd_table_keeps_a_full_store_full(tmp_path):
    r, log = probe(tmp_path, r"""
guard = server.LogRejectedRedirects(None, store=root, store_max_files=5)
first = guard._store_is_full()
guard.store_checked -= 61
held = fill()
during = guard._store_is_full()
free(held)
after = guard._store_is_full()   # cached for the next 60 s
done(first=first, during=during, after=after)
""")
    print(r, log)
    assert r["first"] is True
    assert r["during"] is True and r["after"] is True, (r, log)


def test_real_app_full_store_does_not_reopen_after_a_check_under_emfile(tmp_path):
    r, log = probe(tmp_path, r"""
import hashlib, httpx2
from cryptography.fernet import Fernet
from starlette.testclient import TestClient
from test_server import CLIENT_SECRET, OWNER_ID, READ_TOKEN, SIGNING_KEY
offset = [0.0]
real = time
server.time = types.SimpleNamespace(monotonic=lambda: real.monotonic() + offset[0], time=real.time, sleep=real.sleep)
cfg = server.Config(public_url="https://frankbox.example.ts.net:8443/mcp", github_client_id="gh-client-id",
    github_client_secret=CLIENT_SECRET, owner_github_id=OWNER_ID, jwt_signing_key=SIGNING_KEY, storage_dir=root,
    storage_key=Fernet.generate_key().decode(), read_token=READ_TOKEN, storage_max_files=5)
app, _ = server.build_app(cfg, github=httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(401))),
                          dashboard_transport=httpx2.MockTransport(lambda r: httpx2.Response(200, json={})))
reg = {"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"], "token_endpoint_auth_method": "none",
       "client_name": "n" * 8000}
codes = []
with TestClient(app, base_url="https://frankbox.example.ts.net:8443", follow_redirects=False) as c:
    codes.append(c.post("/register", json=reg).status_code)       # store over cap: 503
    before = count(root)
    offset[0] += 61                                                 # the next minute's check is due
    held = fill()
    try:
        codes.append(c.post("/register", json=reg).status_code)   # the check runs with no free fd
    except Exception as e:
        codes.append(type(e).__name__)
    free(held)
    for _ in range(5):
        codes.append(c.post("/register", json=reg).status_code)
    after = count(root)
done(codes=codes, before=before, after=after)
""")
    print(r, log)
    assert r["codes"][0] == 503
    assert r["after"] == r["before"], ("store grew past its cap", r, log)
    assert r["codes"][2:] == [503] * 5, (r, log)
