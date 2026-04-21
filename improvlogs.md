# Improv logs

## Artifacts/Files attachments

Does _build_user_content() handle artifacts/files attachments other than images?
What happens if the file is a video or json or pdf etc?

## Tool output

Do tools exclusive output strings? Or can they output other data types?
I suppose they can output other data types, but the output is then converted to a string before being sent to the LLM but this could be a problem for some data types.

## Progressive Tool Loading (MCP → Skills approach)

**Inspiration**: [MCPorter CLI](https://youtu.be/fG95XsBO5U4?si=-C26_EexykfQk32N) — "Why MCP is dead & How I vibe now"

**Current behavior**: All tool schemas (built-in + MCP) are sent to the LLM on **every** `provider.chat()` call via `self.tools.get_definitions()`. With many MCP servers, this causes context window bloat.

**Proposed improvement**: Apply the same progressive loading pattern that `SkillsLoader` already uses for skills to MCP tools:
1. Convert each MCP server's tools into a `SKILL.md`-style summary (name + one-line description)
2. Only send summaries to the LLM in the system prompt (like `build_skills_summary()` does)
3. When the AI decides it needs a tool, it reads the full schema via `read_file`, then calls it via CLI/shell (`exec` tool) instead of the native MCP protocol
4. This would unify the `tools/` and `skills/` systems into one progressive loading model

**MCPorter's SKILL.md structure** (from video):
```markdown
---
name: context7
description: Query up-to-date documentation for any programming library or framework.
---
# Context7 Documentation Query
## When to Use
- Need current documentation for a library
- Looking for code examples and implementation patterns

## Available Tools
### resolve-library-id
Resolves a library name to a Context7-compatible library ID. **Call this first**
**Parameters:**
- `query` (required): The user's question or task
- `libraryName` (required): Library name to search for
**Example:**
npx mcporter call context7.resolve-library-id query:"How to set up routing" libraryName:"next.js"
```
The agent discovers the tool through the skill summary, reads the SKILL.md for usage details, then uses `exec` to call `npx mcporter call ...` — no MCP protocol needed.

**Trade-offs**:
- ✅ Massively reduces tokens per LLM call when many MCP tools are registered
- ✅ Nanobot already has the infrastructure (`SkillsLoader`, `ExecTool`)
- ⚠️ Adds one extra LLM round-trip (AI must read the skill before calling the tool)
- ⚠️ CLI invocation may be slower than native MCP protocol for some tools

## 

The global lock implies -
- One message to agent at a time i.e. can't serve multiple users at once even in different channels/with different session ids so not enterprise-ready


- OK so for this we have to make sure that. The assistant or the agent can not. Do what the user will do when the user wants to talk to the agent. So. For instance, we have to check the agent's response if it's. Could be. A triggering response that means if. Let's see if there's a phrase at the beginning that the user would use when they want to call the agent. In their with their number. Then we just took it away before we, you know, send back the response. So that way. You know the agent doesn't have to. They doesn't make a mistake. You know. You know to trigger its own self.

I believe for a multi. Interesting. So there are different charts. And different charts. They seem to share the same memory. Some memory the same. History markdown files. So I don't know if this is something that we should. Keep or we should try to have like a separation. But I think it's all good. Just a thought to consider later on.


So I just tested it out myself and it looks very awesome. So I do a couple things to add to the to the realism, right? Number one. We named the school Tales, Tales Academy rather than Greenfield Academy, right Academy. That's number one. Number two. Make the inquiry cost 500 naira. That way I can actually generate a real. A real transaction receipts, right? Because I'm going to actually send money into my other account, right? So. Make the the transaction the cost 500 naira. I'm going to provide you the account balance below. And the bank, right? So because I noticed something interesting. One, when I sent a random picture, it actually looked at it. I was like, this is not a real receipt, you know, please send me a real receipt, right? So that was very interesting. Umm. So yeah, I'm gonna let's, let's add to that. So once I do that. One more thing I want you to do is I want you to. Yeah, just do that for now. So yes, the account number below.

Bank: Providus Bank
Acc no: 65

- **Bug / Edge Case**: 
  - Starting and stopping the bridge repeatedly can corrupt the WhatsApp Web session (resulting in Baileys `Status: 408 Timeout` and `Status: 405 Not Logged In` errors).
  - *Mitigation*: We need a way to gracefully handle this situation in the codebase. Currently, the workaround is to completely wipe the `~/.nanobot/whatsapp-auth` folder and force a fresh QR code scan.

## Multimodal Media Handling Improvements

**Reference repo**: `C:\Users\user\Documents\Dev\Agentic Workflows\GenAI` — has proven patterns for media processing.

### Current State (Nanobot)
- WhatsApp bridge downloads images, videos, documents, audio and saves to `{authDir}/../media/`
- Only **images** are base64-encoded and sent to the LLM (via `context.py:_build_user_content()`)
- Video, audio, and documents are referenced as path strings only (`[file: /path]`) — agent can't see their content
- No file metadata is surfaced (no size, duration, dimensions, codec, page count)
- Agent has no choice — images are always inlined, everything else is always ignored

### Goal 1: Video Support for LLM

Two implementation paths, used together based on model capability:

**Path A — Frame Extraction (universal fallback, no internet needed)**
- Extract key frames via OpenCV (configurable FPS, max frames) — GenAI's `core/llm/media.py:extract_video_frames()`
- Base64-encode frames as images and send as multi-image content
- Optionally transcribe audio track via Whisper
- Works with every vision model. Trade-off: loses audio/motion context.
- Already implemented and proven in the GenAI repo

**Path C — Local Media Server (for models with native video support)**
- Nanobot spins up a lightweight local HTTP server to serve media files from disk
- Produces URLs like `http://localhost:PORT/media/wa_xxxxx.mp4`
- Pass the URL to the LLM — models like Gemini can fetch and "watch" the video natively
- Same concept as the Cloudinary approach in GenAI repo, but fully local (no cloud dependency)
- Only works with providers that actually fetch URLs and support video (e.g., Gemini)

**Routing logic in `_build_user_content()`:**
1. Detect video file (by MIME/extension)
2. Check if the current provider supports native video → use Path C (local URL)
3. Otherwise → use Path A (frame extraction)

**Important**: Agent should be able to **choose** whether to view a video (via tool call), not auto-consume everything. Large files especially need opt-in processing.

### Goal 2: File Metadata Surfacing

Before the agent processes any file, it should receive metadata so it can make informed decisions:
- **Images**: dimensions, format, file size
- **Video**: duration, resolution, FPS, codec, audio presence, file size
- **Audio**: duration, sample rate, channels, file size
- **PDFs**: page count, text vs scanned, file size

GenAI's `core/file_utils.py:inspect_file()` already handles all of these using PIL (images) and ffprobe (audio/video).

Metadata should be injected into the message content alongside the `[image: /path]` or `[file: /path]` tags so the agent can reason about the file before deciding to consume it.

### Files to Modify
- `nanobot/agent/context.py` — media preparation & metadata injection
- `bridge/src/whatsapp.ts` — already handles video download, may need metadata extraction
- New module: media utilities (borrowing from GenAI's `file_utils.py` and `media.py`)
- Provider-aware routing logic (native video vs frame extraction)

### Key GenAI Files to Reference
- `core/file_utils.py` — `inspect_file()`, `inspect_url()`, metadata extraction
- `core/llm/media.py` — `prepare_media()`, `extract_video_frames()`, `prepare_audio()`
- `core/llm/process.py` — `call_llm_multimodal()` for batch processing with media
- `tools/local_video_analyzer.py` — frame sampling + Whisper transcription