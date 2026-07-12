from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FACTORY_CONFIG = ROOT / ".agents" / "factory.yaml"
CANONICAL_PYTHON = "/Users/admin/fitness-dashboard/venv/bin/python3"
TAILNET_HOST = "100.90.15.93"


def test_factory_checks_and_preview_use_canonical_python_runtime():
    config = FACTORY_CONFIG.read_text(encoding="utf-8")

    assert f"boot: HOST={TAILNET_HOST} PORT={{port}} {CANONICAL_PYTHON} app.py" in config
    assert f"- {CANONICAL_PYTHON} -m pytest -q" in config
    assert "boot: PORT={port} venv/bin/python3 app.py" not in config
    assert "HOST=0.0.0.0" not in config
    assert "- python -m pytest -q" not in config
