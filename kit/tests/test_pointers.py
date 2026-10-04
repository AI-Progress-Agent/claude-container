"""Files that could lead git on the Mac to hooks or a config written inside.

The container can write anywhere in the repo and in each writable sibling. A
.git, a bare repo's HEAD, or a commondir there could lead git on the Mac to
hooks or a config the container wrote. A start refuses each one it finds.
During a session, the watcher moves each one aside.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from harness import MAIN_FOLDERS, Kit, folder_mounts

REFUSAL = (
    "docker/cc: these could lead git on the Mac to hooks or a config written inside, "
    "so the container did not start:"
)
MOVED = (
    "docker/cc: moved aside to .cc-blocked, since each could lead git on the Mac "
    "to hooks or a config written inside:"
)


def assert_refused(kit: Kit, repo: Path, *paths: Path) -> None:
    run = kit.run(repo)
    assert run.returncode != 0
    lines = run.lines()
    assert REFUSAL in lines, run.stderr
    named = lines[lines.index(REFUSAL) + 1 : lines.index(REFUSAL) + 1 + len(paths)]
    assert named == [f"  {p}" for p in paths]
    assert not lines[lines.index(REFUSAL) + 1 + len(paths)].startswith("  ")
    assert not run.started


def test_repo_with_worktrees_and_a_submodule_starts(kit: Kit) -> None:
    repo = kit.main_clone()
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert REFUSAL not in run.stderr


def test_nested_clone_is_refused_until_nested_clones_names_it(kit: Kit) -> None:
    repo = kit.main_clone()
    dep = kit.clone(repo / "vendor/dep")
    assert_refused(kit, repo, dep / ".git")

    kit.settings(repo, nested_clones=["vendor/dep/"])
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert "docker/cc: guarded nested clones: vendor/dep/" in run.lines()
    # Its hooks, config and commondir mount read-only, as a writable
    # sibling's do.
    for file in ("hooks", "config", "commondir"):
        assert run.container.has_mount(dep / ".git" / file, ro=True), file
    # Each folder from the repo's root down to its .git mounts on its own.
    assert folder_mounts(run.container, kit) == sorted([
        *MAIN_FOLDERS,
        "Main-Clone/vendor",
        "Main-Clone/vendor/dep",
        "Main-Clone/vendor/dep/.git",
    ])  # fmt: skip


def test_cc_local_can_add_to_nested_clones(kit: Kit) -> None:
    repo = kit.main_clone()
    kit.clone(repo / "vendor/dep")
    kit.cc_local(repo, "nested_clones+=(vendor/dep)\n")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert "docker/cc: guarded nested clones: vendor/dep" in run.lines()


def test_nested_clone_that_is_no_repo_is_not_guarded(kit: Kit) -> None:
    repo = kit.main_clone()
    kit.settings(repo, nested_clones=["vendor/none"])
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert (
        "docker/cc: nested clone vendor/none has no .git directory and is no bare repo, "
        "so it is not guarded"
    ) in run.lines()


def test_nested_bare_repo_named_in_nested_clones_starts(kit: Kit) -> None:
    repo = kit.main_clone()
    fixture = kit.clone(repo / "fixture", bare=True)
    kit.settings(repo, nested_clones=["fixture"])
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    for file in ("hooks", "config", "commondir"):
        assert run.container.has_mount(fixture / file, ro=True), file


def test_git_file_naming_another_repos_git_folder(kit: Kit) -> None:
    repo = kit.main_clone()
    evil = kit.clone(kit.work / "evil")
    (repo / "elsewhere").mkdir()
    (repo / "elsewhere/.git").write_text(f"gitdir: {evil / '.git'}\n")
    assert_refused(kit, repo, repo / "elsewhere/.git")


def test_bare_repo_which_needs_no_git_for_git_to_find_it(kit: Kit) -> None:
    repo = kit.main_clone()
    kit.clone(repo / "bare", bare=True)
    assert_refused(kit, repo, repo / "bare/HEAD")


def test_head_beside_a_commondir(kit: Kit) -> None:
    """Git looks for objects and refs in the folder a commondir names."""
    repo = kit.main_clone()
    evil = kit.clone(kit.work / "evil")
    odd = repo / "odd"
    odd.mkdir()
    (odd / "HEAD").write_text("ref: refs/heads/main\n")
    (odd / "commondir").write_text(f"{evil / '.git'}\n")
    assert_refused(kit, repo, odd / "HEAD")


def test_head_beside_objects_and_refs_as_files(kit: Kit) -> None:
    repo = kit.main_clone()
    odd = repo / "odd"
    odd.mkdir()
    (odd / "HEAD").write_text("ref: refs/heads/main\n")
    for name in ("objects", "refs"):
        (odd / name).write_text("")
        (odd / name).chmod(0o755)
    assert_refused(kit, repo, odd / "HEAD")


def test_names_in_other_case_and_a_head_that_is_a_link(kit: Kit) -> None:
    """The Mac's disk ignores case, so git takes .GIT for .git and head for HEAD."""
    repo = kit.main_clone()
    upper = kit.clone(repo / "upper")
    (upper / ".git").rename(upper / ".GIT")
    lower = kit.clone(repo / "lower", bare=True)
    (lower / "HEAD").rename(lower / "head")
    linked = kit.clone(repo / "linked", bare=True)
    (linked / "HEAD").unlink()
    (linked / "HEAD").symlink_to("refs/heads/main")
    run = kit.run(repo)
    assert run.returncode != 0
    named = sorted(line.strip() for line in run.lines() if line.startswith("  "))
    assert named == sorted([str(upper / ".GIT"), str(lower / "head"), str(linked / "HEAD")])


@pytest.mark.parametrize("target_exists", [False, True])
def test_commondir_that_is_a_link_is_refused_and_never_written_through(
    kit: Kit, target_exists: bool
) -> None:
    repo = kit.main_clone()
    target = kit.work / "written-through"
    if target_exists:
        target.write_text(f"{repo / '.git'}\n")
    (repo / ".git/commondir").symlink_to(target)
    run = kit.run(repo)
    assert run.returncode != 0
    assert REFUSAL in run.lines(), run.stderr
    if target_exists:
        assert f"  {repo / '.git/commondir'}" in run.lines()
        assert target.read_text() == f"{repo / '.git'}\n"
    else:
        # Git cannot read the clone at all, so its .git leads nowhere guarded.
        assert f"  {repo / '.git'}" in run.lines()
        assert not target.exists()
    assert not run.started


def test_commondir_naming_another_repo(kit: Kit) -> None:
    """Git then reads the other repo's config, so the repo's .git counts as unguarded."""
    repo = kit.main_clone()
    evil = kit.clone(kit.work / "evil")
    (repo / ".git/commondir").write_text(f"{evil / '.git'}\n")
    run = kit.run(repo)
    assert run.returncode != 0
    assert REFUSAL in run.lines()
    assert any(line.startswith(f"  {repo / '.git'}") for line in run.lines()), run.stderr
    assert not run.started


# During a session, the watcher moves each one aside.


def test_session_moves_each_one_aside_and_tells_notify_host(kit: Kit) -> None:
    repo = kit.main_clone()
    # A path can hold a tab, a quote or a backslash, and the event is JSON.
    odd = repo / 'odd\t"name\\'
    kit.cc_local(
        repo, f'notify_host() {{ cat >>"{kit.work / "heard"}"; echo >>"{kit.work / "heard"}"; }}\n'
    )
    kit.on_run(f"""
git init -q {repo}/vendor/dep
git init -q --bare {repo}/vendor/dep/.git/modules/sub
git init -q --bare '{odd}'
git init -q {repo}/x.cc-blocked/dep
""")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    moved = [repo / "vendor/dep/.git", odd / "HEAD", repo / "x.cc-blocked/dep/.git"]
    lines = run.lines()
    assert MOVED in lines
    assert sorted(lines[lines.index(MOVED) + 1 :]) == sorted(f"  {p}" for p in moved)
    # A folder's HEAD moves aside too, so git does not take it for a bare repo.
    assert (repo / "vendor/dep/.git.cc-blocked/HEAD.cc-blocked").is_file()
    assert (odd / "HEAD.cc-blocked").is_file()
    assert (repo / "x.cc-blocked/dep/.git.cc-blocked").is_dir()
    events = [json.loads(line) for line in (kit.work / "heard").read_text().splitlines() if line]
    assert sorted(e["message"] for e in events) == sorted(
        f"docker/cc blocked {p}: it could lead git on the Mac to hooks or a config written inside"
        for p in moved
    )
    assert {(e["hook_event_name"], e["cwd"]) for e in events} == {("Notification", str(repo))}

    # A submodule's git folder in a moved .git can still be opened, so the
    # next start finds it.
    assert_refused(kit, repo, repo / "vendor/dep/.git.cc-blocked/modules/sub/HEAD")


def test_session_moves_aside_while_the_container_runs(kit: Kit) -> None:
    """The watcher searches about every 5 seconds, not only at the end."""
    repo = kit.main_clone()
    kit.on_run(f"""
git init -q {repo}/late
for _ in $(seq 100); do
  [ ! -e {repo}/late/.git.cc-blocked ] || {{ echo moved >{kit.work / "seen"}; exit 0; }}
  sleep 0.1
done
""")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert (kit.work / "seen").read_text() == "moved\n"


def test_worktree_made_during_the_session_passes(kit: Kit) -> None:
    repo = kit.main_clone()
    kit.on_run(f"git -C {repo} worktree add -q {repo}/.claude/worktrees/new\n")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert MOVED not in run.stderr
    assert (repo / ".claude/worktrees/new/.git").is_file()


def test_new_worktree_whose_commondir_names_another_repo_moves_aside(kit: Kit) -> None:
    repo = kit.main_clone()
    evil = kit.clone(kit.work / "evil")
    kit.on_run(f"""
git -C {repo} worktree add -q {repo}/.claude/worktrees/new
printf '%s\\n' {evil}/.git >{repo}/.git/worktrees/new/commondir
""")
    run = kit.run(repo)
    assert f"  {repo / '.claude/worktrees/new/.git'}" in run.lines()
    assert (repo / ".claude/worktrees/new/.git.cc-blocked").is_file()


@pytest.mark.parametrize(("config", "moves"), [("[core]\n\thooksPath = /tmp\n", True), ("", False)])
def test_new_worktrees_config_worktree_passes_only_while_empty(
    kit: Kit, config: str, moves: bool
) -> None:
    """With extensions.worktreeConfig on, a new worktree's config.worktree did not mount."""
    repo = kit.main_clone()
    kit.git("-C", repo, "config", "extensions.worktreeConfig", "true")
    kit.on_run(f"""
git -C {repo} worktree add -q {repo}/.claude/worktrees/new
printf '{config.replace(chr(10), "\\n").replace(chr(9), "\\t")}' >{repo}/.git/worktrees/new/config.worktree
""")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert (f"  {repo / '.claude/worktrees/new/.git'}" in run.lines()) is moves
