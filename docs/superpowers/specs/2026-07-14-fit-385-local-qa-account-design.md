# FIT-385 Local QA Account Design

## Role

Add one opt-in QA login that agents can share while testing Wesley's persistent owner instance. The QA account authenticates separately from Wesley's owner account, but it intentionally reads and writes the owner's existing dashboard data.

## Scope

FIT-385 adds a single environment-provisioned QA identity to the existing single-owner authentication model. It does not remove login, enable public registration, create one account per agent, introduce multi-tenancy, change the owner account, or reuse FIT-383's disposable preview credentials.

The owner remains the lowest `users.id` or the user selected by `FITNESS_DASHBOARD_OWNER_USER_ID`. The QA account is allowed through owner-gated routes without being reported as, or promoted to, the owner.

## Configuration

The feature is disabled unless all of the following are intentionally configured:

```text
FITNESS_DASHBOARD_LOCAL_QA_ENABLED=true
FITNESS_DASHBOARD_LOCAL_QA_USERNAME="${LOCAL_QA_USERNAME}"
FITNESS_DASHBOARD_LOCAL_QA_PASSWORD="${LOCAL_QA_PASSWORD}"
```

The username and password are supplied at runtime and are never committed, logged, added to examples as literals, or returned by an endpoint. The password must contain at least eight characters, matching normal registration. Documentation must label the feature "local testing only - never production" and instruct operators not to set the flag on public or shared deployments.

The existing repository has no production-environment discriminator. The explicit local-only flag is therefore the code-level opt-in boundary: ordinary local and production boots leave it unset and cannot create the account.

## Design

### Designated-account persistence

Use a singleton `local_qa_account` table in the existing authentication database. It stores only the designated QA user's numeric `user_id`; credentials remain in the existing `users` table and are password-hashed through the existing password helper. The table is created only on an enabled boot. A never-enabled default boot does not change the existing auth schema.

This is preferred over an `is_local_qa` column because the special role does not become part of every user row. It is preferred over identifying the account by username because the exact account can still be removed safely after its configured username changes or disappears from the environment.

The singleton mapping is the only authority for QA designation. A matching username without that mapping is an ordinary account and must remain forbidden by the owner-only guard.

### Boot reconciliation

Authentication initialization reconciles configuration and database state in one transaction after the auth schema exists.

When disabled:

1. If the singleton table does not exist, do nothing.
2. Read the singleton mapping, if present.
3. Refuse cleanup if the mapped ID equals the resolved owner ID; preserve both rows and fail closed instead of risking owner deletion.
4. Delete the mapping and only its designated `users` row, then drop the now-unused singleton table.
5. Leave the owner and every unrelated account unchanged.

When enabled:

1. Require non-empty username and password values and a password of at least eight characters.
2. Require a valid, existing owner before creating the QA account so the QA row can never become the lowest-ID owner.
3. Reject a username already owned by the owner or any non-designated account.
4. Reject a pre-existing singleton mapping to the owner ID as invalid state.
5. Create the designated QA user when absent, or update that same designated user's username/password when credentials rotate.
6. Record its user ID in the singleton mapping.

Invalid or incomplete enabled configuration raises a startup error without committing partial changes. Credential values never appear in the error message. A missing mapped user is repaired by clearing the stale mapping and recreating the designated account within the same transaction.

### Access and owner identity

Keep `_owner_user_id()` and `_is_owner_user_id()` unchanged. Add a separate designated-QA predicate and a route-access predicate:

```text
owner_route_access(user_id) = is_owner(user_id) OR is_designated_local_qa(user_id)
```

The global login guard uses the route-access predicate. An enabled designated QA user can therefore reach authenticated pages and APIs, while arbitrary authenticated non-owner accounts still receive 403. Disabling the feature removes the QA row, so existing QA sessions fail user loading and return to normal unauthenticated behavior on the next request.

### Shared owner data

Authentication identity and data identity remain deliberately distinct:

```text
data_user_id(owner) = owner_id
data_user_id(designated_qa) = owner_id
data_user_id(any_other_user) = that user's own id
```

`app._current_data_user_id()` will use the auth-layer resolver. Its existing callers cover the user-scoped food, workout-plan, adaptation, settings, Open Wearables, WHOOP, push, recommendation, backup, and related paths. Global JSON and `DATA_DIR` resources are already shared. Direct authentication checks continue using the actual logged-in identity.

This mapping makes the QA account exercise Wesley's real owner dataset without rewriting records to a second user ID. It does not weaken cross-user checks for arbitrary accounts and does not change the owner ID.

## Invariants

| State | Authentication identity | Owner identity | Data identity | Gated-route result |
|---|---|---|---|---|
| Feature disabled, owner | Owner | Owner | Owner | Allowed |
| Feature disabled, former QA session | Missing user | Owner | Not resolved | Login required |
| Feature disabled, arbitrary user | Arbitrary user | Owner | Arbitrary user | 403 |
| Feature enabled, owner | Owner | Owner | Owner | Allowed |
| Feature enabled, designated QA | QA user | Owner | Owner | Allowed |
| Feature enabled, username match without designation | Arbitrary user | Owner | Arbitrary user | 403 |
| Invalid configured owner | Any authenticated user | Invalid/locked | No privileged mapping | 403 or startup failure before QA creation |

## Error Handling

- Missing enabled credentials, a short password, a missing owner, an invalid configured owner ID, or a username collision fails startup with a credential-free explanation.
- Database reconciliation is transactional. A failed create, update, delete, or mapping write rolls back the entire reconciliation.
- Disabling removes only the mapped QA account. It never deletes by the current environment username, and it refuses a mapping to the owner ID, so configuration or mapping drift cannot delete the owner.
- Repeated enabled boots are idempotent. They reuse the mapped user and update credentials only when necessary.
- Repeated disabled boots are no-ops after the mapped account is gone.
- The QA password is processed only by the existing slow password-hash and verification path.

## Testing

Tests use isolated temporary auth/data databases and environment variables. No real owner database or credential is loaded.

Focused tests must prove:

1. Disabled/default boot creates no QA account and preserves today's single-user registration denial.
2. Enabled boot provisions the designated account, hashes its password, allows login, and returns 200 from a gated browser route and API route.
3. Owner resolution remains unchanged and the owner's login still succeeds while QA is enabled.
4. The designated QA account is not considered the owner by `_is_owner_user_id()`.
5. An arbitrary non-owner and a username match without the singleton designation still receive 403.
6. QA data resolution returns the owner ID, and a representative owner-scoped record is visible and mutable through the QA session.
7. Disabling after enablement deletes the designated QA account, invalidates its session on the next request, and leaves the owner account/data intact.
8. Credential rotation updates only the designated QA account and keeps its designation stable.
9. Missing credentials, short passwords, missing/invalid owners, and username collisions fail without partial rows or leaked credential text.
10. Repeated enabled and disabled boots are idempotent.

After focused RED/GREEN proof, run the configured repository check:

```text
/Users/admin/fitness-dashboard/venv/bin/python3 -m pytest -q
```

## Documentation

Update the active authentication/operator documentation with:

- the three environment variable names;
- the fact that one account is shared by testing agents;
- the local-testing-only and never-production boundary;
- the eight-character minimum without publishing an actual password;
- restart-to-enable, restart-to-rotate, and restart-to-disable behavior;
- the fact that disabling removes the QA account;
- the fact that QA actions operate on the owner's real shared data;
- the fact that neither owner login nor owner credentials are removed.

## Non-Goals

- Automatic or passwordless owner login; FIT-386 owns that separate trusted-network feature.
- Multiple QA accounts or per-agent identities.
- Public registration, multi-tenant access, or per-user data partitioning.
- Production deployment or a production-mode framework.
- Changes to Wesley's username, password, owner ID, or login requirement.
- Reuse of FIT-383 preview credentials or preview database seeding.
- Changes to the deterministic coaching core.

## Acceptance Mapping

- Opt-in environment provisioning: Configuration and Boot reconciliation.
- QA access without disabling single-user mode: Access and owner identity.
- Shared owner data: Shared owner data and invariant matrix.
- Disabled behavior unchanged: Boot reconciliation and disabled tests.
- Owner remains owner: Access predicates and owner-resolution tests.
- Local-only documentation: Configuration and Documentation.
- Enabled/disabled/owner/arbitrary-user coverage: Testing.
