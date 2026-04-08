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
            "Omit session_id on the first call to start a new workflow. "
            "Pass the same session_id for follow-up messages (e.g., approvals)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to send to the workflow agency",
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "Session ID for an ongoing workflow. "
                        "Omit or leave empty to start a new workflow session."
                    ),
                },
            },
            "required": ["message"],
        }

    async def execute(
        self,
        message: str,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        # Route to the right bridge action
        msg_lower = message.strip().lower()

        if msg_lower == "list_workflows":
            request = {"action": "list_workflows"}
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
