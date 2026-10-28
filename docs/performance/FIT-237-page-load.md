# FIT-237 Page-Load Benchmark

## Target

Every reachable HTML page must finish `PerformanceNavigationTiming.loadEventEnd`
in under 50 ms under the repeatable local test conditions below.

`networkidle` is intentionally not the target metric because Playwright defines
it as a 500 ms quiet window, so even a one-request page reports roughly 500 ms.

## Conditions

- Server: Flask dev server, debug off, `DEBUG_TIMING=1`
- Data: `FIT_LOAD_SAMPLE_DATA=1` with isolated temp `DATA_DIR`
- Browser: system Google Chrome controlled by Playwright
- Viewport: 390 x 844
- Browser state: new temporary context per measured navigation
- Service workers: blocked
- Runs: one warmup, then 10 measured navigations per URL
- Target metric: `PerformanceNavigationTiming.loadEventEnd <= 50ms`

## URL Set

These are the reachable HTML pages in this checkout:

- `/login`
- `/register` before owner creation
- `/` after owner sign-in
- `/gym-now` after owner sign-in

## Baseline

Baseline run used the same viewport, browser, service-worker, sample-data, and
isolated-data conditions with 5 measured runs per URL.

| Page | Path | p95 load ms | Max load ms | Result |
| --- | --- | ---: | ---: | --- |
| login | `/login` | 30.9 | 30.9 | pass |
| register-before-owner | `/register` | 39.0 | 39.0 | pass |
| dashboard-shell | `/` | 70.9 | 70.9 | fail |
| gym-now | `/gym-now` | 37.1 | 37.1 | pass |

Root cause: the dashboard loaded the 505 KB `static/js/app.js` bundle directly
from `index.html`, so browser load waited for the full bundle parse and boot
work.

## Final

Final clean-state run used one warmup and 10 measured runs per URL.

| Page | Path | p50 load ms | p95 load ms | Max load ms | Result |
| --- | --- | ---: | ---: | ---: | --- |
| login | `/login` | 28.9 | 32.3 | 32.3 | pass |
| register-before-owner | `/register` | 28.9 | 30.9 | 30.9 | pass |
| dashboard-shell | `/` | 44.0 | 46.7 | 46.7 | pass |
| gym-now | `/gym-now` | 24.3 | 36.1 | 36.1 | pass |

Optimization: `index.html` now loads a tiny `app-loader.js`, which starts the
async app bundle once the DOM is ready: immediately when `document.readyState`
is no longer `loading`, otherwise on one-shot `DOMContentLoaded`. The dashboard
still hydrates and exposes `window.__aicoach` after load.
