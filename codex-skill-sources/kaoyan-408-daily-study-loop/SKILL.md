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

## Exact single entry

Use scripts/managed_408_current_turn.py answer-current-and-next exactly once for
each answer. Do not separately call current-turn, date, prepare-current-turn,
continue-current, or next-item. The managed entry commits one
current-question-evidence-bundle-v3 with current-question-interaction-trace-v2,
keeps formal_write_count=0, and advances only at the navigation frontier.

high-confidence, unprompted independent correct creates a private observation
and may advance. Correct with medium/low confidence or a prompt creates one
capture awaiting_daily_curation. wrong, partial, blank, or uncertain creates
one capture under current_question_failure_standing_policy_v1 and holds the
current item. recover-current-capture is type-preserving and closes the original
operation; a receipt failure cannot reveal answer-bearing feedback or advance.
After capture recovery, show the frozen feedback, return capture_pending_recovery
only until the capture is repaired, and keep the position. Luna failure never
changes capture success.
