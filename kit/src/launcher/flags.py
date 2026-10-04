"""The Mac's immutable flag, uchg, on each guarded file for the session.

A file mounted read-only on its own sits in a writable folder. The Mac's disk
ignores letter case, so a write inside to .git/CONFIG gets past the mount of
.git/config and replaces the Mac's file. The flag stops every write, rename
and removal, from inside or from the Mac. Root inside cannot clear it.

Two sessions can share a file: two worktrees of one clone, or two repos with
one writable sibling. So each launcher records the files it flags in
flags_dir, in a file named after its process ID. It writes the record before
it sets a flag. clear() clears a file only when no other live launcher's
record names it. A crash that ends the watcher before it clears leaves its
flags set. So a flag already set at start counts as one that a launcher set.
"""

from __future__ import annotations

import contextlib
import os
import subprocess

from launcher.draft import Draft
from launcher.output import say
from launcher.paths import is_under_any, real_dir


def guarded_files(draft: Draft) -> list[str]:
    """Each guarded file by its real path, once each.

    A guarded file mounts read-only on its own inside a writable mount. The
    mount's source is a file, and its target sits inside a writable mount.
    compose.yaml mounts the repo's .git/config that way too.
    A folder, such as hooks, needs no flag: a write to .git/HOOKS lands in
    the read-only mount. A file mounted twice, at its real path and at the
    repo's path as typed, counts once.
    """
    writable = draft.writable_mounts()
    config = f"{draft.repo_git}/config"
    found: list[str] = []
    for source, target, read_only in [
        (config, config, True),
        *((m.source, m.target, m.read_only) for m in draft.mounts),
    ]:
        if not read_only or not draft.disk.is_file(source) or not is_under_any(target, writable):
            continue
        # A folder the plan makes has no real path yet. Its path is spelled
        # as on disk up to the part that is missing.
        folder = real_dir(os.path.dirname(source)) or os.path.dirname(source)
        real = f"{folder}/{os.path.basename(source)}"
        if real not in found:
            found.append(real)
    return found


class FlagSet:
    """The files one launcher flags, and its record of them."""

    def __init__(self, flags_dir: str, pid: int, files: list[str]) -> None:
        self.flags_dir = flags_dir
        self.pid = pid
        self.files = list(files)

    @property
    def record(self) -> str:
        return f"{self.flags_dir}/{self.pid}"

    def flag(self) -> list[str]:
        """Records, then flags, each file. Returns the files it could not flag.

        chflags fails on a read-only disk, and on a file another user owns.
        Those leave the set, so clear() does not touch them.
        """
        if not self.files:
            return []
        try:
            os.makedirs(self.flags_dir, exist_ok=True)
            with open(self.record, "w") as record:
                record.write("".join(f"{file}\n" for file in self.files))
        except OSError:
            # No file is flagged yet, so clear() must touch none.
            self.files = []
            raise
        failed = [file for file in self.files if not _chflags("uchg", file)]
        self.files = [file for file in self.files if file not in failed]
        return failed

    def clear(self) -> None:
        """Clears the flag on each file that no other live launcher's record names.

        Then removes this launcher's record. A launcher that starts meanwhile
        may have read the flag as set, and so set nothing. It wrote its
        record first, so a second look finds it, and each file it names is
        flagged again. Names each file whose flag stays set. A second call
        does nothing.
        """
        with contextlib.suppress(OSError):
            os.unlink(self.record)
        files, self.files = self.files, []
        if not files:
            return
        held = self._held_files()
        cleared: list[str] = []
        stuck: list[str] = []
        for file in files:
            if file in held:
                continue
            (cleared if _chflags("nouchg", file) else stuck).append(file)
        held = self._held_files()
        for file in cleared:
            if file in held:
                _chflags("uchg", file)
        if stuck:
            say(
                "docker/cc: these stay flagged read-only on the Mac. "
                "Clear each one with chflags nouchg:",
                *(f"  {file}" for file in stuck),
            )

    def _held_files(self) -> set[str]:
        """Each file that another live launcher's record names.

        Removes each record whose launcher has ended.
        """
        held: set[str] = set()
        try:
            names = os.listdir(self.flags_dir)
        except OSError:
            return held
        for name in names:
            if not name.isdigit() or int(name) == self.pid:
                continue
            record = f"{self.flags_dir}/{name}"
            if is_alive(int(name)):
                try:
                    with open(record) as file:
                        held.update(file.read().splitlines())
                except OSError:
                    pass
            else:
                with contextlib.suppress(OSError):
                    os.unlink(record)
        return held


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _chflags(flag: str, file: str) -> bool:
    """Sets or clears the flag, uchg or nouchg, on file. The tests stand in for chflags."""
    try:
        result = subprocess.run(
            ["chflags", flag, file],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0
