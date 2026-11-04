"""File, Sounds (#250). One list and one detail area rather than a row of controls per event,
which would be some 45 tab stops. Choices rather than radio buttons, so Tab can't skip a group
that has nothing chosen (#428)."""
from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path
from typing import Callable, Dict, List

import wx
from theideaplace_appkit import sounds as appkit_sounds

from .. import platform_paths, sound_events
from ..speech import SpeechSettings
from .a11y import refresh_list_item, set_accessible_name, set_list_items_accessible
from .sizes import dialog_size


def keep_custom_sound(source: str) -> str:
    """Copy a person's file into the data folder, so it survives the original moving and goes
    with backups, and return its name there. Raises SoundProblem or OSError, with words to show."""
    appkit_sounds.read_wav(source)
    name = Path(source).name
    if not appkit_sounds.safe_file_name(name):
        raise appkit_sounds.SoundProblem("Its name can't be used for a file here. Rename it first.")
    folder = platform_paths.custom_sounds_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    number = 2
    while target.exists():
        if target.read_bytes() == Path(source).read_bytes():
            return target.name  # chosen before
        target = folder / f"{Path(name).stem} {number}{Path(name).suffix}"
        number += 1
    shutil.copyfile(source, target)
    return target.name


class SoundsDialog(wx.Dialog):
    def __init__(self, parent, speech: SpeechSettings, player: Callable):
        super().__init__(parent, title="Sounds", size=dialog_size(parent, 560, 560))
        self._original = speech
        self._player = player
        self._choices: Dict[str, str] = dict(speech.sound_choices)
        self._events = list(sound_events.EVENTS)
        self._refs: List[str] = []

        outer = wx.BoxSizer(wx.VERTICAL)
        self.play_sounds = wx.CheckBox(self, label="&Play sounds")
        self.play_sounds.SetValue(speech.sounds_on)
        outer.Add(self.play_sounds, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.mute = wx.CheckBox(self, label="M&ute all sounds")
        self.mute.SetValue(speech.sounds_muted)
        outer.Add(self.mute, 0, wx.ALL, 10)

        grid = wx.FlexGridSizer(cols=2, vgap=8, hgap=8)
        grid.AddGrowableCol(1, 1)

        def row(label, choices, name, selection):
            grid.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            choice = wx.Choice(self, choices=choices)
            set_accessible_name(choice, name)
            choice.SetSelection(selection)
            grid.Add(choice, 0)
            return choice

        volumes = sound_events.VOLUMES
        self.volume = row("&Volume:", [f"{v}%" for v in volumes], "Volume",
                          volumes.index(speech.sound_volume)
                          if speech.sound_volume in volumes
                          else volumes.index(sound_events.DEFAULT_VOLUME))
        self._style_ids = list(sound_events.STYLES)
        self.style = row("St&yle:", list(sound_events.STYLES.values()), "Style",
                         self._style_ids.index(speech.sound_style)
                         if speech.sound_style in self._style_ids else 0)
        self._tool_ids = list(sound_events.TOOLS_LABELS)
        self.tools = row("&Tool sounds for:", list(sound_events.TOOLS_LABELS.values()),
                         "Tool sounds for",
                         self._tool_ids.index(speech.sound_tools)
                         if speech.sound_tools in self._tool_ids else 0)
        self._other_ids = list(sound_events.OTHERS_LABELS)
        self.others = row("&Other chats:", list(sound_events.OTHERS_LABELS.values()),
                          "Sounds from other chats",
                          self._other_ids.index(speech.sound_others)
                          if speech.sound_others in self._other_ids else 0)
        outer.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        outer.Add(wx.StaticText(self, label="Sound &events:"), 0, wx.LEFT | wx.RIGHT, 10)
        self.events = wx.ListBox(self, choices=self._rows(), style=wx.LB_SINGLE)
        set_accessible_name(self.events, "Sound events")
        set_list_items_accessible(self.events, lambda: "Sound events", lambda index: None)
        self.events.SetSelection(0)
        outer.Add(self.events, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        detail = wx.BoxSizer(wx.HORIZONTAL)
        detail.Add(wx.StaticText(self, label="&Sound:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.sound = wx.Choice(self)
        set_accessible_name(self.sound, "Sound")
        detail.Add(self.sound, 1, wx.RIGHT, 8)
        self.preview = wx.Button(self, label="P&review")
        detail.Add(self.preview, 0, wx.RIGHT, 8)
        self.choose = wx.Button(self, label="Choose &File...")
        detail.Add(self.choose, 0)
        outer.Add(detail, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        outer.Add(buttons, 0, wx.EXPAND | wx.ALL, 10)
        self.SetSizer(outer)

        self.events.Bind(wx.EVT_LISTBOX, lambda e: self._show_event())
        self.sound.Bind(wx.EVT_CHOICE, lambda e: self._sound_chosen())
        self.preview.Bind(wx.EVT_BUTTON, lambda e: self._preview())
        self.choose.Bind(wx.EVT_BUTTON, lambda e: self._choose_file())
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)
        self._show_event()
        self.play_sounds.SetFocus()

    def _on_destroy(self, event):
        if event.GetEventObject() is self:
            self.stop_preview()
        event.Skip()

    def _rows(self) -> List[str]:
        return [sound_events.row_label(e.id, self._choices) for e in self._events]

    def _event_id(self) -> str:
        return self._events[max(self.events.GetSelection(), 0)].id

    def _show_event(self) -> None:
        """The Sound choice, for the selected event."""
        event_id = self._event_id()
        current = sound_events.ref_for(self._choices, event_id)
        self._refs = [sound_events.NONE_REF]
        self._refs += [sound_events.built_in_ref(name) for name in sound_events.BUILT_IN]
        self._refs += [sound_events.custom_ref(name) for name in sound_events.custom_files()]
        if current not in self._refs:
            self._refs.append(current)  # a file that has gone: it still shows what's chosen
        self.sound.Set([sound_events.ref_label(ref) for ref in self._refs])
        self.sound.SetSelection(self._refs.index(current))

    def _sound_chosen(self) -> None:
        index = self.sound.GetSelection()
        if 0 <= index < len(self._refs):
            self._set_ref(self._refs[index])

    def _set_ref(self, ref: str) -> None:
        event_id = self._event_id()
        self._choices[event_id] = ref
        row = self.events.GetSelection()
        self.events.SetString(row, sound_events.row_label(event_id, self._choices))
        refresh_list_item(self.events)  # Orca: the row changed under it

    def _style_id(self) -> str:
        return self._style_ids[max(self.style.GetSelection(), 0)]

    def _volume(self) -> int:
        return sound_events.VOLUMES[max(self.volume.GetSelection(), 0)]

    def _preview(self) -> None:
        """The sound showing, at the volume and style showing, even when muted. No words: you're
        listening to it."""
        event_id = self._event_id()
        chosen = sound_events.resolve(event_id, self._choices, self._style_id())
        if chosen is None:
            self.stop_preview()
            return
        self._player().preview(chosen[0], volume=self._volume() / 100)

    def stop_preview(self) -> None:
        try:
            self._player().stop_preview()
        except Exception:  # noqa: BLE001 - closing a dialog must never fail on a sound
            pass

    def _choose_file(self) -> None:
        with wx.FileDialog(self, "Choose a sound", wildcard="WAV files (*.wav)|*.wav",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as picker:
            if picker.ShowModal() != wx.ID_OK:
                self.choose.SetFocus()
                return
            path = picker.GetPath()
        try:
            name = keep_custom_sound(path)
        except (appkit_sounds.SoundProblem, OSError) as exc:
            wx.MessageBox(f"That sound can't be used. {exc}", "Sounds",
                          wx.OK | wx.ICON_WARNING, self)
            self.choose.SetFocus()
            return
        self._set_ref(sound_events.custom_ref(name))
        self._show_event()
        # Here rather than back on Choose File, so what's read says it worked and which file:
        # cancelling the picker sounds just like coming back to the button.
        self.sound.SetFocus()

    def get_settings(self) -> SpeechSettings:
        return dataclasses.replace(
            self._original,
            sounds_on=self.play_sounds.GetValue(),
            sounds_muted=self.mute.GetValue(),
            sound_volume=self._volume(),
            sound_style=self._style_id(),
            sound_tools=self._tool_ids[max(self.tools.GetSelection(), 0)],
            sound_others=self._other_ids[max(self.others.GetSelection(), 0)],
            sound_choices=sound_events.clean_choices(self._choices))
