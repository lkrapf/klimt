"""Size and timeout limits for tool implementations.

Centralized so schemas and impls share the same numbers.
"""
from __future__ import annotations

BASH_TIMEOUT = 120  # seconds
WEBFETCH_TIMEOUT = 30  # seconds
WEBFETCH_MAX_BYTES = 2_000_000
WEBSEARCH_MAX_RESULTS = 5
WEBSEARCH_MAX_IMAGE_RESULTS = 15
WEBFETCH_MAX_TEXT_CHARS = 200_000
WEBFETCH_MAX_LINKS = 40
READ_MAX_LINES = 2000
READ_MAX_BYTES = 50_000
GLOB_MAX_RESULTS = 500
GREP_TIMEOUT = 30  # seconds
GREP_MAX_LINES = 500
GREP_MAX_BYTES = 200_000
# Cap on the raw image bytes a single `visual` call will accept. Anthropic's
# per-image limit is 5 MB; we leave headroom for the base64 expansion in the
# outgoing payload (4/3x).
VISUAL_MAX_BYTES = 3_750_000
