"""The Chat Place's dialogs: New Session, Settings, Keyboard Shortcuts."""
from __future__ import annotations

import dataclasses
import os
import sys
import threading
from types import SimpleNamespace
from pathlib import Path
from typing import Optional

import wx
from theideaplace_appkit import difftext

from .. import chat_permissions, lists, workplaces, platform_paths
from ..claude_cli import (DEFAULT_PERMISSION_MODE, EFFORT_NOTE, EFFORTS, MODELS,
                          PERMISSION_MODES)
from ..sessions import (DEFAULT_FIELDS, FIELD_IDS, FIELD_NAMES, clean_fields, field_short_name,
                        WORKING, NEEDS_YOU, STATUS_UNAVAILABLE)
from ..speech import (ANNOUNCE_LABELS, ANNOUNCE_LEVELS, NOTIFY_LABELS, NOTIFY_LEVELS, RATE_PRESET_LABELS,
                      SpeechSettings)
from ..prompts import MAX_NAME as PROMPT_NAME_MAX
from ..ui_text import shortcuts_text, codex_effort_label, key_name
from . import gtk_a11y, mac_a11y
from .sizes import SCREEN_MARGIN, dialog_size, dip, dip_width, screen_area
from .a11y import set_accessible_name, set_list_items_accessible
from .notify import SETTING_LABEL as NOTIFY_SETTING_LABEL, SETTING_NAME as NOTIFY_SETTING_NAME


class ShortcutsDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Keyboard Shortcuts", size=dialog_size(parent, 620, 520),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="&Shortcuts:"), 0, wx.LEFT | wx.TOP, 8)
        text = wx.TextCtrl(self, value=shortcuts_text(),
                           style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        set_accessible_name(text, "Keyboard shortcuts")
        sizer.Add(text, 1, wx.EXPAND | wx.ALL, 8)
        close = wx.Button(self, wx.ID_CANCEL, "&Close")
        close.SetDefault()
        sizer.Add(close, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        wx.CallAfter(text.SetFocus)


PERMISSION_NOTE = (
    key_name("The permission mode decides what Claude may do without asking. When Claude "
    "asks for permission, asks you a question or has a plan for you to approve, "
    "the turn waits: The Chat Place announces it, the session shows as needing "
    "you, and Ctrl+Shift+A answers. Effort is how hard Claude thinks; models that "
    "don't support effort levels ignore it. File, Change Permission Mode and Change "
    "Effort change them later."))


def single_choice_dialog(parent, prompt: str, title: str, choices) -> wx.SingleChoiceDialog:
    """wx's own single-choice list, which screen readers handle well. On
    Linux its list has no name of its own (Windows takes the label before
    it), and Orca reached "a table with no name" (#230, found by the Orca
    audit in tools/ui_probe.py), so there it takes the dialog's title."""
    dialog = wx.SingleChoiceDialog(parent, prompt, title, choices)
    if gtk_a11y.IS_LINUX:
        for child in dialog.GetChildren():
            if isinstance(child, wx.ListBox):
                set_accessible_name(child, title)
    return dialog


def codex_permission_prompt(parent, title, folder):
    """Wrap only the native picker's prompt, using its actual GUI font (#257)."""
    from wx.lib.wordwrap import wordwrap
    from ..ui_text import CODEX_SAVED_RULES_NOTE
    from .permission_hosts import CODEX_REVIEW_NOTE
    dc = wx.ClientDC(parent)
    dc.SetFont(wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT))
    width = min(dip_width(parent, 560), screen_area(parent).width - dip_width(parent, 80))
    text = (f'Codex permissions for {title}.\nFolder: {folder}.\n'
            'Finish or Stop active work first.\n\n' + CODEX_REVIEW_NOTE + '\n\n' + CODEX_SAVED_RULES_NOTE)
    return '\n'.join(wordwrap(line, width, dc, breakLongWords=True).rstrip('\n')
                     for line in text.split('\n'))


CONTINUE_NOTE = (
    "This starts a Chat Place session that is a copy of this session, with its "
    "whole conversation so far, so you can carry on and reply here. The original, "
    "in the desktop app or a terminal, isn't changed, and replies here don't "
    "appear in it. The copy starts with nothing allowed beyond its permission "
    "mode.")


def _end_modal(dialog: wx.Dialog, code: int) -> None:
    """EndModal, or (a dialog a test made without showing it) the same
    return code and hidden."""
    if dialog.IsModal():
        dialog.EndModal(code)
    else:
        dialog.SetReturnCode(code)
        dialog.Hide()


#: The two places a new session can work in (#154).
WORK_IN_FOLDER, WORK_IN_WORKTREE = 0, 1


def _copilot_session_modes():
    from ..providers.copilot_runtime import AGENT_MODE, PLAN_MODE
    return ((AGENT_MODE, "Agent: asks before running commands or changing files"),
            (PLAN_MODE, "Plan: plans first and doesn't edit your files; asks before anything else"))


#: The modes a Copilot chat can be in here (New Session, Change Permission
#: Mode): Copilot's agent and plan modes, both of which ask before acting.
#: Its autopilot mode, which doesn't, is never offered.
COPILOT_SESSION_MODES = _copilot_session_modes()


def copilot_mode_name(mode: str) -> str:
    """A Copilot mode in words: "agent mode", "plan mode" or "another mode"."""
    from ..providers.copilot_runtime import AGENT_MODE, PLAN_MODE
    return {AGENT_MODE: "agent mode", PLAN_MODE: "plan mode"}.get(mode, "another mode")


#: What New Session says about a Copilot chat (#352).
COPILOT_NOTE = ("Copilot uses your GitHub Copilot sign-in, and each message uses requests from "
                "your Copilot plan. Model lists the models Copilot offered your last Copilot "
                "chat here, each with the premium requests a message costs; Copilot's own "
                "choice is first. File, Change Model changes it later. Effort is Copilot's "
                "own. Permission mode chooses agent mode, which can change your files, or "
                "plan mode, which plans first and doesn't edit them; File, Change Permission "
                "Mode switches it later. In both, Copilot asks before it runs a command or "
                "changes a file: "
                "each request appears in Answer, where you can allow it once or deny it. "
                "The Chat Place won't send to Copilot in a folder whose Copilot hooks could act "
                "without asking you.")


COPILOT_CONTINUE_NOTE = (
    "The copy is a new Copilot chat of The Chat Place's own, in the same folder. Copilot can't "
    "copy a chat, so your first message goes with the conversation so far: your messages and "
    "Copilot's replies, newest kept if it's long, but not the commands it ran or their output. "
    "The original chat isn't changed.")


class NewSessionDialog(wx.Dialog):
    """Folder, where in it to work, title, model, permission mode, first message.

    The folder is typed, chosen from ``recent`` (the folders sessions have
    used, #154), browsed for, or cloned From GitHub. Work In chooses the
    folder as it is or a new git worktree on a branch of its own, which is
    made when Start is pressed.

    With ``continue_from`` (a desktop session's title, #189) it continues that
    session as a copy: the folder is the session's own and can't be changed,
    and the title starts as "<title> (continued)".
    """

    def __init__(self, parent, default_folder: str, continue_from: str = "",
                 recent=(), projects_root: str = "", feedback=None, clone_root: str = "",
                 remove_recent=None, providers=("claude",), provider="claude", source_state="", load_models=None, load_rules=None,
                 title_text: str = "", message: str = "", copilot_models=(),
                 copilot_plan: bool = True):
        title = f"Continue Here: {continue_from}" if continue_from \
            else "New Chat Place Session"
        super().__init__(parent, title=title, size=dialog_size(parent, 680, 620),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.continuing = bool(continue_from)
        self._source_state = source_state
        self._projects_root = projects_root or default_folder
        self._clone_root = clone_root or self._projects_root
        self._feedback = feedback or (lambda text: None)
        # Takes a folder off the recent list for good (#215): True if it did.
        self._remove_recent = remove_recent
        self._repo = None           # workplaces.RepoInfo of the folder, or None
        self._repo_for = None       # the folder being read, or read
        self._repo_known = False    # whether _repo is the answer for _repo_for
        self._worktree = ""         # made by Start
        self._making = False        # a worktree is being made
        self._folder_timer = None   # reads a folder chosen from the list, shortly
        outer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, vgap=8, hgap=8)
        grid.AddGrowableCol(1, 1)
        self._providers = tuple(p for p in providers if p in ("claude", "codex", "copilot")) or ("claude",)
        self._provider = provider if provider in self._providers else self._providers[0]
        self.provider_choice = None
        self._control_provider = "claude"
        self._claude_choices = None
        self._load_rules = load_rules
        self._rules_for = None
        self._rule_count = None
        self._rules_error = ""
        self._codex_models = ()
        self._copilot_mode_choice = 0
        #: The Copilot modes offered: agent only for a chat made to join an
        #: agent conversation (#424).
        self._copilot_modes = COPILOT_SESSION_MODES if copilot_plan else COPILOT_SESSION_MODES[:1]
        #: (value, label) of each model Copilot last offered (#352).
        self._copilot_models = tuple(copilot_models)
        self._copilot_choice = 0
        self._codex_choices = (0, 0, 0 if continue_from else 1)
        self._codex_model_error = False
        self._codex_models_loaded = False
        if len(self._providers) > 1:
            grid.Add(wx.StaticText(self, label="Pro&vider:"), 0, wx.ALIGN_CENTER_VERTICAL)
            self.provider_choice = wx.Choice(self, choices=[p.capitalize() for p in self._providers])
            self.provider_choice.SetSelection(self._providers.index(self._provider))
            set_accessible_name(self.provider_choice, "Provider")
            grid.Add(self.provider_choice, 1, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="&Folder:"), 0, wx.ALIGN_CENTER_VERTICAL)
        if continue_from:
            self.folder = wx.TextCtrl(self, value=default_folder, style=wx.TE_READONLY)
        else:
            # Typed, or one of the recent folders (Up and Down, or Alt+Down).
            recent = [f for f in recent if f]
            self.folder = wx.ComboBox(self, value=recent[0] if recent else default_folder,
                                      choices=recent, style=wx.CB_DROPDOWN)
            if recent:
                # Chosen, not just typed in: otherwise the first Down Arrow
                # chooses the folder that's already there.
                self.folder.SetSelection(0)
        set_accessible_name(self.folder, "Folder")
        grid.Add(self.folder, 1, wx.EXPAND)
        # The buttons on a row of their own under the box, which then has
        # the width the other fields have: beside it, they left it a third
        # narrower, showing only the end of a path (#178). Tab still goes
        # from the box to them, as they're made in that order.
        browse = wx.Button(self, label="&Browse...")
        self.github_btn = wx.Button(self, label="From &GitHub...")
        browse.Show(not continue_from)
        self.github_btn.Show(not continue_from)
        if not continue_from:
            folder_buttons = wx.BoxSizer(wx.HORIZONTAL)
            folder_buttons.Add(browse, 0, wx.RIGHT, 6)
            folder_buttons.Add(self.github_btn, 0)
            grid.Add((0, 0))
            grid.Add(folder_buttons, 0)

        self.work_in_label = wx.StaticText(self, label="Work &in:")
        grid.Add(self.work_in_label, 0, wx.ALIGN_CENTER_VERTICAL)
        self.work_in = wx.Choice(self, choices=["The folder as it is", "A new worktree"])
        self.work_in.SetSelection(WORK_IN_FOLDER)
        set_accessible_name(self.work_in, "Work in")
        grid.Add(self.work_in, 1, wx.EXPAND)

        self.branch_label = wx.StaticText(self, label="B&ranch (choose one, or type a new name):")
        grid.Add(self.branch_label, 0, wx.ALIGN_CENTER_VERTICAL)
        self.branch = wx.ComboBox(self, style=wx.CB_DROPDOWN)
        set_accessible_name(self.branch, "Branch (choose one, or type a new name)")
        grid.Add(self.branch, 1, wx.EXPAND)
        for control in (self.work_in_label, self.work_in, self.branch_label, self.branch):
            control.Show(not continue_from)

        grid.Add(wx.StaticText(self, label="&Title:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.title_text = wx.TextCtrl(self, value=f"{continue_from} (continued)"
                                      if continue_from else title_text)
        set_accessible_name(self.title_text, "Title (optional)")
        grid.Add(self.title_text, 1, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Mo&del:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.model = wx.Choice(self, choices=[label for _v, label in MODELS])
        set_accessible_name(self.model, "Model")
        self.model.SetSelection(0)  # Claude Code's own default
        self.model.Bind(wx.EVT_CHOICE, self._on_codex_model)
        grid.Add(self.model, 1, wx.EXPAND)

        # Effort (#189). What it does is in the note below, with permissions.
        grid.Add(wx.StaticText(self, label="&Effort:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.effort_choice = wx.Choice(self, choices=[label for _v, label in EFFORTS])
        set_accessible_name(self.effort_choice, "Effort")
        self.effort_choice.SetSelection(0)  # Claude Code's own setting
        grid.Add(self.effort_choice, 1, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Permission m&ode:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.mode = wx.Choice(self, choices=[label for _v, label in PERMISSION_MODES])
        set_accessible_name(self.mode, "Permission mode")
        values = [v for v, _l in PERMISSION_MODES]
        self.mode.SetSelection(values.index(DEFAULT_PERMISSION_MODE))
        grid.Add(self.mode, 1, wx.EXPAND)
        outer.Add(grid, 0, wx.EXPAND | wx.ALL, 10)

        # A read-only text box rather than a static label, so it is in the tab
        # order and a screen reader reaches it.
        about = "About continuing" if continue_from else "About permissions"
        outer.Add(wx.StaticText(self, label=f"{about.replace('About ', 'About &')}:"),
                  0, wx.LEFT, 10)
        note = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
                           value=(CONTINUE_NOTE + "\n\n" + PERMISSION_NOTE) if continue_from
                           else PERMISSION_NOTE)
        set_accessible_name(note, about)
        self.note = note  # sized to its text once it has its width, below
        outer.Add(note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        outer.Add(wx.StaticText(self, label="First &message:"), 0, wx.LEFT | wx.TOP, 10)
        # A reply sent on to a new chat (#378) starts here, to read first.
        self.message = wx.TextCtrl(self, value=message, style=wx.TE_MULTILINE | wx.TE_RICH2)
        set_accessible_name(self.message, "First message")
        outer.Add(self.message, 1, wx.EXPAND | wx.ALL, 10)

        buttons = wx.StdDialogButtonSizer()
        ok = wx.Button(self, wx.ID_OK, "&Start")
        ok.SetDefault()
        buttons.AddButton(ok)
        buttons.AddButton(wx.Button(self, wx.ID_CANCEL))
        buttons.Realize()
        outer.Add(buttons, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(outer)
        # Laid out now, before it's shown: the title box was still its small
        # first size (110 pixels) when its text was set, so Continue Here
        # showed only the title's end, "ad test (continued)", though the
        # whole title fits the 540-pixel box (#178).
        self.Layout()
        self._fit_note()

        browse.Bind(wx.EVT_BUTTON, self._on_browse)
        self.github_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_github())
        ok.Bind(wx.EVT_BUTTON, self._on_ok)
        self.Bind(wx.EVT_BUTTON, self._on_cancel, id=wx.ID_CANCEL)
        self.start_btn = ok
        if not continue_from:
            self.folder.Bind(wx.EVT_COMBOBOX, lambda e: self._folder_chosen())
            self.folder.Bind(wx.EVT_KILL_FOCUS, self._on_folder_left)
            self.folder.Bind(wx.EVT_KEY_DOWN, self._on_folder_key)
            self.work_in.Bind(wx.EVT_CHOICE, lambda e: self._on_work_in())
            self.refresh_repo()
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        if self.provider_choice is not None:
            self.provider_choice.Bind(wx.EVT_CHOICE, self._on_provider)
        if self._provider in ("codex", "copilot"):
            self._on_provider()
        if load_models is not None and "codex" in self._providers:
            wx.CallAfter(load_models, self._set_codex_models)
        # Continuing, the folder is fixed: start at the first thing to type.
        wx.CallAfter(self._focus_first)

    def _focus_first(self):
        if self:
            (self.message if self.continuing else self.provider_choice or self.folder).SetFocus()
            # A backstop for the title's start being in view (#178): the
            # layout before showing is what puts it there.
            self.title_text.SetInsertionPoint(0)
            self.title_text.ShowPosition(0)

    def _fit_note(self):
        """The About box tall enough for its whole note at its width (#178:
        Continue Here's note is two paragraphs, and a fixed four lines cut
        its third line through the middle). Measured, not counted, so a
        bigger font or a narrower window still fits it."""
        from wx.lib.wordwrap import wordwrap
        self.Layout()
        # A little narrower than the box draws text, so it never counts a
        # line short (at some widths it counts one more than there is).
        width = self.note.GetClientSize().width - 10
        if width <= 40:
            return
        dc = wx.ClientDC(self.note)
        dc.SetFont(self.note.GetFont())
        lines = sum(len(wordwrap(paragraph, width, dc).rstrip("\n").split("\n"))
                    for paragraph in self.note.GetValue().split("\n"))
        # Plus the box's own padding, which grows with the scaling (#185).
        padding = dip_width(self, 12)
        char = self.note.GetCharHeight()
        whole = char * lines + padding
        self.note.SetMinSize((-1, whole))
        # First message keeps four lines: the whole note had left it one at
        # 175% (Codex's note, #368). When everything is then taller than the
        # dialog, the dialog grows into the screen's spare height before it's
        # shown (centred again, so its foot stays on screen), and past that
        # the note gives up whole lines, keeping three and scrolling. Only
        # then does First message give way, down to one line, rather than
        # Start and Cancel being pushed out of the window.
        line = self.message.GetCharHeight()
        self.message.SetMinSize((-1, line * 4 + padding))
        self.Layout()
        over = self.GetSizer().GetMinSize().height - self.GetClientSize().height
        if over > 0 and not self.IsShown():
            room = screen_area(self).height - SCREEN_MARGIN - self.GetSize().height
            grow = min(over, max(room, 0))
            if grow:
                self.SetSize(self.GetSize().width, self.GetSize().height + grow)
                self.CentreOnParent(wx.VERTICAL)
                over -= grow
        if over > 0:
            fit = max(3, (whole - over - padding) // char)
            self.note.SetMinSize((-1, char * fit + padding))
            self.Layout()
            over = self.GetSizer().GetMinSize().height - self.GetClientSize().height
            if over > 0:
                self.message.SetMinSize((-1, max(line + padding, line * 4 + padding - over)))
        self.Layout()

    def _on_char_hook(self, event):
        # Ctrl+Enter starts the session from anywhere, as Send does elsewhere.
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and event.ControlDown():
            self._on_ok(None)
            return
        event.Skip()

    def _on_folder_key(self, event):
        # Delete on a recent folder (#215). On the box itself, not the
        # dialog's char hook: with the list open (Alt+Down) the hook never
        # hears a key, and Delete went on to empty the box.
        if event.GetKeyCode() in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE) \
                and not event.HasAnyModifiers() and self.remove_recent_folder():
            return
        event.Skip()

    def _recent_under_delete(self) -> int:
        """The recent folder Delete would take off the list (#215), or
        NOT_FOUND. Only when the box holds that folder as it was chosen and
        all selected, as arrowing leaves it, so Delete still edits a folder
        being typed or changed, and does nothing at the end of one, as ever.
        With the list open, the folder it's on is the current selection."""
        index = self.folder.GetCurrentSelection()
        if index == wx.NOT_FOUND:
            index = self.folder.GetSelection()
        if index == wx.NOT_FOUND or index >= self.folder.GetCount():
            return wx.NOT_FOUND
        value = self.folder.GetValue()
        if value != self.folder.GetString(index):
            return wx.NOT_FOUND
        if value and self.folder.GetTextSelection() == (0, len(value)):
            return index
        return wx.NOT_FOUND

    def remove_recent_folder(self) -> bool:
        """Delete on a recent folder: take it off the list, here and for good,
        and go to the next one (the one before, if it was the last)."""
        index = self._recent_under_delete()
        if index == wx.NOT_FOUND or self._remove_recent is None:
            return False
        folder = self.folder.GetString(index)
        if not self._remove_recent(folder):
            return True  # couldn't be saved, and the caller said so
        # Closed first, so the open list isn't changed under it.
        self.folder.Dismiss()
        self.folder.Delete(index)
        name = os.path.basename(folder.rstrip("\\/")) or folder
        count = self.folder.GetCount()
        if count:
            self.folder.SetSelection(min(index, count - 1))
            said = f"Removed {name} from recent folders."
        else:
            self.folder.ChangeValue(self._projects_root)
            said = f"Removed {name} from recent folders. None left."
        self.folder.SelectAll()
        self._folder_chosen()
        # Not the folder now in the box: the screen reader reads that as
        # the box's value changes, and saying it too read it twice.
        self._feedback(said)
        return True

    def _folder_chosen(self):
        # Arrowing through the recent folders chooses each in turn: read the
        # one stopped on, not every one passed (each read runs git a few times).
        if self._folder_timer is not None:
            self._folder_timer.Stop()
        self._folder_timer = wx.CallLater(300, lambda: self and self.refresh_repo())

    def _on_folder_left(self, event):
        event.Skip()
        wx.CallAfter(lambda: self and self.refresh_repo())

    def _on_cancel(self, event):
        if self._making:
            # The worktree is half made: let git finish, then Start or
            # Cancel again. Leaving now would leave a worktree nobody uses.
            self._feedback("Still making the worktree.")
            return
        event.Skip()

    # -- the folder's repository and branches (read in the background, #154:
    # git, or a folder on a network drive, can be slow to answer)

    def refresh_repo(self):
        """Read the folder's git repository again, if the folder changed:
        the branches offered, and what Work In says about the folder."""
        self._refresh_rules()
        if self.continuing:
            return
        folder = self.folder.GetValue().strip()
        if folder == self._repo_for:
            return
        self._repo_for, self._repo_known = folder, False
        threading.Thread(target=self._read_repo, args=(folder,), name="repo-info",
                         daemon=True).start()

    def _read_repo(self, folder: str):
        git = workplaces.find_git()
        info = workplaces.repo_info(git, folder) if git else None
        wx.CallAfter(self._repo_read, folder, info)

    def _repo_read(self, folder: str, info):
        if not self or folder != self._repo_for:
            return  # closed, or the folder has changed since
        self._repo, self._repo_known = info, True
        typed = self.branch.GetValue()
        self.branch.Set(info.branches if info else [])
        self.branch.ChangeValue(typed)
        if info and info.branch:
            here = f"The folder as it is, on {info.branch}"
        elif info:
            here = "The folder as it is"
        else:
            here = "The folder as it is (not a git repository, so no worktree)"
        self.work_in.SetString(WORK_IN_FOLDER, here)
        self._on_work_in()

    def _on_work_in(self):
        if self.work_in.GetSelection() == WORK_IN_WORKTREE and self._repo_known \
                and self._repo is None:
            self.work_in.SetSelection(WORK_IN_FOLDER)
            self._feedback("That folder isn't in a git repository, so it can't have a "
                           "worktree.")
        worktree = self.work_in.GetSelection() == WORK_IN_WORKTREE
        self.branch.Enable(worktree)
        self.branch_label.Enable(worktree)

    # -- choosing the folder

    def set_folder(self, folder: str):
        self.folder.SetValue(folder)
        self.refresh_repo()

    def _on_browse(self, _event):
        start = self.folder.GetValue().strip()
        with wx.DirDialog(self, "Choose the folder for this session",
                          defaultPath=start if os.path.isdir(start) else self._projects_root,
                          style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.set_folder(dlg.GetPath())
        self.folder.SetFocus()

    def on_github(self):
        gh = workplaces.find_gh()
        if not gh:
            wx.MessageBox(workplaces.GH_MISSING, "From GitHub", wx.OK | wx.ICON_WARNING, self)
            self.github_btn.SetFocus()
            return
        dialog = GitHubRepoDialog(self, gh, self._clone_root, self._feedback)
        try:
            chosen = dialog.folder if dialog.ShowModal() == wx.ID_OK else None
        finally:
            dialog.Destroy()
        if chosen:
            self.set_folder(str(chosen))
            self.folder.SetFocus()
        else:
            self.github_btn.SetFocus()

    # -- Start

    def _warn(self, text: str, control):
        wx.MessageBox(text, self.GetTitle(), wx.OK | wx.ICON_WARNING, self)
        control.SetFocus()

    def _on_ok(self, _event):
        if self._making:
            return
        folder = self.folder.GetValue().strip()
        if not folder or not os.path.isdir(folder):
            self._warn("That folder doesn't exist. Choose an existing folder.", self.folder)
            return
        if not self.message.GetValue().strip():
            self._warn("Type the first message for this session.", self.message)
            return
        if not self.continuing and self.work_in.GetSelection() == WORK_IN_WORKTREE:
            self.make_worktree()  # Start finishes when it's made
            return
        _end_modal(self, wx.ID_OK)

    def make_worktree(self):
        """Make the worktree Work In asks for, last, once everything else
        is filled in, in the background (a large repository takes a while
        to check out). The dialog closes when it's made, or says why not."""
        git = workplaces.find_git()
        if not git:
            self._warn("Git isn't installed, or The Chat Place can't find it, so there can't "
                       "be a worktree. Choose The folder as it is, or install Git.", self.work_in)
            return
        folder, branch = self.folder.GetValue().strip(), self.branch.GetValue()
        self._making = True
        self.start_btn.Enable(False)
        self._feedback("Making the worktree.")

        def make():
            focus = self.branch
            try:
                info = workplaces.repo_info(git, folder)
                if info is None:
                    focus = self.work_in
                    made, error = None, ("That folder isn't in a git repository, so it can't "
                                         "have a worktree. Choose The folder as it is, or "
                                         "another folder.")
                else:
                    made, error = workplaces.add_worktree(git, info, branch), ""
            except workplaces.ToolError as exc:
                made, error = None, f"Couldn't make the worktree. {exc}"
            except Exception as exc:  # noqa: BLE001 - always come back to the dialog
                made, error = None, f"Couldn't make the worktree: {exc}"
            wx.CallAfter(self._worktree_made, made, error, focus)
        threading.Thread(target=make, name="worktree", daemon=True).start()

    def _worktree_made(self, made, error: str, focus=None):
        if not self:
            return
        self._making = False
        self.start_btn.Enable(True)
        if made is None:
            self._warn(error, focus or self.branch)
            return
        self._worktree = str(made)
        _end_modal(self, wx.ID_OK)

    def values(self):
        folder = self._worktree or os.path.abspath(self.folder.GetValue().strip())
        message = self.message.GetValue().strip()
        title = " ".join(self.title_text.GetValue().split())
        if not title:
            words = message.split()
            title = " ".join(words[:8]) + ("…" if len(words) > 8 else "")
        index = self.mode.GetSelection()
        mode = PERMISSION_MODES[index][0] if 0 <= index < len(PERMISSION_MODES) \
            else DEFAULT_PERMISSION_MODE
        index = self.model.GetSelection()
        model = MODELS[index][0] if 0 <= index < len(MODELS) else ""
        if self.provider() == "codex":
            mode = DEFAULT_PERMISSION_MODE
            index = self.model.GetSelection() - 1
            model = self._codex_models[index].model if 0 <= index < len(self._codex_models) else ""
        elif self.provider() == "copilot":
            mode = DEFAULT_PERMISSION_MODE  # it always asks
            index = self.model.GetSelection() - 1
            model = (self._copilot_models[index][0]
                     if 0 <= index < len(self._copilot_models) else "")
        return folder, title, mode, message, model

    def copilot_mode(self) -> str:
        """Copilot's mode to start in, agent or plan, asked for even when it's
        agent: Copilot's own default needn't be (#424). "" for another provider."""
        if self.provider() != "copilot":
            return ""
        index = max(self.mode.GetSelection(), 0)
        return self._copilot_modes[min(index, len(self._copilot_modes) - 1)][0]

    def codex_policy(self):
        from .permission_hosts import CODEX_REVIEW_MODES
        sandbox, approval, reviewer = CODEX_REVIEW_MODES[max(0, self.mode.GetSelection())][0]
        options = {'sandbox': sandbox, 'approval_policy': approval}
        if reviewer != 'user':
            options['approvals_reviewer'] = reviewer
        return options

    def provider(self):
        return (self._providers[self.provider_choice.GetSelection()]
                if self.provider_choice is not None else self._provider)

    def _on_provider(self, _event=None):
        wx.CallAfter(self._refresh_rules)
        provider = self.provider()
        if self._control_provider == "claude":
            self._claude_choices = (self.model.GetSelection(), self.effort_choice.GetSelection(), self.mode.GetSelection())
        elif self._control_provider == "codex":
            self._codex_choices = (self.model.GetSelection(), self.effort_choice.GetSelection(), self.mode.GetSelection())
        elif self._control_provider == "copilot":
            self._copilot_mode_choice = max(self.mode.GetSelection(), 0)
            self._copilot_choice = max(self.model.GetSelection(), 0)
        if provider == "copilot":
            # The models Copilot last offered; effort is Copilot's own. Shown,
            # not hidden, so the dialog keeps its shape and says so.
            self.effort_choice.Set(["Copilot's own setting"])
            self.effort_choice.SetSelection(0)
            self.effort_choice.Disable()
            self.model.Set(["Copilot's configured model"]
                           + [label for _value, label in self._copilot_models])
            self.model.SetSelection(min(self._copilot_choice, len(self._copilot_models)))
            self.model.Enable(bool(self._copilot_models))
            # Agent or plan mode; both ask before acting (copilot_runtime).
            self.mode.Set([label for _value, label in self._copilot_modes])
            self.mode.SetSelection(min(self._copilot_mode_choice, len(self._copilot_modes) - 1))
            self.mode.Enable(len(self._copilot_modes) > 1)
            self.note.SetValue(COPILOT_NOTE)
            if self.continuing:
                note = COPILOT_CONTINUE_NOTE
                if self._source_state in (WORKING, NEEDS_YOU, STATUS_UNAVAILABLE):
                    note = "This chat may still be working in Copilot CLI. " + note
                self.note.SetValue(note + "\n\n" + COPILOT_NOTE)
        elif provider == "codex":
            self.model.Set(["Configured Codex model"] + [choice.label for choice in self._codex_models])
            self.model.SetSelection(min(self._codex_choices[0], len(self._codex_models)))
            self.model.Enable(bool(self._codex_models))
            self._on_codex_model()
            self.effort_choice.SetSelection(min(self._codex_choices[1], self.effort_choice.GetCount() - 1))
            from .permission_hosts import CODEX_REVIEW_MODES, CODEX_REVIEW_NOTE
            self.mode.Set([label for value, label in CODEX_REVIEW_MODES])
            self.mode.SetSelection(self._codex_choices[2])
            self.mode.Enable()
            self.note.SetValue("Codex uses your ChatGPT sign-in. Choose a model and effort level, "
                "or leave the configured defaults. "
                "Choose read-only or allow edits in this folder, with human or automatic approval review. "
                "Human requests appear in Answer. Permissions are verified before sending. " + CODEX_REVIEW_NOTE + ' ' + self._saved_rules_note() +
                (" Model choices could not be loaded; configured defaults remain available." if self._codex_model_error else
                 " Model choices load in the background." if not self._codex_models_loaded else ""))
            if self.continuing:
                note = "The copy starts from the last saved point with the permissions selected here. Your saved always-allow commands still apply."
                if self._source_state in (WORKING, NEEDS_YOU, STATUS_UNAVAILABLE):
                    note = "This chat may still be working in Codex. " + note
                self.note.SetValue(note + "\n\n" + self.note.GetValue())
        else:
            for control, choices, selected in zip((self.model, self.effort_choice, self.mode),
                    ([label for value, label in MODELS], [label for value, label in EFFORTS],
                     [label for value, label in PERMISSION_MODES]), self._claude_choices or (0, 0, 0)):
                control.Set(choices)
                control.SetSelection(selected)
                control.Enable()
            self.note.SetValue((CONTINUE_NOTE + "\n\n" + PERMISSION_NOTE) if self.continuing else PERMISSION_NOTE)
        self._control_provider = provider
        self._fit_note()

    def effort(self) -> str:
        """The ``--effort`` chosen, "" for Claude Code's own setting (#189)."""
        if self.provider() == "copilot":
            return ""
        if self.provider() == "codex":
            model = self.model.GetSelection() - 1
            effort = self.effort_choice.GetSelection() - 1
            choices = self._codex_models[model].efforts if 0 <= model < len(self._codex_models) else ()
            return choices[effort] if 0 <= effort < len(choices) else ""
        index = self.effort_choice.GetSelection()
        return EFFORTS[index][0] if 0 <= index < len(EFFORTS) else ""

    def _on_codex_model(self, _event=None):
        if self.provider() != "codex":
            return
        index = self.model.GetSelection() - 1
        efforts = self._codex_models[index].efforts if 0 <= index < len(self._codex_models) else ()
        self.effort_choice.Set(["Configured Codex effort"] + [codex_effort_label(value) for value in efforts])
        self.effort_choice.SetSelection(0)
        self.effort_choice.Enable(bool(efforts))

    def _saved_rules_note(self):
        from ..ui_text import codex_saved_rules_text
        return self._rules_error or codex_saved_rules_text(self._rule_count)

    def _refresh_rules(self):
        if not self or self.IsBeingDeleted():
            return
        if self._load_rules is None or self.provider() != 'codex':
            return
        folder = self.folder.GetValue().strip()
        if folder == self._rules_for:
            return
        self._rules_for, self._rule_count, self._rules_error = folder, None, ''
        self._on_provider()  # Stop displaying the previous folder's count immediately.
        def done(count, error):
            if not self or self.IsBeingDeleted() or folder != self._rules_for:
                return
            self._rule_count = None if error else count
            self._rules_error = (count or 'Saved command rules could not be checked.') if error else ''
            self._on_provider()
        self._load_rules(folder, done)

    def _set_codex_models(self, models, error):
        # An asynchronous catalog can arrive after Cancel/Destroy. It must not
        # change focus, resurrect the dialog or touch a dead native window.
        if not self or self.IsBeingDeleted():
            return
        self._codex_models = () if error else tuple(models)
        self._codex_model_error = error
        self._codex_models_loaded = True
        if self.provider() == "codex":
            self._on_provider()
            if self.IsShown():
                self._feedback("Codex model choices are available." if self._codex_models else
                               "Codex model choices are unavailable. Configured defaults remain available.")


class GitHubRepoDialog(wx.Dialog):
    """New Session, From GitHub (#154): your repositories from ``gh repo
    list``. Type to filter, or type owner/name (or a GitHub address) for any
    repository. Use takes its folder under the GitHub folder, cloning it
    there first if it isn't there yet. ``folder`` is the result."""

    def __init__(self, parent, gh: str, root: str, feedback=None):
        super().__init__(parent, title="From GitHub", size=dialog_size(parent, 720, 520),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._gh, self._root = gh, root
        self._feedback = feedback or (lambda text: None)
        self._all = []
        self._shown = []
        self._loaded = False
        self._error = ""  # why the list couldn't be had
        self._clone = None
        self.folder = None
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="&Search, or type owner/name:"), 0,
                  wx.LEFT | wx.TOP, 8)
        self.search = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        # Where a clone goes is part of the box's name: the status line
        # below isn't in the tab order.
        set_accessible_name(self.search, "Search your repositories, or type owner/name. "
                                         f"Clones go into {root}")
        sizer.Add(self.search, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.list_label = wx.StaticText(self, label="&Repositories:")
        sizer.Add(self.list_label, 0, wx.LEFT | wx.TOP, 8)
        self.list = wx.ListBox(self, style=wx.LB_SINGLE)
        set_accessible_name(self.list, "Repositories")
        sizer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.status = wx.StaticText(self, label=f"Cloned into {root}, if it isn't there yet.")
        sizer.Add(self.status, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.use_btn = wx.Button(self, wx.ID_OK, "&Use")
        self.use_btn.SetDefault()
        row.Add(self.use_btn, 0, wx.RIGHT, 6)
        self.cancel_btn = wx.Button(self, wx.ID_CANCEL)
        row.Add(self.cancel_btn, 0)
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        self.search.Bind(wx.EVT_TEXT, lambda e: self._filter())
        self.search.Bind(wx.EVT_TEXT_ENTER, lambda e: self.choose())
        self.search.Bind(wx.EVT_KEY_DOWN, self._on_search_key)
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.choose())
        self.use_btn.Bind(wx.EVT_BUTTON, lambda e: self.choose())
        self.Bind(wx.EVT_BUTTON, self._on_cancel, id=wx.ID_CANCEL)
        self.Bind(wx.EVT_CLOSE, lambda e: self._on_cancel(None))
        self._filter()
        threading.Thread(target=self._load, name="gh-repos", daemon=True).start()
        wx.CallAfter(lambda: self and self.search.SetFocus())

    def _load(self):
        try:
            repos, error = workplaces.list_github_repos(self._gh), ""
        except workplaces.ToolError as exc:
            repos, error = [], str(exc)
        wx.CallAfter(self.loaded, repos, error)

    def loaded(self, repos, error: str = ""):
        if not self:
            return
        self._all, self._loaded, self._error = list(repos), True, error
        if error:
            self.status.SetLabel(f"Couldn't list your repositories: {error} You can still "
                                 "type owner/name.")
            self._feedback("Couldn't list your repositories. You can still type owner/name.")
        else:
            count = len(self._all)
            self._feedback(f"{count} repositor{'y' if count == 1 else 'ies'}.")
        self._filter()

    def _filter(self):
        typed = workplaces.parse_repo(self.search.GetValue())
        if typed:
            # owner/name means that repository, not one that mentions it.
            self._shown = [r for r in self._all if r.name.lower() == typed.lower()]
        else:
            words = self.search.GetValue().lower().split()
            self._shown = [r for r in self._all
                           if all(w in (r.name + " " + r.description).lower() for w in words)]
        if self._shown:
            self.list.Set([r.row() for r in self._shown])
        elif not self._loaded:
            self.list.Set(["Loading your repositories…"])
        elif workplaces.parse_repo(self.search.GetValue()):
            self.list.Set([f"Not one of yours: {key_name('Enter')} uses {workplaces.parse_repo(self.search.GetValue())}."])
        elif self._error:
            # In the list, where Tab and the arrows reach it: the status
            # line below isn't in the tab order.
            self.list.Set([f"Couldn't list your repositories. {self._error}"])
        else:
            self.list.Set(["Nothing matches."])
        self.list.SetSelection(0)
        count = len(self._shown)
        self.list_label.SetLabel(f"&Repositories ({count} of {len(self._all)}):")
        set_accessible_name(self.list, f"Repositories, {count} of {len(self._all)}")

    def _on_search_key(self, event):
        if event.GetKeyCode() in (wx.WXK_DOWN, wx.WXK_UP) and self._shown:
            self.list.SetFocus()
            self.list.SetSelection(0)
            return
        event.Skip()

    def chosen_repo(self):
        """owner/name: the selected repository, or else what was typed."""
        index = self.list.GetSelection()
        if self._shown and 0 <= index < len(self._shown):
            return self._shown[index].name
        return workplaces.parse_repo(self.search.GetValue())

    def choose(self):
        if self._clone is not None:
            return  # one clone at a time
        repo = self.chosen_repo()
        if not repo:
            self._feedback("Type owner/name, or choose one of your repositories.")
            self.search.SetFocus()
            return
        target = workplaces.clone_target(Path(self._root), repo)
        if target.exists():
            git = workplaces.find_git()
            if target.is_dir() and git and workplaces.is_clone_of(git, target, repo):
                self._feedback(f"{repo} is already in {target}.")
                self.folder = target
                _end_modal(self, wx.ID_OK)
                return
            wx.MessageBox(f"{target} already exists and isn't a clone of {repo}, so it "
                          "wasn't changed. Browse to it, or clone it yourself somewhere "
                          "else.", self.GetTitle(), wx.OK | wx.ICON_WARNING, self)
            self.search.SetFocus()
            return
        self.status.SetLabel(f"Cloning {repo} into {target}… Cancel stops it.")
        self._feedback(f"Cloning {repo} into {target}. Cancel stops it.")
        # Onto Cancel before the rest are disabled, so focus doesn't wander
        # through them (each read aloud) as they go.
        self.cancel_btn.SetFocus()
        for control in (self.search, self.list, self.use_btn):
            control.Enable(False)
        self._clone = workplaces.Clone(self._gh, repo, target,
                                       lambda folder, error: wx.CallAfter(
                                           self.cloned, repo, folder, error)).start()

    def cloned(self, repo: str, folder, error: str):
        if not self:
            return
        self._clone = None
        for control in (self.search, self.list, self.use_btn):
            control.Enable(True)
        if folder is not None:
            self._feedback(f"Cloned {repo}.")
            self.folder = folder
            _end_modal(self, wx.ID_OK)
            return
        self.status.SetLabel(error or "Cancelled.")
        if error:
            wx.MessageBox(error, self.GetTitle(), wx.OK | wx.ICON_WARNING, self)
        self.search.SetFocus()

    def _on_cancel(self, _event):
        if self._clone is not None:
            self._clone.cancel()
            self._clone = None
        _end_modal(self, wx.ID_CANCEL)


#: The Changed Files styles whose marks a screen reader may not speak (#174).
_SYMBOL_MARKS = (difftext.SYMBOLS, difftext.CHANGED)


class SettingsDialog(wx.Dialog):
    """Announcements and speech (the Speech tab of IDT's settings, adapted)."""

    def __init__(self, parent, speech: SpeechSettings, options, clear_recent=None, say=None):
        super().__init__(parent, title="Settings", size=dialog_size(parent, 680, 620))
        self._options = list(options)
        # Settings kept elsewhere (the session list's sort order) pass through.
        self._original = speech
        # The settings scroll when the screen is too short for them all, so
        # OK and Cancel stay in view (#231: at 175% on a 1080-pixel screen
        # the task-list check box pushed them into a sliver). Where they fit
        # it looks as it always did: no scroll bar.
        body = wx.ScrolledWindow(self, style=wx.VSCROLL)
        body.SetScrollRate(0, 20)
        self._body = body
        outer = wx.BoxSizer(wx.VERTICAL)

        self.level = wx.RadioBox(
            body, label="Announcements",
            choices=[ANNOUNCE_LABELS[level] for level in ANNOUNCE_LEVELS],
            majorDimension=1, style=wx.RA_SPECIFY_COLS)
        self.level.SetSelection(ANNOUNCE_LEVELS.index(speech.announce)
                                if speech.announce in ANNOUNCE_LEVELS else 0)
        outer.Add(self.level, 0, wx.EXPAND | wx.ALL, 10)

        self.all_sessions = wx.CheckBox(
            body, label="Announce when &any listed session finishes a turn, "
                        "not just the open one")
        self.all_sessions.SetValue(speech.announce_all_sessions)
        outer.Add(self.all_sessions, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        if gtk_a11y.IS_LINUX:
            # wxGTK makes a radio box's label the label of the control after
            # it, at idle time, so Orca read this as "Announcements, Announce
            # when...". It isn't one of Announcements' choices. A few idle
            # events at most: a wxGTK that never links it there would
            # otherwise keep this running for as long as Settings is open.
            tries = [0]

            def unlink(event):
                event.Skip()
                tries[0] += 1
                if gtk_a11y.forget_labels(self.all_sessions) or tries[0] >= 20:
                    self.Unbind(wx.EVT_IDLE, handler=unlink)
            self.Bind(wx.EVT_IDLE, unlink)

        self.own_messages = wx.CheckBox(
            body, label="Read your own &messages back when they're sent")
        self.own_messages.SetValue(speech.announce_own)
        outer.Add(self.own_messages, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # What you read, apart from what's spoken on its own.
        # Siblings of the group box on purpose (see QuestionDialog); wx's note
        # about that is kept out of the log.
        with wx.LogNull():
            messages = wx.StaticBoxSizer(wx.VERTICAL, body, "Reading messages")
            self.formatted = wx.CheckBox(
                body, label="Open full messages as a formatted &page (headings, lists and "
                           "tables), not plain text")
            self.formatted.SetValue(speech.formatted_messages)
            messages.Add(self.formatted, 0, wx.ALL, 6)
            self.whole_in_list = wx.CheckBox(
                body, label="Read the &whole message on each item in the messages list "
                           "(otherwise just its first line)")
            self.whole_in_list.SetValue(speech.full_messages_in_list)
            messages.Add(self.whole_in_list, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        outer.Add(messages, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # The choices share one grid, and the rate's note sits beside it, not
        # on a row of its own: that's the room the changed-lines choice (#174)
        # takes, so Settings still fits a 768-high screen without scrolling.
        grid = wx.FlexGridSizer(cols=2, vgap=8, hgap=8)
        grid.AddGrowableCol(1, 1)
        grid.Add(wx.StaticText(body, label="Speech &engine:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.engine_choice = wx.Choice(body, choices=[o.label for o in self._options])
        set_accessible_name(self.engine_choice, "Speech engine")
        grid.Add(self.engine_choice, 1, wx.EXPAND)
        grid.Add(wx.StaticText(body, label="Speaking &rate:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.rate_choice = wx.Choice(
            body, choices=[label.capitalize() for label in RATE_PRESET_LABELS])
        set_accessible_name(self.rate_choice, "Speaking rate")
        rate_row = wx.BoxSizer(wx.HORIZONTAL)
        rate_row.Add(self.rate_choice, 0)
        self.rate_note = wx.StaticText(body, label="")
        rate_row.Add(self.rate_note, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
        grid.Add(rate_row, 0)

        # Why the last announcement wasn't spoken, while that's still so
        # (#98): a read-only box, so Tab reaches it and a screen reader reads
        # it, as static text under a disabled choice never is. Under the
        # speech choices it explains, in the grid's second column.
        from .. import speech as speech_module
        problem = speech_module.speaker.last_problem
        self.speech_problem = None
        if problem:
            self.speech_problem = wx.TextCtrl(
                body, value=f"Last announcement {problem}.",
                style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL, size=dip(self, -1, 44))
            set_accessible_name(self.speech_problem, "Speech problem")
            grid.AddSpacer(0)
            grid.Add(self.speech_problem, 0, wx.EXPAND)

        grid.Add(wx.StaticText(body, label=NOTIFY_SETTING_LABEL), 0, wx.ALIGN_CENTER_VERTICAL)
        self.notify_choice = wx.Choice(body, choices=[NOTIFY_LABELS[n] for n in NOTIFY_LEVELS])
        set_accessible_name(self.notify_choice, f"{NOTIFY_SETTING_NAME}, while The Chat Place "
                                                "isn't the active window")
        self.notify_choice.SetSelection(NOTIFY_LEVELS.index(speech.notifications)
                                        if speech.notifications in NOTIFY_LEVELS else 0)
        grid.Add(self.notify_choice, 0)

        # How Changed Files marks each line of a change (#174). Symbols can be
        # silent at a screen reader's lower punctuation levels, so moving the
        # choice onto one says so, spoken as it's chosen. Only someone using a
        # screen reader needs that, and static text would never be read to
        # them (#98), so there's no note on screen; nor is it in the choice's
        # name, which on Windows comes from its label, not SetName.
        grid.Add(wx.StaticText(body, label="Mark c&hanged lines with:"), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        self.marks_choice = wx.Choice(body, choices=[difftext.LABELS[style]
                                                     for style in difftext.STYLES])
        set_accessible_name(self.marks_choice, "Mark changed lines with")
        self.marks_choice.SetSelection(difftext.STYLES.index(speech.change_marks)
                                       if speech.change_marks in difftext.STYLES else 0)
        self._say = say
        self._marks_symbols = self._marks_style() in _SYMBOL_MARKS
        self.marks_choice.Bind(wx.EVT_CHOICE, lambda e: self._on_marks_choice())
        grid.Add(self.marks_choice, 0)
        outer.Add(grid, 0, wx.EXPAND | wx.ALL, 10)

        # New Session's recent folders (#215): how many, and clearing them,
        # which is asked first and then done at once, not on OK: Cancel
        # can't take it back, so it isn't made to look as if it could.
        recent_row = wx.BoxSizer(wx.HORIZONTAL)
        recent_row.Add(wx.StaticText(body, label="Recent &folders New Session offers:"), 0,
                       wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.recent_limit = wx.SpinCtrl(body, min=0, max=workplaces.RECENT_MAX,
                                        initial=speech.recent_folder_limit)
        set_accessible_name(self.recent_limit, "Recent folders New Session offers")
        # On a Mac it's a text box and arrows of wx's own: VoiceOver reads
        # the text box, so that's named too. Windows' has no such children.
        for part in self.recent_limit.GetChildren():
            if isinstance(part, wx.TextCtrl):
                set_accessible_name(part, "Recent folders New Session offers")
        recent_row.Add(self.recent_limit, 0, wx.RIGHT, 8)
        self.clear_recent_btn = None
        if clear_recent is not None:
            self.clear_recent_btn = wx.Button(body, label="C&lear Recent Folders")
            self.clear_recent_btn.Bind(wx.EVT_BUTTON,
                                       lambda e: self._on_clear_recent(clear_recent))
            recent_row.Add(self.clear_recent_btn, 0)
        outer.Add(recent_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # The label wraps onto several lines: as one it ran past the dialog's
        # edge (#176). A check box's label is its spoken name, so screen
        # readers hear the same sentence as before.
        self.remote_control = wx.CheckBox(body, label=_wrapped(
            self, "Turn on Remote &Control for The Chat Place's sessions, so you can reach "
                  "them from claude.ai and other devices. Their conversations are copied to "
                  "claude.ai and kept there. (File, Remote Control changes one session.)",
            self.GetClientSize().width - 20 - _check_box_glyph(self)))
        self.remote_control.SetValue(speech.remote_control)
        outer.Add(self.remote_control, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # #231: Claude Code gives newer models no task list unless asked.
        self.task_list = wx.CheckBox(body, label=_wrapped(
            self, "Let Claude &keep a task list in The Chat Place's sessions, as it does in "
                  "the desktop app, so View, Tasks shows how far along it is "
                  "(from the next message)",
            self.GetClientSize().width - 20 - _check_box_glyph(self)))
        self.task_list.SetValue(speech.task_list)
        outer.Add(self.task_list, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        codex_label = "Include Code&x chats (restart required)"
        if not platform_paths.find_codex():
            codex_label += (". Codex isn't installed on this computer; "
                            "install the Codex app or CLI, then restart.")
        self.codex_enabled = wx.CheckBox(body, label=_wrapped(
            self, codex_label,
            self.GetClientSize().width - 20 - _check_box_glyph(self)))
        self._codex_choice = speech.codex_enabled
        self._codex_changed = False
        self._initial_codex_effective = speech.includes_codex()
        self.codex_enabled.SetValue(self._initial_codex_effective)
        self.codex_enabled.Bind(wx.EVT_CHECKBOX, self._on_codex_choice)
        outer.Add(self.codex_enabled, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # Only where Copilot CLI has saved a session (or the preview is
        # already on, so it can be turned off): elsewhere there is nothing to
        # read, and Settings keeps fitting a 768-high screen unscrolled (#352).
        self._copilot_choice = speech.copilot_enabled
        self.copilot_enabled = None
        # Only once Copilot CLI is here (installed, or has saved a chat), so
        # Settings keeps its height for everyone else (the Mac's 768).
        if (speech.copilot_enabled or platform_paths.copilot_has_sessions()
                or platform_paths.find_copilot() is not None):
            self.copilot_enabled = wx.CheckBox(body, label=_wrapped(
                self, "Include GitHub C&opilot CLI chats (preview; restart required)",
                self.GetClientSize().width - 20 - _check_box_glyph(self)))
            self.copilot_enabled.SetValue(speech.copilot_enabled)
            outer.Add(self.copilot_enabled, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # Agent conversations (#248): automatic hand-offs are off until
        # turned on here; each conversation then chooses it, and its limit.
        self.agent_auto = wx.CheckBox(body, label=_wrapped(
            self, "Let a&gent conversations hand replies across automatically",
            self.GetClientSize().width - 20 - _check_box_glyph(self)))
        self.agent_auto.SetValue(speech.agent_auto_mode)
        # A little less below it than the others, so Settings still fits a
        # 768-high screen without scrolling (it did by 2 pixels on a Mac).
        # Only below: its sides keep the others' 10, so it lines up (#368).
        outer.Add(self.agent_auto, 0, wx.LEFT | wx.RIGHT, 10)
        outer.AddSpacer(6)

        # #141: the dialog at the first start after an update.
        self.update_notice = wx.CheckBox(
            body, label="&Tell me when an update has been installed, with a link to what's new")
        self.update_notice.SetValue(speech.update_installed_notice)
        outer.Add(self.update_notice, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        from ..retention import describe as describe_retention
        outer.Add(wx.StaticText(body, label="History retention and ke&pt copies:"),
                  0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.retention = wx.TextCtrl(body, value=describe_retention(),
                                    size=(-1, self.FromDIP(110)),
                                    style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        set_accessible_name(self.retention, "History retention and kept copies")
        self.retention.SetInsertionPoint(0)
        outer.Add(self.retention, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.retention_settings = wx.Button(body, label="Open Claude's retention settings")
        self.retention_settings.Bind(wx.EVT_BUTTON, self._open_retention_settings)
        outer.Add(self.retention_settings, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        buttons = wx.StdDialogButtonSizer()
        ok = wx.Button(self, wx.ID_OK)
        ok.SetDefault()
        buttons.AddButton(ok)
        buttons.AddButton(wx.Button(self, wx.ID_CANCEL))
        buttons.Realize()
        body.SetSizer(outer)
        page = wx.BoxSizer(wx.VERTICAL)
        page.Add(body, 1, wx.EXPAND)
        page.Add(buttons, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(page)
        self._fit_scrolling(body, outer)

        selected = 0
        for i, option in enumerate(self._options):
            if option.engine == speech.engine and option.voice == speech.voice:
                selected = i
                break
        self.engine_choice.SetSelection(selected)
        try:
            self.rate_choice.SetSelection(RATE_PRESET_LABELS.index(speech.rate_preset))
        except ValueError:
            self.rate_choice.SetSelection(0)
        self.engine_choice.Bind(wx.EVT_CHOICE, lambda e: self._update_rate_state())
        self._update_rate_state()
        wx.CallAfter(self.level.SetFocus)

    def _open_retention_settings(self, _event):
        path = platform_paths.claude_home() / 'settings.json'
        if not path.is_file():
            wx.MessageBox("Claude's settings.json does not exist yet. Set cleanupPeriodDays "
                          "in Claude Code's user settings; The Chat Place never creates or edits it.",
                          "History Retention", wx.OK | wx.ICON_INFORMATION, self)
            return
        try:
            platform_paths.edit_file(path)
        except OSError:
            wx.MessageBox("Couldn't open Claude's settings in your editor.",
                          "History Retention", wx.OK | wx.ICON_ERROR, self)

    def _on_clear_recent(self, clear_recent):
        if wx.MessageBox("Clear all of New Session's recent folders? Each comes back when a "
                         "session works in it again.", "Clear Recent Folders",
                         wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) == wx.YES:
            clear_recent()

    def _selected_option(self):
        index = self.engine_choice.GetSelection()
        if index == wx.NOT_FOUND or index >= len(self._options):
            return self._options[0]
        return self._options[index]

    def _update_rate_state(self):
        option = self._selected_option()
        self.rate_choice.Enable(option.has_rate)
        if option.is_screen_reader:
            self.rate_note.SetLabel("Voice and rate follow your screen reader's own settings.")
        elif option.has_rate:
            self.rate_note.SetLabel("")
        else:
            self.rate_note.SetLabel("This engine uses its default rate.")
        self.Layout()

    def _marks_style(self) -> str:
        index = self.marks_choice.GetSelection()
        return difftext.STYLES[index] if 0 <= index < len(difftext.STYLES) \
            else difftext.DEFAULT

    def _on_marks_choice(self):
        """Say the punctuation note once on moving from words to symbols,
        not again between the two symbol styles."""
        symbols = self._marks_style() in _SYMBOL_MARKS
        if symbols and not self._marks_symbols and self._say is not None:
            self._say(difftext.SYMBOLS_NOTE)
        self._marks_symbols = symbols

    def _fit_scrolling(self, body, content):
        """As tall as the settings need, up to the screen; past that they
        scroll above OK and Cancel. As in QuestionDialog, the body's minimum
        is the settings' height only while fitting. When they will scroll,
        the wrapped check box labels are wrapped again short of the scroll
        bar, so their ends aren't hidden under it."""
        height = content.GetMinSize().height + self.GetSizer().GetMinSize().height
        chrome = self.GetSize().height - self.GetClientSize().height
        if height + chrome > screen_area(self).height:
            narrower = (self.GetClientSize().width - 20 - _check_box_glyph(self)
                        - wx.SystemSettings.GetMetric(wx.SYS_VSCROLL_X, self))
            for box in (self.remote_control, self.task_list, self.codex_enabled,
                        self.copilot_enabled, self.agent_auto):
                if box is None:
                    continue
                box.SetLabel(_wrapped(self, box.GetLabel().replace("\n", " "), narrower))
        body.SetMinSize((-1, content.GetMinSize().height))
        _fit_height(self)
        body.SetMinSize((-1, -1))
        self.Layout()
        body.FitInside()

    def _on_codex_choice(self, event):
        self._codex_changed = True
        event.Skip()

    def get_settings(self) -> SpeechSettings:
        option = self._selected_option()
        index = self.rate_choice.GetSelection()
        preset = RATE_PRESET_LABELS[index] if 0 <= index < len(RATE_PRESET_LABELS) else "default"
        level_index = self.level.GetSelection()
        level = ANNOUNCE_LEVELS[level_index] if 0 <= level_index < len(ANNOUNCE_LEVELS) \
            else ANNOUNCE_LEVELS[0]
        return dataclasses.replace(self._original, announce=level,
                                   announce_all_sessions=self.all_sessions.GetValue(),
                                   announce_own=self.own_messages.GetValue(),
                                   engine=option.engine, voice=option.voice,
                                   rate_preset=preset,
                                   formatted_messages=self.formatted.GetValue(),
                                   full_messages_in_list=self.whole_in_list.GetValue(),
                                   notifications=NOTIFY_LEVELS[max(
                                       self.notify_choice.GetSelection(), 0)],
                                   remote_control=self.remote_control.GetValue(),
                                   task_list=self.task_list.GetValue(),
                                   codex_enabled=(self.codex_enabled.GetValue() if self._codex_changed
                                       or self.codex_enabled.GetValue() != self._initial_codex_effective
                                       else self._codex_choice),
                                   copilot_enabled=(self.copilot_enabled.GetValue()
                                                    if self.copilot_enabled is not None
                                                    else self._copilot_choice),
                                   update_installed_notice=self.update_notice.GetValue(),
                                   agent_auto_mode=self.agent_auto.GetValue(),
                                   change_marks=self._marks_style(),
                                   recent_folder_limit=self.recent_limit.GetValue())


class MessageDialog(wx.Dialog):
    """One message's full text, read-only, to read by line, word and character.

    A RichEdit, like the reply box: checked in the vmtest VM, a RichEdit takes
    its accessible name from the label before it, where a plain multiline
    EDIT reports its contents instead. Escape (or Close) returns to the list,
    on the same message.
    """

    def __init__(self, parent, speaker_label: str, text: str):
        super().__init__(parent, title=(speaker_label if speaker_label == "User Guide"
                                      else f"Message: {speaker_label}"),
                         size=dialog_size(parent, 720, 520), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        sizer = wx.BoxSizer(wx.VERTICAL)
        label = f"{speaker_label}:"
        sizer.Add(wx.StaticText(self, label="&" + label), 0, wx.LEFT | wx.TOP, 8)
        self.text = wx.TextCtrl(self, value=text,
                                style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        set_accessible_name(self.text, label.rstrip(":"))
        sizer.Add(self.text, 1, wx.EXPAND | wx.ALL, 8)
        close = wx.Button(self, wx.ID_CANCEL, "&Close")
        sizer.Add(close, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        self.text.SetInsertionPoint(0)
        wx.CallAfter(self.text.SetFocus)


class UpdateInstalledDialog(wx.Dialog):
    """The first start after an update (#141), as QuickMail's: says which
    version is now running, offers its release notes, and closes.

    The message is the dialog's text, which JAWS and NVDA read when it
    opens. Focus starts on See What's New, whose label names the version
    too, so VoiceOver (which reads the window title and the focused
    control, not the text beside it) hears the whole of it. See What's New
    opens the notes in the browser and closes the dialog; Close or Escape
    just closes it."""

    TITLE = "The Chat Place Update Installed"

    def __init__(self, parent, version: str, open_notes):
        super().__init__(parent, title=self.TITLE)
        self._open_notes = open_notes
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.message = wx.StaticText(self, label=f"The Chat Place was updated to {version}.")
        sizer.Add(self.message, 0, wx.ALL, 16)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.whats_new = wx.Button(self, label=f"See &what's new in {version}")
        set_accessible_name(self.whats_new, f"See what's new in {version}")
        row.Add(self.whats_new, 0, wx.RIGHT, 8)
        self.close_button = wx.Button(self, wx.ID_CANCEL, "&Close")
        row.Add(self.close_button, 0)
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 16)
        self.SetSizerAndFit(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        self.whats_new.Bind(wx.EVT_BUTTON, lambda e: self.see_whats_new())
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.CentreOnParent()
        self.whats_new.SetFocus()
        wx.CallAfter(self._focus_first)

    def _focus_first(self):
        if self:
            self.whats_new.SetFocus()

    def _on_char_hook(self, event):
        if press_focused_button_on_a_mac(event):
            return
        event.Skip()

    def see_whats_new(self):
        # The caller's open_notes reports its own failures, so this always
        # gets back to the app.
        self._open_notes()
        if self.IsModal():
            self.EndModal(wx.ID_OK)
        else:
            self.Hide()


# -- a message as a formatted page (#190) -----------------------------------------------

#: EndModal code for "Read as Plain Text": the caller opens MessageDialog.
ID_PLAIN_TEXT = wx.NewIdRef()

#: Escape is pressed inside the page, where wx never sees it, so a script
#: added to the page posts it back. Added with AddUserScript, which runs
#: whatever the page's Content-Security-Policy says. Edge reaches the "hub"
#: handler as window.hub, WebKit as window.webkit.messageHandlers.hub (#119).
#: P is matched by its key code: on a Mac, Option+P types "π".
_KEY_RELAY = """
document.addEventListener('keydown', function (e) {
  var hub = (window.webkit && window.webkit.messageHandlers &&
             window.webkit.messageHandlers.hub) || window.hub;
  if (!hub) { return; }
  if (e.key === 'Escape') { e.preventDefault(); hub.postMessage('escape'); }
  else if (e.altKey && e.code === 'KeyP') {
    e.preventDefault(); hub.postMessage('plain'); }
}, true);
"""

#: Linux: WebKitGTK puts the focus on its own widget, not in the page, and
#: Orca then has nowhere to start reading. Focusing the message's first
#: element (a heading, a paragraph) puts Orca in the page, in browse mode,
#: with its caret on the first line. Focusing the page's main element
#: instead left Orca's caret after the last line.
_FOCUS_PAGE = """
document.addEventListener('DOMContentLoaded', function () {
  var main = document.querySelector('main') || document.body;
  var first = main.firstElementChild || main;
  first.setAttribute('tabindex', '-1');
  first.focus();
  window.getSelection().collapse(first, 0);
});
"""


def _webview_backend():
    """The engine for the formatted page: Edge WebView2 on Windows, the
    system's WebKit on a Mac (#119), and on Linux WebKitGTK, which Orca reads
    like a web page in a browser."""
    import wx.html2
    if sys.platform == "win32":
        return wx.html2.WebViewBackendEdge
    return wx.html2.WebViewBackendWebKit


def formatted_view_available() -> bool:
    """Whether this computer's web view (Edge WebView2 on Windows, WebKit on
    a Mac, WebKitGTK on Linux) can show the formatted page."""
    try:
        import wx.html2
        return bool(wx.html2.WebView.IsBackendAvailable(_webview_backend()))
    except Exception:  # noqa: BLE001 - no html2, no runtime: plain text
        return False


def _prepare_webview_data_folder() -> None:
    """WebView2 keeps a profile folder. Its default is beside the program,
    which for an installed copy is the folder Velopack replaces on update, and
    for a source run may be Python's own folder. Put it in local app data
    instead, before the first WebView is made. Windows only: a Mac's WebKit
    keeps no such folder.

    Linux: WebKitGTK's DMA-BUF renderer draws a blank page with some
    graphics drivers (seen on ARM boards and in virtual machines); a page of
    text loses nothing without it."""
    if sys.platform.startswith("linux"):
        os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
        return
    if sys.platform != "win32" or os.environ.get("WEBVIEW2_USER_DATA_FOLDER"):
        return
    local = os.environ.get("LOCALAPPDATA")
    if local:
        os.environ["WEBVIEW2_USER_DATA_FOLDER"] = os.path.join(local, "TheChatPlace WebView2")


class FormattedMessageDialog(wx.Dialog):
    """One message as a web page: the screen reader's browse mode moves by
    heading, table, list and code block. Escape closes it; Read as Plain Text
    (Alt+P; Option+P on a Mac) switches to the text box, to read by character.

    Raises RuntimeError if the WebView can't be made; the caller falls back to
    MessageDialog.
    """

    def __init__(self, parent, title: str, page_html: str):
        import wx.html2
        from .. import platform_paths
        super().__init__(parent, title=title, size=dialog_size(parent, 820, 620),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        _prepare_webview_data_folder()
        self._open_url = platform_paths.open_url
        self._loaded = False
        self.app_link = None  # a thechatplace:// link chosen in the page (#144)
        try:
            self.view = wx.html2.WebView.New(self, backend=_webview_backend())
        except Exception as exc:  # noqa: BLE001
            self.Destroy()
            raise RuntimeError(f"Couldn't show the formatted page: {exc}") from exc
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.view, 1, wx.EXPAND | wx.ALL, 4)
        row = wx.BoxSizer(wx.HORIZONTAL)
        plain = wx.Button(self, ID_PLAIN_TEXT, "Read as &Plain Text")
        row.Add(plain, 0, wx.RIGHT, 6)
        row.Add(wx.Button(self, wx.ID_CANCEL, "&Close"), 0)
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        plain.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(ID_PLAIN_TEXT))
        if wx.Platform == "__WXMAC__":
            # A Mac button has no Alt mnemonic, so Option+P works from the
            # buttons too, and Command+W closes, as in any Mac window (#119).
            close_id = wx.NewIdRef()
            self.SetAcceleratorTable(wx.AcceleratorTable([
                (wx.ACCEL_ALT, ord("P"), ID_PLAIN_TEXT),
                (wx.ACCEL_CTRL, ord("W"), close_id),
            ]))
            self.Bind(wx.EVT_MENU, lambda e: self.EndModal(ID_PLAIN_TEXT), id=ID_PLAIN_TEXT)
            self.Bind(wx.EVT_MENU, lambda e: self.EndModal(wx.ID_CANCEL), id=close_id)
        try:
            self.view.EnableAccessToDevTools(False)
            self.view.AddScriptMessageHandler("hub")
            self.view.AddUserScript(_KEY_RELAY)
            if wx.Platform == "__WXGTK__":
                self.view.AddUserScript(_FOCUS_PAGE)
        except Exception:  # noqa: BLE001 - Escape still works from the buttons
            pass
        self.view.Bind(wx.html2.EVT_WEBVIEW_SCRIPT_MESSAGE_RECEIVED, self._on_script_message)
        self.view.Bind(wx.html2.EVT_WEBVIEW_NAVIGATING, self._on_navigating)
        self.view.Bind(wx.html2.EVT_WEBVIEW_NEWWINDOW, self._on_new_window)
        self.view.Bind(wx.html2.EVT_WEBVIEW_LOADED, self._on_loaded)
        self.view.SetPage(page_html, "")

    def _on_loaded(self, _event):
        if not self._loaded:
            self._loaded = True
            # Into the page, so the screen reader starts reading it.
            self.view.SetFocus()
            gtk_a11y.focus_web_view(self.view)  # Linux: past wx's wrapper, into the page

    def _on_script_message(self, event):
        message = event.GetString()
        if message == "escape":
            wx.CallAfter(self.EndModal, wx.ID_CANCEL)
        elif message == "plain":
            wx.CallAfter(self.EndModal, ID_PLAIN_TEXT)

    def _on_navigating(self, event):
        if not self._loaded:
            return  # the page itself
        # A link: never in here. http, https and mailto go to the browser.
        event.Veto()
        self._follow(event.GetURL())

    def _on_new_window(self, event):
        self._follow(event.GetURL())

    def _follow(self, url: str):
        """A link to a session (#144) closes the page and is opened by the
        window itself (``app_link``), never handed to the shell, which would
        only start The Chat Place again."""
        from ..links import is_app_link
        if is_app_link(url):
            self.app_link = url
            wx.CallAfter(self.EndModal, wx.ID_CANCEL)
        elif url.lower().startswith(("http:", "https:", "mailto:")):
            self._open_url(url)


# -- Claude is waiting for an answer (#187, #188) ---------------------------------------
#
# Shared rules: Escape (Answer Later) closes without answering, so the turn
# keeps waiting and Ctrl+Shift+A comes back to it. Nothing refuses or approves
# by accident: Enter's default button is always the harmless choice.

ALLOW, ALLOW_SESSION, DENY = "allow", "session", "deny"
ID_ALLOW = wx.NewIdRef()
ID_ALLOW_SESSION = wx.NewIdRef()
ID_DENY = wx.NewIdRef()


def _fit_height(dialog, max_height: Optional[int] = None) -> None:
    """Make ``dialog`` the height its controls need, keeping its width: no
    band of empty space (#176: Settings and Question had them), and no
    controls cut off, up to ``max_height`` (the screen's height by
    default)."""
    dialog.Layout()
    width, height = dialog.GetSize()
    chrome = height - dialog.GetClientSize().height
    limit = max_height if max_height is not None else screen_area(dialog).height
    target = min(dialog.GetSizer().GetMinSize().height + chrome, limit)
    if target != height:
        dialog.SetSize((width, target))
        dialog.Layout()


def _fit_width(dialog) -> None:
    """Make ``dialog`` at least as wide as its controls need, up to the
    screen's width. A row of buttons that fits on Windows can need more
    room in another system's font (Prompts' seven, on Linux)."""
    dialog.Layout()
    width, height = dialog.GetSize()
    chrome = width - dialog.GetClientSize().width
    target = min(dialog.GetSizer().GetMinSize().width + chrome, screen_area(dialog).width)
    if target > width:
        dialog.SetSize((target, height))
        dialog.Layout()


def _check_box_glyph(window) -> int:
    """The room a check box's square and its gap take beside its label, at
    this screen's scaling: 22 pixels at 100%, 46 at 200% (#185)."""
    probe = wx.CheckBox(window, label="Ag")
    try:
        return probe.GetBestSize().width - probe.GetTextExtent("Ag").width
    finally:
        probe.Destroy()


def _wrapped(window, text: str, width: int) -> str:
    """``text`` with line breaks so no line is wider than ``width`` in
    ``window``'s font. For a control whose label can't wrap itself (a check
    box), where the breaks are part of the label (#176)."""
    from wx.lib.wordwrap import wordwrap
    dc = wx.ClientDC(window)
    dc.SetFont(window.GetFont())
    # Without the space wordwrap leaves at each line's end, which a braille
    # display would show.
    lines = wordwrap(text, width, dc).rstrip("\n").split("\n")
    return "\n".join(line.rstrip() for line in lines)


class WrappingText(wx.StaticText):
    """A paragraph that wraps to the width its sizer gives it, and wraps
    again when that changes (#176). Add it with wx.EXPAND. A plain StaticText
    is one line as long as its text, and a long one ran past the dialog's
    edge: Report a Bug hid the support address that way. Screen readers read
    the label as written, without the line breaks."""

    def __init__(self, parent, label: str = ""):
        # Not resized to its label: its width is the sizer's to give.
        super().__init__(parent, label=label, style=wx.ST_NO_AUTORESIZE)
        self._text = label
        self._width = 0
        self._laying_out = False
        self._again = False
        self.SetMinSize((1, -1))  # its width comes from the sizer, never the text
        self.Bind(wx.EVT_SIZE, self._on_size)

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self._width = 0
            self._rewrap(self.GetSize().width)

    # The label as written, without the line breaks: what callers compare.
    # Set it with set_text.
    def GetLabel(self):
        return self._text

    def SetLabel(self, label):
        self.set_text(label)

    def _on_size(self, event):
        event.Skip()
        self._rewrap(event.GetSize().width)

    def _rewrap(self, width: int):
        # Nothing to lay out in a window that's going.
        if (width <= 40 or width == self._width or self.IsBeingDeleted()
                or self.GetParent().IsBeingDeleted()):
            return
        self._width = width
        # Measured with the control's own font in its own pixels: on a PC at
        # 150%, while the app wasn't DPI aware (#185), wx's Wrap left a
        # heading 50 pixels too wide unwrapped.
        wx.StaticText.SetLabel(self, _wrapped(self, self._text, width))
        # Measured with no minimum, which would hold the old height.
        self.SetMinSize((1, -1))
        self.InvalidateBestSize()
        self.SetMinSize((1, self.GetBestSize().height))
        # Laid out again with the new height. If that changes the width (a
        # column whose share depends on its contents), it wraps once more
        # and is laid out again; a few rounds settle it.
        if self._laying_out:
            self._again = True
            return
        self._laying_out = True
        try:
            for _round in range(4):
                self._again = False
                self.GetParent().Layout()
                if not self._again:
                    break
        finally:
            self._laying_out = False


def _read_only_text(parent, value: str, name: str, min_height: int = 160) -> wx.TextCtrl:
    text = wx.TextCtrl(parent, value=value,
                       style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
    set_accessible_name(text, name)
    text.SetMinSize(dip(parent, -1, min_height))
    text.SetInsertionPoint(0)
    return text


class PermissionDialog(wx.Dialog):
    """Claude wants to use a tool: Allow, Allow for This Chat, Deny.

    The request is in a read-only box to read by line (the whole command, or
    the file and what would change). Deny is the default button, so a stray
    Enter refuses; a reason typed for Deny goes back to Claude.
    """

    def __init__(self, parent, session_title: str, request):
        super().__init__(parent, title=f"Claude needs permission: {session_title}",
                         size=dialog_size(parent, 700, 540), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.choice = ""
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="&Request:"), 0, wx.LEFT | wx.TOP, 8)
        self.request_text = _read_only_text(self, request.detail(), "Request")
        sizer.Add(self.request_text, 1, wx.EXPAND | wx.ALL, 8)
        sizer.Add(wx.StaticText(self, label="&Reason to give Claude if you deny (optional):"),
                  0, wx.LEFT, 8)
        self.reason = wx.TextCtrl(self)
        set_accessible_name(self.reason, "Reason to give Claude if you deny (optional)")
        sizer.Add(self.reason, 0, wx.EXPAND | wx.ALL, 8)

        row = wx.BoxSizer(wx.HORIZONTAL)
        allow = wx.Button(self, ID_ALLOW, "&Allow")
        row.Add(allow, 0, wx.RIGHT, 6)
        session_label = request.allow_for_session_label()
        if session_label:
            self.session_button = wx.Button(self, ID_ALLOW_SESSION, "Allow for This &Chat")
            # The button says what it does in full to a screen reader.
            self.session_button.SetToolTip(session_label)
            set_accessible_name(self.session_button, session_label)
            row.Add(self.session_button, 0, wx.RIGHT, 6)
        else:
            self.session_button = None
        deny = wx.Button(self, ID_DENY, "&Deny")
        deny.SetDefault()
        row.Add(deny, 0, wx.RIGHT, 6)
        row.Add(wx.Button(self, wx.ID_CANCEL, "Answer &Later"), 0)
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        for button_id, choice in ((ID_ALLOW, ALLOW), (ID_ALLOW_SESSION, ALLOW_SESSION),
                                  (ID_DENY, DENY)):
            self.Bind(wx.EVT_BUTTON, lambda e, c=choice: self._choose(c), id=button_id)
        wx.CallAfter(self.request_text.SetFocus)

    def _choose(self, choice: str):
        self.choice = choice
        self.EndModal(wx.ID_OK)

    def reason_text(self) -> str:
        return self.reason.GetValue().strip()


#: Why a Codex request has no Allow for This Chat (#307).
CODEX_NOT_KEPT = ("Allow for This Chat isn't offered for this request, so approving it keeps "
                  "nothing in Chat Permissions. The Chat Place can keep only some Git commands, "
                  "with their usual options: status, switch, add, commit, push, fetch, diff and log.")


#: Each runtime provider's one-time decisions: allowing, and denying (#352).
ALLOW_DECISIONS = ("accept", "allow_once")
DENY_DECISIONS = ("decline", "reject_once")


class CodexPermissionDialog(wx.Dialog):
    """A runtime provider's decisions: only the one-time ones it listed
    (#284). Codex's, and Copilot's (``provider``), whose requests have the
    same methods (copilot_runtime.CopilotRequest).

    The request text says everything being asked for, including extra access
    (folders, network) beyond the chat's own folder. Deny is the default
    button when offered; otherwise Answer Later is, so a stray Enter never
    allows anything or stops the turn.
    """
    def __init__(self, parent, session_title, request, file_detail="", *, chat_rule=None,
                 provider="Codex"):
        super().__init__(parent, title=f"{provider} needs permission: {session_title}",
                         size=dialog_size(parent, 700, 540),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.choice = ""
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="&Request:"), 0, wx.LEFT | wx.TOP, 8)
        detail = request.detail(file_detail)
        if chat_rule is not None:
            from ..providers.codex_permissions import NOTICE
            from ..providers.codex_requests import SAVED_RULE_NOTE
            detail = detail.replace(SAVED_RULE_NOTE, 'Codex offered to keep a native approval. '
                                    'If you choose Allow for This Chat, The Chat Place remembers it only in this chat. '
                                    'Allow Once keeps no approval. Codex keeps no approval with either choice.')
            detail += '\n\nRemember for this chat: ' + chat_rule.description + '\n\n' + NOTICE
        else:
            # Say why there's no Allow for This Chat, so approving and then
            # finding Chat Permissions empty isn't a surprise (#307).
            from ..providers.codex_requests import SAVED_RULE_NOTE
            detail = detail.replace(SAVED_RULE_NOTE, SAVED_RULE_NOTE + ' ' + CODEX_NOT_KEPT)
        self.request_text = _read_only_text(self, detail, "Request")
        sizer.Add(self.request_text, 1, wx.EXPAND | wx.ALL, 8)
        self.chat_rule_button = None
        if chat_rule is not None:
            self.chat_rule_button = wx.Button(self, label='Allow for This &Chat')
            name = 'Allow for this chat: ' + chat_rule.description
            self.chat_rule_button.SetToolTip(name)
            set_accessible_name(self.chat_rule_button, name)
            self.chat_rule_button.Enable(request.reviewable(file_detail))
            self.chat_rule_button.Bind(wx.EVT_BUTTON, lambda event: self._choose('tcp_chat_rule'))
            sizer.Add(self.chat_rule_button, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        reviewable = request.reviewable(file_detail)
        self.buttons = {}
        for decision in request.offered():
            button = wx.Button(self, label=request.choice_text(decision))
            if decision in ALLOW_DECISIONS:
                # Nothing to review means nothing to allow.
                button.Enable(reviewable)
            row.Add(button, 0, wx.RIGHT, 6)
            button.Bind(wx.EVT_BUTTON, lambda event, choice=decision: self._choose(choice))
            self.buttons[decision] = button
        self.allow = next((self.buttons[d] for d in ALLOW_DECISIONS if d in self.buttons), None)
        later = wx.Button(self, wx.ID_CANCEL, "Answer &Later")
        row.Add(later, 0)
        next((self.buttons[d] for d in DENY_DECISIONS if d in self.buttons), later).SetDefault()
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        wx.CallAfter(self.request_text.SetFocus)

    def _choose(self, choice):
        self.choice = choice
        self.EndModal(wx.ID_OK)


class ChatPermissionsDialog(wx.Dialog):
    """File, Chat Permissions (#307, #329): what this chat may do without
    asking. The permission mode in words with Change, then one row per kept
    rule, read in full, with Remove (or Delete) and Remove All, each after one
    confirmation that names what goes.

    The window does the work through ``host`` (an object with ``load(done)``,
    ``change_mode(done)``, ``mode_text()``, ``can_change_mode()``, ``note()``,
    ``empty_text`` and ``can_remove``, with ``remove(rule_id, done)`` when it can;
    a host may add ``native(done)`` and ``native_pending`` for a read-only note:
    Codex's rule files, or Claude Code's own settings), whose ``done(rows, error)`` comes back on the
    UI thread, after the work is finished: a removal is never said to be done
    before it is. A host that keeps nothing (Codex, #307) gets no Remove buttons.
    """

    def __init__(self, parent, title: str, host):
        super().__init__(parent, title=f"Chat Permissions: {title}",
                         size=dialog_size(parent, 680, 520),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.host = host
        self.can_remove = getattr(host, "can_remove", True)
        self.rows = []
        #: Why an action must wait ("" when none must): the list is still
        #: being read, or a removal or mode change hasn't finished.
        self.busy = ""
        self.loaded = False
        self.load_error = ""
        sizer = wx.BoxSizer(wx.VERTICAL)
        mode_row = wx.BoxSizer(wx.HORIZONTAL)
        mode_row.Add(wx.StaticText(self, label="Permission &mode:"), 0,
                     wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.mode = wx.TextCtrl(self, style=wx.TE_READONLY)
        set_accessible_name(self.mode, "Permission mode")
        mode_row.Add(self.mode, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.change_btn = wx.Button(self, label="C&hange...")
        mode_row.Add(self.change_btn, 0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(mode_row, 0, wx.EXPAND | wx.ALL, 8)
        sizer.Add(wx.StaticText(self, label="&Allowed for this chat:"), 0, wx.LEFT, 8)
        self.list = wx.ListBox(self, style=wx.LB_SINGLE | wx.LB_HSCROLL)
        # A row is read in full by a screen reader; sighted, a long one scrolls.
        self.list.SetMinSize(dip(self, -1, 150))
        sizer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.remove_btn = self.remove_all_btn = None
        if self.can_remove:
            self.remove_btn = wx.Button(self, label="&Remove...")
            self.remove_all_btn = wx.Button(self, label="Remove A&ll...")
            for button in (self.remove_btn, self.remove_all_btn):
                row.Add(button, 0, wx.RIGHT, 6)
            self.remove_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_remove())
            self.remove_all_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_remove_all())
        row.AddStretchSpacer()
        self.close_btn = wx.Button(self, wx.ID_CANCEL, "&Close")
        row.Add(self.close_btn, 0)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 8)
        sizer.Add(wx.StaticText(self, label="Abou&t these permissions:"), 0, wx.LEFT, 8)
        self.note = _read_only_text(self, "", "About these permissions", min_height=110)
        # A share of the height: it can list Claude Code's or Codex's own
        # rules, which shouldn't start below the fold (#307).
        sizer.Add(self.note, 1, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        self._native = ""
        self.change_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_change())
        self.list.Bind(wx.EVT_KEY_DOWN, self._on_list_key)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        set_list_items_accessible(self.list, self._list_name, lambda index: None)
        self.refresh_mode()
        self.list.Set(["Checking what this chat allows..."])
        self.list.SetSelection(0)
        self.reload()
        native = getattr(host, "native", None)
        if callable(native):
            self._native = getattr(host, "native_pending", "")
            native(self._native_ready)
        self._show_note()
        wx.CallAfter(self.list.SetFocus)

    # -- what it shows ----------------------------------------------------------

    def _alive(self) -> bool:
        return bool(self) and not self.IsBeingDeleted()

    def _list_name(self) -> str:
        if not self.loaded or self.load_error:
            return "Allowed for this chat"
        return f"Allowed for this chat, {len(self.rows)}"

    def refresh_mode(self):
        self.mode.ChangeValue(self.host.mode_text())
        # Change stays enabled, so the Tab order doesn't change while a turn
        # starts and ends; while the chat is working it says why it can't.

    def _show_note(self):
        parts = [self.host.note()]
        if self._native:
            parts.append(self._native)
        self.note.ChangeValue("\n\n".join(p for p in parts if p))
        self.note.SetInsertionPoint(0)

    def _native_ready(self, text: str):
        if self._alive():
            self._native = text
            self._show_note()

    def _name_list(self):
        self.list.SetName(self._list_name())
        mac_a11y.set_label(self.list, self._list_name())

    def _fill(self, rows, select: int = 0):
        self.rows = list(rows)
        self.loaded = True
        self.load_error = ""
        if self.rows:
            self.list.Set([r.text() for r in self.rows])
            self.list.SetSelection(max(0, min(select, len(self.rows) - 1)))
        else:
            self.list.Set([getattr(self.host, "empty_text", chat_permissions.EMPTY)])
            self.list.SetSelection(0)
        self._name_list()

    def reload(self, select: int = 0):
        self.busy = self.busy or "Still checking what this chat allows. Try again in a moment."

        def done(rows, error):
            if not self._alive():
                return
            if self.busy.startswith("Still checking"):
                self.busy = ""
            if error:
                # Unknown is not "nothing": the chat may still keep rules.
                self.loaded = True
                self.rows = []
                self.load_error = f"Couldn't read this chat's permissions. {error}"
                self.list.Set([self.load_error])
                self.list.SetSelection(0)
                self._name_list()
                return
            self._fill(rows, select)
        self.host.load(done)

    def _selected(self):
        index = self.list.GetSelection()
        return index if self.loaded and 0 <= index < len(self.rows) else None

    # -- keys -------------------------------------------------------------------

    def _on_list_key(self, event):
        # A Mac's Delete key is Backspace.
        if (self.can_remove and event.GetKeyCode() in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE, wx.WXK_BACK)
                and not event.HasAnyModifiers()):
            self.on_remove()
            return
        event.Skip()

    def _on_key(self, event):
        if press_focused_button_on_a_mac(event):
            return
        event.Skip()

    # -- actions ----------------------------------------------------------------

    def _tell(self, text: str, icon=wx.ICON_INFORMATION, back_to=None):
        wx.MessageBox(text, self.GetTitle(), wx.OK | icon, self)
        (back_to or self.list).SetFocus()

    def _still_busy(self, back_to=None) -> bool:
        if self.busy:
            self._tell(self.busy, back_to=back_to)
        return bool(self.busy)

    def _nothing_to_remove(self) -> bool:
        if self.load_error:
            self._tell(self.load_error + " Close and open Chat Permissions again to retry.")
            return True
        if not self.rows:
            self._tell("There's no rule to remove. " + chat_permissions.EMPTY)
            return True
        return False

    def _confirm(self, text: str) -> bool:
        return wx.MessageBox(text, self.GetTitle(),
                             wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) == wx.YES

    def on_remove(self):
        if self._still_busy() or self._nothing_to_remove():
            return
        index = self._selected()
        if index is None:
            self._tell("Select the rule to remove first.")
            return
        row = self.rows[index]
        if not self._confirm(chat_permissions.confirm_text(row, self.rows, **(
                {'after': self.host.removal_effect} if hasattr(self.host, 'removal_effect') else {}))):
            self.list.SetFocus()
            return
        self._remove(row.rule_id, index)

    def on_remove_all(self):
        if self._still_busy() or self._nothing_to_remove():
            return
        # One rule is named, as Remove names it.
        only = self.rows[0] if len(self.rows) == 1 else None
        if not self._confirm(chat_permissions.confirm_text(only, self.rows, **(
                {'after': self.host.removal_effect} if hasattr(self.host, 'removal_effect') else {}))):
            self.list.SetFocus()
            return
        self._remove(None, 0)

    def _remove(self, rule_id, index: int):
        self.busy = "Still finishing the last change. Try again in a moment."

        def done(rows, error):
            if not self._alive():
                return
            self.busy = ""
            if error:
                # Nothing is claimed: read the list again, as it now is.
                self.reload(index)
                self._tell(f"Couldn't remove it. {error}", wx.ICON_WARNING)
                return
            # The row that took its place, or the one before when it was last.
            self._fill(rows, index)
            self.list.SetFocus()
        self.host.remove(rule_id, done)

    def on_change(self):
        if self._still_busy(back_to=self.change_btn):
            return
        allowed, why = self.host.can_change_mode()
        if not allowed:
            self._tell(why, back_to=self.change_btn)
            return
        self.busy = "Still changing the permission mode. Try again in a moment."

        def done():
            if not self._alive():
                return
            self.busy = ""
            self.refresh_mode()
            # Read the host's own rules again: what they mean can depend on
            # the mode (a Codex chat that never asks refuses a rule that
            # says to ask, #307).
            native = getattr(self.host, "native", None)
            if callable(native):
                self._native = getattr(self.host, "native_pending", "")
                native(self._native_ready)
            self._show_note()
            self.reload(self.list.GetSelection())
            # Back to Change, unless you've moved on while it was checked.
            focus = wx.Window.FindFocus()
            if focus is None or focus is self.change_btn or focus.GetTopLevelParent() is not self:
                self.change_btn.SetFocus()
        self.host.change_mode(done)


OTHER = "Other"
# How far each arrow key moves in a group of radio buttons (#121).
RADIO_ARROW_STEPS = {wx.WXK_UP: -1, wx.WXK_LEFT: -1, wx.WXK_DOWN: 1, wx.WXK_RIGHT: 1}


class QuestionDialog(wx.Dialog):
    """Claude's questions (AskUserQuestion), one group per question: the
    options as radio buttons (check boxes when several may be chosen), each
    with its description, and Other with a text box. Send Answers is the
    default button.

    It opens on a read-only box, as a permission request does, with what
    the chat wrote just before asking (``said``, often what the question is
    about: a draft to approve) and each question in full, to read by line.
    Options that carry a preview show it in a second read-only box as each
    is reached (#415)."""

    def __init__(self, parent, session_title: str, request, provider="Claude", said=None):
        super().__init__(parent, title=f"{provider} asks: {session_title}", size=dialog_size(parent, 700, 600),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._questions = request.questions()
        self._answer_key = "id" if provider == "Codex" else "question"
        self._controls = []  # per question: (kind, [controls], other text)
        self._previews = {}  # option control -> its preview
        self.preview_text = None
        outer = wx.BoxSizer(wx.VERTICAL)
        said = str(getattr(request, "said", "") or "") if said is None else str(said or "")
        lines = [f"{question.get('header') or f'Question {number}'}: {question['question']}"
                 for number, question in enumerate(self._questions, start=1)]
        outer.Add(wx.StaticText(self, label="&Question:"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.question_text = _read_only_text(
            self, "\n\n".join(part for part in (said.strip(), "\n".join(lines)) if part),
            "Question", min_height=120)
        outer.Add(self.question_text, 0, wx.EXPAND | wx.ALL, 8)
        panel = self.questions_panel = wx.ScrolledWindow(self, style=wx.VSCROLL)
        panel.SetScrollRate(0, 20)
        sizer = wx.BoxSizer(wx.VERTICAL)
        first = None
        # wx logs that a box's controls "should be" its children: on purpose
        # they aren't (see below), so that note is kept out of the log.
        with wx.LogNull():
            for number, question in enumerate(self._questions, start=1):
                header = str(question.get("header") or f"Question {number}")
                # The question is the group's label. The options are the group
                # box's siblings, after it and inside its rectangle, as in a
                # classic Windows dialog: that is how JAWS and NVDA find a
                # group's label when Tab moves into it. As the box's children,
                # only the first question was read (the one focus starts in).
                box = wx.StaticBoxSizer(wx.VERTICAL, panel, f"{header}: {question['question']}")
                parent_window = panel
                options = [o for o in question.get("options") or [] if isinstance(o, dict)]
                multi = bool(question.get("multiSelect"))
                controls = []
                for index, option in enumerate(options):
                    label = str(option.get("label") or f"Option {index + 1}")
                    description = str(option.get("description") or "")
                    text = f"{label}: {description}" if description else label
                    if multi:
                        control = wx.CheckBox(parent_window, label=text)
                    else:
                        style = wx.RB_GROUP if index == 0 else 0
                        control = wx.RadioButton(parent_window, label=text, style=style)
                        control.SetValue(False)
                    control._hub_label = label
                    preview = option.get("preview")
                    if isinstance(preview, str) and preview.strip():
                        self._previews[control] = preview
                    control.Bind(wx.EVT_SET_FOCUS, self._on_option_focus)
                    # A choice made by the arrow keys too: on a Mac, focus
                    # moved by move_in_radio_group may not send SET_FOCUS.
                    control.Bind(wx.EVT_CHECKBOX if multi else wx.EVT_RADIOBUTTON,
                                 self._on_option_focus)
                    controls.append(control)
                    box.Add(control, 0, wx.ALL, 4)
                    first = first or control
                allow_other = provider != "Codex" or question.get("isOther") or not options
                if multi:
                    other = wx.CheckBox(parent_window, label=f"{OTHER} (type below)")
                else:
                    other = wx.RadioButton(parent_window, label=f"{OTHER} (type below)",
                                           style=0 if controls else wx.RB_GROUP)
                    other.SetValue(False)
                other._hub_label = OTHER
                other.Bind(wx.EVT_SET_FOCUS, self._on_option_focus)
                controls.append(other)
                box.Add(other, 0, wx.ALL, 4)
                other_text = wx.TextCtrl(parent_window, style=wx.TE_PASSWORD if question.get("isSecret") else 0)
                other_text._preserve_answer = provider == "Codex"
                set_accessible_name(other_text, f"{header}: your own answer")
                box.Add(other_text, 0, wx.EXPAND | wx.ALL, 4)
                other_text.Bind(wx.EVT_TEXT, lambda e, o=other: o.SetValue(True)
                                if e.GetString().strip() else None)
                if not allow_other:
                    controls.remove(other)
                    other.Hide()
                    other_text.Hide()
                self._controls.append(("multi" if multi else "single", controls, other_text))
                sizer.Add(box, 0, wx.EXPAND | wx.ALL, 6)
                first = first or other
        panel.SetSizer(sizer)
        outer.Add(panel, 1, wx.EXPAND | wx.ALL, 4)
        if self._previews:
            # After the questions in Tab order, and only when an option has
            # one: the preview of the option you're on (#415).
            outer.Add(wx.StaticText(self, label="Pre&view:"), 0, wx.LEFT | wx.RIGHT, 8)
            self.preview_text = _read_only_text(self, "", "Preview", min_height=120)
            outer.Add(self.preview_text, 0, wx.EXPAND | wx.ALL, 8)
            self._show_preview(first)

        row = wx.BoxSizer(wx.HORIZONTAL)
        send = wx.Button(self, wx.ID_OK, "&Send Answers")
        send.SetDefault()
        row.Add(send, 0, wx.RIGHT, 6)
        decline = wx.Button(self, ID_DENY, "Decline and Stop &Turn" if provider == "Codex" else "&Don't Answer")
        row.Add(decline, 0, wx.RIGHT, 6)
        later = wx.Button(self, wx.ID_CANCEL, "Answer &Later")
        row.Add(later, 0)
        self._buttons = [send, decline, later]
        outer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(outer)
        # As tall as the questions need, up to the size it was given; past
        # that the questions scroll (#176: two short questions left a band
        # of empty space above the buttons).
        # The questions' own height, up to the size it was given; past that
        # they scroll. The panel's minimum is the questions' height only
        # while fitting: left in place it would push the buttons out of a
        # dialog too short for it.
        panel.SetMinSize((-1, sizer.GetMinSize().height))
        _fit_height(self, max_height=self.GetSize().height)
        panel.SetMinSize((-1, -1))
        self.Layout()
        panel.FitInside()
        self.SetEscapeId(wx.ID_CANCEL)
        self.declined = False
        send.Bind(wx.EVT_BUTTON, self._on_send)
        decline.Bind(wx.EVT_BUTTON, self._on_decline)
        self._first = first
        # Set as the dialog starts, before it's shown: wx would otherwise
        # give the focus to the last group first, and a screen reader would
        # begin reading the wrong question. It starts on the question's own
        # text, as a permission request does (#415).
        self.Bind(wx.EVT_INIT_DIALOG, self._on_init_dialog)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

    def tab_stops(self) -> list:
        """The dialog's Tab order: the question's text, then each question
        as one stop (its chosen option, or its first while none is chosen),
        its own answer, the preview and the buttons. Kept here rather than
        left to the system: the options start with none chosen, so you pick
        one, and Windows' Tab goes only to a group's chosen option, so it
        skipped every question with none chosen. With focus starting on the
        question's text (#415), Tab went straight past the options to the
        answer box."""
        stops = [self.question_text]
        for kind, controls, other_text in self._controls:
            shown = [c for c in controls if c.IsShown()]
            if kind == "single":
                chosen = [c for c in shown if c.GetValue()]
                stops += (chosen or shown)[:1]
            else:
                stops += shown
            if other_text.IsShown():
                stops.append(other_text)
        if self.preview_text is not None:
            stops.append(self.preview_text)
        return stops + [b for b in self._buttons if b.IsShown() and b.IsEnabled()]

    def _tab_from(self, focus, forward: bool) -> bool:
        """Move to the next (or previous) stop after ``focus``. False when
        ``focus`` isn't one of the dialog's, so Tab goes on as usual."""
        stops = self.tab_stops()
        here = None
        if focus in stops:
            here = stops.index(focus)
        else:
            for kind, controls, _other_text in self._controls:
                if kind == "single" and focus in controls:
                    # An option of a group whose stop is another option.
                    group = [s for s in stops if s in controls]
                    here = stops.index(group[0]) if group else None
                    break
        if here is None:
            return False
        step = 1 if forward else -1
        for offset in range(1, len(stops)):
            stop = stops[(here + step * offset) % len(stops)]
            # A stop that won't take focus from the keyboard is passed over:
            # on a Mac with Full Keyboard Access off, radio buttons and push
            # buttons refuse it, and Tab would otherwise go nowhere.
            if not stop.CanAcceptFocusFromKeyboard():
                continue
            stop.SetFocusFromKbd()
            if wx.Window.FindFocus() is stop:
                return True
        return False  # nothing took it: Tab goes on as the system has it

    def _on_init_dialog(self, event):
        event.Skip()
        self.question_text.SetFocus()

    def _on_option_focus(self, event):
        event.Skip()
        self._show_preview(event.GetEventObject())

    def _show_preview(self, control):
        """The preview box shows the preview of ``control``'s option, and is
        named for it, so a screen reader says whose preview it is."""
        if self.preview_text is None or control is None:
            return
        label = getattr(control, "_hub_label", "")
        preview = self._previews.get(control)
        self.preview_text.SetValue(preview if preview is not None
                                   else f"{label} has no preview.")
        self.preview_text.SetInsertionPoint(0)
        set_accessible_name(self.preview_text, f"Preview of {label}")

    def _on_char_hook(self, event):
        focus = wx.Window.FindFocus()
        if (event.GetKeyCode() == wx.WXK_TAB and not event.ControlDown()
                and not event.AltDown() and self._tab_from(focus, not event.ShiftDown())):
            return
        # On a Mac the arrow keys did nothing in a group of radio buttons,
        # and Tab treats the group as one stop, so only the first option
        # could be reached (#121). Windows moves through the group itself.
        step = RADIO_ARROW_STEPS.get(event.GetKeyCode()) if wx.Platform == "__WXMAC__" else None
        if (step is None or event.HasAnyModifiers() or not isinstance(focus, wx.RadioButton)
                or not self.move_in_radio_group(focus, step)):
            event.Skip()

    def move_in_radio_group(self, radio, step: int) -> bool:
        """Choose and focus the option `step` places from `radio` in its
        question, wrapping at the ends as Windows does, and send the
        EVT_RADIOBUTTON a click would. False if `radio` isn't one of ours."""
        for kind, controls, _other_text in self._controls:
            if kind == "single" and radio in controls:
                target = controls[(controls.index(radio) + step) % len(controls)]
                target.SetValue(True)
                # Focus follows the choice, so VoiceOver reads the new option.
                target.SetFocus()
                event = wx.CommandEvent(wx.wxEVT_RADIOBUTTON, target.GetId())
                event.SetEventObject(target)
                event.SetInt(1)
                target.GetEventHandler().ProcessEvent(event)
                return True
        return False

    def _on_decline(self, _event):
        self.declined = True
        self.EndModal(wx.ID_OK)

    def _on_send(self, _event):
        for (_kind, _controls, other_text), question in zip(self._controls, self._questions):
            if not self._answer_for(_controls, other_text):
                wx.MessageBox(f"Choose an answer for: {question['question']}",
                              self.GetTitle(), wx.OK | wx.ICON_WARNING, self)
                (_controls[0] if _controls else other_text).SetFocus()
                return
        self.EndModal(wx.ID_OK)

    @staticmethod
    def _answer_for(controls, other_text) -> str:
        chosen = []
        for control in controls:
            if not control.GetValue():
                continue
            if control._hub_label == OTHER:
                typed = (other_text.GetValue() if getattr(other_text, "_preserve_answer", False)
                         else " ".join(other_text.GetValue().split()))
                if typed:
                    chosen.append(typed)
            else:
                chosen.append(control._hub_label)
        return ", ".join(chosen)

    def answers(self):
        """{question text: answer} as Claude Code expects it; several
        choices are joined with commas."""
        return {question[self._answer_key]: self._answer_for(controls, other_text)
                for (_kind, controls, other_text), question
                in zip(self._controls, self._questions)}


class PlanDialog(wx.Dialog):
    """Claude's plan, to read, then Approve (choosing the permission mode to
    carry on in) or Keep Planning with a note. Keep Planning is the default
    button: approving starts real changes, so it needs a deliberate press."""

    def __init__(self, parent, session_title: str, request, modes, default_mode: str):
        super().__init__(parent, title=f"Claude's plan: {session_title}", size=dialog_size(parent, 760, 600),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._modes = list(modes)  # (value, label)
        self.approved = False
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="&Plan:"), 0, wx.LEFT | wx.TOP, 8)
        self.plan_text = _read_only_text(self, request.plan() or "(Claude sent no plan text.)",
                                         "Plan", min_height=260)
        sizer.Add(self.plan_text, 1, wx.EXPAND | wx.ALL, 8)

        grid = wx.FlexGridSizer(cols=2, vgap=8, hgap=8)
        grid.AddGrowableCol(1, 1)
        grid.Add(wx.StaticText(self, label="If approved, carry on &in:"), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        self.mode = wx.Choice(self, choices=[label for _v, label in self._modes])
        set_accessible_name(self.mode, "If approved, carry on in")
        values = [v for v, _l in self._modes]
        self.mode.SetSelection(values.index(default_mode) if default_mode in values else 0)
        grid.Add(self.mode, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, label="&What to change (for Keep Planning):"), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        self.note = wx.TextCtrl(self)
        set_accessible_name(self.note, "What to change (for Keep Planning)")
        grid.Add(self.note, 1, wx.EXPAND)
        sizer.Add(grid, 0, wx.EXPAND | wx.ALL, 8)

        row = wx.BoxSizer(wx.HORIZONTAL)
        self.formatted_button = None
        if formatted_view_available():
            # The plan as a page, by heading and list (#190); back here after.
            self.formatted_button = wx.Button(self, label="Read &Formatted...")
            row.Add(self.formatted_button, 0, wx.RIGHT, 18)
            self.formatted_button.Bind(wx.EVT_BUTTON, lambda e: self._read_formatted(request))
        approve = wx.Button(self, ID_ALLOW, "&Approve")
        row.Add(approve, 0, wx.RIGHT, 6)
        keep = wx.Button(self, ID_DENY, "&Keep Planning")
        keep.SetDefault()
        row.Add(keep, 0, wx.RIGHT, 6)
        row.Add(wx.Button(self, wx.ID_CANCEL, "Answer &Later"), 0)
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        approve.Bind(wx.EVT_BUTTON, lambda e: self._finish(True))
        keep.Bind(wx.EVT_BUTTON, lambda e: self._finish(False))
        wx.CallAfter(self.plan_text.SetFocus)

    def _finish(self, approved: bool):
        self.approved = approved
        self.EndModal(wx.ID_OK)

    def _read_formatted(self, request):
        from ..rendering import message_page
        title = "Claude's plan"
        try:
            dialog = FormattedMessageDialog(self, title, message_page(title, request.plan()))
        except RuntimeError:
            self.plan_text.SetFocus()
            return
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()
        (self.formatted_button or self.plan_text).SetFocus()

    def chosen_mode(self) -> str:
        index = self.mode.GetSelection()
        return self._modes[index][0] if 0 <= index < len(self._modes) else self._modes[0][0]

    def note_text(self) -> str:
        return self.note.GetValue().strip()


class ManageGroupsDialog(wx.Dialog):
    """File, Manage Groups (#31): the groups, each with how many sessions
    it has, and New, Rename, Delete. Changes are saved as they're made;
    Close (or Escape) closes. Deleting a group asks first, and never touches
    its sessions."""

    def __init__(self, parent, groups, counts):
        super().__init__(parent, title="Manage Groups", size=dialog_size(parent, 480, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.groups = groups
        self._counts = dict(counts)
        #: old name -> new name, for every rename made here (chains followed).
        self.renamed = {}
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="&Groups:"), 0, wx.LEFT | wx.TOP, 8)
        self.list = wx.ListBox(self, style=wx.LB_SINGLE)
        set_accessible_name(self.list, "Groups")
        sizer.Add(self.list, 1, wx.EXPAND | wx.ALL, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.new_btn = wx.Button(self, label="&New...")
        self.rename_btn = wx.Button(self, label="&Rename...")
        self.delete_btn = wx.Button(self, label="&Delete...")
        for button in (self.new_btn, self.rename_btn, self.delete_btn):
            row.Add(button, 0, wx.RIGHT, 6)
        row.AddStretchSpacer()
        row.Add(wx.Button(self, wx.ID_CANCEL, "&Close"), 0)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        self.new_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_new())
        self.rename_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_rename())
        self.delete_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_delete())
        self._fill()
        wx.CallAfter(self.list.SetFocus)

    def _row(self, name: str) -> str:
        count = self._counts.get(name, 0)
        return f"{name}, {count} session{'s' if count != 1 else ''}"

    def _fill(self, select: str = ""):
        names = self.groups.names()
        if names:
            self.list.Set([self._row(n) for n in names])
            self.list.SetSelection(names.index(select) if select in names else 0)
        else:
            self.list.Set(["No groups yet. New makes one."])
            self.list.SetSelection(0)
        # Always enabled, so the Tab order doesn't change; they say if
        # there's nothing to act on.

    def _selected(self):
        names = self.groups.names()
        index = self.list.GetSelection()
        return names[index] if names and 0 <= index < len(names) else None

    def _ask(self, title: str, value: str = ""):
        dialog = wx.TextEntryDialog(self, "Group name:", title, value)
        try:
            return dialog.GetValue() if dialog.ShowModal() == wx.ID_OK else None
        finally:
            dialog.Destroy()

    def _try(self, action):
        try:
            return action()
        except (ValueError, OSError) as exc:
            wx.MessageBox(str(exc), self.GetTitle(), wx.OK | wx.ICON_WARNING, self)
            return None

    def on_new(self):
        name = self._ask("New Group")
        if name is not None:
            made = self._try(lambda: self.groups.create(name))
            if made:
                self._fill(made)
        self.list.SetFocus()

    def on_rename(self):
        old = self._selected()
        if old is None:
            wx.MessageBox("There's no group to rename.", self.GetTitle(),
                          wx.OK | wx.ICON_INFORMATION, self)
        else:
            new = self._ask("Rename Group", old)
            if new is not None:
                renamed = self._try(lambda: self.groups.rename(old, new))
                if renamed:
                    self._counts[renamed] = self._counts.pop(old, 0)
                    for first, latest in list(self.renamed.items()):
                        if latest == old:
                            self.renamed[first] = renamed
                    self.renamed.setdefault(old, renamed)
                    self._fill(renamed)
        self.list.SetFocus()

    def on_delete(self):
        name = self._selected()
        if name is None:
            wx.MessageBox("There's no group to delete.", self.GetTitle(),
                          wx.OK | wx.ICON_INFORMATION, self)
        elif wx.MessageBox(f"Delete the group {name}? Its sessions stay; only the group "
                           "goes.", self.GetTitle(),
                           wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) == wx.YES:
            self._try(lambda: self.groups.delete(name))
            self._fill()
        self.list.SetFocus()


class CommandPickerDialog(wx.Dialog):
    """File, Insert Command or Skill (#23): Claude Code's slash commands
    and your skills for this folder, yours first. Type to filter by name or
    description; Down moves into the list; Enter (or Insert) chooses."""

    def __init__(self, parent, commands):
        super().__init__(parent, title="Insert Command or Skill", size=dialog_size(parent, 720, 520),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._all = list(commands)
        self._shown = []
        self.chosen = None
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="&Search:"), 0, wx.LEFT | wx.TOP, 8)
        self.search = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        set_accessible_name(self.search, "Search commands and skills")
        sizer.Add(self.search, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.list_label = wx.StaticText(self, label="&Commands and skills:")
        sizer.Add(self.list_label, 0, wx.LEFT | wx.TOP, 8)
        self.list = wx.ListBox(self, style=wx.LB_SINGLE)
        set_accessible_name(self.list, "Commands and skills")
        sizer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        insert = wx.Button(self, wx.ID_OK, "&Insert")
        insert.SetDefault()
        row.Add(insert, 0, wx.RIGHT, 6)
        row.Add(wx.Button(self, wx.ID_CANCEL), 0)
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        self.search.Bind(wx.EVT_TEXT, lambda e: self._filter())
        self.search.Bind(wx.EVT_TEXT_ENTER, lambda e: self._choose())
        self.search.Bind(wx.EVT_KEY_DOWN, self._on_search_key)
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self._choose())
        insert.Bind(wx.EVT_BUTTON, lambda e: self._choose())
        self._filter()
        wx.CallAfter(self.search.SetFocus)

    @staticmethod
    def row(command) -> str:
        name = f"/{command['name']}"
        hint = str(command.get("argumentHint") or "").strip()
        text = " ".join(str(command.get("description") or "").split())
        if len(text) > 200:
            text = text[:199] + "…"
        owner = ", Claude Code" if command.get("builtin") else ""
        return f"{name}{' ' + hint if hint else ''}{owner}: {text}" if text else f"{name}{owner}"

    def _filter(self):
        words = self.search.GetValue().lower().split()
        self._shown = [c for c in self._all
                       if all(w in (c["name"] + " " + str(c.get("description") or "")).lower()
                              for w in words)]
        if self._shown:
            self.list.Set([self.row(c) for c in self._shown])
            self.list.SetSelection(0)
        else:
            self.list.Set(["Nothing matches."])
            self.list.SetSelection(0)
        count = len(self._shown)
        self.list_label.SetLabel(f"&Commands and skills ({count} of {len(self._all)}):")
        set_accessible_name(self.list, f"Commands and skills, {count} of {len(self._all)}")

    def _on_search_key(self, event):
        if event.GetKeyCode() in (wx.WXK_DOWN, wx.WXK_UP) and self._shown:
            # Down lands on the first entry (the top one is already chosen by
            # default, so Down from the box shouldn't need pressing twice).
            self.list.SetFocus()
            self.list.SetSelection(0)
            return
        event.Skip()

    def _choose(self):
        index = self.list.GetSelection()
        if not self._shown or not (0 <= index < len(self._shown)):
            wx.Bell()
            return
        self.chosen = self._shown[index]
        self.EndModal(wx.ID_OK)
class BugReportDialog(wx.Dialog):
    """Help, Report a Bug (#28), as QuickMail's: a summary, what happened,
    what you expected and the steps, then what the report will include, to
    read before it goes anywhere. Open on GitHub (the default) copies the
    whole report and opens GitHub's new-issue page with it filled in; Copy
    Report only copies it."""

    def __init__(self, parent, environment_lines):
        super().__init__(parent, title="Report a Bug", size=dialog_size(parent, 660, 640),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.action = ""
        outer = wx.BoxSizer(wx.VERTICAL)

        def field(label, name, multiline=False, height=-1):
            outer.Add(wx.StaticText(self, label=label), 0, wx.LEFT | wx.TOP, 8)
            style = (wx.TE_MULTILINE | wx.TE_RICH2) if multiline else 0
            control = wx.TextCtrl(self, style=style)
            set_accessible_name(control, name)
            if multiline:
                control.SetMinSize(dip(self, -1, height))
            outer.Add(control, 1 if multiline else 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
            return control

        self.summary = field("&Summary (the issue's title):", "Summary")
        self.summary.SetMaxLength(200)
        self.happened = field("What &happened:", "What happened", True, 70)
        self.expected = field("What you &expected (optional):", "What you expected (optional)",
                              True, 50)
        self.steps = field("S&teps to reproduce (optional):", "Steps to reproduce", True, 50)
        outer.Add(wx.StaticText(self, label="What the report &includes besides your words "
                                            "(no session titles, folders or messages):"),
                  0, wx.LEFT | wx.TOP, 8)
        self.included = _read_only_text(self, "\n".join(environment_lines),
                                        "What the report includes", min_height=90)
        outer.Add(self.included, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        outer.Add(WrappingText(self, (
            "Open on GitHub needs access to the app's repository on GitHub. Without it, "
            "use Copy Report and email the report to support@theideaplace.net.")),
            0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        row = wx.BoxSizer(wx.HORIZONTAL)
        self.open_btn = wx.Button(self, wx.ID_OK, "&Open on GitHub")
        self.open_btn.SetDefault()
        copy = wx.Button(self, label="&Copy Report")
        row.Add(self.open_btn, 0, wx.RIGHT, 6)
        row.Add(copy, 0, wx.RIGHT, 6)
        row.Add(wx.Button(self, wx.ID_CANCEL), 0)
        outer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(outer)
        self.SetEscapeId(wx.ID_CANCEL)
        self.open_btn.Bind(wx.EVT_BUTTON, lambda e: self._finish("open"))
        copy.Bind(wx.EVT_BUTTON, lambda e: self._finish("copy"))
        wx.CallAfter(self.summary.SetFocus)

    def _finish(self, action: str):
        for control, what in ((self.summary, "a summary"), (self.happened, "what happened")):
            if not control.GetValue().strip():
                wx.MessageBox(f"Please write {what} first.", self.GetTitle(),
                              wx.OK | wx.ICON_INFORMATION, self)
                control.SetFocus()
                return
        self.action = action
        self.EndModal(wx.ID_OK)

    def values(self):
        return (" ".join(self.summary.GetValue().split()), self.happened.GetValue(),
                self.expected.GetValue(), self.steps.GetValue())


class LinesDialog(wx.Dialog):
    """Lines of a list, to read by arrowing rather than only hear in the
    system voice. Copy (or Ctrl+C in the list) copies the selected line;
    Copy All copies every line. ``label`` names the list, with its access
    key."""

    def __init__(self, parent, title, label, lines, copy):
        super().__init__(parent, title=title, size=dialog_size(parent, 640, 360),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._lines = list(lines)
        # A list's rows don't wrap, so the dialog is as wide as its longest
        # line, within the screen (#176: the context line was cut off).
        longest = max((self.GetTextExtent(line).width for line in self._lines), default=0)
        # The parent's screen, as every dialog_size: an unshown dialog's own
        # screen isn't settled yet.
        screen = screen_area(parent).width
        # The room around a row: borders, the dialog's frame and a scroll
        # bar, which shows once the list is long enough to need one.
        room = dip_width(parent, 40) + wx.SystemSettings.GetMetric(wx.SYS_VSCROLL_X, self)
        width = min(max(dip_width(parent, 640), longest + room), screen - SCREEN_MARGIN)
        if width != self.GetSize().width:
            self.SetSize((width, self.GetSize().height))
        self._copy = copy
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label=f"{label}:"), 0, wx.LEFT | wx.TOP, 8)
        self.list = wx.ListBox(self, style=wx.LB_SINGLE, choices=self._lines)
        set_accessible_name(self.list, label.replace("&", ""))
        sizer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        # The same keys as Code Blocks: Alt+C copies, Alt+L closes.
        copy_btn = wx.Button(self, label="&Copy")
        row.Add(copy_btn, 0, wx.RIGHT, 6)
        copy_all = wx.Button(self, label="Copy &All")
        row.Add(copy_all, 0, wx.RIGHT, 6)
        close = wx.Button(self, wx.ID_CANCEL, "C&lose")
        close.SetDefault()
        row.Add(close, 0)
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        # Linux: GTK's client-side decorations and shadow take more than
        # ``room`` allows for the frame (52 pixels with Adwaita), which left
        # the longest line 13 short, so widen by what the list still lacks
        # (#230). Not elsewhere: a Windows list box's best width has three
        # characters' slack, so it would widen dialogs that already fit.
        if gtk_a11y.IS_LINUX:
            self.Layout()
            lacking = self.list.GetBestSize().width - self.list.GetSize().width
            if lacking > 0:
                self.SetSize((min(width + lacking, screen - SCREEN_MARGIN),
                              self.GetSize().height))
        self.SetEscapeId(wx.ID_CANCEL)
        # A char hook, as the main window's Ctrl+C on the messages list:
        # it's the path Cmd+C takes on macOS too.
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        copy_btn.Bind(wx.EVT_BUTTON, lambda e: self.copy_selected())
        copy_all.Bind(wx.EVT_BUTTON, lambda e: self.copy_all())
        if self._lines:
            self.list.SetSelection(0)
        wx.CallAfter(self.list.SetFocus)

    def _on_char_hook(self, event):
        if event.GetKeyCode() in (ord("C"), ord("c")) and event.ControlDown() \
                and not event.AltDown() and not event.ShiftDown() \
                and wx.Window.FindFocus() is self.list:
            self.copy_selected()
            return
        event.Skip()

    def copy_selected(self):
        index = self.list.GetSelection()
        if 0 <= index < len(self._lines):
            self._copy(self._lines[index], True)

    def copy_all(self):
        self._copy("\n".join(self._lines), False)


class UsageDialog(LinesDialog):
    """View, Usage and Context (Ctrl+Shift+U, #130): the loaded session's
    context and each usage limit as a line of a list."""

    def __init__(self, parent, lines, copy):
        super().__init__(parent, "Usage and Context", "&Usage and context", lines, copy)


class PropertiesDialog(LinesDialog):
    """View, Properties (Alt+Enter, #214): a session's or a message's
    properties, one "Property: value" line each. The title says whose."""

    def __init__(self, parent, title, lines, copy):
        super().__init__(parent, title, "&Properties", lines, copy)


class AboutYouDialog(wx.Dialog):
    """What AI Knows About You (#92, #305): a provider's local customization
    files. A list of kinds, the chosen kind's items, and the selected item's
    file to read by line. The Chat Place only reads them: Edit opens the file
    in your own editor, and Reload shows what changed."""

    def __init__(self, parent, kinds, load, edit, show, copy, provider_load=None):
        """``kinds`` is what was found for Claude. ``provider_load(provider,
        done)`` reads a provider's files in the background and calls
        ``done(kinds)`` on the UI thread, or ``done(None)`` if the read failed;
        ``done`` returns False when the result came too late to be shown.
        Without ``provider_load`` the Provider list is off and ``load(done)``
        reads Claude's files again."""
        super().__init__(parent, title="What AI Knows About You", size=dialog_size(parent, 860, 700),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._load, self._edit, self._show_file, self._copy = load, edit, show, copy
        self._kinds = list(kinds)
        self._provider_load = provider_load
        self._request = 0
        self._items = []
        # What the lists say while they're empty: reading, or a read that failed,
        # mustn't sound like nothing was found.
        self._placeholder = "Nothing here yet."
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label=(
            "Local customization files, not an account-wide memory or active-context report.\n"
            "Edit opens a file in your editor; Reload reads your changes.")),
            0, wx.LEFT | wx.TOP | wx.RIGHT, 8)
        sizer.Add(wx.StaticText(self, label="Pro&vider:"), 0, wx.LEFT | wx.TOP, 8)
        self.provider = wx.Choice(self, choices=["Claude", "Codex"])
        self.provider.SetSelection(0)
        set_accessible_name(self.provider, "Provider")
        sizer.Add(self.provider, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.provider.Bind(wx.EVT_CHOICE, self._change_provider)
        self.provider.Enable(provider_load is not None)
        sizer.Add(wx.StaticText(self, label="&Kind:"), 0, wx.LEFT | wx.TOP, 8)
        self.kinds = wx.ListBox(self, style=wx.LB_SINGLE)
        set_accessible_name(self.kinds, "Kind")
        self.kinds.SetMinSize(dip(self, -1, 120))
        sizer.Add(self.kinds, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        sizer.Add(wx.StaticText(self, label="&Items:"), 0, wx.LEFT | wx.TOP, 8)
        self.items = wx.ListBox(self, style=wx.LB_SINGLE)
        set_accessible_name(self.items, "Items")
        # 80, not 140: the list scrolls and still shows four or five rows,
        # and the Provider row (#305) had left Contents two lines tall at
        # 175% (#368).
        self.items.SetMinSize(dip(self, -1, 80))
        sizer.Add(self.items, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(self, label="Locatio&n:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.location = wx.TextCtrl(self, style=wx.TE_READONLY)
        set_accessible_name(self.location, "Location")
        row.Add(self.location, 1)
        sizer.Add(row, 0, wx.EXPAND | wx.LEFT | wx.TOP | wx.RIGHT, 8)
        sizer.Add(wx.StaticText(self, label="C&ontents:"), 0, wx.LEFT | wx.TOP, 8)
        self.text = _read_only_text(self, "", "Contents", min_height=200)
        sizer.Add(self.text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.edit_btn = wx.Button(self, label="&Edit in Your Editor")
        self.show_btn = wx.Button(self, label="Show in Fol&der")
        self.copy_btn = wx.Button(self, label="Copy &Path")
        self.reload_btn = reload_btn = wx.Button(self, label="&Reload")
        for button in (self.edit_btn, self.show_btn, self.copy_btn, reload_btn):
            buttons.Add(button, 0, wx.RIGHT, 6)
        buttons.Add(wx.Button(self, wx.ID_CANCEL, "C&lose"), 0)
        sizer.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        self.kinds.Bind(wx.EVT_LISTBOX, lambda e: self._fill_items())
        self.kinds.Bind(wx.EVT_KEY_DOWN, self._on_kinds_key)
        self.items.Bind(wx.EVT_LISTBOX, lambda e: self._show())
        self.items.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.text.SetFocus())
        self.items.Bind(wx.EVT_KEY_DOWN, self._on_items_key)
        self.edit_btn.Bind(wx.EVT_BUTTON, lambda e: self.edit_selected())
        self.show_btn.Bind(wx.EVT_BUTTON, lambda e: self.show_selected())
        self.copy_btn.Bind(wx.EVT_BUTTON, lambda e: self.copy_selected())
        reload_btn.Bind(wx.EVT_BUTTON, lambda e: self.reload())
        self._fill_kinds()
        wx.CallAfter(self.kinds.SetFocus)

    def reload(self):
        """Read everything again (in the background), keeping your place
        where it still exists."""
        self.reload_btn.Disable()
        if not self._kinds:  # after a failed read, don't still say it failed
            self._placeholder = "Reading…"
            self._fill_kinds()
        self._request += 1
        request = self._request
        def done(kinds):
            if self and request == self._request:
                self._reloaded(kinds)
                return True
            return False
        if self._provider_load is not None:
            self._provider_load(self.provider.GetStringSelection().lower(), done)
        else:
            self._load(done)

    def _change_provider(self, event):
        self._kinds = []
        self._placeholder = "Reading…"
        self._fill_kinds()
        self.reload()

    def _reloaded(self, kinds):
        if not self:
            return  # closed while reading
        self.reload_btn.Enable()
        if kinds is not None:
            self._kinds = list(kinds)
            self._placeholder = "Nothing here yet."
        elif not self._kinds:
            self._placeholder = "Couldn't read these files."
        self._fill_kinds()

    def _fill_kinds(self):
        kind_index = max(self.kinds.GetSelection(), 0)
        selected = self.selected_item()
        # Each kind says what it is, since focus never reaches a label.
        self.kinds.Set([f"{k.row()}: {k.about}" for k in self._kinds] or [self._placeholder])
        self.kinds.SetSelection(min(kind_index, max(len(self._kinds) - 1, 0)))
        self._fill_items(keep=selected.path if selected is not None else None)

    def _fill_items(self, keep=None):
        index = self.kinds.GetSelection()
        kind = self._kinds[index] if 0 <= index < len(self._kinds) else None
        self._items = list(kind.items) if kind is not None else []
        self.items.Set([i.row() for i in self._items]
                       or [self._placeholder if not self._kinds else "Nothing here yet."])
        name = kind.name if kind is not None else "Items"
        set_accessible_name(self.items, f"{name}, {len(self._items)}")
        paths = [i.path for i in self._items]
        self.items.SetSelection(paths.index(keep) if keep in paths else 0)
        self._show()

    def selected_item(self):
        index = self.items.GetSelection() if self._items else -1
        return self._items[index] if 0 <= index < len(self._items) else None

    def _show(self):
        from ..about_you import read_text

        item = self.selected_item()
        for button in (self.edit_btn, self.show_btn, self.copy_btn):
            button.Enable(item is not None)
        if item is None:
            self.location.ChangeValue("")
            self.text.ChangeValue("")
            set_accessible_name(self.text, "Contents")
            return
        try:
            text = read_text(item.path)
        except OSError as exc:
            text = f"Couldn't read this file: {exc}"
        self.location.ChangeValue(str(item.path))
        self.text.ChangeValue(text)
        self.text.SetInsertionPoint(0)
        # Tabbing in says whose file it is.
        set_accessible_name(self.text, f"Contents of {item.name}")

    def _on_kinds_key(self, event):
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.items.SetFocus()  # Enter: into the kind's items
            return
        event.Skip()

    def _on_items_key(self, event):
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.text.SetFocus()  # Enter: read the file
            return
        event.Skip()

    def edit_selected(self):
        item = self.selected_item()
        if item is None:
            return
        try:
            self._edit(item.path)
        except OSError as exc:
            wx.MessageBox(f"Couldn't open {item.path} in an editor: {exc}",
                          "What AI Knows About You", wx.OK | wx.ICON_WARNING, self)

    def show_selected(self):
        item = self.selected_item()
        if item is None:
            return
        try:
            self._show_file(item.path)
        except OSError as exc:
            wx.MessageBox(f"Couldn't show {item.path}: {exc}",
                          "What AI Knows About You", wx.OK | wx.ICON_WARNING, self)

    def copy_selected(self):
        item = self.selected_item()
        if item is not None:
            self._copy(str(item.path))


class SessionColumnsDialog(wx.Dialog):
    """View, Session List Columns (#134): which parts each session row reads,
    and in what order, so a row can start with its status. Modelled on
    QuickMail's message list fields: the columns not shown, the ones shown in
    the order they're read, Add, Remove, Move Up, Move Down, Move to Top,
    Move to Bottom and Reset to Default, and a preview of the selected
    session's row. OK keeps the choice; Cancel (or Escape) leaves it.

    Keys as well as the buttons, since a Mac has no Alt+letter access keys:
    Enter in Available adds, Delete (or Backspace) in Shown removes, and Alt+Up, Alt+Down,
    Alt+Home and Alt+End in Shown move (Option on a Mac), as QuickMail's
    Alt+Up and Alt+Down do. Every action is said (``say``), and focus stays
    on the column it moved, so arrowing on reads its new neighbours."""

    def __init__(self, parent, fields, sample, say):
        """``fields``: the shown column ids, in order. ``sample``: the
        session whose row the preview shows. ``say(text)`` speaks."""
        super().__init__(parent, title="Session List Columns", size=dialog_size(parent, 640, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._shown = clean_fields(list(fields))
        self._sample = sample
        self._say = say
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(WrappingText(self, (
            "Each session in the list is read as one line. Choose what it says, and in "
            "what order: put Status first to hear what each session is doing before "
            "its title.")), 0, wx.EXPAND | wx.LEFT | wx.TOP | wx.RIGHT, 8)
        lists = wx.BoxSizer(wx.HORIZONTAL)
        left = wx.BoxSizer(wx.VERTICAL)
        left.Add(wx.StaticText(self, label="A&vailable columns:"), 0, wx.BOTTOM, 4)
        self.available = wx.ListBox(self, style=wx.LB_SINGLE)
        set_accessible_name(self.available, "Available columns")
        left.Add(self.available, 1, wx.EXPAND)
        lists.Add(left, 1, wx.EXPAND | wx.ALL, 8)
        right = wx.BoxSizer(wx.VERTICAL)
        right.Add(wx.StaticText(self, label="&Shown columns, in the order they're read:"),
                  0, wx.BOTTOM, 4)
        self.shown = wx.ListBox(self, style=wx.LB_SINGLE)
        set_accessible_name(self.shown, "Shown columns, in the order they're read")
        right.Add(self.shown, 1, wx.EXPAND)
        lists.Add(right, 1, wx.EXPAND | wx.ALL, 8)
        # Both lists as wide as their longest column name, scroll bar
        # included: a list box has no sideways scroll bar, so a longer name
        # was cut off at its edge (#289: "Kind (desktop app, Cowork,
        # terminal or The Chat Place)").
        widest = max(self.shown.GetTextExtent(name).width for name in FIELD_NAMES.values())
        room = (widest + wx.SystemSettings.GetMetric(wx.SYS_VSCROLL_X, self)
                + dip_width(self, 16))
        for box in (self.available, self.shown):
            box.SetMinSize(wx.Size(max(dip_width(self, 220), room), dip(self, 220, 200).height))
        sizer.Add(lists, 1, wx.EXPAND)
        row = wx.WrapSizer(wx.HORIZONTAL)
        self.add_btn = wx.Button(self, label="&Add")
        self.remove_btn = wx.Button(self, label="&Remove")
        self.up_btn = wx.Button(self, label="Move &Up")
        self.down_btn = wx.Button(self, label="Move &Down")
        self.top_btn = wx.Button(self, label="Move to &Top")
        self.bottom_btn = wx.Button(self, label="Move to &Bottom")
        self.reset_btn = wx.Button(self, label="R&eset to Default")
        # Always enabled, so the Tab order never changes; each says when
        # there's nothing it can do.
        for button in (self.add_btn, self.remove_btn, self.up_btn, self.down_btn,
                       self.top_btn, self.bottom_btn, self.reset_btn):
            row.Add(button, 0, wx.RIGHT | wx.BOTTOM, 6)
        sizer.Add(row, 0, wx.LEFT | wx.RIGHT, 8)
        sizer.Add(wx.StaticText(self, label="&Preview of the selected session's line:"),
                  0, wx.LEFT | wx.TOP, 8)
        self.preview = _read_only_text(self, "", "Preview of the selected session's line",
                                       min_height=60)
        sizer.Add(self.preview, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        buttons = wx.StdDialogButtonSizer()
        ok = wx.Button(self, wx.ID_OK, "OK")
        ok.SetDefault()
        buttons.AddButton(ok)
        buttons.AddButton(wx.Button(self, wx.ID_CANCEL, "Cancel"))
        buttons.Realize()
        sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(sizer)
        # Wide enough for the lists above, though never wider than the screen.
        need = self.ClientToWindowSize(sizer.GetMinSize()).width
        width = min(max(self.GetSize().width, need), screen_area(parent).width - SCREEN_MARGIN)
        self.SetSize(wx.Size(width, self.GetSize().height))
        self.CentreOnParent()
        self.SetEscapeId(wx.ID_CANCEL)
        self.add_btn.Bind(wx.EVT_BUTTON, lambda e: self.add())
        self.remove_btn.Bind(wx.EVT_BUTTON, lambda e: self.remove())
        self.up_btn.Bind(wx.EVT_BUTTON, lambda e: self.move(-1))
        self.down_btn.Bind(wx.EVT_BUTTON, lambda e: self.move(1))
        self.top_btn.Bind(wx.EVT_BUTTON, lambda e: self.move_to_end(top=True))
        self.bottom_btn.Bind(wx.EVT_BUTTON, lambda e: self.move_to_end(top=False))
        self.reset_btn.Bind(wx.EVT_BUTTON, lambda e: self.reset())
        self.available.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.add())
        self.shown.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.remove())
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self._fill(shown_index=0, available_index=0)
        wx.CallAfter(self.shown.SetFocus)

    # -------------------------------------------------------------- state

    def fields(self):
        """The shown column ids, in the order they're read."""
        return list(self._shown)

    def _not_shown(self):
        return [f for f in FIELD_IDS if f not in self._shown]

    def _show_preview(self):
        line = self._sample.list_line(fields=self._shown) if self._sample is not None else ""
        self.preview.ChangeValue(line)
        self.preview.SetInsertionPoint(0)

    def _fill(self, shown_index=None, available_index=None):
        """Both lists again, each keeping its place (or going to the given
        row), and the preview."""
        if shown_index is None:
            shown_index = self.shown.GetSelection()
        if available_index is None:
            available_index = self.available.GetSelection()
        self.shown.Set([FIELD_NAMES[f] for f in self._shown])
        if self._shown:
            self.shown.SetSelection(min(max(shown_index, 0), len(self._shown) - 1))
        rest = self._not_shown()
        # An empty list reads as nothing at all to a screen reader.
        self.available.Set([FIELD_NAMES[f] for f in rest] or ["Every column is shown."])
        self.available.SetSelection(min(max(available_index, 0), max(len(rest), 1) - 1))
        self._show_preview()

    def _selected_shown(self):
        index = self.shown.GetSelection()
        return index if 0 <= index < len(self._shown) else None

    def _position(self, index):
        return f"{index + 1} of {len(self._shown)}"

    # ------------------------------------------------------------ actions

    def add(self):
        """Add the selected available column at the end of the line; stay in
        Available, on the next one, to add more (or go to Shown, on the
        column just added, when that was the last)."""
        rest = self._not_shown()
        index = self.available.GetSelection()
        if not rest:
            self._say("Every column is shown already.")
            return
        if not 0 <= index < len(rest):
            self._say("No column selected.")
            return
        field = rest[index]
        self._shown.append(field)
        self._fill(shown_index=len(self._shown) - 1, available_index=index)
        self._say(f"{field_short_name(field)} added, {self._position(len(self._shown) - 1)}.")
        (self.available if len(rest) > 1 else self.shown).SetFocus()

    def remove(self):
        index = self._selected_shown()
        if index is None:
            self._say("No column selected.")
            return
        self.shown.SetFocus()
        if len(self._shown) == 1:
            self._say("At least one column has to be shown.")
            return
        field = self._shown.pop(index)
        self._fill(shown_index=index)
        count = len(self._shown)
        self._say(f"{field_short_name(field)} removed. {count} column"
                  f"{'s' if count != 1 else ''} shown.")

    def move(self, step):
        """Move Up (``step`` -1) or Move Down (1)."""
        index = self._selected_shown()
        if index is None:
            self._say("No column selected.")
            return
        self._move_to(index, index + step, "up" if step < 0 else "down",
                      "top" if step < 0 else "bottom")

    def move_to_end(self, top):
        index = self._selected_shown()
        if index is None:
            self._say("No column selected.")
            return
        where = "top" if top else "bottom"
        self._move_to(index, 0 if top else len(self._shown) - 1, f"to {where}", where)

    def _move_to(self, index, target, how, end):
        name = field_short_name(self._shown[index])
        self.shown.SetFocus()
        if index == target or not 0 <= target < len(self._shown):
            self._say(f"{name} is already at the {end}.")
            return
        self._shown.insert(target, self._shown.pop(index))
        # Only the rows that changed, not the whole list: setting every row
        # again would reset the list under the screen reader, and Available
        # hasn't changed at all.
        low, high = min(index, target), max(index, target)
        for row in range(low, high + 1):
            self.shown.SetString(row, FIELD_NAMES[self._shown[row]])
        self.shown.SetSelection(target)
        self._show_preview()
        self._say(f"{name} moved {how}, {self._position(target)}.")

    def reset(self):
        self._shown = list(DEFAULT_FIELDS)
        self._fill(shown_index=0, available_index=0)
        self.shown.SetFocus()
        self._say(f"Columns reset to the default: {len(self._shown)} shown, title first.")

    def _on_char_hook(self, event):
        key = event.GetKeyCode()
        focus = wx.Window.FindFocus()
        plain = not (event.ControlDown() or event.ShiftDown() or event.AltDown())
        if focus is self.available and plain and key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.add()  # Enter in Available adds, rather than pressing OK
            return
        if focus is self.shown:
            # Backspace too: a Mac's Delete key sends it, and it means
            # nothing else in this list.
            if plain and key in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE, wx.WXK_BACK):
                self.remove()
                return
            if event.AltDown() and not (event.ControlDown() or event.ShiftDown()):
                moves = {wx.WXK_UP: lambda: self.move(-1), wx.WXK_DOWN: lambda: self.move(1),
                         wx.WXK_HOME: lambda: self.move_to_end(True),
                         wx.WXK_END: lambda: self.move_to_end(False)}
                if key in moves:
                    moves[key]()
                    return
        event.Skip()


class PromptEditDialog(wx.Dialog):
    """New Prompt and Edit Prompt (#131): a one-line name and the text,
    which can have line breaks (Enter starts a new line; Ctrl+Enter saves,
    as it sends in the reply box)."""

    def __init__(self, parent, title: str, name: str = "", text: str = "",
                 focus_name: Optional[bool] = None):
        super().__init__(parent, title=title, size=dialog_size(parent, 620, 460),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="&Name:"), 0, wx.LEFT | wx.TOP, 8)
        self.name = wx.TextCtrl(self, value=name)
        self.name.SetMaxLength(PROMPT_NAME_MAX)  # as Rename does: no silent cut
        set_accessible_name(self.name, "Prompt name")
        sizer.Add(self.name, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        sizer.Add(wx.StaticText(self, label="&Text:"), 0, wx.LEFT | wx.TOP, 8)
        self.text = wx.TextCtrl(self, value=text, style=wx.TE_MULTILINE | wx.TE_RICH2)
        set_accessible_name(self.text, "Prompt text")
        self.text.SetMinSize(dip(self, -1, 200))
        sizer.Add(self.text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        save = wx.Button(self, wx.ID_OK, "&Save")
        save.SetDefault()
        row.Add(save, 0, wx.RIGHT, 6)
        row.Add(wx.Button(self, wx.ID_CANCEL, "Cancel"), 0)
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        # A new prompt starts at its name; an existing one at its text,
        # which is more often what's being changed. Asked again after a
        # problem, it starts at the field that has it.
        if focus_name is None:
            focus_name = not name
        wx.CallAfter((self.name if focus_name else self.text).SetFocus)

    def _on_key(self, event):
        if (event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
                and event.ControlDown() and not event.AltDown()):
            self.EndModal(wx.ID_OK)
            return
        if press_focused_button_on_a_mac(event):
            return
        event.Skip()

    def values(self):
        return self.name.GetValue(), self.text.GetValue()


def press_focused_button_on_a_mac(event) -> bool:
    """A Mac presses a focused button only with Space, so Return did
    nothing on one; press it, as Windows does itself. True if pressed."""
    focus = wx.Window.FindFocus()
    if (wx.Platform == "__WXMAC__" and isinstance(focus, wx.Button)
            and event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
            and not event.HasAnyModifiers()):
        click = wx.CommandEvent(wx.wxEVT_BUTTON, focus.GetId())
        click.SetEventObject(focus)
        focus.GetEventHandler().ProcessEvent(click)
        return True
    return False


class RelayDialog(wx.Dialog):
    """File, Send Reply to Another Chat (#248): which chat to send a reply
    to, and the message, labelled with who wrote it, to read and change
    first. Enter in the list sends; in the message, Enter starts a new line
    and Ctrl+Enter sends, as in the reply box. ``check(index, text)`` says
    why the message can't go to that chat, or None; the dialog stays open
    with the reason, so nothing written is lost.

    With ``joint`` (#378) it also offers to start an agent conversation of
    the two chats instead of just sending: ``joint.problem(index)`` says why
    that can't be done with that chat (a ``relay.Problem``), or None, and
    ``joint.title`` starts its title. ``joint_chosen`` and ``joint_title``
    hold the choice."""

    JUST_SEND = "&Just send it"
    JOINT = "Start an agent &conversation with both chats"

    def __init__(self, parent, source_title: str, choices, text: str, check,
                 working: bool = False, title: str = "", joint=None):
        # Said first, with the title: the chat is mid-turn, so the reply
        # may be a step on the way rather than its answer. An agent
        # conversation's hand-off (#248) gives its own title.
        title = title or f"Send Reply to Another Chat: {source_title}"
        if working:
            title += " (still working; this is its latest reply so far)"
        super().__init__(parent, title=title,
                         size=dialog_size(parent, 700, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._check = check
        self._joint = joint
        self.chosen = None
        self.joint_chosen = False
        self.joint_title = ""
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="Send &to:"), 0, wx.LEFT | wx.TOP, 8)
        self.targets = wx.ListBox(self, choices=list(choices), style=wx.LB_SINGLE)
        set_accessible_name(self.targets, "Send to")
        self.targets.SetMinSize(dip(self, -1, 120))
        if choices:
            self.targets.SetSelection(0)
        sizer.Add(self.targets, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.just_send = self.joint_option = self.joint_title_box = None
        if joint is not None:
            # Two radio buttons whose labels say the whole choice, so each
            # reads on its own; when the joint one can't be done for the
            # chosen chat, its label says why, rather than it being skipped.
            self.just_send = wx.RadioButton(self, label=self.JUST_SEND, style=wx.RB_GROUP)
            self.joint_option = wx.RadioButton(self, label=self.JOINT)
            self.just_send.SetValue(True)
            sizer.Add(self.just_send, 0, wx.LEFT | wx.TOP, 8)
            sizer.Add(self.joint_option, 0, wx.LEFT | wx.TOP, 8)
            self.joint_title_label = wx.StaticText(self, label="Agent conversation t&itle:")
            sizer.Add(self.joint_title_label, 0, wx.LEFT | wx.TOP, 8)
            self.joint_title_box = wx.TextCtrl(self, value=joint.title or "")
            self.joint_title_box.SetMaxLength(200)
            set_accessible_name(self.joint_title_box, "Agent conversation title")
            sizer.Add(self.joint_title_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
            for radio in (self.just_send, self.joint_option):
                radio.Bind(wx.EVT_RADIOBUTTON, lambda e: self._sync_joint())
            self.targets.Bind(wx.EVT_LISTBOX, lambda e: self._sync_joint())
            self._sync_joint()
        sizer.Add(wx.StaticText(self, label="&Message:"), 0, wx.LEFT | wx.TOP, 8)
        self.text = wx.TextCtrl(self, value=text, style=wx.TE_MULTILINE | wx.TE_RICH2)
        set_accessible_name(self.text, "Message")
        self.text.SetMinSize(dip(self, -1, 200))
        sizer.Add(self.text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        send = wx.Button(self, wx.ID_OK, "&Send")
        send.SetDefault()
        row.Add(send, 0, wx.RIGHT, 6)
        row.Add(wx.Button(self, wx.ID_CANCEL, "Cancel"), 0)
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        send.Bind(wx.EVT_BUTTON, lambda e: self._send())
        self.targets.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self._send())
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        wx.CallAfter(self.targets.SetFocus)

    def _on_key(self, event):
        enter = event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
        if enter and event.ControlDown() and not event.AltDown():
            self._send()
            return
        if enter and not event.HasAnyModifiers() and wx.Window.FindFocus() is self.targets:
            self._send()
            return
        if press_focused_button_on_a_mac(event):
            return
        event.Skip()

    def _send(self):
        index = self.targets.GetSelection()
        if not (0 <= index < self.targets.GetCount()):
            wx.Bell()
            self.targets.SetFocus()
            return
        text = self.text.GetValue().strip()
        problem = "There's nothing to send: the message is empty." if not text \
            else self._check(index, text)
        if problem:
            wx.MessageBox(problem, self.GetTitle(), wx.OK | wx.ICON_INFORMATION, self)
            self.text.SetFocus()
            return
        joint = self.joint_option is not None and self.joint_option.GetValue()
        if joint:
            why = self._joint.problem(index)
            if why:
                wx.MessageBox(f"An agent conversation can't be started: {why.full}. Choose Just "
                              "send it, or another chat.", self.GetTitle(),
                              wx.OK | wx.ICON_INFORMATION, self)
                self.joint_option.SetFocus()
                return
            self.joint_title = " ".join(self.joint_title_box.GetValue().split())
        self.joint_chosen = joint
        self.chosen = (index, text)
        self.EndModal(wx.ID_OK)

    def _sync_joint(self):
        """The joint option's label says, briefly, why it can't be done with
        the chosen chat (Send says it in full); its title box is there only
        while it's chosen. The short reason never has a chat's title in it,
        so it fits on one line and brings no "&" of a title's."""
        index = self.targets.GetSelection()
        why = self._joint.problem(index) if index != wx.NOT_FOUND else None
        label = f"{self.JOINT} (unavailable: {why.short})" if why else self.JOINT
        if self.joint_option.GetLabel() != label:
            self.joint_option.SetLabel(label)
            set_accessible_name(self.joint_option, label.replace("&", ""))
        on = self.joint_option.GetValue()
        self.joint_title_label.Enable(on)
        self.joint_title_box.Enable(on)
        self.Layout()


class AgentConversationDialog(wx.Dialog):
    """File, New Agent Conversation (#248): what kind of conversation (a
    template fills in roles, permissions and who goes first, or your own);
    two chats, and an optional third, each an existing one of The Chat
    Place's own or a new Claude, Codex or Copilot chat, with a role each; how
    hand-offs go; who answers first; and the opening message. Ctrl+Enter
    starts it from anywhere, as Send does in the reply box. ``result`` holds
    what was chosen once Start is pressed."""

    NEW_LABELS = {"claude": "New Claude chat", "codex": "New Codex chat",
                  "copilot": "New Copilot chat"}
    #: A new Copilot chat has nothing to choose (#369): it always asks
    #: before it acts, and the runtime makes sure it still does (#364). The
    #: one choice says so where the others' permission would be.
    COPILOT_MODES = (("ask", "Asks you before it changes files or runs commands"),)
    NO_THIRD = "No third agent"
    SLOT_NAMES = ("First agent", "Second agent", "Third agent")
    #: Each agent's labels, with access keys no other control here uses.
    LABELS = (("First &agent's chat:", "First agent's &role (optional), such as Build the fix:",
               "First agent's new chat &permission:"),
              ("S&econd agent's chat:", "Second agent's r&ole (optional), such as Review each "
               "change:", "Second agent's new chat permissio&n:"),
              ("Thir&d agent's chat (optional):", "Third agent's role (optional), such as "
               "&Check the facts:", "Third a&gent's new chat permission:"))

    def __init__(self, parent, existing, new_kinds, folders=(), auto_allowed=False,
                 turn_limit=6):
        super().__init__(parent, title="New Agent Conversation",
                         size=dialog_size(parent, 720, 760),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        from ..agents import MAX_TURN_LIMIT, TEMPLATES
        from .permission_hosts import CODEX_REVIEW_MODES, CODEX_REVIEW_NOTE
        self.result = None
        self._templates = TEMPLATES
        self._existing = list(existing)
        self._new_kinds = list(new_kinds)
        self._auto_allowed = auto_allowed
        self._claude_modes = PERMISSION_MODES
        self._codex_modes = CODEX_REVIEW_MODES
        self._options = ([("new", kind) for kind in self._new_kinds]
                         + [("chat", info) for info in self._existing])
        labels = ([self.NEW_LABELS[kind] for kind in self._new_kinds]
                  + [f"{i.title}, {i.provider.capitalize()}, {i.repo}" for i in self._existing])
        body = wx.ScrolledWindow(self, style=wx.VSCROLL)
        body.SetScrollRate(0, 10)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(wx.StaticText(body, label="&Kind of conversation:"), 0, wx.LEFT | wx.TOP, 8)
        self.template = wx.Choice(body, choices=["Your own roles"]
                                  + [f"{t.name}: {t.description}" for t in TEMPLATES])
        self.template.SetSelection(0)
        set_accessible_name(self.template, "Kind of conversation")
        self.template.Bind(wx.EVT_CHOICE, lambda e: self._apply_template())
        outer.Add(self.template, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        outer.Add(wx.StaticText(body, label="&Title:"), 0, wx.LEFT | wx.TOP, 8)
        self.title_box = wx.TextCtrl(body)
        self.title_box.SetMaxLength(200)
        set_accessible_name(self.title_box, "Title")
        outer.Add(self.title_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        self.chats, self.roles, self.permissions, self._permission_labels = [], [], [], []
        self._role_labels = []
        for slot, name in enumerate(self.SLOT_NAMES):
            chat_label, role_label, permission_text = self.LABELS[slot]
            outer.Add(wx.StaticText(body, label=chat_label), 0, wx.LEFT | wx.TOP, 8)
            choices = ([self.NO_THIRD] if slot == 2 else []) + labels
            choice = wx.Choice(body, choices=choices)
            set_accessible_name(choice, f"{name}'s chat" + (" (optional)" if slot == 2 else ""))
            outer.Add(choice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
            role_text = wx.StaticText(body, label=role_label)
            outer.Add(role_text, 0, wx.LEFT | wx.TOP, 8)
            role = wx.TextCtrl(body)
            role.SetMaxLength(500)
            set_accessible_name(role, f"{name}'s role (optional)")
            outer.Add(role, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
            permission_label = wx.StaticText(body, label=permission_text)
            outer.Add(permission_label, 0, wx.LEFT | wx.TOP, 8)
            permission = wx.Choice(body)
            set_accessible_name(permission, f"{name}'s new chat permission")
            outer.Add(permission, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
            self.chats.append(choice)
            self.roles.append(role)
            self._role_labels.append(role_text)
            self.permissions.append(permission)
            self._permission_labels.append(permission_label)
            choice.Bind(wx.EVT_CHOICE, lambda e: self._sync())

        self.codex_note_label = wx.StaticText(body, label="About Codex permissions:")
        outer.Add(self.codex_note_label, 0, wx.LEFT | wx.TOP, 8)
        self.codex_note = _read_only_text(body, CODEX_REVIEW_NOTE, "About Codex permissions",
                                          min_height=110)
        outer.Add(self.codex_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        self.folder_label = wx.StaticText(body, label="&Folder for new chats:")
        outer.Add(self.folder_label, 0, wx.LEFT | wx.TOP, 8)
        folder_row = wx.BoxSizer(wx.HORIZONTAL)
        self.folder = wx.ComboBox(body, choices=list(folders), value=folders[0] if folders else "")
        set_accessible_name(self.folder, "Folder for new chats")
        folder_row.Add(self.folder, 1, wx.RIGHT, 6)
        self.browse = wx.Button(body, label="&Browse...")
        set_accessible_name(self.browse, "Browse for the folder for new chats")
        self.browse.Bind(wx.EVT_BUTTON, self._on_browse)
        folder_row.Add(self.browse, 0)
        outer.Add(folder_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        outer.Add(wx.StaticText(body, label="&Hand-offs:"), 0, wx.LEFT | wx.TOP, 8)
        modes = [f"Approve each hand-off: you pass each reply on (type, or {key_name('Ctrl+Shift+E')})"]
        if auto_allowed:
            modes.append("Automatic: replies go across by themselves, up to a limit")
        self.mode = wx.Choice(body, choices=modes)
        self.mode.SetSelection(0)
        set_accessible_name(self.mode, "Hand-offs")
        self.mode.Bind(wx.EVT_CHOICE, lambda e: self._sync())
        outer.Add(self.mode, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.limit_label = wx.StaticText(body, label="Turn &limit (exchanges before it stops):")
        outer.Add(self.limit_label, 0, wx.LEFT | wx.TOP, 8)
        self.limit = wx.SpinCtrl(body, min=1, max=MAX_TURN_LIMIT, initial=turn_limit)
        set_accessible_name(self.limit, "Turn limit, in exchanges")
        outer.Add(self.limit, 0, wx.LEFT | wx.RIGHT, 8)

        outer.Add(wx.StaticText(body, label="Who goes f&irst:"), 0, wx.LEFT | wx.TOP, 8)
        self.first = wx.Choice(body)
        set_accessible_name(self.first, "Who answers first")
        outer.Add(self.first, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        outer.Add(wx.StaticText(body, label=f"Opening &message ({key_name('Enter')} starts a new "
                                            f"line; {key_name('Ctrl+Enter')} starts the "
                                            "conversation):"),
                  0, wx.LEFT | wx.TOP, 8)
        self.message = wx.TextCtrl(body, style=wx.TE_MULTILINE | wx.TE_RICH2)
        self.message.SetMinSize(dip(self, -1, 120))
        set_accessible_name(self.message, "Opening message")
        outer.Add(self.message, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        body.SetSizer(outer)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(body, 1, wx.EXPAND)
        row = wx.BoxSizer(wx.HORIZONTAL)
        start = wx.Button(self, wx.ID_OK, "&Start")
        start.SetDefault()
        row.Add(start, 0, wx.RIGHT, 6)
        row.Add(wx.Button(self, wx.ID_CANCEL, "Cancel"), 0)
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        self._body = body
        start.Bind(wx.EVT_BUTTON, lambda e: self._start())
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        # A Claude chat and a Codex chat, new or the most recent of each;
        # no third agent unless you choose one.
        self._default(0, "claude")
        self._default(1, "codex" if self._pick(0)[1] != "codex" else "claude")
        self.chats[2].SetSelection(0)
        self._sync()
        self.first.SetSelection(0)
        body.FitInside()
        wx.CallAfter(self.template.SetFocus)

    def _slots(self):
        """The agents chosen: two, or three with a third."""
        return 3 if self.chats[2].GetSelection() > 0 else 2

    def _pick(self, slot):
        index = self.chats[slot].GetSelection() - (1 if slot == 2 else 0)
        if not (0 <= index < len(self._options)):
            return None, None
        kind, value = self._options[index]
        return (None, value) if kind == "new" else (value, value.provider)

    def _default(self, slot, provider):
        taken = self.chats[0].GetSelection() if slot == 1 else -1
        for wanted in (("new", provider), ("chat", provider), ("new", None), ("chat", None)):
            for index, (kind, value) in enumerate(self._options):
                if index == taken and kind == "chat":
                    continue
                value_provider = value if kind == "new" else value.provider
                if kind == wanted[0] and (wanted[1] is None or value_provider == wanted[1]):
                    self.chats[slot].SetSelection(index)
                    return
        if self._options:
            self.chats[slot].SetSelection(0)

    def _modes(self, provider):
        return (self._claude_modes if provider == "claude"
                else self.COPILOT_MODES if provider == "copilot" else self._codex_modes)

    def _sync(self):
        """Each new chat's permission choice, the third agent's role, the
        folder, the turn limit and who can go first follow what's chosen."""
        any_new = new_codex = False
        slots = self._slots()
        for slot in range(3):
            info, provider = self._pick(slot)
            used = slot < slots
            new = used and info is None and provider is not None
            any_new = any_new or new
            new_codex = new_codex or (new and provider == "codex")
            permission = self.permissions[slot]
            if new:
                labels = [label for _value, label in self._modes(provider)]
                if list(permission.GetStrings()) != labels:
                    # A new list: the template's choice for this agent, so
                    # a reviewer made a Claude chat after choosing the
                    # template still can't edit.
                    permission.Set(labels)
                    permission.SetSelection(self._template_permission(slot, provider))
            permission.Show(new)
            self._permission_labels[slot].Show(new)
            if slot == 2:
                self.roles[2].Show(used)
                self._role_labels[2].Show(used)
        self.codex_note_label.Show(new_codex)
        self.codex_note.Show(new_codex)
        for control in (self.folder_label, self.folder, self.browse):
            control.Show(any_new)
        auto = self._auto_allowed and self.mode.GetSelection() == 1
        self.limit_label.Show(auto)
        self.limit.Show(auto)
        firsts = list(self.SLOT_NAMES[:slots]) + [
            "Everyone (their replies aren't passed on)" if slots > 2
            else "Both (their replies aren't passed on)"]
        old = list(self.first.GetStrings())
        if old != firsts:
            chosen = self.first.GetSelection()
            self.first.Set(firsts)
            # "Both" becomes "Everyone" and back; an agent stays chosen
            # while it's still there.
            if old and chosen == len(old) - 1:
                chosen = slots
            elif not 0 <= chosen < slots:
                chosen = 0
            self.first.SetSelection(chosen)
        self._body.Layout()
        self._body.FitInside()

    def _template(self):
        index = self.template.GetSelection() - 1
        return self._templates[index] if 0 <= index < len(self._templates) else None

    def _template_permission(self, slot, provider) -> int:
        """Where a new chat's permission starts: the template's choice for
        that agent; a third agent with a template is read-only, being given
        no part in it; otherwise the first choice, as in New Session."""
        template = self._template()
        if template is None or provider == "copilot":
            return 0
        claude_mode, codex_sandbox = (template.permissions[slot]
                                      if slot < len(template.permissions)
                                      else ("plan", "read-only"))
        wanted = claude_mode if provider == "claude" else codex_sandbox
        for position, (value, _label) in enumerate(self._modes(provider)):
            if (value if provider == "claude" else value[0]) == wanted:
                return position
        return 0

    def _apply_template(self):
        """A template fills in each agent's role, a new chat's permission and
        who goes first; you can change any of them after."""
        template = self._template()
        if template is None:
            return
        for slot, role in enumerate(template.roles):
            self.roles[slot].SetValue(role)
        self._sync()
        for slot in range(3):
            info, provider = self._pick(slot)
            if info is None and provider is not None:
                self.permissions[slot].SetSelection(self._template_permission(slot, provider))
        self.first.SetSelection(template.first)

    def _on_browse(self, _event):
        with wx.DirDialog(self, "Choose the folder for the new chats", self.folder.GetValue(),
                          style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.folder.SetValue(dialog.GetPath())
        self.folder.SetFocus()

    def _on_key(self, event):
        if (event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
                and event.ControlDown() and not event.AltDown()):
            self._start()
            return
        if press_focused_button_on_a_mac(event):
            return
        event.Skip()

    def _problem(self):
        slots = self._slots()
        picks = [self._pick(slot) for slot in range(slots)]
        if any(provider is None for _info, provider in picks):
            return "Choose a chat for each agent.", self.chats[0]
        seen = set()
        for slot, (info, _provider) in enumerate(picks):
            if info is not None:
                if info.key in seen:
                    # The later of the two: the one to change.
                    return "Each agent needs a different chat.", self.chats[slot]
                seen.add(info.key)
        if any(info is None for info, _provider in picks):
            folder = self.folder.GetValue().strip()
            if not folder or not os.path.isdir(folder):
                return ("Choose a folder that exists for the new chats.", self.folder)
        return None, None

    def _start(self):
        problem, control = self._problem()
        if problem:
            wx.MessageBox(problem, self.GetTitle(), wx.OK | wx.ICON_INFORMATION, self)
            control.SetFocus()
            return
        members = []
        slots = self._slots()
        for slot in range(slots):
            info, provider = self._pick(slot)
            permission = ""
            approval_policy, approvals_reviewer = "on-request", "user"
            if info is None:
                index = max(self.permissions[slot].GetSelection(), 0)
                value = self._modes(provider)[index][0]
                if provider == "claude":
                    permission = value
                elif provider == "codex":
                    permission, approval_policy, approvals_reviewer = value
            members.append(SimpleNamespace(info=info, provider=provider,
                                           role=" ".join(self.roles[slot].GetValue().split()),
                                           permission=permission,
                                           approval_policy=approval_policy,
                                           approvals_reviewer=approvals_reviewer))
        first = self.first.GetSelection()
        to_all = first == slots
        self.result = SimpleNamespace(
            title=" ".join(self.title_box.GetValue().split()) or self._default_title(),
            members=members, folder=self.folder.GetValue().strip(),
            mode="auto" if self._auto_allowed and self.mode.GetSelection() == 1 else "approve",
            turn_limit=self.limit.GetValue(), message=self.message.GetValue().strip(),
            first=0 if to_all else max(first, 0), to_all=to_all)
        self.EndModal(wx.ID_OK)

    def _default_title(self):
        line = self.message.GetValue().strip().split("\n")[0]
        return line[:60] if line else "Agent conversation"


class PromptsDialog(wx.Dialog):
    """File, Prompts (#131): your saved prompts by name, the selected one's
    text to read, and Use, New, Edit, Delete, Move Up and Move Down. Use
    (or Enter on a prompt) closes the dialog and puts the prompt in the
    reply box. Changes are saved as they're made; Close or Escape closes.

    ``say`` speaks what each action did, as the main window's answers to
    what you do are spoken. ``use_refused`` is why Use can't put a prompt
    in a reply box right now ("" when it can): the dialog still opens, to
    look after your prompts, and Use says why."""

    def __init__(self, parent, store, say, use_refused: str = ""):
        super().__init__(parent, title="Prompts", size=dialog_size(parent, 680, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.store = store
        self._say = say
        self._use_refused = use_refused
        #: The prompt to put in the reply box, once Use is chosen.
        self.chosen = None
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="&Prompts:"), 0, wx.LEFT | wx.TOP, 8)
        self.list = wx.ListBox(self, style=wx.LB_SINGLE)
        set_accessible_name(self.list, "Prompts")
        self.list.SetMinSize(dip(self, -1, 150))
        sizer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        sizer.Add(wx.StaticText(self, label="Te&xt:"), 0, wx.LEFT | wx.TOP, 8)
        self.text = _read_only_text(self, "", "Prompt text", min_height=140)
        sizer.Add(self.text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.use_btn = wx.Button(self, wx.ID_OK, "&Use")
        self.use_btn.SetDefault()
        self.new_btn = wx.Button(self, label="&New...")
        self.edit_btn = wx.Button(self, label="&Edit...")
        self.delete_btn = wx.Button(self, label="&Delete...")
        self.up_btn = wx.Button(self, label="Mo&ve Up")
        self.down_btn = wx.Button(self, label="Move Do&wn")
        for button in (self.use_btn, self.new_btn, self.edit_btn, self.delete_btn,
                       self.up_btn, self.down_btn):
            row.Add(button, 0, wx.RIGHT, 6)
        row.AddStretchSpacer()
        row.Add(wx.Button(self, wx.ID_CANCEL, "&Close"), 0)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(sizer)
        _fit_width(self)
        self.SetEscapeId(wx.ID_CANCEL)
        self.use_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_use())
        self.new_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_new())
        self.edit_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_edit())
        self.delete_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_delete())
        self.up_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_move(-1))
        self.down_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_move(1))
        self.list.Bind(wx.EVT_LISTBOX, lambda e: self._show())
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.on_use())
        self.list.Bind(wx.EVT_KEY_DOWN, self._on_list_key)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        set_list_items_accessible(self.list, self._list_name, lambda index: None)
        self._fill(0)
        wx.CallAfter(self.list.SetFocus)

    # -- the list -------------------------------------------------------------

    def _list_name(self) -> str:
        return f"Prompts, {len(self.store)}"

    def _fill(self, select: int = 0):
        count = len(self.store)
        if count:
            self.list.Set(self.store.names())
            self.list.SetSelection(max(0, min(select, count - 1)))
        else:
            self.list.Set(["No prompts yet. New makes one."])
            self.list.SetSelection(0)
        # The count is in the name, so arriving in the list says how many:
        # for MSAA through the list's accessible (set up once, asked each
        # time), which SetName doesn't reach; for VoiceOver on the view.
        self.list.SetName(self._list_name())
        mac_a11y.set_label(self.list, self._list_name())
        gtk_a11y.set_label(self.list, self._list_name())  # Orca
        # Always enabled, so the Tab order doesn't change; they say if
        # there's nothing to act on.
        self._show()

    def _show(self):
        index = self._selected()
        self.text.ChangeValue("" if index is None else self.store.get(index).text)
        self.text.SetInsertionPoint(0)

    def _selected(self):
        index = self.list.GetSelection()
        return index if 0 <= index < len(self.store) else None

    def _none(self, doing: str) -> bool:
        """Say there's no prompt to act on; True when there isn't one."""
        if self._selected() is not None:
            return False
        wx.MessageBox(f"There's no prompt to {doing}. New makes one.", self.GetTitle(),
                      wx.OK | wx.ICON_INFORMATION, self)
        self.list.SetFocus()
        return True

    def _on_list_key(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not event.HasAnyModifiers():
            self.on_use()
            return
        if key == wx.WXK_DELETE and not event.HasAnyModifiers():
            self.on_delete()
            return
        event.Skip()

    def _on_key(self, event):
        if press_focused_button_on_a_mac(event):
            return
        event.Skip()

    # -- actions --------------------------------------------------------------

    def _edit(self, title: str, name: str, text: str, save):
        """Ask for a name and text until they can be saved, or Cancel.
        ``save(name, text)`` saves them and returns the prompt's index."""
        focus_name = None
        while True:
            dialog = PromptEditDialog(self, title, name, text, focus_name=focus_name)
            try:
                if dialog.ShowModal() != wx.ID_OK:
                    return None
                name, text = dialog.values()
            finally:
                dialog.Destroy()
            # Either way it's asked again with what was typed, never lost:
            # only Cancel throws it away.
            try:
                return save(name, text)
            except ValueError as exc:
                wx.MessageBox(str(exc), title, wx.OK | wx.ICON_WARNING, self)
                # A missing name, or one already taken, is the name's
                # problem; only "needs some text" is the text's.
                focus_name = "text" not in str(exc)
            except OSError as exc:
                wx.MessageBox(f"Couldn't save your prompts: {exc}\n\nWhat you typed is "
                              "still there, to try again or copy.", title,
                              wx.OK | wx.ICON_WARNING, self)
                focus_name = False

    def on_new(self):
        index = self._edit("New Prompt", "", "", self.store.add)
        if index is not None:
            self._fill(index)
            self._say(f"Prompt saved: {self.store.get(index).name}.")
        self.list.SetFocus()

    def on_edit(self):
        if self._none("edit"):
            return
        index = self._selected()
        prompt = self.store.get(index)
        saved = self._edit("Edit Prompt", prompt.name, prompt.text,
                           lambda name, text: self.store.update(index, name, text))
        if saved is not None:
            self._fill(saved)
            self._say(f"Prompt saved: {self.store.get(saved).name}.")
        self.list.SetFocus()

    def on_delete(self):
        if self._none("delete"):
            return
        index = self._selected()
        name = self.store.get(index).name
        if wx.MessageBox(f"Delete the prompt {name}?", self.GetTitle(),
                         wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) == wx.YES:
            try:
                self.store.delete(index)
            except (ValueError, OSError) as exc:
                wx.MessageBox(f"Couldn't delete the prompt: {exc}", self.GetTitle(),
                              wx.OK | wx.ICON_WARNING, self)
            else:
                # The next one takes its place (the one before, if it was last).
                self._fill(index)
                self._say(f"Prompt deleted: {name}.")
        self.list.SetFocus()

    def on_move(self, step: int):
        if self._none("move"):
            return
        index = self._selected()
        try:
            moved = self.store.move(index, step)
        except (ValueError, OSError) as exc:
            wx.MessageBox(f"Couldn't move the prompt: {exc}", self.GetTitle(),
                          wx.OK | wx.ICON_WARNING, self)
            self.list.SetFocus()
            return
        name = self.store.get(moved).name
        if moved == index:
            self._say(f"{name} is already {'first' if step < 0 else 'last'}.")
        else:
            self._fill(moved)
            self._say(f"Moved {'up' if step < 0 else 'down'}: {name}, "
                      f"{moved + 1} of {len(self.store)}.")
        self.list.SetFocus()

    def on_use(self):
        if self._none("use"):
            return
        if self._use_refused:
            wx.MessageBox(self._use_refused, self.GetTitle(),
                          wx.OK | wx.ICON_INFORMATION, self)
            self.list.SetFocus()
            return
        self.chosen = self.store.get(self._selected())
        self.EndModal(wx.ID_OK)


class ListsPage(wx.Panel):
    """One kind in View, Lists (#279): a filter, the list, the selected item
    to read in full, and what can be done with it. The items come from
    ``lists.build``."""

    def __init__(self, book, owner, category: str):
        super().__init__(book)
        self.owner = owner
        self.category = category
        self.title = lists.TITLES[category]
        self.items = []
        self.shown = []
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="&Filter:"), 0, wx.LEFT | wx.TOP, 8)
        self.filter = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        set_accessible_name(self.filter, f"Filter {self.title.lower()}")
        sizer.Add(self.filter, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        # The label carries the count: a list box's name, for JAWS and NVDA,
        # is the label before it, as in the other lists.
        self.list_label = wx.StaticText(self, label=f"&List: {self.title}:")
        sizer.Add(self.list_label, 0, wx.LEFT | wx.TOP, 8)
        self.list = wx.ListBox(self, style=wx.LB_SINGLE)
        set_accessible_name(self.list, self.title)  # with its count once filled
        self.list.SetMinSize(dip(self, -1, 140))
        sizer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        sizer.Add(wx.StaticText(self, label="&Details:"), 0, wx.LEFT | wx.TOP, 8)
        self.detail = _read_only_text(self, "", "Details", min_height=170)
        if category in (lists.CODE, lists.FILES, lists.COMMANDS):
            self.detail.SetFont(wx.Font(10, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL,
                                        wx.FONTWEIGHT_NORMAL))
        sizer.Add(self.detail, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.open_btn = self.markdown_btn = None
        if category == lists.LINKS:
            self.open_btn = wx.Button(self, label="&Open")
            self.open_btn.Bind(wx.EVT_BUTTON, lambda e: self.open_selected())
        self.copy_btn = wx.Button(self, label="&Copy")
        self.copy_btn.Bind(wx.EVT_BUTTON, lambda e: self.copy_selected())
        if category == lists.LINKS:
            self.markdown_btn = wx.Button(self, label="Copy as &Markdown")
            self.markdown_btn.Bind(wx.EVT_BUTTON, lambda e: self.copy_selected(markdown=True))
        # The gap goes between buttons, not after the last, so the row ends
        # where the dialog's Close does (#288).
        for index, button in enumerate(b for b in (self.open_btn, self.copy_btn, self.markdown_btn)
                                       if b is not None):
            row.Add(button, 0, wx.LEFT if index else 0, 6)
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.TOP | wx.LEFT | wx.RIGHT, 8)
        self.SetSizer(sizer)
        self.filter.Bind(wx.EVT_TEXT, lambda e: self.apply_filter())
        self.filter.Bind(wx.EVT_TEXT_ENTER, lambda e: self.activate())
        self.filter.Bind(wx.EVT_KEY_DOWN, self._on_filter_key)
        self.list.Bind(wx.EVT_LISTBOX, lambda e: self.show_selected())
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.activate())
        self.list.Bind(wx.EVT_KEY_DOWN, self._on_list_key)
        self.list.Bind(wx.EVT_CHAR, self._on_list_char)

    def fill(self, items):
        """New items (when the dialog opens, or for a new scope)."""
        self.items = list(items)
        self.apply_filter()

    def apply_filter(self):
        typed = self.filter.GetValue()
        self.shown = [item for item in self.items if item.matches(typed)]
        if self.shown:
            self.list.Set([item.row for item in self.shown])
        elif self.items:
            self.list.Set([f"No {lists.NOUNS[self.category]} match."])
        else:
            self.list.Set([self.owner.unavailable.get(self.category)
                           or lists.empty_text(self.category, self.owner.scope)])
        self.list.SetSelection(0)
        label = lists.count_text(self.category, len(self.shown), len(self.items))
        self.list_label.SetLabel(f"&List: {label}:")
        # VoiceOver's name for the list; JAWS and NVDA read the label.
        set_accessible_name(self.list, label)
        self.show_selected()

    def selected(self):
        index = self.list.GetSelection()
        return self.shown[index] if 0 <= index < len(self.shown) else None

    def show_selected(self):
        item = self.selected()
        self.detail.ChangeValue(item.detail if item is not None else "")
        self.detail.SetInsertionPoint(0)
        set_accessible_name(self.detail, item.detail_name or "Details" if item is not None
                            else "Details")
        for button in (self.copy_btn, self.open_btn, self.markdown_btn):
            if button is not None:
                button.Enable(item is not None)

    def activate(self):
        """Enter: a link opens; anything else is read in full."""
        item = self.selected()
        if item is None:
            return
        if self.category == lists.LINKS:
            self.open_selected()
        else:
            self.detail.SetFocus()

    def open_selected(self):
        item = self.selected()
        if item is None or item.link is None:
            self.owner.feedback(f"No {lists.NOUNS[self.category]} match." if self.items
                                else lists.empty_text(self.category, self.owner.scope))
            return
        if not item.link.can_open:
            self.owner.feedback(item.link.why_not())
            return
        self.owner.chosen_link = item.link
        _end_modal(self.owner, wx.ID_OK)

    def copy_selected(self, markdown: bool = False):
        item = self.selected()
        if item is None:
            self.owner.feedback(f"No {lists.NOUNS[self.category]} match." if self.items
                                else lists.empty_text(self.category, self.owner.scope))
            return
        text = item.link.markdown() if markdown and item.link is not None else item.copy_text
        if not self.owner.copy(text):
            self.owner.feedback("Couldn't open the clipboard.")
            return
        what = f"{item.copy_what} as Markdown" if markdown else item.copy_what
        self.owner.feedback(f"Copied {what}.")

    def _on_filter_key(self, event):
        if event.GetKeyCode() in (wx.WXK_DOWN, wx.WXK_UP) and self.shown:
            self.list.SetFocus()
            return
        event.Skip()

    def _on_list_key(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.activate()
            return
        if key == ord("C") and event.ControlDown() and not event.AltDown():
            # Ctrl+Shift+C is Copy as Markdown, which only links have.
            self.copy_selected(markdown=event.ShiftDown() and self.category == lists.LINKS)
            return
        event.Skip()

    def _on_list_char(self, event):
        """Typing in the list narrows it: the letter goes to the filter box,
        and focus with it, as in the other lists."""
        key = event.GetUnicodeKey()
        if (key != wx.WXK_NONE and key >= 32 and chr(key).isprintable()
                and not event.ControlDown() and not event.AltDown()):
            self.filter.SetFocus()
            self.filter.AppendText(chr(key))
            return
        event.Skip()


class ListsDialog(wx.Dialog):
    """View, Lists (Ctrl+Shift+I, #279): everything of one kind in a chat:
    Code Blocks, Links, Changed Files, Commands and Decisions.

    The kinds are a list of their own at the top, each with its count, so
    Tab and Shift+Tab always reach it on Windows and on a Mac (a native tab
    strip isn't a Tab stop on a Mac, #280). Arrowing it shows that kind
    below; Enter goes to its list. From anywhere in the dialog, the next or
    previous kind is Ctrl+Tab / Ctrl+Shift+Tab or Ctrl+Page Down / Page Up
    on Windows, and Cmd+Shift+] / Cmd+Shift+[ or Cmd+Page Down / Page Up on a
    Mac; on Windows, Ctrl+1 to Ctrl+6 go straight to one. Each leaves you on
    that kind's list.

    "Show items from" chooses this message or the whole chat.
    ``build(category, scope)`` gives a kind's ``lists.ListItem`` list. A link
    chosen with Open is in ``chosen_link`` once the dialog has closed.
    """

    def __init__(self, parent, title: str, build, copy, feedback,
                 category: str = lists.CODE, scope: str = lists.SCOPE_CHAT,
                 unavailable=None):
        super().__init__(parent, title=f"Lists: {title}", size=dialog_size(parent, 820, 660),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._build = build
        #: Kinds this chat can't fill yet, and the line each says instead of
        #: an empty list that would look like "none" (a Codex chat's commands).
        self.unavailable = dict(unavailable or {})
        self.copy = copy  # (text) -> bool
        self.feedback = feedback
        self.chosen_link = None
        self._built = {}  # (kind, scope) -> its items
        #: Which platform's keys switch kinds (tests choose either).
        self.platform = sys.platform
        self.scope = scope if scope in dict(lists.SCOPES) else lists.SCOPE_CHAT
        sizer = wx.BoxSizer(wx.VERTICAL)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(self, label="&Show items from:"), 0,
                wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.scope_choice = wx.Choice(self, choices=[label for _id, label in lists.SCOPES])
        set_accessible_name(self.scope_choice, "Show items from")
        self.scope_choice.SetSelection([s for s, _l in lists.SCOPES].index(self.scope))
        row.Add(self.scope_choice, 0)
        sizer.Add(row, 0, wx.ALL, 8)
        sizer.Add(wx.StaticText(self, label="&Kinds:"), 0, wx.LEFT, 8)
        self.kinds = wx.ListBox(self, style=wx.LB_SINGLE,
                                choices=[lists.TITLES[c] for c in lists.CATEGORY_IDS])
        set_accessible_name(self.kinds, "Kinds")
        # Every kind in view without scrolling: at 175% the text grows more
        # than 96 scaled pixels allow, and the sixth kind (Tasks, #231) was
        # cut in half. A row is the font's height; 96 stays the least.
        rows = len(lists.CATEGORY_IDS) * self.kinds.GetCharHeight() + dip(self, -1, 6).height
        self.kinds.SetMinSize((-1, max(dip(self, -1, 96).height, rows)))
        sizer.Add(self.kinds, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        # The kinds' pages, one shown at a time; the Kinds list chooses.
        self.book = wx.Simplebook(self)
        self.pages = []
        for tab in lists.CATEGORY_IDS:
            page = ListsPage(self.book, self, tab)
            self.book.AddPage(page, lists.TITLES[tab])
            self.pages.append(page)
        # No border of its own: each page's controls keep 8 pixels from its
        # edges, so they line up with Kinds above and Close below (#288).
        sizer.Add(self.book, 1, wx.EXPAND)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        # Alt+S is Show items from and Alt+K Kinds; every page has Filter,
        # List, Details and Copy, and Links Open and Markdown, so E it is.
        buttons.Add(wx.Button(self, wx.ID_CANCEL, "Clos&e"), 0)
        sizer.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        self.scope_choice.Bind(wx.EVT_CHOICE, lambda e: self.set_scope(
            lists.SCOPES[self.scope_choice.GetSelection()][0]))
        self.kinds.Bind(wx.EVT_LISTBOX, lambda e: self._show_kind(self.kinds.GetSelection()))
        self.kinds.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.current().list.SetFocus())
        self.kinds.Bind(wx.EVT_KEY_DOWN, self._on_kinds_key)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        # Only the controls take focus, never the panels around them (with
        # real keys on a Mac, Control+Tab landed on the book itself, #280).
        self.book.SetCanFocus(False)
        for page in self.pages:
            page.SetCanFocus(False)
        self._index = lists.CATEGORY_IDS.index(category) if category in lists.CATEGORY_IDS else 0
        self._fill_all()
        self._show_kind(self._index)
        wx.CallAfter(lambda: self and self.current().list.SetFocus())

    @property
    def category(self) -> str:
        return self.current().category

    def current(self) -> ListsPage:
        return self.pages[self._index]

    def _fill_all(self):
        """Every kind, for the current scope, so the Kinds list can say how
        many each has. Each kind is built once per scope."""
        for page in self.pages:
            key = (page.category, self.scope)
            if key not in self._built:
                self._built[key] = self._build(page.category, self.scope)
            page.fill(self._built[key])
        self._label_kinds()

    def _label_kinds(self):
        for index, page in enumerate(self.pages):
            if page.category in self.unavailable and not page.items:
                text = f"{page.title}, not available"
            else:
                text = f"{page.title} ({len(page.items)})"
            if self.kinds.GetString(index) != text:
                self.kinds.SetString(index, text)

    def set_scope(self, scope: str):
        """Every kind now shows ``scope``."""
        self.scope = scope
        self._fill_all()

    def _show_kind(self, index: int):
        if not 0 <= index < len(self.pages):
            return
        self._index = index
        # Showing another page moves focus into it, so arrowing the Kinds
        # list would leave it (found with real keys on a Mac, #280). Keep
        # the focus where it was unless that control is now hidden.
        focus = wx.Window.FindFocus()
        if self.kinds.GetSelection() != index:
            self.kinds.SetSelection(index)
        if self.book.GetSelection() != index:
            self.book.ChangeSelection(index)
        self.Layout()
        if focus is self.kinds and wx.Window.FindFocus() is not self.kinds:
            self.kinds.SetFocus()

    def select_category(self, index: int, focus_list: bool = True):
        """Show the kind at ``index`` (wrapping around), and go to its list."""
        self._show_kind(index % len(self.pages))
        if focus_list:
            self.current().list.SetFocus()

    def _on_kinds_key(self, event):
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not event.HasAnyModifiers():
            self.current().list.SetFocus()  # Enter on a kind: into its list
            return
        event.Skip()

    def _on_char_hook(self, event):
        step = self._switch_step(event, self.platform)
        if step is not None:
            kind, value = step
            self.select_category(value if kind == "to" else self._index + value)
            return
        event.Skip()

    @staticmethod
    def _switch_step(event, platform=None):
        """("by", ±1) for the next or previous kind, ("to", n) for kind n,
        or None. wx's ControlDown is Command on a Mac and Ctrl elsewhere.

        Ctrl+Tab is Windows' tab key. On a Mac, Control+Tab is the system's
        own "next group of controls" key, and a list takes it before the
        dialog sees it, so it's left to macOS everywhere rather than working
        only sometimes (tested with real keys, #280); Cmd+Shift+] and [ are
        the Mac's tab keys."""
        key = event.GetKeyCode()
        command = event.ControlDown() and not event.AltDown()
        if ((platform or sys.platform) != "darwin" and command and key == wx.WXK_TAB):
            return ("by", -1 if event.ShiftDown() else 1)
        if command and not event.ShiftDown() and key in (wx.WXK_PAGEDOWN, wx.WXK_PAGEUP):
            return ("by", 1 if key == wx.WXK_PAGEDOWN else -1)
        if command and event.ShiftDown() and key in (ord("]"), ord("}")):
            return ("by", 1)
        if command and event.ShiftDown() and key in (ord("["), ord("{")):
            return ("by", -1)
        # Ctrl+1 to Ctrl+6 jump to a kind on Windows. On a Mac, Cmd+1 to
        # Cmd+3 are the main window's Go To keys, and the menu bar takes
        # them before the dialog (real keys, #282), so none are used there.
        if ((platform or sys.platform) != "darwin" and command and not event.ShiftDown()
                and ord("1") <= key < ord("1") + len(lists.CATEGORY_IDS)):
            return ("to", key - ord("1"))
        return None


class FindResultsDialog(wx.Dialog):
    """Find in All Sessions' results (#109): what was searched, then each
    message found ("Build, Claude, yesterday 10:42: …the migration ran…").
    Enter or Go to Message loads that session on that message."""

    def __init__(self, parent, results, now=None, app_name="The Chat Place"):
        super().__init__(parent, title=results.title(app_name),
                         size=dialog_size(parent, 820, 520), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._matches = list(results.matches)
        #: The match to go to once the dialog has closed, or None.
        self.chosen = None
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="&Results:"), 0, wx.LEFT | wx.TOP, 8)
        self.list = wx.ListBox(self, style=wx.LB_SINGLE,
                               choices=[m.row(now) for m in self._matches])
        # The summary is the list's name, so it's heard on arriving.
        set_accessible_name(self.list, f"Results. {results.summary()}")
        sizer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        sizer.Add(wx.StaticText(self, label="&Searched:"), 0, wx.LEFT | wx.TOP, 8)
        self.summary = _read_only_text(self, results.summary(), "Searched", min_height=40)
        sizer.Add(self.summary, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.go_btn = wx.Button(self, label="&Go to Message")
        self.go_btn.SetDefault()
        row.Add(self.go_btn, 0, wx.RIGHT, 6)
        row.Add(wx.Button(self, wx.ID_CANCEL, "C&lose"), 0)
        sizer.Add(row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        self.go_btn.Bind(wx.EVT_BUTTON, lambda e: self.go())
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.go())
        self.list.Bind(wx.EVT_KEY_DOWN, self._on_list_key)
        if self._matches:
            self.list.SetSelection(0)
        wx.CallAfter(lambda: self and self.list.SetFocus())

    def _on_list_key(self, event):
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.go()
            return
        event.Skip()

    def go(self):
        index = self.list.GetSelection()
        if 0 <= index < len(self._matches):
            self.chosen = self._matches[index]
            _end_modal(self, wx.ID_OK)
