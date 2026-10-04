# The example repo's settings for the claude-container launcher, which
# sources this file. Each one is optional; the launcher's load_repo_config
# lists them with their defaults.
#
# The launcher is bash, and it reads each variable after it sources this.
# shellcheck shell=bash disable=SC2034

# The compose project: the image, and the volumes with the login in them.
project_name=example-claude

# Sibling repos this repo's sessions edit, mounted writable.
writable_siblings=()

# Run inside at each start, before your docker/cc.local's local_setup.
start_commands=true

# Clones kept inside the repo on purpose, by their paths in the repo.
nested_clones=()
