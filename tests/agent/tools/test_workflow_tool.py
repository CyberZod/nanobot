"""Tests for WorkflowTool session metadata tracking.

The tool maintains a per-session metadata dict (`_sessions`) that captures
workflow_name, original_inputs, previewed-state, and the latest preview
outputs. Required so downstream feedback logging has the data it needs
when a free-text correction lands on the message branch.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

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
        # Most recent decoded request body the tool sent to the bridge.
        # Tests use this to verify the request shape.
        self.last_request: dict = {}

    def set_response(self, response: dict, *, returncode: int = 0) -> None:
        self._next_response = response
        self._returncode = returncode

    def _build_proc(self) -> MagicMock:
        proc = MagicMock()
        proc.returncode = self._returncode
        stdout = (json.dumps(self._next_response) + "\n").encode("utf-8")
        outer = self

        async def capture_communicate(input=None):
            if input:
                try:
                    outer.last_request = json.loads(input.decode("utf-8"))
                except Exception:
                    outer.last_request = {}
            return (stdout, b"")

        proc.communicate = capture_communicate
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
            action="execute",
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
        await tool.execute(action="execute", workflow_name="doc_mutation", inputs={})

        mock_bridge.set_response({
            "session_id": "sess_abc",
            "status": "complete",
            "outputs": {"modified_pdf": "/tmp/v1.pdf"},
            "session_workdir": "/tmp/sess_abc",
        })
        await tool.execute(action="preview", session_id="sess_abc")

        meta = tool._sessions["sess_abc"]
        assert meta.previewed is True
        assert meta.preview_outputs == {"modified_pdf": "/tmp/v1.pdf"}

    @pytest.mark.asyncio
    async def test_preview_with_error_does_not_mark_previewed(self, tool, mock_bridge):
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(action="execute", workflow_name="doc_mutation", inputs={})

        mock_bridge.set_response({"session_id": "sess_abc", "error": "render failed"})
        await tool.execute(action="preview", session_id="sess_abc")

        assert tool._sessions["sess_abc"].previewed is False
        assert tool._sessions["sess_abc"].preview_outputs is None

    @pytest.mark.asyncio
    async def test_finalize_blocked_without_preview(self, tool, mock_bridge):
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(action="execute", workflow_name="doc_mutation", inputs={})

        result = await tool.execute(action="finalize", session_id="sess_abc")
        assert "preview first" in json.loads(result)["error"]

    @pytest.mark.asyncio
    async def test_finalize_blocked_for_unknown_session(self, tool, mock_bridge):
        result = await tool.execute(action="finalize", session_id="sess_unknown")
        assert "preview first" in json.loads(result)["error"]

    @pytest.mark.asyncio
    async def test_finalize_allowed_after_successful_preview(self, tool, mock_bridge):
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(action="execute", workflow_name="doc_mutation", inputs={})

        mock_bridge.set_response({
            "session_id": "sess_abc",
            "status": "complete",
            "outputs": {"modified_pdf": "/tmp/v1.pdf"},
            "session_workdir": "/tmp/sess_abc",
        })
        await tool.execute(action="preview", session_id="sess_abc")

        mock_bridge.set_response({"session_id": "sess_abc", "finalized": True})
        result = await tool.execute(action="finalize", session_id="sess_abc")
        parsed = json.loads(result)
        assert "preview first" not in parsed.get("error", "")

    @pytest.mark.asyncio
    async def test_freetext_followup_preserves_session_metadata(self, tool, mock_bridge):
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(
            action="execute",
            workflow_name="doc_mutation",
            inputs={"x": 1},
        )

        mock_bridge.set_response({
            "session_id": "sess_abc",
            "status": "complete",
            "outputs": {"modified_pdf": "/tmp/v1.pdf"},
            "session_workdir": "/tmp/sess_abc",
        })
        await tool.execute(action="preview", session_id="sess_abc")

        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(action="message", message="add the underline", session_id="sess_abc")

        meta = tool._sessions["sess_abc"]
        assert meta.workflow_name == "doc_mutation"
        assert meta.original_inputs == {"x": 1}
        assert meta.previewed is True


class TestUserCorrectionsThreading:
    """Manager passes user_corrections on finalize; bridge request carries it.

    Self-annealing capture (see SELF_ANNEALING_PLAN.md §3 + §5). The
    WorkflowTool is a dumb relay for the manager's structured data — it
    does NOT compose reflection prompts. It threads `user_corrections`
    (when present) and `notes_dir` through to the bridge on the finalize
    action only. The bridge owns the prompt template and the friction
    gate.
    """

    @pytest.mark.asyncio
    async def test_finalize_with_user_corrections_threads_to_bridge(self, tool, mock_bridge):
        # Set up a previewed session so finalize gate passes
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(action="execute", workflow_name="doc_mutation", inputs={})
        mock_bridge.set_response({
            "session_id": "sess_abc",
            "status": "complete",
            "outputs": {"modified_pdf": "/tmp/v1.pdf"},
        })
        await tool.execute(action="preview", session_id="sess_abc")

        # Now finalize with user_corrections
        mock_bridge.set_response({
            "session_id": "sess_abc",
            "status": "complete",
            "outputs": {},
            "reflection_note_written": True,
        })
        corrections = [
            "the underline is missing on 'abeg'",
            "the page numbers got shifted",
        ]
        await tool.execute(
            action="finalize",
            session_id="sess_abc",
            user_corrections=corrections,
        )

        req = mock_bridge.last_request
        assert req.get("action") == "finalize"
        assert req.get("user_corrections") == corrections
        assert req.get("notes_dir"), f"notes_dir missing from request: {req}"

    @pytest.mark.asyncio
    async def test_finalize_without_corrections_threads_notes_dir_only(self, tool, mock_bridge):
        """Empty/missing user_corrections → notes_dir still threaded so the bridge
        can write a note if the workflow itself ended in failure (the bridge's
        own friction gate fires on outcome=failed even without corrections)."""
        # Set up a previewed session
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(action="execute", workflow_name="doc_mutation", inputs={})
        mock_bridge.set_response({
            "session_id": "sess_abc",
            "status": "complete",
            "outputs": {"modified_pdf": "/tmp/v1.pdf"},
        })
        await tool.execute(action="preview", session_id="sess_abc")

        # Finalize without user_corrections
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(action="finalize", session_id="sess_abc")

        req = mock_bridge.last_request
        # user_corrections absent (manager omitted), but notes_dir present
        # so the bridge can still reflect on failed-outcome sessions.
        assert "user_corrections" not in req
        assert req.get("notes_dir")

    @pytest.mark.asyncio
    async def test_user_corrections_ignored_on_non_finalize_actions(self, tool, mock_bridge):
        # Even if the manager passes user_corrections on execute (a bug),
        # it should NOT be sent to the bridge for non-finalize actions.
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(
            action="execute",
            workflow_name="doc_mutation",
            inputs={"x": 1},
            user_corrections=["this should be ignored"],
        )

        req = mock_bridge.last_request
        assert req.get("action") == "execute"
        assert "user_corrections" not in req
        assert "notes_dir" not in req


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
            action="execute",
            workflow_name="doc_mutation",
            inputs={"x": 1},
        )
        parsed = json.loads(result)
        assert "user_facing_note" in parsed["error"]
        # Error identifies the slow-call kind. Phrasing was tightened in
        # the action-based rewrite to "action='execute' (initial)".
        assert "execute" in parsed["error"]
        assert "initial" in parsed["error"]

    @pytest.mark.asyncio
    async def test_iteration_without_note_returns_error(self, raw_tool, mock_bridge):
        # Seed a session so the iteration path is reachable.
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await raw_tool.execute(
            action="execute",
            workflow_name="doc_mutation",
            inputs={"x": 1},
            user_facing_note="On it.",
        )
        result = await raw_tool.execute(
            action="message",
            message="add the underline",
            session_id="sess_abc",
        )
        parsed = json.loads(result)
        assert "user_facing_note" in parsed["error"]
        # Error text identifies the slow-call shape; the kind label was
        # rephrased in the action-based rewrite to "action='message'
        # (feedback follow-up)" — keep both fragments asserted so we
        # catch unintentional drift either way.
        assert "feedback follow-up" in parsed["error"]
        assert "message" in parsed["error"]

    @pytest.mark.asyncio
    async def test_blank_note_treated_as_missing(self, raw_tool):
        result = await raw_tool.execute(
            action="execute",
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
        result = await raw_tool.execute(action="list_workflows")
        assert "error" not in json.loads(result) or "user_facing_note" not in json.loads(result).get("error", "")


class TestExplicitActionParameter:
    """`action` is the required, canonical dispatch field. `message` is
    strictly free-text content sent to the workflow agent — never parsed
    as an action keyword.

    Closes the dispatch-overload class of bugs that produced
    sess_776060606d1f (a free-text `message` with empty session_id used
    to fall through to a new-session-with-no-inputs branch, forcing the
    workflow agent to fabricate `input.pdf` and silently substitute from
    workflow assets)."""

    @pytest.fixture
    def raw_tool(self):
        # Bypass the default-note injection in the `tool` fixture.
        # These tests verify dispatch & validation, which happen before
        # the user_facing_note gate; the gate is exercised separately
        # in TestUserFacingNoteEnforcement.
        return WorkflowTool(tools=None)

    @pytest.mark.asyncio
    async def test_action_execute_routes_to_bridge_execute(self, tool, mock_bridge):
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(
            action="execute",
            workflow_name="doc_mutation",
            inputs={"x": 1},
        )
        assert mock_bridge.last_request["action"] == "execute"
        assert mock_bridge.last_request["workflow_name"] == "doc_mutation"
        assert mock_bridge.last_request["inputs"] == {"x": 1}

    @pytest.mark.asyncio
    async def test_action_message_requires_session_id(self, raw_tool):
        """The load-bearing test. Free-text bootstrapping of a new workflow
        is no longer possible — manager must use action='execute' with
        structured inputs to start a session. This is the structural fix
        for sess_776060606d1f's input fabrication."""
        result = await raw_tool.execute(
            action="message",
            message="Use the doc_mutation workflow on this PDF...",
            user_facing_note="On it",
        )
        parsed = json.loads(result)
        assert "session_id" in parsed["error"].lower()
        # Error should redirect the manager to the correct path.
        assert "execute" in parsed["error"].lower()

    @pytest.mark.asyncio
    async def test_action_message_with_session_id_routes_through(self, tool, mock_bridge):
        # Seed a session via execute.
        mock_bridge.set_response({"session_id": "sess_abc", "status": "complete"})
        await tool.execute(
            action="execute",
            workflow_name="doc_mutation",
            inputs={"x": 1},
        )
        # Rework round.
        mock_bridge.set_response({"session_id": "sess_abc", "status": "in_progress"})
        await tool.execute(
            action="message",
            message="add the underline",
            session_id="sess_abc",
            user_facing_note="Got it",
        )
        # Bridge receives the free-text body verbatim.
        assert mock_bridge.last_request.get("message") == "add the underline"
        assert mock_bridge.last_request.get("session_id") == "sess_abc"

    @pytest.mark.asyncio
    async def test_missing_action_returns_error(self, raw_tool):
        # Calling with just workflow_name + inputs no longer infers execute.
        result = await raw_tool.execute(
            workflow_name="doc_mutation",
            inputs={"x": 1},
        )
        parsed = json.loads(result)
        assert "action" in parsed["error"].lower()
        assert "required" in parsed["error"].lower()

    @pytest.mark.asyncio
    async def test_legacy_magic_string_message_no_longer_dispatches(self, raw_tool):
        # `message="execute"` used to be a magic string meaning "do an
        # execute action". Now `message` is strictly free-text content;
        # without `action`, this errors out (no inference). Keeps the
        # parameter contract clean and forces the manager to be explicit.
        result = await raw_tool.execute(
            message="execute",
            workflow_name="doc_mutation",
            inputs={"x": 1},
        )
        parsed = json.loads(result)
        assert "action" in parsed["error"].lower()

    @pytest.mark.asyncio
    async def test_action_execute_requires_workflow_name_and_inputs(self, raw_tool):
        # No workflow_name, no inputs — workflow_name error fires first.
        result = await raw_tool.execute(
            action="execute",
            user_facing_note="On it",
        )
        parsed = json.loads(result)
        assert "workflow_name" in parsed["error"].lower()

        # Workflow_name present, inputs missing — inputs error fires.
        # Note: `inputs={}` is allowed (workflow may take no inputs);
        # only `inputs=None`/omitted is rejected here.
        result = await raw_tool.execute(
            action="execute",
            workflow_name="doc_mutation",
            user_facing_note="On it",
        )
        parsed = json.loads(result)
        assert "inputs" in parsed["error"].lower()

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self, raw_tool):
        result = await raw_tool.execute(action="explode")
        parsed = json.loads(result)
        assert "action" in parsed["error"].lower()
        assert "explode" in parsed["error"].lower()

