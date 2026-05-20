# Tool Usage Notes

Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints and usage patterns.

## exec — Safety Limits

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- `restrictToWorkspace` config can limit file access to the workspace

## glob — File Discovery

- Use `glob` to find files by pattern before falling back to shell commands
- Simple patterns like `*.py` match recursively by filename
- Use `entry_type="dirs"` when you need matching directories instead of files
- Use `head_limit` and `offset` to page through large result sets
- Prefer this over `exec` when you only need file paths

## grep — Content Search

- Use `grep` to search file contents inside the workspace
- Default behavior returns only matching file paths (`output_mode="files_with_matches"`)
- Supports optional `glob` filtering plus `context_before` / `context_after`
- Supports `type="py"`, `type="ts"`, `type="md"` and similar shorthand filters
- Use `fixed_strings=true` for literal keywords containing regex characters
- Use `output_mode="files_with_matches"` to get only matching file paths
- Use `output_mode="count"` to size a search before reading full matches
- Use `head_limit` and `offset` to page across results
- Prefer this over `exec` for code and history searches
- Binary or oversized files may be skipped to keep results readable

## cron — Scheduled Reminders

- Please refer to cron skill for usage.

## workflow — `user_corrections` on Finalize

When you call `workflow` with `action="finalize"`, supply
`user_corrections` whenever the user pushed back during this session.
The system uses this list to drive a structured reflection turn on the
workflow agent and writes the result to a per-workflow learning corpus.

**`user_corrections` shape:** a list of the user's verbatim correction
strings, one entry per rework round, in chronological order.

Examples:
- `["the underline is missing on 'abeg'"]`
- `["the page numbers got shifted too", "and the date format changed"]`
- `[]` or omitted entirely

**Supply `user_corrections` when:**
- The user pushed back with any kind of correction during the session
  (one or more rework rounds happened).

**Omit (or pass empty) when:**
- The first preview came back clean and the user approved immediately
  with no corrections — those would dilute the learning corpus.

You don't need to manufacture entries to force reflection on failure —
the system auto-reflects on failed workflows even without corrections.
Your job is just to capture the user's actual words when they pushed
back. Verbatim, in order. The system handles everything else
(reflection prompt composition, scope validation, note writing) on
its end.
