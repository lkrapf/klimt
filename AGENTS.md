# Klimt — agent notes

Project-local guidance for AI assistants working on this repo.

## Philosophy

- **Lean code.** Add features only when needed. No speculative fallbacks, no
  options nobody asked for. Delete code that isn't earning its keep.
- **No compatibility theater.** Do not keep old APIs, env vars, UI hooks, or
  fallback paths unless there is a current caller or an explicit requirement.
- Small, readable modules over clever abstractions.
- Pushback over politeness. If a request is wrong-headed, say so.

## Stack

- Python 3.10+ (developed against 3.14).
- `pywebview` for the native window (WebKit on macOS).
- `openai` SDK with OpenAI-compatible chat-completions clients.
- Frontend: vanilla HTML/JS, `marked` + `DOMPurify` + KaTeX + mermaid from CDNs.

## Model providers

- Config is read from `~/.klimt/models.json` by `model_config.py`.
- Supported providers: `azure`, `openai`, `ollama`, `anthropic`.
- Do not add inline keys or provider-specific auth env fallbacks. API-key based
  providers use `api_key_env`; Anthropic may omit it to use native OAuth PKCE.
- No Azure env-only fallback; endpoint config must be in `~/.klimt/models.json`.
- The value sent as `model` is provider-specific. For Azure it is the deployment
  name, not the public model name.
- We use `max_completion_tokens`, not `max_tokens`.
- We do not send `temperature` or `top_p`.

## Known model gotchas

- Some reasoning deployments reject `role: "system"`. If we hit this, replace
  with `role: "developer"` or drop the system message deliberately; don't add a
  broad fallback until there is a concrete failing provider.
- Anthropic is currently used through its OpenAI-compatible endpoint. Native-only
  Anthropic features are not exposed.

## Streaming

Python streams via the provider adapter and pushes events to JS by calling
`window.klimt.handleEvent(...)` through pywebview's `window.evaluate_js`.

Important event types:

- `reasoning_start` / `reasoning_delta` / `reasoning_end` — streamed reasoning.
- `reasoning` — restored reasoning during session replay.
- `text_start` / `text_delta` / `text_end` — streamed assistant text.
- `text` — atomic Markdown message, e.g. command output.
- `tool` — atomic tool box with name, args, result.
- `error` — error string surfaced to the UI.
- `done` — command/request finished.

During streaming the JS renders Markdown only, with an auto-close ``` heuristic
for unterminated code fences. KaTeX and mermaid run once at `text_end` because
both choke on partial input and mermaid re-renders are expensive. Renders are
rAF-throttled to one per frame.

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
  commands.py      # slash/bang command metadata and helpers
  tools.py         # tools + JSON schemas
  skills.py        # ~/.klimt/skills discovery
  session_store.py # per-folder session persistence
  web/             # frontend
```

- Python owns conversation history.
- JS calls `window.pywebview.api.send(text)`.
- Python pushes UI events via `window.klimt.handleEvent(...)`.

## Input prefixes

Handled before any model call:

- `!cmd` — runs `cmd` via the bash tool. Output is shown in the UI and appended
  to history as a user message (`$ cmd\n<output>`). No model call.
- `/help` — shows command help.
- `/skills` — lists discovered skills.
- `/compact [N]` — compacts older context, keeping the last N messages raw.
- `/model [name]` — shows or switches the model endpoint.
- `/new` — starts a new empty session.
- `/sessions ...` — lists/resumes/deletes/clears sessions for this folder.
- `/save [name]` — saves the session to disk, optionally under a new name.
- `/reload` — reloads prompt layers, skills, tools, model config, and CSS.
- `/quit` — exits immediately.
- `/<skill>` — loads `~/.klimt/skills/<skill>/SKILL.md` into history.

## Skills

At startup `prompt.py` enumerates all `~/.klimt/skills/**/SKILL.md` files and
adds a runtime skill manifest to the system prompt. The model sees skill names,
descriptions, and paths, but does not carry full skill bodies until a matching
skill is loaded.

Skill frontmatter parsing is a small shim in `skills.py`; no PyYAML dependency.
It handles single-line values and folded multi-line `description:`.

## Tools

The model has six tools:

- `read(path, offset?, limit?)`
- `edit(path, edits)`
- `write(path, content)`
- `bash(command)`
- `webfetch(url)`
- `websearch(query, category='web'|'images')`

Notes:

- `bash` runs with `shell=True` and a 120s timeout. No allowlist, no sandbox.
  The model can do anything the user can.
- `edit` requires exact, unique, non-overlapping replacements.
- `write` overwrites unconditionally and creates parent directories.
- Tool errors are returned to the model as strings, not raised.
- The send loop iterates: model call → tool calls → tool results → next model
  call, until the model returns a final text answer.
- `text_select=True` is required on `create_window` or selection is dead.

## Prompt layering

`klimt/prompt.py` assembles the system prompt in physical order:

1. kernel/harness protocol from `klimt/KERNEL.md`;
2. generated tool and skill manifests;
3. global profile from `~/.klimt/AGENTS.md`;
4. project `AGENTS.md` files from the current working tree, outermost first.

The kernel is intentionally not persona-specific. Keep Claudette/Lars/tone-style
instructions in the global profile, not in the kernel. Project instructions may
specialize the global profile but must not redefine tool behavior or harness
safety boundaries.

## Planning

Use `PLAN.md` for roadmap/planning notes. Keep `README.md` focused on user setup
and operation, and this file focused on maintainer guidance for agents.
