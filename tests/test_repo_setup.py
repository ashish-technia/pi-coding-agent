"""Repository resolution and the Docker clone step.

These exercise real git against local bare repositories - no network, no Bitbucket.
"""

import subprocess
from pathlib import Path

import pytest

from pi_jira_agent import repo_setup
from pi_jira_agent.config import settings
from pi_jira_agent.models import RepoConfig


def _bare_repo(tmp_path: Path, name: str, branch: str = "develop") -> str:
    """A bare repo with one commit on `branch`, usable as a clone_url."""
    work = tmp_path / f"{name}-work"
    work.mkdir()
    run = lambda *a: subprocess.run(a, cwd=work, check=True, capture_output=True)  # noqa: E731
    run("git", "init", "--initial-branch", branch)
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (work / "README.md").write_text("hello", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-m", "init")

    bare = tmp_path / f"{name}.git"
    subprocess.run(["git", "clone", "--bare", str(work), str(bare)], check=True, capture_output=True)
    return str(bare)


def test_repos_root_rebases_every_path(monkeypatch):
    """In a container the host paths in repos.json are replaced by <root>/<name>."""
    monkeypatch.setattr(settings, "repos_root", "/workspace")
    by_name = {r.name: r for r in settings.repos()}
    assert Path(by_name["web"].path) == Path("/workspace/web").resolve()
    assert Path(by_name["api"].path) == Path("/workspace/api").resolve()


def test_without_repos_root_the_configured_paths_win():
    paths = {r.name: r.path for r in settings.repos()}
    assert paths["web"].endswith("web") and "workspace" not in paths["web"]


def test_clone_url_is_derived_from_workspace_and_slug(monkeypatch):
    monkeypatch.setattr(settings, "bitbucket_workspace", "acme")
    monkeypatch.setattr(settings, "bitbucket_host", "bitbucket.org")
    assert settings.default_clone_url("web-repo") == "https://bitbucket.org/acme/web-repo.git"
    assert settings.default_clone_url("") == "", "no slug, no URL"


def test_explicit_clone_url_is_kept(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "repos_config_path", str(tmp_path / "missing.json"))
    monkeypatch.setattr(settings, "bitbucket_repo_slug", "solo")
    monkeypatch.setattr(settings, "bitbucket_workspace", "acme")
    (only,) = settings.repos()
    assert only.clone_url == "https://bitbucket.org/acme/solo.git"


def test_ensure_repo_clones_the_target_branch(tmp_path):
    url = _bare_repo(tmp_path, "svc", branch="develop")
    dest = tmp_path / "clones" / "svc"
    repo = RepoConfig(name="svc", path=str(dest), clone_url=url, target_branch="develop")

    outcome, detail = repo_setup.ensure_repo(repo)
    assert outcome == "cloned", detail
    assert (dest / ".git").exists() and (dest / "README.md").exists()
    assert repo_setup.describe(dest) == ("develop", 0)

    # Second call is a no-op: an existing clone is never re-cloned or reset.
    assert repo_setup.ensure_repo(repo)[0] == "present"


def test_ensure_repo_falls_back_when_the_branch_is_missing(tmp_path):
    url = _bare_repo(tmp_path, "svc2", branch="main")
    dest = tmp_path / "svc2"
    repo = RepoConfig(name="svc2", path=str(dest), clone_url=url, target_branch="nonexistent")

    outcome, detail = repo_setup.ensure_repo(repo)
    assert outcome == "cloned", detail
    assert repo_setup.describe(dest)[0] == "main"


def test_ensure_repo_reports_a_bad_url_without_raising(tmp_path):
    repo = RepoConfig(
        name="ghost", path=str(tmp_path / "ghost"), clone_url=str(tmp_path / "does-not-exist.git")
    )
    outcome, detail = repo_setup.ensure_repo(repo)
    assert outcome == "failed" and "git clone" in detail


def test_ensure_repo_needs_a_url(tmp_path):
    repo = RepoConfig(name="ghost", path=str(tmp_path / "ghost"))
    outcome, detail = repo_setup.ensure_repo(repo)
    assert outcome == "failed" and "clone_url" in detail


def test_ensure_repo_refuses_a_non_empty_non_repo_directory(tmp_path):
    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "stuff.txt").write_text("x", encoding="utf-8")
    repo = RepoConfig(name="occupied", path=str(dest), clone_url="https://example.invalid/x.git")
    outcome, detail = repo_setup.ensure_repo(repo)
    assert outcome == "failed" and "not a git repository" in detail


def test_ensure_repo_skips_a_repo_with_no_path():
    assert repo_setup.ensure_repo(RepoConfig(name="x"))[0] == "skipped"


@pytest.mark.parametrize("outcome", ["cloned", "present"])
def test_main_never_blocks_startup(monkeypatch, outcome):
    """A broken repository must not stop the service from booting."""
    monkeypatch.setattr(repo_setup, "ensure_all", lambda: [(RepoConfig(name="a", path=""), "failed", "boom")])
    assert repo_setup.main() == 0
