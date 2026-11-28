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
    original_action = window.sessions.owned_action
    api_connection = object()
    def owned_action(action, record, *args, **kwargs):
        if action == 'api_billing':
            return record.provider_options.get('model', 'gpt-5-nano'), api_connection
        return original_action(action, record, *args, **kwargs)
    monkeypatch.setattr(window.sessions, 'owned_action', owned_action)
    yield window
    fake_env.close_frame(window)


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


def written(frame, session_id="api-1", title="Paid", kind="prompt"):
    """The runner wrote a message on API credits (#663): its "api_sent"."""
    frame._on_turn_event({"id": session_id}, title, TurnEvent("api_sent", data={"kind": kind}))


#: Said alone, and what's said again after an interruption (#663).
SENT_PAID = "Sent on API credits to Paid."


def test_codex_charge_to_uses_openai_key_priced_models_and_human_modes(frame, folder):
    from thechatplace.ui.dialogs import NewSessionDialog
    from thechatplace.openai_prices import model_choices
    dialog = NewSessionDialog(frame, folder, providers=('claude', 'codex'), provider='codex', openai_key=True)
    try:
        assert dialog.charge.IsShown() and dialog.billing() == 'subscription'
        assert dialog.charge.GetString(0) == 'ChatGPT subscription'
        dialog.charge.SetSelection(1)
        dialog._on_charge()
        assert dialog.billing() == 'api'
        assert dialog.model.GetStrings() == [m.label for m in model_choices()]
        assert dialog.values()[-1] == 'gpt-5-nano'
        assert dialog.mode.GetCount() == 2
        assert 'estimated' in dialog.note.GetValue()
        dialog.provider_choice.SetSelection(0)
        dialog._on_provider()
        assert dialog.billing() == 'subscription' and not dialog.charge.IsShown()
    finally:
        dialog.Destroy()


def test_codex_api_confirmation_and_sent_event_use_own_identity(frame, folder, monkeypatch, env):
    record = OwnSession('openai-1', 'OpenAI paid', folder, provider='codex-api', billing='api')
    frame.store.add(record)
    platform_paths.save_secret(platform_paths.OPENAI_API_KEY_SECRET, 'sk-proj-'+'0'*30)
    calls = []
    monkeypatch.setattr(frame, '_confirm_api_send', lambda title, provider: calls.append((title, provider)) or True)
    key = record.to_info().key
    assert frame._api_send_problem(key) is None
    assert calls == [('OpenAI paid', 'codex-api')]
    assert key not in frame._api_confirmed
    frame._on_codex_api_event(TurnEvent('api_sent', session_id=record.cli_session_id, data={'billing': 'api'}))
    assert key in frame._api_confirmed
    assert frame._api_send_problem(key) is None and len(calls) == 1


def test_codex_api_unknown_cost_is_not_zero_in_status_and_properties(frame, folder):
    from thechatplace import properties, openai_billing
    record = OwnSession('unknown-openai', 'Unknown cost', folder, provider='codex-api', billing='api')
    frame.store.add(record)
    key = record.to_info().key
    ledger = spend_ledger.default_ledger('openai')
    reservation = ledger.reserve(key)
    assert frame._api_spend_line(key) == 'API: priced $0.00'
    ledger.close(reservation, complete=False)
    assert frame._api_spend_line(key) == 'API: priced cost not known'
    ledger.release_held()
    assert frame._api_spend_line(key) == 'API: priced cost not known'
    words = openai_billing.chat_cost_words(ledger, key)
    lines = properties.session_properties(record.to_info(), own=record, api_cost=words)
    assert 'Spending on API credits by this chat: cost not known' in lines
    assert frame._is_api_chat(record.to_info())


def sent_said(said):
    """What was said of a message going on API credits into Paid: its
    read-back with "on API credits" in it, or the notice alone (#663)."""
    return [text for text in said if text.startswith("Sent on API credits to Paid")]


def ended(frame, session_id="api-1", title="Paid", kind="finished", text="Done.", **data):
    """The turn's last event, as an API turn's runner gives it."""
    frame._on_turn_event({"id": session_id}, title, TurnEvent(
        kind, text=text, is_error=kind == "failed",
        data={"billing": "api", "sent": True, "cost_complete": True, **data}))


# -- always knowing ---------------------------------------------------------------------------


def test_the_row_title_and_reply_box_say_api_credits(frame):
    rows = [frame.session_list.GetString(i) for i in range(frame.session_list.GetCount())]
    assert any(row.startswith("Paid, Claude, Claude API credits, ") for row in rows)
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
    assert part.GetLabel() == "API credits: this chat $0.42, The Chat Place $0.42 of $20"
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
    # Said once the runner has written it, not as Send is pressed (#663):
    # the read-back waits for it, and says it, so it's said once.
    assert sent_said(env["feedback"]) == []
    assert not any(text.startswith("Sent to Paid") for text in env["feedback"])
    # Until then the status bar says it's going, unspoken (#663).
    assert frame._status_latest == "Sending on API credits…"
    assert "Sending on API credits…" not in env["feedback"] + env["spoken"]
    written(frame)
    said = sent_said(env["feedback"])
    assert len(said) == 1 and said[0].startswith("Sent on API credits to Paid: First")
    assert said[0] in env["kept"]                     # an interruption can't lose it
    assert (said[0], SENT_PAID) in env["again"]       # said again in short
    assert (said[0], "api-1") in env["about"]         # kept under the chat
    # The turn ends, which settles it; the next send isn't asked again this run.
    frame._on_turn_event({"id": "api-1"}, "Paid", TurnEvent("finished", text="ok"))
    assert "api-1" in env["forgotten"]
    # A reply with no cost (the setting's off here: no billing data) doesn't
    # say the send was charged, so it doesn't settle the notice (#663).
    assert not [text for text, about in env["about"] if about == "api-1"
                and text.startswith("Paid")]
    env["feedback"].clear()
    frame.reply_text.SetValue("Second")
    frame.on_send()
    assert titles == ["Paid"]
    assert len(FakeRunner.instances) == 2
    written(frame)
    assert sent_said(env["feedback"])[0].startswith("Sent on API credits to Paid: Second")


def test_the_first_send_is_asked_again_if_nothing_went(frame, env, monkeypatch):
    # Answered "Send", but the turn ended without writing the message (a
    # refused account, say): it counts only once a message went (#663).
    add_key()
    titles = asked(frame, monkeypatch)
    open_chat(frame, "api-1")
    frame.reply_text.SetValue("First")
    frame.on_send()
    # Queued behind it while it goes: not asked again meanwhile.
    frame.reply_text.SetValue("Queued")
    frame.on_send()
    assert titles == ["Paid"]
    ended(frame, kind="failed", text="Claude Code would have used another API key.",
          cost_usd=0.0, sent=False)
    frame.reply_text.SetValue("Again")
    frame.on_send()
    assert titles == ["Paid", "Paid"]
    assert sent_said(env["feedback"]) == []          # the held read-back never said


def test_sent_on_api_credits_is_said_even_with_announcements_silent(frame, env, monkeypatch):
    from thechatplace.speech import ANNOUNCE_SILENT
    add_key()
    asked(frame, monkeypatch)
    frame.speech.announce = ANNOUNCE_SILENT
    open_chat(frame, "api-1")
    frame.reply_text.SetValue("Quietly")
    frame.on_send()
    assert len(FakeRunner.instances) == 1
    written(frame)
    # Said, queued behind other speech rather than interrupting it, and kept;
    # Silent reads nothing back, so it's the notice alone.
    assert sent_said(env["feedback"]) == [SENT_PAID]
    assert SENT_PAID in env["kept"]
    assert sent_said(env["spoken"]) == []
    # Repeat Last Announcement isn't taken over by it.
    assert getattr(frame, "_last_announcement", "") != SENT_PAID


@pytest.mark.parametrize("kind", ["steer", "now", "follow_up"])
def test_each_way_into_a_running_turn_is_said_when_written(frame, env, monkeypatch, kind):
    # Steer, Send Now and a follow-up all go through the runner, which says
    # "api_sent" for each message it writes; nothing is said at the key.
    add_key()
    asked(frame, monkeypatch)
    open_chat(frame, "api-1")
    frame.reply_text.SetValue("First")
    frame.on_send()
    written(frame)
    env["feedback"].clear()
    runner = FakeRunner.instances[-1]
    if kind == "follow_up":
        runner.waiting_on_background = True
        frame.reply_text.SetValue("More")
        frame.on_send()
        assert runner.follow_ups
    elif kind == "steer":
        monkeypatch.setattr(frame, "_history_unknown", lambda: False)
        frame.reply_text.SetValue("Also this")
        frame.on_steer()
        assert runner.steered
    else:
        frame._queued["api-1"] = ["Now this"]
        frame._rebuild_chat_list()
        monkeypatch.setattr(frame, "_selected_queued", lambda: 0)
        frame.send_queued_now()
        assert runner.sent_now
    written(frame, kind=kind)
    said = sent_said(env["feedback"])
    assert len(said) == 1
    if kind == "follow_up":
        # A send with a read-back: it waited for this, and says it (#663).
        assert said[0].startswith("Sent on API credits to Paid: More")
    else:
        # Steer and Send Now say their own words at the key; the notice,
        # alone, names the chat.
        assert said == [SENT_PAID]


def test_a_queued_message_after_background_work_needs_the_key_still(frame, env, monkeypatch):
    # _answered_with_background sends what's queued on its own (#161); on
    # API credits it's checked as at a turn's end (#663).
    add_key()
    asked(frame, monkeypatch)
    open_chat(frame, "api-1")
    frame.reply_text.SetValue("Start the build")
    frame.on_send()
    runner = FakeRunner.instances[-1]
    frame._queued["api-1"] = ["Then this"]
    platform_paths.delete_secret(platform_paths.CLAUDE_API_KEY_SECRET)
    runner.waiting_on_background = True
    frame._on_turn_event({"id": "api-1"}, "Paid", TurnEvent(
        "waiting", text="Started it.", data={"tasks": ["the build"], "billing": "api",
                                             "cost_usd": 0.02, "sent": True,
                                             "cost_complete": True}))
    assert not getattr(runner, "follow_ups", [])
    assert frame._queued["api-1"] == ["Then this"]          # waits; the turn's end gives it back
    add_key()
    runner.waiting_on_background = True
    frame._on_turn_event({"id": "api-1"}, "Paid", TurnEvent(
        "waiting", text="Still going.", data={"tasks": ["the build"], "billing": "api",
                                              "cost_usd": 0.03, "sent": True,
                                              "cost_complete": True}))
    assert runner.follow_ups == [("Then this", None)]


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
    assert sent_said(env["feedback"]) == []


def test_a_damaged_tally_stops_an_aside_too(frame, env, monkeypatch):
    from thechatplace.ui import claude_extras
    add_key()
    asked(frame, monkeypatch)
    ledger = spend_ledger.default_ledger()
    ledger.folder.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text("not a ledger line\n", encoding="utf-8")

    def opened(*args, **kwargs):
        pytest.fail("Ask Aside opened on a damaged total")
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
    # Loaded, not just highlighted: a hidden window's focus isn't on the list
    # on a Mac, so the command means the loaded chat there.
    open_chat(frame, "api-1")
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
    assert titles == [] and not any("on API credits" in text for text in env["feedback"])


def test_a_reply_on_api_credits_says_its_cost(frame, env, monkeypatch):
    add_key()
    asked(frame, monkeypatch)
    open_chat(frame, "api-1")
    frame.reply_text.SetValue("Hi")
    frame.on_send()
    frame._on_turn_event({"id": "api-1"}, "Paid", TurnEvent(
        "finished", text="Done.", data={"billing": "api", "cost_usd": 0.06}))
    assert "Paid replied, 6 cents. Done." in env["spoken"]
    # Never "0 cents": nothing is "at no cost" (#663).
    frame.reply_text.SetValue("Again")
    frame.on_send()
    frame._on_turn_event({"id": "api-1"}, "Paid", TurnEvent(
        "finished", text="Done.", data={"billing": "api", "cost_usd": 0.0}))
    assert "Paid replied at no cost. Done." in env["spoken"]
    frame.reply_text.SetValue("Third")
    frame.on_send()
    frame._on_turn_event({"id": "api-1"}, "Paid", TurnEvent(
        "finished", text="Done.", data={"billing": "api", "cost_usd": 0.004}))
    assert "Paid replied, less than a cent. Done." in env["spoken"]


def _send(frame, text="Hi"):
    frame.reply_text.SetValue(text)
    frame.on_send()


def test_stop_and_failures_say_what_they_cost(frame, env, monkeypatch):
    add_key()
    asked(frame, monkeypatch)
    open_chat(frame, "api-1")
    _send(frame)
    ended(frame, kind="failed", text="Stopped.", cost_usd=0.02)
    assert env["spoken"][-1] == "Paid: the turn failed. Stopped. It cost 2 cents."
    _send(frame)
    ended(frame, kind="failed", text="Stopped.", cost_usd=0.02, cost_complete=False)
    assert env["spoken"][-1].endswith("It cost at least 2 cents.")
    _send(frame)
    ended(frame, kind="failed", text="Claude exited.", cost_usd=None)
    assert env["spoken"][-1].endswith("Claude exited. Cost not known.")
    _send(frame)
    ended(frame, text="Done.", cost_usd=None, cost_complete=False)
    assert env["spoken"][-1] == "Paid replied, cost not known. Done."
    # Nothing went: no cost to speak of.
    _send(frame)
    ended(frame, kind="failed", text="Nothing was sent.", cost_usd=0.0, sent=False)
    assert "It cost" not in env["spoken"][-1]
    # Said once, not twice, when the words already say it.
    _send(frame)
    ended(frame, kind="failed", text="Claude stopped at your per-message limit. It cost $2.03, "
          "as a message can run over the limit by one request.", cost_usd=2.03)
    assert env["spoken"][-1].count("It cost") == 1


def test_a_slash_command_says_its_cost_only_when_it_cost_something(frame, env, monkeypatch):
    # The review (#663): every local command said "Paid: it cost nothing.",
    # even at Silent.
    add_key()
    asked(frame, monkeypatch)
    open_chat(frame, "api-1")
    _send(frame, "/context")
    ended(frame, text="Context: 12,000 tokens.", cost_usd=0.0)
    said = env["spoken"] + env["feedback"]
    assert not any("cost" in text.lower() and text.startswith("Paid") for text in said)
    _send(frame, "/compact-notes")
    ended(frame, text="Summarized.", cost_usd=0.02)
    assert "Paid: it cost 2 cents." in env["feedback"]


def test_a_reply_already_said_still_says_its_cost(frame, env, monkeypatch):
    # The turn's text was said already (a command's output, say), but its
    # cost wasn't (#663).
    add_key()
    asked(frame, monkeypatch)
    open_chat(frame, "api-1")
    _send(frame)
    frame._command_speech_state("api-1")["spoken"].append("Done.")
    ended(frame, text="Done.", cost_usd=0.03)
    # Queued, so it doesn't cut off the text being read (#663).
    assert env["feedback"][-1] == "Paid finished, 3 cents."
    assert "Paid finished, 3 cents." not in env["spoken"]


def test_background_work_says_the_cost_then_only_whats_more(frame, env, monkeypatch):
    add_key()
    asked(frame, monkeypatch)
    open_chat(frame, "api-1")
    _send(frame)
    frame._on_turn_event({"id": "api-1"}, "Paid", TurnEvent(
        "waiting", text="Started the build.", data={
            "tasks": ["the build"], "billing": "api", "cost_usd": 0.02, "sent": True,
            "cost_complete": True}))
    assert env["spoken"][-1].startswith("Paid replied, 2 cents. Started the build.")
    # Claude carried on after the work: only what that cost.
    ended(frame, text="The build passed.", cost_usd=0.05)
    assert env["spoken"][-1] == "Paid replied, 3 cents more. The build passed."
    # What came after isn't known: "cost not known", never "… more" (#663).
    _send(frame)
    frame._on_turn_event({"id": "api-1"}, "Paid", TurnEvent(
        "waiting", text="Started.", data={"tasks": ["x"], "billing": "api", "cost_usd": 0.02,
                                          "sent": True, "cost_complete": True}))
    ended(frame, text="Stopped it.", cost_usd=0.02, cost_complete=False)
    assert env["spoken"][-1] == "Paid replied, cost not known. Stopped it."
    # Nothing more after the work: nothing more said about money.
    _send(frame)
    frame._on_turn_event({"id": "api-1"}, "Paid", TurnEvent(
        "waiting", text="Started.", data={"tasks": ["x"], "billing": "api", "cost_usd": 0.01,
                                          "sent": True, "cost_complete": True}))
    count = len(env["spoken"]) + len(env["feedback"])
    ended(frame, text="", cost_usd=0.01, after_background=True)
    assert len(env["spoken"]) + len(env["feedback"]) == count


def test_with_announcements_silent_the_cost_is_still_said(frame, env, monkeypatch):
    from thechatplace.speech import ANNOUNCE_SILENT
    add_key()
    asked(frame, monkeypatch)
    frame.speech.announce = ANNOUNCE_SILENT
    open_chat(frame, "api-1")
    _send(frame)
    ended(frame, text="Done.", cost_usd=0.06)
    assert env["feedback"][-1] == "Paid finished, 6 cents."
    assert not any("Done." in said for said in env["spoken"])
    _send(frame)
    ended(frame, kind="failed", text="Stopped.", cost_usd=0.01)
    assert env["feedback"][-1] == "Paid: it cost 1 cent."       # which chat, too (#663)
    _send(frame)
    ended(frame, kind="failed", text="Stopped.", cost_usd=None)
    assert env["feedback"][-1] == "Paid: cost not known."
    # Off in Settings: never.
    frame.speech.api_say_cost = False
    _send(frame)
    before = list(env["feedback"])
    ended(frame, text="Done.", cost_usd=0.06)
    assert env["feedback"] == before


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
    written(frame, branch.cli_session_id, "Paid (branch)")
    assert titles == ["Paid (branch)"]
    assert any(text.startswith("Sent on API credits to Paid (branch): Carry on")
               for text in env["feedback"])


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
    assert "API credits left of your $20.00 limit: $20.00" in lines
    # A chat on the subscription: one line about API credits, before the
    # plan's limits, so the last line (End) is still a plan limit.
    frame._limits = {"status": "allowed", "unifiedWindows": {
        "five_hour": {"utilization": 0.08}, "seven_day": {"utilization": 0.34}}}
    expected_plan = usage.limit_lines(frame._limits)
    assert expected_plan[-1] == "Weekly limit 34% used."
    open_chat(frame, "own-1")
    frame.on_usage()
    lines = shown[-1]
    api = [line for line in lines if line.startswith("API credits")]
    assert api == ["API credits: nothing spent since the total was reset, $20.00 left of "
                   "your $20.00 limit. For today, the last 7 days and spending by model and by chat, open Usage and Context from a chat that uses API credits."]
    assert lines[-len(expected_plan):] == expected_plan
    assert lines.index(api[0]) == len(lines) - len(expected_plan) - 1
    # With the weekly Fable limit kept from a Fable turn (#722), it's one of
    # the plan's lines: the API line still comes just before them.
    import time as _time
    frame._fable_limits = {"status": "allowed", "unifiedWindows": {
        "seven_day_overage_included": {"utilization": 0.25,
                                       "resetsAt": _time.time() + 86400}}}
    frame.on_usage()
    lines = shown[-1]
    assert any(line.startswith("Weekly Fable limit 25% used") for line in lines[-3:])
    assert lines.index(api[0]) == len(lines) - len(expected_plan) - 2


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


class _ApiAside:
    """AsideRunner for a chat on API credits: takes the billing kwargs."""
    instances = []

    def __init__(self, command, cwd, question, on_event, **billing):
        self.command, self.question, self.on_event, self.billing = command, question, on_event, \
            billing
        self.cancelled = False
        _ApiAside.instances.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


def test_an_aside_says_sent_when_written_and_its_cost_from_its_window(frame, env, monkeypatch):
    from thechatplace.aside import AsideEvent
    from thechatplace.ui import claude_extras, main_frame
    add_key()
    titles = asked(frame, monkeypatch)
    _ApiAside.instances = []
    monkeypatch.setattr(main_frame, "AsideRunner", _ApiAside)
    windows = []
    real = frame._speak_announcement

    def speak(text, interrupt, window=None, keep=False, about="", again="", resay=True):
        windows.append((text, window))
        real(text, interrupt, window=window, keep=keep, about=about, again=again, resay=resay)
    monkeypatch.setattr(frame, "_speak_announcement", speak)

    def show(dialog):
        dialog.question.SetValue("What's the code word?")
        dialog.on_ask()
        assert pump(lambda: _ApiAside.instances)
        runner = _ApiAside.instances[-1]
        assert runner.billing["billing"] == "api"
        assert SENT_PAID not in env["feedback"]                 # not as Ask is pressed
        runner.on_event(AsideEvent("sent"))
        assert pump(lambda: SENT_PAID in env["feedback"])       # names the chat (#663)
        assert (SENT_PAID, dialog) in windows                   # from the dialog, for VoiceOver
        # Kept under the aside's own key, not the chat's turn's (#663).
        assert (SENT_PAID, "aside:api-1") in env["about"]
        runner.on_event(AsideEvent("done", "ZEPHYR-42", cost_usd=0.03, sent=True))
        assert pump(lambda: not dialog.asking)
        assert env["spoken"][-1] == "Answer, 3 cents. ZEPHYR-42"
        assert (env["spoken"][-1], dialog) in windows
        # With its cost, the answer settles the notice, and the aside's end forgets it.
        assert (env["spoken"][-1], "aside:api-1") in env["about"]
        assert "aside:api-1" in env["forgotten"] and "api-1" not in env["forgotten"]
        return wx.ID_CANCEL
    monkeypatch.setattr(claude_extras.AskAsideDialog, "ShowModal", show)
    select(frame, "Paid")
    frame.on_ask_aside()
    assert titles == ["Paid"] and "api-1" in frame._api_confirmed


def test_a_stopped_aside_says_what_it_cost(frame, env, monkeypatch):
    from thechatplace.aside import AsideEvent
    from thechatplace.ui import claude_extras, main_frame
    add_key()
    asked(frame, monkeypatch)
    _ApiAside.instances = []
    monkeypatch.setattr(main_frame, "AsideRunner", _ApiAside)

    def show(dialog):
        dialog.question.SetValue("Q?")
        dialog.on_ask()
        assert pump(lambda: _ApiAside.instances)
        _ApiAside.instances[-1].on_event(AsideEvent("failed", "Stopped.", cost_usd=0.01,
                                                    sent=True, cost_complete=False))
        assert pump(lambda: not dialog.asking)
        assert env["feedback"][-1] == "Stopped asking. It cost at least 1 cent."
        # Stopped: its "Sent on API credits" is forgotten, not owed for hours (#663).
        assert "aside:api-1" in env["forgotten"]
        return wx.ID_CANCEL
    monkeypatch.setattr(claude_extras.AskAsideDialog, "ShowModal", show)
    select(frame, "Paid")
    frame.on_ask_aside()


def test_api_keys_speaks_from_its_own_window(frame, env, monkeypatch):
    # VoiceOver drops what comes from the window behind a dialog (#663).
    from thechatplace.ui import api_keys_dialog
    windows = []
    monkeypatch.setattr(frame, "_speak_announcement",
                        lambda text, interrupt, window=None, **kwargs: windows.append(
                            (text, window)))

    class Dialog:
        def __init__(self, parent, speak=None, **kwargs):
            Dialog.made = self
            self.speak = speak

        def ShowModal(self):
            self.speak("Key saved.")
            return wx.ID_CANCEL

        def Destroy(self):
            pass
    monkeypatch.setattr(api_keys_dialog, "ApiKeysDialog", Dialog)
    frame.on_api_keys()
    assert windows == [("Key saved.", Dialog.made)]


def test_a_renamed_chat_takes_its_spending_with_it(frame, env, monkeypatch):
    # Claude Code reported another id than the one asked for (#663).
    add_key()
    asked(frame, monkeypatch)
    ledger = spend_ledger.default_ledger()
    held = ledger.reserve("api-1")
    ledger.settle(held, 0.25)
    ledger.close(held, complete=True)
    ledger.set_baseline("api-1", 0.25)
    open_chat(frame, "api-1")
    _send(frame)
    written(frame)
    frame._on_turn_event({"id": "api-1"}, "Paid", TurnEvent("started", session_id="api-2",
                                                             data={"model": ""}))
    assert frame.store.get("api-2").billing == "api"
    totals = ledger.totals()
    assert totals.chat_all_time == {"api-2": pytest.approx(0.25)}
    assert ledger.recorded_baseline("api-2")[0] == pytest.approx(0.25)
    assert "api-2" in frame._api_confirmed


# -- what settles "Sent on API credits" (#663) ------------------------------------------------


def _sent_into_paid(frame, monkeypatch):
    add_key()
    asked(frame, monkeypatch)
    open_chat(frame, "api-1")
    frame.reply_text.SetValue("First")
    frame.on_send()
    written(frame)
    return FakeRunner.instances[-1]


def test_the_chats_needs_you_settles_its_notice(frame, env, monkeypatch):
    # "Paid needs you … Sent on API credits to Paid." sounded as if the
    # answer were charged: the needs-you is the chat's own and settles it.
    from thechatplace.claude_cli import PermissionRequest
    _sent_into_paid(frame, monkeypatch)
    request = PermissionRequest("r1", "Bash", {"command": "ls"})
    frame._on_turn_event({"id": "api-1"}, "Paid",
                         TurnEvent("permission", text=request.summary(), request=request))
    needs = [text for text in env["spoken"] if text.startswith("Paid needs you")]
    assert needs and (needs[-1], "api-1") in env["about"]


def test_a_reply_settles_the_notice_only_with_its_cost(frame, env, monkeypatch):
    _sent_into_paid(frame, monkeypatch)
    ended(frame, cost_usd=0.03)
    reply = env["spoken"][-1]
    assert "3 cents" in reply and (reply, "api-1") in env["about"]
    env["about"].clear()
    frame.speech.api_say_cost = False
    frame.reply_text.SetValue("Second")
    frame.on_send()
    written(frame)
    ended(frame, cost_usd=0.03)
    reply = env["spoken"][-1]
    # No cost in it, so not about the chat: the notice is said once more
    # after it if it cut it off; the turn's end then forgets it.
    assert "cent" not in reply and not [t for t, a in env["about"] if t == reply]
    assert env["forgotten"][-1] == "api-1"


def test_repeat_last_announcement_doesnt_bring_the_notice_back(frame, env, monkeypatch):
    _sent_into_paid(frame, monkeypatch)
    frame._say("Mine finished.")
    frame._command_handlers()["repeat_last_announcement"](None)
    assert env["no_resay"] == ["Mine finished."]


def test_an_aside_closed_while_answering_forgets_its_notice(frame, env, monkeypatch):
    from thechatplace.aside import AsideEvent
    from thechatplace.ui import claude_extras, main_frame
    add_key()
    asked(frame, monkeypatch)
    _ApiAside.instances = []
    monkeypatch.setattr(main_frame, "AsideRunner", _ApiAside)

    def show(dialog):
        dialog.question.SetValue("Q?")
        dialog.on_ask()
        assert pump(lambda: _ApiAside.instances)
        _ApiAside.instances[-1].on_event(AsideEvent("sent"))
        assert pump(lambda: SENT_PAID in env["feedback"])
        assert "aside:api-1" not in env["forgotten"]
        return wx.ID_CANCEL                           # closed with no answer yet
    monkeypatch.setattr(claude_extras.AskAsideDialog, "ShowModal", show)
    select(frame, "Paid")
    frame.on_ask_aside()
    assert "aside:api-1" in env["forgotten"]


def test_a_send_now_not_taken_in_comes_back_on_stop(frame, env, monkeypatch):
    runner = _sent_into_paid(frame, monkeypatch)
    runner.now_untaken = ["Do this instead"]
    runner.cancelled = True
    ended(frame, kind="failed", text="Stopped.", cost_usd=0.01, cost_complete=False)
    assert frame.reply_text.GetValue().endswith("Do this instead")
    note = "Your Send Now message for Paid wasn't taken in. It's back in the message box."
    # Said after the turn's ending, which interrupts, so it isn't cut off (#663).
    assert pump(lambda: note in env["feedback"])
    assert frame._last_announcement == note


def test_without_echoes_a_steer_given_back_may_have_gone_in(frame, env, monkeypatch):
    runner = _sent_into_paid(frame, monkeypatch)
    runner.steered = ["Change course"]
    runner.echoes = False
    runner.cancelled = True
    ended(frame, kind="failed", text="Stopped.", cost_usd=0.01, cost_complete=False)
    assert frame.reply_text.GetValue().endswith("Change course")
    assert pump(lambda: "Your steered message for Paid may not have been taken in. "
                "It's back in the message box." in env["feedback"])


def test_codex_api_pasted_files_are_visible_and_removable(frame, folder, monkeypatch):
    from types import SimpleNamespace
    record = OwnSession('attached-api', 'Attachments', folder, provider='codex-api', billing='api')
    frame.store.add(record)
    frame._open = record.to_info()
    monkeypatch.setattr(frame, '_clipboard_files', lambda: ([folder + '/notes.txt'], []))
    frame._on_reply_paste(SimpleNamespace(Skip=lambda: pytest.fail('File paste was ignored')))
    assert frame.attach_list.IsShown()
    assert frame.attach_list.GetStrings() == ['notes.txt']
    assert frame._attachments[record.to_info().key] == [folder + '/notes.txt']
    monkeypatch.setattr(frame, '_require_capability', lambda *a: True)
    frame._on_attach_key(SimpleNamespace(GetKeyCode=lambda: wx.WXK_DELETE))
    assert not frame.attach_list.IsShown() and not frame._attachments.get(record.to_info().key)


def test_codex_charge_preserves_model_identity_and_effort(frame, folder):
    from thechatplace.ui.dialogs import NewSessionDialog
    from thechatplace.providers.codex_models import CodexModel
    dialog = NewSessionDialog(frame, folder, providers=('codex',), provider='codex', openai_key=True)
    try:
        dialog._set_codex_models((CodexModel('other', 'Other', ('low',), 'low', True),
                                 CodexModel('gpt-5.4-mini', 'Mini', ('low', 'high'), 'low', False),
                                 CodexModel('gpt-5-nano', 'Nano', ('low', 'high'), 'low', False)), False)
        dialog.model.SetSelection(3)  # nano, not API-list position 3
        dialog._on_codex_model()
        dialog.effort_choice.SetSelection(2)  # high
        dialog.charge.SetSelection(1)
        dialog._on_charge()
        assert dialog.values()[-1] == 'gpt-5-nano' and dialog.effort() == 'high'
        dialog.charge.SetSelection(0)
        dialog._on_charge()
        assert dialog.values()[-1] == 'gpt-5-nano' and dialog.effort() == 'high'
        dialog.charge.SetSelection(1)
        dialog._on_charge()
        dialog._set_codex_models((), True)
        dialog.charge.SetSelection(0)
        dialog._on_charge()
        assert dialog.model.GetSelection() == 0 and dialog.values()[-1] == ''
    finally:
        dialog.Destroy()


def test_codex_cost_and_failure_are_spoken_with_announcements_off(frame, env, folder):
    from thechatplace.speech import ANNOUNCE_SILENT
    record = OwnSession('cost-api', 'Paid Codex', folder, provider='codex-api', billing='api')
    frame.store.add(record)
    frame.speech.announce = ANNOUNCE_SILENT
    frame.speech.api_say_cost = True
    frame._on_codex_api_event(TurnEvent('finished', session_id='cost-api', data={
        'sent': True, 'cost_words': 'estimated 3 cents'}))
    assert not frame.speech.enabled  # this bypasses ordinary announcement gating
    assert 'Paid Codex: the cost is estimated at 3 cents.' in env['feedback']
    assert (env['feedback'][-1], record.to_info().key) in env['about']
    frame._on_codex_api_event(TurnEvent('api_error', session_id='cost-api', data={'failure': 'connection_dropped'}))
    assert "OpenAI's connection dropped" in env['feedback'][-1]
    # Stopped at what was left of the overall limit, the overall limit is
    # named, not the per-message one.
    frame._on_codex_api_event(TurnEvent('api_error', session_id='cost-api', data={
        'failure': 'message_limit', 'limit': 0.5, 'overall': True}))
    assert 'overall limit for OpenAI API credits ran out' in env['feedback'][-1]
    assert '50 cents of it was left' in env['feedback'][-1]
    frame._on_codex_api_event(TurnEvent('api_error', session_id='cost-api', data={
        'failure': 'message_limit', 'limit': 0.5, 'overall': True, 'others_held': True}))
    assert 'Send again once they finish' in env['feedback'][-1]
    frame._on_codex_api_event(TurnEvent('api_error', session_id='cost-api', data={
        'failure': 'message_limit', 'limit': 2.0, 'overall': False}))
    assert 'per-message API-credit limit was reached' in env['feedback'][-1]
    frame.speech.api_say_cost = False
    before = list(env['feedback'])
    frame._on_codex_api_event(TurnEvent('finished', session_id='cost-api', data={'sent': True, 'cost_words': 'estimated 3 cents'}))
    assert env['feedback'] == before


def test_api_bundled_smoke_report_imports_api_backend(tmp_path):
    import json
    from thechatplace import app
    out = tmp_path / 'smoke.json'
    app.smoke_test(str(out))
    assert json.loads(out.read_text())['codex_api'] is True


def test_api_relay_checks_own_route_and_actual_running_state(frame, folder):
    from types import SimpleNamespace
    record = OwnSession('relay-api', 'API relay', folder, provider='codex-api', billing='api')
    frame.store.add(record)
    info = record.to_info()
    assert frame._relay_on_api_credits(info)
    frame._codex_states[info.key] = SimpleNamespace(outcome='inProgress', state='working')
    assert frame._relay_source_working(info)
    frame._codex_states[info.key] = SimpleNamespace(outcome='completed', state='idle')
    assert not frame._relay_source_working(info)


def test_unpriced_confirmation_is_required_with_first_send_setting_off(frame, folder, monkeypatch):
    record = OwnSession('unpriced-api', 'Unknown price', folder, provider='codex-api', billing='api',
                        provider_options={'model': 'future-model'})
    frame.store.add(record)
    platform_paths.save_secret(platform_paths.OPENAI_API_KEY_SECRET, 'sk-proj-'+'0'*30)
    frame.speech.api_confirm_first = False
    answers = [wx.ID_CANCEL, wx.ID_OK, wx.ID_OK]
    questions = []
    def question(dialog):
        questions.append(dialog.warning.GetValue())
        assert dialog.GetDefaultItem() is dialog.cancel
        assert dialog.GetChildren().index(dialog.cancel) < dialog.GetChildren().index(dialog.send)
        try:
            return answers.pop(0)
        finally:
            dialog.Destroy()
    monkeypatch.setattr(frame, '_modal', question)
    key = record.to_info().key
    assert frame._api_send_problem(key) == ''
    assert key not in frame._unpriced_confirmed
    assert frame._api_send_problem(key) is None
    assert frame._unpriced_confirmed[key][:2] == ('future-model', 'default')
    assert frame._api_send_problem(key) is None
    assert len(questions) == 2
    record.provider_options['model'] = 'other-future-model'
    assert frame._api_send_problem(key, ask=False).startswith('Confirm this price-unknown')
    assert frame._api_send_problem(key) is None
    assert len(questions) == 3
    assert "your dollar limits can't stop a message" in questions[-1]


def test_api_picker_uses_separate_catalog_and_preserves_unknown_id(frame, folder):
    from thechatplace.ui.dialogs import NewSessionDialog
    from thechatplace.providers.codex_models import CodexModel
    dialog = NewSessionDialog(frame, folder, providers=('codex',), provider='codex', openai_key=True)
    try:
        dialog._set_codex_models((CodexModel('subscription-only', 'Subscription', ('high',), 'high', True),), False)
        dialog._set_api_models((CodexModel('future-model', 'Future', ('low', 'ultra'), 'low', False),), False)
        dialog.charge.SetSelection(1)
        dialog._on_charge()
        assert 'Subscription' not in dialog.model.GetStrings()
        dialog.model.SetSelection(dialog.model.GetStrings().index('Future (price unknown)'))
        dialog._on_codex_model()
        assert dialog.values()[-1] == 'future-model'
        assert 'ultra' not in dialog.effort_choice.GetStrings()
    finally:
        dialog.Destroy()


def test_unpriced_reply_reports_tokens_even_with_cost_setting_off(frame, folder, env):
    record = OwnSession('unpriced-reply', 'Budget', folder, provider='codex-api', billing='api')
    frame.store.add(record)
    frame.speech.api_say_cost = False
    frame._on_codex_api_event(TurnEvent('finished', session_id=record.cli_session_id,
        data={'sent': True, 'unpriced': True, 'cost_usd': None,
              'cost_words': 'used 12,400 tokens, cost unknown'}))
    assert 'Budget replied; used 12,400 tokens, cost unknown.' in env['feedback']


@pytest.mark.parametrize('outcome,verb', [('interrupted', 'stopped'), ('failed', 'failed')])
def test_unpriced_late_report_speaks_only_changed_counts(frame, folder, env, outcome, verb):
    record = OwnSession('late-unknown', 'Budget', folder, provider='codex-api', billing='api')
    frame.store.add(record)
    data = dict(sent=True, unpriced=True, outcome=outcome, payment_id=(1, 1),
                tokens={'inputTokens': 100, 'outputTokens': 20},
                cost_words='used at least 120 tokens; some tokens not reported, cost unknown')
    event = TurnEvent('finished', session_id=record.cli_session_id, data=data)
    frame._on_codex_api_event(event)
    said = f'Budget {verb}; used at least 120 tokens; some tokens not reported, cost unknown.'
    assert env['feedback'].count(said) == 1
    frame._on_codex_api_event(event)
    assert env['feedback'].count(said) == 1
    data['tokens'] = {'inputTokens': 110, 'outputTokens': 20}
    data['cost_words'] = 'used at least 130 tokens; some tokens not reported, cost unknown'
    frame._on_codex_api_event(event)
    assert any('at least 130 tokens' in line for line in env['feedback'])


def test_missing_saved_model_uses_reported_model_for_required_question(frame, folder, monkeypatch):
    record = OwnSession('missing-model', 'Unknown', folder, provider='codex-api', billing='api')
    frame.store.add(record)
    platform_paths.save_secret(platform_paths.OPENAI_API_KEY_SECRET, 'sk-proj-'+'0'*30)
    connection = object()
    monkeypatch.setattr(frame.sessions, 'owned_action', lambda *args: ('reported-future', connection))
    questions = []
    def cancel(dialog):
        questions.append(dialog.warning.GetValue())
        dialog.Destroy()
        return wx.ID_CANCEL
    monkeypatch.setattr(frame, '_modal', cancel)
    assert frame._api_send_problem(record.to_info().key) == ''
    assert 'reported-future' in questions[0]


@pytest.mark.parametrize('steer', [False, True])
def test_api_send_and_steer_preserve_latched_runtime_reason(frame, folder, env, monkeypatch, steer):
    from thechatplace.providers.codex_rpc import CodexRuntimeProblem
    record = OwnSession('latched-api', 'Priced', folder, provider='codex-api', billing='api',
                        provider_options={'model': 'gpt-5-nano'})
    frame.store.add(record)
    platform_paths.save_secret(platform_paths.OPENAI_API_KEY_SECRET, 'sk-proj-'+'0'*30)
    reason = 'Could not confirm this Codex turn. Use Stop to recheck this chat.'
    def refused(*args, **kwargs):
        raise CodexRuntimeProblem(reason)
    monkeypatch.setattr(frame.sessions, 'owned_action', refused)
    if steer:
        restored = []
        monkeypatch.setattr(frame, '_restore_codex_text', lambda key, texts: restored.extend(texts))
        frame._steer_codex_owned(record, 'Keep this')
        assert reason in env['feedback'] and restored == ['Keep this']
    else:
        assert frame._api_send_problem(record.to_info().key) == reason


def test_api_billing_lookup_does_not_mask_unexpected_errors(frame, folder, monkeypatch):
    record = OwnSession('unexpected-api', 'Priced', folder, provider='codex-api', billing='api')
    frame.store.add(record)
    platform_paths.save_secret(platform_paths.OPENAI_API_KEY_SECRET, 'sk-proj-'+'0'*30)
    def broken(*args, **kwargs):
        raise RuntimeError('programming error')
    monkeypatch.setattr(frame.sessions, 'owned_action', broken)
    with pytest.raises(RuntimeError, match='programming error'):
        frame._api_send_problem(record.to_info().key)


@pytest.mark.parametrize('outcome', ['', 'unknown', 'failed'])
def test_unpriced_reply_after_stop_is_said_as_stopped(frame, folder, env, outcome):
    record = OwnSession('stopping-unknown', 'Budget', folder, provider='codex-api', billing='api')
    frame.store.add(record)
    frame._on_codex_api_event(TurnEvent('finished', session_id=record.cli_session_id,
        data={'sent': True, 'unpriced': True, 'stopping': True, 'outcome': outcome,
              'cost_words': 'used at least 100 tokens, cost unknown; allowance kept'}))
    assert 'Budget stopped; used at least 100 tokens, cost unknown; allowance kept.' in env['feedback']


# -- what each reply cost, and the Cost column (#692) -----------------------------------------


def _chat_rows(frame):
    return [frame.chat_list.GetString(i) for i in range(frame.chat_list.GetCount())]


def test_each_reply_ends_with_its_cost_even_when_it_isnt_said(frame, env, monkeypatch, folder):
    add_key()
    asked(frame, monkeypatch)
    frame.speech.api_say_cost = False  # Settings only stops it being said
    transcript = platform_paths.transcript_path(folder, "api-1", env["projects"])
    before = transcript.read_bytes()
    open_chat(frame, "api-1")
    assert pump(lambda: "Claude: Hi" in _chat_rows(frame))
    _send(frame)
    ended(frame, text="Hi", cost_usd=0.19, reply_message_id="m1", reply_uuid="u1")
    assert frame.store.get("api-1").reply_costs == {"m1": "Cost: 19 cents"}
    # At the end of the row, and the message's last line, read in full.
    assert pump(lambda: "Claude: Hi. Cost: 19 cents" in _chat_rows(frame))
    reply = next(m for m in frame._chat_messages if m.key == "m1")
    assert reply.whole == "Hi\n\nCost: 19 cents"
    # What a screen reader reads for the row, with whole messages in the
    # list (the default), ends with it too.
    frame.speech.full_messages_in_list = True
    row = frame._chat_keys.index("m1")
    assert frame._message_item_text(row) == "Claude: Hi. Cost: 19 cents"
    # Copy Message leaves the cost line out: a copied reply goes elsewhere.
    copied = []
    monkeypatch.setattr(frame, "_copy_text", lambda text: copied.append(text) or True)
    frame.chat_list.SetSelection(row)
    frame._copy_message()
    assert copied == ["Claude:\nHi"]
    # Kept in The Chat Place's own store, so a restart still has it; never
    # in Claude Code's transcript.
    again = OwnSessionStore(env["tmp"] / "own.json")
    again.load()
    assert again.get("api-1").reply_costs == {"m1": "Cost: 19 cents"}
    assert transcript.read_bytes() == before
    # A subscription chat's replies have no cost line.
    open_chat(frame, "own-1")
    assert pump(lambda: "Claude: Hi" in _chat_rows(frame))
    assert not any("Cost:" in row for row in _chat_rows(frame))


def test_an_answer_with_background_work_and_the_rest_each_have_their_cost(frame):
    frame._on_turn_event({"id": "api-1"}, "Paid", TurnEvent(
        "waiting", text="Started.", data={"tasks": ["x"], "billing": "api", "cost_usd": 0.01,
                                          "sent": True, "cost_complete": True,
                                          "reply_message_id": "m1"}))
    assert frame.store.get("api-1").reply_costs == {"m1": "Cost: 1 cent"}
    # Claude carried on with a new reply: it has what came after.
    ended(frame, text="All done.", cost_usd=0.04, reply_message_id="m2")
    assert frame.store.get("api-1").reply_costs == {"m1": "Cost: 1 cent", "m2": "Cost: 3 cents"}
    # No new reply: the same one has the whole.
    frame._on_turn_event({"id": "api-1"}, "Paid", TurnEvent(
        "waiting", text="Started.", data={"tasks": ["x"], "billing": "api", "cost_usd": 0.02,
                                          "sent": True, "cost_complete": True,
                                          "reply_message_id": "m3"}))
    ended(frame, text="", cost_usd=0.05, reply_message_id="m3")
    assert frame.store.get("api-1").reply_costs["m3"] == "Cost: 5 cents"


def test_a_codex_reply_keeps_the_words_it_was_said_in(frame, folder):
    record = OwnSession('priced-reply', 'Budget', folder, provider='codex-api', billing='api')
    frame.store.add(record)
    unpriced = OwnSession('unpriced-cost', 'Unknown', folder, provider='codex-api', billing='api')
    frame.store.add(unpriced)
    frame._on_codex_api_event(TurnEvent('finished', session_id='priced-reply', data={
        'billing': 'api', 'sent': True, 'turn_id': 't1', 'cost_words': 'estimated 3 cents'}))
    frame._on_codex_api_event(TurnEvent('finished', session_id='unpriced-cost', data={
        'billing': 'api', 'sent': True, 'unpriced': True, 'turn_id': 't9',
        'tokens': {'inputTokens': 12_000, 'outputTokens': 400},
        'cost_words': 'used 12,400 tokens, cost unknown'}))
    # Nothing sent, nothing kept.
    frame._on_codex_api_event(TurnEvent('finished', session_id='priced-reply', data={
        'billing': 'api', 'sent': False, 'turn_id': 't2', 'cost_words': 'nothing was charged'}))
    assert frame.store.get('priced-reply', provider='codex-api').reply_costs == {
        'turn:t1': 'Cost: an estimated 3 cents'}
    assert frame.store.get('unpriced-cost', provider='codex-api').reply_costs == {
        'turn:t9': 'Cost: unknown, 12,400 tokens'}


def test_the_cost_column_says_what_a_chat_on_api_credits_has_spent(frame):
    book = spend_ledger.default_ledger()
    reservation = book.reserve("api-1")
    book.settle(reservation, 1.42)
    book.close(reservation, complete=True)
    frame.refresh_sessions()

    def rows():
        return [frame.session_list.GetString(i) for i in range(frame.session_list.GetCount())]
    assert pump(lambda: any(r.startswith("Paid, Claude, Claude API credits, $1.42, ")
                            for r in rows()))
    mine = next(r for r in rows() if r.startswith("Mine"))
    assert "$" not in mine and "cent" not in mine
