"""What chats on API credits say and show (#658), checked without a window:
the settings and their defaults, the reply's cost, the session list row,
Properties, Usage's API credits section, and the menu commands."""
import json

from thechatplace import announce, commands, properties, sound_events, spend_ledger, usage
from thechatplace.own_store import OwnSession
from thechatplace.speech import (ANNOUNCE_FULL, ANNOUNCE_SUMMARY, API_NOTICE_SOUND,
                                 API_NOTICE_SPEECH, SpeechSettings)


def test_settings_default_to_saying_asking_and_costing(tmp_path):
    fresh = SpeechSettings()
    assert fresh.api_send_notice == API_NOTICE_SPEECH
    assert fresh.api_confirm_first is True
    assert fresh.api_say_cost is True
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    loaded = SpeechSettings.load(path)
    assert (loaded.api_send_notice, loaded.api_confirm_first, loaded.api_say_cost) == (
        API_NOTICE_SPEECH, True, True)


def test_settings_keep_choices_and_odd_values_fall_back_to_saying_more(tmp_path):
    path = tmp_path / "settings.json"
    settings = SpeechSettings(api_send_notice=API_NOTICE_SOUND, api_confirm_first=False,
                              api_say_cost=False)
    settings.save(path)
    loaded = SpeechSettings.load(path)
    assert (loaded.api_send_notice, loaded.api_confirm_first, loaded.api_say_cost) == (
        API_NOTICE_SOUND, False, False)
    path.write_text(json.dumps({"api_send_notice": "shout", "api_confirm_first": "no",
                                "api_say_cost": 0}), encoding="utf-8")
    odd = SpeechSettings.load(path)
    assert (odd.api_send_notice, odd.api_confirm_first, odd.api_say_cost) == (
        API_NOTICE_SPEECH, True, True)


def test_the_api_send_sound_waits_for_app_kit():
    # Not an event until app-kit's catalog has it, with a sound in each pack.
    assert sound_events.API_SENT not in sound_events.BY_ID
    assert sound_events.has_api_sent_sound() is False


def test_a_reply_says_its_cost_before_the_reply():
    assert announce.reply_text("Fix", "All done.", ANNOUNCE_FULL, cost="6 cents") == \
        "Fix replied, 6 cents. All done."
    assert announce.reply_text("Fix", "All done. Bye.", ANNOUNCE_SUMMARY,
                               cost="less than a cent") == \
        "Fix replied, less than a cent: All done. 3 words."
    # Without a cost, as it always was.
    assert announce.reply_text("Fix", "All done.", ANNOUNCE_FULL) == "Fix replied. All done."


def test_the_row_says_api_credits_after_the_name():
    api = OwnSession("a-1", "Budget", "C:\\G\\Repo", billing="api").to_info()
    plain = OwnSession("s-1", "Budget", "C:\\G\\Repo").to_info()
    assert api.api_credits and not plain.api_credits
    assert api.list_line(0, ["title", "status"]).startswith("Budget, API credits, ")
    assert "API credits" not in plain.list_line(0, ["title", "status"])
    # Said even with the Title column turned off.
    assert api.list_line(0, ["status"]).startswith("API credits, ")
    # A Codex chat is never on API credits.
    assert not OwnSession("c-1", "Codex", "C:\\G", provider="codex").to_info().api_credits


def test_properties_say_how_a_chat_is_charged_and_what_it_spent():
    api = OwnSession("a-1", "Budget", "C:\\G\\Repo", billing="api")
    lines = properties.session_properties(api.to_info(), own=api, now_ms=0, api_spent=0.4234)
    assert "Charged to: API credits" in lines
    assert "Spent on API credits by this chat: 42 cents" in lines
    plain = OwnSession("s-1", "Plain", "C:\\G\\Repo")
    lines = properties.session_properties(plain.to_info(), own=plain, now_ms=0)
    assert "Charged to: Claude subscription" in lines
    assert not any(line.startswith("Spent on API credits") for line in lines)


def test_usage_lists_the_tally_by_model_and_by_chat_name(tmp_path):
    clock = [1_800_000_000.0]
    ledger = spend_ledger.SpendLedger(tmp_path, clock=lambda: clock[0])
    first = ledger.reserve("chat-1")
    ledger.settle(first, 1.25, {"claude-opus-5-5": 1.25})
    ledger.close(first, complete=True)
    gone = ledger.reserve("chat-gone")
    ledger.settle(gone, 0.04, {"claude-haiku-5-5": 0.04})
    ledger.close(gone, complete=True)
    ledger.reserve("chat-1")  # still running: held
    lines = usage.api_credit_lines(ledger.totals(), {"chat-1": "Budget"})
    assert "API credits today: $1.29" in lines
    assert "API credits in the last 7 days: $1.29" in lines
    assert "API credits since the total was reset: $1.29" in lines
    assert "API credits held for running messages: $5.00" in lines
    assert "API credits left of your $190.00 limit: $183.71" in lines
    assert lines.index("API credits on claude-opus-5-5: $1.25") < lines.index(
        "API credits on claude-haiku-5-5: 4 cents")
    assert "API credits in Budget: $1.25" in lines
    # Chat names only, never ids.
    assert "API credits in chats no longer listed: 4 cents" in lines
    assert not any("chat-gone" in line or "chat-1" in line for line in lines)


def test_a_chat_keeps_its_total_across_a_reset(tmp_path):
    ledger = spend_ledger.SpendLedger(tmp_path)
    one = ledger.reserve("chat-1")
    ledger.settle(one, 0.30)
    ledger.close(one, complete=True)
    ledger.reset()
    two = ledger.reserve("chat-1")
    ledger.settle(two, 0.12)
    ledger.close(two, complete=True)
    totals = ledger.totals()
    assert round(totals.by_chat["chat-1"], 6) == 0.12  # since the reset
    assert round(totals.chat_all_time["chat-1"], 6) == 0.42  # in all


def test_usage_says_a_problem_with_the_tally(tmp_path):
    ledger = spend_ledger.SpendLedger(tmp_path)
    (tmp_path / spend_ledger.LEDGER_FILE).write_text("not json\n", encoding="utf-8")
    lines = usage.api_credit_lines(ledger.totals(), {})
    assert lines[0].startswith("API credits: The Chat Place's record of API spending is damaged")


def test_the_commands_are_where_the_guide_says():
    assert commands.menu_path("api_keys") == "Tools, API Keys"
    assert commands.menu_path("continue_on_api_credits") == "Chat, Continue on API Credits"
    assert commands.access_key(commands.command("api_keys").label) == "p"
    assert commands.access_key(commands.command("continue_on_api_credits").label) == "e"
