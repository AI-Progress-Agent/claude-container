"""The folder that core.hooksPath names mounts read-only in each worktree.

Git on the Mac runs hooks from there, so a hook written inside would run on
the Mac. Only a folder inside a writable mount needs the mount.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from harness import MAIN_GUARDED, Kit, folder_mounts, hooks_mounts, ignores_case, read_only

MOVED = (
    "docker/cc: moved aside to .cc-blocked, since each could lead git on the Mac "
    "to hooks or a config written inside:"
)


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
    # A folder guarded at start stays where it is.
    assert MOVED not in run.stderr


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


# During the session, the watcher moves aside each hooks folder inside a
# writable mount that was not guarded at start.


def test_hooks_folder_of_a_worktree_made_inside_moves_aside(kit: Kit) -> None:
    repo = kit.main_clone()
    (repo / ".husky/_").mkdir(parents=True)
    kit.git("-C", repo, "config", "core.hooksPath", ".husky/_")
    new = repo / ".claude/worktrees/new"
    kit.on_run(f"""
git -C {repo} worktree add -q {new}
mkdir -p {new}/.husky/_
printf '#!/bin/sh\\necho ran >&2\\n' >{new}/.husky/_/pre-commit
""")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    lines = run.lines()
    assert MOVED in lines
    assert lines[lines.index(MOVED) + 1 :] == [f"  {new / '.husky/_'}"]
    assert (new / ".husky/_.cc-blocked/pre-commit").is_file()
    assert not (new / ".husky/_").exists()
    # The worktree itself keeps working.
    assert (new / ".git").is_file()


def test_hooks_folder_of_a_worktree_missing_at_start_moves_aside(kit: Kit) -> None:
    repo = kit.main_clone()
    kit.git("-C", repo, "config", "core.hooksPath", ".githooks")
    other = repo / ".worktrees/other"
    shutil.rmtree(other)
    kit.on_run(f"""
mkdir -p {other}/.githooks
printf 'gitdir: %s\\n' {repo}/.git/worktrees/other >{other}/.git
""")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert f"  {other / '.githooks'}" in run.lines()
    assert (other / ".githooks.cc-blocked").is_dir()
    assert (other / ".git").is_file()


def test_hooks_folder_a_branch_switch_turns_on_moves_aside(kit: Kit) -> None:
    repo = kit.main_clone()
    config = kit.work / "x.cfg"
    config.write_text("[core]\n\thooksPath = .x-hooks\n")
    kit.git("-C", repo, "config", "includeIf.onbranch:x.path", config)
    kit.on_run(f"git -C {repo} switch -q -c x\nmkdir {repo}/.x-hooks\n")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert f"  {repo / '.x-hooks'}" in run.lines()
    assert (repo / ".x-hooks.cc-blocked").is_dir()


@pytest.mark.parametrize("trick", ["link", "worktree"])
def test_hooks_folder_it_cannot_move_safely_cuts_the_worktree_off(kit: Kit, trick: str) -> None:
    """The container can make a new worktree's hooks folder one the launcher cannot guard.

    A link inside a writable mount moves aside, so git on the Mac finds no
    folder. A worktree inside the folder leaves no safe folder to move, so
    the new worktree's .git file moves aside.
    """
    repo = kit.main_clone()
    (repo / ".husky/_").mkdir(parents=True)
    kit.git("-C", repo, "config", "core.hooksPath", ".husky/_")
    new = repo / ".claude/worktrees/new"
    make = {
        "link": f"mkdir -p {repo}/real-hooks {new}/.husky\nln -s {repo}/real-hooks {new}/.husky/_",
        "worktree": f"git -C {repo} worktree add -q {new}/.husky/_/inner",
    }[trick]
    kit.on_run(f"git -C {repo} worktree add -q {new}\n{make}\n")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    moved = {"link": new / ".husky/_", "worktree": new / ".git"}[trick]
    assert f"  {moved}" in run.lines()
    assert Path(f"{moved}.cc-blocked").exists() or Path(f"{moved}.cc-blocked").is_symlink()
