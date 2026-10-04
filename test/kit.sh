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

# Prints the -v arguments that mount each source=target pair read-only.
ro() {
  local file
  for file in "$@"; do
    printf -- '-v %s:%s:ro ' "${file%%=*}" "${file#*=}"
  done
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

# Your own git config could set core.hooksPath, which the launcher reads.
export GIT_CONFIG_GLOBAL=$work/gitconfig GIT_CONFIG_NOSYSTEM=1
: >"$GIT_CONFIG_GLOBAL"

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

# A worktree of the submodule has a commondir in the submodule's git
# directory, and it mounts read-only too.
git -C "$work/Main-Clone/lib" worktree add -q "$work/lib-wt"
mounts=()
collect_worktree_mounts "$work/Main-Clone"
file=$lib/worktrees/lib-wt/commondir
is_one_of "$file:$file:ro" "${mounts[@]}" || fail "the submodule's worktree commondir did not mount"
git -C "$work/Main-Clone/lib" worktree remove "$work/lib-wt"

# Sets the mounts for the repo at $1 afresh, as a start does.
mount_repo() {
  use_repo "$1"
  export_compose_env
  mounts=()
  guarded_git_dirs=()
  writable_dirs=()
  nested_clones=()
  collect_worktree_mounts "$repo"
}
# Stands in for docker compose config. The claude service bind-mounts each
# path in compose_binds.
compose_binds=()
fake_compose() {
  jq -n '{services: {claude: {volumes: ($ARGS.positional | map({type: "bind", target: .}))}}}' \
    --args ${compose_binds[@]+"${compose_binds[@]}"}
}
compose=(fake_compose)
# Runs collect_folder_mounts, and prints each folder it mounts, relative to
# $work, sorted, on one line.
added_folders() {
  local before=${#mounts[@]} arg
  collect_folder_mounts
  for arg in "${mounts[@]:before}"; do
    case $arg in -v | *:ro) ;; *) printf '%s\n' "${arg#*:}" ;; esac
  done | sed "s#^$work/##" | LC_ALL=C sort | paste -sd ' ' -
}

# Each folder above a read-only mount inside a writable mount mounts on its
# own, writable. Then the container cannot rename it and move the mount away.
# The writable mounts are the repo and the main clone's .git. The repo's own
# .git is already a mount point, so it needs nothing new.
mount_repo "$work/Main-Clone"
main_folders="Main-Clone/.claude Main-Clone/.claude/worktrees Main-Clone/.claude/worktrees/agent \
Main-Clone/.git/modules Main-Clone/.git/modules/lib Main-Clone/.git/worktrees \
Main-Clone/.git/worktrees/agent Main-Clone/.git/worktrees/other Main-Clone/.worktrees \
Main-Clone/.worktrees/other"
[ "$(added_folders)" = "$main_folders" ] || fail "from the main clone, folder mounts were: $(added_folders)"

# A folder that a compose file mounts is already a mount point. A second
# mount there would hide the compose file's.
mount_repo "$work/Main-Clone"
compose_binds=("$work/Main-Clone/.worktrees")
want="Main-Clone/.claude Main-Clone/.claude/worktrees Main-Clone/.claude/worktrees/agent \
Main-Clone/.git/modules Main-Clone/.git/modules/lib Main-Clone/.git/worktrees \
Main-Clone/.git/worktrees/agent Main-Clone/.git/worktrees/other Main-Clone/.worktrees/other"
[ "$(added_folders)" = "$want" ] ||
  fail "with a compose file mounting .worktrees, folder mounts were: $(added_folders)"
compose_binds=()

# From a worktree, the main clone's folders hold no writable mount, except
# its .git.
mount_repo "$work/Main-Clone/.claude/worktrees/agent"
want="Main-Clone/.git/modules Main-Clone/.git/modules/lib Main-Clone/.git/worktrees \
Main-Clone/.git/worktrees/agent Main-Clone/.git/worktrees/other"
[ "$(added_folders)" = "$want" ] || fail "from a worktree, folder mounts were: $(added_folders)"

# Reached through a link, the repo mounts writable at the link's path, so
# each folder mounts there too.
ln -s "$work/Main-Clone" "$work/Link"
mount_repo "$work/Link"
want="Link/.claude Link/.claude/worktrees Link/.claude/worktrees/agent Link/.git/modules \
Link/.git/modules/lib Link/.git/worktrees Link/.git/worktrees/agent Link/.git/worktrees/other \
Link/.worktrees Link/.worktrees/other"
[ "$(added_folders)" = "$want" ] || fail "through a link, folder mounts were: $(added_folders)"
rm -f "$work/Link"

# A writable sibling's .git holds its read-only hooks and config.
git init -q "$work/Sib"
git -C "$work/Sib" remote add origin git@github.com:program-org/sib.git
git -C "$work/Main-Clone" remote add origin git@github.com:program-org/main-clone.git
writable_siblings=(Sib)
mount_repo "$work/Main-Clone"
collect_sibling_repos 2>/dev/null
[ "$(added_folders)" = "$main_folders Sib/.git" ] ||
  fail "with a writable sibling, folder mounts were: $(added_folders)"
writable_siblings=()
git -C "$work/Main-Clone" remote remove origin
rm -rf "$work/Sib"

# Runs collect_hooks_paths, and prints each path it mounts read-only,
# relative to $work, sorted, on one line.
added_hooks() {
  local before=${#mounts[@]} arg
  collect_hooks_paths
  for arg in "${mounts[@]:before}"; do
    case $arg in *:ro) arg=${arg#*:} && printf '%s\n' "${arg%:ro}" ;; esac
  done | sed "s#^$work/##" | LC_ALL=C sort | paste -sd ' ' -
}

# The folder that core.hooksPath names mounts read-only in each worktree, as
# git on the Mac resolves it there. A missing one is made empty first. The
# folders above it then mount on their own, as for every read-only mount.
mount_repo "$work/Main-Clone"
mkdir -p "$repo/.husky/_"
git -C "$repo" config core.hooksPath .husky/_
want="Main-Clone/.claude/worktrees/agent/.husky/_ Main-Clone/.husky/_ Main-Clone/.worktrees/other/.husky/_"
[ "$(added_hooks)" = "$want" ] || fail "with core.hooksPath, hooks mounts were: $(added_hooks)"
other_hooks=$repo/.worktrees/other/.husky/_
[ -d "$other_hooks" ] && [ -z "$(ls -A "$other_hooks")" ] || fail "the missing $other_hooks was not made empty"
collect_hooks_paths
case " $(added_folders) " in *" Main-Clone/.husky "*) ;; *) fail "with core.hooksPath, folder mounts were: $(added_folders)" ;; esac

# A core.hooksPath that names the repo's .git/hooks needs nothing new.
git -C "$repo" config core.hooksPath .git/hooks
[ -z "$(added_hooks)" ] || fail "with .git/hooks, hooks mounts were: $(added_hooks)"

# Nor does one outside every writable mount.
git -C "$repo" config core.hooksPath "$work/outside-hooks"
[ -z "$(added_hooks)" ] || fail "outside the repo, hooks mounts were: $(added_hooks)"
[ ! -e "$work/outside-hooks" ] || fail "the launcher made a hooks folder outside the repo"

# One from the global config is followed the same way, in each submodule's
# worktree too.
git -C "$repo" config --unset core.hooksPath
git config --global core.hooksPath .githooks
want="Main-Clone/.claude/worktrees/agent/.githooks Main-Clone/.githooks Main-Clone/.worktrees/other/.githooks Main-Clone/lib/.githooks"
[ "$(added_hooks)" = "$want" ] || fail "with a global core.hooksPath, hooks mounts were: $(added_hooks)"
git config --global --unset core.hooksPath

# Git expands ~ in the value. Every worktree then names one folder, which
# mounts once.
# shellcheck disable=SC2088 # git expands it, not the shell
git config --global core.hooksPath "~/Main-Clone/.tilde-hooks"
[ "$(HOME=$work && added_hooks)" = "Main-Clone/.tilde-hooks" ] ||
  fail "with ~ in core.hooksPath, hooks mounts were: $(HOME=$work && added_hooks)"
git config --global --unset core.hooksPath

# A bare repo that nested_clones names resolves its value in its git
# directory.
git init -q --bare "$repo/fixture"
git -C "$repo/fixture" config core.hooksPath fixture-hooks
nested_clones=(fixture)
collect_nested_clones 2>/dev/null
[ "$(added_hooks)" = "Main-Clone/fixture/fixture-hooks" ] ||
  fail "in a bare repo, hooks mounts were: $(added_hooks)"
rm -rf "$repo/fixture"

# A writable sibling and a nested clone that set core.hooksPath.
git init -q "$work/Sib"
git -C "$work/Sib" remote add origin git@github.com:program-org/sib.git
git -C "$work/Sib" config core.hooksPath sib-hooks
git -C "$repo" remote add origin git@github.com:program-org/main-clone.git
git init -q "$repo/vendor/dep"
git -C "$repo/vendor/dep" config core.hooksPath dep-hooks
writable_siblings=(Sib)
mount_repo "$work/Main-Clone"
collect_sibling_repos 2>/dev/null
nested_clones=(vendor/dep)
collect_nested_clones 2>/dev/null
[ "$(added_hooks)" = "Main-Clone/vendor/dep/dep-hooks Sib/sib-hooks" ] ||
  fail "in a writable sibling and a nested clone, hooks mounts were: $(added_hooks)"
writable_siblings=()
git -C "$repo" remote remove origin
rm -rf "$work/Sib" "$repo/vendor"

# Exits unless collect_hooks_paths refuses core.hooksPath = $1 in the repo.
expect_hooks_refused() {
  local err
  git -C "$repo" config core.hooksPath "$1"
  mount_repo "$work/Main-Clone"
  err=$( (collect_hooks_paths) 2>&1) && fail "started with core.hooksPath = $1"
  case $err in *"did not start"*"  $repo: core.hooksPath = $1"*) ;; *) fail "with $1, refusal was: $err" ;; esac
}

# The repo's root, a folder that holds it, and a link each stop the start.
# So does a folder that sits below a link: the container could point the
# link elsewhere.
mkdir -p "$work/real-hooks"
ln -s "$work/real-hooks" "$repo/hooks-link"
ln -s "$work" "$repo/up-link"
expect_hooks_refused .
expect_hooks_refused ..
expect_hooks_refused hooks-link
expect_hooks_refused up-link/real-hooks
git -C "$repo" config --unset core.hooksPath
rm -f "$repo/hooks-link" "$repo/up-link"
rm -rf "$repo/.husky" "$repo/.claude/worktrees/agent/.husky" "$repo/.worktrees/other/.husky" \
  "$repo/.githooks" "$repo/.claude/worktrees/agent/.githooks" "$repo/.worktrees/other/.githooks" \
  "$repo/lib/.githooks" "$repo/.tilde-hooks"

# The pointer checks. guard_main sets the mounts for the main clone afresh,
# as a start does.
guard_main() {
  mount_repo "$work/Main-Clone"
}
# Fails unless find_git_pointers prints exactly the lines in $1.
expect_pointers() {
  local found
  found=$(find_git_pointers)
  [ "$found" = "$1" ] || fail "$2: find_git_pointers printed: ${found:-nothing}"
}
git init -q "$work/evil"

# The repo as a start finds it: its .git, its worktrees and its submodule
# all lead to guarded git directories.
guard_main
expect_pointers "" "from the main clone"

# A start by a path in other letter case, as a shell's cd can leave it, still
# finds the repo's own .git guarded. Inside, the repo mounts at that path, so
# each file also mounts there. Only a disk that ignores case, such as the
# Mac's, can check this.
if [ -d "$work/main-clone" ]; then
  mount_repo "$work/main-clone"
  expect_pointers "" "from the main clone by a path in lower case"
  is_one_of "$main_dir:$repo/.git/commondir:ro" "${mounts[@]}" ||
    fail "by a path in lower case, mounts were: ${mounts[*]:-none}"
  # Each folder above it mounts at that path too.
  collect_folder_mounts
  is_one_of "$repo/.git/worktrees:$repo/.git/worktrees" "${mounts[@]}" ||
    fail "by a path in lower case, folder mounts were: ${mounts[*]:-none}"
  guard_main
fi

# A clone inside the repo is refused, until nested_clones names it. Then its
# hooks, config and commondir mount read-only, as a writable sibling's do.
git init -q "$repo/vendor/dep"
expect_pointers "$repo/vendor/dep/.git" "with a nested clone"
nested_clones=(vendor/dep/)
collect_nested_clones 2>/dev/null
expect_pointers "" "with nested_clones naming the clone"
dep=$repo/vendor/dep/.git
for file in "$dep/hooks" "$dep/config" "$dep/commondir"; do
  is_one_of "$file:$file:ro" "${mounts[@]}" || fail "$file did not mount read-only"
done
# Each folder from the repo's root down to the clone's .git mounts on its own.
[ "$(added_folders)" = "$main_folders Main-Clone/vendor Main-Clone/vendor/dep Main-Clone/vendor/dep/.git" ] ||
  fail "with a nested clone, folder mounts were: $(added_folders)"
rm -rf "$repo/vendor"

# A .git file that names another repo's git directory.
mkdir -p "$repo/elsewhere"
printf 'gitdir: %s\n' "$work/evil/.git" >"$repo/elsewhere/.git"
guard_main
expect_pointers "$repo/elsewhere/.git" "with a .git file naming another repo"
rm -rf "$repo/elsewhere"

# A bare repo, which needs no .git for git to find it.
git init -q --bare "$repo/bare"
expect_pointers "$repo/bare/HEAD" "with a bare repo"
rm -rf "$repo/bare"

# Git looks for objects and refs in the directory a commondir names, and
# takes them as files too.
mkdir -p "$repo/odd/common" "$repo/odd/files"
printf 'ref: refs/heads/main\n' >"$repo/odd/common/HEAD"
printf '%s\n' "$work/evil/.git" >"$repo/odd/common/commondir"
expect_pointers "$repo/odd/common/HEAD" "with a HEAD beside a commondir"
rm -rf "$repo/odd/common"
printf 'ref: refs/heads/main\n' >"$repo/odd/files/HEAD"
touch "$repo/odd/files/objects" "$repo/odd/files/refs"
chmod +x "$repo/odd/files/objects" "$repo/odd/files/refs"
expect_pointers "$repo/odd/files/HEAD" "with objects and refs as files"
rm -rf "$repo/odd"

# nested_clones can name a bare repo, such as a test fixture. Its hooks,
# config and commondir then mount read-only, and its HEAD passes.
git init -q --bare "$repo/fixture"
nested_clones=(fixture)
collect_nested_clones 2>/dev/null
expect_pointers "" "with nested_clones naming a bare repo"
for file in "$repo/fixture/hooks" "$repo/fixture/config" "$repo/fixture/commondir"; do
  is_one_of "$file:$file:ro" "${mounts[@]}" || fail "$file did not mount read-only"
done
rm -rf "$repo/fixture"
guard_main

# The Mac's disk ignores case, so git takes .GIT for .git and head for HEAD.
# Git also takes a HEAD that is a link.
git init -q "$repo/upper" && mv "$repo/upper/.git" "$repo/upper/.GIT"
expect_pointers "$repo/upper/.GIT" "with a .GIT directory"
rm -rf "$repo/upper"
git init -q --bare "$repo/bare"
mv "$repo/bare/HEAD" "$repo/bare/head"
expect_pointers "$repo/bare/head" "with a bare repo's head in lower case"
rm "$repo/bare/head" && ln -s refs/heads/main "$repo/bare/HEAD"
expect_pointers "$repo/bare/HEAD" "with a bare repo's HEAD as a link"
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
expect_pointers "$work/linked/.git/commondir" "with a commondir link"
rm -rf "$work/linked"
guard_main

# A worktree made during the session passes while its commondir names the
# main clone's .git. One that names another repo's does not.
git -C "$repo" worktree add -q "$repo/.claude/worktrees/new"
new=$work/Main-Clone/.git/worktrees/new
expect_pointers "" "with a worktree made during the session"
printf '%s\n' "$work/evil/.git" >"$new/commondir"
expect_pointers "$repo/.claude/worktrees/new/.git" "with a worktree's commondir naming another repo"
printf '../..\n' >"$new/commondir"

# With extensions.worktreeConfig on, its config.worktree did not mount, so it
# passes only while empty.
git -C "$repo" config extensions.worktreeConfig true
printf '[core]\n\thooksPath = /tmp\n' >"$new/config.worktree"
expect_pointers "$repo/.claude/worktrees/new/.git" "with a new worktree's config.worktree"
: >"$new/config.worktree"
expect_pointers "" "with a new worktree's empty config.worktree"
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
# repo. A submodule's git directory in a moved .git can still be opened, so
# the next search finds it and moves its HEAD aside.
guard_main
git init -q "$repo/vendor/dep"
git init -q --bare "$repo/vendor/dep/.git/modules/sub"
git init -q --bare "$repo/bare"
notify_log=$work/notify.log
notify_host() { cat; }
blocked=()
block_git_pointers
[ ${#blocked[@]} -eq 2 ] || fail "blocked was: ${blocked[*]:-none}"
[ -f "$repo/vendor/dep/.git.cc-blocked/HEAD.cc-blocked" ] || fail "the nested .git did not move aside"
[ -f "$repo/bare/HEAD.cc-blocked" ] || fail "the bare repo's HEAD did not move aside"
grep -qF "docker/cc blocked $repo/bare/HEAD" "$notify_log" || fail "the notifier heard: $(cat "$notify_log")"
# The event is JSON, so a tab or newline in a path is escaped.
[ "$(json_escape $'a\tb"c\\d\ne')" = 'a\tb\"c\\d\ne' ] ||
  fail "json_escape printed: $(json_escape $'a\tb"c\\d\ne')"
sub=$repo/vendor/dep/.git.cc-blocked/modules/sub
expect_pointers "$sub/HEAD" "after moving each one aside"
block_git_pointers
expect_pointers "" "after moving the moved .git's submodule aside"

# A name the container chooses hides nothing.
git init -q "$repo/x.cc-blocked/dep"
expect_pointers "$repo/x.cc-blocked/dep/.git" "inside a folder named like a moved one"
rm -rf "$repo/vendor" "$repo/bare" "$repo/x.cc-blocked"

# A TERM that ends the watcher still runs the last search, names what it
# moved aside and removes its directory. The watcher has set its traps once
# it takes the event.
git init -q "$repo/late"
notify_dir=$(mktemp -d "$work/notify.XXXXXX")
printf '{}\n' >"$notify_dir/event.json"
watch_events 2>"$work/watch.err" &
watcher=$!
for _ in $(seq 50); do
  [ -e "$notify_dir/event.json" ] || break
  sleep 0.1
done
kill -TERM "$watcher"
wait "$watcher" || true
[ -d "$repo/late/.git.cc-blocked" ] || fail "after a TERM, the watcher did not move the clone aside"
grep -qF "  $repo/late/.git" "$work/watch.err" || fail "after a TERM, the watcher printed: $(cat "$work/watch.err")"
[ ! -d "$notify_dir" ] || fail "after a TERM, the watcher left $notify_dir"
rm -rf "$repo/late"
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
