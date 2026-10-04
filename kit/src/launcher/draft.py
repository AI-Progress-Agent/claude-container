"""The plan while the planning step builds it, and the disk as the plan will leave it."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from launcher.paths import exists


@dataclass(frozen=True)
class Mount:
    """One bind mount for docker compose run's -v: source:target, or source:target:ro."""

    source: str
    target: str
    read_only: bool

    @property
    def arg(self) -> str:
        return f"{self.source}:{self.target}" + (":ro" if self.read_only else "")


class Refused(Exception):  # it names what happened, as SystemExit does
    """The launcher cannot keep the Mac safe, so the container must not start."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class Disk:
    """The Mac's disk as it will be once the plan's folders and files are made.

    The planning step makes nothing. A later step reads a folder or file the
    plan will make as though it were there.
    """

    folders: list[str] = field(default_factory=list[str])
    files: dict[str, str] = field(default_factory=dict[str, str])

    def exists(self, path: str) -> bool:
        return path in self.files or path in self.folders or exists(path)

    def is_file(self, path: str) -> bool:
        return path in self.files or os.path.isfile(path)

    def first_line(self, path: str) -> str | None:
        """The file's first line without its newline, or None when it cannot be read."""
        if path in self.files:
            return self.files[path].partition("\n")[0]
        try:
            with open(path, encoding="utf-8", errors="surrogateescape") as file:
                return file.readline().removesuffix("\n")
        except OSError:
            return None


@dataclass
class Draft:
    """What the planning step has found so far."""

    repo: str
    repo_git: str
    repo_git_read_only: bool
    disk: Disk = field(default_factory=Disk)
    mounts: list[Mount] = field(default_factory=list[Mount])
    # Each git folder of its own that the launcher guards, by its real path.
    guarded_git_dirs: list[str] = field(default_factory=list[str])
    # The writable siblings.
    writable_dirs: list[str] = field(default_factory=list[str])
    # What to print before the container starts.
    messages: list[str] = field(default_factory=list[str])

    def mount(self, source: str, target: str | None = None, *, read_only: bool) -> None:
        self.mounts.append(Mount(source, target or source, read_only))

    def make_dir(self, path: str) -> None:
        """Makes the folder path on the Mac, and each missing folder above it."""
        if path not in self.disk.folders:
            self.disk.folders.append(path)

    def make_file(self, path: str, line: str | None = None) -> None:
        """Makes the file path on the Mac, holding line, or empty."""
        self.disk.files[path] = "" if line is None else f"{line}\n"

    def writable_mounts(self) -> list[str]:
        """The repo, the repo's git folder when it mounts writable, and each writable sibling."""
        mounts = [self.repo]
        if not self.repo_git_read_only:
            mounts.append(self.repo_git)
        return mounts + self.writable_dirs
