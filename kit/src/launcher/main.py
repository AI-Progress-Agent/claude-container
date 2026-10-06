"""The launcher's entry point: docker/cc's commands."""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from launcher.clones import main_clone
from launcher.compose import (
    DEV_IMAGE,
    bind_targets,
    compose_command,
    dev_override_path,
    dev_override_text,
)
from launcher.environment import compose_env, gh_token, timezone, tmp_dir
from launcher.execute import exec_compose, execute, refuse_mac_write
from launcher.local import LocalError, LocalHelper
from launcher.output import refuse, say
from launcher.plan import Host, plan_run
from launcher.settings import SettingsError, load_settings

# The kit: this package's folder is src/launcher in it.
KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    docker_dir = os.environ.get("KIT_DOCKER_DIR", "")
    if not docker_dir:
        refuse("docker/cc: run docker/cc in a repo, not the launcher itself")
    repo = os.path.dirname(docker_dir)
    home = os.environ["HOME"]
    try:
        settings, warnings = load_settings(repo, docker_dir, main_clone(repo))
    except SettingsError as error:
        say(error.message)
        sys.stdout.write(error.output)
        return 1
    say(*warnings)
    env = compose_env(repo, settings.project_name, home)
    os.environ.update(env)
    dev = os.environ.get("KIT_DEV")
    override = None
    if dev:
        override = dev_override_path(home, settings.project_name)
        base = os.environ.get("KIT_BASE_IMAGE")
        if not base:
            refuse("docker/cc: the stub sets KIT_BASE_IMAGE")
        try:
            Path(override).parent.mkdir(parents=True, exist_ok=True)
            Path(override).write_text(dev_override_text(base))
        except OSError:
            refuse_mac_write(override)
    compose = compose_command(KIT, docker_dir, settings.project_name, override)

    program = "claude"
    match args[:1]:
        case ["build"]:
            _build_dev_base(dev)
            _update_stub(dev, docker_dir)
            exec_compose([*compose, "build", *args[1:]], os.environ)
        # A plain build reuses each cached layer, so nothing the repo's mise
        # config installs as "latest" ever moves. This skips the cache.
        # Claude and the base image's tools move only with a new base image.
        # On a fixed tag, move the FROM line to a new release, then build. On
        # the latest tag, the stub pulls the newest base image before this runs.
        case ["upgrade"]:
            _build_dev_base(dev)
            _update_stub(dev, docker_dir)
            exec_compose([*compose, "build", "--no-cache", *args[1:]], os.environ)
        case ["shell"]:
            program = "bash"
            args = args[1:]
        case _:
            pass

    # A run builds the image when it is missing. Under KIT_DEV, that build
    # needs the base image from the checkout.
    if dev and not _has_image(DEV_IMAGE):
        _build_dev_base(dev)

    os.environ["GH_TOKEN"] = gh_token()
    if not os.environ["GH_TOKEN"]:
        say("docker/cc: gh has no login on this machine, so git push and gh will fail inside")
    os.environ["TZ"] = timezone()

    cc_local = f"{docker_dir}/cc.local"
    try:
        helper, local = LocalHelper.start(
            KIT,
            cc_local if os.path.isfile(cc_local) else None,
            repo=repo,
            log=f"{tmp_dir()}/cc-notify.log",
            nested_clones=settings.nested_clones,
            args=args,
            env=os.environ,
        )
    except LocalError as error:
        say("docker/cc: docker/cc.local failed, so the container did not start")
        return error.returncode or 1

    # cc.local's exports are for compose and the container. Each git command
    # on the Mac runs with this shell's environment, as git run from this
    # shell does, so the guards see what that git sees.
    plan = plan_run(
        Host(
            repo=repo,
            repo_git=env["REPO_GIT"],
            repo_git_read_only=env["REPO_GIT_MODE"] == "ro",
            home=home,
            project_key=env["PROJECT_KEY"],
            settings=settings,
            local=local,
            compose_binds=lambda: bind_targets(compose, local.env),
            program=program,
            args=tuple(args),
        )
    )
    execute(plan, compose, helper)


def _build_dev_base(dev: str | None) -> None:
    """Builds the base image from the KIT_DEV checkout, when KIT_DEV is set."""
    if dev:
        result = subprocess.run(["docker", "build", "-t", DEV_IMAGE, dev], check=False)
        if result.returncode != 0:
            raise SystemExit(result.returncode)


def _update_stub(dev: str | None, docker_dir: str) -> None:
    """Writes the kit's stub over docker/cc when the two differ.

    The image ships the stub beside the launcher, so a repo's docker/cc
    follows the image its FROM line names. The new file goes in under a
    temporary name, then replaces docker/cc in one step. The stub's last
    line execs the launcher, so bash reads no more of the old file.

    Under KIT_DEV this does nothing: a checkout's stub may be work in
    progress. A stale stub still runs the launcher. So a failed read or
    write only warns, and the build goes on.
    """
    if dev:
        return
    source = f"{KIT}/stub/cc"
    # An image from before the kit held the stub.
    if not os.path.lexists(source):
        return
    target = f"{docker_dir}/cc"
    try:
        stub = Path(source).read_bytes()
        if Path(target).read_bytes() == stub:
            return
        _replace_executable(target, stub)
    except OSError:
        say(
            "docker/cc: could not update docker/cc to this image's stub, so the build goes on with it as it is"
        )
        return
    say("docker/cc: updated docker/cc to this image's stub; commit it")


def _replace_executable(path: str, data: bytes) -> None:
    """Writes data to a temporary file beside path, then renames it over path."""
    fd, tmp = tempfile.mkstemp(prefix=".cc.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.chmod(tmp, 0o755)
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _has_image(image: str) -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0
