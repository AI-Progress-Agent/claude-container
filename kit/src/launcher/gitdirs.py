"""The read-only mounts of the files that lead git to a clone's hooks and config.

A linked worktree's .git file names its git folder, under worktrees/ in the
main clone's .git. That folder's commondir file names the main clone's .git.
Both sit in writable mounts otherwise. The container could then point git on
the Mac at a config of its own. That config would get past the read-only
mounts of hooks and config. So these files mount read-only for every worktree
of the clone, not only the one docker/cc runs from.

The main clone's .git gets the same guard as every git folder of its own: see
guard_git_dir. So does each submodule's git folder, kept under modules/ in the
main clone's .git. Git in the repo runs git in each submodule, so a config
written there runs on the Mac at git status. Each one's hooks and config mount
read-only too.

Each worktree's .git file mounts even where the worktree does not. Without
it, git inside takes a worktree it cannot see for a deleted one. Then git
worktree prune, or git gc, removes its git folder from the Mac's .git.
"""

from __future__ import annotations

import os

from launcher import git
from launcher.draft import Disk, Draft
from launcher.paths import absolute_path, children, is_under, real_dir


def collect_worktree_mounts(draft: Draft, folder: str) -> None:
    """Guards the clone that holds folder, by the path the container sees it at."""
    common = git.common_dir(folder)
    if common is None:
        return
    real = real_dir(folder) or ""
    for worktree in git.worktrees("-C", folder):
        file = f"{worktree}/.git"
        if os.path.isfile(file):
            add_git_file_mount(draft, file, real, folder)
    guard_git_dir(draft, common, real, folder)
    for module in submodule_git_dirs(common):
        guard_whole_git_dir(draft, module, real, folder)


def guard_clone(draft: Draft, clone: str) -> None:
    """Mounts read-only the clone's .git/hooks, .git/config and the files that lead git to them.

    A missing hooks folder would become an empty one the container could
    fill, so it is made on the Mac first.
    """
    draft.make_dir(f"{clone}/.git/hooks")
    draft.mount(f"{clone}/.git/hooks", read_only=True)
    draft.mount(f"{clone}/.git/config", read_only=True)
    collect_worktree_mounts(draft, clone)


def guard_git_dir(draft: Draft, git_dir: str, real: str, typed: str) -> None:
    """Guards git_dir, a git folder of its own, not a worktree's.

    It joins guarded_git_dirs, and the files in it that could send git
    elsewhere mount read-only. real and typed are as add_git_file_mount
    takes them.

    Each worktree's git folder under git_dir/worktrees has a commondir file
    that names git_dir. Each one mounts read-only.

    A commondir file in git_dir would send git to the hooks and config of the
    folder it names. A missing file cannot mount: Docker would make it empty,
    and git fails on an empty one. So a missing one is made on the Mac first,
    naming git_dir itself, which git reads as no commondir at all. It names
    git_dir as ../ and git_dir's own name, not as ".", because libgit2 cannot
    open a repo whose commondir is ".". One that names another folder, or is
    a link, does not mount, and the pointer search names it. The launcher
    does not write to the file a link names.

    With extensions.worktreeConfig on, git also reads config.worktree in
    git_dir, and in each worktree's git folder under git_dir/worktrees. Each
    one mounts read-only too. A missing one is made empty on the Mac first.
    Otherwise the container could make it in the writable mount.
    """
    draft.guarded_git_dirs.append(real_dir(git_dir) or "")
    for worktree in children(f"{git_dir}/worktrees"):
        file = f"{worktree}/commondir"
        if os.path.isfile(file):
            add_git_file_mount(draft, file, real, typed)
    commondir = f"{git_dir}/commondir"
    if not draft.disk.exists(commondir):
        draft.make_file(commondir, f"../{os.path.basename(git_dir)}")
    if commondir_names_itself(draft.disk, git_dir):
        add_git_file_mount(draft, commondir, real, typed)
    if git.worktree_config_on(git_dir):
        for folder in [git_dir, *children(f"{git_dir}/worktrees")]:
            if os.path.isdir(folder):
                file = f"{folder}/config.worktree"
                if not draft.disk.exists(file):
                    draft.make_file(file)
                add_git_file_mount(draft, file, real, typed)


def guard_whole_git_dir(draft: Draft, git_dir: str, real: str, typed: str) -> None:
    """Mounts read-only the hooks and config of git_dir, then guards it as guard_git_dir does.

    This is for a git folder that compose.yaml and guard_clone do not mount,
    such as a submodule's. A missing hooks folder would become an empty one
    the container could fill, so it is made on the Mac first. So is a missing
    config, as an empty file.
    """
    draft.make_dir(f"{git_dir}/hooks")
    if not draft.disk.exists(f"{git_dir}/config"):
        draft.make_file(f"{git_dir}/config")
    add_git_file_mount(draft, f"{git_dir}/hooks", real, typed)
    add_git_file_mount(draft, f"{git_dir}/config", real, typed)
    guard_git_dir(draft, git_dir, real, typed)


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


def add_git_file_mount(draft: Draft, file: str, real: str, typed: str) -> None:
    """Mounts file read-only at the real path git names it by.

    When file sits under real, the real path of typed, it also mounts at the
    same place under typed. That is the path the container writes it by.
    typed differs from real when it reaches the clone through a symbolic
    link, or in other letter case.
    """
    draft.mount(file, read_only=True)
    if typed != real and is_under(file, real):
        draft.mount(file, typed + file[len(real) :], read_only=True)


def commondir_names_itself(disk: Disk, git_dir: str) -> bool:
    """Whether git_dir's commondir is a file, not a link, and names git_dir itself."""
    file = f"{git_dir}/commondir"
    return not os.path.islink(file) and names_dir(disk, file, git_dir, git_dir)


def names_dir(disk: Disk, file: str, base: str, folder: str) -> bool:
    """Whether file, a commondir or a .git file, names folder.

    A relative name is relative to the folder base.
    """
    named = named_dir(disk, file, base)
    return named is not None and named == real_dir(folder)


def named_dir(disk: Disk, file: str, base: str) -> str | None:
    """The real path of the folder that file, a commondir or a .git file, names.

    A relative name is relative to the folder base. A .git file names its
    folder after "gitdir: ". None when file names no folder.
    """
    line = disk.first_line(file)
    if line is None:
        return None
    name = line.removeprefix("gitdir: ")
    if not name:
        return None
    return real_dir(absolute_path(name, base))


def submodule_git_dirs(common: str) -> list[str]:
    """The git folder of each submodule under the git folder common.

    These include each submodule's own submodules: each folder under a
    modules/ there that holds a HEAD file and an objects folder.
    """
    found: list[str] = []

    def walk(folder: str) -> None:
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            if entry.name == "objects":
                continue
            if entry.name == "HEAD" and entry.is_file(follow_symlinks=False):
                if os.path.isdir(f"{folder}/objects"):
                    found.append(folder)
            elif entry.is_dir(follow_symlinks=False):
                walk(f"{folder}/{entry.name}")

    for worktree in ["", *children(f"{common}/worktrees")]:
        walk(f"{worktree or common}/modules")
    return found
