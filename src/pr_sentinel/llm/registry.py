"""Prompt registry.

Prompts are versioned files on disk, and the bundle hash of everything loaded is
stamped onto every review. When a finding is disputed three weeks later, the
question "what exactly did we ask the model" has an answer.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_VERSION = re.compile(r"<!--\s*version:\s*([^\s>]+)\s*-->")


@lru_cache(maxsize=32)
def load_prompt(name: str) -> tuple[str, str]:
    """Return (text, version) for a prompt file, base prompt prepended."""
    path = PROMPT_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"no prompt named {name!r} in {PROMPT_DIR}")
    raw = path.read_text()
    match = _VERSION.search(raw)
    version = match.group(1) if match else "0"
    body = _VERSION.sub("", raw).strip()

    if name.startswith("_"):
        return body, version

    base_body, base_version = load_prompt("_base")
    return f"{base_body}\n\n---\n\n{body}", f"{name}@{version}+base@{base_version}"


@lru_cache(maxsize=1)
def bundle_version() -> str:
    """A short hash over every prompt file. Changes when any prompt changes."""
    h = hashlib.sha256()
    for path in sorted(PROMPT_DIR.glob("*.md")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


def reset_cache() -> None:
    load_prompt.cache_clear()
    bundle_version.cache_clear()
