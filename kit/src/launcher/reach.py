"""Where the container can write, and where a path that git on the Mac reads sits against it.

A path that git on the Mac reads or runs needs a read-only mount when it sits
inside a writable mount. The launcher cannot guard some paths. Each one is:

  - a writable mount, or a folder that holds one
  - inside a writable mount, and holds a folder the caller names, such as its
    worktree, or a read-only mount
  - reached through a symbolic link inside a writable mount. The container
    could point the link elsewhere.
  - reached through a .. out of a folder inside a writable mount. The
    container could swap that folder for a link.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from launcher.draft import Draft
from launcher.gitdirs import add_git_file_mount
from launcher.paths import follow_links, holds_any, is_under, is_under_any, real_dir


@dataclass(frozen=True)
class Place:
    """Where a path sits inside a writable mount."""

    # The path with each symbolic link in it followed, or as given when the
    # launcher cannot follow it.
    path: str
    # The index in Reach.real_roots of the writable mount it sits in. None
    # when the launcher cannot guard it.
    root: int | None


@dataclass(frozen=True)
class Reach:
    # Each writable mount as the draft names it, and its real path at the
    # same index.
    typed_roots: tuple[str, ...]
    real_roots: tuple[str, ...]
    # The target of each read-only mount, and its real path where that
    # differs.
    read_only: tuple[str, ...]

    @classmethod
    def of(cls, draft: Draft) -> Reach:
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
        # compose.yaml mounts these two.
        real = real_dir(draft.repo_git)
        if real is not None:
            read_only += [f"{real}/hooks", f"{real}/config"]
        return cls(tuple(typed_roots), tuple(real_roots), tuple(read_only))

    def place(self, path: str, holders: Iterable[str]) -> Place | None:
        """Where the absolute path sits. None when it needs no guard.

        It needs none outside every writable mount, or inside a read-only
        mount. The Place has no root when the launcher cannot guard it, or
        when it holds one of holders.
        """
        followed = follow_links(path, self.real_roots)
        if followed is None or holds_any(followed, self.real_roots):
            return Place(followed or path, None)
        root = next((i for i, r in enumerate(self.real_roots) if is_under(followed, r)), None)
        if root is None or is_under_any(followed, self.read_only):
            return None
        if holds_any(followed, [*holders, *self.read_only]):
            return Place(followed, None)
        return Place(followed, root)

    def guard(self, draft: Draft, place: Place) -> None:
        """Mounts place's path read-only, as add_git_file_mount does."""
        assert place.root is not None
        add_git_file_mount(
            draft, place.path, self.real_roots[place.root], self.typed_roots[place.root]
        )
