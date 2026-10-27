"""What File, Chat Permissions does for each provider (#307, #329).

``ChatPermissionsDialog`` shows; these do the work. Claude's kept rules are in
The Chat Place's session store and given to each later turn as
``--allowedTools``. Codex consent records stay in TCP and authorize one-time
answers, separately from Codex's native rule files (#307).
"""
from __future__ import annotations

from .. import chat_permissions, claude_settings, codex_saved_rules
from ..claude_cli import PERMISSION_MODES, normalize_permission_mode
from ..sessions import NEEDS_YOU, WORKING

#: Codex's sandbox choices, also used by agent-conversation templates.
CODEX_MODES = [(('read-only', 'on-request'), 'Read-only; asks before allowing changes'),
               (('workspace-write', 'on-request'),
                'Can edit files in this folder; asks before anything else')]

# Human review comes first so agent-conversation templates choose it for
# either sandbox; automatic review is an explicit choice (#358).
CODEX_REVIEW_MODES = [(value + ('user',), label) for value, label in CODEX_MODES] + [
    (('read-only', 'on-request', 'auto_review'), 'Read-only; automatic approval review'),
    (('workspace-write', 'on-request', 'auto_review'), 'Can edit files in this folder; automatic approval review')]

CODEX_REVIEW_NOTE = ('Automatic approval review lets Codex review eligible requests instead of asking you. '
                     'It can deny requests, and does not grant unrestricted access. Human review remains available. '
                     'Network access and access to temporary folders outside this folder require approval. '
                     'The Chat Place overrides these boundaries for this chat only; saved Codex rules still apply.')


class ClaudePermissions:
    can_remove = True
    empty_text = chat_permissions.EMPTY
    native_pending = "Checking Claude Code's own settings for this folder..."

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

    def native(self, done):
        """The rules in Claude Code's own settings for this chat's folder,
        read on a worker (a folder on a network drive can be slow), #336."""
        own = self._own()
        folder = own.cwd if own is not None else ""

        def read():
            return claude_settings.describe(*claude_settings.rules_for(folder))
        self.frame._codex_work(read, lambda text, error: done(
            "Claude Code's own settings couldn't be read." if error else text))

    def remove(self, rule_id, done):
        own = self._own()
        if own is None:
            done(None, "This chat is no longer in The Chat Place.")
            return
        folder = rule_id[len(chat_permissions.FOLDER):] if rule_id and rule_id.startswith(
            chat_permissions.FOLDER) else None
        if rule_id is not None and folder is None and rule_id not in own.allowed_tools \
                or folder is not None and folder not in own.allowed_dirs:
            done(None, "That is no longer kept.")
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
    #: TCP consent records, separate from native saved rule files (#307).
    can_remove = True
    empty_text = 'No command approvals are remembered for this chat.'
    native_pending = "Checking Codex's own saved rules..."

    def __init__(self, frame, info, record):
        self.frame, self.info, self.record = frame, info, record
        self.removal_effect = 'Codex will ask again on the next matching request. Work already approved may finish.'

    def mode_text(self) -> str:
        options = self.record.provider_options
        current = (options.get('sandbox', 'read-only'), options.get('approval_policy', 'on-request'))
        return dict(CODEX_REVIEW_MODES).get(current + (options.get('approvals_reviewer', 'user'),),
                                          dict(CODEX_MODES).get(current, 'not recorded'))

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
        from ..providers.codex_permissions import NOTICE
        options = self.record.provider_options
        reviewer = 'automatic approval review' if options.get('approvals_reviewer') == 'auto_review' else 'human review'
        files = ('Can read files and edit the chat folder: ' + self.record.cwd
                 if options.get('sandbox') == 'workspace-write' else 'Can read files; changes require approval.')
        return (f'Selected reviewer: {reviewer}. {files}\n' + CODEX_REVIEW_NOTE + '\n\n'
                'Removal applies before the next approval is sent; work already approved may finish. '
                'Changing mode clears remembered approvals. ' + NOTICE)

    def load(self, done):
        self.frame._codex_work(lambda: self.frame.sessions.owned_action('chat_rules', self.record),
                               lambda rules, error: done(None if error else self._rows(rules),
                                                        (rules or 'Remembered approvals could not be read. No approvals were changed; close and reopen this dialog to retry.') if error else None))

    @staticmethod
    def _rows(rules):
        return [chat_permissions.PermissionRow(r.rule_id, r.description, ' '.join(r.prefix), r.allowed_at_ms)
                for r in rules]

    def remove(self, rule_id, done):
        def removed(rules, error):
            if not error:
                self.frame._feedback('Removed. The next matching request will ask again; work already approved may finish.')
            done(None if error else self._rows(rules),
                 (rules or 'The removal could not be saved. Open Chat Permissions to check what is still kept.') if error else None)
        self.frame._codex_work(lambda: self.frame.sessions.owned_action('remove_chat_rule', self.record, rule_id), removed)

    def native(self, done):
        """Codex's own saved rules, listed read only, so a command that runs
        without asking here says why (#307). Read on a worker."""
        def listed(result, error):
            if error:
                text = codex_saved_rules.ERROR + (result or "Codex didn't say why.")
            else:
                never = self.record.provider_options.get('approval_policy') == 'never'
                text = codex_saved_rules.describe(*result, never_asks=never)
            # Local decisions remain readable even when the app-server cannot
            # list native rules. A server failure cannot hide the audit history.
            def history_ready(reviews, failed):
                if failed:
                    done(text + '\n\nAutomatic review history could not be read. Original records were preserved.')
                elif reviews:
                    done(text + '\n\nRecent automatic review decisions (oldest first):\n\n'
                         + '\n\n'.join(row[2] for row in reviews))
                else:
                    done(text)
            self.frame._codex_work(lambda: self.frame.sessions.owned_action('review_history', self.record), history_ready)
        self.frame._load_codex_saved_rules(self.record.cwd, listed)
