"""The repo's settings, the compose command, the environment and the setup text."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from harness import BASE_IMAGE, KIT, Kit


def test_project_is_named_after_the_main_clone_in_lower_case(kit: Kit) -> None:
    repo = kit.main_clone()
    for where in (repo, repo / ".claude/worktrees/agent"):
        run = kit.run(where)
        assert run.returncode == 0, run.stderr
        compose = run.container.compose
        assert compose[compose.index("-p") + 1] == "main-clone-claude", where
        assert run.container.env["PROJECT_NAME"] == "main-clone-claude"


def test_project_name_takes_only_what_compose_accepts(kit: Kit) -> None:
    repo = kit.src / "My.App"
    repo.mkdir()
    run = kit.run(repo)
    assert run.container.env["PROJECT_NAME"] == "my-app-claude"


def test_defaults_run_nothing_before_claude(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    run = kit.run(repo)
    assert run.container.setup == '{ true; } && { true; } && exec claude "$@"'
    inside = kit.start_container(run.container)
    assert inside.returncode == 0, inside.stderr
    assert inside.links == []
    assert inside.cdp == "unset"


def test_arguments_reach_claude_unchanged(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    run = kit.run(repo, "--resume", "a b", "")
    assert run.container.service == "claude"
    assert run.container.program == ["claude", "--resume", "a b", ""]
    inside = kit.start_container(run.container)
    assert inside.program[1:] == ["--resume", "a b", ""]


def test_shell_runs_bash(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    run = kit.run(repo, "shell", "-l")
    assert run.container.program == ["bash", "-l"]
    assert run.container.setup.endswith('exec bash "$@"')


@pytest.mark.parametrize(("command", "extra"), [("build", []), ("upgrade", ["--no-cache"])])
def test_build_and_upgrade_build_the_image(kit: Kit, command: str, extra: list[str]) -> None:
    repo = kit.clone(kit.src / "app")
    run = kit.run(repo, command, "--pull")
    assert run.returncode == 0, run.stderr
    [call] = run.compose_calls("build")
    docker = repo / "docker"
    assert call.argv == [
        "compose", "-p", "app-claude", "--project-directory", str(docker),
        "-f", str(kit.cache / "claude-container/v1.2.3/compose.yaml"),
        "build", *extra, "--pull",
    ]  # fmt: skip


def test_compose_files_stack_kit_then_repo_then_yours(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    docker = kit.install(repo)
    (docker / "compose.local.yaml").write_text("")
    (docker / "compose.repo.yaml").write_text("")
    kit.settings(repo, project_name="other-claude")
    run = kit.run(repo)
    assert run.container.compose == [
        "docker", "compose", "-p", "other-claude", "--project-directory", str(docker),
        "-f", str(kit.cache / "claude-container/v1.2.3/compose.yaml"),
        "-f", str(docker / "compose.repo.yaml"),
        "-f", str(docker / "compose.local.yaml"),
    ]  # fmt: skip


def test_settings_override_every_default(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app", "git@github.com:org/app.git")
    kit.clone(kit.src / "plugins", "git@github.com:org/plugins.git")
    (repo / "agent-browser.json").write_text('{"cdp": "10099"}\n')
    kit.settings(
        repo,
        project_name="other-claude",
        writable_siblings=["plugins"],
        start_commands=["echo one >>start.log", "echo two >>start.log"],
        agent_browser_port=False,
    )
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert run.container.env["PROJECT_NAME"] == "other-claude"
    assert run.container.has_mount(kit.src / "plugins", ro=False)
    inside = kit.start_container(run.container)
    assert (kit.work / "inside/start.log").read_text() == "one\ntwo\n"
    assert inside.cdp == "unset"


def test_environment_for_compose(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    run = kit.run(repo)
    env = run.container.env
    zone = str(Path("/etc/localtime").readlink()).split("zoneinfo/", 1)[1]
    key = re.sub("[^A-Za-z0-9]", "-", str(repo))
    assert {k: env[k] for k in (
        "REPO", "REPO_GIT", "REPO_GIT_MODE", "HOST_HOME", "HOST_USER",
        "PROJECT_KEY", "GH_TOKEN", "TZ",
    )} == {
        "REPO": str(repo),
        "REPO_GIT": str(repo / ".git"),
        "REPO_GIT_MODE": "rw",
        "HOST_HOME": str(kit.home),
        "HOST_USER": os.environ.get("USER") or env["HOST_USER"],
        "PROJECT_KEY": key,
        "GH_TOKEN": "gho_fake",
        "TZ": zone,
    }  # fmt: skip
    # The project's memory folder exists before compose mounts it.
    assert (kit.home / ".claude/projects" / key).is_dir()


def test_repo_git_is_the_folder_with_the_hooks_and_config(kit: Kit) -> None:
    """From a worktree, it is the main clone's .git. Below the clone's root, it is read-only."""
    repo = kit.main_clone()
    (repo / "sub").mkdir()
    plain = kit.src / "My.App"
    plain.mkdir()
    for where, git, mode in (
        (repo, repo / ".git", "rw"),
        (repo / ".claude/worktrees/agent", repo / ".git", "rw"),
        (repo / "sub", repo / ".git", "ro"),
        (plain, plain / ".git", "rw"),
    ):
        env = kit.run(where).container.env
        assert (env["REPO_GIT"], env["REPO_GIT_MODE"]) == (str(git), mode), where


def test_no_gh_login_warns_and_starts(kit: Kit) -> None:
    (kit.fake / "gh-token").unlink()
    repo = kit.clone(kit.src / "app")
    run = kit.run(repo)
    assert run.returncode == 0
    assert run.container.env["GH_TOKEN"] == ""
    assert (
        "docker/cc: gh has no login on this machine, so git push and gh will fail inside"
        in run.lines()
    )


def test_launcher_refuses_to_run_outside_a_repo(kit: Kit) -> None:
    run = kit.run_command([KIT / "cc"], kit.work)
    assert run.returncode != 0
    assert "run docker/cc in a repo, not the launcher itself" in run.stderr
    assert run.calls == []


def test_base_image_reaches_kit_dev_override(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    run = kit.run(repo, env={"KIT_DEV": str(KIT.parent)})
    assert run.returncode == 0, run.stderr
    compose = run.container.compose
    override = compose[-1]
    assert compose[-2] == "-f"
    assert override == str(kit.cache / "claude-container/dev/app-claude.yaml")
    text = Path(override).read_text()
    assert f'        "{BASE_IMAGE}": docker-image://claude-container:dev\n' in text


def test_kit_dev_builds_a_missing_base_image_first(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    dev = {"KIT_DEV": str(KIT.parent)}
    builds = ["build", "-t", "claude-container:dev", str(KIT.parent)]
    run = kit.run(repo, env=dev)
    assert builds not in [c.argv for c in run.calls]
    (kit.fake / "no-dev-image").touch()
    run = kit.run(repo, env=dev)
    assert builds in [c.argv for c in run.calls]
    assert run.started


def test_kit_dev_build_builds_the_base_image_then_the_repos(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    run = kit.run(repo, "build", env={"KIT_DEV": str(KIT.parent)})
    assert run.returncode == 0, run.stderr
    assert run.calls[0].argv == ["build", "-t", "claude-container:dev", str(KIT.parent)]
    assert len(run.compose_calls("build")) == 1


# agent-browser drives the Mac's Chrome on the cdp port.


@pytest.mark.parametrize(
    "text",
    [
        '{\n  "cdp": "10099"\n}\n',
        '{"cdp":"10099","contentBoundary":true}\n',
        '{"cdp": 10099}\n',
    ],
)
def test_agent_browser_port_comes_from_agent_browser_json(kit: Kit, text: str) -> None:
    repo = kit.clone(kit.src / "app")
    (repo / "agent-browser.json").write_text(text)
    run = kit.run(repo)
    inside = kit.start_container(run.container)
    assert inside.returncode == 0, inside.stderr
    assert inside.cdp == "http://192.168.65.254:10099"


def test_agent_browser_json_with_no_port_warns_and_starts(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    (repo / "agent-browser.json").write_text("{}\n")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert (
        "docker/cc: agent-browser.json names no cdp port, so agent-browser drives nothing on the Mac"
        in run.lines()
    )
    assert kit.start_container(run.container).cdp == "unset"


def test_settings_set_the_agent_browser_port(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    (repo / "agent-browser.json").write_text("{}\n")
    kit.settings(repo, agent_browser_port=9222)
    run = kit.run(repo)
    assert "agent-browser.json names no cdp port" not in run.stderr
    assert kit.start_container(run.container).cdp == "http://192.168.65.254:9222"


def test_unresolved_mac_warns_inside_and_starts_claude(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    kit.settings(repo, agent_browser_port=9222)
    inside = kit.start_container(kit.run(repo).container, mac_ip=None)
    assert inside.returncode == 0
    assert inside.cdp == "unset"
    assert inside.program == ["claude"]
    assert (
        "docker/cc: host.docker.internal does not resolve, so agent-browser cannot reach the Mac's Chrome"
        in inside.stderr
    )


# The host's Claude and git config.


def test_host_config_mounts_read_only_at_its_own_path(kit: Kit) -> None:
    home = kit.home
    dotfiles = kit.work / "dotfiles"
    for path in (
        dotfiles / "git",
        dotfiles / "claude",
        dotfiles / "skills",
        home / ".claude",
        home / "bin",
    ):
        path.mkdir(parents=True)
    (home / ".claude/CLAUDE.md").write_text("")
    (home / ".claude/settings.json").write_text("{}")
    (dotfiles / "claude/settings.local.json").write_text("{}")
    (home / ".claude/settings.local.json").symlink_to(dotfiles / "claude/settings.local.json")
    (home / ".claude/skills").symlink_to(dotfiles / "skills")
    (dotfiles / "git/gitconfig").write_text("")
    (dotfiles / "git/gitconfig.local").write_text("")
    (home / ".gitconfig").symlink_to(dotfiles / "git/gitconfig")
    (home / ".gitconfig.local").symlink_to(dotfiles / "git/gitconfig.local")
    repo = kit.clone(kit.src / "app")
    run = kit.run(repo)
    host = [
        (m.source, m.target)
        for m in run.container.mounts
        if m.read_only and not m.source.startswith(str(kit.src))
    ]
    # A file that is a link mounts by the folder its target is in, so an
    # editor's rename there reaches the container. Two links into one folder
    # share its mount.
    assert host == [
        (str(home / ".claude/CLAUDE.md"), str(home / ".claude/CLAUDE.md")),
        (str(home / ".claude/settings.json"), str(home / ".claude/settings.json")),
        (str(dotfiles / "claude"), "/mnt/host-links/0"),
        (str(home / ".claude/skills"), str(home / ".claude/skills")),
        (str(home / "bin"), str(home / "bin")),
        (str(dotfiles / "git"), "/mnt/host-links/1"),
    ]
    inside = kit.start_container(run.container)
    assert inside.links == [
        [
            "-sfn",
            "/mnt/host-links/0/settings.local.json",
            str(home / ".claude/settings.local.json"),
        ],
        ["-sfn", "/mnt/host-links/1/gitconfig", str(home / ".gitconfig")],
        ["-sfn", "/mnt/host-links/1/gitconfig.local", str(home / ".gitconfig.local")],
    ]


# docker/cc.local: your own settings.


def test_cc_local_exports_and_setup_and_arguments(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    kit.settings(repo, start_commands=["echo start >>start.log"])
    kit.cc_local(
        repo,
        'export MY_VAR="from cc.local"\n'
        "local_setup='echo local >>start.log'\n"
        'printf "%s\\n" "$@" >"$HOME/cc-local.args"\n',
    )
    run = kit.run(repo, "shell", "-x", "y z")
    assert run.returncode == 0, run.stderr
    assert run.container.env["MY_VAR"] == "from cc.local"
    assert (kit.home / "cc-local.args").read_text() == "-x\ny z\n"
    inside = kit.start_container(run.container)
    assert inside.returncode == 0, inside.stderr
    assert (kit.work / "inside/start.log").read_text() == "start\nlocal\n"
    assert inside.program == ["bash", "-x", "y z"]
