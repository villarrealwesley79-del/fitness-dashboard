# Zero-Context Meal Intake Contract

Linear: FIT-57

`POST /api/meal-intake` is the canonical backend entrypoint for quick meal logging from either text or a photo. The caller does not need to choose a form mode first: a plain description, a photo, or a photo with extra text all flow into the same food estimate contract.

## Request

Content type: `multipart/form-data` or `application/x-www-form-urlencoded`.

Fields:

- `client_id` string, required, max 128 chars. This is the idempotency key for mobile retries.
- `text` string, optional, max 500 chars. Free-form meal description or extra context for a photo.
- `image` file, optional. Must be `image/*`, non-empty, max 6 MB. Image bytes are validated and discarded after estimation; raw image data is never echoed.
- `local_timestamp` string, optional, max 64 chars. Browser-local timestamp used for `logged_at`, `source_timestamp`, and the persisted meal date.

At least one of `text` or `image` must be present.

## Estimate Schema

All text and image paths return one normalized `estimate` object compatible with FIT-5:

```json
{
  "item_name": "Protein shake",
  "portion_description": null,
  "meal_type": "snack",
  "calories": 210,
  "protein_g": 30,
  "carbs_g": 14,
  "fat_g": 4,
  "sodium_mg": 180,
  "fiber_g": 2,
  "confidence": 0.82,
  "ambiguous": false,
  "uncertainty_notes": [],
  "source": "ai_text_estimate"
}
```

Allowed `meal_type` values are `breakfast`, `lunch`, `dinner`, and `snack`. Confidence is `0.0` through `1.0`. Unknown model fields, prompt echoes, raw traces, and raw image references are not part of the public schema.

## Response

Success response:

```json
{
  "status": "pending_review",
  "estimate": {},
  "food_log": {},
  "policy": {
    "confidence_band": "high",
    "reasons": []
  }
}
```

Auto-log is currently disabled for fresh meal submissions. Text, photo, and
barcode capture always return `pending_review`, including high-confidence,
unambiguous estimates. The policy result is advisory for these capture routes:
it still supplies confidence bands and reason codes, but it does not bypass
review. Auto-log is neither feature-flagged nor available on the fresh-capture
routes today.

`logged` remains a legacy response for an idempotent replay after that meal was
already confirmed. It is not a possible first response for a fresh submission.
`pending_review` means the estimate was returned for user review and must not
affect coaching totals until explicitly accepted.

When `status` is `pending_review`, `food_log` is persisted with `correction_state: "pending_review"` and remains excluded from nutrition totals. The client can hydrate unresolved estimates with `GET /api/meal-intake/pending`, accept later with `POST /api/meal-intake/{client_id}/accept`, or discard with `DELETE /api/meal-intake/{client_id}`. Pending review rows older than 7 days are removed during pending-list hydration.

Accept request:

```json
{
  "estimate": {
    "item_name": "Popcorn",
    "portion_description": "shared portion",
    "meal_type": "snack",
    "calories": 300,
    "protein_g": 5,
    "carbs_g": 36,
    "fat_g": 18,
    "sodium_mg": 520,
    "fiber_g": 6,
    "confidence": 0.45,
    "ambiguous": true,
    "uncertainty_notes": ["Portion is unclear."],
    "source": "ai_text_estimate"
  },
  "text": "shared movie popcorn"
}
```

The accept endpoint persists the supplied estimate through `food_logs` using the URL `client_id`. `estimate.calories` is required, and the rest of the estimate should keep the same schema returned by `POST /api/meal-intake`.

## Barcode Intake

`POST /api/meal-intake/barcode` accepts packaged-food UPC/EAN/GTIN values and returns the same pending-review v2 payload as text and photo capture.

Content type: `application/json`.

Request:

```json
{
  "client_id": "meal-barcode-123",
  "barcode": "012345678905",
  "local_timestamp": "2026-05-24T12:30:00",
  "local_date": "2026-05-24",
  "local_iso": "2026-05-24T12:30:00-05:00",
  "allow_pending": false
}
```

Fields:

- `client_id` string, required, max 128 chars. It is the same idempotency key used by text/photo meal intake.
- `barcode` string, required. Whitespace, dots, underscores, and hyphens are ignored; the normalized value must be 8, 12, 13, or 14 digits.
- `local_timestamp`, `local_date`, and `local_iso` are optional and follow the same length limits as text/photo intake.
- `allow_pending` boolean, optional. When true and no verified provider match is available, the backend creates a low-confidence `barcode_pending_source` review draft instead of returning 404.

Lookup order is local barcode cache, Nutritionix UPC item lookup, USDA FoodData Central branded barcode lookup, then Open Food Facts barcode lookup. Verified provider results are cached per user for future reuse. Pending-source fallbacks are never cached.

Successful responses include the normal pending-review payload plus barcode metadata:

```json
{
  "status": "pending_review",
  "barcode": "012345678905",
  "lookup_source": "nutritionix_barcode",
  "cache_hit": false,
  "pending_source": false,
  "estimate": {}
}
```

Errors:

- `415 invalid_content_type` when the request is not JSON.
- `413 payload_too_large` when `Content-Length` exceeds 18 MB.
- `400 missing_field`, `invalid_field`, or `invalid_barcode` for malformed requests.
- `404 barcode_not_found` when no verified provider result exists and `allow_pending` is false.

## Auto-Log Policy

`meal_log_policy.evaluate_meal_log` defines the theoretical auto-log policy:

- Auto-log requires `confidence >= 0.75`, no ambiguity flag, and plausible nutrition values.
- Confidence from `0.55` to `< 0.75` is pending review with a `medium_confidence` policy reason.
- Confidence below `0.55`, ambiguous input, missing calories, impossible calories/macros, or impossible sodium is pending review.

The text/photo and barcode routes currently override that theoretical status and
force every fresh estimate to `pending_review`. The policy's confidence band and
reasons remain response metadata. Pending review estimates are excluded from
dashboard nutrition totals and future coaching changes until accepted.

## Persistence and Idempotency

Accepted and pending-review meals persist through existing `food_logs` storage via the same path used by `/api/add-nutrition`. The `client_id` key prevents duplicate meals for mobile retries and lets a later accept update the same pending row to `correction_state: "accepted"`.

Legacy `NUTRITION_DATA` JSON remains a compatibility surface, but `food_logs` is the canonical accepted meal log for this endpoint.

## Privacy

Errors and normal responses do not expose:

- Raw image bytes or image references.
- Prompt text beyond the user-provided `text` field already sent by the client.
- Model traces, chain-of-thought, `_meta`, debug payloads, or token material.

Raw food photo retention is governed by FIT-9. The current backend behavior is discard-after-estimation.
