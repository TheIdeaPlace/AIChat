"""Chats on API credits in the window (#658), on a frame that's never shown:
what's said and shown for them, the key and first-send checks, New Session's
Charge to, Continue on API Credits, Usage and Settings. No real key, claude
or speech: tests/fake_env.py keeps secrets in memory and records speech."""
import pytest

wx = pytest.importorskip("wx")

from thechatplace import platform_paths, spend_ledger, usage  # noqa: E402
from thechatplace.claude_cli import API_MODELS, MODELS, TurnEvent  # noqa: E402
from thechatplace.own_store import OwnSession, OwnSessionStore  # noqa: E402

from records import assistant_block, text_block, user_text  # noqa: E402
import fake_env  # noqa: E402
from fake_env import FakeRunner, add_transcript, now_ms, pump  # noqa: E402

FAKE_KEY = "sk-ant-test-0000000000000000000000000000"


@pytest.fixture
def env(tmp_path, monkeypatch, app):
    return fake_env.install(tmp_path, monkeypatch.setattr)


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "Work"
    path.mkdir()
    return str(path)


@pytest.fixture
def frame(env, folder, monkeypatch):
    from thechatplace.ui import main_frame
    from thechatplace.ui.main_frame import MainFrame
    FakeRunner.instances = []
    monkeypatch.setattr(main_frame, "TurnRunner", FakeRunner)
    monkeypatch.setattr(platform_paths, "find_claude",
                        lambda: platform_paths.ClaudeLookup("claude.exe"))
    for cli in ("own-1", "api-1"):
        add_transcript(env, folder, cli, [user_text("Hello"),
                                          assistant_block(text_block("Hi"), "m1")])
    store = OwnSessionStore(env["tmp"] / "own.json")
    store.add(OwnSession("own-1", "Mine", folder, last_activity_ms=now_ms()))
    store.add(OwnSession("api-1", "Paid", folder, last_activity_ms=now_ms(), billing="api"))
    window = MainFrame(store=store, check_updates_at_start=False)
    assert pump(lambda: window.session_list.GetCount() == 2)
    yield window
    window._runners.clear()
    window.stop_timers()
    window._pool.shutdown(wait=True)
    wx.GetApp().ProcessPendingEvents()
    window.Destroy()
    wx.GetApp().ProcessPendingEvents()


def add_key():
    platform_paths.save_secret(platform_paths.CLAUDE_API_KEY_SECRET, FAKE_KEY)


def open_chat(frame, session_id):
    frame.open_session(frame.store.get(session_id).to_info())


def select(frame, title):
    for i in range(frame.session_list.GetCount()):
        if frame.session_list.GetString(i).startswith(title):
            frame.session_list.SetSelection(i)
            frame.session_list.SetFocus()
            return
    raise AssertionError(f"{title} not in list")


def asked(frame, monkeypatch, answer=True):
    """Stand in for "Send on API credits?"; returns the titles it was asked for."""
    titles = []
    monkeypatch.setattr(frame, "_confirm_api_send",
                        lambda title: titles.append(title) or answer)
    return titles


# -- always knowing ---------------------------------------------------------------------------


def test_the_row_title_and_reply_box_say_api_credits(frame):
    rows = [frame.session_list.GetString(i) for i in range(frame.session_list.GetCount())]
    assert any(row.startswith("Paid, API credits") for row in rows)
    assert not any("API credits" in row for row in rows if row.startswith("Mine"))
    open_chat(frame, "api-1")
    assert frame.GetTitle() == "Paid, API credits — The Chat Place"
    assert frame.reply_text.GetName() == "Your message, API credits"
    assert ", on API credits" in frame.session_heading.GetLabel()
    open_chat(frame, "own-1")
    assert frame.GetTitle() == "Mine — The Chat Place"
    assert frame.reply_text.GetName() == "Your message"


def test_the_status_bar_shows_what_an_api_chat_and_the_app_spent(frame):
    ledger = spend_ledger.default_ledger()
    held = ledger.reserve("api-1")
    ledger.settle(held, 0.42)
    ledger.close(held, complete=True)
    open_chat(frame, "api-1")
    part = frame.status_parts.get("api_credits")
    assert part.IsShown()
    assert part.GetLabel() == "API credits: this chat $0.42, The Chat Place $0.42 of $190"
    open_chat(frame, "own-1")
    assert not frame.status_parts.get("api_credits").IsShown()


# -- the key and the first send ---------------------------------------------------------------


def test_with_no_key_an_api_chat_refuses_and_keeps_the_message(frame, env, monkeypatch):
    titles = asked(frame, monkeypatch)
    open_chat(frame, "api-1")
    frame.reply_text.SetValue("Spend nothing")
    frame.on_send()
    assert env["boxes"][-1] == ("This chat is charged to API credits, but there's no Claude "
                                "API key. Add one with Tools, API Keys.")
    assert frame.reply_text.GetValue() == "Spend nothing"
    assert FakeRunner.instances == [] and titles == []


def test_the_first_send_is_asked_once_and_each_send_is_said(frame, env, monkeypatch):
    add_key()
    titles = asked(frame, monkeypatch)
    open_chat(frame, "api-1")
    frame.reply_text.SetValue("First")
    frame.on_send()
    assert titles == ["Paid"]
    runner = FakeRunner.instances[-1]
    assert (runner.billing, runner.chat_id) == ("api", "api-1")
    assert runner.ledger is spend_ledger.default_ledger()
    assert "Sent on API credits." in env["feedback"]
    # The turn ends; the next send isn't asked again this run.
    frame._on_turn_event({"id": "api-1"}, "Paid", TurnEvent("finished", text="ok"))
    env["feedback"].clear()
    frame.reply_text.SetValue("Second")
    frame.on_send()
    assert titles == ["Paid"]
    assert len(FakeRunner.instances) == 2
    assert "Sent on API credits." in env["feedback"]


def test_sent_on_api_credits_is_said_even_with_announcements_silent(frame, env, monkeypatch):
    from thechatplace.speech import ANNOUNCE_SILENT
    add_key()
    asked(frame, monkeypatch)
    frame.speech.announce = ANNOUNCE_SILENT
    open_chat(frame, "api-1")
    frame.reply_text.SetValue("Quietly")
    frame.on_send()
    assert len(FakeRunner.instances) == 1
    # Said, queued behind other speech rather than interrupting it.
    assert "Sent on API credits." in env["feedback"]
    assert "Sent on API credits." not in env["spoken"]
    # Repeat Last Announcement isn't taken over by it.
    assert getattr(frame, "_last_announcement", "") != "Sent on API credits."


def test_a_reached_limit_is_said_before_the_question_and_keeps_the_message(frame, env,
                                                                          monkeypatch):
    add_key()
    titles = asked(frame, monkeypatch)
    ledger = spend_ledger.default_ledger()
    ledger.set_limits(5, 5)
    held = ledger.reserve("api-1")
    ledger.settle(held, 5.0)
    ledger.close(held, complete=True)
    open_chat(frame, "api-1")
    frame.reply_text.SetValue("Over the limit")
    frame.on_send()
    assert env["boxes"][-1].startswith("The Chat Place has reached your limit of $5.00")
    assert frame.reply_text.GetValue() == "Over the limit"
    assert FakeRunner.instances == [] and titles == []
    assert "Sent on API credits." not in env["feedback"]


def test_a_damaged_tally_stops_an_aside_too(frame, env, monkeypatch):
    from thechatplace.ui import claude_extras
    add_key()
    asked(frame, monkeypatch)
    ledger = spend_ledger.default_ledger()
    ledger.folder.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text("not a ledger line\n", encoding="utf-8")

    def opened(*args, **kwargs):
        pytest.fail("Ask Aside opened on a damaged tally")
    monkeypatch.setattr(claude_extras, "AskAsideDialog", opened)
    select(frame, "Paid")
    frame.on_ask_aside()
    assert "damaged" in env["boxes"][-1]


def test_remote_control_is_never_on_in_an_api_chat(frame, env, monkeypatch):
    from thechatplace.ui.main_frame import REMOTE_NOT_ON_API
    add_key()
    asked(frame, monkeypatch)
    frame.speech.remote_control = True         # on by default in Settings
    frame.store.update("api-1", remote_control="on")
    frame.store.update("own-1", remote_control="on")
    open_chat(frame, "api-1")
    frame.reply_text.SetValue("Paid")
    frame.on_send()
    assert FakeRunner.instances[-1].remote_control is None
    open_chat(frame, "own-1")
    frame.reply_text.SetValue("Free")
    frame.on_send()
    assert FakeRunner.instances[-1].remote_control is not None   # the subscription's as before
    # Session, Remote Control says why instead of offering it.
    chosen = []
    monkeypatch.setattr(frame, "_choose", lambda *a, **k: chosen.append(a) or None)
    select(frame, "Paid")
    frame.on_remote_control()
    assert env["boxes"][-1] == REMOTE_NOT_ON_API.format(title="Paid")
    assert "isn't available in chats on API credits" in env["boxes"][-1]
    assert chosen == []


def test_a_subscription_turn_keeps_its_chats_cost_baseline(frame, monkeypatch):
    open_chat(frame, "own-1")
    frame.reply_text.SetValue("Free")
    frame.on_send()
    runner = FakeRunner.instances[-1]
    assert runner.billing == "subscription" and runner.ledger is None
    assert runner.cost_baselines is spend_ledger.default_ledger()


def test_cancel_at_the_question_keeps_the_message(frame, env, monkeypatch):
    add_key()
    asked(frame, monkeypatch, answer=False)
    open_chat(frame, "api-1")
    frame.reply_text.SetValue("Not yet")
    frame.on_send()
    assert frame.reply_text.GetValue() == "Not yet"
    assert FakeRunner.instances == []
    assert "Not sent." in env["feedback"]


def test_the_question_can_be_turned_off(frame, monkeypatch):
    add_key()
    titles = asked(frame, monkeypatch)
    frame.speech.api_confirm_first = False
    open_chat(frame, "api-1")
    frame.reply_text.SetValue("Go")
    frame.on_send()
    assert titles == [] and len(FakeRunner.instances) == 1


def test_a_subscription_chat_is_never_asked_or_charged(frame, env, monkeypatch):
    add_key()
    titles = asked(frame, monkeypatch)
    open_chat(frame, "own-1")
    frame.reply_text.SetValue("Free")
    frame.on_send()
    runner = FakeRunner.instances[-1]
    assert runner.billing == "subscription" and runner.ledger is None
    assert titles == [] and "Sent on API credits." not in env["feedback"]


def test_a_reply_on_api_credits_says_its_cost(frame, env, monkeypatch):
    add_key()
    asked(frame, monkeypatch)
    open_chat(frame, "api-1")
    frame.reply_text.SetValue("Hi")
    frame.on_send()
    frame._on_turn_event({"id": "api-1"}, "Paid", TurnEvent(
        "finished", text="Done.", data={"billing": "api", "cost_usd": 0.06}))
    assert "Paid replied, 6 cents. Done." in env["spoken"]
    # Never "0 cents".
    frame.reply_text.SetValue("Again")
    frame.on_send()
    frame._on_turn_event({"id": "api-1"}, "Paid", TurnEvent(
        "finished", text="Done.", data={"billing": "api", "cost_usd": 0.0}))
    assert "Paid replied, less than a cent. Done." in env["spoken"]


def test_the_cost_can_be_turned_off(frame, env, monkeypatch):
    add_key()
    asked(frame, monkeypatch)
    frame.speech.api_say_cost = False
    open_chat(frame, "api-1")
    frame.reply_text.SetValue("Hi")
    frame.on_send()
    frame._on_turn_event({"id": "api-1"}, "Paid", TurnEvent(
        "finished", text="Done.", data={"billing": "api", "cost_usd": 0.06}))
    assert "Paid replied. Done." in env["spoken"]


# -- making chats on API credits --------------------------------------------------------------


def test_new_session_offers_charge_to_only_with_a_key(frame, folder):
    from thechatplace.ui.dialogs import NewSessionDialog
    dialog = NewSessionDialog(frame, folder)
    try:
        assert not dialog.charge.IsShown() and not dialog.charge.IsEnabled()
        assert dialog.billing() == "subscription"
    finally:
        dialog.Destroy()
    dialog = NewSessionDialog(frame, folder, api_key=True)
    try:
        assert dialog.charge.IsShown() and dialog.charge.IsEnabled()
        assert dialog.charge.GetSelection() == 0  # the subscription, always chosen first
        assert dialog.charge.GetName() == "Charge to"
        assert dialog.model.GetCount() == len(MODELS)
        assert "Fable" not in dialog.model.GetStrings()
        dialog.charge.SetSelection(1)
        dialog._on_charge()
        assert dialog.billing() == "api"
        assert dialog.model.GetStrings() == [label for _v, label in API_MODELS]
        dialog.model.SetSelection(dialog.model.GetStrings().index("Fable"))
        dialog.message.SetValue("Go")
        assert dialog.values()[4] == "fable"
        assert dialog.note.GetValue().startswith("Every message in this chat is charged")
        # Back to the subscription: Fable goes, and the default is chosen.
        dialog.charge.SetSelection(0)
        dialog._on_charge()
        assert "Fable" not in dialog.model.GetStrings()
        assert dialog.values()[4] == ""
        order = list(dialog.GetChildren())
        assert order.index(dialog.title_text) < order.index(dialog.charge) < \
            order.index(dialog.model)
    finally:
        dialog.Destroy()


def test_charge_to_is_claude_only(frame, folder):
    from thechatplace.ui.dialogs import NewSessionDialog
    dialog = NewSessionDialog(frame, folder, providers=("claude", "codex"), provider="claude",
                              api_key=True)
    try:
        dialog.charge.SetSelection(1)
        dialog._on_charge()
        dialog.provider_choice.SetSelection(1)
        dialog._on_provider()
        assert not dialog.charge.IsShown() and not dialog.charge.IsEnabled()
        assert dialog.billing() == "subscription"
    finally:
        dialog.Destroy()


class _NewChatDialog:
    """New Session as if API credits were chosen and Start pressed."""

    def __init__(self, parent, folder, **options):
        _NewChatDialog.options = options
        self._folder = folder

    def ShowModal(self):
        return wx.ID_OK

    def Destroy(self):
        pass

    def values(self):
        return self._folder, "Fresh paid", "auto", "Plan it", "fable"

    def effort(self):
        return ""

    def provider(self):
        return "claude"

    def billing(self):
        return "api"


def test_new_session_on_api_credits_makes_an_api_chat(frame, folder, monkeypatch):
    from thechatplace.ui import main_frame
    add_key()
    titles = asked(frame, monkeypatch)
    monkeypatch.setattr(main_frame, "NewSessionDialog",
                        lambda parent, root, **options: _NewChatDialog(parent, folder, **options))
    frame.on_new_session()
    assert _NewChatDialog.options["api_key"] is True
    made = next(s for s in frame.store.all() if s.title == "Fresh paid")
    assert made.billing == "api" and made.model == "fable"
    runner = FakeRunner.instances[-1]
    assert runner.billing == "api" and runner.chat_id == made.cli_session_id
    assert titles == ["Fresh paid"]


def test_continue_on_api_credits_makes_a_new_chat_and_leaves_the_old(frame, env, monkeypatch):
    questions = []
    monkeypatch.setattr(frame, "_confirm_continue_on_api",
                        lambda title: questions.append(title) or True)
    select(frame, "Mine")
    frame.on_continue_on_api_credits()
    assert env["feedback"][-1] == ("There's no Claude API key. Add one with Tools, API Keys, "
                                   "then try again.")
    add_key()
    select(frame, "Paid")
    frame.on_continue_on_api_credits()
    assert env["feedback"][-1] == "Paid is already on API credits."
    before = len(frame.store.all())
    select(frame, "Mine")
    frame.on_continue_on_api_credits()
    assert questions == ["Mine"]
    assert len(frame.store.all()) == before + 1
    # Named as Continue Here would; the row and title add "API credits".
    made = next(s for s in frame.store.all() if s.title == "Mine (continued)")
    assert made.billing == "api" and made.fork_source == "own-1" and not made.started
    assert frame.store.get("own-1").billing == "subscription"  # untouched
    assert frame._open.cli_session_id == made.cli_session_id
    assert FakeRunner.instances == []  # nothing is sent until you send


def test_a_branch_of_an_api_chat_is_an_api_chat_and_keeps_fable(frame, env, monkeypatch):
    add_key()
    titles = asked(frame, monkeypatch)
    frame.store.update("api-1", model="fable")
    branch = frame._make_branch(frame.store.get("api-1").to_info(), "m1", "Paid (branch)", "")
    assert branch.billing == "api" and branch.model == "fable"
    assert branch.fork_source == "api-1" and frame.store.get(branch.cli_session_id) is not None
    # Its first send goes the API way: asked, charged and said.
    frame.reply_text.SetValue("Carry on from here")
    frame.on_send()
    runner = FakeRunner.instances[-1]
    assert (runner.billing, runner.chat_id) == ("api", branch.cli_session_id)
    assert "--fork-session" in runner.command
    assert titles == ["Paid (branch)"] and "Sent on API credits." in env["feedback"]


def test_a_branch_of_a_subscription_chat_stays_on_it(frame):
    branch = frame._make_branch(frame.store.get("own-1").to_info(), "m1", "Mine (branch)", "")
    assert branch.billing == "subscription" and branch.model == ""
    retry = frame._make_branch(frame.store.get("own-1").to_info(), "", "Mine (retry)", "Hi")
    assert retry.billing == "subscription"


def test_continue_on_api_credits_cancelled_makes_nothing(frame, env, monkeypatch):
    add_key()
    monkeypatch.setattr(frame, "_confirm_continue_on_api", lambda title: False)
    before = len(frame.store.all())
    select(frame, "Mine")
    frame.on_continue_on_api_credits()
    assert len(frame.store.all()) == before
    assert env["feedback"][-1] == "No new chat was made."


# -- Usage and Settings -----------------------------------------------------------------------


def test_usage_has_an_api_credits_section(frame, monkeypatch):
    from thechatplace.ui import main_frame
    shown = []

    class Usage:
        def __init__(self, parent, lines, copy):
            shown.append(lines)

        def ShowModal(self):
            return wx.ID_OK

        def Destroy(self):
            pass
    monkeypatch.setattr(main_frame, "UsageDialog", Usage)
    open_chat(frame, "own-1")
    frame.on_usage()
    assert not any(line.startswith("API credits") for line in shown[-1])  # no key, nothing spent
    add_key()
    open_chat(frame, "api-1")
    frame.on_usage()
    lines = shown[-1]
    assert lines[1] == usage.PLAN_LIMITS_NOT_FOR_API
    assert "API credits today: nothing" in lines
    assert "API credits left of your $190.00 limit: $190.00" in lines


def test_settings_show_the_api_credit_choices(frame):
    from thechatplace.speech import default_options
    from thechatplace.ui.dialogs import SettingsDialog
    dialog = SettingsDialog(frame, frame.speech, default_options())
    try:
        assert dialog.api_confirm.GetValue() and dialog.api_cost.GetValue()
        # No sound for it yet, so no choice of one.
        assert dialog.api_notice is None
        dialog.api_confirm.SetValue(False)
        dialog.api_cost.SetValue(False)
        chosen = dialog.get_settings()
        assert (chosen.api_confirm_first, chosen.api_say_cost) == (False, False)
        assert chosen.api_send_notice == "speech"
    finally:
        dialog.Destroy()


def test_an_aside_in_an_api_chat_needs_the_key(frame, env, monkeypatch):
    from thechatplace.ui import claude_extras

    def opened(*args, **kwargs):
        pytest.fail("Ask Aside opened without a key")
    monkeypatch.setattr(claude_extras, "AskAsideDialog", opened)
    select(frame, "Paid")
    frame.on_ask_aside()
    assert env["boxes"][-1].startswith("This chat is charged to API credits")
