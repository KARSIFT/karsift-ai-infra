# karsift-ai-infra

Reusable GitHub Actions automation for the loop: **plan or implement a governed change → deterministic
CI checks it → an independent reviewer verifies the exact revision → the proven automated gate merges
it.** Formerly `vocanova-ai-infra` - renamed because the pipeline itself was never
Vocanova-specific; only the project wiring was. Any KARSIFT project can call these workflows.

## Roles are technology-agnostic

Every AI step in this pipeline is a **role**, not a vendor commitment:

| Role | What it does |
|---|---|
| `planner` | Turns a request - free text, an existing document, or a GitHub issue's whole thread - into a full DRAFT change package (spec, acceptance criteria, task breakdown) in the calling project's own package format. Asks a clarifying question instead of guessing. The independently reviewed exact revision is adopted deterministically after merge; the planner cannot adopt or review its own output. |
| `implementer` | Implements one approved task on a branch. No merge authority, no production access, cannot approve its own work. Escalates to a stronger model (`implementer_escalation`) on its last retry attempt rather than retrying blind. |
| `reviewer` | Independent, read-only verification. Posts a structured, commit-bound verdict. Never edits, merges, or approves. Routes to a cheaper model (`reviewer_fast_retry`) on a low-risk retry. |

**This README deliberately never names which model or vendor currently fills any role** - that
information changes often (this repo's git history includes moves across at least four different
CLIs/vendors so far) and a hardcoded table here has gone stale every single time it changed. **The
only file that names a specific model or vendor is `config/roles.yml`** - read that file directly for
the current occupant of each role, why it's there, and the full history of what it replaced. Swapping
any role to a different model/provider means editing that one file plus the relevant workflow's
execution step (`implement.yml`'s "Run implementer" step, `review.yml`'s/`plan.yml`'s "Run
independent verification"/"Run planner" step) - nothing else in this repo, and nothing in a calling
project's own workflow, should need to change. `CHANGELOG.md` covers the parallel history of *which
CLI/execution mechanism* each role's workflow step invokes, independent of which model sits behind it.

**Reviewer and implementer are supposed to stay different vendors** - independent review that shares
a vendor with the implementer isn't independent, it's self-review. Check `config/roles.yml`'s actual
current values before assuming that holds at any given moment; it has been temporarily violated more
than once when a vendor outage or quota exhaustion left no better option, always documented there
when it happens.

## What this is not

Not a full Control Plane, not a durable work queue, not an AI Budget Governor, not a founder-facing
chat interface. It's the smallest reusable slice that makes "implement → verify → merge" real and
auditable: a working, evidenced loop from an approved change package to a reviewed PR. A durable
queue, staged production rollout, and anything past PR-merge is real future work for whichever
calling project needs it, not simulated here.

## Why this is a separate repo

Reusable GitHub Actions workflows belong in one place, editable independent of any project repo. A
calling project gets a thin `pipeline.yml` (see `templates/project-repo/`) that wires its triggers
into this repo's reusable workflows - nothing project-specific belongs here, and nothing about this
repo's internals should require touching a calling project's copy.

## Governance: this repo enforces gates, it does not set policy

This repo has an opinion about *mechanism* (approved package required, independent read-only review,
fail-closed merge gate) and deliberately no opinion about *policy* (what counts as R0 vs. R4, who the
founder is, what a project's change-package format looks like). Each calling project supplies that
through its own governance documents and through inputs to `merge-gate.yml`:

- **Branch model**: `implement.yml` and `review.yml` both take an `integration_branch` input
  (default `"develop"`, for projects that split `develop`/`main`). Set it to `"main"` for a
  GitHub-flow-only project with a single long-lived branch - get this wrong and the very first
  checkout step fails outright, with a git error that doesn't obviously say "wrong branch name."
- **Implementation authority**: `implement.yml` refuses to run unless the calling project's own
  `change.yaml`-equivalent shows the package as adopted and authorized. A chat prompt or bare issue is
  never sufficient - only an approved package is.
- **PR-creation identity (optional but recommended)**: by default, `implement.yml` opens its PR using
  the workflow's default `GITHUB_TOKEN`. GitHub requires a manual "Approve workflows to run" click on
  every resulting PR when it detects `GITHUB_TOKEN` created or updated it - same-repo or not, this is
  GitHub's own security behavior, not a bug here. Set `KARSIFT_BOT_APP_ID` and
  `KARSIFT_BOT_PRIVATE_KEY` (a GitHub App installed on the calling project, `contents`/`issues`/
  `pull-requests: read & write`) to remove that friction - `implement.yml` mints a short-lived
  installation token and uses it instead, automatically, whenever those two secrets are present.
  Without them, behavior is unchanged from before.
- **Release PR identity and checks**: `release.yml` requires those same GitHub App credentials for
  promotion PRs. It creates and merges the PR as the App identity so GitHub does not pause the PR's
  workflows for maintainer approval, waits fail-closed for every registered PR check, and only then
  merges the integration branch into the production branch. Release PRs declare the conservative R4
  risk class because the promotion updates the production branch, even though deployment remains out
  of scope.
- **Independent review**: `review.yml` runs the reviewer role with **read-only** tools only. It can
  read the diff and the package and post one comment - nothing else. Findings are Critical / High /
  Medium / Low; the verdict is one of `PASS`, `PASS WITH NON-BLOCKING FINDINGS`, or `FAIL`, bound to
  the exact reviewed commit SHA.
- **Merge authority**: `merge-gate.yml` is risk-aware and **fails closed**. It reads a
  `Risk classification: R#` line from the PR body (any project can use a different risk scheme, but
  this is the convention the gate parses today); a PR with no parseable risk declaration never
  merges and must be corrected. R0-R4 can auto-merge only when
  `auto_merge_enabled: "true"` is explicitly passed by the calling project **and** CI is green **and**
  the reviewer's verdict passed. Historical `automatic_merge_allowed: false` values are not a
  founder-attention gate. `auto_merge_enabled` defaults to `"false"` - this is the real,
  current, evidenced activation state in every KARSIFT project checked against this repo as of this
  writing, not a cautious guess. Flipping it is a deliberate edit made after real evidence the
  loop is reliable, never a default. Automatic merges use the GitHub App token so their
  `pull_request: closed` event reaches adoption, and delete only the merged task/plan branch.

**Planner output is a draft, never an authoritative risk signal.** `plan.yml` lets
the planner role propose a `risk:` value in the change package it drafts, but that
proposal is exactly as authoritative as a human's first guess would be - nothing
more. The actual gate is unchanged: a human reviews and adopts (or rejects) the
draft, and once any task from it is implemented, this repo's own `merge-gate.yml`
still fails closed on any unparseable or under-declared risk, and the calling
project's own deterministic path-based classifier (if it has one, e.g.
vocanova-platform's `scripts/governance/classify-change-risk.sh`) still runs against
the real diff, same as for a human-drafted package. A planner-drafted `risk:` value
must never be treated as the ground truth on its own - that's the entire point of
keeping a path-based floor independent of anything an LLM declares about its own
proposal.

This mirrors a real pattern already adopted and active in at least one calling project
(`vocanova-platform`'s governance amendments): **governance permission and technical activation are
separate states.** A project can formally decide that R0-R2 releases may eventually auto-merge
without becoming true the moment that decision is written down - it becomes true only when this
gate's `auto_merge_enabled` is actually flipped for that project, with evidence. Don't represent a
capability as active just because policy permits it.

## Automated remediation

When the reviewer returns `FAIL`, **or CI itself fails outright before review ever runs** (`ci`
failing means `review` never gets a chance to produce a verdict at all, a real blind spot until this
was added), `remediate.yml` (wired into the caller template right after `review`) automatically
re-dispatches the implementer once, with the failure details - the reviewer's exact findings, or the
exact failed base/head plus an instruction to reproduce repository-controlled checks when there was
no review to draw from - included in the prompt as required reading, not a blind second guess. Raw
CI logs, artifacts, step output, and environment values are never copied into PR comments or model
context. It force-updates the same PR rather than opening a new one. On
that one retry, `implement.yml` escalates to a stronger model (`implementer_escalation` in
`config/roles.yml`) rather than reusing the same model that already failed once. If the retry also
fails, it stops and escalates to the authority issue instead of trying a third time - the same
two-attempt cap `implement.yml` already enforced for its own internal failures, now closing the gap
where an implementer *success* followed by a reviewer *FAIL* (or a plain CI failure) previously went
nowhere until a human happened to notice.

A declared operator-owned live-evidence task may instead receive the exact
machine-readable state `VERDICT: WAITING FOR OPERATOR LIVE EVIDENCE` when its
implementation is correct and the only missing acceptance proof is the declared
live Actions run. Merge remains fail-closed, but `remediate.yml` does not spend an
implementation retry on that state. It is also forbidden to tell the implementer
to edit unrelated workflows merely to manufacture the evidence.

The same ownership boundary applies when a current exact-SHA run has a genuine
review `FAIL` or CI failure. Before `remediate.yml` can dispatch the bounded
implementer retry, it reads the task's live-evidence contract and exact task
stanza from an immutable checkout of the caller head. A valid `operator` or
`live-actions` contract produces a sanitized, deduplicated operator escalation
instead of an implementation dispatch. Missing, malformed, unreadable, or
conflicting ownership metadata fails closed in the same lane. Ordinary tasks
(no contract and no ownership marker) retain the existing two-attempt behavior;
stale runs, waiting verdicts, and reviewer-infrastructure failures retain their
separate no-retry semantics.

Calling repositories may expose the read-only
`verify-remediate-operator-ownership.yml` reusable verifier for controlled live
proof. It checks the exact carrier head, source pipeline/PR lineage, successful
remediation decision, skipped retry jobs, and one trusted sanitized escalation
marker for that exact source run and head; markers from later source heads do
not make the earlier proof ambiguous. The controlled failing source head and the
later metadata-bearing carrier head are intentionally distinct: allowlisted
evidence binds the source run's immutable `head_sha` (not the mutable head in its
PR association), and GitHub's compare metadata must prove that source is an
ancestor of the exact dispatched carrier head. It reads Actions/PR metadata only
and never downloads logs or artifacts. Trusted marker authors include GitHub's
GraphQL-normalized built-in Actions identity as well as its REST-style bot login.

Before either task or plan review becomes an App-signed record, a bounded
normalizer removes full-line commit/task/package/issue/base binding lookalikes
from the model narrative. The clean workflow then supplies those authoritative
bindings exactly once and revalidates the final verdict plus live base/head pair.
Reviewer preambles or repeated metadata therefore cannot make a valid review
ambiguous, while duplicate or non-final verdicts still fail closed.

A `PASS`, `PASS WITH NON-BLOCKING FINDINGS`, waiting state, or no verdict yet
(with CI still green) are implementation-remediation no-ops. Only an explicit
implementation `FAIL` or CI failure can consume the bounded implementation
retry. A review-job error stays in the isolated reviewer-infrastructure lane.

The implementer job deliberately has no `actions` permission and receives no
general Actions inspection/dispatch credential. Operator reconciliation is a
separate repository-controlled responsibility; adding it must not broaden the
implementer's permissions or secrets.

On an eligible unchanged draft-to-ready transition, `ci.yml` still emits the
repository ruleset's required `ci / ci` check context, but runs only a named
exact-SHA reuse marker. Checkout and application validation remain skipped. This
small compatibility context is necessary because GitHub evaluates the newest
required check context; omitting the reusable CI caller changes its visible name
to `ci` and leaves `ci / ci` unsatisfied even when prior evidence is valid. The
post-transition verifier requires the marker to succeed and both checkout steps
plus the full validation step to be skipped. Merge-gate also requires exactly one current `ci / ci`
context in `SUCCESS`; prior evidence can never replace a skipped or ambiguous
current compatibility context.

The unrestricted implementer also never shares a runner with the GitHub App
token. It produces and uploads a Git bundle with persisted checkout credentials
disabled; a separate clean `publish` job downloads that artifact, mints a
repository-scoped contents/issues/pull-requests token, imports the exact
declared commit into a new bare repository, and pushes with hooks disabled plus
an explicit lease. Repository hooks, PATH changes, or tools left by model code
therefore cannot observe the App credential or forge its bot identity. The
publisher rejects every `.github/workflows/**` change before push; those
security-sensitive edits use a separately supervised review/publication path
instead of executing from an unreviewed same-repository PR.
All PR operations and bounded issue notifications in these clean jobs name the
calling repository explicitly, because they intentionally have no checked-out
Git worktree from which GitHub CLI could infer repository context.
Its model-facing job also has read-only issue and pull-request permissions;
no-change reporting is performed by a separate clean runner.

The unrestricted planner uses the same privilege boundary. Its runner has no
persisted checkout credential while the model is active and can only upload a
Git bundle or clarifying-question artifact. A fresh `publish-plan` runner
validates the exact bundle lineage and rejects any changed path outside the new
package directory before it receives a repository-scoped publishing token.

`live-evidence-reconcile.yml` implements that separate responsibility. Calling
repositories invoke it from an hourly schedule (or an explicit reconcile
dispatch), so it can poll any workflow named by a task contract without adding a
broad `workflow_run` trigger that recursively observes the pipeline itself. It:

- accepts only the contract at
  `<package>/.karsift/live-evidence/<task_id>.yaml` on a current waiting PR;
- reads Actions run and job metadata only — never logs, artifacts, steps, or
  arbitrary output;
- validates workflow identity, required successful jobs, event, branch, exact
  SHA lineage, conclusion, and age before qualifying evidence, paginating a
  bounded maximum of 1,000 candidates and failing closed beyond that bound;
- can dispatch only the workflow/ref/inputs declared by that contract, and
  only when the target ref is protected and the workflow file is byte-identical
  to the protected default-branch copy (the caller pipeline itself is always
  forbidden).
  A trusted App-authored reservation precedes the single API attempt, so an
  uncertain outcome cannot be retried into a duplicate dispatch. The PR's
  head/ref, body-bound package/task/authority issue, latest trusted WAITING
  verdict, 72-hour deadline, and immutable target/default branch snapshots are
  re-read both before reservation and immediately before the dispatch API call;
- serializes per calling repository, records one allowlisted
  `<task_id>.result.json`, and updates the PR ref without force; and
- emits one timeout escalation after 72 hours without invoking implementation
  remediation, Sentry, or the operational-failure observer.

The result commit changes the PR head. The calling project's normal
`pull_request: synchronize` path therefore runs CI and independent review again,
bound to the post-reconcile SHA; a PASS from the older waiting head cannot carry
forward. Result-commit, ref, and trusted-comment mutations use a short-lived
installation token from the KARSIFT GitHub App. Declared workflow dispatch alone
uses the dedicated reconcile job's `GITHUB_TOKEN` with `actions: write`; other
caller jobs retain their existing permission floor, and the implementer receives
neither token. The App token is repository-scoped and requests only contents,
issues, and pull-request write permissions: contents publishes the result
commit, issues handles task/timeout comments, and pull requests publishes the
exact-head attestation on the waiting PR conversation. It uses an immutable
post-fix `create-github-app-token` revision whose parser honors the
`permission-*` inputs. Waiting is accepted only
when the successful check resolves to the exact PR, head, branch, active caller
pipeline workflow ID, and an unchanged head/base pipeline file; the comment
timestamp must also fall inside that check's run window. Its trusted comment
is signed by the dedicated GitHub App from an isolated `publish-review` job and
binds the package path, task ID, and authority issue observed by that review, so
neither another `github-actions[bot]` workflow nor a later PR-body edit can
retarget authorization. The post-reconcile
review requires a trusted App-authored attestation bound to its new exact head,
and that attestation is posted before the branch advances to prevent a fast
`synchronize` review from racing it.

Merge, remediation, and plan adoption apply the same identity rule: only an
App-signed, exact-head verdict with its successful isolated publisher check is
accepted. Plan review has its own clean `publish-plan-review` stage, so neither
review model can impersonate the credential that records its decision.
The caller template wires that review for every `plan/` PR and makes merge-gate
wait for it explicitly.

For `pull_request` and `pull_request_target` evidence, the run's GitHub PR
association must include the waiting PR number; matching workflow, branch, and
SHA alone is insufficient. The read-only workflow token explicitly grants
Actions, Checks, contents, issues, and pull-request metadata access so these
provenance checks work in private repositories.
Only open, unmerged PRs can reconcile or dispatch, and dispatch authority
expires at the same 72-hour waiting deadline. Workflow names and every
App-authored structured comment field are restricted to single-line safe
scalars; result attestation requires an exact line rather than a substring.

Caller pipelines pass the triggering PR base/head into plan review, task review,
remediation, and merge-gate. A newer push makes older runs stale: reviewer model
work is skipped, remediation cannot target the newer head, and merge uses GitHub
CLI's atomic `--match-head-commit` guard. The caller template also cancels
superseded pull-request runs to avoid duplicate model cost; the exact-SHA guards
remain the correctness boundary when cancellation races or is unavailable.

**Ready-for-review reuse (VOC-104):** when a draft PR becomes ready on an unchanged exact
base/head that already has green required checks and a trusted App-signed PASS verdict, the
caller's read-only `ready-for-review-reuse.yml` job may emit `reuse-evidence` so the current
`ready_for_review` run skips full CI and model review while merge-gate still re-evaluates using
the validated prior pipeline run. Any failed reuse precondition, helper uncertainty, or
non-`ready_for_review` activity selects the normal full CI and review path
(`full-path` or `fail-closed-to-full-path`). Draft PRs remain non-mergeable regardless of reuse.
Only App-signed publisher comments qualify; human or implementer text never authorizes reuse.
A qualifying publisher record binds its exact pipeline run ID as well as the base/head and
package/task identity, preventing evidence from different base revisions from being combined.
Reuse also requires GitHub's authenticated `referenced_workflows` metadata to show that the
eligibility helper, CI, task review, plan review, and merge gate all resolved to one identical
shared-infrastructure commit in both runs. A mutable `@main` policy change therefore forces the
full path even when the application base/head did not change. Merge-gate repeats this revision
comparison independently before it can authorize merge.
A separate read-only `verify-ready-for-review-reuse.yml` workflow validates controlled live proof
from allowlisted Actions metadata only. Its evidence-carrier head is intentionally distinct from
the earlier observed ready-transition head: explicit dispatch inputs bind the carrier SHA, while
the source PR number/base/head/ref bind both source runs. The ready run must also contain the
workflow-controlled `decide (ready_for_review)` job marker. If GitHub has cleared a closed run's
`pull_requests` array, the verifier never infers ownership from a matching branch/head alone. A
prior run is admitted only when an App-authored review comment on that exact PR binds its run ID,
base/head, and package/task identity. Before optimized auto-merge, merge-gate also publishes one
App-authored transition attestation binding repository, PR number, branch, base/head, ready run,
selected prior run, and shared-policy SHA; the post-merge verifier requires that unique record.
The verifier also recomputes the latest eligible prior run strictly before the ready run and
requires it to equal the declared prior run ID, so proof cannot substitute a different valid run.

Callers must pass those exact base/head inputs to plan review, task review,
remediation, and merge-gate. Every reusable-workflow schema keeps them optional
so a shared-infrastructure rollout does not prevent an older default-branch
caller workflow from starting; an omitted or invalid value fails closed at
runtime by refusing plan review, skipping task review, refusing remediation, and
leaving merge pending instead of silently falling back to a live head.
A remediation retry carries the failed head into `implement.yml`, validates it
before model work, revalidates it immediately before publishing, and uses an
explicit SHA-valued force-with-lease. A commit arriving in either timing window
therefore survives instead of being overwritten by the stale retry.
Adoption similarly removes its merged roster ref through an exact-SHA lease;
if native merged-branch cleanup wins the deletion race, an authoritative
absence recheck is accepted, while any surviving ref still stops dispatch.
The implementer records the pre-model task tip and the fetched integration tip
as separate boundaries. It soft-resets only to the former, but its recovery
bundle spans from the integration anchor through the complete task lineage.
The isolated publisher fetches only that trusted integration history, verifies
and imports the self-contained task bundle, scans the whole lineage for denied
workflow changes, and then applies the exact old-head lease. This also covers a
remediation branch whose prior commits were locally rebased and therefore never
existed on the remote under their new commit IDs.

Reviewer responses are shape-validated inside the bounded transient-retry loop.
A provider process that exits successfully but omits its documented non-empty
result is retried as reviewer infrastructure, with sanitized diagnostics only.
If all reviewer attempts fail, remediation records bounded Actions metadata but
does not dispatch the implementer or consume its one code-remediation attempt.
Only a genuine App-signed FAIL verdict or failed CI can start implementation.

## Ordered autonomous task execution

Adoption starts the first task automatically. The adopted roster records an explicit
`depends_on` edge from every later task to its predecessor. `auto-advance.yml` releases the next
task only after one valid App-authored completion marker proves the preceding task's exact reviewed
caller PR merged. The issue-close event is only a wake-up hint; closed state alone cannot advance.
For an ordinary next task, it preserves the existing deterministic-branch guard and dispatches
`implement.yml` attempt 1. For a task with a valid operator/live-actions contract at
`<package>/.karsift/live-evidence/<task_id>.yaml`, it does not execute the general implementer:
a separate clean App-scoped job creates or repairs one deterministic draft evidence-carrier PR
and one sanitized waiting marker. The task's own `tasks.md` stanza may declare the exact secondary
expectation marker `- Automation ownership: operator` or
`- Automation ownership: live-actions`; a missing required contract, malformed contract, invalid
or duplicate marker, or marker/contract conflict fails closed. Narrative prose is never parsed as
ownership. “No marker” means the task stanza was successfully read and contains no ownership
marker; a missing or unreadable `tasks.md` cannot establish that condition and therefore fails
closed instead of guessing that the task is ordinary. Re-entry repairs a partially published
carrier without overwriting an existing evidence file. When an adopted plan already contains a
strictly identity-bound, incomplete `source_run_id: pending` evidence stub on the protected
integration branch, the first carrier publication normalizes that stub to the canonical pending
body; completed, mismatched, ambiguous, or oversized content still fails closed. Implementer jobs
remain serialized per change package.

Carrier PRs inherit their `Risk classification: R#` line from the adopted package's root
`change.yaml`. The publisher and verifier accept exactly one root `risk: R0` through `risk: R4`
declaration and fail closed when it is absent, duplicated, or malformed; they do not hardcode R4
for lower-risk operator-owned packages.

If the deterministic carrier branch already belongs to a closed or merged PR, publication fails
closed with `conflicting_existing_pr`. The publisher never silently reopens or replaces that PR:
an operator must first confirm why it was closed and then restore the trusted draft or perform a
new governed cleanup/retry. This keeps an intentional abandonment or completed history from being
mistaken for permission to continue.

The ownership classifier is read-only. Only the non-model carrier publisher receives an App token
for contents/issues/pull-request writes, and the fail-closed notifier receives issue-write only.
Neither receives Actions-write or model credentials. A separate read-only
`verify-auto-advance-live-evidence.yml` workflow validates the later controlled source-run/carrier
proof from Actions, issue, and PR metadata only—never logs or artifacts.

`implement.yml` enforces the same ordering independently, including for direct
`workflow_dispatch` calls: the dispatched task and issue must match the adopted roster, every
dependency issue must be closed, the dependency's bot PR must be merged, and that merge must be an
ancestor of the integration branch checked out for the new task. This makes a manual or duplicated
dispatch fail before a branch or PR is created instead of allowing dependent tasks to race.

Remediation attempts fetch and rebase onto the current integration branch before the implementer
runs. If upstream changes make the old branch conflict, the workflow preserves the old revision as
a remote reference and restarts the retry from the current integration tip, with that fact included
in the implementer prompt. Stale sibling-task state therefore cannot silently consume the final
attempt.

## Drafting and issue-creation are two separate steps

`plan.yml` only ever drafts a package and opens a PR for it - it does not open any tracking issues.
Those come from `adopt.yml`, which fires after a `plan/`-branch PR merges and re-verifies that the
independent PASS verdict is bound to the exact merged head. It writes adoption metadata and the task
roster together through a checked bookkeeping PR. A caller can dispatch the same merged plan PR to
reconcile a missed event; task issue lookup and the roster commit are idempotent.
Adoption is serialized per plan authority. After the checked roster PR is
confirmed merged, its ephemeral `karsift/roster-*` head is deleted only through
an exact-SHA lease; a queued-but-not-merged PR or concurrently advanced ref is
never deleted. Caller repositories should also enable GitHub's native
`delete_branch_on_merge` setting as a low-cost second guard; protected
integration and production branches are not eligible for that deletion.

**Anyone - a human, or another agent - can start this by opening an issue,** not just by dispatching
`plan.yml` by hand. The calling project's `pipeline.yml` routes any newly-opened issue with no
`karsift:*` label into `plan.yml` with that issue's number; the planner drafts from the issue's full
thread (body plus every comment so far). If that's not enough to draft from - a bare "this is broken"
with no repro, say, whether from a human or from an automated log-reading agent that noticed
something wrong - the planner posts a clarifying question back on the issue instead of guessing, and
labels it `karsift:needs-info`. A reply (from whoever or whatever opened the issue) re-triggers
planning with the updated thread - no manual re-dispatch needed either way. The one thing this never
does is skip adoption: however planning started, the resulting package is still only ever a draft
until a human reviews and merges it.

## Release gate: checked automatic promotion per completed change package

`merge-gate.yml` gates each *task*; `release.yml` gates the layer above it - promoting a project's
integration branch (e.g. `develop`) to its production branch (e.g. `main`) once an entire *package*
is done. Package completion means every roster entry has one valid App-authored marker bound to a
live merged caller PR at its reviewed head. Those markers plus newest green promotion-PR checks are
the gate; issue state and founder comments are not release authority.

A package's task roster is fixed once, at adoption time: `adopt.yml` writes
`<package_path>/.karsift/tasks.json`
(`[{"task_id": ..., "issue": <number>, "depends_on": [...]}, ...]`) once it opens the
per-task issues. `release.yml` never re-parses a project's own `tasks.md` prose to determine
completion - that was tried for issue-opening itself and broke against a real house-style mismatch
(see `adopt.yml`'s task-parser comments, carried over from where this logic used to live in
`plan.yml`); the roster file is the sole source of truth instead. Each
task's tracking issue receives one strict App-authored completion marker only after
`merge-gate.yml` observes its exact caller PR merged. The gate then emits the
issue-close event; issue state by itself is never completion authority. Both
`auto-advance.yml` and `release.yml` validate the same marker against the live
caller PR, so a foreign closing reference or premature manual close is a safe
no-op. Cross-repository PRs use `Relates to OWNER/CALLER#N`; only the caller task
PR uses local `Closes #N`.

Completion identity is parsed from a fresh REST read of the merged caller PR,
not from the workflow event's original body snapshot. If an authorized body
correction happens after the run starts, rerunning the failed completion job
therefore sees the correction. The adapter still requires exactly one matching
task, package, and local issue binding, then binds that identity to both the
immutable adopted roster at the exact reviewed base and the newest App-signed
exact-base/head PASS review before any issue mutation. Conflicting evidence
fails closed. A retry after the exact App marker
and a timeline-proven later issue-close wake-up makes no further mutation; if the
marker was published after an earlier automatic close, the retry safely emits
the missing post-marker close wake-up.

When every roster marker validates, `release.yml` opens a `Release: <change_id>`
audit issue and automatically opens (or reuses) and merges a real
`develop → main` pull request - never a direct ref update, since a project's own
branch-protection intent (e.g. vocanova-platform's "release pull requests only, no direct or force
pushes") depends on promotion staying a real, reviewable PR. Adoption, merge/reuse,
and release select the newest authoritative attempt for each logical exact-SHA
gate, so an obsolete failure cannot poison a later pass and a newer failure cannot
hide behind an older pass. Automatic, reconcile, and terminal-check wake-ups share
one per-package concurrency group and final merge path. A terminal check completion
wakes only that cheap evaluator; it does not rerun unchanged-SHA CI or the reviewer.
If an attempt is interrupted, the caller can dispatch `reconcile-release` with the
audit issue number; reconciliation remains idempotent and fail-closed.

**Deploy is explicitly out of scope.** The promotion PR's merge is the entire scope of `release.yml`
- no hosted deployment is triggered by anything in this repo today.

Packages that predate `.karsift/tasks.json` (planned before this feature existed, or never
planner-authored) aren't covered - the release gate only applies going forward.

## What's deliberately not built yet

- A run-time-swappable reviewer/planner *execution step* (today, swapping the model within a
  provider is config-driven; swapping to a different provider's CLI/action - as happened 2026-07-24,
  Claude Code CLI to `openai/codex-action`, for both `review.yml` and `plan.yml` - is still a workflow
  edit, not just a config edit)
- Per-project custom risk-classification schemes beyond the `Risk classification: R#` convention
  `merge-gate.yml` parses
- Writing verification verdicts back into a package's own machine-readable status (the reviewer has
  no write authority; a human or a later deterministic step does this today)
- A durable work queue, staged/production deployment, or anything past PR-merge into a project's
  integration branch
- A real-time/synchronous chat interface - "anyone can open an issue and reply to the planner's
  questions" (see above) covers the same ground asynchronously, through GitHub's own issue/comment
  events, but there is no live conversational session
- Any deploy trigger after a `release.yml` promotion merges - `main` gets updated, nothing hosted
  does

## Layout

```
karsift-ai-infra/
  config/
    roles.yml             # the only file naming a specific model/vendor
    resolve-model.sh
  prompts/
    plan.md                # planner role instructions - draft only, never adopts/authorizes
    implement.md           # implementer role instructions - scope discipline, no self-approval
    review.md              # reviewer role instructions - read-only, structured verdict
  .github/workflows/
    ci.yml                 # generic pnpm checks, once a project's app foundation adds them
    plan.yml                # drafts a DRAFT package from free text, a document, or an issue thread
    adopt.yml                # opens per-task issues only once a plan/ PR is actually adopted+merged
    implement.yml
    review.yml
    remediate.yml           # re-dispatches implement.yml once on a FAIL verdict, then escalates
    merge-gate.yml          # risk-aware, fails closed, auto_merge_enabled defaults false
    release.yml              # marker-validated serialized package promotion, gates develop -> main
  templates/project-repo/
    .github/workflows/pipeline.yml   # thin caller template - copy into a project repo
```
