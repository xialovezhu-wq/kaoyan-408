"""Portable answer-safe primitives shared by the 408 capture writer."""

from __future__ import annotations

import re


FORMAL_ID_RE = re.compile(r"^(?:DS|CO|OS|CN)_(?:\d{4}|UNK)_\d{3}$")


def scan_leak(value: str) -> tuple[list[str], list[str]]:
    """Detect only explicit answer-bearing markers in public fact text.

    Field-name checks remain the authoritative gate.  This scanner is kept
    intentionally conservative so ordinary learner wording is not rewritten or
    rejected merely because it mentions a choice or a mechanism.
    """

    text = str(value or "")
    failures: list[str] = []
    warnings: list[str] = []
    if any(marker in text for marker in ("标准答案", "正确答案", "correct_answer", "answer_key")):
        failures.append("answer-bearing marker")
    if "file://" in text or "/private/tmp/" in text or "/var/folders/" in text:
        warnings.append("temporary locator")
    return failures, warnings
