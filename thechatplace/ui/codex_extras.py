"""Codex operations using TCP's existing conversation workflows (#467)."""
import wx

from .. import rewind
from ..providers.codex_features import turn_anchor, goal_lines, CodexFeatureError
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


class CodexExtras:
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

        def loaded(models, error):
            if self.store.get(record.cli_session_id, provider='codex') is not record:
                return
            if error or not models:
                self._feedback(models or 'Codex model choices are unavailable.')
                return
            current = dict(record.provider_options)
            model, effort = current.get('model'), current.get('effort')
            action, arguments = 'prepare_settings', None
            if what == 'mode':
                index = self._choose('Codex Collaboration Mode', 'Mode for ' + info.title + ', from its next turn:',
                    ['Default: carry out the task with this chat\'s permissions.', 'Plan: discuss and produce a plan first.'])
                if index is None:
                    return
                action, arguments = 'prepare_mode', (('default', 'plan')[index],)
            elif what == 'model':
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

            def prepared(change, failed):
                if failed:
                    self._feedback(change or 'Could not verify the Codex settings.')
                    return
                updated = change.record()
                saved = self.store.get(record.cli_session_id, provider='codex') is record and self._store_write(self.store.update, record.cli_session_id,
                    provider='codex', provider_options=updated.provider_options)
                if not saved:
                    record.provider_options = current

                def finished(_result, problem):
                    self._feedback('Could not activate the Codex settings. Use Stop to recheck this chat.'
                        if problem or not saved else f'Settings for {info.title} changed, from its next turn.')
                    self._update_heading()
                    self._update_send_state()
                self._codex_work(lambda: self.sessions.owned_action('finish_policy', record, change, saved), finished)
            if arguments is None:
                arguments = (model, effort)
            self._codex_work(lambda: self.sessions.owned_action(action, record, *arguments), prepared)
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
        self._codex_work(lambda: self.sessions.branch(info, title, anchor, before), created)

    def _codex_commands(self, info, *, skills_only=False):
        def loaded(skills, error):
            if self._open is None or self._open.key != info.key:
                return
            rows = [] if skills_only else [dict(name=n, description=d, builtin=True, provider='Codex') for n, d in COMMANDS]
            if not error:
                rows += [dict(s, prefix='$', builtin=False) for s in skills]
            if not rows:
                self._feedback('No enabled Codex skills are available for this folder.')
                return
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
        if command == '/model':
            self._change_codex_settings(info, 'model')
        elif command == '/reasoning':
            self._change_codex_settings(info, 'effort')
        elif command == '/plan':
            self._change_codex_settings(info, 'mode')
        elif command == '/skills':
            self._codex_commands(info, skills_only=True)
        elif command in ('/compact', '/goal', '/goal pause', '/goal clear', '/memories on', '/memories off', '/background'):
            self._codex_control(info, command)
        else:
            return False
        if separator:
            self.reply_text.SetValue(draft)
        return True

    def _codex_control(self, info, command):
        record = self._codex_record(info)
        if record is None:
            return
        method, args = {
            '/compact': ('thread/compact/start', {}),
            '/goal': ('thread/goal/get', {}),
            '/goal pause': ('thread/goal/set', {'status': 'paused', 'origin': 'user'}),
            '/goal clear': ('thread/goal/clear', {}),
            '/memories on': ('thread/memoryMode/set', {'mode': 'enabled'}),
            '/memories off': ('thread/memoryMode/set', {'mode': 'disabled'}),
            '/background': ('thread/backgroundTerminals/list', {}),
        }[command]

        def done(result, error):
            if error:
                self._feedback(result or 'Codex could not confirm that operation.')
                return
            if command == '/goal':
                self._modal(LinesDialog(self, 'Codex Goal', 'Goal', goal_lines(result), self._copy_text))
            elif command == '/background':
                self._codex_background(info, record, result)
            else:
                self._feedback('Codex accepted ' + command[1:] + ' for ' + info.title + '.')
            self._refresh_chat()
        self._codex_work(lambda: self.sessions.owned_action('feature', record, method, args), done)

    def _codex_background(self, info, record, result):
        rows = result.get('data', [])
        if not isinstance(rows, list) or not rows:
            self._feedback('No background terminals are running in this Codex chat.')
            return
        rows = [r for r in rows if isinstance(r, dict) and isinstance(r.get('processId'), str)]
        if not rows:
            self._feedback('Codex did not return readable background terminal details.')
            return
        index = self._choose('Stop Background Task', 'Stop which Codex terminal?',
            [str(r.get('command') or r['processId']) for r in rows])
        if index is None or wx.MessageBox('Stop the selected terminal? The Codex turn and other terminals continue.',
                'Stop Background Task', wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) != wx.YES:
            return
        self._codex_work(lambda: self.sessions.owned_action('feature', record,
            'thread/backgroundTerminals/terminate', {'processId': rows[index]['processId']}),
            lambda result, error: self._feedback(result or 'Could not stop the terminal.' if error else 'Codex terminal stopped.'))

    def _codex_message(self, info, text):
        import re
        skills = getattr(self, '_codex_skills', {}).get(info.key, {})
        selected = [(name, path) for name, path in skills.items()
                    if re.search(r'(?<!\w)\$' + re.escape(name) + r'(?![\w-])', text)]
        return build_input(text, self._attachments.get(self._attachment_key(info), []), skills=selected)
