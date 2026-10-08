"""Prompts as versioned template files (R-40).

Each system prompt of a stage that calls a model directly lives in ``prompts/<name>.md``. Its
first line is ``<!-- version: N -->``; bump N whenever the text changes in a way that should be
visible in a run's manifest. The manifest also records a hash of the text, so an edit that
forgot to bump the version still shows up as a different prompt.

The Pi runner builds its prompts in ``node/pi-sdk-runner.mjs`` and ``node/review-mode.mjs``;
those are versioned as a whole by the hash of the two files (see ``manifest.py``).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent
_HEADER = re.compile(r"^<!--\s*version:\s*(\d+)\s*-->\s*\n")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    text: str
    sha: str  # first 12 hex digits of the text's SHA-256


@cache
def get(name: str) -> Prompt:
    raw = (_DIR / f"{name}.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    header = _HEADER.match(raw)
    if not header:
        raise ValueError(f"Prompt file {name}.md must start with '<!-- version: N -->'.")
    text = raw[header.end() :].strip()
    return Prompt(
        name=name, version=int(header.group(1)), text=text, sha=hashlib.sha256(text.encode()).hexdigest()[:12]
    )


def catalogue() -> dict[str, dict]:
    """Every prompt file with its version and hash, for the run manifest."""
    return {
        path.stem: {"version": get(path.stem).version, "sha": get(path.stem).sha} for path in sorted(_DIR.glob("*.md"))
    }
