# GitHub Copilot CLI as a provider: first probe

Written for #352, while checking whether The Chat Place can read and run GitHub Copilot CLI
sessions the way it does Claude and Codex ones (#184 built the provider layer this would use).

Probed on Windows 11 (arm64) with Copilot CLI 1.0.102, installed by `winget install GitHub.Copilot`
(it updated itself to 1.0.103 on first run), signed in with `copilot login` on a Copilot Free
account. Every prompt was a throwaway test in a scratch folder outside the repo. No conversation
content is copied here beyond those test prompts.

## Install and sign-in

- winget puts `copilot.exe` in `%LOCALAPPDATA%\Microsoft\WinGet\Packages\GitHub.Copilot_…\` and adds
  that folder to the user PATH. No Node is needed. Finding it would belong in `platform_paths.py`,
  next to finding `claude.exe`; npm installs (`copilot.cmd`) should be refused for the same reason
  as `claude.cmd`.
- `copilot login` uses a browser flow and stores the token in the Windows credential store. The
  CLI also accepts `COPILOT_GITHUB_TOKEN`, `GH_TOKEN` and `GITHUB_TOKEN` from the environment, so a
  runner should decide deliberately which of those it passes through.
- A brand-new account with no Copilot plan fails every prompt with "Access denied by policy
  settings" (exit 1, message on stderr). Turning on Copilot Free fixed it. The app should show
  that message plainly rather than as a crash.
- Free accounts get a policy warning that third-party MCP servers are disabled. It arrives as a
  `session.warning` event and doesn't stop the turn.

## Headless turns: `copilot -p … --output-format json`

JSONL on stdout, one event per line: `{"type", "data", "id", "parentId", "timestamp"}`, plus
`"ephemeral": true` on events that aren't saved. One turn produced, in order:

`session.warning` ×2, `session.mcp_*`, `session.auto_mode_resolved` (model picked by "auto"),
`user.message`, `assistant.turn_start`, `model.call_start`, `assistant.message_start`,
`assistant.message_delta` (streamed text, `deltaContent`), `model.call_finished`,
`assistant.message` (full text in `content`, plus `toolRequests`), `assistant.turn_end`,
`session.usage_checkpoint` (`totalPremiumRequests`), `assistant.idle`, `result`.

Billing shows in every turn (`totalPremiumRequests`, `totalNanoAiu`, and `isByok` on
`model.call_final_result`), so a guard like the Claude one ("abort if billed off the
subscription") can check `isByok` is false.

## Saved sessions: `~/.copilot/session-state/<uuid>/`

- `workspace.yaml`: `id`, `cwd`, `name` (the title; it's the first prompt until renamed),
  `user_named`, `created_at`, `updated_at`. Plain data, cheap to read for the session list.
- `events.jsonl`: the persistent events from the stream above (the ephemeral ones are left out),
  plus `session.start` (with `copilotVersion`), `system.message`, `session.usage_record` and
  `session.shutdown`. Same envelope as the live stream, so one parser can serve both. A session
  that failed before its first prompt has no `events.jsonl` at all.
- Also `checkpoints/`, `files/`, `research/`, `rewind-file-snapshots/`, and lock files in
  `session-state/.session-operation-locks/`. `~/.copilot/session-store.db` is a SQLite index
  (WAL mode) and isn't needed for reading.
- `copilot sessions` only offers `import`. There's no listing command, so discovery means reading
  these folders, read-only, as the app does for Claude.

## ACP mode: `copilot --acp`

NDJSON JSON-RPC over stdio (Agent Client Protocol). `initialize` reports `loadSession: true` and
`sessionCapabilities` with `list` and `close`, so resuming and listing sessions over ACP should
work too (not yet tested). `session/new` returns the available models and a `mode` option.

**Permissions reach the client.** Asked to create a file, Copilot sent a `tool_call` update (kind
`edit`, status `pending`), then a `session/request_permission` request with the file path, the
full diff and the options `allow_once`, `allow_always` and `reject_once`. Answering
`reject_once` produced a `tool_call_update` with status `failed` ("The user rejected this tool
call."), the turn ended with `stopReason: end_turn`, and no file was written. So the problem
reported in copilot-cli#845 (ACP approving tools without asking) doesn't happen in 1.0.103 for
file edits.

**Shell commands ask too** (checked later): asked to run `echo shellprobe > probe_shell.txt`,
Copilot sent `session/request_permission` with `toolCall.kind` `execute`, the title "Run the
requested shell command" and `rawInput.command` / `rawInput.commands`. `reject_once` left the
file unwritten. `command` is the text that runs; `commands` lists each command Copilot found in
it, as objects (`{"identifier": "echo shellprobe > probe_shell.txt", "readOnly": false}`). The
approval dialog shows `command`, plus any listed one it doesn't already contain.

**Reopening:** `session/load` of an earlier session id returns its `configOptions` and replays the
history first as `user_message_chunk`, `agent_message_chunk`, `tool_call` and `tool_call_update`
updates. It sends no prompt and uses no premium request.

**Stopping:** a `session/cancel` notification mid-reply ended the `session/prompt` request at once
with `stopReason: cancelled`.

**Settings that would stop it asking:** `configOptions` holds `mode` (`…/session-modes#agent`,
`#plan` or `#autopilot`) and `allow_all` (`on` / `off`), and `config_option_update` reports
changes. Copilot also advertises slash commands (`available_commands_update`), including
`allow-all`, which a prompt starting with `/` would run. The environment can do the same:
`COPILOT_ALLOW_ALL`, and `COPILOT_PROVIDER_*` (bring your own key) or `COPILOT_OFFLINE` send
prompts to another service. Each model in `session/new` carries `_meta.copilotUsage` ("1x"),
and the session's own `events.jsonl` gets a `session.usage_record` naming `provider.kind`
(`copilot`) for every turn, ACP or not.

**Copilot's own permission settings don't reach ACP** (checked for #364's review, each setting
restored afterwards). With `copilot config defaultPermissionMode allow-all`, `… assisted` (an AI
reviewer approving what it judges safe) or `defaultMode autopilot`, a new ACP session still
reported agent mode with `allow_all` off. Under `assisted`, a shell command still came as
`session/request_permission`, and refusing it left the file unwritten. A project's
`.github/copilot/settings.json` with `defaultPermissionMode: allow-all` and
`defaultMode: autopilot` was ignored the same way: agent mode, `allow_all` off, the shell
command asked, nothing ran. (Copilot's help says permission modes are ignored outside
interactive runs; ACP counts as outside.) Not tried: `hooks`, which run the user's own scripts.

**The client's name is recorded.** Sending `clientInfo: {name: "thechatplace"}` in `initialize`
makes Copilot write `client_name: thechatplace` in the session's `workspace.yaml`. Without it,
any ACP client's sessions say `github/acp`, and a terminal session says `github/cli`.

**A real file-edit request** (1.0.103) has `toolCall.kind` `edit`, `locations`, and
`rawInput.fileName` and `rawInput.diff`; `toolCall.content` is null. ACP's standard place for a
diff, `toolCall.content` items of type `diff`, is read too, in case that changes.

Streamed text arrives as `agent_message_chunk` updates. The title arrives as
`session_info_update`, and context use as `usage_update`.

## What this suggests

1. **Reading:** a `providers/copilot.py` discovery and history adapter over `session-state/`,
   in the shape of the Codex one, off by default behind a Settings preview.
2. **Running:** drive `copilot --acp` (like Codex's app-server) so permission requests become the
   app's own permission dialog. `-p` with `--allow-tool` lists is the simpler fallback, but it
   can't ask before each change.
3. **Checked since:** shell commands ask, `session/load` reopens, `session/cancel` stops (above).
   Still unseen: how ACP reports the "access denied" policy error.

## Reading slice (built)

`thechatplace/providers/copilot.py` reads `session-state/` directly, the way the Claude adapter
reads transcripts. Unlike Codex there's no server to ask, and ACP's `session/list` would mean
starting `copilot` on every refresh. It's off by default (Settings, `copilot_enabled`), and its
capabilities grant nothing but reading.

- **Listing:** one row per folder with an `events.jsonl` and at least one prompt. Folders that
  stopped before a prompt (a refused sign-in, say) are left out. The title is `workspace.yaml`'s
  `name`, or the first prompt when that can't be read. Only flat one-line scalars are parsed;
  nothing is guessed.
- **State:** from the last 256 KB of the log. One reply is several model steps, each with its
  own `assistant.turn_start` / `assistant.turn_end` (turnId 0, 1, 2…): a step that asks for
  tools, then the next, until one answers without `toolRequests`. So `user.message` or
  `turn_start` means working, and an outstanding `permission.requested` means needs you. Idle
  means a `turn_end` after an `assistant.message` with no `toolRequests`, or `session.shutdown`
  or `abort`. A `session.error` ends a reply only after `ERROR_SETTLE_SECONDS` (30) with nothing
  after it; a later `turn_start`, `assistant.message`, tool event or `permission.requested` means Copilot
  carried on. No real log
  has shown a `session.error` yet, so a Copilot that recovers more than 30 seconds after an error
  would be announced as finished twice: worth checking when one is seen. The reply's real end, `assistant.idle`, is ephemeral and never
  saved. Only an explicit end announces a finished reply: the Codex rule, extended to
  `sessions.LISTED_PROVIDERS` in `hub.finished_turns` and `Snapshot.fresh_states`. A reply with
  no writes for ten minutes stays working, with a detail saying so, so its finish is still
  announced; after two hours (`ABANDONED_TURN_SECONDS`) it's status unavailable. Needs you
  lasts a day (`ABANDONED_QUESTION_SECONDS`), since a Copilot closed while asking never writes
  its end either. Each session's files are re-read only when their size or time changes, and the
  state, title and last message come from one read.
- **Messages:** `user.message` (its `content`, not `transformedContent`), `assistant.message`,
  `tool.execution_start` and `tool.execution_complete` (as activity), a refused
  `permission.completed` (Permission denied, naming what was asked), `session.error` and `abort`.
  Everything else is skipped; bad lines are counted. The reader is incremental and waits for a
  whole line.
- **Confirmed on a real two-turn session** (a `-p` run, then `--continue`): a successful
  `tool.execution_complete` has `result.content` (the command's output, or "Added 1 file(s): …"),
  plus `result.detailedContent` (a diff for file tools), `success`, and `fileEdits` or
  `shellExecution` depending on the tool. `--continue` appends to the same `events.jsonl` after a
  `session.resume` event, so one reader covers every turn. The model can change between turns
  ("auto" picked two different ones). apply_patch arguments are summarised by the files they
  touch. Still unseen: what `workspace.yaml` does with a renamed session or a very long name.
- **Later:** `fileEdits` could feed Changed Files (Ctrl+Shift+D), as Claude's edits do.

## Execution slice: the ACP runtime (built, no window yet)

Three modules, none of which the window uses yet, so nothing here can send on its own:

- `platform_paths`: `find_copilot()` takes only a native executable (winget, Homebrew, GitHub's
  install script), never npm's `copilot.cmd` or node script, for the reason `claude.cmd` is
  refused. `copilot_child_environment()` drops `COPILOT_ALLOW_ALL`, the tokens (`GH_TOKEN`,
  `GITHUB_TOKEN`, `COPILOT_GITHUB_TOKEN`: another account), `COPILOT_PROVIDER_*` and
  `COPILOT_OFFLINE` (another service), and `COPILOT_MODEL` / `COPILOT_AUTO_TIER`.
  `start_copilot_acp()` runs `copilot --acp --disable-client-telemetry` without a shell, in
  Codex's kill-on-close process wrapper.
- `providers/copilot_acp.py` moves JSON-RPC messages. It offers Copilot no file or terminal
  access through the client, refuses every server request except permission requests, refuses
  lines over 32 MB, never shows Copilot's own error text, and checks `initialize` (protocol 1,
  `loadSession`, agent "Copilot").
- `providers/copilot_runtime.py` runs chats: create, prepare (`session/load`, replayed history
  ignored), send or queue (20 messages / 128k characters, as Codex), stop, answer, and state
  snapshots shaped like `CodexChatState`. Its rules, each tested against
  `tests/fake_copilot_acp.py`:
  - nothing is sent unless the chat is in agent mode with `allow_all` off, checked on open and
    on every `config_option_update` or `current_mode_update`; a change mid-turn cancels the turn;
  - a message starting with `/` is refused (slash commands include `/allow-all`);
  - permission requests offer Allow Once and Deny only, never `allow_always` (Codex's #284 rule);
    requests with no deny option, or for a session or turn not running and watched here, are
    answered "cancelled" (checked and stored under one lock, so a Stop can't leave one waiting);
  - every `session.usage_record` a turn appends to the session log must name `provider.kind`
    `copilot`; another provider stops every chat until restart (Claude's API-key abort, in
    Copilot's terms), and a completed turn with no record stops its own chat until reopened;
  - only chats this runtime created, or that the window adopts from its own record of owned
    chats, can be reopened, and only if Copilot recorded `client_name: thechatplace` (the name
    sent in `clientInfo`); other ACP clients' chats say `github/acp` or their own name, and a
    terminal chat says `github/cli`;
  - Copilot works only in a project folder (`platform_paths.is_specific_folder`, as #334 does
    for Codex), never a whole drive, a share's root or `/`;
  - a `/` hidden behind spaces, a byte-order mark or zero-width characters is still refused;
  - writes go through a writer thread with a bounded queue (256), a 1 MB message cap and at
    most 32 waiting requests (as `codex_rpc`), so a Copilot that stops reading can't block the
    reader or the window; a request that times out gives up alone;
  - when Copilot exits, every chat on it is told and stops being ready; a Stop that Copilot
    ignores for 15 seconds closes its process, ending the turn;
  - at most 8 permission requests wait per chat; more are cancelled;
  - Stop answers waiting requests "cancelled", sends `session/cancel` and never resends; queued
    messages come back unsent, including after an automatic stop.

Checked against the real Copilot CLI 1.0.103 too: a chat opened in agent mode with allow_all off,
streamed its reply, and passed the billing check; reopened in a new Copilot process, it answered a
second turn; a terminal chat was refused.

`tools/probe_copilot.py` runs one chat through this runtime in plain text, asking "allow once or
deny" at the keyboard; `testing/copilot.md` is the hand-testing guide for each Copilot PR.

### Hooks and terminals (checked 2026-10-25, for Kelly's review of #364)

**A hook can approve for the person.** In a scratch folder never marked trusted, a
`.github/hooks/allow.json` whose `preToolUse` hook printed `{"permissionDecision":"allow"}` let
Copilot run a shell command over ACP with no `session/request_permission` at all. Setting
`disableAllHooks: true` in `~/.copilot/settings.json` (the file `copilot config` writes) didn't
stop it: ACP ignores that setting, as it ignores `defaultPermissionMode`. A hook that runs leaves
`hook.start` (with `hookType`) and `hook.end` in the session's log. Copilot rejects a hook file
that isn't strict JSON, and logs why in `~/.copilot/logs`.

So `providers/copilot_hooks.py` looks wherever the
[hooks reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-hooks-reference)
says hooks load from: policy (`%ProgramData%\GitHub\Copilot\policy.d`, `/etc/github-copilot/policy.d`,
`HKLM\Software\Policies\GitHub\Copilot`), the user's `hooks/*.json` and `settings.json`, the
repository's `.github/hooks/*.json`, `.github/copilot/settings*.json` and `.claude/settings*.json`
(from the working folder up to the first folder with a `.git`, or only the working folder outside
a repository, both as given and with links resolved), and installed plugins (`hooks.json`, JSON
in a `hooks` folder, `plugin.json` manifests and any hooks file they name; `node_modules` is
skipped). In the person's own places a `preToolUse` or `permissionRequest` entry counts, whatever
its matcher. In the project any hook counts: Copilot runs a project's hooks over ACP without the
folder being trusted, so a cloned repository's `sessionStart` command would run unasked too. A
file that can't be read in full, or a search cut short, refuses as well. Nothing is opened or sent
while one is found, and a turn whose log shows an approving hook ran stops that chat until the app
restarts (the search missed it, so reopening can't prove it gone). Not covered: a custom agent's
own hooks, which apply only while that agent is chosen (this app never chooses one).

**A terminal can use this app's chat.** `copilot --resume <id> -p …` in a terminal left
`client_name: thechatplace` in `workspace.yaml`, so ownership alone can't tell. The log shows it:
a `session.resume` with `alreadyInUse: true` (this app's Copilot had the chat open) and another
`user.message` (a sub-agent's messages name it in `parentAgentTaskId`, so they don't count). Copilot writes `session.start` / `session.resume` lazily, when the first prompt
after opening arrives. Copilot also won't `session/load` a chat its process already has open
("Copilot couldn't do that"); `session/close` (advertised in `sessionCapabilities`) releases it and
writes `session.shutdown`. So the runtime remembers the log's size after each turn and after
opening; anything another Copilot added since means the chat must be opened again, which closes it
in this app's Copilot and loads it afresh. Checked end to end: refused after the terminal reply,
reopened, and answered again.

## Next, in rough order

Ideas from the first listening test, kept here so they aren't lost:

1. **Changed Files for Copilot chats** (Ctrl+Shift+D), from `tool.execution_complete`'s
   `fileEdits` and `result.detailedContent` diffs, with Open File and Open Folder actions.
2. **Owned Copilot chats:** start and continue a Copilot chat from The Chat Place over
   `copilot --acp`, with `session/request_permission` shown as the app's permission dialog and
   a billing guard (`isByok` false, premium requests reported). First check the items under
   "Still to check" above.
3. **Upstream:** an issue proposing Copilot support, then this reading slice as its first PR,
   following the slice-by-slice pattern used for Codex (#184).
