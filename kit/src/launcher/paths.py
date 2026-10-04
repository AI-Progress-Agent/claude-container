"""Paths as git and the Mac's disk name them.

Paths here are strings, not pathlib paths. A path keeps the spelling it was
given, a trailing name of ".." or a link included, until a function here
resolves it on purpose.
"""

from __future__ import annotations

import fcntl
import os
import sys
from collections.abc import Iterable


def is_under(path: str, root: str) -> bool:
    """Whether path is root, or sits under it."""
    return path == root or path.startswith(root + "/")


def is_under_any(path: str, roots: Iterable[str]) -> bool:
    return any(is_under(path, root) for root in roots)


def holds_any(folder: str, paths: Iterable[str]) -> bool:
    """Whether one of paths is folder, or sits under it."""
    return any(is_under(path, folder) for path in paths)


def absolute_path(path: str, base: str) -> str:
    """path, made absolute against the folder base when relative."""
    return path if path.startswith("/") else f"{base}/{path}"


def parent(path: str) -> str:
    """path without its last name. The parent of "/a" is "", not "/"."""
    return path.rpartition("/")[0]


def exists(path: str) -> bool:
    """Whether something is at path: a file, a folder or a link, even a link to nothing."""
    return os.path.lexists(path)


def real_dir(path: str) -> str | None:
    """The real path of the folder path, each name in it spelled as on disk.

    That is how git prints it. The Mac's disk ignores case, and resolving the
    links alone keeps the case path was given in. None when path is no folder.
    """
    if not os.path.isdir(path):
        return None
    if sys.platform == "darwin":
        try:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        except OSError:
            return os.path.realpath(path)
        try:
            spelled = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024))
        finally:
            os.close(fd)
        return os.fsdecode(spelled.partition(b"\0")[0])
    return os.path.realpath(path)


def nearest_is_dir(path: str) -> bool:
    """Whether the deepest part of path that exists is a folder."""
    while not os.path.lexists(path):
        path = os.path.dirname(path)
    return os.path.isdir(path)


def outermost(paths: Iterable[str]) -> list[str]:
    """Each of paths that sits under none of the others, sorted, once each."""
    found: list[str] = []
    # Sorted, a folder comes before each path inside it.
    for path in sorted(set(paths)):
        if not is_under_any(path, found):
            found.append(path)
    return found


def first_link(path: str, roots: Iterable[str]) -> str | None:
    """The first part of the absolute path, as spelled, that is a symbolic link under one of roots."""
    roots = list(roots)
    current = ""
    for part in path.split("/"):
        if not part:
            continue
        current = f"{current}/{part}"
        if os.path.islink(current) and is_under_any(current, roots):
            return current
    return None


def children(folder: str) -> list[str]:
    """The path of each entry in folder, sorted, as a shell's * lists them."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return [f"{folder}/{name}" for name in sorted(names) if not name.startswith(".")]


def follow_links(path: str, guarded: Iterable[str]) -> str | None:
    """path with each symbolic link in it followed, as the kernel follows them.

    Each folder that exists is spelled as on disk, which may differ in case
    from path. path is absolute. A part of it may be missing. None when a
    link sits at or under one of guarded, when a .. steps out of a folder
    under one of them, or when links loop.
    """
    roots = list(guarded)
    rest = path
    current = ""
    hops = 0
    while rest:
        part, _, rest = rest.partition("/")
        if part in ("", "."):
            continue
        if part == "..":
            if is_under_any(parent(current), roots):
                return None
            current = parent(current)
            continue
        step = f"{current}/{part}"
        if os.path.islink(step):
            if is_under_any(step, roots):
                return None
            hops += 1
            if hops > 40:
                return None
            target = os.readlink(step)
            if target.startswith("/"):
                current = ""
            rest = f"{target}/{rest}"
        elif os.path.isdir(step):
            real = real_dir(step)
            if real is None:
                return None
            current = real
        else:
            current = step
    return current or "/"
