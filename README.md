# claude-container

[![ci](https://github.com/AI-Progress-Agent/claude-container/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/AI-Progress-Agent/claude-container/actions/workflows/ci.yml?query=branch%3Amain)

`claude-container` runs Claude Code for a repo in a Linux container that
mirrors your Mac's Claude setup. Your global `CLAUDE.md`, settings, hooks,
output styles, skills, agents, plugins, status line and git identity all work
inside, unchanged. Claude can write to the repo and to the project's memory,
and to little else.

It is built for Apple Silicon Macs running Docker Desktop. The image is arm64
only. The Mac also needs uv, which runs the launcher: `brew install uv`.
Without it, `docker/cc` stops and says so. uv fetches the launcher's Python
on the first run if the Mac does not have it.

If something you use on the Mac is missing inside, start at
[When something is missing](#when-something-is-missing).

## How it fits together

The kit has three parts:

- **The base image**, `ghcr.io/ai-progress-agent/claude-container`. It holds
  Claude Code, `gh`, `gh stack`, mise and the tools a Mac's Claude setup calls,
  all at system paths. It has no user. This repo's `Dockerfile` builds it.
- **The repo layer**, each repo's `docker/Dockerfile`. It builds `FROM` the
  base image, adds your Mac user with your Mac home path, and installs the
  repo's own `mise.toml`. `example/docker/Dockerfile` is one.
- **The launcher**, which builds the repo layer and starts the container. It
  is a Python package in `kit/src/launcher`. `kit/cc` sets it up with uv and
  runs it. It ships inside the base image at `/opt/kit`. Each repo keeps a
  short stub as `docker/cc`. The stub reads the tag from the `FROM` line,
  copies `/opt/kit` to `~/.cache/claude-container/<tag>` on first use, and runs
  the launcher from there. With `XDG_CACHE_HOME` set, the copy goes under it
  instead of `~/.cache`.

So the `FROM` line in a repo's `docker/Dockerfile` is the one version pin. It
pins the image and the launcher together. A repo can name the `latest` tag
instead, and follow the newest release (see
[On the `latest` tag](#on-the-latest-tag)).

Settings stack in three levels, and a later level wins:

1. The kit's defaults, in `kit/src/launcher/settings.py` and
   `kit/compose.yaml`.
2. The repo's committed files: `docker/kit.toml` and `docker/compose.repo.yaml`.
3. Your uncommitted files: `docker/cc.local` and `docker/compose.local.yaml`.
   See [Your machine's own setup](#your-machines-own-setup).

Three limits apply to `docker/cc.local`:

- The launcher takes `project_name` and `writable_siblings` from
  `docker/kit.toml` alone. So `docker/cc.local` cannot change them.
- `docker/cc build` and `docker/cc upgrade` do not read it.
- The launcher sets `HOST_HOME`, `REPO`, `REPO_GIT`, `REPO_GIT_MODE`,
  `HOST_USER`, `PROJECT_KEY` and `PROJECT_NAME` for Compose before it reads
  the file. An export of one there reaches Compose, but not what the launcher
  has already done with it. So `export PROJECT_NAME=x` renames the image but
  not the volumes. Leave those seven alone.

## Set up a repo

1. Copy [`stub/cc`](stub/cc) to the repo's `docker/cc`, and keep it
   executable. Do not edit it: the repo's settings go in `docker/kit.toml`.
2. Write `docker/Dockerfile` from
   [`example/docker/Dockerfile`](example/docker/Dockerfile). Pin the newest
   release by tag and digest. Each release's notes give the line to copy:

   ```dockerfile
   FROM ghcr.io/ai-progress-agent/claude-container:v2.0.0@sha256:<digest>
   ```

   Keep the image name in lower case, as above. The stub looks for that exact
   name, and stops when the `FROM` line has no match.

   Add what the repo needs from apt before `add-user`, while the build is
   still root.

3. Copy
   [`example/docker/Dockerfile.dockerignore`](example/docker/Dockerfile.dockerignore)
   to `docker/Dockerfile.dockerignore` too. The build context is the repo
   root, and the build then sees only `mise.toml` and `mise.local.toml`, so the
   rest of the repo stays out of the image.
4. Add `docker/kit.toml` if a default does not fit. Each setting is optional:

   | Setting              | Default                                                                                                                                 | Sets                                                                                                                                                                 |
   | -------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
   | `project_name`       | the main clone's folder name plus `-claude`, in lower case, with each character other than `a-z`, `0-9`, `_` and `-` turned into a dash | the image name and the volumes, the login among them, as a string such as `"app-claude"`. It takes only `a-z`, `0-9`, `_` and `-`, and starts with a letter or digit |
   | `writable_siblings`  | none                                                                                                                                    | the sibling repos that mount writable, as a list of folder names, such as `["plugins-repo"]`                                                                         |
   | `start_commands`     | none                                                                                                                                    | shell commands run inside at each start, before Claude, as a list such as `["pnpm install"]`. They run joined with `&&`                                              |
   | `agent_browser_port` | the `cdp` port in the repo's `agent-browser.json`                                                                                       | the Mac Chrome port that `agent-browser` drives, as a number. With `false`, it drives no Chrome                                                                      |
   | `nested_clones`      | none                                                                                                                                    | the clones and bare repos inside the repo kept on purpose, as a list of paths in the repo, such as `["vendor/sdk"]`                                                  |

   Two repos with one `project_name` share a login and an image, so give each
   repo its own. A worktree takes its main clone's name, so it shares that
   clone's login and image.
   [`example/docker/kit.toml`](example/docker/kit.toml) sets each one but
   `agent_browser_port`, which it leaves at the default. The launcher checks
   the file at each start. An unknown key, or a value that does not fit, stops
   the start, and the message names the file and the key.

5. Add `docker/compose.repo.yaml` for the repo's own volumes and environment
   variables. For example, a repo that installs `node_modules` at start keeps
   them in a Linux-only volume:

   ```yaml
   services:
     claude:
       volumes:
         - node-modules:${REPO}/node_modules
   volumes:
     node-modules:
   ```

   Docker creates a volume's directory as root unless the image already has
   it. So add the directory to the `add-user` line that step 2 copied from
   [`example/docker/Dockerfile`](example/docker/Dockerfile), as an extra
   argument such as `"$REPO/node_modules"`. `add-user` creates it owned by
   your user.

6. Add the per-person files to `.gitignore`:

   ```gitignore
   docker/cc.local
   docker/compose.local.yaml
   mise.local.toml
   ```

7. Ask Dependabot to bump the `FROM` line. Skip this step for a repo on
   the `latest` tag:

   ```yaml
   - package-ecosystem: docker
     directory: /docker
     schedule:
       interval: weekly
   ```

## Daily use

| Command             | What it does                                                   |
| ------------------- | -------------------------------------------------------------- |
| `docker/cc [args]`  | starts Claude inside. The arguments go to `claude` unchanged.  |
| `docker/cc shell`   | opens a bash prompt in the same container                      |
| `docker/cc build`   | builds the repo layer. Run it again after `mise.toml` changes. |
| `docker/cc upgrade` | rebuilds the repo layer without the cache                      |

The first `docker/cc` asks you to sign in. The Mac keeps its Claude login in
the keychain, which a container cannot read, so the container keeps its own
login in a Docker volume. Later runs skip the sign-in. The volume belongs to
the repo's `project_name`, so a login in another repo's container does not
carry over.

Every run starts a fresh container. It runs the repo's `start_commands`, then
your `local_setup`, and then starts Claude. What lasts between runs is in
Docker volumes and in the Mac paths the container mounts.

## Upgrading

A new Claude Code comes with a new base image. Each Monday, CI rebuilds the
newest release as the next patch, with the newest Claude Code and mise that
are at least seven days old. Dependabot then opens a pull request that moves
the repo's `FROM` line. To upgrade, merge that pull request and run
`docker/cc build`. A repo on the `latest` tag has no pull request to merge
(see [On the `latest` tag](#on-the-latest-tag)).

`docker/cc upgrade` rebuilds every step of the repo's `docker/Dockerfile`
without the cache. So the repo's apt installs move, and so does each tool in
the repo's `mise.toml` and your `mise.local.toml` whose version is not exact,
such as `latest` or `node = "22"`. On a fixed tag, it does not pull a new base
image, so Claude and the base image's tools stay at the versions the `FROM`
line pins.

Autoupdate is off inside. `claude update` still downloads a new Claude, about
245 MB, into `~/.local/share/claude`. But that directory goes when the
container does, so the next run is back on the pinned version. Upgrade
through the `FROM` line instead.

`claude doctor` gives warnings about `~/.local/bin`. They are expected,
because the image puts Claude at `/usr/local/bin/claude`.

### On the `latest` tag

A repo can follow the newest release instead of pinning one:

```dockerfile
FROM ghcr.io/ai-progress-agent/claude-container:latest
```

Then Dependabot has nothing to bump. `docker/cc build` and `docker/cc upgrade`
pull the newest image first, and the stub copies that image's launcher. Other
runs use the image already on the Mac. So to upgrade, run `docker/cc build`.

The cost is the pin. Two machines can run different versions. A new major
version arrives with no pull request, even when it needs changes to the
repo's `docker/` files. A repo on `latest` needs the current `stub/cc`. An
older stub copies the launcher once and never updates it. Each image's
launcher copy stays under `~/.cache/claude-container`, one folder for each
image, until you delete it.

### From v1 to v2

v2 replaces the bash launcher with a Python one. Two things change for a
repo:

- The repo's settings move from `docker/kit.sh` to `docker/kit.toml`.
- Each person who runs `docker/cc` needs uv on the Mac.

A repo whose `FROM` line names a v1 tag keeps the bash launcher, and its
`kit.sh` keeps working. To move a repo to v2:

1. Install uv on the Mac: `brew install uv`.
2. Move the `FROM` line in `docker/Dockerfile` to a v2 tag, or merge
   Dependabot's pull request that does.
3. Run `docker/cc`. With a `docker/kit.sh` and no `docker/kit.toml`, the
   launcher does not start the container. It prints the `kit.toml` with the
   same settings, below a message that says what to do. Save those lines as
   `docker/kit.toml`. When `kit.sh` sets only defaults, the message says to
   make `docker/kit.toml` an empty file.
4. Delete `docker/kit.sh`, and commit `docker/kit.toml`. Then run `docker/cc`
   again.

Do not send the output to `docker/kit.toml` with `>`. The shell makes the
empty file first, and the launcher then reads it in place of `kit.sh`. So the
container starts with the defaults.

The printed `kit.toml` holds each setting that `kit.sh` changes from its
default. It also keeps a `project_name` or `agent_browser_port` that `kit.sh`
sets to this clone's default. Another clone can have another default, so the
value stays. A setting that `kit.sh` builds, such as a path made from `$repo`,
comes out as the value it had on your Mac. Check those before you commit.
`docker/cc.local` needs no change.

## How the container mirrors the Mac

Each Mac path mounts at the same absolute path inside the container. So
plugin paths, hook paths, dotfiles links and worktree paths all resolve
unchanged. The repo sits at its Mac path too, so a git `includeIf` that picks
your work email by path still matches. The container also takes the Mac's
time zone, and your terminal's `TERM` and `COLORTERM`.

The launcher mounts the usual Claude Code and git config read-only. That is
the files and directories under `~/.claude` that hold your settings, plus
`~/.agents`, `~/bin`, `~/.config/ccstatusline`, `~/.gitconfig`,
`~/.gitconfig.local` and `~/.config/git`. It leaves out any path your machine
does not have, so it works with any setup. The list is `HOST_CONFIG` in
[`kit/src/launcher/mounts.py`](kit/src/launcher/mounts.py).

A file in that list can be a link, into a dotfiles repo for example. For a
link, the launcher mounts the directory that holds the link's target under
`/mnt/host-links/`. Then it recreates the link inside to point there. Some
editors save a file by writing a new copy over the old one. With this
handling, that save still reaches the container. A plain file mounted on its
own does not get the save: the container keeps reading the copy that existed
at start.

Links elsewhere, such as a skill that links into your dotfiles repo, resolve
only if you mount their targets yourself. See
[`docker/compose.local.yaml`](#dockercomposelocalyaml).

By default, the container can write to these Mac paths:

- the repo
- the main clone's `.git`, when you run `docker/cc` from a linked worktree
- the project's memory and session transcripts, shared with the Mac
- each sibling repo that `writable_siblings` names (see below)

"The repo" is the whole clone, not only what git tracks. A folder you keep at
the clone's root is readable and writable inside, even when `.gitignore`
keeps it out of every commit. So treat anything you keep in the clone as
seen.

Each mount you add in `docker/compose.local.yaml` without `:ro` is one more.

### Folder marketplaces

A plugin marketplace added from a folder on the Mac is read from that folder.
Without it, Claude inside drops the marketplace's plugins. So the launcher
mounts each such folder read-only, at its own path. It finds them in
`~/.claude/plugins/known_marketplaces.json`, which lists marketplaces from
`settings.json` and from `/plugin marketplace add` alike. That file can lag
behind `settings.json`, such as on a new machine. So the launcher also reads
`extraKnownMarketplaces` in `~/.claude/settings.json`. When the two files give
one marketplace different sources, the one in `settings.json` wins.

The launcher leaves a marketplace out in these cases:

- `skip_marketplaces` in [`docker/cc.local`](#dockercclocal) names it.
- Its folder is missing on the Mac.
- The container already sees its folder: the folder is in the repo, in a
  mount the launcher already adds, such as a sibling repo or
  `~/.claude/plugins`, or in a bind mount in any compose file. Those files
  are the kit's `compose.yaml`, `docker/compose.repo.yaml` and
  `docker/compose.local.yaml`.

At start, the launcher prints these lines:

| Line                                                        | What it means                                                     |
| ----------------------------------------------------------- | ----------------------------------------------------------------- |
| `docker/cc: mounted marketplaces read-only: <names>`        | These marketplaces' plugins load inside                           |
| `docker/cc: skipped marketplaces: <names>`                  | `skip_marketplaces` keeps these out, so their plugins do not load |
| `docker/cc: marketplace <name> has no folder at <path> ...` | The Mac has no folder at that path, so the plugins do not load    |
| `docker/cc: docker compose config failed, ...`              | The compose files did not read, so a marketplace can mount twice  |
| `docker/cc: ... does not read as JSON, ...`                 | One of the two files is not valid JSON, so no marketplace mounts  |

### Sibling repos

The container can also read the program's other repos, but cannot write to
them. The launcher looks at each directory next to the main clone. It mounts
the directory read-only, at its own path, if both of these are true:

- it is a clone, with its own `.git` directory
- its `origin` has the same host and owner as the repo's, such as
  `github.com/AI-Progress-Agent`

So another client's repo or a partner folder in the same parent directory
stays out. A worktree finds the same repos as its main clone, wherever the
worktree sits. At start, the launcher names each repo it mounted. A clone
cannot fetch inside, so it is as current as the Mac's last fetch.

A sibling named in `writable_siblings` mounts writable instead. Its
`.git/hooks` and `.git/config` still mount read-only, as the repo's do, and
so do the other files that tell git where to find them. The launcher searches
it for a `.git` or a bare repo, as it searches the repo. See
[GitHub operations](#github-operations). Use
it for a repo that has no container of its own, so its work happens in this
repo's sessions. Code written there can run on the Mac too, such as a plugin
that a Mac session loads. Review it as you would a change to the repo's
hooks.

### What stays apart

The container keeps these apart from the Mac:

- the Claude login and `~/.claude.json`, since a second writer could corrupt
  the Mac's copy
- every other project's history, since `~/src` can hold unrelated clients'
  work

Some things stay on the Mac:

- Codex, and your user-level MCP servers
- the status line's time-left widget, which reads the keychain
- a signed-in browser: `agent-browser` inside drives a Chrome on the Mac (see
  [The browser](#the-browser))

Plugins mount read-only, so a plugin update fails inside, and so does a
plugin that saves its own state.

The container runs with no Linux capabilities and with `no-new-privileges`,
so nothing inside can become root. Memory and process limits keep a runaway
process from starving Docker Desktop's VM. These settings are in
[`kit/compose.yaml`](kit/compose.yaml), and a repo's
`docker/compose.repo.yaml` can override the limits.

### The browser

`agent-browser` inside drives a Chrome on the Mac. Use it for a page that
needs a login.

1. On the Mac, open Chrome with a profile of its own and remote debugging on
   the repo's port:

   ```bash
   open -na "Google Chrome" --args --remote-debugging-port=<port> \
     --user-data-dir="$HOME/.agent-browser/profiles/<repo>"
   ```

2. Sign that Chrome in once. The profile keeps the login.
3. Inside, run `agent-browser` as usual. When the repo has a port, the
   launcher sets `AGENT_BROWSER_CDP` at start, so each command connects to
   that Chrome.

The port is `agent_browser_port`, which by default comes from the `cdp` field
of the repo's `agent-browser.json`. Keep it in that file, so the Mac task that
opens Chrome and a Mac session read the same port. Give each repo its own
port, so the Chromes of two repos can run at once.

The launcher reaches the Mac through `host.docker.internal`, which Docker
Desktop provides. If the name does not resolve, it warns at start and leaves
`AGENT_BROWSER_CDP` unset.

### Notifications

When Claude waits for you or finishes a turn inside, your Mac's notifier runs
on the Mac. The image's managed settings, in
`/etc/claude-code/managed-settings.json`, add `Stop` and `Notification`
hooks. Each hook writes the event's JSON as a file into `/run/cc-notify`. The
launcher mounts a new temporary directory there for each session, and it
checks that directory every half second. For each file it runs
`notify_host`, then deletes the file.

`notify_host` runs `cc-notify.mjs` when the Mac has it on the `PATH`, and does
nothing otherwise. `cc-notify.mjs` is a personal script, not part of this
kit, so you probably do not have it. Without a notifier, you get no
notification and no warning. To set one up:

1. Install a notifier on the Mac, for example `brew install terminal-notifier`.
2. Redefine `notify_host` in [`docker/cc.local`](#dockercclocal) to call it.
   The example below does this.
3. Restart `docker/cc`.
4. Wait for Claude to finish a turn. A notification appears on the Mac.

The launcher runs `notify_host` this way:

- The event's JSON arrives on stdin.
- It runs in the session's directory. A directory outside the repo becomes
  the repo root.
- It has the launcher's environment, so `$TMUX_PANE` names your pane.
- Its output goes to `cc-notify.log` in `$TMPDIR`, because Claude owns the
  terminal. Look there when a notifier fails.

```bash
notify_host() { terminal-notifier -title Claude -message "Waiting in ${PWD##*/}"; }
```

The directory and the loop go when the container does. A container started
without `docker/cc` has no `/run/cc-notify`, so the hooks drop the event.
If an admin at your company pushes managed settings to Claude, those
replace the image's file, and nothing is forwarded.

Your Mac's own hooks may call `cc-notify.mjs` by name too. The base image
installs a stand-in at `/usr/local/bin/cc-notify.mjs`, which reads its input
and does nothing. The `PATH` in
[`example/docker/Dockerfile`](example/docker/Dockerfile) puts `~/bin` last,
after `/usr/local/bin`. So the stand-in wins over a real `cc-notify.mjs` in
`~/bin`, and those hooks neither fail nor notify twice. A repo's
`docker/Dockerfile` that sets its own `PATH` has to keep that order.

## GitHub operations

Everything GitHub does inside the container goes over HTTPS with your Mac's
`gh` login. The container has no SSH keys and no SSH agent.

1. At start, the launcher runs `gh auth token` on the Mac and passes the
   token into the container as `GH_TOKEN`. It goes through the environment,
   not the command line, so `ps` does not show it. If `gh` has no login on the
   Mac, the launcher warns you, and `git push` and `gh` fail inside.
2. The `origin` remote in `.git/config` is SSH (`git@github.com:...`). The
   image's system git config rewrites every `git@github.com:` URL to
   `https://github.com/`, so `git remote -v` inside shows HTTPS.
3. Your `~/.gitconfig` may name `/opt/homebrew/bin/gh auth git-credential`
   as the credential helper. That macOS path does not exist in Debian, so the
   image links it to `/usr/bin/gh`. So that config works unchanged, and `gh`
   answers git with `GH_TOKEN`.
4. `gh` itself reads `GH_TOKEN`, so `gh pr`, `gh issue` and the project board
   moves work too.

So the container can do on GitHub whatever your Mac's `gh` login can do. To
narrow that, sign `gh` in on the Mac with a token of smaller scope.

The repo mounts writable, except `.git/hooks` and `.git/config`, which mount
read-only. Git runs commands from both. So a write there from the container
would run on your Mac at its next `git` command, and `git status` would not
show it. Commits and pushes still work. A command that writes `.git/config`
cannot write it inside. Run from a linked worktree, `docker/cc` also mounts
the main clone's `.git`, writable, because the worktree's commits and branches
live there. Its `hooks` and `config` mount read-only, in the same way. When
`docker/` sits below the clone's root, the clone's `.git` mounts read-only.
Git inside stops at the repo's mount and never finds it, so nothing inside
needs to write it.

Other files tell git where to find hooks and a config. A container that
writes one could send git on the Mac to hooks or a config of its own. These
mount read-only:

- The `.git` file and the `commondir` file of each of the clone's worktrees.
  Every worktree's `.git` file mounts, even for a worktree the container
  cannot see. Otherwise `git worktree prune` inside would take that worktree
  for deleted, and remove its records from the Mac's `.git`.
- A `commondir` file in the main clone's `.git`. Git takes the directory it
  names for the `.git`. The launcher first makes one that names the `.git`
  itself, as `../.git`, which git reads as no `commondir` at all. It does the
  same in each git directory it guards: a writable sibling's `.git`, a
  nested clone's, and each submodule's. Each file stays after the session.
  With it, `git rev-parse --git-common-dir` prints an absolute path in the
  main clone, not `.git`.
- With `extensions.worktreeConfig` on, each `config.worktree`. The launcher
  first makes any missing one as an empty file.
- The `hooks`, `config` and `commondir` of each submodule's git directory,
  under `.git/modules`. `git status` in the repo runs git in each submodule.
- The folder that `core.hooksPath` names. See below.
- Each config file inside a writable mount that git reads, an included one
  among them, and each file or folder that a command value in the config
  names. See below.

The Mac's disk ignores letter case. Each file above mounts read-only on its
own, in a writable folder. So a write inside to `.git/CONFIG`, by a name in
other letter case, would get past the mount of `.git/config` and replace the
Mac's file. A rename, such as `mv t .git/CONFIG`, would too.

To stop that, at start the launcher sets the Mac's immutable flag, `uchg`, on
each file that mounts read-only on its own inside a writable mount. That
covers each `config`, `commondir`, `config.worktree` and worktree `.git` file
above that the container can reach. It also covers each file that the config
includes or names. Every write, rename, removal and `chmod`
of the file from inside then fails with "Operation not permitted". Root
inside cannot clear the flag. A folder, such as `hooks`, needs no flag: a
write to `.git/HOOKS` lands in the read-only mount. The flag does not guard a
file made during the session, such as a new worktree's `commondir`. The
search described below covers those.

While the session runs, git on the Mac cannot write a flagged file either.
These commands fail on the Mac with "Operation not permitted":

- `git config`
- `git push -u`
- `git branch --set-upstream-to`
- `git remote add`
- `git worktree remove`

Run them after the session ends. `git status` and `git commit` on the Mac
still work. `git worktree remove` deletes some of the worktree's files
before it fails on its flagged `.git` file. Which ones depends on the order
it reads the folder. Without `--force`, it refuses a worktree with
uncommitted changes, so `git restore .` in the worktree brings back the
tracked files. An ignored file, such as `.env`, can be lost.

When the session ends, the launcher clears each flag it set. Two sessions can
share a file, such as two worktrees of one clone, or two repos with one
writable sibling. Each launcher records the files it flags, in
`$TMPDIR/cc-flags`. A flag stays set until the last session that holds it
ends. A crash that ends the launcher's watcher too, such as the Mac losing
power, leaves its flags set. The next session that guards the same file
clears it at its end. Or clear one yourself with `chflags nouchg <file>`.
The opposite can happen too. A `kill -9` of `docker/cc` can leave its
container running. The watcher then clears the flags while that container
can still write.

`chflags` fails on a read-only disk, and on a file another user owns. It
works on an exFAT or FAT32 drive. When `chflags` fails, the launcher
clears the flags it set, names each file, and does not start:

```text
docker/cc: these could not be flagged read-only on the Mac, so the container did not start:
  /path/to/repo/.git/config
chflags fails on a read-only disk, and on a file another user owns.
Move the repo to a writable disk, or give each file to your user with chown.
```

A repo can set `core.hooksPath` to run hooks from another folder. Husky
sets it to `.husky/_`. A `.gitignore` in `.husky/_` leaves out every file. So
`git status` would not show a hook that the container writes there. At start, the
launcher asks git which hooks folder each worktree uses. It asks in each
worktree of the repo, of each writable sibling, of each nested clone and of
each submodule. Git follows `core.hooksPath` from every config it reads, your
global one too. When that folder sits inside a writable mount, it mounts
read-only. The launcher first makes a missing one on the Mac, empty. It
stays after the session. A folder outside every writable mount gets no
mount.

The launcher guards only the `core.hooksPath` that is set at start. Run the
install that sets it, such as `pnpm install` with Husky, on the Mac before
`docker/cc` starts. Or restart `docker/cc` after it. Husky's install inside
cannot set `core.hooksPath`, because `.git/config` is read-only. Husky then
writes no file and exits 0.

At start, the launcher mounts the hooks folders of the worktrees that exist
then, for the branch checked out then. During the session, it reads
`core.hooksPath` again in each worktree, about every 5 seconds. These
worktrees can then have a hooks folder with no read-only mount:

- a worktree that `git worktree add` makes inside
- a worktree whose folder is missing at start, if the container makes it
  again
- a worktree where the container switches to a branch that an
  `[includeIf "onbranch:..."]` section sets `core.hooksPath` for

When such a folder sits inside a writable mount, the launcher moves it aside,
to its name plus `.cc-blocked`, as it moves a `.git`. See below. The
worktree keeps working, and git on the Mac finds no hooks to run there. In a
repo that commits its hooks folder, the hooks then show as deleted in that
worktree. Git on the Mac can still run a hook in the seconds before the
launcher moves its folder.

The container can make a hooks folder that the launcher could not move
safely, such as a symbolic link, or a folder that holds a worktree. Then the
launcher moves aside the first symbolic link on the way, so git on the Mac
finds no folder. With no link, it moves aside the worktree's `.git` file, so
git on the Mac no longer takes the folder for a worktree. It never moves a
`.git` directory. Do not point `core.hooksPath` at a folder that the launcher
refuses at start, such as the worktree's root, in an `onbranch` section.

The launcher refuses to start when it cannot mount the folder read-only.
It refuses these folders:

- the worktree's root, or a folder that holds it
- a writable mount, or a folder that holds one
- a folder that holds a read-only mount
- a path through a symbolic link inside a writable mount
- a path with a `..` that steps out of a folder inside a writable mount
- a path through a file

It names each worktree and its value:

```text
docker/cc: core.hooksPath names a folder the launcher cannot mount read-only, so the container did not start:
  /path/to/repo: core.hooksPath = .
Point core.hooksPath at a folder below the worktree's root, with no symbolic link or .. on the way. Or unset it.
```

A repo's config can name other files in the repo, and git on the Mac reads
or runs them. A config file that `include.path` names can set any value.
`core.fsmonitor`, an alias that starts with `!` and a filter driver each
name a command that git runs, some at `git status`. At start, the launcher
reads every config file that git reads in each worktree: the system, global
and repo configs, each `config.worktree`, and each file they include. Inside
a writable mount, these mount read-only:

- Each config file, an included one among them. Every `include.path` and
  `includeIf.<condition>.path` counts, whatever its condition. The launcher
  first makes a missing included file on the Mac, empty. Git skips a missing
  one, so the container could make it otherwise. The file stays after the
  session.
- Each file or folder that a command value names. The launcher splits the
  value into words, as a shell does. It also splits a string that a shell
  runs, as in `sh -c 'cmd'`. It resolves a relative word against each folder
  the shell may be in: the worktree's root, or a folder that a `cd` goes to.
  A `cd` in a pipeline, or after `&&`, may leave the shell where it was, so
  a later word resolves against both folders. A `cd` in a subshell, such as
  `(cd x && ...)`, ends at the `)`, so a word after it resolves against the
  folder before the subshell.

The launcher checks the values of these keys: `core.fsmonitor`,
`core.editor`, `core.pager`, `pager.*`, `core.sshCommand`, `core.gitProxy`,
`core.askPass`, `core.alternateRefsCommand`, `sequence.editor`, `alias.*`,
`hook.*.command`, `credential.helper`, `credential.*.helper`,
`diff.external`, `diff.*.command`, `diff.*.textconv`, `filter.*.clean`,
`filter.*.smudge`, `filter.*.process`, `merge.*.driver`, `difftool.*.cmd`,
`difftool.*.path`, `mergetool.*.cmd`, `mergetool.*.path`, `gpg.program`,
`gpg.*.program`, `gpg.ssh.defaultKeyCommand`, `remote.*.uploadpack`,
`remote.*.receivepack`, `trailer.*.cmd`, `trailer.*.command`,
`gc.recentObjectsHook`, `browser.*.cmd`, `man.*.cmd`, `guitool.*.cmd` and
`sendemail.*cmd`. An alias counts only when it starts with `!`. A credential
helper counts when it starts with `!` or is an absolute path. Keys that only
read a file, such as `core.excludesFile` and `commit.template`, run nothing,
so the launcher does not check them.

So you cannot edit a file or folder that the config names inside. Edit it on
the Mac after the session. The launcher flags each such file, as above.

The launcher does not catch everything:

- It skips a word that the shell expands when the command runs, such as one
  with a `$`. So it does not catch a path that a command builds, such as
  `$(git rev-parse --show-toplevel)/x`.
- It skips a long option with no `=`, such as `--wait`. It reads a short
  option's value after its letter, as in `-Ftools/ssh.cfg`.
- It reads the config only at start. It does not guard a value set during
  the session, or a path made during it.
- A worktree made inside resolves a relative word against its own root. The
  file there stays writable. Before you run git on the Mac in such a
  worktree, check the files that the config's commands name there.

The launcher refuses to start when the config names a path it cannot mount
read-only. It refuses the paths it refuses for `core.hooksPath`, and an
included path that is not a file. It also refuses a missing path inside a
writable mount that a word with a `/`, or a `cd`, names, because the
container could make it. It names the worktree, the key, the value and the path:

```text
docker/cc: a config value names a path the launcher cannot mount read-only, so the container did not start:
  /path/to/repo: core.fsmonitor = ./tools/fsm.sh names /path/to/repo/tools/fsm.sh
Make each missing path. Or point the value at a path below the worktree's root, with no symbolic link or .. on the way. Or unset it.
```

Linux lets the container rename a folder that holds a read-only mount. The
mount moves with the folder. The container could then make a new folder at
the old path, with its own hooks or config. Git on the Mac would read them.
Linux refuses to rename a folder that is a mount point. So each folder
above a read-only file, up to the root of the writable mount it sits in, also
mounts on its own. It mounts writable, at its own path. These folders are:

- `.git/modules` and `.git/modules/<name>`, for each submodule
- `.git/worktrees` and `.git/worktrees/<name>`, for each worktree
- each folder that holds a worktree's `.git` file, such as `.worktrees` and
  `.worktrees/<name>`, or `.claude`, `.claude/worktrees` and
  `.claude/worktrees/<name>`
- each folder down to a nested clone's `.git`, such as `vendor`,
  `vendor/sdk` and `vendor/sdk/.git`
- a writable sibling's `.git`
- each folder above the `core.hooksPath` folder, such as `.husky`
- each folder above a file or folder that the config names, such as `tools`

The repo's own `.git` is already a mount of its own, and so is a folder that
a compose file mounts. Writes inside each folder still work. A rename or
removal of one of these folders fails inside with "Resource busy", in any
letter case. So do `git worktree move` and `git worktree remove`. Do them on
the Mac after the session. A move of a file into or out of one of these
folders, such as `git mv vendor/a src/a`, fails with "Cross-device link".
Plain `mv` copies the file instead.

A read-only mount needs a file that exists at start. So these stay writable:

- a `.git` anywhere in the repo, as a directory, a file or a link, with its
  letters in upper or lower case. The Mac's disk ignores letter case, so git
  takes `.GIT` for `.git`. Git run below it on the Mac uses it in place of
  the repo's own.
- a bare repo anywhere in the repo: a folder with a `HEAD` and either a
  `commondir` or both `objects` and `refs`, which git takes for a `.git`
- the files of a worktree or a submodule made during the session

The launcher searches the repo and each writable sibling for these. At
start, it refuses to start if it finds one. It names each one, for you to
delete:

```text
docker/cc: these could lead git on the Mac to hooks or a config written inside, so the container did not start:
  /path/to/repo/vendor/sdk/.git
Delete each one. To keep a clone or a bare repo inside the repo or a writable sibling, add its path to nested_clones in docker/kit.toml or docker/cc.local.
```

To keep a clone inside the repo, add its path to `nested_clones` in
`docker/kit.toml` or [`docker/cc.local`](#dockercclocal). Its `.git/hooks`,
`.git/config` and the files above then mount read-only, as a writable
sibling's do. You can add a bare repo, such as a test fixture, in the same
way. Its own `hooks` and `config` then mount read-only. For a clone inside a
writable sibling, add its absolute path.

During the session, the launcher searches again about every 5 seconds. It
moves each one it finds aside, to its name plus `.cc-blocked`, and tells
`notify_host`. When the session ends, it names each one it moved.

A worktree that `git worktree add` makes inside passes, with two conditions.
Its `commondir` must name the main clone's `.git`. Its `config.worktree`
must stay empty. A submodule made inside does not pass, whether by
`git submodule add` or `git submodule update --init`. Its config is
writable, so its `.git` file moves aside. The launcher guards only what
exists at start, so the same happens to a clone or a submodule made on the
Mac during the session. Add or initialize submodules on the Mac before
`docker/cc` starts. Git on the Mac can still read a new file in the seconds
before the launcher moves it.

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
busy` twice. Then it prints "set up to track" as if it worked, and exits 0.
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

After the session ends, record the upstream on the Mac with
`git branch --set-upstream-to=origin/<branch> <branch>`. During the session,
that command fails on the Mac too, because `.git/config` is flagged. The next
session sees the upstream. See
[How the container mirrors the Mac](#how-the-container-mirrors-the-mac).

No setting fixes this inside. Git always writes an upstream to `.git/config`,
even when that file includes others. Git writes a new file, then renames it
over the old one. A file mounted on its own refuses that rename. So a
writable mount of `.git/config` fails the same way.

Deleting a branch inside warns that git cannot update `.git/config`. The
branch is still deleted.

In a fresh worktree, `git rebase` can fail on a clean tree with "Your local
changes would be overwritten", naming a different file on each try. Replay
the branch in the object store instead. This squashes the branch into one
commit on `origin/main`, with the last commit's message:

```bash
tree=$(git merge-tree --write-tree origin/main HEAD)
commit=$(git commit-tree "$tree" -p origin/main -m "$(git log -1 --format=%B)")
git reset --hard "$commit"
```

`git merge-tree` exits 1 and prints the conflicts when the two sides
conflict. Stop there and resolve them by hand.

The image also sets `safe.directory '*'`, because the repo is a bind mount
owned by another user ID and git would refuse it as dubious ownership.

The image has the `gh stack` extension, pinned in the base `Dockerfile`.
GitHub merges a stacked PR only with `gh stack merge`: `gh pr merge` and the
merge REST endpoint both refuse it. The read-only `.git/config` does not stop
`gh stack`. With one remote it pushes to that remote without
`remote.pushDefault`.

## Your machine's own setup

Three uncommitted files carry what only your machine needs. All three are
optional, and the repo's `.gitignore` keeps them out of commits. The snippets
below come from one developer's working setup. Change the paths to yours.

| File                        | What it adds                          | Takes effect         |
| --------------------------- | ------------------------------------- | -------------------- |
| `docker/compose.local.yaml` | Mounts and environment variables      | The next `docker/cc` |
| `docker/cc.local`           | Commands on the Mac, and setup inside | The next `docker/cc` |
| `mise.local.toml`           | Tools installed in the image          | `docker/cc build`    |

### `docker/compose.local.yaml`

The launcher stacks this file on the kit's `compose.yaml` and the repo's
`docker/compose.repo.yaml`. Add a mount here when your config links into a
directory the `optional` list does not cover, such as a dotfiles repo or a
skill's state. A folder marketplace needs no mount here: the launcher mounts
it. See [Folder marketplaces](#folder-marketplaces). A marketplace mount
already in this file keeps working, and you can delete it.

```yaml
services:
  claude:
    volumes:
      # The dotfiles repo. ~/.claude/CLAUDE.md, the settings and ~/bin's
      # scripts are links into it.
      - ${HOST_HOME}/.home-directory:${HOST_HOME}/.home-directory:ro
      # A skill's hooks keep their log here, so this one is writable.
      - ${HOST_HOME}/.claude/state/writing-line:${HOST_HOME}/.claude/state/writing-line
```

Mount each path at its own path, as above, so links into it resolve. Add
`:ro` unless the container has to write there.

Every source you name must exist on the Mac. A missing one does not fail:
Docker creates it as an empty directory owned by root, and the container sees
an empty directory.

The launcher sets `HOST_HOME`, `REPO`, `REPO_GIT`, `REPO_GIT_MODE`,
`HOST_USER`, `PROJECT_KEY` and `PROJECT_NAME`, and you can use them here.
`REPO_GIT` is the main clone's `.git`, which differs from `$REPO/.git` in a
linked worktree. A variable that `docker/cc.local` exports works here too. A
relative path resolves against the repo's `docker/` directory.

### `docker/cc.local`

This file stays bash. A helper,
[`kit/cc-local.bash`](kit/cc-local.bash), sources it once per session on the
Mac, before the container starts. The helper then reports to the launcher what
the file set. It keeps running for the session, and runs the file's
`notify_host` for each event. So `notify_host` sees the functions and
variables the file defines.

The helper sources the file inside a function. So `declare` and `typeset`
need `-g` to set a variable that the launcher or `notify_host` reads
afterwards. `$@` holds the arguments that go to Claude or bash. Use the file
for five things:

- Export a value that a variable in `compose.local.yaml` copies. List the
  variable name with no value under `environment:`, and Compose copies it from
  the launcher's shell.
- Append commands to `local_setup`. They run inside the container, after
  the repo's `start_commands` and before Claude starts.
- Redefine `notify_host`, the command that turns an event inside into a
  notification on the Mac. See [Notifications](#notifications).
- Name in `skip_marketplaces` each folder marketplace to keep out of this
  repo's container, such as one that belongs to another client. Use the name
  `/plugin` shows. See [Folder marketplaces](#folder-marketplaces).

  ```bash
  skip_marketplaces=(other-client-marketplace)
  ```

- Add to `nested_clones` each clone inside the repo that is yours alone,
  such as one that `.gitignore` keeps out. See
  [GitHub operations](#github-operations).

  ```bash
  nested_clones+=(scratch/upstream)
  ```

This is where a login that the Mac keeps in the keychain gets handed over. The
container cannot read the keychain, so the Mac exports the login and the
container writes it to a file. This example hands over the login of `gws`,
the Google Workspace CLI:

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
  the Mac's disk and it goes when the container does.
- Write it with `umask 077`, so only your user can read it.
- `unset` the variable at the end of `local_setup`, so Claude's tools do not
  inherit it.
- Warn on the Mac when the export is empty, so a missing login is not a
  mystery inside.

`local_setup` starts as `true`, and the example replaces it. With a second
recipe in the same file, append instead: `local_setup="$local_setup; ..."`.

### `mise.local.toml`

The repo layer installs the tools in this file on top of the repo's
`mise.toml` and the base image's tools. It sits at the repo root, and mise on
the Mac reads it too. Rebuild the image after you change it, with
`docker/cc build`.

```toml
[tools]
# The Google Workspace CLI.
"github:googleworkspace/cli" = "latest"

# ctrl-g in Claude opens the EDITOR the Mac's settings name.
neovim = "latest"
```

A tool that needs a login usually needs a `docker/cc.local` recipe as well.
Installing it only puts the command on the `PATH`.

A tool in this file is on the `PATH` only in this repo. A sibling repo's
session does not have it. Anywhere else, call the tool by the full path that
`mise which <tool>` prints in this repo.

## When something is missing

| What you see inside                                                  | Why                                                         | Fix                                                                                   |
| -------------------------------------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `command not found`                                                  | The image does not have the tool                            | Add it to `mise.local.toml`, then run `docker/cc build`                               |
| A CLI works on the Mac but is not signed in inside                   | The Mac keeps the login in the keychain                     | Export it in `docker/cc.local`, write it with `local_setup`, then restart `docker/cc` |
| A link in your config points at a path that is missing               | Nothing mounts the link's target                            | Mount the target directory in `docker/compose.local.yaml`, then restart `docker/cc`   |
| A path is there but empty, and owned by root                         | Its source in `docker/compose.local.yaml` is not on the Mac | Fix the source path in `docker/compose.local.yaml`, then restart `docker/cc`          |
| A setting you just changed on the Mac is not seen                    | A plain file mounts as it was at start                      | Restart `docker/cc`                                                                   |
| `git push` or `gh` fails to authenticate                             | `gh` has no login on the Mac                                | Run `gh auth login` on the Mac, then restart `docker/cc`                              |
| `could not write config file …/.git/config: Device or resource busy` | `.git/config` is read-only inside                           | Work without the upstream, as [GitHub operations](#github-operations) says            |
| A plugin update fails                                                | Plugins mount read-only                                     | Update the plugin on the Mac                                                          |
| A plugin from a folder marketplace is missing                        | It did not mount, and a start line at `docker/cc` says why  | See [Folder marketplaces](#folder-marketplaces), then restart `docker/cc`             |
| A program repo is not at `../<name>`                                 | It is not cloned next to this one, or its `origin` differs  | Clone it next to this repo from the same GitHub org, then restart `docker/cc`         |
| A program repo at `../<name>` is missing recent commits              | It cannot fetch inside                                      | Use `gh`, or run `git fetch` in it on the Mac                                         |
| `agent-browser` cannot connect                                       | No Chrome is open on the Mac on the repo's port             | Open Chrome on the Mac as [The browser](#the-browser) says                            |
| `docker/cc: host.docker.internal does not resolve` at start          | The Docker engine is not Docker Desktop                     | Run the container under Docker Desktop                                                |
| `claude doctor` warns about `~/.local/bin`                           | The image puts Claude at `/usr/local/bin/claude`            | Nothing: the warning is expected                                                      |

An agent inside the container can edit the three files in
[Your machine's own setup](#your-machines-own-setup), because they sit in the
writable repo. But the edit does nothing in the running container. The
launcher reads each file on the Mac, when it starts or builds the image. So
make the edit or propose it. Then tell the developer what to restart or
rebuild.

## Working on the kit

The repo has these parts:

| Path         | What it holds                                                                                                     |
| ------------ | ----------------------------------------------------------------------------------------------------------------- |
| `Dockerfile` | the base image                                                                                                    |
| `image/`     | the files the base image installs: the global `mise.toml`, the notification hook, `add-user` and `release-before` |
| `kit/`       | the launcher and the kit's `compose.yaml`, shipped at `/opt/kit`. A uv project, listed below                      |
| `stub/cc`    | the `docker/cc` each repo copies                                                                                  |
| `example/`   | a repo layer in miniature, which the smoke test builds                                                            |
| `test/`      | the smoke test                                                                                                    |
| `.github/`   | the CI and release workflows, and Dependabot's config (see [Releases](#releases))                                 |

`kit/` holds these files:

| Path                                     | What it holds                                                                                         |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `cc`                                     | the bash entry point the stub runs. It checks for uv, syncs the kit's `.venv`, then runs the launcher |
| `cc-local.bash`                          | the helper that sources `docker/cc.local` and runs its `notify_host`                                  |
| `compose.yaml`                           | the container's service, mounts and limits                                                            |
| `pyproject.toml`, `uv.lock`              | the uv project. The launcher uses only the standard library, and the dev group holds the checks       |
| `.python-version`                        | the Python that uv runs the launcher with                                                             |
| `src/__main__.py`                        | what `kit/cc` runs, which calls `launcher.main`                                                       |
| `src/launcher/main.py`                   | `docker/cc`'s commands                                                                                |
| `src/launcher/plan.py`                   | the planning step. It reads the Mac's state and returns a `RunPlan`, and writes nothing               |
| `src/launcher/execute.py`                | the executor. It does each side effect the `RunPlan` names, then runs `docker compose`                |
| `src/launcher/settings.py`               | `docker/kit.toml` and its checks, and the `kit.toml` it prints for a v1 repo                          |
| `src/launcher/local.py`                  | `docker/cc.local`, through `cc-local.bash`                                                            |
| `src/launcher/watcher.py`                | the session's watcher on the Mac: notifications, searches, and the flags at the end                   |
| `src/launcher/mounts.py`                 | the Mac's Claude and git config, and the folders above each read-only mount                           |
| `src/launcher/gitdirs.py`, `pointers.py` | the files that lead git to a clone's hooks and config, and the search for new ones                    |
| `src/launcher/hooks.py`, `configs.py`    | the `core.hooksPath` folder, and what the repo's config names                                         |
| `src/launcher/flags.py`                  | the Mac's immutable flag on each guarded file                                                         |
| `src/launcher/clones.py`                 | sibling repos and nested clones                                                                       |
| `src/launcher/marketplaces.py`           | folder marketplaces                                                                                   |
| `src/launcher/draft.py`                  | the plan while the planning step builds it, with its mounts and refusals                              |
| `src/launcher/reach.py`                  | where the container can write, and where a path that git on the Mac reads sits against it             |
| the rest of `src/launcher/`              | helpers the modules above share: git, paths, compose, the environment and output                      |
| `tests/`                                 | the pytest tests. The image leaves them out                                                           |

Run the checks with mise:

- `mise run check` runs shellcheck, ruff, basedpyright and the tests that need
  no Docker. The pytest tests in `kit/tests` run the launcher through the stub
  with a fake `docker`. The image leaves them out.
- `mise run build` builds the base image as `claude-container:dev`, builds the
  example repo's layer on it, and smoke-tests what the user sees inside.

To try a kit change on a real repo before a release, point the repo's stub at
your checkout with `KIT_DEV`:

```bash
KIT_DEV=../claude-container docker/cc build
KIT_DEV=../claude-container docker/cc
```

The stub then runs the checkout's launcher. `build` and `upgrade` build the
checkout's base image first, and the repo's image is built on it in place of
the image the `FROM` line names. A plain `docker/cc` builds that base image
too, when it is missing. The `FROM` line itself stays as it is. Run a
plain `docker/cc build` afterwards to go back to the release.

### Releases

The kit follows semantic versioning. A change to what `kit.toml`, the stub or a
repo's `Dockerfile` must contain is a major version, because each repo has to
change with it.

CI on GitHub's arm64 runner checks every push to `main` and every pull
request: `mise run check` and the smoke build. A release happens two ways:

- Push a tag such as `v1.2.0`. CI checks and builds that commit,
  smoke-tests it, and publishes it to GHCR, the GitHub Container Registry.
  Then it creates the GitHub release. If any step fails, the tag stays
  without a release, and the weekly rebuild skips it. A failure in that last
  step leaves the image published on GHCR without a release.
- Each Monday, CI rebuilds the newest release's commit and publishes it as
  the next patch, such as `v1.2.1`. That picks up Claude Code and mise releases
  at least seven days old, newer global tools and Debian's security updates.
  It builds the tag, not `main`, so a change waiting on `main` never ships as
  a patch. Running the `release` workflow by hand from the Actions tab does
  the same at once.

Each publish also moves the `latest` tag, when its version is the newest.

Dependabot bumps the base `Dockerfile`'s Debian digest each week and the
pinned GitHub Actions each month, each after a seven-day wait.

## License

[MIT](LICENSE)
