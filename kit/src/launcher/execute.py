"""The executor: does what a RunPlan says, then replaces the launcher with docker compose run."""

from __future__ import annotations

import os
import signal
import sys
import tempfile
from collections.abc import Mapping
from typing import NoReturn

from launcher.environment import tmp_dir
from launcher.flags import FlagSet
from launcher.local import LocalHelper
from launcher.output import refusal, refuse, say
from launcher.plan import RunPlan
from launcher.watcher import WATCHER_SIGNALS, Watcher


def execute(plan: RunPlan, compose: list[str], helper: LocalHelper) -> NoReturn:
    """Makes what the plan needs on the Mac, starts the watcher, then runs the container.

    It refuses before it makes anything when the plan holds a refusal.
    """
    say(*plan.messages)
    if plan.refusal is not None:
        refuse(plan.refusal)
    for folder in plan.make_dirs:
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            refuse_mac_write(folder)
    for file, text in plan.make_files:
        try:
            with open(file, "w") as out:
                out.write(text)
        except OSError:
            refuse_mac_write(file)
    # Each launcher's record is a file named after its process ID. compose
    # takes this process over, so the ID stays the session's.
    flags = FlagSet(f"{tmp_dir()}/cc-flags", os.getpid(), list(plan.flag_files))
    try:
        failed = flags.flag()
        if failed:
            flags.clear()
            refuse(
                refusal(
                    "these could not be flagged read-only on the Mac",
                    failed,
                    "chflags fails on a read-only disk, and on a file another user owns.\n"
                    "Move the repo to a writable disk, or give each file to your user with chown.",
                )
            )
        notify_dir = tempfile.mkdtemp(prefix="cc-notify.", dir=tmp_dir())
        _start_watcher(
            Watcher(
                launcher_pid=os.getpid(),
                notify_dir=notify_dir,
                scope=plan.pointers,
                flags=flags,
                helper=helper,
            )
        )
    except BaseException:
        # Until the watcher starts, nothing else clears the flags. So an
        # error or a Ctrl-C here clears them.
        flags.clear()
        raise
    argv = [
        *compose,
        "run",
        "--rm",
        *(arg for mount in plan.mounts for arg in ("-v", mount.arg)),
        "-v",
        f"{notify_dir}:/run/cc-notify",
        "claude",
        *plan.command,
    ]
    exec_compose(argv, plan.env)


def exec_compose(argv: list[str], env: Mapping[str, str]) -> NoReturn:
    """Replaces the launcher with the docker command argv.

    Python ignores SIGPIPE and SIGXFSZ for itself, and an ignored signal stays
    ignored across exec. compose gets the defaults back, as from a shell.
    """
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
    os.execvpe(argv[0], argv, env)


def refuse_mac_write(path: str) -> NoReturn:
    """Names the file or folder that the launcher could not make on the Mac.

    A write fails on a read-only disk, and in a folder another user owns.
    Each guard makes what it needs before any file is flagged, so nothing is
    left to clear.
    """
    refuse(
        refusal(
            "could not make this on the Mac",
            [path],
            "A write fails on a read-only disk, and in a folder another user owns.\n"
            "Move the repo to a writable disk, or give the folder that holds it to your user "
            "with chown.",
        )
    )


def _start_watcher(watcher: Watcher) -> None:
    """Forks the watcher. It runs in its own process for the session.

    The signals it handles stay blocked until it has its own handlers. So a
    Ctrl-C during the fork cannot end it before it clears the flags.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    signal.pthread_sigmask(signal.SIG_BLOCK, WATCHER_SIGNALS)
    if os.fork() == 0:
        try:
            watcher.run()
        finally:
            os._exit(0)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, WATCHER_SIGNALS)
