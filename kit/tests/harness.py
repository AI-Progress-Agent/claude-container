"""Runs the launcher as a repo runs it: through docker/cc, without Docker.

Each test gets its own folder with a home, a cache and fake commands. Fake
docker, gh and chflags commands go first on the PATH:

- docker logs each call, with the environment it got and the files flagged at
  that moment. `docker cp` copies the kit as the image holds it. `docker
  compose config` prints the bind mounts that compose.yaml makes. `docker
  compose run` runs the test's run hook, which stands in for the container.
  `docker image inspect --format` prints the image ID in the file image-id.
  `docker pull` replaces that file with pulled-id, when there is one, and
  fails when the file pull-fails exists.
- gh prints a token.
- chflags keeps each flagged file as a line in a file, since Linux has no
  chflags. The launcher must call it by name, as `chflags uchg FILE`.

uv comes next, in a folder of its own, so a test can leave it out. It uses
this machine's own Python and cache, so no test downloads anything. The rest
of the PATH holds only the system's folders. Every test moves HOME, and a
tool that a version manager installs stops working without its config. So on
the Mac, docker/cc.local runs under the bash that macOS ships, 3.2, as it
does for a user without a newer one.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, cast

KIT = Path(__file__).resolve().parents[1]
ROOT = KIT.parent
STUB = ROOT / "stub" / "cc"
FORWARD = ROOT / "image" / "cc-forward-notify"
BASE_IMAGE = "ghcr.io/ai-progress-agent/claude-container:v1.2.3@sha256:0123abcd"
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
NOTIFY_TARGET = "/run/cc-notify"

# What .dockerignore keeps out of the image's copy of kit/.
NOT_IN_IMAGE = (".venv", "tests", "__pycache__", ".pytest_cache", ".ruff_cache")

# What a start of Kit.main_clone mounts read-only inside the clone, and the
# folders above those mounts that mount on their own.
MAIN_GUARDED = [
    "Main-Clone/.claude/worktrees/agent/.git",
    "Main-Clone/.git/commondir",
    "Main-Clone/.git/modules/lib/commondir",
    "Main-Clone/.git/modules/lib/config",
    "Main-Clone/.git/modules/lib/hooks",
    "Main-Clone/.git/worktrees/agent/commondir",
    "Main-Clone/.git/worktrees/other/commondir",
    "Main-Clone/.worktrees/other/.git",
]

MAIN_FOLDERS = [
    "Main-Clone/.claude",
    "Main-Clone/.claude/worktrees",
    "Main-Clone/.claude/worktrees/agent",
    "Main-Clone/.git/modules",
    "Main-Clone/.git/modules/lib",
    "Main-Clone/.git/worktrees",
    "Main-Clone/.git/worktrees/agent",
    "Main-Clone/.git/worktrees/other",
    "Main-Clone/.worktrees",
    "Main-Clone/.worktrees/other",
]

FAKE_DOCKER = """
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

fake = Path(os.environ["FAKE_DIR"])
argv = sys.argv[1:]


def lines(path):
    return path.read_text().splitlines() if path.exists() else None


# The launcher's record is named after its process ID. Bash hands its process
# to compose, so the record is named after this one. A launcher that runs
# compose as a child would name it after this one's parent.
flags_dir = Path(os.environ.get("TMPDIR", "/tmp")) / "cc-flags"
record = next(
    (flags_dir / str(pid) for pid in (os.getpid(), os.getppid())
     if (flags_dir / str(pid)).exists()),
    flags_dir / str(os.getpid()),
)
with (fake / "docker.jsonl").open("a") as log:
    log.write(json.dumps({
        "argv": argv,
        "env": dict(os.environ),
        "flags": lines(fake / "flags") or [],
        "record": lines(record),
    }) + "\\n")


def subcommand(args):
    i = 0
    while i < len(args) and args[i] in ("-p", "--project-directory", "-f"):
        i += 2
    return args[i] if i < len(args) else None


def compose_config():
    if (fake / "compose-config-fails").exists():
        return 1
    env = os.environ
    git = env["REPO_GIT"]
    binds = [env["REPO"], git, git + "/hooks", git + "/config",
             env["HOST_HOME"] + "/.claude/projects/" + env["PROJECT_KEY"]]
    binds += lines(fake / "compose-binds") or []
    volumes = [{"type": "bind", "source": t, "target": t} for t in binds]
    volumes.append({"type": "volume", "source": "claude-config",
                    "target": env["HOST_HOME"] + "/.claude"})
    print(json.dumps({"services": {"claude": {"volumes": volumes}}}))
    return 0


def compose_run(args):
    hook = fake / "run-hook"
    if not hook.exists():
        return 0
    notify = ""
    for flag, arg in zip(args, args[1:]):
        if flag == "-v" and arg.endswith(":/run/cc-notify"):
            notify = arg.removesuffix(":/run/cc-notify")
    env = {**os.environ, "NOTIFY_DIR": notify, "DOCKER_PID": str(os.getpid())}
    return subprocess.run([str(hook)], env=env, check=False).returncode


def main():
    match argv:
        case ["create", *_]:
            print("container-id")
        case ["cp", _, dest]:
            shutil.copytree(os.environ["IMAGE_KIT"], dest, dirs_exist_ok=True)
        case ["pull", *_]:
            if (fake / "pull-fails").exists():
                return 1
            if (fake / "pulled-id").exists():
                (fake / "pulled-id").replace(fake / "image-id")
        case ["image", "inspect", "--format", _, *_]:
            if not (fake / "image-id").exists():
                return 1
            print((fake / "image-id").read_text().strip())
        case ["image", "inspect", *_]:
            return 1 if (fake / "no-dev-image").exists() else 0
        case ["compose", *rest]:
            match subcommand(rest):
                case "config":
                    return compose_config()
                case "run":
                    return compose_run(rest)
    return 0


sys.exit(main())
"""

FAKE_CHFLAGS = """#!/usr/bin/env bash
# Sets or clears the flag $1, uchg or nouchg, on the file $2, as a line in
# $FAKE_DIR/flags. It refuses a file named in refuse-uchg or refuse-nouchg,
# as chflags does on a read-only disk. A chflags-hook runs first.
set -euo pipefail
state=$FAKE_DIR/flags
printf '%s %s\\n' "$1" "$2" >>"$FAKE_DIR/chflags.log"
if [ -x "$FAKE_DIR/chflags-hook" ]; then "$FAKE_DIR/chflags-hook" "$@"; fi
if [ -f "$FAKE_DIR/refuse-$1" ] && grep -qxF "$2" "$FAKE_DIR/refuse-$1"; then
  echo "chflags: $2: Operation not permitted" >&2
  exit 1
fi
touch "$state"
case $1 in
  uchg) grep -qxF "$2" "$state" || printf '%s\\n' "$2" >>"$state" ;;
  nouchg)
    grep -vxF "$2" "$state" >"$state.new" || true
    mv "$state.new" "$state"
    ;;
  *) exit 2 ;;
esac
"""

FAKE_GH = """#!/usr/bin/env bash
# Prints the token in gh-token, as `gh auth token` does. Without one, fails
# as gh does with no login.
[ -f "$FAKE_DIR/gh-token" ] || exit 1
cat "$FAKE_DIR/gh-token"
"""

# Commands the container's setup text calls, for start_container.
# Each one names bash by its path, since a fake bash may come first on the
# PATH.
FAKE_LN = """#!/bin/bash
printf '%s\\0' "$@" >>"$SANDBOX/ln.log"
printf '\\n' >>"$SANDBOX/ln.log"
"""
FAKE_GETENT = """#!/bin/bash
[ -f "$SANDBOX/mac-ip" ] || exit 2
printf '%s       STREAM host.docker.internal\\n' "$(cat "$SANDBOX/mac-ip")"
"""
FAKE_PROGRAM = """#!/bin/bash
printf '%s\\n' "${0##*/}" "$@" >"$SANDBOX/program.args"
if [ -n "${AGENT_BROWSER_CDP+set}" ]; then printf '%s' "$AGENT_BROWSER_CDP" >"$SANDBOX/cdp"; fi
"""


@dataclass(frozen=True)
class Uv:
    """The uv that runs these tests, and the Python and cache it keeps."""

    binary: str
    cache: str
    pythons: str

    @classmethod
    def find(cls) -> Uv:
        # uv run sets UV to its own path. A version manager's shim in its
        # place would stop working once a test moves HOME.
        binary = os.environ.get("UV") or shutil.which("uv")
        assert binary, "the tests need uv"

        def ask(*args: str) -> str:
            return subprocess.run(
                [binary, *args], capture_output=True, text=True, check=True
            ).stdout.strip()

        return cls(binary, ask("cache", "dir"), ask("python", "dir"))


UV = Uv.find()


@dataclass(frozen=True)
class Mount:
    """One -v argument: source:target, or source:target:ro."""

    source: str
    target: str
    read_only: bool

    @classmethod
    def parse(cls, arg: str) -> Mount:
        read_only = arg.endswith(":ro")
        source, _, target = arg.removesuffix(":ro").partition(":")
        return cls(source, target, read_only)


@dataclass(frozen=True)
class DockerCall:
    """One call to the fake docker."""

    argv: list[str]
    env: dict[str, str]
    # The files flagged when docker ran.
    flags: list[str]
    # The launcher's record of the files it flags, or None when it has none.
    record: list[str] | None


@dataclass(frozen=True)
class Container:
    """The `docker compose run` call that starts the container."""

    # docker compose and its options, up to the run subcommand.
    compose: list[str]
    mounts: list[Mount]
    service: str
    # What runs inside: bash -c <setup> <program> <args>.
    command: list[str]
    env: dict[str, str]
    flags: list[str]
    record: list[str] | None

    @property
    def setup(self) -> str:
        assert self.command[:2] == ["bash", "-c"], self.command
        return self.command[2]

    @property
    def program(self) -> list[str]:
        """The program, claude or bash, and its arguments."""
        return self.command[3:]

    @property
    def notify_dir(self) -> Path:
        return Path(self.mount_at(NOTIFY_TARGET).source)

    def mount_at(self, target: str | Path) -> Mount:
        found = [m for m in self.mounts if m.target == str(target)]
        assert len(found) == 1, f"mounts at {target}: {found}"
        return found[0]

    def has_mount(self, source: str | Path, target: str | Path | None = None, *, ro: bool) -> bool:
        return Mount(str(source), str(target or source), ro) in self.mounts


@dataclass(frozen=True)
class Run:
    returncode: int
    stdout: str
    stderr: str
    calls: list[DockerCall]

    @property
    def started(self) -> bool:
        return bool(self.compose_calls("run"))

    @property
    def container(self) -> Container:
        runs = self.compose_calls("run")
        assert len(runs) == 1, f"compose run calls: {len(runs)}\nstderr:\n{self.stderr}"
        call = runs[0]
        argv = ["docker", *call.argv]
        start = argv.index("run")
        i = start + 1
        mounts: list[Mount] = []
        while argv[i].startswith("-"):
            if argv[i] == "-v":
                mounts.append(Mount.parse(argv[i + 1]))
                i += 2
            else:
                i += 1
        return Container(
            compose=argv[:start],
            mounts=mounts,
            service=argv[i],
            command=argv[i + 1 :],
            env=call.env,
            flags=call.flags,
            record=call.record,
        )

    def compose_calls(self, subcommand: str) -> list[DockerCall]:
        return [c for c in self.calls if _compose_subcommand(c.argv) == subcommand]

    def lines(self) -> list[str]:
        return self.stderr.splitlines()


@dataclass(frozen=True)
class Inside:
    """What the setup text did when start_container ran it."""

    returncode: int
    stderr: str
    # Each `ln` call's arguments.
    links: list[list[str]]
    # $0 and the arguments of the program the setup ran last.
    program: list[str]
    # AGENT_BROWSER_CDP as the program saw it, or None when unset.
    cdp: str | None


class Kit:
    """A test's own home, cache and fake commands, and repos to run in."""

    def __init__(self, work: Path, image_kit: Path) -> None:
        self.work = work
        self.home = work / "home"
        # The folder that holds the repos, so each one's siblings are here.
        self.src = self.home / "src"
        self.fake = work / "fake"
        self.tmp = work / "tmp"
        self.cache = work / "cache"
        for path in (self.src, self.fake / "bin", self.fake / "uv", self.tmp, self.cache):
            path.mkdir(parents=True)
        self._write_tool("docker", f"#!{sys.executable}\n{FAKE_DOCKER}")
        self._write_tool("chflags", FAKE_CHFLAGS)
        self._write_tool("gh", FAKE_GH)
        (self.fake / "uv" / "uv").symlink_to(UV.binary)
        (self.fake / "gh-token").write_text("gho_fake\n")
        self.env: dict[str, str] = {
            "HOME": str(self.home),
            "PATH": f"{self.fake / 'bin'}:{self.fake / 'uv'}:{SYSTEM_PATH}",
            "UV_CACHE_DIR": UV.cache,
            "UV_PYTHON_INSTALL_DIR": UV.pythons,
            "TMPDIR": str(self.tmp),
            "XDG_CACHE_HOME": str(self.cache),
            "FAKE_DIR": str(self.fake),
            "IMAGE_KIT": str(image_kit),
            "GIT_CONFIG_NOSYSTEM": "1",
            "LC_ALL": "C",
        }

    # Repos.

    def git(self, *args: str | Path, cwd: Path | None = None) -> str:
        return subprocess.run(
            ["git", *map(str, args)],
            cwd=cwd,
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def clone(
        self,
        path: Path,
        origin: str | None = None,
        *,
        commit: bool = True,
        bare: bool = False,
    ) -> Path:
        """Makes a clone at path, with an empty first commit unless bare."""
        self.git("init", "-q", *(["--bare"] if bare else []), path)
        if origin:
            self.git("-C", path, "remote", "add", "origin", origin)
        if commit and not bare:
            self.commit(path)
        return path

    def commit(self, repo: Path) -> None:
        self.git(
            "-C", repo, "-c", "user.name=test", "-c", "user.email=test",
            "commit", "-q", "--allow-empty", "-m", "commit",
        )  # fmt: skip

    def main_clone(self, name: str = "Main-Clone", origin: str | None = None) -> Path:
        """Makes a clone with a worktree in each usual place and a submodule.

        A person's worktree goes in .worktrees, and a subagent's in
        .claude/worktrees.
        """
        repo = self.clone(self.src / name, origin)
        self.git("-C", repo, "worktree", "add", "-q", repo / ".claude/worktrees/agent")
        self.git("-C", repo, "worktree", "add", "-q", repo / ".worktrees/other")
        lib = self.clone(self.work / "upstream" / "Lib")
        self.git(
            "-C", repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", lib, "lib",
        )  # fmt: skip
        return repo

    def install(self, repo: Path, base_image: str = BASE_IMAGE) -> Path:
        """Puts the stub and a Dockerfile in repo's docker folder, if missing."""
        docker = repo / "docker"
        docker.mkdir(parents=True, exist_ok=True)
        if not (docker / "cc").exists():
            shutil.copy2(STUB, docker / "cc")
        if not (docker / "Dockerfile").exists():
            (docker / "Dockerfile").write_text(
                f"# The base.\nFROM {base_image} AS base\nRUN true\n"
            )
        return docker

    def settings(
        self,
        repo: Path,
        *,
        project_name: str | None = None,
        writable_siblings: Iterable[str] | None = None,
        start_commands: Iterable[str] | None = None,
        nested_clones: Iterable[str] | None = None,
        agent_browser_port: int | bool | None = None,
    ) -> None:
        """Writes the repo's settings. A setting left as None stays unset.

        start_commands run joined with &&. agent_browser_port=False turns
        agent-browser off.
        """
        # A JSON string or list of strings reads the same in TOML.
        lines: list[str] = []
        if project_name is not None:
            lines.append(f"project_name = {json.dumps(project_name)}")
        for name, values in (
            ("writable_siblings", writable_siblings),
            ("start_commands", start_commands),
            ("nested_clones", nested_clones),
        ):
            if values is not None:
                lines.append(f"{name} = {json.dumps(list(values))}")
        if agent_browser_port is not None:
            port = "false" if agent_browser_port is False else str(int(agent_browser_port))
            lines.append(f"agent_browser_port = {port}")
        self.install(repo)
        (repo / "docker" / "kit.toml").write_text("".join(f"{line}\n" for line in lines))

    def cc_local(self, repo: Path, text: str) -> None:
        self.install(repo)
        (repo / "docker" / "cc.local").write_text(text)

    # Stand-ins.

    def on_run(self, script: str) -> None:
        """Runs the bash script in place of the container.

        It gets NOTIFY_DIR, the folder mounted at /run/cc-notify, and
        DOCKER_PID, the process that took over the launcher's.
        """
        self._write_script(self.fake / "run-hook", script)

    def on_chflags(self, script: str) -> None:
        """Runs the bash script with chflags's arguments before each call."""
        self._write_script(self.fake / "chflags-hook", script)

    def refuse_chflags(self, flag: str, *files: Path) -> None:
        """Makes chflags fail to set (uchg) or clear (nouchg) each file."""
        (self.fake / f"refuse-{flag}").write_text("".join(f"{f}\n" for f in files))

    def compose_binds(self, *targets: Path) -> None:
        """Adds bind mounts to what `docker compose config` prints."""
        (self.fake / "compose-binds").write_text("".join(f"{t}\n" for t in targets))

    def flagged(self) -> list[str]:
        state = self.fake / "flags"
        return sorted(state.read_text().splitlines()) if state.exists() else []

    @property
    def flags_dir(self) -> Path:
        return self.tmp / "cc-flags"

    def path_without(self, *names: str) -> str:
        """A PATH like the tests' own, without the named system commands, nor uv when named."""
        tools = self.fake / "system"
        tools.mkdir(exist_ok=True)
        for folder in SYSTEM_PATH.split(":"):
            for entry in Path(folder).iterdir():
                link = tools / entry.name
                if entry.name not in names and not link.exists():
                    link.symlink_to(entry)
        uv = "" if "uv" in names else f"{self.fake / 'uv'}:"
        return f"{self.fake / 'bin'}:{uv}{tools}"

    # Running.

    def run(self, repo: Path, *args: str, env: Mapping[str, str] | None = None) -> Run:
        """Runs repo's docker/cc with args, and waits for its watcher too."""
        return self.run_command([self.install(repo) / "cc", *args], repo, env)

    def run_command(
        self, command: list[str | Path], cwd: Path, env: Mapping[str, str] | None = None
    ) -> Run:
        log = self.fake / "docker.jsonl"
        log.unlink(missing_ok=True)
        returncode, stdout, stderr = _run_and_reap(command, cwd, {**self.env, **(env or {})})
        calls: list[DockerCall] = []
        if log.exists():
            for line in log.read_text().splitlines():
                data = cast(dict[str, object], json.loads(line))
                calls.append(
                    DockerCall(
                        argv=cast(list[str], data["argv"]),
                        env=cast(dict[str, str], data["env"]),
                        flags=cast(list[str], data["flags"]),
                        record=cast(list[str] | None, data["record"]),
                    )
                )
        return Run(returncode, stdout, stderr, calls)

    def start_container(
        self, container: Container, *, mac_ip: str | None = "192.168.65.254"
    ) -> Inside:
        """Runs the container's command on this machine, with fake ln and getent.

        ln logs its arguments, and getent resolves host.docker.internal to
        mac_ip, or to nothing when mac_ip is None. The program logs its
        arguments and AGENT_BROWSER_CDP.
        """
        sandbox = self.work / "inside"
        shutil.rmtree(sandbox, ignore_errors=True)
        (sandbox / "bin").mkdir(parents=True)
        for name, text in (("ln", FAKE_LN), ("getent", FAKE_GETENT)):
            self._write_script(sandbox / "bin" / name, text)
        program = container.program[0]
        self._write_script(sandbox / "bin" / program, FAKE_PROGRAM)
        if mac_ip is not None:
            (sandbox / "mac-ip").write_text(mac_ip)
        env = {
            **self.env,
            "PATH": f"{sandbox / 'bin'}:{SYSTEM_PATH}",
            "SANDBOX": str(sandbox),
        }
        result = subprocess.run(
            ["/bin/bash", *container.command[1:]],
            cwd=sandbox,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        ln_log = sandbox / "ln.log"
        links = [
            line.removesuffix("\0").split("\0")
            for line in (ln_log.read_text().splitlines() if ln_log.exists() else [])
        ]
        args = sandbox / "program.args"
        cdp = sandbox / "cdp"
        return Inside(
            returncode=result.returncode,
            stderr=result.stderr,
            links=links,
            program=args.read_text().splitlines() if args.exists() else [],
            cdp=cdp.read_text() if cdp.exists() else None,
        )

    # Paths.

    def rel(self, paths: Iterable[str | Path]) -> list[str]:
        """Each path relative to the repos' folder, sorted."""
        return sorted(os.path.relpath(p, self.src) for p in paths)

    def _write_tool(self, name: str, text: str) -> None:
        self._write_script(self.fake / "bin" / name, text)

    @staticmethod
    def _write_script(path: Path, text: str) -> None:
        if not text.startswith("#!"):
            text = f"#!/usr/bin/env bash\nset -euo pipefail\n{text}"
        path.write_text(text)
        path.chmod(0o755)


def read_only(container: Container, kit: Kit, *, under: Iterable[Path] | None = None) -> list[str]:
    """The targets of the read-only mounts, relative to kit.src, sorted.

    With under, only those at or under one of those folders.
    """
    roots = [str(p) for p in under] if under is not None else None
    return kit.rel(
        m.target
        for m in container.mounts
        if m.read_only and (roots is None or any(_is_under(m.target, r) for r in roots))
    )


def folder_mounts(container: Container, kit: Kit) -> list[str]:
    """The writable mounts at their own paths, relative to kit.src, sorted.

    These are the folders above a read-only mount, and each writable sibling.
    """
    return kit.rel(m.target for m in container.mounts if not m.read_only and m.source == m.target)


def hooks_mounts(container: Container, kit: Kit, *roots: Path) -> list[str]:
    """The read-only folder mounts outside every .git, under roots, sorted.

    These are the core.hooksPath folders.
    """
    return kit.rel(
        m.target
        for m in container.mounts
        if m.read_only
        and ".git" not in Path(m.target).parts
        and any(_is_under(m.target, str(r)) for r in roots)
    )


def send_event(event: str) -> str:
    """Run-hook text that sends event as the image's hooks do.

    It waits until the watcher has taken the event from the notify folder.
    """
    return f"""
printf '%s' {shlex.quote(event)} | CC_NOTIFY_DIR="$NOTIFY_DIR" sh '{FORWARD}'
for _ in $(seq 50); do
  [ -n "$(ls "$NOTIFY_DIR")" ] || break
  sleep 0.1
done
"""


def ignores_case(folder: Path) -> bool:
    """Whether the disk that holds folder ignores letter case, as the Mac's does."""
    probe = folder / "case-probe"
    probe.write_text("")
    try:
        return (folder / "CASE-PROBE").exists()
    finally:
        probe.unlink()


def _run_and_reap(
    command: list[str | Path], cwd: Path, env: Mapping[str, str]
) -> tuple[int, str, str]:
    """Runs command, and waits for it and for every process it leaves behind.

    The launcher's watcher keeps stdout and stderr open until it ends, so
    reading them to the end waits for it. The watcher ends once the launcher's
    process has gone. A process that has exited stays until it is reaped, so
    this reaps it first, as a terminal's shell would. subprocess.run reaps only
    after the output ends, and so would wait forever.
    """
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out: dict[str, str] = {}

    def read(name: str, stream: IO[str]) -> None:
        out[name] = stream.read()

    assert proc.stdout is not None
    assert proc.stderr is not None
    # Daemons, so a watcher that never ends fails the test and not the run.
    readers = [
        threading.Thread(target=read, args=("stdout", proc.stdout), daemon=True),
        threading.Thread(target=read, args=("stderr", proc.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        returncode = proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise
    for reader in readers:
        reader.join(timeout=60)
        assert not reader.is_alive(), "the watcher did not end"
    return returncode, out["stdout"], out["stderr"]


def _compose_subcommand(argv: list[str]) -> str | None:
    if argv[:1] != ["compose"]:
        return None
    i = 1
    while i < len(argv) and argv[i] in ("-p", "--project-directory", "-f"):
        i += 2
    return argv[i] if i < len(argv) else None


def _is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")
