from __future__ import annotations

import pytest

import app as module


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "BODY_DATA", [])
    monkeypatch.setattr(module, "BODY_FILE", str(tmp_path / "body.json"))
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    return module.app.test_client()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("date", "2026-02-30"),
        ("date", "07/11/2026"),
        ("date", ""),
        ("weight_lbs", float("nan")),
        ("weight_lbs", float("inf")),
        ("body_fat_pct", float("nan")),
        ("body_fat_pct", float("inf")),
        ("neck_in", 7.9),
        ("waist_in", 80.1),
        ("chest_in", "wide"),
        ("hips_in", float("nan")),
        ("arms", 30.1),
        ("legs", 9.9),
    ],
)
def test_add_body_measurement_rejects_invalid_date_and_tape_fields(client, field, value):
    response = client.post(
        "/api/add-body-measurement",
        json={"weight_lbs": 180, field: value},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_field"
    assert module.BODY_DATA == []


def test_add_body_measurement_normalizes_valid_date_and_tape_fields(client):
    response = client.post(
        "/api/add-body-measurement",
        json={
            "date": "2026-07-11",
            "weight_lbs": 180,
            "neck_in": "16.5",
            "waist_in": 34,
            "chest_in": 42,
            "hips_in": 40,
            "arms": 15,
            "legs": 24,
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert response.status_code == 200
    measurement = response.get_json()["body_measurement"]
    assert measurement["date"] == "2026-07-11"
    assert measurement["neck_in"] == 16.5
    assert measurement["waist_in"] == 34.0
    assert measurement["chest_in"] == 42.0
    assert measurement["hips_in"] == 40.0
    assert measurement["arms"] == 15.0
    assert measurement["legs"] == 24.0


def test_body_history_projects_malformed_legacy_fields_safely(client):
    module.BODY_DATA.extend(
        [
            {
                "date": "not-a-date",
                "weight_lbs": 10**1000,
                "body_fat_pct": float("inf"),
                "neck_in": "unknown",
                "waist_in": 900,
                "chest_in": None,
                "hips_in": {},
                "arms": [],
                "legs": float("nan"),
            },
            {"date": "2026-07-11", "weight_lbs": 180, "body_fat_pct": 20},
        ]
    )

    response = client.get("/api/body-history")

    assert response.status_code == 200
    malformed = response.get_json()["history"][1]
    assert malformed["date"] is None
    for field in (
        "weight_lbs",
        "body_fat_pct",
        "neck_in",
        "waist_in",
        "chest_in",
        "hips_in",
        "arms",
        "legs",
    ):
        assert malformed[field] is None

    recomp = client.get("/api/body-recomp")
    assert recomp.status_code == 200
    assert recomp.get_json()["history"][0]["date"] is None
    assert recomp.get_json()["history"][0]["weight_lbs"] is None


def test_body_recomp_skips_invalid_legacy_weight_in_eta_window(client, monkeypatch):
    monkeypatch.setitem(module.USER_SETTINGS, "target_weight_lbs", 170)
    module.BODY_DATA.extend(
        {"date": f"2026-06-{day:02d}", "weight_lbs": "invalid" if day == 1 else 180 - day / 10}
        for day in range(1, 15)
    )

    response = client.get("/api/body-recomp")

    assert response.status_code == 200
    assert response.get_json()["summary"]["eta_weeks"] is None


def test_body_history_excludes_invalid_dates_from_deltas_and_trend(client):
    module.BODY_DATA.extend(
        [
            {"date": "not-a-date", "weight_lbs": 900},
            {"date": "2026-07-09", "weight_lbs": 180},
            {"date": "2026-07-10", "weight_lbs": 180},
            {"date": "2026-07-11", "weight_lbs": 180},
        ]
    )

    response = client.get("/api/body-history")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["trend"] == "stable"
    assert payload["history"][-2]["weight_change"] is None
    assert payload["history"][-1]["date"] is None
    assert payload["history"][-1]["weight_change"] is None


def test_body_recomp_excludes_invalid_dates_from_eta_window(client, monkeypatch):
    monkeypatch.setitem(module.USER_SETTINGS, "target_weight_lbs", 170)
    module.BODY_DATA.append({"date": "not-a-date", "weight_lbs": 900})
    module.BODY_DATA.extend(
        {"date": f"2026-06-{day:02d}", "weight_lbs": 180 - day / 10}
        for day in range(1, 14)
    )

    response = client.get("/api/body-recomp")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["history"][0]["date"] is None
    assert payload["dates"] == [f"2026-06-{day:02d}" for day in range(1, 14)]
    assert payload["summary"]["eta_weeks"] is None


def test_dashboard_ignores_malformed_legacy_rows_for_latest_and_change(client):
    today = module.datetime.now().date()
    module.BODY_DATA.extend(
        [
            {"date": "not-a-date", "weight_lbs": 900, "body_fat_pct": float("inf")},
            {"date": (today - module.timedelta(days=40)).isoformat(), "weight_lbs": 190, "body_fat_pct": 21},
            {"date": (today - module.timedelta(days=1)).isoformat(), "weight_lbs": 180, "body_fat_pct": 20},
        ]
    )

    response = client.get("/api/dashboard")

    assert response.status_code == 200
    assert response.get_json()["body_stats"] == {
        "latest_weight": 180.0,
        "latest_body_fat": 20.0,
        "weight_change_30d": -10.0,
        "trend": "decreasing",
    }


def test_vitals_selects_latest_safe_values_and_finite_trend(client):
    recent_date = (module.datetime.now().date() - module.timedelta(days=1)).isoformat()
    module.BODY_DATA.extend(
        [
            {"date": "zzzz", "weight_lbs": float("inf"), "body_fat_pct": float("nan")},
            {"date": recent_date, "weight_lbs": 180, "body_fat_pct": 20},
        ]
    )

    response = client.get("/api/vitals")

    assert response.status_code == 200
    weight = response.get_json()["weight"]
    assert weight["current_lbs"] == 180.0
    assert weight["body_fat_pct"] == 20.0
    assert weight["trend_7d"] == [{"date": recent_date, "weight_lbs": 180.0}]


def test_latest_weight_skips_malformed_legacy_rows(client):
    recent_date = (module.datetime.now().date() - module.timedelta(days=1)).isoformat()
    module.BODY_DATA.extend(
        [
            {"date": "zzzz", "weight_lbs": 900},
            {"date": recent_date, "weight_lbs": 180},
        ]
    )

    assert module._get_latest_weight() == 180.0


def test_body_trend_excludes_non_finite_legacy_weights(client):
    today = module.datetime.now().date()
    invalid_date = (today - module.timedelta(days=2)).isoformat()
    valid_date = (today - module.timedelta(days=1)).isoformat()
    module.BODY_DATA.extend(
        [
            {"date": invalid_date, "weight_lbs": float("inf")},
            {"date": valid_date, "weight_lbs": 180},
        ]
    )

    assert module._body_trend(7) == [{"date": valid_date, "weight_lbs": 180.0}]
