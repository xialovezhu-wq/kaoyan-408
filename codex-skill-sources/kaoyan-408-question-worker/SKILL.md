---
name: kaoyan-408-question-worker
description: current 408 question worker with answer-safe feedback
---

Work only on the current question and current evidence. Do not read
personalization, MEMORY, formal nodes, another pack, history, relations, Luna,
port 8767, reference books, or source code. Use the managed
answer-current-and-next entry and current-question-evidence-bundle-v3. The hot
path keeps formal_write_count=0.

High-confidence, unprompted independent correct is an observation. Correct with
medium/low confidence is captured. Wrong, partial, blank, or uncertain is
captured under current_question_failure_standing_policy_v1 and returns
awaiting_daily_curation. An explicit user date is required for Sol formal
curation; ordinary learning is not silently formalized. Use recover-current-capture
for type-preserving repair and keep the navigation frontier.

高置信、无提示、无推理断点的独立正确只形成观察；正确但中低置信或有提示的
结果进入 awaiting_daily_curation。Luna is proposal-only and Sol performs
explicit-date curation.
