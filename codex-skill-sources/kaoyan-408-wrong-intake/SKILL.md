---
name: kaoyan-408-wrong-intake
description: "408 当前题的答案安全快速捕获与正式事务边界。预处理题包中的选项作答只走 answer-current-and-next；独立正确生成私有 observation，失败题生成 bundle v3/capture，wrong 类在同一题纠正后补齐 trace supplement；Luna v2 只提候选，显式日期后由 Sol 核验。"
---

# Kaoyan 408 Wrong Intake

## Goal

Preserve the first answer and the bounded teaching exchange without slowing study.
Knowledge classification, history matching, relations, Luna execution, and formal
writes stay outside the learner turn.

## Exact entry

For every first or correction A-D reply call exactly once:

```text
python3 scripts/managed_408_current_turn.py --repo <repo> --private-root <root> answer-current-and-next --display-receipt-locator <current-question-turn://sha256/...> --choice <A|B|C|D> --confidence <high|medium|low> --prompt-level <none|L1|L2|L3|L4|L5> --trace-json '<JSON list>' --attachments-json '<JSON object>'
```

The external schema is `managed-408-answer-current-and-next-v1`. Do not separately
call `date`, `prepare-current-turn`, `current-turn`, `continue-current`, `next-item`,
the retired direct router, or a capture writer. Do not reconstruct context JSON from
tool output.

Keep the original display receipt through corrections. `--trace-json` contains only
this turn's observed `role`, `kind`, and `text`; the runtime owns cumulative order.
An answer event conflicting with `--choice` fails closed; free-form speech gets a
separate canonical choice binding. Never deduplicate identical utterances across turns.

`--attachments-json` defaults to
`{"question_mode":"dialogue_only","attachments":[]}`. Its only top-level keys are
`question_mode` and `attachments`; attachment keys are `path`, `sha256`, `mime_type`,
`role`, and `label`. Paths are invocation-only. Corrections reuse frozen attachments.
`image_question` requires both `question_image` and `solution_image`.

## First-answer split

- High-confidence, unprompted `independent_correct`: one private
  `current-question-evidence-bundle-v3` bound to exactly one
  `current-question-study-observation-v1` at `awaiting_background_analysis`. It is
  one central-consumable Luna candidate, creates zero failure Captures, and stays
  outside the formal wrong-question batch.
- `fragile_correct`: no observation; one `current-question-evidence-bundle-v3` and
  one answer-safe `awaiting_daily_curation` capture. With complete evidence its
  background handoff becomes `ready` with `completion_kind=first_turn_complete`,
  then receipt-gated advancement. An image-role gap fails before Capture creation.
- `wrong|partial|uncertain`: no observation; the same bundle-v3/capture pair under
  `current_question_failure_standing_policy_v1`, but the result is
  `feedback_ready_continue_current`; handoff stays `teaching_pending`, the first-turn
  receipt has `advance_allowed=false`, and the
  same display receipt is retained for the next correction answer.

All branches have `luna_call_count=0` and `formal_write_count=0`. An observation is
positive study evidence, not mastery. A capture is a durable fact handoff, not a
formal node, classification, recurring-error conclusion, relation, or formal intake.
A new failure Capture publicly returns `subject=cs408`, `capture_id`,
`capture_content_sha256` equal to its committed `payload_sha256`, `status`,
`producer_binding_status=attested`, non-null
`producer_binding_attestation_sha256`, and `formal_write_count=0`. Non-Capture turns
return null Capture fields. The release-neutral sidecar binds this Skill, Producer
closure, and Capture contract. `historical_pre_attestation` is read-only legacy state.
Every completed item produces exactly one background candidate: its study observation
or its one Capture with the final ready handoff. Corrections only append the original
trace supplement and cannot create another candidate, bundle, or Capture.

## Same-question correction

Use the same command and display receipt for each correction answer.

- Still wrong: return the prepared correction, retain the item, and merge this turn's
  private trace. Do not repeat the immutable first outcome, bundle, or capture.
- Correct after teaching: publish one `teaching-resolution-attestation-v1`, one
  `current-question-trace-supplement-v1` bound to the original capture/evidence
  manifest, and one session resolution `relearn_required`. It explicitly records
  `mastery_effect=none`, `retention_effect=none`, and
  `independent_repair=false`. Complete evidence then atomically promotes the handoff
  to `ready` with `completion_kind=teaching_resolved`. Navigation stays receipt-gated.

This resolution says only that the immediate correction was completed. It never
rewrites the original failure into independent correct.

## Trace and privacy

The private `current-question-interaction-trace-v2` allows 24 included events, 2048
UTF-8 bytes each, and 32 KiB canonical total. It preserves original ordinals, counts,
exact omitted ranges, truncation reason, and full-trace SHA-256.

Bundle v3 declares `question_mode=dialogue_only|image_question`. Image attachments
are ordered and bind ordinal, role, an original-bytes content reference, byte count,
SHA-256, declared/detected MIME, and detected PNG/JPEG/WebP magic. Roles are
`question_image|solution_image`; an image question requires at least one of each. At
most eight are accepted and a ninth fails closed. If a required image role is
missing before the operation is established, the external result has
`status=blocked` and
`reason_code=image_question_requires_question_and_solution_images`. It creates no
Capture, background handoff, navigation advancement, model enqueue, or formal write.

Capture-eligible evidence also requires a nonempty question surface, exactly four
nonempty distinct A-D options, the real learner answer bound to the CLI choice and a
learner answer event, plus a nonempty standard explanation or grader basis. Any
missing item after the operation is established uses the applicable
`capture_pending_recovery` path and never creates a pending quality attempt.

The first bundle contains the first-turn trace. On teaching resolution, the private
trace supplement contains the bounded cumulative exchange and its hash. Public
capture rows contain neither trace text nor the complete stem, answer, options, full
learner response, feedback, grader, image, handwriting, or private path.

## Hot-path prohibitions and recovery

The learner turn reads no personalization, MEMORY, history, formal nodes, knowledge
indexes, relations, old questions, another pack item, Luna, Dashboard, model, Provider,
MCP, Sol, worker/report state, port 8767, daily-curation state, reference books, or
source code. It runs no whole-ledger scan, audit, reconcile, generation, status, or
schema discovery.

Show only authorized feedback and a returned next surface when
`next_item_published=true`. An idempotent replay creates zero new learner writes. Receipt
and `recover-current-capture` recovery reuse original identities and only add the
missing type-preserving layer. Independent-correct evidence recovery creates an
observation and never a failure Capture; capture-eligible recovery creates only its missing
bundle/capture. Successful recovery closes the original answer operation and may
return its receipt-gated successor. New incomplete image evidence has no Capture or
handoff to promote. Old advancing receipts are rejected unless their
item is the current navigation frontier. Recovery never duplicates an outcome,
observation, capture, resolution, supplement, or successor.

## Deterministic background candidates

Outside the learner turn, pin a complete-matching answer-safe knowledge/history
snapshot to the evidence manifest and source hashes. Coverage records scanned,
matched, included, excluded, truncation, and partition counts; limits are partitions,
not silent top-N. Preserve ledger-backed dates. Separate exposed/involved knowledge,
hierarchy/confusion candidates, prior wrong questions, existing edges, and proposal-only
relations. Missing verified snapshot schema, locator, hash, or binding remains a gap.

## Luna proposal boundary

`current-question-background-handoff-v1` and
`current-question-background-handoff-binding-v1` use
`current-question-background-handoff://sha256/<object_sha256>`. Resolve by capture ID;
missing, drifted, `teaching_pending`, or `evidence_pending` fails closed. Only verified
`ready` is eligible, and resolved failures also require `resolved_trace`.

The chat never starts, waits for, polls, retries, or inspects Luna or port 8767.
Producer objects are release-neutral; central Dispatcher adds runtime authority.
`study-intake-luna-analysis-v2` uses `model=gpt-5.6-luna`,
`reasoning_effort=max`, and consumable `two_pass_ready`. Luna is `proposal_only` with
`formal_write_count=0`; all semantic groups need evidence, counterevidence, confidence,
and a Sol verification action.

## Explicit-date Sol curation

This is the explicit-date Sol batch boundary.
Only an explicit user-specified `Asia/Shanghai` date freezes failure captures. Sol
reopens raw capture, bundle, resolved trace, pinned snapshot, and at most one Luna v2
report, then records adopt/modify/reject before one serial batch-size-1 transaction.
Conflicts become `needs_user` with zero apply; failed closeout resumes its receipt.

managed_fast_intake_route_v11=entry:managed-408-answer-current-and-next-v1-only;correct:current-question-evidence-bundle-v3-plus-study-observation-v1-central-candidate;failure:current-question-evidence-bundle-v3-plus-one-capture;candidate:exactly-one-per-completed-item;correction:same-display-trace-supplement-no-duplicate-candidate;recovery:type-preserving-and-operation-closing;navigation:frontier-bound;handoff:ready-only-after-completion;neutral:no-release-activation-authority;luna:no-hotpath-wait-or-query;formal:explicit-date-sol-adopt-modify-reject
