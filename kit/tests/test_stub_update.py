"""build and upgrade keep a repo's docker/cc in step with the image's stub.

The image ships the stub at stub/cc in the kit. When docker/cc differs from
it, build and upgrade write it over docker/cc. A stale docker/cc still runs
the launcher, so it never stops a build.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from harness import STUB, Kit

STUB_UPDATED = "docker/cc: updated docker/cc to this image's stub; commit it"
STUB_NOT_UPDATED = "docker/cc: could not update docker/cc to this image's stub, so the build goes on with it as it is"
STALE = STUB.read_text() + "# an older stub\n"


@pytest.mark.parametrize("command", ["build", "upgrade"])
def test_build_writes_the_images_stub_over_a_stale_docker_cc(kit: Kit, command: str) -> None:
    repo, stub = _repo_with_stale_stub(kit)
    run = kit.run(repo, command)
    assert run.returncode == 0, run.stderr
    assert STUB_UPDATED in run.lines()
    assert stub.read_bytes() == STUB.read_bytes()
    assert stub.stat().st_mode & 0o777 == 0o755
    assert len(run.compose_calls("build")) == 1


def test_build_leaves_a_matching_docker_cc_alone(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    stub = kit.install(repo) / "cc"
    before = stub.stat()
    run = kit.run(repo, "build")
    assert run.returncode == 0, run.stderr
    assert "docker/cc" not in run.stderr
    assert stub.stat().st_ino == before.st_ino
    assert stub.stat().st_mtime_ns == before.st_mtime_ns


@pytest.mark.parametrize("args", [[], ["shell"]])
def test_a_run_never_touches_docker_cc(kit: Kit, args: list[str]) -> None:
    repo, stub = _repo_with_stale_stub(kit)
    run = kit.run(repo, *args)
    assert run.returncode == 0, run.stderr
    assert STUB_UPDATED not in run.lines()
    assert stub.read_text() == STALE


@pytest.mark.parametrize("args", [[], ["shell"], ["build"], ["upgrade"]])
def test_kit_dev_never_touches_docker_cc(kit: Kit, image_kit: Path, args: list[str]) -> None:
    """A checkout's stub may be work in progress.

    The checkout here holds a kit with its own stub/cc, so only KIT_DEV
    keeps the launcher from writing it.
    """
    dev = kit.work / "dev"
    shutil.copytree(image_kit, dev / "kit")
    repo, stub = _repo_with_stale_stub(kit)
    run = kit.run(repo, *args, env={"KIT_DEV": str(dev)})
    assert run.returncode == 0, run.stderr
    assert STUB_UPDATED not in run.lines()
    assert stub.read_text() == STALE


def test_a_kit_without_a_stub_leaves_docker_cc_alone(kit: Kit, image_kit: Path) -> None:
    """An image from before the stub shipped in the kit."""
    older = kit.work / "older-kit"
    shutil.copytree(image_kit, older, ignore=shutil.ignore_patterns("stub"))
    kit.env["IMAGE_KIT"] = str(older)
    repo, stub = _repo_with_stale_stub(kit)
    run = kit.run(repo, "build")
    assert run.returncode == 0, run.stderr
    assert "docker/cc" not in run.stderr
    assert stub.read_text() == STALE
    assert len(run.compose_calls("build")) == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the folder's mode")
def test_a_docker_cc_the_launcher_cannot_write_warns_and_builds(kit: Kit) -> None:
    repo, stub = _repo_with_stale_stub(kit)
    docker = stub.parent
    docker.chmod(0o555)
    try:
        run = kit.run(repo, "build")
    finally:
        docker.chmod(0o755)
    assert run.returncode == 0, run.stderr
    assert STUB_NOT_UPDATED in run.lines()
    assert stub.read_text() == STALE
    # No temporary file is left beside it.
    assert sorted(p.name for p in docker.iterdir()) == ["Dockerfile", "cc"]
    assert len(run.compose_calls("build")) == 1


def test_a_kit_stub_the_launcher_cannot_read_warns_and_builds(kit: Kit, image_kit: Path) -> None:
    broken = kit.work / "broken-kit"
    shutil.copytree(image_kit, broken, ignore=shutil.ignore_patterns("stub"))
    (broken / "stub" / "cc").mkdir(parents=True)
    kit.env["IMAGE_KIT"] = str(broken)
    repo, stub = _repo_with_stale_stub(kit)
    run = kit.run(repo, "build")
    assert run.returncode == 0, run.stderr
    assert STUB_NOT_UPDATED in run.lines()
    assert stub.read_text() == STALE
    assert len(run.compose_calls("build")) == 1


def _repo_with_stale_stub(kit: Kit) -> tuple[Path, Path]:
    """A repo whose docker/cc differs from the image's stub, and that docker/cc."""
    repo = kit.clone(kit.src / "app")
    stub = kit.install(repo) / "cc"
    stub.write_text(STALE)
    return repo, stub
