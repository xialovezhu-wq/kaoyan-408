Morning current-question review

The exact entry is answer-current-and-next; continue-current and next-item are
receipt-bound actions at the navigation frontier.

The prepared pack is answer-safe. Ordinary independent correct is a private
observation only after the current user message, normalized with NFKC, contains
the exact contiguous phrase `快速入库`. Correct with medium/low confidence enters
`awaiting_daily_curation` only after admission; correct with medium/low confidence and
wrong, partial, blank, or uncertain results need the same phrase for Capture.
Split or punctuated wording, doing the question wrong, recurrence, scoring,
warmup state, or model judgment is not authorization. Without admission,
Capture, central observation, and consumer handoff are all zero.

Morning review is the only automatic exception. Errors append only to the
current session's ordered buffer and do not create a durable Capture or advance.
The first correct answer, or a final correct answer after errors, freezes the
complete ordered dialogue and creates exactly one release-neutral Capture. The
Capture and session receipts must be durable before one navigation advance.
Retries are idempotent; no new wrong Capture or trace supplement is written.

The original answer operation closes only after its receipt is durable. A
receipt failure must not reveal answer-bearing feedback and must not advance. A
Capture failure keeps the position and shows the frozen feedback, returning
`capture_pending_recovery`. Luna failure never changes Capture success.
Recovery remains type-preserving and closes the original answer operation.
If the first-answer or session receipt cannot be committed, do not reveal
answer-bearing feedback and do not advance. A capture failure must show the
frozen feedback, return `capture_pending_recovery`, and keep the position.
Luna failure never changes capture success.
