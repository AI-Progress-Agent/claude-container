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
"""

from __future__ import annotations

import os

from launcher import git
from launcher.draft import Draft, Refused
from launcher.gitdirs import add_git_file_mount
from launcher.output import refusal
from launcher.paths import absolute_path, follow_links, holds_any, is_under, is_under_any, real_dir


def collect_hooks_paths(draft: Draft) -> None:
    typed_roots: list[str] = []
    real_roots: list[str] = []
    for folder in draft.writable_mounts():
        real = real_dir(folder)
        if real is not None:
            typed_roots.append(folder)
            real_roots.append(real)
    read_only: list[str] = []
    for mount in draft.mounts:
        if mount.read_only:
            read_only.append(mount.target)
            real = real_dir(mount.target)
            if real is not None and real != mount.target:
                read_only.append(real)
    # compose.yaml mounts this one.
    real = real_dir(draft.repo_git)
    if real is not None:
        read_only.append(f"{real}/hooks")

    refused: list[str] = []
    hook_dirs: list[tuple[str, int]] = []
    for worktree in guarded_worktrees(draft.guarded_git_dirs):
        value = git.git("-C", worktree, "config", "core.hooksPath")
        # Git refuses an empty value, so it runs no hook.
        if not value:
            continue
        folder = hooks_folder(worktree, real_roots)
        if folder is None or holds_any(folder, real_roots):
            refused.append(f"{worktree}: core.hooksPath = {value}")
            continue
        root = next((i for i, r in enumerate(real_roots) if is_under(folder, r)), None)
        if root is None or is_under_any(folder, read_only):
            continue
        if holds_any(folder, [worktree, *read_only]) or not nearest_is_dir(folder):
            refused.append(f"{worktree}: core.hooksPath = {value}")
            continue
        hook_dirs.append((folder, root))
    if refused:
        raise Refused(
            refusal(
                "core.hooksPath names a folder the launcher cannot mount read-only",
                refused,
                "Point core.hooksPath at a folder below the worktree's root, "
                "with no symbolic link or .. on the way. Or unset it.",
            )
        )
    # Sorted, a folder comes before each folder inside it, which then needs
    # nothing new. So the order of the worktrees does not matter.
    mounted: list[str] = []
    for folder, root in sorted(hook_dirs, key=lambda d: f"{d[0]}\t{d[1]}"):
        if is_under_any(folder, mounted):
            continue
        mounted.append(folder)
        draft.make_dir(folder)
        add_git_file_mount(draft, folder, real_roots[root], typed_roots[root])


def hooks_folder(worktree: str, guarded: list[str]) -> str | None:
    """The folder that core.hooksPath names for worktree, as follow_links gives it.

    None when git cannot expand the value, or when follow_links fails.
    """
    value = git.git("-C", worktree, "config", "--type=path", "core.hooksPath")
    if value is None:
        return None
    return follow_links(absolute_path(value, worktree), guarded)


def guarded_worktrees(git_dirs: list[str]) -> list[str]:
    """The real path of each worktree of each git folder in git_dirs.

    A bare repo has no worktree, so its git folder stands in for one: git
    resolves a relative core.hooksPath there. A worktree whose folder is
    missing is left out.

    A submodule's git folder can list itself for its checkout, which its
    core.worktree names, relative to it. Git ignores core.worktree there once
    the folder has a commondir, so this reads it.
    """
    found: list[str] = []
    for git_dir in git_dirs:
        for worktree in git.worktrees(f"--git-dir={git_dir}"):
            if worktree == git_dir:
                named = git.git("config", "-f", f"{git_dir}/config", "core.worktree")
                if named is not None:
                    worktree = absolute_path(named, git_dir)
            real = real_dir(worktree)
            if real is not None:
                found.append(real)
    return found


def nearest_is_dir(path: str) -> bool:
    """Whether the deepest part of path that exists is a folder."""
    while not os.path.lexists(path):
        path = os.path.dirname(path)
    return os.path.isdir(path)
