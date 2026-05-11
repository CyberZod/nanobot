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

## workflow — Reflection Prompts on Finalize

When you call `workflow` with `action="finalize"`, decide whether to also pass
a `reflection_prompt`. This is how the workflow system learns from
friction-bearing sessions.

**Send a reflection_prompt when ANY of these is true:**
- The user pushed back with any kind of correction during the session
  (one or more rework rounds happened).
- The workflow ended in failure or partial delivery.
- The user expressed notable frustration, surprise, or multi-round patience.

**Skip reflection_prompt when:**
- The first preview came back clean and the user approved immediately with
  no corrections — those would dilute the learning corpus.

**What to include in the prompt:**
- The session's outcome (succeeded / failed / abandoned).
- The user's verbatim feedback text from each correction turn.
- A brief tone signal if the user's reaction was notable.
- A request for mechanical reflection in operation-shaped framing.
  Mechanical: *"how the operation works at the implementation level."*
  NOT checklist: *"things to look for next time"* — that's hint leakage.

**Template you can adapt:**

```
This {workflow_name} session has been finalized by the user. Reflecting on
the work you just did:

- What did you actually change, and how?
- What was non-obvious about the mechanics of this kind of operation that
  another agent doing similar work might not naturally think about?
- What did you initially miss that the user had to flag?

(For context: the user's correction was "<verbatim feedback>", which you
addressed in your second attempt.)

Write a short paragraph (~150 words) of mechanical reflection. Frame in
terms of how the operation works, not "look for X" checklists. Future runs
of this workflow will read it as background context.
```

For failed sessions, substitute: *"This session ended without a successful
finalize. Where did you hit a wall? What about this case made it harder
than expected?"*

The prompt becomes one more message in the workflow agent's session, so it
has full memory of what it did — you're giving it the context only the user
side has: the user's words, the tone, the outcome verdict.
