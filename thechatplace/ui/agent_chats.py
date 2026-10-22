"""Agent conversations (#248, phase 2): the window's side of agents.py.

A conversation is one row in the session list. Loaded, its messages list is
every member's messages in order (providers/agents.py), and what you type
goes to the member whose turn it is, or to the one you address ("@Codex ...").
When a member answers a message the conversation sent it, its reply is handed
to the other member: after you approve it (Ctrl+Shift+E, the default), or by
itself in automatic mode, up to the turn limit.

Every message goes through each member's own send path, as if you'd typed it
in that chat, so nothing here can answer a question or approve a tool: those
still come to you through Answer (Ctrl+Shift+A). Sends, replies and stops
reach this module from the Claude turn handler and the Codex update handler
(main_frame.py, codex_chats.py), which keep everything else they do.
"""
import time

import wx

from .. import agents, announce
from ..agents import AGENTS, MODE_APPROVE, MODE_AUTO, AgentConversation, Member, Pending
from ..claude_cli import describe_elapsed, new_session_id
from ..own_store import OwnSession
from ..sessions import NEEDS_YOU, WORKING
from ..ui_text import key_name
from .dialogs import AgentConversationDialog, RelayDialog


class AgentChats:
    # -- finding things -----------------------------------------------------------------

    def _agent_conversation(self, info=None):
        info = self._open if info is None else info
        if info is None or info.provider != AGENTS:
            return None
        return self.agents.by_key(info.key)

    def _agent_member_info(self, member):
        record = self.store.get(member.native_id, provider=member.provider)
        return record.to_info() if record is not None else None

    def _agent_quiet(self, provider, native_id) -> bool:
        """A member's turn that the conversation started: the conversation
        announces its reply, by the conversation's name, not the member chat."""
        return (provider, native_id) in self._agent_awaiting

    def _agent_rename_member(self, provider, old_id, new_id):
        """Claude chose another id for a new member chat on its first turn:
        the conversation follows it."""
        conversation = self.agents.containing(provider, old_id)
        if conversation is not None:
            index = conversation.member_index(provider, old_id)
            conversation.members[index].native_id = new_id
            self._agent_save(conversation)
        for table in (self._agent_awaiting, self._agent_last_reply):
            if (provider, old_id) in table:
                table[(provider, new_id)] = table.pop((provider, old_id))
        if (provider, old_id) in self._agent_asked:
            self._agent_asked.discard((provider, old_id))
            self._agent_asked.add((provider, new_id))

    def _agent_send_state(self, info) -> str:
        """The one-line status under the reply box for a conversation."""
        conversation = self._agent_conversation(info)
        if conversation is None:
            return "This agent conversation is gone."
        if conversation.pending is not None:
            frm = conversation.members[conversation.pending.from_index].name
            to = conversation.members[conversation.pending.to_index].name
            return f"{frm}'s reply is waiting to go to {to}. {key_name('Ctrl+Shift+E')} hands it on."
        for member in conversation.members:
            state = self._codex_states.get(member.key) if member.provider == "codex" else None
            if (member.provider == "claude" and self._pending.get(member.native_id)) or (
                    state is not None and state.requests):
                return (f"{member.name} is waiting for you. "
                        f"{key_name('Ctrl+Shift+A')} answers.")
        working = []
        for member in conversation.members:
            if member.provider == "claude" and member.native_id in self._runners:
                working.append(member.name)
            elif member.provider == "codex":
                state = self._codex_states.get(member.key)
                if member.key in self._agent_codex_waiting or (
                        state is not None and (state.outcome == "inProgress"
                                               or state.state == WORKING)):
                    working.append(member.name)
        if working:
            return f"{' and '.join(working)} {'is' if len(working) == 1 else 'are'} working."
        if conversation.paused and conversation.stop_reason:
            return conversation.stop_reason
        return f"Ready. What you type goes to {conversation.members[conversation.next_index].name}."

    def _agent_rows(self, sessions):
        if not self.agents.all():
            return list(sessions)
        from ..sessions import sort_sessions
        waiting = {cid for cid, _index, _text in self._agent_codex_waiting_items()}
        rows = agents.rows(sessions, self.agents.all(),
                           waiting_keys={f"{AGENTS}:{cid}" for cid in waiting})
        return sort_sessions(rows, self.speech.session_order)

    def _agent_waiting_names(self):
        """Conversations with a message waiting for a Codex member to be
        ready, for quitting and updating to name (they'd lose it)."""
        names = []
        for cid, index, _text in self._agent_codex_waiting_items():
            conversation = self.agents.get(cid)
            if conversation is not None:
                name = f"{conversation.members[index].name} in {conversation.title}"
                if name not in names:
                    names.append(name)
        return names

    def _agent_codex_waiting_items(self):
        for _key, items in self._agent_codex_waiting.items():
            yield from items

    def _agent_save(self, conversation: AgentConversation, touch: bool = False) -> bool:
        """``touch``: something happened (a send, a reply, a hand-off), so it
        moves up a newest-first list; opening or renaming it doesn't."""
        if touch:
            conversation.last_activity_ms = agents.now_ms()
        try:
            self.agents.put(conversation)
            return True
        except Exception as exc:  # noqa: BLE001
            self._feedback(f"Couldn't save the agent conversation: {exc}")
            return False

    def _agent_note(self, conversation, text):
        conversation.add_note(text)

    def _agent_is_open(self, conversation) -> bool:
        return self._open is not None and self._open.key == conversation.key

    def _agent_after_change(self, conversation, touch: bool = True):
        """Save, then bring the list and (if loaded) the messages up to date."""
        self._agent_save(conversation, touch=touch)
        if self._agent_is_open(conversation):
            self._refresh_chat()
            self._update_send_state()
        self.refresh_sessions()

    # -- starting one -------------------------------------------------------------------

    def on_new_agent_conversation(self):
        """File, New Agent Conversation (Ctrl+Shift+G)."""
        if self.agents.load_error and self.agents._save_blocked:
            wx.MessageBox(self.agents.load_error, "New Agent Conversation",
                          wx.OK | wx.ICON_WARNING, self)
            return
        taken = agents.member_identities(self.agents.all())
        existing = []
        for record in sorted(self.store.all(provider=None),
                             key=lambda r: -(r.last_activity_ms or r.created_ms)):
            info = record.to_info()
            if record.identity in taken or record.provider not in ("claude", "codex"):
                continue
            if not self._can_edit(info):
                continue
            existing.append(info)
        new_kinds = []
        if self._agent_can_start("claude"):
            new_kinds.append("claude")
        if self.sessions.can_create("codex"):
            new_kinds.append("codex")
        if len(existing) + 2 * len(new_kinds) < 2 or (not new_kinds and len(existing) < 2):
            wx.MessageBox("An agent conversation needs two of The Chat Place's own chats, and "
                          "there aren't two free to use. Start one with File, New Session, or "
                          "make sure Claude Code or Codex is installed.",
                          "New Agent Conversation", wx.OK | wx.ICON_INFORMATION, self)
            return
        folders = []
        for info in [s for s in self._snapshot.sessions if s.cwd] + existing:
            if info.cwd and info.cwd not in folders:
                folders.append(info.cwd)
        dialog = AgentConversationDialog(
            self, existing, new_kinds, folders=folders[:20],
            auto_allowed=self.speech.agent_auto_mode)
        try:
            if dialog.ShowModal() != wx.ID_OK or dialog.result is None:
                return
            plan = dialog.result
        finally:
            dialog.Destroy()
        self._agent_create(plan)

    def _agent_can_start(self, provider) -> bool:
        from .. import platform_paths
        return provider == "claude" and bool(platform_paths.find_claude().path)

    def _agent_create(self, plan):
        """Make any new member chats, then the conversation, then send the
        opening message. A Codex chat is made on a worker, as New Session
        makes one; nothing is saved until every member exists."""
        members, records = [], []
        pending_codex = []
        for slot, choice in enumerate(plan.members):
            if choice.info is not None:
                members.append(Member(choice.info.provider, choice.info.cli_session_id, "",
                                      choice.role))
                continue
            title = f"{plan.title}: {agents.provider_name(choice.provider)}"
            if choice.provider == "claude":
                record = OwnSession(cli_session_id=new_session_id(), title=title,
                                    cwd=plan.folder, permission_mode=choice.permission,
                                    started=False)
                records.append(record)
                members.append(Member("claude", record.cli_session_id, "", choice.role))
            else:
                members.append(None)
                pending_codex.append((slot, title, choice))

        def finish():
            for record in records:
                if not self._store_write(self.store.add, record):
                    return
            for member, name in zip(members, agents.member_names(members)):
                member.name = name
            try:
                conversation = agents.new_conversation(plan.title, members, plan.mode,
                                                       plan.turn_limit)
                conversation.next_index = plan.first
                self.agents.add(conversation)
            except Exception as exc:  # noqa: BLE001
                self._feedback(f"Couldn't start the agent conversation: {exc}")
                return
            self.refresh_sessions()
            self._agent_open(conversation)
            if plan.message.strip():
                self._agent_send(conversation.conversation_id, plan.message, [plan.first]
                                 if not plan.to_all else list(range(len(members))))
            else:
                self._feedback(f"Started {conversation.title}. Type the first message.")

        def make_codex(remaining):
            if not remaining:
                finish()
                return
            slot, title, choice = remaining[0]
            sandbox = choice.permission or "read-only"

            def create():
                return self.sessions.create_owned("codex", plan.folder, title, sandbox=sandbox,
                                                  approval_policy="on-request")

            def done(record, error):
                if error:
                    made = [r.title for r in records] + made_codex
                    kept = ""
                    if plan.message.strip() and self._copy_text(plan.message):
                        kept = " Your opening message is copied to the clipboard."
                    left = (f" {' and '.join(made)} {'was' if len(made) == 1 else 'were'} "
                            "started and stay in the list." if made else "")
                    self._feedback("Couldn't start the Codex chat for the agent conversation, so "
                                   "it wasn't started. " + (record or "Check Codex sign-in.")
                                   + kept + left)
                    for record_made in records:
                        self._store_write(self.store.add, record_made)
                    self.refresh_sessions()
                    return
                if not self._store_write(self.store.add, record):
                    return
                members[slot] = Member("codex", record.cli_session_id, "", choice.role)
                made_codex.append(record.title)
                make_codex(remaining[1:])
            self._codex_work(create, done)

        made_codex = []
        if pending_codex:
            self._feedback("Starting the Codex chat for the agent conversation.")
        make_codex(pending_codex)

    def _agent_open(self, conversation):
        rows = [s for s in self._snapshot.sessions if s.key == conversation.key]
        info = rows[0] if rows else agents.rows([], [conversation])[-1]
        self.open_session(info)

    # -- sending ------------------------------------------------------------------------

    def _agent_send_from_box(self, info, text):
        """Send what's in the reply box. "@Codex ..." goes to Codex only, and
        "@all ..." to every member; otherwise to the member whose turn it is."""
        conversation = self._agent_conversation(info)
        if conversation is None:
            self._feedback("This agent conversation is gone.")
            return False
        targets, body = agents.address(text, conversation.members)
        if not body.strip():
            self._feedback("Type a message after the name.")
            return False
        if targets is None:
            targets = [conversation.next_index]
        return self._agent_send(conversation.conversation_id, body, targets)

    def _agent_send(self, conversation_id, text, targets) -> bool:
        conversation = self.agents.get(conversation_id)
        if conversation is None:
            return False
        # Your message starts the relay again, with a fresh count.
        conversation.paused, conversation.stop_reason, conversation.exchanges = False, "", 0
        pending = conversation.pending
        relay = len(targets) == 1
        sent, carried = [], ""
        for index in targets:
            message = text
            if pending is not None and pending.to_index == index:
                # A reply was waiting to go to this agent: it goes with your
                # words, ahead of them, rather than being dropped.
                message = (f"{agents.relay_text(conversation, pending.from_index, pending.text)}"
                           f"\n\nThe user adds:\n\n{text}")
            if self._agent_deliver(conversation, index, message, relay=relay):
                sent.append(conversation.members[index].name)
                if message is not text:
                    carried = conversation.members[pending.from_index].name
                    conversation.pending = None
                    self._agent_note(conversation, f"Passed {carried}'s reply to "
                                                   f"{conversation.members[index].name} with "
                                                   "your message.")
        if len(targets) == 1 and sent:
            conversation.next_index = conversation.next_after(targets[0])
        self._agent_after_change(conversation)
        if sent:
            names = " and ".join(sent)
            self._feedback(f"Sent {carried}'s reply and your message to {names}." if carried
                           else f"Sent to {names}.")
        return bool(sent)

    def _agent_deliver(self, conversation, index, text, *, relay=True) -> bool:
        """Send ``text`` to member ``index`` through its own provider, with
        the setup the first time. ``relay``: hand its reply on when it comes.
        Changes ``conversation`` (briefed); the caller saves it."""
        member = conversation.members[index]
        info = self._agent_member_info(member)
        if info is None:
            self._agent_note(conversation, f"{member.name}'s chat is gone, so nothing was sent "
                                           "to it.")
            self._feedback(f"{member.name}'s chat is gone. Nothing was sent to it.")
            return False
        message = agents.outgoing(conversation, index, text)
        identity = member.identity
        self._agent_awaiting[identity] = (conversation.conversation_id, relay)
        if index not in conversation.briefed:
            # Until its first turn ends well, a failure takes "briefed" back,
            # so the setup goes again with the next message.
            self._agent_unconfirmed.add(identity)
        if member.provider == "claude":
            if not self._send_claude(info, message, [], announce_send=False):
                self._agent_awaiting.pop(identity, None)
                self._agent_note(conversation, f"Couldn't send to {member.name}.")
                return False
        elif member.provider == "codex":
            record = self.store.get(member.native_id, provider="codex")
            self._agent_send_codex(conversation.conversation_id, index, record, message)
        else:
            self._agent_awaiting.pop(identity, None)
            return False
        if index not in conversation.briefed:
            conversation.briefed.append(index)
        return True

    def _agent_send_codex(self, conversation_id, index, record, message):
        """A Codex member must be ready (its thread resumed here) before it
        takes a message; one that isn't is got ready first, on a worker."""
        key = record.to_info().key

        def result(queued, error):
            if error:
                self._agent_send_failed(conversation_id, index, message, queued)

        try:
            ready = self.sessions.owned_action("ready", record)
        except Exception:  # noqa: BLE001
            ready = False
        if ready:
            self._send_codex_owned(record, message, announce_send=False, restore=False,
                                   on_result=result)
            return
        waiting = self._agent_codex_waiting.setdefault(key, [])
        waiting.append((conversation_id, index, message))
        if len(waiting) > 1:
            return  # already being got ready

        def prepare():
            self.sessions.owned_action("prepare", record)

        def done(problem, error):
            # Anything Stop or Delete took out of the list doesn't go.
            items = self._agent_codex_waiting.pop(key, [])
            for cid, slot, text in items:
                if error:
                    self._agent_send_failed(cid, slot, text, problem)
                else:
                    self._send_codex_owned(record, text, announce_send=False, restore=False,
                                           on_result=lambda q, e, cid=cid, slot=slot, text=text:
                                           e and self._agent_send_failed(cid, slot, text, q))
            self.refresh_sessions()
        self._codex_work(prepare, done)
        self.refresh_sessions()

    def _agent_send_failed(self, conversation_id, index, text, problem):
        """Nothing goes missing: the message comes back to the conversation's
        reply box (or the clipboard when it isn't loaded), and the relay stops."""
        conversation = self.agents.get(conversation_id)
        if conversation is None:
            return
        member = conversation.members[index]
        self._agent_awaiting.pop(member.identity, None)
        self._agent_unbrief(conversation, index)
        reason = problem if isinstance(problem, str) and problem else "it couldn't be sent"
        conversation.paused, conversation.stop_reason = True, f"Couldn't send to {member.name}."
        self._agent_note(conversation, f"Couldn't send to {member.name}: {reason}")
        self._agent_after_change(conversation)
        body = text.partition(agents.SETUP_END)[2].strip() or text
        if self._agent_is_open(conversation):
            current = self.reply_text.GetValue()
            self.reply_text.SetValue(body + ("\n\n" + current if current.strip() else ""))
            self._feedback(f"{conversation.title}: couldn't send to {member.name}. "
                           f"{reason}. The message is back in the reply box.")
        elif self._copy_text(body):
            self._feedback(f"{conversation.title}: couldn't send to {member.name}. "
                           f"{reason}. The message is copied to the clipboard.")
        else:
            self._feedback(f"{conversation.title}: couldn't send to {member.name}. {reason}.")

    def _agent_unbrief(self, conversation, index):
        identity = conversation.members[index].identity
        if identity in self._agent_unconfirmed:
            self._agent_unconfirmed.discard(identity)
            if index in conversation.briefed:
                conversation.briefed.remove(index)

    # -- replies ----------------------------------------------------------------------

    def _agent_member_asked(self, provider, native_id):
        """A member needs you mid-turn (a permission or question): the relay
        doesn't go on by itself after this turn."""
        if (provider, native_id) in self._agent_awaiting:
            self._agent_asked.add((provider, native_id))

    def _agent_answered(self, provider, native_id, reply):
        """A member answered and left work running in the background (#161):
        its turn ends later, perhaps with nothing more to say, so its reply
        is kept for then."""
        if (provider, native_id) in self._agent_awaiting and (reply or "").strip():
            self._agent_background_reply[(provider, native_id)] = reply

    def _agent_unsent(self, provider, native_id):
        """A member gave a conversation's message back unsent: the turn it
        was waiting for won't come."""
        awaited = self._agent_awaiting.pop((provider, native_id), None)
        if awaited is None:
            return
        conversation = self.agents.get(awaited[0])
        if conversation is None:
            return
        index = conversation.member_index(provider, native_id)
        if index is not None:
            self._agent_unbrief(conversation, index)
            name = conversation.members[index].name
            conversation.paused, conversation.stop_reason = True, f"{name} didn't take the message."
            self._agent_note(conversation, f"{name} didn't take the message; it's in {name}'s "
                                           "own chat's reply box (File, Agent Conversation "
                                           "Members).")
            self._agent_after_change(conversation)

    def _agent_turn_ended(self, provider, native_id, reply, failed, *, more_queued=False,
                          detail=""):
        """A member's turn ended. If the conversation started it, hand the
        reply on (or wait for you to), or stop, and say so. ``detail``: why it
        failed, or what was refused, which the member chat would have said."""
        identity = (provider, native_id)
        conversation = self.agents.containing(provider, native_id)
        if conversation is None:
            self._agent_awaiting.pop(identity, None)
            self._agent_background_reply.pop(identity, None)
            return
        if self._agent_is_open(conversation):
            self._refresh_chat()
        if more_queued:
            # Another of the conversation's messages goes in first; what this
            # turn asked you doesn't carry over to that one.
            self._agent_asked.discard(identity)
            return
        if not (reply or "").strip():
            reply = self._agent_background_reply.pop(identity, reply)
        self._agent_background_reply.pop(identity, None)
        awaited = self._agent_awaiting.pop(identity, None)
        asked = identity in self._agent_asked
        self._agent_asked.discard(identity)
        index = conversation.member_index(provider, native_id)
        member = conversation.members[index]
        conversation.unread = not self._agent_is_open(conversation)
        if awaited is None or awaited[0] != conversation.conversation_id:
            # You wrote in the member chat itself: nothing to hand on.
            self._agent_after_change(conversation)
            return
        title = conversation.title
        if failed or not (reply or "").strip():
            if failed:
                self._agent_unbrief(conversation, index)
            what = "turn failed" if failed else "turn ended without a reply"
            why = f" {detail.strip().rstrip('.')}." if detail and detail.strip() else ""
            conversation.paused, conversation.stop_reason = True, f"{member.name}'s {what}.{why}"
            self._agent_note(conversation, f"{member.name}'s {what}.{why} The relay stopped.")
            self._agent_after_change(conversation)
            self._say(f"{title}: {member.name}'s {what}.{why} The relay stopped.")
            self._notify(conversation.key, f"{title}: the relay stopped",
                         f"{member.name}'s {what}.{why}", needs_you=True)
            return
        self._agent_unconfirmed.discard(identity)
        refused = f" {detail.strip()}" if detail and detail.strip() else ""
        previous = self._agent_last_reply.get(identity, "")
        heard = self._agent_last_heard.get(conversation.conversation_id, "")
        self._agent_last_reply[identity] = reply
        self._agent_last_heard[conversation.conversation_id] = reply
        said = announce.reply_text(f"{member.name} in {title}", reply, self.speech.announce)
        if not awaited[1]:
            # One of several you wrote to at once: nothing goes across.
            self._agent_after_change(conversation)
            self._agent_say(conversation, said, always=True)
            return
        to = conversation.next_after(index)
        conversation.next_index = to
        target = conversation.members[to].name
        stop = ""
        if agents.said_done(reply):
            stop = f"{member.name} said the work is done."
        elif agents.repeating(previous, reply) or agents.repeating(heard, reply):
            stop = "The replies were repeating, so the relay stopped."
        auto = (conversation.mode == MODE_AUTO and self.speech.agent_auto_mode
                and not conversation.paused)
        if stop:
            # Finished (or going round): nothing waits, so the row doesn't
            # keep needing you. A message from you starts it again.
            conversation.paused, conversation.stop_reason = True, stop
            conversation.pending = None
            self._agent_note(conversation, stop)
            self._agent_after_change(conversation)
            self._agent_say(conversation, " ".join(p for p in (said, refused.strip(), stop) if p),
                            always=True)
            self._notify(conversation.key, f"{title}: the relay stopped", stop)
            return
        if auto and conversation.exchanges >= conversation.turn_limit:
            stop = (f"Stopped after {conversation.exchanges} exchange"
                    f"{'s' if conversation.exchanges != 1 else ''}, the turn limit.")
        elif auto and (asked or refused):
            stop = (f"Paused: {member.name} asked you something during its turn." if asked
                    else f"Paused: {refused.strip()}")
        if auto and not stop:
            text = agents.relay_text(conversation, index, reply)
            conversation.exchanges += 1
            if self._agent_deliver(conversation, to, text, relay=True):
                self._agent_note(conversation, f"Passed {member.name}'s reply to {target} "
                                               f"(exchange {conversation.exchanges} of "
                                               f"{conversation.turn_limit}).")
                self._agent_after_change(conversation)
                self._agent_say(conversation, said)
                return
            stop = f"Couldn't pass {member.name}'s reply to {target}."
        conversation.pending = Pending(index, to, reply)
        if stop:
            conversation.paused, conversation.stop_reason = True, stop
            self._agent_note(conversation, stop)
        self._agent_after_change(conversation)
        hint = f"{key_name('Ctrl+Shift+E')} hands it to {target}."
        spoken = " ".join(part for part in (said, refused.strip(), stop, hint) if part)
        self._agent_say(conversation, spoken, always=True)
        self._notify(conversation.key, f"{title}: {member.name} replied",
                     f"{stop} {hint}".strip(), needs_you=True)

    def _agent_say(self, conversation, text, always=False):
        """Each reply is news, queued behind what you're hearing rather than
        cutting it off; the announcement level (Settings) applies as ever."""
        if text:
            self._say(text, interrupt=False)

    # -- handing on -----------------------------------------------------------------------

    def on_agent_hand_off(self):
        """Ctrl+Shift+E in a conversation: hand the waiting reply on, after
        reading and changing it if you like. It restarts a stopped relay."""
        conversation = self._agent_conversation()
        if conversation is None:
            return
        pending = conversation.pending
        if pending is None:
            self._feedback("No reply is waiting to be handed on. Type a message to send one, "
                           "or @ and a name to choose who it goes to.")
            return
        to = conversation.members[pending.to_index]
        text = agents.relay_text(conversation, pending.from_index, pending.text)
        target = self._agent_member_info(to)
        label = f"{to.name}, {target.title}" if target is not None else to.name
        dialog = RelayDialog(self, conversation.title, [label], text,
                             lambda _i, t: agents_too_long(to.provider, t),
                             title=f"Hand Off: {conversation.title}")
        try:
            if dialog.ShowModal() != wx.ID_OK or dialog.chosen is None:
                return
            _index, edited = dialog.chosen
        finally:
            dialog.Destroy()
        conversation = self.agents.get(conversation.conversation_id)
        if conversation is None or conversation.pending != pending:
            self._feedback("The waiting reply changed while the dialog was open. Nothing was "
                           f"sent; press {key_name('Ctrl+Shift+E')} again for the new one."
                           if conversation is not None and conversation.pending is not None
                           else "Nothing was sent: the reply is no longer waiting.")
            return
        frm = conversation.members[conversation.pending.from_index]
        conversation.pending = None
        conversation.paused, conversation.stop_reason, conversation.exchanges = False, "", 1
        if self._agent_deliver(conversation, pending.to_index, edited, relay=True):
            conversation.next_index = conversation.next_after(pending.to_index)
            self._agent_note(conversation, f"You passed {frm.name}'s reply to {to.name}.")
            self._feedback(f"Passed to {to.name}.")
        else:
            conversation.pending = pending
        self._agent_after_change(conversation)

    # -- stopping, status, members --------------------------------------------------------

    def _agent_stop(self, info):
        """Ctrl+Period: stop every member's running turn and the relay."""
        conversation = self._agent_conversation(info)
        if conversation is None:
            return
        stopped, taken_back = [], []
        for member in conversation.members:
            self._agent_awaiting.pop(member.identity, None)
            self._agent_background_reply.pop(member.identity, None)
            member_info = self._agent_member_info(member)
            if member_info is None:
                continue
            if member.provider == "claude":
                runner = self._runners.get(member.native_id)
                if runner is not None:
                    runner.cancel()
                    stopped.append(member.name)
                    queued = self._take_queued(member.native_id)
                    if queued:
                        taken_back.append(queued.partition(agents.SETUP_END)[2].strip() or queued)
            elif member.provider == "codex":
                state = self._codex_states.get(member_info.key)
                if (member_info.key in self._codex_send_pending
                        or (state is not None and (state.outcome == "inProgress"
                                                   or state.state == WORKING))):
                    self._stop_codex_owned(member_info)
                    stopped.append(member.name)
        for key in [k for k, items in self._agent_codex_waiting.items()
                    if any(cid == conversation.conversation_id for cid, _i, _t in items)]:
            self._agent_codex_waiting[key] = [item for item in self._agent_codex_waiting[key]
                                              if item[0] != conversation.conversation_id]
        conversation.paused, conversation.stop_reason = True, "Stopped."
        dropped = conversation.pending is not None
        conversation.pending = None
        self._agent_note(conversation, "Stopped." if not stopped
                         else f"Stopped {' and '.join(stopped)}.")
        self._agent_after_change(conversation, touch=False)
        said = (f"Stopping {' and '.join(stopped)}. The relay is stopped." if stopped
                else "The relay is stopped. Nothing was running.")
        if dropped:
            said += " The waiting reply wasn't passed on."
        if taken_back and self._agent_is_open(conversation):
            current = self.reply_text.GetValue()
            self.reply_text.SetValue("\n\n".join(taken_back)
                                     + ("\n\n" + current if current.strip() else ""))
            said += " A message that hadn't gone yet is back in the reply box."
        self._feedback(said)

    def _agent_status_text(self, info) -> str:
        conversation = self._agent_conversation(info)
        if conversation is None:
            return "This agent conversation is gone."
        parts = []
        for member in conversation.members:
            member_info = self._agent_member_info(member)
            if member_info is None:
                parts.append(f"{member.name}'s chat is gone.")
                continue
            if member.provider == "claude":
                runner = self._runners.get(member.native_id)
                if runner is not None and self._pending.get(member.native_id):
                    parts.append(f"{member.name} is waiting for you: "
                                 f"{self._pending[member.native_id][0].summary()}.")
                elif runner is not None:
                    parts.append(f"{member.name} is working ({describe_elapsed(runner.elapsed())}).")
                else:
                    parts.append(f"{member.name} is idle.")
            else:
                state = self._codex_states.get(member_info.key)
                if member_info.key in self._agent_codex_waiting:
                    parts.append(f"{member.name} is getting ready.")
                elif state is not None and state.requests:
                    parts.append(f"{member.name} is waiting for you.")
                elif state is not None and (state.outcome == "inProgress" or state.state == WORKING):
                    parts.append(f"{member.name} is working.")
                else:
                    parts.append(f"{member.name} is idle.")
        mode = agents.MODE_NAMES[conversation.mode]
        if conversation.mode == MODE_AUTO and not self.speech.agent_auto_mode:
            mode += " (off in Settings, so you approve each hand-off)"
        relay = (f"Hand-offs: {mode}, {conversation.exchanges} of {conversation.turn_limit} "
                 "exchanges." if conversation.mode == MODE_AUTO else f"Hand-offs: {mode}.")
        if conversation.pending is not None:
            frm = conversation.members[conversation.pending.from_index].name
            to = conversation.members[conversation.pending.to_index].name
            parts.append(f"{frm}'s reply is waiting to go to {to}; "
                         f"{key_name('Ctrl+Shift+E')} hands it on.")
        if conversation.paused and conversation.stop_reason:
            parts.append(conversation.stop_reason)
        nxt = conversation.members[conversation.next_index].name
        return f"{conversation.title}: {' '.join(parts)} {relay} What you type goes to {nxt}."

    def on_agent_members(self):
        """File, Agent Conversation Members: who's in it, their roles, and
        going to one of their chats (they aren't in the list on their own)."""
        selected = self._selected_session()
        conversation = self._agent_conversation(selected) if selected is not None else None
        if conversation is None:
            conversation = self._agent_conversation()
        if conversation is None:
            self._feedback("Load an agent conversation first.")
            return
        choices = []
        for member in conversation.members:
            info = self._agent_member_info(member)
            where = info.title if info is not None else "its chat is gone"
            role = f", role: {member.role}" if member.role else ""
            choices.append(f"{member.name}: {where}{role}")
        index = self._choose(f"Members: {conversation.title}",
                             "Go to which member's own chat? (Its messages are also in the "
                             "conversation.)", choices)
        if index is None:
            return
        info = self._agent_member_info(conversation.members[index])
        if info is None:
            self._feedback("That chat is gone.")
            return
        self.open_session(info)

    def on_agent_mode(self):
        """File, Agent Conversation Hand-offs: approve each, or automatic up
        to a limit (automatic only once Settings allows it)."""
        selected = self._selected_session()
        conversation = self._agent_conversation(selected) if selected is not None else None
        if conversation is None:
            conversation = self._agent_conversation()
        if conversation is None:
            self._feedback("Load an agent conversation first.")
            return
        choices = [f"Approve each hand-off ({key_name('Ctrl+Shift+E')} passes each reply on)"]
        if self.speech.agent_auto_mode:
            choices.append("Automatic: replies go across by themselves, up to a limit")
        else:
            choices.append("Automatic is off in Settings (Agent conversations); choose to "
                           "open Settings")
        current = 1 if conversation.mode == MODE_AUTO else 0
        index = self._choose(f"Hand-offs: {conversation.title}", "How replies go across:",
                             choices, selection=current)
        if index is None:
            return
        if index == 1 and not self.speech.agent_auto_mode:
            self.on_settings()
            return
        mode = MODE_AUTO if index == 1 else MODE_APPROVE
        limit = conversation.turn_limit
        if mode == MODE_AUTO:
            limits = [str(n) for n in range(1, agents.MAX_TURN_LIMIT + 1)]
            chosen = self._choose(f"Turn limit: {conversation.title}",
                                  "Stop after how many exchanges? (A reply passed from one agent "
                                  "to the other is one exchange.)", limits,
                                  selection=conversation.turn_limit - 1)
            if chosen is None:
                return
            limit = chosen + 1
        conversation = self.agents.get(conversation.conversation_id)
        conversation.mode, conversation.turn_limit = mode, agents.clamp_limit(limit)
        self._agent_note(conversation, "Hand-offs: approve each." if mode == MODE_APPROVE
                         else f"Hand-offs: automatic, up to {conversation.turn_limit} exchanges.")
        self._agent_after_change(conversation, touch=False)
        self._feedback(f"{conversation.title}: " + (
            "you approve each hand-off." if mode == MODE_APPROVE
            else f"replies go across by themselves, up to {conversation.turn_limit} exchanges."))

    def _agent_rename(self, info, title):
        conversation = self._agent_conversation(info)
        if conversation is None:
            return False
        conversation.title = agents.clean_title(title) or conversation.title
        self._agent_after_change(conversation, touch=False)
        return True

    def _agent_delete(self, info):
        """Delete the conversation record. Its member chats stay, with their
        histories, and come back to the list."""
        conversation = self._agent_conversation(info)
        if conversation is None:
            return
        names = " and ".join(m.name for m in conversation.members)
        answer = wx.MessageBox(
            f"Delete the agent conversation {conversation.title}? Its member chats ({names}) "
            "stay, with everything said in them, and come back to the session list.",
            "Delete Agent Conversation", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self)
        if answer != wx.YES:
            return
        for member in conversation.members:
            for table in (self._agent_awaiting, self._agent_last_reply,
                          self._agent_background_reply):
                table.pop(member.identity, None)
            self._agent_asked.discard(member.identity)
            self._agent_unconfirmed.discard(member.identity)
        for key in list(self._agent_codex_waiting):
            self._agent_codex_waiting[key] = [
                item for item in self._agent_codex_waiting[key]
                if item[0] != conversation.conversation_id]
        self._agent_last_heard.pop(conversation.conversation_id, None)
        try:
            self.agents.remove(conversation.conversation_id)
        except Exception as exc:  # noqa: BLE001
            self._feedback(f"Couldn't delete the agent conversation: {exc}")
            return
        if self._open is not None and self._open.key == conversation.key:
            self.unload_session()
        self.session_list.SetFocus()
        self.refresh_sessions(force=False, resort=True)
        self._feedback(f"Deleted {conversation.title}. Its member chats are back in the list.")

    def _agent_properties(self, info):
        conversation = self._agent_conversation(info)
        if conversation is None:
            return None
        lines = [f"Title: {conversation.title}", "Kind: agent conversation",
                 f"Hand-offs: {agents.MODE_NAMES[conversation.mode]}"]
        if conversation.mode == MODE_AUTO:
            lines.append(f"Turn limit: {conversation.turn_limit} exchanges "
                         f"({conversation.exchanges} so far)")
        for member in conversation.members:
            member_info = self._agent_member_info(member)
            where = member_info.title if member_info is not None else "chat gone"
            lines.append(f"{member.name}: {where}" + (f"; role: {member.role}" if member.role else ""))
        if conversation.paused and conversation.stop_reason:
            lines.append(f"Relay: {conversation.stop_reason}")
        lines.append("Started: " + time.strftime("%Y-%m-%d %H:%M",
                                                 time.localtime(conversation.created_ms / 1000)))
        return lines


def agents_too_long(provider, text):
    from .. import relay
    return relay.too_long(provider, text)
