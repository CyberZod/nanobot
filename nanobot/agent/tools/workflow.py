"""Workflow tool for delegating tasks to the Maroc workflow agency.

Uses a subprocess bridge for full isolation — GenAI runs in its own
venv with its own dependencies. Communication is via JSON over stdin/stdout.
"""

import asyncio
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger("workflow_tool")

from nanobot.agent.feedback import FeedbackLogger
from nanobot.agent.tools.base import Tool

# Maroc agency paths — hardcoded for Phase 1
AGENCY_PATH = Path("C:/Users/user/Documents/Dev/Agentic Workflows/GenAI")
BRIDGE_SCRIPT = AGENCY_PATH / "workflow_bridge.py"
AGENCY_PYTHON = AGENCY_PATH / ".venv" / "Scripts" / "python.exe"
LOG_DIR = Path.home() / ".nanobot" / "workspace" / "workflow_logs"
MEDIA_DIR = Path.home() / ".nanobot" / "media" / "workflow_outputs"
FEEDBACK_DIR = Path.home() / ".nanobot" / "workspace" / "feedback_logs"

# Action keywords that route to the bridge as workflow actions, not free-text
# messages. Used to gate the feedback hook to message-branch calls only.
_ACTION_KEYWORDS = frozenset({
    "execute", "preview", "finalize", "validate_inputs", "list_workflows",
})

# Fallback heads-ups sent to the user before a (slow) workflow call.
# The agent is expected to pass a tailored `user_facing_note` on execute and
# feedback follow-ups; these pools are only used when the agent omits one. We
# pick at random for variety so the user doesn't see the same canned line on
# back-to-back runs.
START_ANNOUNCEMENTS = [
    "On it — I'll let you know once it's ready, and ping you if I need anything.",
    "Got it — starting now, I'll send the result over shortly.",
    "Sure thing — working on it, I'll be back with the result in a moment.",
    "On it now — I'll send it through as soon as it's done.",
]
FOLLOWUP_ANNOUNCEMENTS = [
    "Got it — working on the changes now.",
    "On it — I'll send the updated version shortly.",
    "Understood — making the changes now.",
    "Sure — applying the changes, one moment.",
]


@dataclass
class _SessionMeta:
    workflow_name: str | None = None
    original_inputs: dict | None = None
    previewed: bool = False
    preview_outputs: dict | None = None
    last_agent_response: str | None = None


def _build_preview_summary(meta: _SessionMeta) -> str:
    """Distill a short text summary of what was shown to the user.

    Two-source ladder, in priority order:
      1. Workflow-declared 'summary' value-output (richest signal — opt-in by
         the workflow author via the outputs schema).
      2. The agent's final response text from the most-recent run, captured
         on execute / message turns from the bridge's 'response' field.
    If neither is available we warn and return empty — a structural smell
    (bridge response shape unexpected, or session metadata lost) worth
    surfacing in logs rather than silently writing a useless placeholder.
    """
    if meta.preview_outputs and isinstance(meta.preview_outputs, dict):
        s = meta.preview_outputs.get("summary")
        if isinstance(s, str) and s.strip():
            return s.strip()
    if meta.last_agent_response and meta.last_agent_response.strip():
        return meta.last_agent_response.strip()[:500]
    logger.warning(
        "preview_summary fallback hit for workflow={} — no declared 'summary' "
        "output AND no captured agent response. Distiller will see empty.",
        meta.workflow_name,
    )
    return ""


class WorkflowTool(Tool):
    """Send messages to the Maroc workflow agency to execute workflows."""

    def __init__(
        self,
        tools: Any = None,
        feedback_logger: FeedbackLogger | None = None,
    ) -> None:
        # Per-session metadata: workflow name + inputs from the originating
        # execute call, whether preview has been shown (gates finalize), and
        # the latest preview outputs. The feedback hook reads this.
        self._sessions: dict[str, _SessionMeta] = {}
        # Optional ToolRegistry — used to look up the 'message' tool for the
        # start announcement. Pass the agent loop's registry at construction.
        self._tools = tools
        # When set, free-text correction messages are recorded for self-
        # annealing (see SELF_ANNEALING_PLAN.md). None = logging disabled.
        self._feedback_logger = feedback_logger

    @property
    def name(self) -> str:
        return "workflow"

    @property
    def description(self) -> str:
        return (
            "Send a message to the Maroc workflow agency to execute structured workflows. "
            "Typical flow: validate_inputs -> execute -> preview (show user for approval) -> "
            "finalize (after approval). Use free-text messages with session_id to pass user "
            "feedback/iterations in between. Omit session_id on the first call; pass the "
            "returned session_id on every follow-up. "
            "Always pass `user_facing_note` on `execute` and feedback follow-ups — a brief, "
            "conversational heads-up sent to the user before the (slow) bridge call so they "
            "know work has started. "
            "On free-text follow-ups after a preview, also pass `classification` "
            "(correction|extension) and `classification_certainty` (high|medium|low). "
            "If user feedback is ambiguous, ask a clarifying question first (do NOT call "
            "this tool); only call once intent is clear."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": (
                        "The message or action: a free-text message to the agency, "
                        "or one of: 'execute', 'validate_inputs', 'list_workflows', 'preview', 'finalize'. "
                        "Defaults to 'execute' when workflow_name and inputs are provided."
                    ),
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "Session ID for an ongoing workflow. "
                        "Omit or leave empty to start a new workflow session."
                    ),
                },
                "workflow_name": {
                    "type": "string",
                    "description": "Workflow name (for execute or validate_inputs)",
                },
                "inputs": {
                    "type": "object",
                    "description": "Input key-value pairs (for execute or validate_inputs)",
                },
                "user_facing_note": {
                    "type": "string",
                    "description": (
                        "Brief, user-facing acknowledgement sent to the user before this "
                        "call runs. Required on `execute` (initial) and on free-text "
                        "feedback follow-ups, both of which can block for 1-3 minutes. "
                        "Plain conversational language addressed to the user — no internal "
                        "reasoning, no tool names, no JSON, no 'I will…' planning narration. "
                        "Examples: 'On it, I'll send the draft shortly.' / 'Got it — fixing "
                        "the underline now.' / 'Reworking the totals, one moment.' "
                        "Ignored for preview, finalize, validate_inputs, list_workflows."
                    ),
                },
                "classification": {
                    "type": "string",
                    "enum": ["correction", "extension"],
                    "description": (
                        "Set ONLY on free-text follow-up messages after a preview, when you "
                        "judge what the user is asking for. "
                        "'correction' = user is pointing to a defect or misalignment with "
                        "their original intent. "
                        "'extension' = user wants additional/different work, NOT a defect "
                        "in the existing output. "
                        "When intent is ambiguous, ask a clarifying question first (do NOT "
                        "call this tool) and only set classification once intent is clear. "
                        "When the user clearly approves (e.g. 'looks good', 'ship it'), "
                        "call action='finalize' instead. "
                        "Omit on initial execute and on workflow-action calls."
                    ),
                },
                "classification_certainty": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": (
                        "Required alongside `classification`. Your self-reported certainty. "
                        "'low' means a best-guess — the system still runs the rework but "
                        "won't add the event to its learning corpus. Prefer asking a "
                        "clarifying question to upgrade certainty rather than logging a "
                        "low-certainty guess."
                    ),
                },
            },
            "required": [],
        }

    async def _announce(self, text: str) -> str | None:
        """
        Send a heads-up to the user before a slow bridge call.

        Uses the registered `message` tool (looked up on the shared
        ToolRegistry passed at construction). Relies on the message tool's
        default channel/chat_id ContextVars, which are set per-turn by the
        agent loop — so no explicit routing info is needed here.

        Returns the text on success, None if no message was sent (tool
        unavailable, registry missing, or send failed). The caller uses the
        return to decide whether to echo `announced_to_user` back to the
        agent — only what actually reached the user gets recorded.
        """
        if not self._tools:
            return None
        msg_tool = self._tools.get("message")
        if msg_tool is None:
            return None
        try:
            await msg_tool.execute(content=text)
            return text
        except Exception as exc:
            logger.warning("Workflow announcement failed: {}", exc)
            return None

    async def execute(
        self,
        message: str | None = None,
        session_id: str | None = None,
        workflow_name: str | None = None,
        inputs: dict | None = None,
        user_facing_note: str | None = None,
        classification: str | None = None,
        classification_certainty: str | None = None,
        **kwargs: Any,
    ) -> str:
        # Infer action when message is missing
        if not message and workflow_name and inputs:
            message = "execute"
        elif not message and workflow_name:
            message = "validate_inputs"
        elif not message:
            return json.dumps({"error": "Provide a message or workflow_name + inputs"})

        msg_lower = message.strip().lower()

        if msg_lower == "list_workflows":
            request = {"action": "list_workflows"}
        elif msg_lower == "validate_inputs":
            request = {
                "action": "validate_inputs",
                "workflow_name": workflow_name or "",
                "inputs": inputs or {},
            }
        elif msg_lower == "execute":
            request = {
                "action": "execute",
                "workflow_name": workflow_name or "",
                "inputs": inputs or {},
                "log_dir": str(LOG_DIR),
                "user_id": "nanobot",
            }
        elif msg_lower == "preview" and session_id:
            request = {
                "action": "preview",
                "session_id": session_id,
                "user_id": "nanobot",
            }
        elif msg_lower == "finalize" and session_id:
            meta = self._sessions.get(session_id)
            if meta is None or not meta.previewed:
                return json.dumps({
                    "error": (
                        "finalize requires preview first. Call `preview` with this "
                        "session_id, show the user the outputs, and only call `finalize` "
                        "after the user explicitly approves (e.g. 'yes', 'go ahead')."
                    ),
                    "session_id": session_id,
                })
            request = {
                "action": "finalize",
                "session_id": session_id,
                "copy_to": str(MEDIA_DIR),
                "user_id": "nanobot",
            }
        else:
            request = {
                "message": message,
                "session_id": session_id or "",
                "log_dir": str(LOG_DIR),
                "user_id": "nanobot",
            }

        # Determine which Python to use
        python = str(AGENCY_PYTHON) if AGENCY_PYTHON.exists() else "python"

        # Heads-up to the user before the bridge blocks (1-3 min). Fires on
        # initial execute and free-text feedback iterations; silent for fast
        # calls (preview/finalize/validate_inputs/list_workflows). Prefers the
        # agent-supplied `user_facing_note`; falls back to a randomly-picked
        # string so the user still gets *something* if the agent forgets to
        # pass one (and back-to-back runs don't see the identical line).
        is_initial_execute = msg_lower == "execute" and not session_id
        is_iteration = bool(session_id) and msg_lower not in {
            "preview", "finalize", "validate_inputs", "list_workflows", "execute"
        }
        announced: str | None = None
        if is_initial_execute or is_iteration:
            pool = FOLLOWUP_ANNOUNCEMENTS if is_iteration else START_ANNOUNCEMENTS
            text = user_facing_note or random.choice(pool)
            announced = await self._announce(text)

        # Self-annealing capture (see SELF_ANNEALING_PLAN.md §7 Phase 1).
        # Decide whether this turn is a loggable correction; if so, write an
        # OPEN event before the bridge runs so the user's feedback survives a
        # mid-bridge crash. The same `logged_meta` is used after the bridge
        # returns to close the event with success/failure + a summary.
        is_logged_correction = (
            self._feedback_logger is not None
            and msg_lower not in _ACTION_KEYWORDS
            and bool(session_id)
            and classification == "correction"
            and classification_certainty in ("high", "medium")
        )
        logged_meta: _SessionMeta | None = None
        if is_logged_correction:
            meta = self._sessions.get(session_id or "")
            if meta and meta.workflow_name:
                assert self._feedback_logger is not None  # narrowed by gate
                self._feedback_logger.log_rework(
                    workflow_name=meta.workflow_name,
                    session_id=session_id or "",
                    classification_certainty=classification_certainty,  # type: ignore[arg-type]
                    original_inputs=meta.original_inputs or {},
                    preview_summary=_build_preview_summary(meta),
                    user_feedback=message or "",
                )
                logged_meta = meta

        logger.info(
            "Calling workflow bridge (session={})",
            session_id or "new",
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                python, str(BRIDGE_SCRIPT),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(AGENCY_PATH),
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=json.dumps(request).encode()),
                # 30 min: doc_mutation-class workflows need 8-15 min for a
                # clean run and up to ~25 min when the agent iterates several
                # times on judge-flagged artifacts. Earlier 5-min cap killed
                # nearly every realistic run.
                timeout=1800,
            )

        except asyncio.TimeoutError:
            logger.error("Workflow bridge timed out (30 min)")
            proc.kill()
            return json.dumps({"error": "Workflow timed out after 30 minutes"})
        except Exception as e:
            logger.error("Failed to run workflow bridge: {}", e)
            return json.dumps({"error": f"Bridge process error: {e}"})

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace").strip()
            logger.error("Bridge exited with code {}: {}", proc.returncode, err_msg[-500:])
            return json.dumps({"error": f"Bridge error: {err_msg[-500:]}"})

        # Parse JSON response from stdout
        raw_output = stdout.decode("utf-8", errors="replace").strip()

        # stdout may have multiple lines — the last line is our JSON
        lines = raw_output.strip().splitlines()
        json_line = lines[-1] if lines else ""

        try:
            result = json.loads(json_line)
        except json.JSONDecodeError:
            logger.error("Bridge returned invalid JSON: {}", raw_output[:500])
            return json.dumps({"error": f"Invalid bridge response: {raw_output[:200]}"})

        # Track per-session metadata. Execute creates a fresh entry with the
        # workflow name + original inputs (used downstream for feedback
        # logging). Preview marks the session previewed (unlocks the finalize
        # gate) and snapshots the bridge's resolved outputs dict. Free-text
        # rework turns refresh the captured agent response so the next
        # correction's preview_summary reflects the latest output the user saw.
        sid = result.get("session_id", "")
        if sid and not result.get("error"):
            if msg_lower == "execute":
                self._sessions[sid] = _SessionMeta(
                    workflow_name=workflow_name,
                    original_inputs=inputs,
                    last_agent_response=result.get("response"),
                )
            elif msg_lower == "preview":
                meta = self._sessions.setdefault(sid, _SessionMeta())
                meta.previewed = True
                meta.preview_outputs = result.get("outputs")
            elif msg_lower not in _ACTION_KEYWORDS:
                meta = self._sessions.setdefault(sid, _SessionMeta())
                meta.last_agent_response = (
                    result.get("response") or meta.last_agent_response
                )

        # Close the open feedback event recorded before the bridge call. The
        # rework's outcome is the bridge's success/failure; the summary is the
        # agent's last response text (truncated). Distillers read both.
        if logged_meta and logged_meta.workflow_name and self._feedback_logger is not None:
            succeeded = not bool(result.get("error"))
            summary = (
                result.get("response")
                or result.get("error")
                or "rework completed"
            )
            self._feedback_logger.mark_rework_outcome(
                workflow_name=logged_meta.workflow_name,
                session_id=session_id or "",
                succeeded=succeeded,
                summary=str(summary)[:500],
            )

        logger.info(
            "Workflow bridge responded (session={})",
            sid,
        )

        # Echo the heads-up that reached the user back into the tool response
        # so the agent's conversation history records what was sent. Without
        # this, an "Ok" reply to the announcement looks like an orphan to the
        # agent (the announcement is sent server-side and never lands in the
        # message log otherwise).
        if announced is not None and isinstance(result, dict):
            result["announced_to_user"] = announced

        return json.dumps(result)
