"""Files that could lead git on the Mac to hooks or a config that the container wrote.

The repo and each writable sibling are searched, since the container can
write anywhere in them. A start refuses each one it finds. During a session,
the watcher moves each one aside.
"""

from __future__ import annotations

import contextlib
import json
import os
import random
from collections.abc import Callable
from dataclasses import dataclass

from launcher import git
from launcher.clones import has_git_dir_parts
from launcher.draft import Disk, Draft, Refused
from launcher.gitdirs import commondir_names_itself, named_dir, names_dir
from launcher.output import refusal
from launcher.paths import exists, is_under_any, real_dir
from launcher.reach import Reach


@dataclass(frozen=True)
class PointerScope:
    """Where to search, and what counts as guarded."""

    repo: str
    writable_dirs: tuple[str, ...]
    guarded_git_dirs: tuple[str, ...]
    # The files that mount read-only at their own paths.
    read_only_files: frozenset[str]
    # The writable mounts and the read-only mounts, for the hooks folders.
    reach: Reach

    @classmethod
    def of(cls, draft: Draft) -> PointerScope:
        return cls(
            repo=draft.repo,
            writable_dirs=tuple(draft.writable_dirs),
            guarded_git_dirs=tuple(draft.guarded_git_dirs),
            read_only_files=frozenset(
                m.source for m in draft.mounts if m.read_only and m.source == m.target
            ),
            reach=Reach.of(draft),
        )


def refuse_git_pointers(draft: Draft) -> None:
    """Refuses to start when _find_git_pointers finds a file.

    The file is already on the Mac, so the launcher names it and leaves it
    for you.
    """
    found = _find_git_pointers(PointerScope.of(draft), draft.disk)
    if found:
        raise Refused(
            refusal(
                "these could lead git on the Mac to hooks or a config written inside",
                found,
                "Delete each one. To keep a clone or a bare repo inside the repo or a writable "
                "sibling, add its path to nested_clones in docker/kit.toml or docker/cc.local.",
            )
        )


def block_git_pointers(scope: PointerScope, notify: Callable[[str], None]) -> list[str]:
    """Moves aside each path that _find_git_pointers finds, so git on the Mac stops reading it.

    The new name is <path>.cc-blocked. When that is taken, a number goes
    after it. A folder's HEAD moves aside too, or git would take the moved
    folder for a bare repo. Each move goes to notify as a Notification
    event's JSON. Returns each moved path.
    """
    blocked: list[str] = []
    for path in _find_git_pointers(scope, Disk()):
        to = move_aside(path)
        if to is None:
            continue
        if os.path.isdir(to) and not os.path.islink(to) and exists(f"{to}/HEAD"):
            with contextlib.suppress(OSError):
                os.rename(f"{to}/HEAD", f"{to}/HEAD.cc-blocked")
        blocked.append(path)
        notify(blocked_event(scope.repo, path))
    return blocked


def move_aside(path: str) -> str | None:
    """Renames path to <path>.cc-blocked, or with a number after that when it is taken.

    Returns the new name, or None when the rename fails.
    """
    to = f"{path}.cc-blocked"
    if exists(to):
        to = f"{to}-{random.randrange(1 << 30)}"
    try:
        os.rename(path, to)
    except OSError:
        return None
    return to


def blocked_event(repo: str, path: str) -> str:
    """The Notification event's JSON that tells notify_host the watcher moved path aside."""
    message = (
        f"docker/cc blocked {path}: it could lead git on the Mac to hooks or a config "
        "written inside"
    )
    event = {"hook_event_name": "Notification", "cwd": repo, "message": message}
    return json.dumps(event) + "\n"


def _find_git_pointers(scope: PointerScope, disk: Disk) -> list[str]:
    """Each path that could lead git on the Mac to hooks or a config that the container wrote.

    A path is one of:

      - a commondir in a guarded git folder that names another folder, or is
        a link
      - a .git, as a folder, a file or a link, that does not lead to a
        guarded git folder. See _leads_to_guarded.
      - the HEAD of a folder that git takes for a git folder, with no .git:
        see has_git_dir_parts. HEAD can be a link too. A HEAD inside a
        guarded git folder, such as a bare repo that nested_clones names, is
        left alone.

    The Mac's disk ignores case by default, so git takes .GIT for .git, and
    head for HEAD. The search ignores case too. It does not go into a .git,
    so it skips the git folders themselves, guarded or not. It does go into a
    .git that block_git_pointers moved aside. Git can still open a git folder
    in it, such as a submodule's, so a later search finds each one. The
    container can choose any name, so no name is skipped.
    """
    found: list[str] = []
    for git_dir in scope.guarded_git_dirs:
        commondir = f"{git_dir}/commondir"
        if disk.exists(commondir) and not commondir_names_itself(disk, git_dir):
            found.append(commondir)
    for path in _search(scope.repo, *scope.writable_dirs):
        if os.path.basename(path).lower() == ".git":
            if not _leads_to_guarded(scope, disk, path):
                found.append(path)
            continue
        folder = os.path.dirname(path)
        if has_git_dir_parts(folder) and not is_under_any(
            real_dir(folder) or "", scope.guarded_git_dirs
        ):
            found.append(path)
    return found


def _leads_to_guarded(scope: PointerScope, disk: Disk, path: str) -> bool:
    """Whether the .git at path leads git to a guarded git folder.

    It does when it is one, or names one. It also does when it names a
    worktree's git folder, <guarded>/worktrees/<name>, and both files git
    reads there are safe. Its commondir must name that guarded folder. Its
    config.worktree, read only with extensions.worktreeConfig on, must be
    empty or read-only. git worktree add inside makes such a worktree, so
    this lets one through.
    """
    if os.path.isdir(path):
        git_dir = real_dir(path)
    else:
        git_dir = named_dir(disk, path, os.path.dirname(path))
    if git_dir is None:
        return False
    if git_dir in scope.guarded_git_dirs:
        return True
    if os.path.basename(os.path.dirname(git_dir)) != "worktrees":
        return False
    common = os.path.dirname(os.path.dirname(git_dir))
    if common not in scope.guarded_git_dirs:
        return False
    if not names_dir(disk, f"{git_dir}/commondir", git_dir, common):
        return False
    config = f"{git_dir}/config.worktree"
    if not _has_content(config):
        return True
    return not git.worktree_config_on(common) or config in scope.read_only_files


def _search(*roots: str) -> list[str]:
    """Each .git and each HEAD file or link under roots, matched in any case.

    The search does not go into a .git, nor follow a link below a root.
    """
    found: list[str] = []

    def walk(folder: str) -> None:
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            path = f"{folder}/{entry.name}"
            name = entry.name.lower()
            if name == ".git":
                found.append(path)
                continue
            if name == "head" and (entry.is_file(follow_symlinks=False) or entry.is_symlink()):
                found.append(path)
            elif entry.is_dir(follow_symlinks=False):
                walk(path)

    for root in roots:
        walk(root)
    return found


def _has_content(path: str) -> bool:
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False
