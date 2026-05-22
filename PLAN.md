# Klimt plan

Planning notes for future work. Keep this file practical: concrete items, no
roadmap theater.

## Near term

- [x] Queue user messages submitted while a request is active.
- [ ] Add a session tree / branching model instead of only linear named sessions.
- [ ] Add independent UI tabs.
  - [ ] Put tabs on top and move active-tab status below the input line.
  - [ ] Each tab owns its own `ChatSession`, model, history, queue, and busy state.
  - [ ] Route all stream events by tab id; never rely on the currently selected tab.
  - [ ] Provide visible tab controls: click tab, `+` new tab, close button.
  - [ ] Add tab shortcuts: Ctrl+T, Ctrl+W, Ctrl+Tab, Ctrl+Shift+Tab, Alt+1..9; do not bind Cmd shortcuts.
  - [ ] Add `/hotkeys` for keyboard shortcut documentation; keep `/help` focused on commands and link to `/hotkeys`.
  - [ ] Do not add `/tab` commands in the first cut.
  - [ ] Keep tab layout ephemeral in the first cut.
  - [ ] Do not add cross-posting in the first cut.
- [ ] Add explicit quoted cross-post / handoff between tabs.
  - [ ] Preserve provenance and make forwarded content non-authoritative by default.
  - [ ] Prefer summaries or selected quotes over ambient tab-to-tab chat.
- [ ] Add optional per-tab instruction overlays / agent presets.
- [x] Show model/session select boxes only when `/model` or `/session` is invoked.
- [ ] Vendor static copies of frontend dependencies instead of loading them from CDNs.
- [ ] Support Anthropic web authentication flow.

## Agent architecture

- [ ] Design subagent support.
- [ ] Define how subagents inherit or receive prompt layers.
- [ ] Decide whether subagents get separate tool permissions or only separate
      task context.

## Context management

- [ ] Add automatic context compaction threshold.
- [ ] Expose compaction settings in the UI or config if needed.

## Packaging

- [ ] Add proper Python packaging so users do not need to run from a checkout.
