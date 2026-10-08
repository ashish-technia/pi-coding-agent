"""One git worktree per run and repository.

The configured clone of each repository (the *base clone*) is only ever fetched. A run
works in ``<RUNS_ROOT>/<issue key>/<repo name>``, a detached worktree created from the
target branch, so two issues on the same repository never see each other's edits and
ending a run is a matter of removing its directory.

Worktree paths are derived, never stored: a checkpoint written on the host stays valid
in a container where RUNS_ROOT points somewhere else.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from .git_client import GitBranchClient
from .models import RepoConfig

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


class RunWorkspaces:
    def __init__(self, repo_map: dict[str, RepoConfig], *, runs_root: str, remote_name: str = "origin"):
        self.repo_map = repo_map
        self.runs_root = Path(runs_root).expanduser().resolve()
        self.remote_name = remote_name

    # ------------------------------------------------------------------ paths
    def run_dir(self, issue_key: str) -> Path:
        # Issue keys arrive from the API and from webhooks, so they never become a path as given.
        name = _UNSAFE.sub("_", issue_key.strip())
        if not name.strip("."):
            raise ValueError(f"Invalid issue key for a run directory: {issue_key!r}")
        return self.runs_root / name

    def path(self, issue_key: str, repo_name: str) -> Path:
        return self.run_dir(issue_key) / repo_name

    def snapshot_ref(self, issue_key: str, phase_index: int) -> str:
        """The ref that keeps a phase's latest tree snapshot alive until the run ends."""
        return f"{self._ref_prefix(issue_key)}phase-{phase_index}"

    def _ref_prefix(self, issue_key: str) -> str:
        return f"refs/pi-jira/{self.run_dir(issue_key).name}/"

    def exists(self, issue_key: str, repo_name: str) -> bool:
        return (self.path(issue_key, repo_name) / ".git").exists()

    def git(self, issue_key: str, repo_name: str) -> GitBranchClient:
        """The git client for a run's worktree.

        A missing worktree is an error, never recreated empty: it held the run's
        uncommitted work.
        """
        path = self.path(issue_key, repo_name)
        if not self.exists(issue_key, repo_name):
            raise RuntimeError(
                f"The worktree for {issue_key} in repo {repo_name!r} is missing at {path}. "
                "Its uncommitted work is gone, so the run cannot continue; start the issue again."
            )
        return GitBranchClient(repo_path=str(path), remote_name=self.remote_name)

    def roots_payload(self, issue_key: str, repos: list[RepoConfig]) -> list[dict]:
        """The `repoRoots` block handed to the Pi runner.

        Pi resolves absolute paths as given, so listing the worktrees is all it takes to
        let one agent session read and edit several checkouts.
        """
        return [
            {"name": r.name, "path": str(self.git(issue_key, r.name).repo_path), "properties": r.properties}
            for r in repos
            if r.path.strip()
        ]

    def cwd(self, issue_key: str, repos: list[RepoConfig]) -> str:
        """The primary repo's worktree: the Pi session's working directory."""
        primary = next((r for r in repos if r.path.strip()), None)
        return str(self.git(issue_key, primary.name).repo_path) if primary else "."

    # ------------------------------------------------------------------ lifecycle
    def ensure(self, issue_key: str, repo: RepoConfig) -> tuple[str, str | None]:
        """Create the run's worktree for ``repo`` if it is not there.

        Returns the commit it sits on, and a warning when the remote could not be fetched
        and the worktree therefore starts from the last commit that was.
        """
        if self.exists(issue_key, repo.name):
            head = self.git(issue_key, repo.name).resolve("HEAD")
            if head:
                return head, None

        base = self._base(repo)
        if not base.repo_path.exists():
            raise RuntimeError(f"Repository path does not exist: {base.repo_path}")
        if not (base.repo_path / ".git").exists():
            raise RuntimeError(f"Repository path is not a git repository: {base.repo_path}")

        warning = None
        if base.has_remote():
            try:
                base.fetch()
            except RuntimeError as exc:
                # An expired token or no network should not stop a run from reading and
                # editing code. It will matter again at the push, which says so itself.
                reason = str(exc).strip().splitlines()[-1][:200]
                warning = (
                    f"Could not fetch {repo.name} from {self.remote_name}, so this run starts from the last "
                    f"commit that was fetched and may be behind the remote. ({reason})"
                )
                logger.warning("%s [%s]", warning, issue_key)
        # A clone without the remote branch (or without a remote at all) still gets a run.
        candidates = [f"{self.remote_name}/{repo.target_branch}", repo.target_branch, "HEAD"]
        ref, sha = None, None
        for candidate in candidates:
            sha = base.resolve(candidate) if candidate.strip("/") else None
            if sha:
                ref = candidate
                break
        if not ref or not sha:
            raise RuntimeError(f"Repo {repo.name!r} has no commit to start from (tried {', '.join(candidates)}).")
        if ref != candidates[0]:
            logger.warning("Repo %s has no %s; starting %s from %s instead.", repo.name, candidates[0], issue_key, ref)

        target = self.path(issue_key, repo.name)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)  # a leftover that is not a worktree
        target.parent.mkdir(parents=True, exist_ok=True)
        base.add_worktree(target, sha)
        logger.info("Worktree for %s in repo %s at %s (%s @ %s)", issue_key, repo.name, target, ref, sha[:10])
        return sha, warning

    # ------------------------------------------------------------------ standalone reviews
    def branch_head(self, repo: RepoConfig, branch: str, *, fetch: bool = True) -> str | None:
        """The commit ``branch`` points at in ``repo`` (the remote's copy first), or None."""
        if not repo.path.strip() or not (Path(repo.path) / ".git").exists():
            return None
        base = self._base(repo)
        if fetch and base.has_remote():
            try:
                base.fetch()
            except RuntimeError as exc:  # offline: review what was last fetched
                logger.warning("Could not fetch %s before a review: %s", repo.name, exc)
        return base.resolve(f"{self.remote_name}/{branch}") or base.resolve(f"refs/heads/{branch}")

    def checkout_branch(self, key: str, repo: RepoConfig, branch: str) -> dict[str, str]:
        """A detached worktree of ``repo`` at ``branch``, for a review.

        Returns ``{"head": ..., "base": ...}``: the branch's commit, and the commit it last
        shared with the repository's target branch. The review covers base to head, which is
        what a pull request of the branch would show.
        """
        base = self._base(repo)
        head = self.branch_head(repo, branch, fetch=False)
        if not head:
            raise RuntimeError(f"Branch {branch!r} does not exist in repo {repo.name!r}.")
        target = base.resolve(f"{self.remote_name}/{repo.target_branch}") or base.resolve(repo.target_branch)
        fork = base.merge_base(target, head) if target else None
        if not fork:
            raise RuntimeError(
                f"Branch {branch!r} of repo {repo.name!r} shares no history with {repo.target_branch!r}, "
                "so there is no change to review."
            )
        path = self.path(key, repo.name)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        base.add_worktree(path, head)
        logger.info("Review worktree %s in repo %s at %s (%s..%s)", key, repo.name, path, fork[:10], head[:10])
        return {"head": head, "base": fork}

    def remove(self, issue_key: str, *, branch: str | None = None) -> None:
        """Remove every worktree of a run and its local run branch. Safe when nothing is there."""
        run_dir = self.run_dir(issue_key)
        for repo in self.repo_map.values():
            if not repo.path.strip() or not (Path(repo.path) / ".git").exists():
                continue
            base = self._base(repo)
            target = run_dir / repo.name
            try:
                if target.exists():
                    try:
                        base.remove_worktree(target)
                    except RuntimeError:
                        shutil.rmtree(target, ignore_errors=True)
                    logger.info("Removed worktree for %s in repo %s", issue_key, repo.name)
                base.prune_worktrees()
                base.delete_refs(self._ref_prefix(issue_key))
                if branch:
                    base.delete_branch(branch)
            except Exception:  # noqa: BLE001
                logger.exception("Could not clean up the worktree for %s in repo %s", issue_key, repo.name)
        shutil.rmtree(run_dir, ignore_errors=True)

    def _base(self, repo: RepoConfig) -> GitBranchClient:
        return GitBranchClient(repo_path=repo.path, remote_name=self.remote_name)
