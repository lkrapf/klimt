"""Shared lightweight type aliases."""
from __future__ import annotations

from typing import Any, Callable, Dict

Event = Dict[str, Any]
Emit = Callable[[Event], None]
