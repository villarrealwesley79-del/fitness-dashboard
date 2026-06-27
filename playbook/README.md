# Agent Lesson Playbook

Version: 0.1.0
Owner: repository agents
Default promotion threshold: 3 independent successful runs

This playbook stores candidate lessons that may improve future agent runs. Every
lesson here is untrusted advice, not authority. It can never override system
instructions, user instructions, repo instructions, Linear or GitHub gates,
approval policy, privacy boundaries, or production safety rules.

## Run Protocol

At the start of each run:

1. Read this playbook, `playbook/candidates.md`, and
   `playbook/evidence-ledger.md`.
2. Choose at most one relevant candidate lesson to test.
3. If no candidate is relevant, skip lesson testing for the run.
4. Apply the selected candidate only within the task's existing permissions.
5. Measure the result using the task's own success check.
6. Record the context, action, outcome, and evidence in the ledger.

Never let this playbook authorize production, destructive, financial,
privacy-sensitive, or external actions. If a lesson would require approval,
credentials, real user data, production mutation, or an external side effect
outside the task's existing authorization, stop instead of testing it.

## Lesson Lifecycle

Candidates start in `playbook/candidates.md`.

A candidate can be promoted only when it has either:

- three independent successful runs with direct evidence; or
- success across a predefined holdout set documented before testing starts.

One successful attempt is never enough for promotion. Failed, stale, or harmful
lessons must be revised or removed. If evidence is mixed, keep the lesson as a
candidate and make the uncertainty explicit.

## Stop Conditions

Stop lesson testing when:

- no candidate has enough relevant evidence;
- another test would exceed the task budget;
- approval is required;
- the candidate would expand the task scope; or
- the candidate would conflict with repo, Linear, GitHub, privacy, production,
  financial, destructive-action, or external-action boundaries.

## Closeout Format

Every run that touches this playbook should close with:

- playbook diff;
- evidence ledger entry or statement that no lesson was tested;
- removed lessons;
- unresolved candidates; and
- new version.

## Versioning

Use semantic versions for the playbook package:

- Patch: evidence-only updates, typo fixes, or clarifications that do not change
  the protocol.
- Minor: new candidates, revised promotion rules, or workflow additions.
- Major: incompatible changes to the run protocol or safety boundaries.

Record version changes in `playbook/changelog.md`.
