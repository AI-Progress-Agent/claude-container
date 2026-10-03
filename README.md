# Claude Code in a container

`docker/cc` runs Claude Code in a Linux container that mirrors your Mac’s
Claude setup. The root `README.md` says how to build and start it. This file
says how the mirror works, how GitHub works inside, and how to add what only
your machine needs.

If something you use on the Mac is missing inside, start at
[When something is missing](#when-something-is-missing).

## How the container mirrors the Mac

Each Mac path mounts at the same absolute path inside the container. So
plugin paths, hook paths, dotfiles links and worktree paths all resolve
unchanged. The repo sits at its Mac path too, so a git `includeIf` that picks
your work email by path still matches.

Every run starts a fresh container. It runs your `local_setup`, and then
starts Claude. What lasts between runs is in Docker volumes and
in the Mac paths the container mounts.

`docker:build` reuses Docker’s cached layers, so it upgrades nothing that is
pinned to `latest`: Claude Code, mise, and the tools in `docker/mise.toml`
stay at the versions of the first build. Run `docker:upgrade` to move them.
It downloads everything again. mise still skips any release younger than
seven days.

The first `docker/cc` asks you to sign in. The Mac keeps its Claude login in
the keychain, which a container cannot read, so the container keeps its own
login in a Docker volume. Later runs skip the sign-in. The volume belongs to
this repo’s container, so a login in another repo’s container does not carry
over.

`docker/cc` mounts the usual Claude Code and git config read-only. That is
the files and directories under `~/.claude` that hold your settings, plus
`~/.agents`, `~/bin`, `~/.config/ccstatusline`, `~/.gitconfig`,
`~/.gitconfig.local` and `~/.config/git`. `docker/cc` leaves out any path
your machine does not have, so it works with any setup. The list is
`optional` in `docker/cc`.

A file in that list can be a link, into a dotfiles repo for example. For a
link, `docker/cc` mounts the directory that holds the link’s target under
`/mnt/host-links/`. Then it recreates the link inside to point there. Some
editors save a file by writing a new copy over the old one. With this
handling, that save still reaches the container. A plain file mounted on its
own does not get the save: the container keeps reading the copy that existed
at start.

Links elsewhere, such as a skill that links into your dotfiles repo, resolve
only if you mount their targets yourself. See
[`docker/compose.local.yaml`](#dockercomposelocalyaml).

By default, the container can write to these Mac paths:

- this repo
- this project’s memory and session transcripts, shared with the Mac
- the `ai-progress-plugins` clone next to this one, if you have it (see
  below)

“This repo” is the whole clone, not only what git tracks. A folder of partner
material at the clone’s root, such as a per-org folder, is readable and
writable inside. `.gitignore` denies the root by default, so that folder
still stays out of every commit. But Claude can read it, so treat anything
you keep in the clone as seen.

Each mount you add in `docker/compose.local.yaml` without `:ro` is one more.

The container can also read the program’s other repos, but cannot write to
them. `docker/cc` looks at each
directory next to this clone. It mounts the directory read-only, at its own
path, if both of these are true:

- it is a clone, with its own `.git` directory
- its `origin` has the same host and owner as this clone’s, such as
  `github.com/AI-Progress-Agent`

So another client’s repo or a partner folder in the same parent directory
stays out. At start, `docker/cc` names each repo it mounted. A clone cannot
fetch inside, so it is as current as the Mac’s last fetch. To check the
search without Docker, run `docker/test-siblings.sh`. It prints `ok` when
only the right directories mount.

One sibling mounts writable: `ai-progress-plugins`, the program’s shared
plugin. It has no container of its own yet, so its work happens in this
repo’s sessions. Its `.git/hooks` and `.git/config` still mount read-only,
as this repo’s do. The list is `writable_siblings` in `docker/cc`. Clone the
plugin repo next to this one on the Mac, then restart `docker/cc`:

```bash
gh repo clone AI-Progress-Agent/ai-progress-plugins ../ai-progress-plugins
```

Code you write there runs on the Mac too, when a Mac session loads the
plugin. Review it as you would a change to this repo’s hooks.

It keeps these apart from the Mac:

- the Claude login and `~/.claude.json`, since a second writer could corrupt
  the Mac’s copy
- every other project’s history, since `~/src` holds unrelated clients’ work

Some things stay on the Mac:

- Codex, and your user-level MCP servers
- the status line’s time-left widget, which reads the keychain
- Google Drive: the Drive folders are not mounted, so reach Drive with the
  `gws` CLI (see [`docker/cc.local`](#dockercclocal))
- the signed-in browser: `agent-browser` inside drives a Chrome on the Mac
  (see [The browser](#the-browser))

Plugins mount read-only, so a plugin update fails inside, and so does a plugin
that saves its own state.

### The browser

`agent-browser` inside drives a Chrome on the Mac.
Use it for a page that needs a login, such as a project view’s layout, which
the GitHub API cannot set.

1. On the Mac, run `mise run browser:start`. It opens Chrome with a profile of
   its own, `~/.agent-browser/profiles/ai-progress`, and remote debugging on
   a port.
2. Sign that Chrome in to GitHub once. The profile keeps the login.
3. Inside, run `agent-browser` as usual. `docker/cc` sets `AGENT_BROWSER_CDP`
   at start, so each command connects to that Chrome.

`agent-browser.json` names the port, 10083, and is the only place that does.
`browser:start` and `docker/cc` read it from there, and a session on the Mac
reaches the same Chrome through it. Give each repo its own port, so the
Chromes of two repos can run at once.

### Notifications

When Claude waits for you or finishes a turn inside, your Mac’s notifier runs
on the Mac. The image’s managed settings, in
`/etc/claude-code/managed-settings.json`, add `Stop` and `Notification`
hooks. Each hook writes the event’s JSON as a file into `/run/cc-notify`.
`docker/cc` mounts a new temporary directory there for each session, and it
checks that directory every half second. For each file it runs
`notify_host`, then deletes the file.

`notify_host` runs `cc-notify.mjs` when the Mac has it on the `PATH`, and does
nothing otherwise. `cc-notify.mjs` is a personal script, not part of this
repo, so you probably do not have it. Without a notifier, you get no
notification and no warning. To set one up:

1. Install a notifier on the Mac, for example `brew install terminal-notifier`.
2. Redefine `notify_host` in [`docker/cc.local`](#dockercclocal) to call it.
   The example below does this.
3. Restart `docker/cc`.
4. Wait for Claude to finish a turn. A notification appears on the Mac.

`docker/cc` runs `notify_host` this way:

- The event’s JSON arrives on stdin.
- It runs in the session’s directory. A directory outside the repo becomes
  the repo root.
- It has `docker/cc`’s environment, so `$TMUX_PANE` names your pane.
- Its output goes to `cc-notify.log` in `$TMPDIR`, because Claude owns the
  terminal. Look there when a notifier fails.

```bash
notify_host() { terminal-notifier -title Claude -message "Waiting in ${PWD##*/}"; }
```

The directory and the loop go when the container does. A container started
without `docker/cc` has no `/run/cc-notify`, so the hooks drop the event.
If an admin at your company pushes managed settings to Claude, those
replace the image’s file, and nothing is forwarded.

To check the hook script and the loop without Docker, run
`docker/notify/test-forward.sh`. It prints `ok` when an event reaches
`notify_host`.

## GitHub operations

Everything GitHub does inside the container goes over HTTPS with your Mac’s
`gh` login. The container has no SSH keys and no SSH agent.

1. At start, `docker/cc` runs `gh auth token` on the Mac and passes the token
   into the container as `GH_TOKEN`. It goes through the environment, not the
   command line, so `ps` does not show it. If `gh` has no login on the Mac,
   `docker/cc` warns you, and `git push` and `gh` fail inside.
2. The `origin` remote in `.git/config` is SSH (`git@github.com:...`). The
   image’s system git config rewrites every `git@github.com:` URL to
   `https://github.com/`, so `git remote -v` inside shows HTTPS.
3. Your `~/.gitconfig` may name `/opt/homebrew/bin/gh auth git-credential`
   as the credential helper. That macOS path does not exist in Debian, so the
   image links it to `/usr/bin/gh`. So that config works unchanged, and `gh`
   answers git with `GH_TOKEN`.
4. `gh` itself reads `GH_TOKEN`, so `gh pr`, `gh issue` and the project board
   moves work too.

So the container can do on GitHub whatever your Mac’s `gh` login can do. To
narrow that, sign `gh` in on the Mac with a token of smaller scope.

The repo mounts writable, except `.git/hooks` and `.git/config`, which mount
read-only. Git runs commands from both. So a write there from the container
would run on your Mac at its next `git` command. Commits and pushes still
work. A command that writes `.git/config` cannot write it inside.

So a branch you create inside has no upstream, the remote branch it tracks.
Git records an upstream in `.git/config`. These commands try to record one:

| Command inside                                                                                                   | What still happens              |
| ---------------------------------------------------------------------------------------------------------------- | ------------------------------- |
| `git push -u`                                                                                                    | The push                        |
| `git push` with no branch named, when your `~/.gitconfig` sets `push.autoSetupRemote`                            | The push                        |
| `git switch -c` or `git checkout -b` from a remote branch                                                        | The new branch, checked out     |
| `git branch <new>` from a remote branch                                                                          | The new branch                  |
| `git worktree add -b` from a remote branch                                                                       | The new branch and its worktree |
| `git switch <name>`, `git checkout <name>` or `git worktree add <path> <name>`, when only `origin/<name>` exists | The new branch, checked out     |
| `gh pr checkout`, for a branch you do not have                                                                   | The new branch, checked out     |
| `git branch --set-upstream-to`                                                                                   | Nothing                         |

Each one prints `could not write config file .git/config: Device or resource
busy` twice. Then it prints “set up to track” as if it worked, and exits 0.
Believe the error. `git rev-parse --abbrev-ref @{u}` says whether a branch
has an upstream.

Inside, name the remote branch in place of the upstream:

- Push with `git push origin <branch>`.
- Pull with `git pull origin <branch>`.
- Create a branch from a remote one with `--no-track`, and name its start
  point. For example, `git switch -c <branch> --no-track origin/<branch>`.
  The same flag works for `git branch` and `git worktree add -b`.
- Compare against `origin/<branch>`, not `@{u}`. For example,
  `git rev-list --left-right --count HEAD...origin/<branch>` counts the
  commits on each side.

On the Mac, `git branch --set-upstream-to=origin/<branch> <branch>` records
the upstream. The container sees it only after `docker/cc` restarts. See
[How the container mirrors the Mac](#how-the-container-mirrors-the-mac).

No setting fixes this inside. Git always writes an upstream to `.git/config`,
even when that file includes others. Git writes a new file, then renames it
over the old one. A file mounted on its own refuses that rename. So a
writable mount of `.git/config` fails the same way.

Deleting a branch inside warns that git cannot update `.git/config`. The
branch is still deleted.

In a fresh worktree, `git rebase` can fail on a clean tree with “Your local
changes would be overwritten”, naming a different file on each try. Replay
the branch in the object store instead. This squashes the branch into one
commit on `origin/main`, with the last commit’s message:

```bash
tree=$(git merge-tree --write-tree origin/main HEAD)
commit=$(git commit-tree "$tree" -p origin/main -m "$(git log -1 --format=%B)")
git reset --hard "$commit"
```

`git merge-tree` exits 1 and prints the conflicts when the two sides
conflict. Stop there and resolve them by hand.

The image also sets `safe.directory '*'`, because the repo is a bind mount
owned by another user ID and git would refuse it as dubious ownership.

The image has the `gh stack` extension, pinned in `docker/Dockerfile`.
GitHub merges a stacked PR only with `gh stack merge`: `gh pr merge` and the
merge REST endpoint both refuse it. The read-only `.git/config` does not stop
`gh stack`. With one remote it pushes to that remote without
`remote.pushDefault`. To move to a new release, change `GH_STACK_VERSION` and
both digests, then run `mise run docker:build`.

## Your machine’s own setup

Three uncommitted files carry what only your machine needs. All three are
optional and gitignored. The snippets below come from one developer’s working
setup. Change the paths to yours.

To reach Google Drive from inside, you need all three. The `gws` recipes
below install the CLI, hand over its login, and point it at the login file.
The Drive folders themselves are not mounted.

| File                        | What it adds                          | Takes effect            |
| --------------------------- | ------------------------------------- | ----------------------- |
| `docker/compose.local.yaml` | Mounts and environment variables      | The next `docker/cc`    |
| `docker/cc.local`           | Commands on the Mac, and setup inside | The next `docker/cc`    |
| `mise.local.toml`           | Tools installed in the image          | `mise run docker:build` |

### `docker/compose.local.yaml`

`docker/cc` stacks this file on `docker/compose.yaml`. Add a mount here when
your config links into a directory the `optional` list does not cover: a
dotfiles repo, a plugin marketplace that `settings.json` names by a local
directory, or a skill’s state.

```yaml
services:
  claude:
    volumes:
      # The dotfiles repo. ~/.claude/CLAUDE.md, the settings and ~/bin's
      # scripts are links into it.
      - ${HOST_HOME}/.home-directory:${HOST_HOME}/.home-directory:ro
      # settings.json names this marketplace by a local directory, and its
      # plugins fail to load without it.
      - ${HOST_HOME}/src/my-marketplace:${HOST_HOME}/src/my-marketplace:ro
      # A skill's hooks keep their log here, so this one is writable.
      - ${HOST_HOME}/.claude/state/writing-line:${HOST_HOME}/.claude/state/writing-line
```

Mount each path at its own path, as above, so links into it resolve. Add
`:ro` unless the container has to write there.

Every source you name must exist on the Mac. A missing one does not fail:
Docker creates it as an empty directory owned by root, and the container sees
an empty directory.

`HOST_HOME`, `REPO`, `HOST_USER` and `PROJECT_KEY` are set by `docker/cc`, and
you can use them here.

### `docker/cc.local`

`docker/cc` sources this shell file on the Mac before the container starts.
It sources the file inside a function. So `declare` and `typeset` need `-g`
to set a variable that `docker/cc` still sees afterwards. `$@` holds the
arguments that go to Claude or bash. Use the file for three things:

- Export a value that a variable in `compose.local.yaml` copies. List the
  variable name with no value under `environment:`, and Compose copies it from
  `docker/cc`’s shell.
- Append commands to `local_setup`. They run inside the container, after
  the links are recreated and before Claude starts.
- Redefine `notify_host`, the command that turns an event inside into a
  notification on the Mac. See [Notifications](#notifications).

This is where a login that the Mac keeps in the keychain gets handed over. The
container cannot read the keychain, so the Mac exports the login and the
container writes it to a file. This example hands over the `gws` login:

```bash
# gws on macOS keeps the key to its login in the keychain. Hand the container
# the decrypted login instead. local_setup writes it to a file readable by
# this user only, then drops it from the environment so Claude's tools never
# see it.
GWS_CREDENTIALS=$(gws auth export --unmasked 2>/dev/null || true)
export GWS_CREDENTIALS
if [ -z "$GWS_CREDENTIALS" ]; then
  echo "docker/cc: gws has no login on this machine, so gws will fail inside" >&2
fi
local_setup='[ -z "$GWS_CREDENTIALS" ] || (umask 077 \
    && mkdir -p "$(dirname "$GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE")" \
    && printf %s "$GWS_CREDENTIALS" > "$GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"); \
  unset GWS_CREDENTIALS'
```

It needs the matching entries in `compose.local.yaml`:

```yaml
services:
  claude:
    environment:
      # Copied from docker/cc.local's export.
      - GWS_CREDENTIALS
      # A path inside the container, not a mount, so the file goes when the
      # container does.
      - GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE=${HOST_HOME}/.config/gws/credentials.json
```

Keep these rules for a secret:

- Write it to a path inside the container, not a mount, so it never lands on
  the Mac’s disk and it goes when the container does.
- Write it with `umask 077`, so only your user can read it.
- `unset` the variable at the end of `local_setup`, so Claude’s tools do not
  inherit it.
- Warn on the Mac when the export is empty, so a missing login is not a
  mystery inside.

`local_setup` starts as `true`, and the example replaces it. With a second
recipe in the same file, append instead: `local_setup="$local_setup; ..."`.

### `mise.local.toml`

The image installs the tools in this file on top of `mise.toml` and
`docker/mise.toml`. It sits at the repo root, and mise on the Mac reads it
too. Rebuild the image after you change it, with `mise run docker:build`.

```toml
[tools]
# The Google Workspace CLI, which the gws-* skills call.
"github:googleworkspace/cli" = "latest"

# ctrl-g in Claude opens the EDITOR the Mac's settings name.
neovim = "latest"
```

A tool that needs a login usually needs a `docker/cc.local` recipe as well.
Installing it only puts the command on the `PATH`.

A tool in this file is on the `PATH` only in this repo. A sibling repo’s
session does not have this repo’s `gws`. So run the `ai-progress:drive`
skill’s script with this repo as the working directory. Anywhere else, call
`gws` by the full path that `mise which gws` prints here.

## When something is missing

| What you see inside                                                  | Why                                                                 | Fix                                                                                   |
| -------------------------------------------------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `command not found`                                                  | The image does not have the tool                                    | Add it to `mise.local.toml`, then run `mise run docker:build`                         |
| A CLI works on the Mac but is not signed in inside                   | The Mac keeps the login in the keychain                             | Export it in `docker/cc.local`, write it with `local_setup`, then restart `docker/cc` |
| A link in your config points at a path that is missing               | Nothing mounts the link’s target                                    | Mount the target directory in `docker/compose.local.yaml`, then restart `docker/cc`   |
| A path is there but empty, and owned by root                         | Its source in `docker/compose.local.yaml` is not on the Mac         | Fix the source path in `docker/compose.local.yaml`, then restart `docker/cc`          |
| A setting you just changed on the Mac is not seen                    | A plain file mounts as it was at start                              | Restart `docker/cc`                                                                   |
| `git push` or `gh` fails to authenticate                             | `gh` has no login on the Mac                                        | Run `gh auth login` on the Mac, then restart `docker/cc`                              |
| `could not write config file …/.git/config: Device or resource busy` | `.git/config` is read-only inside                                   | Work without the upstream, as [GitHub operations](#github-operations) says            |
| A plugin update fails                                                | Plugins mount read-only                                             | Update the plugin on the Mac                                                          |
| A program repo is not at `../<name>`                                 | It is not cloned next to this one, or its `origin` differs          | Clone it next to this repo from the same GitHub org, then restart `docker/cc`         |
| A program repo at `../<name>` is missing recent commits              | It cannot fetch inside                                              | Use `gh`, or run `git fetch` in it on the Mac                                         |
| `agent-browser` cannot connect                                       | No Chrome is open on the Mac on the port `agent-browser.json` names | Run `mise run browser:start` on the Mac                                               |
| `docker/cc: host.docker.internal does not resolve` at start          | The Docker engine is not Docker Desktop                             | Run the container under Docker Desktop                                                |

An agent inside the container can edit all three files, because they sit in
the writable repo. But the edit does nothing in the running container.
`docker/cc` reads each file on the Mac, when it starts or builds the image.
So make the edit or propose it. Then tell the developer what to restart or
rebuild.
