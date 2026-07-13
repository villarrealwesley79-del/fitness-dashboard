# FIT-320 meal photo and offline replay browser QA

`tests/test_fit320_meal_photo_offline_browser_qa.py` boots the real Flask app
with an isolated temporary `DATA_DIR`, opens the production template with a
390x844 iPhone user agent, 3x device scale, and touch emulation, and drives the
production `static/js/app.js` meal composer and sync queue. It does not use
production data or services: the server child gets a scrubbed environment and
temporary home, an isolated Apple Health database and test token, and unrelated
integration status requests are browser-routed to local fixtures. Every key
declared by a checkout-local `.env` is shadowed in the child before `app.py`
imports it; no `.env` value is copied into the test server.

The browser proof selects five images and verifies the four-photo cap, rejects
an image larger than 6 MB, removes a selected thumbnail, and observes object URL
revocation. The fixtures are decodable 1x1 PNGs with distinct bytes and the
proof requires nonzero rendered preview dimensions. It then saves a two-photo meal offline and reads the real IndexedDB
stores to prove both photo values are non-empty `Blob` records. It then reloads
the production shell from the isolated local server while forcing the app's
`navigator.onLine === false` branch, and repeats those storage and pending-UI
checks before reconnecting. This proves IndexedDB boot persistence; it does not
claim the network-first service worker can load the app shell with browser
network access disconnected.

Reconnection exercises the shipped online event handler and a deterministic
successful meal-intake response. That response is allowed only after the
isolated fixture endpoint observes both multipart `images` parts in their
original order with exact filename, MIME type, byte size, and SHA-256. The proof then waits
until both the queued meal and its blobs are evicted. A second offline meal is
discarded through the visible sync queue and independently proves blob deletion.
Screenshots are temporary test artifacts; the repository stores only this state
matrix and runbook.

Run the focused proof with:

```bash
FIT320_LIVE_BROWSER_QA=1 venv/bin/python -m pytest -q tests/test_fit320_meal_photo_offline_browser_qa.py
```

The test uses the pinned transient `@playwright/cli@0.1.17` package through
`npx` and installs Chromium idempotently. A missing browser runner is a failure,
not a skipped gate once explicitly enabled. The browser case is marked
`allow_net` and skipped unless `FIT320_LIVE_BROWSER_QA=1`, keeping npm/CDN
downloads out of normal pytest and CI as required by `docs/testing.md`. The
state-matrix and runbook contract checks remain in the default suite.
Each Playwright subprocess is bounded by a timeout; its working directory, npm
cache, home, CLI artifacts, and browser installation all live inside pytest's
temporary directory. Each npx invocation runs in an owned process group that is
killed and reaped on timeout. The test mocks authentication scope and replay responses,
but all file selection, validation, IndexedDB persistence, reconnect replay,
queue rendering, discard handling, and cleanup execute through production code.
