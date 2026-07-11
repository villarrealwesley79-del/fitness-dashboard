import json
import subprocess
import sys
from pathlib import Path

import data_store


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "support" / "local_data_deletion_inventory.py"


def test_deletion_inventory_dry_run_lists_structured_and_full_purge_boundaries(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--data-dir",
            str(tmp_path),
            "--app-dir",
            str(ROOT),
            "--working-directory",
            str(ROOT),
            "--service-environment-reviewed",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["mode"] == "dry_run_only"
    assert payload["mutated"] is False
    assert payload["structured_user_delete"]["database"] == str(tmp_path / "fitness_data.db")
    assert set(payload["structured_user_delete"]["tables"]) >= {
        "body_data",
        "food_logs",
        "push_subscriptions",
        "branded_lookup_cache",
        "barcode_lookup_cache",
    }
    assert payload["structured_user_delete"]["tables"] == list(data_store.DELETE_USER_DATA_TABLES)
    assert set(payload["not_deleted_by_structured_user_delete"]["data_dir_paths"]) >= {
        str(tmp_path / "auth.db"),
        str(tmp_path / "auth.db-wal"),
        str(tmp_path / "auth.db-shm"),
        str(tmp_path / "auth.db-journal"),
        str(tmp_path / "fitness_data.db"),
        str(tmp_path / "fitness_data.db-wal"),
        str(tmp_path / "fitness_data.db-shm"),
        str(tmp_path / "data_workouts.json"),
        str(tmp_path / "oura_daily.sqlite3"),
        str(tmp_path / "whoop.sqlite3"),
        str(tmp_path / "wearable_facts.sqlite3"),
        str(tmp_path / "apple_health_sync.db"),
        str(tmp_path / "ai_coach_cache.sqlite3"),
        str(tmp_path / "open_wearables_config.json"),
        str(tmp_path / "whoop_sync.lock"),
        str(tmp_path / ".whoop-client-id"),
    }
    assert payload["not_deleted_by_structured_user_delete"]["browser_stores"] == {
        "indexed_db": ["fitMealIntakeQueueDB"],
        "local_storage": [
            "fit168:active-workout-draft:v1",
            "fit51:sync-queue:v1",
            "fit145:meal-queue-auth-scope:v1",
            "fit60_meal_draft",
        ],
        "push_manager": ["active service-worker push subscription"],
        "session_storage": ["fit24:adjust-intent:v1"],
    }
    assert str(ROOT / ".whoop-client-id") in payload["not_deleted_by_structured_user_delete"]["app_dir_paths"]
    assert str(ROOT / ".env") in payload["not_deleted_by_structured_user_delete"]["app_dir_paths"]
    assert str(ROOT / ".health-sync-token") in payload["not_deleted_by_structured_user_delete"]["app_dir_paths"]
    assert {item["service"] for item in payload["protected_material"]} == {
        "fitness-dashboard-apple-health-webhook",
        "fitness-dashboard-whoop-client-secret",
        "fitness-dashboard-whoop-oauth-material",
        "fitness-dashboard-open-wearables-password",
    }
    apple_health = next(
        item for item in payload["protected_material"]
        if item["service"] == "fitness-dashboard-apple-health-webhook"
    )
    assert apple_health == {
        "service": "fitness-dashboard-apple-health-webhook",
        "storage": "app-directory fallback file",
        "fallback_pattern": str(ROOT / ".health-sync-token"),
    }
    assert payload["configuration_ready_for_full_purge"] is None
    assert payload["configuration_source"] == "self_reported_flags_only"
    assert {item["name"] for item in payload["configuration_status"]} >= {
        "HEALTH_SYNC_TOKEN",
        "OURA_API_TOKEN",
        "OW_PASSWORD",
        "ANTHROPIC_API_KEY",
        "NUTRITIONIX_APP_KEY",
        "USDA_FDC_API_KEY",
        "FITNESS_PUSH_VAPID_PRIVATE_KEY",
        "GARMIN_CLIENT_SECRET",
        "STRIPE_SECRET_KEY",
    }
    assert all(item["configured"] is False for item in payload["configuration_status"])
    assert all(
        item["present"] is False
        for item in payload["path_status"]
        if item["path"].startswith(str(tmp_path))
    )
    assert payload["glob_status"][0]["pattern"] == str(ROOT / ".env.before-managed-connectors-*")
    assert isinstance(payload["glob_status"][0]["matches"], list)
    assert payload["glob_status"][1]["pattern"].endswith(
        "/open-wearables/backend/config/.env.before-managed-connectors-*"
    )
    assert isinstance(payload["glob_status"][1]["matches"], list)
    assert payload["glob_status"][2] == {
        "pattern": str(tmp_path / "*.json.corrupt-*.json"),
        "matches": [],
    }
    assert payload["glob_status"][3]["pattern"].endswith("/.whoop-protected-material-*.json")
    assert isinstance(payload["glob_status"][3]["matches"], list)
    assert not any(tmp_path.iterdir())


def test_deletion_inventory_resolves_active_override_paths(tmp_path):
    data_dir = tmp_path / "data"
    overrides = tmp_path / "overrides"
    service_environment = tmp_path / "service-environment.json"
    service_environment.write_text(json.dumps({
        "APPLE_HEALTH_SYNC_DB": str(overrides / "apple.db"),
        "APPLE_HEALTH_FIRST_SEEN_FILE": str(overrides / "apple-health-first-seen"),
        "HEALTH_SYNC_TOKEN": "secret-not-printed",
        "GARMIN_CLIENT_SECRET": "secret-not-printed",
        "OURA_API_TOKEN": "secret-not-printed",
        "OPEN_WEARABLES_PASSWORD_FILE": str(overrides / "open-wearables.secret"),
        "OW_SIDECAR_ENV_PATH": str(overrides / "sidecar.env"),
        "WHOOP_CLIENT_ID_FILE": str(overrides / "whoop-client-id"),
        "WHOOP_PROTECTED_MATERIAL_DIR": str(overrides / "whoop-secrets"),
    }), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--data-dir",
            str(data_dir),
            "--app-dir",
            str(ROOT),
            "--working-directory",
            str(ROOT),
            "--service-environment-json",
            str(service_environment),
            "--service-environment-reviewed",
            "--apple-health-sync-db",
            str(overrides / "apple.db"),
            "--apple-health-first-seen-file",
            str(overrides / "apple-health-first-seen"),
            "--open-wearables-password-file",
            str(overrides / "open-wearables.secret"),
            "--open-wearables-sidecar-env-path",
            str(overrides / "sidecar.env"),
            "--whoop-client-id-file",
            str(overrides / "whoop-client-id"),
            "--whoop-protected-material-dir",
            str(overrides / "whoop-secrets"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["active_override_paths"] == {
        "APPLE_HEALTH_SYNC_DB": str((overrides / "apple.db").resolve()),
        "APPLE_HEALTH_FIRST_SEEN_FILE": str((overrides / "apple-health-first-seen").resolve()),
        "OPEN_WEARABLES_PASSWORD_FILE": str((overrides / "open-wearables.secret").resolve()),
        "OW_SIDECAR_ENV_PATH": str((overrides / "sidecar.env").resolve()),
        "WHOOP_CLIENT_ID_FILE": str((overrides / "whoop-client-id").resolve()),
        "WHOOP_PROTECTED_MATERIAL_DIR": str((overrides / "whoop-secrets").resolve()),
    }
    whoop = next(item for item in payload["protected_material"] if item["service"] == "fitness-dashboard-whoop-oauth-material")
    assert whoop["fallback_pattern"] == str((overrides / "whoop-secrets" / ".whoop-protected-material-*.json").resolve())
    assert str(data_dir / ".apple-health-first-sync") not in payload["not_deleted_by_structured_user_delete"]["data_dir_paths"]
    status_paths = {item["path"] for item in payload["path_status"]}
    assert str((overrides / "apple.db").resolve()) in status_paths
    assert str((overrides / "apple.db-wal").resolve()) in status_paths
    assert str((overrides / "apple.db-shm").resolve()) in status_paths
    assert str((overrides / "apple.db-journal").resolve()) in status_paths
    assert str((overrides / "whoop-secrets").resolve()) not in status_paths
    assert str((overrides / "sidecar.env").resolve()) in status_paths
    apple_health = next(
        item for item in payload["protected_material"]
        if item["service"] == "fitness-dashboard-apple-health-webhook"
    )
    assert apple_health["storage"] == "HEALTH_SYNC_TOKEN environment variable"
    configured = {
        item["name"] for item in payload["configuration_status"] if item["configured"]
    }
    assert configured == {"GARMIN_CLIENT_SECRET", "HEALTH_SYNC_TOKEN", "OURA_API_TOKEN"}
    assert payload["configuration_ready_for_full_purge"] is False
    assert payload["configuration_source"] == "machine_readable_service_environment"
    assert "secret-not-printed" not in result.stdout
    open_wearables = next(
        item for item in payload["protected_material"]
        if item["service"] == "fitness-dashboard-open-wearables-password"
    )
    assert open_wearables["fallback_pattern"] == str((overrides / "open-wearables.secret").resolve())
    assert not data_dir.exists()
    assert not overrides.exists()


def test_deletion_inventory_reports_corrupt_history_recovery_copies(tmp_path):
    recovery_copy = tmp_path / "data_workouts.json.corrupt-20260711T120000.json"
    config_recovery_copy = tmp_path / "open_wearables_config.json.corrupt-20260711T120001.json"
    recovery_copy.write_text("retained copy", encoding="utf-8")
    config_recovery_copy.write_text("retained config", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--data-dir",
            str(tmp_path),
            "--app-dir",
            str(ROOT),
            "--working-directory",
            str(ROOT),
            "--service-environment-reviewed",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["glob_status"][2]["matches"] == sorted([
        str(recovery_copy),
        str(config_recovery_copy),
    ])
    assert recovery_copy.read_text(encoding="utf-8") == "retained copy"
    assert config_recovery_copy.read_text(encoding="utf-8") == "retained config"


def test_deletion_inventory_resolves_sidecar_path_from_local_config(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sidecar_env = Path("open-wearables") / ".env"
    config = data_dir / "open_wearables_config.json"
    config.write_text(json.dumps({"sidecar_env_path": str(sidecar_env)}), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--data-dir",
            str(data_dir),
            "--app-dir",
            str(ROOT),
            "--working-directory",
            str(ROOT),
            "--service-environment-reviewed",
            "--open-wearables-sidecar-env-path",
            str(tmp_path / "ignored-service-sidecar.env"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert str((ROOT / sidecar_env).resolve()) in {item["path"] for item in payload["path_status"]}
    assert str((tmp_path / "ignored-service-sidecar.env").resolve()) not in {
        item["path"] for item in payload["path_status"]
    }
    assert payload["inventory_errors"] == []
    assert json.loads(config.read_text(encoding="utf-8"))["sidecar_env_path"] == str(sidecar_env)
