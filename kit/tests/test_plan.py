"""The planning step, called directly on fixture folders, with no fake docker.

It reads the host and returns a RunPlan. It writes nothing: each folder or
file the run needs is in the plan, for the executor to make.
"""

from __future__ import annotations

import pytest
from harness import MAIN_FOLDERS, MAIN_GUARDED, Kit
from planning import Binds, host, overrides, read_only

from launcher.plan import plan_run
from launcher.settings import Settings

pytestmark = pytest.mark.usefixtures("plan_env")


def test_main_clone_plans_its_guards_and_makes_nothing(kit: Kit) -> None:
    repo = kit.main_clone()
    binds = Binds([])
    plan = plan_run(host(kit, repo, binds))
    assert plan.refusal is None
    assert read_only(kit, plan, repo) == MAIN_GUARDED
    assert kit.rel(m.target for m in plan.mounts if not m.read_only) == MAIN_FOLDERS
    # The folder mounts needed compose's view once.
    assert binds.reads == 1
    # Each commondir names its own git folder, and the project folder for
    # compose.yaml's mount: each one planned, none made.
    assert dict(plan.make_files) == {
        f"{repo}/.git/commondir": "../.git\n",
        f"{repo}/.git/modules/lib/commondir": "../lib\n",
    }
    assert f"{kit.home}/.claude/projects/key" in plan.make_dirs
    assert not (repo / ".git/commondir").exists()
    assert not (kit.home / ".claude/projects").exists()
    # A planned file counts as guarded, so it is flagged once made.
    assert kit.rel(plan.flag_files) == sorted([
        "Main-Clone/.claude/worktrees/agent/.git",
        "Main-Clone/.git/commondir",
        "Main-Clone/.git/config",
        "Main-Clone/.git/modules/lib/commondir",
        "Main-Clone/.git/modules/lib/config",
        "Main-Clone/.git/worktrees/agent/commondir",
        "Main-Clone/.git/worktrees/other/commondir",
        "Main-Clone/.worktrees/other/.git",
    ])  # fmt: skip


def test_a_repo_with_no_read_only_mount_reads_no_compose_file(kit: Kit) -> None:
    repo = kit.src / "plain"
    repo.mkdir()
    binds = Binds(None)
    plan = plan_run(host(kit, repo, binds))
    assert plan.refusal is None
    assert binds.reads == 0
    assert plan.messages == ()


def test_a_failed_compose_config_is_named_for_its_purpose(kit: Kit) -> None:
    repo = kit.main_clone()
    plan = plan_run(host(kit, repo, Binds(None)))
    assert plan.messages == (
        "docker/cc: docker compose config failed, so a folder mount may hide a mount from a "
        "compose file",
    )


def test_a_hooks_folder_is_planned_and_not_made(kit: Kit) -> None:
    repo = kit.main_clone()
    kit.git("-C", repo, "config", "core.hooksPath", ".husky/_")
    plan = plan_run(host(kit, repo))
    assert plan.refusal is None
    other = repo / ".worktrees/other/.husky/_"
    assert str(other) in plan.make_dirs
    assert not other.exists()
    assert any(m.target == str(other) and m.read_only for m in plan.mounts)


def test_an_unnamed_nested_clone_is_refused_and_a_named_one_guarded(kit: Kit) -> None:
    repo = kit.main_clone()
    dep = kit.clone(repo / "vendor/dep")
    plan = plan_run(host(kit, repo))
    assert plan.refusal is not None
    assert plan.refusal.startswith(
        "docker/cc: these could lead git on the Mac to hooks or a config written inside"
    )
    assert f"\n  {dep / '.git'}\n" in plan.refusal

    # docker/cc.local can name it, as kit.toml can.
    plan = plan_run(host(kit, repo, local=overrides(nested_clones=("vendor/dep",))))
    assert plan.refusal is None
    assert "docker/cc: guarded nested clones: vendor/dep" in plan.messages
    assert any(m.target == str(dep / ".git/config") and m.read_only for m in plan.mounts)


def test_a_hooks_folder_it_cannot_guard_is_refused(kit: Kit) -> None:
    repo = kit.main_clone()
    kit.git("-C", repo, "config", "core.hooksPath", ".")
    plan = plan_run(host(kit, repo))
    assert plan.refusal is not None
    assert f"\n  {repo}: core.hooksPath = .\n" in plan.refusal


def test_setup_runs_start_commands_then_local_setup_then_the_program(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    plan = plan_run(
        host(
            kit,
            repo,
            settings=Settings("app-claude", start_commands=("make a", "make b")),
            local=overrides(local_setup="echo local"),
            program="bash",
            args=("-l",),
        )
    )
    assert plan.command == [
        "bash",
        "-c",
        '{ make a && make b; } && { echo local; } && exec bash "$@"',
        "bash",
        "-l",
    ]


def test_agent_browser_port_reaches_the_setup(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    plan = plan_run(host(kit, repo, settings=Settings("app-claude", agent_browser_port=9222)))
    assert "export AGENT_BROWSER_CDP=http://$mac_ip:9222\n" in plan.setup


def test_skipped_marketplace_stays_out(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    folder = kit.work / "mp"
    folder.mkdir()
    plugins = kit.home / ".claude/plugins"
    plugins.mkdir(parents=True)
    (plugins / "known_marketplaces.json").write_text(
        f'{{"mp": {{"source": {{"source": "directory", "path": "{folder}"}}}}}}'
    )
    assert any(m.source == str(folder) for m in plan_run(host(kit, repo)).mounts)
    plan = plan_run(host(kit, repo, local=overrides(skip_marketplaces=("mp",))))
    assert not any(m.source == str(folder) for m in plan.mounts)
    assert "docker/cc: skipped marketplaces: mp" in plan.messages
