"""The host's Claude and git config, and the folders above each read-only mount."""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable

from launcher.draft import Draft
from launcher.paths import is_under, parent

# Host config to mirror, each read-only at its own path inside. A path this
# machine does not have is left out, so any Claude setup runs as it is. Links
# resolve only if their targets are mounted too: mount a dotfiles repo in
# docker/compose.local.yaml. marketplaces.py mounts the folders that plugin
# marketplaces are read from.
HOST_CONFIG = (
    ".claude/CLAUDE.md",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/output-styles",
    ".claude/skills",
    ".claude/agents",
    ".claude/commands",
    ".claude/hooks",
    # installed_plugins.json names each plugin by its absolute host path.
    ".claude/plugins",
    # Where the skills CLI installs shared skills.
    ".agents",
    # Scripts the status line and hooks call by name. The image puts ~/bin
    # last on PATH.
    "bin",
    ".config/ccstatusline",
    ".gitconfig",
    ".gitconfig.local",
    ".config/git",
)


def collect_host_mounts(draft: Draft, home: str) -> str:
    """Mounts the host config, and returns the shell text that recreates each mounted link inside.

    A file mounted on its own is pinned to the copy that existed at start. An
    editor that saves by writing a new copy and renaming it over the old one
    leaves the container reading the deleted original, and Claude drops every
    setting in it without a word. Nothing fixes that for a plain file in
    $HOME. A file that is a link, into a dotfiles repo say, can be fixed:
    mount the folder its target is in, which follows the rename, and recreate
    the link inside, pointing there. Those folders mount under
    /mnt/host-links, not at their own paths, so they never collide with a
    mount in compose.local.yaml.
    """
    link_dirs: list[str] = []
    links = ""
    for path in HOST_CONFIG:
        source = f"{home}/{path}"
        if not os.path.exists(source):
            continue
        if os.path.islink(source) and os.path.isfile(source):
            target = os.path.realpath(source)
            folder = os.path.dirname(target)
            if folder not in link_dirs:
                link_dirs.append(folder)
                draft.mount(folder, f"/mnt/host-links/{len(link_dirs) - 1}", read_only=True)
            inside = f"/mnt/host-links/{link_dirs.index(folder)}/{os.path.basename(target)}"
            links += f"ln -sfn {shlex.quote(inside)} {shlex.quote(source)} && "
        else:
            draft.mount(source, read_only=True)
    return links


def collect_folder_mounts(draft: Draft, compose_binds: Callable[[str], list[str]]) -> None:
    """Mounts writable, at its own path, each folder above a read-only mount inside a writable mount.

    Linux lets the container rename a folder that holds a mount. The mount
    moves with the folder. The container could then make a new folder at the
    old path, with its own config or hooks. Git on the Mac would read them.
    Linux refuses to rename a mount point. So each folder from the writable
    mount's root down to the read-only mount gets a mount of its own. Writes
    inside each one still work.

    The read-only mounts are those in the draft. Those that compose.yaml adds
    sit directly in the repo's git folder, a mount point, so they need no
    folder mounts. A folder that is already a mount point needs no new one.
    That includes a folder that a compose file mounts: a second mount there
    would hide it. compose_binds gives those. The launcher can mount a
    read-only file a second time, at the repo's path as typed. Then that
    copy's folders mount at that path too, because the writable repo mounts
    there.
    """
    writable = draft.writable_mounts()
    mount_points = [draft.repo, draft.repo_git, *(m.target for m in draft.mounts)]
    folders: list[str] = []
    for mount in draft.mounts:
        if not mount.read_only:
            continue
        roots = [w for w in writable if mount.target != w and is_under(mount.target, w)]
        if not roots:
            continue
        root = max(roots, key=len)
        folder = parent(mount.target)
        while folder != root:
            folders.append(folder)
            folder = parent(folder)
    if not folders:
        return
    mount_points += compose_binds("a folder mount may hide a mount from a compose file")
    for folder in folders:
        if folder in mount_points:
            continue
        draft.mount(folder, read_only=False)
        mount_points.append(folder)
