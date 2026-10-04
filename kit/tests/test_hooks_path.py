"""The folder that core.hooksPath names mounts read-only in each worktree.

Git on the Mac runs hooks from there, so a hook written inside would run on
the Mac. Only a folder inside a writable mount needs the mount.
"""

from __future__ import annotations

import pytest
from harness import MAIN_GUARDED, Kit, folder_mounts, hooks_mounts, ignores_case, read_only


def test_hooks_folder_mounts_in_each_worktree(kit: Kit) -> None:
    repo = kit.main_clone()
    (repo / ".husky/_").mkdir(parents=True)
    kit.git("-C", repo, "config", "core.hooksPath", ".husky/_")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert hooks_mounts(run.container, kit, repo) == [
        "Main-Clone/.claude/worktrees/agent/.husky/_",
        "Main-Clone/.husky/_",
        "Main-Clone/.worktrees/other/.husky/_",
    ]
    # A missing one is made empty first.
    other = repo / ".worktrees/other/.husky/_"
    assert other.is_dir()
    assert list(other.iterdir()) == []
    # The folders above it mount on their own, as for every read-only mount.
    assert "Main-Clone/.husky" in folder_mounts(run.container, kit)


def test_git_hooks_folder_needs_nothing_new(kit: Kit) -> None:
    repo = kit.main_clone()
    kit.git("-C", repo, "config", "core.hooksPath", ".git/hooks")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert read_only(run.container, kit, under=[repo]) == MAIN_GUARDED


def test_folder_outside_every_writable_mount_needs_nothing(kit: Kit) -> None:
    repo = kit.main_clone()
    outside = kit.work / "outside-hooks"
    kit.git("-C", repo, "config", "core.hooksPath", outside)
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert read_only(run.container, kit, under=[repo]) == MAIN_GUARDED
    assert not outside.exists()


def test_global_value_is_followed_in_each_submodules_worktree_too(kit: Kit) -> None:
    repo = kit.main_clone()
    kit.git("config", "--global", "core.hooksPath", ".githooks")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert hooks_mounts(run.container, kit, repo) == [
        "Main-Clone/.claude/worktrees/agent/.githooks",
        "Main-Clone/.githooks",
        "Main-Clone/.worktrees/other/.githooks",
        "Main-Clone/lib/.githooks",
    ]


def test_tilde_names_one_folder_that_mounts_once(kit: Kit) -> None:
    repo = kit.main_clone()
    kit.git("config", "--global", "core.hooksPath", "~/src/Main-Clone/.tilde-hooks")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert hooks_mounts(run.container, kit, repo) == ["Main-Clone/.tilde-hooks"]


def test_bare_repo_resolves_its_value_in_its_git_folder(kit: Kit) -> None:
    repo = kit.main_clone()
    fixture = kit.clone(repo / "fixture", bare=True)
    kit.git("-C", fixture, "config", "core.hooksPath", "fixture-hooks")
    kit.settings(repo, nested_clones=["fixture"])
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    # The bare repo is its own git folder, so its hooks and config mount too.
    assert read_only(run.container, kit, under=[fixture]) == [
        "Main-Clone/fixture/commondir",
        "Main-Clone/fixture/config",
        "Main-Clone/fixture/fixture-hooks",
        "Main-Clone/fixture/hooks",
    ]


def test_writable_sibling_and_nested_clone_mount_theirs(kit: Kit) -> None:
    repo = kit.main_clone(origin="git@github.com:program-org/main-clone.git")
    sib = kit.clone(kit.src / "Sib", "git@github.com:program-org/sib.git")
    kit.git("-C", sib, "config", "core.hooksPath", "sib-hooks")
    dep = kit.clone(repo / "vendor/dep")
    kit.git("-C", dep, "config", "core.hooksPath", "dep-hooks")
    kit.settings(repo, writable_siblings=["Sib"], nested_clones=["vendor/dep"])
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert hooks_mounts(run.container, kit, repo, sib) == [
        "Main-Clone/vendor/dep/dep-hooks",
        "Sib/sib-hooks",
    ]


@pytest.mark.parametrize(("in_repo", "in_sibling"), [("h/sub", "h"), ("h", "h/sub")])
def test_only_the_outer_of_two_nested_folders_mounts(
    kit: Kit, in_repo: str, in_sibling: str
) -> None:
    repo = kit.main_clone(origin="git@github.com:program-org/main-clone.git")
    sib = kit.clone(kit.src / "Sib", "git@github.com:program-org/sib.git")
    kit.git("-C", repo, "config", "core.hooksPath", repo / in_repo)
    kit.git("-C", sib, "config", "core.hooksPath", repo / in_sibling)
    kit.settings(repo, writable_siblings=["Sib"])
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert hooks_mounts(run.container, kit, repo, sib) == ["Main-Clone/h"]


def test_value_in_other_case_mounts_as_the_disk_spells_it(kit: Kit) -> None:
    if not ignores_case(kit.src):
        pytest.skip("this disk does not ignore case")
    repo = kit.main_clone()
    (repo / ".husky/_").mkdir(parents=True)
    kit.git("-C", repo, "config", "core.hooksPath", kit.src / "MAIN-CLONE/.HUSKY/_")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert hooks_mounts(run.container, kit, repo) == ["Main-Clone/.husky/_"]


# The repo's root, a folder that holds it, and a link each stop the start.
# So does a folder below a link: the container could point the link
# elsewhere. So does a .. out of a missing folder: the container could make
# it a link. A folder that holds .git/hooks, a path through a file and links
# that loop stop it too.


@pytest.mark.parametrize(
    "value",
    [
        ".",
        "..",
        "hooks-link",
        "up-link/real-hooks",
        "missing/../.husky/_",
        ".git",
        "a-file/hooks",
        "{work}/loop/hooks",
        "{src}/MAIN-CLONE/hooks-link",
    ],
)
def test_folder_it_cannot_guard_stops_the_start(kit: Kit, value: str) -> None:
    if "MAIN-CLONE" in value and not ignores_case(kit.src):
        pytest.skip("this disk does not ignore case")
    value = value.format(work=kit.work, src=kit.src)
    repo = kit.main_clone()
    (kit.work / "real-hooks").mkdir()
    (repo / "hooks-link").symlink_to(kit.work / "real-hooks")
    (repo / "up-link").symlink_to(kit.work)
    (kit.work / "loop").symlink_to(kit.work / "loop")
    (repo / "a-file").write_text("")
    kit.git("-C", repo, "config", "core.hooksPath", value)
    run = kit.run(repo)
    assert run.returncode != 0
    assert "core.hooksPath names a folder the launcher cannot mount read-only" in run.stderr
    assert "did not start" in run.stderr
    assert f"\n  {repo}: core.hooksPath = {value}\n" in run.stderr
    assert not run.started
