#!/usr/bin/env bash
# Builds the base image as claude-container:dev, then the example repo's
# layer on it for this machine's user, and checks what that user sees inside:
# Claude runs, the base image's tools run but cannot be changed, the repo's
# own tool installs for the user, and gh-stack and agent-browser work for a
# user who does not own them. It runs the container as compose.yaml does, with
# no capabilities. It needs Docker, and removes the example's image after.
#
# Extra arguments go to the base image's docker build, such as
# --build-arg CLAUDE_VERSION=2.1.283.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
example=kit-smoke-example
trap 'docker rmi -f "$example" >/dev/null 2>&1 || true' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

docker build -t claude-container:dev "$@" "$root"
base=$(sed -nE 's#^FROM[[:space:]]+([^[:space:]]+).*#\1#p' "$root/example/docker/Dockerfile")
docker build -t "$example" -f "$root/example/docker/Dockerfile" \
  --build-context "$base=docker-image://claude-container:dev" \
  --build-arg HOST_HOME="$HOME" --build-arg HOST_USER="$(id -un)" \
  --build-arg REPO="$root/example" "$root/example"

# Runs $1 as a bash script inside, as compose.yaml runs the container, in the
# example repo with its mise.toml trusted.
inside() {
  docker run --rm --cap-drop ALL --security-opt no-new-privileges:true \
    -v "$root/example:$root/example:ro" -w "$root/example" \
    -e MISE_TRUSTED_CONFIG_PATHS="$root/example" "$example" bash -c "$1"
}

out=$(inside 'claude --version') || fail "claude: $out"
echo "claude: $out"
[ "$(inside 'id -un')" = "$(id -un)" ] || fail "the user is not $(id -un)"
# shellcheck disable=SC2016 # the container's HOME, not this one
[ "$(inside 'echo $HOME')" = "$HOME" ] || fail "the home is not $HOME"
inside 'which node' | grep -qx /usr/local/share/mise/shims/node ||
  fail "node is not the base image's: $(inside 'which node')"
# The base image strips these tools and removes parts of them. Each must
# still run.
for tool in mise node python3 uv ruff ast-grep prettier pyright; do
  inside "$tool --version" >/dev/null || fail "$tool does not run"
done
inside 'shellcheck --version' >/dev/null || fail "the repo's shellcheck does not run"
inside '! touch /usr/local/share/mise/installs/x 2>/dev/null' ||
  fail "the user can write the base image's tools"
inside 'gh stack --help' >/dev/null || fail "gh stack does not run"
inside 'agent-browser --version' >/dev/null || fail "agent-browser does not run"
inside '[ -x /opt/kit/cc ] && [ -f /opt/kit/compose.yaml ] && [ -f /opt/kit/cc-local.bash ]' ||
  fail "the kit is not in /opt/kit"
# The stub copies /opt/kit to the Mac, and uv runs the launcher from there.
inside '[ -f /opt/kit/src/launcher/main.py ] && [ -f /opt/kit/uv.lock ] && [ -f /opt/kit/.python-version ]' ||
  fail "the Python launcher or its uv project is not in /opt/kit"
inside '[ ! -e /opt/kit/tests ] && [ ! -e /opt/kit/.venv ]' ||
  fail "/opt/kit holds the tests or a virtual environment"
# build and upgrade write this copy over a repo's docker/cc.
inside 'cat /opt/kit/stub/cc' | cmp -s - "$root/stub/cc" ||
  fail "/opt/kit/stub/cc is not stub/cc"

echo "ok"
