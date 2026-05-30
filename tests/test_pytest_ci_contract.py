from __future__ import annotations

import stat
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


def test_pre_push_hook_runs_pytest_suite() -> None:
    hook = ROOT / ".githooks" / "pre-push"
    source = hook.read_text()

    assert hook.stat().st_mode & stat.S_IXUSR
    assert "FITNESS_SKIP_PRE_PUSH_TESTS" in source
    assert "-m pytest -q" in source


def test_hook_installer_copies_all_committed_hooks() -> None:
    installer = (ROOT / "scripts" / "install-worktree-guard.sh").read_text()

    assert 'for hook in "${ROOT}/.githooks/"*' in installer
    assert '$(basename "${hook}")' in installer
