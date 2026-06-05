"""`visual` tool + provider message rewriting."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from klimt import tools
from klimt.providers import _anthropic_messages, _chat_completions_sanitize_messages
from klimt.tool_impl import visual as visual_impl
from klimt.tool_impl.limits import VISUAL_MAX_BYTES


_PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c63000100000005000100020df1cf000000000049454e44"
    "ae426082"
)
_JPEG_HEADER = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
_GIF_HEADER = b"GIF89a"
_WEBP_HEADER = b"RIFF\x00\x00\x00\x00WEBP"


def _write_png(path: Path) -> Path:
    path.write_bytes(_PNG_1x1)
    return path


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------


def test_visual_round_trips_png(tmp_path: Path) -> None:
    p = _write_png(tmp_path / "shot.png")
    out = tools.run("visual", {"path": str(p)})
    env = json.loads(out)
    assert env["_klimt_image"] is True
    assert env["media_type"] == "image/png"
    assert env["bytes"] == len(_PNG_1x1)
    assert env["path"].endswith("shot.png")
    assert base64.b64decode(env["data"]) == _PNG_1x1


def test_visual_sniffs_jpeg(tmp_path: Path) -> None:
    p = tmp_path / "photo.bin"  # extension is wrong on purpose
    p.write_bytes(_JPEG_HEADER + b"\x00" * 32)
    env = json.loads(tools.run("visual", {"path": str(p)}))
    assert env["media_type"] == "image/jpeg"


def test_visual_sniffs_gif(tmp_path: Path) -> None:
    p = tmp_path / "anim.gif"
    p.write_bytes(_GIF_HEADER + b"\x00" * 16)
    env = json.loads(tools.run("visual", {"path": str(p)}))
    assert env["media_type"] == "image/gif"


def test_visual_sniffs_webp(tmp_path: Path) -> None:
    p = tmp_path / "pic.webp"
    p.write_bytes(_WEBP_HEADER + b"\x00" * 16)
    env = json.loads(tools.run("visual", {"path": str(p)}))
    assert env["media_type"] == "image/webp"


def test_visual_rejects_non_image(tmp_path: Path) -> None:
    p = tmp_path / "notes.txt"
    p.write_text("hello world\n")
    out = tools.run("visual", {"path": str(p)})
    assert out.startswith("error: unsupported image format")


def test_visual_rejects_oversized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Shrink the cap so we don't have to write multi-MB blobs.
    monkeypatch.setattr(visual_impl, "VISUAL_MAX_BYTES", 64)
    p = _write_png(tmp_path / "big.png")
    out = tools.run("visual", {"path": str(p)})
    assert out.startswith("error: image too large")


def test_visual_rejects_missing(tmp_path: Path) -> None:
    out = tools.run("visual", {"path": str(tmp_path / "missing.png")})
    assert out.startswith("error: path does not exist")


def test_visual_with_note(tmp_path: Path) -> None:
    p = _write_png(tmp_path / "shot.png")
    env = json.loads(tools.run("visual", {"path": str(p), "note": "login screen"}))
    assert env["note"] == "login screen"


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------


def test_parse_envelope_accepts_dict() -> None:
    payload = {"_klimt_image": True, "media_type": "image/png", "data": ""}
    assert visual_impl.parse_envelope(payload) is payload


def test_parse_envelope_rejects_unrelated_json() -> None:
    assert visual_impl.parse_envelope('{"hello": "world"}') is None


def test_parse_envelope_rejects_plain_text() -> None:
    assert visual_impl.parse_envelope("just a tool result") is None


# ---------------------------------------------------------------------------
# Provider rewriting
# ---------------------------------------------------------------------------


def _envelope() -> str:
    return json.dumps({
        "_klimt_image": True,
        "media_type": "image/png",
        "data": base64.b64encode(_PNG_1x1).decode("ascii"),
        "path": "/tmp/shot.png",
        "bytes": len(_PNG_1x1),
    })


def test_chat_completions_unpacks_visual_tool_result() -> None:
    history = [
        {"role": "user", "content": "look at this"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "visual", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": _envelope()},
    ]

    out = _chat_completions_sanitize_messages("openai", history)

    # user, assistant, tool (text placeholder), user (image)
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "user"]

    tool_msg = out[2]
    assert tool_msg["tool_call_id"] == "call_1"
    assert "image attached" in tool_msg["content"]

    image_msg = out[3]
    assert isinstance(image_msg["content"], list)
    types = [block["type"] for block in image_msg["content"]]
    assert types == ["text", "image_url"]
    assert image_msg["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_chat_completions_leaves_normal_tool_results_alone() -> None:
    history = [
        {"role": "tool", "tool_call_id": "x", "content": "plain text output"},
    ]
    out = _chat_completions_sanitize_messages("openai", history)
    assert out == history


def test_anthropic_emits_image_block_in_tool_result() -> None:
    history = [
        {"role": "user", "content": "look at this"},
        {"role": "assistant", "content": "sure", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "visual", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": _envelope()},
    ]

    out = _anthropic_messages(history)

    # find the tool_result block
    blocks = []
    for msg in out:
        if msg["role"] == "user":
            blocks.extend(msg["content"])
    tool_results = [b for b in blocks if b.get("type") == "tool_result"]
    assert len(tool_results) == 1
    inner = tool_results[0]["content"]
    assert isinstance(inner, list)
    assert {b["type"] for b in inner} == {"text", "image"}
    image_block = next(b for b in inner if b["type"] == "image")
    assert image_block["source"]["media_type"] == "image/png"
    assert image_block["source"]["type"] == "base64"


def test_visual_schema_is_read_only_and_present() -> None:
    spec = tools.SPECS_BY_NAME["visual"]
    assert spec.read_only is True
    assert spec.schema["function"]["name"] == "visual"
    props = spec.schema["function"]["parameters"]["properties"]
    assert "path" in props
    assert "note" in props


def test_visual_cap_is_sane() -> None:
    # If somebody bumps it past Anthropic's 5 MB per-image limit, surface it.
    assert VISUAL_MAX_BYTES <= 5_000_000


# ---------------------------------------------------------------------------
# Schema gating by model vision flag
# ---------------------------------------------------------------------------


def test_session_factory_omits_visual_from_manifest_for_text_only_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """System-prompt manifest should not list `visual` for non-vision models."""
    from klimt import session_factory
    from klimt.model_config import ModelConfig

    fake = ModelConfig(name="fake", provider="openai", model="gpt-fake", vision=False)
    monkeypatch.setattr(session_factory, "resolve_model_config", lambda _name: fake)
    assert not session_factory._vision_for("fake")
    prompt = session_factory.build_system_prompt(model="fake")
    # The manifest section must not list `visual` as an available tool.
    import re
    tool_line = re.search(r"- `visual`", prompt)
    assert tool_line is None, "visual should not appear in the manifest for non-vision models"
    # But the no-vision note should be present.
    assert "not vision-capable" in prompt


def test_session_factory_includes_visual_in_manifest_for_vision_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """System-prompt manifest should list `visual` for vision-capable models."""
    from klimt import session_factory
    from klimt.model_config import ModelConfig

    fake = ModelConfig(name="fake-v", provider="openai", model="gpt-fake", vision=True)
    monkeypatch.setattr(session_factory, "resolve_model_config", lambda _name: fake)
    assert session_factory._vision_for("fake-v")
    prompt = session_factory.build_system_prompt(model="fake-v")
    import re
    assert re.search(r"- `visual`", prompt), "visual should appear in the manifest for vision models"
    assert "not vision-capable" not in prompt
