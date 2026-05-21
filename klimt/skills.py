"""Skill discovery and loading from ~/.klimt/skills/**/SKILL.md."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

SKILLS_DIR = Path.home() / ".klimt" / "skills"
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
_KEY_RE = re.compile(r"^[A-Za-z_][\w-]*:")


def _parse_frontmatter(text: str) -> Dict[str, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    meta: Dict[str, str] = {}
    current: Optional[str] = None
    for line in m.group(1).splitlines():
        if _KEY_RE.match(line):
            k, _, v = line.partition(":")
            current = k.strip()
            v = v.strip()
            if v in ("|", ">"):
                v = ""
            elif len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            meta[current] = v
        elif current and line.strip():
            meta[current] = (meta[current] + " " + line.strip()).strip()
    return meta


def list_skills() -> List[Dict[str, str]]:
    if not SKILLS_DIR.exists():
        return []
    out = []
    for p in sorted(SKILLS_DIR.rglob("SKILL.md")):
        meta = _parse_frontmatter(p.read_text(encoding="utf-8"))
        out.append({
            "name": meta.get("name") or p.parent.name,
            "description": meta.get("description", ""),
            "path": str(p),
        })
    return out


def find_skill(name: str) -> Optional[Path]:
    if not SKILLS_DIR.exists():
        return None
    # Prefer directory name match (cheap), fall back to frontmatter name.
    for p in SKILLS_DIR.rglob("SKILL.md"):
        if p.parent.name == name:
            return p
    for p in SKILLS_DIR.rglob("SKILL.md"):
        if _parse_frontmatter(p.read_text(encoding="utf-8")).get("name") == name:
            return p
    return None
