"""Tool registry and public dispatch.

Implementations live in klimt.tool_impl; this module is the facade the rest
of Klimt imports. It owns:

- The JSON schemas the model sees (`_SCHEMAS_RAW`).
- The `ToolSpec` registry that ties each schema to a dispatch adapter and
  a read-only/mutating classification.
- The single dispatcher `run(name, args, cancel, cwd)` used by the parent
  and subagent runners.

websearch supports two categories:
  - ``web`` (default): titles, URLs, and snippets.
  - ``images``: titles, direct image URLs, thumbnail URLs, and source pages.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Any, Callable, Dict

from .tool_impl import fs as _fs
from .tool_impl import shell as _shell
from .tool_impl import web as _web
from .tool_impl.limits import (
    BASH_TIMEOUT,
    GLOB_MAX_RESULTS,
    GREP_MAX_BYTES,
    GREP_MAX_LINES,
    GREP_TIMEOUT,
    READ_MAX_BYTES,
    READ_MAX_LINES,
    WEBFETCH_MAX_BYTES,
    WEBFETCH_MAX_LINKS,
    WEBFETCH_MAX_TEXT_CHARS,
    WEBFETCH_TIMEOUT,
    WEBSEARCH_MAX_IMAGE_RESULTS,
    WEBSEARCH_MAX_RESULTS,
)

# Re-exported limits. Tests and AGENTS.md docs reference these.
__all__ = [
    "BASH_TIMEOUT",
    "GLOB_MAX_RESULTS",
    "GREP_MAX_BYTES",
    "GREP_MAX_LINES",
    "GREP_TIMEOUT",
    "READ_MAX_BYTES",
    "READ_MAX_LINES",
    "WEBFETCH_MAX_BYTES",
    "WEBFETCH_MAX_LINKS",
    "WEBFETCH_MAX_TEXT_CHARS",
    "WEBFETCH_TIMEOUT",
    "WEBSEARCH_MAX_IMAGE_RESULTS",
    "WEBSEARCH_MAX_RESULTS",
    "ToolSpec",
    "SPECS",
    "SPECS_BY_NAME",
    "SCHEMAS",
    "READ_ONLY_TOOLS",
    "MUTATING_TOOLS",
    "ALL_TOOL_NAMES",
    "run",
]


ToolRunner = Callable[[Dict[str, Any], "Event | None", "str | None"], str]


@dataclass(frozen=True)
class ToolSpec:
    """One tool: name, JSON schema, dispatch target, side-effect classification.

    `run` accepts (args, cancel, cwd). Tools that ignore some of those still
    take the same signature so dispatch stays uniform.

    `read_only=True` means the tool has no observable side effects on the
    working tree, network state we own, or subagent state, and may be safely
    parallelized with other read-only tools inside a barrier group.
    """
    name: str
    description: str
    parameters: Dict[str, Any]
    run: ToolRunner
    read_only: bool

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ---------------------------------------------------------------------------
# Schemas. Schema strings (description, defaults) reference limits that live
# in tool_impl/limits.py, so this stays the single place where the model-facing
# contract is defined.
# ---------------------------------------------------------------------------

_SCHEMAS_RAW = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a UTF-8 text file from disk, with optional 1-indexed line offset and line limit. Output is capped and includes line numbers plus a continuation hint when truncated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file."},
                    "offset": {"type": "integer", "description": "Optional 1-indexed start line. Defaults to 1.", "minimum": 1},
                    "limit": {"type": "integer", "description": f"Optional maximum number of lines to return. Capped at {READ_MAX_LINES}.", "minimum": 1},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Edit a single file using exact text replacement. Every edits[].oldText must match a unique, non-overlapping region of the original file. If two changes affect the same block or nearby lines, merge them into one edit instead of emitting overlapping edits. Do not include large unchanged regions just to connect distant changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the existing file to edit."},
                    "edits": {
                        "type": "array",
                        "description": "Exact replacements to apply. Each entry is matched against the original file, not incrementally after earlier entries.",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "oldText": {"type": "string", "description": "Exact text for one targeted replacement. It must be unique in the original file and must not overlap with any other edits[].oldText in the same call."},
                                "newText": {"type": "string", "description": "Replacement text for this edit."},
                            },
                            "required": ["oldText", "newText"],
                        },
                    },
                },
                "required": ["path", "edits"],
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
    {
        "type": "function",
        "function": {
            "name": "webfetch",
            "description": "Read a URL over HTTP(S). Returns response metadata and text body.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "HTTP or HTTPS URL to fetch."},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "websearch",
            "description": "Search the web with Startpage and return compact result titles, URLs, and snippets. Use category='images' to search for images instead; image results include direct image URLs and thumbnail URLs suitable for inline display.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "category": {"type": "string", "enum": ["web", "images"], "description": "Search category. Defaults to 'web'."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": f"List files matching a shell-style glob pattern. Supports `**` for recursive matches. Returns up to {GLOB_MAX_RESULTS} paths relative to the search root, sorted by most recently modified first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern, e.g. `**/*.py` or `src/**/test_*.py`."},
                    "path": {"type": "string", "description": "Optional search root, defaults to the current working directory."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": f"Search file contents for a regex pattern using `ag` (the_silver_searcher). Returns matching lines with file path and line number. Output is bounded to {GREP_MAX_LINES} lines and {GREP_MAX_BYTES} bytes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regular expression to search for."},
                    "path": {"type": "string", "description": "Optional file or directory to search; defaults to the current working directory."},
                    "glob": {"type": "string", "description": "Optional filename glob to limit which files are searched, e.g. `*.py`."},
                    "case_insensitive": {"type": "boolean", "description": "Match case-insensitively."},
                },
                "required": ["pattern"],
            },
        },
    },
]


def _schema_by_name(name: str) -> Dict[str, Any]:
    for s in _SCHEMAS_RAW:
        if s.get("function", {}).get("name") == name:
            return s["function"]
    raise KeyError(f"no schema defined for tool {name!r}")


# ---------------------------------------------------------------------------
# Dispatch adapters. Each pulls fields out of the args dict and calls the
# typed implementation. Keeping these next to ToolSpec makes the signature
# contract obvious and avoids a big if/elif chain inside run().
# ---------------------------------------------------------------------------


def _run_read(args: Dict[str, Any], cancel: Event | None, cwd: str | None) -> str:
    return _fs.read(args["path"], args.get("offset", 1), args.get("limit"), cwd)


def _run_edit(args: Dict[str, Any], cancel: Event | None, cwd: str | None) -> str:
    return _fs.edit(args["path"], args["edits"], cwd)


def _run_write(args: Dict[str, Any], cancel: Event | None, cwd: str | None) -> str:
    return _fs.write(args["path"], args["content"], cwd)


def _run_bash(args: Dict[str, Any], cancel: Event | None, cwd: str | None) -> str:
    return _shell.bash(args["command"], cancel, cwd)


def _run_webfetch(args: Dict[str, Any], cancel: Event | None, cwd: str | None) -> str:
    return _web.webfetch(args["url"])


def _run_websearch(args: Dict[str, Any], cancel: Event | None, cwd: str | None) -> str:
    return _web.websearch(args["query"], args.get("category", "web"))


def _run_glob(args: Dict[str, Any], cancel: Event | None, cwd: str | None) -> str:
    return _fs.glob_(args["pattern"], args.get("path"), cwd)


def _run_grep(args: Dict[str, Any], cancel: Event | None, cwd: str | None) -> str:
    return _fs.grep(
        args["pattern"],
        path=args.get("path"),
        glob_filter=args.get("glob"),
        case_insensitive=bool(args.get("case_insensitive")),
        cancel=cancel,
        cwd=cwd,
    )


# ---------------------------------------------------------------------------
# Registry. Single source of truth: name, description, parameters, dispatch,
# read-only flag. SCHEMAS, READ_ONLY_TOOLS, MUTATING_TOOLS, and the
# `run()` dispatcher are derived from this tuple.
# ---------------------------------------------------------------------------

SPECS: tuple[ToolSpec, ...] = tuple(
    ToolSpec(
        name=name,
        description=str(_schema_by_name(name)["description"]),
        parameters=dict(_schema_by_name(name)["parameters"]),
        run=runner,
        read_only=read_only,
    )
    for name, runner, read_only in (
        ("read", _run_read, True),
        ("edit", _run_edit, False),
        ("write", _run_write, False),
        ("bash", _run_bash, False),
        ("webfetch", _run_webfetch, True),
        ("websearch", _run_websearch, True),
        ("glob", _run_glob, True),
        ("grep", _run_grep, True),
    )
)

SPECS_BY_NAME: dict[str, ToolSpec] = {s.name: s for s in SPECS}

SCHEMAS = [s.schema for s in SPECS]

READ_ONLY_TOOLS: frozenset[str] = frozenset(s.name for s in SPECS if s.read_only)

MUTATING_TOOLS: frozenset[str] = frozenset(s.name for s in SPECS if not s.read_only)

ALL_TOOL_NAMES: tuple[str, ...] = tuple(s.name for s in SPECS)


def run(name: str, args: Dict[str, Any], cancel: Event | None = None, cwd: str | None = None) -> str:
    spec = SPECS_BY_NAME.get(name)
    if spec is None:
        return f"error: unknown tool {name!r}"
    try:
        return spec.run(args, cancel, cwd)
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}: {e}"
