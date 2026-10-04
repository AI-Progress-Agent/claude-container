"""What the tests that call the planning step directly share.

Each such test module uses the plan_env fixture from conftest.py.
"""

from __future__ import annotations

from pathlib import Path

from harness import Kit

from launcher.local import LocalOverrides
from launcher.plan import Host, RunPlan
from launcher.settings import Settings


class Binds:
    """Stands in for docker compose config, and counts the reads."""

    def __init__(self, targets: list[str] | None) -> None:
        self.targets = targets
        self.reads = 0

    def __call__(self) -> list[str] | None:
        self.reads += 1
        return self.targets


def host(
    kit: Kit,
    repo: Path,
    binds: Binds | None = None,
    *,
    settings: Settings | None = None,
    local: LocalOverrides | None = None,
    program: str = "claude",
    args: tuple[str, ...] = (),
) -> Host:
    return Host(
        repo=str(repo),
        # Each fixture starts at a clone's root, or outside git.
        repo_git=str(repo / ".git"),
        repo_git_read_only=False,
        home=str(kit.home),
        project_key="key",
        settings=settings or Settings("app-claude"),
        local=local or overrides(),
        compose_binds=binds or Binds([]),
        program=program,
        args=args,
    )


def overrides(
    *,
    local_setup: str = "true",
    skip_marketplaces: tuple[str, ...] = (),
    nested_clones: tuple[str, ...] = (),
) -> LocalOverrides:
    """What docker/cc.local set: nothing, by default."""
    return LocalOverrides(local_setup, skip_marketplaces, nested_clones, env={})


def read_only(kit: Kit, plan: RunPlan, repo: Path) -> list[str]:
    """The read-only mounts below repo, relative to kit.src, sorted."""
    return kit.rel(m.target for m in plan.mounts if m.read_only and m.target.startswith(f"{repo}/"))
