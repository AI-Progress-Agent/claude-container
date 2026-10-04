"""docker/kit.toml, the kit.sh it replaces, and the uv that runs the launcher."""

from __future__ import annotations

import tomllib

import pytest
from harness import Kit


def test_without_uv_the_launcher_names_how_to_install_it(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    run = kit.run(repo, env={"PATH": kit.path_without("uv")})
    assert run.returncode == 1
    assert run.stderr == "docker/cc: needs uv; run `brew install uv`\n"
    assert not run.started


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("start_command = []\n", "unknown key start_command"),
        ('start_commands = "make"\n', "start_commands must be a list of strings"),
        ("nested_clones = [1]\n", "nested_clones must be a list of strings"),
        ('writable_siblings = "plugins"\n', "writable_siblings must be a list of strings"),
        ("agent_browser_port = true\n", "agent_browser_port must be a port number, or false"),
        ('agent_browser_port = "9222"\n', "agent_browser_port must be a port number, or false"),
        ("agent_browser_port = 70000\n", "agent_browser_port must be a port number, or false"),
        ('project_name = "My App"\n', "project_name must be a string of lower-case letters"),
    ],
)
def test_a_wrong_setting_stops_the_start_and_names_the_file_and_key(
    kit: Kit, text: str, problem: str
) -> None:
    repo = kit.clone(kit.src / "app")
    toml = kit.install(repo) / "kit.toml"
    toml.write_text(text)
    run = kit.run(repo)
    assert run.returncode == 1
    assert run.stderr.startswith(f"docker/cc: {toml}: {problem}")
    assert not run.started


def test_kit_toml_that_is_no_toml_stops_the_start(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    toml = kit.install(repo) / "kit.toml"
    toml.write_text("project_name = \n")
    run = kit.run(repo)
    assert run.returncode == 1
    assert run.stderr.startswith(f"docker/cc: {toml} does not read as TOML: ")
    assert not run.started


def test_kit_sh_stops_the_start_and_prints_its_kit_toml(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app", "git@github.com:org/app.git")
    kit.clone(kit.src / "plugins", "git@github.com:org/plugins.git")
    kit.clone(repo / "vendor/app-sdk")
    (repo / "agent-browser.json").write_text('{"cdp": "9222"}\n')
    docker = kit.install(repo)
    # As a repo's kit.sh was written: bash arrays, commands added to the
    # default, the repo's own path, and agent-browser turned off.
    (docker / "kit.sh").write_text(
        "project_name=example-claude\n"
        "writable_siblings=(plugins)\n"
        "start_commands=\"$start_commands && echo one >>start.log && echo 'two' >>start.log\"\n"
        'nested_clones=("vendor/${repo##*/}-sdk")\n'
        "agent_browser_port=\n"
    )
    run = kit.run(repo)
    assert run.returncode == 1
    assert not run.started
    assert (
        f"docker/cc: the launcher reads docker/kit.toml now, not {docker / 'kit.sh'}, "
        "so the container did not start. "
        f"Save the lines below as {docker / 'kit.toml'}, then delete kit.sh."
    ) in run.lines()
    assert tomllib.loads(run.stdout) == {
        "project_name": "example-claude",
        "writable_siblings": ["plugins"],
        "start_commands": ["true && echo one >>start.log && echo 'two' >>start.log"],
        "nested_clones": ["vendor/app-sdk"],
        "agent_browser_port": False,
    }

    # Saved, it starts with the same settings.
    (docker / "kit.toml").write_text(run.stdout)
    (docker / "kit.sh").unlink()
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert run.container.env["PROJECT_NAME"] == "example-claude"
    assert run.container.has_mount(kit.src / "plugins", ro=False)
    assert "docker/cc: guarded nested clones: vendor/app-sdk" in run.lines()
    inside = kit.start_container(run.container)
    assert (kit.work / "inside/start.log").read_text() == "one\ntwo\n"
    assert inside.cdp is None


def test_kit_sh_that_sets_only_defaults_asks_for_an_empty_kit_toml(kit: Kit) -> None:
    """A setting left at its default stays out of kit.toml."""
    repo = kit.clone(kit.src / "app")
    (repo / "agent-browser.json").write_text('{"cdp": "9222"}\n')
    docker = kit.install(repo)
    (docker / "kit.sh").write_text(
        "project_name=app-claude\nstart_commands=true\nnested_clones=()\nagent_browser_port=9222\n"
    )
    run = kit.run(repo)
    assert run.returncode == 1
    assert run.stdout == ""
    assert (
        f"docker/cc: the launcher reads docker/kit.toml now, not {docker / 'kit.sh'}, "
        f"so the container did not start. kit.sh sets only defaults: make {docker / 'kit.toml'} "
        "an empty file, then delete kit.sh."
    ) in run.lines()


def test_kit_toml_wins_over_a_kit_sh_beside_it(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    kit.settings(repo, project_name="from-toml")
    (repo / "docker/kit.sh").write_text("project_name=from-sh\n")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert run.container.env["PROJECT_NAME"] == "from-toml"
