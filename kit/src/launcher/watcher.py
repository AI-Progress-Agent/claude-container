"""The session's watcher, which runs on the host while the container runs.

The image's managed settings hook Stop and Notification to drop each event as
a file into /run/cc-notify. That is this session's own folder, notify_dir,
and the watcher hands each file to docker/cc.local's notify_host. A file drop
needs no port, so two sessions never collide.

compose replaces the launcher, through exec, so every signal reaches compose
directly. compose keeps the launcher's process ID. The watcher checks that ID
on each pass, about every half second. When the process has ended, the
watcher ends the session.

Every tenth pass, about every 5 seconds, it also moves aside each file that
could lead git on the Mac to hooks or a config written inside. A full search
of the repo takes too long to run on every pass.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import tempfile
import time
from types import FrameType
from typing import cast

from launcher.flags import FlagSet, is_alive
from launcher.local import LocalHelper
from launcher.output import say
from launcher.paths import is_under
from launcher.pointers import PointerScope, block_git_pointers

# The signals the watcher handles itself.
WATCHER_SIGNALS = {signal.SIGINT, signal.SIGQUIT, signal.SIGHUP, signal.SIGTERM}


class Watcher:
    def __init__(
        self,
        *,
        launcher_pid: int,
        notify_dir: str,
        scope: PointerScope,
        flags: FlagSet,
        helper: LocalHelper,
    ) -> None:
        self.launcher_pid = launcher_pid
        self.notify_dir = notify_dir
        self.scope = scope
        self.flags = flags
        self.helper = helper
        self.blocked: list[str] = []
        self._stopping = False

    def run(self) -> None:
        """Watches until the session ends, then ends it."""
        # Ctrl-C belongs to the container. A hangup or TERM, such as from a
        # closed terminal, ends the watch, but the session still ends in
        # full.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGQUIT, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, self._stop)
        signal.signal(signal.SIGTERM, self._stop)
        # The launcher blocked them for the fork.
        signal.pthread_sigmask(signal.SIG_UNBLOCK, WATCHER_SIGNALS)
        # An error in a pass must not skip the end of the session, or the
        # flags stay set.
        try:
            passes = 0
            while is_alive(self.launcher_pid) and not self._stopping:
                passes += 1
                if passes % 10 == 0:
                    self._block()
                self._hand_events()
                time.sleep(0.5)
        finally:
            self._finish()

    def _stop(self, signum: int, frame: FrameType | None) -> None:
        self._stopping = True

    def _finish(self) -> None:
        """Waits for compose to end, then ends the session.

        It clears the flags and runs the last pointer search of the session.
        Then it names each path it moved aside during the session, and
        removes notify_dir. A hangup can reach the watcher before compose
        ends. The wait keeps each flag set while the container can still
        write.
        """
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        while is_alive(self.launcher_pid):
            time.sleep(0.5)
        stuck = self.flags.clear()
        if stuck:
            say(
                "docker/cc: these stay flagged read-only on the Mac. "
                "Clear each one with chflags nouchg:",
                *(f"  {file}" for file in stuck),
            )
        self._block()
        if self.blocked:
            say(
                "docker/cc: moved aside to .cc-blocked, since each could lead git on the Mac "
                "to hooks or a config written inside:",
                *(f"  {path}" for path in self.blocked),
            )
        shutil.rmtree(self.notify_dir, ignore_errors=True)
        self.helper.close()

    def _hand_events(self) -> None:
        """Hands each event in notify_dir to notify_host, in order, then deletes it.

        The repo mounts at its own path, so a folder under it inside is the
        same folder on the host. notify_host runs in the event's cwd there.
        Any other folder falls back to the repo's root. So does one missing on
        the host.
        """
        try:
            names = os.listdir(self.notify_dir)
        except OSError:
            return
        # A dot name is an event still being written.
        for name in sorted(n for n in names if n.endswith(".json") and not n.startswith(".")):
            event = f"{self.notify_dir}/{name}"
            self.helper.notify(event, self._event_cwd(event))
            with contextlib.suppress(OSError):
                os.unlink(event)

    def _event_cwd(self, event: str) -> str:
        try:
            with open(event, encoding="utf-8") as file:
                data = cast(object, json.load(file))
        except OSError, ValueError:
            return self.scope.repo
        cwd = cast(dict[str, object], data).get("cwd") if isinstance(data, dict) else None
        if not isinstance(cwd, str) or not is_under(cwd, self.scope.repo):
            return self.scope.repo
        # The container writes the event. A path the Mac cannot hold, or a
        # NUL that would end the field early, falls back too.
        try:
            if b"\0" in os.fsencode(cwd):
                return self.scope.repo
        except UnicodeError:
            return self.scope.repo
        return cwd

    def _block(self) -> None:
        self.blocked += block_git_pointers(self.scope, self._notify_text)

    def _notify_text(self, text: str) -> None:
        """Hands the event text to notify_host, run at the repo's root."""
        fd, path = tempfile.mkstemp(dir=self.notify_dir, prefix=".blocked-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as file:
                file.write(text)
            self.helper.notify(path, self.scope.repo)
        finally:
            os.unlink(path)
