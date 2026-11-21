# claude-agents-kit

**Run a whole team of long-lived [Claude Code](https://claude.com/claude-code) sessions on one machine, and keep an eye on it.**

![License: MIT](https://img.shields.io/badge/license-MIT-blue)
![Claude Code mods](https://img.shields.io/badge/Claude%20Code-mods-D97757)
![Platform: Linux](https://img.shields.io/badge/platform-Linux-informational)

[Русская версия](README.ru.md)

You keep an agent team in tmux and a few project tabs open for days. This kit
gives you:

- **`agents`**: one table of every live session on the machine (busy or idle,
  cache left, context size, model, tmux window), plus `up` / `down` to start
  and stop a project's sessions in tmux without losing their cache.
- **agents-panel**: a one-line band above the prompt with who is working
  right now, and a `/team` pane where every session is a pixel figure in its
  model's colour.
- **push-guard**: stops `git push` to a branch whose PR is already merged and
  asks *you* before anything goes out.

Prompt-cache upkeep (compacting idle sessions before the cache expires) lives
in a sibling repo, [claude-cache-keeper](https://github.com/nazmang/claude-cache-keeper).
The two work well together but do not need each other.

## What is inside

| part | what it does | needs |
|---|---|---|
| `bin/agents` | `agents status`: every live session on the machine with status, idle time, cache left, context, model and tmux window. `agents up/down`: start and stop a project's sessions in tmux. `agents messages`: messages between sessions. | Python 3, tmux |
| `plugins/push-guard/` (**push-guard mod**) | Stops `git push` to a branch whose PR is already merged (the commits would never reach the base branch) and asks **you** in a dialog. Only your "yes" lets it run; otherwise the agent is told to start a fresh branch. There is no override the model can type itself. No answer within `PUSH_GUARD_ASK_MIN` minutes (10) counts as a refusal, so an unattended agent never hangs on the question; a desktop notification tells you an agent is waiting. Unknown repos and missing `gh` pass through. | `gh` logged in |
| `plugins/agents-panel/` (**agents-panel mod**) | A one-line band above the prompt: who is working right now, how many sessions are idle and how many of them have a cold cache (💤), the latest message between sessions, and how many checklist items wait on you. `/team` toggles the full pane: every live session as a pixel girl or boy (`people.conf`) in the model's colour (Haiku blue, Sonnet amber, Opus purple, Fable green), what each is doing, the recent messages; click a name (or press 1–9) to jump to its tmux window. `/checklist` toggles a read-only tab with open items by project, yours first. | `bin/agents` on `PATH` |
| `bin/agents-inbox`, `agents ask` | **Inbox**: agents queue questions for you; you answer from one web page (and ntfy pushes), and the answer is typed into the asking agent's window. See [Inbox](#inbox). | Python 3, tmux, optional ntfy |

## Quick start

```bash
git clone https://github.com/nazmang/claude-agents-kit ~/src/claude-agents-kit
~/src/claude-agents-kit/install.sh

# optional: example configs to edit
mkdir -p ~/.config/agents
cp -n ~/src/claude-agents-kit/examples/*.conf ~/.config/agents/
```

The installer cuts a release (`release.sh`) and links `agents` from it into
`~/.local/bin`. It does **not** touch `settings.json`; add the mods yourself
(replace `/home/you`):

```jsonc
// ~/.claude/settings.json
{
  // several plugins: colon-separated (a comma does not work)
  "env": { "CLAUDE_CODE_PLUGIN_DIRS": "/home/you/.local/share/claude-agents-kit/current/plugins/push-guard:/home/you/.local/share/claude-agents-kit/current/plugins/agents-panel" }
}
```

With claude-cache-keeper installed too, put all three mod paths into the same
variable.

> [!IMPORTANT]
> - **Running sessions do not pick a mod up**, not even with `/reload-plugins`:
>   the plugin dir list is read at process start. Restart them (after `/compact`!).
> - Set the variable in `settings.json`, not in the shell: exported in the
>   environment it was not picked up (Claude Code 2.1.296).

### Requirements

- Linux: `agents` reads `/proc`, `release.sh` uses GNU `mv -T`
- Python 3 and tmux
- Claude Code with mods support (tested on 2.1.296 and 2.1.318)
- `gh`, logged in, for push-guard
- `agents compact` and `down` find the input box by the `❯` prompt of the
  default Claude Code terminal UI

## Usage

```text
$ agents status
session                      status      idle   cache   context  model / window
Coordinator                  idle         34m     26m   compact  sonnet-5-5 / team:Coordinator
Reviewer                     idle        114m expired      191k  opus-5-5 / team:Reviewer
webapp-dev                   busy          0m     60m       77k  opus-5-5 / no tmux
```

- `context` = `compact` means nothing has been added since the last compaction.
- `agents compact <name> --wait`: compact one session now and wait until the
  transcript gets a new `compact_boundary`.
- `agents clear <name> [--timeout MIN]`: wait until the session is `idle` with
  an empty input box, then `/clear` it; done = the session id changed. A
  background command (`shell`) counts as busy. It cannot tell a finished task
  from a session waiting for a reply: run it only when the agent reported it is
  done.
- `agents up|down <project> [names…]`: start/stop a project's sessions in tmux.
  - Plain sessions: list them in `~/.config/agents/sessions.conf`
    (`project | name | dir | model | session id [| extra args]`). `up` opens
    tmux session `<project>`, one window per line, with
    `claude -n <name> --resume <id>`; a line without an id starts a new session
    and records its id. Sessions that are already running, in tmux or in some
    terminal tab, are left alone. `down` compacts each session first, then
    sends `/exit`; busy sessions and drafts in the input box are skipped.
  - Teams with their own start/stop scripts: `~/.config/agents/projects.conf`
    (`project | dir | up command | down command`).
  - Moving a session from a terminal tab into tmux: `/compact`, `/exit` in the
    tab, then `agents up <project>`.
  - `#` starts a comment at the start of a line or after a space, outside
    quotes: `--append-system-prompt "fix issue #5"` is kept as is.
- `agents down <project> --no-compact` closes without compacting: for a
  session whose cache is already cold, compaction would itself re-read the
  whole context at full price. `agents up <project> --fresh` ignores the
  stored id and starts a new session (e.g. a nightly clean restart of a
  watcher). The optional 6th column in `sessions.conf` adds extra `claude`
  arguments for that session.
- A session that only runs a background shell or monitor shows as `shell`; it
  counts as free, and `down` answers the "Background work is running" dialog
  itself.
- `agents messages [--hours H]`: messages between sessions (SendMessage);
  ❄ marks a message that woke a recipient with an expired cache.
- Log of compactions done by `agents`: `~/.local/state/agents/compact.log`.

### agents-panel data

The mod has no file system, so it asks `agents status --json --max-age 5`; the
answer is cached, so all sessions together recompute it at most every 5 s.

- `~/.config/agents/people.conf`: `session name | display name | f|m|-|x`
  (girl, boy, anyone, hidden; see `examples/`).
- Checklist: one JSON per item in `~/.local/state/agents/checklist/items/`
  (`{id, data: {project, title, who, status}}`), e.g. a dump of a Claude
  artifact database. Open items (`status: todo`) whose `who` is in `OWNER_WHO`
  (default `owner,together,you` and their Russian and Ukrainian twins) count as waiting on you.

### A dashboard window

Start one cheap session (any model; it never sends a request) with
`AGENTS_PANEL_OPEN=1` in the `env` of its own settings file
(`claude --settings ~/.config/agents/panel-settings.json`), and it opens `/team`
and `/checklist` by itself. Mark it `x` in `people.conf` so it does not show up
among the agents. Panes stand beside the prompt only when the terminal is wide
enough; otherwise they open above it. Pixel figures (`Raster`) are drawn by the
terminal UI only.

### Settings

| variable | default | what it does |
|---|---|---|
| `MIN_CTX` | `60000` | `agents down`: skip compacting smaller contexts |
| `OWNER_WHO` | `owner,together,you` + Russian and Ukrainian twins | checklist: whose items wait on you |
| `PUSH_GUARD_ASK_MIN` | `10` | push-guard: minutes to wait for your answer |
| `AGENTS_PANEL_OPEN` | unset | agents-panel: `1` opens `/team` and `/checklist` at start |
| `AGENTS_LANG` | from locale | `en`, `ru` or `uk` for everything a person reads |
| `DEST` | `~/.local/share/claude-agents-kit` | `release.sh`, `install.sh`: where releases go |
| `BIN` | `~/.local/bin` | `install.sh`: where `agents` is linked |
| `SKIP_PLUGIN_CHECKS` | unset | `release.sh`: `1` releases without `claude` on `PATH` (no validate/test) |

The interface speaks English, Russian and Ukrainian. `AGENTS_LANG` wins when it
names one of them; otherwise the language part of the first non-empty of
`LC_ALL`, `LC_MESSAGES`, `LANG` decides (`uk_UA.UTF-8` → `uk`, `ru_RU` → `ru`),
and anything else (`C`, `de_DE`…) means English. `status --json` and the text
the model gets are never translated.

Adding a language: the supported languages are simply the keys of each strings
table, so add one more entry with the same keys as `en` to `STRINGS` in
`bin/agents` (plus its `--help` text in `DOCS`), `bin/agents_inbox/cli.py`,
`deliver.py`, `server.py`, `plugins/agents-panel/hooks/register.jsx` and
`plugins/push-guard/hooks/register.js`. No code changes; a key you miss falls
back to English, and the tests check that every language has exactly the
English keys.

## Inbox

Agents that need you (a decision, a command to run, a brainstorm, missing
facts) do not have to wait for you to look at their window. They put the
question in a queue with `agents ask`; you open one page, see every open
question, and answer. The answer is typed into the asking agent's tmux window
as one line, **only when that session is idle and its input box is empty**. A
draft you are typing is never touched; the answer stays `pending` and is
retried every 30 s (after 15 min the page marks it `stuck`, with no push).
The line looks like `<owner_name> (<inbox_label>, <id>): <text>`; newlines
become ` ⏎ `, control characters are stripped, and an answer over 2000
characters goes to a file whose path is typed instead.

```bash
agents ask "Which cache: redis or in-process?" --options "redis,in-process"   # decision (default kind)
agents ask "Run the migration on staging" --kind command --command "make migrate"
agents ask "Ideas for the onboarding flow" --kind brainstorm --body "context..."
agents ask "Which branch is the release?" --kind info-needed --blocking
agents inbox                      # open questions (--all: closed ones too)
agents ask --withdraw q-1120-01         # you answered in the window yourself
```

`agents ask` prints the question id. It finds the session name from the tmux
window it runs in; from a sub-agent or a service without tmux pass `--from
NAME` (a name from `sessions.conf`); `--withdraw` needs it outside tmux too. `--blocking` marks a question that
stops the agent's work.

### Setup

Copy `examples/inbox.conf` to `~/.config/agents/inbox.conf` and edit it; every
key is commented there (`owner_name`, `inbox_label`, `listen`, `port`,
`base_url`, `ntfy_url`, `ntfy_topic`, `ntfy_token_file`, `presence_file`,
`away_marker`, `quiet_hours`, `data_dir`, `token_file`). Without a file the
server listens on `127.0.0.1:8787` with pushes off. The config path can also
be set with `agents-inbox --conf PATH` or the env `AGENTS_INBOX_CONF`; the
env `AGENTS_SESSIONS_CONF` points to another `sessions.conf`.

On first start the server writes a page token to `~/.config/agents/inbox.token`
(mode 600). Open `base_url/?t=<token>` once; the server sets a cookie and
redirects to `/`. The token stays in the browser history of that visit. To
rotate it, delete the token file and restart the service.

Run it as a systemd user unit:

```bash
mkdir -p ~/.config/systemd/user
cp examples/agents-inbox.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agents-inbox
```

`install.sh` links `agents-inbox` into `~/.local/bin` next to `agents`.

### Pushes

With `ntfy_topic` set, a push goes out only for a `--blocking` question or a
`decision`. Quiet hours (default 23:00-08:00): a blocking question is pushed
without sound, a decision waits. If `presence_file` holds `away_marker`
(default `не дома`) and is fresh (under 15 min), you count as away: only a
`--blocking` question is pushed (with sound), a decision waits until you are
home. With no file, an empty path or a stale file you count as home. The push
carries the asker's name, the kind and the question title, never the body, and
no page token. Its buttons carry one-time tokens that
answer that one question, once; a second tap is rejected.

### Inbox safety

- The server binds only the addresses in `listen` and refuses `0.0.0.0` and
  `::`. Put your LAN or VPN address there, not a wildcard.
- Every POST except `/api/created` (localhost only; it is the nudge `agents
  ask` sends) needs the cookie or a one-time token. The page needs the cookie.
  A query string is never logged.
- Delivery follows the same rule as `agents compact`: idle session, empty
  input box, otherwise nothing is typed.

## Safety

- `agents compact` and `down` type into a tmux pane only if the session status
  is `idle` (or `shell`) **and** the input box is empty (detected on screen
  after the `❯` prompt). Sessions outside tmux are never touched.
- `down` reports a session as closed only once its process has exited; one
  still running 30 s after `/exit` is reported and the exit code is 1.
- push-guard only asks; it never pushes anything itself.

## Development

### Releases: never load mods from the tree you edit

Running sessions **hot-reload** a mod whenever its files change. If settings
point at your working tree, every half-finished save lands in every agent at
once. So:

- `./release.sh` refuses uncommitted and untracked files, runs
  `claude plugin validate` and `claude plugin test` for every plugin (and
  refuses to run without `claude`), copies the committed `bin/` and `plugins/`
  to `~/.local/share/claude-agents-kit/releases/<sha>` and switches `current`
  atomically. Releasing the same commit again only switches back to it. The
  last 5 releases are kept.
- `./release.sh --list` shows releases, `./release.sh --rollback <sha>` goes back.
- Settings and `~/.local/bin/agents` point at
  `~/.local/share/claude-agents-kit/current/...`, never at the repo.
- Develop in a separate worktree (`git worktree add ../claude-agents-kit-dev -b dev`),
  merge into `main`, then release.

### Tests

```bash
claude --plugin-dir plugins/agents-panel   # once, interactively: generates .claude-plugin/types
for p in plugins/*; do claude plugin validate $p && claude plugin test $p; done
```

Inbox (stdlib only, no real tmux or network touched):

```bash
python3 -m unittest discover -s tests/inbox -v
```

### Lessons learned

- Do not decide that `/compact` has finished by looking at the screen: an old
  `Compacted` line may still be visible. `agents compact --wait` checks the
  transcript for a new `compact_boundary`.
- Before restarting a session with `--resume`, compact it first, otherwise the
  whole uncompacted tail is written to the cache again.
- The last `usage` in a transcript overstates the context if a compaction came
  after it.
- Dev and release copies of `agents` share one `status.json` cache; test a mod
  after releasing it, not from the dev tree.

## Uninstall

```bash
rm ~/.local/bin/agents
```

Remove the two mod paths from `CLAUDE_CODE_PLUGIN_DIRS` in `settings.json`, then
restart the sessions. `~/.local/share/claude-agents-kit/`, `~/.config/agents/`
and `~/.local/state/agents/` can be deleted afterwards.

<details>
<summary>Upgrading from claude-cache-keeper (before October 2026)</summary>

`agents`, agents-panel and push-guard used to ship inside
[claude-cache-keeper](https://github.com/nazmang/claude-cache-keeper); this repo
carries their history. If you installed them from there, your settings and
`~/.local/bin/agents` still point into `~/.local/share/claude-cache-keeper/`:

1. Run `install.sh` from this repo: it relinks `~/.local/bin/agents`.
2. In `CLAUDE_CODE_PLUGIN_DIRS`, replace the `push-guard` and `agents-panel`
   paths with the ones under `~/.local/share/claude-agents-kit/current/plugins/`.
3. Update claude-cache-keeper too: its new releases no longer contain these mods.
4. Restart the sessions (after `/compact`).

`~/.config/agents/` and `~/.local/state/agents/` stay as they are.

</details>

## License

[MIT](LICENSE)
