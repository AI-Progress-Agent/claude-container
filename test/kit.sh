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
# inside the clone gets the main clone's .git too, but read-only. Git there
# stops at the repo's mount, so nothing inside needs to write it. Outside
# git, REPO_GIT is the repo's .git.
mkdir -p "$work/Main-Clone/sub/docker"
for repo_and_git in "$work/Main-Clone:$work/Main-Clone/.git:rw" \
  "$work/Main-Clone/.claude/worktrees/agent:$work/Main-Clone/.git:rw" \
  "$work/Main-Clone/sub:$work/Main-Clone/.git:ro" \
  "$work/My.App:$work/My.App/.git:rw"; do
  use_repo "${repo_and_git%%:*}"
  export_compose_env
  [ "$REPO_GIT:$REPO_GIT_MODE" = "${repo_and_git#*:}" ] ||
    fail "from $repo, REPO_GIT was $REPO_GIT:$REPO_GIT_MODE"
done

# Each worktree's .git file and the commondir file in its git directory tell
# git where to find the hooks and config. They mount read-only, so the
# container cannot point git on the Mac at a config of its own. That holds
# for every worktree of the clone, not only the one docker/cc runs from. The
# main clone's .git gets a commondir that names itself, read-only, so the
# container cannot write one that names another.
git -C "$work/Main-Clone" worktree add -q "$work/Main-Clone/.worktrees/other"
ro() {
  local file
  for file in "$@"; do
    printf -- '-v %s:%s:ro ' "${file%%=*}" "${file#*=}"
  done
}
agent=$work/Main-Clone/.claude/worktrees/agent/.git
other=$work/Main-Clone/.worktrees/other/.git
agent_dir=$work/Main-Clone/.git/worktrees/agent/commondir
other_dir=$work/Main-Clone/.git/worktrees/other/commondir
main_dir=$work/Main-Clone/.git/commondir
want=$(ro "$agent=$agent" "$other=$other" "$agent_dir=$agent_dir" "$other_dir=$other_dir" \
  "$main_dir=$main_dir")
for dir in "$work/Main-Clone/.claude/worktrees/agent" "$work/Main-Clone"; do
  mounts=()
  collect_worktree_mounts "$dir"
  [ "${mounts[*]} " = "$want" ] || fail "from $dir, mounts were: ${mounts[*]:-none}"
done
mounts=()
collect_worktree_mounts "$work/My.App"
[ ${#mounts[@]} -eq 0 ] || fail "outside git, mounts were: ${mounts[*]}"

# The commondir names the directory as ../.git, which git reads as no
# commondir at all. libgit2 cannot open a repo whose commondir is ".".
[ "$(cat "$main_dir")" = ../.git ] || fail "the main clone's commondir was: $(cat "$main_dir")"
[ "$(git_common_dir "$work/Main-Clone")" = "$work/Main-Clone/.git" ] ||
  fail "with its commondir, the main clone's common dir was $(git_common_dir "$work/Main-Clone")"

# Reached through a symbolic link, the container writes each file by the
# link's path, and git names it by its real path. Each mounts at both.
ln -s "$work/Main-Clone" "$work/Link"
want=$(ro "$agent=$agent" "$agent=$work/Link/.claude/worktrees/agent/.git" \
  "$other=$other" "$other=$work/Link/.worktrees/other/.git" \
  "$agent_dir=$agent_dir" "$agent_dir=$work/Link/.git/worktrees/agent/commondir" \
  "$other_dir=$other_dir" "$other_dir=$work/Link/.git/worktrees/other/commondir" \
  "$main_dir=$main_dir" "$main_dir=$work/Link/.git/commondir")
mounts=()
collect_worktree_mounts "$work/Link"
[ "${mounts[*]} " = "$want" ] || fail "through a link, mounts were: ${mounts[*]:-none}"
rm -f "$work/Link"

# With extensions.worktreeConfig on, git also reads each git directory's
# config.worktree. Each mounts read-only, and a missing one is made empty
# first.
git -C "$work/Main-Clone" config extensions.worktreeConfig true
main_config=$work/Main-Clone/.git/config.worktree
agent_config=$work/Main-Clone/.git/worktrees/agent/config.worktree
other_config=$work/Main-Clone/.git/worktrees/other/config.worktree
want=$(ro "$agent=$agent" "$other=$other" "$agent_dir=$agent_dir" "$other_dir=$other_dir" \
  "$main_dir=$main_dir" "$main_config=$main_config" "$agent_config=$agent_config" \
  "$other_config=$other_config")
mounts=()
collect_worktree_mounts "$work/Main-Clone"
[ "${mounts[*]} " = "$want" ] || fail "with worktreeConfig, mounts were: ${mounts[*]:-none}"
[ -f "$other_config" ] || fail "with worktreeConfig, $other_config was not made"
git -C "$work/Main-Clone" config --unset extensions.worktreeConfig

# A submodule's git directory, under modules/ in the main clone's .git, is a
# git directory of its own. Git in the repo runs git there, so its hooks,
# config and commondir mount read-only too.
git init -q "$work/Lib"
git -C "$work/Lib" -c user.name=test -c user.email=test commit -q --allow-empty -m init
git -C "$work/Main-Clone" -c protocol.file.allow=always submodule add -q "$work/Lib" lib 2>/dev/null
lib=$work/Main-Clone/.git/modules/lib
want=$(ro "$lib/hooks=$lib/hooks" "$lib/config=$lib/config" "$lib/commondir=$lib/commondir")
mounts=()
collect_worktree_mounts "$work/Main-Clone"
case "${mounts[*]} " in *"$want") ;; *) fail "with a submodule, mounts were: ${mounts[*]:-none}" ;; esac
[ "$(cat "$lib/commondir")" = ../lib ] || fail "the submodule's commondir was: $(cat "$lib/commondir")"
git -C "$work/Main-Clone/lib" status --short >/dev/null || fail "git fails in the submodule with its commondir"

# The pointer checks. guard_main sets the mounts for the main clone afresh,
# as a start does.
guard_main() {
  use_repo "$work/Main-Clone"
  mounts=()
  guarded_git_dirs=()
  writable_dirs=()
  nested_clones=()
  collect_worktree_mounts "$repo"
}
# Fails unless find_git_pointers prints exactly the lines in $1.
pointers() {
  local found
  found=$(find_git_pointers)
  [ "$found" = "$1" ] || fail "$2: find_git_pointers printed: ${found:-nothing}"
}
git init -q "$work/evil"

# The repo as a start finds it: its .git, its worktrees and its submodule
# all lead to guarded git directories.
guard_main
pointers "" "from the main clone"

# A clone inside the repo is refused, until nested_clones names it. Then its
# hooks, config and commondir mount read-only, as a writable sibling's do.
git init -q "$repo/vendor/dep"
pointers "$repo/vendor/dep/.git" "with a nested clone"
nested_clones=(vendor/dep/)
collect_nested_clones 2>/dev/null
pointers "" "with nested_clones naming the clone"
dep=$repo/vendor/dep/.git
for file in "$dep/hooks" "$dep/config" "$dep/commondir"; do
  is_one_of "$file:$file:ro" "${mounts[@]}" || fail "$file did not mount read-only"
done
rm -rf "$repo/vendor"

# A .git file that names another repo's git directory.
mkdir -p "$repo/elsewhere"
printf 'gitdir: %s\n' "$work/evil/.git" >"$repo/elsewhere/.git"
guard_main
pointers "$repo/elsewhere/.git" "with a .git file naming another repo"
rm -rf "$repo/elsewhere"

# A bare repo, which needs no .git for git to find it.
git init -q --bare "$repo/bare"
pointers "$repo/bare/HEAD" "with a bare repo"
rm -rf "$repo/bare"

# The Mac's disk ignores case, so git takes .GIT for .git and head for HEAD.
# Git also takes a HEAD that is a link.
git init -q "$repo/upper" && mv "$repo/upper/.git" "$repo/upper/.GIT"
pointers "$repo/upper/.GIT" "with a .GIT directory"
rm -rf "$repo/upper"
git init -q --bare "$repo/bare"
mv "$repo/bare/HEAD" "$repo/bare/head"
pointers "$repo/bare/head" "with a bare repo's head in lower case"
rm "$repo/bare/head" && ln -s refs/heads/main "$repo/bare/HEAD"
pointers "$repo/bare/HEAD" "with a bare repo's HEAD as a link"
rm -rf "$repo/bare"

# A commondir that is a link is never written through or mounted, and a
# start refuses it.
git init -q "$work/linked"
ln -s "$work/written-through" "$work/linked/.git/commondir"
guard_main
guard_git_dir "$work/linked/.git" "$work/linked" "$work/linked"
[ ! -e "$work/written-through" ] || fail "the launcher wrote through a commondir link"
is_one_of "$work/linked/.git/commondir:$work/linked/.git/commondir:ro" "${mounts[@]}" &&
  fail "a commondir link mounted"
pointers "$work/linked/.git/commondir" "with a commondir link"
rm -rf "$work/linked"
guard_main

# A worktree made during the session passes while its commondir names the
# main clone's .git. One that names another repo's does not.
git -C "$repo" worktree add -q "$repo/.claude/worktrees/new"
new=$work/Main-Clone/.git/worktrees/new
pointers "" "with a worktree made during the session"
printf '%s\n' "$work/evil/.git" >"$new/commondir"
pointers "$repo/.claude/worktrees/new/.git" "with a worktree's commondir naming another repo"
printf '../..\n' >"$new/commondir"

# With extensions.worktreeConfig on, its config.worktree did not mount, so it
# passes only while empty.
git -C "$repo" config extensions.worktreeConfig true
printf '[core]\n\thooksPath = /tmp\n' >"$new/config.worktree"
pointers "$repo/.claude/worktrees/new/.git" "with a new worktree's config.worktree"
: >"$new/config.worktree"
pointers "" "with a new worktree's empty config.worktree"
git -C "$repo" config --unset extensions.worktreeConfig
git -C "$repo" worktree remove --force "$repo/.claude/worktrees/new"

# A start refuses a commondir in the main clone's .git that names another
# repo. Git then reads the other repo's config, so the main clone's .git no
# longer counts as guarded.
printf '%s\n' "$work/evil/.git" >"$repo/.git/commondir"
guard_main
err=$( (refuse_git_pointers) 2>&1) && fail "started with a commondir naming another repo"
case $err in *"did not start"*"  $repo/.git"*) ;; *) fail "refusal was: $err" ;; esac
printf '../.git\n' >"$repo/.git/commondir"

# During a session, each one found moves aside, and notify_host hears of it.
# A directory's HEAD moves aside too, so git does not take it for a bare
# repo.
guard_main
git init -q "$repo/vendor/dep"
git init -q --bare "$repo/bare"
notify_log=$work/notify.log
notify_host() { cat; }
blocked=()
block_git_pointers
[ ${#blocked[@]} -eq 2 ] || fail "blocked was: ${blocked[*]:-none}"
[ -f "$repo/vendor/dep/.git.cc-blocked/HEAD.cc-blocked" ] || fail "the nested .git did not move aside"
[ -f "$repo/bare/HEAD.cc-blocked" ] || fail "the bare repo's HEAD did not move aside"
grep -qF "docker/cc blocked $repo/bare/HEAD" "$notify_log" || fail "the notifier heard: $(cat "$notify_log")"
pointers "" "after moving each one aside"
rm -rf "$repo/vendor" "$repo/bare"
unset -f notify_host

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
is_one_of plugins "${writable_siblings[@]}" || fail "kit.sh writable_siblings was ${writable_siblings[*]}"
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
