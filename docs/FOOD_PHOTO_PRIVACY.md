# Food Photo Privacy and Retention

Linear: FIT-9

## Decision

Food photos are discarded after extraction by default. The app keeps only the
accepted nutrition values and sanitized estimate metadata needed for review,
correction history, coaching context, and backup/restore.

The product does not store raw food photos long-term and does not include raw
photos in normal API responses or backup exports. A future retention feature
must be a separate opt-in issue with explicit storage, deletion, and export
rules.

## Backend Contract

- Image bytes may be accepted only as request input for the meal-intake flow.
- Image bytes are not echoed in API responses.
- Image filenames, data URLs, model traces, prompts, chain-of-thought, and raw
  model responses are not persisted in `food_logs`.
- `food_logs.original_estimate_json` stores only the safe estimate fields
  allowed by `data_store.sanitize_food_estimate`.
- Backup export includes accepted food log rows and sanitized original
  estimates only.
- Import replay must use the same `add_food_log` path so imported estimates are
  sanitized again.

## Current API Signal

`/api/meal-intake` returns a `photo_retention` object:

```json
{
  "policy": "discard_after_extraction",
  "raw_photo_retained": false,
  "raw_model_trace_retained": false,
  "backup_includes_raw_photo": false
}
```

The UI should show this in the food review surface without changing the storage
policy.

## What Is Intentionally Not Stored

- Raw uploaded photo bytes.
- Base64 image data.
- Local image paths or original filenames.
- Raw model prompts, completions, traces, or chain-of-thought.
- Full model debug payloads.

## Temporary Offline Queue Storage

FIT-145 adds one narrow client-side exception for offline meal logging:
when the browser is offline, queued meal photos are stored as `Blob`s in
IndexedDB database `fitMealIntakeQueueDB`, store `meal_photos`, only until the
queued meal can be sent to `/api/meal-intake`.

The offline queue never stores raw photo bytes, base64 image data, local image
paths, object URLs, or original filenames in localStorage. Filenames are
stripped before queueing; replay uses synthetic names such as `meal-1.jpg`.

Queued photo blobs must be deleted immediately after the server accepts the
meal response, before the browser treats the queued entry as synced. User
discard also deletes both the queue entry and its photo blobs. If IndexedDB is
unavailable or quota prevents writing the queue, offline photo submissions are
not silently dropped; the composer keeps the draft visible and shows an error.

## Future Opt-In Retention Requirements

If photo retention is ever added, it must define:

- User-visible opt-in and opt-out behavior.
- Storage location and encryption assumptions.
- Deletion behavior for a single meal and account-wide export/delete.
- Backup/export inclusion rules.
- Maximum retention window for temporary processing files.
