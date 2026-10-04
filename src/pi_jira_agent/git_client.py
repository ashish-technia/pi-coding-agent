import subprocess
from pathlib import Path


class GitBranchClient:
    def __init__(self, repo_path: str, remote_name: str = "origin"):
        self.repo_path = Path(repo_path)
        self.remote_name = remote_name

    def prepare_branch(self, *, target_branch: str, source_branch: str, push: bool = True) -> None:
        if not self.repo_path.exists():
            raise RuntimeError(f"Repository path does not exist: {self.repo_path}")
        if not (self.repo_path / ".git").exists():
            raise RuntimeError(f"Repository path is not a git repository: {self.repo_path}")

        self._run("git", "fetch", self.remote_name)
        self._run("git", "checkout", target_branch)
        self._run("git", "pull", "--ff-only", self.remote_name, target_branch)
        self._run("git", "checkout", "-B", source_branch)
        if push:
            self._run("git", "push", "-u", self.remote_name, source_branch)

    def has_changes(self) -> bool:
        completed = self._run_capture("git", "status", "--porcelain")
        return bool(completed.stdout.strip())

    def commit_all(self, message: str) -> None:
        self._run("git", "add", "-A")
        self._run("git", "commit", "-m", message)

    def push_branch(self, source_branch: str) -> None:
        self._run("git", "push", "-u", self.remote_name, source_branch)

    def diff(self, *, target_branch: str | None = None) -> str:
        if target_branch:
            completed = self._run_capture("git", "diff", target_branch)
        else:
            completed = self._run_capture("git", "diff", "HEAD")
        if completed.returncode != 0:
            raise RuntimeError(f"git diff failed: {completed.stderr}")
        return completed.stdout

    def ahead_count(self, *, target_branch: str, source_branch: str) -> int:
        completed = self._run_capture(
            "git",
            "rev-list",
            "--left-right",
            "--count",
            f"{target_branch}...{source_branch}",
        )
        parts = completed.stdout.strip().split()
        if len(parts) != 2:
            raise RuntimeError(
                f"Unexpected rev-list output for {source_branch} vs {target_branch}: {completed.stdout}"
            )
        return int(parts[1])

    def _run(self, *args: str) -> None:
        completed = self._run_capture(*args)
        if completed.returncode != 0:
            raise RuntimeError(
                f"Command failed: {' '.join(args)}\n"
                f"stdout: {completed.stdout}\n"
                f"stderr: {completed.stderr}"
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
