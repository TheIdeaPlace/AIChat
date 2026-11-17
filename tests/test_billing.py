"""How a chat is charged (#658): the account check before anything is sent,
the second check on system/init, the key's settings, budgets, the scrubber
and the plain words for API failures. Windowless; keys are made up."""
import json

import pytest

from thechatplace import billing, claude_mcp, platform_paths
from thechatplace.billing import ROUTE_API, ROUTE_SUBSCRIPTION

FAKE_KEY = "sk-ant-test-0000000000000000000000000000"

SUBSCRIPTION = {"email": "someone@example.com", "organization": "Example",
                "subscriptionType": "max", "apiProvider": "firstParty"}
HELPER = {"apiKeySource": "apiKeyHelper", "apiProvider": "firstParty", "tokenSource": "claude.ai"}


def body(account=None, **extra):
    data = {"commands": [], **extra}
    if account is not None:
        data["account"] = account
    return data


# -- the pre-send check, both routes ------------------------------------------------------


def test_an_api_chat_sends_only_on_the_apps_own_key_at_anthropic():
    assert billing.account_problem(ROUTE_API, body(HELPER)) is None
    assert billing.account_problem(ROUTE_API, body(HELPER, auth_conflict={"x": 1})) is None


@pytest.mark.parametrize("init_body", [
    None,                                    # no answer at all
    {},                                      # an answer with no account (older Claude Code)
    body("not a dict"),
    body([]),
    body({}),                                # an account that says nothing
    body(SUBSCRIPTION),                      # the subscription: the key wasn't used
    body({**HELPER, "apiKeySource": "ANTHROPIC_API_KEY"}),   # a key from the environment
    body({**HELPER, "apiKeySource": "/login managed key"}),
    body({**HELPER, "apiKeySource": None}),
    body({**HELPER, "apiKeySource": "none"}),
    body({**HELPER, "apiProvider": "bedrock"}),
    body({**HELPER, "apiProvider": "vertex"}),
    body({"apiKeySource": "apiKeyHelper"}),  # no provider: not known to be Anthropic's
    "not a dict",
])
def test_an_api_chat_fails_closed_on_anything_else(init_body):
    problem = billing.account_problem(ROUTE_API, init_body)
    assert problem and "Nothing was sent" in problem


def test_a_subscription_chat_stops_for_any_key_or_other_provider():
    assert billing.account_problem(ROUTE_SUBSCRIPTION, body(SUBSCRIPTION)) is None
    for account in (HELPER, {**SUBSCRIPTION, "apiKeySource": "ANTHROPIC_API_KEY"},
                    {"apiKeySource": "apiKeyHelper"}, {**SUBSCRIPTION, "apiProvider": "bedrock"}):
        problem = billing.account_problem(ROUTE_SUBSCRIPTION, body(account))
        assert problem and "didn't send your message" in problem and "Nothing was sent" in problem
    # "none" and "" are no key, as system/init has always said.
    assert billing.account_problem(ROUTE_SUBSCRIPTION,
                                   body({**SUBSCRIPTION, "apiKeySource": "none"})) is None
    assert billing.account_problem(ROUTE_SUBSCRIPTION,
                                   body({**SUBSCRIPTION, "apiKeySource": ""})) is None


def test_a_subscription_chat_without_an_account_goes_on_as_before():
    # Claude Code older than the account field: system/init still checks.
    assert billing.account_problem(ROUTE_SUBSCRIPTION, {}) is None
    assert billing.account_problem(ROUTE_SUBSCRIPTION, None) is None
    assert billing.account_problem(ROUTE_SUBSCRIPTION, body("odd")) is None


def test_an_unknown_route_is_the_subscription():
    for value in (None, "", "API", "credits", 1, "api "):
        assert billing.normalize_route(value) == ROUTE_SUBSCRIPTION
        assert billing.account_problem(value, body(HELPER))  # a key: refused
    assert billing.normalize_route("api") == ROUTE_API
    assert billing.route_label("api") == "API credits"
    assert billing.route_label("x") == "Claude subscription"


def test_a_key_in_an_odd_source_is_never_said():
    problem = billing.account_problem(ROUTE_API, body({**HELPER, "apiKeySource": FAKE_KEY}))
    assert FAKE_KEY not in problem and billing.HIDDEN_KEY in problem


# -- the second check, on system/init ---------------------------------------------------


def test_source_problem_on_each_route():
    assert billing.source_problem(ROUTE_API, "apiKeyHelper") is None
    for source in (None, "", "none", "ANTHROPIC_API_KEY"):
        assert billing.source_problem(ROUTE_API, source)
    assert billing.source_problem(ROUTE_SUBSCRIPTION, "none") is None
    assert billing.source_problem(ROUTE_SUBSCRIPTION, None) is None
    assert billing.source_problem(ROUTE_SUBSCRIPTION, "apiKeyHelper")
    assert billing.source_problem(ROUTE_SUBSCRIPTION, "ANTHROPIC_API_KEY")


# -- the key's settings and the budget -------------------------------------------------


def test_api_settings_name_the_helper_command_never_the_key(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    settings = billing.api_settings()
    assert list(settings) == ["apiKeyHelper"]
    assert settings["apiKeyHelper"] == platform_paths.api_key_helper_command(
        platform_paths.CLAUDE_API_KEY_SECRET)
    assert "sk-ant" not in json.dumps(settings)
    with pytest.raises(ValueError):
        billing.api_settings("not-a-secret")


def test_the_helper_is_merged_into_the_one_settings_and_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    style = json.dumps({"outputStyle": "Concise", "apiKeyHelper": "echo someone-elses"})
    command = ["claude", "-p", "--settings", style, "--resume", "s1"]
    command = claude_mcp.with_settings(command, ["github"])
    merged = claude_mcp.merge_settings(command, billing.api_settings())
    assert merged.count("--settings") == 1
    settings = json.loads(merged[merged.index("--settings") + 1])
    assert settings["outputStyle"] == "Concise"
    assert settings["deniedMcpServers"] == [{"serverName": "github"}]
    assert settings["apiKeyHelper"] == billing.api_settings()["apiKeyHelper"]


def test_merge_adds_a_settings_when_there_is_none_and_skips_a_flags_value():
    merged = claude_mcp.merge_settings(["claude", "--name", "--settings", "--resume", "s"],
                                       {"apiKeyHelper": "x"})
    assert merged[-2:] == ["--settings", '{"apiKeyHelper": "x"}']
    assert merged[:5] == ["claude", "--name", "--settings", "--resume", "s"]
    assert claude_mcp.merge_settings(["claude"], {}) == ["claude"]


@pytest.mark.parametrize("amount,text", [(5, "5.00"), (5.009, "5.00"), (0.01, "0.01"),
                                         (4.999, "4.99"), (190, "190.00")])
def test_budget_is_rounded_down_to_the_cent(amount, text):
    assert billing.budget_flags(amount) == ["--max-budget-usd", text]


@pytest.mark.parametrize("amount", [0, 0.009, -1, float("nan"), float("inf"), True, "5"])
def test_no_budget_too_small_or_odd(amount):
    with pytest.raises(ValueError):
        billing.budget_text(amount)


# -- keys: shape, ending, scrubbing -----------------------------------------------------


def test_key_shape_and_ending():
    assert billing.looks_like_key(FAKE_KEY)
    assert billing.looks_like_key("  " + FAKE_KEY + "\n")
    for text in ("", "sk-ant-", "sk-ant-short", "sk-proj-0000000000000000000000000",
                 FAKE_KEY + " extra", None, 5):
        assert not billing.looks_like_key(text)
    assert billing.key_ending(FAKE_KEY) == "0000"
    assert billing.key_ending("abc") == ""


def test_scrub_hides_anything_shaped_like_a_key():
    text = f"Error: key {FAKE_KEY} refused; also SK-ANT-api03-AbC_d-e"
    scrubbed = billing.scrub(text)
    assert "0000" not in scrubbed and "AbC" not in scrubbed
    assert scrubbed.count(billing.HIDDEN_KEY) == 2
    assert billing.scrub("nothing here") == "nothing here"
    assert billing.scrub(None) is None and billing.scrub(5) == 5


# -- what's said ------------------------------------------------------------------------


def test_failure_text_for_budget_credit_and_key():
    over = billing.failure_text("error_max_budget_usd", "", 5.03, 5.0)
    assert "per-message limit of $5.00" in over and "$5.03" in over and "run over" in over
    assert "per-message limit for" in billing.failure_text("error_max_budget_usd", "")
    low = billing.failure_text("success", "Credit balance is too low to access the API")
    assert "no credit left" in low and "may have been charged" in low
    bad = billing.failure_text("", "Invalid API key · Fix external API key")
    assert "refused your Claude API key" in bad
    assert billing.failure_text("", "authentication_error: invalid x-api-key")
    assert billing.failure_text("success", "Some other error") is None
    assert billing.failure_text("error_during_execution", "") is None


@pytest.mark.parametrize("amount,said", [
    (0, "less than a cent"), (0.0043, "less than a cent"), (0.005, "1 cent"),
    (0.06, "6 cents"), (0.42, "42 cents"), (0.994, "99 cents"), (0.995, "$1.00"),
    (12.3, "$12.30"), (1234.5, "$1,234.50"), (None, "an unknown amount"),
    (float("nan"), "an unknown amount")])
def test_money_is_said_plainly(amount, said):
    assert billing.money(amount) == said
