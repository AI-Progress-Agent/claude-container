"""The folders that plugin marketplaces are read from on the Mac.

Each folder marketplace mounts read-only at its own path, unless the
container already sees it or skip_marketplaces names it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from harness import Kit


def marketplace(path: Path, *, install_location: bool = True) -> dict[str, object]:
    entry: dict[str, object] = {"source": {"source": "directory", "path": str(path)}}
    if install_location:
        entry["installLocation"] = str(path)
    return entry


GITHUB = {"source": {"source": "github", "repo": "o/r"}, "installLocation": "/x"}


def write_known(kit: Kit, entries: dict[str, object]) -> None:
    plugins = kit.home / ".claude/plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    (plugins / "known_marketplaces.json").write_text(json.dumps(entries))


def marketplace_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if "marketplace" in line]


@pytest.fixture
def repo(kit: Kit) -> Path:
    """A repo with one read-only sibling, and no worktree."""
    kit.clone(kit.src / "sibling", "git@github.com:org/sibling.git", commit=False)
    return kit.clone(kit.src / "repo", "git@github.com:org/repo.git")


def test_only_folders_the_container_does_not_see_mount(kit: Kit, repo: Path) -> None:
    mp = kit.work / "mp"
    paths = {
        "in-repo": repo / "plugins",
        "in-sibling": kit.src / "sibling/m",
        "in-compose": kit.work / "dotfiles/m",
        # The login's volume holds none of the Mac's files.
        "in-volume": kit.home / ".claude/m",
        "a-nested": mp / "mine/inner",
        "mine": mp / "mine",
        "client": mp / "client",
        "legacy": mp / "legacy",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    gone = mp / "gone"
    # Named so that a sort by name would put the nested folder before its
    # parent.
    write_known(
        kit,
        {
            "github": GITHUB,
            **{name: marketplace(path) for name, path in paths.items() if name != "legacy"},
            "gone": marketplace(gone),
            # Recorded with no installLocation, it mounts from its source path.
            "legacy": marketplace(paths["legacy"], install_location=False),
        },
    )
    kit.compose_binds(kit.work / "dotfiles")
    kit.cc_local(repo, "skip_marketplaces=(client)\n")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    candidates = {str(p) for p in [*paths.values(), gone]}
    mounted = [m for m in run.container.mounts if m.source in candidates]
    assert all(m.read_only and m.source == m.target for m in mounted)
    assert [m.source for m in mounted] == [
        str(paths["in-volume"]),
        str(paths["legacy"]),
        str(paths["mine"]),
    ]
    assert marketplace_lines(run.stderr) == [
        f"docker/cc: marketplace gone has no folder at {gone} on the Mac, so its plugins will not load",
        "docker/cc: mounted marketplaces read-only: in-volume legacy mine",
        "docker/cc: skipped marketplaces: client",
    ]
    [config] = run.compose_calls("config")
    assert config.argv[-3:] == ["config", "--format", "json"]


def test_with_nothing_to_mount_the_compose_files_are_not_read(kit: Kit, repo: Path) -> None:
    client = kit.work / "mp/client"
    client.mkdir(parents=True)
    write_known(kit, {"client": marketplace(client)})
    kit.cc_local(repo, "skip_marketplaces=(client)\n")
    run = kit.run(repo)
    assert not run.container.has_mount(client, ro=True)
    assert run.compose_calls("config") == []


def test_marketplaces_mount_without_jq(kit: Kit, repo: Path) -> None:
    mine = kit.work / "mp/mine"
    mine.mkdir(parents=True)
    write_known(kit, {"mine": marketplace(mine)})
    run = kit.run(repo, env={"PATH": kit.path_without("jq")})
    assert run.returncode == 0, run.stderr
    assert run.container.has_mount(mine, ro=True)
    assert marketplace_lines(run.stderr) == ["docker/cc: mounted marketplaces read-only: mine"]


def test_without_either_file_nothing_mounts_and_nothing_prints(kit: Kit, repo: Path) -> None:
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert marketplace_lines(run.stderr) == []


def test_unreadable_json_mounts_nothing(kit: Kit, repo: Path) -> None:
    write_known(kit, {})
    known = kit.home / ".claude/plugins/known_marketplaces.json"
    known.write_text("{")
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert marketplace_lines(run.stderr) == [
        f"docker/cc: {known} or {kit.home / '.claude/settings.json'} does not read as JSON, "
        "so no folder marketplace mounted"
    ]


def test_an_empty_file_reads_as_no_marketplaces(kit: Kit, repo: Path) -> None:
    mine = kit.work / "mp/mine"
    mine.mkdir(parents=True)
    write_known(kit, {})
    (kit.home / ".claude/plugins/known_marketplaces.json").write_text("")
    (kit.home / ".claude/settings.json").write_text(
        json.dumps({"extraKnownMarketplaces": {"mine": marketplace(mine)}})
    )
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert run.container.has_mount(mine, ro=True)
    assert marketplace_lines(run.stderr) == ["docker/cc: mounted marketplaces read-only: mine"]


def test_settings_json_entry_wins_when_known_marketplaces_lags(kit: Kit, repo: Path) -> None:
    """A path may hold a backslash or a tab."""
    src = kit.work / "mp"
    odd = src / "back\\slash\ttab"
    for path in (src / "new", src / "moved", src / "old", odd):
        path.mkdir(parents=True)
    write_known(kit, {"moving": marketplace(src / "old")})
    (kit.home / ".claude/settings.json").write_text(
        json.dumps(
            {
                "extraKnownMarketplaces": {
                    "new": {"source": {"source": "directory", "path": str(src / "new")}},
                    "moving": {"source": {"source": "directory", "path": str(src / "moved")}},
                    "odd": {"source": {"source": "directory", "path": str(odd)}},
                    "remote": {"source": {"source": "github", "repo": "o/r"}},
                }
            }
        )
    )
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    candidates = {str(p) for p in (src / "new", src / "moved", src / "old", odd)}
    assert [m.source for m in run.container.mounts if m.source in candidates] == [
        str(odd),
        str(src / "moved"),
        str(src / "new"),
    ]


def test_when_compose_config_fails_the_marketplace_still_mounts(kit: Kit, repo: Path) -> None:
    mine = kit.work / "mp/mine"
    mine.mkdir(parents=True)
    write_known(kit, {"mine": marketplace(mine)})
    (kit.fake / "compose-config-fails").touch()
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert run.container.has_mount(mine, ro=True)
    assert (
        "docker/cc: docker compose config failed, so a marketplace that a compose file mounts "
        "may mount twice"
    ) in run.lines()
