# Apple Health Helper SLA

FIT-41 keeps native HealthKit work behind a measured bridge-freshness failure. The product remains a Flask PWA using Health Auto Export or Shortcut-style webhook sync unless this SLA fails.

## Trigger

Open a native HealthKit helper implementation issue only when all of these are true:

- Apple Health freshness is stale more than three times in a rolling 30-day window.
- The phone was in normal use during those stale windows.
- HAE or Shortcut instrumentation shows attempted sync failures, rejected uploads, or missing accepted attempts.
- Existing bridge fixes have been tried or explicitly rejected.
- The owner approves native work after seeing the stale-window evidence.

One stale day is not enough. Missing instrumentation is not enough. Native work is not a fallback for unclear data.

## Native Helper Scope

If the trigger is met, the helper is limited to HealthKit export and delivery:

- Read workout, step, energy, and heart-rate data already used by the dashboard.
- Request only the HealthKit permissions required for those data types.
- Deliver records to the existing token-gated backend contract.
- Preserve backend ownership of recommendation, history, freshness, and UI behavior.

Body-mass ingestion is not part of the current bridge contract. It remains out of scope for a native helper unless a separate change adds end-to-end mapping, persistence, status/UI evidence, and tests.

Out of scope: a full iOS app rewrite, native food capture, native recommendation UI, App Store productization, or any replacement of the Flask PWA.

## Bridge-First Comparison

Before native implementation, compare the helper against bridge hardening:

- HAE or Shortcut retry visibility in Settings.
- Last attempted sync, last accepted sync, event counts, and rejection reasons.
- Better stale warnings and setup repair instructions.
- Launchd or local watchdog checks for backend reachability.

Choose the native helper only if those bridge improvements cannot keep Apple Health fresh under normal use.

## Cost And Test Burden

A native helper adds Swift, HealthKit permissions, background-delivery assumptions, provisioning, device-only testing, privacy strings, and an extra release path. The acceptance bar must include device proof that the helper improves freshness compared with HAE or Shortcuts, not just that HealthKit APIs compile.
