"""The notification path: the image's hook script and the launcher's watcher.

Inside, the image's Stop and Notification hooks run cc-forward-notify, which
drops each event as a file into /run/cc-notify. That folder is the session's
own folder on the Mac. The watcher hands each file to notify_host there.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from harness import FORWARD, Kit, send_event


def send_events(kit: Kit, *cwds: str) -> None:
    """In place of the container, sends a Stop event from each folder in turn."""
    kit.on_run("".join(send_event(event(cwd)) for cwd in cwds))


def event(cwd: str | Path) -> str:
    return json.dumps({"hook_event_name": "Stop", "cwd": str(cwd)})


def test_each_event_reaches_notify_host_in_its_folder(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    (repo / "project").mkdir()
    seen = kit.work / "seen"
    kit.cc_local(repo, f'notify_host() {{ echo "$PWD $(cat)" >>"{seen}"; }}\n')
    # A folder outside the repo is not the same folder on the Mac, and nor is
    # one that is missing there. The notifier then runs at the repo's root.
    send_events(kit, str(repo / "project"), "/", str(repo / "missing"))
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert seen.read_text().splitlines() == [
        f"{repo / 'project'} {event(repo / 'project')}",
        f"{repo} {event('/')}",
        f"{repo} {event(repo / 'missing')}",
    ]
    # Each event file goes once handled, and the folder goes at the end.
    assert not run.container.notify_dir.exists()


def test_an_event_whose_folder_the_mac_cannot_hold_runs_at_the_root(kit: Kit) -> None:
    """The container writes the event, so the watcher must outlive a bad one."""
    repo = kit.main_clone()
    seen = kit.work / "seen"
    kit.cc_local(repo, f'notify_host() {{ echo "$PWD" >>"{seen}"; }}\n')
    send_events(kit, f"{repo}/\ud800", f"{repo}/a\0b", str(repo))
    run = kit.run(repo)
    assert run.returncode == 0, run.stderr
    assert seen.read_text().splitlines() == [str(repo)] * 3
    # The session still ends in full.
    assert kit.flagged() == []
    assert not run.container.notify_dir.exists()


def test_notifier_output_goes_to_the_log(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    kit.cc_local(repo, "notify_host() { echo notified; echo complaint >&2; }\n")
    send_events(kit, str(repo))
    run = kit.run(repo)
    assert "notified" not in run.stdout + run.stderr
    assert (kit.tmp / "cc-notify.log").read_text() == "notified\ncomplaint\n"


def test_default_notifier_is_cc_notify_on_the_path(kit: Kit) -> None:
    repo = kit.clone(kit.src / "app")
    heard = kit.work / "heard"
    notifier = kit.fake / "bin/cc-notify.mjs"
    notifier.write_text(f'#!/bin/sh\ncat >"{heard}"\n')
    notifier.chmod(0o755)
    send_events(kit, str(repo))
    kit.run(repo)
    assert heard.read_text() == event(repo)


def test_cc_local_runs_once_per_session(kit: Kit) -> None:
    """So notify_host sees what cc.local defines, without running it again."""
    repo = kit.clone(kit.src / "app")
    count = kit.work / "count"
    seen = kit.work / "seen"
    kit.cc_local(
        repo,
        f'echo sourced >>"{count}"\n'
        "greeting=hello\n"
        f'notify_host() {{ echo "$greeting" >>"{seen}"; }}\n',
    )
    send_events(kit, str(repo), str(repo))
    kit.run(repo)
    assert count.read_text() == "sourced\n"
    assert seen.read_text() == "hello\nhello\n"


def test_hook_without_the_folder_exits_cleanly(kit: Kit) -> None:
    """A container started some other way has no /run/cc-notify."""
    result = subprocess.run(
        ["sh", FORWARD],
        input="{}",
        env={**kit.env, "CC_NOTIFY_DIR": str(kit.work / "missing")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert not (kit.work / "missing").exists()
