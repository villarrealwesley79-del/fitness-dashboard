from datetime import datetime, timedelta

import data_store


def _save_subscription(endpoint_suffix: str) -> dict:
    return data_store.save_push_subscription(
        1,
        {
            "endpoint": f"https://push.example.test/send/{endpoint_suffix}",
            "keys": {"p256dh": f"public-{endpoint_suffix}", "auth": f"secret-{endpoint_suffix}"},
        },
    )


def test_prune_revoked_push_subscriptions_removes_only_rows_past_retention(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    now = datetime(2026, 7, 12, 9, 0, 0)

    expired = _save_subscription("expired")
    retained = _save_subscription("retained")
    active = _save_subscription("active")
    assert data_store.revoke_push_subscription(1, expired["endpoint_hash"])
    assert data_store.revoke_push_subscription(1, retained["endpoint_hash"])

    cutoff = now - timedelta(days=data_store.PUSH_SUBSCRIPTION_REVOKED_RETENTION_DAYS)
    with data_store._get_db() as conn:
        conn.execute(
            "UPDATE push_subscriptions SET revoked_at = ? WHERE endpoint_hash = ?",
            ((cutoff - timedelta(seconds=1)).isoformat(), expired["endpoint_hash"]),
        )
        conn.execute(
            "UPDATE push_subscriptions SET revoked_at = ? WHERE endpoint_hash = ?",
            (cutoff.isoformat(), retained["endpoint_hash"]),
        )
        conn.execute(
            "UPDATE push_subscriptions SET updated_at = ? WHERE endpoint_hash = ?",
            ((cutoff - timedelta(days=365)).isoformat(), active["endpoint_hash"]),
        )

    assert data_store.prune_revoked_push_subscriptions(now=now) == 1

    with data_store._get_db() as conn:
        rows = conn.execute(
            "SELECT endpoint_hash, revoked_at FROM push_subscriptions ORDER BY endpoint_hash"
        ).fetchall()
    remaining = {row["endpoint_hash"]: row["revoked_at"] for row in rows}
    assert expired["endpoint_hash"] not in remaining
    assert remaining[retained["endpoint_hash"]] == cutoff.isoformat()
    assert remaining[active["endpoint_hash"]] is None


def test_init_data_db_prunes_expired_revoked_push_subscriptions(tmp_path, monkeypatch):
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    expired = _save_subscription("startup-cleanup")
    assert data_store.revoke_push_subscription(1, expired["endpoint_hash"])

    with data_store._get_db() as conn:
        conn.execute(
            "UPDATE push_subscriptions SET revoked_at = ? WHERE endpoint_hash = ?",
            ("2000-01-01T00:00:00", expired["endpoint_hash"]),
        )

    data_store.init_data_db()

    with data_store._get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
    assert count == 0
