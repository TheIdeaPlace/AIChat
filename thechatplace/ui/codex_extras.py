"""Codex operations using TCP's existing conversation workflows (#467)."""
import wx

from .. import rewind
from ..providers.codex_features import turn_anchor, goal_lines, terminal_stopped_text, CodexFeatureError
from ..providers.codex_input import build_input
from ..transcript import USER, ASSISTANT
from .dialogs import CommandPickerDialog, LinesDialog, RewindDialog, AskAsideDialog


#: Ctrl+/ reads these aloud, so they're in a user's words: no "native" (#596).
COMMANDS = (
    ('model', 'Choose the model for this chat from its next turn.'),
    ('reasoning', 'Choose reasoning effort for this chat from its next turn.'),
    ('plan', 'Choose Plan mode, or go back to Default mode.'),
    ('compact', 'Compact this chat\'s context now.'),
    ('goal', 'Read this chat\'s Codex goal and its usage.'),
    ('goal pause', 'Pause the goal; the current turn may finish.'),
    ('goal clear', 'Clear the goal.'),
    ('memories on', 'Allow this chat to use and contribute to Codex memories.'),
    ('memories off', 'Disable Codex memories for this chat.'),
    ('background', 'List background tasks and stop one.'),
    ('skills', 'Choose a Codex skill to include explicitly in the next message.'),
)


#: Each control's call, and what's said while Codex is asked: these take
#: seconds, and an unspoken wait sounds like a key that didn't work.
CONTROLS = {
    '/compact': ('thread/compact/start', {}, 'Asking Codex to compact this chat.'),
    '/goal': ('thread/goal/get', {}, "Getting this chat's Codex goal."),
    '/goal pause': ('thread/goal/set', {'status': 'paused', 'origin': 'user'}, 'Asking Codex to pause the goal.'),
    '/goal clear': ('thread/goal/clear', {}, 'Asking Codex to clear the goal.'),
    '/memories on': ('thread/memoryMode/set', {'mode': 'enabled'}, 'Asking Codex to turn memories on.'),
    '/memories off': ('thread/memoryMode/set', {'mode': 'disabled'}, 'Asking Codex to turn memories off.'),
    '/background': ('thread/backgroundTerminals/list', {}, "Getting this chat's background tasks."),
}

#: What's said once Codex has done a control with nothing to show: what is
#: now so, not that Codex "accepted" a command (#596). ``{title}`` is the chat's.
CONTROL_DONE = {
    '/goal pause': 'Goal paused for {title}.',
    '/goal clear': 'Goal cleared for {title}.',
    '/memories on': 'Codex memories are on for {title}.',
    '/memories off': 'Codex memories are off for {title}.',
}

#: The /plan picker. "Collaboration mode" is Codex's name inside; the guide
#: and the release notes call it Plan mode (#596).
PLAN_MODE_TITLE = 'Plan Mode'
PLAN_MODE_CHOICES = ['Default: carry out the task with this chat\'s permissions.',
                     'Plan: discuss and produce a plan first.']

#: Why Retry from Here can't put a Codex chat's files back, and what the new
#: chat keeps (#596, finding 4 and decision 3). The dialog shows it for Files
#: only too, which makes no chat, so it says "a retried chat", not "the new
#: chat".
REWIND_FILES_NOTE = ("Codex doesn't put files back, so only the conversation goes back. "
                     "A retried Codex chat keeps the original's settings.")
#: The same for a chat started elsewhere, whose settings The Chat Place
#: doesn't have: its copy starts as a new chat does (codex_runtime._kept_settings).
REWIND_FILES_NOTE_ELSEWHERE = ("Codex doesn't put files back, so only the conversation goes back. "
                               "A retried Codex chat asks before changing files, and uses Codex's "
                               "default model and effort.")


def effort_rows(values, now):
    """Change Effort's rows for a Codex chat: None is whatever Codex is set
    to use, so it's named as Codex's, not "Configured effort", and the
    others read as words ("Extra high", not "xhigh") (#596)."""
    from ..ui_text import CODEX_DEFAULT, codex_effort_label
    return [(CODEX_DEFAULT if v is None else codex_effort_label(v)) + (' (now)' if v == now else '')
            for v in values]


def copy_note(*, short=False, tools=True, own=True):
    """What a Codex copy (Rewind, Branch, Copy with Tool) keeps: the
    original's permission mode, model, effort, Plan mode and Chat Options,
    and its connected tools unless it's a tools copy, as Claude's copies keep
    theirs (codex_runtime._kept_settings; #596, decision 3).

    ``own`` False is a copy of a chat started elsewhere, whose settings The
    Chat Place doesn't have: it starts read-only on Codex's defaults, as a
    new chat with nothing chosen would, and says how to let it edit.

    ``short`` is for when the copy has opened, and is said once the window
    is on it, so it names nothing of "this chat". The full note is for a
    question asked before; ``tools=False`` leaves connected tools out of it,
    for a tools copy, which has the ones you chose."""
    if not own:
        from ..ui_text import key_name
        key = key_name('Ctrl+Shift+O')
        if short:
            return f"It asks before changing files; Change Permission Mode ({key}) lets it edit."
        return ("The new chat asks before changing files, and uses Codex's default model and effort. "
                f"Change Permission Mode ({key}) in it lets it edit files.")
    if short:
        return "It keeps the original's settings."
    kept = ('permission mode, model, effort, Chat Options and connected tools' if tools
            else 'permission mode, model, effort and Chat Options')
    return f"The new chat keeps the original's {kept}."


def codex_effort_plan(models, model):
    """Which model's efforts Change Effort offers for a Codex chat on
    ``model`` (None: Codex's default), as ``(choice, pinned)``, or what to
    say instead. A chat on Codex's default was refused, though New Session
    offers its efforts (#596): it gets the efforts of the model Codex marks as
    its default, and ``pinned`` says choosing one also names that model, as
    Codex takes an effort only with a model. A model Codex no longer lists
    can't be given an effort at all (codex_owned refuses it), so that's said
    with what to do."""
    if model:
        chosen = next((m for m in models if m.model == model), None)
        if chosen is not None:
            return chosen, False
        return (f"{model} isn't in Codex's list of models now, so its effort can't be changed. "
                "Change Model, on the File menu, chooses another.")
    chosen = next((m for m in models if m.is_default), None)
    if chosen is None:
        return ("Codex didn't say which model is its default, so its effort can't be changed. "
                "Change Model, on the File menu, chooses a model.")
    return chosen, True


#: Said when OK is pressed in a Codex chat's Chat Options during a turn. The
#: runtime changes a chat's settings only between turns (prepare_policy, a
#: verified barrier: #184, #467), and a queued message goes the moment a turn
#: ends, so the change can't wait to be made then; the dialog stays open with
#: what you typed instead of losing it (#596).
CHAT_OPTIONS_WAIT = ("Codex is still working in this chat, and Chat Options change only between "
                     "turns. Press OK again once it has finished; what you typed stays here.")


class CodexExtras:
    def _codex_has_work(self, key):
        """Whether a Codex chat has a turn running, a send on its way or
        messages queued: what prepare_policy refuses a change during."""
        from ..sessions import WORKING
        if getattr(self, '_codex_send_pending', {}).get(key):
            return True
        state = self._codex_states.get(key)
        return state is not None and bool(state.state == WORKING or state.outcome == 'inProgress'
                                          or getattr(state, 'queued', ()) or getattr(state, 'requests', ()))

    def _codex_mark(self):
        """Where the keyboard is when a picker is asked for, for
        _codex_may_open once Codex has answered."""
        return wx.Window.FindFocus(), self.reply_text.GetValue()

    def _codex_may_open(self, mark, what, over=None, still_on=None):
        """Whether a picker or dialog may open now that Codex has answered.
        Opening one a moment later would take keys you were typing elsewhere
        (on_insert_command says the same for Claude), so it doesn't if focus
        has moved, the reply box has changed or another dialog is up.
        ``over`` is the dialog it opens from, which isn't another (Lists, for
        a subagent's conversation). There, moving focus about in that dialog
        doesn't count, so ``still_on()`` says instead whether that dialog is
        still on what you asked for: another row is having moved on (#468)."""
        focus, text = mark
        if ((over is not None or wx.Window.FindFocus() is focus)
                and (still_on is None or still_on())
                and self.reply_text.GetValue() == text
                and not any(isinstance(window, wx.Dialog) and window.IsModal()
                            and window is not over for window in wx.GetTopLevelWindows())):
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
            # The thread's turn list decides where the copy ends (#559).
            return CodexAsideRunner(info, question,
                lambda event: wx.CallAfter(on_event, event)).start()
        focus = wx.Window.FindFocus()
        dialog = AskAsideDialog(self, info.title, ask, provider='Codex',
            say=lambda text, failed=False: self._say(text if failed else
                announce.reply_text('Ask Aside', text, self.speech.announce)),
            copy=self._copy_text, feedback=self._feedback, history=self._aside_history(info))
        try:
            dialog.ShowModal()
        finally:
            dialog.settle()
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
            self._feedback(f'Checking the new {what} with Codex.')

            def prepared(change, failed):
                if failed:
                    self._codex_policy_pending.discard(info.key)
                    from ..ui_text import with_stop_key
                    self._feedback(with_stop_key(change or f'Could not change the Codex {what}. The chat keeps its current one.'))
                    return
                updated = change.record()
                saved = self.store.get(record.cli_session_id, provider='codex') is record and self._store_write(self.store.update, record.cli_session_id,
                    provider='codex', provider_options=updated.provider_options)
                if not saved:
                    record.provider_options = current

                def finished(_result, problem):
                    self._codex_policy_pending.discard(info.key)
                    latched = getattr(self._codex_states.get(info.key), 'problem', '')
                    from ..ui_text import with_stop_key
                    if latched and latched != earlier:
                        # The usual failure: the runtime latched what went
                        # wrong, ending with what Stop does; the key is ours
                        # to name (#596).
                        self._feedback(with_stop_key(latched))
                    elif problem:
                        # Stop is the recheck (#385): with no turn running it
                        # checks the chat with Codex again, so say why to
                        # press it (#596).
                        from ..providers.codex_runtime import STOP_TO_RECHECK
                        self._feedback(with_stop_key(f"Codex didn't confirm the new {what}. " + STOP_TO_RECHECK))
                    elif not saved:
                        self._feedback(f'Could not save the Codex {what}. The chat keeps its current one.')
                    else:
                        self._feedback(f'Codex {what} for {info.title} changed, from its next turn.')
                    self._update_heading()
                    self._update_send_state()
                self._codex_work(lambda: self.sessions.owned_action('finish_policy', record, change, saved), finished)
            self._codex_work(lambda: self.sessions.owned_action(action, record, *arguments), prepared)

        if what == 'chat options':
            from ..chat_options import ChatOptions
            from .dialogs import ChatOptionsDialog
            dialog = ChatOptionsDialog(self, info.title, record.cwd,
                options=ChatOptions(instructions=current.get('instructions') or ''),
                folders=current.get('extra_roots', ()), provider='codex', existing=True,
                feedback=self._feedback, busy=lambda: CHAT_OPTIONS_WAIT if self._codex_has_work(info.key) else '')
            try:
                if dialog.ShowModal() == wx.ID_OK:
                    prepare('prepare_chat_options', (dialog.options().instructions, dialog.chosen_folders()))
            finally:
                dialog.Destroy()
            return
        if what == 'mode':
            # Nothing to wait for, so this opens straight from the key press.
            index = self._choose(PLAN_MODE_TITLE, 'Mode for ' + info.title + ', from its next turn:',
                list(PLAN_MODE_CHOICES))
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
            from ..ui_text import CODEX_DEFAULT
            model, effort = current.get('model'), current.get('effort')
            if what == 'model':
                # Codex's default first, as in New Session, so a chat can go
                # back to it, and to its effort (#596). As in New Session, the
                # chat then follows Codex's default at each turn (decision 19;
                # codex_runtime._follow_default); the row names the model that
                # default is now, when Codex says which it is.
                default = next((m for m in models if m.is_default), None)
                values = [None, *models]
                index = self._choose('Change Model', f'Model for {info.title}, from its next turn:',
                    [CODEX_DEFAULT + (f' ({default.label})' if default else '')
                     + (' (now)' if not model else '')]
                    + [m.label + (' (now)' if m.model == model else '') for m in models])
                if index is None:
                    return
                chosen = values[index]
                model, effort = (chosen.model if chosen else None), None
            else:
                plan = codex_effort_plan(models, model)
                if isinstance(plan, str):
                    self._feedback(plan)
                    return
                chosen, pinned = plan
                values = [None, *chosen.efforts]
                on = f", on {chosen.label}, Codex's default model" if pinned else ''
                index = self._choose('Change Effort', f'Effort for {info.title}{on}, from its next turn:',
                    effort_rows(values, effort))
                if index is None:
                    return
                effort = values[index]
                # Codex takes an effort only with a model named, so a chat on
                # Codex's default names the model that default is (#596).
                if effort is not None and pinned:
                    model = chosen.model
            prepare('prepare_settings', (model, effort))
        self._load_codex_models(loaded)

    def _branch_codex(self, info, message, *, before=False):
        if not self._require_capability('branch', info):
            return
        if message is None or message.kind not in (USER, ASSISTANT) or (before and message.kind != USER):
            self._feedback('Choose one of your messages to retry from, or a message or reply to branch from.')
            return
        try:
            anchor = turn_anchor(message)
        except CodexFeatureError:
            self._feedback('Reload this Codex chat and choose a saved message first.')
            return
        draft = message.text if before else ''
        title = rewind.rewound_title(info.title) if before else rewind.branch_title(info.title)
        # Only a chat started here has settings to keep; a copy of one
        # started elsewhere starts as a new chat does, and says so (#596).
        own = self.store.get(info.cli_session_id, provider='codex') if info.is_own else None
        kept = own is not None
        if before:
            plan = rewind.RewindPlan(info.title, message, anchor,
                                     files_problem=REWIND_FILES_NOTE if kept else REWIND_FILES_NOTE_ELSEWHERE)
            dialog = RewindDialog(self, plan, feedback=self._feedback)
            try:
                if dialog.ShowModal() != wx.ID_OK:
                    return
            finally:
                dialog.Destroy()
        elif wx.MessageBox('A new Codex chat will start with the whole turn containing this message, '
                'and this chat is kept. ' + copy_note(own=kept) + ' Branch here?',
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
            said = f'Opened {title}. {copy_note(short=True, own=kept)} The original chat is kept.'
            self._feedback(said + (' Change your message and send when ready.' if before else ''))
        self._feedback(f'Creating {title}.')
        self._codex_work(lambda: self.sessions.branch(info, title, anchor, before, own=own), created)

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
                self._feedback(CONTROL_DONE[command].format(title=info.title))
            self._refresh_chat()
        self._codex_work(lambda: self.sessions.owned_action('feature', record, method, args), done)

    def _codex_background(self, record, result, mark):
        # "Background task" throughout, as File, Stop Background Task and a
        # Claude chat say it: "terminal" sounded like a window (#596).
        rows = result.get('data', [])
        if not isinstance(rows, list) or not rows:
            self._feedback('No background tasks are running in this Codex chat.')
            return
        rows = [r for r in rows if isinstance(r, dict) and isinstance(r.get('processId'), str)]
        if not rows:
            self._feedback('Codex did not return readable background task details.')
            return
        if not self._codex_may_open(mark, "This chat's background tasks are"):
            return
        names = [str(r.get('command') or r['processId']) for r in rows]
        index = self._choose('Stop Background Task', f'Background tasks in {record.title}. Stop which?', names)
        if index is None:
            return
        # The command, on one line and not too long to hear, as Claude's
        # question names its task.
        command = ' '.join(names[index].split())
        command = command if len(command) <= 200 else command[:199] + '…'
        if wx.MessageBox(f'Stop {command}? The turn and other '
                'background tasks carry on.',
                'Stop Background Task', wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) != wx.YES:
            return
        self._feedback('Stopping the background task.')
        self._codex_work(lambda: self.sessions.owned_action('feature', record,
            'thread/backgroundTerminals/terminate', {'processId': rows[index]['processId']}),
            lambda result, error: self._feedback((result or 'Could not stop the background task.')
                                                 if error else terminal_stopped_text(result)))

    def _codex_message(self, info, text):
        import re
        skills = getattr(self, '_codex_skills', {}).get(info.key, {})
        selected = [(name, path) for name, path in skills.items()
                    if re.search(r'(?<!\w)\$' + re.escape(name) + r'(?![\w-])', text)]
        return build_input(text, self._attachments.get(self._attachment_key(info), []), skills=selected)
