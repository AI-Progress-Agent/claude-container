"""The variables compose.yaml reads. The image is built for this user and home path, so a teammate building it gets their own."""

from __future__ import annotations

import os
import pwd
import re
import subprocess

from launcher import git


def compose_env(repo: str, project_name: str, home: str) -> dict[str, str]:
    repo_git = repo_git_dir(repo)
    # Below the clone's root, git inside stops at the repo's mount and never
    # finds the clone's .git. Nothing inside needs to write it, so it mounts
    # read-only there.
    prefix = git.git("-C", repo, "rev-parse", "--show-prefix")
    return {
        "PROJECT_NAME": project_name,
        "REPO": repo,
        # Not GIT_COMMON_DIR: git reads that name, and would then send every
        # git command here to this repo.
        "REPO_GIT": repo_git,
        "REPO_GIT_MODE": "ro" if prefix else "rw",
        "HOST_HOME": home,
        "HOST_USER": pwd.getpwuid(os.getuid()).pw_name,
        # Claude names a project's folder under ~/.claude/projects after its
        # path, with every character that is not a letter or digit turned
        # into a dash.
        "PROJECT_KEY": re.sub(r"[^A-Za-z0-9]", "-", repo),
    }


def repo_git_dir(repo: str) -> str:
    """The git folder that holds the repo's hooks and config.

    In the main clone or outside git, that is the repo's own .git. It keeps
    the repo's path here, where git inside looks for it. A linked worktree's
    .git is a file that points into the main clone's .git, so this gives the
    main clone's .git. That path has no symbolic links, as the worktree's
    .git file names it.
    """
    if not os.path.isdir(f"{repo}/.git"):
        common = git.common_dir(repo)
        if common is not None:
            return common
    return f"{repo}/.git"


def gh_token() -> str:
    """The token of gh's login, or "".

    gh on macOS keeps its token in the keychain. The container gets the token
    itself. compose.yaml copies it from the launcher's environment, which
    keeps it off the command line where `ps` would show it.
    """
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return result.stdout.rstrip("\n") if result.returncode == 0 else ""


def timezone() -> str:
    """The Mac's time zone, so the container's clock matches it.

    /etc/localtime links into the zone database, and the part after
    zoneinfo/ is the zone's name.
    """
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        return ""
    return target.rpartition("/zoneinfo/")[2]


def tmp_dir() -> str:
    """The Mac's TMPDIR, the user's own folder, the same in every shell."""
    return os.environ.get("TMPDIR") or "/tmp"
