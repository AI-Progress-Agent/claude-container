#!/usr/bin/env bash
# Checks the stub, stub/cc, and how the launcher, kit/cc, reads a repo's
# settings, without Docker. A fake docker command on the PATH logs each call
# and copies a fake kit where `docker cp` would copy the image's.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/cc-kit-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
# On macOS TMPDIR is a link, and git prints the resolved path.
work=$(cd "$work" && pwd -P)

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# The stub.

mkdir -p "$work/bin" "$work/image-kit" "$work/dev/kit" "$work/repo/docker"
cat >"$work/bin/docker" <<'EOF'
#!/usr/bin/env bash
echo "$*" >>"$DOCKER_LOG"
case $1 in
  create) echo container-id ;;
  cp) cp -R "$IMAGE_KIT/." "$3" ;;
esac
EOF
# Each fake launcher prints which one it is and what the stub handed it.
for kit in image:"$work/image-kit" dev:"$work/dev/kit"; do
  # shellcheck disable=SC2016 # expands when the fake launcher runs
  printf '#!/usr/bin/env bash\necho "%s $KIT_DOCKER_DIR $KIT_BASE_IMAGE ${KIT_DEV:-} $*"\n' \
    "${kit%%:*}" >"${kit#*:}/cc"
done
chmod 0755 "$work/bin/docker" "$work/image-kit/cc" "$work/dev/kit/cc"
cp "$root/stub/cc" "$work/repo/docker/cc"

image=ghcr.io/ai-progress-agent/claude-container:v1.2.3@sha256:0123abcd
printf '# The base.\nFROM %s AS base\nRUN true\n' "$image" >"$work/repo/docker/Dockerfile"

export DOCKER_LOG=$work/docker.log IMAGE_KIT=$work/image-kit
export XDG_CACHE_HOME=$work/cache PATH=$work/bin:$PATH
unset KIT_DEV
cached=$work/cache/claude-container/v1.2.3

# The first run copies the image's kit into the cache for its tag, and runs it.
out=$("$work/repo/docker/cc" shell --x)
[ "$out" = "image $work/repo/docker $image  shell --x" ] ||
  fail "first run printed: $out"
[ -x "$cached/cc" ] || fail "no kit at $cached"
grep -qx "create $image" "$DOCKER_LOG" || fail "docker calls were: $(cat "$DOCKER_LOG")"
[ -z "$(find "$work/cache/claude-container" -name 'v1.2.3.*')" ] ||
  fail "a temporary copy was left in the cache"

# A cached kit without its launcher, as a crash could leave, is replaced.
rm "$cached/cc"
"$work/repo/docker/cc" >/dev/null
[ -x "$cached/cc" ] || fail "a broken kit was not replaced"

# A later run uses the cached kit and calls docker for nothing.
: >"$DOCKER_LOG"
"$work/repo/docker/cc" >/dev/null
[ ! -s "$DOCKER_LOG" ] || fail "second run called docker: $(cat "$DOCKER_LOG")"

# KIT_DEV runs the checkout's launcher, even by a relative path, and hands
# it the absolute path.
out=$(cd "$work" && KIT_DEV=dev repo/docker/cc build)
[ "$out" = "dev $work/repo/docker $image $work/dev build" ] ||
  fail "KIT_DEV run printed: $out"

# A Dockerfile on another base image stops the stub before docker runs.
printf 'FROM debian:bookworm-slim\n' >"$work/repo/docker/Dockerfile"
: >"$DOCKER_LOG"
if err=$("$work/repo/docker/cc" 2>&1); then fail "ran with no claude-container FROM line"; fi
case $err in *"has no FROM"*) ;; *) fail "error was: $err" ;; esac
[ ! -s "$DOCKER_LOG" ] || fail "called docker with no FROM line"

# The launcher's settings.

# Source the launcher for the real code. It reads KIT_DOCKER_DIR once, so the
# test sets docker_dir and repo itself for each case.
# shellcheck source=kit/cc
. "$root/kit/cc"

mkdir -p "$work/Main-Clone/docker"
git init -q "$work/Main-Clone"
git -C "$work/Main-Clone" -c user.name=test -c user.email=test commit -q --allow-empty -m init
git -C "$work/Main-Clone" worktree add -q "$work/Main-Clone/.claude/worktrees/agent"

# Points the launcher at the repo at $1.
use_repo() {
  repo=$1
  docker_dir=$1/docker
}

# The defaults, from the main clone and from a worktree: the project is named
# after the main clone, in lower case, so a worktree shares its login.
for dir in "$work/Main-Clone" "$work/Main-Clone/.claude/worktrees/agent"; do
  use_repo "$dir"
  load_repo_config
  [ "$project_name" = main-clone-claude ] || fail "from $dir, project_name was $project_name"
done
[ ${#writable_siblings[@]} -eq 0 ] || fail "writable_siblings was ${writable_siblings[*]}"
[ "$start_commands" = true ] || fail "start_commands was $start_commands"
[ -z "$agent_browser_port" ] || fail "with no agent-browser.json, the port was $agent_browser_port"
[ -z "$(browser_setup)" ] || fail "with no port, browser_setup printed: $(browser_setup)"

# A directory name Compose would refuse becomes one it takes.
mkdir -p "$work/My.App/docker"
use_repo "$work/My.App"
load_repo_config
[ "$project_name" = my-app-claude ] || fail "from My.App, project_name was $project_name"

# REPO_GIT is the git directory whose hooks and config compose.yaml mounts
# read-only. From the main clone, it is the clone's .git. A worktree's .git is
# a file, so from a worktree REPO_GIT is the main clone's .git. A directory
# inside the clone gets the same. Outside git, REPO_GIT is the repo's .git.
mkdir -p "$work/Main-Clone/sub/docker"
for pair in "$work/Main-Clone:$work/Main-Clone/.git" \
  "$work/Main-Clone/.claude/worktrees/agent:$work/Main-Clone/.git" \
  "$work/Main-Clone/sub:$work/Main-Clone/.git" \
  "$work/My.App:$work/My.App/.git"; do
  use_repo "${pair%%:*}"
  export_compose_env
  [ "$REPO_GIT" = "${pair#*:}" ] || fail "from $repo, REPO_GIT was $REPO_GIT"
done

# A worktree's .git file and the commondir file in its git directory tell git
# where to find the hooks and config. They mount read-only, so the container
# cannot point git on the Mac at a config of its own. That holds for every
# worktree of the clone, not only the one docker/cc runs from.
git -C "$work/Main-Clone" worktree add -q "$work/Main-Clone/.worktrees/other"
commondirs="-v $work/Main-Clone/.git/worktrees/agent/commondir:$work/Main-Clone/.git/worktrees/agent/commondir:ro \
-v $work/Main-Clone/.git/worktrees/other/commondir:$work/Main-Clone/.git/worktrees/other/commondir:ro"
use_repo "$work/Main-Clone/.claude/worktrees/agent"
export_compose_env
mounts=()
collect_worktree_mounts
[ "${mounts[*]}" = "-v $repo/.git:$repo/.git:ro $commondirs" ] ||
  fail "from a worktree, mounts were: ${mounts[*]:-none}"
use_repo "$work/Main-Clone"
export_compose_env
mounts=()
collect_worktree_mounts
[ "${mounts[*]}" = "$commondirs" ] || fail "from the main clone, mounts were: ${mounts[*]:-none}"
use_repo "$work/My.App"
export_compose_env
mounts=()
collect_worktree_mounts
[ ${#mounts[@]} -eq 0 ] || fail "outside git, mounts were: ${mounts[*]}"

# The port comes from agent-browser.json, and the browser setup names it.
use_repo "$work/Main-Clone"
printf '{\n  "cdp": "10099"\n}\n' >"$repo/agent-browser.json"
load_repo_config
[ "$agent_browser_port" = 10099 ] || fail "the port was $agent_browser_port"
case $(browser_setup) in *":10099"*" && ") ;; *) fail "browser_setup printed: $(browser_setup)" ;; esac

# A minified file gives the same port.
printf '{"cdp":"10099","contentBoundary":true}\n' >"$repo/agent-browser.json"
load_repo_config
[ "$agent_browser_port" = 10099 ] || fail "from minified JSON, the port was $agent_browser_port"

# A file with no port warns and goes on, so kit.sh can still set one.
printf '{}\n' >"$repo/agent-browser.json"
err=$(load_repo_config 2>&1) || fail "load_repo_config failed with no cdp port"
case $err in *"names no cdp port"*) ;; *) fail "no warning for a missing port: $err" ;; esac
rm "$repo/agent-browser.json"

# docker/kit.sh overrides every default.
cat >"$docker_dir/kit.sh" <<'EOF'
project_name=other-claude
writable_siblings=(plugins)
start_commands='pnpm install'
agent_browser_port=
EOF
load_repo_config
[ "$project_name" = other-claude ] || fail "kit.sh project_name was $project_name"
is_writable_sibling plugins || fail "kit.sh writable_siblings was ${writable_siblings[*]}"
[ "$start_commands" = 'pnpm install' ] || fail "kit.sh start_commands was $start_commands"
[ -z "$agent_browser_port" ] || fail "kit.sh port was $agent_browser_port"

# The compose files stack in order: the kit's, the repo's, then yours.
touch "$docker_dir/compose.local.yaml" "$docker_dir/compose.repo.yaml"
build_compose_command
[ "${compose[*]}" = "docker compose -p other-claude --project-directory $docker_dir -f $root/kit/compose.yaml -f $docker_dir/compose.repo.yaml -f $docker_dir/compose.local.yaml" ] ||
  fail "compose was: ${compose[*]}"

# Under KIT_DEV, one more file builds the repo's image on the dev base, by
# naming a build context after the FROM line's image.
KIT_DEV=$work/dev KIT_BASE_IMAGE=$image
build_compose_command
override=${compose[${#compose[@]} - 1]}
grep -qxF "        \"$image\": docker-image://claude-container:dev" "$override" ||
  fail "the KIT_DEV file was: $(cat "$override")"

echo "ok"
