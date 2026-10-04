"""What a repo's config names inside a writable mount mounts read-only.

Git on the Mac reads each included config file, and runs each command value.
A file or folder they name inside a writable mount could be written inside,
and git on the Mac would then read or run it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from harness import MAIN_GUARDED, Kit
from planning import host, read_only

from launcher.plan import RunPlan, plan_run

pytestmark = pytest.mark.usefixtures("plan_env")

REFUSAL = (
    "docker/cc: a config value names a path the launcher cannot mount read-only, "
    "so the container did not start:"
)

# What a plain clone mounts read-only below its root before any config.
PLAIN_GUARDED = ["app/.git/commondir"]


def app(kit: Kit) -> Path:
    return kit.clone(kit.src / "app")


def plan(kit: Kit, repo: Path) -> RunPlan:
    return plan_run(host(kit, repo))


def guarded(kit: Kit, plan: RunPlan, repo: Path) -> list[str]:
    """The read-only mounts below repo that the config adds."""
    return [p for p in read_only(kit, plan, repo) if p not in PLAIN_GUARDED]


def refused(plan: RunPlan) -> list[str]:
    """Each line the refusal names."""
    assert plan.refusal is not None
    assert plan.refusal.startswith(f"{REFUSAL}\n"), plan.refusal
    return [line for line in plan.refusal.splitlines() if line.startswith("  ")]


# Included files.


def test_included_file_mounts_read_only_and_is_flagged(kit: Kit) -> None:
    repo = app(kit)
    kit.git("-C", repo, "config", "include.path", "../tools/git.cfg")
    (repo / "tools").mkdir()
    (repo / "tools/git.cfg").write_text("[core]\n\tfsmonitor = false\n")
    result = plan(kit, repo)
    assert result.refusal is None
    assert guarded(kit, result, repo) == ["app/tools/git.cfg"]
    assert str(repo / "tools/git.cfg") in result.flag_files
    # The folder above it mounts on its own, as for every read-only mount.
    assert any(m.target == str(repo / "tools") and not m.read_only for m in result.mounts)


def test_include_whose_condition_is_off_mounts_too(kit: Kit) -> None:
    repo = app(kit)
    kit.git("-C", repo, "config", "includeIf.onbranch:x.path", "../tools/x.cfg")
    (repo / "tools").mkdir()
    (repo / "tools/x.cfg").write_text("")
    result = plan(kit, repo)
    assert guarded(kit, result, repo) == ["app/tools/x.cfg"]


def test_missing_included_file_is_planned_empty_then_mounts(kit: Kit) -> None:
    repo = app(kit)
    kit.git("-C", repo, "config", "include.path", "../tools/git.cfg")
    result = plan(kit, repo)
    assert result.refusal is None
    assert str(repo / "tools") in result.make_dirs
    assert dict(result.make_files)[str(repo / "tools/git.cfg")] == ""
    assert not (repo / "tools").exists()
    assert guarded(kit, result, repo) == ["app/tools/git.cfg"]
    assert str(repo / "tools/git.cfg") in result.flag_files


def test_include_inside_an_included_file_is_followed(kit: Kit) -> None:
    repo = app(kit)
    kit.git("-C", repo, "config", "include.path", "../tools/git.cfg")
    (repo / "tools").mkdir()
    (repo / "tools/git.cfg").write_text("[include]\n\tpath = nested/more.cfg\n")
    (repo / "tools/nested").mkdir()
    (repo / "tools/nested/more.cfg").write_text("[core]\n\tfsmonitor = ./tools/fsm.sh\n")
    (repo / "tools/fsm.sh").write_text("")
    result = plan(kit, repo)
    assert result.refusal is None
    assert guarded(kit, result, repo) == [
        "app/tools/fsm.sh",
        "app/tools/git.cfg",
        "app/tools/nested/more.cfg",
    ]


def test_global_include_with_a_tilde_names_a_file_in_the_repo(kit: Kit) -> None:
    repo = app(kit)
    kit.git("config", "--global", "include.path", "~/src/app/team.cfg")
    (repo / "team.cfg").write_text("")
    assert guarded(kit, plan(kit, repo), repo) == ["app/team.cfg"]


def test_included_file_outside_every_writable_mount_needs_nothing(kit: Kit) -> None:
    repo = app(kit)
    outside = kit.work / "outside.cfg"
    outside.write_text("")
    kit.git("-C", repo, "config", "include.path", outside)
    result = plan(kit, repo)
    assert result.refusal is None
    assert guarded(kit, result, repo) == []
    assert str(outside) not in dict(result.make_files)


def test_included_file_through_a_link_is_refused(kit: Kit) -> None:
    repo = app(kit)
    (kit.work / "real").mkdir()
    (repo / "tools").symlink_to(kit.work / "real")
    kit.git("-C", repo, "config", "include.path", "../tools/git.cfg")
    assert refused(plan(kit, repo)) == [
        f"  {repo}: include.path = ../tools/git.cfg names {repo}/tools/git.cfg"
    ]


# Command values.


@pytest.mark.parametrize(
    ("key", "value", "made", "mounted"),
    [
        ("core.fsmonitor", "./tools/fsm.sh", "tools/fsm.sh", "app/tools/fsm.sh"),
        ("filter.x.clean", "python tools/strip.py", "tools/strip.py", "app/tools/strip.py"),
        ("alias.t", "!cd scripts && ./run.sh", "scripts/run.sh", "app/scripts"),
        ("core.sshCommand", "ssh -F tools/ssh.cfg", "tools/ssh.cfg", "app/tools/ssh.cfg"),
        ("diff.x.textconv", "conv --map=tools/map.txt", "tools/map.txt", "app/tools/map.txt"),
        ("credential.helper", "!sh tools/cred.sh", "tools/cred.sh", "app/tools/cred.sh"),
        # An alias named path is a shell command like any other.
        ("alias.path", "!cd scripts && ./run.sh", "scripts/run.sh", "app/scripts"),
        # A cd goes from the folder of the cd before it.
        ("alias.deep", "!cd a && cd b && ./x", "a/b/x", "app/a"),
    ],
)
def test_path_a_command_value_names_mounts_read_only(
    kit: Kit, key: str, value: str, made: str, mounted: str
) -> None:
    repo = app(kit)
    kit.git("-C", repo, "config", key, value)
    (repo / made).parent.mkdir(parents=True, exist_ok=True)
    (repo / made).write_text("")
    result = plan(kit, repo)
    assert result.refusal is None, result.refusal
    assert guarded(kit, result, repo) == [mounted]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("filter.lfs.process", "git-lfs filter-process"),
        ("core.sshCommand", "ssh -o UserKnownHostsFile=/dev/null"),
        ("core.editor", "code --wait"),
        ("alias.top", '!cd "$(git rev-parse --show-toplevel)/tools" && ./x'),
        ("alias.web", "!open https://github.com/org/repo"),
        # git runs these as git subcommands, not as programs.
        ("alias.st", "status tools/fsm.sh"),
        ("credential.helper", "store --file tools/creds"),
        # Git runs nothing from a key that reads content only.
        ("commit.template", "tools/missing.txt"),
    ],
)
def test_value_with_no_word_inside_a_writable_mount_adds_nothing(
    kit: Kit, key: str, value: str
) -> None:
    repo = app(kit)
    kit.git("-C", repo, "config", key, value)
    result = plan(kit, repo)
    assert result.refusal is None, result.refusal
    assert guarded(kit, result, repo) == []


@pytest.mark.parametrize(
    ("value", "path"),
    [
        (".", "{repo}"),
        ("./tools/fsm.sh", "{repo}/tools/fsm.sh"),
        ("sh tools-link/fsm.sh", "{repo}/tools-link/fsm.sh"),
        # A folder that a cd names is a path, with or without a /.
        ("cd missing && ./fsm.sh", "{repo}/missing"),
    ],
)
def test_path_it_cannot_guard_stops_the_start(kit: Kit, value: str, path: str) -> None:
    repo = app(kit)
    (kit.work / "real").mkdir()
    (kit.work / "real/fsm.sh").write_text("")
    (repo / "tools-link").symlink_to(kit.work / "real")
    (repo / "fsm.sh").write_text("")
    kit.git("-C", repo, "config", "core.fsmonitor", value)
    assert refused(plan(kit, repo)) == [
        f"  {repo}: core.fsmonitor = {value} names {path.format(repo=repo)}"
    ]


def test_relative_word_resolves_in_each_worktree(kit: Kit) -> None:
    repo = kit.main_clone()
    for worktree in (repo, repo / ".claude/worktrees/agent", repo / ".worktrees/other"):
        (worktree / "fsm.sh").write_text("")
    kit.git("-C", repo, "config", "core.fsmonitor", "fsm.sh")
    result = plan(kit, repo)
    assert result.refusal is None, result.refusal
    assert [p for p in read_only(kit, result, repo) if p not in MAIN_GUARDED] == [
        "Main-Clone/.claude/worktrees/agent/fsm.sh",
        "Main-Clone/.worktrees/other/fsm.sh",
        "Main-Clone/fsm.sh",
    ]


# Through the launcher.


def test_launcher_mounts_an_included_file_read_only(kit: Kit) -> None:
    repo = app(kit)
    kit.git("-C", repo, "config", "include.path", "../tools/git.cfg")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert (repo / "tools/git.cfg").read_text() == ""
    assert run.container.has_mount(repo / "tools/git.cfg", ro=True)
    assert str(repo / "tools/git.cfg") in run.container.flags


def test_launcher_refuses_a_missing_path_a_command_names(kit: Kit) -> None:
    repo = app(kit)
    kit.git("-C", repo, "config", "core.fsmonitor", "./tools/fsm.sh")
    run = kit.run(repo)
    assert run.returncode != 0
    lines = run.lines()
    assert REFUSAL in lines, run.stderr
    assert lines[lines.index(REFUSAL) + 1] == (
        f"  {repo}: core.fsmonitor = ./tools/fsm.sh names {repo}/tools/fsm.sh"
    )
    assert not run.started
