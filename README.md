# Klimt

A tiny standalone-window harness for Azure OpenAI. Python + pywebview + an
HTML/JS frontend with streaming Markdown rendering, KaTeX, mermaid, and model
tool calls.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export AZURE_OPENAI_BASE_URL=https://<your-resource>.openai.azure.com
export AZURE_OPENAI_API_KEY=...
export AZURE_OPENAI_DEPLOYMENT=<your-deployment-name>
python3 -m klimt
```

## Model list

`/model` reads selectable endpoint configs from `~/.klimt/models.json`. A plain
string is treated as a legacy Azure OpenAI deployment name:

```json
["gpt-4.1", "o3"]
```

For multiple endpoint types, use objects. `name` is what you type after
`/model`; `model` or `deployment` is what gets sent to the provider. Keep API
keys in environment variables via `api_key_env`; putting secrets directly in
JSON works but is a bad habit.

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

Supported `provider` values are `azure`, `ollama`, `openai`, and `anthropic`.
`openai` is also useful for OpenAI-compatible gateways: set `base_url` and
`api_key_env`. Anthropic is currently wired through its OpenAI SDK
compatibility layer, so native-only Anthropic features are not exposed.

Optional env:

- `AZURE_OPENAI_API_VERSION` — defaults to `2024-10-21`
- `KLIMT_MODEL` — override the default deployment name used for new sessions
- `KLIMT_SYSTEM` — system prompt
- `KLIMT_CONTEXT_WINDOW` — context window used for the top-bar usage indicator. If unset, Klimt hides the percentage because Azure does not expose this reliably via the chat API.
- `KLIMT_DEBUG=1` — enables the webview devtools

## Streaming showcase

Klimt streams assistant output token-by-token from Azure OpenAI and updates the
current assistant bubble incrementally. While text is arriving, the frontend does
a cheap Markdown-only render once per animation frame. At `text_end`, it does the
expensive final pass for KaTeX and mermaid.

Good prompt for exercising the path:

```text
Show me a compact streaming demo: first a bullet list, then a Python code block,
then this equation $$E = mc^2$$, then a small mermaid sequence diagram.
```

What to expect:

- the placeholder `thinking_` is removed when the first streamed event arrives;
- Markdown, lists, tables, and code fences render incrementally;
- unterminated triple-backtick fences are temporarily auto-closed so partial code
  blocks don't trash the layout;
- KaTeX and mermaid render after the stream finishes, not during partial input;
- tool calls are shown as separate tool boxes between streamed assistant turns.

## Keys

- `Enter` — send
- `Shift+Enter` — newline
- `reset` button — clears server-side conversation history

The top bar shows `working...` while a request is still running and displays approximate context fill as `<percent>/<window>`, e.g. `42.1%/128k`. New sessions start with a unique temporary name and are auto-renamed from the first normal prompt.

## Input prefixes

- `!cmd` — run a shell command directly and show the result as a tool box.
- `/help` — show built-in commands and discovered skills.
- `/skills` — list available skills with short descriptions.
- `/compact [N]` — compact older context into structured state, keeping the last N history messages raw (default 8).
- `/model [name]` — show or switch the model endpoint for this session. Choices come from `~/.klimt/models.json`.
- `/reload` — reload `~/.klimt/AGENTS.md`, skill discovery, `tools.py`, Azure client config, and CSS.
- `/resume [name]` — resume a saved session for this folder.
- `/name <name>` — name the current session.
- `/quit` — close Klimt.
- `/<skill>` — load `~/.klimt/skills/<skill>/SKILL.md` into the conversation.

## Architecture

```text
klimt/
  app.py          # pywebview window + JS bridge (Api class)
  api.py          # ChatSession: history, streaming, Azure OpenAI tool loop
  tools.py        # read / write / bash implementations + JSON schemas
  skills.py       # ~/.klimt/skills discovery
  web/
    index.html    # transcript + textarea + CDN deps
    app.js        # bridge events, streaming renderer, marked/DOMPurify/KaTeX/mermaid
    style.css
```

Python owns conversation history. JS calls `window.pywebview.api.send(text)`.
During the call, Python pushes events into the page via
`window.klimt.handleEvent(...)`:

| Event | Purpose |
| --- | --- |
| `text_start` / `text_delta` / `text_end` | streamed assistant text |
| `text` | atomic Markdown message, e.g. skill-load confirmation |
| `tool` | tool call box with name, args, and result |
| `error` | error surfaced to the transcript |

## Roadmap

- persist conversations to disk
- automatic context compaction threshold
- richer multi-turn tool use
