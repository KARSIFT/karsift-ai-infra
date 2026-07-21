# karsift-ai-infra

Reusable GitHub Actions automation for the loop: **implement an approved change → deterministic CI
checks it → an independent reviewer verifies it → a human (or, once earned, a proven automated gate)
merges it.** Formerly `vocanova-ai-infra` - renamed because the pipeline itself was never
Vocanova-specific; only the project wiring was. Any KARSIFT project can call these workflows.

## Roles are technology-agnostic

Every AI step in this pipeline is a **role**, not a vendor commitment:

| Role | What it does | Current occupant |
|---|---|---|
| `implementer` | Implements one approved task on a branch. No merge authority, no production access, cannot approve its own work. | Claude Code CLI (was `openai/codex-action` - see compromise note below) |
| `reviewer` | Independent, read-only verification. Posts a structured, commit-bound verdict. Never edits, merges, or approves. | Claude Code CLI |

**The only file that names a specific model or vendor is `config/roles.yml`.** Swapping either role
to a different model/provider means editing that one file plus the relevant workflow's execution
step (`implement.yml`'s "Run implementer" step, or `review.yml`'s install/run step). Nothing else in
this repo, and nothing in a calling project's own workflow, should need to change.

**The two roles are supposed to stay different vendors** - independent review that shares a vendor
with the implementer isn't independent, it's self-review. **That principle is currently violated,
on purpose and temporarily**, not silently: `openai/codex-action` requires a metered, billed OpenAI
API key with no subscription-auth equivalent to Claude Code's `setup-token`, and that billing isn't
set up. Until it is, both roles run on Claude Code - different models (`claude-sonnet-5` implementing,
`claude-opus-4-8` reviewing) as a partial mitigation, not a substitute for real cross-vendor
independence. `implement.yml`'s header comment has the exact revert steps once Codex billing is
available. Don't treat a `PASS` verdict from this configuration as equivalent to a genuinely
independent review - it isn't one yet.

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
- **Independent review**: `review.yml` runs the reviewer role with **read-only** tools only. It can
  read the diff and the package and post one comment - nothing else. Findings are Critical / High /
  Medium / Low; the verdict is one of `PASS`, `PASS WITH NON-BLOCKING FINDINGS`, or `FAIL`, bound to
  the exact reviewed commit SHA.
- **Merge authority**: `merge-gate.yml` is risk-aware and **fails closed**. It reads a
  `Risk classification: R#` line from the PR body (any project can use a different risk scheme, but
  this is the convention the gate parses today); a PR with no parseable risk declaration, or declared
  `R4`, never auto-merges regardless of any switch - both require a human's literal `approved`
  comment from the project's configured founder identity. R0-R3 can auto-merge only when
  `auto_merge_enabled: "true"` is explicitly passed by the calling project **and** CI is green **and**
  the reviewer's verdict passed. `auto_merge_enabled` defaults to `"false"` - this is the real,
  current, evidenced activation state in every KARSIFT project checked against this repo as of this
  writing, not a cautious guess. Flipping it is a deliberate future edit made after real evidence the
  loop is reliable, never a default.

This mirrors a real pattern already adopted and active in at least one calling project
(`vocanova-platform`'s governance amendments): **governance permission and technical activation are
separate states.** A project can formally decide that R0-R2 releases may eventually auto-merge
without becoming true the moment that decision is written down - it becomes true only when this
gate's `auto_merge_enabled` is actually flipped for that project, with evidence. Don't represent a
capability as active just because policy permits it.

## What's deliberately not built yet

- A run-time-swappable reviewer *execution step* (today, swapping the model is config-driven;
  swapping the reviewer to a non-Claude-Code CLI/action is a workflow edit, not just a config edit)
- Per-project custom risk-classification schemes beyond the `Risk classification: R#` convention
  `merge-gate.yml` parses
- Writing verification verdicts back into a package's own machine-readable status (the reviewer has
  no write authority; a human or a later deterministic step does this today)
- A durable work queue, staged/production deployment, or anything past PR-merge into a project's
  integration branch

## Layout

```
karsift-ai-infra/
  config/
    roles.yml             # the only file naming a specific model/vendor
    resolve-model.sh
  prompts/
    implement.md           # implementer role instructions - scope discipline, no self-approval
    review.md              # reviewer role instructions - read-only, structured verdict
  .github/workflows/
    ci.yml                 # generic pnpm checks, once a project's app foundation adds them
    implement.yml
    review.yml
    merge-gate.yml          # risk-aware, fails closed, auto_merge_enabled defaults false
  templates/project-repo/
    .github/workflows/pipeline.yml   # thin caller template - copy into a project repo
```
