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

## Executing a Workflow

### Starting a new workflow

Call `workflow` with a clear instruction. The agency agent will discover and execute the right workflow on its own. Include the workflow name if you know it.

```
workflow(message="Use the image_caption workflow to generate an image with the text 'Hello World' displayed on it.")
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
