from __future__ import annotations

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
