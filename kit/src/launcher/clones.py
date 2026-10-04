"""The other clones the container sees: the program's sibling repos, and the clones inside the repo."""

from __future__ import annotations

import os
import re

from launcher import git
from launcher.draft import Draft
from launcher.gitdirs import guard_clone, guard_whole_git_dir
from launcher.paths import absolute_path, children, exists

# host/owner in an SSH form (git@github.com:owner/repo), ssh:// or https://.
_ORIGIN = re.compile(r"^([a-z+]+://)?([^@/]+@)?([^:/]+)(:[0-9]+)?[:/]([^/]+)/.*")


def main_clone(repo: str) -> str:
    """The main clone's path.

    From a worktree, that is the clone the worktree belongs to, wherever the
    worktree sits. Outside git, it is the repo itself.
    """
    common = git.common_dir(repo)
    return os.path.dirname(common) if common is not None else repo


def collect_sibling_repos(draft: Draft, writable_siblings: list[str]) -> None:
    """Mounts the program's other repos.

    Each folder next to the main clone that is a clone itself, with an origin
    on the same host and owner as this one's, mounts read-only at its own
    path. The owner test keeps out whatever else shares the parent folder,
    such as another client's work or a partner folder. A worktree's parent is
    the main clone's, wherever the worktree sits: .worktrees,
    .claude/worktrees or elsewhere.

    A sibling named in writable_siblings mounts writable instead, with its
    .git/hooks and .git/config read-only, as compose.yaml mounts this repo's.
    Git runs commands from both, so a write there from the container would
    run on the Mac. The files that lead its worktrees to them mount read-only
    too. Each one joins writable_dirs.
    """
    main = main_clone(draft.repo)
    own = _origin_owner(main)
    # An origin the pattern cannot read, such as a local path, gives no owner.
    # Every sibling without a readable origin would match it.
    if not own:
        return
    found: list[str] = []
    writable: list[str] = []
    for folder in children(os.path.dirname(main)):
        if folder == main or not os.path.isdir(f"{folder}/.git") or _origin_owner(folder) != own:
            continue
        name = os.path.basename(folder)
        if name in writable_siblings:
            draft.mount(folder, read_only=False)
            guard_clone(draft, folder)
            draft.writable_dirs.append(folder)
            writable.append(name)
        else:
            draft.mount(folder, read_only=True)
            found.append(name)
    if found:
        draft.messages.append(f"docker/cc: mounted read-only: {' '.join(found)}")
    if writable:
        draft.messages.append(f"docker/cc: mounted writable: {' '.join(writable)}")


def collect_nested_clones(draft: Draft, nested_clones: list[str]) -> None:
    """Guards each clone that nested_clones names, as a writable sibling is guarded.

    Its .git/hooks, its .git/config and the files that lead git to them mount
    read-only. A bare repo named there, such as a test fixture, gets its own
    hooks and config mounted read-only too.

    A path in nested_clones is relative to the repo. An absolute path names a
    clone inside a writable sibling.

    The container could write any other clone or bare repo inside the repo,
    so the pointer search refuses to start. A named path that holds neither
    is not guarded. So a clone made there during the session is moved aside.
    """
    guarded: list[str] = []
    for path in nested_clones:
        folder = absolute_path(path.removesuffix("/"), draft.repo)
        if os.path.isdir(f"{folder}/.git"):
            guard_clone(draft, folder)
        elif os.path.isfile(f"{folder}/HEAD") and has_git_dir_parts(folder):
            guard_whole_git_dir(draft, folder, folder, folder)
        else:
            draft.messages.append(
                f"docker/cc: nested clone {path} has no .git directory and is no bare repo, "
                "so it is not guarded"
            )
            continue
        guarded.append(path)
    if guarded:
        draft.messages.append(f"docker/cc: guarded nested clones: {' '.join(guarded)}")


def has_git_dir_parts(folder: str) -> bool:
    """Whether folder, if it holds a HEAD, is one that git takes for a git folder.

    It is when it holds a commondir, or both objects and refs. With a
    commondir, git looks for objects and refs in the folder it names instead.
    Git also accepts objects and refs as files. So this checks only that each
    one exists.
    """
    return exists(f"{folder}/commondir") or (
        exists(f"{folder}/objects") and exists(f"{folder}/refs")
    )


def _origin_owner(clone: str) -> str | None:
    """host/owner from the clone's origin URL, in lower case, because GitHub ignores case in both.

    "" when the pattern cannot read the URL, and None when there is no origin.
    """
    url = git.git("-C", clone, "remote", "get-url", "origin")
    if url is None:
        return None
    match = _ORIGIN.match(url)
    return f"{match[3]}/{match[5]}".lower() if match else ""
