"""FIT-150 — pendingRefresh visually disables v2 card edit controls.

Asserts against JS/CSS source so each acceptance criterion has a traceable
code path. The card is fully rebuilt by renderMealPendingList when
pendingRefresh flips, so disabled attributes set at render time are the
correct mechanism.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")


def _v2_block() -> str:
    start = APP_JS.find("// FIT-134 — Multi-item meal review (V2).")
    end = APP_JS.find("function clearMealComposerInputs()", start)
    assert start != -1 and end != -1, "FIT-134 V2 block markers not found"
    return APP_JS[start:end]


# ── Meal type select ──────────────────────────────────────────────────────────

def test_meal_type_select_disabled_during_pending_refresh():
    block = _v2_block()
    # The select must carry a conditional disabled attribute.
    assert "data-action=\"set-meal-type\" aria-label=\"Meal type\"${entry.pendingRefresh ? ' disabled' : ''}" in block


# ── Follow-up controls ────────────────────────────────────────────────────────

def test_followup_input_disabled_during_pending_refresh():
    block = _v2_block()
    assert "data-field=\"followup-answer\"" in block
    assert "required${entry.pendingRefresh ? ' disabled' : ''}" in block


def test_followup_submit_disabled_during_pending_refresh():
    block = _v2_block()
    # Submit button in the followup form must be conditionally disabled.
    followup_section = block.split("data-action=\"followup-form\"", 1)[1].split("</form>", 1)[0]
    assert "${entry.pendingRefresh ? ' disabled' : ''}>" in followup_section


def test_followup_skip_disabled_during_pending_refresh():
    block = _v2_block()
    followup_section = block.split("data-action=\"followup-form\"", 1)[1].split("</form>", 1)[0]
    assert "data-action=\"followup-dismiss\"${entry.pendingRefresh ? ' disabled' : ''}" in followup_section


# ── Add-item controls ─────────────────────────────────────────────────────────

def test_add_item_input_disabled_during_pending_refresh():
    block = _v2_block()
    add_section = block.split("data-action=\"add-item-form\"", 1)[1].split("</form>", 1)[0]
    assert "data-field=\"add-item-text\"" in add_section
    assert "required${entry.pendingRefresh ? ' disabled' : ''}" in add_section


def test_add_item_submit_disabled_during_pending_refresh():
    block = _v2_block()
    add_section = block.split("data-action=\"add-item-form\"", 1)[1].split("</form>", 1)[0]
    assert "btn btn-ghost\"${entry.pendingRefresh ? ' disabled' : ''}>Add" in add_section


# ── pendingRefresh propagated to item renderer ────────────────────────────────

def test_pending_refresh_passed_to_item_html_builder():
    block = _v2_block()
    # The per-item builder call must forward pendingRefresh from the entry.
    items_map_section = block.split("buildMealReviewV2ItemHtml(item, {", 1)[1].split("}).join('')", 1)[0]
    assert "pendingRefresh: entry.pendingRefresh" in items_map_section


def test_item_builder_extracts_pending_refresh_from_opts():
    block = _v2_block()
    item_fn = block.split("function buildMealReviewV2ItemHtml", 1)[1].split("\n    function ", 1)[0]
    assert "const pendingRefresh = !!opts.pendingRefresh;" in item_fn


# ── Candidate chips ───────────────────────────────────────────────────────────

def test_candidate_chips_disabled_during_pending_refresh():
    block = _v2_block()
    item_fn = block.split("function buildMealReviewV2ItemHtml", 1)[1].split("\n    function ", 1)[0]
    assert "data-action=\"choose-candidate\" data-candidate-id=\"${escapeHtml(c.candidate_id)}\"${pendingRefresh ? ' disabled' : ''}" in item_fn


# ── Portion edit controls ─────────────────────────────────────────────────────

def test_portion_edit_input_disabled_during_pending_refresh():
    block = _v2_block()
    item_fn = block.split("function buildMealReviewV2ItemHtml", 1)[1].split("\n    function ", 1)[0]
    assert "data-field=\"portion-text\"" in item_fn
    portion_section = item_fn.split("data-action=\"portion-edit-form\"", 1)[1].split("</form>", 1)[0]
    assert "required${pendingRefresh ? ' disabled' : ''}" in portion_section


def test_portion_edit_cancel_disabled_during_pending_refresh():
    block = _v2_block()
    item_fn = block.split("function buildMealReviewV2ItemHtml", 1)[1].split("\n    function ", 1)[0]
    portion_section = item_fn.split("data-action=\"portion-edit-form\"", 1)[1].split("</form>", 1)[0]
    assert "data-action=\"portion-edit-cancel\"${pendingRefresh ? ' disabled' : ''}" in portion_section


def test_portion_edit_update_disabled_during_pending_refresh():
    block = _v2_block()
    item_fn = block.split("function buildMealReviewV2ItemHtml", 1)[1].split("\n    function ", 1)[0]
    portion_section = item_fn.split("data-action=\"portion-edit-form\"", 1)[1].split("</form>", 1)[0]
    assert "btn btn-primary\"${pendingRefresh ? ' disabled' : ''}>Update" in portion_section


def test_portion_edit_open_disabled_during_pending_refresh():
    block = _v2_block()
    item_fn = block.split("function buildMealReviewV2ItemHtml", 1)[1].split("\n    function ", 1)[0]
    assert "data-action=\"portion-edit-open\"${pendingRefresh ? ' disabled' : ''}" in item_fn


# ── Skip / Delete / Restore controls ─────────────────────────────────────────

def test_skip_item_disabled_during_pending_refresh():
    block = _v2_block()
    item_fn = block.split("function buildMealReviewV2ItemHtml", 1)[1].split("\n    function ", 1)[0]
    assert "data-action=\"skip-item\"${pendingRefresh ? ' disabled' : ''}" in item_fn


def test_delete_item_disabled_during_pending_refresh():
    block = _v2_block()
    item_fn = block.split("function buildMealReviewV2ItemHtml", 1)[1].split("\n    function ", 1)[0]
    assert "data-action=\"delete-item\"${pendingRefresh ? ' disabled' : ''}" in item_fn


def test_restore_item_disabled_during_pending_refresh():
    block = _v2_block()
    item_fn = block.split("function buildMealReviewV2ItemHtml", 1)[1].split("\n    function ", 1)[0]
    assert "data-action=\"restore-item\"${pendingRefresh ? ' disabled' : ''}" in item_fn


# ── Save / Discard buttons stay unaffected by this issue ─────────────────────

def test_discard_button_has_no_pending_refresh_disabled():
    block = _v2_block()
    # actionsHtml has two branches: the `allRemoved` true-branch (discard-log)
    # and the false-branch (Discard + Save). Target the false-branch.
    normal_branch = block.split("const actionsHtml = allRemoved ?", 1)[1].split("`;\n", 1)[0]
    normal_branch = normal_branch.split(": `", 1)[1]
    assert "data-action=\"discard\"" in normal_branch
    discard_line = [ln for ln in normal_branch.splitlines() if 'data-action="discard"' in ln][0]
    assert "pendingRefresh" not in discard_line


def test_save_button_disabled_only_by_blocked_state():
    block = _v2_block()
    normal_branch = block.split("const actionsHtml = allRemoved ?", 1)[1].split("`;\n", 1)[0]
    normal_branch = normal_branch.split(": `", 1)[1]
    save_line = [ln for ln in normal_branch.splitlines() if 'data-action="save"' in ln][0]
    assert "${blocked ? ' disabled' : ''}" in save_line
    assert "entry.pendingRefresh" not in save_line


# ── Early return guard in submitMealV2Refresh unchanged ──────────────────────

def test_submit_refresh_early_return_guard_unchanged():
    block = _v2_block()
    refresh_fn = block.split("async function submitMealV2Refresh", 1)[1].split("\n    }", 1)[0]
    assert "if (!entry || entry.pendingRefresh) return;" in refresh_fn


# ── CSS: pointer-events rule for disabled elements ────────────────────────────

def test_css_disabled_pointer_events_within_refreshing_card():
    assert ".meal-review-v2--refreshing :disabled" in APP_CSS
    assert "pointer-events: none" in APP_CSS
