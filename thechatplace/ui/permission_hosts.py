"""What File, Chat Permissions does for each provider (#307, #329).

``ChatPermissionsDialog`` shows; these do the work. Claude's kept rules are in
The Chat Place's session store and given to each later turn as
``--allowedTools``; Codex's are in its provider store, read and changed on a
worker through the session controller, never on the UI thread.
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
    def __init__(self, frame, info):
        self.frame, self.info = frame, info

    def _own(self):
        return self.frame.store.get(self.info.cli_session_id)

    def mode_text(self) -> str:
        own = self._own()
        mode = normalize_permission_mode(own.permission_mode) if own is not None else ""
        return dict(PERMISSION_MODES).get(mode, mode or "not recorded")

    def can_change_mode(self):
        # A Claude chat's mode counts from its next turn, so it can change
        # at any time, as File, Change Permission Mode already allows.
        return True, ""

    def change_mode(self, done):
        self.frame.on_change_permission_mode()
        done()

    def note(self) -> str:
        running = self.info.cli_session_id in self.frame._runners
        return chat_permissions.CLAUDE_RUNNING if running else chat_permissions.CLAUDE_TAKES_EFFECT

    def load(self, done):
        own = self._own()
        if own is None:
            done(None, "This chat is no longer in The Chat Place.")
            return
        done(chat_permissions.claude_rows(own.allowed_tools, own.allowed_tools_at), None)

    def remove(self, rule_id, done):
        own = self._own()
        if own is None:
            done(None, "This chat is no longer in The Chat Place.")
            return
        if rule_id is not None and rule_id not in own.allowed_tools:
            done(None, "That rule is no longer kept.")
            return
        kept = [] if rule_id is None else [r for r in own.allowed_tools if r != rule_id]
        times = {r: t for r, t in own.allowed_tools_at.items() if r in kept}
        if not self.frame._store_write(self.frame.store.update, own.cli_session_id,
                                       allowed_tools=kept, allowed_tools_at=times):
            done(None, "It couldn't be saved, so the chat still keeps it.")
            return
        self.frame._feedback("Removed. Claude will ask again from the next turn."
                             if rule_id is not None else
                             "Removed all. Claude will ask again from the next turn.")
        done(chat_permissions.claude_rows(kept, times), None)


class CodexPermissions:
    def __init__(self, frame, info, record):
        self.frame, self.info, self.record = frame, info, record

    def _work(self, action, done):
        self.frame._codex_work(action, done)

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
        return chat_permissions.CODEX_ABOUT + "\n\n" + chat_permissions.CODEX_TAKES_EFFECT

    def load(self, done):
        record = self.record
        self._work(lambda: self.frame.sessions.owned_action('chat_rules', record),
                   lambda rules, error: done(None, rules or "Codex isn't available.") if error
                   else done(chat_permissions.codex_rows(rules), None))

    def remove(self, rule_id, done):
        record = self.record

        def finished(rules, error):
            if error:
                done(None, rules or "The rule may still be kept.")
                return
            self.frame._feedback("Removed. Codex will ask again from its next request."
                                 if rule_id is not None else
                                 "Removed all. Codex will ask again from its next request.")
            done(chat_permissions.codex_rows(rules), None)
        self._work(lambda: self.frame.sessions.owned_action('remove_chat_rule', record, rule_id),
                   finished)

    def native(self, done):
        def counted(result, error):
            done(chat_permissions.native_rule_files(None if error else result,
                                                    (result or "Codex didn't say why.") if error else ""))
        self.frame._load_codex_rules(self.record.cwd, counted)
