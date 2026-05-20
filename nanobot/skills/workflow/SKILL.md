---
name: workflow
description: How to use the workflow tool to discover and execute Maroc agency workflows (image generation, document mutation, etc.)
always: true
---

# Workflow Tool

You have access to a `workflow` tool that connects to a specialized workflow agency. The agency has its own AI agent that executes structured, multi-step workflows (image generation, document processing, etc.). You are the intermediary — you discover workflows, relay instructions, deliver outputs, and manage the lifecycle.

## The `action` parameter is required on every call

Every call to `workflow` requires an explicit `action`. The action determines what the call does and which other parameters are required:

| `action` | Purpose | Required co-fields |
|---|---|---|
| `list_workflows` | Enumerate available workflows | — |
| `validate_inputs` | Dry-run input validation | `workflow_name`, `inputs` |
| `execute` | Start a new workflow run | `workflow_name`, `inputs`, `user_facing_note` |
| `message` | Send free-text feedback to an ongoing session | `session_id`, `message`, `user_facing_note` |
| `preview` | Read the current declared outputs of a session | `session_id` |
| `finalize` | Deliver outputs and close the session | `session_id` (preview must have happened first) |

The `message` parameter is **strictly free-text content** sent verbatim to the workflow agent. It is NEVER parsed for control keywords — use the `action` parameter for control commands.

## Discovering Workflows

```
workflow(action="list_workflows")
```

Response:
```json
{
  "workflows": [
    {"name": "doc_mutation", "description": "Adjust text in PDF/DOCX", "inputs": [...]},
    {"name": "image_caption", "description": "Generate image with text overlay", "inputs": [...]}
  ]
}
```

Each workflow has a `name`, `description`, and `inputs` (with `required` flag and `description`).

**When to list workflows:**
- When the user asks "what workflows are available?" or similar
- When you're unsure which workflow fits a user's request
- Present the results in a user-friendly way — don't dump raw JSON

**When to match a workflow to a user request:**
- If the user says "edit my PDF" → that's likely `doc_mutation`
- If the user says "generate an image" → that's likely `image_caption`
- If unsure, list workflows and ask the user which one they want

## Validating Inputs Before Execution

Before starting a workflow, validate the user's inputs to catch issues early (missing inputs, wrong file types, files not found):

```
workflow(action="validate_inputs", workflow_name="doc_mutation",
         inputs={"Document": "C:\\path\\to\\file.pdf", "Instructions": "change the date"})
```

Response when valid:
```json
{"valid": true, "inputs": {"Document": {"value": "...", "file_meta": {"type": "pdf", "pages": 3, "size": "245 KB"}}, ...}}
```

Response when invalid:
```json
{"valid": false, "missing": [{"name": "Document", "description": "Path to the input document"}], "errors": ["Missing required input: Document"]}
```

**When to validate:**
- ALWAYS validate before starting a workflow that takes file inputs
- If validation fails, tell the user what's wrong and ask them to fix it
- If a file type doesn't match (e.g., user sent a JPEG but workflow needs PDF), tell them specifically what's needed
- Once validation passes, proceed to execute the workflow

**Collecting inputs from the user:**
- Use `list_workflows` to know what inputs are needed
- If the user provides a file, use the file path from the `media` array in their message
- If inputs are missing, ask the user for them before validating
- You may need to go back and forth with the user until all inputs are valid

## Executing a Workflow

Use `action="execute"` with `workflow_name`, `inputs`, and `user_facing_note`. This validates inputs first — if anything is wrong, it returns errors without starting the workflow. If inputs are valid, it executes.

```
workflow(action="execute", workflow_name="image_caption",
         inputs={"Message": "Hello World"},
         user_facing_note="On it, generating the image now.")
```

```
workflow(action="execute", workflow_name="doc_mutation",
         inputs={"Document": "C:\\path\\to\\file.pdf",
                 "Instructions": "change the date to March 2026"},
         user_facing_note="On it, I'll send the modified document shortly.")
```

If validation fails:
```json
{"valid": false, "errors": ["Document: expected document or pdf, got image"]}
```
→ Tell the user what's wrong and ask them to fix it.

If validation passes and execution completes:
```json
{"session_id": "sess_abc123", "status": "success", "response": "All steps complete! ...", "output_files": ["C:\\...\\image.png"]}
```

**There is no free-text bootstrap path.** Starting a workflow requires structured inputs via `action="execute"`. Never call `action="message"` without a `session_id` — that's only for follow-ups on an existing session, and the tool will reject the call. If the user describes what they want in chat, your job is to translate that into a structured `execute` call.

## Sending Feedback / Continuing a Workflow

After preview, if the user has feedback or corrections, relay it back to the session using `action="message"`:

```
workflow(action="message",
         message="Underline 'To' in the heading",
         session_id="sess_abc123",
         user_facing_note="Got it — fixing the underline now.")
```

The `message` field is the user's feedback in their own words. The workflow agent receives it verbatim and continues working in the same session.

If the agency itself asks a question (e.g., needs more info), relay the user's reply the same way:

```
workflow(action="message",
         message="The file is at C:\\Users\\...\\doc.pdf",
         session_id="sess_abc123",
         user_facing_note="One moment, passing that along.")
```

## Previewing and Delivering Outputs

After a workflow run completes (status `success`), call `preview` to get the resolved output paths:

```
workflow(action="preview", session_id="sess_abc123")
```

When the response includes resolved output paths or the original execute returned `output_files`, you MUST send them to the user using the `message` tool BEFORE asking for approval:

```
message(content="Here is the generated image. Please review.", media=["C:\\...\\image.png"])
```

## Finalizing After Approval

After the user reviews and approves, finalize the workflow:

```
workflow(action="finalize", session_id="sess_abc123")
```

This copies important files to permanent storage and cleans up temporary data.

### Passing `user_corrections` on finalize

If the session had friction (the user pushed back at least once with corrections), pass the verbatim correction strings as `user_corrections`:

```
workflow(action="finalize", session_id="sess_abc123",
         user_corrections=["the underline is missing on 'abeg'",
                           "the page numbers got shifted"])
```

- Supply one entry per rework round, in chronological order.
- Omit or pass an empty list on clean first-attempt successes — those dilute the learning corpus.
- The system also auto-reflects on failed workflows even without corrections; don't manufacture entries to force reflection on failure.

## Heads-up to the user (`user_facing_note`)

`action="execute"` and `action="message"` block for 1-3 minutes while the bridge runs. **You MUST pass `user_facing_note`** on these calls — a brief, conversational acknowledgement that gets sent to the user before the bridge call starts so they know work has started and aren't waiting in silence.

The tool will reject the call with an error if `user_facing_note` is missing on these paths; you'll need to retry with one.

**Rules for `user_facing_note`:**
- Plain conversational language addressed directly to the user. No internal reasoning, no tool names, no JSON, no "I will…" planning narration.
- Tailor it to the situation. On a retry after a failure, say so ("Hit a snag, retrying for you now.") — don't reuse the same line as the first attempt.
- Keep it short — one sentence.
- Skip on `preview`, `finalize`, `validate_inputs`, `list_workflows` (those are fast, no heads-up needed).

## Reading the response status

Every bridge reply includes a `status` field. Read it first — don't treat any successful JSON return as workflow success:

- **`success`** — The plan resolved cleanly (every step complete or skipped). Proceed to `preview`, show the user, finalize on approval.
- **`failed`** — At least one step failed. The workflow agent has given an honest report in `response`. Do NOT present the partial result as a success. Surface the failure to the user honestly (using the `response` text), and either send `action="message"` to try a different angle, or `action="finalize"` as failure if the user wants to abandon.
- **`in_progress`** — The plan is partially resolved; the workflow agent stopped without finishing. If `response` looks like a question or stuck-state report, send `action="message"` to probe (e.g., ask the agent to clarify or to try a different approach). Do NOT call `preview`/`finalize` yet.

Use the `response` text as your source of truth for what happened on that turn — it's the workflow agent's narrative, treat it as if a teammate handed you a status note.

## Complete Flow Example

1. User: "Replace 'kindly' with 'abeg' in this PDF" (attaches a PDF).
2. You call `workflow(action="validate_inputs", workflow_name="doc_mutation", inputs={"Document": "<path>", "Instructions": "Replace 'kindly' with 'abeg'"})`. Validation passes.
3. You call `workflow(action="execute", workflow_name="doc_mutation", inputs={...}, user_facing_note="On it, sending the modified PDF shortly.")`. Agency responds with `session_id` + `status: "success"`.
4. You call `workflow(action="preview", session_id="sess_abc123")` to get resolved outputs.
5. You send output files to the user via `message(content="...", media=[...])`.
6. You ask: "Does this look correct?"
7. User: "Looks good but the spacing is off on that line — can you fix?"
8. You call `workflow(action="message", message="The spacing is off on that line — fix the spacing between the words", session_id="sess_abc123", user_facing_note="Got it — fixing the spacing now.")`.
9. Agency reworks, returns updated session state. You preview again and resend.
10. User: "Looks good, approved."
11. You call `workflow(action="finalize", session_id="sess_abc123", user_corrections=["the spacing is off on that line"])`.
12. Done — tell the user the workflow is complete.

## Important Rules

1. **Always pass `action`** — it's a required parameter on every call. No inference.
2. **`message` is strictly free-text** — never use it for action keywords. To start a workflow, use `action="execute"` with `workflow_name` + `inputs`; to send feedback, use `action="message"` with `session_id`.
3. **Discover before guessing** — if you're not sure which workflow to use, call `action="list_workflows"` first.
4. **Always preserve the session_id** — every follow-up (`message`, `preview`, `finalize`) must use the same `session_id`.
5. **ALWAYS send output files to the user** — use the `message` tool with `media` parameter before asking for approval.
6. **Wait for user approval before finalizing** — never auto-finalize.
7. **Only YOU finalize workflows** — the agency cannot do this. You must call `action="finalize"` after approval.
8. **Always pass `user_facing_note` on `execute` and `message`** — these block for 1-3 minutes; the user needs a tailored heads-up. Tailor it to the situation (especially on retries).
9. **Supply `user_corrections` on `finalize` when the session had friction** — verbatim user correction strings, one per rework round, in chronological order. Omit on clean first-attempt successes.
