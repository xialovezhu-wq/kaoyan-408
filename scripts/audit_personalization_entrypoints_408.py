"""Read-only audit for current-question hot-path isolation."""

from __future__ import annotations

from pathlib import Path


HOT_FILES = [
    "scripts/managed_408_current_turn.py",
    "scripts/current_question_context_408.py",
    "scripts/current_question_evidence_408.py",
    "scripts/capture_hot_writer_408.py",
    "scripts/review_outcome_hot_408.py",
    "scripts/morning_review_prepared_pack_408.py",
    "scripts/morning_review_session.py",
    "scripts/morning_session_hot_state_408.py",
]
RETIRED_WRITE_GUARDS = {
    "scripts/ordinary_capture_hot_adapter_408.py": ["RETIRED_WRITE_GUARD_408"],
}
RETIRED_CONTROL_FILES = {
    "scripts/managed_408_turn_broker.py",
    "scripts/intake_queue_worker_408.py",
}


def audit(repo: str | Path) -> dict[str, object]:
    root = Path(repo).resolve()
    violations: list[str] = []
    for relative in HOT_FILES:
        path = root / relative
        if not path.is_file():
            violations.append(f"missing_hot_file:{relative}")
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        if "personalization_turn_receipt_408" in body or "personalized_study_turn_408" in body:
            violations.append(f"forbidden_hot_dependency:{relative}")
        if "sub.add_parser('next-item')" in body or 'sub.add_parser("next-item")' in body:
            violations.append("retired_current_turn_cli_present:next-item")
    for relative, guards in RETIRED_WRITE_GUARDS.items():
        path = root / relative
        if not path.is_file():
            violations.append(f"missing_retired_guard:{relative}")
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        for guard in guards:
            if guard not in body:
                violations.append(f"legacy_writer_not_retired:{relative}:{guard}")
    for relative in sorted(RETIRED_CONTROL_FILES):
        if (root / relative).exists():
            violations.append(f"retired_control_still_active:{relative}")
    harness = root / "codex-skill-sources/_shared/kaoyan-408/personalization-harness.md"
    if harness.is_file():
        body = harness.read_text(encoding="utf-8", errors="replace")
        if "retired from the current-question hot path" not in body or "must not call" not in body:
            violations.append("missing_skill_contract:personalization-harness")
    else:
        violations.append("missing_skill_contract:personalization-harness")
    return {
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "personalization_required": False,
        "luna_chat_call_count": 0,
        "formal_write_count": 0,
    }
