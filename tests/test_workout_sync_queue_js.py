from pathlib import Path


def test_workout_queue_auth_and_server_failures_remain_retryable():
    source = Path("static/js/app.js").read_text()

    assert "const WORKOUT_QUEUE_RETRYABLE_STATUSES = new Set(['pending', 'auth_required']);" in source
    assert "if (res.status === 401 || res.status === 403) syncStatus = 'auth_required';" in source
    assert "else if (res.status >= 500) syncStatus = 'pending';" in source
    assert ".filter((e) => WORKOUT_QUEUE_RETRYABLE_STATUSES.has(e.last_status || 'pending'))" in source
    assert "if (result.syncStatus === 'pending') {\n                    settleQueued('Server unavailable" in source


def test_workout_queue_surfaces_recoverable_failures_to_user():
    source = Path("static/js/app.js").read_text()

    assert "function annotateWorkoutSyncReason(rawReason, syncStatus)" in source
    assert "Sign in with the account that saved this workout, then retry." in source
    assert "auth_required: 'Sign-in needed'" in source
    assert "['rejected', 'conflicted', 'auth_required'].includes(entry.last_status)" in source
    assert "Sign in, then retry the workout from the sync queue." in source
    assert "const pending = queue.filter((e) => (e.last_status || 'pending') === 'pending').length" in source
