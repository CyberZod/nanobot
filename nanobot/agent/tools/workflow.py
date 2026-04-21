"""Workflow tool for delegating tasks to the Maroc workflow agency.

Uses a subprocess bridge for full isolation — GenAI runs in its own
venv with its own dependencies. Communication is via JSON over stdin/stdout.
"""

import asyncio
import json
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


class WorkflowTool(Tool):
    """Send messages to the Maroc workflow agency to execute workflows."""

    def __init__(self) -> None:
        # Track session IDs so we can pass them on follow-ups
        self._active_sessions: set[str] = set()

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
            "returned session_id on every follow-up."
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
            },
            "required": [],
        }

    async def execute(
        self,
        message: str | None = None,
        session_id: str | None = None,
        workflow_name: str | None = None,
        inputs: dict | None = None,
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
                timeout=300,  # 5 min max per turn
            )

        except asyncio.TimeoutError:
            logger.error("Workflow bridge timed out (5 min)")
            proc.kill()
            return json.dumps({"error": "Workflow timed out after 5 minutes"})
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

        # Track session
        sid = result.get("session_id", "")
        if sid:
            self._active_sessions.add(sid)

        logger.info(
            "Workflow bridge responded (session={})",
            sid,
        )

        return json.dumps(result)
