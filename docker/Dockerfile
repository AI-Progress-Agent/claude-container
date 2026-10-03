# Claude Code for this repo, in a container that mirrors the host's Claude
# setup. docker/cc builds and runs it; read that file first.
#
# The trick is paths. The container user's home is the host's home path, and
# docker/cc mounts each piece of host config that exists at the same absolute
# path it has on the Mac. Plugin install paths, hook paths, dotfiles
# symlinks, the project's memory key and worktree paths all stay valid,
# because nothing has to be rewritten.
FROM debian:bookworm-slim

# The host's $HOME, user name and repo path. docker/cc passes all three.
ARG HOST_HOME
ARG HOST_USER
ARG REPO
# Pin a release, for example 2.1.259, for a reproducible image. Left at
# latest, it moves only on `docker/cc upgrade`: a plain build keeps the cached
# layer.
ARG CLAUDE_VERSION=latest

# git, gh and ripgrep are what Claude reaches for. jq and perl run the host's
# hooks. xz-utils unpacks the Node that mise downloads.
RUN apt-get update && apt-get install -y --no-install-recommends \
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
# The helper reads GH_TOKEN, which docker/cc copies from the host's gh.
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
# its event to docker/cc on the host. docker/cc then runs the host's notifier.
# These hooks exist only in the image. Claude runs them alongside the host's
# own hooks.
COPY docker/notify/cc-forward-notify /usr/local/bin/
COPY docker/notify/managed-settings.json /etc/claude-code/

# A user with the host's name and the host's home path. macOS file sharing
# remaps ownership, so the uid itself does not matter.
RUN useradd --create-home --home-dir "$HOST_HOME" --shell /bin/bash --uid 1000 "$HOST_USER"

USER $HOST_USER
WORKDIR $HOST_HOME
# The host's ~/bin comes last. The status line's widgets call scripts there,
# and the cc-notify.mjs above has to win over the real one.
ENV HOME=$HOST_HOME \
    PATH=$HOST_HOME/.local/bin:$HOST_HOME/.local/share/mise/shims:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$HOST_HOME/bin \
    CLAUDE_CONFIG_DIR=$HOST_HOME/.claude \
    DISABLE_AUTOUPDATER=1

# The native installer puts claude in ~/.local/bin. Autoupdate is off above:
# upgrade with `docker/cc upgrade`, so a running container never changes
# itself.
RUN curl -fsSL https://claude.ai/install.sh | bash -s "$CLAUDE_VERSION"

# gh-stack is the only way GitHub merges a stacked PR, and the gh-stack skill
# runs it for every stack operation. `gh extension install` needs a login, so
# fetch the release binary into the directory gh reads extensions from, and
# check it against the digest GitHub publishes for the release.
# Bump the version and both digests together.
ARG GH_STACK_VERSION=v0.1.1
RUN arch=$(dpkg --print-architecture) \
 && case "$arch" in \
      amd64) sum=9ed103934fab0f90d3341fdfc4a342785396d39f5621fc7313a62602ce2b5462 ;; \
      arm64) sum=2da13f8c46f2770237c744b341ab6be9f07508585a6762634c4a88aa355460bc ;; \
      *) echo "gh-stack: no digest for $arch" >&2; exit 1 ;; \
    esac \
 && dir=$HOST_HOME/.local/share/gh/extensions/gh-stack \
 && mkdir -p "$dir" \
 && curl -fsSL -o "$dir/gh-stack" \
      "https://github.com/github/gh-stack/releases/download/$GH_STACK_VERSION/linux-$arch" \
 && echo "$sum  $dir/gh-stack" | sha256sum -c - \
 && chmod 0755 "$dir/gh-stack" \
 && printf 'owner: github\nname: gh-stack\nhost: github.com\ntag: %s\nispinned: true\npath: %s\n' \
      "$GH_STACK_VERSION" "$dir/gh-stack" > "$dir/manifest.yml"

# mise supplies every language and tool. The global config is the subset of
# the host's that hooks, the status line and the LSP plugins call.
RUN curl -fsSL https://mise.run | sh
COPY --chown=$HOST_USER docker/mise.toml $HOST_HOME/.config/mise/config.toml
RUN mise install

# The repo's own mise.toml is the one list of its tool versions. Install
# them into the image so a run never waits for a download. Rebuild after
# mise.toml changes.
#
# mise.local.toml adds this machine's own tools, such as gws. It is not
# committed, so the bracket makes the COPY match nothing on a clone without
# one, rather than fail.
COPY --chown=$HOST_USER mise.toml mise.local.tom[l] /tmp/repo/
RUN cd /tmp/repo \
 && MISE_TRUSTED_CONFIG_PATHS=/tmp/repo mise install \
 && rm -rf /tmp/repo

# Created as the user, so a fresh named volume copies these with the right
# owner. Docker would otherwise create each as root: the volume root, and
# the parents of the host files compose.yaml mounts inside it.
RUN mkdir -p "$HOST_HOME/.claude/projects" "$HOST_HOME/.claude/state" \
      "$HOST_HOME/.config"

CMD ["claude"]
