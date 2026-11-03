"""Codex operations using TCP's existing conversation workflows (#467)."""
import wx

from .. import rewind
from ..providers.codex_features import turn_anchor, goal_lines, terminal_stopped_text, CodexFeatureError
from ..providers.codex_input import build_input
from ..transcript import USER, ASSISTANT
from .dialogs import CommandPickerDialog, LinesDialog, RewindDialog, AskAsideDialog


COMMANDS = (
    ('model', 'Choose the model for this chat from its next turn.'),
    ('reasoning', 'Choose reasoning effort for this chat from its next turn.'),
    ('plan', 'Choose native Codex Plan mode or return to Default mode.'),
    ('compact', 'Compact this chat\'s context now.'),
    ('goal', 'Read this chat\'s native goal and its usage.'),
    ('goal pause', 'Pause the native goal; the current turn may finish.'),
    ('goal clear', 'Clear the native goal.'),
    ('memories on', 'Allow this chat to use and contribute to Codex memories.'),
    ('memories off', 'Disable Codex memories for this chat.'),
    ('background', 'List background terminals and stop one.'),
    ('skills', 'Choose a Codex skill to include explicitly in the next message.'),
)


#: Each control's native call, and what's said while Codex is asked: these take
#: seconds, and an unspoken wait sounds like a key that didn't work.
CONTROLS = {
    '/compact': ('thread/compact/start', {}, 'Asking Codex to compact this chat.'),
    '/goal': ('thread/goal/get', {}, "Getting this chat's Codex goal."),
    '/goal pause': ('thread/goal/set', {'status': 'paused', 'origin': 'user'}, 'Asking Codex to pause the goal.'),
    '/goal clear': ('thread/goal/clear', {}, 'Asking Codex to clear the goal.'),
    '/memories on': ('thread/memoryMode/set', {'mode': 'enabled'}, 'Asking Codex to turn memories on.'),
    '/memories off': ('thread/memoryMode/set', {'mode': 'disabled'}, 'Asking Codex to turn memories off.'),
    '/background': ('thread/backgroundTerminals/list', {}, "Getting Codex's background terminals."),
}


class CodexExtras:
    def _codex_mark(self):
        """Where the keyboard is when a picker is asked for, for
        _codex_may_open once Codex has answered."""
        return wx.Window.FindFocus(), self.reply_text.GetValue()

    def _codex_may_open(self, mark, what):
        """Whether a picker or dialog may open now that Codex has answered.
        Opening one a moment later would take keys you were typing elsewhere
        (on_insert_command says the same for Claude), so it doesn't if focus
        has moved, the reply box has changed or another dialog is up."""
        focus, text = mark
        if (wx.Window.FindFocus() is focus and self.reply_text.GetValue() == text
                and not any(isinstance(window, wx.Dialog) and window.IsModal()
                            for window in wx.GetTopLevelWindows())):
            return True
        self._feedback(f"{what} ready, but you've moved on, so nothing opened. Choose it again "
                       'when you want it.')
        return False

    def _codex_ask_aside(self, info):
        if not self._require_capability('aside', info):
            return
        from ..providers.codex_aside import CodexAsideRunner
        from .. import announce
        def ask(question, on_event):
            return CodexAsideRunner(info, question,
                lambda event: wx.CallAfter(on_event, event)).start()
        focus = wx.Window.FindFocus()
        dialog = AskAsideDialog(self, info.title, ask, provider='Codex',
            say=lambda text, failed=False: self._say(text if failed else
                announce.reply_text('Ask Aside', text, self.speech.announce)),
            copy=self._copy_text, feedback=self._feedback)
        try:
            dialog.ShowModal()
        finally:
            dialog.stop()
            dialog.Destroy()
        if focus and wx.Window.FindFocus() is not focus:
            focus.SetFocus()

    def _codex_record(self, info):
        if info is None or info.provider != 'codex' or not info.is_own:
            self._feedback('Load a Codex chat started here first.')
            return None
        return self.store.get(info.cli_session_id, provider='codex')

    def _change_codex_settings(self, info, what):
        record = self._codex_record(info)
        if record is None:
            return
        if info.key in self._codex_policy_pending:
            self._feedback("This Codex chat's settings are already being changed.")
            return
        current = dict(record.provider_options)
        earlier = getattr(self._codex_states.get(info.key), 'problem', '')

        def prepare(action, arguments):
            # Pending, as for a permission change (#385): a failure is said
            # once, by what's below, and not by the chat's update as well.
            self._codex_policy_pending.add(info.key)
            self._feedback(f'Verifying the Codex {what}.')

            def prepared(change, failed):
                if failed:
                    self._codex_policy_pending.discard(info.key)
                    self._feedback(change or f'Could not change the Codex {what}. The chat keeps its current one.')
                    return
                updated = change.record()
                saved = self.store.get(record.cli_session_id, provider='codex') is record and self._store_write(self.store.update, record.cli_session_id,
                    provider='codex', provider_options=updated.provider_options)
                if not saved:
                    record.provider_options = current

                def finished(_result, problem):
                    self._codex_policy_pending.discard(info.key)
                    latched = getattr(self._codex_states.get(info.key), 'problem', '')
                    if latched and latched != earlier:
                        self._feedback(latched)
                    elif problem:
                        self._feedback(f'Could not activate the Codex {what}. Use Stop to recheck this chat.')
                    elif not saved:
                        self._feedback(f'Could not save the Codex {what}. The chat keeps its current one.')
                    else:
                        self._feedback(f'Codex {what} for {info.title} changed, from its next turn.')
                    self._update_heading()
                    self._update_send_state()
                self._codex_work(lambda: self.sessions.owned_action('finish_policy', record, change, saved), finished)
            self._codex_work(lambda: self.sessions.owned_action(action, record, *arguments), prepared)

        if what == 'mode':
            # Nothing to wait for, so this opens straight from the key press.
            index = self._choose('Codex Collaboration Mode', 'Mode for ' + info.title + ', from its next turn:',
                ['Default: carry out the task with this chat\'s permissions.', 'Plan: discuss and produce a plan first.'])
            if index is not None:
                prepare('prepare_mode', (('default', 'plan')[index],))
            return
        mark = self._codex_mark()
        self._feedback("Getting Codex's models.")

        def loaded(models, error):
            if self.store.get(record.cli_session_id, provider='codex') is not record:
                return
            if error or not models:
                self._feedback(models or 'Codex model choices are unavailable.')
                return
            if not self._codex_may_open(mark, "Codex's models are"):
                return
            model, effort = current.get('model'), current.get('effort')
            if what == 'model':
                index = self._choose('Change Model', f'Model for {info.title}, from its next turn:',
                    [m.label + (' (now)' if m.model == model else '') for m in models])
                if index is None:
                    return
                chosen = models[index]
                model, effort = chosen.model, None
            else:
                chosen = next((m for m in models if m.model == model), None)
                if chosen is None:
                    self._feedback('Choose an available Codex model first.')
                    return
                values = [None, *chosen.efforts]
                index = self._choose('Change Effort', f'Reasoning effort for {info.title}:',
                    [('Configured effort' if v is None else v) + (' (now)' if v == effort else '') for v in values])
                if index is None:
                    return
                effort = values[index]
            prepare('prepare_settings', (model, effort))
        self._load_codex_models(loaded)

    def _branch_codex(self, info, message, *, before=False):
        if not self._require_capability('branch', info):
            return
        if message is None or message.kind not in (USER, ASSISTANT) or (before and message.kind != USER):
            self._feedback('Choose one of your messages to rewind, or a message or reply to branch from.')
            return
        try:
            anchor = turn_anchor(message)
        except CodexFeatureError:
            self._feedback('Reload this Codex chat and choose a saved message first.')
            return
        draft = message.text if before else ''
        title = rewind.rewound_title(info.title) if before else rewind.branch_title(info.title)
        if before:
            plan = rewind.RewindPlan(info.title, message, anchor,
                files_problem='Codex conversation branching leaves files as they are. File checkpoints are not available here.')
            dialog = RewindDialog(self, plan)
            try:
                if dialog.ShowModal() != wx.ID_OK:
                    return
            finally:
                dialog.Destroy()
        elif wx.MessageBox('The new Codex chat will include the whole turn containing this message. '
                'Files stay as they are, and the original chat is kept. Branch here?',
                'Branch from Here', wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) != wx.YES:
            return

        def created(record, error):
            if error:
                self._feedback(record or 'Codex could not create the branch.')
                return
            record.forked_from = info.title
            if not self._store_write(self.store.add, record):
                self._feedback('The Codex copy was made but could not be saved in The Chat Place.')
                return
            if draft:
                self._codex_drafts[record.to_info().key] = draft
            self.open_session(record.to_info())
            self.refresh_sessions()
            self.reply_text.SetFocus()
            self._feedback(f'Opened {title}. The original chat and its files are kept. '
                           'Change your message and send when ready.' if before else f'Opened {title}.')
        self._feedback(f'Creating {title}.')
        self._codex_work(lambda: self.sessions.branch(info, title, anchor, before), created)

    def _codex_commands(self, info, *, skills_only=False):
        mark = self._codex_mark()
        self._feedback('Getting the Codex skills for this folder.' if skills_only
                       else 'Getting the Codex commands and skills for this folder.')

        def loaded(skills, error):
            if skills_only and error:
                self._feedback(skills or "Could not get this folder's Codex skills.")
                return
            rows = [] if skills_only else [dict(name=n, description=d, builtin=True, provider='Codex') for n, d in COMMANDS]
            if not error:
                rows += [dict(s, prefix='$', builtin=False) for s in skills]
            if not rows:
                self._feedback('No enabled Codex skills are available for this folder.')
                return
            if self._open is None or self._open.key != info.key:
                return  # another chat is loaded now: a picker would fill its reply box
            if not self._codex_may_open(mark, 'The Codex commands are'):
                return
            if error:
                self._feedback("Could not get this folder's Codex skills, so only the chat controls are listed.")
            dialog = CommandPickerDialog(self, rows)
            try:
                if dialog.ShowModal() != wx.ID_OK or dialog.chosen is None:
                    return
                chosen = dialog.chosen
            finally:
                dialog.Destroy()
            prefix = chosen.get('prefix', '/') + chosen['name']
            draft = self.reply_text.GetValue().lstrip()
            self.reply_text.SetValue(prefix + ('\n' if chosen.get('builtin') and draft else ' ') + draft)
            if chosen.get('prefix') == '$':
                selected = getattr(self, '_codex_skills', {})
                selected.setdefault(info.key, {})[chosen['name']] = chosen['path']
                self._codex_skills = selected
            self.reply_text.SetFocus()
            self.reply_text.SetInsertionPointEnd()
        self._codex_work(lambda: self.sessions.skills(info), loaded)

    def _handle_codex_command(self, info, text):
        command, separator, draft = text.partition('\n')
        command = command.strip()
        if command not in ('/model', '/reasoning', '/plan', '/skills') and command not in CONTROLS:
            return False
        # Before anything is asked of Codex, so the box is as it will stay when
        # _codex_may_open compares it.
        self.reply_text.SetValue(draft if separator else '')
        if command == '/model':
            self._change_codex_settings(info, 'model')
        elif command == '/reasoning':
            self._change_codex_settings(info, 'effort')
        elif command == '/plan':
            self._change_codex_settings(info, 'mode')
        elif command == '/skills':
            self._codex_commands(info, skills_only=True)
        else:
            self._codex_control(info, command)
        return True

    def _codex_control(self, info, command):
        record = self._codex_record(info)
        if record is None:
            return
        method, args, asking = CONTROLS[command]
        mark = self._codex_mark()
        self._feedback(asking)

        def done(result, error):
            if error:
                self._feedback(result or 'Codex could not confirm that operation.')
                return
            if command == '/goal':
                try:
                    lines = goal_lines(result)
                except CodexFeatureError:
                    self._feedback("Codex returned a goal The Chat Place can't read.")
                else:
                    if self._codex_may_open(mark, "This chat's Codex goal is"):
                        self._modal(LinesDialog(self, 'Codex Goal', 'Goal', lines, self._copy_text))
            elif command == '/background':
                self._codex_background(record, result, mark)
            elif command == '/compact':
                # The end of the compaction is what gets said (_on_owned_update).
                self._status(f'{info.title}: Codex is compacting its context.')
            else:
                self._feedback('Codex accepted ' + command[1:] + ' for ' + info.title + '.')
            self._refresh_chat()
        self._codex_work(lambda: self.sessions.owned_action('feature', record, method, args), done)

    def _codex_background(self, record, result, mark):
        rows = result.get('data', [])
        if not isinstance(rows, list) or not rows:
            self._feedback('No background terminals are running in this Codex chat.')
            return
        rows = [r for r in rows if isinstance(r, dict) and isinstance(r.get('processId'), str)]
        if not rows:
            self._feedback('Codex did not return readable background terminal details.')
            return
        if not self._codex_may_open(mark, "Codex's background terminals are"):
            return
        index = self._choose('Stop Background Task', 'Stop which Codex terminal?',
            [str(r.get('command') or r['processId']) for r in rows])
        if index is None or wx.MessageBox('Stop the selected terminal? The Codex turn and other terminals continue.',
                'Stop Background Task', wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) != wx.YES:
            return
        self._feedback('Stopping the terminal.')
        self._codex_work(lambda: self.sessions.owned_action('feature', record,
            'thread/backgroundTerminals/terminate', {'processId': rows[index]['processId']}),
            lambda result, error: self._feedback((result or 'Could not stop the terminal.')
                                                 if error else terminal_stopped_text(result)))

    def _codex_message(self, info, text):
        import re
        skills = getattr(self, '_codex_skills', {}).get(info.key, {})
        selected = [(name, path) for name, path in skills.items()
                    if re.search(r'(?<!\w)\$' + re.escape(name) + r'(?![\w-])', text)]
        return build_input(text, self._attachments.get(self._attachment_key(info), []), skills=selected)
