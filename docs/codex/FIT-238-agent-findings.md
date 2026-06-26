# FIT-238 Agent Findings Synthesis

Issue: `FIT-238`
Worktree: `/Users/admin/codex-worktrees/fitness-fit-238-qol`
Branch: `villarrealwesley79/fit-238-one-time-quality-of-life-pass-for-core-fitness-dashboard`

## Sources

- UI/UX agent: `docs/codex/FIT-238-uiux-agent.md`
- Architecture/code agent: `docs/codex/FIT-238-architecture-agent.md`
- Surface inventory agent: `docs/codex/FIT-238-surface-agent.md`
- Accessibility agent: returned findings in-thread; it did not write a report file.
- Verification agent: returned test and browser validation recommendations in-thread; it did not write a report file.

## Confirmed Findings Fixed In FIT-238

1. Mobile zoom was globally disabled by the viewport meta tag.
   - Fix: removed `maximum-scale=1.0, user-scalable=no` from `templates/index.html`.
   - Verification: `test_viewport_allows_browser_zoom`.

2. Modal shells had focus/Escape behavior but lacked explicit dialog semantics.
   - Fix: added `role="dialog"`, `aria-modal="true"`, and `aria-labelledby` to every static modal shell, adding title ids where needed.
   - Verification: `test_modal_markup_exposes_dialog_semantics_and_labels`.

3. Non-active modal focus could escape into background page controls with Tab.
   - Fix: added topmost-modal Tab and Shift+Tab focus cycling in `static/js/app.js`.
   - Verification: `test_modal_escape_and_focus_helpers_are_delegated_and_guard_active_workout`.

4. Log and Settings controls had visible labels that were not programmatically bound to controls.
   - Fix: added `for`/`id` label associations for core Log forms and Settings preferences.
   - Verification: `test_core_log_and_settings_controls_have_programmatic_labels`.

5. Bottom mobile tab bar overflowed at 390px because each tab required a 48px minimum width.
   - Fix: changed tab button flex sizing to `flex: 1 1 0`, `min-width: 0`, and tighter horizontal padding.
   - Verification: `test_mobile_tab_bar_does_not_force_horizontal_overflow` plus browser mobile check.

6. Training-goal picker exposed single-selection state only visually.
   - Fix: added `role="radiogroup"` to the host and `role="radio"`/`aria-checked` to generated goal buttons; decorative checkmarks are now `aria-hidden`.
   - Verification: `test_training_goal_picker_exposes_single_selection_state`.

## Deferred Findings

1. Active workout modal does not close on `Escape`.
   - Reason deferred: existing FIT-192 contract intentionally guards `#modal-active` from generic Escape close, likely to avoid accidental workout loss. This needs a separate product decision to route Escape through the guarded discard-confirm path.

2. Macro card header can read as `TODAY'S MACROSno entries` on fresh state.
   - Reason deferred: visible polish issue, but lower accessibility impact than the fixed barriers. It should be handled as a focused follow-up if desired.

3. Fresh no-data states can look like load failures in dashboard cards.
   - Reason deferred: requires distinguishing transport failures from empty/not-connected states in dashboard loaders. That touches data-state semantics and should be a separate issue.

4. Product naming differs across auth, shell, and browser title.
   - Reason deferred: copy/product decision, not a safe incidental fix.

## Chosen Implementation Scope

FIT-238 implements the high-impact accessibility and quality-of-life fixes that are static/template/JS/CSS scoped, testable, and low-risk. It does not change data APIs, persistence, workout state, nutrition calculations, or production integrations.
