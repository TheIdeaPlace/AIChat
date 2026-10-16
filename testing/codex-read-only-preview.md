# Try the local Codex history preview

## In the app

Run `.venv/bin/python -m thechatplace` from the feature branch. On Kelly's Mac,
the explicit development CLI can be made available just for this launch:

```sh
env PATH="/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS:$PATH" .venv/bin/python -m thechatplace
```

This selects the already-used bundled development CLI explicitly; it is not a
supported released minimum or an automatic desktop installation fallback.
 Open Settings,
check **Include local Codex chats (restart required)**, save, close
The Chat Place and start it again. A native Codex executable must be on PATH
or in a supported native install location; keep Codex signed in to ChatGPT.

Claude and local Codex chats now appear in the same list. Codex rows say
**Codex chat** in the Kind column. View, Show Sessions offers Claude Chats and
Codex Chats filters; View, Session List Columns can add a Provider column. Select one and open it normally: messages,
formatted reading, Find and export use the Codex reader automatically. Existing Codex
chats remain read-only. New Session offers Codex as a Provider; Continue Here
copies a local Codex chat into a new owned chat. Owned chats support text
replies, Stop, queued messages and Answer (Ctrl+Shift+A). Claude-specific
actions remain unavailable for Codex.
Turn the setting off and restart to hide Codex without deleting its saved data.

Try a known local chat, archived history, title/hide/group preferences and
export. Check keyboard focus, provider names and reply/error announcements with
your screen reader. No Windows visual or hand-check result is claimed. A native
released minimum, npm shim resolution and cloud-only coverage remain unverified.
The optional Last message column reads Codex histories in small background batches
and caches short lines. Large catalogs fill progressively; activity changes
invalidate a cached line, and discovery outages retain known lines without
reading more histories.

## Command-line reader

This development preview on `codex/codex-integration-plan` lists the local
Codex app-server catalog and exports one chat through the same adapter and
managed backend being built for The Chat Place. No build is needed: use the
existing development virtual environment. The in-app opt-in is described above.

On Kelly's current Mac, from the repository folder:

```sh
.venv/bin/python tools/read_codex.py --codex "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
```

The JSON listing includes each chat's ID, title, archived flag and reported
state. Copy an ID and request its history:

```sh
.venv/bin/python tools/read_codex.py --codex "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex" --thread "CHAT_ID" --format txt
```

To read in a browser, change `--format txt` to `--format html` and redirect
stdout to a file you choose. Markdown uses `--format md`. If a native supported
Codex CLI is on PATH, omit `--codex`. Shell/npm shims are not accepted yet.
The explicit desktop-bundled CLI is a development probe, not an established
supported release minimum or a Windows compatibility claim.

Try listing active and archived chats, then read a chat with user and assistant
messages, code and tool output. Assistant messages should say Codex. Check the
export against what you remember from that chat; report missing content without
posting private conversation bodies publicly. No prompts are sent, no threads
are resumed/forked, and the managed process closes after each command.

Only top-level chats in the local app-server catalog are listed. Cloud-only
coverage is not established. Unknown status remains in Properties and selected
State columns, but is omitted from default rows and spoken message titles.
Long chats load every history page before displaying messages or exporting.
While loading, the message list says Loading messages. Try Find, export and the
Last message column on a long chat; a failed load must never appear complete.
Try a new Codex chat, a continued copy, text replies, a queued reply,
and Stop. Stopping or a failed turn returns queued text to that chat's draft.
A disconnected or uncertain send is never retried automatically: reload and
check history before sending again. Answer supports Allow Once, Deny, Stop
Turn and Answer Later. File approval requires matching readable changes;
session write grants are unavailable. Questions keep their own identities
and secret answers use masked controls. Model and policy overrides,
attachments, individual queue editing and Send Now are unavailable.
Windows validation is deferred in #226; it does not block this functional
feature-branch preview.
