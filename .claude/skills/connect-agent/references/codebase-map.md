# Codebase map for a new provider

Where each phase's work goes, with the Muse Code version to read first.
`<agent>` is the new provider's id (lowercase, as in `PROVIDER_LABELS`).

## Phase 1: detection and sign-in

| What | Where | Muse example |
|---|---|---|
| Find the executable, version check, child environment | `thechatplace/platform_paths.py` | `find_muse`, `MuseLookup`, `muse_child_environment`, `start_muse_server` |
| Account connection (own allowlist) | `thechatplace/<agent>_account.py` | `muse_account.py` |
| Sign-in states and flow | `thechatplace/<agent>_signin.py` | `muse_signin.py` |
| Tools, Sign In, *Agent* command | `thechatplace/commands.py` + `MainFrame._command_handlers` | `muse_sign_in` |
| Sign-in dialog | `thechatplace/ui/dialogs.py` | `MuseLoginDialog` |
| Tests | `tests/test_<agent>_account.py`, `_signin.py`, `_ui.py` | the Muse ones |

## Phase 2: read-only history

| What | Where | Muse example |
|---|---|---|
| History connection, bounds, version check | `thechatplace/providers/<agent>_connection.py` | `HistoryConnection`, `ManagedMuseConnection`, `VERIFIED_SCHEMAS` |
| Provider: collect, reader, capabilities | `thechatplace/providers/<agent>.py` | `MuseProvider`, `MuseReader`, `session_from_muse`, `transcript_from_muse` |
| Registration with the window | `thechatplace/ui/main_frame.py` (provider setup near the top) | the `muse_exe` block |
| Labels, view filter, home app | `thechatplace/sessions.py` | `PROVIDER_LABELS`, `HOME_APPS`, `VIEW_MUSE` |
| Agent names | `thechatplace/agents.py` | `provider_name` |
| Empty-view guidance | `thechatplace/ui/main_frame.py` | `_muse_empty_view_message` |
| Tests | `tests/test_<agent>_provider.py`, `_history_ui.py`, `_history_paths.py` | the Muse ones |

## Phase 3: chats of the app's own

| What | Where | Muse example |
|---|---|---|
| Execution connection, allowlist, events | `providers/<agent>_connection.py` | `ExecutionConnection`, `ManagedMuseExecution` |
| Owned lifecycle: create, prepare, send, poll, stop, answer | `providers/<agent>.py` | `MuseOwnedChats` (`create`, `prepare`, `_policy`, `_reconcile`, `send`, `poll`, `stop`, `answer`) |
| Saved recovery state, validated | `thechatplace/own_store.py` | `_valid_muse_options` |
| Companion store | `thechatplace/provider_storage.py` | provider-sessions-muse.json |
| Routing owned actions | `thechatplace/session_controller.py` | `owned_action` allowlist, `_merge_owned`, `owned_state` hook |
| Runtime list | `thechatplace/sessions.py` | `RUNTIME_PROVIDERS` |
| Window: send, stop, monitor, announce | `thechatplace/ui/codex_chats.py` | `_send_codex_owned` (Muse branch), `_stop_codex_owned`, `_poll_muse_owned`, `_apply_muse_owned_update` |
| Status line text | `ui/main_frame.py` | `_muse_state_text`, `_update_send_state` |
| Permission dialog, permissions host | `ui/dialogs.py`, `ui/permission_hosts.py` | `MuseApprovalDialog`, `MusePermissions` |
| User-facing error text | `thechatplace/codex_diagnostics.py` | `safe_reason` (Muse branch) |
| Announce once | `ui/main_frame.py` | `_runtime_announces` |
| Tests | `tests/test_<agent>_execution.py`, `_connection.py`, `_status.py` | the Muse ones (`Execution` fake host) |

## Phase 4: menu commands (Muse helpers to copy)

| Command | Provider | Window |
|---|---|---|
| Answer | `answer`, `approvals_from_muse` | `_answer_codex_owned` (Muse path) |
| Stop Running Turn | `stop` | `_stop_codex_owned` |
| Turn Status |: | `on_turn_status` -> `_muse_state_text` |
| Steer | `steer` | `_steer_muse_owned` |
| Change Model / Effort | `models`, `set_model`, `efforts`, `set_effort` | `_change_muse_model`, `_change_muse_effort` |
| Change Permission Mode | `modes`, `set_mode` | `_change_muse_mode`, `_confirm_muse_mode` |
| Insert Command or Skill | `skills`, `_input` | `_muse_skills` |
| Attach Files | `send` + `providers/muse_input.py` | `on_send` Muse branch |
| Ask Aside | `MuseAsideRunner` | `_muse_ask_aside`, `_hide_muse_aside` |
| Retry / Branch from Here | `retry`, `branch`, `_fork_copy` | `_retry_muse`, `_branch_muse` |
| Stop Background Task | `tasks`, `stop_task` | `_stop_muse_task`, `_can_stop_background` |
| Chat Options (refused) |: | `on_chat_options` Muse branch |
| Read-only refusal |: | `_require_capability` |

Provider methods reach the window as `owned_action("<name>", record, ...)`,
which calls `<name>_owned` on the provider; add each name to the
`owned_action` allowlist in `session_controller.py`.

## Every phase

| What | Where |
|---|---|
| Menu commands | `thechatplace/commands.py` (never straight into `_build_menu`) |
| Spoken key names | `ui_text.key_name(your_key(...))` |
| Probe surfaces for new dialogs | `tools/ui_probe.py` (`tests/test_ui_probe.py` enforces) |
| Orca speech steps | `tools/orca_speech.py` `STEPS` + `TRIGGERS` |
| Packaging (lazy imports) | `build_windows.cmd`, `build_macos.sh`, `build_linux.sh`, `release-thechatplace.yml`, plus `app.py` smoke test |
| Docs | `README.md`, `thechatplace/assets/user-guide.md` -> `tools/make_docs.py`, open `release-notes/`, `testing/<agent>-*.md`, `dev-notes/<agent>-*.md`, `DEVELOPING.md` file table |
