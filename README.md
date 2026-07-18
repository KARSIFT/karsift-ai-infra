# vocanova-ai-infra

Reusable automation implementing the near-term slice of
[vocanova-platform's autonomous-development roadmap (DOC-18)](https://github.com/KARSIFT/vocanova-platform/blob/develop/docs/planning/18-autonomous-development-implementation-roadmap.md):
Codex implements an approved `VOC-###` task, deterministic CI checks it, Claude Code
independently verifies it, and a human (the founder) is the only merge authority —
for every risk class, until the loop has earned more autonomy.

This is deliberately **not** the full Control Plane DOC-18 describes (no Postgres,
no durable work queue, no ChatGPT founder interface, no AI Budget Governor). It is
the smallest slice DOC-18 itself recommends building first: a working, evidenced,
human-gated loop from an approved change package to a verified PR. Everything past
that — the queue, the budget governor, RL1/RL2 production autonomy — is real future
work, not simulated here.

## Why this exists as a separate repo

Same reasoning as [KARSIFT/ai-infra](https://github.com/KARSIFT/ai-infra): reusable
GitHub Actions workflows belong in one place editable independent of any project
repo. `vocanova-platform` gets a thin `pipeline.yml` that calls into this repo -
nothing project-specific lives outside `vocanova-platform`'s own `registry`-equivalent
config (see `config/roles.yml` for the model mapping).

## Governance is not optional here

Everything in this repo exists to satisfy constraints already ratified in
`vocanova-platform`'s own governance documents - it does not invent new rules:

- **`AGENTS.md`** (vocanova-platform): meaningful implementation requires an approved
  `VOC-###` change package with stable requirements and acceptance criteria. A chat
  prompt or bare issue is not implementation authority. This repo's `codex-implement.yml`
  refuses to run without a `voc_id` pointing at an adopted package.
- **`CLAUDE.md`** (vocanova-platform): Claude Code is the independent verifier only -
  it cannot approve its own correction, self-approve, merge, or hold repository-write,
  deployment, secret, founder, or technical-steward authority. `claude-verify.yml`
  runs Claude in a **read-only** tool configuration for exactly this reason - it can
  read the diff and the package, and it can post a comment, and that is all.
  Independent findings are Critical / High / Medium / Low; open Critical or High
  findings block; the verdict is one of `PASS`, `PASS WITH NON-BLOCKING FINDINGS`,
  or `FAIL`, bound to the exact reviewed commit SHA.
- **`docs/governance/approval-matrix.md`** and **`change-risk-classification.md`**:
  R0-R4 risk classes, and under active A-003, routine R3 doesn't get standing
  founder/steward approval just for being R3 - but **automation permission is
  separately gated: "only where separately implemented and proven."** Nothing here
  has been proven yet. `merge-gate.yml` therefore never auto-merges anything, at any
  risk class, by default - it posts what it *would* do (`WOULD AUTO-MERGE (shadow
  mode)`) and waits for the founder's literal `approved` reply, same mechanism as
  every other project on this account. Flipping shadow mode off - and note it's an
  all-or-nothing switch today, not per risk class - is the `shadow_mode` input on
  `merge-gate.yml`, changed deliberately once there's real evidence the loop is
  reliable, not a default.

## Roles, mapped to DOC-16's table

| DOC-16 role | Who/what | Authority here |
|---|---|---|
| Codex | `openai/codex-action` (metered `OPENAI_API_KEY`) | Implements an approved task on a branch. No merge authority, no production access. |
| Claude Code | `claude` CLI, read-only tools | Independent verification only. Posts findings; never edits, merges, or approves. |
| GitHub Actions | This repo's workflows | Deterministic checks, gating, evidence - no product/business decisions. |
| Founder | `@m-e-h-r-d-a-a-d` (from `vocanova-platform`'s `approval-matrix.md`) | The only merge authority, for every risk class, until shadow mode is explicitly disabled for a proven class. |

## What's deliberately not built yet

- Automatic merge at any risk class (shadow mode only)
- Writing verification verdicts back into a package's `change.yaml` (Claude has no
  write authority; a human or a later deterministic step does this today)
- The Control Plane, durable queue, AI Budget Governor, provider-health/fallback
  logic, staging/production deploy, and everything in DOC-18 Phase 10 onward

## Layout

```
vocanova-ai-infra/
  config/
    roles.yml            # codex / claude model defaults, per DOC-18 §7 policy-baseline intent
    resolve-model.sh
  prompts/
    codex-implement.md   # builder instructions - scope discipline, no self-approval
    claude-verify.md      # mirrors vocanova-platform's CLAUDE.md review process exactly
  .github/workflows/
    ci.yml                # generic pnpm checks, once the app foundation adds them
    codex-implement.yml
    claude-verify.yml
    merge-gate.yml         # shadow mode by default
  templates/project-repo/
    .github/workflows/pipeline.yml   # thin caller template
```
