"""What chats on API credits say and show (#658), checked without a window:
the settings and their defaults, the reply's cost, the session list row,
Properties, Usage's API credits section, and the menu commands."""
import json

import pytest

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
    # Nothing is "at no cost", never "replied, nothing" (#663).
    assert announce.reply_text("Fix", "All done.", ANNOUNCE_FULL, cost="nothing") == \
        "Fix replied at no cost. All done."
    assert announce.reply_text("Fix", "All done.", ANNOUNCE_FULL, cost="at least 6 cents") == \
        "Fix replied, at least 6 cents. All done."
    assert announce.cost_after("") == ""


def test_the_default_row_says_api_credits_in_charged_to_not_the_title():
    # Kelly on bld07: one kind of information per column, no if-then rules.
    api = OwnSession("a-1", "Budget", "C:\\G\\Repo", billing="api").to_info()
    plain = OwnSession("s-1", "Budget", "C:\\G\\Repo").to_info()
    assert api.api_credits and not plain.api_credits
    assert api.list_line(0).startswith("Budget, Claude, Claude API credits, Repo, ")
    assert plain.list_line(0).startswith("Budget, Claude, Claude subscription, Repo, ")
    assert api.field_values(0)["title"] == "Budget"
    # Only Charged to says it: with that column removed, nothing does.
    assert "API credits" not in api.list_line(0, ["title", "status"])
    # A Codex chat is never on API credits.
    assert not OwnSession("c-1", "Codex", "C:\\G", provider="codex").to_info().api_credits


def test_the_charged_to_column_says_how_each_own_chat_is_paid_for():
    from thechatplace.sessions import DEFAULT_FIELDS, FIELD_CHARGED, FIELD_PROVIDER
    # Shown at first, just after Provider.
    assert DEFAULT_FIELDS.index(FIELD_CHARGED) == DEFAULT_FIELDS.index(FIELD_PROVIDER) + 1
    cases = [
        (OwnSession("a-1", "Budget", "C:\\G", billing="api"), "Claude API credits"),
        (OwnSession("s-1", "Budget", "C:\\G"), "Claude subscription"),
        (OwnSession("c-1", "Budget", "C:\\G", provider="codex"), "ChatGPT subscription"),
        (OwnSession("o-1", "Budget", "C:\\G", provider="codex-api", billing="api"),
         "OpenAI API credits"),
        (OwnSession("p-1", "Budget", "C:\\G", provider="copilot"), "Copilot plan"),
    ]
    for own, said in cases:
        info = own.to_info()
        assert info.charged_to == said
        line = info.list_line(0, ["title", "charged", "status"])
        assert line.startswith(f"Budget, {said}"), line
        # Shown in its column, so not said again after the name.
        assert line.count("API credits") <= 1


@pytest.mark.parametrize("fields", [["title", "charged", "status"], ["charged", "status"], None])
def test_charged_to_says_api_credits_for_kept_and_not_owned_rows(fields):
    # Review of the Charged to column: a kept row of an API chat, and a
    # Codex row on API credits that no record of ours covers, say API credits
    # in Charged to, as one of our own does.
    from dataclasses import replace
    from thechatplace.sessions import KEPT, OWN, SessionInfo
    kept = replace(OwnSession("a-1", "Budget", "C:\\G", billing="api").to_info(),
                   source=KEPT, kept_from=OWN)
    foreign = SessionInfo(source="app_server", key="codex-api:t", title="Thread", cwd="C:\\G",
                          cli_session_id="t", provider="codex-api", api_credits=True)
    for info in (kept, foreign):
        assert "API credits" in info.list_line(0, fields), (info.source, fields)
    assert kept.charged_to == "Claude API credits"
    assert foreign.charged_to == "OpenAI API credits"
    # A kept subscription chat of ours says so too.
    plain = replace(OwnSession("s-1", "Plain", "C:\\G").to_info(), source=KEPT, kept_from=OWN)
    assert plain.charged_to == "Claude subscription"


def test_charged_to_says_nothing_for_sessions_the_app_didnt_start():
    from thechatplace.sessions import DESKTOP, TERMINAL, SessionInfo
    for source in (DESKTOP, TERMINAL):
        info = SessionInfo(source=source, key="d", title="Desk", cwd="C:\\G",
                           cli_session_id="d")
        assert info.charged_to == ""
        assert info.list_line(0, ["title", "charged"]) == "Desk"


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
    assert "API credits held for running messages: $2.00" in lines
    assert "API credits left of your $20.00 limit: $16.71" in lines
    assert lines.index("API credits on claude-opus-5-5: $1.25") < lines.index(
        "API credits on claude-haiku-5-5: 4 cents")
    assert "API credits in Budget: $1.25" in lines
    # Chat names only, never ids.
    assert "API credits in chats no longer listed: 4 cents" in lines
    assert not any("chat-gone" in line or "chat-1" in line for line in lines)


def test_a_subscription_chats_usage_has_one_api_credits_line(tmp_path):
    ledger = spend_ledger.SpendLedger(tmp_path)
    assert usage.api_credit_summary(ledger.totals()) == (
        "API credits: nothing spent since the total was reset, $20.00 left of your $20.00 "
        "limit. For today, the last 7 days and spending by model and by chat, open Usage and Context from a chat that uses API credits.")
    one = ledger.reserve("chat-1")
    ledger.settle(one, 1.25)
    ledger.close(one, complete=True)
    assert usage.api_credit_summary(ledger.totals()).startswith(
        "API credits: $1.25 spent since the total was reset, $18.75 left")
    (tmp_path / spend_ledger.LEDGER_FILE).write_text("not json\n", encoding="utf-8")
    assert usage.api_credit_summary(ledger.totals()).startswith(
        "API credits: The Chat Place's record of API spending is damaged")


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


def test_an_asides_answer_says_its_cost_as_a_cost():
    # The review (#663): "Answer, nothing." read as no answer; "It cost an
    # amount that isn't known" didn't read as a cost at all.
    from thechatplace.ui.claude_extras import _aside_with_cost
    assert _aside_with_cost("Answer. ZEPHYR", "3 cents", False) == "Answer, 3 cents. ZEPHYR"
    assert _aside_with_cost("Answer. ZEPHYR", "nothing", False) == "Answer, at no cost. ZEPHYR"
    assert _aside_with_cost("Answer: Yes. 1 words.", "cost not known", False) == (
        "Answer, cost not known: Yes. 1 words.")
    assert _aside_with_cost("Not answered. It failed.", "cost not known", True) == (
        "Not answered. It failed. Cost not known.")
    assert _aside_with_cost("Not answered. It failed.", "2 cents", True) == (
        "Not answered. It failed. It cost 2 cents.")


def test_unpriced_text_and_release_allowance_without_zero_cost(tmp_path):
    from thechatplace import openai_billing as words
    ledger = spend_ledger.SpendLedger(tmp_path)
    r = ledger.reserve('codex-api:chat', unpriced=True)
    tokens = dict(inputTokens=12000, cachedInputTokens=4000, cacheWriteInputTokens=1000,
                  outputTokens=400, reasoningOutputTokens=100)
    ledger.record_openai_tokens(r, 'future', tokens, unpriced=True)
    ledger.close(r, complete=False)
    totals = ledger.totals()
    assert words.spending_words(totals).startswith('cost unknown; unpriced usage:')
    assert '$0.00' not in words.chat_cost_words(ledger, 'codex-api:chat')
    assert 'may be more' in words.release_question(totals)
    assert 'up to' not in words.release_question(totals)
    assert totals.unpriced_releasable == 2
    priced = ledger.reserve('codex-api:chat')
    ledger.settle(priced, .42)
    ledger.close(priced, complete=False)
    mixed = words.spending_words(ledger.totals())
    assert 'priced spending $0.42' in mixed and 'plus unpriced usage' in mixed
    assert 'priced cost not known' in mixed
    status = words.status_words(ledger.totals())
    assert status == 'API: priced cost not known + 12,400 unpriced tokens'
    assert len(status) < 80
    assert 'cached input' in '\n'.join(words.usage_lines(ledger, 'codex-api:chat'))
    assert words.token_words(tokens, complete=False).startswith('used at least 12,400 tokens')


def test_status_keeps_unpriced_and_unsettled_priced_use_distinct(tmp_path):
    from thechatplace import openai_billing as words
    ledger = spend_ledger.SpendLedger(tmp_path)
    unknown = ledger.reserve('codex-api:unknown', unpriced=True)
    ledger.close(unknown, complete=False)
    assert words.status_words(ledger.totals()) == 'API: cost unknown; unpriced tokens not reported'
    priced = ledger.reserve('codex-api:priced')
    ledger.close(priced, complete=False)
    status = words.status_words(ledger.totals())
    assert 'priced cost not known' in status and 'unpriced tokens not reported' in status
