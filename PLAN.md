# Klimt plan

Planning notes for future work. Keep this file practical: concrete items, no
roadmap theater.

## Near term

- [ ] Queue user messages submitted while a request is active.
- [ ] Add a session tree / branching model instead of only linear named sessions.
- [ ] Add multi-tab or parallel invocation support.
- [ ] Replace free-text model/session selection with HTML select boxes.

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
- [ ] Document Linux `pywebview` system dependencies once tested.
