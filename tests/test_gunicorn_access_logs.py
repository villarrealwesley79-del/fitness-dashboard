from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_gunicorn_uses_safe_access_logs_and_single_threaded_worker_pool():
    dockerfile = (ROOT / "Dockerfile").read_text()
    procfile = (ROOT / "Procfile").read_text()
    for text in (dockerfile, procfile):
        assert "--access-logformat" in text
        assert "%(U)s" in text
        assert "%(q)s" not in text
        assert "%(r)s" not in text
    assert "--workers 1" in dockerfile
    assert "--workers 2" not in dockerfile
    assert "--threads 1" in dockerfile
