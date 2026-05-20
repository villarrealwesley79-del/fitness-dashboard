# FIT-30 Synthetic History Cleanup

Linear: FIT-30

## Provenance

FIT-30 came from an old `docs/SESSION_AUDIT.md:104` note that questioned five
synthetic pre-session history rows dated April 12-14. That audit file and the
runtime JSON history files are not tracked in the sanitized GitHub repository.

Current `origin/main` contains no tracked `data_workouts.json`,
`data_cardio.json`, or `data_recovery.json` fixture rows to delete. The repo
change therefore ships the cleanup procedure and proof guard instead of
committing runtime data.

## Runtime Cleanup Procedure

Inspect local runtime history candidates:

```bash
python3 scripts/remove_synthetic_history_rows.py --data-dir .
```

The script lists rows dated `2026-04-12`, `2026-04-13`, or `2026-04-14` from:

- `data_workouts.json`
- `data_cardio.json`
- `data_recovery.json`

Delete only the five manually confirmed synthetic rows by explicit key:

```bash
python3 scripts/remove_synthetic_history_rows.py \
  --data-dir . \
  --remove workout:<id-or-index-key> \
  --remove workout:<id-or-index-key> \
  --remove cardio:<id-or-index-key> \
  --expect-count 5 \
  --apply
```

Use the exact keys printed by the dry-run inspection. The script does not delete
by date alone because real history could exist on those same days.

## Verification

After cleanup, restart the Flask app or reload the JSON data process, then check:

```bash
PYTHONPATH=. venv/bin/pytest tests/test_history_detail_and_analyze.py tests/test_synthetic_history_cleanup.py
```

History counts and aggregates recompute from the remaining JSON rows at request
time, so removing rows from the runtime files is enough to update `/api/history`
and `/api/history-all` after reload.
