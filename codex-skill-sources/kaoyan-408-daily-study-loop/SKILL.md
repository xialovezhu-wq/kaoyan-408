---
name: kaoyan-408-daily-study-loop
description: "408 已发布当前题的晨间复盘、普通讲题和晚间 D0 快速反馈。只有第一题 display receipt 已存在后才使用；选项作答只走 answer-current-and-next，错误题在同一 receipt 上纠正，Luna 仅后台分析。"
---

# Kaoyan 408 Daily Study Loop

## Outcome

Record, grade, teach, and navigate one prepared current question through
one external entry. Return frozen feedback quickly. Never wait for Luna or formal
curation.

## Entry boundary

Without a display receipt, “开始今天的晨间复盘” routes to
`kaoyan-408-morning-control`, which owns active-session closeout, the dated queue,
Sol author/verifier, immutable pack, unique session, and first display.

Enter this skill only after a `current-question-turn://sha256/...` display receipt
has been published. Never create a pack, repair a lifecycle projection, invoke a
model, or start a session from this current-question hot path.

## Hot-path context

Use only the current display receipt, this turn's answer/confidence/prompt level and
observed interaction trace, plus its runtime-bound `current-question-context-v1` and
prepared surface/evaluator. Do not open
source code, skill references, MEMORY, personalization, formal nodes, knowledge
indexes, relations, history, linked practice, variants, another pack surface, Luna,
Dashboard, model, Provider, MCP, Sol, background status, port 8767, end-of-day state,
or reference books. Do not run `date`, `--help`, status, audit, reconcile, generation,
schema discovery, or a full-ledger scan.

`current_question_minimal` limits reads but keeps exact `action_id`: consume
at most once, derive `cross_day` only from its `origin_date` and observed date, create
at most one successor. Missing/conflicting bindings fail closed without searching
history, formal data, another question, relations, or Luna; `luna_call_count=0`.
Frozen unanswered duplicates get answer-free `item_scheduling_superseded` and are skipped.

## Exact single entry

For every first or correction A-D reply call exactly once:

```text
python3 scripts/managed_408_current_turn.py --repo <repo> --private-root <root> answer-current-and-next --display-receipt-locator <current-question-turn://sha256/...> --choice <A|B|C|D> --confidence <high|medium|low> --prompt-level <none|L1|L2|L3|L4|L5> --trace-json '<JSON list>' --attachments-json '<JSON object>'
```

Keep the same display receipt during correction. Trace events contain exactly
`role`, `kind`, and `text`; never invent learner speech. A canonical learner answer
`A|B|C|D` must match `--choice` or fail as `interaction_trace_choice_drifted`.
Free-form answer speech is preserved and receives a separate canonical choice binding.

`--attachments-json` is the only attachment input. Its default is
`{"question_mode":"dialogue_only","attachments":[]}`. The top level contains only
`question_mode` and `attachments`; each attachment contains only `path`, `sha256`,
`mime_type`, `role`, and `label`. The private path is read for this invocation only
and never enters the operation, bundle, Capture, or output. A correction must not
resubmit attachments and reuses the first bundle's frozen attachment objects.
`dialogue_only` has no attachments. `image_question` requires both `question_image`
and `solution_image`; missing either fails before Capture creation.

The command validates one prepared display/evaluator, freezes feedback, commits the
applicable receipts, and retains the item or returns one successor. Do not separately call
`prepare-current-turn`, `current-turn`, `next-item`, `continue-current`, the retired
direct router, a capture writer, or `date`.
Its external result schema is `managed-408-answer-current-and-next-v1`.

## Result branches

The runtime applies `current_question_failure_standing_policy_v1` exactly once per
first answer:

- First high-confidence unprompted `independent_correct`: one private
  `current-question-evidence-bundle-v3` bound to exactly one
  `current-question-study-observation-v1` at `awaiting_background_analysis`. This is
  one central-consumable Luna candidate with zero failure Captures and stays outside
  the formal wrong-question batch, then receipt-gated next item.
- First `fragile_correct`: no observation; one private
  `current-question-evidence-bundle-v3` plus one capture at
  `awaiting_daily_curation`; complete evidence promotes its background handoff to
  `ready` with `completion_kind=first_turn_complete`. An image-role, integrity, or
  required question-evidence gap fails before Capture creation with zero model calls;
  navigation remains receipt-gated.
- First `wrong|partial|uncertain` with a stable capture:
  handoff `status=teaching_pending`, answer
  `status=feedback_ready_continue_current`, and `next_item_published=false`. Its
  first-turn receipt has `advance_allowed=false`. Show the prepared first-break
  correction and keep the same display receipt; the next A-D reply uses the same
  external command and display receipt.
- Correction still wrong: the same status and item; privately accumulate the trace,
  with no duplicate first outcome, bundle, or capture.
- Correction correct: publish one `teaching-resolution-attestation-v1` and one
  capture-bound `current-question-trace-supplement-v1`, commit session resolution
  `relearn_required` with `mastery_effect=none`, `retention_effect=none`, and
  `independent_repair=false`, atomically promote the handoff to `ready` with
  `completion_kind=teaching_resolved`, then return the receipt-gated next item.

Display only authorized `feedback_text`, one short observation/capture state, and
`next_item.display_text` when `next_item_published=true`. A corrected failure is not
independent mastery. Every branch keeps `luna_call_count=0` and
`formal_write_count=0`.

Every completed item produces exactly one background candidate: its study observation
or its one Capture with the final ready handoff. Correction turns only extend the
original trace supplement and cannot create another candidate, bundle, or Capture.

## Trace boundary

Private `current-question-interaction-trace-v2` keeps at most 24 included events,
2048 UTF-8 bytes per event, and 32 KiB canonical total. It always records original,
included, and omitted counts, exact omitted ordinal ranges, the truncation reason,
and the full-trace SHA-256. Any omission is an explicit evidence gap. First-turn trace
stays in bundle v3; resolved cumulative trace uses a private capture-bound supplement.
Public captures contain no full trace text.

## Recovery

Receipt failure withholds answer-bearing feedback. Exact `recover-current-capture` is
type-preserving: `independent_correct` restores observation and never a failure Capture;
capture-eligible turns restore only bundle/capture. Recovery closes the original
operation, so exact replay returns its sealed final result with zero new writes. A
prior receipt must be the current navigation frontier and cannot skip an unresolved
item. Never duplicate outcome, observation, capture, resolution, supplement, or successor.

## Background and formal boundary

`current-question-background-handoff-v1` and
`current-question-background-handoff-binding-v1` use
`current-question-background-handoff://sha256/<object_sha256>`. Missing, drifted, or
`teaching_pending` fails closed; verified `ready` proves input completion, not Luna execution.
The chat never starts, waits for, polls, or inspects Luna, worker, report, or port 8767.
Background candidate snapshots are bounded and pinned; missing bindings remain gaps.
`study-intake-luna-analysis-v2` is proposal-only and cannot write facts or formal state.
Only the current immutable preprocessor release may consume a verified bundle-v3 and
trace-v2 handoff; every background stage requests `gpt-5.6-luna` with
`reasoning_effort=max`, and incomplete evidence causes zero model calls.
Only an explicit user-specified `Asia/Shanghai` date starts Sol; it reopens sources,
records adopt/modify/reject, and writes serial batch-size-1. Correct observations stay outside.

The producer-side observation, bundle, and Capture are release-neutral and contain no
`release`, `activation`, Dispatcher authority, or MCP authority. Only the central
Dispatcher injects those fields after consuming the neutral candidate.

managed_current_question_skill_v7=entry:managed-408-answer-current-and-next-v1-only;correct:current-question-evidence-bundle-v3-plus-study-observation-v1-central-candidate;failure:current-question-evidence-bundle-v3-plus-one-capture;candidate:exactly-one-per-completed-item;correction:same-display-trace-supplement-no-duplicate-candidate;recovery:type-preserving-and-operation-closing;navigation:frontier-bound;handoff:ready-only-after-completion;neutral:no-release-activation-authority;luna:no-hotpath-wait-or-query;formal:explicit-date-sol-verification
