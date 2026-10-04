"""The docker compose command, and compose's own view of the files."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from typing import cast

# The tag KIT_DEV builds its base image under.
DEV_IMAGE = "claude-container:dev"


def compose_command(
    kit: str, docker_dir: str, project_name: str, dev_override: str | None
) -> list[str]:
    """docker compose with the kit's compose file and the repo's.

    Relative paths in the repo's files resolve against its docker folder, as
    they would if the kit's file sat there too. -p names the project.
    Without it, a COMPOSE_PROJECT_NAME in your shell would win over
    compose.yaml's name, and two repos could share one login.
    """
    command = ["docker", "compose", "-p", project_name, "--project-directory", docker_dir]
    command += ["-f", f"{kit}/compose.yaml"]
    for name in ("compose.repo.yaml", "compose.local.yaml"):
        if os.path.isfile(f"{docker_dir}/{name}"):
            command += ["-f", f"{docker_dir}/{name}"]
    if dev_override is not None:
        command += ["-f", dev_override]
    return command


def dev_override_path(home: str, project_name: str) -> str:
    cache = os.environ.get("XDG_CACHE_HOME") or f"{home}/.cache"
    return f"{cache}/claude-container/dev/{project_name}.yaml"


def dev_override_text(base_image: str) -> str:
    """A compose file that builds the repo's image on the KIT_DEV base.

    A build context named after the image in the FROM line takes that
    image's place, so the FROM line, digest and all, stays as it is. The stub
    sets KIT_BASE_IMAGE to that image.
    """
    return (
        "services:\n  claude:\n    build:\n      additional_contexts:\n"
        f'        "{base_image}": docker-image://{DEV_IMAGE}\n'
    )


def bind_targets(compose: list[str], env: Mapping[str, str]) -> list[str] | None:
    """The path inside of each bind mount the compose files give the claude service.

    A named volume, such as the login's, holds none of the Mac's files, so it
    does not count. None when docker compose config fails.
    """
    result = subprocess.run(
        [*compose, "config", "--format", "json"],
        env=dict(env),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        config = cast(dict[str, object], json.loads(result.stdout))
        services = cast(dict[str, object], config.get("services") or {})
        claude = cast(dict[str, object], services.get("claude") or {})
        volumes = cast(list[dict[str, object]], claude.get("volumes") or [])
        return [str(v["target"]) for v in volumes if v.get("type") == "bind"]
    except ValueError, AttributeError, KeyError, TypeError:
        return None
