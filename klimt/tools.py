"""Tool implementations exposed to the model.

Three tools: read, write, bash. Each returns a string. Errors are returned
as strings (not raised) so the model can see them and recover.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict

BASH_TIMEOUT = 120  # seconds

SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read the contents of a file from disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Write text to a file, overwriting any existing content. Parent directories are created.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": f"Execute a bash command. Returns stdout, stderr, and exit code. Timeout {BASH_TIMEOUT}s.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    },
]


def _read(path: str) -> str:
    return Path(path).expanduser().read_text(encoding="utf-8")


def _write(path: str, content: str) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {p}"


def _bash(command: str) -> str:
    r = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=BASH_TIMEOUT,
    )
    return (
        f"exit={r.returncode}\n"
        f"--- stdout ---\n{r.stdout}"
        f"--- stderr ---\n{r.stderr}"
    )


def run(name: str, args: Dict[str, Any]) -> str:
    try:
        if name == "read":
            return _read(args["path"])
        if name == "write":
            return _write(args["path"], args["content"])
        if name == "bash":
            return _bash(args["command"])
        return f"error: unknown tool {name!r}"
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}: {e}"
