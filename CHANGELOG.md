# Changelog

## Unreleased

- Added `live-evidence-reconcile.yml`, a serialized, App-authenticated operator
  path that validates declared Actions metadata, records one sanitized result
  commit, forces fresh exact-SHA review, optionally dispatches only declared
  workflow inputs, and escalates one 72-hour timeout. The caller template polls
  hourly instead of using a recursive catch-all `workflow_run` trigger.
- Added dependency-free strict contract parsing and deterministic rejection,
  sanitization, lineage, staleness, timeout, dispatch, and deduplication tests.

- Added an exact-SHA `WAITING FOR OPERATOR LIVE EVIDENCE` review lifecycle:
  merge stays fail-closed, remediation does not spend an implementation retry,
  and genuine implementation/CI/reviewer failures retain their bounded retry.
  Superseded PR runs now cancel, stale runs cannot review/remediate a newer head,
  remediation retries enforce the failed head before model work and again with
  an explicit SHA-valued push lease, and merge atomically requires the head SHA
  whose gate passed. Callers must pass exact-head inputs; their reusable schema
  remains rollout-compatible, while omissions fail closed at runtime.
  Pull-request `closed` events no longer cancel the source run that merged them.
- Removed founder-comment merge authority: R0-R4 now share the same CI and
  exact-revision independent-verification gate when autonomous merge is enabled.
- Fixed plan adoption handoff by merging with the GitHub App token, autonomously
  recording adoption in the roster PR, and adding idempotent reconciliation for
  missed `pull_request: closed` events.
- Removed founder-comment release authority. Completed rosters promote through a
  checked PR automatically, with `reconcile-release` as the idempotent retry path.

Execution-mechanism history for `implement.yml`, `plan.yml`, and `review.yml` -
which CLI/action each role's "Run ..." step actually invoked, why it changed,
and what broke along the way. This is the *how it's invoked* history; for
*which model/vendor fills each role and why*, see `config/roles.yml`'s own
header comment, which is the single source of truth for that and is kept
current there, not duplicated here. Each workflow file itself now carries
only a short "current state" summary + a pointer to the relevant section
below - read this file for the full reasoning behind why the file looks the
way it does.

Extracted from those three files' own header/inline comments on 2026-08-08
(see that commit for exactly what moved) so the files themselves stay
readable as *current* documentation rather than an ever-growing narrative of
every past state.

## implement.yml

- **2026-07-23, restored to `openai/codex-action`**: once OpenAI API billing
  became available again, after an earlier same-vendor compromise (Claude
  Code CLI for both implementer and reviewer) that this replaced.
- **2026-07-24, same-vendor compromise (again)**: the Anthropic Console org
  behind `ANTHROPIC_API_KEY` was disabled (billing/account issue, not a
  karsift-ai-infra bug), with no fallback Claude access available, so
  reviewer/planner also moved to `openai/codex-action` - see `review.yml`'s
  own changelog section below for that side.
- **2026-07-25, mixed-vendor pilot, superseded 2026-07-26**: `implementer`
  moved to OpenCode (`opencode-go/kimi-k2.7-code`), after strong review-role
  evidence for that model (see `review.yml`'s 2026-07-25 entry below) -
  founder made the call anyway, on the theory review/analysis quality was a
  reasonable proxy for implementation quality; watched real CI/review pass
  rates to confirm. `implementer_escalation` deliberately stayed on
  `openai/codex-action` (`gpt-5.6-sol`) at first, as a genuinely different,
  strong fallback for the one retry that matters most - traded away
  2026-07-26 ("no more quota in codex") when it moved to `opencode-go/glm-5.2`,
  collapsing the file to a single real execution step. The `openai/codex-action`
  step was disabled (`if: false`), not deleted, kept as a one-line revert path.
- **2026-07-31, moved to Cursor**: the OpenCode Go account behind
  `OPENCODE_API_KEY` looked exhausted/degraded across every model tried the
  same day (see `review.yml`'s 2026-07-31 entry below for the specific
  live-evaluation evidence that surfaced this). Founder directive: move every
  role - implementer, implementer_escalation, reviewer, planner - to Cursor
  (a pre-existing Pro+ subscription), a different cost-effective model per
  role rather than one model everywhere. Base implementer and escalation now
  run through a single "Run implementer (cursor-agent)" step - only
  `config/roles.yml`'s `implementer` vs `implementer_escalation` values
  differ, not the CLI. The opencode and claude-code-escalation steps were
  disabled (`if: false`), not deleted, for the same one-line-revert reason.
- Every claim about the Cursor CLI's real flags/behavior in the current file
  was verified against the actually-installed CLI in a disposable sandbox,
  not assumed from fetched docs - see `review.yml`'s 2026-07-31 entry below
  for the specific checks (workspace-trust blocking, `--mode plan`'s real
  read-only guarantee, stdin prompt input avoiding the `execve()`
  argument-length limit, the single-JSON-object `--output-format json`
  shape) that were verified once and apply to every Cursor-CLI execution
  step across all three files.

## plan.yml

- **2026-07-24, same-vendor compromise**: rewritten from Claude Code CLI to
  `openai/codex-action` for the same Anthropic Console org outage described
  in `implement.yml`'s 2026-07-24 entry above - lower-stakes here since
  planner has no cross-vendor independence requirement the way
  implementer/reviewer do (planner output is a draft a human reviews, never
  something an independent AI verifier checks).
- **2026-07-25, OpenCode pilot**: rewritten a second time to a raw `opencode`
  CLI invocation (`opencode-go/qwen3.7-max`, later `glm-5.2` same day per
  founder follow-up - see `config/roles.yml` for the full model history).
  Uses OpenCode's built-in `build` agent (full read/write, same as
  `implement.yml`) since it needs to create real files, not the restricted
  read-only `reviewer` agent `review.yml` defines.
- **2026-07-26, misdiagnosed then corrected**: `glm-5.2` hung with zero
  progress dispatching a real package; briefly swapped to `kimi-k2.7-code`
  on the theory this was model-specific, but that hung identically. Root
  cause was neither model - the `OPENCODE_API_KEY` account's weekly OpenCode
  Go quota had run out, a hard cap that blocks the CLI indefinitely instead
  of returning a clean error (also why the workflow's own quota-fallback
  string-matching never caught it). Fixed at the credentials layer, not the
  model layer.
- **2026-07-26, restored to Claude Code**: founder follow-up same day ("use
  claude code as a planner, I remember we used to use it"). Rewritten back
  from `opencode run` to the Claude Code CLI (prompt via stdin, not `-p` as a
  CLI argument - avoids the same argv-length class of bug `review.yml`'s
  2026-07-31 entry documents for Cursor).
- **2026-07-31, moved to Cursor**: same founder consolidation directive as
  `implement.yml`'s 2026-07-31 entry - planner itself wasn't on OpenCode at
  the time (it was on Claude Code), so wasn't directly affected by that
  day's OpenCode-account incident, but moved anyway as part of the same
  "everything through Cursor" decision.

## review.yml

- **2026-07-24, same-vendor compromise**: rewritten from Claude Code CLI to
  `openai/codex-action` - same Anthropic Console org outage as
  `implement.yml`'s entry above. Explicit note at the time to revert once
  Claude access was restored (later superseded by the 2026-07-25 OpenCode
  pilot below, not reverted to Claude).
- **2026-07-25, OpenCode pilot, reviewer-only**: rewritten a second time from
  `openai/codex-action` to a raw `opencode` CLI invocation. Founder's
  explicit reason: cost - an existing OpenCode Go subscription ($10/month)
  unlocks `opencode-go/`-prefixed models without OpenAI's per-token pricing.
  No official reusable Action exists for one-shot `workflow_call` dispatch
  (OpenCode's own GitHub Action is built for interactive PR-comment
  mentions), so a raw CLI invocation was used instead.
  - Found live: `opencode run` (v1.18.5 at the time) has no `--permissions`
    or `--quiet` flag - an earlier version of this step assumed both existed
    (from an unreliable fetched-docs source - see `config/roles.yml`'s
    correction note about a fabricated pricing table from the same
    research pass) and every run hard-failed immediately. Real read-only
    enforcement ended up agent-based: a `reviewer` agent defined in a
    generated `opencode.jsonc` with explicit per-permission-key
    allow/deny, the OpenCode equivalent of `codex-action`'s `:read-only`
    profile.
  - This pilot was reviewer-only by explicit founder instruction -
    implementer/planner did not follow until later (see their own entries).
- **2026-07-31, moved to Cursor**: the OpenCode Go account behind
  `OPENCODE_API_KEY` looked exhausted/degraded live - every model on the
  account (including this role's own `opencode-go/deepseek-v4-pro`) either
  timed out or errored in a same-day bounded probe (VOC-032-T10's
  live-evaluation evidence), and the review job on a real PR was stuck ~30
  minutes with zero verdict as a direct result, not a one-off flake. Founder
  had a pre-existing Cursor Pro+ subscription ($60/month) - moved to the raw
  `cursor-agent` CLI.
  - Verified live before relying on any of it (`agent --version` reported
    `2026.07.23-e383d2b` at the time), in a disposable sandbox, not assumed
    from fetched docs - the same discipline the 2026-07-25 `--permissions`
    mistake above was meant to prevent repeating:
    - `--mode plan` is a first-class documented flag, not something built
      out of a generated permission-profile file - confirmed the CLI
      genuinely refuses file creation/deletion in that mode even when asked
      directly and plainly to use its tools.
    - A fresh working directory is workspace-untrusted by default and blocks
      waiting on an interactive trust prompt it can never receive
      non-interactively (same *class* of bug as the 2026-07-25 OpenCode
      permission-key hang above) - `--trust` is required every run for this
      reason, but only answers that one prompt; verified it grants no
      additional write permission on its own.
    - The prompt is piped via stdin, not passed as a positional argument -
      sidesteps the `execve()` `MAX_ARG_STRLEN` ("Argument list too long")
      crash a large real prompt (full diff + every package doc) would hit if
      passed as an argument.
    - `--output-format json` (non-streaming) is a single JSON object on
      stdout, not OpenCode's nd-JSON per-event stream.
  - implementer and planner followed to Cursor separately (see their own
    2026-07-31 entries) - this file's own execution step only ever covers
    the reviewer role.
