"""FIT-134 — Frontend review UI contract tests.

The Linear acceptance list calls for visual QA covering 14 scenarios. Without
a Playwright harness in this project, the established pattern is to assert
against the JS source so each acceptance bullet has a traceable code path.

These tests sit next to ``test_meal_intake_api.py`` / ``test_meal_logging_e2e.py``
which exercise the legacy single-item flow. They specifically prove that the
new multi-item review path (FIT-134) is wired and does not regress the locked
contract agreed with Codex on 2026-05-22 (see
``/Users/admin/.claude/plans/codex-is-owrking-on-shiny-ripple.md``).
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")


def _v2_block() -> str:
    """Return the FIT-134 V2 block of app.js so individual assertions don't
    pick up matches from the legacy single-item review code.
    """
    start = APP_JS.find("// FIT-134 — Multi-item meal review (V2).")
    end = APP_JS.find("function clearMealComposerInputs()", start)
    assert start != -1 and end != -1, "FIT-134 V2 block markers not found"
    return APP_JS[start:end]


# ── Scenario 1: Collapsed view shows meal totals only (no per-item rows) ──

def test_collapsed_view_renders_meal_totals_only():
    """Collapsed-view markup carries meal_totals + meal-type chip; per-item
    rows live in the .meal-review-v2-expanded container which is hidden
    until the user expands.
    """
    block = _v2_block()
    assert "meal-review-v2-collapsed" in block
    assert "meal-review-v2-totals" in block
    assert "meal-review-v2-kcal" in block
    assert "meal-review-v2-macros" in block
    # The items list lives in the expanded container, not the collapsed one.
    assert "meal-review-v2-items" in block
    assert "meal-review-v2-expanded" in block
    assert "${expanded ? '' : ' hidden'}" in block


# ── Scenario 2: NL add item updates totals + items list ──

def test_add_item_uses_natural_language_text_only():
    block = _v2_block()
    assert "data-action=\"add-item-form\"" in block
    assert "data-field=\"add-item-text\"" in block
    # Refresh is wired as { kind: 'add_item', text }.
    assert "kind: 'add_item', text" in block
    # No portion-chip / numeric-macro inputs on the V2 add path.
    assert "meal-pending-portion-chips" not in block
    assert 'inputmode="numeric"' not in block


# ── Scenario 3: Branded added item triggers branded-lookup loading state ──

def test_branded_added_item_shows_lookup_state():
    block = _v2_block()
    # pendingRefresh state drives the "Looking up…" status indicator.
    assert "Looking up" in block
    assert "meal-review-v2--refreshing" in block
    # The mock recognizes branded names and assigns a branded source.
    assert "(heb|h-?e-?b|hot cheetos?|chipotle|bill miller|whataburger" in block
    assert "Branded lookup" in block


# ── Scenario 4: Source viewer in-app (no target=_blank) ──

def test_source_viewer_opens_in_app_iframe_not_target_blank():
    block = _v2_block()
    # In-app modal with a sandboxed iframe.
    assert "openMealV2SourceViewer" in block
    assert "meal-review-v2-source-frame" in block
    assert "sandbox=\"allow-same-origin\"" in block
    # The V2 source chip never carries target=_blank.
    v2_chip_section = block.split("meal-review-v2-source-chip", 2)[1]
    assert "target=\"_blank\"" not in v2_chip_section[:600]
    # Sanitizer rejects cross-origin URLs and non-http schemes by checking
    # against window.location.origin.
    assert "sanitizeMealV2SourceLink" in block
    assert "u.origin !== window.location.origin" in block
    # Defense-in-depth: protocol-relative URLs ("//evil.example.com/x")
    # would otherwise resolve to an attacker-controlled origin once placed
    # in an iframe src. The sanitizer rejects them explicitly before the
    # URL() parse, and also requires the resolved protocol to match the
    # current page's protocol.
    assert "value.startsWith('//')" in block
    assert "u.protocol !== window.location.protocol" in block


# ── Scenario 5: NL portion edit triggers refresh; no chips/numeric controls ──

def test_portion_edit_is_natural_language_only():
    block = _v2_block()
    assert "data-action=\"portion-edit-form\"" in block
    assert "data-field=\"portion-text\"" in block
    assert "kind: 'edit_portion', item_id: itemId, text" in block
    # The V2 item body does NOT render the portion-chip strip or numeric
    # macro inputs.
    after = block.split("function buildMealReviewV2ItemHtml", 1)[1]
    expanded_item = after.split("\n    function ", 1)[0]
    assert "meal-pending-portion-chip" not in expanded_item
    assert 'data-field="calories"' not in expanded_item


# ── Scenario 6: One-follow-up budget honored ──

def test_follow_up_budget_uses_server_used_flag():
    block = _v2_block()
    # Banner only shows when followup.available && !used && !local mirror.
    assert "entry.followup.available" in block
    assert "!entry.followup.used" in block
    assert "!entry.lastFollowupAnswered" in block
    # Submit posts followup_answer with the user's text in `answer`, matching
    # the FIT-144 backend (app.py followup_answer reads `answer` and
    # `skipped`, not `text`).
    assert "kind: 'followup_answer', answer: text" in block
    # The server-side `used` flag is mirrored on normalize so a refresh
    # that returns used=true keeps the banner dismissed.
    assert "lastFollowupAnswered = !!(payload.followup && payload.followup.used)" in block


# ── Scenario 7: Skipping the follow-up keeps unclear items save-blocked ──

def test_dismiss_follow_up_does_not_unblock_save():
    block = _v2_block()
    # Dismiss only flips the local mirror; never posts followup_answer and
    # never mutates save_blocked_item_ids.
    needle = 'data-action="followup-dismiss"'
    idx = block.find('if (dismissBtn) {')
    assert idx != -1, "followup-dismiss wireup not found"
    dismiss_section = block[idx:idx + 400]
    assert "entry.lastFollowupAnswered = true" in dismiss_section
    assert "kind: 'followup_answer'" not in dismiss_section
    assert "save_blocked_item_ids" not in dismiss_section
    # The dismiss button selector is wired with the expected data-action.
    assert needle in block


# ── Scenario 8: All-items-deleted shows Discard log; DELETE called ──

def test_all_items_deleted_replaces_save_with_discard_log():
    block = _v2_block()
    # When every included item is gone, the actions row swaps to a single
    # destructive button.
    assert "data-state=\"all-removed\"" in block
    assert "data-action=\"discard-log\"" in block
    assert "Discard log" in block
    # The Discard log button calls the same discardMealV2 path as plain
    # Discard (which posts DELETE /api/meal-intake/<meal_id>).
    idx = block.find("const discardLogBtn")
    assert idx != -1, "discard-log button wireup not found"
    discard_log_section = block[idx:idx + 220]
    assert "discardMealV2(mealId)" in discard_log_section
    # discardMealV2 uses DELETE /api/meal-intake/<meal_id>.
    delete_section = block.split("async function discardMealV2", 1)[1].split("async function", 1)[0]
    assert "method: 'DELETE'" in delete_section
    assert "/api/meal-intake/${encodeURIComponent(mealId)}" in delete_section


# ── Scenario 9: Expanded details show per-item fields ──

def test_expanded_details_show_item_fields():
    block = _v2_block()
    # Slice to the per-item builder; "function buildMealReviewV2ItemHtml"
    # body ends at the next "function " definition in the block.
    after = block.split("function buildMealReviewV2ItemHtml", 1)[1]
    item_html = after.split("\n    function ", 1)[0]
    assert "meal-review-v2-item-name" in item_html
    assert "meal-review-v2-item-portion" in item_html
    assert "meal-review-v2-item-macros" in item_html
    assert "meal-review-v2-item-conf" in item_html
    assert "meal-review-v2-source-chip" in item_html


# ── Scenario 10: Blocked-save auto-expands save_blocked_item_ids ──

def test_blocked_save_auto_expands_offending_items():
    block = _v2_block()
    # Render-time: items in save_blocked_item_ids open expanded.
    assert "blockedSet.has(item.item_id)" in block
    # After each refresh, the applier auto-adds blocked ids to the expanded
    # set so a fresh block opens the right item.
    apply_section = block.split("function applyMealV2Refresh", 1)[1].split("}\n", 1)[0]
    assert "entry.save_blocked_item_ids" in apply_section
    assert "entry.expandedItems.add(id)" in apply_section
    # Save button is disabled while any item is blocked.
    assert "blocked || entry.pendingRefresh" in block
    assert "Resolve items to save" in block


# ── Scenario 11: Add and Delete item flows update list + totals ──

def test_add_and_delete_item_refresh_payload_replaces_state():
    block = _v2_block()
    # Each refresh response fully replaces local entry state (no JSON
    # patch path), per the locked contract refresh invariant.
    apply_section = block.split("function applyMealV2Refresh", 1)[1].split("}\n", 1)[0]
    assert "normalizeMealV2Entry(payload)" in apply_section
    assert "upsertMealV2Entry(entry)" in apply_section
    # delete_item is wired.
    assert "data-action=\"delete-item\"" in block
    assert "kind: 'delete_item', item_id: itemId" in block
    # add_item is wired.
    assert "kind: 'add_item', text" in block


# ── Scenario 12: Skip unclear item unblocks Save when no other blocks remain ──

def test_skip_item_routes_through_backend_recompute():
    block = _v2_block()
    # Skip posts skip_item; backend recomputes save_blocked_item_ids and
    # the frontend re-renders with the new state.
    assert "kind: 'skip_item', item_id: itemId" in block
    # restore_item is wired so the user can Undo a skip/delete — required
    # because skipped/deleted items stay in items[] per the contract.
    assert "kind: 'restore_item', item_id: itemId" in block
    assert "data-action=\"restore-item\"" in block
    # The undo affordance only renders on removed items.
    assert "meal-review-v2-item-actions--removed" in block
    # Mock backend recompute proves the rule for local exercise.
    mock_recompute = block.split("recompute(payload) {", 1)[1].split("        }", 1)[0]
    assert "it.status === 'included' && it.unclear" in mock_recompute
    assert "payload.save_blocked_item_ids =" in mock_recompute


# ── Scenario 13: Meal type inferred + editable via collapsed-view chip ──

def test_meal_type_inferred_and_editable():
    block = _v2_block()
    # Backend returns meal_type; frontend renders it as a <select> in the
    # collapsed head, and change posts set_meal_type refresh.
    assert "data-action=\"set-meal-type\"" in block
    assert "kind: 'set_meal_type', meal_type: next" in block
    # Mock inferMealType drives the default for local exercise.
    assert "inferMealType(text)" in block
    assert "breakfast|eggs?|bagel|cereal|oatmeal|pancake" in block


# ── Scenario 14: No workout-skip / low-confidence-workout copy in food log UI ──

def test_food_log_review_card_has_no_workout_skip_messaging():
    """The V2 review card must not surface workout adaptation skip reasons.
    Workout messaging stays in workout surfaces only (covered by FIT-137).
    """
    block = _v2_block()
    forbidden = [
        "Workout updated",
        "Workout skipped",
        "Workout adaptation",
        "workout-skip",
        "low-confidence-workout",
        "confidence too low",
    ]
    for needle in forbidden:
        assert needle.lower() not in block.lower(), (
            f"FIT-134 acceptance: the food log review card must not mention "
            f"workout adaptation. Found {needle!r} in the V2 block."
        )


# ── Cross-cutting: the duck-typed gate doesn't break the legacy path ──

def test_legacy_single_item_path_still_wired():
    """FIT-134 only adds a V2 branch; the legacy single-item review card
    (buildMealPendingRow) and its accept/discard endpoints must stay live
    so the production path keeps working until FIT-135 lands the new
    backend contract.
    """
    assert "function buildMealPendingRow(entry)" in APP_JS
    assert "function acceptMealPending(clientId, rowEl)" in APP_JS
    assert "function discardMealPending(clientId)" in APP_JS
    # The duck-typed gate routes V2 payloads but does not delete the
    # legacy branches.
    branch = APP_JS.split("function handleMealIntakeResponse(payload, ctx) {", 1)[1].split("\n    }\n", 1)[0]
    assert "isMealV2Payload(payload)" in branch
    assert "handleMealIntakeV2Response(payload, ctx)" in branch
    assert "status === 'logged'" in branch
    assert "status === 'pending_review'" in branch


def test_contract_refresh_kinds_match_locked_plan():
    """Every refresh kind from the locked contract is supported, and only
    those kinds are accepted by submitMealV2Refresh.
    """
    block = _v2_block()
    expected = {
        "add_item", "edit_portion", "followup_answer", "choose_candidate",
        "skip_item", "delete_item", "restore_item", "set_meal_type",
    }
    enum_line = block.split("const MEAL_V2_REFRESH_KINDS = [", 1)[1].split("];", 1)[0]
    for kind in expected:
        assert f"'{kind}'" in enum_line, f"refresh kind {kind!r} missing"


def test_pending_hydration_routes_v2_entries_through_normalize():
    """FIT-144's `/api/meal-intake/pending` returns saved v2 snapshots
    (meal_id + items[]) alongside legacy single-item entries. After a
    reload, those v2 entries must hydrate into the v2 review card so
    FIT-134's "Review UI carries the same meal_id through refresh/save/
    discard" acceptance criterion holds across page reloads. Legacy
    entries must keep routing through upsertMealPendingEntry.
    """
    block = APP_JS  # hydrateMealPending lives outside _v2_block
    hydrate = block.split("async function hydrateMealPending", 1)[1].split("\n    }\n", 1)[0]
    assert "isMealV2Payload(entry)" in hydrate
    assert "normalizeMealV2Entry(entry)" in hydrate
    assert "upsertMealV2Entry(" in hydrate
    # Legacy fallback stays wired so single-item entries still hydrate.
    assert "upsertMealPendingEntry(entry)" in hydrate


def test_item_and_candidate_portion_reads_live_backend_field():
    """FIT-144 backend exposes the review item's portion text as `portion`
    (app.py:_review_item_from_estimate sets `"portion": estimate.get(
    "portion_description")`) and the same for candidates. The frontend
    must read `item.portion` / `c.portion` first, falling back to
    `portion_description` so the mock backend (which keeps the schema
    field name) still works.
    """
    block = _v2_block()
    # Expanded item row prefers `item.portion`, with portion_description as
    # a fallback so the mock data and tests keep working.
    assert "item.portion || item.portion_description" in block
    # Candidate chip renders `c.portion` with the same fallback.
    assert "c.portion || c.portion_description" in block


def test_live_accept_sends_meal_id_and_items_body():
    """FIT-144 _meal_intake_accept_multi (app.py:_meal_intake_accept_multi)
    expects a JSON body `{ meal_id, items: [{state, item_id, estimate, ...}] }`
    on POST /api/meal-intake/<meal_id>/accept. PR #123 was coded against an
    earlier draft that sent no body; the live backend would reject that
    with `items must be a list`. The backend re-sanitizes each item's
    `estimate` (requires `ambiguous` bool, `source` string, valid macros),
    so the frontend must pass the backend-owned `item.estimate` through
    untouched rather than building a fresh dict from the flattened item
    fields (which would drop `ambiguous` and send `source` as an object).
    """
    block = _v2_block()
    accept_section = block.split("async function acceptMealV2", 1)[1].split("async function", 1)[0]
    # Live path posts the body (mock path is allowed to short-circuit).
    assert "method: 'POST'" in accept_section
    assert "'Content-Type': 'application/json'" in accept_section
    assert "JSON.stringify(buildMealV2AcceptBody(entry))" in accept_section
    # Body builder exists, includes meal_id + items, and maps status -> state.
    builder_section = block.split("function buildMealV2AcceptBody", 1)[1].split("\n    function ", 1)[0]
    assert "meal_id: entry.meal_id" in builder_section
    assert "items:" in builder_section
    assert "state: MEAL_V2_ITEM_STATUSES.includes(item.status) ? item.status : 'included'" in builder_section
    # Reuse the backend's own per-item estimate (schema-clean) instead of
    # rebuilding one from the flattened top-level fields. Skipped/deleted
    # items still carry their estimate so backend negative-feedback
    # persistence (FIT-135) can read the phrase.
    assert "item.estimate" in builder_section
    assert "item.original_estimate" in builder_section


def test_live_refresh_injects_request_id_for_guarded_kinds():
    """FIT-144 backend at app.py:_REVIEW_REQUEST_ID_KINDS returns 400 without
    a `request_id` for add_item, edit_portion, choose_candidate, and
    followup_answer. A duplicate request_id with the same kind is treated as
    an idempotent replay (returns the prior payload, drops the new body), so
    the frontend must generate a fresh id per logical mutation attempt — not
    one per (meal_id, kind).
    """
    block = _v2_block()
    # The guarded set matches the backend constant exactly.
    guarded_line = block.split("const MEAL_V2_REQUEST_ID_KINDS = new Set([", 1)[1].split("])", 1)[0]
    for kind in {"add_item", "edit_portion", "choose_candidate", "followup_answer"}:
        assert f"'{kind}'" in guarded_line, f"guarded kind {kind!r} missing"
    # skip_item / delete_item / restore_item / set_meal_type are NOT guarded
    # (backend does not require request_id for those).
    for kind in {"skip_item", "delete_item", "restore_item", "set_meal_type"}:
        assert f"'{kind}'" not in guarded_line, (
            f"kind {kind!r} should not require request_id"
        )
    # Helper exists and mirrors the newMealClientId UUID-with-fallback pattern.
    assert "function mealV2GenerateRequestId()" in block
    assert "crypto.randomUUID" in block.split("function mealV2GenerateRequestId", 1)[1].split("\n    }", 1)[0]
    # submitMealV2Refresh injects request_id BEFORE the network call for
    # guarded kinds, and leaves it absent otherwise so the backend can
    # validate the 400 path correctly.
    submit_section = block.split("async function submitMealV2Refresh", 1)[1].split("async function", 1)[0]
    assert "MEAL_V2_REQUEST_ID_KINDS.has(body.kind)" in submit_section
    assert "mealV2GenerateRequestId()" in submit_section
    # The caller's body object is not mutated (a fresh object with the id
    # is constructed via spread).
    assert "{ ...body, request_id:" in submit_section


def test_css_contains_v2_review_styles():
    """Visual QA needs the new review card to be styled; assert the V2
    block actually shipped to style.css so the contract test isn't fooled
    by JS-only class names.
    """
    expected = [
        ".meal-review-v2",
        ".meal-review-v2-collapsed",
        ".meal-review-v2-expanded",
        ".meal-review-v2-totals",
        ".meal-review-v2-kcal",
        ".meal-review-v2-items",
        ".meal-review-v2-item--removed",
        ".meal-review-v2-item--blocked",
        ".meal-review-v2-candidate-chip",
        ".meal-review-v2-source-modal",
        ".meal-review-v2-source-frame",
        ".meal-review-v2-followup",
    ]
    missing = [s for s in expected if s not in APP_CSS]
    assert not missing, f"FIT-134 V2 styles missing from style.css: {missing}"
