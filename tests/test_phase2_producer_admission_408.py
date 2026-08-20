from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import capture_hot_writer_408 as capture_hot  # noqa: E402
import intake_fact_capture_408 as capture_model  # noqa: E402
import managed_408_current_turn as current_turn  # noqa: E402
import morning_review_session as morning_session  # noqa: E402
import morning_session_hot_state_408 as session_hot  # noqa: E402


class ProducerAdmissionPhase2Tests(unittest.TestCase):
    def test_exact_phrase_matrix_uses_nfkc_and_rejects_split_intent(self) -> None:
        accepted = current_turn.normalize_capture_authorization("Ａ请快速入库Ｂ")
        self.assertTrue(accepted["authorized"])
        self.assertEqual(
            accepted["normalized_message_sha256"],
            hashlib.sha256("A请快速入库B".encode("utf-8")).hexdigest(),
        )
        for message in (
            "快速 入库",
            "快速，入库",
            "快速\n入库",
            "这题做错了",
            "评分写入 2 分",
            "旧题复发",
            "模型判断应该保存",
            None,
        ):
            with self.subTest(message=message):
                self.assertFalse(
                    current_turn.normalize_capture_authorization(message)[
                        "authorized"
                    ]
                )

    def test_current_question_capture_without_authorization_is_rejected_before_append(
        self,
    ) -> None:
        payload = {
            "schema": capture_model.SCHEMA,
            "study_date": "2026-08-21",
            "timezone": "Asia/Shanghai",
            "idempotency_key": "phase2:missing-auth",
            "stable_evidence_refs": [
                {
                    "kind": capture_model.CURRENT_QUESTION_EVIDENCE_REF_KIND,
                    "locator": "current-question-evidence://sha256/" + "a" * 64,
                    "sha256": "a" * 64,
                }
            ],
            "source_facts": {"subject": "未确认", "source_id": "synthetic"},
            "user_facts": {
                "user_error_entry": "未观察到",
                "user_error_provenance": "not_observed",
            },
            "identity_hint": {
                "status": "unknown",
                "mode": "unknown",
                "basis": "synthetic",
            },
            "answer_safe_context_anchor": "synthetic current question",
            "missing_fields": ["formal_identity"],
            "formalization_authorized": True,
            "authorization_policy": capture_model.CURRENT_QUESTION_FAILURE_STANDING_POLICY,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(capture_model.CaptureError, "连续短语"):
                capture_model._validate_capture_payload(payload)
            self.assertFalse(capture_model.capture_root(root).exists())

    def test_morning_buffer_is_ordered_idempotent_and_freezable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_id = "MR-phase2"
            item_id = "MQ-phase2"
            session_dir = root / morning_session.STATE_REL / session_id
            session_dir.mkdir(parents=True)
            (session_dir / "events.jsonl").write_text("", encoding="utf-8")
            state = {
                "schema": "morning_review_session_state_v1",
                "status": "in_progress",
                "session_id": session_id,
                "review_date": "2026-08-21",
                "item_order": [item_id],
                "items": {
                    item_id: {
                        "first": {"result": "wrong"},
                        "resolution": None,
                        "answer_buffer": [],
                    }
                },
                "formal_write_count": 0,
            }
            (session_dir / "state.json").write_text(
                json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            session_hot.bootstrap(session_dir, state)
            trace = {
                "schema": "current-question-interaction-trace-v2",
                "original_event_count": 1,
                "included_event_count": 1,
                "omitted_event_count": 0,
                "omitted_ranges": [],
                "truncation_reason": None,
                "full_trace_sha256": "a" * 64,
                "events": [
                    {
                        "ordinal": 1,
                        "role": "learner",
                        "kind": "answer",
                        "text": "A",
                    }
                ],
            }
            first = morning_session.append_answer_buffer(
                root,
                session_id,
                item_id,
                attempt_key="first:phase2",
                result="wrong",
                choice_result="incorrect",
                confidence="high",
                prompt_level="none",
                interaction_trace=trace,
                recorded_at="2026-08-21T08:00:00+08:00",
            )
            replay = morning_session.append_answer_buffer(
                root,
                session_id,
                item_id,
                attempt_key="first:phase2",
                result="wrong",
                choice_result="incorrect",
                confidence="high",
                prompt_level="none",
                interaction_trace=trace,
                recorded_at="2026-08-21T08:00:00+08:00",
            )
            frozen = morning_session.freeze_answer_buffer(
                root,
                session_id,
                item_id,
                freeze_key="capture:phase2",
                recorded_at="2026-08-21T08:00:00+08:00",
            )
            frozen_replay = morning_session.freeze_answer_buffer(
                root,
                session_id,
                item_id,
                freeze_key="capture:phase2",
                recorded_at="2026-08-21T08:00:00+08:00",
            )
            self.assertEqual("BUFFERED", first["status"])
            self.assertEqual("ALREADY_BUFFERED", replay["status"])
            self.assertEqual("FROZEN", frozen["status"])
            self.assertEqual("ALREADY_FROZEN", frozen_replay["status"])
            self.assertEqual(1, len(frozen["buffer"]))
            self.assertEqual(
                2,
                len(
                    (session_dir / "events.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ),
            )


if __name__ == "__main__":
    unittest.main()
