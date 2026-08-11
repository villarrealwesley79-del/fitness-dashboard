from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pytest_config_registers_tests_and_allow_net_marker() -> None:
    config = (ROOT / "pyproject.toml").read_text()

    assert "[tool.pytest.ini_options]" in config
    assert 'testpaths = ["tests"]' in config
    assert "allow_net: allow live external network access" in config


def test_ci_workflow_runs_pytest_suite() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pytest.yml").read_text()

    assert "pull_request:" in workflow
    assert "push:" in workflow
    assert 'python-version: "3.11.7"' in workflow
    assert "python -m pytest -q" in workflow
    assert "concurrency:" in workflow
    assert "cancel-in-progress: true" in workflow
    assert "timeout-minutes: 10" in workflow


def test_pre_push_hook_runs_pytest_suite() -> None:
    hook = ROOT / ".githooks" / "pre-push"
    source = hook.read_text()

    assert hook.stat().st_mode & stat.S_IXUSR
    assert "FITNESS_SKIP_PRE_PUSH_TESTS" in source
    assert "-m pytest -q" in source


def test_pre_push_prefers_repo_virtualenv_over_path_python(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    bin_dir = tmp_path / "bin"
    marker = tmp_path / "python-used"
    (repo / "venv" / "bin").mkdir(parents=True)
    bin_dir.mkdir()

    repo_python = repo / "venv" / "bin" / "python"
    repo_python.write_text(f"#!/bin/sh\nprintf repo-venv > {marker}\n")
    repo_python.chmod(0o755)
    fake_git = bin_dir / "git"
    fake_git.write_text(f"#!/bin/sh\nprintf '%s\\n' {repo}\n")
    fake_git.chmod(0o755)
    path_python = bin_dir / "python"
    path_python.write_text(f"#!/bin/sh\nprintf path-python > {marker}\n")
    path_python.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:/bin:/usr/bin"
    result = subprocess.run(
        [str(ROOT / ".githooks" / "pre-push")],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text() == "repo-venv"


def test_hook_installer_copies_all_committed_hooks() -> None:
    installer = (ROOT / "scripts" / "install-worktree-guard.sh").read_text()

    assert 'for hook in "${ROOT}/.githooks/"*' in installer
    assert '$(basename "${hook}")' in installer


def test_shell_static_check_covers_operational_scripts() -> None:
    checker = ROOT / "scripts" / "check-shell-scripts.sh"

    completed = subprocess.run(
        ["bash", str(checker)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [
        "bash -n: scripts/install-launchd-agents.sh",
        "bash -n: scripts/check-apple-health-staleness.sh",
        "bash -n: scripts/worktree-server-guard.sh",
        "bash -n: scripts/install-worktree-guard.sh",
        "bash -n: .githooks/pre-push",
        "bash -n: .githooks/post-checkout",
        "bash -n: support/self_test.sh",
        "Shell static checks passed (7 files).",
    ]
