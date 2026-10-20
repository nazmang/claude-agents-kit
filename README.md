# claude-agents-kit

Tools for running many long-lived [Claude Code](https://claude.com/claude-code)
sessions on one machine: an agent team in tmux, a few project tabs.

[Русская версия](README.ru.md)

Prompt-cache upkeep (compacting idle sessions before the cache expires) lives in
a separate repo, [claude-cache-keeper](https://github.com/nazmang/claude-cache-keeper).
The two work together but do not need each other.

## What is inside

| part | what it does | needs |
|---|---|---|
| `bin/agents` | `agents status` — every live session on the machine: status, idle, cache left, context, model, tmux window. `agents up/down` — start and stop a project's sessions in tmux. `agents messages` — messages between sessions. | Python 3, tmux |
| `plugins/push-guard/` — **push-guard mod** | Stops `git push` to a branch whose PR is already merged (the commits would never reach the base branch) and asks **you** in a dialog; only your “yes” lets it run, otherwise the agent is told to start a fresh branch. There is no override the model can type itself. No answer within `PUSH_GUARD_ASK_MIN` minutes (10) is a refusal, so an unattended agent never hangs on the question; a desktop notification tells you an agent is waiting. Unknown repos and missing `gh` pass through. | `gh` logged in |
| `plugins/agents-panel/` — **agents-panel mod** | A one-line band above the prompt: who is working right now, how many sessions are idle and how many of them have a cold cache (💤), the latest message between sessions, and how many checklist items wait on you. `/team` toggles the full pane: every live session as a pixel girl or boy (`people.conf`) in the model's colour (Haiku blue, Sonnet amber, Opus purple, Fable green), what each is doing, the recent messages; click a name (or press 1–9) to jump to its tmux window. `/checklist` toggles a read-only tab with open items by project, yours first. | `bin/agents` on `PATH` |

## Install

```bash
git clone https://github.com/nazmang/claude-agents-kit ~/src/claude-agents-kit
~/src/claude-agents-kit/install.sh
```

The installer cuts a release (`release.sh`) and links `agents` from it into
`~/.local/bin`. It does **not** touch `settings.json`; add the mods yourself:

```jsonc
// ~/.claude/settings.json
{
  // several plugins: colon-separated (a comma does not work)
  "env": { "CLAUDE_CODE_PLUGIN_DIRS": "/home/you/.local/share/claude-agents-kit/current/plugins/push-guard:/home/you/.local/share/claude-agents-kit/current/plugins/agents-panel" }
}
```

With claude-cache-keeper installed too, put all three mod paths into the same
variable.

> Set the variable in `settings.json`, not in the shell: exported in the
> environment it was not picked up in our tests (Claude Code 2.1.296).

> **Running sessions do not pick a mod up**, not even with `/reload-plugins`:
> the plugin dir list is read at process start. Restart them (after `/compact`!).

## Usage

```text
$ agents status
session                      status      idle   cache   context  model / window
Coordinator                  idle         34m     26m   compact  sonnet-5-5 / team:Coordinator
Reviewer                     idle        114m expired      191k  opus-5-5 / team:Reviewer
webapp-dev                   busy          0m     60m       77k  opus-5-5 / no tmux
```

- `context` = `compact` means nothing has been added since the last compaction.
- `agents compact <name> --wait` — compact one session now and wait until the
  transcript gets a new `compact_boundary`.
- `agents up|down <project> [names…]` — start/stop a project's sessions in tmux.
  - Plain sessions: list them in `~/.config/agents/sessions.conf`
    (`project | name | dir | model | session id`). `up` opens tmux session
    `<project>`, one window per line, with `claude -n <name> --resume <id>`; a
    line without an id starts a new session and records its id. Sessions that
    are already running — in tmux or in some terminal tab — are left alone.
    `down` compacts each session first, then sends `/exit`; busy sessions and
    drafts in the input box are skipped.
  - Teams with their own start/stop scripts: `~/.config/agents/projects.conf`
    (`project | dir | up command | down command`).
  - Moving a session from a terminal tab into tmux: `/compact`, `/exit` in the
    tab, then `agents up <project>`.
- `agents down <project> --no-compact` closes without compacting — for a
  session whose cache is already cold, where compaction would itself re-read
  the whole context at full price. `agents up <project> --fresh` ignores the
  stored id and starts a new session (e.g. a nightly clean restart of a
  watcher). An optional 6th column in `sessions.conf` adds extra `claude`
  arguments for that session.
- A session that only runs a background shell or monitor shows as `shell`; it
  counts as free, and `down` answers the "Background work is running" dialog
  itself.
- `agents messages [--hours H]` — messages between sessions (SendMessage);
  ❄ marks a message that woke a recipient with an expired cache.
- Log of compactions done by `agents`: `~/.local/state/agents/compact.log`.

### agents-panel data

The mod has no file system, so it asks `agents status --json --max-age 5`; the
answer is cached, so all sessions together recompute it at most every 5 s.

- `~/.config/agents/people.conf` — `session name | display name | f|m|-|x` (see `examples/`).
- Checklist: one JSON per item in `~/.local/state/agents/checklist/items/`
  (`{id, data: {project, title, who, status}}`) — e.g. a dump of a Claude
  artifact database. Open items (`status: todo`) whose `who` is in `OWNER_WHO`
  (default `owner,together,you` and their Russian twins) count as waiting on you.

### A dashboard window

Start one cheap session (any model; it never sends a request) with
`AGENTS_PANEL_OPEN=1` in the `env` of its own settings file
(`claude --settings ~/.config/agents/panel-settings.json`), and it opens `/team`
and `/checklist` by itself. Mark it `x` in `people.conf` so it does not show up
among the agents. Panes stand beside the prompt only when the terminal is wide
enough; otherwise they open above it. Pixel figures (`Raster`) are drawn by the
terminal UI only.

### Releases: never load mods from the tree you edit

Running sessions **hot-reload** a mod whenever its files change. If settings
point at your working tree, every half-finished save lands in every agent at
once. So:

- `./release.sh` refuses uncommitted and untracked files, runs
  `claude plugin validate` and `claude plugin test` for every plugin, copies
  the committed `bin/` and `plugins/` to
  `~/.local/share/claude-agents-kit/releases/<sha>` and switches `current`
  atomically. `--list`, `--rollback <sha>`.
- Settings and `~/.local/bin/agents` point at
  `~/.local/share/claude-agents-kit/current/...`, never at the repo.
- Develop in a separate worktree (`git worktree add ../claude-agents-kit-dev -b dev`),
  merge into `main`, then release.

### Tuning

| variable | default | where |
|---|---|---|
| `MIN_CTX` | 60000 | `agents down`: skip compacting smaller contexts |
| `OWNER_WHO` | `owner,together,you` + Russian twins | checklist: whose items wait on you |
| `PUSH_GUARD_ASK_MIN` | 10 | push-guard: minutes to wait for your answer |
| `AGENTS_PANEL_OPEN` | unset | agents-panel: `1` opens `/team` and `/checklist` at start |
| `AGENTS_LANG` | from locale | `ru` or `en` for everything a person reads. Unset: the first non-empty of `LC_ALL`, `LC_MESSAGES`, `LANG` - `ru…` means Russian, anything else English. `status --json` and the text the model gets are never translated |

## Safety

- `agents compact` and `down` type into a tmux pane only if the session status
  is `idle` (or `shell`) **and** the input box is empty (detected on screen
  after the `❯` prompt). Sessions outside tmux are never touched.
- push-guard only asks; it never pushes anything itself.

## Lessons learned

- Do not decide that `/compact` has finished by looking at the screen: an old
  `Compacted` line may still be visible. `agents compact --wait` checks the
  transcript for a new `compact_boundary`.
- Before restarting a session with `--resume`, compact it first — otherwise the
  whole uncompacted tail is written to the cache again.
- The last `usage` in a transcript overstates the context if a compaction came
  after it.
- Dev and release copies of `agents` share one `status.json` cache; test a mod
  after releasing it, not from the dev tree.

## Tests

```bash
claude --plugin-dir plugins/agents-panel   # once, interactively: generates .claude-plugin/types
for p in plugins/*; do claude plugin validate $p && claude plugin test $p; done
```

## Uninstall

```bash
rm ~/.local/bin/agents
```

Remove the two mod paths from `CLAUDE_CODE_PLUGIN_DIRS` in `settings.json`, then
restart the sessions. `~/.local/share/claude-agents-kit/`, `~/.config/agents/`
and `~/.local/state/agents/` can be deleted afterwards.

## License

MIT
