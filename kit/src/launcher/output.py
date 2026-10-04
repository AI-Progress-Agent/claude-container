"""What the launcher prints. Each line goes to stderr, so stdout stays the container's."""

from __future__ import annotations

import sys
from collections.abc import Iterable
from typing import NoReturn


def say(*lines: str) -> None:
    if lines:
        sys.stderr.write("".join(f"{line}\n" for line in lines))
        sys.stderr.flush()


def refuse(message: str) -> NoReturn:
    """Prints message, and exits before the container starts."""
    say(message)
    raise SystemExit(1)


def refusal(problem: str, paths: Iterable[str], advice: str) -> str:
    """The message for a refusal: the problem, each path it names, then what to do."""
    named = "".join(f"  {path}\n" for path in paths)
    return f"docker/cc: {problem}, so the container did not start:\n{named}{advice}"
