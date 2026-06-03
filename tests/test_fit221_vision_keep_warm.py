from __future__ import annotations

import importlib
import logging

import local_vision_adapter


def test_warm_all_candidates_warms_each_candidate_and_reports_failures(monkeypatch, caplog):
    candidates = [
        {"role": "primary", "url": "http://primary.test", "model": "primary-vision"},
        {"role": "fallback", "url": "http://fallback.test", "model": "fallback-vision"},
    ]
    preflight_calls = []
    warm_calls = []

    monkeypatch.setattr(local_vision_adapter, "_lm_studio_candidates", lambda: list(candidates))
    monkeypatch.setattr(
        local_vision_adapter,
        "_preflight_candidate",
        lambda candidate: preflight_calls.append(candidate["role"]),
    )

    def fake_warm(candidate):
        warm_calls.append(candidate["role"])
        if candidate["role"] == "fallback":
            raise local_vision_adapter.LocalVisionError("http 400: out of memory")

    monkeypatch.setattr(local_vision_adapter, "_warm_candidate", fake_warm)

    with caplog.at_level(logging.WARNING):
        result = local_vision_adapter.warm_all_candidates()

    assert preflight_calls == ["primary", "fallback"]
    assert warm_calls == ["primary", "fallback"]
    assert result["started"] is True
    assert result["status"] == "ok"
    assert result["candidates"][0] == {
        "role": "primary",
        "model": "primary-vision",
        "status": "ok",
    }
    assert result["candidates"][1]["status"] == "error"
    assert result["candidates"][1]["error"].startswith("out_of_memory:")
    assert "out of memory" in caplog.text
    assert "data:image" not in caplog.text


def test_warm_all_candidates_single_flight_skips_second_run(monkeypatch):
    monkeypatch.setattr(
        local_vision_adapter,
        "_warm_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("warm should not run")),
    )

    acquired = local_vision_adapter._KEEP_WARM_LOCK.acquire(blocking=False)
    assert acquired is True
    try:
        result = local_vision_adapter.warm_all_candidates()
    finally:
        local_vision_adapter._KEEP_WARM_LOCK.release()

    assert result == {
        "started": False,
        "status": "skipped",
        "reason": "already_running",
        "candidates": [],
    }


def test_warm_all_candidates_does_not_double_warm_after_preflight_load(monkeypatch):
    candidate = {"role": "primary", "url": "http://primary.test", "model": "primary-vision"}
    monkeypatch.setattr(local_vision_adapter, "_lm_studio_candidates", lambda: [candidate])
    monkeypatch.setattr(local_vision_adapter, "_preflight_candidate", lambda _candidate: True)
    monkeypatch.setattr(
        local_vision_adapter,
        "_warm_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("preflight already warmed")),
    )

    result = local_vision_adapter.warm_all_candidates()

    assert result["status"] == "ok"
    assert result["candidates"] == [{"role": "primary", "model": "primary-vision", "status": "ok"}]


def test_start_vision_keep_warm_daemon_is_env_and_provider_gated(monkeypatch):
    module = importlib.import_module("app")
    thread_calls = []

    def fake_thread(*_args, **_kwargs):
        thread_calls.append((_args, _kwargs))
        raise AssertionError("thread should not start when gated off")

    monkeypatch.setattr(module.threading, "Thread", fake_thread)
    monkeypatch.setattr(module.vision_estimator, "configured_provider", lambda: "lm_studio")
    monkeypatch.delenv("VISION_LM_STUDIO_KEEP_WARM", raising=False)

    assert module._start_vision_keep_warm_daemon() is None

    monkeypatch.setenv("VISION_LM_STUDIO_KEEP_WARM", "1")
    monkeypatch.setattr(module.vision_estimator, "configured_provider", lambda: "claude")

    assert module._start_vision_keep_warm_daemon() is None
    assert thread_calls == []


def test_start_vision_keep_warm_daemon_runs_fire_and_forget(monkeypatch):
    module = importlib.import_module("app")
    warm_calls = []
    started_threads = []

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self):
            started_threads.append({"name": self.name, "daemon": self.daemon})
            self.target()

    monkeypatch.setenv("VISION_LM_STUDIO_KEEP_WARM", "true")
    monkeypatch.setattr(module.vision_estimator, "configured_provider", lambda: "lm_studio")
    monkeypatch.setattr(module.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        module.vision_estimator.local_vision_adapter,
        "warm_all_candidates",
        lambda: warm_calls.append("warm") or {"status": "ok", "candidates": []},
    )

    thread = module._start_vision_keep_warm_daemon()

    assert isinstance(thread, FakeThread)
    assert started_threads == [{"name": "vision-keep-warm", "daemon": True}]
    assert warm_calls == ["warm"]
