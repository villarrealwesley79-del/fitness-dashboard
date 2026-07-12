# Enum Dictionary

> **Sources:** `app.py`, `auth.py`, `meal_log_policy.py`, `meal_estimate_schema.py`, `meal_text_parser.py`, `branded_food_lookup.py`, `data_store.py`, `workout_adaptation.py`, `whoop_recommendations.py`, `whoop_store.py`, `whoop_client.py`, `wearable_fact_store.py`, `recommendation_sources.py`, `local_vision_adapter.py`, `lm_studio_adapter.py`, `open_wearables_adapter.py`, `apple_health_parser.py`, `health_ingest.py`, `runtime_config.py`, `stripe_checkout.py`, `static/js/app.js`, `static/js/sw.js`
> **Routes:** Cross-app constants and status vocabulary consumed by multiple routes.
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

This file lists business-significant enum values, status strings, source tags, thresholds, limits, and magic constants. Python does not consistently use `Enum`; many product states are plain strings.

## Training Goals And Recommendation Parameters

| Value | Meaning | Provenance |
| --- | --- | --- |
| `strength` | Maximize 1RM, heavy loads, low reps; 1-5 reps, 5 sets, RPE 8.5, 85% intensity, 0.8 volume multiplier. | `app.py:339`, `app.py:350` |
| `hypertrophy` | Muscle growth, moderate loads, higher volume; 8-12 reps, 4 sets, RPE 7.5, 70% intensity, 1.2 volume multiplier. | `app.py:339`, `app.py:362` |
| `endurance` | Muscular endurance; 15-25 reps, 3 sets, RPE 6.5, 50% intensity, cardio included. | `app.py:339`, `app.py:374`, `app.py:544` |
| `weight_loss` | Calorie-burn/circuit-style goal; 8-12 reps, 3 sets, RPE 7, cardio included. | `app.py:339`, `app.py:386`, `app.py:556` |
| `toning` | Muscle definition; 8-15 reps, 3 sets, RPE 6.5, cardio included. | `app.py:339`, `app.py:422`, `app.py:568` |
| `strength_hypertrophy` | Default hybrid goal; 5-8 reps, 4 sets, RPE 8, no default cardio. | `app.py:339`, `app.py:398`, `app.py:580` |
| `hypertrophy_endurance` | Concurrent hypertrophy/endurance; 10-15 reps, 4 sets, RPE 7, cardio included. | `app.py:339`, `app.py:410`, `app.py:592` |
| `weight_loss_toning` | Hybrid fat-loss/definition goal; 8-15 reps, 3 sets, RPE 7, cardio included. | `app.py:339`, `app.py:432`, `app.py:604` |
| `On Track` | Progression status for expected improvement. | `app.py:931` |
| `Plateau` | Progression status for stalled performance. | `app.py:931` |
| `Regression` | Progression status for declining performance. | `app.py:931` |

Default settings:

| Constant / field | Value | Meaning | Provenance |
| --- | --- | --- | --- |
| `training_goal` | `strength_hypertrophy` | Default goal. | `app.py:442` |
| `sessions_per_week_target` | `3` | Default weekly session target. | `app.py:446` |
| `available_time_minutes` | `75` | Default workout duration target. | `app.py:447` |
| `target_weight_lbs` | `175` | Default body-weight goal. | `app.py:448` |
| `target_body_fat_pct` | `18` | Default body-fat goal. | `app.py:449` |
| `daily_calorie_target` | `2200` | Default nutrition calorie target. | `app.py:450` |
| `daily_protein_target_g` | `148` | Default protein target. | `app.py:451` |
| `fatigue_threshold` | `72` | Default fatigue/readiness threshold used by planning. | `app.py:452` |
| `equipment_preference` | `machines_only` | Default workout equipment mode. | `app.py:453` |
| `preferred_equipment_brands` | `Hoist`, `Nautilus` | Preferred machine brands. | `app.py:454` |
| `excluded_exercises` | `Preacher Curl` | Default excluded exercise. | `app.py:455` |
| `volume_landmarks.default` | `mv=6`, `mev=9`, `mav_min=12`, `mav_max=18`, `mrv=22` | Default volume landmarks. | `app.py:456` |
| `TIME_OPTIONS` | `20`, `30`, `45`, `60`, `75`, `90` minutes | Allowed preferred workout duration options. | `app.py:497` |
| `MESOCYCLE_PLAN` | Week 1 `Accumulation`, Week 2 `Overreach`, Week 3 `Intensification`, Week 4 `Deload` | Four-week training-cycle labels and volume/RPE shaping. | `app.py:3525` |

## Cardio And Workout Source Constants

| Value | Meaning | Provenance |
| --- | --- | --- |
| Zone 1 | 50-60% max HR, recovery/warmup; comments assume max HR 190, 95-114 BPM. | `app.py:514` |
| Zone 2 | 60-70% max HR, fat-burning/endurance base; 114-133 BPM. | `app.py:515` |
| Zone 3 | 70-80% max HR, aerobic/cardio; 133-152 BPM. | `app.py:516` |
| Zone 4 | 80-90% max HR, threshold/performance; 152-171 BPM. | `app.py:517` |
| Zone 5 | 90-100% max HR, max effort; 171-190 BPM. | `app.py:518` |
| `CARDIO_MODALITY_POOLS` | Goal-keyed cardio modality pools: endurance uses Outdoor run/Treadmill run/Bike/Rower/Stairmaster; weight_loss uses Stairmaster/Rower. | `app.py:604` |
| `CARDIO_RECOVERY_MODALITIES` | Recovery cardio options: Treadmill incline walk, Bike, Elliptical, Outdoor walk. | `app.py:618` |
| `outdoor run`, `outdoor walk` | Outdoor modality set. | `app.py:647` |
| `bike -> cycling`, `rower -> rowing`, treadmill variants -> `treadmill`, outdoor variants -> `running`/`walking` | Cardio fatigue-category aliases. | `app.py:669` |
| `APPLE_HEALTH_WORKOUT_HR_MIN` | `30` | Minimum plausible Apple Health workout HR. | `app.py:1340` |
| `APPLE_HEALTH_WORKOUT_HR_MAX` | `230` | Maximum plausible Apple Health workout HR. | `app.py:1341` |
| `strength_training` | Canonical history category for lifted/strength labels. | `history_normalization.py:10`, `static/js/app.js:3722` |

## Meal Estimate, Review, And Nutrition Policy

| Value | Meaning | Provenance |
| --- | --- | --- |
| `breakfast`, `lunch`, `dinner`, `snack` | Allowed meal types. | `meal_estimate_schema.py:14`, `app.py:6536`, `static/js/app.js:10188` |
| `logged` | Meal estimate was accepted/auto-logged and counts toward nutrition/coaching. | `meal_log_policy.py:55` |
| `pending_review` | Meal estimate requires owner review before it counts. | `meal_log_policy.py:56` |
| `accepted` | Persisted correction state for accepted food logs. | `meal_log_policy.py:61` |
| `high`, `medium`, `low` | Confidence bands for meal policy. | `meal_log_policy.py:65` |
| `low_confidence` | Review reason when confidence is below `0.55`. | `meal_log_policy.py:70` |
| `medium_confidence` | Review reason when confidence is between `0.55` and `0.75`. | `meal_log_policy.py:71` |
| `ambiguous_input` | Review reason when estimate marks ambiguity. | `meal_log_policy.py:72` |
| `implausible_calories` | Review reason for calories outside `0..5000`. | `meal_log_policy.py:73` |
| `implausible_macros` | Review reason for protein/carbs/fat/fiber outside `0..500g`. | `meal_log_policy.py:74` |
| `implausible_sodium` | Review reason for sodium outside `0..12000mg`. | `meal_log_policy.py:75` |
| `missing_calories` | Review reason when calories are absent/malformed. | `meal_log_policy.py:76` |
| `HIGH_CONFIDENCE_THRESHOLD` | `0.75`; minimum auto-log confidence. | `meal_log_policy.py:40` |
| `MEDIUM_CONFIDENCE_THRESHOLD` | `0.55`; threshold for gentler uncertainty copy. | `meal_log_policy.py:44` |
| `CALORIE_MAX` | `5000` per meal. | `meal_log_policy.py:48`, `meal_estimate_schema.py:51` |
| `MACRO_GRAM_MAX` | `500` grams per meal for protein/carbs/fat/fiber. | `meal_log_policy.py:49`, `meal_estimate_schema.py:52` |
| `SODIUM_MG_MAX` | `12000` mg sodium per meal. | `meal_log_policy.py:50`, `meal_estimate_schema.py:53` |
| `included`, `skipped`, `deleted` | Meal review item states. Included items count toward totals; skipped/deleted do not. | `app.py:6391`, `app.py:6535`, `static/js/app.js:11361` |
| `add_item`, `edit_portion`, `choose_candidate`, `followup_answer` | Meal review refresh request kinds that receive live request IDs. | `app.py:6537`, `static/js/app.js:11357` |
| `skip_item`, `delete_item`, `restore_item`, `set_meal_type` | Additional v2 meal review refresh actions from UI. | `static/js/app.js:11347` |
| `vision`, `text`, `branded`, `vocab`, `manual` | Meal review v2 source kinds accepted by UI. | `static/js/app.js:11362` |
| `external_food_id`, `verified_source_url`, `data_fetched_at`, `portion_basis`, `brand_id`, `underlying_source`, `off_attribution`, `personal_vocab_phrase` | Public provenance fields allowed in estimates. | `meal_estimate_schema.py:40` |
| `vision_description`, `vision_provider`, `vision_confidence`, `fallback_reason` | Extra safe metadata preserved for vision/text fallbacks. | `app.py:5598` |
| `verified_source_url`, `raw_model_trace`, `raw_trace`, `prompt`, `messages`, `image_bytes`, `image`, `provider_payload`, `vendor_payload`, `candidates` | Prohibited estimate keys for review payloads. | `app.py:6538` |
| `discard_after_extraction` | Food-photo retention policy: raw photos/model traces not retained or backed up. | `app.py:5611` |
| `PENDING_MEAL_REVIEW_TTL_DAYS` | `7`; pending review TTL. | `app.py:5618` |
| `_MEAL_INTAKE_MAX_IMAGE_BYTES` | `6 MB` per photo. | `app.py:5591`, `static/js/app.js:9585` |
| `_MEAL_INTAKE_MAX_IMAGE_COUNT` | `4` photos per meal. | `app.py:5593`, `static/js/app.js:9584` |
| `_MEAL_INTAKE_MAX_AGGREGATE_BYTES` | `18 MB` total image payload. | `app.py:5596`, `static/js/app.js:9589` |
| Supported image MIME types | `image/jpeg`, `image/png`, `image/webp`, `image/gif`. | `app.py:5597` |
| `MEAL_UNDO_MS` | `30000`; undo toast window after accepted meal logging. | `static/js/app.js:9581` |

Food-source and lookup constants:

| Value | Meaning | Provenance |
| --- | --- | --- |
| `cache`, `heb_curated_reference`, `nutritionix`, `usda_fdc`, `open_food_facts` | Text lookup source priority. | `branded_food_lookup.py:19` |
| `cache`, `nutritionix_barcode`, `usda_fdc_barcode`, `open_food_facts_barcode` | Barcode lookup source priority. | `branded_food_lookup.py:23` |
| Barcode lengths | `8`, `12`, `13`, `14` | Accepted barcode lengths. | `branded_food_lookup.py:24` |
| `KJ_PER_KCAL` | `4.184` | Energy conversion. | `branded_food_lookup.py:27` |
| `CACHE_TTL_DAYS` | `180` | Branded lookup cache TTL. | `branded_food_lookup.py:18` |
| `MIN_ACCEPTS_FOR_EXACT` | `3` | Personal vocab exact-match threshold. | `personal_vocab.py:13` |
| `MIN_ACCEPTS_FOR_FUZZY` | `1` | Personal vocab fuzzy-match threshold. | `personal_vocab.py:14` |
| `FUZZY_CUTOFF` | `0.78` | Personal vocab fuzzy cutoff. | `personal_vocab.py:15` |
| `REFRESH_CALORIE_DELTA_THRESHOLD` | `1` kcal | Food-log refresh event threshold for calories. | `data_store.py:26` |
| `REFRESH_MACRO_DELTA_THRESHOLD` | `0.5g` | Food-log refresh event threshold for macros. | `data_store.py:27` |
| `REFRESH_SODIUM_DELTA_THRESHOLD` | `1mg` | Food-log refresh event threshold for sodium. | `data_store.py:28` |

Meal text fallback reasons:

| Value | Meaning | Provenance |
| --- | --- | --- |
| `empty_input` | No useful text was supplied. | `meal_text_parser.py:82` |
| `needs_quantity` | Parser needs quantity/portion clarification. | `meal_text_parser.py:83` |
| `timeout` | LM Studio text estimate timed out. | `meal_text_parser.py:84` |
| `invalid_json` | AI returned invalid JSON. | `meal_text_parser.py:85` |
| `schema_mismatch` | AI JSON failed required schema. | `meal_text_parser.py:86` |
| `lock_timeout` | Meal-text inference lock could not be acquired. | `meal_text_parser.py:87` |
| `all_endpoints_failed` | All text-estimate endpoints failed. | `meal_text_parser.py:88` |
| `LM_STUDIO_MEAL_TEXT_TIMEOUT_SEC` | `45` seconds default. | `meal_text_parser.py:81` |

## Nutrition-To-Workout Adaptation

| Constant / value | Meaning | Provenance |
| --- | --- | --- |
| `COALESCING_WINDOW_SECONDS` | `180`; wait window before processing accepted food into workout adaptation events. | `workout_adaptation.py:22` |
| `MIN_WORKOUT_CONFIDENCE` | `0.65`; minimum confidence for workout-affecting nutrition adaptation. | `workout_adaptation.py:23` |
| `UNDER_FUELED_CALORIES_PCT` | `60`; low calorie percentage threshold. | `workout_adaptation.py:24`, `app.py:1809` |
| `UNDER_FUELED_PROTEIN_PCT` | `50`; low protein percentage threshold before hard workout context. | `workout_adaptation.py:25`, `app.py:1810` |
| `LOW_PROTEIN_PCT` | `80`; general low-protein threshold for guidance. | `workout_adaptation.py:26` |
| `SODIUM_RECOVERY_CONTEXT_MG` / `SODIUM_NEXT_DAY_CONTEXT_MG` | `2300`; high-sodium next-day/recovery context threshold. | `workout_adaptation.py:27`, `app.py:1807` |
| `LATE_MEAL_HOUR` / `LATE_MEAL_CONTEXT_HOUR` | `20`; 8 PM local cutoff for late-meal context. | `workout_adaptation.py:28`, `app.py:1808` |
| `HEAVY_MEAL_CALORIES` | `900`; heavy-meal threshold. | `workout_adaptation.py:29` |
| `bad`, `cheat`, `guilt`, `punish`, `punitive`, `unhealthy` | Moral labels to avoid in nutrition guidance. | `workout_adaptation.py:58` |
| `alcohol`, `beer`, `wine`, `cocktail`, `margarita`, `whiskey`, `vodka`, `tequila`, `bourbon`, `rum`, `hard seltzer` | Alcohol context terms. | `workout_adaptation.py:59` |
| `pollable_event_feed` | Delivery contract for workout adaptation events. | `workout_adaptation.py:129`, `workout_adaptation.py:163` |

## Wearable Freshness And Recommendation Source States

| Value | Meaning | Provenance |
| --- | --- | --- |
| `fresh` | Last data point is under 24 hours old. | `app.py:13650`, `app.py:13654` |
| `aging` | Last data point is 24-48 hours old. | `app.py:13650`, `app.py:13651` |
| `stale` | Last data point is over 48 hours old. | `app.py:13651` |
| `missing` | No data point exists. | `app.py:13654` |
| `low`, `medium`, `high` | Recovery/readiness bands. Oura: low <65, medium <80, high >=80. WHOOP: low <45, medium <67, high >=67. | `whoop_recommendations.py:6`, `whoop_recommendations.py:15` |
| `none`, `low`, `medium`, `high` | Bounded confidence values for WHOOP recommendation signals. | `whoop_recommendations.py:30`, `whoop_recommendations.py:118` |
| `display_only` | WHOOP state when freshness is not fresh/aging, score is not `SCORED`, or no metrics exist. | `whoop_recommendations.py:57` |
| `deload` | WHOOP modifier for low recovery, high strain, or meaningfully low sleep; sets recommendation to recovery, volume 0.8, RPE -1. | `whoop_recommendations.py:91`, `whoop_recommendations.py:221` |
| `caution` | WHOOP modifier for moderately low recovery/strain/sleep; downgrades recommendation one step, volume 0.9, RPE -1. | `whoop_recommendations.py:94`, `whoop_recommendations.py:224` |
| `sleep_priority` | WHOOP modifier when sleep need gap is >=60 minutes. | `whoop_recommendations.py:114` |
| `fuel_up` | WHOOP modifier paired with sleep need gap >=60 minutes. | `whoop_recommendations.py:115` |
| `apple_health` | Load source used by WHOOP recommendation signals. | `whoop_recommendations.py:33` |
| `source_conflict` | UI/source state when Oura and WHOOP bands diverge by more than one band. | `whoop_recommendations.py:139`, `static/js/app.js:1348` |
| `warning` | Source conflict severity when conservative-source selection is required. | `whoop_recommendations.py:165` |

Recommendation-source proof vocabulary:

| Value | Meaning | Provenance |
| --- | --- | --- |
| `bounded modifier` | Source role when a wearable can conservatively change the recommendation. | `recommendation_sources.py:35` |
| `display only` | Source role when data is shown but does not modify recommendation. | `recommendation_sources.py:36` |
| `bounded_modifier` | Machine influence value for conservative modifier source proof. | `recommendation_sources.py:37` |
| `none` | Machine influence value when no source modified the recommendation. | `recommendation_sources.py:37` |
| `open_wearables` / `Open Wearables` | Source key/label for Open Wearables recommendation proof. | `recommendation_sources.py:16` |
| `connected`, `blocked` | Additional freshness/status values used by recommendation-source proof. | `recommendation_sources.py:16` |
| `open_wearables`, `whoop`, `source_conflict`, `load_source`, `summary_hidden`, `load_source_summary_hidden` | Recommendation-source payload keys and suppression flags. | `app.py:11877` |

WHOOP UI/freshness states:

| Value | Meaning | Provenance |
| --- | --- | --- |
| `connected` | WHOOP connection exists and can be used normally. | `static/js/app.js:1337` |
| `disconnected` | No active WHOOP connection. | `static/js/app.js:1337` |
| `missing_config` | Server lacks required WHOOP OAuth config. | `static/js/app.js:1337` |
| `csv_only` | Local data came from CSV/import path without live OAuth. | `static/js/app.js:1337` |
| `syncing` | WHOOP sync is in progress. | `static/js/app.js:1338` |
| `fresh` | WHOOP data is fresh. | `static/js/app.js:1338` |
| `stale` | WHOOP data is stale. | `static/js/app.js:1337` |
| `aging` | WHOOP data is aging but not stale. | `static/js/app.js:1337` |
| `missing` | No WHOOP fact data available; raw `no_data` / no-data aliases are normalized to `missing`. | `static/js/app.js:1337`, `static/js/app.js:1387` |
| `pending_score` | Latest WHOOP day is pending score. | `static/js/app.js:1346` |
| `unscorable` | WHOOP data cannot be scored. | `static/js/app.js:1337` |
| `calibrating` | WHOOP needs calibration before score can be trusted. | `static/js/app.js:1337` |
| `reauth_required` | WHOOP must be reconnected. | `static/js/app.js:1349` |
| `error` | WHOOP status/sync failed. | `static/js/app.js:1337` |

WHOOP import and metric bounds:

| Field | Bounds / value | Meaning | Provenance |
| --- | --- | --- | --- |
| `WHOOP_CSV_MAX_BYTES` | `512 KB` | Max manual CSV upload size. | `app.py:213` |
| `WHOOP_CSV_MAX_ROWS` | `5000` | Max manual CSV rows. | `app.py:214` |
| `recovery_score` | `0..100` | WHOOP recovery score. | `app.py:12459` |
| `strain` | `0..21` | WHOOP strain. | `app.py:12459` |
| `sleep_performance_pct` | `0..100` | WHOOP sleep performance percent. | `app.py:12459` |
| `sleep_need_gap_min` | `0..1440` | WHOOP sleep gap minutes. | `app.py:12459` |
| `workout_kj` | `0..20000` | WHOOP workout kilojoules. | `app.py:12459` |
| `hrv_rmssd` | `0..300` | HRV RMSSD. | `app.py:12459` |
| `resting_hr` | `25..220` | Resting heart rate. | `app.py:12459` |
| `respiratory_rate` | `5..40` | Respiratory rate. | `app.py:12459` |
| `spo2` | `50..100` | Blood oxygen percent. | `app.py:12459` |
| `skin_temp` | `-20..60` | Skin temp delta/value as stored. | `app.py:12459` |
| `percent_recorded` | `0..100` | Recording completeness. | `app.py:12459` |
| `WHOOP_DEFAULT_SCOPES` | `offline`, `read:recovery`, `read:cycles`, `read:sleep`, `read:workout` | OAuth scopes. | `whoop_client.py:20` |
| `fitness-dashboard-whoop-client-secret` | Keychain service for WHOOP client secret. | `whoop_client.py:27` |
| `fitness-dashboard-whoop-oauth-material` | Stable material reference stored in normal DB instead of raw token payload. | `whoop_store.py:13` |

## Open Wearables

| Value | Meaning | Provenance |
| --- | --- | --- |
| Providers: `garmin`, `oura`, `whoop`, `suunto`, `polar`, `ultrahuman`, `strava`, `fitbit` | Cloud provider tiles. Cloud sign-in is gated on connector credentials. | `app.py:10211` |
| Providers: `apple`, `samsung`, `google` | SDK/phone health sources using mobile invite codes instead of provider website OAuth. | `app.py:10211` |
| Provider credential keys | `WHOOP_CLIENT_ID/SECRET`, `OURA_CLIENT_ID/SECRET`, `GARMIN_CLIENT_ID/SECRET`, `STRAVA_CLIENT_ID/SECRET`, `FITBIT_CLIENT_ID/SECRET`, `POLAR_CLIENT_ID/SECRET`, `SUUNTO_CLIENT_ID/SECRET`, `ULTRAHUMAN_CLIENT_ID/SECRET` | Connector readiness gate. | `app.py:10224` |
| `OPEN_WEARABLES_MANAGED_PROVIDER_IDS` | `whoop` | Providers the app can manage into sidecar env. | `app.py:10234` |
| Loopback hosts | `127.0.0.1`, `localhost`, `0.0.0.0`, `::1` | Local/loopback URL validation context. | `app.py:10237`, `open_wearables_adapter.py:15` |
| Error codes | `open_wearables_auth_error`, `open_wearables_config_error`, `open_wearables_sync_error`, `open_wearables_sync_failed` | Stable public errors; raw upstream errors are redacted. | `app.py:11640`, `app.py:11610` |
| Public error keys | `auth`, `config`, `sleep`, `workouts`, `activity_summary`, fallback `sync` | Error names exposed in metadata-only sync. | `app.py:11650` |

Wearable fact store:

| Value | Meaning | Provenance |
| --- | --- | --- |
| Forbidden fields | `authorization`, `access_token`, `refresh_token`, `token`, `password`, `secret`, `raw`, `payload`, `samples`, `records`, `user_id`, and any `*_token` | Rejected from normalized fact payloads. | `wearable_fact_store.py:15` |
| `confidence` default | `unknown` | Default confidence on wearable daily fact. | `wearable_fact_store.py:26` |
| `freshness` default | `unknown` | Default freshness on wearable daily fact. | `wearable_fact_store.py:27` |
| `profile_key` default | `1` | Single-owner profile partition used in wearable fact tables. | `wearable_fact_store.py:84` |

## Apple Health And HealthKit

| Value | Meaning | Provenance |
| --- | --- | --- |
| `HEALTH_DIR` | `~/Documents/Health` | Legacy Apple Health export directory. | `apple_health_parser.py:25`, `health_ingest.py:20` |
| `FITNESS_DASHBOARD_PUBLIC_BASE_URL` | Public URL source for Apple setup URL and origin checks. | `apple_health_parser.py:26`, `runtime_config.py:12`, `auth.py:385` |
| `APPLE_HEALTH_WEBHOOK_URL` | Optional explicit Apple sync endpoint override. | `apple_health_parser.py:27` |
| `APPLE_HEALTH_SYNC_DB` | Optional sync DB path override; default `apple_health_sync.db`. | `apple_health_parser.py:28` |
| `HEALTH_SYNC_TOKEN` | Required shared token for `/api/apple-health/sync`; auto-provisioned to `.health-sync-token` if absent. | `apple_health_parser.py:608` |
| HAE metric map | `step_count/steps -> steps`, `active_energy/active_energy_burned -> active_energy`, `resting_heart_rate/heart_rate -> heart_rate`, `heart_rate_variability -> hrv`, `sleep_analysis/sleep -> sleep` | `apple_health_parser.py:634` |
| Sync record types | `workouts`, `heart_rate`, `hrv`, `sleep`, `steps`, `active_energy` | `apple_health_parser.py:815` |
| Sync source | `health_auto_export` | Stored source for webhook rows/events. | `apple_health_parser.py:817` |
| Activity map examples | `Walking`, `Running`, `Cycling`, `Hiking`, `Traditional Strength Training`, `Swimming`, `Yoga`, `Elliptical`, `Stair Climbing`, `Core Training`, `Functional Strength Training` | `apple_health_parser.py:30`, `health_ingest.py:45` |

## AI Coach, LM Studio, Vision

| Value | Meaning | Provenance |
| --- | --- | --- |
| `LM_STUDIO_PRIMARY_URL` | Default `http://127.0.0.1:1234`, falls back from `LM_STUDIO_URL`. | `lm_studio_adapter.py:26` |
| `LM_STUDIO_PRIMARY_MODEL` | Default `qwen/qwen3-30b-a3b-2507`, falls back from `LM_STUDIO_MODEL`. | `lm_studio_adapter.py:30` |
| `LM_STUDIO_FALLBACK_URL` | Default `http://127.0.0.1:1234`. | `lm_studio_adapter.py:34` |
| `LM_STUDIO_FALLBACK_MODEL` | Default `qwen/qwen3.6-35b-a3b`. | `lm_studio_adapter.py:35` |
| `LM_STUDIO_TIMEOUT_SEC` | `8` seconds adjust timeout. | `lm_studio_adapter.py:36` |
| `LM_STUDIO_ANALYZE_TIMEOUT_SEC` | `25` seconds analyze timeout. | `lm_studio_adapter.py:37` |
| `LM_STUDIO_SWAP_RESOLVE_TIMEOUT_SEC` | `2.5` seconds for free-text swap resolution. | `lm_studio_adapter.py:38` |
| `LM_STUDIO_PREFLIGHT_TIMEOUT_SEC` | `1.5` seconds for model preflight. | `lm_studio_adapter.py:39` |
| `MEAL_TEXT_LOCK_ACQUIRE_SEC` | `2.0` seconds to acquire text inference lock. | `lm_studio_adapter.py:40` |
| `ANALYZE_PROMPT_VERSION` | `notes-v2` | Workout analysis prompt version. | `lm_studio_adapter.py:651` |
| Vision supported providers | `claude`, `lm_studio`, `ollama` | Provider dispatch set. | `vision_estimator.py:13` |
| Vision default provider | `lm_studio` | Default local vision route. | `vision_estimator.py:12` |
| Served vision model | `qwen3-vl-30b-a3b-instruct@q4_k_xl` | Default local LM Studio vision model. | `local_vision_adapter.py:39` |
| `VISION_LOCAL_TIMEOUT_SEC` | `25` seconds default. | `local_vision_adapter.py:56` |
| `VISION_LM_STUDIO_LOAD_RETRY_LIMIT` | `2` retries. | `local_vision_adapter.py:62` |
| `VISION_LM_STUDIO_LOAD_RETRY_BACKOFF_SEC` | `1.0` second base backoff. | `local_vision_adapter.py:63` |
| `VISION_LM_STUDIO_WARMUP_TIMEOUT_SEC` | `45` seconds. | `local_vision_adapter.py:64` |
| `VISION_LM_STUDIO_MULTI_IMAGE_MAX_DIMENSION` | `1024` px. | `local_vision_adapter.py:65` |
| `VISION_LM_STUDIO_MULTI_IMAGE_JPEG_QUALITY` | `80`. | `local_vision_adapter.py:69` |
| `DEFAULT_VISION_TEMPERATURE` | `0.1`. | `local_vision_adapter.py:73` |
| `SCHEMA_RETRY_LIMIT` | `2`. | `local_vision_adapter.py:74` |
| Vision required fields | `item_name`, `portion_description`, `meal_type`, `calories`, `protein_g`, `carbs_g`, `fat_g`, `sodium_mg`, `fiber_g`, `confidence`, `ambiguous`, `uncertainty_notes`, `items` | `local_vision_adapter.py:75` |
| Vision item required fields | `item_name`, `quantity`, `brand`, `modifiers`, `portion_hint` | `local_vision_adapter.py:90` |
| `claude-sonnet-4-5` | Default Claude vision model if `CLAUDE_VISION_MODEL` unset. | `claude_vision_adapter.py:14` |

## Auth, Security, Sessions, CSRF

| Value | Meaning | Provenance |
| --- | --- | --- |
| `_RATE_LIMIT_WINDOW_SEC` | `600` seconds. | `auth.py:23` |
| `_RATE_LIMIT_MAX_FAILS` | `10` failed attempts before lockout. | `auth.py:24` |
| `AUTH_DB` | `auth.db` under `DATA_DIR`. | `auth.py:47` |
| CSRF header | `X-Requested-With: XMLHttpRequest` | Required for mutating browser API calls unless same-origin/form token path applies. | `auth.py:55` |
| CSRF form field | `csrf_token` | Form-based CSRF token name. | `auth.py:57` |
| CSRF mutating methods | `POST`, `PUT`, `PATCH`, `DELETE` | Methods checked by CSRF guard. | `auth.py:59` |
| CSRF exempt paths | `/api/apple-health/sync`, `/webhook` | External token/signed webhook paths. | `auth.py:60` |
| Password hash method | `scrypt:32768:8:1` | Current password hash method; legacy SHA-256 hashes are upgraded after successful login. | `auth.py:66` |
| Public prefixes | `/login`, `/register`, `/logout`, `/landing`, `/pricing`, `/manifest.json`, `/sw.js`, `/static/`, `/robots.txt`, `/sitemap.xml`, `/webhook`, `/success`, `/cancel`, `/api/apple-health/sync` | Paths exempt from login guard. | `auth.py:342` |
| `FITNESS_DASHBOARD_SINGLE_USER` | Defaults true; when true, only owner user id can access protected routes. | `auth.py:228` |
| `FITNESS_DASHBOARD_OWNER_USER_ID` | Optional explicit owner id. | `auth.py:238` |
| Session cookie config | `HttpOnly`, `SameSite=Lax`, secure by default unless `SESSION_COOKIE_SECURE=false` | Browser session hardening. | `auth.py:487` |

## Push, PWA, Offline Queue

| Value | Meaning | Provenance |
| --- | --- | --- |
| `CACHE_NAME` | `fitness-dashboard-v20260627-fit249-auto-mobile-invite` | Service-worker version; caches are cleared on activate and not used for app shell. | `static/js/sw.js:3` |
| Push payload default title | `Fitness Dashboard` | Notification title fallback. | `static/js/sw.js:29` |
| Push payload default tag | `fitness-dashboard-reminder` | Notification tag fallback. | `static/js/sw.js:32` |
| `safety_critical` | Always low-stakes/false in current reminder/test flows. | `static/js/sw.js:35`, `app.py:14091` |
| `FITNESS_PUSH_VAPID_PUBLIC_KEY` / `VAPID_PUBLIC_KEY` | Public key source for push subscription. | `app.py:13927` |
| `FITNESS_PUSH_VAPID_PRIVATE_KEY` / `VAPID_PRIVATE_KEY` | Private key source for server push delivery. | `app.py:13935` |
| `FITNESS_PUSH_VAPID_SUBJECT` / `VAPID_SUBJECT` | VAPID subject; defaults to public base URL if HTTPS, else `mailto:admin@example.com`. | `app.py:13943` |
| Push UI states | `unsupported`, `needs_install`, `denied`, `prompt`, `granted_active`, `granted_inactive`, `revoked` | Browser push setup states. A missing VAPID key surfaces as an error message, not a named state. | `static/js/app.js:6066`, `static/js/app.js:6100` |
| `DASHBOARD_FETCH_TIMEOUT_MS` | `30000` ms | Core dashboard fetch timeout. | `static/js/app.js:133` |
| AI status interval | `60000` ms | Refresh AI status every minute. | `static/js/app.js:12248` |
| Toast duration | `2400` ms default removal. | `static/js/app.js:205` |
| Undo toast duration | `10000` ms default; meal undo uses `30000` ms. | `static/js/app.js:208`, `static/js/app.js:9581` |
| Active workout draft key | `fit168:active-workout-draft:v1`; version `1` | Browser-local active workout draft. | `static/js/app.js:7040` |
| Workout sync queue key | `fit51:sync-queue:v1` | Browser-local offline workout queue. | `static/js/app.js:7821` |
| Workout queue retryable statuses | `pending`, `auth_required` | Workout queue statuses that retry. | `static/js/app.js:7822` |
| Meal queue DB | `fitMealIntakeQueueDB`, version `1`, stores `queued_meals`, `meal_photos` | IndexedDB offline meal queue. | `static/js/app.js:7827` |
| Meal queue retryable statuses | `pending`, `auth_required` | Meal queue statuses that retry. | `static/js/app.js:7831` |
| Queue terminal/problem statuses | `conflicted`, `rejected`, `eviction_failed`, `auth_required` | Queue rows needing user attention or discard. | `static/js/app.js:8443` |
| Meal draft key | `fit60_meal_draft` | Browser-local meal composer text/photo draft metadata. | `static/js/app.js:9580` |
| Meal queue auth-scope key | `fit145:meal-queue-auth-scope:v1` | Browser-local auth-scope guard for queued meal sync. | `static/js/app.js:7832` |

## Data Stores, Backup, And Runtime Paths

| Constant / value | Meaning | Provenance |
| --- | --- | --- |
| `DATA_DIR` | Env override or app directory. | `runtime_config.py:11` |
| `data_workouts.json`, `data_soreness.json`, `data_settings.json`, `data_cardio.json`, `data_recovery.json`, `data_baselines.json`, `data_body.json`, `data_sleep.json`, `data_nutrition.json` | JSON-backed local stores. | `app.py:200` |
| `oura_daily.sqlite3`, `whoop.sqlite3`, `wearable_facts.sqlite3`, `fitness_data.db`, `apple_health_sync.db`, `ai_coach_cache.sqlite3` | SQLite local stores/caches. | `app.py:209`, `data_store.py:24`, `apple_health_parser.py:78`, `app.py:8978` |
| Backup version | `1.1` | Exported backup schema version. | `app.py:15161` |
| Backup filename | `fitness_backup_YYYY-MM-DD.json` | Export filename pattern. | `app.py:15185` |
| Forbidden WHOOP backup fields | `access_token`, `refresh_token`, `token_ref`, `material_ref`, `raw`, `raw_json`, `payload`, `provider_payload`, `client_secret` | Import rejection fields. | `app.py:15216` |
| `DEBUG_TIMING` | `1` enables timing logs for selected endpoints. | `app.py:11579` |
| Debug timing endpoints | `/api/dashboard`, `/api/oura/status`, `/api/recommendation/smart`, `/api/oura/sleep-summary` | Routes with optional timing logs. | `app.py:11570` |
| Default port | `5050` | Runtime port when `PORT` unset. | `app.py:16040` |
| Default host | `127.0.0.1` | Runtime host when `HOST` unset. | `app.py:16059` |
| `FLASK_DEBUG` | `1` enables debug mode. | `app.py:16055` |

This appendix is an inventory reference and intentionally has no `### IC-n` issue-candidate section; issue candidates live in the owning PRD files.
