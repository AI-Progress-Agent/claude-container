# The claude-container base image: Claude Code and the tools a Mac's Claude
# setup calls, on Debian, for arm64. It has no user. Each repo builds a thin
# layer on top that adds the host's user and the repo's own tools;
# example/docker/Dockerfile is one. The repo's docker/cc stub starts the
# launcher in /opt/kit, which builds that layer and runs it.
#
# The trick is paths. The container user's home is the host's home path, and
# the launcher mounts each piece of host config that exists at the same
# absolute path it has on the Mac. Plugin install paths, hook paths, dotfiles
# symlinks, the project's memory key and worktree paths all stay valid,
# because nothing has to be rewritten. So everything here sits at a system
# path, and nothing waits for a home directory.
#
# Dependabot moves the digest when Debian publishes a new image.
FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251

# git, gh and ripgrep are what Claude reaches for. jq and perl run the host's
# hooks. xz-utils unpacks the Node that mise downloads.
#
# The upgrade brings in Debian's security fixes for the packages already in
# the base image, such as libc and OpenSSL. The digest above moves only with
# a pull request, and the weekly rebuild needs the fixes without one.
RUN apt-get update && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends \
      bash-completion ca-certificates curl git gnupg jq less openssh-client \
      perl procps ripgrep unzip xz-utils \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && chmod 0644 /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*

# The host's ~/.gitconfig, mounted read-only, names /opt/homebrew/bin/gh as
# the GitHub credential helper. Put gh at that path rather than edit the file.
# The helper reads GH_TOKEN, which the launcher copies from the host's gh.
#
# The system config holds what only the container needs. The remote is SSH
# and the container has no keys, so GitHub SSH URLs go over HTTPS with that
# token instead. The repo is a bind mount owned by another uid, which git
# otherwise refuses as "dubious ownership".
RUN mkdir -p /opt/homebrew/bin \
 && ln -s /usr/bin/gh /opt/homebrew/bin/gh \
 && git config --system url."https://github.com/".insteadOf "git@github.com:" \
 && git config --system --add safe.directory '*'

# The host's Stop and Notification hooks call cc-notify.mjs, which posts a
# macOS notification. There is no macOS here, so it drains its input and
# does nothing.
RUN printf '#!/bin/sh\ncat >/dev/null\n' > /usr/local/bin/cc-notify.mjs \
 && chmod 0755 /usr/local/bin/cc-notify.mjs
# Instead, managed settings add Stop and Notification hooks. Each one hands
# its event to the launcher on the host. The launcher then runs the host's
# notifier. These hooks exist only in the image. Claude runs them alongside
# the host's own hooks.
COPY image/cc-forward-notify /usr/local/bin/
COPY image/managed-settings.json /etc/claude-code/

# The helpers the build and a repo's thin layer run: release-before picks a
# version, and add-user makes the host's user.
COPY image/release-before image/add-user /usr/local/libexec/claude-container/

# gh-stack is the only way GitHub merges a stacked PR, and the gh-stack skill
# runs it for every stack operation. `gh extension install` needs a login, so
# fetch the release binary and check it against the digest GitHub publishes
# for the release. gh reads extensions from the user's home, so add-user links
# this copy in there. Bump the version and the digest together.
ARG GH_STACK_VERSION=v0.1.1
ARG GH_STACK_SHA256=2da13f8c46f2770237c744b341ab6be9f07508585a6762634c4a88aa355460bc
RUN dir=/usr/local/share/gh-stack \
 && mkdir -p "$dir" \
 && curl -fsSL -o "$dir/gh-stack" \
      "https://github.com/github/gh-stack/releases/download/$GH_STACK_VERSION/linux-arm64" \
 && echo "$GH_STACK_SHA256  $dir/gh-stack" | sha256sum -c - \
 && chmod 0755 "$dir/gh-stack" \
 && echo "$GH_STACK_VERSION" > "$dir/version"

# Claude Code, as the one binary the native installer would fetch, checked
# against the digest in its release's manifest. The installer itself puts
# claude in a home directory, which this image does not have. Left empty,
# CLAUDE_VERSION becomes the newest release at least seven days old, the same
# window mise uses. CI passes the version it picked, so a new release moves
# the cached layer. Autoupdate is off: a running container never changes
# itself, and a new release comes as a new image.
ARG CLAUDE_VERSION=
RUN set -eu; \
    base=https://downloads.claude.ai/claude-code-releases; \
    v=${CLAUDE_VERSION:-$(/usr/local/libexec/claude-container/release-before @anthropic-ai/claude-code 7)}; \
    sum=$(curl -fsSL "$base/$v/manifest.json" | jq -er '.platforms["linux-arm64"].checksum'); \
    curl -fsSL -o /usr/local/bin/claude "$base/$v/linux-arm64/claude"; \
    echo "$sum  /usr/local/bin/claude" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/claude
ENV DISABLE_AUTOUPDATER=1

# mise supplies every language and tool. MISE_VERSION follows the same rule
# as CLAUDE_VERSION.
#
# The global config is the subset of the host's that hooks, the status line
# and the LSP plugins call. It goes in /etc/mise, and --system installs its
# tools into /usr/local/share/mise as root, so the user can run them but not
# change them. A repo's thin layer then runs mise as the user. mise reuses
# these tools there and puts only the repo's extras in the user's home.
#
# agent-browser ships its platform binary without the execute bit, and sets
# the bit on first run. A user who does not own the file cannot set it, so
# set it here.
ARG MISE_VERSION=
RUN set -eu; \
    v=${MISE_VERSION:-$(/usr/local/libexec/claude-container/release-before @jdxcode/mise 7)}; \
    curl -fsSL https://mise.run | MISE_VERSION="v$v" MISE_INSTALL_PATH=/usr/local/bin/mise sh
COPY image/mise.toml /etc/mise/config.toml
RUN MISE_SYSTEM_PACKAGES_SUDO=false mise install --system \
 && chmod a+x "$(mise where npm:agent-browser)"/node_modules/.mise/agent-browser@*/node_modules/agent-browser/bin/agent-browser-linux-*
# mise would warn about its own newer release on every call. A new mise
# comes with a new base image, so the warning is noise here.
ENV MISE_HIDE_UPDATE_WARNING=1 \
    PATH=/usr/local/share/mise/shims:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# The launcher kit. A repo's docker/cc copies this directory to the Mac once
# per image tag and runs it from there. So the image tag pins the launcher
# and the image together.
COPY kit/ /opt/kit/

LABEL org.opencontainers.image.source=https://github.com/AI-Progress-Agent/claude-container \
      org.opencontainers.image.description="Claude Code in a container that mirrors a Mac's Claude setup" \
      org.opencontainers.image.licenses=MIT

CMD ["claude"]
