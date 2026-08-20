Shared CS408 runtime contract

The answer-current-and-next entry is distinct from continue-current and
next-item, and all three are bound to the navigation frontier.

The only learner-turn entry is answer-current-and-next, with continue-current
and next-item bound to the navigation frontier. The hot path uses
current-question-evidence-bundle-v3 and never waits, polls, or retries Luna.
The explicit study date freezes the capture-set SHA-256 for Sol. The cold
consumer command is ~/.codex/study-intake-preprocessor/current/bin/preprocess_consumer.py consume.
Consumable Luna status is two_pass_ready with model gpt-5.6-luna and
reasoning_effort=max. Sol uses sol_curation_decision_408.py seal and chooses
adopt, modify, or reject for every candidate, serially, batch-size-1. already_current
is a terminal result. prepare-next-morning closes only the minimal next-morning
state. Never wait, poll, retry Luna in the learner path.
