"""UI-thread callbacks for owned chats that run through a provider runtime
(Codex; sessions.RUNTIME_PROVIDERS); all backend actions use workers.

The names say Codex because Codex came first; the shared paths take the
provider from the chat's record or key (#352). Change Permission Mode, saved
rules, plans and creation stay Codex's own."""
import time
from dataclasses import replace
from types import SimpleNamespace

import wx

from ..providers.codex_requests import QUESTIONS
from ..providers.codex_rpc import CodexRpcError, CodexSteerRefused, CodexTurnRejected
from ..providers.codex_runtime import STOP_TO_RECHECK
from ..sessions import IDLE, WORKING, STATUS_UNAVAILABLE, home_app
from ..agents import provider_name
from .. import announce
from ..claude_cli import describe_elapsed
from ..codex_diagnostics import record_failure, safe_reason
from ..ui_text import key_name
from .dialogs import (COPILOT_FREE, COPILOT_FREE_LABEL, COPILOT_SESSION_MODES,
                      CodexPermissionDialog, QuestionDialog, codex_permission_prompt,
                      copilot_free_warning, copilot_mode_name)
from .permission_hosts import CODEX_MODES


class CodexChats:
    def _codex_task_steps(self, state, turn, messages=None):
        # Live events can arrive before the new turn is readable in history.
        # Match its native ID rather than attaching it to the previous turn.
        messages = self._chat_messages if messages is None else messages
        position = next((m.turn for m in messages if m.key.startswith(state.turn_id + ':')),
                        max(1, turn + 1))
        return [replace(task, turn=position, changed_turn=position) for task in state.tasks]

    @staticmethod
    def _codex_status_text(state, provider='codex'):
        name = provider_name(provider)
        if state is None:
            return f'No {name} turn is running in this chat.'
        if state.problem:
            return state.problem
        if state.state == STATUS_UNAVAILABLE:
            return f'{name} status is unavailable. Use Stop ({key_name("Ctrl+Period")}) to recheck this chat.'
        if state.requests:
            text = f'{name} is waiting for you. {key_name("Ctrl+Shift+A")} answers.'
            if state.activity:
                text += ' Latest activity: ' + state.activity
        elif state.outcome == 'inProgress':
            text = state.activity or f'{name} is working.'
        elif state.state == WORKING:
            text = f'Starting a {name} turn.'
        else:
            text = f'No {name} turn is running in this chat.'
        if state.started_at and state.outcome == 'inProgress':
            text += ' The turn has run for ' + describe_elapsed(time.monotonic() - state.started_at) + '.'
        if state.queued:
            count = len(state.queued)
            text += f' {count} {"message" if count == 1 else "messages"} queued.'
        if getattr(state, 'free', False):
            text += (' It runs freely in its folder: what stays inside it is allowed without '
                     'asking.')
            if getattr(state, 'location_changed', False):
                text += (' Its commands ask again, since a command you allowed may have moved '
                         "Copilot's shell; file changes are still allowed.")
        steering = len(getattr(state, 'steering', ()))
        if steering:
            text += (f' 1 steered message waiting for {name} to accept it.' if steering == 1
                     else f' {steering} steered messages waiting for {name} to accept them.')
        if state.notice:
            text += ' ' + state.notice
        notices = [text for turn, _identity, text in getattr(state, 'permission_notices', ()) if turn == state.turn_id]
        if notices:
            count = len(notices)
            text += f' {count} {"request" if count == 1 else "requests"} allowed automatically. Latest: ' + notices[-1]
        return text

    def _codex_work(self, action, done):
        def work():
            try:
                result, error = action(), False
            except Exception as exc:
                record_failure(exc)
                result, error = safe_reason(exc) or None, True
            wx.CallAfter(finish, result, error)
        def finish(result, error):
            if not self._gone():
                done(result, error)
        self._pool.submit(work)

    def _restore_codex_text(self, key, texts):
        text = '\n\n'.join(texts)
        if not text:
            return
        if self._open is not None and self._open.key == key:
            current = self.reply_text.GetValue()
            self.reply_text.SetValue(text + ('\n\n' + current if current else ''))
            self._codex_drafts[key] = self.reply_text.GetValue()
        else:
            current = self._codex_drafts.get(key, '')
            self._codex_drafts[key] = text + ('\n\n' + current if current else '')

    def _load_codex_rules(self, folder, callback):
        self._codex_work(lambda: self.sessions.saved_rule_count('codex', folder), callback)

    def _load_codex_saved_rules(self, folder, callback):
        self._codex_work(lambda: self.sessions.saved_rules('codex', folder), callback)

    def _load_codex_models(self, callback):
        self._codex_work(lambda: self.sessions.model_choices('codex'), callback)

    def _create_codex_owned(self, folder, title, message, source=None, model='', effort='', sandbox=None,
                            approval_policy=None, made=None, joint=False, provider='codex', approvals_reviewer=None,
                            copilot_mode=''):
        """A new owned chat with a runtime provider (Codex's name is history:
        Copilot comes here too, #352). ``made(info, message)`` hears of the
        new chat (#378); with ``joint`` it sends the message, as an agent
        conversation's."""
        if joint and provider == 'copilot':
            # An agent conversation hands work between its chats, so a new
            # Copilot one is asked for agent mode, whatever else was (#424).
            from ..providers.copilot_runtime import AGENT_MODE
            copilot_mode = AGENT_MODE
        options = {key: value for key, value in (('model', model), ('effort', effort),
                   ('sandbox', sandbox), ('approval_policy', approval_policy),
                   ('approvals_reviewer', approvals_reviewer), ('copilot_mode', copilot_mode))
                   if value}
        name = provider_name(provider)
        def create():
            return (self.sessions.fork_owned(source, title, **options) if source is not None
                    else self.sessions.create_owned(provider, folder, title, **options))
        def done(record, error):
            if error:
                text = record or (f'Could not create this {name} chat. Check {name} sign-in and '
                                  'configuration.')
                if message:
                    text = self._keep_first_message(message, provider) + ' ' + text
                self._feedback(text)
                return
            if not self._store_write(self.store.add, record):
                self._feedback(f'{name} created the chat, but it could not be saved here. Open it in '
                               f'{home_app(provider)}.')
                return
            if joint:
                self.refresh_sessions()
                made(record.to_info(), message)
                self._say_early_notice(provider, record, message)
                return
            self._codex_drafts[record.to_info().key] = message
            self.open_session(record.to_info())
            self.refresh_sessions()
            if message:
                self.reply_text.SetValue('')
                self._codex_drafts.pop(record.to_info().key, None)
                self._send_codex_owned(record, message)
            if made is not None:
                made(record.to_info(), message)
            self._say_early_notice(provider, record, message)
        self._codex_work(create, done)

    def _say_early_notice(self, provider, record, message=''):
        """Say a notice a new chat had before the window had its record.
        The runtime can report one while it is still being created (Copilot
        refusing the model chosen in New Session); that update arrives before
        the record is stored, so _on_owned_update keeps the state but can't
        say it, and later updates with the same notice won't either. So too
        a problem, such as plan mode refused (#424), unless a first message
        was sent: its refusal says it."""
        state = self._codex_states.get(f'{provider}:' + record.cli_session_id)
        if state is not None and state.notice:
            self._say(f'{record.title}: {state.notice}', interrupt=False)
        if state is not None and state.problem and not message:
            self._say(f'{record.title}: {state.problem}', interrupt=False)

    def _change_copilot_model(self, info):
        """File, Change Model for a Copilot chat: the models Copilot offers
        that chat, each with the premium requests a message costs. From its
        next turn (what a turn already running does is Copilot's; unchecked)."""
        if not info.is_own:
            self._feedback(f"{info.title} wasn't started in The Chat Place: its model is set in "
                           "Copilot CLI.")
            return
        if not self._can_edit(info):
            # Started here, but Copilot isn't running here now (turned off
            # in Settings, or Copilot CLI not found): the chat is read-only.
            self._feedback(f"Copilot isn't available here now, so {info.title} is read-only and "
                           "its model can't be changed.")
            return
        record = self.store.get(info.cli_session_id, provider='copilot')
        if record is None:
            self._feedback(f"Couldn't find {info.title} in The Chat Place's sessions.")
            return
        state = self._codex_states.get(info.key)
        models = tuple(getattr(state, 'models', ()) or ())
        if not models:
            self._feedback(f"{info.title} isn't open in Copilot yet, so its models aren't known. "
                           "Select it, wait until it's ready, then change its model.")
            return
        now = getattr(state, 'model', '')
        names = dict(models)
        current = names.get(now, now or "Copilot's own")
        chosen = self._change_setting('Change Model', f'Model for {info.title}, now {current}:',
                                      models, now)
        if chosen is None:
            return
        def done(result, error):
            if error:
                self._feedback(result if isinstance(result, str) and result
                               else "Copilot couldn't switch to that model.")
            else:
                self._feedback(f'{info.title} now uses {names[chosen]}, from its next turn.')
        self._codex_work(lambda: self.sessions.owned_action('set_model', record, chosen), done)

    def _change_copilot_mode(self, info):
        """Change Permission Mode (Ctrl+Shift+O) for a Copilot chat: agent or
        plan mode, as Copilot's own Shift+Tab switches them, or "run freely in
        this folder" (copilot_folder, #426), after a warning. Copilot asks
        before it acts in agent and plan mode; running freely, The Chat Place
        answers for you what stays inside the chat's folder. What's said is
        the mode Copilot then reports, which needn't be the one chosen (#424)."""
        from ..providers.copilot_runtime import AGENT_MODE, MODE_PROBLEMS
        if not info.is_own:
            self._feedback(f"{info.title} wasn't started in The Chat Place: its mode is set in "
                           "Copilot CLI.")
            return
        if not self._can_edit(info):
            self._feedback(f"Copilot isn't available here now, so {info.title} is read-only and "
                           "its mode can't be changed.")
            return
        record = self.store.get(info.cli_session_id, provider='copilot')
        if record is None:
            self._feedback(f"Couldn't find {info.title} in The Chat Place's sessions.")
            return
        state = self._codex_states.get(info.key)
        if state is None or not getattr(state, 'mode', ''):
            self._feedback(f"{info.title} isn't open in Copilot yet, so its mode isn't known. "
                           "Select it, wait until it's ready, then change its mode.")
            return
        pending = self._copilot_mode_pending = getattr(self, '_copilot_mode_pending', set())
        if info.key in pending:
            # One switch at a time: a second, on its way at once, could
            # finish first (Claude's review of #426).
            self._feedback(f"{info.title}'s mode is still being switched. Try again in a moment.")
            return
        free_now = getattr(state, 'free', False)
        now = COPILOT_FREE if free_now else state.mode
        # A chat that can't send until its mode is chosen again (plan mode
        # refused or not kept): either choice goes through, the one it's in
        # now too, and a switch Copilot confirms lets it send (#424).
        blocked = getattr(state, 'problem', '') in MODE_PROBLEMS
        now_name = ('running freely in its folder' if free_now
                    else copilot_mode_name(state.mode))
        # Not offered where an organisation manages Copilot (#426): the
        # runtime refuses it there too.
        from ..providers.copilot_runtime import copilot_managed
        offered = COPILOT_SESSION_MODES + (() if copilot_managed() and not free_now
                                           else ((COPILOT_FREE, COPILOT_FREE_LABEL),))
        chosen = self._change_setting('Change Permission Mode',
                                      f'Mode for {info.title}, now {now_name}:',
                                      offered, '' if blocked else now)
        if chosen is None:
            return
        folder = record.cwd
        if chosen == COPILOT_FREE:
            answer = wx.MessageBox(copilot_free_warning(folder), 'Run Freely in This Folder',
                                   wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING, self)
            if answer != wx.YES:
                self._feedback(f'{info.title} still asks before Copilot acts.')
                return
        else:
            # Off at once, here, not on the worker: a message sent while the
            # switch is on its way mustn't be answered for you (Copilot's
            # review of #426). Turning it off waits on nothing.
            try:
                self.sessions.owned_action('set_free', record, False)
            except Exception:  # noqa: BLE001 - the chat's gone; nothing runs freely
                pass
        mode = AGENT_MODE if chosen == COPILOT_FREE else chosen
        def change():
            # Agent mode confirmed first, always asked of Copilot, never taken
            # from the window's copy of the state; running freely goes on only
            # then. Its refusal is said after the mode it's in.
            result = self.sessions.owned_action('set_mode', record, mode)
            refused = ''
            if chosen == COPILOT_FREE and result == AGENT_MODE:
                try:
                    self.sessions.owned_action('set_free', record, True)
                except Exception as exc:  # noqa: BLE001 - said, with the mode
                    refused = str(exc) or "It couldn't be set to run freely."
            return result, refused
        pending.add(info.key)
        # Turning running freely off is said whatever else happens: it went
        # off at once, above.
        ended = (' It no longer runs freely; Copilot asks before it acts.'
                 if free_now and chosen != COPILOT_FREE else '')
        def done(outcome, error):
            pending.discard(info.key)
            result, refused = outcome if not error else (outcome, '')
            if error:
                self._feedback((result if isinstance(result, str) and result
                                else "Copilot couldn't switch modes.") + ended)
            elif chosen == COPILOT_FREE and result == AGENT_MODE and not refused:
                moved = getattr(self._codex_states.get(info.key), 'location_changed', False)
                self._feedback(f'{info.title} runs freely in {folder}. It still asks before '
                               'anything outside it. Change Permission Mode turns it off.'
                               + (" Its commands ask for now, since a command you allowed "
                                  "earlier may have moved Copilot's shell; file changes are "
                                  "allowed for you." if moved else ''))
            elif chosen == COPILOT_FREE and result == AGENT_MODE:
                self._feedback(f'{info.title}: agent mode. {refused}')
            elif result == mode:
                self._feedback(f'{info.title}: {copilot_mode_name(chosen)}.{ended}')
            else:
                self._feedback(f"Copilot didn't switch {info.title} to "
                               f"{copilot_mode_name(mode)}; it's in "
                               f"{copilot_mode_name(result)}."
                               + (" It doesn't run freely." if chosen == COPILOT_FREE else ended))
            self._update_send_state()
            if self._open is not None and self._open.key == info.key:
                self._update_heading()
        self._codex_work(change, done)

    def _keep_first_message(self, message, provider='codex'):
        """New Session is gone when Codex refuses, so keep its first message
        somewhere reachable and say where, first, before the reason (#257).
        Only an editable chat shows the message box; otherwise it would be
        hidden and overwritten by the next chat opened, so use the clipboard."""
        info = self._open
        if self._can_edit(info):
            self._give_back(info.cli_session_id, message, True)
            self._save_draft()
            return (f'No {provider_name(provider)} chat was started. Your first message is in the '
                    f'message box of {info.title}.')
        if self._copy_text(message):
            return (f'No {provider_name(provider)} chat was started. Your first message is copied '
                    'to the clipboard.')
        return f"No {provider_name(provider)} chat was started, and your first message couldn't be kept."

    def _change_codex_permissions(self, info, after=None):
        """``after()`` runs once the change is finished, refused or cancelled,
        so File, Chat Permissions can show the new mode (#329)."""
        after = after or (lambda: None)
        if not info.is_own or not self.sessions.can_create('codex'):
            self._feedback('Only Codex chats started here can change permissions here.')
            after()
            return
        record = self.store.get(info.cli_session_id, provider='codex')
        if record is None or info.key in self._codex_policy_pending:
            self._feedback('Codex permissions are unavailable or already being changed.')
            after()
            return
        from .permission_hosts import CODEX_REVIEW_MODES
        choices = CODEX_REVIEW_MODES
        current = (record.provider_options.get('sandbox', 'read-only'),
                   record.provider_options.get('approval_policy', 'on-request'),
                   record.provider_options.get('approvals_reviewer', 'user'))
        selected = self._change_setting('Change Permission Mode',
            codex_permission_prompt(self, info.title, record.cwd),
            choices, current)
        if selected is None:
            after()
            return
        if len(selected) == 2:
            selected += ('user',)
        before_cwd, before_options = record.cwd, dict(record.provider_options)
        earlier = self._codex_states.get(info.key)
        earlier_problem = earlier.problem if earlier is not None else ''
        self._codex_policy_pending.add(info.key)
        self._feedback('Verifying Codex permissions.')
        def failure(fallback):
            # The runtime's update for a latched problem arrives first (both
            # come through wx.CallAfter, in order) but is not spoken while the
            # change is pending; say it once here, ending with what Stop does.
            # A problem from before this change is not replayed for a refusal.
            self._codex_policy_pending.discard(info.key)
            state = self._codex_states.get(info.key)
            problem = state.problem if state is not None else ''
            text = problem if problem and problem != earlier_problem else fallback
            if problem and 'use stop' not in text.lower():
                text += ' ' + STOP_TO_RECHECK
            self._feedback(text)
        def prepared(change, error):
            if error:
                failure(change or 'Could not change Codex permissions. Wait for an idle chat, or use Stop '
                        f'({key_name("Ctrl+Period")}) to check its status.')
                after()
                return
            proposed = change.record()
            saved = self.store.get(record.cli_session_id, 'codex') is record and self._store_write(self.store.update, record.cli_session_id, provider='codex',
                                      cwd=proposed.cwd, provider_options=proposed.provider_options)
            if not saved:
                # Store.update mutates its record before writing. A failed
                # save must not make Stop use an unpersisted broader grant.
                record.cwd, record.provider_options = before_cwd, before_options
            def finished(_result, failed):
                state = self._codex_states.get(info.key)
                # A problem that arrived meanwhile also stops the new mode.
                if failed or not saved or (state is not None and state.problem):
                    failure('Changed Codex permissions could not be saved or confirmed. Use Stop to recheck the saved permissions before sending.')
                else:
                    self._codex_policy_pending.discard(info.key)
                    label = next(label for value, label in choices if value == selected)
                    self._feedback(f'Codex permissions for {info.title}: {label}.')
                self._update_send_state()
                self.refresh_sessions()
                after()
            self._codex_work(lambda: self.sessions.owned_action('finish_policy', record, change, saved), finished)
        self._codex_work(lambda: self.sessions.owned_action('prepare_policy', record, *selected), prepared)

    def _prepare_codex_owned(self, generation):
        info = self._open
        if info is None or self._codex_preparing.get(info.key) != generation:
            return
        # A runtime chat's key starts with its provider ("codex:<id>"); some
        # callers pass a bare object with only a key and an id.
        provider = getattr(info, 'provider', '') or info.key.split(':', 1)[0]
        record = self.store.get(info.cli_session_id, provider=provider)
        if record is None or info.key in self._codex_preparation_running:
            return
        key = info.key
        self._codex_preparation_running.add(key)
        def prepare():
            self.sessions.owned_action('prepare', record)
        def done(_result, error):
            self._codex_preparation_running.discard(key)
            self._codex_preparing.pop(key, None)
            from ..providers.copilot_runtime import MODE_PROBLEMS
            state = self._codex_states.get(key)
            problem = getattr(state, 'problem', '')
            if not error and provider == 'copilot' and problem in MODE_PROBLEMS:
                # Not said while the chat was being opened: Copilot may
                # have put it back in its mode by the end (#424).
                self._say(f'{record.title}: {problem}', interrupt=False)
            # A reply sent here from another chat waited for this (#248);
            # RelayChats provides it, so a window without that sends none.
            relay = getattr(self, '_relay_prepared', None)
            relayed = relay(key, record, error) if relay is not None else ''
            if error:
                if isinstance(_result, str) and _result.startswith(
                        (CodexTurnRejected.REASONS['sandbox_unavailable'],
                         CodexTurnRejected.REASONS['auto_review_unavailable'])):
                    # A saved chat whose sandbox or automatic review could
                    # not be confirmed: once that is fixed, Stop opens it
                    # again without sending anything (#257) (#390).
                    _result += f' Once that\'s fixed, use Stop ({key_name("Ctrl+Period")}) to open this chat again.'
                name = provider_name(provider)
                self._feedback(relayed + (_result or f'Could not prepare this {name} chat. Automatic resume is disabled while its turn status is uncertain. Check the chat in {name} before restarting The Chat Place.'))
            self._update_send_state()
        self._codex_work(prepare, done)

    def _send_codex_owned(self, record, text, announce_send=True, restore=True, on_result=None):
        """``announce_send`` and ``restore`` off: an agent conversation (#248)
        says what was sent, and keeps the text if it fails (``on_result``)."""
        key = record.to_info().key
        self._codex_send_pending[key] = self._codex_send_pending.get(key, 0) + 1
        def done(queued, error):
            remaining = self._codex_send_pending[key] - 1
            if remaining:
                self._codex_send_pending[key] = remaining
            else:
                del self._codex_send_pending[key]
            if error and not restore:
                pass
            elif error:
                self._restore_codex_text(key, (text,))
                self._feedback(queued or f'{provider_name(record.provider)} could not confirm this send. Check the chat history before sending again.')
            elif not announce_send:
                pass
            else:
                formatter = announce.queued_text if queued else announce.sent_text
                self._feedback(formatter(record.title, text, self.speech.announce,
                                         self.speech.announce_own))
            if not remaining:
                for state in self._codex_delayed_updates.pop(key, ()):
                    self._on_owned_update(record.provider, state)
            self._update_send_state()
            if on_result is not None:
                on_result(queued, error)
        self._codex_work(lambda: self.sessions.owned_action('send', record, text), done)

    def _steer_codex_owned(self, record, text, index=None):
        """Steer (#239): ``text`` into the running turn, or the queued message
        at ``index``. Said once Codex has accepted it. Refused (the turn has
        just ended, or another steer is on its way): the text comes back to
        the reply box, or a queued message stays queued, with why. Never sent
        as a new turn without Kelly choosing to."""
        key = record.to_info().key
        name = provider_name(record.provider)
        self._steer_row(key, text, f'waiting for {name}')

        accepted = []  # the turn Codex took it into

        def action():
            try:
                if index is None:
                    turn = self.sessions.owned_action('steer', record, text)
                else:
                    turn = self.sessions.owned_action('steer_queued', record, index, text)
            except (CodexSteerRefused, CodexTurnRejected) as exc:
                # ``returned``: a queued one that Stop sent back to the box.
                return (safe_reason(exc) or f'{name} could not take it.',
                        getattr(exc, 'returned', False))
            if turn:
                accepted.append(turn)
            return None

        def done(refused, error):
            # Accepted, it stays until the chat's history has it; otherwise
            # it's back with you, in the reply box or the queue.
            self._steer_status(key, text, None if error or refused else f'accepted by {name}')
            state = self._codex_states.get(key)
            finished = bool(accepted) and (record.provider, record.cli_session_id,
                                           accepted[0]) in self._codex_finished
            if not (error or refused) and (state is None or state.outcome != 'inProgress'
                                           or finished):
                # Accepted into a turn known to have ended (Stop, say), even if
                # a queued message's turn has started since: it may never be
                # used, so the history read next decides where it goes.
                self._steers_turn_over(key)
            if error:
                # Not known whether it arrived: back in the box, never resent.
                if index is None:
                    self._restore_codex_text(key, (text,))
                self._feedback(f'{name} could not confirm your message. Check the chat '
                               'history before sending again.')
            elif refused:
                reason, returned = refused
                if index is None:
                    self._restore_codex_text(key, (text,))
                    self._feedback(f'{reason} Your message is back in the reply box.')
                elif returned:
                    self._feedback(f'{reason} It is back in the reply box.')
                else:
                    self._feedback(f'{reason} It stays queued.')
            else:
                self._feedback(announce.steered_text(record.title, text, self.speech.announce,
                                                     index is None and self.speech.announce_own,
                                                     accepted=True))
            self._update_send_state()
        self._codex_work(action, done)

    def _stop_codex_owned(self, info, take_back=None):
        """``take_back(texts)``: an agent conversation (#369) takes back the
        waiting messages that were its own, and returns the rest."""
        record = self.store.get(info.cli_session_id, provider=info.provider)
        if record is None:
            return
        name = provider_name(record.provider)
        state = self._codex_states.get(info.key)
        recover_idle = (record.provider == 'codex' and state is not None
                        and state.outcome in ('idle', 'completed', 'failed', 'interrupted'))
        # Copilot's Stop always goes to the runtime, never decided from the
        # window's state: that can lag behind it, as when a turn has ended and
        # the next waiting message is starting. Stopping an idle chat costs
        # nothing, and the message is worded from what the runtime found
        # (Kelly, #372, #386).
        def done(result, error):
            if error:
                self._feedback(result or f'Could not stop or recover this {name} chat. Check its state in {name}.')
                self._update_send_state()
                return
            reconnected, waiting, stopped = result
            if waiting and take_back is not None:
                waiting = tuple(take_back(waiting))
            if waiting:
                # Copilot's stop gives back the messages still waiting; Codex
                # gives them back through its state's unsent instead.
                self._restore_codex_text(info.key, waiting)
            back = (f' {len(waiting)} waiting {"message is" if len(waiting) == 1 else "messages are"} '
                    'back in the message box, unsent.') if waiting else ''
            if reconnected == 'permissions':
                text = f'Saved {name} permissions rechecked. You can send again. No message was sent.'
            elif reconnected == 'rechecked':
                text = f'{name} chat rechecked. You can send again. No message was sent.'
            elif reconnected:
                text = f'{name} chat reconnected after verifying its previous turn ended. No message was resent.'
            elif record.provider == 'codex':
                text = f'Stop requested. If the turn does not finish, press Stop again to check whether {name} has ended it.'
            elif stopped is not None and stopped.answered:
                # Copilot's prompt has returned, which needn't be an answer: a
                # cancel, a refusal or its process ending return it too. Only
                # the checks after it say which, so this doesn't (Kelly, #386).
                text = f'{record.title}: The Chat Place is checking how {name}\'s turn ended'
                text += '.' if waiting else ', so there\'s nothing to stop.'
            elif stopped is not None and getattr(stopped, 'before_sending', False):
                # Stopped before its message went: that message comes back
                # unsent with the turn's end, which says where (#405).
                text = f'{record.title}: Stopped before your message went to {name}.'
            elif stopped is not None and getattr(stopped, 'already_asked', False):
                # Stop pressed again: the first Stop's timer still runs, so
                # promising a fresh grace period would be wrong (#405).
                text = (f'{record.title}: Already asked {name} to stop; '
                        "The Chat Place closes it if it doesn't.")
            elif stopped is not None and not stopped.cancelled:
                # The turn ended meanwhile, or Copilot's process has gone and
                # the turn is ending with it (a cancel that couldn't go out,
                # #405): either way it isn't answering.
                text = f'{record.title}: {name} isn\'t answering'
                text += '.' if waiting else ', so there\'s nothing to stop.'
            else:
                from ..providers.copilot_runtime import CANCEL_GRACE_SECONDS
                text = (f'Stop requested. If {name} does not stop within '
                        f'{CANCEL_GRACE_SECONDS:g} seconds, The Chat Place closes it.')
            self._feedback(text + back)
            self._update_send_state()
        def recover():
            # Stop is explicit intent to recover, unlike selecting a chat.
            # Reopen checks both local reservations and native turn history
            # before resuming, and never resends an uncertain message. Codex
            # says which check it made, so Stop announces that one (#385).
            if record.provider == 'codex':
                return self.sessions.owned_action('recover', record) or 'resumed', (), None
            self.sessions.owned_action('reopen', record)
            return 'resumed', (), None
        def stop_or_recover():
            try:
                stopped = self.sessions.owned_action('stop', record)
            except CodexRpcError:
                return recover()
            # Outside the try: a failed recheck must not run a second time.
            if recover_idle and not self.sessions.owned_action('ready', record):
                return recover()
            if record.provider == 'codex':
                return False, (), None
            if hasattr(stopped, 'cancelled'):  # copilot_runtime.Stopped
                return False, tuple(stopped.waiting), stopped
            # A provider that gives back only the waiting messages, or
            # nothing, is taken to have cancelled.
            return False, (tuple(stopped) if isinstance(stopped, tuple) else ()), None
        self._codex_work(stop_or_recover, done)

    def _usage_check_slot(self, provider):
        """Each provider's own pending/again pair, so one provider's check
        never replaces another's. Codex keeps the attributes it always had."""
        if provider == 'codex':
            return None
        if not hasattr(self, '_runtime_usage_checks'):
            self._runtime_usage_checks = {}
        return self._runtime_usage_checks.setdefault(provider, {'pending': False, 'again': None})

    def _check_codex_usage(self, info):
        read = getattr(self.sessions, 'usage_warnings', None)
        if not callable(read):
            return
        provider = getattr(info, 'provider', 'codex')
        slot = self._usage_check_slot(provider)
        if slot is not None:
            if slot['pending']:
                slot['again'] = info
                return
            slot['pending'], slot['again'] = True, None
        else:
            if getattr(self, '_codex_usage_check_pending', False):
                self._codex_usage_check_again = info
                return
            self._codex_usage_check_pending = True
            self._codex_usage_check_again = None
        def done(warnings, error):
            if slot is not None:
                again, slot['pending'], slot['again'] = slot['again'], False, None
            else:
                self._codex_usage_check_pending = False
                again = self._codex_usage_check_again
                self._codex_usage_check_again = None
            if not error:
                if not hasattr(self, '_warned'):
                    self._warned = set()
                for warning in warnings:
                    key = (f'{provider}-account', warning.key)
                    if key not in self._warned:
                        self._warned.add(key)
                        self._say(warning.text, interrupt=False)
            # A second chat can finish after the first report was sampled.
            # Coalesce that request without overlapping queries or losing it.
            if again is not None:
                self._check_codex_usage(again)
        self._codex_work(lambda: read(info), done)

    def _on_codex_update(self, state):
        self._on_owned_update('codex', state)

    def _on_owned_update(self, provider, state):
        """A runtime's state for one of its chats (on the UI thread)."""
        if self._gone():
            return
        name = provider_name(provider)
        key = f'{provider}:' + state.native_id
        if self._codex_send_pending.get(key):
            updates = self._codex_delayed_updates.setdefault(key, [])
            # Keep terminal transitions and unsent text, but replace progressive
            # snapshots while the send result is still returning to the UI.
            if (updates and not state.unsent and not updates[-1].unsent
                    and (updates[-1].turn_id, updates[-1].outcome) == (state.turn_id, state.outcome)):
                updates[-1] = state
            else:
                updates.append(state)
            return
        previous = self._codex_states.get(key)
        self._codex_states[key] = state
        if getattr(self, '_steered', {}).get(key) and state.outcome != 'inProgress':
            # The turn is over. An accepted steer may not have been used
            # (Stop): it stays until the history is read again (#410).
            self._steers_turn_over(key)
        unsent = state.unsent
        if unsent:
            # An agent conversation's own message goes back to it (#248).
            agent_unsent = getattr(self, '_agent_unsent', None)
            if agent_unsent is not None:
                unsent = agent_unsent(provider, state.native_id, unsent)
        self._restore_codex_text(key, unsent)
        record = self.store.get(state.native_id, provider=provider)
        if record is None:
            return
        # Run freely in this folder (copilot_folder): each request answered
        # for you goes on the status bar, so what was allowed can be read.
        said_before = (previous.auto_allowed[-1][0]
                       if previous is not None and getattr(previous, 'auto_allowed', ()) else 0)
        for number, summary in getattr(state, 'auto_allowed', ()):
            if number > said_before:
                self._status(f'{record.title}: allowed for you: {summary}')
        # Once a command you allowed may have moved the shell, commands ask
        # again: said once, when it happens, while the chat runs freely.
        if (getattr(state, 'free', False) and getattr(state, 'location_changed', False)
                and not getattr(previous, 'location_changed', False)):
            self._say(f"{record.title}: commands ask again, since the one you allowed may have "
                      "moved Copilot's shell. File changes in its folder are still allowed.",
                      interrupt=False)
        loaded = self._open is not None and self._open.key == key
        seen = getattr(self, '_codex_permission_notices_seen', None)
        if seen is None:
            seen = self._codex_permission_notices_seen = set()
        for notice in getattr(state, 'permission_notices', ()):
            identity = (provider, state.native_id, notice[0], notice[1])
            if identity not in seen:
                seen.add(identity)
                self._say(f'{record.title}: {notice[2]}', interrupt=False)
        if loaded and (state.token_usage is not None or previous is not None and previous.token_usage is not None):
            self._check_context()
        if (state.turn_id and state.outcome == 'inProgress' and not state.problem
                and (previous is None or previous.turn_id != state.turn_id)):
            self._status(f'{record.title}: {name} is working.')
        from ..providers.copilot_runtime import MODE_PROBLEMS
        if (state.problem and (previous is None or previous.problem != state.problem)
                and key not in getattr(self, '_codex_policy_pending', ())
                and not (provider == 'copilot' and state.problem in MODE_PROBLEMS
                         and key in self._codex_preparing)):
            # A pending permission change says its own failure once (#385).
            # A Copilot chat being opened may be switched back to its mode
            # yet: its preparation's done() says a mode problem still left
            # (#424).
            self._say(f'{record.title}: {state.problem}', interrupt=False)
        if state.notice and (previous is None or previous.notice != state.notice):
            self._say(f'{record.title}: {state.notice}', interrupt=False)
        before_mode, mode = getattr(previous, 'mode', ''), getattr(state, 'mode', '')
        if (provider == 'copilot' and before_mode and mode and before_mode != mode
                and key not in getattr(self, '_copilot_mode_pending', ())
                and key not in self._codex_preparing
                and getattr(state, 'problem', '') not in MODE_PROBLEMS):
            # Copilot switched modes itself (Shift+Tab in Copilot CLI, or a
            # current_mode_update mid-turn): said once, as nobody here asked.
            # A switch asked for with Change Permission Mode says its own; a
            # chat being opened, which may be switched back, and a mode
            # problem, which is said instead, say nothing of it here.
            self._say(f'{record.title}: Copilot switched to {copilot_mode_name(mode)}.',
                      interrupt=False)
            if loaded:
                self._update_heading()
        if state.requests:
            agent_asked = getattr(self, '_agent_member_asked', None)
            if agent_asked is not None:
                agent_asked(provider, state.native_id)
        if state.requests and not state.problem and (previous is None or previous.requests != state.requests):
            request = state.requests[0]
            summary = ' '.join(request.summary().split())[:200]
            self._say(f'{record.title}: {name} needs your answer. {summary} {key_name("Ctrl+Shift+A")} answers.', interrupt=False)
            self._notify(key, f'{record.title} needs you', summary, needs_you=True)
        if loaded and state.plan_received and provider == 'codex':  # plans are Codex's own
            self._apply_tasks(self._codex_task_steps(state, self._chat_turns),
                              first_load=previous is None)
        finished = (provider, state.native_id, state.turn_id)
        fresh_terminal = (state.turn_id and state.outcome in ('completed', 'failed', 'interrupted')
                          and finished not in self._codex_finished)
        if fresh_terminal:
            self._codex_finished.add(finished)
            self._store_write(self.store.update, state.native_id, provider=provider,
                              state=IDLE, unread=not loaded,
                              last_activity_ms=int(time.time() * 1000))
            reply = '\n\n'.join(text for _identity, text in state.replies)
            text = (f'{record.title}: {name} answered.' if state.outcome == 'completed'
                    else f'{record.title}: {name} turn {state.outcome}.')
            # Use the same configured announcement level as Claude replies.
            if reply:
                text = announce.reply_text(record.title, reply, self.speech.announce)
            agent_quiet = getattr(self, '_agent_quiet', None)
            quiet = agent_quiet is not None and agent_quiet(provider, state.native_id)
            if not quiet:  # an agent conversation says it, by its own name (#248)
                self._say(text, interrupt=False)
            if state.outcome != 'interrupted' and not quiet:
                self._notify(key, f'{record.title}: {name} ' + ('answered' if state.outcome == 'completed' else state.outcome),
                             text, needs_you=state.outcome == 'failed')
            self._check_codex_usage(record.to_info())
        if unsent:
            # Queue this after interrupting error/reply speech, so the news
            # about recovered text is not immediately cut off by that speech.
            # Only what came back here: an agent conversation's own message
            # went back to the conversation, which said so.
            count = len(unsent)
            destination = 'reply box' if loaded else f'draft for {record.title}'
            self._feedback(f'{count} unsent message' + ('s are' if count != 1 else ' is')
                           + f' back in the {destination}.')
        if fresh_terminal:
            agent_ended = getattr(self, '_agent_turn_ended', None)
            if agent_ended is not None:
                agent_ended(provider, state.native_id, reply, state.outcome != 'completed',
                            more_queued=bool(state.queued),
                            detail=state.problem or '',
                            files=tuple(getattr(state, 'changed_paths', ())))
        if loaded:
            if fresh_terminal:
                # A verified turn ended even if second-resolution catalog
                # metadata is unchanged. Bypass the reader's history cache and
                # reject any older in-flight read before loading the new turn.
                self._open_generation += 1
                self._reader = None
                # Then say what the turn changed in files, as for a Claude
                # turn, where the provider can tell.
                capabilities = getattr(self.sessions, 'capabilities', None)
                try:
                    # Not for an agent conversation's member turn, which the
                    # conversation announces by its own name (#248, #369).
                    if (not quiet and capabilities is not None
                            and getattr(capabilities(self._open), 'changes', False)):
                        self._changes_due = True
                except ValueError:
                    pass  # its provider has gone: nothing to say
            self._open.state = state.state
            self._update_heading()
            self._update_send_state()
            self._rebuild_chat_list()
            if state.outcome != 'inProgress':
                self._refresh_chat()
        self.refresh_sessions()

    def _answer_codex_owned(self, info, state):
        record = self.store.get(info.cli_session_id, provider=info.provider)
        if record is None or not state.requests:
            return
        request = state.requests[0]
        # Only Codex can offer to keep an approval for the chat (#307).
        if request.method != QUESTIONS and info.provider == 'codex':
            pending = getattr(self, '_codex_answer_checks', None)
            if pending is None:
                pending = self._codex_answer_checks = set()
            if info.key in pending:
                self._feedback(f'Still checking this {provider_name(info.provider)} request.')
                return
            pending.add(info.key)
            def checked(rule, error):
                pending.discard(info.key)
                if not self._gone():
                    self._show_codex_answer(info, record, request, state, None if error else rule)
            self._codex_work(lambda: self.sessions.owned_action('chat_rule_candidate', record, request), checked)
            return
        self._show_codex_answer(info, record, request, state, None)

    def _show_codex_answer(self, info, record, request, state, chat_rule):
        if request.method == QUESTIONS:
            questions = [dict(id=q.question_id, header=q.header, question=q.text,
                              options=[dict(label=l, description=d) for l, d in q.options],
                              isOther=q.allow_other, isSecret=q.secret) for q in request.questions]
            # What it wrote in this turn before asking goes with the
            # question (#415).
            said = next((text for reply_id, text in reversed(state.replies)
                         if reply_id != 'display-limit'), '')
            dialog = QuestionDialog(self, info.title, SimpleNamespace(questions=lambda: questions),
                                    provider=provider_name(info.provider), said=said)
        else:
            detail = dict(state.file_changes).get(request.item_id, '')
            dialog = CodexPermissionDialog(self, info.title, request, detail, chat_rule=chat_rule,
                                           provider=provider_name(info.provider))
        keep = False
        choice = ''
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            if request.method == QUESTIONS:
                if dialog.declined:
                    self._stop_codex_owned(info)
                    return
                result = request.question_answer(dialog.answers())
            else:
                choice = dialog.choice
                keep = choice == 'tcp_chat_rule'
                result = None if keep else request.approval_answer(choice)
        except ValueError:
            self._feedback(f'These {provider_name(info.provider)} answers are invalid. Open Answer and choose again.')
            return
        finally:
            dialog.Destroy()
        def done(_result, error):
            name = provider_name(info.provider)
            self._feedback((_result or f'This {name} request could not be answered. It may no longer be waiting.')
                           if error else f'Approval kept for this chat and answer sent to {name}.' if keep else f'Answer sent to {name}.')
            agent_answered = getattr(self, '_agent_member_answered', None)
            if not error and agent_answered is not None:
                # An approval or an answer lets an agent conversation's
                # automatic hand-offs go on; a refusal pauses them (#248).
                allowed = keep or request.method == QUESTIONS or (
                    choice not in ('decline', 'cancel', 'reject_once')
                    and (result or {}).get('decision') not in ('decline', 'cancel'))
                # An approval with another request still waiting counts once
                # that one is answered too.
                # Any other request waiting now (not when the dialog opened;
                # and the one answered may or may not have left the state yet).
                current = self._codex_states.get(info.key, state)
                others = any(r.request_id != request.request_id
                             for r in (current.requests or ()))
                if not allowed or not others:
                    agent_answered(info.provider, info.cli_session_id, allowed)
        action = (lambda: self.sessions.owned_action('allow_for_chat', record, request)) if keep else (
            lambda: self.sessions.owned_action('answer', record, request, result))
        self._codex_work(action, done)
