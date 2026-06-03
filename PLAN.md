# Klimt plan

Planning notes for future work. Keep this file practical: concrete items, no
roadmap theater.

## Near term

- [x] Queue user messages submitted while a request is active.
- [ ] Add a session tree / branching model instead of only linear named sessions.
- [x] Add independent UI tabs.
  - [x] Put tabs on top and move active-tab status below the input line.
  - [x] Each tab owns its own `ChatSession`, model, history, queue, and busy state.
  - [x] Route all stream events by tab id; never rely on the currently selected tab.
  - [x] Provide visible tab controls: click tab, `+` new tab, close button.
  - [x] Add tab shortcuts: Ctrl+T, Ctrl+W, Ctrl+Tab, Ctrl+Shift+Tab, Alt+1..9; do not bind Cmd shortcuts.
  - [x] Add `/hotkeys` for keyboard shortcut documentation; keep `/help` focused on commands and link to `/hotkeys`.
  - [x] Do not add `/tab` commands in the first cut.
  - [x] Keep tab layout ephemeral in the first cut.
  - [x] Do not add cross-posting in the first cut.
- [ ] Add explicit quoted cross-post / handoff between tabs.
  - [ ] Preserve provenance and make forwarded content non-authoritative by default.
  - [ ] Prefer summaries or selected quotes over ambient tab-to-tab chat.
- [ ] Add optional per-tab instruction overlays / agent presets.
- [x] Replace model/session select boxes with tab completion and Markdown listings.
- [ ] Vendor static copies of frontend dependencies instead of loading them from CDNs.
- [x] Support Anthropic Claude Code OAuth token auth; do not support Claude web session-cookie auth.
- [ ] Add CSS theme support.
- [x] Add tab completion in the composer.
  - [x] Backend: expose one `complete(text, cursor, tab_id)` API that returns `{range, items}`; keep completion state on the client, not in chat history.
  - [x] Complete slash command names from `commands.command_rows()` and skill names from `skills.list_skills()` when the token starts with `/`.
  - [x] Complete command arguments by command context:
    - `/cd <path>`: directories only, relative to the tab cwd, with `~` expansion.
    - `/model <prefix>`: configured model names from `list_model_configs()`.
    - `/session <prefix>` and `/sessions resume|delete <prefix>`: saved session names from `ChatSession.list_sessions()`; no numeric-index completion needed.
  - [x] Complete filesystem paths in `!` shell commands and ordinary prompts.
    - Use lightweight shell-ish token detection around the cursor: respect whitespace, quotes, and backslash escapes enough for path tokens; do not try to parse full shell syntax.
    - Resolve relative candidates against the active tab cwd; expand `~`; append `/` for directories.
    - Preserve the user's quote style and escape spaces/special chars only for unquoted replacements.
  - [x] Frontend: intercept bare `Tab` in `input.js`; ask backend for candidates; if one match, replace the active token; if many, insert the longest common prefix and show a small chooser anchored near the composer.
  - [x] Repeated `Tab` cycles visible candidates; `Esc` closes the chooser; normal typing invalidates stale candidates.
  - [x] Keep completion out of busy-state semantics: it works while a tab is streaming, using that tab's cwd/session/model metadata.
  - [x] Add tests for path tokenization/replacement, cwd-relative lookup, session filtering, and slash-command context detection.

## Agent architecture

- [x] Design subagent support.
- [x] Implement subagent support (loader, agent tool, sidecar transcripts,
      parallel read-mode dispatch, /agents command, native grep/glob).

### Subagent first-cut spec

- Model-callable tool name: `agent`.
- Default/fallback agent: `read-only`.
- Built-ins: `read-only` (read-mode research) and `read-write` (full local
  tool access). No built-in reviewer/planner/security/tester cast.
- Agent definitions:
  - Load `.klimt/agents/**/*.md` and `~/.klimt/agents/**/*.md` only.
  - Project agents override user agents; built-in `read-only` / `read-write` are lowest priority.
  - Use Markdown files with frontmatter.
  - Support `name`, `description`, `tools`, `model`, `maxTurns` / `max_turns`, and `skills`.
  - `tools` is an allowlist. Ignore `disallowedTools` for now.
  - Unknown tools are ignored with a warning; if all listed tools are unknown, effective tools are `none`.
- Prompt inheritance:
  - Include Klimt kernel / harness rules.
  - Include only the allowed tool manifest.
  - Include project `AGENTS.md` files.
  - Do not include global `~/.klimt/AGENTS.md`.
  - Do not include parent chat history.
  - Include explicit task/context from the parent.
  - Include the lightweight skill manifest, analogous to the parent prompt.
- Skills:
  - No dedicated skill tool.
  - Subagents with read-capable tools can load `SKILL.md` files via `read`.
  - Agent-file `skills:` prioritizes/advertises those skills; it does not preload full skill bodies.
- Tool modes:
  - `none`: no tools.
  - `read`: `read`, `grep`, `glob`, `webfetch`, `websearch`.
  - `full`: `read`, `grep`, `glob`, `edit`, `write`, `bash`, `webfetch`, `websearch`.
  - Default mode is `read`.
  - Subagents never get `agent`; no nested delegation in the first cut.
- Add native read-only tools:
  - `glob`: Python implementation, bounded results.
  - `grep`: `rg` wrapper via `subprocess` without shell, bounded output, clear error if `rg` is missing.
- Models:
  - `model: inherit` or omitted uses the parent session model.
  - Other model values must exactly match configured model names in `~/.klimt/models.json`.
  - No built-in model families or aliases for now.
- Max turns:
  - Configurable per agent with `maxTurns` / `max_turns`.
  - Default is `3`.
- Parallelism:
  - Parallelize read-only tool calls in barrier groups: `read`, `grep`, `glob`, `webfetch`, `websearch`, and `agent` with `none` / `read` tools.
  - Side-effecting calls remain sequential: `edit`, `write`, `bash`, and `agent` with `full` tools.
  - Preserve original tool-call result order in history.
- UI/events:
  - Add `tool_start` so parallel tools and subagents show pending boxes immediately.
  - Subagents are not normal tabs in the first cut.
- Transcripts:
  - Persist subagent transcripts as sidecar files.
  - Parent session stores metadata/reference only.
  - No subagent resume support in the first cut.
  - Transcripts should be inspectable after app restart when the parent session is resumed.
- Cancellation:
  - Parent interrupt cancels active subagents.
  - Track and close active subagent streams on interrupt.
- Result format:
  - Return metadata-wrapped Markdown as the `agent` tool result: status, model, tools, transcript reference, result, and notes.
- Parent-visible catalog:
  - Include a lightweight available-agent catalog in the parent prompt, analogous to skills.
  - Add `/agents` to list available agents.

## UI

- [x] Links (and similar activations) must never navigate the Klimt window itself; open externally or not at all.
- [x] Handle output wider than the window without a horizontal scrollbar. Lean toward forced wrapping; figure out how to do it sanely for code blocks and tool output.
- [ ] Allow hiding reasoning blocks (via hotkey and potentially config).

## Context management

- [ ] Add automatic context compaction threshold.
- [ ] Expose compaction settings in the UI or config if needed.

## Packaging

- [ ] Add proper Python packaging so users do not need to run from a checkout.
