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

Optional env:

- `AZURE_OPENAI_API_VERSION` — defaults to `2024-10-21`
- `KLIMT_MODEL` — override the deployment name used for this session
- `KLIMT_SYSTEM` — system prompt
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

## Input prefixes

- `!cmd` — run a shell command directly and show the result as a tool box.
- `/name` — load `~/.klimt/skills/<name>/SKILL.md` into the conversation.

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

- syntax highlighting (highlight.js or shiki)
- persist conversations to disk
- richer multi-turn tool use
