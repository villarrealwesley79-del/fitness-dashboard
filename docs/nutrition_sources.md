# Nutrition data sources — compliance, auth, caching, attribution

**Status:** Nutritionix is retired as of 2026-07-15 because no free personal access remains. It is not an active lookup source, credential target, cache-refresh target, or smoke-test prerequisite.
**Owner:** Historical source-compliance and audit record; active-source changes belong to a new issue.
**Last verified:** 2026-05-19 for the historical FIT-72/FIT-75/FIT-76 research record.
**Re-verify:** Nutritionix is not re-verified unless a future, separately approved reintroduction issue exists.

## Why this doc exists

FIT-72 introduced external nutrition-data lookups and FIT-75/FIT-76 added offline and Open Food Facts coverage. Those are historical implementation and audit records. Active lookup sources are the curated H-E-B product reference, USDA FoodData Central, Open Food Facts, local cache replay where valid, and deterministic/manual fallbacks. Each active source has its own auth model, rate limits, license, and Terms of Service constraints.

Additionally, this doc codifies the repo-wide rule that **live scraping is not allowed in the meal log path** — see [Scraping policy](#scraping-policy) below.

---

## 1. Nutritionix (retired)

### Status: **retired — no free personal access remains**

Nutritionix is not an active provider. Do not configure Nutritionix credentials, onboard an account, refresh Nutritionix cache rows, or use Nutritionix in runtime/provider priority or smoke coverage. Existing accepted meals, provenance fields, and cache rows tagged `nutritionix` or `nutritionix_barcode` remain historical records only; they are not live lookup results.

The remainder of this section preserves the FIT-72/FIT-81 planning record for auditability. It is historical context, not current integration or onboarding guidance.

### Endpoint (historical planning context, unverified)
- **Historical base URL:** `https://trackapi.nutritionix.com`
- **Historical natural-language nutrition endpoint:** `POST /v2/natural/nutrients`
- **Historical body:** `{"query": "<free text>"}`
- **Historical response:** array of `foods` with `food_name`, `serving_qty`, `serving_unit`, `nf_calories`, `nf_protein`, `nf_total_carbohydrate`, `nf_total_fat`, `nf_sodium`, `nf_dietary_fiber`, and per-food provenance fields.

### Auth (historical planning context, **UNVERIFIED**)
- Historical env vars recorded: `NUTRITIONIX_APP_ID` and `NUTRITIONIX_APP_KEY`.
- Historical request headers recorded: `x-app-id`, `x-app-key`.
- Never logged or echoed. FIT-72 recorded absent keys as a graceful skip.
- The recorded header and env-var names are retained for historical provenance only; no current owner verification or credential onboarding is required.

### Quota (historical planning context, UNVERIFIED)
- "Free developer tier: ~150 requests/day" — **this number is from prior planning notes, NOT confirmed against the live dashboard**. The actual number may differ. It must not be relied on for current runtime behavior.
- Paid tiers: existence unverified. The $79/mo / 5K req/day figure mentioned in earlier planning is **also unverified.**

### ToS — caching duration (historical planning context, UNVERIFIED)
- FIT-72 plan assumed 180-day local-cache TTL was acceptable.
- The actual Nutritionix Terms of Service were not verified in that planning record. No current cache refresh or TTL decision is based on this assumption.

### ToS — redistribution (historical planning context, UNVERIFIED)
- FIT-75 recorded a planned offline snapshot containing Nutritionix responses. Redistribution permission was not verified. This plan is historical and is not an active snapshot or redistribution requirement.

### Historical verification record

FIT-72/FIT-81 retained the endpoint, header, quota, cache, and redistribution questions above as unverified research gaps. FIT-387 supersedes those onboarding and verification actions by retiring Nutritionix; no current credentials or provider smoke run should be created from this record.

---

## 2. USDA FoodData Central (FDC)

### Status: **verified 2026-05-19 against `https://fdc.nal.usda.gov/api-guide/`**

Source: the FDC API Guide page, sections "Sample Calls," "Gaining Access," "Rate Limits," "API Endpoints," "What's Available," "Licensing." Retrieved via WebFetch 2026-05-19.

### Endpoint
- **Base URL:** `https://api.nal.usda.gov/fdc/v1/`
- **Search endpoint:** `/foods/search` (GET or POST).
  - GET example: `GET https://api.nal.usda.gov/fdc/v1/foods/search?api_key=YOUR_KEY&query=Cheddar%20cheese`
  - POST example: same URL, JSON body `{"query": "Cheddar cheese"}`

### Auth
- API key is **required.** Documentation: "a data.gov API key must be incorporated into each API request."
- **Passed as query parameter:** `api_key=YOUR_KEY` — not a header.
- Two key types:
  - **`USDA_FDC_API_KEY`** (env var name used by `usda_fdc_client.py` on the FIT-72 branch) — the standard data.gov API key. Each user obtains one from https://api.data.gov/signup/. Use this in production.
  - **`DEMO_KEY`** — global demo key with much lower rate limits. Dev-only. Do not use in production.

### Rate limits
- **Standard data.gov API key** (`USDA_FDC_API_KEY` in this repo): 1,000 requests per hour per IP address. (No documented daily cap on this page.) Exceeding the hourly limit triggers a **temporary 1-hour block** on the offending IP.
- **`DEMO_KEY`:** "much lower rate limits" — exact numbers not documented on the FDC API Guide page; per data.gov's standard DEMO_KEY policy these are commonly ~30 req/hr and ~50 req/day, but the FDC page does not specify. Treat DEMO_KEY as **dev-only with assume-the-worst quotas.**

### Data types available
- Foundation Foods
- SR Legacy Foods
- Survey Foods (FNDDS)
- Experimental Foods
- Branded Foods

Which subset FIT-72 actually filters to is a repo-specific implementation choice; see `usda_fdc_client.py` on the FIT-72 branch for the historical `PREFERRED_DATA_TYPES` value. Branded Foods coverage exists but is thin for chain restaurants — FIT-72's historical rationale placed the now-retired Nutritionix ahead in the old lookup chain.

### License
- **Public domain** under CC0 1.0 Universal. "USDA FoodData Central data are in the public domain and they are not copyrighted."
- **Redistribution:** unrestricted. The FIT-75 offline snapshot can include USDA FDC data without license concern.
- **Attribution:** not legally required (CC0 waives all rights), but courteous to credit "Data: USDA FoodData Central."

### Caching
- No caching restrictions documented on the FDC API Guide page.
- For practical use: long caching (months to years for stable foods like "banana") is fine; refresh on schema change announcements.

### Production posture for this repo
- Production: require `USDA_FDC_API_KEY` in env. Skip the USDA layer if unset (FIT-72 graceful-degradation AC).
- Local dev: `DEMO_KEY` is acceptable for spike work. Do not commit `DEMO_KEY` cassettes to vcr — re-record with `USDA_FDC_API_KEY` for the committed cassettes so CI behavior is predictable.

---

## 3. Open Food Facts (OFF)

### Status: **partial — license confirmed via `world.openfoodfacts.org/data`; full attribution text and rate limits require verification at FIT-76 integration time**

Source: the OFF data page (https://world.openfoodfacts.org/data). The Terms of Use PDF and the OFF wiki's Reusing Data page were both unreachable at writeup time (404 / bot-protection respectively). Re-verify at FIT-76 implementation.

### Endpoint (partial)
- **Base URL:** `https://world.openfoodfacts.org/api/v2/`
- **Barcode lookup:** `/api/v2/product/<barcode>.json` (confirmed example on data page).
- **Search endpoint:** the data page references an external API documentation site for endpoint shapes but doesn't repeat them. Verify at integration time.

### Auth
- No API key required (confirmed on the data page; no auth mentioned).
- "Sending an HTTP header with your API call" is recommended — likely `User-Agent: <app-name>/<version> (<contact email>)` per OFF community convention, but this should be confirmed against the external API docs.

### License
- **Open Database License (ODbL)** for the database itself.
- **Database Contents License (DbCL)** for the individual content rows.
- **Creative Commons Attribution-ShareAlike** for product images.

### Attribution — REQUIRES VERIFICATION (research gap)
- ODbL requires attribution for derived works. Format and placement: the data page references "Conditions for reuse" / "Terms and conditions" as separate docs that were not retrievable.
- Best-effort default (until verified): each Settings panel that surfaces an OFF-sourced estimate should show `Source: Open Food Facts (ODbL/DbCL)` plus a link to the upstream product page (`/product/<barcode>`).
- FIT-76's UI follow-up must verify the exact required attribution text against current OFF Terms before merge.

### Share-alike
- ODbL is share-alike for the database; DbCL is permissive for individual rows.
- Practical impact for this single-user app: minimal. We're not republishing the OFF database — just consuming individual rows for personal use. The attribution requirement remains.

### Rate limits (partial)
- The data page suggests the social contract is "1 API call = 1 real scan by a user." Aggressive crawling / scraping is prohibited.
- No specific per-second / per-hour limits documented. Be a good citizen: polite request cadence, `User-Agent` identifier, cache aggressively.

### Production posture for this repo
- OFF goes in the lookup chain between USDA FDC and LM Studio (FIT-76 placement).
- Cache responses aggressively (TTL matching FIT-72's general policy; tightening only if FIT-76's attribution verification surfaces a constraint).
- Surface the ODbL/DbCL attribution in the source-provenance UI (FIT-76 UI follow-up).

---

## 4. Scraping policy

### Rule

**Live scraping is not allowed in the meal log path.** Specifically, the following libraries must not appear in any code path reachable from `/api/meal-intake`:

- Scrapling
- Playwright
- Selenium
- BeautifulSoup against restaurant or grocery sites
- Any other HTML scraping / browser automation library

Active source aggregators (USDA FDC and Open Food Facts) already publish licensed data; rebuilding that data via scraping is wasted effort with active ToS exposure.

### Audit

Before each nutrition-source PR merges, run:

```sh
grep -ri "from scrapling\|import scrapling\|from playwright\|import playwright\|from selenium\|import selenium\|from bs4\|import bs4" --include="*.py" .
```

The result must be empty (or limited to test files that mock scraper-like behavior — even those are not encouraged).

### Exception path

If a future offline data-refresh experiment requires scraping a specific source (e.g. a chain's nutrition PDF that has no API), it must:

1. File its own Linear issue with explicit ToS and `robots.txt` review for that source.
2. Implement the scraper as an **offline admin script only** — not callable from any user-facing endpoint.
3. Cache results to JSON committed to the repo; the live app never executes the scraper.
4. Tests use snapshot HTML fixtures, never the live site.

This is the same posture FIT-72 documented in its rejection of Scrapling. Do not relax it.

---

## 5. FIT-98 regional-chain coverage smoke test (historical)

This section is a retained FIT-98 audit artifact from 2026-05-20. It is not a current Nutritionix smoke procedure. Nutritionix is retired, and no credentialed Nutritionix smoke run is expected.

**Run date:** 2026-05-20 23:35 CDT
**Command:** `.venv/bin/python scripts/smoke_branded_lookup_coverage.py`
**Mode:** cache reads skipped; cache writes disabled by the helper. Provider lookup is exercised for the coverage matrix; the production direct-lookup gate status is recorded per row.

Provider status for this run:

- **Direct lookup gate:** bypassed for coverage matrix; production gate recorded per row.
- **Nutritionix (historical):** missing `NUTRITIONIX_APP_ID` / `NUTRITIONIX_APP_KEY`, so this run could not prove Nutritionix live coverage. This remains historical evidence, not a current credential prerequisite.
- **USDA FDC:** missing `USDA_FDC_API_KEY`, so this run could not prove USDA live coverage.
- **Open Food Facts:** no credentials required. The restaurant-chain queries below are not packaged-food queries, so OFF is not expected to cover them.

Local food history was not available in the clean FIT-98 worktree, so the additional rows below are proxy Texas/regional-chain checks, not user-confirmed frequent restaurants. HEB/private-label grocery coverage is intentionally out of scope for FIT-98 and tracked separately in FIT-118.

Follow-up filed: FIT-123 tracks the direct-lookup gate blocking regional restaurant queries before branded provider lookup.

| Category | Query | Outcome | Matched item | Calories | Source URL | Confidence | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| required | bill miller bacon and egg taco | provider unavailable |  |  |  |  | FIT-98 historical Bill Miller BBQ query; historical Nutritionix skipped: missing NUTRITIONIX_APP_ID/NUTRITIONIX_APP_KEY; historical usda_fdc skipped: missing USDA_FDC_API_KEY; open_food_facts reached with no verified match; fell through before AI fallback; production direct-lookup gate would block this query |
| required | bill miller breakfast sandwich on biscuit | provider unavailable |  |  |  |  | FIT-98 historical Bill Miller BBQ query; historical Nutritionix skipped: missing NUTRITIONIX_APP_ID/NUTRITIONIX_APP_KEY; historical usda_fdc skipped: missing USDA_FDC_API_KEY; open_food_facts reached with no verified match; fell through before AI fallback; production direct-lookup gate would block this query |
| required | bill miller brisket sandwich | provider unavailable |  |  |  |  | FIT-98 historical Bill Miller BBQ query; historical Nutritionix skipped: missing NUTRITIONIX_APP_ID/NUTRITIONIX_APP_KEY; historical usda_fdc skipped: missing USDA_FDC_API_KEY; open_food_facts reached with no verified match; fell through before AI fallback; production direct-lookup gate would block this query |
| proxy | whataburger patty melt | provider unavailable |  |  |  |  | Texas/regional chain proxy, not user-confirmed; historical Nutritionix skipped: missing NUTRITIONIX_APP_ID/NUTRITIONIX_APP_KEY; historical usda_fdc skipped: missing USDA_FDC_API_KEY; open_food_facts reached with no verified match; fell through before AI fallback; production direct-lookup gate would block this query |
| proxy | taco cabana bean and cheese taco | provider unavailable |  |  |  |  | Texas/regional chain proxy, not user-confirmed; historical Nutritionix skipped: missing NUTRITIONIX_APP_ID/NUTRITIONIX_APP_KEY; historical usda_fdc skipped: missing USDA_FDC_API_KEY; open_food_facts reached with no verified match; fell through before AI fallback; production direct-lookup gate would block this query |
| proxy | torchys democrat taco | provider unavailable |  |  |  |  | Texas/regional chain proxy, not user-confirmed; historical Nutritionix skipped: missing NUTRITIONIX_APP_ID/NUTRITIONIX_APP_KEY; historical usda_fdc skipped: missing USDA_FDC_API_KEY; open_food_facts reached with no verified match; fell through before AI fallback; production direct-lookup gate would block this query |
| proxy | rudys brisket sandwich | provider unavailable |  |  |  |  | Texas/regional chain proxy, not user-confirmed; historical Nutritionix skipped: missing NUTRITIONIX_APP_ID/NUTRITIONIX_APP_KEY; historical usda_fdc skipped: missing USDA_FDC_API_KEY; open_food_facts reached with no verified match; fell through before AI fallback; production direct-lookup gate would block this query |
| proxy | p terrys cheeseburger | provider unavailable |  |  |  |  | Texas/regional chain proxy, not user-confirmed; historical Nutritionix skipped: missing NUTRITIONIX_APP_ID/NUTRITIONIX_APP_KEY; historical usda_fdc skipped: missing USDA_FDC_API_KEY; open_food_facts reached with no verified match; fell through before AI fallback; production direct-lookup gate would block this query |
| proxy | schlotzskys original sandwich | provider unavailable |  |  |  |  | Regional chain proxy, not user-confirmed; historical Nutritionix skipped: missing NUTRITIONIX_APP_ID/NUTRITIONIX_APP_KEY; historical usda_fdc skipped: missing USDA_FDC_API_KEY; open_food_facts reached with no verified match; fell through before AI fallback; production direct-lookup gate would block this query |
| proxy | golden chick chicken tenders | provider unavailable |  |  |  |  | Texas/regional chain proxy, not user-confirmed; historical Nutritionix skipped: missing NUTRITIONIX_APP_ID/NUTRITIONIX_APP_KEY; historical usda_fdc skipped: missing USDA_FDC_API_KEY; open_food_facts reached with no verified match; fell through before AI fallback; production direct-lookup gate would block this query |
| proxy | la madeleine chicken caesar salad | provider unavailable |  |  |  |  | Texas/regional chain proxy, not user-confirmed; historical Nutritionix skipped: missing NUTRITIONIX_APP_ID/NUTRITIONIX_APP_KEY; historical usda_fdc skipped: missing USDA_FDC_API_KEY; open_food_facts reached with no verified match; fell through before AI fallback; production direct-lookup gate would block this query |

### FIT-98 UI source-label verification

The meal review surface already distinguishes source types in `static/js/app.js`:

- Historical verified branded/generic lookup labels included `Nutritionix`, `USDA`, and `Open Food Facts`; Nutritionix labels are retained only to explain old meal provenance.
- Fallback/low-certainty label: `Fallback preset`.
- Provenance links render from `verified_source_url`, `source_url`, or `product_url` when present.

FIT-98 added static test coverage for these labels so a future UI edit does not collapse verified-source and fallback estimates into the same label.

---

## 6. Verification log (historical)

| Date | Verifier | Source | Action |
|---|---|---|---|
| 2026-05-19 | Claude Code | https://fdc.nal.usda.gov/api-guide/ | Initial USDA FDC section populated from live docs. |
| 2026-05-19 | Claude Code | https://world.openfoodfacts.org/data | OFF license confirmed (ODbL + DbCL + CC-BY-SA). Attribution text unresolved — flagged for FIT-76 verification. |
| 2026-05-19 | Claude Code | developer.nutritionix.com | **Blocked by Cloudflare; could not retrieve.** Historical Nutritionix section relied on prior planning context and remained unverified. Owner verification was a FIT-72-era action, superseded by FIT-387 retirement. |
| 2026-05-20 | Codex | FIT-98 smoke helper | Historical regional-chain smoke matrix. Provider lookup was exercised for coverage, with the production direct-lookup gate recorded separately per row. Historical live coverage was environment-limited because retired Nutritionix and USDA credentials were absent; Open Food Facts was reached and did not verify these restaurant-chain rows. |

### Historical issue records (superseded by FIT-387)

- **FIT-72 (PR #56):** retained the original Nutritionix endpoint, credential, quota, response, cache, and cassette verification questions. No current verification or onboarding action remains.
- **FIT-75 (PR #59):** retained the original Nutritionix offline-snapshot redistribution question. No current Nutritionix snapshot or redistribution action remains.
- [ ] **FIT-76 (PR #60):** integrator verifies all OFF integration details that this doc marks partial / unverified, AND updates the doc with verified facts:
  - Exact attribution text and format required by ODbL + DbCL for derived works.
  - Share-alike obligations that apply to per-row consumption vs full-database republication.
  - Required `User-Agent` header format and any other request headers OFF expects from API consumers.
  - Search endpoint shape (path, query parameters, response schema) — the data page references an external API doc that wasn't retrievable at FIT-81 writeup time.
  - Documented or socially-expected rate-limit policy (beyond the "1 API call = 1 real scan" social contract).

### Re-verification cadence

Quarterly. Update the Verification log row for each source. If quota / ToS / license changes, surface the change as a Linear issue immediately.
