"""The Mac's immutable flag on each guarded file, for the session.

A file mounted read-only on its own sits in a writable folder. The Mac's disk
ignores case, so a write inside to .git/CONFIG would replace .git/config. The
launcher flags each such file with chflags uchg before the container starts,
and clears the flag when the session ends. Each launcher records the files it
flags in $TMPDIR/cc-flags/<its process ID>, so two sessions can share one.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from harness import Kit

MAIN_FLAGGED = [
    "Main-Clone/.claude/worktrees/agent/.git",
    "Main-Clone/.git/commondir",
    "Main-Clone/.git/config",
    "Main-Clone/.git/modules/lib/commondir",
    "Main-Clone/.git/modules/lib/config",
    "Main-Clone/.git/worktrees/agent/commondir",
    "Main-Clone/.git/worktrees/other/commondir",
    "Main-Clone/.worktrees/other/.git",
]

REFUSAL = (
    "docker/cc: these could not be flagged read-only on the Mac, so the container did not start:"
)
STUCK = "docker/cc: these stay flagged read-only on the Mac. Clear each one with chflags nouchg:"


@pytest.fixture
def other_launcher() -> Iterator[subprocess.Popen[bytes]]:
    """A live process that stands in for another session's launcher."""
    proc = subprocess.Popen(["sleep", "300"])
    yield proc
    proc.kill()
    proc.wait()


def ended_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def test_each_guarded_file_is_flagged_for_the_session(kit: Kit) -> None:
    repo = kit.main_clone()
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    # Flagged, and recorded, while the container runs.
    assert kit.rel(run.container.flags) == MAIN_FLAGGED
    assert run.container.record is not None
    assert kit.rel(run.container.record) == MAIN_FLAGGED
    # The end clears each one and removes the record.
    assert kit.flagged() == []
    assert list(kit.flags_dir.iterdir()) == []


def test_from_a_worktree_files_outside_every_writable_mount_are_not_flagged(kit: Kit) -> None:
    repo = kit.main_clone()
    run = kit.run(repo / ".claude/worktrees/agent")
    assert kit.rel(run.container.flags) == [
        f for f in MAIN_FLAGGED if f != "Main-Clone/.worktrees/other/.git"
    ]


def test_below_the_clones_root_nothing_is_flagged(kit: Kit) -> None:
    """The clone's .git then mounts read-only as a whole."""
    repo = kit.main_clone()
    (repo / "sub").mkdir()
    run = kit.run(repo / "sub")
    assert run.returncode == 0, run.stderr
    assert run.container.flags == []
    assert run.container.record is None


def test_worktree_config_and_nested_clone_files_are_flagged(kit: Kit) -> None:
    repo = kit.main_clone()
    kit.git("-C", repo, "config", "extensions.worktreeConfig", "true")
    kit.clone(repo / "vendor/dep")
    kit.settings(repo, nested_clones=["vendor/dep"])
    run = kit.run(repo)
    assert kit.rel(run.container.flags) == sorted([
        *MAIN_FLAGGED,
        "Main-Clone/.git/config.worktree",
        "Main-Clone/.git/worktrees/agent/config.worktree",
        "Main-Clone/.git/worktrees/other/config.worktree",
        "Main-Clone/vendor/dep/.git/commondir",
        "Main-Clone/vendor/dep/.git/config",
    ])  # fmt: skip


def test_through_a_link_each_file_is_flagged_once_by_its_real_path(kit: Kit) -> None:
    repo = kit.main_clone()
    (kit.src / "Link").symlink_to(repo)
    run = kit.run(kit.src / "Link")
    assert run.container.record is not None
    assert kit.rel(run.container.record) == MAIN_FLAGGED


def test_writable_siblings_files_are_flagged(kit: Kit) -> None:
    repo = kit.main_clone(origin="git@github.com:program-org/main-clone.git")
    kit.clone(kit.src / "Sib", "git@github.com:program-org/sib.git")
    kit.settings(repo, writable_siblings=["Sib"])
    run = kit.run(repo)
    assert kit.rel(run.container.flags) == sorted(
        [*MAIN_FLAGGED, "Sib/.git/commondir", "Sib/.git/config"]
    )


def test_a_flag_already_set_at_start_is_cleared_at_the_end(kit: Kit) -> None:
    """A launcher killed before its end leaves its flags set."""
    repo = kit.main_clone()
    (kit.fake / "flags").write_text(f"{repo / '.git/config'}\n")
    kit.run(repo)
    assert kit.flagged() == []


def test_another_live_launchers_record_keeps_its_files_flagged(
    kit: Kit, other_launcher: subprocess.Popen[bytes]
) -> None:
    repo = kit.main_clone()
    config = repo / ".git/config"
    live = kit.flags_dir / str(other_launcher.pid)
    ended = kit.flags_dir / str(ended_pid())
    kit.flags_dir.mkdir()
    live.write_text(f"{config}\n")
    ended.write_text(f"{repo / '.git/commondir'}\n")
    kit.run(repo)
    assert kit.flagged() == [str(config)]
    # A record whose launcher has ended counts for nothing, and goes.
    assert not ended.exists()
    assert live.exists()

    # Once that launcher ends too, a later end clears its files.
    other_launcher.kill()
    other_launcher.wait()
    kit.run(repo)
    assert kit.flagged() == []


def test_a_launcher_that_starts_during_the_end_keeps_its_files_flagged(
    kit: Kit, other_launcher: subprocess.Popen[bytes]
) -> None:
    """It records its files before it flags them, so the end looks again."""
    repo = kit.main_clone()
    config = repo / ".git/config"
    record = kit.flags_dir / str(other_launcher.pid)
    kit.on_chflags(f"""
if [ "$1" = nouchg ]; then printf '%s\\n' '{config}' >'{record}'; fi
""")
    kit.run(repo)
    assert kit.flagged() == [str(config)]


def test_a_file_that_cannot_be_flagged_stops_the_start(kit: Kit) -> None:
    repo = kit.main_clone()
    commondir = repo / ".git/commondir"
    kit.refuse_chflags("uchg", commondir)
    run = kit.run(repo)
    assert run.returncode != 0
    lines = run.lines()
    assert REFUSAL in lines
    assert lines[lines.index(REFUSAL) + 1] == f"  {commondir}"
    # It clears each flag it set, and does not ask you to clear the one it
    # could not set.
    assert STUCK not in run.stderr
    assert kit.flagged() == []
    assert list(kit.flags_dir.iterdir()) == []
    assert not run.started


def test_a_flag_the_end_cannot_clear_is_named(kit: Kit) -> None:
    repo = kit.main_clone()
    commondir = repo / ".git/commondir"
    kit.refuse_chflags("nouchg", commondir)
    run = kit.run(repo)
    assert run.returncode == 0
    lines = run.lines()
    assert STUCK in lines
    assert lines[lines.index(STUCK) + 1 :] == [f"  {commondir}"]


def test_a_term_to_the_watcher_waits_for_compose_then_ends_the_session(kit: Kit) -> None:
    """A closed terminal can reach the watcher before compose ends."""
    repo = kit.main_clone()
    after_term = kit.work / "after-term"
    kit.on_run(f"""
pgrep -P "$DOCKER_PID" >'{kit.work / "children"}' || true
while read -r pid; do
  [ "$pid" = $$ ] || kill -TERM "$pid"
done <'{kit.work / "children"}'
sleep 1
# The flags stay set while the container can still write.
cp '{kit.fake / "flags"}' '{after_term}'
git init -q '{repo}/late'
""")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert kit.rel(after_term.read_text().splitlines()) == MAIN_FLAGGED
    # Then it runs the last search, clears the flags and removes its folder.
    assert (repo / "late/.git.cc-blocked").is_dir()
    assert f"  {repo / 'late/.git'}" in run.lines()
    assert kit.flagged() == []
    assert not Path(run.container.notify_dir).exists()
