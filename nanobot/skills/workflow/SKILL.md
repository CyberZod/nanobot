---
name: workflow
description: How to use the workflow tool to execute Maroc agency workflows (image generation, document mutation, etc.)
always: true
---

# Workflow Execution

You have access to a `workflow` tool that delegates structured tasks to a specialized workflow agency. Use it when the user asks you to run a workflow, generate content, or process files through a multi-step pipeline.

## How It Works

The `workflow` tool sends a message to the Maroc workflow agency, which has its own AI agent that executes structured workflows (image generation, document processing, etc.). You are the intermediary — you relay instructions and responses between the user and the agency.

## Usage Pattern

### Starting a new workflow

Call `workflow` with just a `message`. A new session is created automatically. The response includes a `session_id` you must use for follow-ups.

```
workflow(message="Use the image_caption workflow to generate an image with the text 'Hello World' displayed on it.")
```

Response:
```json
{"session_id": "sess_abc123", "response": "All steps complete! ...", "output_files": ["C:\\...\\image.png"]}
```

### Delivering output files to the user

When the response includes `output_files`, you MUST send them to the user using the `message` tool BEFORE asking for approval:

```
message(content="Here is the generated image. Please review.", media=["C:\\...\\image.png"])
```

### Getting user approval and finalizing

After the user reviews and approves, finalize the workflow using the **same session_id** with `action: "finalize"`:

```
workflow(message="finalize", session_id="sess_abc123")
```

This copies important files and cleans up temporary data.

### Continuing a workflow (follow-ups)

If the agency needs more information, relay the question to the user. When the user responds, pass their reply back with the same `session_id`:

```
workflow(message="The file is located at C:\\Users\\...\\doc.pdf", session_id="sess_abc123")
```

## Complete flow example

1. User: "Generate an image with the text 'Platform Verified'"
2. You call `workflow(message="Use the image_caption workflow to generate an image with the text 'Platform Verified' displayed on it.")`
3. Agency responds with result + `output_files`
4. You send the output files to the user via `message(content="...", media=[...output_files])`
5. You ask: "Does this look correct?"
6. User: "Looks good, approved"
7. You call `workflow(message="finalize", session_id="sess_abc123")`
8. Done — tell the user the workflow is complete

## Important Rules

1. **Always pass the user's intent clearly** — the agency agent reads workflow files and executes tools on its own.
2. **Always preserve the session_id** — every follow-up must use the same `session_id`.
3. **ALWAYS send output files to the user** — use the `message` tool with `media` parameter before asking for approval.
4. **Wait for user approval before finalizing** — never auto-finalize.
5. **Only YOU finalize workflows** — the agency cannot do this. You must call `workflow(message="finalize", session_id="...")` after approval.
