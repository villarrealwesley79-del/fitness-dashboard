from __future__ import annotations

import sqlite3


class _CountingConnection:
    def __init__(self, conn: sqlite3.Connection):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "closed", False)
        object.__setattr__(self, "close_count", 0)

    def close(self):
        object.__setattr__(self, "closed", True)
        object.__setattr__(self, "close_count", self.close_count + 1)
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name in {"_conn", "closed", "close_count"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)


def test_food_log_calls_close_every_fitness_data_connection(tmp_path, monkeypatch):
    import data_store

    db_path = tmp_path / "fitness_data.db"
    opened: list[_CountingConnection] = []
    real_connect = data_store.sqlite3.connect

    def counting_connect(*args, **kwargs):
        wrapped = _CountingConnection(real_connect(*args, **kwargs))
        opened.append(wrapped)
        return wrapped

    monkeypatch.setattr(data_store, "DATA_DB", str(db_path))
    monkeypatch.setattr(data_store.sqlite3, "connect", counting_connect)

    data_store.init_data_db()
    for index in range(25):
        data_store.add_food_log(
            user_id=1,
            record={
                "client_id": f"fd-leak-{index}",
                "date": "2026-05-29",
                "logged_at": f"2026-05-29T12:{index:02d}:00",
                "item_name": "test meal",
                "calories": 500 + index,
            },
        )
        rows = data_store.get_food_logs(user_id=1)
        assert rows

    assert len(opened) >= 51
    assert all(conn.closed for conn in opened)
    assert all(conn.close_count == 1 for conn in opened)
