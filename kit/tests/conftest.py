from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from harness import KIT, NOT_IN_IMAGE, Kit


@pytest.fixture(scope="session")
def image_kit(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The kit as the image holds it at /opt/kit."""
    dest = tmp_path_factory.mktemp("image") / "kit"
    shutil.copytree(KIT, dest, ignore=shutil.ignore_patterns(*NOT_IN_IMAGE))
    return dest


@pytest.fixture
def kit(tmp_path: Path, image_kit: Path) -> Kit:
    # On the Mac, the temporary folder sits behind a link, and git prints the
    # real path.
    return Kit(tmp_path.resolve(), image_kit)


@pytest.fixture
def plan_env(kit: Kit, monkeypatch: pytest.MonkeyPatch) -> None:
    """For a test that calls the planning step: git reads the test's own home, not this machine's config."""
    for name in list(os.environ):
        monkeypatch.delenv(name)
    for name, value in kit.env.items():
        monkeypatch.setenv(name, value)
