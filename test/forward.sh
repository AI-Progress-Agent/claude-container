#!/usr/bin/env bash
# Checks both halves of the notification path without Docker:
#   - the hook script in the image (cc-forward-notify)
#   - the watcher in the launcher, kit/cc (watch_events)
# It points both halves at a temp directory instead of /run/cc-notify.
# It then checks that a hook event reaches notify_host with its JSON and
# working directory.
set -euo pipefail

# Source the launcher for watch_events, so the test runs the real code. It
# sets repo for itself, so the test sets it again below.
# shellcheck source=kit/cc
. "$(dirname "$0")/../kit/cc"

hook=$(cd "$(dirname "$0")/../image" && pwd)/cc-forward-notify
work=$(mktemp -d "${TMPDIR:-/tmp}/cc-notify-test.XXXXXX")
trap 'kill "${watcher:-}" 2>/dev/null || true; rm -rf "$work"' EXIT

notify_dir=$work/events
seen=$work/seen
mkdir -p "$notify_dir" "$work/project"
repo=$work

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# Sends a Stop event from directory $1 through the hook script. Then checks
# that notify_host ran in directory $2 and got the event on stdin.
check_event() {
  local event
  event=$(printf '{"hook_event_name":"Stop","cwd":"%s"}' "$1")
  : >"$seen"
  echo "$event" | CC_NOTIFY_DIR=$notify_dir sh "$hook"
  for _ in $(seq 20); do
    [ -s "$seen" ] && break
    sleep 0.2
  done
  [ "$(cat "$seen")" = "$2 $event" ] ||
    fail "notify_host saw: $(cat "$seen" 2>/dev/null || echo nothing)"
}

notify_host() { echo "$PWD $(cat)" >>"$seen"; }

watch_events &
watcher=$!

check_event "$work/project" "$work/project"
# A directory outside the repo is not the same directory on the host, so the
# notifier runs at the repo root.
check_event / "$work"
sleep 0.7
[ -z "$(ls -A "$notify_dir")" ] || fail "event file was not removed"

# Without the directory, the hook must exit 0 and write nothing.
echo '{}' | CC_NOTIFY_DIR=$work/missing sh "$hook" ||
  fail "hook failed with no directory"

echo "ok"
