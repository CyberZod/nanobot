"""Tests for FeedbackLogger — append-only JSONL log of workflow rework events.

Mirrors MemoryStore's cursor-recovery pattern: one .jsonl + .cursor sidecar
per workflow_name under base_dir. Schema and semantics per
SELF_ANNEALING_PLAN.md §4.1 and §7 Phase 1.
"""

from __future__ import annotations

import json

import pytest

from nanobot.agent.feedback import FeedbackLogger


@pytest.fixture
def logger(tmp_path):
    return FeedbackLogger(tmp_path / "feedback_logs")


def _read_rows(path):
    """Read a jsonl file as a list of dicts."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestLogReworkBasics:
    """Append + cursor mechanics for the happy path."""

    def test_first_log_writes_event_with_cursor_one(self, logger):
        cursor = logger.log_rework(
            workflow_name="doc_mutation",
            session_id="sess_abc",
            classification_certainty="high",
            original_inputs={"file": "letter.pdf", "Instructions": "kindly->abeg"},
            preview_summary="Replaced kindly with abeg in 4 places",
            user_feedback="the underline is missing on 'abeg'",
        )
        assert cursor == 1

        rows = _read_rows(logger.base_dir / "doc_mutation.jsonl")
        assert len(rows) == 1
        row = rows[0]
        assert row["cursor"] == 1
        assert row["session_id"] == "sess_abc"
        assert row["workflow_name"] == "doc_mutation"
        assert row["agent_classification"] == "correction"
        assert row["classification_certainty"] == "high"
        assert row["trigger"] == "rework_after_preview"
        assert row["user_id"] == "nanobot"
        assert row["original_inputs"] == {"file": "letter.pdf", "Instructions": "kindly->abeg"}
        assert row["preview_summary"] == "Replaced kindly with abeg in 4 places"
        assert row["user_feedback"] == "the underline is missing on 'abeg'"
        assert row["rework_succeeded"] is None
        assert row["rework_summary"] is None
        assert "timestamp" in row and row["timestamp"]

    def test_second_log_increments_cursor(self, logger):
        c1 = logger.log_rework(
            workflow_name="doc_mutation",
            session_id="sess_a",
            classification_certainty="high",
            original_inputs={},
            preview_summary="p1",
            user_feedback="f1",
        )
        c2 = logger.log_rework(
            workflow_name="doc_mutation",
            session_id="sess_b",
            classification_certainty="medium",
            original_inputs={},
            preview_summary="p2",
            user_feedback="f2",
        )
        assert c1 == 1 and c2 == 2

        rows = _read_rows(logger.base_dir / "doc_mutation.jsonl")
        assert [r["cursor"] for r in rows] == [1, 2]
        assert [r["session_id"] for r in rows] == ["sess_a", "sess_b"]

    def test_separate_workflows_have_separate_files(self, logger):
        logger.log_rework(
            workflow_name="doc_mutation",
            session_id="sess_a",
            classification_certainty="high",
            original_inputs={},
            preview_summary="p",
            user_feedback="f",
        )
        c2 = logger.log_rework(
            workflow_name="image_caption",
            session_id="sess_b",
            classification_certainty="high",
            original_inputs={},
            preview_summary="p",
            user_feedback="f",
        )
        # image_caption gets its own cursor sequence starting at 1
        assert c2 == 1
        assert (logger.base_dir / "doc_mutation.jsonl").exists()
        assert (logger.base_dir / "image_caption.jsonl").exists()
        assert len(_read_rows(logger.base_dir / "doc_mutation.jsonl")) == 1
        assert len(_read_rows(logger.base_dir / "image_caption.jsonl")) == 1


class TestIdempotency:
    """log_rework on an already-open session is a no-op (returns existing cursor)."""

    def test_repeated_log_for_open_event_returns_existing_cursor(self, logger):
        c1 = logger.log_rework(
            workflow_name="doc_mutation",
            session_id="sess_a",
            classification_certainty="high",
            original_inputs={"x": 1},
            preview_summary="p",
            user_feedback="first feedback",
        )
        c2 = logger.log_rework(
            workflow_name="doc_mutation",
            session_id="sess_a",
            classification_certainty="high",
            original_inputs={"x": 1},
            preview_summary="p",
            user_feedback="second feedback (should be ignored)",
        )
        assert c1 == c2 == 1
        rows = _read_rows(logger.base_dir / "doc_mutation.jsonl")
        assert len(rows) == 1
        assert rows[0]["user_feedback"] == "first feedback"

    def test_log_after_outcome_creates_new_event(self, logger):
        c1 = logger.log_rework(
            workflow_name="doc_mutation",
            session_id="sess_a",
            classification_certainty="high",
            original_inputs={},
            preview_summary="p",
            user_feedback="f1",
        )
        logger.mark_rework_outcome(
            workflow_name="doc_mutation",
            session_id="sess_a",
            succeeded=True,
            summary="resolved",
        )
        c2 = logger.log_rework(
            workflow_name="doc_mutation",
            session_id="sess_a",
            classification_certainty="high",
            original_inputs={},
            preview_summary="p2",
            user_feedback="f2",
        )
        assert c1 == 1 and c2 == 2
        rows = _read_rows(logger.base_dir / "doc_mutation.jsonl")
        assert len(rows) == 2
        assert rows[0]["rework_succeeded"] is True
        assert rows[1]["rework_succeeded"] is None


class TestMarkReworkOutcome:
    """Closing the most-recent open event for a session_id."""

    def test_mark_outcome_sets_succeeded_and_summary(self, logger):
        logger.log_rework(
            workflow_name="doc_mutation",
            session_id="sess_a",
            classification_certainty="high",
            original_inputs={},
            preview_summary="p",
            user_feedback="f",
        )
        ok = logger.mark_rework_outcome(
            workflow_name="doc_mutation",
            session_id="sess_a",
            succeeded=True,
            summary="v2 approved by user",
        )
        assert ok is True
        rows = _read_rows(logger.base_dir / "doc_mutation.jsonl")
        assert rows[0]["rework_succeeded"] is True
        assert rows[0]["rework_summary"] == "v2 approved by user"

    def test_mark_outcome_unknown_session_returns_false(self, logger):
        # No file exists yet at all
        ok = logger.mark_rework_outcome(
            workflow_name="doc_mutation",
            session_id="sess_unknown",
            succeeded=True,
            summary="x",
        )
        assert ok is False

    def test_mark_outcome_only_touches_target_session(self, logger):
        logger.log_rework(
            workflow_name="doc_mutation",
            session_id="sess_a",
            classification_certainty="high",
            original_inputs={},
            preview_summary="p",
            user_feedback="fa",
        )
        logger.log_rework(
            workflow_name="doc_mutation",
            session_id="sess_b",
            classification_certainty="high",
            original_inputs={},
            preview_summary="p",
            user_feedback="fb",
        )
        ok = logger.mark_rework_outcome(
            workflow_name="doc_mutation",
            session_id="sess_a",
            succeeded=True,
            summary="A done",
        )
        assert ok is True
        rows = _read_rows(logger.base_dir / "doc_mutation.jsonl")
        by_sid = {r["session_id"]: r for r in rows}
        assert by_sid["sess_a"]["rework_succeeded"] is True
        assert by_sid["sess_a"]["rework_summary"] == "A done"
        assert by_sid["sess_b"]["rework_succeeded"] is None
        assert by_sid["sess_b"]["rework_summary"] is None


class TestCursorRecovery:
    """Recovery when the .cursor sidecar is missing/corrupt — mirrors MemoryStore."""

    def test_corrupt_cursor_in_last_entry_falls_back_to_scan(self, logger):
        wf = "doc_mutation"
        path = logger.base_dir / f"{wf}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"cursor": 5, "session_id": "old1", "rework_succeeded": True}) + "\n"
            + json.dumps({"cursor": 6, "session_id": "old2", "rework_succeeded": True}) + "\n"
            + json.dumps({"cursor": "bad", "session_id": "corrupt", "rework_succeeded": True}) + "\n",
            encoding="utf-8",
        )
        # Ensure no cursor sidecar so logger has to scan the JSONL
        cursor_file = logger.base_dir / f"{wf}.cursor"
        if cursor_file.exists():
            cursor_file.unlink()

        c = logger.log_rework(
            workflow_name=wf,
            session_id="sess_new",
            classification_certainty="high",
            original_inputs={},
            preview_summary="p",
            user_feedback="f",
        )
        assert c == 7

    def test_all_corrupt_cursors_restart_at_one(self, logger):
        wf = "doc_mutation"
        path = logger.base_dir / f"{wf}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"cursor": "a", "session_id": "x", "rework_succeeded": True}) + "\n"
            + json.dumps({"cursor": "b", "session_id": "y", "rework_succeeded": True}) + "\n",
            encoding="utf-8",
        )
        cursor_file = logger.base_dir / f"{wf}.cursor"
        if cursor_file.exists():
            cursor_file.unlink()

        c = logger.log_rework(
            workflow_name=wf,
            session_id="sess_new",
            classification_certainty="high",
            original_inputs={},
            preview_summary="p",
            user_feedback="f",
        )
        assert c == 1
