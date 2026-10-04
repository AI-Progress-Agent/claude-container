"""docker/cc.local, through kit/cc-local.bash, which sources it once per session.

The helper reports what cc.local set. The watcher then hands it each event,
and the helper runs cc.local's notify_host for it. So notify_host sees the
functions and variables cc.local defines, and cc.local runs only once.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import BinaryIO

# The names bash can export. bash leaves others out of its report.
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class LocalOverrides:
    """What docker/cc.local set."""

    # The commands to run inside before Claude.
    local_setup: str
    # The names of the folder marketplaces to keep out.
    skip_marketplaces: tuple[str, ...]
    # kit.toml's nested_clones, and any that cc.local added.
    nested_clones: tuple[str, ...]
    # The launcher's environment, with what cc.local exported.
    env: dict[str, str]


class LocalError(Exception):
    """docker/cc.local failed. returncode is the helper's."""

    def __init__(self, returncode: int) -> None:
        super().__init__(returncode)
        self.returncode = returncode


class LocalHelper:
    """The running helper. The watcher owns it once the container starts."""

    def __init__(self, report: BinaryIO, events: BinaryIO) -> None:
        self._report = report
        self._events = events
        self._alive = True

    @classmethod
    def start(
        cls,
        kit: str,
        cc_local: str | None,
        *,
        repo: str,
        log: str,
        nested_clones: Sequence[str],
        args: Sequence[str],
        env: Mapping[str, str],
    ) -> tuple[LocalHelper, LocalOverrides]:
        """Starts the helper, and waits for its report. Raises LocalError when cc.local fails."""
        report_out, report_in = os.pipe()
        events_out, events_in = os.pipe()
        process = subprocess.Popen(
            [
                "bash",
                f"{kit}/cc-local.bash",
                str(report_in),
                str(events_out),
                cc_local or "",
                repo,
                log,
                str(len(nested_clones)),
                *nested_clones,
                *args,
            ],
            env=dict(env),
            pass_fds=(report_in, events_out),
        )
        os.close(report_in)
        os.close(events_out)
        helper = cls(os.fdopen(report_out, "rb"), os.fdopen(events_in, "wb"))
        try:
            local_setup = helper._field()
            skip_marketplaces = helper._fields(int(helper._field()))
            nested = helper._fields(int(helper._field()))
            exported = helper._fields(int(helper._field()))
        except EOFError, ValueError:
            raise LocalError(process.wait()) from None
        return helper, LocalOverrides(
            local_setup=local_setup,
            skip_marketplaces=tuple(skip_marketplaces),
            nested_clones=tuple(nested),
            env=_merge(env, exported),
        )

    def notify(self, event: str, cwd: str) -> None:
        """Runs notify_host with the file event as stdin, in the folder cwd, and waits for it."""
        if not self._alive:
            return
        try:
            self._events.write(os.fsencode(event) + b"\0" + os.fsencode(cwd) + b"\0")
            self._events.flush()
            if self._report.read(1) != b"\0":
                self._alive = False
        except OSError:
            self._alive = False

    def close(self) -> None:
        """Ends the events, so the helper ends."""
        for stream in (self._events, self._report):
            with contextlib.suppress(OSError):
                stream.close()

    def _field(self) -> str:
        data = bytearray()
        while (byte := self._report.read(1)) != b"\0":
            if not byte:
                raise EOFError
            data += byte
        return os.fsdecode(bytes(data))

    def _fields(self, count: int) -> list[str]:
        return [self._field() for _ in range(count)]


def _merge(env: Mapping[str, str], exported: list[str]) -> dict[str, str]:
    """env with each variable cc.local exported, and without each one it unset."""
    reported: dict[str, str] = {}
    for item in exported:
        name, _, value = item.partition("=")
        reported[name] = value
    merged = {k: v for k, v in env.items() if k in reported or not _NAME.fullmatch(k)}
    return merged | reported
