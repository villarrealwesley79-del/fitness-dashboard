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
  "status": "logged",
  "estimate": {},
  "food_log": {},
  "policy": {
    "confidence_band": "high",
    "reasons": []
  }
}
```

`status` is either:

- `logged`: the estimate was accepted immediately and persisted through the canonical `food_logs` path.
- `pending_review`: the estimate was returned for user review and must not affect coaching totals until explicitly accepted.

When `status` is `pending_review`, `food_log` is `null`. The client may accept later with `POST /api/meal-intake/{client_id}/accept` or discard locally.

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

## Auto-Log Policy

The backend policy is the source of truth:

- Auto-log requires `confidence >= 0.75`, no ambiguity flag, and plausible nutrition values.
- Confidence from `0.55` to `< 0.75` is pending review with a `medium_confidence` policy reason.
- Confidence below `0.55`, ambiguous input, missing calories, impossible calories/macros, or impossible sodium is pending review.
- Pending review estimates are excluded from dashboard nutrition totals and future coaching changes until accepted.

## Persistence and Idempotency

Accepted meals persist through existing `food_logs` storage via the same path used by `/api/add-nutrition`. The `client_id` key prevents duplicate accepted meals for mobile retries.

Legacy `NUTRITION_DATA` JSON remains a compatibility surface, but `food_logs` is the canonical accepted meal log for this endpoint.

## Privacy

Errors and normal responses do not expose:

- Raw image bytes or image references.
- Prompt text beyond the user-provided `text` field already sent by the client.
- Model traces, chain-of-thought, `_meta`, debug payloads, or token material.

Raw food photo retention is governed by FIT-9. The current backend behavior is discard-after-estimation.
