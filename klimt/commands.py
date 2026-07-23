"""Command parsing, metadata, and execution for Klimt."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from . import skills, tools

BusyPolicy = Literal["allow", "block", "background"]


@dataclass(frozen=True)
class CommandSpec:
    name: str
    usage: str
    description: str
    busy: BusyPolicy = "block"
    docs: str = ""


SPECS: tuple[CommandSpec, ...] = (
    CommandSpec("!", "!<cmd>", "Run a shell command directly and show the result as a tool box.", "background"),
    CommandSpec("/help", "/help", "Show command help.", "allow"),
    CommandSpec("/hotkeys", "/hotkeys", "Show keyboard shortcuts.", "allow"),
    CommandSpec("/skills", "/skills", "List available skills with short descriptions.", "allow"),
    CommandSpec("/agents", "/agents", "List available subagents (built-in, user, and project).", "allow"),
    CommandSpec("/back", "/back", "Go back to an earlier turn in the conversation. Presents a list to choose from."),
    CommandSpec("/goal", "/goal [turns=N] [condition|clear]", "Keep working until a condition is met. No arg shows status; `clear` stops. Optional `turns=N` caps the loop (default 20).", "allow"),
    CommandSpec("/compact", "/compact [N]", "Compact older context, keeping the last N history messages raw. Default: 8."),
    CommandSpec("/cd", "/cd [path]", "Show or change the current working directory for this session."),
    CommandSpec("/model", "/model [name]", "Show or switch the model endpoint for this session. Choices come from `~/.klimt/models.json`."),
    CommandSpec("/theme", "/theme [name]", "Show or switch the UI CSS theme. Use Tab to complete names."),
    CommandSpec("/new", "/new", "Start a completely new empty session."),
    CommandSpec("/session", "/session [name]", "Resume a saved session. Use Tab to complete names."),
    CommandSpec("/sessions", "/sessions", "List saved sessions for this folder and pick one to resume or delete."),
    CommandSpec("/save", "/save [name]", "Save this session to disk, optionally under a new name. New sessions are not stored until saved or resumed."),
    CommandSpec("/reload", "/reload", "Reload prompt layers, skills, tools, model endpoint, and CSS."),
    CommandSpec("/quit", "/quit", "Close Klimt."),
    CommandSpec("/<skill>", "/<skill>", "Load `~/.klimt/skills/<skill>/SKILL.md` into the conversation."),
)


def _slash_name(text: str) -> str:
    return text.split(None, 1)[0]


def classify(text: str) -> CommandSpec | None:
    command = text.strip()
    if not command:
        return None
    if command.startswith("!"):
        return SPECS[0]
    if not command.startswith("/"):
        return None

    head = _slash_name(command)
    for spec in SPECS:
        if spec.name == head:
            return spec
    if skills.find_skill(command[1:].split(None, 1)[0]):
        return next(s for s in SPECS if s.name == "/<skill>")
    return next(s for s in SPECS if s.name == "/<skill>")


def command_rows() -> list[tuple[str, str]]:
    return [(spec.usage, spec.description) for spec in SPECS]


def _table_cell(text: str) -> str:
    return text.replace("|", "\\|")


def command_markdown_table() -> str:
    lines = ["| command | description |", "|---|---|"]
    for usage, description in command_rows():
        lines.append(f"| `{_table_cell(usage)}` | {_table_cell(description)} |")
    return "\n".join(lines)


def command_bullets() -> str:
    return "\n".join(f"- `{usage}` — {description}" for usage, description in command_rows())


def help_markdown() -> str:
    return "\n".join([
        "## Commands",
        "",
        command_markdown_table(),
    ])


def hotkeys_markdown() -> str:
    return "\n".join([
        "## Hotkeys",
        "",
        "| key | action |",
        "|---|---|",
        "| `Enter` | Send. |",
        "| `Shift+Enter` | Insert newline. |",
        "| `Esc` | Interrupt current tab's work. |",
        "| `Ctrl+T` | New tab. |",
        "| `Ctrl+W` | Close current tab. |",
        "| `Ctrl+Tab` | Next tab. |",
        "| `Ctrl+Shift+Tab` | Previous tab. |",
        "| `Alt+1` ... `Alt+9` | Switch to tab 1 ... 9. |",
        "| `Ctrl+R` | Toggle reasoning visibility. |",
        "| `Ctrl+J` / `Ctrl+K` | Scroll current transcript. |",
        "",
        "Cmd shortcuts are deliberately unbound.",
    ])


def run_shell(session: Any, command: str) -> list[dict[str, Any]]:
    if not command:
        return []
    # A bang command is a fresh user action. Clear any stale cancel state left
    # by a prior interrupt; otherwise _bash sees a set event and aborts before
    # it starts, returning exit=interrupted with no output.
    session._cancel.clear()
    result = tools.run("bash", {"command": command}, session._cancel, session.cwd)
    if not session._cancel.is_set():
        session.history.append({"role": "user", "content": f"$ {command}\n{result}"})
    events: list[dict[str, Any]] = [{
        "type": "tool",
        "name": "bash",
        "args": {"command": command},
        "result": result,
    }]
    if session._cancel.is_set():
        events.append({"type": "error", "message": "interrupted"})
    return events


def load_skill(session: Any, name: str) -> list[dict[str, Any]]:
    if not name:
        return [{"type": "text", "content": "_usage: `/<skill-name>`_"}]
    path = skills.find_skill(name)
    if not path:
        return [{"type": "text", "content": f"_unknown skill: `{name}`_"}]
    body = path.read_text(encoding="utf-8")
    session.history.append({"role": "user", "content": f"[Skill loaded: {name}]\n\n{body}"})
    return [{"type": "text", "content": f"loaded skill **{name}** (`{path}`)"}]
