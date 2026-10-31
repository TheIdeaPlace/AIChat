# Restore history reader (#436)

`BackupReader(archive, manifest, cancelled=None)` owns a temporary extracted copy. The UI
passes an open ZipFile-compatible handle, with its password already set, and a successful
`backup.verify` manifest. Extraction rechecks every size and SHA-256; absolute paths,
traversal, links, duplicates, case collisions and unlisted members are rejected.

Use it as a context manager. `sessions()` returns frozen `ImportedSession` records;
`read(session)` returns an existing `Transcript`. `identity` is `(provider, native_id,
computer_id)` and `key` begins `imported:`. `source_key` is the old presentation key
for remapping groups, names and hidden status; it is never an execution credential.
`data_dir` exposes only extracted TCP metadata. Persist selected histories and metadata
in TCP's own import store before closing. Closing stops app-server and deletes scratch;
it never closes the caller's archive handle. `notes` reports partial provider failures.

Claude uses `TranscriptReader` and `SessionFactsCache`, including copies kept by #438.
Copilot uses its existing file reader. Neither starts a provider process.

Codex uses a native app-server in a fresh scratch profile. It copies only sessions,
archived_sessions, session_index and known state/thread_history databases. It never
loads archived configuration, credentials, skills, plugins, queued inputs or goals.
The environment drops credentials and endpoint overrides and replaces HOME, USERPROFILE,
APPDATA, XDG paths, CODEX_HOME, CODEX_SQLITE_HOME and temporary directories. The process
working directory is scratch. The history connection permits only list/read/page RPCs.
No resume, start, inference or approval is sent.

CODEX_HOME alone is insufficient: a copied state database can hold absolute
`threads.rollout_path` references. The reader remaps those metadata paths to files
inside scratch, removes unmapped rows, and replaces indexed working directories with
a scratch workspace. Original working directories remain display metadata. It removes
index triggers from this disposable copy before relocation. Unknown index schemas fail
closed for Codex, while other providers remain importable. This is an isolated profile,
not an OS security sandbox for arbitrary hostile native databases.

Legacy history uses bounded `thread/turns/list` pages with `itemsView=full`; the native
server rejects item pagination for that store. Paginated history uses the existing
bounded turn/item reader. TCP never parses Codex rollouts.

Validation: synthetic tests cover identity, ordinary/encrypted handles, integrity,
unsafe names, retained Claude history, native-unavailable behavior, path relocation,
no execution stores, environment isolation, legacy/paginated RPCs and cleanup. A native
0.163.0 experiment read an invented question and answer from the extracted copy after
its original synthetic profile was renamed out of reach. The unavailable source's files
and hashes were unchanged. This proves legacy recovery for that version; paginated
RPC handling is covered by protocol fixtures, not a native paginated-profile recovery.
