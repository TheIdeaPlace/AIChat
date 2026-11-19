"""Tools, API Keys (#658): adding, replacing, testing and removing the Claude
API key, and the spending limits and total. Built hidden on fake_env's world:
the key store is in memory, the app's folder is in tmp_path, nothing is spoken
and no real claude runs. Keys are made up."""
import os
import threading

import pytest

wx = pytest.importorskip("wx")

from thechatplace import billing, claude_cli, platform_paths, spend_ledger  # noqa: E402
from thechatplace.claude_cli import TurnEvent  # noqa: E402

import fake_env  # noqa: E402

KEY = "sk-ant-test-" + "1" * 24 + "7Q2x"
OTHER_KEY = "sk-ant-test-" + "2" * 24 + "9zZ9"
SECRET = platform_paths.CLAUDE_API_KEY_SECRET


@pytest.fixture
def env(tmp_path, monkeypatch, app):
    return fake_env.install(tmp_path, monkeypatch.setattr)


@pytest.fixture
def parent(env):
    frame = wx.Frame(None, title="API Keys tests")
    yield frame
    wx.GetApp().ProcessPendingEvents()
    frame.Destroy()
    wx.GetApp().ProcessPendingEvents()


@pytest.fixture
def ledger(env):
    return spend_ledger.SpendLedger(env["tmp"] / "appdata")


class FakeTestRunner:
    """Stands in for TurnRunner: start() delivers its events from a thread
    of its own, as the real one's worker does."""

    def __init__(self, command, cwd, prompt, on_event, options, events):
        self.command, self.cwd, self.prompt, self.on_event = command, cwd, prompt, on_event
        self.options = options
        self.events = list(events)
        self.started = self.cancelled = False

    def start(self):
        self.started = True
        if self.events:
            worker = threading.Thread(target=lambda: [self.on_event(e) for e in self.events])
            worker.start()
            worker.join()

    def cancel(self):
        self.cancelled = True


class Runners:
    """A runner_factory that keeps every runner it made."""

    def __init__(self, *events):
        self.events = events
        self.made = []

    def __call__(self, command, cwd, prompt, on_event, **options):
        self.made.append(FakeTestRunner(command, cwd, prompt, on_event, options, self.events))
        return self.made[-1]


def found_claude():
    return platform_paths.ClaudeLookup("claude.exe")


def make(parent, ledger, runners=None, find_claude=found_claude):
    from thechatplace.ui.api_keys_dialog import ApiKeysDialog
    said = []
    runners = runners or Runners()
    dialog = ApiKeysDialog(parent, speak=said.append, ledger=ledger, find_claude=find_claude,
                           runner_factory=runners)
    dialog.said, dialog.runners = said, runners
    return dialog


@pytest.fixture
def dialog(parent, ledger):
    window = make(parent, ledger)
    yield window
    if window and not window.IsBeingDeleted():
        window.Destroy()
    wx.GetApp().ProcessPendingEvents()


def _add(dialog, key=KEY):
    dialog.key_box.SetValue(key)
    return dialog.on_add()


def _shown_text(dialog):
    """Everything the dialog shows: labels and the boxes' text."""
    return [c.GetValue() if isinstance(c, wx.TextCtrl) else c.GetLabel()
            for c in dialog.GetChildren()]


# -- how it opens ---------------------------------------------------------------------


def test_with_no_key_it_opens_on_provider_with_test_and_remove_off(dialog):
    from thechatplace.ui import api_keys_dialog as akd
    assert dialog.GetTitle() == "API Keys"
    assert dialog.status.GetValue() == "Claude: No key"
    assert dialog.add_btn.GetLabel() == "&Add Key"
    assert not dialog.test_btn.IsEnabled() and not dialog.remove_btn.IsEnabled()
    assert dialog.first_control() is dialog.provider_choice
    assert dialog.provider_choice.GetString(0) == "Claude (Anthropic), no key"
    assert dialog.provider_choice.GetString(1) == "Codex (OpenAI), no key"
    assert dialog.key_box.GetWindowStyle() & wx.TE_PASSWORD
    assert dialog.result.GetValue() == akd.TEST_INTRO
    assert dialog.about.GetValue() == akd.ABOUT_SPENDING
    assert dialog.overall.GetValue() == "20.00"
    assert dialog.per_message.GetValue() == "2.00"
    assert dialog.totals.GetValue() == ("Spent since the total was reset: nothing. Held for "
                                        "running messages: nothing. Left: $20.00.")
    assert not dialog.limits_edited()


def test_with_a_key_it_opens_on_provider_saying_so(parent, ledger, env):
    # Not on Test Key: which key it would test wasn't said (bld05 feedback).
    billing.save_api_key(KEY)
    window = make(parent, ledger)
    try:
        assert window.first_control() is window.provider_choice
        assert window.provider_choice.GetSelection() == 0
        assert window.provider_choice.GetString(0) == "Claude (Anthropic), key added"
        assert window.provider_choice.GetString(1) == "Codex (OpenAI), no key"
        assert window.add_btn.GetLabel() == "&Replace Key"
        assert window.test_btn.IsEnabled() and window.remove_btn.IsEnabled()
        assert window.status.GetValue().startswith("Claude: key ending in 7Q2x, added ")
    finally:
        window.Destroy()


def test_opening_it_never_reads_the_key(parent, ledger, env, monkeypatch):
    # Reading it can raise a keychain prompt on a Mac.
    billing.save_api_key(KEY)

    def no(name):
        raise AssertionError("load_secret was called")
    monkeypatch.setattr(platform_paths, "load_secret", no)
    window = make(parent, ledger)
    try:
        assert window.status.GetValue().startswith("Claude: key ending in 7Q2x")
    finally:
        window.Destroy()


def test_every_box_is_named_and_labelled_and_tab_goes_in_order(dialog):
    named = {dialog.status: ("Key status", "Key status:"),
             dialog.key_box: ("Claude API key", "Claude API &key:"),
             dialog.result: ("Test result", "Test result:"),
             dialog.totals: ("Total", "Total:"),
             dialog.overall: ("Stop API chats once The Chat Place has spent, in dollars",
                              "Stop API chats once The Chat Place has &spent $"),
             dialog.per_message: ("Stop any one message at about, in dollars",
                                  "Stop any &one message at about $"),
             dialog.about: ("About spending", "About spending:")}
    for box, (name, label) in named.items():
        assert box.GetName() == name
        before = box.GetPrevSibling()
        assert isinstance(before, wx.StaticText) and before.GetLabel() == label
    labels = {dialog.add_btn: "&Add Key", dialog.test_btn: "&Test Key",
              dialog.remove_btn: "Re&move Key...", dialog.save_limits_btn: "Sa&ve Limits",
              dialog.reset_btn: "R&eset Total...", dialog.release_btn: "Release &Held...",
              dialog.billing_btn: "&Billing Page", dialog.close_btn: "C&lose"}
    for button, label in labels.items():
        assert button.GetLabel() == label
    # Tab follows the order the controls were made in: the key status before
    # the key box, the total right after the limits (#658 review).
    order = [dialog.status, dialog.key_box, dialog.add_btn, dialog.test_btn, dialog.remove_btn,
             dialog.result, dialog.overall, dialog.per_message, dialog.totals,
             dialog.save_limits_btn, dialog.reset_btn, dialog.release_btn,
             dialog.about, dialog.billing_btn, dialog.close_btn]
    assert [c for c in dialog.GetChildren() if c in order] == order
    # The status and total are read-only boxes Tab reaches, not static text.
    for box in (dialog.status, dialog.totals):
        # (wx gives a read-only text box the focus only when it's multi-line.)
        assert isinstance(box, wx.TextCtrl) and not box.IsEditable() and box.IsMultiLine()
    # Escape closes through Close.
    assert dialog.close_btn.GetId() == wx.ID_CANCEL
    assert dialog.GetEscapeId() == wx.ID_CANCEL


# -- the key ----------------------------------------------------------------------------


def test_adding_a_key_keeps_it_empties_the_box_and_says_so(dialog, env):
    assert _add(dialog)
    assert dialog.key_box.GetValue() == ""
    assert platform_paths.load_secret(SECRET) == KEY
    assert dialog.status.GetValue().startswith("Claude: key ending in 7Q2x, added ")
    assert dialog.add_btn.GetLabel() == "&Replace Key"
    assert dialog.test_btn.IsEnabled() and dialog.remove_btn.IsEnabled()
    # Provider says so now, and stays on Claude.
    assert dialog.provider_choice.GetString(0) == "Claude (Anthropic), key added"
    assert dialog.provider_choice.GetSelection() == 0
    assert dialog.said == ["Key saved."]
    assert env["boxes"] == []
    # The key is nowhere in the dialog, nor in the plain file beside it.
    assert not any(KEY in text or "1111" in text for text in _shown_text(dialog))
    info = (env["tmp"] / "appdata" / billing.API_KEY_INFO_FILE).read_text(encoding="utf-8")
    assert "sk-ant" not in info


def test_enter_in_the_key_box_adds_it(dialog, monkeypatch):
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: dialog.key_box))
    dialog.key_box.SetValue(KEY)
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(wx.WXK_RETURN)
    dialog._on_key(event)
    assert platform_paths.load_secret(SECRET) == KEY


@pytest.mark.parametrize("typed", ["sk-proj-000000000000000000000000000000", "sk-ant-short",
                                   "my key", KEY + " and more"])
def test_something_that_isnt_a_key_is_refused_and_still_cleared(dialog, env, typed):
    from thechatplace.ui import api_keys_dialog as akd
    assert not _add(dialog, typed)
    assert env["boxes"] == [akd.NOT_A_KEY]
    assert "sk-ant-" in akd.NOT_A_KEY
    assert dialog.key_box.GetValue() == ""
    assert not platform_paths.has_secret(SECRET)
    assert dialog.status.GetValue() == "Claude: No key" and dialog.said == []


def test_an_empty_box_says_to_paste_the_key(dialog, env):
    from thechatplace.ui import api_keys_dialog as akd
    assert not _add(dialog, "   ")
    assert env["boxes"] == [akd.NO_KEY_TYPED]
    assert not platform_paths.has_secret(SECRET)


def test_a_computer_that_cant_protect_a_key_doesnt_keep_it(dialog, env, monkeypatch):
    from thechatplace.ui import api_keys_dialog as akd
    monkeypatch.setattr(platform_paths, "secrets_available", lambda: False)
    assert not _add(dialog)
    assert env["boxes"] == [akd.CANT_PROTECT]
    assert dialog.key_box.GetValue() == ""
    assert not platform_paths.has_secret(SECRET)


def test_a_key_store_that_refuses_says_why_without_the_key(dialog, env, monkeypatch):
    def refuse(name, text):
        raise OSError(f"secret-tool couldn't keep it: rejected {text}")
    monkeypatch.setattr(platform_paths, "save_secret", refuse)
    assert not _add(dialog)
    assert env["boxes"][-1].startswith("The Chat Place couldn't keep the key: ")
    assert KEY not in env["boxes"][-1]
    assert dialog.key_box.GetValue() == ""


def test_replacing_the_key(dialog):
    _add(dialog)
    assert _add(dialog, OTHER_KEY)
    assert platform_paths.load_secret(SECRET) == OTHER_KEY
    assert dialog.status.GetValue().startswith("Claude: key ending in 9zZ9")
    assert dialog.said == ["Key saved.", "Key saved."]


def test_removing_the_key_asks_first(dialog, env, monkeypatch):
    from thechatplace.ui import api_keys_dialog as akd
    _add(dialog)
    asked = []
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: asked.append(a) or wx.NO)
    assert not dialog.on_remove()
    assert asked[0][0] == akd.REMOVE_QUESTION
    assert asked[0][2] & wx.YES_NO and asked[0][2] & wx.NO_DEFAULT
    assert platform_paths.has_secret(SECRET)
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: asked.append(a) or wx.YES)
    assert dialog.on_remove()
    assert not platform_paths.has_secret(SECRET)
    assert not (env["tmp"] / "appdata" / billing.API_KEY_INFO_FILE).exists()
    assert dialog.status.GetValue() == "Claude: No key"
    assert dialog.add_btn.GetLabel() == "&Add Key"
    assert not dialog.test_btn.IsEnabled() and not dialog.remove_btn.IsEnabled()
    assert dialog.provider_choice.GetString(0) == "Claude (Anthropic), no key"
    assert dialog.provider_choice.GetSelection() == 0
    assert dialog.said[-1] == "Key removed."


# -- Test Key ---------------------------------------------------------------------------


def _test(dialog):
    assert dialog.on_test()
    assert fake_env.pump(lambda: not dialog.testing)


def test_test_key_runs_one_api_turn_and_says_what_it_cost(parent, ledger, env):
    runners = Runners(TurnEvent("started"),
                      TurnEvent("finished", text="OK", data={"billing": "api",
                                                             "cost_usd": 0.0043}))
    window = make(parent, ledger, runners)
    try:
        _add(window)
        assert window.on_test()
        runner = runners.made[0]
        assert runner.started
        assert runner.command == claude_cli.build_key_test_command("claude.exe")
        assert runner.prompt == claude_cli.KEY_TEST_PROMPT
        assert runner.cwd == os.path.expanduser("~")
        assert runner.options == {"billing": "api", "purpose": "test", "ledger": ledger}
        # Its answer comes back through CallAfter: until then it's running.
        assert window.testing and not window.test_btn.IsEnabled()
        assert window.result.GetValue() == "Testing your key..."
        assert fake_env.pump(lambda: not window.testing)
        said = "Your key works. That test cost less than a cent."
        assert window.result.GetValue() == said
        assert window.said[-1] == said
        assert window.test_btn.IsEnabled()
    finally:
        window.Destroy()


@pytest.mark.parametrize("event", [
    TurnEvent("failed", text="Anthropic refused your Claude API key. Replace it in Tools, API "
                             "Keys, then send again.", is_error=True),
    TurnEvent("finished", text="The turn ended with an error (error_during_execution).",
              is_error=True, data={"billing": "api", "cost_usd": 0.001}),
])
def test_a_test_that_fails_shows_and_says_why(parent, ledger, event):
    window = make(parent, ledger, Runners(event))
    try:
        _add(window)
        _test(window)
        assert window.result.GetValue() == event.text
        assert window.said[-1] == event.text
        assert window.test_btn.IsEnabled()
    finally:
        window.Destroy()


def test_a_failure_with_no_words_still_says_something(parent, ledger):
    from thechatplace.ui import api_keys_dialog as akd
    window = make(parent, ledger, Runners(TurnEvent("failed", is_error=True)))
    try:
        _add(window)
        _test(window)
        assert window.result.GetValue() == akd.NO_RESULT_WORDS
    finally:
        window.Destroy()


def test_test_key_without_claude_code_says_how_to_get_it(parent, ledger):
    lookup = platform_paths.ClaudeLookup(None, reason="Claude Code isn't installed.")
    window = make(parent, ledger, find_claude=lambda: lookup)
    try:
        _add(window)
        assert not window.on_test()
        assert window.runners.made == []
        assert window.result.GetValue() == lookup.problem
        assert window.said[-1] == lookup.problem
        assert window.test_btn.IsEnabled()
    finally:
        window.Destroy()


def test_test_key_needs_a_key(dialog):
    assert not dialog.on_test()
    assert dialog.runners.made == []


def test_close_stops_a_running_test_and_its_answer_is_dropped(dialog):
    _add(dialog)
    assert dialog.on_test()
    runner = dialog.runners.made[0]
    assert dialog.on_close()
    assert runner.cancelled and not dialog.testing
    runner.on_event(TurnEvent("finished", text="OK", data={"billing": "api", "cost_usd": 0.01}))
    fake_env.pump(lambda: False, timeout=0.3)
    assert dialog.result.GetValue() == "Testing your key..."


def test_a_test_still_running_when_the_dialog_goes_is_stopped(parent, ledger):
    window = make(parent, ledger)
    _add(window)
    assert window.on_test()
    runner = window.runners.made[0]
    window.Destroy()
    assert fake_env.pump(lambda: runner.cancelled)
    # Its answer, coming after, reaches nothing.
    runner.on_event(TurnEvent("finished", text="OK", data={"billing": "api", "cost_usd": 0.01}))
    fake_env.pump(lambda: False, timeout=0.3)


# -- limits and the total ---------------------------------------------------------------


def test_saving_limits(dialog, ledger):
    dialog.overall.SetValue("250")
    dialog.per_message.SetValue("$7.5")
    assert dialog.limits_edited()
    assert dialog.on_save_limits()
    assert ledger.limits() == spend_ledger.Limits(250.0, 7.5)
    assert dialog.overall.GetValue() == "250.00"
    assert dialog.per_message.GetValue() == "7.50"
    assert not dialog.limits_edited()
    assert dialog.said == ["Limits saved."]
    assert dialog.totals.GetValue().endswith("Left: $250.00.")
    # The same amount typed another way isn't a change.
    dialog.overall.SetValue("250")
    assert not dialog.limits_edited()


@pytest.mark.parametrize("overall,per_message,said", [
    ("lots", "5", "Type the overall limit as a number of dollars, such as 20."),
    ("190", "", "Type the per-message limit as a number of dollars, such as 2."),
    ("190", "nan", "Type the per-message limit as a number of dollars, such as 2."),
    ("0", "5", "A limit must be at least 1 cent and at most $100,000."),
    ("190", "-2", "A limit must be at least 1 cent and at most $100,000."),
    ("200000", "5", "A limit must be at least 1 cent and at most $100,000."),
])
def test_a_limit_that_cant_be_used_says_why_and_saves_nothing(dialog, ledger, env, overall,
                                                              per_message, said):
    dialog.overall.SetValue(overall)
    dialog.per_message.SetValue(per_message)
    assert not dialog.on_save_limits()
    assert env["boxes"] == [said]
    assert ledger.limits() == spend_ledger.Limits()
    assert not ledger.limits_path.exists()
    assert dialog.said == []


def test_the_tally_line_says_spent_held_and_left(parent, ledger):
    done = ledger.reserve("chat-1")
    ledger.settle(done, 3.21)
    ledger.close(done, complete=True)
    ledger.reserve("chat-2")  # still running: its $2.00 is held
    window = make(parent, ledger)
    try:
        assert window.totals.GetValue() == ("Spent since the total was reset: $3.21. Held for "
                                            "running messages: $2.00. Left: $14.79.")
    finally:
        window.Destroy()


def test_the_tally_line_says_why_nothing_may_be_spent(parent, ledger):
    ledger.folder.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text("not a ledger line\n", encoding="utf-8")
    window = make(parent, ledger)
    try:
        assert window.totals.GetValue() == ledger.totals().problem
        assert "damaged" in window.totals.GetValue()
    finally:
        window.Destroy()


def test_resetting_the_tally_asks_first(parent, ledger, monkeypatch):
    from thechatplace.ui import api_keys_dialog as akd
    done = ledger.reserve("chat-1")
    ledger.settle(done, 3.21)
    ledger.close(done, complete=True)
    window = make(parent, ledger)
    try:
        asked = []
        monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: asked.append(a) or wx.NO)
        assert not window.on_reset()
        assert asked[0][0] == akd.RESET_QUESTION and asked[0][2] & wx.NO_DEFAULT
        assert ledger.totals().spent == pytest.approx(3.21)
        monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: asked.append(a) or wx.YES)
        assert window.on_reset()
        assert ledger.totals().spent == 0
        assert window.totals.GetValue() == ("Spent since the total was reset: nothing. Held "
                                            "for running messages: nothing. Left: $20.00.")
        assert window.said == ["Total reset."]
    finally:
        window.Destroy()


def test_release_held_is_off_with_nothing_left_over(dialog, ledger):
    assert not dialog.release_btn.IsEnabled()
    ledger.reserve("running")                  # this run's, still going
    dialog._refresh()
    assert not dialog.release_btn.IsEnabled()
    assert not dialog.on_release_held()


def test_release_held_lets_go_of_old_holds_after_asking(parent, ledger, env, monkeypatch):
    from thechatplace.ui import api_keys_dialog as akd
    crashed = spend_ledger.SpendLedger(ledger.folder)       # an earlier run that crashed
    old = crashed.reserve("old")
    crashed.settle(old, 1.0)                                # $1.00 of its $2.00 hold left
    running = ledger.reserve("running")
    window = make(parent, ledger)
    try:
        assert window.release_btn.IsEnabled()
        assert window.totals.GetValue() == (
            "Spent since the total was reset: $1.00. Held: $3.00, $1.00 of it for messages "
            "no longer running. Left: $16.00.")
        asked = []
        monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: asked.append(a) or wx.NO)
        assert not window.on_release_held()
        assert asked[0][0] == akd.RELEASE_QUESTION.format(amount="$1.00")
        assert "may have spent up to $1.00 that won't be counted" in asked[0][0]
        assert asked[0][2] & wx.YES_NO and asked[0][2] & wx.NO_DEFAULT
        assert ledger.totals().held == pytest.approx(3.0)
        monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: asked.append(a) or wx.YES)
        assert window.on_release_held()
        totals = ledger.totals()
        assert totals.held == pytest.approx(2.0) and totals.spent == pytest.approx(1.0)
        assert not window.release_btn.IsEnabled()
        assert window.totals.GetValue() == (
            "Spent since the total was reset: $1.00. Held for running messages: $2.00. "
            "Left: $17.00.")
        assert window.said == ["Released $1.00."]
        ledger.close(running, complete=False)  # a Stop whose cost wasn't known
        window._refresh()
        assert window.release_btn.IsEnabled()
    finally:
        window.Destroy()


def test_the_status_and_tally_follow_every_action(dialog, ledger, monkeypatch):
    _add(dialog)
    assert dialog.status.GetValue().startswith("Claude: key ending in 7Q2x")
    # Spending from elsewhere (a chat) shows after the next action here.
    held = ledger.reserve("chat-1")
    ledger.settle(held, 2.0)
    ledger.close(held, complete=True)
    dialog.overall.SetValue("100")
    assert dialog.on_save_limits()
    assert dialog.totals.GetValue().startswith("Spent since the total was reset: $2.00.")
    assert dialog.totals.GetValue().endswith("Left: $98.00.")
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: wx.YES)
    assert dialog.on_remove()
    assert dialog.status.GetValue() == "Claude: No key"
    assert dialog.on_reset()
    assert dialog.totals.GetValue().startswith("Spent since the total was reset: nothing.")


def test_close_asks_about_limits_typed_and_not_saved(dialog, ledger, monkeypatch):
    from thechatplace.ui import api_keys_dialog as akd
    asked = []
    dialog.overall.SetValue("300")
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: asked.append(a) or wx.CANCEL)
    assert not dialog.on_close()
    assert asked[-1][0] == akd.SAVE_LIMITS_QUESTION and asked[-1][2] & wx.CANCEL
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: asked.append(a) or wx.NO)
    assert dialog.on_close()
    assert ledger.limits().overall == 20.0
    monkeypatch.setattr(wx, "MessageBox", lambda *a, **k: asked.append(a) or wx.YES)
    assert dialog.on_close()
    assert ledger.limits().overall == 300.0


def test_close_stays_open_when_the_limits_it_was_asked_to_save_cant_be(dialog, ledger, env):
    dialog.overall.SetValue("lots")
    assert not dialog.on_close()  # fake_env answers Yes, then the number is refused
    assert env["boxes"] == ["Save the new spending limits?",
                            "Type the overall limit as a number of dollars, such as 20."]


def test_close_with_nothing_changed_asks_nothing(dialog, env):
    assert dialog.on_close()
    assert env["boxes"] == []


def test_enter_in_a_limit_box_saves_the_limits(dialog, ledger, monkeypatch):
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: dialog.per_message))
    dialog.per_message.SetValue("2")
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(wx.WXK_RETURN)
    dialog._on_key(event)
    assert ledger.limits().per_message == 2.0
    assert not platform_paths.has_secret(SECRET)


def test_billing_page_opens_the_console(dialog, env):
    dialog.on_billing_page()
    assert env["opened"] == [billing.CONSOLE_BILLING_URL]


def test_openai_has_separate_key_limits_total_and_release(parent, env, monkeypatch):
    from thechatplace import openai_billing
    from thechatplace.ui.api_keys_dialog import ApiKeysDialog
    claude = spend_ledger.SpendLedger(env['tmp'] / 'claude')
    openai = spend_ledger.SpendLedger(env['tmp'] / 'openai')
    # Both start at the same defaults, so OpenAI's are set apart here to show
    # the boxes follow the provider.
    openai.set_limits(5, .5)
    held = openai.reserve('codex-api:test')
    openai.close(held, complete=False)
    window = ApiKeysDialog(parent, ledger=claude, openai_ledger=openai)
    try:
        window.provider_choice.SetSelection(1)
        assert window.on_provider()
        assert window.status.GetValue() == 'OpenAI: No key'
        assert window.key_box.GetName() == 'OpenAI API key'
        assert window.overall.GetValue() == '5.00' and window.per_message.GetValue() == '0.50'
        assert 'Estimated' in window.totals.GetValue() and window.release_btn.IsEnabled()
        window.key_box.SetValue('sk-proj-' + '0' * 30)
        assert window.on_add() and window.key_box.GetValue() == ''
        assert fake_env.pump(lambda: not window._key_change_pending)
        assert openai_billing.has_api_key() and not billing.has_api_key()
        # Saved in the background; Provider then says so, still on Codex.
        assert window.provider_choice.GetString(1) == 'Codex (OpenAI), key added'
        assert window.provider_choice.GetString(0) == 'Claude (Anthropic), no key'
        assert window.provider_choice.GetSelection() == 1
        monkeypatch.setattr(wx, 'MessageBox', lambda *a, **k: wx.YES)
        assert window.on_release_held() and openai.totals().held == 0
        window.on_billing_page()
        assert env['opened'][-1] == openai_billing.CONSOLE_BILLING_URL
        window.provider_choice.SetSelection(0)
        assert window.on_provider()
        assert window.overall.GetValue() == '20.00' and window.status.GetValue() == 'Claude: No key'
    finally:
        window.Destroy()


def test_provider_switch_cancel_preserves_limits_and_clears_no_secret(dialog, monkeypatch):
    dialog.overall.SetValue('12')
    monkeypatch.setattr(wx, 'MessageBox', lambda *a, **k: wx.CANCEL)
    dialog.provider_choice.SetSelection(1)
    assert not dialog.on_provider()
    assert dialog.provider_choice.GetSelection() == 0 and dialog.overall.GetValue() == '12'


def test_openai_test_result_is_estimated_and_late_event_is_ignored(parent, env):
    from thechatplace import openai_billing
    from thechatplace.ui.api_keys_dialog import ApiKeysDialog
    openai_billing.save_api_key('sk-proj-' + '0' * 30)
    made = []
    class Runner:
        def __init__(self, done, **kwargs):
            self.done, self.cancelled = done, False
            made.append(self)
        def start(self):
            pass
        def cancel(self):
            self.cancelled = True
    window = ApiKeysDialog(parent, openai_runner_factory=Runner)
    try:
        window.provider_choice.SetSelection(1)
        window.on_provider()
        assert window.on_test() and window.testing
        token = window._test_token
        window._test_done(token, TurnEvent('finished', data={'cost_usd': .00001, 'cost_words': 'estimated less than a cent'}))
        assert window.result.GetValue() == 'Your key works. That test was estimated at less than a cent.'
        assert window.on_test()
        token = window._test_token
        window.provider_choice.SetSelection(0)
        window.on_provider()
        assert made[-1].cancelled
        before = window.result.GetValue()
        window._test_done(token, TurnEvent('failed', text='late failure'))
        assert window.result.GetValue() == before
    finally:
        window.Destroy()


def test_openai_key_save_does_not_wait_on_the_ui_thread(parent, env, monkeypatch):
    from thechatplace import openai_billing
    from thechatplace.ui.api_keys_dialog import ApiKeysDialog
    window = ApiKeysDialog(parent)
    try:
        window.provider_choice.SetSelection(1)
        window.on_provider()
        focus = []
        monkeypatch.setattr(window.result, 'SetFocus', lambda: focus.append('result'))
        sync = window._sync
        def checked_sync():
            if window._key_change_pending:
                assert focus == ['result']
            sync()
        monkeypatch.setattr(window, '_sync', checked_sync)
        with openai_billing.credentials_lock:
            window.key_box.SetValue('sk-proj-' + '0' * 30)
            assert window.on_add()
            assert window._key_change_pending and not window.provider_choice.IsEnabled()
            assert not openai_billing.has_api_key()
        assert fake_env.pump(lambda: not window._key_change_pending)
        assert openai_billing.has_api_key()
    finally:
        window.Destroy()


def test_openai_release_held_warns_cost_unknown_and_keeps_token_history(parent, env, monkeypatch):
    from thechatplace.ui.api_keys_dialog import ApiKeysDialog, totals_text
    window = ApiKeysDialog(parent)
    try:
        window.provider_choice.SetSelection(1)
        window.on_provider()
        ledger = spend_ledger.default_ledger('openai')
        r = ledger.reserve('codex-api:unknown', unpriced=True)
        ledger.record_openai_tokens(r, 'future', dict(inputTokens=100, cachedInputTokens=0,
            cacheWriteInputTokens=0, outputTokens=10, reasoningOutputTokens=0), unpriced=True)
        ledger.close(r, complete=False)
        assert 'of it for messages no longer running' in totals_text(ledger.totals(), openai=True)
        questions = []
        monkeypatch.setattr(window, '_ask', lambda text: questions.append(text) or True)
        assert window.on_release_held()
        assert 'cost is unknown and may be more' in questions[0]
        assert 'up to' not in questions[0]
        assert ledger.totals().held == 0
        assert ledger.totals().unpriced_all_time[r.chat_id]['inputTokens'] == 100
    finally:
        window.Destroy()

