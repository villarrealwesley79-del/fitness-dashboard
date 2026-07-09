def test_delete_user_data_removes_active_and_revoked_push_subscriptions(
    tmp_path, monkeypatch
):
    import data_store

    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()

    active = data_store.save_push_subscription(
        1,
        {
            "endpoint": "https://push.example.test/send/active",
            "keys": {"p256dh": "public-key-active", "auth": "auth-secret-active"},
        },
    )
    revoked = data_store.save_push_subscription(
        1,
        {
            "endpoint": "https://push.example.test/send/revoked",
            "keys": {"p256dh": "public-key-revoked", "auth": "auth-secret-revoked"},
        },
    )
    assert data_store.revoke_push_subscription(1, revoked["endpoint_hash"])

    with data_store._get_db() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM push_subscriptions WHERE user_id = ?", (1,)
        ).fetchone()[0] == 2
    assert data_store.get_user_data_summary(1)["push_subscriptions"] == 2

    data_store.delete_user_data(1)

    with data_store._get_db() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM push_subscriptions WHERE user_id = ?", (1,)
        ).fetchone()[0] == 0
    assert data_store.get_user_data_summary(1)["push_subscriptions"] == 0
    assert active["endpoint_hash"] != revoked["endpoint_hash"]
