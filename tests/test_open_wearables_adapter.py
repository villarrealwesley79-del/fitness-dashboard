from open_wearables_adapter import (
    OpenWearablesProviderStatus,
    build_open_wearables_status,
    providers_from_payload,
    provider_status_from_payload,
    validate_open_wearables_base_url,
)


def test_remote_open_wearables_hosts_require_tls_and_allowlist():
    assert validate_open_wearables_base_url("http://localhost:8000") == (True, None)
    assert validate_open_wearables_base_url("http://localhost:bad") == (False, "invalid_url")
    assert validate_open_wearables_base_url("http://wearables.example.com") == (False, "remote_requires_tls")
    assert validate_open_wearables_base_url("https://wearables.example.com", allowed_hosts=set()) == (False, "remote_host_not_allowed")
    assert validate_open_wearables_base_url("https://wearables.example.com", allowed_hosts={"wearables.example.com"}) == (True, None)


def test_public_status_redacts_path_and_marks_missing_config():
    status = build_open_wearables_status(
        username="",
        credential="",
        user_id="",
        base_url="http://localhost:8000/api/v1/users/private-user",
    ).public_dict()

    assert status["status"] == "missing_config"
    assert status["configured"] is False
    assert status["base_url"] == "http://localhost:8000"
    assert "private-user" not in str(status)


def test_provider_status_is_generic_and_capability_based():
    provider = provider_status_from_payload({
        "provider": "Garmin",
        "status": "connected",
        "capabilities": {"metrics": True, "events": True, "backfill": True},
        "last_sync_at": "2026-06-26T12:00:00",
    })

    assert provider.provider_id == "garmin"
    assert provider.label == "Garmin"
    assert provider.state == "connected"
    assert provider.capabilities["metrics"] is True
    assert provider.capabilities["workouts"] is True
    assert provider.capabilities["history"] is True


def test_providers_from_payload_accepts_data_source_lists():
    providers = providers_from_payload({
        "data": [
            {"name": "WHOOP", "status": "connected", "capabilities": {"metrics": True}},
            {"provider_id": "garmin", "status": "stale", "capabilities": {"workouts": True}},
        ]
    })

    assert [p.provider_id for p in providers] == ["whoop", "garmin"]
    assert providers[0].capabilities["metrics"] is True


def test_data_source_rows_without_status_count_as_connected():
    providers = providers_from_payload({
        "data": [
            {
                "provider": "WHOOP",
                "display_name": "WHOOP",
                "device_id": "device-123",
                "capabilities": {"metrics": True, "manual_sync": True},
            }
        ]
    })
    status = build_open_wearables_status(
        username="admin@example.test",
        credential="local-secret",
        user_id="user-123",
        base_url="http://localhost:8000",
        providers=providers,
    ).public_dict()

    assert providers[0].state == "connected"
    assert status["status"] == "connected"
    assert status["error_code"] is None


def test_data_source_row_prefers_provider_over_record_id():
    provider = provider_status_from_payload({
        "id": "4b3795c8-datasource-row-id",
        "provider": "whoop",
        "display_name": "WHOOP",
        "capabilities": {"metrics": True},
    })

    assert provider.provider_id == "whoop"
    assert provider.label == "WHOOP"
    assert provider.state == "connected"


def test_configured_hub_with_no_providers_is_waiting_not_error():
    status = build_open_wearables_status(
        username="admin@example.test",
        credential="local-secret",
        user_id="user-123",
        base_url="http://localhost:8000",
        providers=[],
        error_code="open_wearables_no_providers",
    ).public_dict()

    assert status["configured"] is True
    assert status["status"] == "missing_config"
    assert status["error_code"] is None


def test_configured_hub_requires_healthy_provider_to_connect():
    stale_status = build_open_wearables_status(
        username="admin@example.test",
        credential="local-secret",
        user_id="user-123",
        base_url="http://localhost:8000",
        providers=[
            OpenWearablesProviderStatus(
                provider_id="whoop",
                label="WHOOP",
                state="connected",
                capabilities={"metrics": True},
                stale=True,
            )
        ],
    ).public_dict()

    assert stale_status["status"] == "error"
    assert stale_status["error_code"] == "open_wearables_provider_stale"

    connected_status = build_open_wearables_status(
        username="admin@example.test",
        credential="local-secret",
        user_id="user-123",
        base_url="http://localhost:8000",
        providers=[
            OpenWearablesProviderStatus(
                provider_id="whoop",
                label="WHOOP",
                state="connected",
                capabilities={"metrics": True},
            )
        ],
    ).public_dict()

    assert connected_status["status"] == "connected"
    assert connected_status["error_code"] is None
