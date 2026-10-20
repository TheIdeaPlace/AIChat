"""What File, Chat Permissions does for each provider (#307, #329).

``ChatPermissionsDialog`` shows; these do the work. Claude's kept rules are in
The Chat Place's session store and given to each later turn as
``--allowedTools``. A Codex chat keeps none (#307); it shows its mode and how
many of Codex's own rule files are in effect, read on a worker.
"""
from __future__ import annotations

from .. import chat_permissions
from ..claude_cli import PERMISSION_MODES, normalize_permission_mode
from ..sessions import NEEDS_YOU, WORKING

#: Codex's choices in Change Permission Mode, and how they read.
CODEX_MODES = [(('read-only', 'on-request'), 'Read-only; asks before allowing changes'),
               (('workspace-write', 'on-request'),
                'Can edit files in this folder; asks before anything else')]


class ClaudePermissions:
    can_remove = True
    empty_text = chat_permissions.EMPTY

    def __init__(self, frame, info):
        self.frame = frame
        # The record, not its id: a new chat's id can change on its first
        # turn (Claude reports its own), and the dialog may be open then.
        self.record = frame.store.get(info.cli_session_id)

    def _own(self):
        record = self.record
        if record is None or self.frame.store.get(record.cli_session_id) is not record:
            return None
        return record

    def mode_text(self) -> str:
        own = self._own()
        mode = normalize_permission_mode(own.permission_mode) if own is not None else ""
        return dict(PERMISSION_MODES).get(mode, mode or "not recorded")

    def can_change_mode(self):
        # A Claude chat's mode counts from its next turn, so it can change
        # at any time, as File, Change Permission Mode already allows.
        return True, ""

    def change_mode(self, done):
        own = self._own()
        if own is not None:
            self.frame.on_change_permission_mode(own.to_info())
        done()

    def note(self) -> str:
        own = self._own()
        running = own is not None and own.cli_session_id in self.frame._runners
        return chat_permissions.CLAUDE_RUNNING if running else chat_permissions.CLAUDE_TAKES_EFFECT

    def load(self, done):
        own = self._own()
        if own is None:
            done(None, "This chat is no longer in The Chat Place.")
            return
        done(chat_permissions.claude_rows(own.allowed_tools, own.allowed_tools_at,
                                          own.allowed_dirs, own.allowed_dirs_at), None)

    def remove(self, rule_id, done):
        own = self._own()
        if own is None:
            done(None, "This chat is no longer in The Chat Place.")
            return
        folder = rule_id[len(chat_permissions.FOLDER):] if rule_id and rule_id.startswith(
            chat_permissions.FOLDER) else None
        if rule_id is not None and folder is None and rule_id not in own.allowed_tools \
                or folder is not None and folder not in own.allowed_dirs:
            done(None, "That rule is no longer kept.")
            return
        if rule_id is None:
            kept, folders = [], []
        elif folder is None:
            kept, folders = [r for r in own.allowed_tools if r != rule_id], list(own.allowed_dirs)
        else:
            kept, folders = list(own.allowed_tools), [d for d in own.allowed_dirs if d != folder]
        times = {r: t for r, t in own.allowed_tools_at.items() if r in kept}
        folder_times = {d: t for d, t in own.allowed_dirs_at.items() if d in folders}
        if not self.frame._store_write(self.frame.store.update, own.cli_session_id,
                                       allowed_tools=kept, allowed_tools_at=times,
                                       allowed_dirs=folders, allowed_dirs_at=folder_times):
            done(None, "It couldn't be saved, so the chat still keeps it.")
            return
        self.frame._feedback("Removed. Claude will ask again from the next turn."
                             if rule_id is not None else
                             "Removed all. Claude will ask again from the next turn.")
        done(chat_permissions.claude_rows(kept, times, folders, folder_times), None)


class CodexPermissions:
    #: Nothing to remove, so the dialog offers no Remove (#307).
    can_remove = False
    empty_text = chat_permissions.CODEX_EMPTY

    def __init__(self, frame, info, record):
        self.frame, self.info, self.record = frame, info, record

    def mode_text(self) -> str:
        options = self.record.provider_options
        current = (options.get('sandbox', 'read-only'), options.get('approval_policy', 'on-request'))
        return dict(CODEX_MODES).get(current, 'not recorded')

    def can_change_mode(self):
        state = self.frame._codex_states.get(self.info.key)
        working = state is not None and (state.state in (WORKING, NEEDS_YOU)
                                         or state.outcome == 'inProgress')
        if working or self.info.key in self.frame._codex_policy_pending:
            return False, ("Codex is working in this chat. The permission mode can change once "
                           "it's idle: finish or stop the turn first.")
        return True, ""

    def change_mode(self, done):
        self.frame._change_codex_permissions(self.info, after=done)

    def note(self) -> str:
        return chat_permissions.CODEX_ABOUT

    def load(self, done):
        # Nothing is kept for a Codex chat (#307).
        done([], None)

    def native(self, done):
        def counted(result, error):
            done(chat_permissions.native_rule_files(None if error else result,
                                                    (result or "Codex didn't say why.") if error else ""))
        self.frame._load_codex_rules(self.record.cwd, counted)
