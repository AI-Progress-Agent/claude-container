"""The read-only mount of the folder that core.hooksPath names in each worktree.

Only a folder inside a writable mount mounts. Git on the Mac runs hooks from
there, so a hook written inside would run on the Mac. Husky's .gitignore in
the folder leaves out every file, so git status would not show it. The
worktrees are those of each guarded git folder: the repo's, each writable
sibling's, each nested clone's and each submodule's. The value comes from any
config git reads, the global one among them.

Git resolves a relative value in the worktree, and in a bare repo's git
folder. A missing folder is made empty on the Mac first, and stays after the
session. A folder that is already read-only, such as .git/hooks, needs nothing
new. Nor does a folder inside another worktree's hooks folder. The folder
mounts then mount each folder above it.

The launcher cannot guard some folders. It names the worktree and the value
of each one, and the container does not start. Each one is:

  - a writable mount, or a folder that holds one
  - inside a writable mount, and holds its worktree or a read-only mount
  - reached through a symbolic link inside a writable mount. The container
    could point the link elsewhere.
  - reached through a .. out of a folder inside a writable mount. The
    container could swap that folder for a link.
  - a path through a file, which cannot be a folder

It decides on every worktree before it plans a folder.

During the session, the watcher reads core.hooksPath again in each worktree.
A worktree made inside, or one whose folder was missing at start, has no
read-only mount of its hooks folder. Nor does a folder that an
includeIf "onbranch:" section names once the container switches branch. So
the watcher moves each such folder inside a writable mount aside, as it moves
a .git. A folder the launcher could not guard at start stays where it is.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from launcher import git
from launcher.draft import Draft, Refused
from launcher.gitdirs import guarded_worktrees
from launcher.output import refusal
from launcher.paths import absolute_path, first_link, nearest_is_dir, outermost
from launcher.pointers import PointerScope, blocked_event, move_aside
from launcher.reach import Place, Reach


def collect_hooks_paths(draft: Draft) -> None:
    reach = Reach.of(draft)
    refused: list[str] = []
    hook_dirs: list[Place] = []
    for worktree in draft.worktrees:
        value = git.git("-C", worktree, "config", "core.hooksPath")
        # Git refuses an empty value, so it runs no hook.
        if not value:
            continue
        folder = hooks_folder(worktree)
        place = None if folder is None else reach.place(folder, [worktree])
        if folder is not None and place is None:
            continue
        if place is None or place.mount is None or not nearest_is_dir(place.path):
            refused.append(f"{worktree}: core.hooksPath = {value}")
            continue
        hook_dirs.append(place)
    if refused:
        raise Refused(
            refusal(
                "core.hooksPath names a folder the launcher cannot mount read-only",
                refused,
                "Point core.hooksPath at a folder below the worktree's root, "
                "with no symbolic link or .. on the way. Or unset it.",
            )
        )
    # A folder inside another needs nothing new. So the order of the
    # worktrees does not matter.
    places = {place.path: place for place in hook_dirs}
    for folder in outermost(places):
        draft.make_dir(folder)
        reach.guard(draft, places[folder])


def block_hooks_folders(scope: PointerScope, notify: Callable[[str], None]) -> list[str]:
    """Moves aside each hooks folder inside a writable mount that has no read-only mount.

    The worktrees are those of each guarded git folder now, not at start. A
    folder may not hold any of them.

    The container can make a folder that the launcher cannot guard, such as
    a link, or a folder that holds a worktree. Then the first link on the
    way moves aside, and git on the Mac finds no folder. With no link, the
    worktree's .git file moves aside. Git on the Mac then no longer takes the
    folder for a worktree. A .git folder never moves.

    Each move goes to notify, as block_git_pointers sends it. Returns each
    moved path.
    """
    worktrees = guarded_worktrees(list(scope.guarded_git_dirs))
    blocked: list[str] = []
    for worktree in worktrees:
        if not git.git("-C", worktree, "config", "core.hooksPath"):
            continue
        folder = hooks_folder(worktree)
        if folder is None:
            continue
        place = scope.reach.place(folder, worktrees)
        if place is None:
            continue
        if place.mount is not None:
            path = place.path if os.path.isdir(place.path) else None
        else:
            path = first_link(folder, scope.reach.real_roots) or _git_file(worktree)
        if path is not None and move_aside(path) is not None:
            blocked.append(path)
            notify(blocked_event(scope.repo, path))
    return blocked


def hooks_folder(worktree: str) -> str | None:
    """The absolute path of the folder that core.hooksPath names for worktree.

    None when git cannot expand the value.
    """
    value = git.git("-C", worktree, "config", "--type=path", "core.hooksPath")
    if value is None:
        return None
    return absolute_path(value, worktree)


def _git_file(worktree: str) -> str | None:
    """The worktree's .git when it is a file, not a link nor a folder."""
    path = f"{worktree}/.git"
    return path if os.path.isfile(path) and not os.path.islink(path) else None
