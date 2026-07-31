#!/usr/bin/env python3
"""Print deletion boundaries without deleting or modifying local data."""

import argparse
import json
import os
from pathlib import Path


STRUCTURED_TABLES = [
    "body_data",
    "cardio_data",
    "nutrition_data",
    "food_logs",
    "food_log_refresh_events",
    "workout_adaptation_pending",
    "workout_adaptation_events",
    "personal_vocab",
    "meal_acceptance_events",
    "meal_review_snapshots",
    "current_workout_plans",
    "recovery_data",
    "user_settings",
    "push_subscriptions",
    "branded_lookup_cache",
    "barcode_lookup_cache",
]

CREDENTIAL_ENVIRONMENT_VARIABLES = (
    "ANTHROPIC_API_KEY",
    "FITNESS_PUSH_VAPID_PRIVATE_KEY",
    "FITNESS_PUSH_VAPID_PUBLIC_KEY",
    "FITBIT_CLIENT_ID",
    "FITBIT_CLIENT_SECRET",
    "GARMIN_CLIENT_ID",
    "GARMIN_CLIENT_SECRET",
    "HEALTH_SYNC_TOKEN",
    "NUTRITIONIX_APP_ID",
    "NUTRITIONIX_APP_KEY",
    "OW_PASSWORD",
    "OURA_API_TOKEN",
    "OURA_CLIENT_ID",
    "OURA_CLIENT_SECRET",
    "POLAR_CLIENT_ID",
    "POLAR_CLIENT_SECRET",
    "SECRET_KEY",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "STRAVA_CLIENT_ID",
    "STRAVA_CLIENT_SECRET",
    "SUUNTO_CLIENT_ID",
    "SUUNTO_CLIENT_SECRET",
    "ULTRAHUMAN_CLIENT_ID",
    "ULTRAHUMAN_CLIENT_SECRET",
    "USDA_FDC_API_KEY",
    "VAPID_PRIVATE_KEY",
    "VAPID_PUBLIC_KEY",
    "WHOOP_CLIENT_ID",
    "WHOOP_CLIENT_SECRET",
)

DATA_DIR_NAMES = [
    "fitness_data.db",
    "auth.db",
    "data_workouts.json",
    "data_soreness.json",
    "data_settings.json",
    "data_cardio.json",
    "data_recovery.json",
    "data_baselines.json",
    "data_body.json",
    "data_sleep.json",
    "data_nutrition.json",
    "oura_daily.sqlite3",
    "whoop.sqlite3",
    "wearable_facts.sqlite3",
    "apple_health_sync.db",
    "ai_coach_cache.sqlite3",
    "open_wearables_config.json",
    ".open-wearables-password",
    "whoop_sync.lock",
    ".apple-health-first-sync",
    ".whoop-client-id",
]


def sqlite_paths(path: Path) -> list[Path]:
    return [path, Path(f"{path}-wal"), Path(f"{path}-shm"), Path(f"{path}-journal")]


def inventory(
    data_dir: Path,
    app_dir: Path,
    working_directory: Path,
    override_values: dict[str, str] | None = None,
    configured_credentials: set[str] | None = None,
    machine_environment_inspected: bool = False,
) -> dict:
    data_dir = data_dir.expanduser().resolve()
    app_dir = app_dir.expanduser().resolve()
    working_directory = working_directory.expanduser().resolve()
    override_values = override_values or {}
    configured_credentials = configured_credentials or set()
    def resolve_service_path(value: str) -> str:
        path = Path(value).expanduser()
        return str((path if path.is_absolute() else working_directory / path).resolve())

    active_override_paths = {
        name: resolve_service_path(value)
        for name, value in override_values.items()
        if value.strip()
    }
    inventory_errors = []
    data_dir_names = list(DATA_DIR_NAMES)
    if "APPLE_HEALTH_SYNC_DB" in active_override_paths:
        data_dir_names.remove("apple_health_sync.db")
    if "APPLE_HEALTH_FIRST_SEEN_FILE" in active_override_paths:
        data_dir_names.remove(".apple-health-first-sync")
    if "OPEN_WEARABLES_PASSWORD_FILE" in active_override_paths:
        data_dir_names.remove(".open-wearables-password")
    open_wearables_config_path = data_dir / "open_wearables_config.json"
    sidecar_env_value = ""
    if open_wearables_config_path.exists():
        try:
            config = json.loads(open_wearables_config_path.read_text(encoding="utf-8"))
            configured_path = config.get("sidecar_env_path", "") if isinstance(config, dict) else ""
            if isinstance(configured_path, str):
                sidecar_env_value = configured_path.strip()
        except (OSError, json.JSONDecodeError) as exc:
            inventory_errors.append(f"cannot resolve Open Wearables sidecar path: {exc}")
    if not sidecar_env_value:
        sidecar_env_value = active_override_paths.get("OW_SIDECAR_ENV_PATH", "")
    sidecar_env_path = Path(resolve_service_path(
        sidecar_env_value or "~/open-wearables/backend/config/.env"
    ))
    data_dir_paths = []
    for name in data_dir_names:
        path = data_dir / name
        data_dir_paths.extend(
            sqlite_paths(path) if path.suffix in {".db", ".sqlite3"} else [path]
        )
    override_paths = []
    for name, value in active_override_paths.items():
        if name in {"WHOOP_PROTECTED_MATERIAL_DIR", "OW_SIDECAR_ENV_PATH"}:
            continue
        path = Path(value)
        override_paths.extend(sqlite_paths(path) if name == "APPLE_HEALTH_SYNC_DB" else [path])
    app_dir_paths = [
        app_dir / ".env",
        app_dir / ".flask-secret",
        app_dir / ".health-sync-token",
        app_dir / ".whoop-client-id",
    ]
    whoop_secret_dir = Path(
        active_override_paths.get("WHOOP_PROTECTED_MATERIAL_DIR", "")
        or "~/Library/Application Support/Fitness Dashboard/secrets"
    ).expanduser()
    whoop_fallback_pattern = whoop_secret_dir / ".whoop-protected-material-*.json"
    open_wearables_fallback = Path(
        active_override_paths.get("OPEN_WEARABLES_PASSWORD_FILE", "")
        or data_dir / ".open-wearables-password"
    )
    concrete_paths = list(dict.fromkeys(data_dir_paths + app_dir_paths + override_paths))
    concrete_paths.append(sidecar_env_path)
    return {
        "mode": "dry_run_only",
        "mutated": False,
        "active_override_paths": active_override_paths,
        "structured_user_delete": {
            "database": str(data_dir / "fitness_data.db"),
            "effect": "deletes rows for one user_id; keeps the database file",
            "tables": STRUCTURED_TABLES,
        },
        "not_deleted_by_structured_user_delete": {
            "data_dir_paths": [str(path) for path in data_dir_paths],
            "app_dir_paths": [str(path) for path in app_dir_paths],
            "supported_override_variables": [
                "APPLE_HEALTH_SYNC_DB",
                "APPLE_HEALTH_FIRST_SEEN_FILE",
                "HEALTH_SYNC_TOKEN",
                "OPEN_WEARABLES_PASSWORD_FILE",
                "OW_SIDECAR_ENV_PATH",
                "WHOOP_CLIENT_ID_FILE",
                "WHOOP_PROTECTED_MATERIAL_DIR",
            ],
            "browser_stores": {
                "indexed_db": ["fitMealIntakeQueueDB"],
                "local_storage": [
                    "fit168:active-workout-draft:v1",
                    "fit51:sync-queue:v1",
                    "fit145:meal-queue-auth-scope:v1",
                    "fit60_meal_draft",
                ],
                "push_manager": ["active service-worker push subscription"],
                "session_storage": ["fit24:adjust-intent:v1"],
            },
        },
        "protected_material": [
            {
                "service": "fitness-dashboard-apple-health-webhook",
                "storage": (
                    "HEALTH_SYNC_TOKEN environment variable"
                    if "HEALTH_SYNC_TOKEN" in configured_credentials
                    else "app-directory fallback file"
                ),
                "fallback_pattern": str(app_dir / ".health-sync-token"),
            },
            {
                "service": "fitness-dashboard-whoop-client-secret",
                "storage": "macOS Keychain",
                "fallback_pattern": None,
            },
            {
                "service": "fitness-dashboard-whoop-oauth-material",
                "storage": "macOS Keychain or protected fallback file",
                "fallback_pattern": str(whoop_fallback_pattern),
            },
            {
                "service": "fitness-dashboard-open-wearables-password",
                "storage": "macOS Keychain or protected fallback file",
                "fallback_pattern": str(open_wearables_fallback),
            },
        ],
        "configuration_status": [
            {
                "name": name,
                "configured": name in configured_credentials,
                "required_state_for_full_purge": "unset in the loaded service configuration",
            }
            for name in CREDENTIAL_ENVIRONMENT_VARIABLES
        ],
        "configuration_ready_for_full_purge": (
            not configured_credentials and not inventory_errors
            if machine_environment_inspected
            else None
        ),
        "configuration_source": (
            "machine_readable_service_environment"
            if machine_environment_inspected
            else "self_reported_flags_only"
        ),
        "inventory_errors": inventory_errors,
        "path_status": [
            {"path": str(path), "present": path.exists()} for path in concrete_paths
        ],
        "glob_status": [
            {
                "pattern": str(app_dir / ".env.before-managed-connectors-*"),
                "matches": sorted(str(path) for path in app_dir.glob(".env.before-managed-connectors-*")),
            },
            {
                "pattern": f"{sidecar_env_path}.before-managed-connectors-*",
                "matches": sorted(
                    str(path)
                    for path in sidecar_env_path.parent.glob(
                        f"{sidecar_env_path.name}.before-managed-connectors-*"
                    )
                ),
            },
            {
                "pattern": str(data_dir / "*.json.corrupt-*.json"),
                "matches": sorted(str(path) for path in data_dir.glob("*.json.corrupt-*.json")),
            },
            {
                "pattern": str(whoop_fallback_pattern),
                "matches": sorted(str(path) for path in whoop_secret_dir.glob(whoop_fallback_pattern.name)),
            }
        ],
        "warning": (
            "This command is an inventory only. Full purge requires an owner-approved backup, "
            "service shutdown, provider disconnect/token removal, exact-path verification, "
            "browser-store clearing, and post-purge verification."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("DATA_DIR", "").strip() or Path(__file__).resolve().parents[1]),
    )
    parser.add_argument(
        "--app-dir",
        type=Path,
        required=True,
        help="Exact application directory loaded by the reviewed service.",
    )
    parser.add_argument(
        "--working-directory",
        type=Path,
        required=True,
        help="Exact WorkingDirectory loaded by the reviewed service.",
    )
    parser.add_argument(
        "--service-environment-json",
        type=Path,
        help="JSON object containing the loaded service environment; values are inspected but never printed.",
    )
    parser.add_argument(
        "--service-environment-reviewed",
        action="store_true",
        help="Confirm the loaded service environment was inspected and every active override was supplied.",
    )
    for option, environment_name in (
        ("--apple-health-sync-db", "APPLE_HEALTH_SYNC_DB"),
        ("--apple-health-first-seen-file", "APPLE_HEALTH_FIRST_SEEN_FILE"),
        ("--open-wearables-password-file", "OPEN_WEARABLES_PASSWORD_FILE"),
        ("--open-wearables-sidecar-env-path", "OW_SIDECAR_ENV_PATH"),
        ("--whoop-client-id-file", "WHOOP_CLIENT_ID_FILE"),
        ("--whoop-protected-material-dir", "WHOOP_PROTECTED_MATERIAL_DIR"),
    ):
        parser.add_argument(option, dest=environment_name, default="")
    parser.add_argument(
        "--health-sync-token-configured",
        action="store_true",
        help="Record that HEALTH_SYNC_TOKEN is configured in the loaded service without exposing its value.",
    )
    parser.add_argument(
        "--credential-configured",
        action="append",
        choices=CREDENTIAL_ENVIRONMENT_VARIABLES,
        default=[],
        help="Record a configured credential variable without accepting or printing its value; repeat as needed.",
    )
    args = parser.parse_args()
    if not args.service_environment_reviewed:
        parser.error(
            "inspect the loaded service environment, pass every active override explicitly, "
            "then add --service-environment-reviewed"
        )
    explicit_overrides = {
        name: getattr(args, name)
        for name in (
            "APPLE_HEALTH_SYNC_DB",
            "APPLE_HEALTH_FIRST_SEEN_FILE",
            "OPEN_WEARABLES_PASSWORD_FILE",
            "OW_SIDECAR_ENV_PATH",
            "WHOOP_CLIENT_ID_FILE",
            "WHOOP_PROTECTED_MATERIAL_DIR",
        )
    }
    service_environment = {}
    if args.service_environment_json:
        try:
            service_environment = json.loads(
                args.service_environment_json.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"cannot read service environment JSON: {exc}")
        if not isinstance(service_environment, dict) or not all(
            isinstance(name, str) and isinstance(value, str)
            for name, value in service_environment.items()
        ):
            parser.error("service environment JSON must be an object of string names to string values")
    overrides = {}
    for name, explicit_value in explicit_overrides.items():
        loaded_value = service_environment.get(name, "")
        if explicit_value and loaded_value and explicit_value != loaded_value:
            parser.error(f"explicit {name} path conflicts with the loaded service environment")
        overrides[name] = loaded_value or explicit_value
    configured_credentials = {
        name for name in CREDENTIAL_ENVIRONMENT_VARIABLES
        if service_environment.get(name, "").strip()
    }
    configured_credentials.update(args.credential_configured)
    if args.health_sync_token_configured:
        configured_credentials.add("HEALTH_SYNC_TOKEN")
    print(json.dumps(
        inventory(
            args.data_dir,
            args.app_dir,
            args.working_directory,
            overrides,
            configured_credentials,
            machine_environment_inspected=args.service_environment_json is not None,
        ),
        indent=2,
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
