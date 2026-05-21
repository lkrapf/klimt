"""Streaming wrapper around Azure OpenAI Chat Completions, with tool calling."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from openai import AzureOpenAI

from . import skills, tools

Event = Dict[str, Any]
Emit = Callable[[Event], None]


def _make_client() -> AzureOpenAI:
    return AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_BASE_URL"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
    )


@dataclass
class ChatSession:
    # For Azure, "model" is the *deployment name*.
    model: str
    system: str
    max_tokens: int = 4096
    history: List[Dict] = field(default_factory=list)
    _client: AzureOpenAI = field(default_factory=_make_client, repr=False)

    def reset(self) -> None:
        self.history.clear()

    def stream(self, user_text: str, emit: Emit) -> None:
        """Push events for one user turn.

        Event shapes:
          {type: 'text_start'}
          {type: 'text_delta', content: str}
          {type: 'text_end'}
          {type: 'text', content: str}       atomic markdown message
          {type: 'tool', name, args, result}
        """
        text = user_text.strip()
        if text.startswith("!"):
            for e in self._run_shell(text[1:].strip()):
                emit(e)
            return
        if text.startswith("/"):
            for e in self._run_skill(text[1:].strip()):
                emit(e)
            return

        self.history.append({"role": "user", "content": user_text})

        while True:
            stream = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": self.system}, *self.history],
                tools=tools.SCHEMAS,
                max_completion_tokens=self.max_tokens,
                stream=True,
            )

            content_buf: List[str] = []
            tool_calls: Dict[int, Dict[str, str]] = {}
            text_open = False

            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue

                if delta.content:
                    if not text_open:
                        emit({"type": "text_start"})
                        text_open = True
                    emit({"type": "text_delta", "content": delta.content})
                    content_buf.append(delta.content)

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        slot = tool_calls.setdefault(
                            tc.index, {"id": "", "name": "", "args": ""}
                        )
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                slot["name"] = tc.function.name
                            if tc.function.arguments:
                                slot["args"] += tc.function.arguments

            if text_open:
                emit({"type": "text_end"})

            full_text = "".join(content_buf)
            assistant_entry: Dict[str, Any] = {
                "role": "assistant",
                "content": full_text or None,
            }
            if tool_calls:
                assistant_entry["tool_calls"] = [
                    {
                        "id": v["id"],
                        "type": "function",
                        "function": {"name": v["name"], "arguments": v["args"]},
                    }
                    for _, v in sorted(tool_calls.items())
                ]
            self.history.append(assistant_entry)

            if not tool_calls:
                return

            for _, v in sorted(tool_calls.items()):
                try:
                    args = json.loads(v["args"] or "{}")
                except json.JSONDecodeError:
                    args = {"_raw": v["args"]}
                result = tools.run(v["name"], args)
                emit({
                    "type": "tool",
                    "name": v["name"],
                    "args": args,
                    "result": result,
                })
                self.history.append({
                    "role": "tool",
                    "tool_call_id": v["id"],
                    "content": result,
                })

    # ---- prefix handlers -------------------------------------------------

    def _run_shell(self, command: str) -> List[Event]:
        if not command:
            return []
        result = tools.run("bash", {"command": command})
        self.history.append(
            {"role": "user", "content": f"$ {command}\n{result}"}
        )
        return [{
            "type": "tool",
            "name": "bash",
            "args": {"command": command},
            "result": result,
        }]

    def _run_skill(self, name: str) -> List[Event]:
        if not name:
            return [{"type": "text", "content": "_usage: `/<skill-name>`_"}]
        path = skills.find_skill(name)
        if not path:
            return [{"type": "text", "content": f"_unknown skill: `{name}`_"}]
        body = path.read_text(encoding="utf-8")
        self.history.append(
            {"role": "user", "content": f"[Skill loaded: {name}]\n\n{body}"}
        )
        return [{"type": "text", "content": f"loaded skill **{name}** (`{path}`)"}]
