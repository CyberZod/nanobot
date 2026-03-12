# Nanobot Codebase Walkthrough: My Genesis Notebook

This document tracks our journey through the `nanobot` codebase. I've copied this to my local directory so I can add my own notes and observations.

---

## The 30-Second Architecture Map

If you understand these 5 layers, you understand exactly how the bot breathes!

**Phase 1: The Foundations (World & Bloodstream)**
- **`config/`**: The World Model. Loads settings and API keys, validating everything before boot.
- **`bus/`**: The Bloodstream. An async Queue system. Channels dump inbound messages, the Agent dumps outbound responses. Tight decoupling.

**Phase 2: Base Abstractions (The Blueprints)**
- Enforces the strict rules (interfaces) for all plugins: `providers/base.py`, `channels/base.py`, and `tools/base.py`.

**Phase 3: The Brain (Agent Logic)**
- **`agent/context.py`**: Stitches MEMORY, SKILLS, and history into the LLM prompt.
- **`agent/skills.py` & `agent/memory.py`**: The storage systems for the context.
- **`agent/tools/registry.py` & `mcp.py`**: The Utility belt. Validates and executes tools locally or via MCP.
- **`agent/loop.py`**: The infinite conductor loop that handles concurrency, pulls messages, calls the LLM, and dispatches responses.

**Phase 4: Execution Logic (Autonomy)**
- **`cron/`**: The standalone background Heartbeat service. Triggers the Agent Loop on a schedule so the AI can act autonomously.

**Phase 5: Entry Points (The Front Door)**
- **`__main__.py` & `cli/commands.py`**: The Typer CLI wrapper that initializes the Config, Bus, Channels, Cron, and Agent Loop based on user commands (`gateway`, `agent`, `onboard`).

---

## Checkpoint 1: Foundations & Configuration (The Genesis)
**Date:** 2026-02-20
**Files Covered:** [schema.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/config/schema.py)

### Key Insights
- **Genesis Logic**: The configuration schema isn't just a list of settings; it's the "World Model" that the entire application uses to boot up.
- **Pydantic Power**:
    - `BaseModel`: Used for data validation and structure.
    - `BaseSettings`: Used to automatically pull configurations from the environment (using `NANOBOT_` prefix).
    - `model_config`: The "Magic Word" used to configure the class behavior itself.
- **The Interoperability Bridge**:
    - `alias_generator=to_camel`: Allows the application to talk JSON (camelCase) to the world while staying Pythonic (snake_case) internally.
    
    **Examples of Mapping:**
    | Entity | In `config.json` (World) | In Python Code (Internal) |
    | :--- | :--- | :--- |
    | Max Tokens | `"maxTokens": 8192` | `config.agents.defaults.max_tokens` |
    | API Key | `"apiKey": "sk-..."` | `config.providers.openai.api_key` |
    | Workspace Restrict | `"restrictToWorkspace": true` | `config.tools.restrict_to_workspace` |
    | Client ID | `"clientId": "cli_..."` | `config.channels.feishu.client_id` |

    - `populate_by_name=True`: Allows us to use both snake_case and camelCase during initialization.
- **Inter-file Dependency**: `schema.py` defines the parameters that all other systems (Bus, Agent, Channels) will eventually consume.
- **Advanced Python Patterns**:
    - `@property`: Turns a method into a "read-only attribute." It allows us to compute values on the fly (like `workspace_path`) while keeping the code clean as if we're just reading data.
    - **Logic in Config**: The `Config` root class contains methods like `get_api_key()` because it's the only place that knows about *all* providers and *all* models. It acts as the "Grand Central Station" for configuration lookup.
    - **Dynamic Logic**: Uses `getattr()` to grab config objects by name and `any()` with a generator expression to match keywords (e.g., matching "gpt" in "gpt-4o" to find the OpenAI provider).
    - **The Two-Stage Match**:
        1. **Direct Match**: Looks for keywords matching the model name (e.g., "claude" in "claude-3-opus"). If found AND an API key exists, it returns immediately.
        2. **Fallback Match**: If no keyword match has an API key, it simply returns the first configured provider it finds. This ensures the bot tries *something* if you misconfigure a specific key.

---

## Checkpoint 2: The Circulatory System (Message Bus)
**Date:** 2026-02-20
**Files Covered:** [events.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/bus/events.py), [queue.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/bus/queue.py)

### Key Insights
- **Decoupling**: The Message Bus ensures that components (Channels, Agents) don't need to know about each other's internals.
- **Event Types**: 
    - `InboundMessage`: Captures user input, channel source, and session data.
    - `OutboundMessage`: Captures the response to be delivered back to the user.
- **Asynchronous Flow**: Uses `asyncio.Queue` to handle messages without blocking. This allows the bot to receive new messages even while processing a complex AI task.
- **Session Identification**: The `session_key` (e.g., `telegram:12345`) is the "Genesis" of how Nanobot maintains multiple distinct conversations at once.
- **The Magic of `await Queue.get()`**: Unlike standard `.get()`, this pauses the specific code execution until data exists, but allows the rest of the bot (the Event Loop) to keep running and heartbeating.
- **The Dispatcher Loop**: The `dispatch_outbound` method runs in a background task, constantly checking the outbound queue and pushing messages to the correct channel subscribers. This is the "engine" that keeps the conversation flowing. But it checks checking if it should still be running, if yes, uses a second to wait for a message, if yes, it runs the dispatch, if no (no messages basically), the loop repeats and it checks if it still running again. We do this so if it isnt suppose to run it doesn't just wait for a message forever and we can easily break out of the function.
---

## Checkpoint 4: The Tool Blueprint
**Date:** 2026-02-23
**Files Covered:** [tools/base.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/agent/tools/base.py)

### Key Insights
- **Contract Enforcement**: Uses Python's `ABC` (Abstract Base Class) and `@abstractmethod` to force any new tool to define its `name`, `description`, `parameters`, and `execute` logic.
- **Defensive Validation**:
    - `validate_params` ensures the root of any tool arguments is an object.
    - `_validate` is a recursive engine that checks types, mandatory fields, and constraints (like length or range).
- **The Translator**: `to_schema` converts internal tool metadata into the specific format required by LLM providers (OpenAI Function Schema).

## Checkpoint 5: The Thinker's Blueprint (LLM Providers)
**Date:** 2026-02-23
**Files Covered:** [providers/base.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/providers/base.py)

### Key Insights
- **Standardization**: `LLMResponse` acts as a "Universal Language." It converts fragmented AI outputs into a standard format the bot understands.
- **Reasoning Support**: The `reasoning_content` field (optional) allows the bot to capture "Chain of Thought" tokens from specialized models like DeepSeek-R1 or o1-preview.
- **Decoupling**: By defining `tool_calls` in the base abstraction, the Agent loop can handle tool requests identically, regardless of which AI provider generated them.

## Checkpoint 6: The World Wrapper (Channels)
**Date:** 2026-02-23
**Files Covered:** [channels/base.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/channels/base.py)

### Key Insights
- **The Decoupler**: Channels never talk to the Agent directly. They only talk to the `MessageBus`. This is the "Separation of Concerns"—Telegram doesn't need to know how the Brain works.
- **Lifecycle Management**: Every channel must implement `start()` and `stop()`, allowing the bot to boot up multiple platforms at once.
- **Security Checkpoint**: The `is_allowed()` method is built into the base class, ensuring that regardless of the platform, the bot always checks the `allow_from` list before letting a message through.

---

# Phase 3: The Brain (Agent Logic)

---

## Checkpoint 7: The Skill Library (`skills.py`)
**Date:** 2026-02-24
**Files Covered:** [skills.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/agent/skills.py)

### Class: `SkillsLoader`
- **Purpose**: Discovers, loads, and manages "Skills" — markdown instruction files (`SKILL.md`) that teach the bot how to do specific things.

### Core Functions Explained

#### 1. `__init__(self, workspace, builtin_skills_dir=None)`
- **Purpose**: Sets up two skill directories — workspace (user-created) and built-in (shipped with nanobot).
- **Example**:
```python
loader = SkillsLoader(Path("~/.nanobot/workspace"))
# loader.workspace_skills = Path("~/.nanobot/workspace/skills")
# loader.builtin_skills  = Path(".../nanobot/skills")  (package directory)
```

#### 2. `list_skills(filter_unavailable=True) -> list[dict]`
- **Purpose**: Collects all skills from workspace and built-in directories.
- **Example**:
```python
loader.list_skills()
# Returns:
# [
#   {"name": "summarize", "path": ".../workspace/skills/summarize/SKILL.md", "source": "workspace"},
#   {"name": "translate", "path": ".../builtin/skills/translate/SKILL.md", "source": "builtin"}
# ]
```
- Workspace skills have **priority** — if both directories have a skill with the same name, only the workspace version is kept.
- If `filter_unavailable=True`, it checks requirements (like needing a CLI binary) and removes unavailable ones.

#### 3. `load_skill(name) -> str | None`
- **Purpose**: Loads the full text of a skill's `SKILL.md`.
- **Example**:
```python
loader.load_skill("summarize")
# Returns: "---\ndescription: Summarize text\n---\n\n# Summarize\nUse this skill to..."
```
- Checks workspace first, then built-in. Returns `None` if not found.

#### 4. `load_skills_for_context(skill_names) -> str`
- **Purpose**: Loads multiple skills and formats them for injection into the system prompt.
- **Example**:
```python
loader.load_skills_for_context(["summarize", "translate"])
# Returns:
# "### Skill: summarize\n\n(content without frontmatter)\n\n---\n\n### Skill: translate\n\n(content)"
```
- Note: It strips the YAML frontmatter (`---...---`) before including the content.

#### 5. `build_skills_summary() -> str`
- **Purpose**: Creates an XML summary of ALL skills (even unavailable ones) so the AI knows what exists.
- **Example**:
```python
loader.build_skills_summary()
# Returns:
# <skills>
#   <skill available="true">
#     <name>summarize</name>
#     <description>Summarize long text</description>
#     <location>/path/to/SKILL.md</location>
#   </skill>
#   <skill available="false">
#     <name>ffmpeg_edit</name>
#     <description>Edit video files</description>
#     <location>/path/to/SKILL.md</location>
#     <requires>CLI: ffmpeg</requires>
#   </skill>
# </skills>
```
- This is "Progressive Loading" — the AI sees the menu but only loads a skill's full instructions (via `read_file`) when it needs to use one.

#### 6. `get_always_skills() -> list[str]`
- **Purpose**: Returns skill names marked with `always: true` in their frontmatter. These are always injected into the system prompt.
- **Example**:
```python
loader.get_always_skills()
# Returns: ["code_style"]  (if code_style/SKILL.md has always: true)
```

#### 7. `get_skill_metadata(name) -> dict | None`
- **Purpose**: Parses the YAML frontmatter of a `SKILL.md` into a dictionary.
- **Example**:
```python
loader.get_skill_metadata("summarize")
# If SKILL.md starts with:
# ---
# description: Summarize long text
# always: true
# ---
# Returns: {"description": "Summarize long text", "always": "true"}
```

#### 8. `_check_requirements(skill_meta) -> bool`
- **Purpose**: Checks if the system has the tools a skill needs (CLI binaries, environment variables).
- **Example**:
```python
# skill_meta = {"requires": {"bins": ["ffmpeg"], "env": ["OPENAI_API_KEY"]}}
loader._check_requirements(skill_meta)
# If ffmpeg is NOT installed: Returns False
# If ffmpeg IS installed and OPENAI_API_KEY is set: Returns True
```

#### 9. `_strip_frontmatter(content) -> str`
- **Purpose**: Removes the YAML header (`---...---`) from a skill's markdown content.
- **Example**:
```python
loader._strip_frontmatter("---\ndescription: hello\n---\n\n# My Skill\nDo this...")
# Returns: "# My Skill\nDo this..."
```

---

## Checkpoint 8: The Notebook (`memory.py`)
**Date:** 2026-02-24
**Files Covered:** [memory.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/agent/memory.py)

### Class: `MemoryStore`
- **Purpose**: Manages persistent memory using two simple text files. This is the bot's "notebook" — it survives between conversations.

### Core Functions Explained

#### 1. `__init__(self, workspace)`
- **Purpose**: Sets up the memory directory and file paths. Uses `ensure_dir` (from Phase 1 Utilities) to create the folder if it doesn't exist.
- **Example**:
```python
store = MemoryStore(Path("~/.nanobot/workspace"))
# store.memory_dir  = Path("~/.nanobot/workspace/memory/")  (created if missing)
# store.memory_file = Path("~/.nanobot/workspace/memory/MEMORY.md")
# store.history_file = Path("~/.nanobot/workspace/memory/HISTORY.md")
```

#### 2. `read_long_term() -> str`
- **Purpose**: Reads the entire contents of `MEMORY.md`. Returns empty string if the file doesn't exist yet.
- **Example**:
```python
store.read_long_term()
# Returns: "User prefers dark mode.\nUser's name is Zod.\nTimezone: WAT"
```

#### 3. `write_long_term(content)`
- **Purpose**: Overwrites `MEMORY.md` with new content. This is a full rewrite, not an append.
- **Example**:
```python
store.write_long_term("User prefers dark mode.\nUser's name is Zod.")
# MEMORY.md now contains exactly that text.
```

#### 4. `append_history(entry)`
- **Purpose**: Appends a new entry to `HISTORY.md`. This is append-only (like a diary — you never erase old entries).
- **Example**:
```python
store.append_history("2026-02-24: User asked about the weather in Lagos.")
# HISTORY.md now has a new line at the bottom:
# "2026-02-24: User asked about the weather in Lagos.\n\n"
```

#### 5. `get_memory_context() -> str`
- **Purpose**: Formats the long-term memory for injection into the system prompt.
- **Example**:
```python
store.get_memory_context()
# If MEMORY.md exists: Returns "## Long-term Memory\nUser prefers dark mode..."
# If MEMORY.md is empty: Returns ""  (so the prompt stays clean)
```

---

## Checkpoint 9: The Script Writer (`context.py`)
**Date:** 2026-02-24
**Files Covered:** [context.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/agent/context.py)

### Class: `ContextBuilder`
- **Purpose**: Assembles all pieces (identity, memory, skills, conversation history) into a single prompt that gets sent to the LLM. This is the "Script Writer" — before the AI can think, it writes the script.

### Core Functions Explained

#### 1. `__init__(self, workspace)`
- **Purpose**: Sets up the builder with a workspace path and loads its two helpers.
- **Example**:
```python
builder = ContextBuilder(Path("~/.nanobot/workspace"))
# builder.workspace = Path("~/.nanobot/workspace")
# builder.memory  = MemoryStore(...)   # reads MEMORY.md
# builder.skills  = SkillsLoader(...)  # finds SKILL.md files
```

#### 2. `build_system_prompt(skill_names=None) -> str`
- **Purpose**: Builds the giant "System Prompt" string that tells the AI who it is.
- **Example**:
```python
prompt = builder.build_system_prompt()
# Returns something like:
# "# nanobot 🐈
#  You are nanobot, a helpful AI assistant...
#  ---
#  ## AGENTS.md
#  (contents of your AGENTS.md file)
#  ---
#  # Memory
#  ## Long-term Memory
#  User prefers dark mode...
#  ---
#  # Skills
#  <skills>
#    <skill available="true"><name>summarize</name>...</skill>
#  </skills>"
```
It glues together 4 layers: **Identity → Bootstrap Files → Memory → Skills**, separated by `---`.

#### 3. `_get_identity() -> str`
- **Purpose**: Creates the "Who Am I" section with live system info.
- **Example**:
```python
builder._get_identity()
# Returns:
# "# nanobot 🐈
#  ...
#  ## Current Time
#  2026-02-24 21:54 (Monday) (WAT)
#  ## Runtime
#  Windows AMD64, Python 3.12.0
#  ## Workspace
#  Your workspace is at: C:\Users\user\.nanobot\workspace"
```
This is why the bot always knows today's date and your OS — it's injected fresh every time.

#### 4. `_load_bootstrap_files() -> str`
- **Purpose**: Reads special markdown files from your workspace that customize the bot's personality.
- **Example**:
```python
builder._load_bootstrap_files()
# If workspace has SOUL.md ("You are a pirate") and USER.md ("User speaks Yoruba"):
# Returns: "## SOUL.md\n\nYou are a pirate\n\n## USER.md\n\nUser speaks Yoruba"
```
Files that don't exist are silently skipped (no crash).

#### 5. `build_messages(history, current_message, ...) -> list[dict]`
- **Purpose**: Builds the **full conversation** that gets sent to the LLM. This is the most important function.
- **Example**:
```python
builder.build_messages(
    history=[{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello!"}],
    current_message="What's the weather?",
    channel="telegram",
    chat_id="12345"
)
# Returns:
# [
#   {"role": "system",    "content": "(entire system prompt + Current Session: telegram/12345)"},
#   {"role": "user",      "content": "Hi"},
#   {"role": "assistant", "content": "Hello!"},
#   {"role": "user",      "content": "What's the weather?"}
# ]
```
**System prompt first → Old history → New message last.** This is the standard format every LLM expects.

#### 6. `_build_user_content(text, media) -> str | list`
- **Purpose**: If the user sends an image, converts it to base64 and bundles it with the text. If no image, returns plain text.
- **Example (no image)**:
```python
builder._build_user_content("Hello", None)
# Returns: "Hello"
```
- **Example (with image)**:
```python
builder._build_user_content("What's this?", ["/path/to/photo.jpg"])
# Returns:
# [
#   {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ..."}},
#   {"type": "text", "text": "What's this?"}
# ]
```

#### 7. `add_tool_result(messages, tool_call_id, tool_name, result) -> list`
- **Purpose**: After a tool runs, adds the result back into the conversation so the AI can see what happened.
- **Example**:
```python
builder.add_tool_result(messages, "call_abc123", "get_weather", "Sunny, 25°C")
# Appends: {"role": "tool", "tool_call_id": "call_abc123", "name": "get_weather", "content": "Sunny, 25°C"}
```

#### 8. `add_assistant_message(messages, content, tool_calls, reasoning_content) -> list`
- **Purpose**: Records what the AI said or did (including any tool calls it requested).
- **Example (text reply)**:
```python
builder.add_assistant_message(messages, "The weather is sunny!")
# Appends: {"role": "assistant", "content": "The weather is sunny!"}
```
- **Example (tool call request)**:
```python
builder.add_assistant_message(messages, None, tool_calls=[...])
# Appends: {"role": "assistant", "tool_calls": [...]}
# Notice: content is omitted (not set to empty string) because some backends reject empty content.
```

---

## Checkpoint 10: The Plugin System (MCP & Registry)
**Date:** 2026-03-06
**Files Covered:** [registry.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/agent/tools/registry.py), [mcp.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/agent/tools/mcp.py)

### Key Insights
- **ToolRegistry**: The "Utility Belt". It holds all tools, handles the parameter validation (`validate_params`), and executes them. If a tool fails, the registry appends a self-correction hint (`[Analyze the error...`) to encourage the AI to try again.
- **Model Context Protocol (MCP)**: An open standard for connecting AI agents to external tool servers.
- **MCPToolWrapper**: Takes an external MCP tool definition and wraps it to look exactly like a native Python `Tool` class. The AI doesn't know the difference.
- **The Context Window Problem**: Currently, *all* tool schemas (built-in + MCP) are sent to the AI on every call. This can cause context bloat if there are dozens of MCP tools. The progressive loading pattern used in `SkillsLoader` is a potential future fix for this.

---

## Checkpoint 11: The Dispatcher (`manager.py`)
**Date:** 2026-03-06
**Files Covered:** [manager.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/channels/manager.py)

### Key Insights
- **The Dictionary Pattern**: Instead of complex callbacks, the `ChannelManager` simply holds an active dictionary: `self.channels = {"telegram": TelegramChannel(), ...}`.
- **Outbound Routing**: The `_dispatch_outbound` loop checks the Message Bus every second. When an `OutboundMessage` arrives, it reads `msg.channel`, looks up the correct channel object in the dictionary, and calls its `.send()` method.
- **Progress Filtering**: It checks the metadata of progress updates (`_progress`, `_tool_hint`) and can silently drop them if the user disabled them in the config.

---

## Checkpoint 12: The Conductor (`loop.py`)
**Date:** 2026-03-06
**Files Covered:** [loop.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/agent/loop.py)

### Key Insights
- **The Heartbeat**: The infinite `run()` loop constantly pulls from the inbound bus.
- **Asynchronous Task Dispatching**: Instead of blocking, `run()` spawns a background `asyncio.Task` for every incoming message. This ensures the loop remains instantly responsive to commands like `/stop`.
- **The Processing Lock**: `_processing_lock` ensures that even though tasks spawn in parallel, the actual AI processing (`_process_message`) happens strictly one-at-a-time to prevent race conditions.
- **Session Tracking**: Active tasks are tracked in a dictionary (`self._active_tasks["session_key"]`). If a user sends `/stop`, the bot finds their tasks in the dictionary and cancels them instantly.
- **Smart Turn Saving**: `_save_turn()` truncates massive tool outputs (>500 chars) and replaces base64 images with `[image]` before saving to the session file, preventing the history files from blowing up.
- **Consolidation Guards**: `asyncio.Lock` per session prevents duplicate background memory consolidations from running simultaneously for the same user.

---

## Checkpoint 13: The Scheduler (`cron/service.py`)
**Date:** 2026-03-09
**Files Covered:** [cron/types.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/cron/types.py), [cron/service.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/cron/service.py)

### Key Insights
- **The Epoch**: Time is calculated in milliseconds since the Unix Epoch (Jan 1, 1970). This provides an absolute, universal number that never changes regardless of time zones or leap years. Functions like `_now_ms()` return this integer.
- **The Blueprint (`types.py`)**:
    - `CronSchedule`: Defines *when* a job runs (`at`, `every`, `cron`).
    - `CronPayload`: Defines *what* happens. For nanobot, this is usually `agent_turn` with a text message.
    - `CronJobState`: Tracks the live status (e.g., `next_run_at_ms`).
- **The Engine (`service.py`)**: 
    - Loads/saves jobs to a JSON file.
    - `_compute_next_run`: Calculates the exact next millisecond a job should fire.
- **The Async "Alarm Clock" Loop**:
    - `_arm_timer()` finds the *single earliest* upcoming job and creates an `asyncio.Task` that sleeps (`await asyncio.sleep(delay_s)`) until that exact moment.
    - If a new job is added that runs sooner, `_arm_timer()` cancels (`task.cancel()`) the existing sleep task and creates a new one for the earlier time.
    - When the sleep finishes, it calls `_on_timer()`, which executes the due jobs, updates their next run times, and then critically calls `_arm_timer()` again to set the alarm clock for the *next* job in line. This "circular" asynchronous call keeps the service running indefinitely.
- **Integration**: `CronService` is unaware of AI. The `gateway` in `cli/commands.py` sets a callback (`on_job`) that takes the cron message, sends it through the `AgentLoop`, and publishes the AI's response to the specified channel.

---

## Checkpoint 14: Upstream Config Refinements
**Date:** 2026-03-09
**Files Covered:** [paths.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/config/paths.py), [loader.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/config/loader.py)

### Key Insights
- **The DRY Path Refactor (`paths.py`)**: Previously, tracking down where things were saved (Memory, Cron jobs, session logs) logic was scattered. Now, ALL paths are centralized in a single file via helper functions like `get_data_dir()` and `get_runtime_subdir()`. This makes changing the bot's root installation foldery trivial.
- **Multi-Instance Support (`loader.py`)**: By adding `set_config_path()`, the application no longer hard-codes `~/.nanobot/config.json`. You can now run two independent instances of `nanobot` on the same machine pointing at completely different config files. Because `paths.py` derives the data folders from the config file's location, multiple bots will cleanly maintain their own separate memory, skills, and histories without colliding.

---

## Checkpoint 15: Session Management Deep Dive
**Date:** 2026-03-10
**Files Covered:** [manager.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/session/manager.py), [loop.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/agent/loop.py)

### Key Insights
- **Bus vs. Session**: The `MessageBus` only carries **one raw message** at a time (no history). The `Session` is the full conversation archive on disk. The `AgentLoop` stitches them together: it picks a message from the bus, loads history from the session, builds the prompt, and calls the LLM.
- **`_save_turn()` Transformations**: After the LLM responds, the new messages are cleaned before saving:
    - Tool results > 500 chars → truncated
    - Base64 images → replaced with `[image]`
    - Runtime context tags (time, channel) → stripped entirely
    - Empty assistant messages → skipped (they "poison" context)
    - Error responses (`finish_reason == "error"`) → **not saved at all** (prevents infinite 400 loops, bug #1303)
- **The `last_consolidated` Pointer**: Acts like a bookmark in the session's message list.
    - `get_history()` uses `self.messages[self.last_consolidated:]` — it only returns messages **after** the bookmark.
    - When the unconsolidated count hits `memory_window` (default 100), a background task summarizes the old messages into `MEMORY.md` / `HISTORY.md` and advances the pointer.
    - Old messages are **never deleted** from the `.jsonl` file (append-only for speed and auditability). The pointer simply tells `get_history()` to skip them.

---

## Checkpoint 16: MCP (Model Context Protocol)
**Date:** 2026-03-10
**Files Covered:** [mcp.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/agent/tools/mcp.py)

### Key Insights
- **What MCP Is**: An open standard (by Anthropic) that lets AI agents connect to external tool servers over a standardized protocol. Like USB for AI tools.
- **Three Transports**: Nanobot supports `stdio` (spawns a child process), `sse` (HTTP streaming), and `streamableHttp` (newer HTTP-based).
- **The Disguise (`MCPToolWrapper`)**: For each tool an MCP server exposes, nanobot creates an `MCPToolWrapper` that extends the same `Tool` base class as native tools. The AI cannot tell the difference between `read_file` (local) and `mcp_github_create_issue` (remote).
- **Registration**: MCP tools are registered into the same `ToolRegistry` as native tools. `registry.get_definitions()` returns ALL tool schemas (native + MCP) in one flat list.
- **Schemas Sent Every Turn**: On every LLM call, ALL tool schemas are sent via `tools=self.tools.get_definitions()`. There is no progressive loading for tools yet (unlike Skills, which use summaries). This means adding many MCP tools increases token usage on every turn.
- **Timeout Protection**: Each MCP tool call is wrapped in `asyncio.wait_for(timeout=30s)`. If the external server hangs, it gracefully returns an error string instead of crashing.

---

## Checkpoint 17: Provider Routing & Environment Variables
**Date:** 2026-03-11
**Files Covered:** [schema.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/config/schema.py), [registry.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/providers/registry.py)

### Key Insights
- **The Routing Logic (`_match_provider`)**: When the bot receives a model string (like `"claude-3.5-sonnet"`), it routes the request in three stages:
  1. **Explicit Prefix:** Does it start with an explicit provider? (e.g., `"openrouter/"`) → Matches OpenRouter directly.
  2. **Keyword Matching:** Does the name contain a known keyword? (e.g., `"claude"` keyword matches `anthropic`). If the matched provider actually has an API key configured, it routes there natively.
  3. **Gateway Fallback:** If the keyword match fails or the direct provider is missing an API key (but you have an OpenRouter key starting with `sk-or-` configured in `providers.openrouter.api_key`), the request gracefully falls back to the OpenRouter gateway.
- **Environment Variable Overrides**: pydantic-settings binds anything prefixed with `NANOBOT_` directly to the `Config` hierarchy, using `__` for nesting. Want to change the model without opening `config.json`? Run `$env:NANOBOT_AGENTS__DEFAULTS__MODEL = "openrouter/qwen/qwen3.5-9b"`. `agents.defaults.model` dictates the brain of the single `AgentLoop` instance started by the bot.

---

## Checkpoint 18: Channel Sessions & User Isolation
**Date:** 2026-03-11
**Files Covered:** [manager.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/session/manager.py), [commands.py](file:///c:/Users/user/Documents/Dev/nanobot/nanobot/cli/commands.py)

### Key Insights
- **The CLI is just another Channel**: When you run the single-shot test (`nanobot agent -m "..."`), it doesn't bypass the session system. It simply uses the default session ID of `"cli:direct"`. Every interaction gets a session.
- **Per-User / Per-Chat Isolation**: Channels like Telegram and WhatsApp don't have a single "Telegram Session". They dynamically generate IDs based on the user or group ID (e.g., `"telegram:123456"`, `"whatsapp:14155552671"`).
- **The Beauty of Abstraction**: The core `AgentLoop` receives a raw `InboundMessage` with a `session_id`. It just pulls that file, reads the history, and responds. It has absolutely no idea if it's talking to someone on WhatsApp, Telegram, or the Terminal. This means millions of users can message the same bot instance on WhatsApp simultaneously, and each gets their own perfectly isolated `.jsonl` session file!

*(More checkpoints will be added as we progress...)*
