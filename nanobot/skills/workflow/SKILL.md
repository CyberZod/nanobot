---
name: workflow
description: How to use the workflow tool to discover and execute Maroc agency workflows (image generation, document mutation, etc.)
always: true
---

# Workflow Tool

You have access to a `workflow` tool that connects to a specialized workflow agency. The agency has its own AI agent that executes structured, multi-step workflows (image generation, document processing, etc.). You are the intermediary — you discover workflows, relay instructions, deliver outputs, and manage the lifecycle.

## Discovering Workflows

To see what workflows are available:

```
workflow(message="list_workflows")
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
workflow(message="validate_inputs", workflow_name="doc_mutation", inputs={"Document": "C:\\path\\to\\file.pdf", "Instructions": "change the date"})
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

### Starting a workflow (preferred — with built-in validation)

Use the `execute` action with `workflow_name` and `inputs`. This validates inputs first — if anything is wrong, it returns errors without starting the workflow. If inputs are valid, it executes automatically.

```
workflow(message="execute", workflow_name="image_caption", inputs={"Message": "Hello World"})
```

```
workflow(message="execute", workflow_name="doc_mutation", inputs={"Document": "C:\\path\\to\\file.pdf", "Instructions": "change the date to March 2026"})
```

If validation fails:
```json
{"valid": false, "errors": ["Document: expected document or pdf, got image"]}
```
→ Tell the user what's wrong and ask them to fix it.

If validation passes and execution completes:
```json
{"session_id": "sess_abc123", "response": "All steps complete! ...", "output_files": ["C:\\...\\image.png"]}
```

### Starting a workflow (free-form — no validation)

You can also send a free-text message. The agency agent figures out which workflow to use on its own. Use this for simple requests or when you're just chatting with the agent.

```
workflow(message="Generate an image with the text 'Hello World' on it.")
```

Response includes a `session_id` (for follow-ups) and optionally `output_files`:
```json
{"session_id": "sess_abc123", "response": "All steps complete! ...", "output_files": ["C:\\...\\image.png"]}
```

### Delivering output files to the user

When the response includes `output_files`, you MUST send them to the user using the `message` tool BEFORE asking for approval:

```
message(content="Here is the generated image. Please review.", media=["C:\\...\\image.png"])
```

### Getting user approval and finalizing

After the user reviews and approves, finalize the workflow:

```
workflow(message="finalize", session_id="sess_abc123")
```

This copies important files to permanent storage and cleans up temporary data.

### Continuing a workflow (follow-ups)

If the agency needs more information, relay to the user. Pass their reply back with the same `session_id`:

```
workflow(message="The file is at C:\\Users\\...\\doc.pdf", session_id="sess_abc123")
```

### Heads-up to the user (`user_facing_note`)

`execute` and free-text feedback follow-ups can block for 1-3 minutes while the bridge runs. **You MUST pass `user_facing_note`** on these calls — a brief, conversational acknowledgement that gets sent to the user before the bridge call starts so they know work has started and aren't waiting in silence.

The tool will reject the call with an error if `user_facing_note` is missing on these paths; you'll need to retry with one.

```
workflow(message="execute", workflow_name="doc_mutation", inputs={...},
         user_facing_note="On it, I'll send the modified document shortly.")
```

```
workflow(message="Underline 'To' in the heading", session_id="sess_abc123",
         user_facing_note="Got it — fixing the underline now.")
```

**Rules for `user_facing_note`:**
- Plain conversational language addressed directly to the user. No internal reasoning, no tool names, no JSON, no "I will…" planning narration.
- Tailor it to the situation. On a retry after a failure, say so ("Hit a snag, retrying for you now.") — don't reuse the same line as the first attempt.
- Keep it short — one sentence.
- Skip on `preview`, `finalize`, `validate_inputs`, `list_workflows` (those are fast, no heads-up needed).

## Complete Flow Example

1. User: "Generate an image with the text 'Platform Verified'"
2. You call `workflow(message="Use the image_caption workflow to generate an image with the text 'Platform Verified' displayed on it.")`
3. Agency responds with result + `output_files`
4. You send output files to user via `message(content="...", media=[...output_files])`
5. You ask: "Does this look correct?"
6. User: "Looks good, approved"
7. You call `workflow(message="finalize", session_id="sess_abc123")`
8. Done — tell the user the workflow is complete

## Important Rules

1. **Discover before guessing** — if you're not sure which workflow to use, call `list_workflows` first.
2. **Always preserve the session_id** — every follow-up must use the same `session_id`.
3. **ALWAYS send output files to the user** — use the `message` tool with `media` parameter before asking for approval.
4. **Wait for user approval before finalizing** — never auto-finalize.
5. **Only YOU finalize workflows** — the agency cannot do this. You must call `workflow(message="finalize", session_id="...")` after approval.
6. **Always pass `user_facing_note` on `execute` and feedback follow-ups** — these block for 1-3 minutes; the user needs a tailored heads-up. Tailor it to the situation (especially on retries).
