import subprocess
from pathlib import Path


class GitBranchClient:
    def __init__(self, repo_path: str, remote_name: str = "origin"):
        self.repo_path = Path(repo_path)
        self.remote_name = remote_name

    def has_remote(self) -> bool:
        completed = self._run_capture("git", "remote")
        return self.remote_name in completed.stdout.split()

    def fetch(self) -> None:
        self._run("git", "fetch", self.remote_name)

    def resolve(self, ref: str) -> str | None:
        """The commit SHA ``ref`` points at, or None when it does not exist."""
        completed = self._run_capture("git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if completed.returncode != 0:
            return None
        return completed.stdout.strip() or None

    def add_worktree(self, path: Path, ref: str) -> None:
        """A detached worktree at ``ref``. Pruning first clears a registration whose directory is gone."""
        self._run("git", "worktree", "prune")
        self._run("git", "worktree", "add", "--detach", str(path), ref)

    def remove_worktree(self, path: Path) -> None:
        self._run("git", "worktree", "remove", "--force", str(path))

    def prune_worktrees(self) -> None:
        self._run("git", "worktree", "prune")

    def create_branch(self, name: str) -> None:
        """Point ``name`` at the current commit and switch to it, keeping the working tree."""
        self._run("git", "checkout", "-B", name)

    def delete_branch(self, name: str) -> bool:
        return self._run_capture("git", "branch", "-D", name).returncode == 0

    def has_changes(self) -> bool:
        completed = self._run_capture("git", "status", "--porcelain")
        return bool(completed.stdout.strip())

    def commit_all(self, message: str) -> None:
        self._run("git", "add", "-A")
        self._run("git", "commit", "-m", message)

    def push_branch(self, source_branch: str) -> None:
        # A run always starts from the target branch, so a branch left on the remote by an
        # earlier run of the same issue has diverged and is overwritten.
        self._run("git", "push", "--force", "-u", self.remote_name, source_branch)

    def diff(self, *, target_branch: str | None = None) -> str:
        if target_branch:
            completed = self._run_capture("git", "diff", target_branch)
        else:
            completed = self._run_capture("git", "diff", "HEAD")
        if completed.returncode != 0:
            raise RuntimeError(f"git diff failed: {completed.stderr}")
        return completed.stdout

    def ahead_count(self, base: str) -> int:
        """Commits on the checked-out branch that ``base`` does not have."""
        completed = self._run_capture("git", "rev-list", "--count", f"{base}..HEAD")
        count = completed.stdout.strip()
        if completed.returncode != 0 or not count.isdigit():
            raise RuntimeError(f"Unexpected rev-list output for {base}..HEAD: {completed.stdout}{completed.stderr}")
        return int(count)

    def _run(self, *args: str) -> None:
        completed = self._run_capture(*args)
        if completed.returncode != 0:
            raise RuntimeError(
                f"Command failed: {' '.join(args)}\nstdout: {completed.stdout}\nstderr: {completed.stderr}"
            )

    def _run_capture(self, *args: str) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            args,
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        return completed
