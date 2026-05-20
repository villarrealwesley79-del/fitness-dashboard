# FIT-106 Mobile Visual QA Matrix — 2026-05-20

**Linear:** [FIT-106](https://linear.app/wesley1226/issue/FIT-106)
**Pass cadence:** First re-run after the original [FIT-11](https://linear.app/wesley1226/issue/FIT-11) matrix (2026-05-18).
**Branch under test:** `villarrealwesley79/fit-106-...` rebased from `origin/main` at commit `2ce26fd` (post-FIT-109 merge).
**Auditor:** Claude Opus 4.7 (1M context) via Playwright preview.

## Scope

This matrix covers every mobile-facing surface that landed since the prior pass:

| Ticket | Surface | Verdict |
|---|---|---|
| FIT-93 | Body-tab per-meal expansion | ✅ Pass |
| FIT-96 | Dynamic cardio rotation | ✅ Pass (data) |
| FIT-97 | Meal detail tap-to-inspect | ✅ Pass |
| FIT-100 | Meal detail edit/correct | ⚠️ Bug — [FIT-114](https://linear.app/wesley1226/issue/FIT-114) |
| FIT-103/105 | Starter-load + confidence chip | ✅ Pass on Next Workout list |
| FIT-107 | Dashboard "View food log" sheet | ✅ Pass |
| FIT-108 | Active workout next-set focus | ✅ Pass (Codex P2 fix verified) |
| FIT-109 | Cardio "why" line | ✅ Pass on both surfaces |
| FIT-110 | Quick trends expander | ✅ Pass |

## Defects filed

| Ticket | Priority | Title |
|---|---|---|
| [FIT-113](https://linear.app/wesley1226/issue/FIT-113) | Low | Apple Health freshness chip wraps to two lines on mobile (390 / 375) |
| [FIT-114](https://linear.app/wesley1226/issue/FIT-114) | **High** | Meal detail "Correct" button is unreadable — light text on light background |
| [FIT-115](https://linear.app/wesley1226/issue/FIT-115) | **Urgent** | History tab charts show empty state despite 28 workouts in `/api/history-all` |
| [FIT-116](https://linear.app/wesley1226/issue/FIT-116) | **High** | Settings tab horizontal overflow at 375px — push notifications button row |

## Matrix — 390×844 (iPhone 14 Pro)

### Dashboard
- [x] Greeting + header
- [x] Readiness card with gauge + HRV/RHR/Sleep mini-stats
- [x] AI Recommendation card — title, intensity tags, last-session + avoid chips (FIT-88), reasoning, freshness chips
  - Apple chip text overflows → **FIT-113**
- [x] Macro card with `View food log ›` affordance (FIT-107)
- [x] Food-context chips + warning row (FIT-47)
- [x] Meal composer (FIT-60)
- [x] Glance grid (Steps, Active Cal, Sleep, Weight)
- [x] AI Insight card
- [x] Quick Trends expander closed by default (FIT-110)
- [x] Quick Trends opens → 7D readiness chart + 4W volume chart both render

### Food-log sheet (FIT-107)
- [x] Opens from macro-card button
- [x] Today section with totals chip and 1 meal entry
- [x] Yesterday section "No meals logged."
- [x] Recent 14 days "No meals logged in the last 14 days." (correct for current data)
- [x] Tap meal row → `#modal-meal-detail` stacks above sheet (z-index 120 vs 100)
- [x] Empty state copy match: *"No meals logged yet. Use Log a Meal to add your first entry."*

### Meal detail / correction (FIT-97 + FIT-100)
- [x] Modal opens with ITEM / PORTION / LOGGED / SOURCE / CONFIDENCE / FROM PHOTO / CALORIES / PROTEIN / CARBS / FAT / SODIUM fields populated
- [x] Stub-vision FIT-90 caveat box visible when source startsWith "stub_vision"
- [x] Three CTAs: Close / Correct / Delete entry
  - **Correct button is invisible** (light text on light bg) → **FIT-114**

### Body tab
- [x] Body composition card (Weight 185.7, Body Fat 22.0%)
- [x] FIT-13 interpretation card visible with multi-sentence text
- [x] Target progress card
- [x] Weight Trend 90D chart renders SVG
- [x] Body Fat Trend 90D chart honest empty state ("Not enough data yet.")
- [x] Composition card (lean mass / fat mass / 90D weight delta)
- [x] Nutrition trend (14D) row expands via `<details>` → renders meals via `/api/food-logs/by-date`
- [x] Log measurement form

### Next Workout
- [x] AI Recommended Plan card with title, tags, "why this plan?" reasoning
- [x] Exercise list (11 entries) with load hints
- [x] FIT-105 confidence chips visible on inferred-load exercises ("MED" chip)
- [x] Cardio finisher card with "Zone 2 · Fat Burning / Recovery" why line (FIT-109)
- [x] Start Workout + Adjust Plan buttons

### Active workout (FIT-108)
- [x] Sticky progress header: `Set 1 of 38 · 11 exercises left`
- [x] Next-incomplete row has `.set-row-next` blue accent
- [x] Done tap target = 56×50 (≥44pt) ✅
- [x] Out-of-order completion: check set 2 first → header stays "Set 1 of 38" (next-incomplete row = ex0/set0) — Codex P2 fix verified
- [x] Swap modal alternatives (5 entries for Seated Row) — equipment-filtered, COMPOUND/CABLE tags
- [x] Active workout cardio block shows "Zone 2 · Fat Burning / Recovery" (FIT-109 parity)

### History tab
- [x] Title row + range chips (7D / 14D / 30D / 90D / 1Y)
- [ ] **Workout Frequency chart empty at all ranges** → **FIT-115**
- [ ] **Volume Over Time chart empty at all ranges** → **FIT-115**
- [x] Top Exercises section heading present (empty due to same root cause as charts)

### Stats tab
- [x] Total workouts (6), Total volume (106K), Avg vol/workout (18K), Avg RPE (6.8), Total sets (97), Workout time (4h 50m) — all populated
- [x] Muscle recovery 11/11 fresh, per-muscle cards (Chest 8/10, Back 8/10, Shoulders 10/10, Biceps 8/10)
- [x] Volume by muscle donut chart renders SVG

### Vitals tab
- [x] Core metrics (RHR, HRV, HR Zone, Body Temp)
- [x] Activity (Steps, Active Min, Active Cal, Total Cal)
- [x] Sleep (duration + score with 7d avg)

### Log tab
- [x] Segmented control: Strength / Cardio / Recovery
- [x] Strength form: Date, Exercise dropdown, Sets, Reps, Weight, RPE 1-10, Notes, Log Set CTA

### Settings tab
- [x] Training Goals card (Strength, Hypertrophy, Muscular Endurance, Weight Loss, etc.)
- [x] Workout Preferences (duration, sessions/week, equipment)
- [x] Integrations (Oura connection chip + sync, Apple Health + setup, Weather)
- [x] AI Coach (primary + fallback endpoints, metrics row, warning row when fallback %)
- [x] Push Notifications with all 5 hidden warning rows
- [x] Data & Backup (Export / Import / Last backup)
- [x] App settings (dark mode toggle, units, sign out)

## Matrix — 375×812 (iPhone X / SE-width)

Programmatic overflow check across all 8 tabs:

| Tab | scrollWidth | Overflow? |
|---|---|---|
| Dashboard | 375 | ✅ none |
| Vitals | 375 | ✅ none |
| Next Workout | 375 | ✅ none |
| Log | 375 | ✅ none |
| History | 375 | ✅ none |
| Body | 375 | ✅ none |
| Stats | 375 | ✅ none |
| Settings | **403** | ❌ +28px → **FIT-116** |

## Notes / observations not yet ticket-worthy

- Swap modal alternatives at the moment do not display FIT-105 load-hint confidence chips. That may be intentional (the inference happens after the swap commits and the load is computed against starter inference) — leaving uncalled-out unless the user confirms it should appear there.
- The Push notifications card warning rows are all hidden in the current state (no setup-needed flags) so the visual consistency standardization that FIT-111 will address is harder to fully verify in the absence of an error state. To be re-verified after FIT-111 lands.
- The 1Y range on History tab still shows the empty placeholders even though 28 workouts exist — that's the FIT-115 root cause and not a separate range-selection bug.

## Coverage limits

- Photo meal capture flow (FIT-91) not exercised — requires triggering a real or stub image upload. Stub source `stub_vision_estimate` was observed on existing meals, confirming the source path works end-to-end.
- Sync queue / offline workout flow (FIT-37) not exercised — requires the app to be in offline mode to populate the queue.
- Notification permission flow (FIT-40) not exercised — requires `Notification.requestPermission()` user gesture which Playwright preview doesn't grant automatically.

Each of these could be a follow-up matrix run if the user requests deeper coverage. For this pass the prior auditor (FIT-11) covered them; the surfaces visited here are the ones that have **changed** since.

## Verification environment

- Preview server: `fit107-dashboard` on port 5091 (Flask, `LOGIN_DISABLED=True`)
- Branch: `villarrealwesley79/fit-106-claude-ui-re-run-mobile-visual-qa-matrix-after-food-cardio`
- Backend commit: matches `origin/main` HEAD `2ce26fd`
- Browser: Chromium via Playwright (preview MCP)
- Viewports: 390x844 and 375x812
