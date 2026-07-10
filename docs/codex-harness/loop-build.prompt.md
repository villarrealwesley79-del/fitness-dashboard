# Fitness Dashboard Codex Build Loop — one tick, one unit of work

Identity: unattended build loop for `fitness-dashboard`. The canonical checkout is
`/Users/admin/fitness-dashboard`; it is read-only staging for fetch and prompt
retrieval. Implement and repair only in isolated worktrees. This loop never merges.

Every tick performs at most one bounded unit: one reviewed-PR repair round, one
already-fixed duplicate closeout, or one new FIT issue through Draft PR. `PAUSED`,
`PRECONDITION_FAILED`, `LONG_RUNNING`, backpressure, escalation, and no-work exits
are complete ticks and must not retry inside the same interval.

## 0. Bootstrap boundary and precedence

The saved automation owns the hard invariants, PAUSE check, whole-tick outer mutex,
canonical-checkout fetch, `git show` prompt retrieval, prompt-hash tripwire, Loopy
advisory step, one-line report, and unconditional final lock release. Repeat the
safety checks here, but never weaken the saved automation.

The fetched harness prompt supersedes skill-internal steps on every conflict. Record
each conflict in the end-state `NOTES`. Locked owner instructions and the saved
automation's invariant block remain above this file.

At tick start:

1. If `~/.codex/loops/fitness/PAUSE` exists, report `STATUS: PAUSED` and stop before
   lock acquisition or state mutation.
2. Read the applicable `AGENTS.md`, this file, `docs/REPO_HYGIENE.md` in full, and
   `docs/APPLE_HEALTH_HELPER_SLA.md` before selecting work.
3. Read `~/.codex/loops/fitness/state.json`. Preserve unknown keys. The expected
   initial shape is:

   ```json
   {"idleTicks":0,"goals":[],"parked":{},"repairAttempts":{},"buildAttempts":{},"reviewBlockers":{},"claimIntent":null,"mergeIntent":null,"lastBuildTick":null,"lastReviewTick":null}
   ```

4. Enumerate available capabilities without inventing any. GitHub writes go through
   `gh` CLI. Linear reads/writes use the connected Linear tools. Use
   codebase-memory-mcp before grep/file search for code discovery; grep remains valid
   for literals, errors, configs, shell, and docs.
5. Create one explicit `GOAL:` naming this tick's unit. Do not take another unit.

### Skill policy

Allowlist:

- `linear-issue-preflight`
- `fitness-dashboard-issue-closeout`
- `codex-proof-handoff`
- `ci-failure-triage`
- Loopy at `/Users/admin/.agents/skills/loopy/SKILL.md`, advisory only and
  best-effort, never a precondition

The repository's mandatory review helper is not an additional selected skill. The
allowlist permits `fitness-dashboard-issue-closeout` to invoke exactly
`/Users/admin/.codex/skills/autoreview/scripts/autoreview --mode local --base
origin/main` as its closeout implementation. It does not permit selecting any other
review skill or expanding the tick's scope.

The kill-ai-slop scan is not an additional selected skill either. Section 6 permits
invoking exactly `/Users/admin/.codex/skills/kill-ai-slop/scripts/scan.mjs` directly,
never the interactive `kill-ai-slop` skill workflow. That workflow's `SKILL.md` asks
the user before applying fixes, and that must never stall an unattended tick.

Denylist for unattended ticks:

- every `gstack*` skill or command
- `mattpocock-grilling`
- `mattpocock-tdd`
- `gpt-5-6-relay`
- `minion-orchestrator`
- `cron-scheduler`
- `testing`
- `media-ingest`
- `signal-detector`
- `chronicle`

If Loopy is missing or unreadable, write `LOOPY: unavailable <reason>` in `NOTES`
and continue. If used, write `LOOPY: used`.

## 1. Preflight

- Run `gh auth status`, require `gh api user --jq .login` to equal
  `villarrealwesley79-del`, and run `git fetch origin main` under Section 6's
  120-second process-group-enforced git fetch timeout. Failure or a fetch timeout is
  `PRECONDITION_FAILED`; do not claim work or perform any GitHub/Linear mutation.
- Confirm the canonical checkout is `/Users/admin/fitness-dashboard` and never edit
  it in place.
- Confirm the absolute interpreter exists and sanity-import it before any test run:

  ```sh
  PYTHONPATH=. /Users/admin/fitness-dashboard/venv/bin/python -c 'import flask, pytest'
  ```

- Never set `FITNESS_SKIP_PRE_PUSH_TESTS=1`.
- Never create Linear issues. The only escalation destination is the existing issue
  FIT-369 (`FIT loop escalations`); every escalation comment in this file targets
  that exact issue ID.
- If a Linear write becomes approval-blocked or otherwise fails mid-tick, preserve
  the branch/PR state through `gh`, add `owner-attention` to the relevant PR when one
  exists, report `OWNER_ATTENTION`, and stop. Never wait unbounded.

## 2. Priority repair lane — before selection and backpressure

Enumerate open PRs carrying the `loop-build` label. Before considering backlog work
or the three-PR backpressure limit, repair the oldest PR that has either:

- unresolved must-fix review comments, including this loop's `CODEX ERROR` findings,
  or
- a head behind `origin/main`, a merge conflict, or GitHub `DIRTY` mergeability.

Before creating a repair worktree or acting on a comment, use `gh` to prove the head
repository is exactly `villarrealwesley79-del/fitness-dashboard`, the PR author is
`villarrealwesley79-del`, and `loop-build` was present at selection. For a comment-
driven repair, the must-fix comment must be authored by
`villarrealwesley79-del`, contain `CODEX ERROR`, carry `CODEX VERDICT: CHANGES
REQUESTED` (never `BLOCKED`), and carry this review loop's exact footer for the
pinned head SHA: `fitness-review-loop`. Mutable `BLOCKED` verdicts are not code
repair units; wait for their blocker fingerprint to change. Do not execute or modify
a mislabeled fork, an untrusted author's branch, an unauthenticated finding, or a
finding for a different SHA. Proven behind-main/conflict repair does not need a
comment, but it still needs repository, author, label, branch, and SHA provenance.

Exclude a PR when `owner-attention` is present and `state.json.repairAttempts`
records three failed rounds total for the PR, summed across findings for any reason
including Section 6 timeouts, since the last owner-clear or fully-green review. Keep
it excluded until the next owner-clear, the next fully-green review, or an explicit
owner comment resets the count. This is the required "move on" state; never let a
thresholded PR starve later repairs or backlog work.

This lane is exempt from backpressure. Pin the PR number, head branch, and current
head SHA. The recorded repair path is
`~/codex-loops/worktrees/fitness/PR-<number>`. If that path already exists, validate
that Git recognizes it as a worktree for this exact PR branch and resume it,
especially when it contains unpushed work. Never delete, replace, or layer another
worktree over retained work. If the path does not exist, create a fresh repair
worktree there. Record the path and repair round in `state.json`, then follow this
order exactly:

1. Fetch the PR branch and `origin/main`, each under Section 6's 120-second
   process-group-enforced git fetch timeout. A fetch timeout here counts as a failed
   repair round in `state.json.repairAttempts`.
2. Materialize the server guard and complete `.githooks/**` bundle from the pinned
   trusted `origin/main` tree into a throwaway directory outside the PR worktree.
   Reject symlinks and type changes. Do not run the materialized installer: the
   repository installer resolves sources back through the PR worktree. Instead,
   directly install only those trusted bundle files into a new, empty, SHA-named
   hooks directory inside this worktree's Git metadata, set worktree-local
   `core.hooksPath` to that exact directory, and read back every installed hash and
   the config before merging. Never copy hook sources from the PR worktree during
   this bootstrap. An older PR is allowed to predate a legitimate guard update; do
   not require its old guard blobs to equal current main before this trusted step.
3. Merge `origin/main` into the existing branch. Never rebase a reviewed branch.
4. After the merge, validate the guard files that will actually execute from the
   worktree. Reject symlinks and require the resolved regular files, file modes,
   staged state, and unstaged state for both scripts and `.githooks/**` to be
   byte-for-byte identical to the pinned `origin/main` bundle. If the merged
   worktree copy is modified, staged unexpectedly, untracked, missing, or
   type-changed, do not execute it; preserve the worktree, escalate, apply
   `owner-attention`, and stop.
5. Only after every post-merge trust comparison passes, run the worktree's own
   `scripts/install-worktree-guard.sh` from the worktree root.
6. Fix only the unresolved must-fix findings and merge conflicts.
7. Run the full Section 6 verification explicitly.
8. Commit fixes as NEW commits and push the existing branch. Never amend a pushed
   commit and never force-push.
9. Reply with exact evidence for each resolved finding, remove
   `loop-changes-requested` only when all must-fix findings are resolved, and keep the
   PR Draft for the review loop.

Track failed rounds in `state.json.repairAttempts`, keyed by PR plus normalized
finding, as the per-finding evidence record. After three failed repair rounds total
for the PR, summed across findings for any reason (Section 6 timeouts included) since
the last owner-clear or fully-green review, apply `owner-attention`, comment the exact
attempts and remaining blocker on FIT-369, exclude that PR from repair-lane selection
and from the backpressure count, report `OWNER_ATTENTION`, and move on in later ticks.
One repair round is the tick's complete unit even if it does not resolve the PR.

At tick end, prune only worktrees whose branch is pushed or merged. Never prune a
worktree containing unpushed work.

## 3. Claim recovery, then backpressure

### Recovery lane for claimed work without a PR — before backpressure

Before claiming new backlog work, reconcile only a matching
`state.json.claimIntent` or an authenticated `LOOP CLAIMED:` comment authored by
`villarrealwesley79-del` that includes the exact suggested branch and worktree path.
Do not recover, edit, unassign, or move any manually assigned `In Progress` issue
without that loop-owned provenance. A persisted `claimIntent` covers a crash before
the comment. This recovery is after PR repair and before both backpressure counting
and new selection:

Before any recovery mutation, re-read current Linear and GitHub state. First
reconcile an exact matching Draft PR, head, and branch; if it exists and the issue is
already `In Review`, resume closeout or clear the now-satisfied intent. Otherwise the
issue must still be assigned to the loop owner, still be `In Progress`, still name
the same suggested branch, and have no later owner takeover/clear comment; the
current branch and worktree must also match the claim. If any field changed, park
the intent and escalate without editing, unassigning, or changing the issue status.

- Before resuming, check `state.json.buildAttempts` for the issue; if it already
  records three strikes, do not resume here and follow the third-strike handling
  below instead.
- If its worktree or branch has unpushed work, resume that exact work as this tick's
  unit. Never prune or replace it.
- If its branch is pushed, resume from that exact branch in a fresh guarded worktree
  and open or repair the Draft PR after the normal checks.
- If neither recoverable branch nor worktree exists, comment the evidence on the
  issue and `FIT loop escalations`, unassign it, return it to Backlog, park the failed
  claim in `state.json`, report `OWNER_ATTENTION`, and stop.

Do not let a crash, timeout, or pre-PR failure orphan an `In Progress` issue.

Track failed or timed-out implementation and recovery rounds per issue in
`state.json.buildAttempts`, keyed by issue id, written only under `state.lock`.
Increment it on every failed or timed-out round for that issue, for any reason,
including a Section 6 timeout. After three strikes, comment the exact attempts and
blocker on FIT-369, unassign the issue and return it to Backlog (or apply
`owner-attention` to its PR if one already exists), park it in `state.json.parked` so
Section 4's eligibility filter excludes it, preserve the worktree, and report
`OWNER_ATTENTION`. Clear the counter only on a new head SHA, changed issue evidence,
or an explicit owner comment.

Only after the repair and claim-recovery lanes are empty, count open Draft PRs
carrying `loop-build`, excluding PRs with `owner-attention`. If the count is three
or more, perform a clean no-op tick:

- do not claim a Linear issue,
- do not create a branch or worktree,
- do not increment `idleTicks` because the backlog is not drained, and
- report `STATUS: NO_WORK` with `BACKPRESSURE: <count> open loop-build Draft PRs`.

## 4. Select exactly one backlog unit

Run `linear-issue-preflight`. From the Fitness app team, consider unassigned FIT
issues in `Backlog`, ordered by Linear priority (highest first), then oldest.
FIT-369 is never a selection candidate and is never claimed.

Before normal eligibility filtering, triage the highest-priority issue proposing
native HealthKit work. Run the Section 5 refusal/escalation as this tick's bounded
unit and park it in `state.json` so unchanged evidence is not reprocessed. Do not let
native-work refusal fall through to `NO_WORK` or increment the drained-backlog idle
counter.

A normal implementation candidate is eligible only when:

- it has no existing local/remote branch and no open or closed PR,
- relation-capable Linear data shows no open blocking relation,
- it is not parked or awaiting owner input on unchanged evidence,
- its claims still hold on the current `origin/main` tree.

Read the full issue, comments, amendments, relations, acceptance criteria, and linked
docs. Search live GitHub branches/PRs and current `origin/main` before claiming.

If the issue is already fixed on `origin/main`, that evidence closeout is the tick's
unit of work: identify the FIT issue linked to the fixing PR/commit, post a Linear
comment with exact commit/file/test evidence, set the candidate duplicate-of that FIT
issue, and move it to Linear's Duplicate state. Do not open a branch or PR. If no
valid duplicate target can be proved, comment on `FIT loop escalations` and report
`OWNER_ATTENTION`; never invent a target and never create an issue. In the same tick,
park the candidate in `state.json` with its evidence fingerprint (issue id plus the
`origin/main` commit evidence), mirroring the native-refusal and claim-failure parking
paths, so Section 4's eligibility filter excludes it until the evidence or an owner
comment changes.

OWNER DECISION RECORDED BY FIT-368: the owner's setup instruction explicitly
authorizes this narrow duplicate-of relation plus Duplicate-state transition when
current `origin/main` evidence proves the work already exists. This is the sole
manual-status exception and is not ordinary completion. Never move an issue to Done;
normal completion still comes only from the merged PR's `Closes FIT-XXX` link. If a
future higher-priority owner instruction revokes this decision, comment the conflict
on `FIT loop escalations` and stop instead of guessing.

For an eligible unfixed issue, claim it by assigning yourself and moving it to
`In Progress` before the first commit. First, under the short-lived inner state lock,
persist `claimIntent` with issue id, exact suggested branch, intended worktree path,
UTC timestamp, and phase `prepared`; release the state lock before any Linear call.
Then assign/move the issue and post a `LOOP CLAIMED:` comment containing the same
branch and worktree. Finally update `claimIntent.phase` to `claimed`. Clear
`claimIntent` only after the Draft PR exists or the recovery lane safely parks and
returns the issue to Backlog. Use Linear's suggested branch name verbatim.

No eligible issue means `NO_WORK`, but it is not automatically proof that the
backlog is drained. Classify every remaining Backlog issue before updating the idle
counter. Increment `idleTicks` only when the live Fitness Backlog contains zero
issues after duplicate/refusal reconciliation and there is no recoverable claim,
blocked relation, existing branch or closed/unmerged PR, parked issue, or
owner-input item left to resolve. Existing branches and closed PRs must be
reconciled, parked with durable evidence, or escalated; they are never silently
filtered out. If any Backlog item remains blocked, parked, owned, or otherwise
ineligible, report `STATUS: NO_WORK` with `NO_WORK_BLOCKED` and the classifications
in `NOTES`, and preserve `idleTicks`. Backpressure, precondition, repair, and
escalation exits also preserve it.

## 5. Isolation and implementation

For a new issue, create a fresh worktree from current `origin/main` at:

```text
~/codex-loops/worktrees/fitness/FIT-XXX
```

Create Linear's exact suggested branch in that worktree, then run
`scripts/install-worktree-guard.sh` inside it before editing. Never edit
`/Users/admin/fitness-dashboard` in place. One FIT issue maps to one branch and one
PR. Implement only the acceptance criteria; no opportunistic refactors, dependency
changes, config rewrites, or cleanup. Kill-ai-slop fixes scoped to files this tick
authored are required, not opportunistic, and are exempt from this rule.

Use codebase-memory-mcp for code discovery. Re-index the exact issue worktree when its
graph is absent or stale. Add or update tests for changed behavior.

### UI-facing issues

OWNER DECISION RECORDED BY FIT-368: unattended UI-facing ticks follow
`fitness-dashboard-issue-closeout`'s existing routing. Codex owns backend, tests,
data, scripts, infrastructure, review, and closeout by default. Visible UI, mobile,
and gym-flow implementation routes to Claude unless the issue explicitly assigns
Codex that slice. This is the owner's direct loop-setup choice for unattended work;
it does not change interactive Codex UI ownership outside these loops. Do not port
the Finance image-generation five-variant override. If required visual proof cannot
be produced headlessly, stop and comment the missing proof on
`FIT loop escalations`; never fake screenshots or visual evidence.

### Native HealthKit refusal

Refuse any issue proposing Swift, HealthKit permissions, native targets, or native
HealthKit implementation unless the measured trigger and owner approval in
`docs/APPLE_HEALTH_HELPER_SLA.md` are already proven. Comment the refusal and exact
SLA gap on the issue and `FIT loop escalations`, return the issue to Backlog, and
report `OWNER_ATTENTION`. Do not implement native HealthKit work.

## 6. Tests and runtime safety

Before any other step here, run the kill-ai-slop scan. This is the opening block of
the section, so it runs for both new-issue ticks and repair rounds; Section 2 step
7's "Run the full Section 6 verification explicitly" already covers it. Skip it
entirely when this tick authored no files, for example an empty re-trigger or a
docs-only change. Otherwise, when this tick added or modified UI or user-facing copy
files, run `node /Users/admin/.codex/skills/kill-ai-slop/scripts/scan.mjs
<absolute-issue-worktree-root> --json` under the process-group-enforced 600-second
timeout in the list below. Auto-apply fixes only to files this tick authored, as new
commits, before pytest runs and before the Section 7 artifact sweep. Record the
before/after hit counts in the Section 8 PR evidence list and in the CHECKS line's
`slop-scan` field.

OWNER DECISION RECORDED BY FIT-368: auto-applying kill-ai-slop fixes without asking,
scoped strictly to files this tick authored, is the owner's explicit decision for
unattended ticks. It does not authorize running the interactive skill workflow or
touching any file this tick did not author.

Fresh worktrees have no venv. Always use this interpreter from the worktree root:

Before either explicit pytest or the pre-push rerun, a trusted wrapper must create
a temporary macOS keychain INSIDE the temporary `HOME` (first
`mkdir -p "$TEMP_HOME/Library/Preferences" "$TEMP_HOME/Library/Keychains"`), and
run every `security` configuration command with `HOME` set to the temporary HOME
so the keychain search list and default keychain are written to the temporary
HOME's own preferences — never to the owner's user-global configuration:
`security create-keychain`, `security list-keychains -d user -s "$KC"`,
`security default-keychain -d user -s "$KC"`, `security unlock-keychain`. Prove
isolation by readback in the child context: `list-keychains` and
`default-keychain` show only the temporary keychain, and a lookup of a known
login-keychain item fails there. Run with that temporary `HOME` and
`CFFIXED_USER_HOME` and a credential-free allowlisted environment. No
save/restore of the owner's configuration exists or is needed because it is
never modified; delete only the temporary keychain afterward. A `-60006`
status, any GUI prompt, or any failed readback means isolation is unavailable —
stop before running tests. This applies to the sanity import, explicit suite,
installed review, and hook-triggered push; `DATA_DIR` isolation alone is
insufficient.

```sh
TEST_DATA_DIR="$(mktemp -d /private/tmp/fitness-build-data.XXXXXX)"
export DATA_DIR="$TEST_DATA_DIR"
trap 'rm -rf "$TEST_DATA_DIR"' EXIT
PYTHONPATH=. /Users/admin/fitness-dashboard/venv/bin/python -m pytest -q
```

Run the full suite explicitly; the pre-push hook is not the safety net. Paste the
real result in PR and Linear evidence. Never set
`FITNESS_SKIP_PRE_PUSH_TESTS=1`.

The sanity import, full pytest, installed review/audit flow, the kill-ai-slop
`scan.mjs` scan, and `git push` (including its pre-push pytest rerun) each get a
process-group-enforced hard timeout of about 10 minutes (600 seconds); `git fetch`
gets the same process-group enforcement at a 120-second timeout. Use an
execution-tool timeout or a BSD/macOS-compatible wrapper that terminates the process
group. Never wait unbounded. On timeout, preserve the worktree and unpushed work,
record the exact command and recovery path in `state.json` and `FIT loop
escalations`, report a bounded failure, increment the current issue's or PR's
`state.json.buildAttempts` or `state.json.repairAttempts` entry, and release the
outer lock.

The trusted pre-push hook resolves `python` from `PATH`. On every push, prefix PATH
with the canonical venv so the hook reruns pytest with the same interpreter. Keep
the exported throwaway `DATA_DIR` active through the push so the hook inherits it:

```sh
PUSH_DATA_DIR="$(mktemp -d /private/tmp/fitness-push-data.XXXXXX)"
trap 'rm -rf "$PUSH_DATA_DIR"' EXIT
PATH="/Users/admin/fitness-dashboard/venv/bin:/opt/homebrew/bin:/usr/bin:/bin" \
  DATA_DIR="$PUSH_DATA_DIR" git push <remote> <branch>
```

Do not bypass the hook and do not allow `/opt/homebrew/bin/python3` to replace the
canonical venv interpreter.

Before pytest, run the flask+pytest sanity import from Section 1. For any isolated app
or server run, set `DATA_DIR` to a newly created throwaway directory and remove only
that throwaway directory afterward. `runtime_config.py` reads `DATA_DIR`; a wrong or
missing value can write into the owner's real health data. Never point an isolated
run at the canonical checkout's data directory. Do not launch an app when tests are
sufficient.

Also run:

```sh
git diff --check
```

Run the repository's installed review/audit flow required by
`fitness-dashboard-issue-closeout` and capture exact-head evidence. Tests are
required but not sufficient.

## 7. Artifact safety — full repository policy

Read `docs/REPO_HYGIENE.md` in full on every issue and sweep both tracked files and
the diff. The never-commit set is the full policy, not a summary:

- `.env` and `.env.*`
- `.flask-secret`
- `.health-sync-token`
- `.apple-health-first-sync`
- `.whoop-client-id`
- `.whoop-protected-material-*.json`
- `auth.db*`
- `fitness_data.db`
- `apple_health_sync.db`
- `oura_daily.sqlite3`
- `whoop.sqlite3`
- `whoop_sync.lock`
- `ai_coach_cache.sqlite3`
- `data_*.json`
- logs, backup bundles, generated smoke-test bodies, runtime screenshots/photos,
  visual-review screenshots, generated visual-review folders, temporary audit
  artifacts, one-off proof JSON, and local backup files

Never commit real hostnames, tokens, personal paths, credentials, live health data,
database contents, generated health exports, or protected WHOOP material. Sanitized
durable QA evidence is allowed only when intentionally placed under a tracked docs
path and referenced by the PR. Do not delete local runtime data or artifacts.

## 8. Ship and evidence

Commit subjects are imperative, at most 72 characters, with `FIT-XXX` in the subject
or a `Refs: FIT-XXX` trailer. Push the issue branch. Open a Draft PR whose:

- title is the Linear issue title,
- body follows `.github/pull_request_template.md`,
- body contains `Closes FIT-XXX`,
- acceptance criteria have direct evidence,
- `How to test` has the exact commands/results,
- `What was intentionally not done` explicitly says what was NOT tested or proven,
- `Agent involvement` names the build loop, and
- `loop-build` is applied AT PR creation.

Move the Linear issue to `In Review` when the PR opens. Follow
`codex-proof-handoff`: never compress the receipt, especially the NOT-tested
section, because Claude's end-of-backlog audit targets those seams. Post a standalone
GitHub PR evidence comment with branch, commit, tests, review result, accepted/rejected
findings, slop-scan before/after hit counts, merge state, and exact omissions. Add the
same closeout fields to Linear.

Never mark Ready and never merge. After every push, run:

```sh
gh pr view <PR> --json mergeStateStatus,state,baseRefName,headRefName
```

If `DIRTY`, the next build tick's repair lane handles it.

## 9. Escalation and state

All loop escalations are comments on the existing Linear issue
`FIT loop escalations`. The loop never creates Linear issues. Include issue/PR, pinned
SHA when relevant, exact blocker, checks attempted, evidence, owner action, and safe
retry condition. Add `owner-attention` to the relevant PR for owner-only or repeated
blocks.

Serialize state updates with an inner `~/.codex/loops/fitness/state.lock` mkdir
mutex. Write `meta.txt` with BSD UTC timestamp and pid. A lock is stale ONLY when its
recorded pid is dead. If the pid is alive and the lock is older than 25 minutes, do
not take it over; report `LONG_RUNNING`. Missing/unparseable pid is not proof of
staleness. If `meta.txt` is absent or its pid is unparseable, do not remove or enter
the lock: post one deduplicated `OWNER_ATTENTION` comment on `FIT loop escalations`
with the exact lock path and the manual recovery instruction to verify no owner
process is using it before removing it, then stop. Keep `state.lock` short-lived and
never hold it across network calls.
Write `state.json` through a temp file plus atomic rename, preserve unknown keys,
then release `state.lock`.

- A shipped PR, repaired PR, or duplicate closeout resets `idleTicks` to zero.
- Only a proved empty-Backlog tick increments `idleTicks` by one. FIT-369 is excluded
  from that test; the Backlog counts as empty when only FIT-369 remains.
- `idleTicks` may only increment when, additionally, no open `loop-build` PR remains
  unreviewed or unmerged.
- Backpressure, overlap, precondition failure, escalation, and long-running exits
  preserve `idleTicks`.
- Append the tick goal/status to `goals`, capped at 50.

After 16 consecutive proved-empty-Backlog idle ticks:

1. Post a completion comment on `FIT loop escalations` saying the backlog is drained
   and the app is ready for Claude's full audit. Include open loop-build PR states,
   parked/escalated items, recent goals, and the queue evidence.
2. Read back that exact durable comment. Only after readback succeeds, atomically
   create `~/.codex/loops/fitness/PAUSE`. If the write or readback fails, preserve
   `idleTicks=16`, leave `PAUSE` absent, and retry the completion receipt next tick.
3. Report `STATUS: IDLE_STOPPED` and stop. Do not send Hermes/Telegram messages.

## 10. End-state report

Final output is exactly one line, then the saved automation releases the outer lock
as its unconditional last action:

```text
GOAL: <text> — achieved|blocked|no_work / STATUS: SHIPPED|FIXED_REVIEW|DUPLICATE|NO_WORK|PRECONDITION_FAILED|LONG_RUNNING|OWNER_ATTENTION|ESCALATED|PAUSED|IDLE_STOPPED|FAILED / ISSUE: FIT-XXX|none / PR: <url> label=loop-build|none / COMMITS: <shas>|none / CHECKS: sanity-import=.. pytest=.. diff-check=.. review=.. slop-scan=.. artifact-safety=.. / NOT_DONE: <plainly, including what was not tested> / NOTES: <Loopy; prompt conflict; blocker; or none>
```
