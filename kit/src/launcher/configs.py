"""The read-only mounts of what a repo's config names inside a writable mount.

Git on the Mac reads each config file, and runs each command value. A config
file can include another, and a command value can name a script. The
container can write such a file when it sits inside a writable mount. Git on
the Mac would then read or run it. When git ignores the file, git status
does not show the change.

In each worktree, the launcher reads every config file that git reads there.
These are the system, global and repo configs, and each config.worktree. They
are also each included file, through nested includes. Two kinds of path
mount read-only:

  - each config file inside a writable mount, an included one among them.
    Every include counts, whatever its condition. An "onbranch:" condition
    turns on when the container switches branch. A relative include resolves
    against the folder of the file that holds it, and a ~ expands. Git skips
    a missing included file, so the container could make it. So the launcher
    makes it empty on the Mac first.
  - each file or folder that a command value names. _COMMAND_KEYS lists the
    keys. The value splits into words, as a shell splits it. A string that a
    shell runs, as in sh -c 'cmd', splits too. A relative word resolves
    against each folder the shell may be in. That is the worktree's root, or
    a folder that a cd goes to. _Shell follows each cd. See _path_word for
    the words it skips.

The launcher cannot guard some paths, as reach.py lists them. A missing path
inside a writable mount stops the start too, when a word with a / or a cd
names it. The container could make it. For each such path, the launcher
names the worktree, the key, the value and the path. Then the container does
not start.

The launcher reads the config only at start. So it does not see a value set
or a path made during the session. Nor does it see a path that a command
builds when it runs, such as $(git rev-parse --show-toplevel)/x.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from launcher import git
from launcher.draft import Draft, Refused
from launcher.output import refusal
from launcher.paths import absolute_path, exists, nearest_is_dir, outermost, parent, real_dir
from launcher.reach import Place, Reach

# Each key whose value git runs as a program or a shell command. The list
# comes from the git 2.56 manual, in lower case. A * stands for a subsection
# or a name.
_COMMAND_KEYS = (
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
# The operators in such a word, longest first. shlex keeps a run of
# punctuation together, as in );.
_OPERATOR = re.compile(r"&&|\|\||;;|\|&|&>|>>|<<|>&|<&|[();<>|&]")
# A word such as NAME=value, whose path, if any, follows the =.
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*=")


def collect_config_paths(draft: Draft) -> None:
    reach = Reach.of(draft)
    entries_of: dict[str, list[_Entry]] = {}
    guards: dict[str, Place] = {}
    refused: list[str] = []
    for worktree in draft.worktrees:
        check = _WorktreeCheck(draft, reach, worktree, guards, refused)
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
    # A path inside a guarded folder needs nothing new.
    for path in outermost(guards):
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
class _WorktreeCheck:
    """Decides on each path that one worktree's config names.

    It adds each path to guard to guards, and each refusal's line to refused.
    """

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
        missing = place.mount is not None and not disk.exists(place.path)
        if missing and nearest_is_dir(place.path):
            self.draft.make_dir(parent(place.path))
            self.draft.make_file(place.path)
        if place.mount is None or not disk.is_file(place.path):
            if entry is not None:
                self._refuse(entry, path)
            return
        self.guards[place.path] = place

    def command(self, entry: _Entry) -> None:
        """Guards each file or folder that entry's value names, when git runs it."""
        words = _command_words(entry)
        if words is None:
            return
        shell = _Shell([self.worktree])
        pending = words[::-1]
        # Whether the cd before this word is the first command of its list.
        # None when the word before it is not a cd.
        cd: bool | None = None
        while pending:
            word = pending.pop()
            if set(word) <= _PUNCTUATION:
                if cd is not None:
                    shell.cd(None, cd)
                    cd = None
                for operator in _OPERATOR.findall(word):
                    shell = shell.operator(operator)
                continue
            inner = self._inner_words(word, shell)
            if inner is not None:
                # The shell that runs the string cannot move this one.
                pending += ["(", *inner, ")"][::-1]
                continue
            was_cd, cd = cd, None
            if word == "cd" and shell.command_first is not None:
                cd = shell.command_first
                shell.word(word)
                continue
            path = _path_word(word)
            if path is not None:
                names_path = (was_cd is not None or "/" in path) and not shell.unknown
                self._guard_word(entry, shell.paths(path), names_path=names_path)
            if was_cd is not None:
                shell.cd(path, was_cd)
            shell.word(word)

    def _inner_words(self, word: str, shell: _Shell) -> list[str] | None:
        """The words of the shell command that word holds, as in sh -c 'cmd'.

        None when word holds no space or shell punctuation. None also when it
        names a path that exists, such as a quoted path with a space.
        """
        if not any(c.isspace() or c in _PUNCTUATION for c in word):
            return None
        path = _path_word(word)
        if path is not None and any(exists(p) for p in shell.paths(path)):
            return None
        words = _split(word)
        return None if words == [word] else words

    def _guard_word(self, entry: _Entry, paths: list[str], *, names_path: bool) -> None:
        """Guards what a word names, from each of paths.

        names_path says whether the word surely names a path. Then a missing
        one stops the start.
        """
        for path in paths:
            place = self.reach.place(path, [self.worktree])
            if place is None:
                continue
            if place.mount is None:
                self._refuse(entry, path)
                return
            if exists(place.path):
                self.guards[place.path] = place
            elif names_path:
                self._refuse(entry, path)

    def _refuse(self, entry: _Entry, path: str) -> None:
        line = f"{self.worktree}: {entry.key} = {entry.value} names {os.path.normpath(path)}"
        if line not in self.refused:
            self.refused.append(line)


@dataclass
class _Shell:
    """The folders a shell may be in at each word of a command, as each cd moves it.

    A word resolves against each folder. A command after &&, || or | may run
    in a folder that a cd before it did not go to, so the list grows. A ;
    or a ) ends what a cd in a subshell or a pipeline did.
    """

    # Each folder the shell may be in now.
    now: list[str]
    # Whether a cd went to a folder the launcher cannot know.
    unknown: bool = False
    # The shell that a ( started this one from.
    outer: _Shell | None = None
    # Each folder at the start of this list, and of this pipeline.
    list_start: list[str] = field(init=False)
    pipe_start: list[str] = field(init=False)
    # Each folder the shell stays in when a command before a cd fails.
    skipped: list[str] = field(init=False)
    # Whether a | came since the start of this pipeline.
    piped: bool = field(init=False)
    # Whether the next word starts a command, and whether that command is
    # the first of its list.
    at_command: bool = field(init=False)
    first: bool = field(init=False)

    def __post_init__(self) -> None:
        self._start_list()

    @property
    def command_first(self) -> bool | None:
        """Whether the next word starts the first command of its list.

        None when the next word starts no command.
        """
        return self.first if self.at_command else None

    def paths(self, path: str) -> list[str]:
        """Each absolute path that path may name from here."""
        return [path] if path.startswith("/") else [f"{f}/{path}" for f in self.now]

    def word(self, word: str) -> None:
        """Steps past word. NAME=x, command and builtin come before a command."""
        if not (_ASSIGNMENT.match(word) or word in ("command", "builtin")):
            self.at_command = False

    def cd(self, path: str | None, first: bool) -> None:
        """Moves to path, which a cd names. None for a folder the launcher cannot know.

        first says whether the cd is the first command of its list. A cd to a
        folder that is not there fails, so the shell stays.
        """
        if path is None:
            self.unknown = True
            return
        moved = [absolute_path(path, f) for f in self.now]
        moved = [m if os.path.isdir(m) else f for m, f in zip(moved, self.now, strict=True)]
        if not first:
            self.skipped = _union(self.skipped, self.now)
        self.now = _union(moved)

    def operator(self, operator: str) -> _Shell:
        """Steps past a shell operator, such as ; or &&. Returns the shell after it.

        A ( returns a new shell, and its ) returns the outer one.
        """
        if operator == "(":
            return _Shell(self.now, self.unknown, self)
        if operator == ")" and self.outer is not None:
            self.outer.at_command = False
            return self.outer
        if operator in ("|", "|&"):
            self.piped = True
            self.now = _union(self.now, self.pipe_start)
            self.at_command, self.first = True, False
        elif operator in ("&&", "||", ";", ";;", "&"):
            # Each command of a pipeline runs in a shell of its own.
            if self.piped:
                self.now = _union(self.now, self.pipe_start)
            if operator == "||":
                self.now = _union(self.now, self.skipped, self.list_start)
            elif operator == "&":
                # The list ran in a shell of its own.
                self.now = _union(self.now, self.list_start)
            if operator in ("&&", "||"):
                self.pipe_start = self.now
                self.piped = False
                self.at_command, self.first = True, False
            else:
                self.now = _union(self.now, self.skipped)
                self._start_list()
        return self

    def _start_list(self) -> None:
        self.list_start = self.pipe_start = self.now
        self.skipped = []
        self.piped = False
        self.at_command, self.first = True, True


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
    difftool or mergetool path names one program, so its value is one word.
    """
    key = entry.key.lower()
    value = entry.value
    if not value or not any(fnmatchcase(key, k) for k in _COMMAND_KEYS):
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
    if fnmatchcase(key, "difftool.*.path") or fnmatchcase(key, "mergetool.*.path"):
        return [value]
    return _split(value)


def _split(command: str) -> list[str]:
    """The words of command, as a shell splits them."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    # A shell starts a comment only at the start of a word.
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return command.split()


def _union(*folders: list[str]) -> list[str]:
    return list(dict.fromkeys(f for group in folders for f in group))


def _path_word(word: str) -> str | None:
    """The path that word may name, or None when it names none the launcher can know.

    It skips a word that the shell expands when the command runs, with a $
    or a `. It skips a URL too. A long option names a path only after an =,
    as in --file=x. So does NAME=x. A short option may name one after its
    letter, as in -Fx.
    """
    if "$" in word or "`" in word or "://" in word:
        return None
    if word.startswith("-") and not word.startswith("--"):
        word = word[2:].removeprefix("=")
    if word.startswith("-") or _ASSIGNMENT.match(word):
        _, equals, word = word.partition("=")
        if not equals:
            return None
    if word.startswith("~"):
        word = os.path.expanduser(word)
    return word or None
