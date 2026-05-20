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

from nanobot.agent.tools.base import Tool

# Maroc agency paths — hardcoded for Phase 1
AGENCY_PATH = Path("C:/Users/user/Documents/Dev/Agentic Workflows/GenAI")
BRIDGE_SCRIPT = AGENCY_PATH / "workflow_bridge.py"
AGENCY_PYTHON = AGENCY_PATH / ".venv" / "Scripts" / "python.exe"
LOG_DIR = Path.home() / ".nanobot" / "workspace" / "workflow_logs"
MEDIA_DIR = Path.home() / ".nanobot" / "media" / "workflow_outputs"
NOTES_DIR = Path.home() / ".nanobot" / "workspace" / "workflow_notes"

# Valid values for the explicit `action` parameter. The 'message' action
# is the free-text path (manager sends conversational feedback to an
# ongoing workflow session); everything else is a structured command on
# either the workflow registry or an existing session. The set is the
# manager-side mirror of the bridge's action dispatch.
_VALID_ACTIONS = frozenset({
    "execute", "message", "preview", "finalize",
    "validate_inputs", "list_workflows",
})
# Subset of actions that block for the bridge call long enough to warrant
# a heads-up to the user (the manager must pass `user_facing_note`).
# 'preview', 'finalize', 'validate_inputs', 'list_workflows' return fast.
_SLOW_ACTIONS = frozenset({"execute", "message"})

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
    # Last status reported by the bridge for this session. One of
    # 'success' | 'failed' | 'in_progress'. Set on execute/free-text
    # turns and on preview; used to inform downstream decisions (e.g.,
    # whether a 'failed' outcome should be surfaced honestly to the
    # user instead of presented as a successful result).
    last_status: str | None = None


class WorkflowTool(Tool):
    """Send messages to the Maroc workflow agency to execute workflows."""

    def __init__(self, tools: Any = None) -> None:
        # Per-session metadata: workflow name + inputs from the originating
        # execute call, whether preview has been shown (gates finalize), the
        # latest preview outputs, and the latest agent response text.
        self._sessions: dict[str, _SessionMeta] = {}
        # Optional ToolRegistry — used to look up the 'message' tool for the
        # start announcement. Pass the agent loop's registry at construction.
        self._tools = tools

    @property
    def name(self) -> str:
        return "workflow"

    @property
    def description(self) -> str:
        return (
            "Drive workflows in the Maroc workflow agency. The `action` parameter "
            "is REQUIRED and chooses what this call does:\n"
            "  - `execute`: start a new workflow run. Pass `workflow_name` + "
            "`inputs` + `user_facing_note`. Returns a session_id.\n"
            "  - `message`: send free-text feedback/correction to an ongoing "
            "session. Pass `session_id` + `message` + `user_facing_note`. "
            "Use this for rework after the user reviewed a preview.\n"
            "  - `preview`: read the current declared outputs of a session. "
            "Pass `session_id`. Use before showing the user a result.\n"
            "  - `finalize`: deliver outputs and clean up the session. Pass "
            "`session_id` (preview must have been called first). MAY pass "
            "`user_corrections` (verbatim strings from each rework round) "
            "when the session had friction.\n"
            "  - `validate_inputs`: dry-run input validation. Pass "
            "`workflow_name` + `inputs`.\n"
            "  - `list_workflows`: enumerate available workflows.\n\n"
            "Typical flow: `validate_inputs` -> `execute` -> `preview` (show "
            "the user) -> optionally `message` rework rounds -> `finalize` "
            "after approval.\n\n"
            "`user_facing_note` is REQUIRED on `execute` and `message` (slow "
            "calls that block 1-3 minutes); ignored on the fast actions. "
            "Plain conversational language addressed to the user — no "
            "internal reasoning, no tool names, no JSON.\n\n"
            "The `message` parameter is strictly free-text content sent to "
            "the workflow agent. It is NEVER parsed for action keywords — "
            "use the `action` parameter for control commands.\n\n"
            "Responses include a `status` field — read it first, do not "
            "treat any successful JSON return as workflow success. Status "
            "values:\n"
            "  `success` — the plan resolved cleanly. Proceed to preview, "
            "show the user, finalize on approval.\n"
            "  `failed` — the plan has at least one failed step. Surface the "
            "failure honestly using the `response` text; either send a "
            "free-text `message` action to try a different angle, or "
            "`finalize` as failure if the user wants to abandon.\n"
            "  `in_progress` — partial state. If `response` looks like a "
            "question or stuck-state report, send a `message` action to "
            "probe. Do NOT call preview/finalize yet.\n"
            "Use the `response` text as your source of truth for what "
            "happened on that turn."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": sorted(_VALID_ACTIONS),
                    "description": (
                        "REQUIRED. The workflow action to perform. "
                        "'execute' starts a new run (needs workflow_name + inputs). "
                        "'message' sends free-text feedback to an ongoing session "
                        "(needs session_id + message). "
                        "'preview'/'finalize' read/close a session (need session_id). "
                        "'validate_inputs' is a dry-run check (needs workflow_name + "
                        "inputs). 'list_workflows' enumerates available workflows."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": (
                        "Free-text content sent verbatim to the workflow agent. "
                        "Only used when action='message'; ignored on every other "
                        "action. NEVER parsed for control keywords — use `action` "
                        "for control commands."
                    ),
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "Session ID for an ongoing workflow. Required for "
                        "action ∈ {message, preview, finalize}. Ignored on "
                        "execute (a fresh session is created), validate_inputs, "
                        "and list_workflows."
                    ),
                },
                "workflow_name": {
                    "type": "string",
                    "description": (
                        "Workflow name. Required for action ∈ {execute, "
                        "validate_inputs}. Ignored on other actions."
                    ),
                },
                "inputs": {
                    "type": "object",
                    "description": (
                        "Input key-value pairs. Required for action ∈ {execute, "
                        "validate_inputs}. Ignored on other actions."
                    ),
                },
                "user_facing_note": {
                    "type": "string",
                    "description": (
                        "Brief, user-facing acknowledgement sent to the user "
                        "before this call runs. Required on action ∈ {execute, "
                        "message}, both of which can block for 1-3 minutes. "
                        "Plain conversational language addressed to the user — "
                        "no internal reasoning, no tool names, no JSON, no "
                        "'I will…' planning narration. Examples: 'On it, I'll "
                        "send the draft shortly.' / 'Got it — fixing the "
                        "underline now.' / 'Reworking the totals, one moment.' "
                        "Ignored on preview/finalize/validate_inputs/list_workflows."
                    ),
                },
                "user_corrections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Used ONLY with action='finalize'. A list of the user's "
                        "verbatim correction strings from this session — one "
                        "entry per rework round, in chronological order. Supply "
                        "when the user pushed back during the session (e.g., "
                        "'the underline is missing', 'the page numbers shifted'). "
                        "The system uses this list to compose a reflection "
                        "prompt for the workflow agent and append the agent's "
                        "reflection to a per-workflow notes file. "
                        "Pass an empty list or omit on clean first-attempt "
                        "successes — those dilute the learning corpus. The "
                        "system also auto-reflects on failed workflows even "
                        "without corrections, so you don't need to manufacture "
                        "entries to force reflection on failure. "
                        "Ignored on all actions other than finalize."
                    ),
                },
            },
            "required": ["action"],
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
        action: str | None = None,
        message: str | None = None,
        session_id: str | None = None,
        workflow_name: str | None = None,
        inputs: dict | None = None,
        user_facing_note: str | None = None,
        user_corrections: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        # `action` is required and explicit. No inference from message/inputs.
        # No magic-string interpretation of `message`. Closes the dispatch
        # overload that produced sess_776060606d1f's silent input fabrication
        # (free-text `message` with empty session_id used to fall through to
        # a new-session-with-no-inputs branch).
        if not action or not action.strip():
            return json.dumps({"error": (
                "`action` is required. Pass one of: "
                f"{sorted(_VALID_ACTIONS)}. "
                "Use action='execute' to start a new workflow (with "
                "workflow_name + inputs), action='message' to send free-text "
                "feedback to an ongoing session (with session_id), "
                "action='preview'/'finalize' to read or close a session."
            )})
        action = action.strip().lower()
        if action not in _VALID_ACTIONS:
            return json.dumps({"error": (
                f"Unknown action '{action}'. Valid actions: "
                f"{sorted(_VALID_ACTIONS)}."
            )})

        # Per-action required-field validation. Catches the manager's
        # mistakes at this boundary so the bridge never sees malformed
        # requests (and the workflow agent never has to fabricate missing
        # inputs).
        if action == "execute":
            if not workflow_name:
                return json.dumps({"error": (
                    "action='execute' requires `workflow_name`."
                )})
            if inputs is None:
                return json.dumps({"error": (
                    "action='execute' requires an `inputs` dict (use {} "
                    "explicitly if the workflow takes no inputs; the "
                    "bridge's validate_inputs path enforces the actual "
                    "schema per workflow)."
                )})
            request = {
                "action": "execute",
                "workflow_name": workflow_name,
                "inputs": inputs,
                "log_dir": str(LOG_DIR),
                "user_id": "nanobot",
            }
        elif action == "message":
            if not session_id:
                return json.dumps({"error": (
                    "action='message' requires `session_id` of an ongoing "
                    "workflow. To start a NEW workflow, use action='execute' "
                    "with workflow_name + inputs — never bootstrap one with "
                    "a free-text message (the workflow agent has no way to "
                    "validate the file paths and other inputs you'd want it "
                    "to use)."
                )})
            if not message or not message.strip():
                return json.dumps({"error": (
                    "action='message' requires `message` text — the "
                    "free-text content to send to the workflow agent."
                )})
            request = {
                "message": message,
                "session_id": session_id,
                "log_dir": str(LOG_DIR),
                "user_id": "nanobot",
            }
        elif action == "preview":
            if not session_id:
                return json.dumps({"error": (
                    "action='preview' requires `session_id`."
                )})
            request = {
                "action": "preview",
                "session_id": session_id,
                "user_id": "nanobot",
            }
        elif action == "finalize":
            if not session_id:
                return json.dumps({"error": (
                    "action='finalize' requires `session_id`."
                )})
            meta = self._sessions.get(session_id)
            if meta is None or not meta.previewed:
                return json.dumps({
                    "error": (
                        "finalize requires preview first. Call action='preview' "
                        "with this session_id, show the user the outputs, and "
                        "only call action='finalize' after the user explicitly "
                        "approves (e.g. 'yes', 'go ahead')."
                    ),
                    "session_id": session_id,
                })
            request = {
                "action": "finalize",
                "session_id": session_id,
                "copy_to": str(MEDIA_DIR),
                "user_id": "nanobot",
            }
            # Self-annealing capture (SELF_ANNEALING_PLAN.md §3 + §5). The
            # manager (this caller) supplies the verbatim user_corrections
            # list when the session had friction; the bridge owns the
            # reflection prompt template and decides whether to fire
            # reflection (corrections non-empty OR outcome=failed).
            # Always thread the notes_dir so the bridge knows where to write.
            if user_corrections:
                request["user_corrections"] = list(user_corrections)
            request["notes_dir"] = str(NOTES_DIR)
        elif action == "validate_inputs":
            if not workflow_name:
                return json.dumps({"error": (
                    "action='validate_inputs' requires `workflow_name`."
                )})
            request = {
                "action": "validate_inputs",
                "workflow_name": workflow_name,
                "inputs": inputs or {},
            }
        elif action == "list_workflows":
            request = {"action": "list_workflows"}
        else:  # pragma: no cover — enum-validated above
            return json.dumps({"error": f"Unhandled action '{action}'."})

        # Determine which Python to use
        python = str(AGENCY_PYTHON) if AGENCY_PYTHON.exists() else "python"

        # Heads-up to the user before the bridge blocks (1-3 min). Fires on
        # action='execute' (initial) and action='message' (free-text feedback
        # iterations); silent for the fast actions (preview/finalize/
        # validate_inputs/list_workflows). Prefers the agent-supplied
        # `user_facing_note`; falls back to a randomly-picked string so the
        # user still gets *something* if the agent forgets to pass one (and
        # back-to-back runs don't see the identical line).
        is_slow = action in _SLOW_ACTIONS
        # Enforce user_facing_note on slow paths. Returning an error here
        # short-circuits before announcement, feedback logging, and the
        # bridge call — the agent retries the same call with a note. We
        # don't fall back to the canned pool on misses anymore because
        # back-to-back canned lines (e.g. on a retry) read robotically
        # and the agent can almost always do better with context.
        if is_slow and not (user_facing_note and user_facing_note.strip()):
            kind = "action='message' (feedback follow-up)" if action == "message" else "action='execute' (initial)"
            return json.dumps({
                "error": (
                    f"`user_facing_note` is required on {kind} calls. "
                    "Provide a brief, conversational heads-up addressed to the user "
                    "(e.g. 'On it, I'll send the draft shortly.' / 'Got it — fixing "
                    "the underline now.' / 'Hit a snag, retrying for you now.') and "
                    "call again. Plain user-facing language only — no internal "
                    "reasoning, no tool names, no JSON."
                ),
            })
        announced: str | None = None
        if is_slow:
            text = user_facing_note or random.choice(
                FOLLOWUP_ANNOUNCEMENTS if action == "message" else START_ANNOUNCEMENTS
            )
            announced = await self._announce(text)

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

        # Track per-session metadata. action='execute' creates a fresh entry
        # with the workflow name + original inputs. action='preview' marks the
        # session previewed (unlocks the finalize gate) and snapshots the
        # bridge's resolved outputs dict. action='message' (free-text rework)
        # refreshes the captured agent response. Status (`success` | `failed`
        # | `in_progress`) is captured on every bridge-reply turn so
        # downstream calls can branch on it.
        sid = result.get("session_id", "")
        if sid and not result.get("error"):
            if action == "execute":
                self._sessions[sid] = _SessionMeta(
                    workflow_name=workflow_name,
                    original_inputs=inputs,
                    last_agent_response=result.get("response"),
                    last_status=result.get("status"),
                )
            elif action == "preview":
                meta = self._sessions.setdefault(sid, _SessionMeta())
                meta.previewed = True
                meta.preview_outputs = result.get("outputs")
                if result.get("status"):
                    meta.last_status = result.get("status")
            elif action == "message":
                meta = self._sessions.setdefault(sid, _SessionMeta())
                meta.last_agent_response = (
                    result.get("response") or meta.last_agent_response
                )
                if result.get("status"):
                    meta.last_status = result.get("status")

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
