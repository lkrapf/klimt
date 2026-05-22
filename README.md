# Klimt

Klimt is a small local LLM harness with a native `pywebview` window, streaming
Markdown UI, persistent sessions, prompt layering, skills, and model tool calls.
It supports Azure OpenAI, OpenAI, OpenAI-compatible endpoints, Ollama, and
Anthropic through Anthropic's OpenAI-compatible endpoint.

## Requirements

- Python 3.10+
- A working GUI environment for `pywebview`
  - macOS works through WebKit.
  - Linux may need GTK/WebKit packages installed by the OS package manager.
- One configured model endpoint.

Install from a checkout:

```bash
git clone <repo-url> klimt
cd klimt
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m klimt
```

Running with `python3 -m klimt` assumes you are in the repository checkout. If you
want a packaged install, add packaging first; Klimt does not currently ship one.

## Quick start: Azure via environment

The simplest configuration is the Azure OpenAI env-only path:

```bash
export AZURE_OPENAI_BASE_URL=https://<your-resource>.openai.azure.com
export AZURE_OPENAI_API_KEY=...
export AZURE_OPENAI_DEPLOYMENT=<your-deployment-name>
python3 -m klimt
```

Optional Azure/default-model env:

- `AZURE_OPENAI_API_VERSION` — defaults to `2024-10-21`.
- `KLIMT_MODEL` — default model selector for new sessions.
- `KLIMT_CONTEXT_WINDOW` — context window used for the top-bar usage indicator;
  defaults to `128000`.
- `KLIMT_DEBUG=1` — enables webview devtools.

## Model configuration

For multiple selectable endpoints, create `~/.klimt/models.json`.

A plain string is treated as an Azure deployment name:

```json
["gpt-4.1", "o3"]
```

For explicit endpoints, use objects. `name` is what you type after `/model`.
`model` or `deployment` is what Klimt sends to the provider.

```json
{
  "models": [
    {
      "name": "azure-4.1",
      "provider": "azure",
      "deployment": "gpt-4.1",
      "base_url": "https://your-resource.openai.azure.com",
      "api_version": "2024-10-21",
      "api_key_env": "AZURE_OPENAI_API_KEY"
    },
    {
      "name": "openai-4.1",
      "provider": "openai",
      "model": "gpt-4.1",
      "api_key_env": "OPENAI_API_KEY"
    },
    {
      "name": "local-llama",
      "provider": "ollama",
      "model": "llama3.1",
      "base_url": "http://127.0.0.1:11434/v1"
    },
    {
      "name": "claude",
      "provider": "anthropic",
      "model": "claude-sonnet-4-6",
      "api_key_env": "ANTHROPIC_API_KEY"
    }
  ]
}
```

Supported `provider` values are:

- `azure`
- `openai`
- `ollama`
- `anthropic`

`openai` can also point at OpenAI-compatible gateways by setting `base_url` and
`api_key_env`. Anthropic is currently wired through its OpenAI SDK compatibility
layer, so native-only Anthropic features are not exposed.

## Global and project instructions

Klimt reads prompt instructions from Markdown files:

- `klimt/KERNEL.md` — harness/tool protocol shipped with Klimt.
- `~/.klimt/AGENTS.md` — global user profile and preferences.
- `AGENTS.md` files in the current working tree — project-local instructions.

Minimal global profile example:

```md
# Agent

Be concise and technical.

# Output

Use GitHub-flavored Markdown.
```

Project `AGENTS.md` files are discovered from the current directory upward and
injected outermost first. Project instructions may specialize the global profile,
but they cannot redefine tool behavior or harness safety boundaries.

Use `/reload` after editing prompt files; existing conversation history is kept.

## Skills

Skills live under `~/.klimt/skills/**/SKILL.md`. Klimt discovers them at startup
and injects only the name, description, and path into the system prompt. Full
skill bodies are loaded on demand.

Example:

```text
~/.klimt/skills/example/SKILL.md
```

```md
---
name: example
description: Use when the user asks for the example workflow.
---

# Example skill

Follow these task-specific instructions...
```

Load a skill explicitly with:

```text
/example
```

The model is also instructed to load matching skills itself when the task fits a
skill description.

## Commands

| command | description |
|---|---|
| `!<cmd>` | Run a shell command directly and show the result as a tool box. |
| `/help` | Show built-in commands and session help. |
| `/skills` | List discovered skills. |
| `/compact [N]` | Compact older context, keeping the last N history messages raw. Default: 8. |
| `/model [name]` | Show or switch the model endpoint for this session. |
| `/new` | Start a new empty session. |
| `/sessions` | List saved sessions for this folder. |
| `/sessions resume <number|name>` | Resume a saved session. |
| `/sessions delete <number|name>` | Delete a saved session. |
| `/sessions clear confirm` | Delete all saved sessions for this folder and start a new one. |
| `/name [name]` | Show or rename the current session. |
| `/reload` | Reload prompt layers, skills, tools, model config, and CSS. |
| `/quit` | Close Klimt. |
| `/<skill>` | Load `~/.klimt/skills/<skill>/SKILL.md` into the conversation. |

## Keys and UI

- `Enter` — send.
- `Shift+Enter` — newline.
- `Esc` — interrupt current work.

The top bar shows `working...` while a request is running and displays
approximate context fill as `<percent>/<window>`, for example `42.1%/128k`.
New sessions start with a generated temporary name and are auto-renamed from the
first normal prompt.

Klimt streams assistant output token-by-token. During streaming, the frontend
renders Markdown cheaply once per animation frame. At `text_end`, it runs the
more expensive final pass for KaTeX and mermaid. Reasoning/thinking blocks are
shown separately when the provider streams them.

## Tools exposed to the model

| tool | purpose |
|---|---|
| `read` | Read UTF-8 text files with line numbers and capped output. |
| `edit` | Apply exact, unique, non-overlapping text replacements to one file. |
| `write` | Write a full file, creating parent directories. |
| `bash` | Run a shell command with a 120s timeout. |
| `webfetch` | Fetch and extract text from an HTTP(S) URL. |
| `websearch` | Search DuckDuckGo's HTML endpoint and return compact results. |

Tool errors are returned to the model as strings so it can recover. `bash` uses
the current user account and is not sandboxed.

## Prompt layering

Klimt assembles the system prompt in this physical order:

1. **Kernel** — harness/tool protocol and instruction hierarchy from
   `klimt/KERNEL.md`.
2. **Runtime manifests** — currently available tools and discovered skills.
3. **Global profile** — `~/.klimt/AGENTS.md`.
4. **Project instructions** — `AGENTS.md` files from the current working tree,
   outermost first.

The kernel defines authority order. Project instructions may specialize the
global profile because they are more specific to the current working tree.
Nothing below the kernel may redefine tool behavior, authority order, or harness
safety boundaries.

## Sessions and storage

Sessions are stored per working folder under:

```text
~/.klimt/sessions/<folder-hash>/
```

A new session starts with a generated name and is renamed from the first normal
user prompt. `/sessions` lists saved sessions for the current folder.

## Architecture

```text
klimt/
  KERNEL.md        # non-persona harness prompt
  prompt.py        # prompt assembly and AGENTS.md discovery
  app.py           # pywebview window + JS bridge
  api.py           # ChatSession: history, persistence, compaction
  runner.py        # streaming model/tool turn loop
  providers.py     # provider adapter around OpenAI-compatible clients
  model_config.py  # ~/.klimt/models.json parsing
  commands.py      # slash/bang command metadata and handling helpers
  tools.py         # tool implementations + JSON schemas
  skills.py        # ~/.klimt/skills discovery
  session_store.py # per-folder session persistence
  web/             # frontend
```

Python owns conversation history. JS calls `window.pywebview.api.send(text)`.
During the call, Python pushes events into the page via
`window.klimt.handleEvent(...)`:

| Event | Purpose |
|---|---|
| `reasoning_start` / `reasoning_delta` / `reasoning_end` | streamed reasoning block |
| `reasoning` | restored reasoning block during session replay |
| `text_start` / `text_delta` / `text_end` | streamed assistant text |
| `text` | atomic Markdown message, e.g. skill-load confirmation |
| `tool` | tool call box with name, args, and result |
| `error` | error surfaced to the transcript |
| `done` | request/command finished |

