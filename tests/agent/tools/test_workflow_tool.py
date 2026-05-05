"""Tests for WorkflowTool session metadata tracking.

The tool maintains a per-session metadata dict (`_sessions`) that captures
workflow_name, original_inputs, previewed-state, and the latest preview
outputs. Required so downstream feedback logging has the data it needs
when a free-text correction lands on the message branch.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.tools.workflow import WorkflowTool


def _patch_default_note(t: WorkflowTool) -> WorkflowTool:
    """Inject a default `user_facing_note` so existing tests focused on
    other behavior don't need to thread the kwarg everywhere. The tool
    requires it on slow paths (execute / free-text iterations) and
    ignores it on fast actions (preview / finalize / etc.)."""
    original = t.execute

    async def patched(*args, **kwargs):
        kwargs.setdefault("user_facing_note", "test note")
        return await original(*args, **kwargs)

    t.execute = patched  # type: ignore[method-assign]
    return t


@pytest.fixture
def tool():
    return _patch_default_note(WorkflowTool(tools=None))


class _MockBridge:
    """Patches asyncio.create_subprocess_exec to return a configurable JSON response.

    Each test calls `set_response(...)` to stage what the next bridge call
    should return; subsequent `tool.execute(...)` calls use the staged value.
    """

    def __init__(self) -> None:
        self._next_response: dict = {}
        self._returncode: int = 0

    def set_response(self, response: dict, *, returncode: int = 0) -> None:
        self._next_response = response
        self._returncode = returncode

    def _build_proc(self) -> MagicMock:
        proc = MagicMock()
        proc.returncode = self._returncode
        stdout = (json.dumps(self._next_response) + "\n").encode("utf-8")
        proc.communicate = AsyncMock(return_value=(stdout, b""))
        return proc


@pytest.fixture
def mock_bridge(monkeypatch):
    bridge = _MockBridge()

    async def fake_exec(*_args, **_kwargs):
        return bridge._build_proc()

    monkeypatch.setattr(
        "nanobot.agent.tools.workflow.asyncio.create_subprocess_exec",
        fake_exec,
    )
    return bridge


class TestSessionTracking:
    """Per-session metadata captured at the right tool-call moments."""

    @pytest.mark.asyncio
    async def test_execute_creates_session_entry_with_inputs(self, tool, mock_bridge):
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})

        await tool.execute(
            message="execute",
            workflow_name="doc_mutation",
            inputs={"file": "letter.pdf", "Instructions": "kindly->abeg"},
        )

        meta = tool._sessions["sess_abc"]
        assert meta.workflow_name == "doc_mutation"
        assert meta.original_inputs == {"file": "letter.pdf", "Instructions": "kindly->abeg"}
        assert meta.previewed is False
        assert meta.preview_outputs is None

    @pytest.mark.asyncio
    async def test_preview_marks_session_previewed_and_populates_outputs(self, tool, mock_bridge):
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(message="execute", workflow_name="doc_mutation", inputs={})

        mock_bridge.set_response({
            "session_id": "sess_abc",
            "status": "complete",
            "outputs": {"modified_pdf": "/tmp/v1.pdf"},
            "session_workdir": "/tmp/sess_abc",
        })
        await tool.execute(message="preview", session_id="sess_abc")

        meta = tool._sessions["sess_abc"]
        assert meta.previewed is True
        assert meta.preview_outputs == {"modified_pdf": "/tmp/v1.pdf"}

    @pytest.mark.asyncio
    async def test_preview_with_error_does_not_mark_previewed(self, tool, mock_bridge):
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(message="execute", workflow_name="doc_mutation", inputs={})

        mock_bridge.set_response({"session_id": "sess_abc", "error": "render failed"})
        await tool.execute(message="preview", session_id="sess_abc")

        assert tool._sessions["sess_abc"].previewed is False
        assert tool._sessions["sess_abc"].preview_outputs is None

    @pytest.mark.asyncio
    async def test_finalize_blocked_without_preview(self, tool, mock_bridge):
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(message="execute", workflow_name="doc_mutation", inputs={})

        result = await tool.execute(message="finalize", session_id="sess_abc")
        assert "preview first" in json.loads(result)["error"]

    @pytest.mark.asyncio
    async def test_finalize_blocked_for_unknown_session(self, tool, mock_bridge):
        result = await tool.execute(message="finalize", session_id="sess_unknown")
        assert "preview first" in json.loads(result)["error"]

    @pytest.mark.asyncio
    async def test_finalize_allowed_after_successful_preview(self, tool, mock_bridge):
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(message="execute", workflow_name="doc_mutation", inputs={})

        mock_bridge.set_response({
            "session_id": "sess_abc",
            "status": "complete",
            "outputs": {"modified_pdf": "/tmp/v1.pdf"},
            "session_workdir": "/tmp/sess_abc",
        })
        await tool.execute(message="preview", session_id="sess_abc")

        mock_bridge.set_response({"session_id": "sess_abc", "finalized": True})
        result = await tool.execute(message="finalize", session_id="sess_abc")
        parsed = json.loads(result)
        assert "preview first" not in parsed.get("error", "")

    @pytest.mark.asyncio
    async def test_freetext_followup_preserves_session_metadata(self, tool, mock_bridge):
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(
            message="execute",
            workflow_name="doc_mutation",
            inputs={"x": 1},
        )

        mock_bridge.set_response({
            "session_id": "sess_abc",
            "status": "complete",
            "outputs": {"modified_pdf": "/tmp/v1.pdf"},
            "session_workdir": "/tmp/sess_abc",
        })
        await tool.execute(message="preview", session_id="sess_abc")

        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(message="add the underline", session_id="sess_abc")

        meta = tool._sessions["sess_abc"]
        assert meta.workflow_name == "doc_mutation"
        assert meta.original_inputs == {"x": 1}
        assert meta.previewed is True


class TestUserFacingNoteEnforcement:
    """Slow-path calls (execute / free-text iterations) require user_facing_note."""

    @pytest.fixture
    def raw_tool(self):
        # Bypass the default-note injection in the `tool` fixture; this
        # class needs to verify the enforcement itself.
        return WorkflowTool(tools=None)

    @pytest.mark.asyncio
    async def test_initial_execute_without_note_returns_error(self, raw_tool):
        # No mock_bridge needed — enforcement short-circuits before the
        # bridge call, so the subprocess never runs.
        result = await raw_tool.execute(
            message="execute",
            workflow_name="doc_mutation",
            inputs={"x": 1},
        )
        parsed = json.loads(result)
        assert "user_facing_note" in parsed["error"]
        assert "initial execute" in parsed["error"]

    @pytest.mark.asyncio
    async def test_iteration_without_note_returns_error(self, raw_tool, mock_bridge):
        # Seed a session so the iteration path is reachable.
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await raw_tool.execute(
            message="execute",
            workflow_name="doc_mutation",
            inputs={"x": 1},
            user_facing_note="On it.",
        )
        result = await raw_tool.execute(
            message="add the underline",
            session_id="sess_abc",
        )
        parsed = json.loads(result)
        assert "user_facing_note" in parsed["error"]
        assert "feedback follow-up" in parsed["error"]

    @pytest.mark.asyncio
    async def test_blank_note_treated_as_missing(self, raw_tool):
        result = await raw_tool.execute(
            message="execute",
            workflow_name="doc_mutation",
            inputs={"x": 1},
            user_facing_note="   ",
        )
        parsed = json.loads(result)
        assert "user_facing_note" in parsed["error"]

    @pytest.mark.asyncio
    async def test_fast_actions_do_not_require_note(self, raw_tool, mock_bridge):
        # preview, finalize, validate_inputs, list_workflows must all work
        # without a note (they're synchronous-feeling and need no heads-up).
        mock_bridge.set_response({"workflows": []})
        result = await raw_tool.execute(message="list_workflows")
        assert "error" not in json.loads(result) or "user_facing_note" not in json.loads(result).get("error", "")


class TestFeedbackHook:
    """WorkflowTool routes correction events into FeedbackLogger correctly."""

    @pytest.fixture
    def mock_logger(self):
        m = MagicMock()
        m.log_rework = MagicMock(return_value=1)
        m.mark_rework_outcome = MagicMock(return_value=True)
        return m

    @pytest.fixture
    def tool_with_logger(self, mock_logger):
        return _patch_default_note(
            WorkflowTool(tools=None, feedback_logger=mock_logger)
        )

    async def _arrive_at_post_preview(self, tool, mock_bridge):
        """Bring a tool through execute → preview so a correction message has session metadata."""
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(
            message="execute",
            workflow_name="doc_mutation",
            inputs={"file": "letter.pdf", "Instructions": "kindly->abeg"},
        )
        mock_bridge.set_response({
            "session_id": "sess_abc",
            "status": "complete",
            "outputs": {
                "modified_pdf": "/tmp/v1.pdf",
                "summary": "Replaced kindly with abeg in 4 places",
            },
            "session_workdir": "/tmp/sess_abc",
        })
        await tool.execute(message="preview", session_id="sess_abc")

    @pytest.mark.asyncio
    async def test_correction_high_certainty_logs_open_then_close(
        self, tool_with_logger, mock_bridge, mock_logger,
    ):
        await self._arrive_at_post_preview(tool_with_logger, mock_bridge)

        mock_bridge.set_response({
            "session_id": "sess_abc",
            "status": "complete",
            "response": "I've added the underline back on 'abeg'.",
        })
        await tool_with_logger.execute(
            message="the underline is missing on 'abeg'",
            session_id="sess_abc",
            classification="correction",
            classification_certainty="high",
        )

        # log_rework called once on the way in
        assert mock_logger.log_rework.call_count == 1
        log_kwargs = mock_logger.log_rework.call_args.kwargs
        assert log_kwargs["workflow_name"] == "doc_mutation"
        assert log_kwargs["session_id"] == "sess_abc"
        assert log_kwargs["classification_certainty"] == "high"
        assert log_kwargs["original_inputs"] == {
            "file": "letter.pdf", "Instructions": "kindly->abeg",
        }
        assert log_kwargs["preview_summary"] == "Replaced kindly with abeg in 4 places"
        assert log_kwargs["user_feedback"] == "the underline is missing on 'abeg'"

        # mark_rework_outcome called once on the way out, succeeded=True
        assert mock_logger.mark_rework_outcome.call_count == 1
        close_kwargs = mock_logger.mark_rework_outcome.call_args.kwargs
        assert close_kwargs["workflow_name"] == "doc_mutation"
        assert close_kwargs["session_id"] == "sess_abc"
        assert close_kwargs["succeeded"] is True
        assert "underline" in close_kwargs["summary"]

    @pytest.mark.asyncio
    async def test_correction_low_certainty_skips_log(
        self, tool_with_logger, mock_bridge, mock_logger,
    ):
        await self._arrive_at_post_preview(tool_with_logger, mock_bridge)

        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool_with_logger.execute(
            message="hmm, the styling looks off",
            session_id="sess_abc",
            classification="correction",
            classification_certainty="low",
        )

        # Safety valve: low certainty drops the log entirely
        assert mock_logger.log_rework.call_count == 0
        assert mock_logger.mark_rework_outcome.call_count == 0

    @pytest.mark.asyncio
    async def test_extension_skips_log(
        self, tool_with_logger, mock_bridge, mock_logger,
    ):
        await self._arrive_at_post_preview(tool_with_logger, mock_bridge)

        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool_with_logger.execute(
            message="can you also do this for page 2?",
            session_id="sess_abc",
            classification="extension",
            classification_certainty="high",
        )

        # Extensions don't feed distillation
        assert mock_logger.log_rework.call_count == 0
        assert mock_logger.mark_rework_outcome.call_count == 0

    @pytest.mark.asyncio
    async def test_correction_without_session_metadata_skips_log(
        self, tool_with_logger, mock_bridge, mock_logger,
    ):
        # No prior execute/preview on this tool instance — _sessions is empty
        mock_bridge.set_response({"session_id": "sess_xyz", "status": "complete"})
        await tool_with_logger.execute(
            message="the underline is missing",
            session_id="sess_xyz",
            classification="correction",
            classification_certainty="high",
        )

        # Can't safely populate workflow_name → skip rather than write a corrupt row
        assert mock_logger.log_rework.call_count == 0
        assert mock_logger.mark_rework_outcome.call_count == 0

    @pytest.mark.asyncio
    async def test_bridge_error_records_failed_outcome(
        self, tool_with_logger, mock_bridge, mock_logger,
    ):
        await self._arrive_at_post_preview(tool_with_logger, mock_bridge)

        mock_bridge.set_response({
            "session_id": "sess_abc",
            "error": "PyMuPDF render failed",
        })
        await tool_with_logger.execute(
            message="the underline is missing on 'abeg'",
            session_id="sess_abc",
            classification="correction",
            classification_certainty="high",
        )

        # Open event written on the way in
        assert mock_logger.log_rework.call_count == 1
        # Closed on the way out with succeeded=False
        assert mock_logger.mark_rework_outcome.call_count == 1
        close_kwargs = mock_logger.mark_rework_outcome.call_args.kwargs
        assert close_kwargs["succeeded"] is False

    @pytest.mark.asyncio
    async def test_finalize_does_not_call_logger(
        self, tool_with_logger, mock_bridge, mock_logger,
    ):
        await self._arrive_at_post_preview(tool_with_logger, mock_bridge)

        mock_bridge.set_response({"session_id": "sess_abc", "finalized": True})
        await tool_with_logger.execute(
            message="finalize",
            session_id="sess_abc",
        )

        # Finalize is a workflow action — prior rework already closed itself
        assert mock_logger.log_rework.call_count == 0
        assert mock_logger.mark_rework_outcome.call_count == 0

    @pytest.mark.asyncio
    async def test_preview_summary_falls_back_to_agent_response(
        self, tool_with_logger, mock_bridge, mock_logger,
    ):
        # Execute that does NOT include a "summary" field in outputs but
        # does include the agent's "response" text.
        mock_bridge.set_response({
            "session_id": "sess_abc",
            "status": "complete",
            "response": "I created a draft with kindly replaced by abeg in 4 places.",
        })
        await tool_with_logger.execute(
            message="execute",
            workflow_name="doc_mutation",
            inputs={"file": "letter.pdf"},
        )
        # Preview returns outputs without a 'summary' key
        mock_bridge.set_response({
            "session_id": "sess_abc",
            "status": "complete",
            "outputs": {"modified_pdf": "/tmp/v1.pdf"},
            "session_workdir": "/tmp/sess_abc",
        })
        await tool_with_logger.execute(message="preview", session_id="sess_abc")

        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool_with_logger.execute(
            message="the underline is missing",
            session_id="sess_abc",
            classification="correction",
            classification_certainty="high",
        )

        assert mock_logger.log_rework.call_count == 1
        log_kwargs = mock_logger.log_rework.call_args.kwargs
        # Falls through path 1 (no summary in outputs) → path 2 (agent's response)
        assert log_kwargs["preview_summary"] == (
            "I created a draft with kindly replaced by abeg in 4 places."
        )

    @pytest.mark.asyncio
    async def test_preview_summary_warns_when_neither_source_available(
        self, tool_with_logger, mock_bridge, mock_logger, caplog,
    ):
        import logging
        caplog.set_level(logging.WARNING)
        # Execute response with NO 'response' field captured
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool_with_logger.execute(
            message="execute",
            workflow_name="doc_mutation",
            inputs={"file": "letter.pdf"},
        )
        # Preview without a 'summary' output
        mock_bridge.set_response({
            "session_id": "sess_abc",
            "status": "complete",
            "outputs": {"modified_pdf": "/tmp/v1.pdf"},
        })
        await tool_with_logger.execute(message="preview", session_id="sess_abc")

        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool_with_logger.execute(
            message="the underline is missing",
            session_id="sess_abc",
            classification="correction",
            classification_certainty="high",
        )

        assert mock_logger.log_rework.call_count == 1
        log_kwargs = mock_logger.log_rework.call_args.kwargs
        assert log_kwargs["preview_summary"] == ""
