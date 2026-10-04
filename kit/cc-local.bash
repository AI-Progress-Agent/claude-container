#!/usr/bin/env bash
# Sources docker/cc.local once per session, on the Mac, and runs its
# notify_host for each event. The launcher starts it before the container
# starts. Its watcher owns it for the rest of the session.
#
# docker/cc.local is yours alone, and optional. It is sourced inside a
# function, with the arguments for Claude or bash as $@. It can:
#
#   - export the variables that docker/compose.local.yaml copies
#   - append to local_setup the commands to run inside before Claude
#   - redefine notify_host to use your own notifier
#   - name in skip_marketplaces the folder marketplaces to keep out
#   - add to nested_clones
#
# The arguments are the file descriptor to report on, the one to read events
# from, the path of cc.local (empty when there is none), the repo, the
# notifier's log, the count of nested_clones and each one, then the arguments
# for Claude or bash.
#
# The report is a list of fields, each ending in a NUL byte: local_setup, the
# count of skip_marketplaces and each one, the count of nested_clones and each
# one, then the count of exported variables and each one as NAME=value.
#
# Each event is two fields, each ending in a NUL byte: the event's file, which
# notify_host gets as stdin, and the folder to run it in. After each one, a
# lone NUL byte on the report says it is done. The helper ends when the
# events run out.
set -euo pipefail

# Names that start with _cc_ are the helper's own. repo and docker_dir are
# there for cc.local, as the bash launcher had them.
_cc_report=$1 _cc_events=$2 _cc_local=$3 repo=$4 _cc_log=$5 _cc_count=$6
shift 6
nested_clones=("${@:1:_cc_count}")
shift "$_cc_count"
# shellcheck disable=SC2034 # for cc.local
docker_dir=${_cc_local%/*}

# Runs on the host for each Stop and Notification event inside. Its stdin is
# the event's JSON, and its working folder is the session's folder. It runs
# in this terminal's environment, so a notifier that checks $TMUX_PANE sees
# your pane. docker/cc.local can redefine it.
notify_host() {
  if command -v cc-notify.mjs >/dev/null; then cc-notify.mjs; fi
}

load_local_overrides() {
  local_setup=true
  skip_marketplaces=()
  if [ -n "$_cc_local" ]; then
    # shellcheck source=/dev/null
    . "$_cc_local"
  fi
}

load_local_overrides "$@"

_cc_names=()
while IFS= read -r _cc_name; do
  case $_cc_name in _ | SHLVL) ;; *) _cc_names+=("$_cc_name") ;; esac
done < <(compgen -e)
{
  printf '%s\0' "$local_setup" "${#skip_marketplaces[@]}" \
    ${skip_marketplaces[@]+"${skip_marketplaces[@]}"} \
    "${#nested_clones[@]}" ${nested_clones[@]+"${nested_clones[@]}"} "${#_cc_names[@]}"
  for _cc_name in ${_cc_names[@]+"${_cc_names[@]}"}; do
    printf '%s=%s\0' "$_cc_name" "${!_cc_name}"
  done
} >&"$_cc_report"

# The watcher alone ends the session, by ending the events. Ctrl-C belongs to
# the container. A hangup or TERM sent to the session's processes would
# otherwise stop the notifications the watcher sends as it ends. A notifier
# gets the default for hangup and TERM back.
trap '' INT QUIT HUP TERM
set +e
while IFS= read -r -d '' _cc_event <&"$_cc_events" &&
  IFS= read -r -d '' _cc_cwd <&"$_cc_events"; do
  # One event runs at a time, with no time limit. A notifier that hangs
  # holds back every later event of the session. Claude owns the terminal,
  # so the notifier writes to the log instead.
  (
    trap - HUP TERM
    cd "$_cc_cwd" 2>/dev/null || cd "$repo" || exit
    notify_host
  ) <"$_cc_event" >>"$_cc_log" 2>&1
  printf '\0' >&"$_cc_report"
done
