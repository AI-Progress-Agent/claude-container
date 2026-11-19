"""host_git_guard = false: the launcher leaves git on the Mac unguarded.

By default the launcher guards every git folder the container can write:
read-only hooks and config, the uchg flag, the start's refusals, and the
watcher that moves git pointers and hooks folders aside. With the setting
false, it does none of that. A git config or hook written inside can then run
on the Mac. It still mounts each worktree's .git file that no writable mount
holds, so git inside does not take that worktree for a deleted one.
"""

from __future__ import annotations

from harness import Container, Kit, folder_mounts, read_only

GUARD_OFF = (
    "docker/cc: host_git_guard = false, so a git config or hook written inside can run on the Mac"
)
MOVED = "docker/cc: moved aside to .cc-blocked"


def guard_file_passed(container: Container) -> bool:
    return any(arg.endswith("/compose.guard.yaml") for arg in container.compose)


def test_by_default_compose_gets_the_guard_file(kit: Kit) -> None:
    repo = kit.main_clone()
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert guard_file_passed(run.container)
    assert GUARD_OFF not in run.lines()


def test_guard_off_mounts_no_git_file_read_only_and_flags_nothing(kit: Kit) -> None:
    repo = kit.main_clone()
    kit.settings(repo, host_git_guard=False)
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert GUARD_OFF in run.lines()
    container = run.container
    assert not guard_file_passed(container)
    assert read_only(container, kit, under=[repo]) == []
    assert folder_mounts(container, kit) == []
    assert container.flags == []
    assert container.record is None
    # The guard makes a commondir that names its own git folder. Off, it makes none.
    assert not (repo / ".git/commondir").exists()


def test_guard_off_mounts_the_git_folder_writable_below_the_clones_root(kit: Kit) -> None:
    repo = kit.main_clone()
    (repo / "sub").mkdir()
    kit.settings(repo / "sub", host_git_guard=False)
    env = kit.run(repo / "sub").container.env
    assert (env["REPO_GIT"], env["REPO_GIT_MODE"]) == (str(repo / ".git"), "rw")


def test_guard_off_starts_with_any_clone_inside_and_keeps_one_made_there(kit: Kit) -> None:
    repo = kit.main_clone()
    dep = kit.clone(repo / "vendor/dep")
    kit.settings(repo, host_git_guard=False)
    kit.on_run(f"git init -q {repo}/late\n")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert MOVED not in run.stderr
    assert (dep / ".git").is_dir()
    assert (repo / "late/.git").is_dir()


def test_guard_off_ignores_nested_clones(kit: Kit) -> None:
    repo = kit.main_clone()
    dep = kit.clone(repo / "vendor/dep")
    kit.settings(repo, host_git_guard=False, nested_clones=["vendor/dep"])
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert "guarded nested clones" not in run.stderr
    assert read_only(run.container, kit, under=[dep]) == []


def test_guard_off_leaves_hooks_folders_alone(kit: Kit) -> None:
    repo = kit.main_clone()
    kit.git("-C", repo, "config", "core.hooksPath", ".husky/_")
    kit.settings(repo, host_git_guard=False)
    new = repo / ".claude/worktrees/new"
    kit.on_run(f"git -C {repo} worktree add -q {new}\nmkdir -p {new}/.husky/_\n")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert MOVED not in run.stderr
    assert (new / ".husky/_").is_dir()
    # The guard would make the folder on the Mac first. Off, it makes none.
    assert not (repo / ".husky").exists()


def test_guard_off_starts_where_the_guard_refuses(kit: Kit) -> None:
    """A hooks folder or a config command path the guard cannot mount read-only."""
    repo = kit.main_clone()
    kit.git("-C", repo, "config", "core.hooksPath", ".")
    kit.git("-C", repo, "config", "core.fsmonitor", "./tools/fsm.sh")
    kit.settings(repo, host_git_guard=False)
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert run.started


def test_guard_off_keeps_a_worktree_outside_the_repo_visible(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    elsewhere = kit.work / "elsewhere"
    kit.git("-C", repo, "worktree", "add", "-q", elsewhere)
    kit.settings(repo, host_git_guard=False)
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert run.container.has_mount(elsewhere / ".git", ro=True)
    assert read_only(run.container, kit, under=[repo]) == []
