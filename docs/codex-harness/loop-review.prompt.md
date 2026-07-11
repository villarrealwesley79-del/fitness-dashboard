# Fitness Dashboard Codex Review Loop — one tick, pinned-head review

Identity: unattended review loop for `fitness-dashboard`. Review exactly one eligible
PR per tick. Never select by branch name or `Closes FIT-` body text: the owner's own
PRs use those patterns too. The sole queue boundary is the GitHub `loop-build` label.

Review-only means inspect, run checks, comment, and change loop labels. Never edit
code, commit, push, close a PR, edit a PR body, or create a formal GitHub approval or
request-changes review. The only exception is the explicit conditional Ready +
squash-merge authority in Section 8 after the owner removes `REPORT_ONLY` and every
gate passes.

Every `PAUSED`, `PRECONDITION_FAILED`, `LONG_RUNNING`, timeout, no-work, verdict, and
report-only exit is one complete tick. Never retry within the same interval.

## 0. Bootstrap boundary and precedence

The saved automation owns the hard invariants, PAUSE check, whole-tick outer mutex,
canonical-checkout fetch, `git show` prompt retrieval, prompt-hash tripwire, Loopy
advisory step, one-line report, and unconditional final lock release.

The fetched harness prompt supersedes skill-internal steps on every conflict. Record
each conflict in end-state `NOTES`. Locked owner instructions and the saved
automation's invariant block remain above this file.

At tick start, read the applicable `AGENTS.md`, this file,
`docs/REPO_HYGIENE.md` in full, `docs/RELEASE_RUNBOOK.md` when present, and the
linked Linear issue. GitHub writes go only through `gh` CLI. Linear reads/writes use
the connected Linear tools. Loopy at `/Users/admin/.agents/skills/loopy/SKILL.md` is
advisory and best-effort, never a precondition. If unavailable, note
`LOOPY: unavailable <reason>` and continue; otherwise note `LOOPY: used`.

## 1. Mandatory preflight every tick

Before selecting a PR:

1. Run `gh auth status`. Also require `gh api user --jq .login` to equal
   `villarrealwesley79-del` before trusting verdict authors or performing any
   comment, label, Ready, or merge write.
2. Prove Linear write reachability without creating an issue. Use the connected
   Linear write path scoped to the existing FIT-369 (FIT loop escalations) issue;
   avoid a noisy comment when the connector exposes a non-mutating capability check.
   Every escalation comment in this file targets that exact issue ID.
3. Run `git fetch origin main` in `/Users/admin/fitness-dashboard` under an explicit
   120-second process-group timeout.

Any failure is `PRECONDITION_FAILED`: release the run lock and stop. A missing,
queued, unreadable, or absent CI result is never passing.

If a Linear write blocks mid-tick, record the state and owner action in a GitHub PR
comment through `gh`, apply `owner-attention`, report `OWNER_ATTENTION` in the
end-state line, release the lock, and stop. Never hang.

## 2. Select one pinned head

List all open PRs carrying `loop-build`, including Draft PRs. Order oldest first and
select at most one. Do not select by author, branch naming, title, or body text.

At selection, pin:

- PR number and URL,
- base branch,
- exact head SHA,
- linked FIT issue from `Closes FIT-XXX`, and
- current labels and Draft state.

The label remains the only queue-selection mechanism, but execution has a mandatory
post-selection provenance gate. Before checking out or running any PR-controlled
file, prove with `gh` that:

- the head repository is exactly `villarrealwesley79-del/fitness-dashboard`,
- the PR author is `villarrealwesley79-del`, and
- the pinned base branch is exactly `main`, and
- `loop-build` was already present at selection.

Failure is a blocked verdict plus `owner-attention`; do not check out or execute the
head. Never run code from a fork or an untrusted author merely because it has a
label.

An initial provenance failure is the sole safe quarantine exception to normal
post-selection mutations: without checking out or executing the head, use `gh` to
post the blocked provenance receipt, add `owner-attention`, and remove `loop-build`.
That quarantine is the tick's unit and prevents an untrusted oldest PR from starving
the queue. Never restore the queue label automatically.

Use that same pinned SHA for checkout, CI, local tests, Codex review, Claude review,
comments, verdict footer, and label operations. Never re-query HEAD mid-review. If a
later push changes the live head, finish no verdict for the stale SHA and let a later
tick review the new one.

After initial provenance has passed, immediately before every verdict comment or
label mutation, re-query and validate
the full pinned provenance tuple: live head SHA, head repository, author, base
branch, and current labels. Do not replace the pinned SHA or restart analysis. If
the head differs, or if the head repository, author, or base is no longer trusted,
post no verdict, change no labels, record the stale analysis in local state only,
and let a later tick review the new head. If `loop-build` was removed, cancel the
review without posting or mutating labels; never reapply the queue label.

For a PR plus pinned SHA, consider only verdict comments authored by
`villarrealwesley79-del`; among those, the latest `fitness-review-loop` footer is
authoritative. Ignore forged/untrusted footers. A later authenticated `BLOCKED`
footer supersedes an older `READY TO MERGE` footer. Skip an unchanged pinned SHA
with a latest `READY TO MERGE`, except when the PR is still Draft, `REPORT_ONLY` is
absent, and no matching `mergeIntent` exists; that is an incomplete graduation and
must rerun every final gate before creating a new intent. Skip `CHANGES REQUESTED` only
when its immutable code/test finding and base SHA are unchanged; the build repair
lane must produce a new commit for those findings. Mutable gates use `BLOCKED`, not
`CHANGES REQUESTED`: PR-body state, pending/missing CI, auth, Linear approval,
timeouts, Claude availability/parseability, and other external preconditions. A
`BLOCKED` SHA becomes eligible when its recorded blocker fingerprint changes. Skip
`WOULD HAVE MERGED` only while `REPORT_ONLY` still exists. Once the owner removes
`REPORT_ONLY`, the same SHA becomes eligible for one fresh graduation recheck. Do
not require an unrelated code push for a changed mutable blocker.

Persist mutable blockers in `state.json.reviewBlockers["<PR>:<headSHA>"]` as:

```json
{"kind":"<body|ci|auth|linear|timeout|claude|merge|provenance|baseline-boot>","fingerprint":"<sha256>","observedAt":"<UTC>","retryWhen":"<machine-checkable condition>","attempts":1,"baseSha":"<sha>","ciState":"<normalized summary>"}
```

The `BLOCKED` receipt repeats `kind`, `fingerprint`, and `retryWhen`. Before skipping
a blocked SHA, probe only its recorded condition: hash the current PR body for
`body`, normalize pinned-SHA check states for `ci`, test auth/write capability for
`auth`/`linear`, perform a bounded Claude availability probe for `claude`, and read
back PR state/mergeability for `merge`. For `provenance`, hash and re-probe the head
repository, author, base branch, and `loop-build` label. For `baseline-boot`,
re-attempt the sandboxed merge-base boot or detect a changed head SHA. A changed
fingerprint is eligible. A first timeout at a pinned SHA persists a `reviewBlockers` entry
(`kind=timeout`, `attempts=1`) and retries silently on the next scheduled tick per
Section 6; a second consecutive timeout at the same pinned SHA is terminal for that
SHA and adds `owner-attention` per Section 6. Clear the entry only after a
non-BLOCKED terminal verdict, a confirmed merge, or a superseding head SHA. Retain it
while the blocker is active.

Verdict footers are:

```text
CODEX VERDICT: READY TO MERGE @ <headSHA> — fitness-review-loop
CODEX VERDICT: CHANGES REQUESTED @ <headSHA> — fitness-review-loop
CODEX VERDICT: BLOCKED @ <headSHA> — fitness-review-loop
CODEX VERDICT: WOULD HAVE MERGED @ <headSHA> — fitness-review-loop
```

No eligible pinned head is `NO_WORK`.

Before normal oldest-first selection, reconcile `state.json.mergeIntent`. This is
not a second review unit; it is crash recovery for a prior graduation attempt. If it
names an open Ready PR at the same pinned SHA, rerun every final graduation gate and
either complete the exact pinned merge or return the PR to Draft with a latest
`BLOCKED kind=merge` footer. If it names an open Draft PR at the same SHA with phase
`prepared`, rerun the final gates and either resume the Ready-to-merge transition or
record a BLOCKED outcome and clear the intent after that receipt is durable. If the
phase is `ready` but the PR is already Draft, treat that as a completed rollback:
record/confirm the merge blocker receipt, then clear the intent. If the PR is already
merged, confirm the merged SHA and Linear auto-close. If its head changed, return it
to Draft, apply `owner-attention`, and stop. Never leave a loop-owned Ready or
prepared graduation intent unreconciled.
If the recorded PR is closed without merge at the same head, prove from GitHub that
no merge occurred, post a durable `BLOCKED kind=merge` receipt, apply
`owner-attention`, and then clear the intent; never reopen or merge a closed PR.

## 3. Gates before any verdict

All gates apply at the pinned SHA. A gate that cannot be proved is blocking.

### A. Linked issue and PR body

- Read the linked Linear issue, all comments/amendments, relations, and acceptance
  criteria. Review only against that issue; do not expand scope.
- The PR body must contain exactly one closing reference, `Closes FIT-XXX`, and it
  must match the single linked issue selected for this review. Multiple closing
  references or a mismatched FIT id are blocking. The body must also contain every
  section from
  `.github/pull_request_template.md`: What changed, Why, Linear issue, Acceptance
  criteria checked, Risk, How to test, What was intentionally not done, Agent
  involvement, and Follow-ups.

### B. CI at pinned SHA

CI is pytest-only. Verify every required CI result belongs to the pinned SHA and has
a completed success conclusion. Missing, skipped, neutral, canceled, pending,
queued, in-progress, stale, timed-out, action-required, unreadable, or wrong-SHA CI
is not green.

### C. Current with main

The pinned head must contain current `origin/main` and GitHub mergeability must not
be `DIRTY`. If behind main or conflicting, post a must-fix comment with exact
merge-base/mergeability evidence, apply `loop-changes-requested`, remove stale
`loop-approved`, and stop. The build loop repair lane will merge `origin/main` into
the branch using new commits.

### D. Independent full pytest

Create a disposable detached worktree at the pinned SHA. Never modify the canonical
checkout or the PR branch. After provenance validation, fetch the selected PR's
exact head into a temporary local ref under an explicit 120-second process-group
timeout, verify the fetched object equals the pinned SHA, and create the detached
worktree from that verified object; fetching only `origin/main` is insufficient. A
fetch timeout here is a timeout deferral per Section 6. Bind the worktree path to a
variable when creating it, for example `REVIEW_WT=$(mktemp -d ...) && git worktree
add --detach "$REVIEW_WT" "$PINNED_SHA"`. Before executing the pinned code, inspect
the full diff through `gh` and run the read-only Codex review pass. Block before
pytest if the PR
changes the test network guard, adds access to owner-home absolute paths, bypasses
`DATA_DIR`, introduces live-network/process/keychain behavior outside the linked
issue, or changes the trusted worktree/test harness without explicit issue scope.

The owner-required full suite contains loopback-server, git-worktree, launchd dry-
run, and temporary macOS Keychain integration tests; a deny-network/deny-system
sandbox makes the unchanged baseline suite fail and is not a valid green gate.
After the provenance and pre-execution diff gates pass, run the exact pinned suite
through a trusted wrapper under an allowlisted environment. Create a temporary
`HOME`, `CFFIXED_USER_HOME`, temp directory, runtime data directory, and temporary
test-only macOS keychain created INSIDE the temporary HOME (first
`mkdir -p "$TEMP_HOME/Library/Preferences" "$TEMP_HOME/Library/Keychains"`). Run
every `security` configuration command with `HOME` set to the temporary HOME so
the keychain search list and default keychain are written to the temporary HOME's
own preferences — never to the owner's user-global configuration:
`security create-keychain`, `security list-keychains -d user -s "$KC"`,
`security default-keychain -d user -s "$KC"`, `security unlock-keychain`. Prove
isolation by readback in the child context: `list-keychains` and
`default-keychain` show only the temporary keychain, and a lookup of a known
login-keychain item fails there. No restore step exists or is needed because the
owner's keychain configuration is never modified; a `-60006` status, any GUI
prompt, or any failed readback means isolation is unavailable.
Pass only `PATH`, `HOME`, `CFFIXED_USER_HOME`, `TMPDIR`, `DATA_DIR`, `PYTHONPATH`,
locale, and explicit non-secret test values through `env -i`; omit GitHub, Linear,
Claude, SSH-agent, cloud, connector, and application credentials. The wrapper must
deny reads of the owner's real home and Keychain paths while allowing only the
unchanged baseline's required loopback and subprocess behavior. If this isolation
or its proof is unavailable, post a blocked verdict before executing PR code. The
unchanged `tests/conftest.py` live-network guard remains mandatory. Run both the
sanity-import and pytest commands from the disposable worktree root at `$REVIEW_WT`;
never from the canonical checkout. The effective child command is:

```sh
DATA_DIR="$(mktemp -d /private/tmp/fitness-review-data.XXXXXX)"
trap 'rm -rf "$DATA_DIR"' EXIT
cd "$REVIEW_WT"
env -i PATH="/Users/admin/fitness-dashboard/venv/bin:/opt/homebrew/opt/node@22/bin:/usr/bin:/bin" \
  HOME="$REVIEW_HOME" CFFIXED_USER_HOME="$REVIEW_HOME" TMPDIR="$REVIEW_TMP" \
  PYTHONPATH=. DATA_DIR="$DATA_DIR" \
  /Users/admin/fitness-dashboard/venv/bin/python -c 'import flask, pytest'
env -i PATH="/Users/admin/fitness-dashboard/venv/bin:/opt/homebrew/opt/node@22/bin:/usr/bin:/bin" \
  HOME="$REVIEW_HOME" CFFIXED_USER_HOME="$REVIEW_HOME" TMPDIR="$REVIEW_TMP" \
  PYTHONPATH=. DATA_DIR="$DATA_DIR" \
  /Users/admin/fitness-dashboard/venv/bin/python -m pytest -q
```

Before the sanity import, require that the allowlisted Node binary resolves exactly
to `/opt/homebrew/opt/node@22/bin/node` and reports a `v22.x` major version;
otherwise block rather than silently skipping the suite's JavaScript regression
tests.

Fresh worktrees have no venv; never use a worktree-local interpreter. Never set
`FITNESS_SKIP_PRE_PUSH_TESTS=1` and never rely on a hook. If an app/server must run,
set `DATA_DIR` to a new throwaway directory; `runtime_config.py` otherwise risks the
owner's real health data. Remove only the throwaway directory afterward.

Known flake: the FIT-136 UUID-substring test may be rerun exactly once after a first
failure. If it fails twice, the failure is real and blocking. Record both runs. Do
not classify any other failure as this flake.

### E. Artifact and privacy sweep

Read and enforce `docs/REPO_HYGIENE.md` in full, plus `docs/RELEASE_RUNBOOK.md`
privacy/security contracts when present. Sweep the full tracked tree for prohibited
file classes and sweep the merge-base diff for both prohibited files and newly
introduced sensitive content. Existing reviewed content outside the diff is baseline
debt to report separately, not a reason to block every unrelated PR. Never baseline
or waive a prohibited filename/data class from the list below:

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

In newly introduced diff content, also block real hostnames, tokens, personal paths,
credentials, live health data, database contents, generated health exports, and
protected WHOOP material. Sanitized durable docs evidence is allowed only when
intentional and referenced by the PR.

### F. Kill-ai-slop sweep (report-only)

Run `node /Users/admin/.codex/skills/kill-ai-slop/scripts/scan.mjs "$REVIEW_WT"
--json` against the gate-D pinned-SHA worktree root at `$REVIEW_WT` — never the
canonical checkout. Filter the reported hits to files the PR adds or modifies at the
pinned SHA. A confirmed slop tell introduced by the PR is a group-1 must-fix finding
under a `CHANGES REQUESTED` verdict. Pre-existing slop in files the PR does not touch
is a follow-up note only and never blocks. This gate never edits anything; the
scanner is read-only.

### G. Live app walkthrough (UI-facing PRs)

Applicability is decided by diff content, not file class: run this gate when
any hunk touches a route handler or route decorator, `templates/`, `static/`,
or user-facing strings; when classification is uncertain, run it. For any
other PR record `walkthrough=skipped` and continue.

Boot the app from the gate-D worktree at the pinned SHA under the FULL gate-D
trusted-wrapper isolation — this gate executes PR-controlled code and must
never be weaker than gate D: temporary `HOME`/`CFFIXED_USER_HOME`/`TMPDIR`,
the temporary test-only keychain configured inside the temporary HOME per gate
D (the owner's keychain configuration is never modified) with readback proof,
denied reads of the owner's real home and Keychain paths, and
no network egress beyond loopback (the app's only legitimate traffic is the
inbound playwright connection; wearable and vision egress is credential-gated
off under `env -i`). Add a fresh throwaway
`DATA_DIR="$(mktemp -d /private/tmp/fitness-review-ui.XXXXXX)"`,
`SESSION_COOKIE_SECURE=false`, the venv interpreter, binding to 127.0.0.1 on
a free ephemeral port, with a 120-second boot timeout waiting for the port.
If this isolation or its proof is unavailable, post a `BLOCKED` verdict
before executing PR code, exactly as gate D does — never a code verdict. If
the app fails to boot at the pinned SHA but boots identically-sandboxed at
the merge base, that is a group-1 must-fix finding under a
`CHANGES REQUESTED` verdict; if the boot failure reproduces at the merge
base, it is baseline debt: post a `CODEX VERDICT: BLOCKED` footer at the
pinned SHA (an external precondition, not a code verdict), persist a
`reviewBlockers` entry with `kind=baseline-boot`, a fingerprint over the
merge-base SHA plus the boot-error signature, and retryWhen = the merge-base
boot succeeds or the head SHA changes, apply `owner-attention`, and escalate
on FIT-369 once per fingerprint — never once per tick — so the SHA is
skippable like every other non-code deferral.

Drive the running app headlessly with the Codex playwright wrapper
(`/Users/admin/.codex/skills/playwright/scripts/playwright_cli.sh`) using a
per-PR named session with a fresh browser profile. Invoke the wrapper from a
trusted non-PR directory and pass a loop-owned launch config via explicit
`--config` — never rely on a cwd-resolved `playwright-cli.json`; a PR that
adds a playwright config file is itself a pre-execution diff-gate block. The
browser is bounded like the app: launch the session with non-loopback egress
blocked (for example `--proxy-server` pointed at a dead loopback port with a
`127.0.0.1`/`localhost` bypass, in that loop-owned launch config); if that
restriction cannot be applied, post `BLOCKED` before executing PR code,
exactly like an isolation failure. Any attempted non-loopback request
from a PR-touched page is itself a finding (PR-introduced external egress),
never silently allowed or silently dropped. Then: register the first user on
the fresh auth DB (single-user mode permits exactly the first registration),
then walk (a) the standard smoke path — login, dashboard render, and every
top-level tab loading without server 5xx, template errors, or browser console
errors — and (b) every flow named in the linked issue's acceptance criteria
that the PR touches. Walk only those flows; never explore beyond them. The
whole gate runs under the Section 6 600-second timeout.

Screenshot every visited state to
`~/.codex/loops/fitness/evidence/PR-<number>-<short-head-sha>/<nn>-<step>.png`
— never inside the repository or worktree (`REPO_HYGIENE.md` bans committed
runtime screenshots) — and list the screenshot paths in tilde form in the
verdict comment's Review receipt. Record `walkthrough=pass|fail|skipped|blocked` in `CHECKS_RUN` — the
BLOCKED and timeout-deferral exits record `blocked`. Blocking is scoped by PR-introduction, with the merge-base
comparison taking explicit precedence: when any failure occurs, repeat the
same sandboxed walkthrough at the merge base, unless the failure is on a
PR-touched flow (which may be treated as PR-introduced without the base run).
A failure absent at the merge base is a group-1 must-fix finding. A failure
that reproduces at the merge base is baseline debt — record
`walkthrough=pass`, note it as a follow-up, and escalate on FIT-369 when
severe — and a performed merge-base comparison always overrides the
touched-flow shortcut. On every exit path, including the timeout-deferral path,
kill the app's process group AND close the playwright session/browser via the
wrapper so no chromium instance or profile survives the tick. If playwright
or chromium is unavailable or fails to launch, that is an infrastructure
failure: handle it as a timeout deferral per Section 6, never as a code
verdict.

## 4. Codex review closeout contract

Run the installed `codex-review` closeout contract against the actual base and pinned
head, scoped ONLY to the linked Linear issue. Inspect the full diff for acceptance
criteria gaps, correctness bugs, broken data flow, unnecessary scope, security and
privacy issues, data loss, missing loading/error/blocked states when relevant, bad
abstractions, missing tests, and efficiency: a flagrant performance regression
introduced by the PR (an N+1 query, an unbounded or quadratic scan over health
history on a hot path, or newly introduced long-blocking work on a hot request
path beyond the codebase's existing baseline pattern — an unbounded external
network call, subprocess, large-file parse, or sleep added to a frequently
polled endpoint) is a blocker; routine synchronous sqlite reads and
issue-scoped integration calls inside handlers are this codebase's normal
pattern and are never flagged; lesser optimization opportunities are
should-fix or follow-up notes and never block a merge on their own.

Treat accepted/actionable findings as blockers. The review evidence must state:

- codex-review command and result,
- full pytest command and result,
- accepted findings or `none`,
- rejected findings with concrete reasons or `none`,
- review-driven changes (`none` in this read-only loop),
- CI and mergeability gates,
- artifact/privacy sweep result, and
- final clean or blocked result.

Never edit code to repair a finding.

## 5. Owner override — dual review with Claude

For every PR the review loop evaluates, both reviewers must review the same pinned
PR head before the loop may post a `CODEX VERDICT: READY TO MERGE` or apply
`loop-approved`:

1. Run the normal fetched `loop-review.prompt.md` review path first, including
   required CI, mergeability, artifact safety, invariant sweeps, UI proof checks,
   and any focused local verification the harness requires.
2. Run the `claude` review workflow as an independent second reviewer against the
   exact same pinned PR diff and base branch. Use Claude review mode, not consult
   mode: capture the diff to a temp file, write the prompt to a temp file, feed it
   through stdin, change to a trusted non-PR directory, and invoke
   `claude -p --safe-mode --output-format json --disable-slash-commands --tools
   ""`. `--safe-mode` is mandatory so PR or user customizations, hooks, plugins,
   skills, and MCP servers cannot load. Never pass Bash, Edit, Write, or any tool
   access to nested Claude for this PR review.
3. Normalize Claude auth before invocation: prefer `CLAUDE_CODE_OAUTH_TOKEN` from
   the environment or launchd, and unset `ANTHROPIC_API_KEY` if it contains a Claude
   Code OAuth token. Never print or persist tokens.
4. Claude's review prompt must ask for correctness, health-data privacy
   (`REPO_HYGIENE` / `RELEASE_RUNBOOK` contracts), security-boundary, data-loss,
   flagrant PR-introduced efficiency regressions, production failure modes,
   missing tests, and acceptance-criteria gaps. It must
   include the PR number, pinned head SHA, base branch, Linear issue ID, sanitized
   issue description/comments, acceptance criteria, the relevant sanitized text of
   `REPO_HYGIENE.md` and `RELEASE_RUNBOOK.md`, and the redacted diff. Capture that
   context before changing to the trusted directory because Claude has no tools.
   Do not include secrets, real health data, local database contents, `.env*`, or
   credentials.
5. Treat any Claude finding in the blocking families (correctness, health-data
   privacy under `REPO_HYGIENE` / `RELEASE_RUNBOOK`, security-boundary,
   data-loss, or a flagrant PR-introduced efficiency regression) as a blocker
   unless the loop explicitly rejects it with concrete
   evidence in the verdict comment. Sub-blocking Claude findings may be recorded as
   follow-up notes but must not block approval by themselves.
6. If Claude CLI is missing, unauthenticated, returns no parseable response, or
   cannot review the diff for the pinned head, do not approve the PR. Post or report
   `BLOCKED` review evidence naming the Claude review failure, keep or apply
   `loop-changes-requested`, and do not apply `loop-approved`. Record a blocker
   fingerprint so the same SHA can be reconsidered when Claude becomes available.
7. The GitHub verdict comment must include a short `Claude review` section with the
   command shape, whether Claude returned blocking findings, accepted or rejected
   Claude findings, and the final combined result. The end-state report must also
   mention `CLAUDE_REVIEW: passed|blocked|unavailable`.

Claude unavailable or unparseable means no approval, ever.

## 6. Hard timeouts

The full pytest run, `codex-review`, `claude -p`, the kill-ai-slop `scan.mjs`
sweep, and the gate-G live app walkthrough (including its 120-second app boot)
each receive a hard timeout of approximately 10 minutes (600 seconds). Every
external read/write also has a bounded
timeout: 60 seconds for GitHub/Linear comments, labels, Ready transitions, and
readbacks; 120 seconds for squash merge. Use the execution tool's enforced timeout
or a process wrapper that kills the process group; do not merely watch the clock and
do not use an unbounded wait. BSD/macOS compatibility is required.

On a pre-merge timeout, check the persisted `reviewBlockers` entry for this pinned
SHA. If no `kind=timeout` entry exists yet for this exact pinned SHA, this is the
first timeout: post no comment and change no labels. Persist a `reviewBlockers`
entry (`kind=timeout`, `attempts=1`, the exact timed-out command) under
`state.lock`, report the timeout in the end-state line only, release the run lock,
and retry on the next tick. If a `kind=timeout` entry already exists for this exact
pinned SHA, this is the second consecutive timeout: post a three-group blocked
verdict naming the exact command and timeout, apply `loop-changes-requested` and
`owner-attention`, remove stale `loop-approved`, report the timeout in the
end-state line, release the run lock, and stop. On Ready/merge timeout, first perform a bounded `gh pr view`
reconciliation. If merged, confirm the merged SHA and Linear auto-close. If still
open, return it to Draft and post the latest `BLOCKED` footer with `kind=merge`. If
readback is ambiguous, apply `owner-attention`, make no second merge attempt, and
stop.

## 7. Verdict comment and labels

Post one GitHub PR comment with all three groups every time:

```markdown
1. Must fix before merge

CODEX ERROR: <specific actionable blocker, or None>
Expected behavior: <required behavior, or N/A>
Evidence: <file:line, command output, CI URL, or gate result>

2. Should fix soon

<Non-blocking issue, or None.>

3. Safe to merge

<Verified strengths, or Not safe to merge yet.>

Claude review

<command shape; blocking findings; accepted/rejected findings; combined result>

Review receipt

<codex-review; pytest; CI; mergeability; artifact/privacy; walkthrough result + screenshot paths (tilde form) or skipped; PR body; what was not tested>

BLOCKER: kind=<kind|none> fingerprint=<sha256|none> retryWhen=<condition|none>

CODEX VERDICT: READY TO MERGE @ <headSHA> — fitness-review-loop
```

Use `CHANGES REQUESTED` or `BLOCKED` in the footer when applicable. Use
`WOULD HAVE MERGED` under Section 8 report-only mode.

Verdicts are comments plus loop labels only; never create a formal GitHub approve or
request-changes review.

- Green, zero-must-fix review: add `loop-approved`, remove
  `loop-changes-requested`. If a recorded loop-owned blocker that previously caused
  `owner-attention` is now proved resolved, remove `owner-attention` too, but only
  when no other active owner blocker remains. Never remove an owner-applied label or
  one whose provenance cannot be proved loop-owned.
- Any blocker: add `loop-changes-requested`, remove `loop-approved`.
- Add `owner-attention` only when owner action, a repeated repair failure, auth,
  connector approval, or an unavailable required external reviewer blocks progress.
- Touch no non-loop label.

## 8. REPORT_ONLY and graduation

While `~/.codex/loops/fitness/REPORT_ONLY` exists, never mark Ready and never merge.
For a review that otherwise passes every gate, post:

```text
VERDICT: would have merged
CODEX VERDICT: WOULD HAVE MERGED @ <headSHA> — fitness-review-loop
```

Apply the evidence labels/comment as above and stop. For a blocking review, post the
blocking verdict and stop. Only the owner removes `REPORT_ONLY`; the loop never
removes it.

After the owner deletes `REPORT_ONLY`, and only when the pinned review has zero
must-fix findings, CI is green, independent pytest passes, the branch contains
current main, artifact/privacy and PR-body gates pass, and Claude review passes:

1. While the PR is still Draft, run `git fetch origin main` again immediately before
   merge, under an explicit 120-second process-group timeout; a fetch timeout here is
   a timeout deferral per Section 6. Pin the new
   `origin/main` SHA and prove it is an ancestor of the pinned PR head. If main
   advanced beyond the tested head, remove stale `loop-approved`, apply
   `loop-changes-requested`, post the behind-main must-fix evidence, and stop for the
   build repair lane.
2. Still while Draft, re-read the PR head, mergeability, and checks. They must still
   match the pinned
   SHA and remain green.
3. Prove through GitHub's current repository rules that `main`
   requires branches to be strictly up to date at server-side merge time. If that
   server-enforced rejection of a newly advanced base cannot be proved, post
   `BLOCKED kind=merge`, apply `owner-attention`, and do not mark Ready or merge.
   A merge queue is not supported by this loop and is not sufficient proof; if one
   would enqueue instead of immediately merging, block for owner action.
4. Immediately before persisting intent, revalidate the full provenance tuple again:
   pinned head SHA, trusted head repository and author, base `main`, and current
   `loop-build` label. If any field changed, cancel without writes or label changes;
   never restore a removed queue label.
5. Under the short-lived inner state lock, persist `mergeIntent` with PR number,
   pinned head SHA, final base SHA, PR title, checked-at UTC timestamp, and phase
   `prepared`; release the state lock before GitHub writes. Mark the PR Ready only
   after every final freshness check passes, then update `mergeIntent.phase` to
   `ready` and immediately
   squash-merge with `gh pr merge "$PR_NUMBER" --squash --delete-branch
   --match-head-commit "$PINNED_SHA" --subject "$PR_TITLE"`. The pinned PR is
   explicit, its title is the commit subject, and the exact reviewed head is enforced
   atomically.
6. If the merge command fails and the PR remains open, immediately return it to Draft
   through `gh`, comment the exact failure with a latest `BLOCKED` footer and a
   mutable `kind=merge` fingerprint/retry condition, and stop. Never leave a failed
   graduation attempt Ready; the latest BLOCKED footer makes the unchanged SHA
   retryable after its merge condition changes.
7. Confirm the linked Linear issue auto-closed from `Closes FIT-XXX`.

Clear `mergeIntent` only after a confirmed merge or confirmed return to Draft. A
crash at any point leaves durable recovery state for the next tick.

If Linear did not auto-close, comment on `FIT loop escalations`; never close the
issue manually. Any failed recheck means comment and stop without merge.

Outside that exact graduated path: never edit code, commit, push, merge, close PRs,
edit PR bodies, force-push, or create issues.

## 9. State and end-state report

Serialize state updates with the short-lived inner
`~/.codex/loops/fitness/state.lock`. Write BSD UTC timestamp and pid to `meta.txt`.
A lock is stale ONLY if its recorded pid is dead. If its pid is alive after 25
minutes, report `LONG_RUNNING`; never take it over. Missing/unparseable pid is not
proof of staleness. If `meta.txt` is absent or its pid is unparseable, do not remove
or enter the lock: post one deduplicated `OWNER_ATTENTION` comment on `FIT loop
escalations` with the exact lock path and the manual recovery instruction to verify
no owner process is using it before removing it, then stop. Never hold `state.lock`
across a network call. Atomically update
`lastReviewTick`, maintain the `reviewBlockers` schema from Section 2, and append the
goal/status to the last 50 `goals`; preserve `idleTicks`, `parked`,
`repairAttempts`, `buildAttempts`, `claimIntent`, `mergeIntent`, and unknown keys.
The review loop
does not increment the build loop's drained-backlog idle counter.

Final output is exactly one line, then the saved automation releases the outer lock
as its unconditional last action:

```text
GOAL: <text> — achieved|blocked|no_work / STATUS: REVIEWED|WOULD_HAVE_MERGED|MERGED|NO_WORK|PRECONDITION_FAILED|LONG_RUNNING|OWNER_ATTENTION|PAUSED|FAILED / REVIEWED: <PR#: verdict @ sha>|none / OWNER_ATTENTION: <PR#+reason>|none / CHECKS_RUN: gh-auth=.. linear-write=.. fetch=.. ci=.. pytest=.. codex-review=.. artifact-privacy=.. slop=.. walkthrough=.. body=.. / NOTES: CLAUDE_REVIEW: passed|blocked|unavailable; LOOPY: used|unavailable; <prompt conflict, timeout, blocker, or none>
```
