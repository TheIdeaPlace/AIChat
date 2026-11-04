"""Playing The Chat Place's sounds (#250): what the window calls when something happens.

Hooks sit beside the announcements they go with, and each says only which event happened and
in which chat. Everything about whether it plays is here, and how sounds are limited and played
is app-kit's (``theideaplace_appkit.sounds``). ``_sound`` returns at once and can't raise: a
sound never gets to delay or break a turn.
"""
from __future__ import annotations

import time
from typing import Optional

from theideaplace_appkit.sounds import SoundPlayer, quiet_now

from .. import sound_events
from ..agents import AGENTS
from ..sound_events import OTHERS_ALL, OTHERS_NONE, OTHERS_OWN, TOOL_EVENTS, TOOLS_ALL
from ..sessions import WORKING

#: How long a loaded chat's turn can go without a sound before "Still working".
#: A long command or long thinking sends no events, so without this the silence
#: would be just when you'd wonder whether anything is happening.
PULSE_SECONDS = 20.0


class SoundHost:
    _sound_player_made: Optional[SoundPlayer] = None
    _sound_last_played: float = 0.0
    #: When the loaded chat was first seen working, so its first pulse waits too.
    _sound_working_since: Optional[float] = None

    def _sound_player(self) -> SoundPlayer:
        if self._sound_player_made is None:
            self._sound_player_made = SoundPlayer()
        return self._sound_player_made

    def _sound_chat_loaded(self, key: Optional[str]) -> bool:
        """Whether a chat is the one you're in. A member of an agent conversation counts
        while its conversation is the one loaded."""
        info = getattr(self, "_open", None)
        if info is None or not key:
            return False
        if info.key == key:
            return True
        conversation = self._sound_loaded_conversation()
        if conversation is None:
            return False
        provider, _, native = key.partition(":")
        if provider == "own":
            provider = "claude"
        return conversation.member_index(provider, native) is not None

    def _sound_loaded_conversation(self):
        finder = getattr(self, "_agent_conversation", None)
        info = getattr(self, "_open", None)
        if finder is None or info is None or getattr(info, "provider", "") != AGENTS:
            return None
        try:
            return finder(info)
        except Exception:  # noqa: BLE001 - a sound never breaks a turn
            return None

    def _sound(self, event_id: str, key: Optional[str] = None, own: bool = True) -> None:
        """An event happened in the chat with this presentation key. No key means it isn't about
        one chat. ``own`` is whether The Chat Place runs that chat, as opposed to only watching."""
        try:
            self._sound_unchecked(event_id, key, own)
        except Exception:  # noqa: BLE001 - a sound never breaks a turn
            pass

    def _sound_unchecked(self, event_id: str, key: Optional[str], own: bool) -> None:
        settings = self.speech
        if not settings.sounds_on or settings.sounds_muted:
            return
        event = sound_events.BY_ID[event_id]
        if key is not None and not self._sound_chat_loaded(key):
            if event_id in TOOL_EVENTS:
                if settings.sound_tools != TOOLS_ALL:
                    return
            elif (settings.sound_others == OTHERS_NONE
                  or (settings.sound_others == OTHERS_OWN and not own)):
                return
        if not event.answers_you and quiet_now():
            return
        chosen = sound_events.resolve(event_id, settings.sound_choices, settings.sound_style)
        if chosen is None:
            return
        path, fallback = chosen
        played = self._sound_player().play(path, key=event_id, priority=event.priority,
                                           volume=settings.sound_volume / 100, fallback=fallback)
        if played:
            self._sound_last_played = time.monotonic()

    def _sound_turn_end(self, key: str, own: bool = True) -> None:
        """A turn ended with a reply: which sound depends on whether you're in that chat."""
        self._sound(sound_events.FINISHED if self._sound_chat_loaded(key)
                    else sound_events.FINISHED_ELSEWHERE, key, own)

    def _sound_tick(self, now: Optional[float] = None) -> None:
        """Called by the chat's timer. "Still working" after a silence, loaded chat only."""
        now = time.monotonic() if now is None else now
        settings = self.speech
        if (not settings.sounds_on or settings.sounds_muted or not self._sound_loaded_working()):
            self._sound_working_since = None
            return
        if self._sound_working_since is None:
            self._sound_working_since = now
        if now - max(self._sound_working_since, self._sound_last_played) >= PULSE_SECONDS:
            # Set here as well: a pulse let through or held back waits its full time again.
            self._sound_last_played = now
            self._sound(sound_events.PULSE)

    def _sound_loaded_working(self) -> bool:
        """Whether the loaded chat's turn is running, and isn't waiting for you."""
        info = getattr(self, "_open", None)
        if info is None:
            return False
        conversation = self._sound_loaded_conversation()
        if conversation is not None:
            return any(self._sound_member_working(member.provider, member.native_id)
                       for member in conversation.members)
        if info.is_own:
            return self._sound_member_working(info.provider, info.cli_session_id)
        return info.state == WORKING

    def _sound_member_working(self, provider: str, native: str) -> bool:
        if provider == "claude":
            runner = self._runners.get(native)
            if runner is None or getattr(runner, "waiting_on_background", False):
                return False
            return not self._pending.get(native)
        state = self._codex_states.get(f"{provider}:{native}")
        return (state is not None and state.outcome == "inProgress" and not state.requests
                and not state.problem)

    def _sound_tools_since(self, provider: str, previous, state) -> None:
        """A provider runtime's state moved on: did it start tools, or change files? Its states
        can arrive merged, so counts are compared rather than each one looked for. One sound
        for however many: they've come together."""
        before = previous if (previous is not None and previous.turn_id == state.turn_id) else None
        key = f"{provider}:{state.native_id}"
        if (getattr(state, "edits_started", 0) > getattr(before, "edits_started", 0)):
            self._sound(sound_events.FILE_CHANGED, key)
        elif getattr(state, "tools_started", 0) > getattr(before, "tools_started", 0):
            self._sound(sound_events.TOOL, key)

    def _sound_close(self) -> None:
        player, self._sound_player_made = self._sound_player_made, None
        if player is not None:
            player.close()

    def on_toggle_mute_sounds(self, _event=None) -> None:
        """View, Mute All Sounds (Ctrl+Shift+X): silences everything at once, what's playing
        too, and is said even with announcements silent, because you asked."""
        muted = not self.speech.sounds_muted
        self._sound_set_mute(muted)
        if muted:
            text = "Sounds muted."
        elif self.speech.sounds_on:
            text = "Sounds on."
        else:
            text = "Sounds not muted, but they're turned off. File, Sounds turns them on."
        self._say(text, force=True, interrupt=False)

    def _sound_set_mute(self, muted: bool) -> None:
        self.speech.sounds_muted = muted
        try:
            self.speech.save()
        except OSError:
            pass  # it holds until the app closes
        self.mute_item.Check(muted)
        if muted and self._sound_player_made is not None:
            self._sound_player_made.stop()

    def on_sounds(self, _event=None) -> None:
        """File, Sounds: whether there are sounds, which, and for which chats."""
        import wx
        from .sounds_dialog import SoundsDialog
        dialog = SoundsDialog(self, self.speech, self._sound_player)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            self.speech = dialog.get_settings()
        finally:
            dialog.stop_preview()
            dialog.Destroy()
        try:
            self.speech.save()
        except OSError as exc:
            wx.MessageBox(f"Couldn't save the sound settings: {exc}", "Sounds",
                          wx.OK | wx.ICON_WARNING, self)
        self.mute_item.Check(self.speech.sounds_muted)
        if (self.speech.sounds_muted or not self.speech.sounds_on) and self._sound_player_made:
            self._sound_player_made.stop()
        self._feedback("Sound settings saved.")
