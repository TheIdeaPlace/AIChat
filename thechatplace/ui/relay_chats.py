"""File, Send Reply to Another Chat (#248): the window's side of relay.py.

A reply in the loaded chat goes, labelled and after you've read it, to
another of The Chat Place's own chats, of either provider, as an ordinary
message. The window then goes to that chat, so its answer is the next thing
heard, and the same key sends that answer back.
"""
from types import SimpleNamespace

import wx

from .. import agents, relay
from ..sessions import WORKING
from .dialogs import RelayDialog


class RelayChats:
    def on_send_reply_to(self):
        info = self._open
        if info is not None and info.provider == "agents":
            self.on_agent_hand_off()  # an agent conversation's hand-off (#248)
            return
        if info is None:
            self._feedback("Load a chat first, then send one of its replies to another chat.")
            return
        if not self._chat_loaded:
            self._feedback(f"{info.title} is still loading.")
            return
        message = relay.reply_to_send(self._chat_messages, self._selected_message())
        if message is None:
            self._feedback(f"{info.title} has no reply to send yet.")
            return
        targets = relay.targets(self._snapshot.sessions, info.key, self._can_edit,
                                self._relay_partners.get(info.key))
        # A new chat of each provider that can start one (#378), after the
        # existing chats, so the chat that last sent a reply here is still
        # first and sending back is still Enter.
        entries = [t for t in targets] + list(self._relay_new_kinds())
        if not entries:
            wx.MessageBox("There's no other chat to send it to. A reply can go to The Chat "
                          "Place's own chats, of any provider; start one with File, New "
                          "Session.", "Send Reply to Another Chat",
                          wx.OK | wx.ICON_INFORMATION, self)
            return
        working = self._relay_source_working(info)
        source_problem = self._relay_joint_problem(info, working)

        def joint_problem(index):
            entry = entries[index]
            if source_problem or isinstance(entry, str):
                return source_problem
            return self._relay_target_problem(entry)

        original = relay.message_text(message, info.title)
        dialog = RelayDialog(self, info.title, [self._relay_describe(e) for e in entries],
                             original,
                             lambda index, text: relay.too_long(self._relay_provider(
                                 entries[index]), text),
                             working=working,
                             joint=SimpleNamespace(problem=joint_problem, title=info.title))
        try:
            if dialog.ShowModal() != wx.ID_OK or dialog.chosen is None:
                return
            index, text = dialog.chosen
            joint, title = dialog.joint_chosen, dialog.joint_title or info.title
        finally:
            dialog.Destroy()
        # Where it came from, for a new chat or an agent conversation.
        came = SimpleNamespace(info=info, agent=message.assistant_name or "Another agent",
                               reply=message.text, edited=text != original.strip())
        entry = entries[index]
        if isinstance(entry, str):
            self._relay_new(came, entry, text, title if joint else None)
        elif joint:
            self._relay_joint(came, entry, text, title)
        else:
            self._relay(info, entry, text)

    @staticmethod
    def _relay_describe(entry) -> str:
        return relay.new_target_label(entry) if isinstance(entry, str) else relay.describe(entry)

    @staticmethod
    def _relay_provider(entry) -> str:
        return entry if isinstance(entry, str) else entry.provider

    def _relay_new_kinds(self):
        """The providers a new chat can be started with here (#378)."""
        from .. import platform_paths
        for kind in relay.NEW_KINDS:
            if kind == "claude" and platform_paths.find_claude().path:
                yield kind
            elif kind == "codex" and self.sessions.can_create("codex"):
                yield kind

    def _relay_joint_problem(self, info, working):
        """Why the chat a reply came from can't start an agent conversation
        (#378), or None."""
        store = self.agents
        blocked = store.load_error if store.load_error and store._save_blocked else ""
        return relay.joint_problem(
            info, can_edit=self._can_edit(info), working=working, blocked=blocked,
            in_conversation=store.containing(info.provider, info.cli_session_id) is not None)

    def _relay_target_problem(self, target):
        return relay.joint_target_problem(
            target, self.agents.containing(target.provider, target.cli_session_id) is not None,
            working=self._relay_source_working(target))

    def _relay_new(self, came, provider, text, joint_title):
        """Start a new chat with the reply as its first message (#378):
        New Session, for the folder, permission and model, with the source's
        folder and the message filled in. ``joint_title``: then make the two
        an agent conversation of that title, which sends the message."""
        source = came.info
        joint = joint_title is not None

        def made(info, message):
            if joint:
                self._relay_joint(came, info, message, joint_title)
            else:
                self._relay_partners[info.key] = source.key

        if joint:
            # Named as the conversation will name it: "Claude 2" beside a
            # Claude chat, as New Agent Conversation names its new chats.
            name = agents.member_names([agents.Member(source.provider, "1", ""),
                                        agents.Member(provider, "2", "")])[1]
            title = f"{joint_title}: {name}"
        else:
            title = relay.new_chat_title(source.title)
        self.on_new_session(forward=SimpleNamespace(provider=provider, folder=source.cwd,
                                                    message=text, title=title, joint=joint,
                                                    made=made, edited=came.edited))

    def _relay_joint(self, came, target, text, title):
        """Make the chat the reply came from and ``target`` an agent
        conversation (#378) and send the reply to ``target`` as the first
        hand-off: it answers first, and its reply waits for you to hand it
        back. Each chat stays an ordinary chat; the conversation is only the
        record that links them."""
        source = came.info
        problem = (self._relay_joint_problem(source, self._relay_source_working(source))
                   or self._relay_target_problem(target))
        if problem:
            # It changed since the dialog: the message isn't lost.
            self._relay_kept(text, f"An agent conversation can't be started: {problem.full}.")
            return
        members = [agents.Member(source.provider, source.cli_session_id, ""),
                   agents.Member(target.provider, target.cli_session_id, "")]
        for member, name in zip(members, agents.member_names(members)):
            member.name = name
        try:
            conversation = agents.new_conversation(title, members, agents.MODE_APPROVE)
            conversation.next_index = 1
            self.agents.add(conversation)
        except Exception as exc:  # noqa: BLE001
            self._relay_kept(text, f"Couldn't start the agent conversation: {exc}.")
            return
        self.refresh_sessions()
        self._agent_open(conversation)
        # The reply goes labelled as the conversation labels a hand-off, and
        # anything you added after it as your own words, as when you type
        # with a reply waiting. The label changed, it's all your own.
        reply, yours = relay.joint_message(text, came.agent, source.title, came.reply)
        if reply is None:
            message = yours
        else:
            message = agents.relay_text(conversation, 0, reply)
            if yours:
                message += agents.USER_ADDS + yours
        if not self._agent_send(conversation.conversation_id, message, [1]):
            # The conversation stays, with nothing sent: the message goes
            # where you can paste it to try again.
            self._relay_kept(text, f"The message wasn't sent to {target.title}.")

    def _relay_source_working(self, info) -> bool:
        """Still working, its latest reply may be a step on the way ("Let me
        check the tests"), not its answer: the dialog says so. A chat of The
        Chat Place's own is working only while it has a turn here; the list's
        state lags a few seconds, and would call a just-finished one busy."""
        if info.provider == "claude":
            runner = self._runners.get(info.cli_session_id)
            if runner is not None:
                return not runner.waiting_on_background
        elif info.provider == "codex":
            state = self._codex_states.get(info.key)
            if state is not None:
                return state.outcome == "inProgress" or state.state == WORKING
        if info.is_own:
            return False
        return info.state == WORKING

    def _relay(self, source, target, text):
        """Go to ``target`` and send ``text`` there, as Send would. Text that
        can't go is put in the target's reply box, or on the clipboard when
        there's no reply box for it, never dropped."""
        self._relay_partners[target.key] = source.key
        self.open_session(target)
        if target.provider == "codex":
            record = self.store.get(target.cli_session_id, provider="codex")
            if record is None:
                self._relay_kept(text, f"Couldn't find {target.title} in The Chat Place's chats, "
                                       "so the message wasn't sent.")
                return
            if target.key in self._codex_preparing:
                # Codex must get a chat ready (reading its history first)
                # before it takes a message; _prepare_codex_owned sends it.
                self._relay_waiting[target.key] = text
                self._feedback(f"Getting {target.title} ready. The message goes as soon as it is.")
                return
            self._send_codex_owned(record, text)
            return
        if target.provider != "claude":
            # Only Claude's runner is left, and another provider's ID must
            # never reach it (CLAUDE.md); a new provider adds its own branch.
            self._relay_kept(text, f"The Chat Place can't send to {target.title} yet.")
            return
        if not self._send_claude(target, text, []):
            self._give_back(target.cli_session_id, text, True)
            self._save_draft()
            self._feedback(f"Not sent to {target.title}. The message is in its reply box.")
            self.reply_text.SetFocus()

    def _relay_waiting_text(self) -> str:
        """What quitting or updating now would lose, "" if nothing: "the
        message forwarded to Reviewer"."""
        titles = []
        for key in self._relay_waiting:
            info = self._current_info(key)
            titles.append(info.title if info is not None else "a Codex chat")
        # An agent conversation's message waiting for its Codex member (#248).
        waiting = getattr(self, "_agent_waiting_names", None)
        titles += waiting() if waiting is not None else []
        if not titles:
            return ""
        if len(titles) == 1:
            return f"the message forwarded to {titles[0]}"
        return f"the messages forwarded to {', '.join(titles[:-1])} and {titles[-1]}"

    def _relay_kept(self, text, why):
        """Somewhere to keep a message that has no reply box to go in."""
        if self._copy_text(text):
            self._feedback(f"{why} It's copied to the clipboard.")
        else:
            self._feedback(f"{why} It couldn't be kept.")

    def _relay_prepared(self, key, record, failed) -> str:
        """A Codex chat a message was waiting for is ready, or failed to be.
        Ready, the message goes. Failed, it goes in the chat's reply box, and
        what to say about it comes back to go before the reason."""
        text = self._relay_waiting.pop(key, None)
        if text is None:
            return ""
        if failed:
            self._restore_codex_text(key, (text,))
            return f"The message for {record.title} wasn't sent. It's in that chat's reply box. "
        self._send_codex_owned(record, text)
        return ""

    def _drop_waiting_relay(self, going_to=None):
        """Leaving a Codex chat before it was ready: its message stays as
        that chat's draft, and you hear that it wasn't sent. Loading the same
        chat again keeps it waiting. ``going_to`` None means the chat is being
        forgotten, with no reply box to keep it in: it's copied instead."""
        info = self._open
        if info is None or (going_to is not None and going_to.key == info.key):
            return
        text = self._relay_waiting.pop(info.key, None)
        if text is None:
            return
        if going_to is None:
            self._relay_kept(text, f"The message for {info.title} wasn't sent.")
            return
        self._restore_codex_text(info.key, (text,))
        self._feedback(f"The message for {info.title} wasn't sent. It's in that chat's reply box.")
