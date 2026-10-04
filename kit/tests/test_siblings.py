"""The program's other repos, cloned next to the repo.

Each clone next to the main clone whose origin has the same host and owner
mounts read-only. One that writable_siblings names mounts writable, with its
hooks, config and the files that lead git to them read-only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from harness import Container, Kit, read_only


@pytest.fixture
def this(kit: Kit) -> Path:
    """A clone among clones and plain folders, with worktrees in both usual places."""
    src = kit.src
    repo = kit.clone(src / "this", "git@github.com:Program-Org/this.git")
    kit.git("-C", repo, "worktree", "add", "-q", repo / ".worktrees/wt")
    kit.git("-C", repo, "worktree", "add", "-q", repo / ".claude/worktrees/agent")
    for name, origin in (
        ("ssh-sibling", "git@github.com:program-org/ssh-sibling.git"),
        ("https-sibling", "https://github.com/Program-Org/https-sibling"),
        ("url-sibling", "ssh://git@github.com/Program-Org/url-sibling.git"),
        ("other-client", "git@github.com:other-client/app.git"),
        ("other-host", "https://gitlab.com/Program-Org/app.git"),
        ("no-origin", None),
        ("no-origin-sibling", None),
        ("rw-other-client", "git@github.com:other-client/rw-other-client.git"),
    ):
        kit.clone(src / name, origin, commit=False)
    rw = kit.clone(src / "rw-sibling", "git@github.com:Program-Org/rw-sibling.git")
    kit.git("-C", rw, "worktree", "add", "-q", rw / ".worktrees/wt")
    for hook in (rw / ".git/hooks").iterdir():
        hook.unlink()
    (rw / ".git/hooks").rmdir()
    (src / "partner-folder").mkdir()
    return repo


def writable(kit: Kit, where: Path, nested_clones: list[str] | None = None) -> None:
    """A writable sibling still needs this repo's origin owner."""
    kit.settings(
        where, writable_siblings=["rw-sibling", "rw-other-client"], nested_clones=nested_clones
    )


def sibling_mounts(kit: Kit, container: Container, repo: Path) -> list[tuple[str, bool]]:
    """Each clone next to repo that mounts, by name, and whether read-only."""
    return sorted(
        (Path(m.target).name, m.read_only)
        for m in container.mounts
        if Path(m.target).parent == kit.src and m.target != str(repo)
    )


@pytest.mark.parametrize("where", [".", ".worktrees/wt", ".claude/worktrees/agent"])
def test_only_clones_with_this_origins_owner_mount(kit: Kit, this: Path, where: str) -> None:
    # A repo commits docker/kit.toml, so each worktree has it. Here it goes
    # where the run starts.
    writable(kit, this / where)
    run = kit.run(this / where)
    assert run.returncode == 0, run.stderr
    assert sibling_mounts(kit, run.container, this) == [
        ("https-sibling", True),
        ("rw-sibling", False),
        ("ssh-sibling", True),
        ("url-sibling", True),
    ]
    rw = kit.src / "rw-sibling"
    assert read_only(run.container, kit, under=[rw]) == [
        "rw-sibling/.git/commondir",
        "rw-sibling/.git/config",
        "rw-sibling/.git/hooks",
        "rw-sibling/.git/worktrees/wt/commondir",
        "rw-sibling/.worktrees/wt/.git",
    ]
    assert "docker/cc: mounted read-only: https-sibling ssh-sibling url-sibling" in run.lines()
    assert "docker/cc: mounted writable: rw-sibling" in run.lines()


def test_without_settings_each_sibling_mounts_read_only(kit: Kit, this: Path) -> None:
    run = kit.run(this)
    assert run.returncode == 0, run.stderr
    assert sibling_mounts(kit, run.container, this) == [
        ("https-sibling", True),
        ("rw-sibling", True),
        ("ssh-sibling", True),
        ("url-sibling", True),
    ]
    assert "mounted writable" not in run.stderr


def test_writable_sibling_without_hooks_gets_them_made(kit: Kit, this: Path) -> None:
    """So the read-only mount has a source."""
    writable(kit, this)
    kit.run(this)
    assert (kit.src / "rw-sibling/.git/hooks").is_dir()


def test_pointer_search_covers_each_writable_sibling(kit: Kit, this: Path) -> None:
    nested = kit.clone(kit.src / "rw-sibling/nested")
    writable(kit, this)
    run = kit.run(this)
    assert run.returncode != 0
    assert f"  {nested / '.git'}" in run.lines()
    assert not run.started

    # nested_clones names a clone in a writable sibling by its absolute path.
    writable(kit, this, nested_clones=[str(nested)])
    run = kit.run(this)
    assert run.returncode == 0, run.stderr


@pytest.mark.parametrize("origin", ["/srv/git/no-origin.git", None])
def test_clone_whose_origin_names_no_owner_mounts_no_sibling(
    kit: Kit, this: Path, origin: str | None
) -> None:
    """The siblings with no origin would otherwise match it."""
    repo = kit.src / "no-origin"
    if origin:
        kit.git("-C", repo, "remote", "add", "origin", origin)
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert [m for m in run.container.mounts if Path(m.target).parent == kit.src] == []
    assert "mounted read-only" not in run.stderr
