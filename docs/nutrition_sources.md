# Nutrition data sources — compliance, auth, caching, attribution

**Owner:** Codex research; this doc gates FIT-72 / FIT-75 / FIT-76 PR merges.
**Last verified:** 2026-05-19 (Claude Code via WebFetch + planning context).
**Re-verify:** quarterly, or before any nutrition-source PR ships.

## Why this doc exists

FIT-72 introduces external nutrition-data lookups (Nutritionix, USDA FoodData Central). FIT-75 ships an offline snapshot. FIT-76 adds Open Food Facts for non-US foods. Each source has its own auth model, rate limits, license, and Terms of Service constraints. Before any of those PRs merge, this file must show the current numbers and constraints for each source — pinned to a verification date so future re-verification is mechanical.

Additionally, this doc codifies the repo-wide rule that **live scraping is not allowed in the meal log path** — see [Scraping policy](#scraping-policy) below.

---

## 1. Nutritionix

### Status: **research gap — requires owner verification at account onboarding**

The Nutritionix developer documentation site (`developer.nutritionix.com`) is behind Cloudflare bot protection at the time of this writeup; the WebFetch tool could not retrieve its content. The following is what FIT-72 / FIT-16 planning context recorded, **explicitly marked unverified.**

Before merging FIT-72 (PR #56), the owner must log into the Nutritionix developer dashboard with the actual account that will be used in production and confirm each of these numbers in writing in this doc.

### Endpoint (planning context, unverified)
- **Base URL:** `https://trackapi.nutritionix.com`
- **Natural-language nutrition endpoint:** `POST /v2/natural/nutrients`
- **Body:** `{"query": "<free text>"}`
- **Response:** array of `foods` with `food_name`, `serving_qty`, `serving_unit`, `nf_calories`, `nf_protein`, `nf_total_carbohydrate`, `nf_total_fat`, `nf_sodium`, `nf_dietary_fiber`, and per-food provenance fields.

### Auth (planning context, **UNVERIFIED**)
- Two env vars expected: `NUTRITIONIX_APP_ID` and `NUTRITIONIX_APP_KEY`.
- Expected to pass as request headers: `x-app-id`, `x-app-key`.
- Never logged or echoed. Absent keys → silently skip the Nutritionix layer (FIT-72 AC8).
- **Owner must verify the exact header names and env-var contract from the Nutritionix developer dashboard before FIT-72 PR #56 merges.**

### Quota (planning context, UNVERIFIED — owner must confirm)
- "Free developer tier: ~150 requests/day" — **this number is from prior planning notes, NOT confirmed against the live dashboard**. The actual number may differ. Verify before relying on it.
- Paid tiers: existence unverified. The $79/mo / 5K req/day figure mentioned in earlier planning is **also unverified.**

### ToS — caching duration (UNVERIFIED — research gap)
- FIT-72 plan assumes 180-day local-cache TTL is acceptable.
- The actual Nutritionix Terms of Service may restrict response caching duration. **Owner must read the ToS during account onboarding and document the actual allowed TTL here.** If it is shorter than 180 days, FIT-72's cache TTL must be tightened before merge.

### ToS — redistribution (UNVERIFIED — research gap)
- FIT-75 (offline snapshot bundle) plans to commit Nutritionix responses to the repo for offline use. Whether the ToS permits this is **unverified**. If forbidden, FIT-75 reduces to a USDA-FDC-only snapshot.

### Verification checklist for the owner
- [ ] Log into Nutritionix developer dashboard with the production account.
- [ ] Record the actual free-tier daily quota in this doc.
- [ ] Read the current Terms of Service. Record cache-TTL and redistribution constraints in this doc.
- [ ] If quota or ToS differ from FIT-72's assumptions, comment on PR #56 to gate merge until reconciled.

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
- **Standard `FDC_API_KEY`:** 1,000 requests per hour per IP address. (No documented daily cap on this page.) Exceeding the hourly limit triggers a **temporary 1-hour block** on the offending IP.
- **`DEMO_KEY`:** "much lower rate limits" — exact numbers not documented on the FDC API Guide page; per data.gov's standard DEMO_KEY policy these are commonly ~30 req/hr and ~50 req/day, but the FDC page does not specify. Treat DEMO_KEY as **dev-only with assume-the-worst quotas.**

### Data types available
- Foundation Foods
- SR Legacy Foods
- Survey Foods (FNDDS)
- Experimental Foods
- Branded Foods

Which subset FIT-72 actually filters to is a repo-specific implementation choice; see `usda_fdc_client.py` on the FIT-72 branch for the current `PREFERRED_DATA_TYPES` value. Branded Foods coverage exists but is thin for chain restaurants — see FIT-72's rationale for Nutritionix sitting ahead in the lookup chain.

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

Source aggregators (Nutritionix, USDA FDC, Open Food Facts) have already paid the licensing cost; rebuilding that data via scraping is wasted effort with active ToS exposure.

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

## 5. Verification log

| Date | Verifier | Source | Action |
|---|---|---|---|
| 2026-05-19 | Claude Code | https://fdc.nal.usda.gov/api-guide/ | Initial USDA FDC section populated from live docs. |
| 2026-05-19 | Claude Code | https://world.openfoodfacts.org/data | OFF license confirmed (ODbL + DbCL + CC-BY-SA). Attribution text unresolved — flagged for FIT-76 verification. |
| 2026-05-19 | Claude Code | developer.nutritionix.com | **Blocked by Cloudflare; could not retrieve.** Nutritionix section relies on prior planning context, marked unverified throughout. Owner verification required before FIT-72 PR #56 merges. |

### Open verification items (must close before the named PRs merge)

- [ ] **FIT-72 (PR #56):** owner verifies all Nutritionix integration details that this doc marks unverified, AND updates the doc with the verified numbers:
  - Exact base URL and `/v2/natural/nutrients` endpoint shape.
  - Required auth header names (`x-app-id` / `x-app-key`) and env-var contract (`NUTRITIONIX_APP_ID` / `NUTRITIONIX_APP_KEY`).
  - Required response and provenance fields actually returned by the live API.
  - Free-tier daily request quota.
  - ToS cache-TTL constraint (relative to the planned 180-day local cache).
  - Whether the planned `vcr` cassettes the implementation depends on can be recorded under the free tier.
- [ ] **FIT-75 (PR #59):** owner confirms whether Nutritionix ToS permits redistributing response data in this repo's offline snapshot bundle. If forbidden, FIT-75 reduces to USDA-FDC-only snapshot.
- [ ] **FIT-76 (PR #60):** integrator verifies all OFF integration details that this doc marks partial / unverified, AND updates the doc with verified facts:
  - Exact attribution text and format required by ODbL + DbCL for derived works.
  - Share-alike obligations that apply to per-row consumption vs full-database republication.
  - Required `User-Agent` header format and any other request headers OFF expects from API consumers.
  - Search endpoint shape (path, query parameters, response schema) — the data page references an external API doc that wasn't retrievable at FIT-81 writeup time.
  - Documented or socially-expected rate-limit policy (beyond the "1 API call = 1 real scan" social contract).

### Re-verification cadence

Quarterly. Update the Verification log row for each source. If quota / ToS / license changes, surface the change as a Linear issue immediately.
