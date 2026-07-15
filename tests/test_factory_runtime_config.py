from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FACTORY_CONFIG = ROOT / ".agents" / "factory.yaml"
FACTORY_PREVIEW_DOCS = ROOT / "docs" / "AGENT_WORKTREES.md"
CANONICAL_PYTHON = "/Users/admin/fitness-dashboard/venv/bin/python3"
TAILNET_HOST = "100.90.15.93"


def test_factory_checks_and_preview_use_canonical_python_runtime():
    config = FACTORY_CONFIG.read_text(encoding="utf-8")

    assert (
        "boot: DATA_DIR=$(/usr/bin/mktemp -d "
        "/tmp/fitness-dashboard-factory-preview-{port}-XXXXXX) "
        f"HOST={TAILNET_HOST} FITNESS_DASHBOARD_FACTORY_PREVIEW=1 "
        f"PORT={{port}} {CANONICAL_PYTHON} app.py"
    ) in config
    assert "SESSION_COOKIE_SECURE=false" not in config
    assert f"- {CANONICAL_PYTHON} -m pytest -q" in config
    assert "boot: PORT={port} venv/bin/python3 app.py" not in config
    assert "HOST=0.0.0.0" not in config
    assert "- python -m pytest -q" not in config


def test_factory_preview_login_is_documented():
    docs = FACTORY_PREVIEW_DOCS.read_text(encoding="utf-8")

    assert "FITNESS_DASHBOARD_FACTORY_PREVIEW=1" in docs
    assert "Username: `test`" in docs
    assert "Password: `1224`" in docs
    assert "Tailnet-only" in docs
