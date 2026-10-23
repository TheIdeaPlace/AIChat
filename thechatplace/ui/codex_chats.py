"""UI-thread callbacks for owned Codex chats; all backend actions use workers."""
import time
from dataclasses import replace
from types import SimpleNamespace

import wx

from ..providers.codex_requests import QUESTIONS
from ..providers.codex_rpc import CodexRpcError, CodexTurnRejected
from ..sessions import IDLE, WORKING, STATUS_UNAVAILABLE
from .. import announce
from ..claude_cli import describe_elapsed
from ..codex_diagnostics import record_failure, safe_reason
from ..ui_text import key_name
from .dialogs import CodexPermissionDialog, QuestionDialog, codex_permission_prompt
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
    def _codex_status_text(state):
        if state is None:
            return 'No Codex turn is running in this chat.'
        if state.problem:
            return state.problem
        if state.state == STATUS_UNAVAILABLE:
            return 'Codex status is unavailable. Reload this chat to check its history.'
        if state.requests:
            text = f'Codex is waiting for you. {key_name("Ctrl+Shift+A")} answers.'
            if state.activity:
                text += ' Latest activity: ' + state.activity
        elif state.outcome == 'inProgress':
            text = state.activity or 'Codex is working.'
        elif state.state == WORKING:
            text = 'Starting a Codex turn.'
        else:
            text = 'No Codex turn is running in this chat.'
        if state.started_at and state.outcome == 'inProgress':
            text += ' The turn has run for ' + describe_elapsed(time.monotonic() - state.started_at) + '.'
        if state.queued:
            count = len(state.queued)
            text += f' {count} {"message" if count == 1 else "messages"} queued.'
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

    def _create_codex_owned(self, folder, title, message, source=None, model='', effort='', sandbox=None, approval_policy=None):
        options = {key: value for key, value in (('model', model), ('effort', effort),
                   ('sandbox', sandbox), ('approval_policy', approval_policy)) if value}
        def create():
            return (self.sessions.fork_owned(source, title, **options) if source is not None
                    else self.sessions.create_owned('codex', folder, title, **options))
        def done(record, error):
            if error:
                text = record or 'Could not create this Codex chat. Check Codex sign-in and configuration.'
                if message:
                    text = self._keep_first_message(message) + ' ' + text
                self._feedback(text)
                return
            if not self._store_write(self.store.add, record):
                self._feedback('Codex created the chat, but it could not be saved here. Open it in Codex.')
                return
            self._codex_drafts[record.to_info().key] = message
            self.open_session(record.to_info())
            self.refresh_sessions()
            if message:
                self.reply_text.SetValue('')
                self._codex_drafts.pop(record.to_info().key, None)
                self._send_codex_owned(record, message)
        self._codex_work(create, done)

    def _keep_first_message(self, message):
        """New Session is gone when Codex refuses, so keep its first message
        somewhere reachable and say where, first, before the reason (#257).
        Only an editable chat shows the message box; otherwise it would be
        hidden and overwritten by the next chat opened, so use the clipboard."""
        info = self._open
        if self._can_edit(info):
            self._give_back(info.cli_session_id, message, True)
            self._save_draft()
            return f'No Codex chat was started. Your first message is in the message box of {info.title}.'
        if self._copy_text(message):
            return 'No Codex chat was started. Your first message is copied to the clipboard.'
        return "No Codex chat was started, and your first message couldn't be kept."

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
        choices = CODEX_MODES
        current = (record.provider_options.get('sandbox', 'read-only'),
                   record.provider_options.get('approval_policy', 'on-request'))
        selected = self._change_setting('Change Permission Mode',
            codex_permission_prompt(self, info.title, record.cwd),
            choices, current)
        if selected is None:
            after()
            return
        before_cwd, before_options = record.cwd, dict(record.provider_options)
        self._codex_policy_pending.add(info.key)
        self._feedback('Verifying Codex permissions.')
        def prepared(change, error):
            if error:
                self._codex_policy_pending.discard(info.key)
                self._feedback(change or 'Could not change Codex permissions. Wait for an idle chat or reload to check its status.')
                after()
                return
            proposed = change.record()
            saved = self.store.get(record.cli_session_id, 'codex') is record and self._store_write(self.store.update, record.cli_session_id, provider='codex',
                                      cwd=proposed.cwd, provider_options=proposed.provider_options)
            if not saved:
                # Store.update mutates its record before writing. A failed
                # save must not make Reload use an unpersisted broader grant.
                record.cwd, record.provider_options = before_cwd, before_options
            def finished(_result, failed):
                self._codex_policy_pending.discard(info.key)
                if failed or not saved:
                    self._feedback('Changed Codex permissions could not be saved or confirmed. Reload before sending.')
                else:
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
        record = self.store.get(info.cli_session_id, provider='codex')
        if record is None or info.key in self._codex_preparation_running:
            return
        key = info.key
        self._codex_preparation_running.add(key)
        def prepare():
            self.sessions.owned_action('prepare', record)
        def done(_result, error):
            self._codex_preparation_running.discard(key)
            self._codex_preparing.pop(key, None)
            # A reply sent here from another chat waited for this (#248);
            # RelayChats provides it, so a window without that sends none.
            relay = getattr(self, '_relay_prepared', None)
            relayed = relay(key, record, error) if relay is not None else ''
            if error:
                if isinstance(_result, str) and _result.startswith(CodexTurnRejected.REASONS['sandbox_unavailable']):
                    # A saved folder-editing chat reopened read-only: reloading
                    # it after fixing the sandbox prepares it again (#257).
                    _result += ' Reload this chat to try again.'
                self._feedback(relayed + (_result or 'Could not prepare this Codex chat. Automatic resume is disabled while its turn status is uncertain. Check the chat in Codex before restarting The Chat Place.'))
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
                self._feedback(queued or 'Codex could not confirm this send. Check the chat history before sending again.')
            elif not announce_send:
                pass
            else:
                formatter = announce.queued_text if queued else announce.sent_text
                self._feedback(formatter(record.title, text, self.speech.announce,
                                         self.speech.announce_own))
            if not remaining:
                for state in self._codex_delayed_updates.pop(key, ()):
                    self._on_codex_update(state)
            self._update_send_state()
            if on_result is not None:
                on_result(queued, error)
        self._codex_work(lambda: self.sessions.owned_action('send', record, text), done)

    def _stop_codex_owned(self, info):
        record = self.store.get(info.cli_session_id, provider='codex')
        if record is None:
            return
        def done(_result, error):
            self._feedback((_result or 'Could not stop or recover this Codex chat. Check its state in Codex.') if error
                           else 'Codex chat reconnected after verifying its previous turn ended. No message was resent.' if _result
                           else 'Stop requested. If the turn does not finish, press Stop again to check whether Codex has ended it.')
            self._update_send_state()
        def stop_or_recover():
            try:
                self.sessions.owned_action('stop', record)
                return False
            except CodexRpcError:
                # Stop is explicit intent to recover, unlike selecting a chat.
                # Reopen checks both local reservations and native turn history
                # before resuming, and never resends an uncertain message.
                self.sessions.owned_action('reopen', record)
                return True
        self._codex_work(stop_or_recover, done)

    def _check_codex_usage(self, info):
        read = getattr(self.sessions, 'usage_warnings', None)
        if not callable(read):
            return
        if getattr(self, '_codex_usage_check_pending', False):
            self._codex_usage_check_again = info
            return
        self._codex_usage_check_pending = True
        self._codex_usage_check_again = None
        def done(warnings, error):
            self._codex_usage_check_pending = False
            again = self._codex_usage_check_again
            self._codex_usage_check_again = None
            if not error:
                if not hasattr(self, '_warned'):
                    self._warned = set()
                for warning in warnings:
                    key = ('codex-account', warning.key)
                    if key not in self._warned:
                        self._warned.add(key)
                        self._say(warning.text, interrupt=False)
            # A second chat can finish after the first report was sampled.
            # Coalesce that request without overlapping queries or losing it.
            if again is not None:
                self._check_codex_usage(again)
        self._codex_work(lambda: read(info), done)

    def _on_codex_update(self, state):
        if self._gone():
            return
        key = 'codex:' + state.native_id
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
        unsent = state.unsent
        if unsent:
            # An agent conversation's own message goes back to it (#248).
            agent_unsent = getattr(self, '_agent_unsent', None)
            if agent_unsent is not None:
                unsent = agent_unsent('codex', state.native_id, unsent)
        self._restore_codex_text(key, unsent)
        record = self.store.get(state.native_id, provider='codex')
        if record is None:
            return
        loaded = self._open is not None and self._open.key == key
        seen = getattr(self, '_codex_permission_notices_seen', None)
        if seen is None:
            seen = self._codex_permission_notices_seen = set()
        for notice in getattr(state, 'permission_notices', ()):
            identity = (state.native_id, notice[0], notice[1])
            if identity not in seen:
                seen.add(identity)
                self._say(f'{record.title}: {notice[2]}', interrupt=False)
        if loaded and (state.token_usage is not None or previous is not None and previous.token_usage is not None):
            self._check_context()
        if (state.turn_id and state.outcome == 'inProgress' and not state.problem
                and (previous is None or previous.turn_id != state.turn_id)):
            self._status(f'{record.title}: Codex is working.')
        if state.problem and (previous is None or previous.problem != state.problem):
            self._say(f'{record.title}: {state.problem}', interrupt=False)
        if state.notice and (previous is None or previous.notice != state.notice):
            self._say(f'{record.title}: {state.notice}', interrupt=False)
        if state.requests:
            agent_asked = getattr(self, '_agent_member_asked', None)
            if agent_asked is not None:
                agent_asked('codex', state.native_id)
        if state.requests and not state.problem and (previous is None or previous.requests != state.requests):
            request = state.requests[0]
            summary = ' '.join(request.summary().split())[:200]
            self._say(f'{record.title}: Codex needs your answer. {summary} {key_name("Ctrl+Shift+A")} answers.', interrupt=False)
            self._notify(key, f'{record.title} needs you', summary, needs_you=True)
        if loaded and state.plan_received:
            self._apply_tasks(self._codex_task_steps(state, self._chat_turns),
                              first_load=previous is None)
        finished = (state.native_id, state.turn_id)
        fresh_terminal = (state.turn_id and state.outcome in ('completed', 'failed', 'interrupted')
                          and finished not in self._codex_finished)
        if fresh_terminal:
            self._codex_finished.add(finished)
            self._store_write(self.store.update, state.native_id, provider='codex',
                              state=IDLE, unread=not loaded,
                              last_activity_ms=int(time.time() * 1000))
            reply = '\n\n'.join(text for _identity, text in state.replies)
            text = (f'{record.title}: Codex answered.' if state.outcome == 'completed'
                    else f'{record.title}: Codex turn {state.outcome}.')
            # Use the same configured announcement level as Claude replies.
            if reply:
                text = announce.reply_text(record.title, reply, self.speech.announce)
            agent_quiet = getattr(self, '_agent_quiet', None)
            quiet = agent_quiet is not None and agent_quiet('codex', state.native_id)
            if not quiet:  # an agent conversation says it, by its own name (#248)
                self._say(text, interrupt=False)
            if state.outcome != 'interrupted' and not quiet:
                self._notify(key, f'{record.title}: Codex ' + ('answered' if state.outcome == 'completed' else state.outcome),
                             text, needs_you=state.outcome == 'failed')
            self._check_codex_usage(record.to_info())
        if state.unsent:
            # Queue this after interrupting error/reply speech, so the news
            # about recovered text is not immediately cut off by that speech.
            count = len(state.unsent)
            destination = 'reply box' if loaded else f'draft for {record.title}'
            self._feedback(f'{count} unsent message' + ('s are' if count != 1 else ' is')
                           + f' back in the {destination}.')
        if fresh_terminal:
            agent_ended = getattr(self, '_agent_turn_ended', None)
            if agent_ended is not None:
                agent_ended('codex', state.native_id, reply, state.outcome != 'completed',
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
            self._open.state = state.state
            self._update_heading()
            self._update_send_state()
            self._rebuild_chat_list()
            if state.outcome != 'inProgress':
                self._refresh_chat()
        self.refresh_sessions()

    def _answer_codex_owned(self, info, state):
        record = self.store.get(info.cli_session_id, provider='codex')
        if record is None or not state.requests:
            return
        request = state.requests[0]
        if request.method != QUESTIONS:
            pending = getattr(self, '_codex_answer_checks', None)
            if pending is None:
                pending = self._codex_answer_checks = set()
            if info.key in pending:
                self._feedback('Still checking this Codex request.')
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
            dialog = QuestionDialog(self, info.title, SimpleNamespace(questions=lambda: questions),
                                    provider='Codex')
        else:
            detail = dict(state.file_changes).get(request.item_id, '')
            dialog = CodexPermissionDialog(self, info.title, request, detail, chat_rule=chat_rule)
        keep = False
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            if request.method == QUESTIONS:
                if dialog.declined:
                    self._stop_codex_owned(info)
                    return
                result = request.question_answer(dialog.answers())
            else:
                keep = dialog.choice == 'tcp_chat_rule'
                result = None if keep else request.approval_answer(dialog.choice)
        except ValueError:
            self._feedback('These Codex answers are invalid. Open Answer and choose again.')
            return
        finally:
            dialog.Destroy()
        def done(_result, error):
            self._feedback((_result or 'This Codex request could not be answered. It may no longer be waiting.')
                           if error else 'Approval kept for this chat and answer sent to Codex.' if keep else 'Answer sent to Codex.')
            agent_answered = getattr(self, '_agent_member_answered', None)
            if not error and agent_answered is not None:
                # An approval or an answer lets an agent conversation's
                # automatic hand-offs go on; a refusal pauses them (#248).
                allowed = keep or request.method == QUESTIONS or (
                    (result or {}).get('decision') not in ('decline', 'cancel'))
                agent_answered('codex', info.cli_session_id, allowed)
        action = (lambda: self.sessions.owned_action('allow_for_chat', record, request)) if keep else (
            lambda: self.sessions.owned_action('answer', record, request, result))
        self._codex_work(action, done)
