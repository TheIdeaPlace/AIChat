"""The dialogs, built from the shared descriptions, for every integration alike.

A new integration needs no new dialog: its choices arrive as ``OptionGroup``s,
its requests as ``PermissionRequest`` and ``QuestionRequest``. Permission buttons
carry this app's words, never an agent's.

Radio buttons aren't used for questions or options: with nothing chosen, Windows'
Tab skips a whole radio group (the shipped app's #428). A list always takes focus.
"""
from __future__ import annotations

import threading
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import wx

from .. import ui_text
from ..contracts import DECISION_LABELS, Decision, OptionGroup, PermissionRequest, QuestionRequest
from . import a11y

_BUTTON_LABELS = {Decision.ALLOW_ONCE: "&Allow Once", Decision.DENY: "&Deny"}


def _labelled(parent: wx.Window, sizer: wx.Sizer, label: str, control: wx.Window, proportion: int = 0) -> None:
    text = wx.StaticText(parent, label=label)
    # The control exists already. Its label goes ahead of it, for reading order
    # and because Windows looks for a label just before a control.
    text.MoveBeforeInTabOrder(control)
    sizer.Add(text, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
    sizer.Add(control, proportion, wx.EXPAND | wx.ALL, 8)
    name = label.replace("&", "").rstrip(":")
    if isinstance(control, wx.ListBox):
        # SetName doesn't reach a list box in MSAA.
        a11y.set_list_items_accessible(control, lambda: name, lambda _index: None)
    else:
        a11y.set_accessible_name(control, name)


def _value_label(label: str, description: str) -> str:
    return f"{label}: {description}" if description else label


class TextDialog(wx.Dialog):
    """Read something long. Escape closes it."""

    def __init__(self, parent: wx.Window, title: str, text: str):
        super().__init__(parent, title=title, size=(760, 520), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.text = wx.TextCtrl(self, value=text, style=wx.TE_MULTILINE | wx.TE_READONLY)
        a11y.set_accessible_name(self.text, title)
        sizer.Add(self.text, 1, wx.EXPAND | wx.ALL, 8)
        close = wx.Button(self, wx.ID_CANCEL, "Close")
        sizer.Add(close, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.text.SetFocus()


class PermissionDialog(wx.Dialog):
    """Escape, or closing it, is Deny. ``decision`` holds the answer."""

    def __init__(self, parent: wx.Window, chat_title: str, request: PermissionRequest):
        super().__init__(parent, title=f"Permission: {chat_title}", size=(640, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.decision = Decision.DENY
        self.request = request
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.text = wx.TextCtrl(self, value=ui_text.permission_text(request.summary, request.detail, request.caution),
                                style=wx.TE_MULTILINE | wx.TE_READONLY)
        _labelled(self, sizer, "What's being asked:", self.text, 1)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.buttons: Dict[Decision, wx.Button] = {}
        for decision in request.decisions:
            button = wx.Button(self, label=_BUTTON_LABELS[decision])
            button.Bind(wx.EVT_BUTTON, lambda _event, d=decision: self._choose(d))
            row.Add(button, 0, wx.ALL, 4)
            self.buttons[decision] = button
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 4)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_NONE)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        self.Bind(wx.EVT_CLOSE, lambda _event: self._choose(Decision.DENY))
        self.text.SetFocus()

    def _on_key(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self._choose(Decision.DENY)
        else:
            event.Skip()

    def _choose(self, decision: Decision) -> None:
        self.decision = decision
        if self.IsModal():
            self.EndModal(wx.ID_OK)
        else:
            self.Destroy()


class QuestionDialog(wx.Dialog):
    """``answers`` holds item id to answer; None if the user chose not to answer."""

    def __init__(self, parent: wx.Window, chat_title: str, request: QuestionRequest):
        super().__init__(parent, title=f"Question: {chat_title}", size=(640, 520),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.request = request
        self.answers: Optional[Dict[str, str]] = None
        self._controls: List[Tuple[object, Optional[wx.ListBox], Optional[wx.TextCtrl]]] = []
        sizer = wx.BoxSizer(wx.VERTICAL)
        first: Optional[wx.Window] = None
        for item in request.items:
            choices, other = None, None
            if item.choices:
                choices = wx.ListBox(self, choices=[_value_label(c.label, c.description) for c in item.choices])
                _labelled(self, sizer, item.prompt, choices, 1)
                first = first or choices
            if item.allow_other:
                label = "Or type your own answer:" if item.choices else item.prompt
                other = wx.TextCtrl(self)
                _labelled(self, sizer, label, other)
                first = first or other
            self._controls.append((item, choices, other))
        self.error = wx.StaticText(self, label="")
        sizer.Add(self.error, 0, wx.LEFT | wx.RIGHT, 8)
        buttons = wx.StdDialogButtonSizer()
        ok = wx.Button(self, wx.ID_OK, "Answer")
        ok.SetDefault()
        ok.Bind(wx.EVT_BUTTON, self._on_ok)
        buttons.AddButton(ok)
        buttons.AddButton(wx.Button(self, wx.ID_CANCEL, "Don't Answer"))
        buttons.Realize()
        sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(sizer)
        if first is not None:
            first.SetFocus()

    def collect(self) -> Optional[Dict[str, str]]:
        answers: Dict[str, str] = {}
        for item, choices, other in self._controls:
            typed = other.GetValue().strip() if other is not None else ""
            chosen = choices.GetSelection() if choices is not None else wx.NOT_FOUND
            if typed:
                answers[item.id] = typed
            elif chosen != wx.NOT_FOUND:
                answers[item.id] = item.choices[chosen].id
            else:
                return None
        return answers

    def _on_ok(self, event: wx.CommandEvent) -> None:
        answers = self.collect()
        if answers is None:
            self.error.SetLabel("Answer every question first, or choose Don't Answer.")
            self.Layout()
            speak = getattr(self.GetParent(), "say", None)
            if speak is not None:
                speak("Answer every question first, or choose Don't Answer.", interrupt=True)
            return
        self.answers = answers
        event.Skip()


class OptionControls:
    """A labelled choice for each group, on any panel."""

    def __init__(self, parent: wx.Window, sizer: wx.Sizer, groups: Sequence[OptionGroup]):
        self.groups = tuple(groups)
        self.choices: Dict[str, wx.Choice] = {}
        for group in self.groups:
            choice = wx.Choice(parent, choices=[_value_label(v.label, v.description) for v in group.values])
            ids = [v.id for v in group.values]
            if group.current in ids:
                choice.SetSelection(ids.index(group.current))
            elif ids:
                choice.SetSelection(0)
            _labelled(parent, sizer, group.label + ":", choice)
            self.choices[group.id] = choice

    def values(self) -> Dict[str, str]:
        result = {}
        for group in self.groups:
            index = self.choices[group.id].GetSelection()
            if index != wx.NOT_FOUND:
                result[group.id] = group.values[index].id
        return result


class OptionsDialog(wx.Dialog):
    """A chat's options, whatever they are for this AI."""

    def __init__(self, parent: wx.Window, chat_title: str, groups: Sequence[OptionGroup]):
        super().__init__(parent, title=f"Options: {chat_title}", size=(560, 200 + 80 * len(groups)),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.controls = OptionControls(self, sizer, groups)
        sizer.AddStretchSpacer(1)
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(sizer)
        for choice in self.controls.choices.values():
            choice.SetFocus()
            break


class NewChatDialog(wx.Dialog):
    """Which AI, where and how. The AI's own choices load when it's chosen."""

    def __init__(self, parent: wx.Window, providers: Sequence[Tuple[str, str]], folder: str,
                 load_options: Callable[[str], Sequence[OptionGroup]]):
        super().__init__(parent, title="New Chat", size=(640, 520), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.providers = tuple(providers)
        self._load_options = load_options
        self._generation = 0
        self.option_controls: Optional[OptionControls] = None
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.provider = wx.Choice(self, choices=[name for _id, name in providers])
        _labelled(self, sizer, "&AI:", self.provider)
        self.title_box = wx.TextCtrl(self)
        _labelled(self, sizer, "&Title:", self.title_box)
        self.folder = wx.TextCtrl(self, value=folder)
        _labelled(self, sizer, "&Folder:", self.folder)
        browse = wx.Button(self, label="&Browse…")
        browse.Bind(wx.EVT_BUTTON, self._on_browse)
        sizer.Add(browse, 0, wx.LEFT | wx.BOTTOM, 8)
        self.options_panel = wx.Panel(self)
        self.options_panel.SetSizer(wx.BoxSizer(wx.VERTICAL))
        sizer.Add(self.options_panel, 1, wx.EXPAND)
        self.error = wx.StaticText(self, label="")
        sizer.Add(self.error, 0, wx.LEFT | wx.RIGHT, 8)
        buttons = wx.StdDialogButtonSizer()
        self.ok = wx.Button(self, wx.ID_OK, "Start Chat")
        self.ok.SetDefault()
        self.ok.Bind(wx.EVT_BUTTON, self._on_ok)
        buttons.AddButton(self.ok)
        buttons.AddButton(wx.Button(self, wx.ID_CANCEL))
        buttons.Realize()
        sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(sizer)
        self.provider.Bind(wx.EVT_CHOICE, lambda _event: self._reload_options())
        if self.providers:
            self.provider.SetSelection(0)
            self._reload_options()
        self.provider.SetFocus()

    def provider_id(self) -> str:
        index = self.provider.GetSelection()
        return self.providers[index][0] if index != wx.NOT_FOUND else ""

    def _reload_options(self) -> None:
        self._generation += 1
        generation, provider = self._generation, self.provider_id()

        def work() -> None:
            try:
                groups = tuple(self._load_options(provider))
            except Exception:  # noqa: BLE001 - shown as no options
                groups = ()
            wx.CallAfter(self._show_options, generation, groups)
        threading.Thread(target=work, daemon=True, name="new-chat-options").start()

    def _show_options(self, generation: int, groups: Sequence[OptionGroup]) -> None:
        if not self or generation != self._generation:
            return
        panel = self.options_panel
        panel.GetSizer().Clear(delete_windows=True)
        self.option_controls = OptionControls(panel, panel.GetSizer(), groups)
        panel.Layout()
        self.Layout()

    def options(self) -> Dict[str, str]:
        return self.option_controls.values() if self.option_controls else {}

    def _on_browse(self, _event: wx.CommandEvent) -> None:
        with wx.DirDialog(self, "Choose the chat's folder", self.folder.GetValue()) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.folder.SetValue(dialog.GetPath())

    def _on_ok(self, event: wx.CommandEvent) -> None:
        import os
        if not self.provider_id() or not os.path.isdir(self.folder.GetValue().strip()):
            self.error.SetLabel("Choose an AI, and a folder that exists.")
            self.Layout()
            speak = getattr(self.GetParent(), "say", None)
            if speak is not None:
                speak("Choose an AI, and a folder that exists.", interrupt=True)
            return
        event.Skip()


class ProvidersDialog(wx.Dialog):
    """Which AIs are on, how each is doing, and what it sent that wasn't understood."""

    def __init__(self, parent: wx.Window, rows: Sequence[Tuple[str, str]], is_on: Callable[[str], bool],
                 set_on: Callable[[str, bool], None], details: Callable[[str], str]):
        super().__init__(parent, title="AI Providers", size=(700, 560), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.rows, self._is_on, self._set_on, self._details = tuple(rows), is_on, set_on, details
        self._generation = 0
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.list = wx.ListBox(self, choices=[self._row(i) for i in range(len(self.rows))])
        _labelled(self, sizer, "&Providers:", self.list, 1)
        self.toggle = wx.Button(self, label="&Turn On")
        self.toggle.Bind(wx.EVT_BUTTON, self._on_toggle)
        sizer.Add(self.toggle, 0, wx.LEFT, 8)
        self.detail = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY)
        _labelled(self, sizer, "&About this provider:", self.detail, 2)
        close = wx.Button(self, wx.ID_CANCEL, "Close")
        sizer.Add(close, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.list.Bind(wx.EVT_LISTBOX, lambda _event: self._show())
        if self.rows:
            self.list.SetSelection(0)
            self._show()
        self.list.SetFocus()

    def _row(self, index: int) -> str:
        provider, name = self.rows[index]
        return f"{name}, {'on' if self._is_on(provider) else 'off'}"

    def _selected(self) -> str:
        index = self.list.GetSelection()
        return self.rows[index][0] if index != wx.NOT_FOUND else ""

    def _show(self) -> None:
        provider = self._selected()
        if not provider:
            return
        self.toggle.SetLabel("&Turn Off" if self._is_on(provider) else "&Turn On")
        self._generation += 1
        generation = self._generation
        self.detail.SetValue("Checking…")

        def work() -> None:
            text = self._details(provider)
            wx.CallAfter(self._set_detail, generation, text)
        threading.Thread(target=work, daemon=True, name="provider-details").start()

    def _set_detail(self, generation: int, text: str) -> None:
        if self and generation == self._generation:
            self.detail.SetValue(text)

    def _on_toggle(self, _event: wx.CommandEvent) -> None:
        provider = self._selected()
        if not provider:
            return
        self._set_on(provider, not self._is_on(provider))
        index = self.list.GetSelection()
        self.list.SetString(index, self._row(index))
        self._show()
