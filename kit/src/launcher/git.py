"""The git commands the launcher reads the repos with. Each one changes nothing."""

from __future__ import annotations

import subprocess


def git(*args: str) -> str | None:
    """git's output without its trailing newlines, or None when git fails."""
    result = subprocess.run(
        ["git", *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="surrogateescape",
        check=False,
    )
    return result.stdout.rstrip("\n") if result.returncode == 0 else None


def common_dir(path: str) -> str | None:
    """The git folder shared by path's clone and all its worktrees: the main clone's .git.

    Git prints it by its real path. None outside git.
    """
    return git("-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir")


def worktrees(*args: str) -> list[str]:
    """The path of each worktree that `git ARGS worktree list` lists."""
    out = git(*args, "worktree", "list", "--porcelain") or ""
    return [
        line.removeprefix("worktree ") for line in out.split("\n") if line.startswith("worktree ")
    ]


def worktree_config_on(git_dir: str) -> bool:
    """Whether the config of git_dir turns on extensions.worktreeConfig.

    Git then also reads each config.worktree.
    """
    value = git("config", "-f", f"{git_dir}/config", "--bool", "extensions.worktreeConfig")
    return value == "true"
