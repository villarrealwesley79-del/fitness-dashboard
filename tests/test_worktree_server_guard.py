import shutil
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(args, cwd, **kwargs):
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **kwargs,
    )


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("import time\ntime.sleep(60)\n")
    (repo / "README.md").write_text("test repo\n")
    _run(["git", "init", "-b", "main"], repo, check=True)
    _run(["git", "config", "user.email", "test@example.com"], repo, check=True)
    _run(["git", "config", "user.name", "Test User"], repo, check=True)
    _run(["git", "add", "."], repo, check=True)
    _run(["git", "commit", "-m", "init"], repo, check=True)
    _run(["git", "branch", "other"], repo, check=True)
    scripts = repo / "scripts"
    scripts.mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "worktree-server-guard.sh", scripts)
    shutil.copy(REPO_ROOT / "scripts" / "install-worktree-guard.sh", scripts)
    shutil.copytree(REPO_ROOT / ".githooks", repo / ".githooks")
    _run(["git", "add", "."], repo, check=True)
    _run(["git", "commit", "-m", "add guard"], repo, check=True)
    _run(["scripts/install-worktree-guard.sh"], repo, check=True)
    return repo


def _start_stub_server(repo):
    proc = subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=repo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.2)
    return proc


def test_guard_blocks_branch_switch_when_server_runs_from_worktree(tmp_path):
    repo = _init_repo(tmp_path)
    proc = _start_stub_server(repo)
    try:
        result = _run(["git", "checkout", "other"], repo)
        branch = _run(["git", "branch", "--show-current"], repo, check=True)
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    assert result.returncode != 0
    assert "Flask server detected" in result.stderr
    assert "git worktree add" in result.stderr
    assert branch.stdout.strip() == "main"
    assert (repo / "scripts" / "worktree-server-guard.sh").exists()


def test_guard_allows_branch_switch_without_server(tmp_path):
    repo = _init_repo(tmp_path)

    result = _run(["git", "checkout", "other"], repo)
    branch = _run(["git", "branch", "--show-current"], repo, check=True)

    assert result.returncode == 0
    assert branch.stdout.strip() == "other"
    assert not (repo / "scripts" / "worktree-server-guard.sh").exists()


def test_guard_allows_branch_switch_with_limited_hook_path(tmp_path):
    repo = _init_repo(tmp_path)

    result = _run(["git", "checkout", "other"], repo, env={"PATH": "/usr/bin:/bin"})
    branch = _run(["git", "branch", "--show-current"], repo, check=True)

    assert result.returncode == 0
    assert branch.stdout.strip() == "other"


def test_guard_blocks_branch_switch_when_detector_cannot_verify(tmp_path):
    repo = _init_repo(tmp_path)
    hooks_path = _run(["git", "config", "--worktree", "--get", "core.hooksPath"], repo, check=True)
    detector = Path(hooks_path.stdout.strip()) / "worktree-server-guard.sh"
    detector.write_text("#!/bin/bash\nexit 2\n")
    detector.chmod(0o755)

    result = _run(["git", "checkout", "other"], repo)
    branch = _run(["git", "branch", "--show-current"], repo, check=True)

    assert result.returncode != 0
    assert "Unable to verify whether a Flask server is running" in result.stderr
    assert "Install pgrep and lsof" in result.stderr
    assert branch.stdout.strip() == "main"


def test_installer_scopes_hooks_path_to_linked_worktree(tmp_path):
    repo = _init_repo(tmp_path)
    repo_hooks_before = _run(["git", "config", "--worktree", "--get", "core.hooksPath"], repo, check=True)
    sibling = tmp_path / "sibling"
    _run(["git", "worktree", "add", "-b", "sibling-branch", str(sibling)], repo, check=True)

    install = _run(["scripts/install-worktree-guard.sh"], sibling)
    sibling_hooks = _run(["git", "config", "--worktree", "--get", "core.hooksPath"], sibling, check=True)
    repo_hooks_after = _run(["git", "config", "--worktree", "--get", "core.hooksPath"], repo, check=True)

    assert install.returncode == 0
    assert str(sibling / ".git") not in sibling_hooks.stdout
    assert "sibling" in sibling_hooks.stdout
    assert "fitness-worktree-guard-hooks" in sibling_hooks.stdout
    assert repo_hooks_after.stdout == repo_hooks_before.stdout


def test_installer_repairs_shared_bare_config_for_fresh_worktrees(tmp_path):
    repo = _init_repo(tmp_path)
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_push_smoke.py").write_text("def test_push_smoke():\n    assert True\n")
    _run(["git", "add", "tests/test_push_smoke.py"], repo, check=True)
    _run(["git", "commit", "-m", "add push smoke test"], repo, check=True)
    remote = tmp_path / "remote.git"
    _run(["git", "init", "--bare", str(remote)], tmp_path, check=True)
    _run(["git", "remote", "add", "origin", str(remote)], repo, check=True)
    _run(["git", "config", "--worktree", "core.bare", "false"], repo, check=True)
    _run(
        ["git", "config", "--file", str(repo / ".git" / "config"), "core.bare", "true"],
        repo,
        check=True,
    )

    _run(["scripts/install-worktree-guard.sh"], repo, check=True)
    sibling = tmp_path / "fresh-worktree"
    _run(["git", "worktree", "add", "-b", "fresh-worktree", str(sibling)], repo, check=True)

    root = _run(["git", "rev-parse", "--show-toplevel"], sibling)
    shared_bare = _run(
        ["git", "config", "--file", str(repo / ".git" / "config"), "--get", "core.bare"],
        sibling,
        check=True,
    )
    (sibling / "README.md").write_text("fresh worktree push\n")
    _run(["git", "add", "README.md"], sibling, check=True)
    _run(["git", "commit", "-m", "fresh worktree proof"], sibling, check=True)
    push = _run(["git", "push", "-u", "origin", "fresh-worktree"], sibling)
    assert push.returncode == 0, push.stderr
    local_head = _run(["git", "rev-parse", "HEAD"], sibling, check=True)
    remote_head = _run(
        ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/fresh-worktree"],
        tmp_path,
        check=True,
    )

    assert root.returncode == 0
    assert Path(root.stdout.strip()) == sibling
    assert shared_bare.stdout.strip() == "false"
    assert "Running pre-push pytest suite" in push.stderr
    assert remote_head.stdout == local_head.stdout


def test_installer_resolves_relative_common_dir_from_subdirectory(tmp_path):
    repo = _init_repo(tmp_path)
    nested = repo / "tests"
    nested.mkdir()
    _run(["git", "config", "--worktree", "core.bare", "false"], repo, check=True)
    _run(
        ["git", "config", "--file", str(repo / ".git" / "config"), "core.bare", "true"],
        repo,
        check=True,
    )

    install = _run(["../scripts/install-worktree-guard.sh"], nested)
    shared_bare = _run(
        ["git", "config", "--file", str(repo / ".git" / "config"), "--get", "core.bare"],
        repo,
        check=True,
    )

    assert install.returncode == 0
    assert shared_bare.stdout.strip() == "false"


def test_guard_detects_matching_server_process(tmp_path):
    repo = _init_repo(tmp_path)
    proc = _start_stub_server(repo)
    try:
        result = _run([str(repo / "scripts" / "worktree-server-guard.sh")], repo)
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    assert result.returncode == 1
    assert f"PID {proc.pid}" in result.stderr


def test_guard_detects_repo_qa_launcher_process(tmp_path):
    repo = _init_repo(tmp_path)
    qa_dir = repo / "docs" / "qa" / "fit-11-mobile"
    qa_dir.mkdir(parents=True)
    launcher = qa_dir / "serve_fit11.py"
    launcher.write_text(
        "import os\n"
        "import pathlib\n"
        "import time\n"
        "os.chdir(pathlib.Path(__file__).resolve().parents[3])\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, str(launcher)],
        cwd=repo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    result = None
    deadline = time.time() + 5
    try:
        while time.time() < deadline:
            result = _run([str(repo / "scripts" / "worktree-server-guard.sh")], repo)
            if result.returncode == 1:
                break
            if proc.poll() is not None:
                break
            time.sleep(0.1)
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    assert result is not None
    assert result.returncode == 1, (
        "worktree guard did not detect the QA launcher before timeout; "
        f"last returncode={result.returncode}, stderr={result.stderr!r}"
    )
    assert f"PID {proc.pid}" in result.stderr
