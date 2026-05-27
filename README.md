# Klimt

Klimt is a small local LLM harness with a native `pywebview` window, streaming
Markdown UI, persistent sessions, prompt layering, skills, and model tool calls.
It supports Azure OpenAI, OpenAI, OpenAI-compatible endpoints, Ollama, and
Anthropic through Anthropic's OpenAI-compatible endpoint.

## Requirements

- Python 3.10+
- A working GUI environment for `pywebview`.
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

For an editable install with the `klimt` console script:

```bash
pip install -e .
klimt
```

## Quick start

Create `~/.klimt/models.json` with at least one model endpoint, then start Klimt:

```bash
mkdir -p ~/.klimt
cat > ~/.klimt/models.json <<'JSON'
{
  "models": [
    {
      "name": "azure-4.1",
      "provider": "azure",
      "deployment": "gpt-4.1",
      "base_url": "https://your-resource.openai.azure.com",
      "api_version": "2024-10-21",
      "api_key_env": "AZURE_OPENAI_API_KEY"
    }
  ]
}
JSON

export AZURE_OPENAI_API_KEY=...
python3 -m klimt
```

Optional env:

- `KLIMT_MODEL` — default model selector for new sessions; must name an entry in
  `~/.klimt/models.json`.
- `KLIMT_DEBUG=1` — enables webview devtools.

The top-bar usage indicator uses the active model's `context_window` from
`~/.klimt/models.json`. If omitted, the bar shows a token count without a
percentage.

## Model configuration

Model endpoints are objects in `~/.klimt/models.json`. `name` is what you type
after `/model`. `model` or `deployment` is what Klimt sends to the provider.

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
      "context_window": 200000,
      "max_completion_tokens": 16000,
      "thinking_budget_tokens": 10000
    }
  ]
}
```

Supported `provider` values are:

- `azure`
- `openai`
- `ollama`
- `anthropic`

Do not put secret values in `models.json`; put the environment variable name in
`api_key_env` for API-key based providers. Authenticated providers
(`azure` and `openai`) require it. `ollama` does not unless your
OpenAI-compatible endpoint enforces auth.

`openai` can also point at OpenAI-compatible gateways by setting `base_url` and
`api_key_env`. Anthropic has two modes:

- With `api_key_env`, Klimt uses the configured Anthropic API key through
  Anthropic's OpenAI SDK compatibility layer.
- Without `api_key_env`, Klimt performs Anthropic OAuth Authorization Code + PKCE
  login in the browser, stores the access/refresh tokens in
  `~/.klimt/anthropic-oauth.json`, refreshes them when expired, and calls
  Anthropic's native Messages API with Claude Code OAuth headers.

OAuth token files are written with mode `0600`. Do not use Claude web session
cookies; Klimt only supports API/OAuth-style credentials through Anthropic's API
endpoint.

### Token limits

Two optional fields control output size per model endpoint:

- **`max_completion_tokens`** — maximum tokens the model may generate in a
  single response. Default: `4096`. Accepts `max_tokens` as an alias.
- **`thinking_budget_tokens`** — reasoning token budget for Anthropic extended
  thinking. Default: `0` (disabled). Accepts `thinking_budget` as an alias.
  Must be strictly less than `max_completion_tokens`.

`thinking_budget_tokens` only takes effect on the native Anthropic OAuth path
(i.e. when `api_key_env` is omitted). It is silently ignored for all other
providers and for Anthropic entries that use an API key.

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
| `/hotkeys` | Show keyboard shortcuts. |
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

Klimt has independent UI tabs. Each tab owns its own session, model, history,
queue, and busy state. Tab layout is ephemeral; saved sessions remain the durable
unit of storage.

Visible controls:

- Click a tab to activate it.
- `+` opens a new tab.
- `×` closes an idle tab.

Keyboard shortcuts:

- `Enter` — send.
- `Shift+Enter` — newline.
- `Tab` — complete commands, paths, models, and session names at the cursor.
- `Esc` — close completion popup, or interrupt current tab's work.
- `Ctrl+T` — new tab.
- `Ctrl+W` — close current tab.
- `Ctrl+Tab` / `Ctrl+Shift+Tab` — next / previous tab.
- `Alt+1` ... `Alt+9` — switch to tab by index.
- `Ctrl+R` — toggle reasoning visibility.
- `Ctrl+J` / `Ctrl+K` — scroll current transcript.

Cmd shortcuts are deliberately unbound.

The status bar below the input shows the active tab's model, saved session name,
working/queue state, and approximate context usage. New sessions start with a
generated temporary name and are auto-renamed from the first normal prompt.

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

