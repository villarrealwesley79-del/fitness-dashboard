# FIT-306 active workout mobile browser QA

`tests/test_fit306_active_workout_browser_qa.py` boots the real Flask app with
an isolated temporary `DATA_DIR`, opens the production template at a 390x844
mobile viewport, and drives the production `static/js/app.js` active-workout
renderer and control handlers. It does not use production data or services.

The matrix covers recovered draft, dirty close, swap, adjust, delete/remove,
set completion, cardio completion, save error, queued, conflicted, saved,
empty, blocked, and warning states. Every state asserts its production DOM copy,
captures a temporary screenshot PNG, and measures the topmost visible modal.
The layout assertion proves the modal stays inside the viewport and its controls
cannot be obscured by the bottom nav: controls either end above the nav or the
visible modal owns the higher stacking context.

The completion endpoint is mocked with deterministic sync outcomes so response
mapping is exercised without intentional browser resource errors. Any browser
error or warning fails the test.

Run the focused proof with:

```bash
venv/bin/python -m pytest -q tests/test_fit306_active_workout_browser_qa.py
```

The test uses the Codex Playwright CLI wrapper when installed. In ordinary CI
it runs the exact transient `@playwright/cli@0.1.17` package through `npx`; a
missing runner is a failure, not a skipped browser gate. The test also runs the
pinned CLI's idempotent Chromium installer before launch so a clean CI worker
does not depend on a pre-populated browser cache. The query-gated
`fit306_qa=1` hook is absent during ordinary app navigation and only supplies
local fixture state to the shipped renderer.
