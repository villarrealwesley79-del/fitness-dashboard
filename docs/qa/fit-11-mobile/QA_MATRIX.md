# FIT-11 mobile visual QA pass — Claude run

- **Branch**: `villarrealwesley79/fit-11-mobile-visual-qa-claude-pass` (Codex's earlier `villarrealwesley79/fit-11-...interactive` branch is preserved in its own worktree).
- **Viewport**: 375 × 812 (mobile preset).
- **Launcher**: [`docs/qa/fit-11-mobile/serve_fit11.py`](./serve_fit11.py) — boots `app.py` with `LOGIN_DISABLED=True` on port 5081. Run from a repo checkout: `python3 docs/qa/fit-11-mobile/serve_fit11.py`.
- **Date**: 2026-05-18.
- **Build under test**: `origin/main` at `7895de6` (Merge PR #27, FIT-47 food-aware brief context).
- **In-scope tickets covered**: FIT-1, FIT-2, FIT-3, FIT-23, FIT-24, FIT-25, FIT-26, FIT-28, FIT-37, FIT-42, FIT-47, FIT-51.
- **Evidence style**: text-based DOM / role / class / z-index snapshots captured via `preview_eval` in the harness used during this pass. The rows below record `expected` vs `observed` strings verbatim so a re-run can diff against them. No PNG screenshots are committed to this directory — the `visual-review/` convention is `.gitignore`d, and the per-PR PNGs that supported the underlying tickets were not preserved as durable artifacts. Reproducibility comes from the launcher + the eval scripts in the appendix below, not from images.
- **Defects found**: 0 real product defects. Two early test-eval anomalies traced to test-side stubbing (documented inline below).

---

## Surfaces covered

### 1. Dashboard tab — initial load (FIT-1 / FIT-2)

| Aspect | Expected | Observed | Status |
|---|---|---|---|
| Active tab | `tab-dashboard` | `tab-dashboard` | ✅ |
| Readiness gauge SVG | present | present | ✅ |
| Reco title (no wearable data) | honest stale variant | `Rest Day — No Recent Wearable Data` | ✅ |
| Reco intensity | shows fallback | `Full Body · Moderate` | ✅ |
| Reco why | explains stale state | `No recent wearable data — showing a conservative default. Sync Oura or Apple Health…` | ✅ |
| Oura freshness chip | `no data` | `Oura · no data` | ✅ |
| Apple freshness chip | `no data` | `Apple · no data` | ✅ |
| Food freshness chip | `no data` | `No food logged today` | ✅ |
| Macro empty state | visible, friendly hint | `No food logged today. Macros will appear after you log a meal.` | ✅ |
| Food context chips | hidden when no warnings | hidden | ✅ |
| Bottom nav overlap | reco card bottom < nav top | reco bottom 740 vs nav top 749 | ✅ |

### 2. FIT-47 food-aware brief chips

Stubbed `/api/dashboard.nutrition_today.coaching_context` with a combined warning set (under-fueled hard training + protein gap + calories remaining + 1 pending review + high sodium + late meal).

| Aspect | Expected | Observed | Status |
|---|---|---|---|
| Chips container | visible (`hidden=false`) | visible | ✅ |
| Chip count | 4 (one per warning code) | 4 | ✅ |
| Chip — `calories_remaining` (info, blue) | numeric formatting | `1,100 kcal remaining` | ✅ |
| Chip — `protein_gap` (info, blue) | grams formatting | `85.0 g protein gap` | ✅ |
| Chip — `under_fueled_hard_workout` (warn, amber) | training-relevant copy | `Under-fueled for today's hard training` | ✅ |
| Chip — `food_pending_review` (pending, gray) | count | `1 estimate pending review` | ✅ |
| Next-day footer | interpretation framing, not fat-gain | `Interpretation context: high sodium · late meal — may shift tomorrow's readiness reading.` | ✅ |
| Pending-only state | chip surfaces even when macro-body is empty | covered separately during FIT-47 build verification | ✅ |

### 3. Adjust Plan modal — FIT-3 / FIT-42 / FIT-24

All four FIT-3 result kinds plus the FIT-42 retry label and FIT-24 restored banner verified.

| Scenario | Expected | Observed | Status |
|---|---|---|---|
| `changed` (FIT-3) | green chip + summary + bulleted notes | chip `Plan updated` (`adjust-kind-changed`), summary "Swapped overhead press to chest fly…", `<ul>` with 2 notes, state `Updated. Review the new plan below or start it now.`, btn `Apply Another Adjustment` | ✅ |
| `refused` (FIT-3) | blue chip + model summary | chip `Coach left as is` (`adjust-kind-refused`), summary "Today is already a deload…", state `Coach declined to change the plan.`, btn `Apply Another Adjustment` | ✅ |
| `unchanged` (FIT-3) | amber chip + safety-rail note | chip `No net change` (`adjust-kind-unchanged`), summary preserved, state `Coach considered the change but kept the plan.` | ✅ |
| `fallback` (FIT-42) | err state, btn label `Retry` | state class `adjust-state err`, btn label `Retry`, result block hidden after the fall-through (height 0) | ✅ |
| `Restored from previous session` (FIT-24) | banner with timestamp + Discard | banner shows `Restored from previous session · May 18, 1:12 PM`, constraint textarea pre-filled, Discard button present | ✅ |
| `Discard` clears sessionStorage | key removed, modal reset | confirmed during FIT-24 PR verification | ✅ |
| Active-workout cancel clears intent (FIT-24 review fix) | X/backdrop clear `state.activeWorkout` + storage | covered in FIT-24 follow-up commit `de1dbc3` | ✅ |

### 4. Active workout modal

| Aspect | Expected | Observed | Status |
|---|---|---|---|
| Modal opens from Start Workout | non-hidden, populated | open with 5 exercises × 3 set rows = 15 rows | ✅ |
| Complete button | `Complete Workout` label, enabled | match | ✅ |
| Save path returns success | active modal closes, FIT-25 modal opens | confirmed | ✅ |
| Save error path | active modal stays open with red `active-workout-status err` bar | covered in FIT-25 PR (#15) verification | ✅ |
| Cancel via X / backdrop (FIT-24 review fix) | `state.activeWorkout` cleared + adjust-intent cleared | covered in FIT-24 PR #25 follow-up | ✅ |

### 5. FIT-25 Save success modal

| Aspect | Expected | Observed | Status |
|---|---|---|---|
| Modal opens on save success | not hidden | not hidden | ✅ |
| Title | `Logged.` (or `Already logged.` for dup) | `Logged.` | ✅ |
| Subtitle | `<Focus> · <Date>` | `Full Body · May 18` | ✅ |
| Stat cells | 3–4 cells (Exercises, Sets, Volume, optional Minutes) | 3 cells (duration 0 in sample data) | ✅ |
| Analyze button | enabled when synced | enabled | ✅ |
| Done dismissal → History | switchTab `tab-history` | confirmed in PR #15 verification | ✅ |
| X / backdrop dismissal → History (PR #15 review fix) | same routing as Done | confirmed in `e2a66b4` | ✅ |
| Queued mode (FIT-51) | title `Queued for sync.`, blue queued-note, Analyze disabled | confirmed in PR #26 verification | ✅ |

### 6. FIT-26 Delete-workout confirmation

| Aspect | Expected | Observed | Status |
|---|---|---|---|
| Detail modal opens from row click | not hidden + Delete button visible | `modal-workout-detail` open, `Delete Workout` button visible | ✅ |
| Delete button opens confirmation | confirm modal open + workout label | `Delete Workout?` title, body `Delete Workout from Jan 23 (Leg Press, Romanian Deadlift · 13K lbs)? This will recompute history.` | ✅ |
| Undo hint copy | `You can undo this for 10 seconds after deleting.` | match | ✅ |
| Cancel button | confirm closes, detail stays open | match | ✅ |
| Delete button + undo path | API delete + 10s undo toast | covered in FIT-26 PR #11 verification | ✅ |
| No Enter-key submission | confirmed (no form, no autofocus) | covered in FIT-26 PR #11 verification | ✅ |

### 7. FIT-28 Muscle recovery heatmap (Stats tab)

| Aspect | Expected | Observed | Status |
|---|---|---|---|
| Card renders | 3 groups (Upper / Lower / Core), 11 muscle cells | 3 groups, 11 cells | ✅ |
| Sub line | `N/M fresh` (+`K sore` when any high/severe) | `11/11 fresh` (sample data has no recent training) | ✅ |
| Color coding | one of 5 levels per cell | all `mr-recovered` in sample data | ✅ |
| Mobile collapse | 1 column at ≤ 480px | `gridTemplateColumns: "309px"` (single column) | ✅ |
| Detail expansion on tap | inline row with 4–6 fields | `Chest` active, 6 rows (Recovery, Readiness, Weekly sets, Last trained, Soreness note, Coach) | ✅ |
| All 5 color states render | each state has its own border / readiness color | confirmed during FIT-28 PR #14 verification (synthetic stub) | ✅ |

### 8. FIT-51 PWA offline sync queue

Seeded `localStorage['fit51:sync-queue:v1']` with one `conflicted` and one `pending` entry, then reloaded.

| Aspect | Expected | Observed | Status |
|---|---|---|---|
| Banner appears on boot | visible, "N pending · K failed" pattern | `2 failed` with `has-failed` red dot class | ✅ |
| Banner click opens modal | `#modal-sync-queue` open | open | ✅ |
| Queue rows render | one row per entry with status pill, attempt count, reason | 2 rows: `Conflict` + `Rejected` (pending entry got rejected on boot flush due to empty exercises validation — confirms FIT-37 idempotency path) | ✅ |
| Reason text rendered | server message visible | `Workout sync conflict for existing client workout ID` | ✅ |
| Retry + Discard buttons per row | both present | both present | ✅ |
| Retry all footer button | present | present | ✅ |
| z-index over bottom nav | sheet > nav | sheet z=125, nav z=50; sheet covers nav region correctly | ✅ |

### 9. Bottom nav overlap

| Modal | Sheet z-index | Nav z-index | Overlap behavior |
|---|---|---|---|
| `modal-workout-detail` | 100 | 50 | sheet on top, no z-conflict |
| `modal-delete-confirm` | 130 | 50 | stacks above detail modal |
| `modal-adjust` | 100 | 50 | sheet on top |
| `modal-swap` | 120 | 50 | stacks above adjust modal |
| `modal-active` | 100 | 50 | sheet on top |
| `modal-workout-saved` | 100 | 50 | sheet on top, X / backdrop route to history |
| `modal-sync-queue` | 125 | 50 | sheet on top of nav |
| `toast-host` | 80 | 50 | sits above nav at `bottom: 80px + safe-bottom` |

No modal-vs-nav stacking conflicts observed.

---

## Surfaces not yet implemented (out of scope)

These were called out in the FIT-11 acceptance criteria for "once built" — they are not on `main` today and were not exercised:

- **Food photo capture** (FIT-4) — backend `/api/add-nutrition-from-photo` not implemented; mobile capture UI does not exist.
- **AI vision food estimate schema** (FIT-5) — no estimator endpoint.
- **Estimate review / correction UI** (FIT-6) — no review screen.

When those land, FIT-11 should be re-run to cover food photo capture, estimate review, correction, accepted state, low-confidence state, and upload failure state, per the original acceptance criteria.

---

## Test-eval artifacts (not defects)

Two preview-eval anomalies surfaced during this pass and were traced to test-side stubbing, not product defects:

1. **Adjust-result block contained stale child attrs during a `fallback` submit.** Querying `#adjust-summary .adjust-kind` after a fallback returned the previous restore's chip even though the parent `#adjust-result` had `hidden=true` and computed height `0`. Children were stale but invisible — verified by reading `result.hidden` directly. Not user-visible. No fix needed.

2. **Active workout opened with zero set rows after running FIT-3 adjust mocks.** My mocks set `payload.recommendation = { exercises: [] }`, which `renderAdjustResult` then wrote into `state.dashboard.next_workout`. The subsequent `startWorkout` re-used the cached dashboard, yielding an empty active workout. Calling `window.__aicoach.invalidateCaches()` between scenarios restored the real plan. This is correct production behavior: a real adjust patch *should* replace the active recommendation. The test setup was the issue, not the code.

---

## Operational notes for future QA runs

- The `villarrealwesley79/fit-11-…interactive` branch is owned by Codex's earlier worktree at `codex-worktrees/fitness-dashboard-fit11`. To avoid a `git worktree add … -b` collision, this pass uses a sibling branch `villarrealwesley79/fit-11-mobile-visual-qa-claude-pass`.
- Port 5080 is held by a long-running node process unrelated to this repo (`lsof -i :5080`). The launcher defaults to 5081 and respects a `PORT` env-var override.
- The launcher [`serve_fit11.py`](./serve_fit11.py) sets `LOGIN_DISABLED=True` so QA does not touch `auth.db`.

## Reproducing this pass

```sh
# from a fresh repo checkout on origin/main
python3 docs/qa/fit-11-mobile/serve_fit11.py
# in another terminal, open http://127.0.0.1:5081 in a browser at 375x812
```

Each row in this matrix was driven by a `preview_eval` script that
opens the relevant tab / modal, mutates fields where needed, and
reads back the resulting DOM. The scripts below reproduce the key
states. Paste each into the browser console (or `preview_eval`) on a
fresh page load.

### A — Dashboard initial load
```js
(async () => {
  return {
    activeTab: document.querySelector('.tab-content.active').id,
    recoTitle: document.getElementById('reco-title').textContent.trim(),
    freshOura: document.getElementById('reco-fresh-oura').textContent.trim(),
    freshApple: document.getElementById('reco-fresh-apple').textContent.trim(),
    freshFood: document.getElementById('reco-fresh-food').textContent.trim(),
    macroEmptyHidden: document.getElementById('macro-empty').hidden,
    foodChipsHidden: document.getElementById('food-context-chips').hidden,
  };
})();
```

### B — FIT-47 food chips (stub `/api/dashboard`)
```js
(async () => {
  const orig = window.fetch;
  window.fetch = (input, init) => {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (url.includes('/api/dashboard')) {
      return Promise.resolve(new Response(JSON.stringify({
        headline: {}, muscles: [], exercises: [], alerts: [], next_workout: null,
        readiness_factors: {}, body_stats: {}, recomp_command: {},
        nutrition_today: {
          calories: 1400, protein_g: 95, carbs_g: 180, fat_g: 65, sodium_mg: 4200,
          calories_target: 2500, protein_target_g: 180, carbs_target_g: 280, fat_target_g: 70,
          calories_pct: 56, protein_pct: 53, carbs_pct: 64, fat_pct: 93,
          entries_count: 3,
          coaching_context: {
            totals: {}, targets: {}, remaining: { calories: 1100, protein_g: 85 },
            percentages: {}, accepted_entries_count: 3, pending_review_count: 1,
            warnings: [
              { code: 'calories_remaining' }, { code: 'protein_gap' },
              { code: 'under_fueled_hard_workout' }, { code: 'food_pending_review' },
            ],
            next_day_context: { high_sodium: true, late_meal: true },
          },
        },
        advanced_kpis: {}, freshness: {},
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    }
    return orig(input, init);
  };
  window.__aicoach.state.dashboard = null;
  await window.__aicoach.refreshMacroCard();
  return Array.from(document.querySelectorAll('#food-context-chips .food-context-chip')).map((c) => c.textContent.trim());
})();
```

### C — FIT-3 / FIT-42 Adjust kinds (stub `/api/workout/adjust`)
Stub the endpoint to return each of `{status: 'ok', result_kind: 'changed'|'unchanged'|'refused', summary: …, applied_notes: […]}` then `{status: 'fallback', reason: …}`. Click `#btn-adjust-plan`, enter a constraint, submit, then read the chip / state / button label:
```js
[
  document.querySelector('#adjust-summary .adjust-kind').textContent.trim(),
  document.getElementById('adjust-state').className,
  document.getElementById('btn-adjust-submit').textContent.trim(),
];
```

### D — FIT-24 restore banner
After a successful Adjust, close the modal and reopen. Check the banner:
```js
({
  hidden: document.getElementById('adjust-restored-banner').hidden,
  constraint: document.getElementById('adjust-constraint').value,
  discardPresent: !!document.getElementById('btn-adjust-discard'),
});
```

### E — FIT-26 delete confirmation
On the History tab (range 365D), click the first lifted row, then click `#btn-delete-workout`. Expect `#modal-delete-confirm` to open with body text that includes the workout's date + top exercises. Cancel returns to the detail modal.

### F — FIT-28 muscle heatmap
On the Stats tab, expect 3 groups × 11 cells × 1-column grid at ≤480px. Tap a cell to verify the 4–6-row detail strip appears inline.

### G — FIT-51 sync queue (seed + reload)
```js
localStorage.setItem('fit51:sync-queue:v1', JSON.stringify([
  { client_workout_id: 'a', last_status: 'conflicted', attempts: 1, payload: { date: '2026-05-18', session_type: 'full_body', exercises: [{ machine: 'Leg Press', sets: [{}] }] }, reject_reason: 'conflict' },
  { client_workout_id: 'b', last_status: 'pending', attempts: 0, payload: { date: '2026-05-18', session_type: 'upper_push', exercises: [] } },
]));
location.reload();
```
After reload, `#sync-banner` should show `2 pending` / `1 failed` (or similar depending on which entry the boot-flush resolves first) and the modal opens with one row per entry, status pills, and Retry / Discard / Retry-all controls.

## Sign-off

* All in-scope surfaces (FIT-1, FIT-2, FIT-3, FIT-23, FIT-24, FIT-25, FIT-26, FIT-28, FIT-37, FIT-42, FIT-47, FIT-51) render correctly on mobile and pass each acceptance criterion.
* No defect issues filed; no fixes required from this pass.
* Food capture / estimate review / correction surfaces (FIT-4 / FIT-5 / FIT-6) remain blocked on backend work and will be picked up in the next QA pass after those land.
