"""The claude-container launcher.

It runs Claude Code for a repo in a container that mirrors the host's Claude
setup: global CLAUDE.md, settings, hooks, output styles, skills, plugins, the
status line, git identity, and the project's memory. The program's other repos
cloned next to the repo mount read-only.

A repo does not call it directly. Its docker/cc stub copies this kit out of
the image that docker/Dockerfile's FROM line names, once per image tag. Then
it runs that copy's kit/cc with KIT_DOCKER_DIR set to the repo's docker
directory. So these are the stub's commands:

  docker/cc [args]      interactive Claude; args go to claude unchanged
  docker/cc shell       a bash prompt in the same container
  docker/cc build       build the image; again after mise.toml changes
  docker/cc upgrade     rebuild from scratch, so every "latest" in the
                        repo's mise config moves

The first run asks you to sign in. The host keeps its login in the macOS
keychain, which the container cannot read, so the container keeps its own in
a volume. Later runs skip the sign-in.

Every run is a fresh container. What lasts is in volumes, and in the host
paths it mounts.

Two committed files, both optional, carry what is the repo's alone:

  docker/kit.toml             the repo's settings, which settings.py lists
  docker/compose.repo.yaml    stacked on compose.yaml: the repo's own
                              volumes and environment variables

Two uncommitted files, both optional, carry what is yours alone:

  docker/compose.local.yaml   stacked on compose.repo.yaml: your own mounts
                              and environment variables
  docker/cc.local             bash, sourced once per session before the
                              container starts. kit/cc-local.bash says what it
                              can set.

KIT_DEV, set to the path of a claude-container checkout, tries a kit change on
a real repo before a release. The stub runs that checkout's launcher, and
build and upgrade build that checkout's base image first. The repo's image is
then built on it, in place of the image the FROM line names.

The launcher works in two steps. plan.py reads the host's state and returns a
RunPlan: the mounts, the environment, the setup text, and any refusal. It
writes nothing. execute.py then does every side effect the plan names, and
replaces the launcher with docker compose run.
"""
