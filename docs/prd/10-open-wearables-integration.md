# Open Wearables Integration

Open Wearables (OW) is the only wearable hub. WHOOP, Oura and Apple Health reach the app through it, and the app reads one store of normalized daily facts (`wearable_facts.sqlite3`, table `wearable_daily_facts`). Coaching stays local: the plan is deterministic rules over those facts. The old direct Oura and WHOOP paths are gone (PER-1584).

## Source rule (the one place: `wearable_fact_store.SOURCE_PREFERENCE`)

| Domain | Order | Facts |
|---|---|---|
| sleep | Oura, WHOOP, Apple | `sleep_*` (duration, stages, efficiency, time in bed) |
| recovery | Oura, WHOOP, Apple | `recovery_score`, HRV, resting heart rate |
| activity | WHOOP, Oura, Apple | steps, active minutes, active calories |

- One fact per date and metric: provider order first, then a live fact over an archive fact, then summary over event, then the newest write.
- Two providers more than 60 minutes apart on hours slept are a conflict: the lower value is used and `conflict_state` says so. The whole night (total and stages) is then that provider's, and the proof names it. Recovery scores are never merged.
- Readiness is a sleep estimate from the newest fresh night of the preferred sleep provider. A recovery score is not readiness; WHOOP's reaches the plan once, through the guard below.
- Facts older than two days are `stale`: they do not feed readiness, sleep debt or the HRV trend, they show as stale in the source proof, and they lower confidence.
- The proof names the provider and date of the fact behind each consumed field (sleep debt row, HRV series, the guard's highest active-minutes fact), not the newest fact of any metric in the domain.
- A WHOOP recovery score under 34 downgrades the plan once; 34-66 caps displayed RPE at 7.0 (`open_wearables_hub.conservative_modifier`).

## What the app shows

- Dashboard sources line: `Sources: sleep Oura · recovery Oura · activity WHOOP via Open Wearables · 07:12`, from `source_proof` on `/api/recommendation/smart`: `{provider, via, date, freshness, used_for_recommendation, ignored_reason, conflict_state}` per domain.
- `/api/wearables/today`, `/api/wearables/trends`, `/api/sleep/summary`, `/api/sleep/analytics`, `/api/vitals`: all read stored facts.
- History: `/api/open-wearables/workouts` lists stored workout facts; one workout recorded by two providers is one row with both labels; rows from the archive import say `(archive)`. History never calls OW, so an OW outage cannot fail it.

## Keeping facts fresh

- A read refreshes the live 7-day window at most once per 15 minutes (`_refresh_wearable_facts`); on any failure the stored facts are served.
- `app.backfill_open_wearables_facts(days=365)` stores history in 28-day windows, oldest first.
- `scripts/import_archive_facts.py --oura <oura archive> --apple <apple archive> --db wearable_facts.sqlite3` imports archived Oura and Apple Health history as `source_system="archive"` facts. It opens archives read-only, never deletes, is safe to repeat, and skips any provider, metric and date that OW already covers.

## Setup, provenance, safety

- Setup (hub profile, provider pairing, phone invitations) is in Settings, "Open Wearables". Only normalized, coaching-safe facts are stored; raw samples, tokens and exception text are never stored or returned.
- Every fact keeps `provider_id`, `source_provider`, `original_label`, `observed_at` and `imported_at` (first import time survives refreshes). A timestamp without an offset is UTC.
- Live code: `open_wearables_hub.py`, `wearable_fact_store.py`, `recommendation_sources.py`, `app.py` (`fetch_open_wearables_data`, `_preferred_facts`).
