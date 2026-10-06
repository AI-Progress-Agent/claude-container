# Claude Code for this repo: the claude-container base image, plus this
# host's user and this repo's own tools. docker/cc builds and runs it.
#
# The FROM line is the one version pin, for the image and for the launcher
# docker/cc runs. Dependabot moves the tag and the digest together. A real
# repo pins the digest too, as in
# FROM ghcr.io/ai-progress-agent/claude-container:v2.0.2@sha256:<digest>.
FROM ghcr.io/ai-progress-agent/claude-container:v2.0.2

# The host's $HOME, user name and repo path. The launcher passes all three.
ARG HOST_HOME
ARG HOST_USER
ARG REPO

# Anything this repo needs from apt goes here, while the build is still root.

RUN /usr/local/libexec/claude-container/add-user "$HOST_USER" "$HOST_HOME"

USER $HOST_USER
WORKDIR $HOST_HOME
# The user's mise shims come first, for the repo's own tools, then the base
# image's. The host's ~/bin comes last. The status line's widgets call scripts
# there, and the image's cc-notify.mjs has to win over the real one.
ENV HOME=$HOST_HOME \
    PATH=$HOST_HOME/.local/bin:$HOST_HOME/.local/share/mise/shims:/usr/local/share/mise/shims:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$HOST_HOME/bin \
    CLAUDE_CONFIG_DIR=$HOST_HOME/.claude

# The repo's own mise.toml is the one list of its tool versions. Install
# them into the image so a run never waits for a download. mise reuses each
# tool the base image already has. Rebuild after mise.toml changes.
#
# mise.local.toml adds this machine's own tools. It is not committed, so the
# bracket makes the COPY match nothing on a clone without one, rather than
# fail.
COPY --chown=$HOST_USER mise.toml mise.local.tom[l] /tmp/repo/
RUN cd /tmp/repo \
 && MISE_TRUSTED_CONFIG_PATHS=/tmp/repo mise install \
 && rm -rf /tmp/repo
