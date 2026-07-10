from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static/js/app.js").read_text()
INDEX_HTML = (ROOT / "templates/index.html").read_text()


def _function_block(name: str, next_name: str) -> str:
    start = APP_JS.index(f"function {name}")
    end = APP_JS.index(f"function {next_name}", start)
    return APP_JS[start:end]


def test_meal_delete_first_click_opens_confirmation_without_deleting():
    detail = _function_block("openMealDetailModal", "readPendingMealDeletes")

    assert 'id="modal-meal-delete-confirm"' in INDEX_HTML
    assert 'id="btn-confirm-meal-delete"' in INDEX_HTML
    assert INDEX_HTML.index('id="modal-food-log"') < INDEX_HTML.index('id="modal-meal-delete-confirm"')
    assert "openMealDeleteConfirm(entry, modal)" in detail
    assert "method: 'DELETE'" not in detail


def test_meal_delete_undo_cancels_persisted_deferred_delete():
    delete_flow = _function_block("readPendingMealDeletes", "setMealDetailMode")

    assert "detailModal.hidden = true" in delete_flow
    assert "detailModal.hidden = false" in delete_flow
    assert "foodLogModal.hidden = true" in delete_flow
    assert "foodLogModal.hidden = false" in delete_flow
    assert "btn-cancel-meal-delete" in delete_flow
    assert "confirmModal.__fit192Close = restoreDetail" in delete_flow
    assert "localStorage.setItem" in delete_flow
    assert "auth_scope" in delete_flow
    assert "fetchCurrentMealQueueAuthScope" in delete_flow
    assert "authCheck.terminal" in delete_flow
    assert "rearmPendingMealDelete" in delete_flow
    assert "toastUndo('Meal queued for deletion'" in delete_flow
    assert "method: 'DELETE'" in delete_flow
    assert "setTimeout" in delete_flow
    assert "fit107:meal-deleted" in delete_flow
    assert "resumePendingMealDeletes().catch" in APP_JS
