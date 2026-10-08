"""What exactly produced a run (R-39).

A run's manifest is taken once, when the run starts, and stored in its state: the versions of
this service, the Pi SDK and the runner, the model each stage was configured with, and the
version and hash of every prompt and rules file. Two runs with the same manifest were produced
by the same system; when results differ, the manifests say what changed between them.

Model ids are recorded as configured. Pi resolves them from the catalogue bundled with the
pinned SDK, so the SDK version is what fixes their meaning (R-41).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
from functools import cache
from importlib import metadata
from pathlib import Path

from . import prompts
from .config import Settings

_ROOT = Path(__file__).resolve().parents[2]
_PI_PACKAGE = "@earendil-works/pi-coding-agent"
_RUNNER_FILES = ("node/pi-sdk-runner.mjs", "node/review-mode.mjs")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


@cache
def _agent_version() -> str:
    try:
        return metadata.version("pi-jira-agent")
    except metadata.PackageNotFoundError:
        return "unknown"


@cache
def _agent_git_sha() -> str:
    """The commit this service was built from: AGENT_GIT_SHA in an image, git in a checkout."""
    baked = os.environ.get("AGENT_GIT_SHA", "").strip()
    if baked and baked != "unknown":
        return baked
    try:
        done = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_ROOT, capture_output=True, text=True, check=False, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else "unknown"


@cache
def _pi_sdk_version() -> str:
    """The installed SDK's version, else the version package.json pins."""
    installed = _ROOT / "node_modules" / _PI_PACKAGE / "package.json"
    for path, read in (
        (installed, lambda data: data.get("version")),
        (_ROOT / "package.json", lambda data: (data.get("dependencies") or {}).get(_PI_PACKAGE)),
    ):
        try:
            version = read(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if version:
            return str(version)
    return "unknown"


@cache
def _runner_sha() -> str:
    """One hash over the runner's files: its prompts are built in code, so this versions them."""
    digest = hashlib.sha256()
    for rel in _RUNNER_FILES:
        try:
            digest.update((_ROOT / rel).read_bytes().replace(b"\r\n", b"\n"))
        except OSError:
            return "unknown"
    return digest.hexdigest()[:12]


def build(settings: Settings, *, pr_review: bool) -> dict:
    """The manifest of a run that starts now with these settings."""
    stages = {}
    for stage in ("requirements", "planning", "coding", "review", "pr_review"):
        cfg = settings.stage_model(stage)  # type: ignore[arg-type]
        stages[stage] = {"provider": cfg.provider, "model": cfg.model}
    for stage in ("planning", "coding"):
        stages[stage]["thinking_level"] = settings.pi_thinking_level
    stages["pr_review"]["thinking_level"] = settings.pr_review_thinking_level or settings.pi_thinking_level
    stages["pr_review"]["enabled"] = pr_review
    return {
        "agent_version": _agent_version(),
        "agent_git_sha": _agent_git_sha(),
        "pi_sdk_version": _pi_sdk_version(),
        "runner_sha": _runner_sha(),
        "stages": stages,
        "prompts": {
            **prompts.catalogue(),
            "pi_system": {"sha": _sha(settings.pi_system_prompt)},
        },
        "rules": {
            "review": _sha(settings.review_rules()),
            "pr_review": _sha(settings.pr_review_rules()),
        },
        # Packs (R-30) do not exist yet; every run uses the built-in behaviour.
        "pack": "builtin",
        "created_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
