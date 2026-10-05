"""Make sure every configured repository exists on disk, cloning what is missing.

The Docker entrypoint runs this once before the API starts, so a container only needs
`repos.json` and git credentials - not a bind mount per repository. On a host you
normally have the clones already and never call this.

Failures are reported but not fatal: the service still starts, the Settings page and
the log show which repository is missing, and the run that needs it fails loudly with
a specific error rather than the whole stack refusing to boot.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .config import settings
from .models import RepoConfig

logger = logging.getLogger(__name__)

# Full clones: the agents branch off the target branch and diff against it, so the
# history has to be there. A shallow clone would break the per-run worktrees.
_CLONE_TIMEOUT_SECONDS = 900


def _run(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=_CLONE_TIMEOUT_SECONDS)


def is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def ensure_repo(repo: RepoConfig) -> tuple[str, str]:
    """Return (outcome, detail) for one repository.

    Outcomes: ``present``, ``cloned``, ``skipped`` (nothing to do), ``failed``.
    """
    if not repo.path.strip():
        return "skipped", "no local path configured"

    path = Path(repo.path)
    if is_git_repo(path):
        return "present", str(path)

    if path.exists() and any(path.iterdir()):
        return "failed", f"{path} already exists and is not a git repository"

    if not repo.clone_url:
        return "failed", (
            f"{path} does not exist and no clone_url is set for {repo.name!r}; "
            "add one to repos.json or set BITBUCKET_WORKSPACE so it can be derived"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    # Try the configured branch first so the clone lands where the agents expect;
    # a repo whose default branch differs is still usable, each run fetches first.
    attempts: list[list[str]] = []
    if repo.target_branch.strip():
        attempts.append(["git", "clone", "--branch", repo.target_branch, repo.clone_url, str(path)])
    attempts.append(["git", "clone", repo.clone_url, str(path)])

    last = ""
    for args in attempts:
        completed = _run(args)
        if completed.returncode == 0:
            return "cloned", f"{repo.clone_url} -> {path}"
        lines = (completed.stderr or completed.stdout).strip().splitlines()
        last = lines[-1].strip() if lines else f"exit code {completed.returncode}"
        # A missing branch is worth retrying without it; anything else fails again.
        if "not found in upstream" not in last.lower() and "remote branch" not in last.lower():
            break
    return "failed", f"git clone {repo.clone_url} failed: {last}"


def describe(path: Path) -> tuple[str, int]:
    """(branch, uncommitted file count) for a clone, for the startup log."""
    branch = _run(["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"])
    status = _run(["git", "-C", str(path), "status", "--porcelain"])
    name = branch.stdout.strip() if branch.returncode == 0 else "unknown"
    dirty = len([line for line in status.stdout.splitlines() if line.strip()]) if status.returncode == 0 else 0
    return name, dirty


def ensure_all() -> list[tuple[RepoConfig, str, str]]:
    results = []
    for repo in settings.repos():
        try:
            outcome, detail = ensure_repo(repo)
        except subprocess.TimeoutExpired:
            outcome, detail = "failed", f"git clone timed out after {_CLONE_TIMEOUT_SECONDS}s"
        except OSError as exc:  # unreadable path, permission problem, git missing
            outcome, detail = "failed", str(exc)
        results.append((repo, outcome, detail))
    return results


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[repo-setup] %(message)s")
    failures = 0
    for repo, outcome, detail in ensure_all():
        if outcome == "failed":
            failures += 1
            logger.error("%s: %s", repo.name, detail)
            continue
        if outcome == "skipped":
            logger.info("%s: %s", repo.name, detail)
            continue

        branch, dirty = describe(Path(repo.path))
        verb = "cloned" if outcome == "cloned" else "ready"
        logger.info("%s: %s at %s (branch: %s, uncommitted: %d)", repo.name, verb, repo.path, branch, dirty)
        if dirty > 200:
            # Same trap the single-repo entrypoint check warns about.
            logger.warning(
                "%s: %d files look modified, which usually means a line-ending mismatch "
                "with the host checkout. Try GIT_AUTOCRLF=true (Windows host) or false.",
                repo.name,
                dirty,
            )
    if failures:
        logger.error("%d repository/repositories unavailable; runs that need them will fail", failures)
    return 0  # never block startup


if __name__ == "__main__":
    raise SystemExit(main())
