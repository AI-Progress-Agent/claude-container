"""The repo's settings: the kit's defaults, then docker/kit.toml, which can change any of them.

  project_name        names the compose project, and so the image and the
                      volumes, the login among them. The main clone's folder
                      name plus -claude, by default. Two repos with one name
                      would share a login and an image.
  writable_siblings   the sibling repos that mount writable. None, by
                      default. See clones.collect_sibling_repos.
  start_commands      shell commands run inside at each start, joined with
                      &&, before docker/cc.local's local_setup. None, by
                      default.
  nested_clones       the clones and bare repos inside the repo kept on
                      purpose, by their paths in the repo. None, by default.
                      A clone inside a writable sibling goes by its absolute
                      path. docker/cc.local can add more. See
                      clones.collect_nested_clones.
  agent_browser_port  the port of the Mac's Chrome that agent-browser drives.
                      The cdp port in the repo's agent-browser.json, by
                      default. false, agent-browser drives nothing on the
                      Mac.

A key the launcher does not know, or a value of the wrong type, stops the
launch. The message names the file and the key.

docker/kit.sh held the same settings before kit.toml. A repo that has kit.sh
and no kit.toml stops too, and the message gives the kit.toml to write.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from typing import TypeIs, cast

KEYS = (
    "project_name",
    "writable_siblings",
    "start_commands",
    "nested_clones",
    "agent_browser_port",
)

# What compose takes as a project name.
_PROJECT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")
_CDP = re.compile(r'"cdp"\s*:\s*"?([0-9]+)')


@dataclass(frozen=True)
class Settings:
    project_name: str
    writable_siblings: tuple[str, ...] = ()
    start_commands: tuple[str, ...] = ()
    nested_clones: tuple[str, ...] = ()
    # None turns agent-browser off.
    agent_browser_port: int | None = None


class SettingsError(Exception):
    """The settings cannot be read. message says why, and output is what to print on stdout."""

    def __init__(self, message: str, output: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.output = output


def load_settings(repo: str, docker_dir: str, main_clone: str) -> tuple[Settings, list[str]]:
    """The repo's settings, and the warnings to print. Raises SettingsError."""
    # Compose takes lower-case letters, digits, dashes and underscores only.
    name = re.sub(r"[^a-z0-9_-]", "-", os.path.basename(main_clone).lower())
    defaults = Settings(project_name=f"{name}-claude")
    browser = f"{repo}/agent-browser.json"
    browser_port = _cdp_port(browser) if os.path.isfile(browser) else None
    toml = f"{docker_dir}/kit.toml"
    if os.path.isfile(toml):
        values = _read_toml(toml)
    elif os.path.isfile(f"{docker_dir}/kit.sh"):
        raise _kit_sh_refusal(repo, docker_dir, defaults.project_name, browser_port)
    else:
        values = {}
    warnings: list[str] = []
    if "agent_browser_port" in values:
        port = values["agent_browser_port"]
    else:
        port = browser_port
        if os.path.isfile(browser) and port is None:
            warnings.append(
                "docker/cc: agent-browser.json names no cdp port, "
                "so agent-browser drives nothing on the Mac"
            )
    return (
        Settings(
            project_name=cast(str, values.get("project_name", defaults.project_name)),
            writable_siblings=_strings(values.get("writable_siblings")),
            start_commands=_strings(values.get("start_commands")),
            nested_clones=_strings(values.get("nested_clones")),
            agent_browser_port=port if _is_port(port) else None,
        ),
        warnings,
    )


def _read_toml(path: str) -> dict[str, object]:
    """kit.toml's settings, each checked."""
    try:
        with open(path, "rb") as file:
            data = cast(dict[str, object], tomllib.load(file))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SettingsError(f"docker/cc: {path} does not read as TOML: {error}") from error
    for key, value in data.items():
        problem = _setting_problem(key, value)
        if problem:
            raise SettingsError(f"docker/cc: {path}: {problem}")
    return data


def _setting_problem(key: str, value: object) -> str | None:
    """What is wrong with one setting, or None."""
    match key:
        case "project_name":
            if not isinstance(value, str) or not _PROJECT_NAME.fullmatch(value):
                return (
                    "project_name must be a string of lower-case letters, digits, dashes and "
                    "underscores that starts with a letter or digit"
                )
        case "writable_siblings" | "start_commands" | "nested_clones":
            if not isinstance(value, list) or not all(
                isinstance(v, str) for v in cast(list[object], value)
            ):
                return f"{key} must be a list of strings"
        case "agent_browser_port":
            if value is not False and not _is_port(value):
                return "agent_browser_port must be a port number, or false"
        case _:
            return f"unknown key {key}. The keys are {', '.join(KEYS)}"
    return None


def _is_port(value: object) -> TypeIs[int]:
    # TOML's true and false are bools, and a bool is an int in Python.
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 65536


def _strings(value: object) -> tuple[str, ...]:
    return tuple(cast(list[str], value)) if isinstance(value, list) else ()


def _cdp_port(path: str) -> int | None:
    """The cdp port that agent-browser.json names."""
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as file:
            match = _CDP.search(file.read())
    except OSError:
        return None
    return int(match[1]) if match else None


# Sets the bash launcher's defaults, then sources kit.sh inside a function,
# as that launcher did. Then it prints each setting. A setting is its name,
# list or text, the count of values, then each value. Each field ends in a
# NUL byte. What kit.sh prints goes to stderr, so it stays out of the report.
_KIT_SH_REPORT = r"""
repo=$1 docker_dir=$2
load_repo_config() {
  project_name=$3
  writable_siblings=()
  start_commands=true
  nested_clones=()
  agent_browser_port=$4
  . "$docker_dir/kit.sh" >&2
}
load_repo_config "$@"
for name in project_name writable_siblings start_commands nested_clones agent_browser_port; do
  declaration=$(declare -p "$name" 2>/dev/null) || continue
  eval "values=(\${$name[@]+\"\${$name[@]}\"})"
  case $declaration in "declare -a"*) kind=list ;; *) kind=text ;; esac
  printf '%s\0' "$name" "$kind" "${#values[@]}" ${values[@]+"${values[@]}"}
done
"""

# Stand-ins for the two defaults that come from the clone. Neither is a value
# kit.sh would set, so a setting that kit.sh pins to this clone's default
# still differs from its stand-in.
_STAND_IN_NAME = "\x01project_name"
_STAND_IN_PORT = "\x01agent_browser_port"

type _KitShSettings = dict[str, tuple[str, list[str]]]


def _kit_sh_refusal(
    repo: str, docker_dir: str, project_name: str, browser_port: int | None
) -> SettingsError:
    """The refusal for a repo with docker/kit.sh and no docker/kit.toml.

    It sources kit.sh in bash, and gives the kit.toml with the same settings.
    A setting that kit.sh leaves at its default stays out. A project_name or
    agent_browser_port that kit.sh sets stays in, even when it matches this
    clone's default: another clone's default can differ.
    """
    kit_sh = f"{docker_dir}/kit.sh"
    port = "" if browser_port is None else str(browser_port)
    try:
        settings = _source_kit_sh(repo, docker_dir, project_name, port)
        stand_in = _source_kit_sh(repo, docker_dir, _STAND_IN_NAME, _STAND_IN_PORT)
    except SettingsError as error:
        return SettingsError(
            f"docker/cc: the launcher reads docker/kit.toml now, not {kit_sh}, "
            "and bash could not source kit.sh to convert it, so the container did not start:\n"
            + error.message
        )
    defaults = _kit_sh_defaults(project_name, port)
    stand_in_defaults = _kit_sh_defaults(_STAND_IN_NAME, _STAND_IN_PORT)
    lines = [
        f"{name} = {_toml_value(name, kind, values)}"
        for name, (kind, values) in settings.items()
        if (kind, values) != defaults.get(name) or stand_in.get(name) != stand_in_defaults.get(name)
    ]
    toml = f"{docker_dir}/kit.toml"
    if not lines:
        return SettingsError(
            f"docker/cc: the launcher reads docker/kit.toml now, not {kit_sh}, "
            f"so the container did not start. kit.sh sets only defaults: make {toml} "
            "an empty file, then delete kit.sh."
        )
    return SettingsError(
        f"docker/cc: the launcher reads docker/kit.toml now, not {kit_sh}, "
        f"so the container did not start. Save the lines below as {toml}, then delete kit.sh.",
        "".join(f"{line}\n" for line in lines),
    )


def _source_kit_sh(repo: str, docker_dir: str, project_name: str, port: str) -> _KitShSettings:
    """Each setting after kit.sh runs on the given defaults, by name.

    Raises SettingsError with bash's stderr when kit.sh fails.
    """
    result = subprocess.run(
        ["bash", "-c", _KIT_SH_REPORT, "kit.sh", repo, docker_dir, project_name, port],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SettingsError(os.fsdecode(result.stderr).rstrip("\n"))
    fields = [os.fsdecode(f) for f in result.stdout.split(b"\0")]
    settings: _KitShSettings = {}
    i = 0
    while i + 2 < len(fields):
        name, kind, count = fields[i], fields[i + 1], int(fields[i + 2])
        settings[name] = (kind, fields[i + 3 : i + 3 + count])
        i += 3 + count
    return settings


def _kit_sh_defaults(project_name: str, port: str) -> _KitShSettings:
    return {
        "project_name": ("text", [project_name]),
        "writable_siblings": ("list", []),
        "start_commands": ("text", ["true"]),
        "nested_clones": ("list", []),
        "agent_browser_port": ("text", [port]),
    }


def _toml_value(name: str, kind: str, values: list[str]) -> str:
    """One kit.sh setting as a TOML value.

    A JSON string, or a JSON list of strings, reads the same in TOML.
    """
    text = values[0] if values else ""
    match name:
        case "project_name":
            return json.dumps(text, ensure_ascii=False)
        case "agent_browser_port":
            if not text:
                return "false"
            return text if text.isdigit() else json.dumps(text, ensure_ascii=False)
        case "start_commands" if kind == "text":
            # The old string becomes one entry. true runs nothing.
            return json.dumps([] if text in ("", "true") else [text], ensure_ascii=False)
        case _:
            return json.dumps(values, ensure_ascii=False)
