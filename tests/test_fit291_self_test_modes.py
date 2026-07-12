import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELF_TEST = ROOT / "support" / "self_test.sh"


def _run(*args):
    env = os.environ.copy()
    env["COOKIE"] = "fit291-secret-cookie"
    return subprocess.run(
        ["bash", str(SELF_TEST), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_route_only(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl_log = tmp_path / "curl.log"
    curl = bin_dir / "curl"
    curl.write_text(
        """#!/usr/bin/env bash
set -e
printf '%s\\n' "$*" >> "$CURL_LOG"
output=/dev/null
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then output="$2"; shift 2; continue; fi
  shift
done
printf '%s' '{"reachable":false,"model_loaded":false,"checks":[]}' > "$output"
printf '200'
""",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    lsof = bin_dir / "lsof"
    lsof.write_text(
        """#!/usr/bin/env bash
if [[ "$1" == -tiTCP:* ]]; then echo 1234; exit 0; fi
printf 'COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\\n'
printf 'python 1234 user 1u REG 1 0 1 /tmp/app.log\\n'
""",
        encoding="utf-8",
    )
    lsof.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "COOKIE": "fit291-secret-cookie",
        "CURL_LOG": str(curl_log),
        "PATH": f"{bin_dir}:{env['PATH']}",
    })
    result = subprocess.run(
        ["bash", str(SELF_TEST), "--route-only"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, curl_log.read_text(encoding="utf-8")


def test_help_documents_route_only_without_printing_secrets():
    result = _run("--help")

    assert result.returncode == 0
    assert "--route-only" in result.stdout
    assert "fit291-secret-cookie" not in result.stdout + result.stderr


def test_unknown_argument_fails_without_printing_secrets():
    result = _run("--unknown")

    assert result.returncode == 2
    assert "unknown argument: --unknown" in result.stderr
    assert "fit291-secret-cookie" not in result.stdout + result.stderr


def test_route_only_skips_mutation_and_downgrades_ai_health(tmp_path):
    result, curl_calls = _run_route_only(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "SKIP: strict AI health validation (--route-only)" in result.stdout
    assert "SKIP: rejected workout POST (--route-only)" in result.stdout
    assert "/api/complete-workout" not in curl_calls
    assert " -d " not in curl_calls
    assert "fit291-secret-cookie" not in result.stdout + result.stderr
