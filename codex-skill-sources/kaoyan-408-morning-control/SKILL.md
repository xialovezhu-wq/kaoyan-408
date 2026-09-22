---
name: kaoyan-408-morning-control
description: "408 晨间复盘控制面。用户说开始今天的晨间复盘且尚无当前 display receipt 时使用：处理旧 session 门禁与幂等收尾，复用 Guardian 预生成的当日不可变题包；题包 ready 时创建唯一 session 并发布第一题，未 ready 时只返回分片进度。"
---

# Kaoyan 408 Morning Control

## Outcome

Turn one dated start request into exactly one answer-safe first-question display.
Stop after publishing that display and wait for the learner. Do not answer the first
question and do not enter the current-question transaction in the same control call.
The start command never waits for authoring or verification. If preparation is still
running, return its bounded progress immediately without creating a session.

## Trigger and handoff

Use this skill for “开始今天的晨间复盘” when no current display receipt exists.
If a same-date active or paused session already exists, resume its current unanswered
surface instead of creating another session. Once the first display receipt exists,
handoff all A-D replies to `kaoyan-408-daily-study-loop`.

## Exact control entry

Use the repository control entry rather than manually composing queue, pack, session,
or display JSON:

```text
python3 scripts/start_morning_review_408.py --repo <repo> --date <Asia/Shanghai-YYYY-MM-DD> --preparation-job <existing-job-id>
```

The optional job argument is omitted only when no same-date job exists. A known job
must be adopted and rebound to the current preparation contract; do not create a
duplicate solely because the response schema or release contract changed.

## Ordered control plane

The entry must enforce this order:

1. Read the bounded morning runtime and detect one active or paused session.
2. If an older session has canonical terminal evidence, rebuild only its projection
   and append at most one close event. Never replay outcomes or delete old packs.
3. Fail closed when items remain incomplete or source/evidence identity conflicts.
4. Bind the dated immutable action queue.
5. Reuse or explicitly adopt the unique current preparation job.
6. Read the preparation job only. If it is not ready, return
   `preparation_in_progress`; do not invoke a model in the learner turn.
7. Publish one immutable prepared manifest for the current input identity.
8. Start one date/queue-bound morning session.
9. Publish one content-addressed, answer-safe first display receipt.
10. Return the public question and stop.

Repeated starts for the same date reuse the current job, pack, session, and display
identity. Historical queue, pack, session, receipt, and failure objects remain audit
evidence and never block a new date merely because they still exist.

## Background preparation

The existing Learning Guardian notices the immutable dated queue and starts the
preparation worker without adding a LaunchAgent. The worker partitions the complete
item order into contiguous chunks of at most four. Every chunk must first call the
isolated `cs408_morning_preparation_bundle` MCP tool and freeze its release,
generation, authority fingerprint, evidence-scope hash, exact source refs, content,
and source hashes. There is no shell or whole-repository fallback when MCP is missing.

Sol author chunks and independent Sol verifier chunks are content-addressed and
resumable. The complete draft is validated against the full immutable queue, but no
pack is visible to a learner until all chunks pass and one manifest is atomically
published. The learner therefore still receives the whole prepared morning review
before question one, never question-by-question generation.

## Model boundary

Preparation author and verifier both request:

```text
model=gpt-5.6-sol
reasoning_effort=max
```

Record requested identity separately from runtime-confirmed identity. A request label
is not runtime attestation. The control plane never calls Luna. After a failed or
fragile learner answer, the later current-question capture handoff may be consumed in
the background with `model=gpt-5.6-luna` and `reasoning_effort=max`; neither this
skill nor the learner hot path waits for, polls, or inspects that work.

## Failure and write boundary

Each model attempt has a 30-minute watchdog. A timeout or transient provider/MCP
failure enters `retry_wait` with exponential backoff and keeps all completed chunks;
it does not fail the complete pack after ten minutes. Deterministic schema errors,
queue identity drift, evidence hash drift, source gaps, and semantic rejections fail
closed. An explicit `adopt-current` recovery may requeue one pre-output process
failure, one deterministic author rejection, or one verifier-rejected chunk. The
last case clears and rewrites only that chunk with the verifier's failed-check labels;
all accepted chunk receipts remain bound. A second rejection stays closed and requires
a corrected successor. Rejected author candidates remain private content-addressed
diagnostics and are never learner-visible.

Author, verifier, or MCP failure must produce an answer-safe receipt containing
the stage, error code/type, requested model and effort, return code, retryability,
sanitized diagnostic summary, `formal_write_count=0`, and
`learner_evidence_write_count=0`. Never expose the queue body, question/answer,
private evaluator, prompt, or raw stderr in public status.

Preparation and display publication do not modify formal nodes, relations, mastery,
formal review dates, redo records, or the canonical learner ledger. The preparation
phase keeps learner evidence writes at zero. Session start writes only session/runtime
state; the first display receipt contains no learner answer.

morning_control_v2=entry:start-morning-read-only-preparation-status;guardian:existing-scan-no-new-launchagent;prepare:mcp-required-four-item-resumable-sol-max-author-plus-independent-verifier;watchdog:1800s-per-attempt-retry-wait;publish:atomic-full-pack-plus-unique-session-plus-answer-safe-first-display;handoff:daily-study-loop-after-display;luna:background-capture-only;formal:zero
