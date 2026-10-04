#!/usr/bin/env bash
# Checks collect_sibling_repos in the launcher, kit/cc, without Docker. It builds a
# parent directory of clones and plain folders, then checks that only the
# clones with this repo's origin owner are mounted, each read-only unless
# writable_siblings names it.
set -euo pipefail

# Source the launcher for the real code. It sets repo for itself, so the test
# sets it again below.
# shellcheck source=kit/cc
. "$(dirname "$0")/../kit/cc"

work=$(mktemp -d "${TMPDIR:-/tmp}/cc-siblings-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
# On macOS TMPDIR is a link, and git prints the resolved path.
work=$(cd "$work" && pwd -P)

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# Makes a clone at $1 with origin $2, or with no origin when $2 is empty.
clone() {
  git init -q "$1"
  [ -z "$2" ] || git -C "$1" remote add origin "$2"
}

clone "$work/this" git@github.com:Program-Org/this.git
clone "$work/ssh-sibling" git@github.com:program-org/ssh-sibling.git
clone "$work/https-sibling" https://github.com/Program-Org/https-sibling
clone "$work/url-sibling" ssh://git@github.com/Program-Org/url-sibling.git
clone "$work/other-client" git@github.com:other-client/app.git
clone "$work/other-host" https://gitlab.com/Program-Org/app.git
clone "$work/no-origin" ""
clone "$work/no-origin-sibling" ""
clone "$work/rw-sibling" git@github.com:Program-Org/rw-sibling.git
clone "$work/rw-other-client" git@github.com:other-client/rw-other-client.git
rm -rf "$work/rw-sibling/.git/hooks"
mkdir "$work/partner-folder"
git -C "$work/this" -c user.name=test -c user.email=test commit -q --allow-empty -m init
# Worktrees in both usual places: .worktrees for a person, .claude/worktrees
# for a subagent.
git -C "$work/this" worktree add -q "$work/this/.worktrees/wt"
git -C "$work/this" worktree add -q "$work/this/.claude/worktrees/agent"
# A writable sibling's worktree: its pointer files mount read-only too.
git -C "$work/rw-sibling" -c user.name=test -c user.email=test commit -q --allow-empty -m init
git -C "$work/rw-sibling" worktree add -q "$work/rw-sibling/.worktrees/wt"

# A writable sibling still needs this repo's origin owner.
writable_siblings=(rw-sibling rw-other-client)

expected="-v $work/https-sibling:$work/https-sibling:ro \
-v $work/rw-sibling:$work/rw-sibling \
-v $work/rw-sibling/.git/hooks:$work/rw-sibling/.git/hooks:ro \
-v $work/rw-sibling/.git/config:$work/rw-sibling/.git/config:ro \
-v $work/rw-sibling/.worktrees/wt/.git:$work/rw-sibling/.worktrees/wt/.git:ro \
-v $work/rw-sibling/.git/worktrees/wt/commondir:$work/rw-sibling/.git/worktrees/wt/commondir:ro \
-v $work/rw-sibling/.git/commondir:$work/rw-sibling/.git/commondir:ro \
-v $work/ssh-sibling:$work/ssh-sibling:ro \
-v $work/url-sibling:$work/url-sibling:ro"

# From the main clone, and from a worktree in each place.
for repo in "$work/this" "$work/this/.worktrees/wt" \
  "$work/this/.claude/worktrees/agent"; do
  mounts=()
  collect_sibling_repos 2>/dev/null
  [ "${mounts[*]}" = "$expected" ] ||
    fail "from $repo, mounts were: ${mounts[*]:-none}"
done

# A writable sibling without .git/hooks gets one made on the host, so the
# read-only mount has a source.
[ -d "$work/rw-sibling/.git/hooks" ] || fail "rw-sibling has no .git/hooks"

# The container can write anywhere in a writable sibling, so the pointer
# checks search it as they search the repo.
repo=$work/this
mounts=()
guarded_git_dirs=()
collect_worktree_mounts "$repo"
collect_sibling_repos 2>/dev/null
[ "${writable_dirs[*]}" = "$work/rw-sibling" ] || fail "writable_dirs was: ${writable_dirs[*]:-none}"
[ -z "$(find_git_pointers)" ] || fail "with no nested clone, found: $(find_git_pointers)"
git init -q "$work/rw-sibling/nested"
[ "$(find_git_pointers)" = "$work/rw-sibling/nested/.git" ] ||
  fail "with a clone in a writable sibling, found: $(find_git_pointers)"
rm -rf "$work/rw-sibling/nested"

# A clone whose origin names no owner, such as a local path, mounts nothing:
# the siblings with no origin would otherwise match it.
git -C "$work/no-origin" remote add origin /srv/git/no-origin.git
repo=$work/no-origin
mounts=()
collect_sibling_repos
[ ${#mounts[@]} -eq 0 ] || fail "with a local-path origin, mounts were: ${mounts[*]}"
git -C "$work/no-origin" remote remove origin

# A clone with no origin mounts nothing.
repo=$work/no-origin
mounts=()
collect_sibling_repos
[ ${#mounts[@]} -eq 0 ] || fail "with no origin, mounts were: ${mounts[*]}"

echo "ok"
