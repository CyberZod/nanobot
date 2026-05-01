"""Append-only JSONL log of workflow rework feedback events.

One JSONL file per workflow_name under base_dir. Each event captures a
user correction landed via WorkflowTool's message branch — schema and
semantics per SELF_ANNEALING_PLAN.md §4.1 and §7 Phase 1.

Mirrors MemoryStore's cursor-recovery pattern: trust the .cursor sidecar
when intact, otherwise scan the JSONL and take max(cursor) + 1. Single
nanobot writer is assumed (no cross-process file locking, matching
MemoryStore's stance).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

try:
    from loguru import logger as _log
except ImportError:
    import logging
    _log = logging.getLogger("feedback_logger")


_CertaintyLogged = Literal["high", "medium"]


class FeedbackLogger:
    """Persist + read raw feedback events for the self-annealing distiller."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._corruption_logged: dict[str, bool] = {}

    # -- public API ----------------------------------------------------------

    def log_rework(
        self,
        *,
        workflow_name: str,
        session_id: str,
        classification_certainty: _CertaintyLogged,
        original_inputs: dict,
        preview_summary: str,
        user_feedback: str,
    ) -> int:
        """Append a rework event and return its cursor.

        Idempotency: if an OPEN event (rework_succeeded is None) already
        exists for this session_id, return its cursor without writing.
        agent_classification is stamped 'correction' by construction —
        the WorkflowTool hook only invokes log_rework when the inbound
        tool call carried classification='correction' AND certainty != 'low'.
        """
        existing = self._find_open_event_cursor(workflow_name, session_id)
        if existing is not None:
            return existing

        cursor = self._next_cursor(workflow_name)
        record = {
            "cursor": cursor,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "workflow_name": workflow_name,
            "user_id": "nanobot",
            "trigger": "rework_after_preview",
            "agent_classification": "correction",
            "classification_certainty": classification_certainty,
            "original_inputs": original_inputs,
            "preview_summary": preview_summary,
            "user_feedback": user_feedback,
            "rework_succeeded": None,
            "rework_summary": None,
        }
        path = self._jsonl_path(workflow_name)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._cursor_path(workflow_name).write_text(str(cursor), encoding="utf-8")
        return cursor

    def mark_rework_outcome(
        self,
        *,
        workflow_name: str,
        session_id: str,
        succeeded: bool,
        summary: str,
    ) -> bool:
        """Close the most-recent open event for session_id.

        Returns True if an open event was found and updated, False if no
        open event exists for that session_id (or the file is missing).
        """
        path = self._jsonl_path(workflow_name)
        if not path.exists():
            return False

        rows = self._read_rows(path)
        # Walk in reverse so we hit the most recent open event first.
        target_idx: int | None = None
        for idx in range(len(rows) - 1, -1, -1):
            row = rows[idx]
            if row.get("session_id") == session_id and row.get("rework_succeeded") is None:
                target_idx = idx
                break
        if target_idx is None:
            return False

        rows[target_idx]["rework_succeeded"] = succeeded
        rows[target_idx]["rework_summary"] = summary
        self._write_rows(path, rows)
        return True

    def read_unprocessed(self, workflow_name: str, since_cursor: int) -> list[dict]:
        """Return events with a valid int cursor strictly greater than since_cursor."""
        path = self._jsonl_path(workflow_name)
        if not path.exists():
            return []
        return [row for row, c in self._iter_valid_entries(path) if c > since_cursor]

    # -- internals -----------------------------------------------------------

    def _jsonl_path(self, workflow_name: str) -> Path:
        return self.base_dir / f"{workflow_name}.jsonl"

    def _cursor_path(self, workflow_name: str) -> Path:
        return self.base_dir / f"{workflow_name}.cursor"

    def _find_open_event_cursor(self, workflow_name: str, session_id: str) -> int | None:
        path = self._jsonl_path(workflow_name)
        if not path.exists():
            return None
        for row in reversed(self._read_rows(path)):
            if row.get("session_id") == session_id and row.get("rework_succeeded") is None:
                cursor = self._valid_cursor(row.get("cursor"))
                if cursor is not None:
                    return cursor
        return None

    def _next_cursor(self, workflow_name: str) -> int:
        cursor_file = self._cursor_path(workflow_name)
        if cursor_file.exists():
            try:
                return int(cursor_file.read_text(encoding="utf-8").strip()) + 1
            except (ValueError, OSError):
                pass
        path = self._jsonl_path(workflow_name)
        if not path.exists():
            return 1
        return max(
            (c for _, c in self._iter_valid_entries(path)),
            default=0,
        ) + 1

    @staticmethod
    def _valid_cursor(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    def _iter_valid_entries(self, path: Path) -> Iterator[tuple[dict, int]]:
        poisoned: Any = None
        for row in self._read_rows(path):
            cursor = self._valid_cursor(row.get("cursor"))
            if cursor is None:
                poisoned = row.get("cursor")
                continue
            yield row, cursor
        if poisoned is not None and not self._corruption_logged.get(str(path)):
            self._corruption_logged[str(path)] = True
            _log.warning(
                "feedback log {} contains a non-int cursor ({!r}); dropping it. "
                "Further occurrences suppressed.",
                path.name, poisoned,
            )

    @staticmethod
    def _read_rows(path: Path) -> list[dict]:
        rows: list[dict] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    @staticmethod
    def _write_rows(path: Path, rows: list[dict]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
