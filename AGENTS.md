# Klimt — agent notes

Project-local guidance for AI assistants working on this repo.

## Philosophy

- **Lean code.** Add features only when needed. No speculative fallbacks, no
  options nobody asked for. Delete code that isn't earning its keep.
- Small, readable modules over clever abstractions.
- Pushback over politeness. If a request is wrong-headed, say so.

## Stack

- Python 3.10+ (developed against 3.14).
- `pywebview` for the native window (WebKit on macOS).
- `openai` SDK with the `AzureOpenAI` client.
- Frontend: vanilla HTML/JS, `marked` + `DOMPurify` from a CDN.

## Azure OpenAI

- Auth via `AZURE_OPENAI_BASE_URL`, `AZURE_OPENAI_API_KEY`,
  `AZURE_OPENAI_DEPLOYMENT`. API version optional via
  `AZURE_OPENAI_API_VERSION` (default `2024-10-21`).
- The `model` argument to `chat.completions.create` is the **deployment name**,
  not a model name. Azure quirk.
- We always use `max_completion_tokens` (not `max_tokens`). Reasoning models
  (o1/o3/o4 family) require it; current GPT-4o-class deployments accept it.
  If a deployment ever rejects it, change the keyword — don't add a fallback.

## Known model gotchas (only fix when actually hit)

- Some reasoning deployments reject `role: "system"`. If we hit this, replace
  with `role: "developer"` or drop the system message.
- Reasoning deployments often reject `temperature`, `top_p`, and `stream`.
  We don't send any of those today; keep it that way unless a feature needs it.

## Streaming

Python streams via `client.chat.completions.create(stream=True)` and pushes
events to JS by calling `window.klimt.handleEvent(...)` through pywebview's
`window.evaluate_js`. Event types:

- `text_start` / `text_delta` / `text_end` — streamed assistant text.
- `text` — atomic markdown message (e.g. skill-load confirmation).
- `tool` — atomic tool box (name, args, result).
- `error` — error string surfaced to the UI.

During streaming the JS renders markdown only, with an auto-close ``` heuristic
for unterminated code fences. KaTeX and mermaid run once at `text_end` because
both choke on partial input and mermaid re-renders are expensive. Renders are
rAF-throttled to one per frame.

## Architecture

```
klimt/
  app.py     # pywebview window, Api class exposed to JS
  api.py     # ChatSession: history + tool-call loop against Azure OpenAI
  tools.py   # read / write / bash implementations + JSON schemas
  web/
    index.html
    app.js   # bridge calls, marked + DOMPurify + KaTeX + mermaid
    style.css
```

- Python owns conversation history.
- JS calls `window.pywebview.api.send(text)` and gets `{ok, events}` back,
  where `events` is a list of `{type: 'text'|'tool', ...}` entries.

## Input prefixes

Handled in `ChatSession.send` before any model call:

- `!cmd` — runs `cmd` via the bash tool. Output is shown in the UI and
  appended to history as a user message (`$ cmd\n<output>`). No model call.
- `/name` — loads `~/.klimt/skills/<name>/SKILL.md` and appends its body to
  history as a user message. Match is by directory name, then by frontmatter
  `name:`. No model call.
- `/reload` — reloads prompt layers, skill discovery, `tools.py`, the
  Azure client/model config, and asks the frontend to cache-bust `style.css`.
  No model call. Conversation history is kept.

At startup `app.py` enumerates all skills and appends a `## Available skills`
block (name + description) to the system prompt. The model knows what exists
but doesn't carry the full SKILL.md bodies until the user invokes one.

Skill frontmatter parsing is a 10-line shim in `skills.py`; no PyYAML dep.
It handles single-line values and folded multi-line `description:`.

## Tools

The model has three tools: `read(path)`, `write(path, content)`, `bash(command)`.

- `bash` runs with `shell=True` and a 120s timeout. No allowlist, no sandbox.
  The model can do anything the user can.
- `write` overwrites unconditionally and creates parent dirs. No diff preview.
- All tool errors are returned to the model as strings, not raised.
- The send loop iterates: call → if `tool_calls`, run them and feed results
  back, repeat until the model returns a plain text answer.
- `text_select=True` is required on `create_window` or selection is dead.

## Prompt layering

`klimt/prompt.py` assembles the system prompt in layers:

1. kernel/harness protocol from `klimt/KERNEL.md`;
2. generated tool and skill manifests;
3. global profile from `~/.klimt/AGENTS.md`;
4. project `AGENTS.md` files from the current working tree, outermost first.

The kernel is intentionally not persona-specific. Keep Claudette/Lars/tone-style
instructions in the global profile, not in the kernel. Project instructions may
specialize the global profile but must not redefine tool behavior or harness
safety boundaries.

## When adding features

- Streaming, KaTeX, mermaid, syntax highlighting, persistence, tool use — all
  on the roadmap but **not yet**. Don't preemptively scaffold for them.
- Keep CDN deps minimal; vendor only when we need offline.
