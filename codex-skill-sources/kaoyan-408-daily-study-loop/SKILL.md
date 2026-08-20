---
name: kaoyan-408-daily-study-loop
description: bounded current-question morning and ordinary study loop
---

## Hot-path context

Only an explicit user-specified study date is used for Sol curation. Read only the current-question-context-v1 surface, the current item, and its
receipt. Do not read MEMORY, personalization, linked practice, variants,
another pack, formal nodes, history, relations, Luna, port 8767, reference
books, source code, or any other question. Do not call --help, status, audit,
reconcile, or search history. A bound morning current_question_minimal action
uses action_id at most once, keeps origin_date and cross_day evidence, and fails
fail closed if item_scheduling_superseded.

If the first-answer or session receipt cannot be committed, do not reveal
answer-bearing feedback and do not advance.

The current action has luna_call_count=0 and formal_write_count=0.
The bound action never uses searching history, formal data, another question,
or relations as a fallback.

## Producer admission

For ordinary new, old, or wrong questions, only the current user message after
stable Unicode NFKC normalization can authorize Capture, and it must contain
the exact contiguous phrase `快速入库`. `快速 入库`, punctuation-separated text,
doing the question wrong, low-confidence correctness, recurrence, scoring,
warmup state, or model judgment never authorizes it. Without the phrase,
Capture, central observation, consumer handoff, model/MCP/Sol calls, and formal
writes are all zero; do not infer intent from surrounding turns.

Morning review is the only automatic exception: errors append only to the
current session's ordered buffer and do not advance. The first correct answer,
or a final correct answer after errors, freezes the complete ordered dialogue
and creates exactly one release-neutral Capture. A durable Capture/session
receipt precedes one navigation advance. Retries are idempotent, and no new
wrong Capture or later trace supplement is allowed.

## Exact single entry

Use scripts/managed_408_current_turn.py answer-current-and-next exactly once for
each answer. Do not separately call current-turn, date, prepare-current-turn,
continue-current, or next-item. The managed entry commits one
current-question-evidence-bundle-v3 with current-question-interaction-trace-v2,
keeps formal_write_count=0, and advances only at the navigation frontier.

high-confidence, unprompted independent correct creates a private observation
and may advance only after ordinary phrase admission. Correct with medium/low
confidence or a prompt creates one Capture awaiting_daily_curation only after
admission. wrong, partial, blank, or uncertain creates one Capture under
current_question_failure_standing_policy_v1 only after admission; without it
there is no Capture, observation, or handoff. In morning review those failures
are buffer-only until final correctness freezes the dialogue. recover-current-capture
is type-preserving and closes the original operation; a receipt failure
cannot reveal answer-bearing feedback or advance. After Capture recovery, show
the frozen feedback, return `capture_pending_recovery` only until the Capture is
repaired, and keep the position. Luna failure never changes Capture success.
