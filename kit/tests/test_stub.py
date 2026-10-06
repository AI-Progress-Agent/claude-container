"""The stub, stub/cc: it copies the image's kit once per tag and runs it.
On the latest tag, it copies the kit once per image ID, and deletes the
copies whose image the Mac no longer has.

Here each kit's launcher is a fake that prints which kit it is and what the
stub handed it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from harness import BASE_IMAGE, Kit

FAKE_LAUNCHER = """#!/usr/bin/env bash
echo "{name} $KIT_DOCKER_DIR $KIT_BASE_IMAGE ${{KIT_DEV:-}} $*"
"""


@pytest.fixture
def fake_kits(kit: Kit) -> tuple[Path, Path]:
    """An image's kit and a KIT_DEV checkout, each with a fake launcher."""
    image = kit.work / "image-kit"
    dev = kit.work / "dev"
    for name, folder in (("image", image), ("dev", dev / "kit")):
        folder.mkdir(parents=True)
        (folder / "cc").write_text(FAKE_LAUNCHER.format(name=name))
        (folder / "cc").chmod(0o755)
    kit.env["IMAGE_KIT"] = str(image)
    return image, dev


@pytest.mark.usefixtures("fake_kits")
def test_first_run_copies_the_images_kit_into_the_cache(kit: Kit) -> None:
    repo = kit.work / "repo"
    run = kit.run(repo, "shell", "--x")
    assert run.returncode == 0, run.stderr
    assert run.stdout == f"image {repo / 'docker'} {BASE_IMAGE}  shell --x\n"
    cached = kit.cache / "claude-container/v1.2.3"
    assert (cached / "cc").is_file()
    assert ["create", BASE_IMAGE] in [c.argv for c in run.calls]
    # No temporary copy is left beside it.
    assert [p.name for p in cached.parent.iterdir()] == ["v1.2.3"]


@pytest.mark.usefixtures("fake_kits")
def test_a_kit_without_its_launcher_is_replaced(kit: Kit) -> None:
    repo = kit.work / "repo"
    kit.run(repo)
    (kit.cache / "claude-container/v1.2.3/cc").unlink()
    run = kit.run(repo)
    assert run.stdout.startswith("image ")
    assert (kit.cache / "claude-container/v1.2.3/cc").is_file()


@pytest.mark.usefixtures("fake_kits")
def test_a_later_run_uses_the_cached_kit(kit: Kit) -> None:
    repo = kit.work / "repo"
    kit.run(repo)
    run = kit.run(repo)
    assert run.stdout.startswith("image ")
    assert run.calls == []


def test_kit_dev_runs_the_checkouts_launcher_by_its_absolute_path(
    kit: Kit, fake_kits: tuple[Path, Path]
) -> None:
    _, dev = fake_kits
    repo = kit.work / "repo"
    kit.install(repo)
    run = kit.run_command([Path("repo/docker/cc"), "build"], kit.work, {"KIT_DEV": "dev"})
    assert run.stdout == f"dev {repo / 'docker'} {BASE_IMAGE} {dev} build\n"


@pytest.mark.usefixtures("fake_kits")
def test_another_base_image_stops_the_stub_before_docker(kit: Kit) -> None:
    repo = kit.work / "repo"
    kit.install(repo, base_image="debian:bookworm-slim")
    run = kit.run(repo)
    assert run.returncode != 0
    assert "has no FROM ghcr.io/ai-progress-agent/claude-container:<tag> line" in run.stderr
    assert run.calls == []


LATEST = "ghcr.io/ai-progress-agent/claude-container:latest"


@pytest.mark.usefixtures("fake_kits")
def test_latest_caches_the_kit_by_the_images_id(kit: Kit) -> None:
    repo = kit.work / "repo"
    kit.install(repo, base_image=LATEST)
    (kit.fake / "image-id").write_text("sha256:aaaaaaaaaaaa1111\n")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert (kit.cache / "claude-container/latest-aaaaaaaaaaaa/cc").is_file()
    assert not any(c.argv[0] == "pull" for c in run.calls)


@pytest.mark.usefixtures("fake_kits")
def test_latest_pulls_when_the_image_is_missing(kit: Kit) -> None:
    repo = kit.work / "repo"
    kit.install(repo, base_image=LATEST)
    (kit.fake / "pulled-id").write_text("sha256:bbbbbbbbbbbb2222\n")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert ["pull", "-q", LATEST] in [c.argv for c in run.calls]
    assert (kit.cache / "claude-container/latest-bbbbbbbbbbbb/cc").is_file()


@pytest.mark.parametrize("command", ["build", "upgrade"])
@pytest.mark.usefixtures("fake_kits")
def test_latest_build_pulls_and_copies_the_new_images_kit(kit: Kit, command: str) -> None:
    repo = kit.work / "repo"
    kit.install(repo, base_image=LATEST)
    (kit.fake / "image-id").write_text("sha256:aaaaaaaaaaaa1111\n")
    kit.run(repo)
    (kit.fake / "pulled-id").write_text("sha256:bbbbbbbbbbbb2222\n")
    run = kit.run(repo, command)
    assert run.returncode == 0, run.stderr
    assert run.stdout.startswith(f"image {repo / 'docker'} {LATEST}  {command}")
    assert (kit.cache / "claude-container/latest-bbbbbbbbbbbb/cc").is_file()


@pytest.mark.usefixtures("fake_kits")
def test_latest_build_uses_the_local_image_when_the_pull_fails(kit: Kit) -> None:
    repo = kit.work / "repo"
    kit.install(repo, base_image=LATEST)
    (kit.fake / "image-id").write_text("sha256:aaaaaaaaaaaa1111\n")
    (kit.fake / "pull-fails").touch()
    run = kit.run(repo, "build")
    assert run.returncode == 0, run.stderr
    assert "could not pull" in run.stderr
    assert (kit.cache / "claude-container/latest-aaaaaaaaaaaa/cc").is_file()


@pytest.mark.parametrize("command", ["shell", "build", "upgrade"])
@pytest.mark.usefixtures("fake_kits")
def test_latest_fails_with_a_message_when_there_is_no_image_to_use(kit: Kit, command: str) -> None:
    repo = kit.work / "repo"
    kit.install(repo, base_image=LATEST)
    (kit.fake / "pull-fails").touch()
    run = kit.run(repo, command)
    assert run.returncode == 1
    assert f"could not pull {LATEST}, and there is no local copy" in run.stderr
    assert "uses the local copy" not in run.stderr


@pytest.mark.usefixtures("fake_kits")
def test_latest_deletes_the_kits_of_images_the_mac_no_longer_has(kit: Kit) -> None:
    repo = kit.work / "repo"
    kit.install(repo, base_image=LATEST)
    cache = kit.cache / "claude-container"
    for name in ("latest-aaaaaaaaaaaa", "latest-cccccccccccc", "v1.2.3"):
        (cache / name).mkdir(parents=True)
    # Another run's copy in progress, which the stub must leave alone.
    (cache / "latest-dddddddddddd.Xy12Ab").mkdir()
    (kit.fake / "kept-ids").write_text("sha256:cccccccccccc3333\n")
    (kit.fake / "pulled-id").write_text("sha256:bbbbbbbbbbbb2222\n")
    run = kit.run(repo, "build")
    assert run.returncode == 0, run.stderr
    assert sorted(p.name for p in cache.iterdir()) == [
        "latest-bbbbbbbbbbbb",
        "latest-cccccccccccc",
        "latest-dddddddddddd.Xy12Ab",
        "v1.2.3",
    ]


@pytest.mark.usefixtures("fake_kits")
def test_latest_keeps_the_other_kits_when_it_copies_none(kit: Kit) -> None:
    repo = kit.work / "repo"
    kit.install(repo, base_image=LATEST)
    (kit.fake / "image-id").write_text("sha256:aaaaaaaaaaaa1111\n")
    kit.run(repo)
    old = kit.cache / "claude-container/latest-cccccccccccc"
    old.mkdir()
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert old.is_dir()


@pytest.mark.usefixtures("fake_kits")
def test_latest_deletes_nothing_when_docker_cannot_list_images(kit: Kit) -> None:
    repo = kit.work / "repo"
    kit.install(repo, base_image=LATEST)
    old = kit.cache / "claude-container/latest-aaaaaaaaaaaa"
    old.mkdir(parents=True)
    (kit.fake / "image-ls-fails").touch()
    (kit.fake / "pulled-id").write_text("sha256:bbbbbbbbbbbb2222\n")
    run = kit.run(repo, "build")
    assert run.returncode == 0, run.stderr
    assert old.is_dir()
