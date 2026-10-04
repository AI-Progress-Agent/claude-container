"""The read-only mounts of the files that lead git to hooks and config.

Each worktree's .git file and the commondir in its git directory tell git
where the hooks and config are. They mount read-only, so the container cannot
point git on the Mac at a config of its own. The same holds for every
worktree of the clone, and for each submodule's git directory.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from harness import (
    MAIN_FOLDERS,
    MAIN_GUARDED,
    Kit,
    folder_mounts,
    ignores_case,
    read_only,
)

# A file or folder the launcher cannot make on the Mac stops the start. Root
# ignores the permission, so these skip under root.

needs_non_root = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission")


def assert_refused_to_make(stderr: str, path: Path) -> None:
    assert "did not start" in stderr
    assert f"\n  {path}\n" in stderr
    assert "read-only disk" in stderr
    # No bash error leaks into the refusal.
    assert " line " not in stderr


@pytest.mark.parametrize("where", [".", ".claude/worktrees/agent"])
def test_every_worktrees_pointer_files_mount_read_only(kit: Kit, where: str) -> None:
    repo = kit.main_clone()
    run = kit.run(repo / where)
    assert run.returncode == 0, run.stderr
    assert read_only(run.container, kit, under=[repo]) == MAIN_GUARDED


def test_outside_git_nothing_mounts(kit: Kit) -> None:
    repo = kit.src / "My.App"
    repo.mkdir()
    run = kit.run(repo)
    assert read_only(run.container, kit, under=[repo]) == []
    assert folder_mounts(run.container, kit) == []


def test_main_clones_commondir_names_itself(kit: Kit) -> None:
    """Git reads ../.git as no commondir at all. libgit2 cannot open a repo whose commondir is "."."""
    repo = kit.main_clone()
    kit.run(repo)
    assert (repo / ".git/commondir").read_text() == "../.git\n"
    common = kit.git("-C", repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    assert common == str(repo / ".git")


def test_through_a_link_each_file_mounts_at_both_paths(kit: Kit) -> None:
    """The container writes each file by the link's path, and git names it by its real path."""
    repo = kit.main_clone()
    link = kit.src / "Link"
    link.symlink_to(repo)
    run = kit.run(link)
    assert run.returncode == 0, run.stderr
    files = [p.removeprefix("Main-Clone/") for p in MAIN_GUARDED]
    for file in files:
        assert run.container.has_mount(repo / file, ro=True), file
        assert run.container.has_mount(repo / file, link / file, ro=True), file


def test_worktree_config_files_mount_read_only(kit: Kit) -> None:
    """With extensions.worktreeConfig on, git also reads each git directory's config.worktree."""
    repo = kit.main_clone()
    kit.git("-C", repo, "config", "extensions.worktreeConfig", "true")
    run = kit.run(repo)
    assert read_only(run.container, kit, under=[repo]) == sorted([
        *MAIN_GUARDED,
        "Main-Clone/.git/config.worktree",
        "Main-Clone/.git/worktrees/agent/config.worktree",
        "Main-Clone/.git/worktrees/other/config.worktree",
    ])  # fmt: skip
    # A missing one is made empty first, so the container cannot make it.
    assert (repo / ".git/worktrees/other/config.worktree").read_text() == ""


def test_submodule_keeps_working_with_its_commondir(kit: Kit) -> None:
    repo = kit.main_clone()
    kit.run(repo)
    assert (repo / ".git/modules/lib/commondir").read_text() == "../lib\n"
    kit.git("-C", repo / "lib", "status", "--short")


def test_submodules_worktree_commondir_mounts_read_only(kit: Kit) -> None:
    repo = kit.main_clone()
    kit.git("-C", repo / "lib", "worktree", "add", "-q", kit.work / "lib-wt")
    run = kit.run(repo)
    file = repo / ".git/modules/lib/worktrees/lib-wt/commondir"
    assert run.container.has_mount(file, ro=True)


@needs_non_root
def test_a_commondir_it_cannot_write_stops_the_start(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    (repo / ".git").chmod(0o555)
    try:
        run = kit.run(repo)
    finally:
        (repo / ".git").chmod(0o755)
    assert run.returncode != 0
    assert_refused_to_make(run.stderr, repo / ".git/commondir")
    assert not run.started


@needs_non_root
def test_a_hooks_folder_it_cannot_make_stops_the_start(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    dep = kit.clone(repo / "vendor/dep")
    (dep / ".git/commondir").write_text("../.git\n")
    shutil.rmtree(dep / ".git/hooks")
    kit.settings(repo, nested_clones=["vendor/dep"])
    (dep / ".git").chmod(0o555)
    try:
        run = kit.run(repo)
    finally:
        (dep / ".git").chmod(0o755)
    assert run.returncode != 0
    assert_refused_to_make(run.stderr, dep / ".git/hooks")
    assert not run.started


# Each folder above a read-only mount inside a writable mount mounts on its
# own, writable. Then the container cannot rename it and move the mount away.


def test_folders_above_each_read_only_mount_mount_on_their_own(kit: Kit) -> None:
    """The repo's own .git is already a mount point, so it needs nothing new."""
    repo = kit.main_clone()
    run = kit.run(repo)
    assert folder_mounts(run.container, kit) == MAIN_FOLDERS


def test_a_folder_a_compose_file_mounts_gets_no_second_mount(kit: Kit) -> None:
    repo = kit.main_clone()
    kit.compose_binds(repo / ".worktrees")
    run = kit.run(repo)
    assert folder_mounts(run.container, kit) == [
        f for f in MAIN_FOLDERS if f != "Main-Clone/.worktrees"
    ]


def test_from_a_worktree_only_the_main_clones_git_holds_folders(kit: Kit) -> None:
    repo = kit.main_clone()
    run = kit.run(repo / ".claude/worktrees/agent")
    assert folder_mounts(run.container, kit) == [
        "Main-Clone/.git/modules",
        "Main-Clone/.git/modules/lib",
        "Main-Clone/.git/worktrees",
        "Main-Clone/.git/worktrees/agent",
        "Main-Clone/.git/worktrees/other",
    ]


def test_through_a_link_folders_mount_at_the_links_path(kit: Kit) -> None:
    repo = kit.main_clone()
    link = kit.src / "Link"
    link.symlink_to(repo)
    run = kit.run(link)
    assert folder_mounts(run.container, kit) == [
        f.replace("Main-Clone/", "Link/") for f in MAIN_FOLDERS
    ]


def test_writable_siblings_git_holds_folders(kit: Kit) -> None:
    repo = kit.main_clone(origin="git@github.com:program-org/main-clone.git")
    kit.clone(kit.src / "Sib", "git@github.com:program-org/sib.git")
    kit.settings(repo, writable_siblings=["Sib"])
    run = kit.run(repo)
    assert folder_mounts(run.container, kit) == [*MAIN_FOLDERS, "Sib", "Sib/.git"]


def test_a_start_by_a_path_in_other_case_mounts_at_that_path(kit: Kit) -> None:
    """Only a disk that ignores case, such as the Mac's, can check this."""
    if not ignores_case(kit.src):
        pytest.skip("this disk does not ignore case")
    repo = kit.main_clone()
    lower = kit.src / "main-clone"
    run = kit.run(lower)
    assert run.returncode == 0, run.stderr
    assert run.container.has_mount(repo / ".git/commondir", lower / ".git/commondir", ro=True)
    assert run.container.has_mount(lower / ".git/worktrees", ro=False)
