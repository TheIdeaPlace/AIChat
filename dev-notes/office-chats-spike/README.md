# Read-only Claude add-in chats: spike and handoff (#541)

This is an experiment kept for whoever builds #541, not product code. Nothing in `thechatplace/`
imports it. The branch `experiment/office-chats` isn't meant to be merged as it stands. Port the
parts that hold up, with the app's own tests and conventions.

## What it shows

The Claude add-ins for Office keep chats only on the device, in browser storage. A reader using just
the standard library decoded all 25 real Excel chats (1,372 messages) across 3 stores on one Windows
PC, with no bad values. LevelDB's files other than `LOCK` and `LOG` had the same bytes and modified
times before and after reading, with Excel running. That was one check on one PC, not a proof for
every timing of a read against a write.

Anthropic documents where the data lives in
[Data storage and retention](https://claude.com/docs/office-agents/data-storage). It doesn't
document the record layout below, which came from looking at real stores and can change.

## Files

| File | What it is |
|---|---|
| `idbread.py` | Reads LevelDB logs and tables, Snappy, IndexedDB keys, Blink/V8 values and blob files. Keeps one named database and drops the rest. |
| `officechats.py` | Turns records into `Chat` and `Message`, merges stores, makes Markdown. `check` prints counts only; `export FOLDER` writes one file per chat. |
| `viewer.pyw` | A small read-only wx window: chats, messages, text, find across chats, copy, save. |
| `fake_store.py` | Writes made-up stores in the real on-disk format. `build_demo` makes one for trying the viewer. |
| `test_officechats.py` | Windowless tests on made-up stores. `python -m pytest dev-notes/office-chats-spike` |
| `probe.py`, `probe2.py` | Print field names, types and counts from real stores, never chat text. Use them to see whether the layout has changed. |

To try the viewer: `pythonw dev-notes/office-chats-spike/viewer.pyw`, with a Python that has wx. Add
`--store PATH` to read a particular `.leveldb` folder. An agent runs test windows in the test VM, not
on Kelly's PC, and uses `fake_store.py` there for a made-up store.

## What's on disk

Windows: `%LOCALAPPDATA%\Microsoft\Office\16.0\Wef\webview2\<account>\...\EBWebView\Default\IndexedDB\`,
in `https_pivot.claude.ai_0.indexeddb.leveldb` with large values in the matching `.blob` folder.
There can be several stores, one set per Office account, and Microsoft 365 versions 2608 and 2609
can move the add-in to a new empty folder. Search for the folder name rather than building the path.

Database `claude-chat-history`, object store `chats`:

- Chat: `id`, `title`, `surface`, `createdAt`, `updatedAt` (milliseconds), `userId`, `orgId`, `fileId`,
  `snapshot`.
- `snapshot.ui.messages`: the conversation as displayed. `type` is `user`, `assistant`, `thinking` or
  `tool`, with `timestamp`. Text is in `content`. Tools have `name`, `status`, `input`, `result`.
  `error` and `images` can appear.
- `snapshot.api.transcript`: what went to the model, in API shape.
- `snapshot.agent.snipArchive`: raw JSON parts moved out of the working context. Many chats share the
  same parts. The spike neither shows nor searches these, and whether they hold anything a person
  would miss hasn't been checked.

Only `surface: "sheet"` (Excel) has been seen. The other names in `officechats.APPS` are guesses.

## Rules to keep when porting

- **Never write to the store or open it with a LevelDB library.** Opening can write, and Office may
  have it open.
- **Keep only `claude-chat-history`.** The same LevelDB holds connector access and refresh tokens in
  `claude-mcp-gateways`, and Local Storage nearby holds sign-in tokens. Filter on the key before
  keeping a value, and never copy a whole store to a temp folder.
- **Skip what isn't understood and count it**, as `transcript.py` does.
- Keep chat text out of logs and bug reports. Transcripts include spreadsheet content and file paths.
- Tests use made-up stores only (`fake_store.py`).

## How it fits the app

On main at the time of writing:

- A new source implements `SessionProvider.collect()` and `ConversationProvider.open_reader()` from
  `providers/base.py`, returning a `ConversationReader`.
- `SessionCapabilities` and `session_controller.py` are where sending and forking get turned off.
- Provider Claude, with Kind showing Excel and so on. The IDs must never reach the Claude runners or
  transcript paths.

Least known: mapping these messages onto `transcript.py`'s `Transcript`, `ChatMessage` and
`ToolCommand`. That hasn't been tried.

Needed beyond the reader: running on a background thread (the viewer loads before its window opens),
skipping stores whose files haven't changed instead of rereading them on the list timer, and the
usual README, release notes, visual probe and keyboard walk.

## Decisions for Kelly

- Show chats from folders the add-in no longer uses, and from other Office accounts? The spike shows
  them, marked "older store", which only means the store wasn't the most recently written.
- Keep a copy past the add-in's limit of 50 chats per Office app? It saves chats from deletion but
  makes a second private copy of spreadsheet content.
- Mac stores differ (SQLite, per Office app, inside each app's container), so that needs its own
  reader. Windows could ship first.

## Known gaps

- The viewer has been walked by keyboard in the VM with made-up chats. Its look was checked from a
  screenshot of that window. When this was written, nobody had reported back on hearing it with JAWS
  or NVDA, or on opening real chats in the window.
- No independent code review. It was written to be thrown away.
- A read during a write, and an obsolete table file left behind by a compaction, aren't handled beyond
  skipping what can't be read and preferring the highest sequence number.
- Tables are read in full, and the reader ignores `MANIFEST`.
