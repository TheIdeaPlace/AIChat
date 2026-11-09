"""Playing The Chat Place's sounds (#250): what the window calls when something happens.

Hooks sit beside the announcements they go with, and each says only which event happened and
in which chat. Everything about whether it plays is here, and how sounds are limited and played
is app-kit's (``theideaplace_appkit.sounds``). ``_sound`` returns at once and can't raise: a
sound never gets to delay or break a turn.
"""
from __future__ import annotations

import time
from typing import Optional

import wx
from theideaplace_appkit.sounds import SoundPlayer, quiet_now

from .. import sound_events
from ..agents import AGENTS
from ..sound_events import OTHERS_ALL, OTHERS_NONE, OTHERS_OWN, TOOL_EVENTS, TOOLS_ALL
from ..sessions import WORKING
from .sounds_dialog import SoundsDialog, SoundTutorialDialog

#: How long a loaded chat's turn can go without a sound before "Still working".
#: A long command or long thinking sends no events, so without this the silence
#: would be just when you'd wonder whether anything is happening.
PULSE_SECONDS = 20.0
#: How long after Stop a turn's being interrupted counts as what you did.
STOP_COUNTS_SECONDS = 30.0


class SoundHost:
    _sound_player_made: Optional[SoundPlayer] = None
    #: Quitting: a state already on its way can't start the player again.
    _sound_closed: bool = False
    _sound_last_played: float = 0.0
    #: When the loaded chat was first seen working, and which it was, so its first pulse waits
    #: too, including after moving to another chat that's working.
    _sound_working_since: Optional[float] = None
    _sound_working_key: Optional[str] = None
    #: Chat key -> when Stop was pressed.
    _sound_stops_asked: Optional[dict] = None
    #: How the turn counts stood when last seen: chat key -> (turn, tools, edits).
    _sound_counts: Optional[dict] = None
    #: Chats whose next send is an agent conversation handing a reply on by itself (#248):
    #: Agent reply handed on has said so, so that send makes no sound of its own.
    _sound_hushed_sends: Optional[set] = None

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

    def _sound(self, event_id: str, key: Optional[str] = None, own: bool = True,
               on_its_own: bool = False) -> None:
        """An event happened in the chat with this presentation key. No key means it isn't about
        one chat. ``own`` is whether The Chat Place runs that chat, as opposed to only watching.
        ``on_its_own`` is for what usually answers you but this time didn't, such as a queued
        message going: it holds back during a presentation like anything else that just happens."""
        try:
            self._sound_unchecked(event_id, key, own, on_its_own)
        except Exception:  # noqa: BLE001 - a sound never breaks a turn
            pass

    def _sound_scheme_has(self, event_id: str) -> bool:
        """Whether the scheme playing gives an event a sound. A scheme of your own made before
        the event existed has none for it, nor has one where you chose None."""
        try:
            return sound_events.resolve(event_id, self._sound_scheme()) is not None
        except Exception:  # noqa: BLE001 - a sound never breaks a turn
            return False

    def _sound_hush_send(self, key: str) -> None:
        if self._sound_hushed_sends is None:
            self._sound_hushed_sends = set()
        self._sound_hushed_sends.add(key)

    def _sound_unhush_send(self, key: str) -> None:
        if self._sound_hushed_sends:
            self._sound_hushed_sends.discard(key)

    def _sound_unchecked(self, event_id: str, key: Optional[str], own: bool,
                         on_its_own: bool = False) -> None:
        if (on_its_own and event_id in (sound_events.SENT, sound_events.QUEUED)
                and self._sound_hushed_sends and key in self._sound_hushed_sends):
            # Taken whatever the settings, so it can't hush a later send. Only a send that
            # didn't come from your key press (on_its_own): yours always sounds.
            self._sound_hushed_sends.discard(key)
            return
        settings = self.speech
        if self._sound_closed or not settings.sounds_on or settings.sounds_muted:
            return
        event = sound_events.BY_ID[event_id]
        if key is not None and not self._sound_chat_loaded(key):
            if event_id in TOOL_EVENTS:
                if settings.sound_tools != TOOLS_ALL:
                    return
            elif (settings.sound_others == OTHERS_NONE
                  or (settings.sound_others == OTHERS_OWN and not own)):
                return
        if (on_its_own or not event.answers_you) and quiet_now():
            return
        chosen = sound_events.resolve(event_id, self._sound_scheme())
        if chosen is None:
            return
        path, fallback = chosen
        played = self._sound_player().play(path, key=event_id, priority=event.priority,
                                           volume=settings.sound_volume / 100, fallback=fallback)
        if played:
            self._sound_last_played = time.monotonic()

    def _sound_scheme(self) -> sound_events.Scheme:
        """The scheme playing, read again only when the settings change (a tap can come every
        fifth of a second)."""
        settings = self.speech
        cached = getattr(self, "_sound_scheme_cache", None)
        # The saved dict itself is held, not its id, so a new one can't be mistaken for it.
        if cached is None or cached[0] is not settings.sound_schemes or cached[1] != settings.sound_scheme:
            scheme = sound_events.find(sound_events.load_schemes(settings.sound_schemes),
                                       settings.sound_scheme)
            cached = (settings.sound_schemes, settings.sound_scheme, scheme)
            self._sound_scheme_cache = cached
        return cached[2]

    def _sound_turn_end(self, key: str, own: bool = True) -> None:
        """A turn ended with a reply: which sound depends on whether you're in that chat."""
        self._sound(sound_events.FINISHED if self._sound_chat_loaded(key)
                    else sound_events.FINISHED_ELSEWHERE, key, own)

    def _sound_tick(self, now: Optional[float] = None) -> None:
        """Called by the chat's timer. "Still working" after a silence, loaded chat only."""
        try:
            self._sound_tick_unchecked(time.monotonic() if now is None else now)
        except Exception:  # noqa: BLE001 - the same timer refreshes the chat
            pass

    def _sound_tick_unchecked(self, now: float) -> None:
        settings = self.speech
        if (not settings.sounds_on or settings.sounds_muted or not self._sound_loaded_working()):
            self._sound_working_since = None
            return
        key = self._open.key
        if self._sound_working_since is None or self._sound_working_key != key:
            self._sound_working_since, self._sound_working_key = now, key
        if now - max(self._sound_working_since, self._sound_last_played) >= PULSE_SECONDS:
            self._sound(sound_events.PULSE)
            # Let through or held back, the next waits its full time again.
            self._sound_last_played = now

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
        if not state.turn_id:
            return  # a chat that's unavailable for now: its turn's counts come back with it
        key = f"{provider}:{state.native_id}"
        if self._sound_counts is None:
            self._sound_counts = {}
        tools, edits = getattr(state, "tools_started", 0), getattr(state, "edits_made", 0)
        turn, tools_before, edits_before = self._sound_counts.get(key, (None, 0, 0))
        if turn != state.turn_id:
            tools_before, edits_before = 0, 0
        self._sound_counts[key] = (state.turn_id, tools, edits)
        if edits > edits_before:
            self._sound(sound_events.FILE_CHANGED, key)
        elif tools > tools_before:
            self._sound(sound_events.TOOL, key)

    def _sound_note_stop(self, key: str) -> None:
        """You pressed Stop, so the turn's ending as interrupted is what you did."""
        if self._sound_stops_asked is None:
            self._sound_stops_asked = {}
        self._sound_stops_asked[key] = time.monotonic()

    def _sound_stop_was_asked(self, key: str) -> bool:
        """Whether this ending is the Stop you pressed. By time rather than by turn: a state can
        arrive late, so the turn you stopped can still be reported as starting after you pressed
        it. Long enough for a slow stop, too short to label some later turn."""
        asked = (self._sound_stops_asked or {}).pop(key, None)
        return asked is not None and time.monotonic() - asked < STOP_COUNTS_SECONDS

    def _sound_close(self) -> None:
        self._sound_closed = True
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

    def on_sound_tutorial(self, _event=None) -> None:
        """Help, Sound Tutorial: hear each event's sound in the scheme you use. What it says
        ("No sound.") is said even with announcements silent, because you pressed Space to hear
        something. From the dialog's own window, so VoiceOver doesn't drop it for coming from the
        window behind."""
        dialog = SoundTutorialDialog(self, self.speech, self._sound_player,
                                     say=lambda text: self._say(text, force=True, interrupt=False,
                                                                window=dialog))
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()  # which stops a sound still playing

    def on_sounds(self, _event=None) -> None:
        """File, Sounds: whether there are sounds, which, and for which chats."""
        before = self.speech
        dialog = SoundsDialog(self, self.speech, self._sound_player, say=self._say)
        try:
            ok = dialog.ShowModal() == wx.ID_OK
            chosen = dialog.get_settings() if ok else None
            keep_files, undo_files = dialog.tidy_files(True), dialog.tidy_files(False)
        finally:
            dialog.Destroy()  # which stops a preview still playing
        if not ok:
            undo_files()  # files written for schemes you didn't keep go again
            return
        try:
            chosen.save()
        except OSError as exc:
            # As if cancelled, so the settings in use and the sound files always agree: keeping
            # changes that weren't saved would play files a restart forgets, or delete files the
            # saved settings still use.
            undo_files()
            wx.MessageBox(f"Couldn't save the sound settings, so your changes weren't kept: {exc}",
                          "Sounds", wx.OK | wx.ICON_WARNING, self)
            self.speech = before
            return
        self.speech = chosen
        keep_files()
        self.mute_item.Check(self.speech.sounds_muted)
        if (self.speech.sounds_muted or not self.speech.sounds_on) and self._sound_player_made:
            self._sound_player_made.stop()
        self._feedback("Sound settings saved.")
