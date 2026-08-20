Shared CS408 runtime contract

The answer-current-and-next entry is distinct from continue-current and
next-item, and all three are bound to the navigation frontier.

The only learner-turn entry is answer-current-and-next, with continue-current
and next-item bound to the navigation frontier. The hot path uses
current-question-evidence-bundle-v3 and never waits, polls, or retries Luna.
Ordinary Producer admission is fail-closed: after stable Unicode NFKC
normalization, only the current user message containing the exact contiguous
phrase `快速入库` authorizes Capture. Split, punctuated, inferred, wrong-answer,
low-confidence, recurrence, score, warmup, and model/MCP/Sol intent never
substitute. Without admission, Capture, central observation, consumer handoff,
and model/MCP/Sol/formal writes are zero. The raw message is never persisted;
only its normalized SHA-256, trigger phrase, and source role may be retained.

Morning review is the sole automatic exception: failures append only to the
current session ordered buffer. The first correct or final correct answer
freezes the complete ordered dialogue and emits one release-neutral Capture;
durable Capture and session receipts gate one successor. No new wrong Capture or
trace supplement is written, and retries are idempotent.
The explicit study date freezes the capture-set SHA-256 for Sol. The cold
consumer command is ~/.codex/study-intake-preprocessor/current/bin/preprocess_consumer.py consume.
Consumable Luna status is two_pass_ready with model gpt-5.6-luna and
reasoning_effort=max. Sol uses sol_curation_decision_408.py seal and chooses
adopt, modify, or reject for every candidate, serially, batch-size-1. already_current
is a terminal result. prepare-next-morning closes only the minimal next-morning
state. Never wait, poll, retry Luna in the learner path.
