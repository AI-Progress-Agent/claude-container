"""The planning step: what a run of the container needs, read from the host's state.

It writes nothing and runs nothing that changes state. execute.py does what
the plan says.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from launcher.clones import collect_nested_clones, collect_sibling_repos
from launcher.configs import collect_config_paths
from launcher.draft import Draft, Mount, Refused
from launcher.flags import guarded_files
from launcher.gitdirs import collect_worktree_mounts, guarded_worktrees
from launcher.hooks import collect_hooks_paths
from launcher.local import LocalOverrides
from launcher.marketplaces import collect_marketplaces
from launcher.mounts import collect_folder_mounts, collect_host_mounts
from launcher.pointers import PointerScope, refuse_git_pointers
from launcher.settings import Settings


@dataclass(frozen=True)
class Host:
    """What the planning step reads that is not on disk."""

    repo: str
    repo_git: str
    repo_git_read_only: bool
    home: str
    # The repo's project folder under ~/.claude/projects.
    project_key: str
    settings: Settings
    # What docker/cc.local set, with the environment compose runs with.
    local: LocalOverrides
    # The path inside of each bind mount the compose files make, or None when
    # docker compose config fails.
    compose_binds: Callable[[], list[str] | None]
    # claude or bash, and its arguments.
    program: str
    args: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunPlan:
    mounts: tuple[Mount, ...]
    env: dict[str, str]
    # The shell text that runs inside before the program.
    setup: str
    program: str
    args: tuple[str, ...]
    # The folders and files to make on the Mac before the container starts.
    # Each file holds its text.
    make_dirs: tuple[str, ...]
    make_files: tuple[tuple[str, str], ...]
    # The files to flag immutable on the Mac for the session.
    flag_files: tuple[str, ...]
    # What the watcher searches during the session.
    pointers: PointerScope
    # What to print before the container starts.
    messages: tuple[str, ...]
    # Why the container must not start, or None.
    refusal: str | None = None

    @property
    def command(self) -> list[str]:
        """What runs inside: bash -c <setup> <program> <args>."""
        return ["bash", "-c", f'{self.setup} && exec {self.program} "$@"', self.program, *self.args]


def plan_run(host: Host) -> RunPlan:
    draft = Draft(host.repo, host.repo_git, host.repo_git_read_only)
    # A bind mount whose source is missing becomes an empty folder owned by
    # root. compose.yaml mounts this one, and it may not exist yet.
    draft.make_dir(f"{host.home}/.claude/projects/{host.project_key}")
    compose_binds = _CachedBinds(host.compose_binds, draft.messages)
    refusal = None
    links = ""
    try:
        links = collect_host_mounts(draft, host.home)
        collect_worktree_mounts(draft, host.repo)
        collect_sibling_repos(draft, list(host.settings.writable_siblings))
        collect_nested_clones(draft, list(host.local.nested_clones))
        draft.worktrees = guarded_worktrees(draft.guarded_git_dirs)
        collect_hooks_paths(draft)
        collect_config_paths(draft)
        collect_folder_mounts(draft, compose_binds)
        refuse_git_pointers(draft)
        collect_marketplaces(draft, host.home, list(host.local.skip_marketplaces), compose_binds)
    except Refused as refused:
        refusal = refused.message
    start = " && ".join(host.settings.start_commands) or "true"
    return RunPlan(
        mounts=tuple(draft.mounts),
        env=host.local.env,
        setup=f"{_browser_setup(host.settings.agent_browser_port)}{links}"
        f"{{ {start}; }} && {{ {host.local.local_setup}; }}",
        program=host.program,
        args=host.args,
        make_dirs=tuple(draft.disk.folders),
        make_files=tuple(draft.disk.files.items()),
        flag_files=tuple(guarded_files(draft)),
        pointers=PointerScope.of(draft),
        messages=tuple(draft.messages),
        refusal=refusal,
    )


class _CachedBinds:
    """compose_binds, read at most once. A failure is named for each purpose that needed it."""

    def __init__(self, read: Callable[[], list[str] | None], messages: list[str]) -> None:
        self._read = read
        self._messages = messages
        self._binds: list[str] | None = None
        self._done = False

    def __call__(self, otherwise: str) -> list[str]:
        if not self._done:
            self._binds = self._read()
            self._done = True
        if self._binds is None:
            self._messages.append(f"docker/cc: docker compose config failed, so {otherwise}")
            return []
        return self._binds


def _browser_setup(port: int | None) -> str:
    """The shell text that points agent-browser inside at the Mac's Chrome, ending in " && ".

    agent-browser drives the Chrome that the repo opens on the Mac with
    remote debugging on that port. Chrome refuses a request that names a host
    other than localhost or an IP, so connect by the IP that
    host.docker.internal resolves to. It is looked up inside, at start,
    because Docker Desktop can change it. It takes the IPv4 address, because
    an IPv6 one would need brackets in the URL. If the name does not resolve,
    it warns and leaves the variable unset. The variable overrides
    agent-browser.json.
    """
    if port is None:
        return ""
    return (
        'mac_ip=$(getent ahostsv4 host.docker.internal | awk "NR == 1 {print \\$1}")\n'
        f'if [ -n "$mac_ip" ]; then export AGENT_BROWSER_CDP=http://$mac_ip:{port}\n'
        'else echo "docker/cc: host.docker.internal does not resolve, '
        "so agent-browser cannot reach the Mac's Chrome\" >&2; fi && "
    )
