"""Tools, API Keys (#658): add, test, replace or remove the Anthropic API key
that chats on API credits are charged to, and set the limits on what The
Chat Place may spend on them.

The key goes straight from its masked box into the system's key store
(billing.save_api_key) and the box is emptied; the dialog never reads it
back. What it shows about the key (its last four characters and the day it
was added) comes from billing.api_key_status, which doesn't read the key
either, so opening the dialog can't raise a keychain prompt on a Mac.

Test Key runs one tiny request as an API turn (claude_cli.TurnRunner with
billing "api" and purpose "test"), so it goes through the same checks as a
chat and its cost is in the total. The limits and the total are the spend
ledger's (spend_ledger); a limit typed and not saved is asked about on Close.
Release Held lets go of what's held for messages no longer running (an
earlier run's or a crash's), after asking, without touching what was spent.

The key status and the total are read-only text boxes in the Tab order (the
status before the key box, the total right after the limits), refreshed
after every action, so they can be reached and read again by line.
"""
from __future__ import annotations

import math
import os
import threading
from typing import Callable, Optional

import wx

from .. import billing, claude_cli, platform_paths, spend_ledger, openai_billing
from .a11y import set_accessible_name
from .dialogs import _fit_width, _read_only_text, press_focused_button_on_a_mac
from .sizes import dialog_size, dip

TITLE = "API Keys"

ADD_LABEL = "&Add Key"
REPLACE_LABEL = "&Replace Key"

TEST_INTRO = ("Test Key sends one short request on your key to check that it works, and "
              "counts what it cost in the total.")
ABOUT_SPENDING = ("The Chat Place counts only its own spending. Your Anthropic account may "
                  "have less left if other apps use the same key, and a limit can be passed by "
                  "one request.")
REMOVE_QUESTION = ("Remove your Claude API key from The Chat Place? Chats on API credits won't "
                   "be able to send until you add one again.")
RESET_QUESTION = ("Set the API spending total back to zero? Spending since the last reset will no "
                  "longer count against the limit.")
SAVE_LIMITS_QUESTION = "Save the new spending limits?"
RELEASE_QUESTION = ("Release {amount} held for messages that are no longer running? These "
                    "messages may have spent up to {amount} that won't be counted.")
#: Release Held is on only for more than this held by no running message
#: (a float's remainder isn't something to release).
RELEASABLE_MIN = 0.000001
NOT_A_KEY = (f"That isn't a Claude API key. A key starts with {billing.KEY_PREFIX}, followed by "
             "a long run of letters and numbers. Copy it again from the Anthropic Console and "
             "paste it here.")
NO_KEY_TYPED = "Paste your Claude API key in the Claude API key box first."
CANT_PROTECT = ("This computer can't keep a key protected, so The Chat Place didn't save it. "
                "On Linux, install secret-tool (the libsecret-tools package), then try again.")
NO_RESULT_WORDS = "The test didn't finish, and Claude Code didn't say why."
TESTING = "Testing your key..."


def limit_text(amount: float) -> str:
    """A limit as its box shows it: "190.00"."""
    return f"{amount:.2f}"


def parse_limit(text: str) -> Optional[float]:
    """Dollars typed in a limit box ("190", "$5.00", "1,000"), or None if
    it isn't a number. Whether it's a usable limit is set_limits' to say."""
    cleaned = (text or "").strip().replace(",", "")
    if cleaned.startswith("$"):
        cleaned = cleaned[1:].strip()
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _set_read_only(box: wx.TextCtrl, text: str) -> None:
    """New text in a read-only box, from its start; untouched if it's the
    same, so a box being read isn't moved back to the top for nothing."""
    if box.GetValue() != text:
        box.ChangeValue(text)
        box.SetInsertionPoint(0)


def _amount(value: float) -> str:
    """Money in the total line: "nothing" for none, as billing.money says
    too (#663)."""
    return billing.money(max(0.0, value))


def totals_text(totals, openai=False) -> str:
    """The total line, from ledger.totals(): what's spent, held and left, or
    why nothing may be spent."""
    if totals.problem:
        return totals.problem
    if openai:
        from ..openai_billing import spending_words
        return (f'{spending_words(totals)}. Held: ${totals.held:,.2f}, including '
                f'${totals.unpriced_allowance:,.2f} in unpriced allowances. '
                f'Remaining allowance: ${totals.left:,.2f}. '
                'Dollar limits cannot stop an unpriced message.')
    held = f"Held for running messages: {_amount(totals.held)}. "
    if totals.releasable > RELEASABLE_MIN:
        held = (f"Held: {_amount(totals.held)}, {_amount(totals.releasable)} of it for "
                "messages no longer running. ")
    return (f"Spent since the total was reset: {_amount(totals.spent)}. {held}"
            f"Left: {_amount(totals.left)}.")


PROVIDER_NAMES = ('Claude (Anthropic)', 'Codex (OpenAI)')


def provider_label(index: int, has_key: bool) -> str:
    """A Provider choice, saying whether a key is kept for it (never the
    key): "Claude (Anthropic), key added" or "Codex (OpenAI), no key"."""
    return f"{PROVIDER_NAMES[index]}, {'key added' if has_key else 'no key'}"


class ApiKeysDialog(wx.Dialog):
    """Tools, API Keys (#658). ``speak`` says what each action did (the
    window's ``_feedback``). ``ledger``, ``find_claude`` and
    ``runner_factory`` are there for the tests and the visual probe."""

    def __init__(self, parent, speak: Optional[Callable[[str], None]] = None, ledger=None,
                 find_claude=None, runner_factory=None, openai_ledger=None,
                 openai_runner_factory=None):
        super().__init__(parent, title=TITLE, size=dialog_size(parent, 600, 680),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._speak = speak or (lambda text: None)
        self._ledger = ledger if ledger is not None else spend_ledger.default_ledger()
        self._ledgers = (self._ledger, openai_ledger if openai_ledger is not None else spend_ledger.default_ledger('openai'))
        self._provider_index = 0
        self._key_change_pending = False
        self._keys = billing
        self._openai_runner_factory = openai_runner_factory
        self._find_claude = find_claude or platform_paths.find_claude
        self._runner_factory = runner_factory or claude_cli.TurnRunner
        #: The running Test Key turn, and the token its events must carry to
        #: be shown: a test's answer that comes after Close (or after the
        #: dialog is gone) is dropped.
        self._runner = None
        self._test_token = None
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label='&Provider:'), 0, wx.LEFT | wx.TOP, 8)
        # Each key store asked once here; after that only the chosen
        # provider's key can change, and _sync relabels just that one.
        self.provider_choice = wx.Choice(self, choices=[
            provider_label(0, billing.has_api_key()),
            provider_label(1, openai_billing.has_api_key())])
        self.provider_choice.SetSelection(0)
        set_accessible_name(self.provider_choice, 'Provider')
        sizer.Add(self.provider_choice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.provider_choice.Bind(wx.EVT_CHOICE, lambda e: self.on_provider())

        # What's kept, never the key: read-only text in the Tab order before
        # the key box, so it can be reached and read again by line.
        sizer.Add(wx.StaticText(self, label="Key status:"), 0, wx.LEFT | wx.TOP, 8)
        self.status = _read_only_text(self, self._keys.api_key_status(), "Key status",
                                      min_height=30)
        sizer.Add(self.status, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        self.key_label = wx.StaticText(self, label="Claude API &key:")
        sizer.Add(self.key_label, 0, wx.LEFT | wx.TOP, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        # Masked, and emptied after every Add, so the key isn't left on screen.
        self.key_box = wx.TextCtrl(self, style=wx.TE_PASSWORD)
        set_accessible_name(self.key_box, "Claude API key")
        row.Add(self.key_box, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.add_btn = wx.Button(self, label=ADD_LABEL)
        row.Add(self.add_btn, 0)
        sizer.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        row = wx.BoxSizer(wx.HORIZONTAL)
        self.test_btn = wx.Button(self, label="&Test Key")
        self.remove_btn = wx.Button(self, label="Re&move Key...")
        row.Add(self.test_btn, 0, wx.RIGHT, 6)
        row.Add(self.remove_btn, 0)
        sizer.Add(row, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)

        sizer.Add(wx.StaticText(self, label="Test result:"), 0, wx.LEFT | wx.TOP, 8)
        # Read-only text, reachable by Tab, to read again by line.
        self.result = _read_only_text(self, TEST_INTRO, "Test result", min_height=54)
        sizer.Add(self.result, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        # Siblings of the group's box, as in Settings, so Tab goes through
        # them in the order they're made.
        group = wx.StaticBoxSizer(wx.VERTICAL, self, "Spending limits")
        grid = wx.FlexGridSizer(2, 6, 6)
        grid.AddGrowableCol(0)
        limits = self._ledger.limits()
        grid.Add(wx.StaticText(self, label="Stop API chats once The Chat Place has &spent $"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.overall = wx.TextCtrl(self, value=limit_text(limits.overall))
        set_accessible_name(self.overall, "Stop API chats once The Chat Place has spent, in dollars")
        self.overall.SetMinSize(dip(self, 110, -1))
        grid.Add(self.overall, 0)
        grid.Add(wx.StaticText(self, label="Stop any &one message at about $"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.per_message = wx.TextCtrl(self, value=limit_text(limits.per_message))
        set_accessible_name(self.per_message, "Stop any one message at about, in dollars")
        self.per_message.SetMinSize(dip(self, 110, -1))
        grid.Add(self.per_message, 0)
        group.Add(grid, 0, wx.EXPAND | wx.ALL, 6)
        # The total, right after the limits: read-only text Tab reaches.
        group.Add(wx.StaticText(self, label="Total:"), 0, wx.LEFT | wx.RIGHT, 6)
        self.totals = _read_only_text(self, "", "Total", min_height=54)
        group.Add(self.totals, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.save_limits_btn = wx.Button(self, label="Sa&ve Limits")
        self.reset_btn = wx.Button(self, label="R&eset Total...")
        self.release_btn = wx.Button(self, label="Release &Held...")
        row.Add(self.save_limits_btn, 0, wx.RIGHT, 6)
        row.Add(self.reset_btn, 0, wx.RIGHT, 6)
        row.Add(self.release_btn, 0)
        group.Add(row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        sizer.Add(group, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        #: The limits as last shown or saved, to tell whether Close should ask.
        self._shown_limits = (limits.overall, limits.per_message)

        sizer.Add(wx.StaticText(self, label="About spending:"), 0, wx.LEFT | wx.TOP, 8)
        self.about = _read_only_text(self, ABOUT_SPENDING, "About spending", min_height=54)
        sizer.Add(self.about, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        row = wx.BoxSizer(wx.HORIZONTAL)
        self.billing_btn = wx.Button(self, label="&Billing Page")
        row.Add(self.billing_btn, 0)
        row.AddStretchSpacer()
        self.close_btn = wx.Button(self, wx.ID_CANCEL, "C&lose")
        row.Add(self.close_btn, 0)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 8)

        self.SetSizer(sizer)
        _fit_width(self)
        # Escape, the title bar's close and Alt+F4 all come to Close's
        # handler, which asks about unsaved limits and stops a running test.
        self.SetEscapeId(wx.ID_CANCEL)
        self.add_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_add())
        self.test_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_test())
        self.remove_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_remove())
        self.save_limits_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_save_limits())
        self.reset_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_reset())
        self.release_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_release_held())
        self.billing_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_billing_page())
        self.close_btn.Bind(wx.EVT_BUTTON, lambda e: self.on_close())
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)
        self._refresh()
        wx.CallAfter(self._focus_start)

    # -- state ------------------------------------------------------------------

    def first_control(self) -> wx.Window:
        """Where focus starts: Provider, which says which provider the rest
        of the dialog is about and whether each has a key. Starting on Test
        Key left you guessing which key it would test. On a Mac with
        keyboard navigation off, a pop-up button can't take focus, so the
        key box does."""
        if not self.provider_choice.AcceptsFocusFromKeyboard():
            return self.key_box
        return self.provider_choice

    def _focus_start(self):
        if self and not self.IsBeingDeleted():
            self.first_control().SetFocus()

    def _sync(self):
        """The key status and the buttons, from whether a key is kept."""
        has = self._keys.has_api_key()
        _set_read_only(self.status, self._keys.api_key_status())
        self.add_btn.SetLabel(REPLACE_LABEL if has else ADD_LABEL)
        self.test_btn.Enable(has and self._runner is None and not self._key_change_pending)
        self.remove_btn.Enable(has and not self._key_change_pending)
        self.add_btn.Enable(not self._key_change_pending)
        self.provider_choice.Enable(not self._key_change_pending)
        # The chosen provider's choice says whether it has a key, kept current
        # as its key is added and removed. SetString on Windows deletes and
        # reinserts the item, so the selection is put back.
        label = provider_label(self._provider_index, has)
        if self.provider_choice.GetString(self._provider_index) != label:
            chosen = self.provider_choice.GetSelection()
            self.provider_choice.SetString(self._provider_index, label)
            self.provider_choice.SetSelection(chosen)
        self.Layout()

    def _show_totals(self):
        """The total, and Release Held: on only while something is held that
        no running message holds."""
        totals = self._ledger.totals()
        _set_read_only(self.totals, ('Estimated OpenAI costs. ' if self._provider_index else '') + totals_text(totals, bool(self._provider_index)))
        self.release_btn.Enable(totals.releasable > RELEASABLE_MIN)

    def on_provider(self):
        index = self.provider_choice.GetSelection()
        if self._key_change_pending:
            self.provider_choice.SetSelection(self._provider_index)
            return False
        if index == self._provider_index:
            return True
        if self.limits_edited():
            answer = wx.MessageBox(SAVE_LIMITS_QUESTION, TITLE, wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION, self)
            if answer == wx.CANCEL or (answer == wx.YES and not self.on_save_limits()):
                self.provider_choice.SetSelection(self._provider_index)
                return False
        self._stop_test()
        self.key_box.SetValue('')
        self._provider_index = index
        self._keys = (billing, openai_billing)[index]
        self._ledger = self._ledgers[index]
        name = 'OpenAI' if index else 'Claude'
        self.key_label.SetLabel(name + ' API &key:')
        set_accessible_name(self.key_box, name + ' API key')
        limits = self._ledger.limits()
        self._shown_limits = (limits.overall, limits.per_message)
        self.overall.ChangeValue(limit_text(limits.overall))
        self.per_message.ChangeValue(limit_text(limits.per_message))
        self.result.SetValue(TEST_INTRO)
        self.about.SetValue(ABOUT_SPENDING.replace('Anthropic', 'OpenAI') if index else ABOUT_SPENDING)
        self._refresh()
        self._speak(name + ': separate key, spending limits and total.' + (' Costs are estimated.' if index else ''))
        return True

    def _refresh(self):
        """Everything shown, after anything that may change it."""
        self._sync()
        self._show_totals()

    def _tell(self, text: str, icon=wx.ICON_WARNING):
        wx.MessageBox(text, TITLE, wx.OK | icon, self)

    def _ask(self, question: str) -> bool:
        """Yes or No, No the default."""
        return wx.MessageBox(question, TITLE, wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
                             self) == wx.YES

    # -- the key ------------------------------------------------------------------

    def on_add(self) -> bool:
        """Keep the key in the box. The box is emptied whatever happens, so
        a key never stays in it. True if it was kept."""
        if self._key_change_pending:
            return False
        typed = self.key_box.GetValue()
        self.key_box.SetValue("")
        if not typed.strip():
            self._tell('Paste your OpenAI API key in the OpenAI API key box first.' if self._provider_index else NO_KEY_TYPED, wx.ICON_INFORMATION)
            self.key_box.SetFocus()
            return False
        if not self._keys.looks_like_key(typed):
            self._tell('That is not an OpenAI API key. Copy the full key starting with sk-.' if self._provider_index else NOT_A_KEY)
            self.key_box.SetFocus()
            return False
        if not platform_paths.secrets_available():
            self._tell(CANT_PROTECT)
            self.key_box.SetFocus()
            return False
        if self._provider_index:
            save = self._keys.save_api_key
            self._change_openai_key(lambda: save(typed), 'Key saved.')
            return True
        try:
            self._keys.save_api_key(typed)
        except (OSError, ValueError) as exc:
            self._tell(f"The Chat Place couldn't keep the key: {billing.scrub(str(exc))}")
            self.key_box.SetFocus()
            return False
        self._refresh()
        self._speak("Key saved.")
        return True

    def on_remove(self) -> bool:
        if self._key_change_pending:
            return False
        if not self._keys.has_api_key():
            self._sync()
            return False
        if not self._ask(REMOVE_QUESTION.replace('Claude', 'OpenAI') if self._provider_index else REMOVE_QUESTION):
            self.remove_btn.SetFocus()
            return False
        if self._provider_index:
            self.key_box.SetFocus()
            self._change_openai_key(self._keys.remove_api_key, 'Key removed.')
            return True
        self._keys.remove_api_key()
        # Focus first: Remove is about to be turned off, and a control
        # turned off with the focus on it leaves the focus nowhere.
        self.key_box.SetFocus()
        self._refresh()
        self._speak("Key removed.")
        return True

    def _change_openai_key(self, action, success):
        # A paid request serializes its final write with key mutations. Never
        # wait for that mutex (or the system key store) on the UI thread.
        self.result.SetFocus()  # before Add/Remove become disabled
        self._key_change_pending = True
        self._sync()
        self.result.SetValue('Updating the OpenAI key. Please wait...')
        def done(problem):
            if not self or self.IsBeingDeleted():
                return
            self._key_change_pending = False
            self._refresh()
            self._show_result(problem or success)
        def change():
            problem = ''
            try:
                action()
            except Exception as error:
                problem = 'The OpenAI key could not be updated: ' + billing.scrub(str(error))
            wx.CallAfter(done, problem)
        threading.Thread(target=change, daemon=True).start()

    def on_test(self) -> bool:
        """Start Test Key. True if a test started."""
        if self._runner is not None or self._key_change_pending or not self._keys.has_api_key():
            return False
        if self._provider_index:
            from ..providers.codex_api import OpenAiKeyTestRunner
            token = object()
            def completed(event):
                wx.CallAfter(self._test_done, token, event)
            factory = self._openai_runner_factory or OpenAiKeyTestRunner
            self._runner = factory(completed, ledger=self._ledger)
            self._test_token = token
            self.result.SetValue(TESTING)
            self.result.SetFocus()
            self.test_btn.Disable()
            self._runner.start()
            return True
        lookup = self._find_claude()
        if not lookup.path:
            self._show_result(lookup.problem or "Claude Code wasn't found.")
            return False
        token = object()

        def on_event(event):
            # On the turn's worker thread: back to the window only by CallAfter.
            if event.kind in ("finished", "failed"):
                try:
                    wx.CallAfter(self._test_done, token, event)
                except Exception:  # noqa: BLE001 - the app is closing; nothing to show
                    pass
        try:
            runner = self._runner_factory(
                claude_cli.build_key_test_command(lookup.path), os.path.expanduser("~"),
                claude_cli.KEY_TEST_PROMPT, on_event, billing=billing.ROUTE_API,
                purpose="test", ledger=self._ledger)
        except (OSError, ValueError) as exc:
            self._show_result(f"Couldn't start the test: {billing.scrub(str(exc))}")
            return False
        self._runner, self._test_token = runner, token
        self.result.SetValue(TESTING)
        # Focus to the result, which reads "Testing your key", before Test
        # Key is turned off (see on_remove).
        self.result.SetFocus()
        self.test_btn.Disable()
        runner.start()
        return True

    def _test_done(self, token, event):
        # The dialog may be gone (wx's wrapper is then false), or this test
        # stopped by Close.
        if not self or self.IsBeingDeleted() or token is not self._test_token:
            return
        self._runner = self._test_token = None
        if event.kind == "finished" and not event.is_error:
            cost = (event.data or {}).get("cost_usd")
            words = (event.data or {}).get('cost_words', 'cost not known') if self._provider_index else billing.money(cost)
            text = (f"Your key works. That test was {words.replace('estimated ', 'estimated at ', 1)}."
                    if self._provider_index else f"Your key works. That test cost {words}.")
        else:
            text = billing.scrub(event.text) or NO_RESULT_WORDS
        self._refresh()
        self._show_result(text)

    def _show_result(self, text: str):
        self.result.SetValue(text)
        self.result.SetInsertionPoint(0)
        self._speak(text)

    def _stop_test(self):
        runner, self._runner, self._test_token = self._runner, None, None
        if runner is not None:
            try:
                runner.cancel()
            except Exception:  # noqa: BLE001 - closing must never fail
                pass

    @property
    def testing(self) -> bool:
        return self._runner is not None

    # -- limits and the total -----------------------------------------------------

    def _typed_limits(self):
        return parse_limit(self.overall.GetValue()), parse_limit(self.per_message.GetValue())

    def limits_edited(self) -> bool:
        """Whether a limit box says something other than what's saved
        ("190" for 190.00 isn't a change)."""
        for value, shown in zip(self._typed_limits(), self._shown_limits):
            if value is None or abs(value - shown) >= 0.005:
                return True
        return False

    def on_save_limits(self) -> bool:
        overall, per_message = self._typed_limits()
        for value, box, which, example in ((overall, self.overall, "overall limit", "20"),
                                           (per_message, self.per_message,
                                            "per-message limit", "2")):
            if value is None:
                self._tell(f"Type the {which} as a number of dollars, such as {example}.")
                box.SetFocus()
                return False
        try:
            self._ledger.set_limits(overall, per_message)
        except ValueError as exc:
            self._tell(str(exc))
            self.overall.SetFocus()
            return False
        except OSError as exc:
            self._tell(f"The Chat Place couldn't save the limits: {exc}")
            return False
        saved = self._ledger.limits()
        self._shown_limits = (saved.overall, saved.per_message)
        self.overall.ChangeValue(limit_text(saved.overall))
        self.per_message.ChangeValue(limit_text(saved.per_message))
        self._refresh()
        self._speak("Limits saved.")
        return True

    def on_reset(self) -> bool:
        if not self._ask(RESET_QUESTION):
            self.reset_btn.SetFocus()
            return False
        try:
            self._ledger.reset()
        except OSError as exc:
            self._tell(f"The Chat Place couldn't reset the total: {exc}")
            return False
        self._refresh()
        self._speak("Total reset.")
        return True

    def on_release_held(self) -> bool:
        """Release Held: let go of what's held for messages no longer
        running (an earlier run's, a crash's, or a Stop whose cost wasn't
        known), once you've agreed it may leave spending uncounted. What
        was spent stays counted. True if anything was let go."""
        totals = self._ledger.totals()
        if totals.releasable <= RELEASABLE_MIN:
            self._refresh()
            return False
        if not self._ask(RELEASE_QUESTION.format(amount=billing.money(totals.releasable))):
            self.release_btn.SetFocus()
            return False
        try:
            released = self._ledger.release_held()
        except OSError as exc:
            self._tell(f"The Chat Place couldn't release what was held: {exc}")
            return False
        # Focus first: Release Held is about to be turned off (see on_remove).
        self.totals.SetFocus()
        self._refresh()
        self._speak(f"Released {billing.money(released)}." if released else "Nothing was held.")
        return bool(released)

    def on_billing_page(self):
        platform_paths.open_url(self._keys.CONSOLE_BILLING_URL)

    # -- closing --------------------------------------------------------------------

    def on_close(self) -> bool:
        """Close, Escape or the title bar: ask about limits typed and not
        saved (Cancel stays), stop a running test, and close. True if it
        closed."""
        if self.limits_edited():
            answer = wx.MessageBox(SAVE_LIMITS_QUESTION, TITLE,
                                   wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION, self)
            if answer == wx.CANCEL:
                return False
            if answer == wx.YES and not self.on_save_limits():
                return False
        self._stop_test()
        if self.IsModal():
            self.EndModal(wx.ID_CANCEL)
        return True

    def Destroy(self):
        # At once: wx deletes a top-level window later, when the app is idle.
        self._stop_test()
        return super().Destroy()

    def _on_destroy(self, event):
        # And when it goes with its parent, which never calls Destroy here.
        event.Skip()
        if event.GetEventObject() is self:
            self._stop_test()

    def _on_key(self, event):
        code = event.GetKeyCode()
        if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not event.HasAnyModifiers():
            focus = wx.Window.FindFocus()
            # Enter does what the box is for; there's no default button, so
            # Enter in a limit box can't add a key, nor the other way round.
            if focus is self.key_box:
                self.on_add()
                return
            if focus in (self.overall, self.per_message):
                self.on_save_limits()
                return
        if press_focused_button_on_a_mac(event):
            return
        event.Skip()
