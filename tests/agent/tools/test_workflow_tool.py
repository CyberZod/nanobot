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


@pytest.fixture
def tool():
    return WorkflowTool(tools=None)


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
