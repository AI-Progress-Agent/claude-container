"""The read-only mounts of what a repo's config names inside a writable mount.

Git on the Mac reads each config file, and runs each command value. A file
they name inside a writable mount could be written inside, and git on the
Mac would then read or run it. git status would not show it when git
ignores the file. In each worktree, the launcher reads every config file git
reads there: the system, global and repo configs, each config.worktree, and
each included file, followed through nested includes. Two kinds of path
mount read-only:

  - each config file inside a writable mount, an included one among them.
    Every include counts, whatever its condition: an "onbranch:" condition
    turns on when the container switches branch. A relative include resolves
    against the folder of the file that holds it, and a ~ expands. A missing
    included file is made empty on the Mac first. Git skips a missing one, so
    the container could make it.
  - each file or folder that a command value names. COMMAND_KEYS lists the
    keys. The value splits into words, as a shell splits it. A relative word
    resolves against the worktree's root, and against each folder that a cd
    in the value names before it. See _path_word for the words it skips.

The launcher cannot guard some paths, as reach.py lists them. A word with a /
that names a missing path inside a writable mount stops the start too,
unless it exists from another folder the value cds to. The container could
make it. The launcher names the worktree, the key, the value and the path of
each one, and the container does not start.

It reads the config only at start, so it does not see a value set or a path
made during the session. Nor does it see a path that a command builds when
it runs, such as $(git rev-parse --show-toplevel)/x.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from fnmatch import fnmatchcase

from launcher import git
from launcher.draft import Draft, Refused
from launcher.hooks import nearest_is_dir
from launcher.output import refusal
from launcher.paths import absolute_path, exists, is_under_any, parent, real_dir
from launcher.reach import Place, Reach

# Each key whose value git runs as a program or a shell command, from the git
# 2.56 manual, in lower case. A * stands for a subsection or a name.
COMMAND_KEYS = (
    "core.fsmonitor",
    "core.editor",
    "core.pager",
    "pager.*",
    "core.sshcommand",
    "core.gitproxy",
    "core.askpass",
    "core.alternaterefscommand",
    "sequence.editor",
    "alias.*",
    "hook.*.command",
    "credential.helper",
    "credential.*.helper",
    "diff.external",
    "diff.*.command",
    "diff.*.textconv",
    "filter.*.clean",
    "filter.*.smudge",
    "filter.*.process",
    "merge.*.driver",
    "difftool.*.cmd",
    "difftool.*.path",
    "mergetool.*.cmd",
    "mergetool.*.path",
    "gpg.program",
    "gpg.*.program",
    "gpg.ssh.defaultkeycommand",
    "remote.*.uploadpack",
    "remote.*.receivepack",
    "trailer.*.cmd",
    "trailer.*.command",
    "gc.recentobjectshook",
    "browser.*.cmd",
    "man.*.cmd",
    "guitool.*.cmd",
    "sendemail.*cmd",
)

# The words of a shell command that are punctuation, as shlex splits them.
_PUNCTUATION = frozenset("();<>|&")
# A word such as NAME=value, whose path, if any, follows the =.
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*=")


def collect_config_paths(draft: Draft) -> None:
    reach = Reach.of(draft)
    entries_of: dict[str, list[_Entry]] = {}
    guards: dict[str, Place] = {}
    refused: list[str] = []
    for worktree in draft.worktrees:
        check = _Check(draft, reach, worktree, guards, refused)
        entries = _worktree_entries(worktree)
        for file in dict.fromkeys(e.file for e in entries if e.file is not None):
            check.config_file(file, None)
        seen: set[str] = set()
        i = 0
        while i < len(entries):
            entry = entries[i]
            i += 1
            check.command(entry)
            path = _include_path(entry)
            if path is None or path in seen:
                continue
            seen.add(path)
            check.config_file(path, entry)
            if os.path.isfile(path):
                if path not in entries_of:
                    entries_of[path] = _file_entries(path)
                entries += entries_of[path]
    if refused:
        raise Refused(
            refusal(
                "a config value names a path the launcher cannot mount read-only",
                refused,
                "Make each missing path. Or point the value at a path below the worktree's root, "
                "with no symbolic link or .. on the way. Or unset it.",
            )
        )
    # Sorted, a folder comes before each path inside it, which then needs
    # nothing new.
    mounted: list[str] = []
    for path in sorted(guards):
        if not is_under_any(path, mounted):
            mounted.append(path)
            reach.guard(draft, guards[path])


@dataclass(frozen=True)
class _Entry:
    """One value from a config file."""

    # The config file that holds it, or None for one from git's command line
    # or the environment.
    file: str | None
    # As git config --list prints it: the section and the name in lower
    # case, a subsection as written.
    key: str
    # None for a key with no =, which git reads as true.
    value: str | None


@dataclass
class _Check:
    """Decides on each path that one worktree's config names."""

    draft: Draft
    reach: Reach
    worktree: str
    guards: dict[str, Place]
    refused: list[str]

    def config_file(self, path: str, entry: _Entry | None) -> None:
        """Guards the config file at path, which entry includes, or git reads on its own."""
        place = self.reach.place(path, [self.worktree])
        if place is None:
            return
        disk = self.draft.disk
        missing = place.root is not None and not disk.exists(place.path)
        if missing and nearest_is_dir(place.path):
            if not exists(parent(place.path)):
                self.draft.make_dir(parent(place.path))
            self.draft.make_file(place.path)
        if place.root is None or not disk.is_file(place.path):
            if entry is not None:
                self._refuse(entry, path)
            return
        self.guards[place.path] = place

    def command(self, entry: _Entry) -> None:
        """Guards each file or folder that entry's value names, when git runs it."""
        words = _command_words(entry)
        if words is None:
            return
        bases = [self.worktree]
        # Whether a cd went to a folder the launcher cannot know.
        lost = False
        after_cd = False
        for word in words:
            if set(word) <= _PUNCTUATION:
                after_cd = False
                continue
            was_cd, after_cd = after_cd, word == "cd"
            path = _path_word(word)
            if path is None:
                lost = lost or was_cd
                continue
            if word == "cd":
                continue
            paths = [path] if path.startswith("/") else [f"{b}/{path}" for b in bases]
            self._word(entry, path, paths, lost)
            if was_cd:
                bases.append(absolute_path(path, self.worktree))

    def _word(self, entry: _Entry, word: str, paths: list[str], lost: bool) -> None:
        """Guards what word names, from each of paths."""
        found = False
        missing: str | None = None
        for path in paths:
            place = self.reach.place(path, [self.worktree])
            if place is None:
                found = found or exists(path)
            elif place.root is None:
                self._refuse(entry, path)
                return
            elif exists(place.path):
                found = True
                self.guards[place.path] = place
            elif missing is None:
                missing = path
        if missing is not None and not found and not lost and "/" in word:
            self._refuse(entry, missing)

    def _refuse(self, entry: _Entry, path: str) -> None:
        line = f"{self.worktree}: {entry.key} = {entry.value} names {os.path.normpath(path)}"
        if line not in self.refused:
            self.refused.append(line)


def _worktree_entries(worktree: str) -> list[_Entry]:
    """Each value git reads in worktree, from each config file but the included ones."""
    out = git.git("-C", worktree, "config", "--list", "--show-origin", "-z", "--no-includes")
    fields = (out or "").split("\0")
    entries: list[_Entry] = []
    for origin, item in zip(fields[0::2], fields[1::2], strict=False):
        kind, _, file = origin.partition(":")
        entries.append(_entry(absolute_path(file, worktree) if kind == "file" else None, item))
    return entries


def _file_entries(file: str) -> list[_Entry]:
    """Each value in file, without the files it includes."""
    out = git.git("config", "-f", file, "--list", "-z")
    return [_entry(file, item) for item in (out or "").split("\0") if item]


def _entry(file: str | None, item: str) -> _Entry:
    key, newline, value = item.partition("\n")
    return _Entry(file, key, value if newline else None)


def _include_path(entry: _Entry) -> str | None:
    """The absolute path of the file that entry includes, or None when it includes none.

    A leading .. resolves against the real path of the folder that holds the
    config file. That folder is a mount point, or outside every writable
    mount, so the container cannot swap it for a link.
    """
    section, _, rest = entry.key.partition(".")
    if section not in ("include", "includeif") or not rest.endswith("path") or not entry.value:
        return None
    if rest != "path" and not rest.endswith(".path"):
        return None
    value = entry.value
    if value.startswith("~"):
        return os.path.expanduser(value)
    if value.startswith("/"):
        return value
    # Git refuses a relative include from the command line.
    if entry.file is None:
        return None
    folder = real_dir(os.path.dirname(entry.file)) or os.path.dirname(entry.file)
    parts = value.split("/")
    while parts and parts[0] in (".", ".."):
        if parts.pop(0) == "..":
            folder = parent(folder) or "/"
    return f"{folder}/{'/'.join(parts)}" if parts else folder


def _command_words(entry: _Entry) -> list[str] | None:
    """The words of entry's value, or None when git does not run it.

    An alias runs only with a ! before it. Without one, it names a git
    command. So does a credential helper, unless it is an absolute path. A
    *.path key names one program, so its value is one word.
    """
    key = entry.key.lower()
    value = entry.value
    if not value or not any(fnmatchcase(key, k) for k in COMMAND_KEYS):
        return None
    if key.startswith("alias."):
        if not value.startswith("!"):
            return None
        value = value[1:]
    elif key.startswith("credential."):
        if value.startswith("!"):
            value = value[1:]
        elif not value.startswith("/"):
            return None
    if key.endswith(".path"):
        return [value]
    lexer = shlex.shlex(value, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    # A shell starts a comment only at the start of a word.
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return value.split()


def _path_word(word: str) -> str | None:
    """The path that word may name, or None when it names none the launcher can know.

    It skips a word that the shell expands when the command runs, with a $
    or a `, and a URL. An option names no path, unless it has a value after
    an =, as in --file=x. So does NAME=x.
    """
    if "$" in word or "`" in word or "://" in word:
        return None
    if word.startswith("-") or _ASSIGNMENT.match(word):
        _, equals, word = word.partition("=")
        if not equals:
            return None
    if word.startswith("~"):
        word = os.path.expanduser(word)
    return word or None
