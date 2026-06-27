# Evidence Ledger

Every entry treats the tested lesson as untrusted advice. Evidence must come from
the task's own success check, not from intent or confidence.

## 2026-06-27 - FIT-251 - Initialize Playbook

- Playbook version: 0.1.0
- Candidate tested: none
- Context: `playbook/` did not exist on `origin/main`; the task was to create a
  durable, versioned lesson playbook and not to promote any lesson from one run.
- Action: created the playbook protocol, candidate registry, evidence ledger,
  and changelog.
- Outcome: initialized the playbook with no active, promoted, or removed
  lessons.
- Evidence: `rg --files | rg '(^|/)playbook/'` initially returned no playbook
  files; the new files are tracked in the FIT-251 branch diff.
- Next decision: future runs may add one candidate at a time, but no candidate
  can be promoted until it meets the documented threshold.
