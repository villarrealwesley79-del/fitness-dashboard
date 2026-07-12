# Nutrition Data Sources — PRD

> **Sources:** `branded_food_lookup.py`, `nutritionix_client.py`, `open_food_facts_client.py`, `usda_fdc_client.py`, `heb_product_lookup.py`, `docs/nutrition_sources.md`, `docs/FIT202_PUBLIC_FOOD_SMOKE_AND_ACCURACY_REPORT.md`, `scripts/smoke_branded_lookup_coverage.py`, `tests/test_branded_food_lookup.py`, `app.py`, `meal_text_parser.py`, `meal_estimate_schema.py`
> **Routes:** Consumed by `POST /api/meal-intake`, `POST /api/meal-intake/barcode`, and `POST /api/meal-intake/<meal_id>/refresh`; provider endpoints are Nutritionix `POST /v2/natural/nutrients`, Nutritionix `GET /v2/search/item`, USDA FDC `/foods/search`, Open Food Facts product lookup, Open Food Facts search, and a curated H-E-B product URL.
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

## 1. Overview

Nutrition data sources turn owner-entered branded foods, restaurant items, packaged barcodes, and selected review-card edits into structured nutrition estimates. They sit inside the meal logging flow but are intentionally separate from canonical food-log persistence: providers propose estimates, the review policy decides whether the estimate needs review, and accepted rows become the durable food log.

The source hierarchy favors the most specific, low-friction source available: fresh local cache, curated H-E-B reference for one known private-label item, Nutritionix, USDA FoodData Central, then Open Food Facts. Barcode lookup uses a related but narrower order: barcode cache, Nutritionix UPC, USDA branded barcode search, then Open Food Facts barcode. H-E-B product-page lookup is intentionally not used for barcode lookup.

Real integration status is mixed. USDA and Open Food Facts client code points at public APIs and gracefully skips/fails closed. Nutritionix client code is implemented, but the repository documentation still marks account quota, Terms of Service, and redistribution/caching details as unverified because the developer docs were not reachable during the earlier research pass. H-E-B is not a general provider client; it is a curated hardcoded reference for one H-E-B Sushiya California Roll product page.

## 2. User-Facing Surfaces

| Surface | User-visible behavior | Source involvement |
| --- | --- | --- |
| Meal text composer | Owner types a branded or restaurant food such as a chain taco, packaged snack, or H-E-B item | `meal_text_parser.py` checks personal vocabulary, branded direct lookup eligibility, source lookup, LM Studio, then deterministic fallback. |
| Barcode scan panel | Owner scans or enters UPC/EAN/GTIN digits | `/api/meal-intake/barcode` calls `branded_food_lookup.lookup_barcode`; unresolved barcodes become manual review if `allow_pending` is true. |
| V2 review card | Source chips show provider labels and may open an in-app source viewer for same-origin links | Provider estimates carry source labels, confidence, candidates, provenance URLs, and manual-review badges. |
| Legacy single-item review card | Source chip, confidence, policy reasons, source provenance text/link | Uses source tags and safe provenance fields from the estimate. |
| Macro card and food log | Accepted values count toward daily calories/macros/sodium | Provider data is no longer treated specially after acceptance except for stored provenance and possible refresh events. |
| Source/provenance UI | Open Food Facts attribution and provider URLs can be shown | UI reads `verified_source_url`, `source_url`, `product_url`, and `off_attribution` when present. |
| Smoke/accuracy reports | Product/engineering validation, not user UI | Documents known provider readiness and accuracy constraints. |

## 3. Field Inventory

### Lookup Input Fields

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `query` | string | Yes for text lookup | None | Normalized by lowercasing, punctuation cleanup, plural/brand/typo aliases | Owner's branded, restaurant, or packaged food phrase. |
| `barcode` | string | Yes for barcode lookup | None | Separators removed; must be 8, 12, 13, or 14 digits | UPC/EAN/GTIN identity. |
| `user_id` | integer | No | 1 | Passed to cache reads/writes | Per-owner lookup cache namespace. |
| `source_priority` | tuple | No | Text priority or barcode priority | Only known source names are useful | Test/smoke override for source order; production uses defaults. |
| `country_tag` / country hint | string | No | Inferred from query when possible | Open Food Facts search filter | Helps non-US packaged-food matching. |

### Canonical Estimate Fields Returned by Sources

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `item_name` | string | Yes | Provider product/food name | Sanitized by `meal_estimate_schema` | Review label and food-log item name. |
| `portion_description` | string/null | Yes | Provider serving or `100g` | Sanitized string/null | Portion basis visible to owner. |
| `meal_type` | enum | Yes | Usually `snack`; inferred breakfast or lunch for some text matches. `dinner` is never source-inferred, only owner-edited. | `breakfast`, `lunch`, `dinner`, `snack` | Review bucket; editable later. |
| `calories` | number | Yes | None | Required for usable estimates; plausible max 5000 | Calories for review/save. |
| `protein_g`, `carbs_g`, `fat_g` | number | Yes | None | Required for most provider estimates; plausible max 500 g | Macro estimates. |
| `sodium_mg` | number | No but usually set | 0 or provider value | Plausible max 12000 mg | Sodium warning/next-day input. |
| `fiber_g` | number | No | 0 or provider value | Plausible max 500 g | Fiber estimate. |
| `confidence` | number | Yes | Source-specific | 0..1 | Provider authority and review policy. |
| `ambiguous` | boolean | Yes | Source-specific | Boolean | Indicates whether review should be required. |
| `uncertainty_notes` | array | Yes | `[]` | Strings only | Why source match needs review or what basis was used. |
| `source` | enum/string | Yes | Source-specific | Sanitized string | Immediate source tag shown by UI. |
| `underlying_source` | string | No | Set on local cache | Safe provenance | Original provider behind cache replay. |
| `external_food_id` | string | No | Provider ID/barcode/FDC ID/OFF code/H-E-B product ID | Safe provenance | Linkage to upstream data. |
| `verified_source_url` | URL string | No | Provider URL/homepage/product URL | Safe provenance | Clickable/source-viewable provenance. |
| `data_fetched_at` | ISO string | No | Current time | Safe provenance | Cache freshness and provenance. |
| `portion_basis` | string | No | Provider-specific | Safe provenance | Explains serving basis and any 100 g fallback. |
| `brand_id` | string | No | Provider/brand normalized ID | Safe provenance | Cache trust and private-label validation. |
| `source_brand_name` | string | No | Provider brand | Preserved in some source paths | Brand verification and UI/debug context. |
| `off_attribution` | string/object | No | Open Food Facts attribution string | Safe provenance | License attribution for OFF-derived values. |

### Provider Response Fields Consumed

| Provider | Fields used | Meaning |
| --- | --- | --- |
| Nutritionix natural nutrients | `foods[]`, `food_name`, `brand_name`, `serving_qty`, `serving_unit`, `serving_weight_grams`, `nf_calories`, `nf_protein`, `nf_total_carbohydrate`, `nf_total_fat`, `nf_sodium`, `nf_dietary_fiber`, `nix_item_id` | Food identity, serving, macros, sodium, fiber, and provenance. Multi-food responses are summed. |
| Nutritionix UPC item | Same nutrition fields plus UPC item identity | Barcode-specific packaged item estimate. |
| USDA FDC search | `foods[]`, `fdcId`, `description`, `brandOwner`, `gtinUpc`, `foodNutrients`, serving-related data where present | Text and barcode fallback, mostly 100 g basis in current code. |
| Open Food Facts search/product | `code`, `product_name`, `brands`, `url`, `nutriments`, `data_quality_tags`, `countries_tags`, `serving_size`, `serving_quantity`, `quantity` | Packaged product identity, quality filters, nutrition per 100 g or serving, attribution source. |
| H-E-B curated reference | Hardcoded product name, URL, macros, portion | One product-specific trusted estimate. |

## 4. Interactions & Flows

### Text Source Lookup Flow

Trigger → `meal_text_parser.parse_meal_text` receives text from `/api/meal-intake` or V2 refresh add/edit.

Behavior → The parser normalizes text and first checks personal vocabulary. If there is no trusted vocabulary hit, it asks `branded_food_lookup.should_attempt_direct_lookup` whether the phrase is specific enough for provider lookup. Eligible branded/private-label terms try source lookup before local-model parsing.

Validation → Direct lookup rejects empty text, half/portion-only modifiers, most multi-item phrases containing hard/soft combo tokens, and regional restaurant names without menu-item tokens. H-E-B private-label queries receive special handling so stale non-H-E-B cache hits do not satisfy H-E-B requests.

API → Internal call to `branded_food_lookup.lookup(query, user_id)`.

Success → A sanitized provider estimate returns with source/confidence/provenance. The meal route still routes it through pending review in current fresh-intake behavior.

Failure → Parser continues to LM Studio text estimation; if the model is unavailable, invalid, timed out, or locked, deterministic fallback creates a low-confidence review estimate.

### Barcode Product Resolution

Trigger → `/api/meal-intake/barcode` receives validated barcode digits.

Behavior → `lookup_barcode` tries barcode cache, Nutritionix UPC, USDA branded barcode search, then Open Food Facts barcode. It never tries H-E-B product-page lookup for barcode. Pending-source fallback is created by `app.py`, not by the provider module.

Validation → Provider estimates must sanitize through the meal estimate schema. USDA barcode results must match barcode variants. Open Food Facts barcode can accept an exact product with nutrition even when complete-quality tags are absent, but still rejects severe quality/energy/macro inconsistencies.

API → Internal `lookup_barcode`; external provider calls as configured.

Success → The estimate is cached per user/barcode unless it is a manual pending-source fallback. The response includes `lookup_source`, `cache_hit`, and `pending_source`.

Failure → The route returns `404 barcode_not_found` when `allow_pending` is false, or creates a manual-review estimate when `allow_pending` is true.

### Cache Replay

Trigger → Text or barcode lookup starts.

Behavior → Cache is checked first for production priorities. Cache entries older than 180 days are ignored. Text cache replay returns `source: local_cache` and `underlying_source` from the stored source. Barcode cache replay also returns `local_cache` with `underlying_source` and backfills a missing OFF `verified_source_url` from the stored `external_food_id` or OFF homepage; text cache replay does not perform that provenance backfill.

Validation → H-E-B private-label cache hits require verified H-E-B brand identity; non-H-E-B or under-verified cache rows are bypassed.

Success → Cache avoids provider network calls and returns a sanitized estimate.

Failure → Lookup continues to the next source in priority order.

### Regional Restaurant Lookup

Trigger → Query contains a recognized regional chain name such as Bill Miller, Whataburger, Taco Cabana, Torchy's, Rudy's, P. Terry's, Schlotzsky's, Golden Chick, or La Madeleine.

Behavior → The direct lookup gate allows single-menu-item restaurant queries with item tokens and blocks multi-item/meal/combo style phrases. Nutritionix is the primary expected source when credentials are configured. USDA and OFF are unlikely to cover regional chain restaurant items.

Validation → Wrong-chain or item-category mismatch lowers confidence to review levels. Missing provider brand for a requested brand also lowers confidence.

Success → Provider estimate returns, usually with confidence 0.85 when brand/item match is clean.

Failure → Falls through to local text estimation/fallback; the report notes this as a known coverage gap when credentials are missing.

### Open Food Facts Packaged-Food Lookup

Trigger → Query is packaged/non-US/country-specific/H-E-B packaged context, or barcode lookup reaches OFF. Text search expands queries with locale-word stripping, hardcoded aliases such as `tim tams` -> `Tim Tam`, and H-E-B / H-E-B Sushiya variants for H-E-B-prefixed queries.

Behavior → Search endpoint returns candidate products; product endpoint returns exact barcode product. OFF estimates include attribution and verified source URL. Text search tends to use 100 g basis; barcode can use serving macros when serving fields are present.

Validation → Text search requires quality tags and brand/country/private-label match. Barcode path is more permissive for exact products but still checks required nutrition, macro plausibility, and Atwater consistency for energy mismatch tags.

Success → Estimate source is `open_food_facts` or `open_food_facts_barcode`, confidence 0.72 for 100 g style estimates and 0.82 for serving-based barcode estimates.

Failure → Returns no estimate; barcode may become manual pending review at the route layer.

### H-E-B Curated Reference

Trigger → Text query matches plain H-E-B Sushiya California Roll without quantity/variant words.

Behavior → `heb_product_lookup.py` returns a hardcoded estimate from an official H-E-B product URL. It rejects variants such as spicy, crunchy, cauliflower, poke, brown, and quantified inputs like two rolls or 12 pieces. Other H-E-B private-label queries that pass direct lookup but miss every provider skip LM Studio unless the text carries quantity context, returning a needs-quantity manual fallback with a no-branded-match note.

Validation → Matching is token-based and exact enough to prevent neighboring H-E-B products from inheriting the curated estimate.

Success → Estimate source `heb_curated_reference`, confidence 0.88, verified URL to the H-E-B product page.

Failure → Lookup continues to later sources or falls back to review/manual parsing.

## 5. API Endpoints

| Method | Path | Auth | Trigger | Key params | Response shape | Real/Mock |
| --- | --- | --- | --- | --- | --- | --- |
| POST | `/api/meal-intake` | Owner session/CSRF | Text meal submit | `text`, `client_id`, timestamps | Review estimate/payload | Real app route; providers internal |
| POST | `/api/meal-intake/barcode` | Owner session/CSRF | Barcode lookup | `barcode`, `allow_pending`, `client_id` | Review payload plus barcode metadata | Real app route; providers internal |
| POST | `/api/meal-intake/<meal_id>/refresh` | Owner session/CSRF | Add/edit/choose source item in review | Refresh kind and item text/candidate | Replacement review payload | Real app route; providers internal |
| POST | `https://trackapi.nutritionix.com/v2/natural/nutrients` | `x-app-id`, `x-app-key` | Text branded/restaurant lookup | JSON `{"query": "<text>"}` | Nutritionix foods array | Real client code; account/TOS details unverified |
| GET | `https://trackapi.nutritionix.com/v2/search/item` | `x-app-id`, `x-app-key` | UPC lookup | `upc`, `servings_per_container=true` | Nutritionix foods/items payload | Real client code; account/TOS details unverified |
| GET | `https://api.nal.usda.gov/fdc/v1/foods/search` | `api_key` query param | Text or barcode USDA lookup | `query`, `dataType`, `pageSize=5` | FDC search payload | Real public API client |
| GET | `https://world.openfoodfacts.org/cgi/search.pl` | None; User-Agent | OFF text search | Search params, fields, optional country tag | Product list | Real public API client |
| GET | `https://world.openfoodfacts.org/api/v2/product/<barcode>.json` | None; User-Agent | OFF barcode lookup | Barcode path; fields query | Product payload with `status==1` | Real public API client |
| GET | `https://www.heb.com/product-detail/h-e-b-sushiya-california-roll/2038218` | None in runtime | Curated H-E-B provenance URL | None | Not fetched live by app | Curated/static reference, not provider client |

Endpoint details:

- Nutritionix clients use 1.5 second timeouts and return `None` for missing credentials, HTTP errors, URL errors, OS errors, timeouts, or JSON parse failures.
- USDA client uses 1.5 second timeout, requires `USDA_FDC_API_KEY`, and returns `None` on missing key or network/parse errors.
- Open Food Facts client uses 5 second per-request timeout, 6 second total timeout for search variants, and a fixed User-Agent string.
- No live scraping or browser automation is allowed in the meal logging path.

## 6. Data Model & Persistence

| Store | Key | Schema/fields | Retention |
| --- | --- | --- | --- |
| `branded_lookup_cache` | `user_id`, `normalized_text` | Source name, response JSON, fetched timestamp | TTL 180 days. Text source replay is `local_cache` with `underlying_source`. |
| `barcode_lookup_cache` | `user_id`, `barcode` | Source name, response JSON, fetched timestamp | TTL 180 days. Pending-source fallbacks are not cached. |
| `food_logs.original_estimate_json` | `client_id` | Accepted estimate/provenance snapshot | Retained with canonical food log and backup export. |
| `personal_vocab.canonical_resolution` | `normalized_input` | Learned canonical estimate JSON | Trust controlled by accept/correct/skip/delete counters. |
| Backup JSON | `food_logs`, `personal_vocab`, `meal_acceptance_events`, `meal_review_snapshots` | Export/import includes provider-derived estimates only after they are part of app data | Raw provider responses are not separately exported unless stored inside safe estimate fields. |

No provider responses are committed by this feature at runtime. The smoke coverage script intentionally skips cache reads by default and disables cache writes so a coverage run cannot teach the local app new nutrition rows.

## 7. Enums & Constants

| Name | Values | Meaning |
| --- | --- | --- |
| Text source priority | `cache`, `heb_curated_reference`, `nutritionix`, `usda_fdc`, `open_food_facts` | Default text lookup order. |
| Barcode source priority | `cache`, `nutritionix_barcode`, `usda_fdc_barcode`, `open_food_facts_barcode` | Default barcode lookup order; excludes H-E-B page lookup. |
| Cache TTL | 180 days | Max age for text/barcode lookup cache rows. Nutritionix ToS compatibility is unverified. |
| Barcode lengths | 8, 12, 13, 14 | Supported UPC/EAN/GTIN normalized digit lengths. |
| Nutritionix source tags | `nutritionix`, `nutritionix_barcode` | Text and UPC estimates from Nutritionix. |
| USDA source tags | `usda_fdc`, `usda_fdc_barcode` | Text and barcode estimates from FoodData Central. |
| Open Food Facts source tags | `open_food_facts`, `open_food_facts_barcode` | Text/search and barcode/product estimates from OFF. |
| Local/curated source tags | `local_cache`, `heb_curated_reference` | Cache replay and curated H-E-B product reference. Historical persisted rows may retain legacy tag `heb_product_page`; cache replay relabels it as curated. |
| Manual fallback source | `barcode_pending_source` | Unknown barcode manual-review estimate created by app route. |
| USDA text data types | `Branded`, `Foundation`, `SR Legacy` | Data types requested for text search. |
| USDA barcode data types | `Branded` | Data type requested for barcode search. |
| Open Food Facts complete-quality tags | `en:nutriments-completed`, `en:nutrition-completed`, `en:nutrition-data-complete` | Quality tags accepted for text search. Barcode can be exact-product permissive. |
| Provider timeouts | Nutritionix 1.5s; USDA 1.5s; OFF 5s request / 6s total search | Network budget before skipping/failing closed. |
| Confidence levels | Nutritionix clean 0.85; Nutritionix barcode 0.88; H-E-B curated 0.88; USDA text 0.55; USDA barcode 0.72; OFF text 0.72; OFF barcode 0.72 or 0.82 serving-based; personal vocab at least 0.9 | Review policy and user trust. |
| OFF attribution | `Source: Open Food Facts (ODbL/DbCL data; product images CC BY-SA)` | Best-effort attribution text currently carried in estimates. [TBC] Exact required text remains unverified. |
| Energy conversion | `KJ_PER_KCAL = 4.184` | Used to convert USDA FDC kJ energy rows to kcal; the OFF energy-mismatch check uses Atwater 4/4/9 factors with 12%/25 kcal tolerance. |
| Smoke query categories | `required`, `proxy` | Required Bill Miller queries and proxy regional-chain checks. |

## 8. Integration Points

- Meal text parsing uses source lookup before local model fallback.
- Barcode intake depends on barcode provider lookup and creates manual review only when no verified source resolves.
- V2 review add/edit/choose flows can invoke text/provider lookup and recompute totals.
- Meal estimate schema sanitizes every provider estimate before it reaches UI or persistence.
- Meal log policy uses confidence, ambiguity, and plausible numeric ranges to decide review status; current meal route still forces fresh review.
- Personal vocabulary can override source lookup when a trusted phrase exists.
- Food-log refresh events can be created when accepted verified-source estimates later change; event listing/acknowledgment is in PRD 04.

## 9. Permissions & Security

Provider API keys are read from environment variables and are never logged or echoed by the clients. Missing credentials cause silent source skips, not user-visible secret errors. The app stores sanitized estimates and safe provenance, not raw provider payloads, prompt traces, or model messages.

Open Food Facts uses a public API with a fixed app User-Agent. USDA uses a query-parameter API key. Nutritionix uses app ID/key headers. Live scraping is explicitly prohibited in the meal path; any future scraper must be an offline admin script with separate issue, ToS review, robots review, fixtures, and no user-facing endpoint access.

Source links shown in V2 source viewer are expected to be same-origin sanitized links. Legacy provenance may render external source links with `target="_blank"` and `rel="noopener noreferrer"`.

## 10. Business Rules

- Cache wins first when fresh and trusted for the query context.
- H-E-B curated lookup wins before provider APIs only for the exact known H-E-B Sushiya California Roll pattern and never for barcode.
- Nutritionix is the preferred live provider for restaurant/branded natural-language lookup when credentials are configured.
- USDA is authoritative/public-domain but often generic and serving-size limited; current text estimates are review-oriented and usually 100 g based.
- Open Food Facts is best for packaged products and international/non-US products, not restaurant chains.
- Unknown barcode with `allow_pending: false` returns 404; with `allow_pending: true` creates a manual review card with confidence 0.2 and source `barcode_pending_source`.
- Provider failures do not block meal logging. They return `None` and allow the parser/route to continue to lower-priority sources or manual review.
- Direct lookup should not run on multi-item meals, combo meals, vague portions, or restaurant names without an item token, because those are better handled by review/candidate flows.
- Source confidence is intentionally below auto-log thresholds for uncertain/generic matches, but current fresh meal intake is review-first regardless of confidence.
- Open Food Facts quality rules reject severe data issues, missing required nutrition, implausible per-100g macros, and inconsistent energy mismatch tags unless Atwater math is consistent within tolerance.

## 11. Config & Environment

| Config | Default | Behavior when unset |
| --- | --- | --- |
| `NUTRITIONIX_APP_ID` | Unset | Nutritionix text and UPC calls return `None`; lookup continues. |
| `NUTRITIONIX_APP_KEY` | Unset | Same as above. |
| `USDA_FDC_API_KEY` | Unset | USDA text and barcode calls return `None`; lookup continues. |
| Open Food Facts credentials | None required | Client can call public endpoints with User-Agent. |
| `DATA_DIR` | App data directory | Lookup caches live in local SQLite under resolved data dir. |
| Source priority override | Not configured in env; optional function arg | Tests and smoke scripts can force source order. |

Documentation caveats:

- Nutritionix endpoint/header names are implemented in code but account quota, cache TTL permission, and redistribution rights remain [TBC] in `docs/nutrition_sources.md`.
- Open Food Facts license family is documented, but exact attribution text/format, rate limits, and any current API header requirements remain [TBC].

## 12. Test Coverage

`tests/test_branded_food_lookup.py` is the main coverage file. It covers normalization, direct-lookup gating, H-E-B private-label matching and variant rejection, Nutritionix provenance, regional restaurant handling, category mismatch confidence lowering, source priority order, cache replay, H-E-B cache trust, barcode normalization, barcode cache replay, Nutritionix UPC, USDA-before-OFF ordering, USDA duplicate/incomplete match behavior, OFF barcode fallback, OFF serving macros, and OFF energy mismatch/Atwater checks.

`scripts/smoke_branded_lookup_coverage.py` provides a read-only regional-chain coverage matrix. It reports provider readiness, skips cache by default, disables cache writes, and records whether production direct lookup would block a query. The committed `docs/nutrition_sources.md` FIT-98 smoke result showed Nutritionix and USDA credentials missing, OFF reachable but not useful for restaurant-chain rows, and regional-chain coverage therefore unresolved in that environment.

`docs/FIT202_PUBLIC_FOOD_SMOKE_AND_ACCURACY_REPORT.md` documents CI-safe public barcode/package and photo metadata cases. It records strict barcode assertions, generic photo confidence/pending-review gates, and known macro accuracy limits for vision. For this PRD, the relevant point is that barcode/package cases require strict source/nutrition assertions, while pure vision remains confidence-capped and pending-review.

Coverage gaps: no live-provider integration test can run without credentials; Nutritionix ToS/account verification is not testable in code; source refresh events are only indirectly tied to accepted verified estimates in the assigned sources. [TBC] Additional tests outside the assigned list may cover refresh event creation.

## 13. Gaps & Issue Candidates

### IC-1: Verify Nutritionix quota, cache TTL, and redistribution
- **Type:** Data-contract
- **Priority:** high
- **Where:** `docs/nutrition_sources.md`; `nutritionix_client.py`; `branded_food_lookup.py`
- **Problem:** The Nutritionix client is implemented, but repository docs still mark live account quota, ToS cache duration, redistribution, and some endpoint details as unverified because the docs were blocked during research.
- **Why it matters:** A 180-day cache or committed/offline snapshot could violate provider terms if the assumptions are wrong.
- **Acceptance criteria:**
  - Owner verifies current Nutritionix dashboard quota and ToS using the production account.
  - Cache TTL and offline/snapshot permissions are documented with date and source.
  - Code TTL is adjusted if the verified limit is shorter than 180 days.
  - PR/test docs state expected behavior when quota is exhausted.
- **Duplicate-of:** none

### IC-2: Add provider fallback observability
- **Type:** Improvement
- **Priority:** high
- **Where:** `branded_food_lookup.py`; `nutritionix_client.py`; `usda_fdc_client.py`; `open_food_facts_client.py`
- **Problem:** Provider clients generally fail closed by returning `None`, which is user-safe but makes it hard to distinguish missing credentials, quota failures, provider errors, quality rejection, and no-match fallthrough.
- **Why it matters:** Silent fallback can turn a provider coverage issue into a low-confidence manual estimate without enough evidence to fix the source chain.
- **Acceptance criteria:**
  - Lookup attempts record structured non-secret diagnostics per source.
  - User-facing review still shows only safe source/review copy.
  - Smoke script can report skipped, failed, rejected, and no-match separately.
  - Tests cover missing credentials, HTTP failure, quality rejection, and no match.
- **Duplicate-of:** FIT-266

### IC-3: Score USDA candidates instead of taking first usable result
- **Type:** Bug
- **Priority:** medium
- **Where:** `branded_food_lookup.py`; `usda_fdc_client.py`
- **Problem:** USDA text/barcode fallback can rely on the first usable search result after basic validation. For branded or generic queries, the first result may not be the best match by brand, UPC variant, portion, or item category.
- **Why it matters:** A plausible but wrong USDA result can create a confident-looking review card and eventually a canonical food log.
- **Acceptance criteria:**
  - Candidate loop scores brand, item tokens, barcode variants, data type, and nutrient completeness.
  - Low-scoring matches are rejected or marked low confidence with review notes.
  - Tests cover wrong brand, wrong category, duplicate barcode variants, and incomplete nutrients.
  - Smoke output includes the selected candidate rationale.
- **Duplicate-of:** FIT-266

### IC-4: Prevent restaurant combos from becoming canonical AI nutrition
- **Type:** Bug
- **Priority:** high
- **Where:** `branded_food_lookup.should_attempt_direct_lookup`; `meal_text_parser.py`; accept path
- **Problem:** The direct lookup gate blocks many combo/multi-item phrases, but unresolved restaurant combo meals can still fall through to AI/fallback estimation and later become accepted canonical nutrition if the owner saves them without authoritative source evidence.
- **Why it matters:** Combo meals are high-impact calorie/macros entries; undercounts can distort readiness and nutrition coaching.
- **Acceptance criteria:**
  - Combo/meal/plate restaurant phrases remain clearly marked as manual/AI, not verified provider estimates.
  - Review UI requires explicit owner confirmation for source-lacking restaurant combos.
  - Accepted rows retain source provenance that distinguishes provider-backed from AI fallback.
  - Tests include Raising Cane's/restaurant combo examples and source assertions.
- **Duplicate-of:** FIT-226

### IC-5: Keep fallback-tier cache and vocabulary trust separated
- **Type:** Data-contract
- **Priority:** high
- **Where:** `branded_food_lookup.py`; `personal_vocab.py`; `meal_text_parser.py`
- **Problem:** Cache replay, personal vocabulary, and fallback/manual estimates all produce the same public estimate shape. Without strict tier separation, a low-authority source can be replayed later as if it were verified.
- **Why it matters:** Trust inflation makes bad estimates durable and harder for the owner to notice.
- **Acceptance criteria:**
  - Cache entries preserve original authority tier and source, not only public nutrition fields.
  - Personal vocabulary cannot trust fallback/manual estimates without explicit accepted evidence.
  - UI labels distinguish cached verified source from cached fallback/manual source.
  - Tests cover source-tier replay and vocabulary trust downgrade.
- **Duplicate-of:** FIT-259

### IC-6: Replace or label the H-E-B curated reference
- **Type:** Docs
- **Priority:** medium
- **Where:** `heb_product_lookup.py`; `branded_food_lookup.py`; source labels
- **Problem:** The H-E-B path is not a general H-E-B API client; it is a hardcoded curated estimate for one product URL. That is useful, but it can be mistaken for a real live H-E-B provider integration.
- **Why it matters:** Product and agents may overestimate H-E-B coverage and file fewer lookup gaps than the owner actually experiences.
- **Acceptance criteria:**
  - Source label or docs call this a curated H-E-B reference, not a live H-E-B provider.
  - Tests keep variant/quantity rejection strict.
  - New H-E-B products require either curated entries with source evidence or a real provider plan.
  - Smoke reports list curated references separately from live provider results.
- **Duplicate-of:** none

### IC-7: Verify Open Food Facts attribution and rate limits
- **Type:** Privacy
- **Priority:** medium
- **Where:** `docs/nutrition_sources.md`; `open_food_facts_client.py`; `static/js/app.js` provenance rendering
- **Problem:** The app carries best-effort OFF attribution text and a User-Agent, but the exact current attribution format, share-alike implications, and rate-limit policy remain partially unverified in the docs.
- **Why it matters:** OFF data has license obligations; incorrect or missing attribution creates compliance risk even in a local-first app.
- **Acceptance criteria:**
  - Verify current OFF API docs and terms for attribution, User-Agent, and rate limits.
  - Update docs and UI attribution text if required.
  - Tests assert OFF attribution reaches review/detail surfaces after cache replay.
  - Rate-limit/backoff behavior is documented or implemented if OFF requires it.
- **Duplicate-of:** none

### IC-8: Add credentialed provider smoke mode with safe output
- **Type:** Test
- **Priority:** medium
- **Where:** `scripts/smoke_branded_lookup_coverage.py`; `docs/nutrition_sources.md`
- **Problem:** The smoke helper is read-only and useful, but the committed FIT-98 result was environment-limited because Nutritionix and USDA credentials were missing. There is no standardized credentialed smoke report shape that proves live regional/provider coverage without leaking secrets or mutating caches.
- **Why it matters:** Provider coverage can appear broken in clean CI while working locally, or vice versa, without a safe evidence artifact.
- **Acceptance criteria:**
  - Add a documented credentialed smoke command that redacts secrets and disables cache writes.
  - Output separates provider unavailable, no match, wrong-chain match, and accepted match.
  - Report includes provider status, source priority, cache mode, and direct-gate mode.
  - Tests cover report formatting and redaction without live network.
- **Duplicate-of:** none
