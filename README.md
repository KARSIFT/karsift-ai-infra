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
CI job's own failure log when there was no review to draw from - included in the prompt as required
reading, not a blind second guess. It force-updates the same PR rather than opening a new one. On
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

A `PASS`, `PASS WITH NON-BLOCKING FINDINGS`, waiting state, or no verdict yet
(with CI still green) are remediation no-ops. Only an explicit implementation
`FAIL`, CI failure, or review-job error can consume the bounded retry.

The implementer job deliberately has no `actions` permission and receives no
general Actions inspection/dispatch credential. Operator reconciliation is a
separate repository-controlled responsibility; adding it must not broaden the
implementer's permissions or secrets.

Caller pipelines pass the triggering PR head into review, remediation, and
merge-gate. A newer push makes older runs stale: reviewer model work is skipped,
remediation cannot target the newer head, and merge uses GitHub CLI's atomic
`--match-head-commit` guard. The caller template also cancels superseded
pull-request runs to avoid duplicate model cost; the exact-SHA guards remain the
correctness boundary when cancellation races or is unavailable.

Those exact-head inputs are mandatory on review, remediation, and merge-gate,
so an older caller fails closed instead of silently falling back to a live head.
A remediation retry carries the failed head into `implement.yml`, validates it
before model work, revalidates it immediately before publishing, and uses an
explicit SHA-valued force-with-lease. A commit arriving in either timing window
therefore survives instead of being overwritten by the stale retry.

## Ordered autonomous task execution

Adoption starts the first task automatically. The adopted roster records an explicit
`depends_on` edge from every later task to its predecessor, and `auto-advance.yml` releases the
next task only after the preceding task's implementation PR merges and its tracking issue closes.
Implementer jobs are serialized per change package.

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
is done. Completion plus green promotion-PR checks is the gate; founder comments are not release
authority.

A package's task roster is fixed once, at adoption time: `adopt.yml` writes
`<package_path>/.karsift/tasks.json`
(`[{"task_id": ..., "issue": <number>, "depends_on": [...]}, ...]`) once it opens the
per-task issues. `release.yml` never re-parses a project's own `tasks.md` prose to determine
completion - that was tried for issue-opening itself and broke against a real house-style mismatch
(see `adopt.yml`'s task-parser comments, carried over from where this logic used to live in
`plan.yml`); the roster file is the sole source of truth instead. Each
task's tracking issue is explicitly closed by `merge-gate.yml` when that task's PR merges (not left
to GitHub's native "Closes #N" auto-close, which has been observed live not to fire reliably on a
squash merge). The moment every issue in a package's roster is closed, `release.yml` opens a
`Release: <change_id>` audit issue and automatically opens (or reuses) and merges a real
`develop → main` pull request - never a direct ref update, since a project's own
branch-protection intent (e.g. vocanova-platform's "release pull requests only, no direct or force
pushes") depends on promotion staying a real, reviewable PR. If that attempt is interrupted, the
caller can dispatch `reconcile-release` with the audit issue number; the retry remains idempotent
and fail-closed on checks.

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
    release.yml              # one human approval per completed package, gates develop -> main
  templates/project-repo/
    .github/workflows/pipeline.yml   # thin caller template - copy into a project repo
```
