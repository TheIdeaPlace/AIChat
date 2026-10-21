"""File, Send Reply to Another Chat (#248): the window's side of relay.py.

A reply in the loaded chat goes, labelled and after you've read it, to
another of The Chat Place's own chats, of either provider, as an ordinary
message. The window then goes to that chat, so its answer is the next thing
heard, and the same key sends that answer back.
"""
import wx

from .. import relay
from .dialogs import RelayDialog


class RelayChats:
    def on_send_reply_to(self):
        info = self._open
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
        if not targets:
            wx.MessageBox("There's no other chat to send it to. A reply can go to The Chat "
                          "Place's own chats, of any provider; start one with File, New "
                          "Session.", "Send Reply to Another Chat",
                          wx.OK | wx.ICON_INFORMATION, self)
            return
        dialog = RelayDialog(self, info.title, [relay.describe(t) for t in targets],
                             relay.message_text(message, info.title),
                             lambda index, text: relay.too_long(targets[index].provider, text))
        try:
            if dialog.ShowModal() != wx.ID_OK or dialog.chosen is None:
                return
            index, text = dialog.chosen
        finally:
            dialog.Destroy()
        self._relay(info, targets[index], text)

    def _relay(self, source, target, text):
        """Go to ``target`` and send ``text`` there, as Send would. Text that
        can't go is put in the target's reply box, never dropped."""
        self._relay_partners[target.key] = source.key
        self.open_session(target)
        if target.provider == "codex":
            record = self.store.get(target.cli_session_id, provider="codex")
            if record is None:
                self._feedback(f"Couldn't find {target.title} in The Chat Place's chats.")
                return
            if target.key in self._codex_preparing:
                # Codex must get a chat ready (reading its history first)
                # before it takes a message; _prepare_codex_owned sends it.
                self._relay_waiting[target.key] = text
                self._feedback(f"Getting {target.title} ready. The message goes as soon as it is.")
                return
            self._send_codex_owned(record, text)
            return
        if not self._send_claude(target, text, []):
            self._give_back(target.cli_session_id, text, True)
            self._save_draft()

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

    def _drop_waiting_relay(self):
        """Leaving a Codex chat before it was ready: its message stays as
        that chat's draft, and you hear that it wasn't sent."""
        info = self._open
        text = self._relay_waiting.pop(info.key, None) if info is not None else None
        if text is None:
            return
        self._restore_codex_text(info.key, (text,))
        self._feedback(f"The message for {info.title} wasn't sent. It's in that chat's reply box.")
