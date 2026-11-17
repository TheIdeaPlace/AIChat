"""How a chat is charged (#658): the account check before anything is sent,
the second check on system/init, the key's settings, budgets, the scrubber
and the plain words for API failures. Windowless; keys are made up."""
import json
import time

import pytest

from thechatplace import billing, claude_mcp, platform_paths
from thechatplace.billing import ROUTE_API, ROUTE_SUBSCRIPTION

FAKE_KEY = "sk-ant-test-0000000000000000000000000000"

SUBSCRIPTION = {"email": "someone@example.com", "organization": "Example",
                "subscriptionType": "max", "apiProvider": "firstParty"}
HELPER = {"tokenSource": "apiKeyHelper", "apiProvider": "firstParty"}  # as 2.1.325 answers (#662)


def body(account=None, **extra):
    data = {"commands": [], **extra}
    if account is not None:
        data["account"] = account
    return data


# -- the pre-send check, both routes ------------------------------------------------------


def test_an_api_chat_sends_only_on_the_apps_own_key_at_anthropic():
    assert billing.account_problem(ROUTE_API, body(HELPER)) is None
    # The helper named as apiKeySource too (as system/init names it) is the same key.
    assert billing.account_problem(ROUTE_API, body(
        {"apiKeySource": "apiKeyHelper", "apiProvider": "firstParty"})) is None
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
    body({"tokenSource": "claude.ai", "apiProvider": "firstParty", "apiKeySource": None}),
    body({"tokenSource": "claude.ai", "apiProvider": "firstParty", "apiKeySource": "none"}),
    body({"tokenSource": "CLAUDE_CODE_OAUTH_TOKEN", "apiProvider": "firstParty"}),
    body({**HELPER, "apiProvider": "bedrock"}),
    body({**HELPER, "apiProvider": "vertex"}),
    body({"apiKeySource": "apiKeyHelper"}),  # no provider: not known to be Anthropic's
    body({"tokenSource": "apiKeyHelper"}),
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
    # The claude.ai sign-in, or none named, is the subscription (#662, #663).
    for token in ("claude.ai", "none", "", None):
        assert billing.account_problem(
            ROUTE_SUBSCRIPTION, body({**SUBSCRIPTION, "tokenSource": token})) is None
    # "none" and "" are no key, as system/init has always said.
    assert billing.account_problem(ROUTE_SUBSCRIPTION,
                                   body({**SUBSCRIPTION, "apiKeySource": "none"})) is None
    assert billing.account_problem(ROUTE_SUBSCRIPTION,
                                   body({**SUBSCRIPTION, "apiKeySource": ""})) is None


@pytest.mark.parametrize("token", ["ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                                   "something-new", FAKE_KEY])
def test_a_subscription_chat_stops_for_any_other_token(token):
    # As 2.1.325 answers for a settings file's env.ANTHROPIC_AUTH_TOKEN or
    # CLAUDE_CODE_OAUTH_TOKEN (checked under a scratch CLAUDE_CONFIG_DIR, #663):
    # no apiKeySource, only the token's source, and firstParty.
    account = {"tokenSource": token, "apiProvider": "firstParty"}
    problem = billing.account_problem(ROUTE_SUBSCRIPTION, body(account))
    assert problem and "another token" in problem and "Nothing was sent" in problem
    assert FAKE_KEY not in problem
    # Beside a real sign-in's fields too.
    assert billing.account_problem(ROUTE_SUBSCRIPTION, body({**SUBSCRIPTION, **account}))


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
    assert list(settings) == ["apiKeyHelper", "env"]
    assert settings["apiKeyHelper"] == platform_paths.api_key_helper_command(
        platform_paths.CLAUDE_API_KEY_SECRET)
    assert "sk-ant" not in json.dumps(settings)
    with pytest.raises(ValueError):
        billing.api_settings("not-a-secret")


def test_api_settings_pin_where_the_key_goes(monkeypatch, tmp_path):
    # A settings file's env.ANTHROPIC_BASE_URL sent the key elsewhere (#663);
    # the turn's own --settings overrides it.
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    env = billing.api_settings()["env"]
    assert env["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"
    assert env["NODE_TLS_REJECT_UNAUTHORIZED"] == "1"
    assert env["NODE_EXTRA_CA_CERTS"] == ""
    env["ANTHROPIC_BASE_URL"] = "changed"           # a copy each time
    assert billing.api_settings()["env"]["ANTHROPIC_BASE_URL"] == billing.API_BASE_URL


def test_the_pinned_env_merges_with_and_wins_over_one_already_there(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    mine = json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9", "KEEP": "1"}})
    merged = claude_mcp.merge_settings(["claude", "--settings", mine], billing.api_settings())
    settings = json.loads(merged[merged.index("--settings") + 1])
    assert settings["env"]["ANTHROPIC_BASE_URL"] == billing.API_BASE_URL
    assert settings["env"]["KEEP"] == "1"
    # An env that isn't an object is simply replaced.
    odd = json.dumps({"env": "x"})
    merged = claude_mcp.merge_settings(["claude", "--settings", odd], billing.api_settings())
    assert json.loads(merged[merged.index("--settings") + 1])["env"] == billing.API_ENV


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


# -- the kept key, for Tools, API Keys ----------------------------------------------------


NOV_16 = time.mktime((2026, 11, 16, 12, 0, 0, 0, 0, -1))
OTHER_KEY = "sk-ant-test-1111111111111111111111117Q2x"


@pytest.fixture
def kept(tmp_path, monkeypatch):
    """The key store in memory and the app's folder in tmp_path, as fake_env
    does: no real keychain, keyring or %APPDATA%."""
    named = {}
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(platform_paths, "has_secret", lambda name: name in named)
    monkeypatch.setattr(platform_paths, "load_secret", lambda name: named.get(name))
    monkeypatch.setattr(platform_paths, "save_secret",
                        lambda name, text: named.__setitem__(name, text))
    monkeypatch.setattr(platform_paths, "delete_secret", lambda name: named.pop(name, None))
    return named


def _info(tmp_path):
    return tmp_path / "appdata" / billing.API_KEY_INFO_FILE


def test_saving_a_key_keeps_it_in_the_key_store_and_only_its_ending_in_a_file(kept, tmp_path):
    assert not billing.has_api_key()
    assert billing.api_key_status(NOV_16) == "Claude: No key"
    billing.save_api_key("  " + OTHER_KEY + "\n", now=NOV_16)
    assert kept == {platform_paths.CLAUDE_API_KEY_SECRET: OTHER_KEY}
    assert billing.has_api_key()
    text = _info(tmp_path).read_text(encoding="utf-8")
    assert json.loads(text) == {"ending": "7Q2x", "added": "2026-11-16"}
    assert "sk-ant" not in text and "1111" not in text
    assert billing.api_key_status(NOV_16) == "Claude: key ending in 7Q2x, added November 16"
    # A key added in another year says which.
    next_year = time.mktime((2027, 2, 1, 12, 0, 0, 0, 0, -1))
    assert billing.api_key_status(next_year) == \
        "Claude: key ending in 7Q2x, added November 16, 2026"


def test_replacing_a_key_replaces_its_ending(kept, tmp_path):
    billing.save_api_key(OTHER_KEY, now=NOV_16)
    billing.save_api_key(FAKE_KEY, now=NOV_16 + 86400)
    assert kept[platform_paths.CLAUDE_API_KEY_SECRET] == FAKE_KEY
    assert billing.api_key_status(NOV_16) == "Claude: key ending in 0000, added November 17"


@pytest.mark.parametrize("text", ["", "   ", "sk-proj-0000000000000000000000000000",
                                  "sk-ant-short", FAKE_KEY + " extra"])
def test_a_key_that_isnt_one_is_refused_and_nothing_is_kept(kept, tmp_path, text):
    with pytest.raises(ValueError, match="sk-ant-"):
        billing.save_api_key(text)
    assert kept == {} and not _info(tmp_path).exists()


def test_a_key_the_store_wont_keep_writes_nothing_about_it(kept, tmp_path, monkeypatch):
    def refuse(name, text):
        raise OSError("The keychain didn't keep it (error -25308).")
    monkeypatch.setattr(platform_paths, "save_secret", refuse)
    with pytest.raises(OSError):
        billing.save_api_key(FAKE_KEY)
    assert not _info(tmp_path).exists()
    assert billing.api_key_status() == "Claude: No key"


@pytest.mark.parametrize("contents", [None, "not json", "[]", '{"ending": "7Q2x"}',
                                      '{"ending": "sk-ant-api03-whole", "added": "2026-11-16"}',
                                      '{"ending": 5, "added": "2026-11-16"}',
                                      '{"ending": "7Q2x", "added": "16 November"}'])
def test_a_key_with_a_missing_or_damaged_info_file_is_just_added(kept, tmp_path, contents):
    billing.save_api_key(OTHER_KEY, now=NOV_16)
    if contents is None:
        _info(tmp_path).unlink()
    else:
        _info(tmp_path).write_text(contents, encoding="utf-8")
    assert billing.api_key_status(NOV_16) == "Claude: key added"


def test_an_info_file_without_a_key_says_no_key(kept, tmp_path):
    # Restored from another computer's backup, which never holds the key.
    _info(tmp_path).parent.mkdir(parents=True)
    _info(tmp_path).write_text('{"ending": "7Q2x", "added": "2026-11-16"}', encoding="utf-8")
    assert billing.api_key_status(NOV_16) == "Claude: No key"


def test_removing_the_key_forgets_it_and_its_ending(kept, tmp_path):
    billing.save_api_key(OTHER_KEY, now=NOV_16)
    billing.remove_api_key()
    assert kept == {} and not _info(tmp_path).exists()
    assert billing.api_key_status(NOV_16) == "Claude: No key"
    billing.remove_api_key()  # nothing to remove: no error


def test_the_status_never_reads_the_key(kept, monkeypatch):
    # Reading it can raise a keychain prompt on a Mac (#658).
    billing.save_api_key(OTHER_KEY, now=NOV_16)

    def no(name):
        raise AssertionError("load_secret was called")
    monkeypatch.setattr(platform_paths, "load_secret", no)
    assert billing.api_key_status(NOV_16).startswith("Claude: key ending in")
    assert billing.has_api_key()


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
    (0, "nothing"), (0.0, "nothing"), (0.0043, "less than a cent"), (0.005, "1 cent"),
    (0.06, "6 cents"), (0.42, "42 cents"), (0.994, "99 cents"), (0.995, "$1.00"),
    (12.3, "$12.30"), (1234.5, "$1,234.50"), (None, "an unknown amount"),
    (float("nan"), "an unknown amount")])
def test_money_is_said_plainly(amount, said):
    assert billing.money(amount) == said


@pytest.mark.parametrize("amount,complete,said", [
    (0.06, True, "6 cents"), (0.0, True, "nothing"), (0.06, False, "at least 6 cents"),
    (0.001, False, "an amount that isn't known"), (0.0, False, "an amount that isn't known"),
    (None, True, "an amount that isn't known"), (float("inf"), True, "an amount that isn't known"),
    (-1.0, True, "an amount that isn't known"), (True, True, "an amount that isn't known")])
def test_cost_words_say_when_the_cost_isnt_known(amount, complete, said):
    assert billing.cost_words(amount, complete) == said


@pytest.mark.parametrize("retry,refused", [
    # What 2.1.325 sent for a made-up key (#663): no text, these two fields.
    ({"error_status": 401, "error": "authentication_failed", "attempt": 3}, True),
    ({"error_status": 403, "error": ""}, True),
    ({"error_status": None, "error": "authentication_failed"}, True),
    ({"error_status": None, "error": "Invalid API key"}, True),
    ({"error_status": 529, "error": "overloaded"}, False),
    ({"error_status": 429, "error": "rate_limit"}, False),
    ({"error_status": 500, "error": "server_error"}, False),
    ({}, False), (None, False), ({"error_status": "401"}, False)])
def test_key_refused_reads_the_retrys_status_and_error(retry, refused):
    assert billing.key_refused(retry) is refused


def test_a_refused_key_in_claude_codes_new_words_is_still_plain():
    assert billing.failure_text("", "API error: authentication_failed") == billing.KEY_REFUSED
