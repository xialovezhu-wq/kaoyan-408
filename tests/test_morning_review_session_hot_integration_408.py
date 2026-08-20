from __future__ import annotations


REVIEW_HEADERS = ["review_unit_id", "subject", "module_id", "topic", "kind", "status", "formal_node_id", "last_review", "next_review", "as_of", "interval", "result", "attempt", "confidence", "difficulty", "prompt", "safe_feedback", "notes"]
MAPPING_HEADERS = ["formal_node_id", "subject", "source_kind", "topic", "review_unit_id", "legacy", "role", "status", "has_details", "eligibility", "notes"]
TRACK_HEADERS = ["formal_node_id", "review_unit_id", "status", "eligibility", "last_review", "next_review", "as_of", "source_id", "year", "locator", "paragraph", "source", "details_ref", "reason", "notes"]


def table(headers: list[str], rows: list[list[str]]) -> str:
    line = "| " + " | ".join(headers) + " |\n"
    divider = "| " + " | ".join("---" for _ in headers) + " |\n"
    body = "".join("| " + " | ".join(row) + " |\n" for row in rows)
    return line + divider + body
