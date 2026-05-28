"""Command parsing, metadata, and execution for Klimt."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

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
    CommandSpec("/compact", "/compact [N]", "Compact older context, keeping the last N history messages raw. Default: 8."),
    CommandSpec("/cd", "/cd [path]", "Show or change the current working directory for this session."),
    CommandSpec("/model", "/model [name]", "Show or switch the model endpoint for this session. Choices come from `~/.klimt/models.json`."),
    CommandSpec("/new", "/new", "Start a completely new empty session."),
    CommandSpec("/session", "/session [name]", "Resume a saved session. Use Tab to complete names."),
    CommandSpec("/sessions", "/sessions [resume|delete|clear] ...", "List, resume, delete, or clear saved sessions for this folder."),
    CommandSpec("/name", "/name [name]", "Show or rename the current session."),
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


def help_markdown(format_session_help: Callable[[], list[str]] | None = None) -> str:
    lines = [
        "## Commands",
        "",
        command_markdown_table(),
    ]

    if format_session_help:
        lines.extend(format_session_help())

    lines.extend([
        "",
        "Keyboard shortcuts moved to `/hotkeys`.",
    ])
    return "\n".join(lines)


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
