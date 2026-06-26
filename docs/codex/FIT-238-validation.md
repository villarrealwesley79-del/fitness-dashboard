# FIT-238 Validation

## Automated Checks

- `python3 -m pytest -q tests/test_fit192_accessibility_contract.py tests/test_button_styles_contract.py tests/test_dashboard_render_contract.py tests/test_fit137_adaptation_ui.py tests/test_fit219_review_card_a11y.py tests/test_fit139_refresh_ui`
  - Result: `39 passed in 0.06s`
- `python3 -m pytest -q tests/test_fit192_accessibility_contract.py tests/test_button_styles_contract.py tests/test_dashboard_render_contract.py tests/test_fit137_adaptation_ui.py tests/test_fit219_review_card_a11y.py tests/test_fit139_refresh_ui.py tests/test_csrf_protection.py tests/test_dashboard_retry_contract.py tests/test_fit145_offline_queue.py`
  - Result after review fixes: `80 passed in 0.15s`
- `git diff --check`
  - Result: exit 0, no whitespace errors.
- `python3 -m pytest -q`
  - Result after review fixes: `1056 passed in 14.73s`
- `node --check static/js/app.js`
  - Result: exit 0.

## Browser Validation

Runtime:
- Command: `DATA_DIR=/tmp/fit238-validation-runtime SECRET_KEY=fit238-validation-secret SESSION_COOKIE_SECURE=false PORT=5052 python3 app.py`
- URL: `http://127.0.0.1:5052`
- Browser: Playwright CLI session `fit238`.
- Browser after review fixes: Playwright via Chrome executable `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`.
- Auth: disposable local registration user `fit238`; runtime data isolated under `/tmp/fit238-validation-runtime`.

Validated:
- `/register` loaded in a clean browser session and exposed labeled Username, Email, Password controls.
- Disposable user registration succeeded and redirected to `/`.
- Dashboard loaded in authenticated first-run state.
- `View food log` opened the food-log sheet; browser snapshot exposed it as `dialog "Food log"`.
- Modal focus check:
  - Initial active element was the close button inside the dialog.
  - `Shift+Tab` kept focus inside the dialog.
  - `Tab` kept focus inside the dialog.
- `Escape` closed the food-log modal and restored focus to `btn-view-food-log`.
- Mobile viewport `390x844`:
  - `.tab-bar` `scrollWidth` was `390`.
  - `.tab-bar` `clientWidth` was `390`.
  - Overflow result was `false`.
  - Settings tab right edge was `384`, fully inside the viewport.
  - Viewport meta was `width=device-width, initial-scale=1.0, viewport-fit=cover`.
- Settings tab:
  - Settings labels exist for date of birth, sex, duration, sessions per week, and equipment.
  - Training goals render inside `role="radiogroup"`.
  - Goal options expose `role="radio"`.
  - Exactly one observed option exposed `aria-checked="true"` in the first-run state.
- Post-review browser checks:
  - FIT-238 CSS and JS asset URLs loaded: `/static/css/style.css?v=20260625-fit238-qol` and `/static/js/app.js?v=20260625-fit238-qol`.
  - `/sw.js` exposed cache name `fitness-dashboard-v20260625-fit238-qol`.
  - Mobile viewport `390x844` had no tab-bar overflow: `scrollWidth=390`, `clientWidth=390`.
  - Training goal `ArrowRight` changed the checked radio while keeping exactly one `aria-checked="true"` and one tabbable radio; focus stayed on a radio.
  - Source-viewer pattern: `Tab` reached the iframe, then the post-iframe sentinel returned focus to the close button inside the modal.
  - Active workout modal guard: `Tab` cycled from the last button to the first button while `Escape` did not close `modal-active`.

Screenshots copied to:
- `/tmp/fit238-validation-artifacts/page-2026-06-26T01-44-07-903Z.png`
- `/tmp/fit238-validation-artifacts/page-2026-06-26T01-44-23-333Z.png`
- `/tmp/fit238-validation-artifacts/page-2026-06-26T01-44-37-931Z.png`

Console caveat:
- The browser showed three expected local empty-integration errors:
  - `500` for `/api/oura/sleep-summary`
  - `503` for `/api/oura/status`
  - `503` for `/api/oura/status?refresh=true`
- The final disposable runs also showed expected local `401`/`404` noise for unauthenticated health/favicon requests before the session settled.
- These are consistent with the disposable runtime lacking Oura data/config and are not caused by FIT-238 static/template accessibility changes.

## Known Caveats

- Initial full-suite baseline had one flaky UUID substring failure in FIT-136; later reruns reported `1051 passed` before implementation and `1056 passed` after implementation.
- Several deeper meal, workout, Oura, Apple Health, push, and populated-history states require credentials, hardware, or seeded runtime data and were not changed by FIT-238.
