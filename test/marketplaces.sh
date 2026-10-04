#!/usr/bin/env bash
# Checks collect_marketplaces in the launcher, kit/cc, without Docker. It
# writes a known_marketplaces.json and a settings.json with folder
# marketplaces in each state. It then checks that only the folders the
# container does not already see are mounted, each read-only. It also checks
# that skip_marketplaces keeps one out.
set -euo pipefail

# Source the launcher for the real code. It sets repo for itself, so the test
# sets it again below.
# shellcheck source=kit/cc
. "$(dirname "$0")/../kit/cc"

work=$(mktemp -d "${TMPDIR:-/tmp}/cc-marketplaces-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
work=$(cd "$work" && pwd -P)

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# Stands in for docker compose. It logs each call and prints a config with
# one bind mount and one named volume.
fake_compose() {
  echo "$*" >>"$work/compose.log"
  printf '{"services":{"claude":{"volumes":[%s,%s]}}}\n' \
    "{\"type\":\"bind\",\"source\":\"$work/dotfiles\",\"target\":\"$work/dotfiles\"}" \
    "{\"type\":\"volume\",\"source\":\"login\",\"target\":\"$work/volume\"}"
}

# Writes known_marketplaces.json from name=path pairs, each a folder
# marketplace, plus one from GitHub that never mounts.
write_known() {
  local pair entries='"github":{"source":{"source":"github","repo":"o/r"},"installLocation":"/x"}'
  for pair in "$@"; do
    entries+=",\"${pair%%=*}\":{\"source\":{\"source\":\"directory\",\"path\":\"${pair#*=}\"},\"installLocation\":\"${pair#*=}\"}"
  done
  printf '{%s}\n' "$entries" >"$HOME/.claude/plugins/known_marketplaces.json"
}

# A mise shim for jq stops working once HOME moves, so put the system's jq
# first on the PATH. macOS 15 and later, and CI's runners, have one.
mkdir -p "$work/bin"
ln -s "$(PATH=/usr/bin:$PATH command -v jq)" "$work/bin/jq"
PATH=$work/bin:$PATH
export HOME=$work/home
mkdir -p "$HOME/.claude/plugins" "$work/repo/plugins" "$work/sibling/m" \
  "$work/dotfiles/m" "$work/volume/m" "$work/src/mine/inner" \
  "$work/src/client" "$work/src/legacy"
repo=$work/repo
compose=(fake_compose)

# Named so that a sort by name would put the nested folder before its
# parent.
write_known \
  in-repo="$work/repo/plugins" \
  in-sibling="$work/sibling/m" \
  in-compose="$work/dotfiles/m" \
  in-volume="$work/volume/m" \
  a-nested="$work/src/mine/inner" \
  mine="$work/src/mine" \
  client="$work/src/client" \
  gone="$work/src/gone"
# A marketplace recorded with no installLocation mounts from its source path.
jq --arg p "$work/src/legacy" '.legacy = {source: {source: "directory", path: $p}}' \
  "$HOME/.claude/plugins/known_marketplaces.json" >"$work/known.json"
mv "$work/known.json" "$HOME/.claude/plugins/known_marketplaces.json"

# A sibling repo mounted earlier in the run.
mounts=(-v "$work/sibling:$work/sibling:ro")
skip_marketplaces=(client)
collect_marketplaces 2>"$work/err"

expected="-v $work/sibling:$work/sibling:ro \
-v $work/src/legacy:$work/src/legacy:ro \
-v $work/src/mine:$work/src/mine:ro \
-v $work/volume/m:$work/volume/m:ro"
[ "${mounts[*]}" = "$expected" ] || fail "mounts were: ${mounts[*]}"

grep -qx "docker/cc: mounted marketplaces read-only: legacy mine in-volume" "$work/err" ||
  fail "no mounted line in: $(cat "$work/err")"
grep -qx "docker/cc: skipped marketplaces: client" "$work/err" ||
  fail "no skipped line in: $(cat "$work/err")"
grep -qx "docker/cc: marketplace gone has no folder at $work/src/gone on the Mac, so its plugins will not load" "$work/err" ||
  fail "no missing line in: $(cat "$work/err")"
[ "$(wc -l <"$work/err")" -eq 3 ] || fail "extra lines in: $(cat "$work/err")"
[ "$(cat "$work/compose.log")" = "config --format json" ] ||
  fail "compose calls were: $(cat "$work/compose.log")"

# With nothing to mount, the compose files are not read.
rm -f "$work/compose.log"
write_known client="$work/src/client"
mounts=()
collect_marketplaces 2>"$work/err"
[ ${#mounts[@]} -eq 0 ] || fail "with every marketplace skipped, mounts were: ${mounts[*]}"
[ ! -e "$work/compose.log" ] || fail "compose was called with nothing to mount"

# Without jq, nothing mounts and one line says so.
mounts=()
(
  # shellcheck disable=SC2123 # no jq on the search path is the point
  PATH=$work/no-bin
  collect_marketplaces
) 2>"$work/err"
grep -q "jq is missing" "$work/err" || fail "no jq line in: $(cat "$work/err")"

# Without known_marketplaces.json, nothing mounts and nothing prints.
rm -f "$HOME/.claude/plugins/known_marketplaces.json"
mounts=()
collect_marketplaces 2>"$work/err"
[ ${#mounts[@]} -eq 0 ] && [ ! -s "$work/err" ] ||
  fail "without the file, mounts were: ${mounts[*]:-none}, and it printed: $(cat "$work/err")"

# settings.json names folder marketplaces too. Its entry wins when
# known_marketplaces.json lacks it or names another path. A path may hold a
# backslash or a tab.
odd=$work/src/back\\slash$'\t'tab
mkdir -p "$work/src/new" "$work/src/moved" "$odd"
write_known moving="$work/src/old"
jq -n --arg new "$work/src/new" --arg moved "$work/src/moved" --arg odd "$odd" \
  '{extraKnownMarketplaces: {
    new: {source: {source: "directory", path: $new}},
    moving: {source: {source: "directory", path: $moved}},
    odd: {source: {source: "directory", path: $odd}},
    remote: {source: {source: "github", repo: "o/r"}}}}' >"$HOME/.claude/settings.json"
mounts=()
collect_marketplaces 2>"$work/err"
expected="-v $odd:$odd:ro -v $work/src/moved:$work/src/moved:ro -v $work/src/new:$work/src/new:ro"
[ "${mounts[*]}" = "$expected" ] || fail "with settings.json, mounts were: ${mounts[*]}"

# When docker compose config fails, the marketplace still mounts, and a line
# says the compose files went unread.
failing_compose() { return 1; }
compose=(failing_compose)
rm -f "$HOME/.claude/settings.json"
write_known mine="$work/src/mine"
mounts=()
collect_marketplaces 2>"$work/err"
[ "${mounts[*]}" = "-v $work/src/mine:$work/src/mine:ro" ] ||
  fail "with compose failing, mounts were: ${mounts[*]}"
grep -q "docker compose config failed" "$work/err" ||
  fail "no compose line in: $(cat "$work/err")"

echo "ok"
