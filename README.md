# Klimt

Klimt is a small local LLM harness with a native `pywebview` window, streaming
Markdown UI, persistent sessions, prompt layering, skills, and model tool calls.
It supports Azure OpenAI, OpenAI, OpenAI-compatible endpoints, Ollama,
Anthropic through Anthropic's OpenAI-compatible endpoint, and AWS Bedrock.

Klimt is heavily inspired by the fantastic [pi harness](https://pi.dev).

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

- `KLIMT_MODEL` — default model selector for new sessions; overrides the
  `default` field in `~/.klimt/models.json`. Must resolve to a configured
  entry (by name, provider model string, or declared class).
- `KLIMT_DEBUG=1` — enables webview devtools.

The top-bar usage indicator uses the active model's `context_window` from
`~/.klimt/models.json`. If omitted, the bar shows a token count without a
percentage.

## Model configuration

Model endpoints are objects in `~/.klimt/models.json`. `name` is what you type
after `/model`. `model` or `deployment` is what Klimt sends to the provider.

Optionally set a top-level `default` field to pick which entry new sessions
start on. It resolves like any other model reference -- by exact `name`,
provider model, or declared class. `KLIMT_MODEL` overrides it. With neither
set, the first listed model wins.

```json
{
  "default": "sonnet",
  "models": [ ... ]
}
```

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
    },
    {
      "name": "bedrock-claude",
      "provider": "bedrock",
      "model": "anthropic.claude-sonnet-4-5",
      "region": "us-east-1",
      "context_window": 200000,
      "max_completion_tokens": 16000
    }
  ]
}
```

Supported `provider` values are:

- `azure`
- `openai`
- `ollama`
- `anthropic`
- `bedrock`

Do not put secret values in `models.json`; put the environment variable name in
`api_key_env` for API-key based providers. Authenticated providers
(`azure` and `openai`) require it. `ollama` does not unless your
OpenAI-compatible endpoint enforces auth. `bedrock` does not use `api_key_env`
either — it uses your AWS credentials from the environment (`AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`) or any credential source that
`boto3` resolves (instance profile, SSO, `~/.aws/credentials`, etc.). Set
`region` to specify the AWS region; if omitted, `boto3`'s default region
resolution applies.

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
  Must be strictly less than `max_completion_tokens`. Ignored when
  `adaptive_thinking` is `true`.
- **`adaptive_thinking`** — boolean. When `true`, uses Bedrock's adaptive
  thinking mode (`thinking.type: "adaptive"`) instead of the fixed-budget
  extended thinking mode (`thinking.type: "enabled"`). Required for newer
  Bedrock models such as Claude Opus 4.7 and later, which reject `"enabled"`
  outright. On these models, Claude decides dynamically how much to think;
  `thinking_budget_tokens` has no effect. Default: `false`.
- **`vision`** — boolean. When `true`, Klimt exposes the `visual` tool to this
  endpoint so the model can load local images. Default: `false`. The model must
  actually support image inputs; setting this on a text-only endpoint just gives
  the model a tool whose results it can't interpret.

`thinking_budget_tokens` only takes effect on the native Anthropic OAuth path
(i.e. when `api_key_env` is omitted) and on Bedrock endpoints that do not use
`adaptive_thinking`. It is silently ignored for all other providers and for
Anthropic entries that use an API key.

Example Bedrock entry for Claude Opus 4.7 (adaptive thinking required):

```json
{
  "name": "bedrock-claude-opus-4-7",
  "provider": "bedrock",
  "model": "us.anthropic.claude-opus-4-7",
  "region": "us-west-2",
  "context_window": 200000,
  "max_completion_tokens": 32000,
  "adaptive_thinking": true,
  "vision": true
}
```

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
| `/agents` | List available subagents (built-in, user, and project). |
| `/compact [N]` | Compact older context, keeping the last N history messages raw. Default: 8. |
| `/cd [path]` | Show or change the current working directory for this session. |
| `/model [name]` | Show configured models or switch the model endpoint for this session. |
| `/theme [name]` | Show or switch the UI CSS theme. Use Tab to complete names. |
| `/new` | Start a new empty session. |
| `/session <name>` | Resume a saved session. Use Tab to complete names. |
| `/sessions` | List saved sessions for this folder and pick one to resume or delete. |
| `/save [name]` | Save this session to disk, optionally under a new name. |
| `/back` | Go back to an earlier turn in the conversation. |
| `/reload` | Reload prompt layers, skills, tools, model config, and CSS. |
| `/quit` | Close Klimt. |
| `/<skill>` | Load `~/.klimt/skills/<skill>/SKILL.md` into the conversation. |

## Image attachments

Images can be attached to any message by pasting (`Cmd+V` / `Ctrl+V`) or
dragging a file onto the input box. Thumbnails appear in the attachment strip
above the textarea; click `×` on a thumbnail to remove it before sending.

On send, Klimt validates the attachment (format, size cap) and inlines it into
the user turn. The image is expanded into the correct multi-part format for each
provider — `image_url` for OpenAI-compatible endpoints, `image` blocks for
Anthropic native and Bedrock. Images are stored as compact JSON envelopes in
session history, so they survive save/reload and replay as inline thumbnails in
the transcript.

Requirements:

- The active model must have `vision: true` in `~/.klimt/models.json`. Sending
  with a non-vision model returns an error.
- Supported formats: PNG, JPEG, GIF, WebP.

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
- `Esc` — close completion popup, interrupt current tab's work, or cancel a pending `/back` or `/sessions` prompt.
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
| `websearch` | Search Startpage and return compact results. Supports `category='web'` (default) or `category='images'`; image results include direct image URLs and thumbnail URLs. |
| `visual` | Attach a local image (PNG, JPEG, GIF, WebP) to the next model turn. Only exposed for models with `vision: true` in `~/.klimt/models.json`. |
| `glob` | List files matching a shell-style glob pattern, sorted by most recently modified. |
| `grep` | Search file contents with `ag` (the_silver_searcher). Supports regex, glob filter, and case-insensitive matching. |

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
user prompt.

`/sessions` shows a numbered list of saved sessions for the current folder and
waits for a reply:

- `<n>` — resume session *n*.
- `delete <n>` — delete session *n*. If the active session is deleted, a new one starts. The updated list is shown so you can delete more without re-typing `/sessions`.
- `clear` — delete all saved sessions for this folder and start a new one.
- Any other reply (or Esc) — cancel.

`/back` lists the conversation turns and waits for a reply:

- `<n>` — truncate history to turn *n*, dropping everything after it.
- `<n> summary` — same, but inject a compacted summary of the dropped turns into context.
- Any other reply (or Esc) — cancel.

`/cd` changes the working directory for the current session. Sessions are scoped to their original folder; switching directory does not carry the current session into the new folder's session list.

## Architecture

```text
klimt/
  KERNEL.md         # non-persona harness prompt
  prompt.py         # prompt assembly and AGENTS.md discovery
  app.py            # pywebview window + JS bridge
  api.py            # ChatSession: history, persistence, compaction
  api_types.py      # shared dataclasses for session/event payloads
  runner.py         # streaming model/tool turn loop
  providers.py      # provider adapter around OpenAI-compatible clients
  anthropic_oauth.py# Anthropic OAuth Authorization Code + PKCE flow
  model_config.py   # ~/.klimt/models.json parsing
  commands.py       # slash/bang command metadata and handling helpers
  completion.py     # Tab-completion for commands, paths, models, sessions
  tools.py          # tool implementations + JSON schemas
  agents.py         # subagent manifest discovery
  agent_runner.py   # subagent execution with scoped tools and prompts
  skills.py         # ~/.klimt/skills discovery
  session_store.py  # per-folder session persistence
  themes.py         # CSS theme discovery and switching
  web/              # frontend
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

