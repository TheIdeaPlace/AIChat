"""File, Sounds (#250). One list and one detail area rather than a row of controls per event,
which would be some 45 tab stops. Choices rather than radio buttons, so Tab can't skip a group
that has nothing chosen (#428).

Sound schemes work as in QuickMail and app-kit's sounds/SCHEMES.md: each built-in scheme (Glass,
Soft bell, Wood) keeps its own changes, and you can make, share and take schemes of your own.
Every sound file of your own is kept in its scheme's own folder (sound_events.scheme_folder).
What a scheme does is held here until OK, and so is deleting files: a scheme deleted or reset,
or a file removed, loses its files only on OK (``finish``). Files a scheme brings in, and ones
Choose File copies, are written straight away; Cancel takes away the folder of a scheme made
here."""
from __future__ import annotations

import dataclasses
import shutil
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

import wx
from theideaplace_appkit import soundschemes as shared
from theideaplace_appkit import sounds as appkit_sounds

from .. import sound_events
from ..speech import SpeechSettings
from .a11y import refresh_list_item, set_accessible_name, set_list_items_accessible
from .sizes import dialog_size, dip_width

# The Scheme Actions menu.
NEW, COPY, RENAME, DELETE, RESET, EXPORT, IMPORT = (wx.NewIdRef() for _ in range(7))


def keep_custom_sound(source: str, scheme: sound_events.Scheme) -> str:
    """Copy a person's file into ``scheme``'s folder in the data folder, so it survives the
    original moving and goes with backups, and return its name there. Raises SoundProblem or
    OSError, with words to show."""
    appkit_sounds.read_wav(source)
    name = Path(source).name
    if not name.lower().endswith(".wav"):
        # Only .wav files are offered for the other events, so it would be lost from the list.
        raise appkit_sounds.SoundProblem("Its name has to end in .wav.")
    if not shared.custom_name_ok(name):
        raise appkit_sounds.SoundProblem("Its name can't be used for a file here. Rename it first.")
    folder = sound_events.scheme_folder(scheme)
    if folder is None:
        raise appkit_sounds.SoundProblem("This scheme can't keep sound files.")
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


def preview_event(player: Callable, event_id: str, scheme: sound_events.Scheme, volume: int) -> bool:
    """Play ``event_id``'s sound in ``scheme`` at ``volume`` percent, even with sounds off or
    muted: someone asked to hear it. ``player()`` gives the window's player, made only when
    there's something to play. False when the scheme gives the event no sound. File, Sounds and
    Help, Sound Tutorial both play through this, so they sound the same."""
    chosen = sound_events.resolve(event_id, scheme)
    if chosen is None:
        return False
    player().preview(chosen[0], volume=volume / 100, fallback=chosen[1])
    return True


def _default_gallery():
    from .. import sound_gallery
    return sound_gallery.gallery()


TOO_MANY = (f"You have {sound_events.MAX_USER_SCHEMES} sound schemes of your own, the most there can "
            "be. Delete one first.")


class SoundsDialog(wx.Dialog):
    def __init__(self, parent, speech: SpeechSettings, player: Callable,
                 say: Optional[Callable[..., None]] = None,
                 make_gallery: Optional[Callable] = None):
        super().__init__(parent, title="Sounds", size=dialog_size(parent, 560, 640))
        self._original = speech
        self._player = player
        self._say = say or (lambda text, **kw: None)
        # The gallery (and Cryptodome's signature code) loads only when you browse: a problem
        # there can't stop the app starting.
        self._make_gallery = make_gallery or _default_gallery
        self._previewed = False  # so closing doesn't make a player only to stop it
        self._schemes = [s.copy() for s in sound_events.load_schemes(speech.sound_schemes)]
        self._scheme_id = sound_events.find(self._schemes, speech.sound_scheme).id
        # What happens to sound files when the dialog closes (finish).
        self._file_changes = sound_events.FileChanges()
        self._events = list(sound_events.EVENTS)
        self._refs: List[str] = []

        outer = wx.BoxSizer(wx.VERTICAL)
        self.play_sounds = wx.CheckBox(self, label="&Play sounds")
        self.play_sounds.SetValue(speech.sounds_on)
        outer.Add(self.play_sounds, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.mute = wx.CheckBox(self, label="M&ute all sounds")
        self.mute.SetValue(speech.sounds_muted)
        outer.Add(self.mute, 0, wx.ALL, 10)
        # Windows' notifications only. Off unless chosen: it's a change to what Windows does.
        self.silent_notifications = None
        if sys.platform == "win32":
            self.silent_notifications = wx.CheckBox(
                self, label="Disable &Windows sound for The Chat Place's notifications")
            self.silent_notifications.SetValue(speech.sound_silent_notifications)
            outer.Add(self.silent_notifications, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

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
        self.scheme = row("Sound sc&heme:", [], "Sound scheme", wx.NOT_FOUND)
        self._tool_ids = list(sound_events.TOOLS_LABELS)
        self.tools = row("&Tool sounds play for:", list(sound_events.TOOLS_LABELS.values()),
                         "Tool sounds play for",
                         self._tool_ids.index(speech.sound_tools)
                         if speech.sound_tools in self._tool_ids else 0)
        self._other_ids = list(sound_events.OTHERS_LABELS)
        self.others = row("&Other sounds play for:", list(sound_events.OTHERS_LABELS.values()),
                          "Other sounds play for",
                          self._other_ids.index(speech.sound_others)
                          if speech.sound_others in self._other_ids else 0)
        outer.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        schemes_row = wx.BoxSizer(wx.HORIZONTAL)
        # One button with a menu rather than seven: a menu's turned-off items still say they're
        # there, and Tab has fewer stops.
        self.actions = wx.Button(self, label="Scheme &Actions...")
        schemes_row.Add(self.actions, 0, wx.RIGHT, 8)
        self.browse = wx.Button(self, label="&Browse Gallery...")
        schemes_row.Add(self.browse, 0)
        outer.Add(schemes_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        self.events_label = wx.StaticText(self, label="Sound &events:")
        outer.Add(self.events_label, 0, wx.LEFT | wx.RIGHT, 10)
        self.events = wx.ListBox(self, style=wx.LB_SINGLE)
        set_accessible_name(self.events, "Sound events")
        set_list_items_accessible(self.events, self._events_name, lambda index: None)
        outer.Add(self.events, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        detail = wx.BoxSizer(wx.HORIZONTAL)
        detail.Add(wx.StaticText(self, label="&Sound:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.sound = wx.Choice(self)
        set_accessible_name(self.sound, "Sound")
        detail.Add(self.sound, 1, wx.RIGHT, 8)
        self.preview = wx.Button(self, label="P&review")
        detail.Add(self.preview, 0, wx.RIGHT, 8)
        self.choose = wx.Button(self, label="Choose &File...")
        detail.Add(self.choose, 0, wx.RIGHT, 8)
        self.remove_file = wx.Button(self, label="Re&move File...")
        detail.Add(self.remove_file, 0)
        outer.Add(detail, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        outer.Add(buttons, 0, wx.EXPAND | wx.ALL, 10)
        self.SetSizer(outer)

        self.scheme.Bind(wx.EVT_CHOICE, lambda e: self._scheme_chosen())
        self.actions.Bind(wx.EVT_BUTTON, lambda e: self._show_actions())
        self.browse.Bind(wx.EVT_BUTTON, lambda e: self.on_browse())
        self.events.Bind(wx.EVT_LISTBOX, lambda e: self._show_event())
        self.events.Bind(wx.EVT_KEY_DOWN, self._on_events_key)
        self.sound.Bind(wx.EVT_CHOICE, lambda e: self._sound_chosen())
        self.preview.Bind(wx.EVT_BUTTON, lambda e: self._preview())
        self.choose.Bind(wx.EVT_BUTTON, lambda e: self._choose_file())
        self.remove_file.Bind(wx.EVT_BUTTON, lambda e: self.on_remove_file())
        self.Bind(wx.EVT_MENU, lambda e: self.on_new(), id=NEW)
        self.Bind(wx.EVT_MENU, lambda e: self.on_copy(), id=COPY)
        self.Bind(wx.EVT_MENU, lambda e: self.on_rename(), id=RENAME)
        self.Bind(wx.EVT_MENU, lambda e: self.on_delete(), id=DELETE)
        self.Bind(wx.EVT_MENU, lambda e: self.on_reset(), id=RESET)
        self.Bind(wx.EVT_MENU, lambda e: self.on_export(), id=EXPORT)
        self.Bind(wx.EVT_MENU, lambda e: self.on_import(), id=IMPORT)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)
        self._fill_schemes()
        self._fill_events(0)
        self.play_sounds.SetFocus()

    def _on_destroy(self, event):
        if event.GetEventObject() is self:
            self.stop_preview()
        event.Skip()

    # -- schemes -------------------------------------------------------------------

    def current(self) -> sound_events.Scheme:
        return sound_events.find(self._schemes, self._scheme_id)

    def _fill_schemes(self) -> None:
        self.scheme.Set([s.name for s in self._schemes])
        self.scheme.SetSelection(self._schemes.index(self.current()))

    def _scheme_chosen(self) -> None:
        index = self.scheme.GetSelection()
        if 0 <= index < len(self._schemes):
            self._scheme_id = self._schemes[index].id
            self._fill_events(max(self.events.GetSelection(), 0))

    def _select_scheme(self, scheme_id: str, focus: bool = True) -> None:
        """Show ``scheme_id``, with the list in its usual order, and put focus on the choice."""
        own = sorted((s for s in self._schemes if not s.built_in), key=lambda s: s.name.casefold())
        self._schemes = [s for s in self._schemes if s.built_in] + own
        self._scheme_id = scheme_id
        self._fill_schemes()
        self._fill_events(max(self.events.GetSelection(), 0))
        if focus:
            self.scheme.SetFocus()

    def _names(self, leaving_out: Optional[sound_events.Scheme] = None) -> List[str]:
        return [s.name for s in self._schemes if s is not leaving_out]

    def _ask_name(self, title: str, value: str, leaving_out: Optional[sound_events.Scheme] = None) -> Optional[str]:
        """A name for a scheme, asked until it's one that can be used, or None for Cancel."""
        while True:
            with wx.TextEntryDialog(self, "Name:", title, value) as ask:
                ask.SetMaxLength(shared.MAX_NAME)
                if ask.ShowModal() != wx.ID_OK:
                    return None
                value = ask.GetValue().strip()
            problem = shared.name_problem(value, self._names(leaving_out))
            if problem is None:
                return value
            wx.MessageBox(problem, title, wx.OK | wx.ICON_WARNING, self)

    def _show_actions(self) -> None:
        scheme = self.current()
        changed = scheme.built_in and bool(sound_events.save_schemes([scheme]))
        menu = wx.Menu()
        menu.Append(NEW, "&New Scheme...")
        menu.Append(COPY, "&Copy Scheme...")
        menu.Append(RENAME, "&Rename Scheme...").Enable(not scheme.built_in)
        menu.Append(DELETE, "&Delete Scheme...").Enable(not scheme.built_in)
        menu.Append(RESET, "Re&set Scheme...").Enable(changed)
        menu.AppendSeparator()
        menu.Append(EXPORT, "&Export Scheme...")
        menu.Append(IMPORT, "&Import Scheme...")
        self.PopupMenu(menu, self.actions.GetPosition() + wx.Point(0, self.actions.GetSize().height))
        menu.Destroy()

    def _add(self, scheme: sound_events.Scheme) -> None:
        self._schemes.append(scheme)
        self._file_changes.made.add(scheme.id)
        self._select_scheme(scheme.id)

    def _room_for_another(self, title: str, back_to: wx.Window) -> bool:
        """Whether another scheme of your own can be added: past the most, the next start would
        leave some out."""
        if sum(1 for s in self._schemes if not s.built_in) < sound_events.MAX_USER_SCHEMES:
            return True
        wx.MessageBox(TOO_MANY, title, wx.OK | wx.ICON_WARNING, self)
        back_to.SetFocus()
        return False

    def on_new(self) -> None:
        if not self._room_for_another("New Sound Scheme", self.actions):
            return
        name = self._ask_name("New Sound Scheme", "")
        if name is None:
            self.actions.SetFocus()
            return
        self._add(sound_events.Scheme(sound_events.new_id(), name, False,
                                      {e.key: sound_events.NONE_REF for e in self._events}))
        self._say(f"Made the sound scheme {name}. Every event is silent until you choose a sound.")

    def on_copy(self) -> None:
        if not self._room_for_another("Copy Sound Scheme", self.actions):
            return
        source = self.current()
        name = self._ask_name("Copy Sound Scheme", shared.unique_name(f"{source.name} copy", self._names()))
        if name is None:
            self.actions.SetFocus()
            return
        events = shared.portable(sound_events.APP, source.events, source.built_in, source.pack)
        copy = sound_events.Scheme(sound_events.new_id(), name, False, events, source.author,
                                   source.license, source.description)
        # Its files are copied into its own folder now, as Import's are.
        try:
            sound_events.copy_files(source, copy)
        except OSError as exc:
            sound_events.remove_scheme_folder(copy.id)
            wx.MessageBox(f"The scheme's sound files couldn't be copied: {exc.strerror or exc}",
                          "Copy Sound Scheme", wx.OK | wx.ICON_WARNING, self)
            self.actions.SetFocus()
            return
        self._add(copy)
        self._say(f"Made {name}, a copy of {source.name}.")

    def on_rename(self) -> None:
        scheme = self.current()
        if scheme.built_in:
            return
        name = self._ask_name("Rename Sound Scheme", scheme.name, leaving_out=scheme)
        if name is None:
            self.actions.SetFocus()
            return
        scheme.name = name
        self._select_scheme(scheme.id)

    def on_delete(self) -> None:
        scheme = self.current()
        if scheme.built_in:
            return
        if wx.MessageBox(f"Delete the sound scheme {scheme.name} and its sound files? They're "
                         "deleted when you choose OK.",
                         "Delete Sound Scheme", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
                         self) != wx.YES:
            self.actions.SetFocus()
            return
        self._schemes.remove(scheme)
        self._file_changes.deleted.add(scheme.id)
        self._select_scheme(sound_events.DEFAULT_SCHEME)
        self._say(f"Deleted {scheme.name}. Glass is chosen.")

    def on_reset(self) -> None:
        scheme = self.current()
        if not scheme.built_in or not sound_events.save_schemes([scheme]):
            return
        files = (" Your sound files for it are deleted when you choose OK."
                 if sound_events.custom_files(scheme) else "")
        if wx.MessageBox(f"Reset {scheme.name} to how it ships? Every event goes back to its "
                         f"built-in sound.{files}", "Reset Sound Scheme",
                         wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) != wx.YES:
            self.actions.SetFocus()
            return
        scheme.events = {}
        self._file_changes.reset.add(scheme.id)
        self._select_scheme(scheme.id)
        self._say(f"{scheme.name} is back to how it ships.")

    def on_export(self) -> None:
        scheme = self.current()
        extension = sound_events.APP.extension
        safe = "".join(c if c.isalnum() or c in " -_()" else "-" for c in scheme.name).strip(" -")
        with wx.FileDialog(self, "Export Sound Scheme", defaultFile=(safe or "Sound scheme") + extension,
                           wildcard=f"Sound schemes (*{extension})|*{extension}",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as picker:
            if picker.ShowModal() != wx.ID_OK:
                self.actions.SetFocus()
                return
            path = picker.GetPath()
        if not path.lower().endswith(extension):
            path += extension
        missing = sound_events.missing_files(scheme)
        try:
            Path(path).write_bytes(sound_events.export(scheme))
        except OSError as exc:
            wx.MessageBox(f"The scheme couldn't be saved: {exc.strerror or exc}", "Export Sound Scheme",
                          wx.OK | wx.ICON_WARNING, self)
            self.actions.SetFocus()
            return
        if missing:
            # Shown, not only said: the file you'd share is missing sounds you'd expect in it.
            wx.MessageBox(f"Exported {scheme.name}. {missing} event{'s' if missing != 1 else ''} "
                          f"{'use' if missing != 1 else 'uses'} a sound file that's missing, so "
                          f"{'they have' if missing != 1 else 'it has'} no sound in the file.",
                          "Export Sound Scheme", wx.OK | wx.ICON_WARNING, self)
        else:
            self._say(f"Exported {scheme.name}.")
        self.actions.SetFocus()

    def on_import(self) -> None:
        extension = sound_events.APP.extension
        with wx.FileDialog(self, "Import Sound Scheme",
                           wildcard=f"Sound schemes (*{extension})|*{extension}",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as picker:
            if picker.ShowModal() != wx.ID_OK:
                self.actions.SetFocus()
                return
            path = picker.GetPath()
        if self._room_for_another("Import Sound Scheme", self.actions):
            self._take(path, "Import Sound Scheme", self.actions)

    def _take(self, source, title: str, back_to: wx.Window,
              gallery_source: Optional[Dict[str, str]] = None,
              replacing: Optional[sound_events.Scheme] = None) -> bool:
        """Add a scheme from a file or downloaded bytes, or update ``replacing`` with it. On a
        problem, say so and put focus on ``back_to``; on success, focus is on the scheme."""
        try:
            # An update goes in the scheme's own folder; a new scheme gets its own.
            imported = sound_events.import_scheme(source, self._names(leaving_out=replacing), gallery_source,
                                                  scheme_id=replacing.id if replacing is not None else None)
        except (shared.SchemeFileProblem, OSError) as exc:
            wx.MessageBox(str(exc), title, wx.OK | wx.ICON_WARNING, self)
            back_to.SetFocus()
            return False
        if replacing is not None:
            # On OK, files only the old version played go.
            self._file_changes.reset.add(replacing.id)
            replacing.events = imported.scheme.events
            replacing.author, replacing.license = imported.scheme.author, imported.scheme.license
            replacing.description, replacing.source = imported.scheme.description, gallery_source
            self._select_scheme(replacing.id)
            done = f"Updated {replacing.name}."
        else:
            self._add(imported.scheme)
            done = imported.message()
        if imported.dropped:
            # Shown, not only said, so it's there with announcements off too.
            wx.MessageBox(done + " Choose OK in Sounds to keep it.", title, wx.OK | wx.ICON_WARNING, self)
            self.scheme.SetFocus()
        else:
            self._say(done + " Choose OK to keep it.")
        return True

    def on_browse(self) -> None:
        from .. import sound_gallery
        from .sound_gallery_dialog import SoundGalleryDialog
        dialog = SoundGalleryDialog(self, self._schemes, self._make_gallery, self._say)
        try:
            if dialog.ShowModal() != wx.ID_OK or dialog.result is None:
                self.browse.SetFocus()
                return
            entry, data = dialog.result
        finally:
            dialog.Destroy()
        replacing = sound_gallery.added_as(entry, self._schemes)
        if replacing is None and not self._room_for_another("Sound Scheme Gallery", self.browse):
            return
        if replacing is not None and wx.MessageBox(
                f"Replace your copy of {replacing.name} with the gallery's newer version? "
                "Any changes you made to it are lost.", "Update Sound Scheme",
                wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) != wx.YES:
            self.browse.SetFocus()
            return
        self._take(data, "Sound Scheme Gallery", self.browse,
                   {"gallery": entry.id, "sha256": entry.sha256}, replacing)

    # -- events --------------------------------------------------------------------

    def _events_name(self) -> str:
        """The list's name for a screen reader, with how many events have a sound."""
        return f"Sound events, {sound_events.count_label(self.current())}"

    def _show_count(self) -> None:
        self.events_label.SetLabel(f"Sound &events ({sound_events.count_label(self.current())}):")
        # Windows reads _events_name through the list's own accessible; VoiceOver and Orca need
        # the name set on the native list each time it changes.
        set_accessible_name(self.events, self._events_name())

    def _fill_events(self, row: int) -> None:
        scheme = self.current()
        self._show_count()
        self.events.Set([sound_events.row_label(e.id, scheme) for e in self._events])
        self.events.SetSelection(min(max(row, 0), len(self._events) - 1))
        self._show_event()
        self.Layout()

    def _event_id(self) -> str:
        return self._events[max(self.events.GetSelection(), 0)].id

    def _show_event(self) -> None:
        """The Sound choice, for the selected event."""
        scheme = self.current()
        current = sound_events.ref_for(scheme, self._event_id())
        # A file you've removed isn't offered again, though it's there until OK.
        removed = {sound_events.custom_ref(name) for sid, name in self._file_changes.removed
                   if sid == scheme.id}
        self._refs = [ref for ref in sound_events.offered(scheme) if ref not in removed]
        if str(current) not in self._refs:
            self._refs.append(str(current))  # a file that has gone: it still shows what's chosen
        self.sound.Set([sound_events.ref_label(sound_events.parse_ref(ref), scheme,
                                               sound_events.custom_missing(sound_events.parse_ref(ref), scheme))
                        for ref in self._refs])
        self.sound.SetSelection(self._refs.index(str(current)))
        self.remove_file.Enable(current.kind == "custom" and not sound_events.custom_missing(current, scheme))

    def _sound_chosen(self) -> None:
        index = self.sound.GetSelection()
        if 0 <= index < len(self._refs):
            self._set_ref(self._refs[index])

    def _set_ref(self, ref: str) -> None:
        scheme = self.current()
        event = sound_events.BY_ID[self._event_id()]
        scheme.events[event.key] = ref
        row = self.events.GetSelection()
        self.events.SetString(row, sound_events.row_label(event.id, scheme))
        self._show_count()
        refresh_list_item(self.events)  # Orca: the row changed under it
        current = sound_events.ref_for(scheme, event.id)
        self.remove_file.Enable(current.kind == "custom" and not sound_events.custom_missing(current, scheme))

    def _volume(self) -> int:
        return sound_events.VOLUMES[max(self.volume.GetSelection(), 0)]

    def _on_events_key(self, event: wx.KeyEvent) -> None:
        """Space on an event plays its sound, so you can arrow through the list and hear each one.
        Every system has it: Alt+R reaches Preview only where buttons have access keys, which a
        Mac doesn't (app-kit's sounds/README.md makes this the standard for every app)."""
        if event.GetKeyCode() == wx.WXK_SPACE and not event.HasAnyModifiers():
            self._preview()
            return
        event.Skip()

    def _preview(self) -> None:
        """The sound showing, at the volume and in the scheme showing, even when muted. No words:
        you're listening to it."""
        if preview_event(self._player, self._event_id(), self.current(), self._volume()):
            self._previewed = True
        else:
            self.stop_preview()

    def stop_preview(self) -> None:
        if not self._previewed:
            return
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
            name = keep_custom_sound(path, self.current())
        except (appkit_sounds.SoundProblem, OSError) as exc:
            wx.MessageBox(f"That sound can't be used. {exc}", "Sounds",
                          wx.OK | wx.ICON_WARNING, self)
            self.choose.SetFocus()
            return
        self._file_changes.removed.discard((self.current().id, name))  # chosen again: it stays
        self._set_ref(sound_events.custom_ref(name))
        self._show_event()
        # Here rather than back on Choose File, so what's read says it worked and which file:
        # cancelling the picker sounds just like coming back to the button.
        self.sound.SetFocus()

    def on_remove_file(self) -> None:
        """Take one of your own sound files off the event, which goes back to its built-in
        sound. The file is deleted from the scheme's folder on OK, unless another event in the
        scheme still plays it."""
        scheme = self.current()
        event = sound_events.BY_ID[self._event_id()]
        current = sound_events.ref_for(scheme, event.id)
        if current.kind != "custom" or sound_events.custom_missing(current, scheme):
            return
        others = sound_events.events_using(scheme, current.name, leaving_out=event.id)
        if others:
            names = others[0] if len(others) == 1 else f"{', '.join(others[:-1])} and {others[-1]}"
            question = (f"Remove the sound file {current.name} from {event.label}? It goes back to "
                        f"its built-in sound. {names} still {'uses' if len(others) == 1 else 'use'} "
                        "the file, so it's kept.")
        else:
            question = (f"Remove the sound file {current.name}? {event.label} goes back to its "
                        "built-in sound, and the file is deleted when you choose OK.")
        if wx.MessageBox(question, "Remove Sound File",
                         wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) != wx.YES:
            self.remove_file.SetFocus()
            return
        sound_events.without_file(scheme, event.id)
        if not others:
            self._file_changes.removed.add((scheme.id, current.name))
        self._fill_events(self.events.GetSelection())
        self._say(f"Removed {current.name}.")
        self.sound.SetFocus()

    def tidy_files(self, ok: bool) -> Callable[[], None]:
        """What to call once the dialog has closed (after OK, once the settings are saved): it
        deletes the files of a scheme deleted or reset and a file removed, or, after Cancel,
        the folders of schemes made here. Made before the dialog is destroyed, and holding no
        part of it."""
        changes = self._file_changes
        kept = ([s.copy() for s in self._schemes] if ok
                else sound_events.load_schemes(self._original.sound_schemes))
        return lambda: sound_events.apply_file_changes(changes, kept, ok)

    def get_settings(self) -> SpeechSettings:
        return dataclasses.replace(
            self._original,
            sounds_on=self.play_sounds.GetValue(),
            sounds_muted=self.mute.GetValue(),
            sound_volume=self._volume(),
            sound_scheme=self._scheme_id,
            sound_tools=self._tool_ids[max(self.tools.GetSelection(), 0)],
            sound_others=self._other_ids[max(self.others.GetSelection(), 0)],
            sound_schemes=sound_events.save_schemes(self._schemes),
            sound_silent_notifications=(self.silent_notifications.GetValue()
                                        if self.silent_notifications is not None
                                        else self._original.sound_silent_notifications))


class SoundTutorialDialog(wx.Dialog):
    """Help, Sound Tutorial: every event by name, and Space (or Enter) plays its sound in the
    scheme you're using, at your volume, to learn what each means. Nothing here changes a
    setting: sound names, files and other schemes are File, Sounds."""

    def __init__(self, parent, speech: SpeechSettings, player: Callable,
                 say: Optional[Callable[[str], None]] = None):
        super().__init__(parent, title="Sound Tutorial", size=dialog_size(parent, 440, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._player = player
        self._say = say or (lambda text: None)
        self._previewed = False
        self._scheme = sound_events.find(sound_events.load_schemes(speech.sound_schemes),
                                         speech.sound_scheme)
        # The volume sounds really play at, as SoundHost plays them.
        self._volume = speech.sound_volume
        self._events = list(sound_events.EVENTS)

        outer = wx.BoxSizer(wx.VERTICAL)
        self.intro = wx.StaticText(self, label=sound_events.tutorial_intro(self._scheme))
        self.intro.Wrap(dip_width(self, 400))
        outer.Add(self.intro, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)
        outer.Add(wx.StaticText(self, label="Sound &events:"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.events = wx.ListBox(self, choices=[e.label for e in self._events], style=wx.LB_SINGLE)
        set_accessible_name(self.events, "Sound events")
        set_list_items_accessible(self.events, lambda: "Sound events", lambda index: None)
        outer.Add(self.events, 1, wx.EXPAND | wx.ALL, 10)
        self.close = wx.Button(self, wx.ID_CANCEL, "&Close")
        outer.Add(self.close, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.SetSizer(outer)
        self.SetEscapeId(wx.ID_CANCEL)

        # Space as in File, Sounds' list, which works on a Mac too (#574). Enter is caught before
        # the dialog would take it as pressing a button, so it plays and the dialog stays open.
        self.events.Bind(wx.EVT_KEY_DOWN, self._on_events_key)
        self.events.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.play())
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)
        self.events.SetSelection(0)
        self.events.SetFocus()

    def _on_destroy(self, event):
        if event.GetEventObject() is self:
            self.stop_preview()
        event.Skip()

    def _on_events_key(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_SPACE and not event.HasAnyModifiers():
            self.play()
            return
        event.Skip()

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        from .dialogs import press_focused_button_on_a_mac
        if press_focused_button_on_a_mac(event):
            return
        if (event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
                and not event.HasAnyModifiers()
                and (wx.Window.FindFocus() is self.events or event.GetEventObject() is self.events)):
            self.play()
            return
        event.Skip()

    def event_id(self) -> str:
        return self._events[max(self.events.GetSelection(), 0)].id

    def play(self) -> None:
        """The selected event's sound, even with sounds off or muted: you asked to hear it. When
        there's none to play, or your file for it has gone, a few words say so."""
        event_id = self.event_id()
        note = sound_events.tutorial_note(event_id, self._scheme)
        if preview_event(self._player, event_id, self._scheme, self._volume):
            self._previewed = True
        else:
            self.stop_preview()  # the last event's sound mustn't run on under "No sound"
        if note:
            self._say(note)

    def stop_preview(self) -> None:
        if not self._previewed:
            return
        try:
            self._player().stop_preview()
        except Exception:  # noqa: BLE001 - closing a dialog must never fail on a sound
            pass
