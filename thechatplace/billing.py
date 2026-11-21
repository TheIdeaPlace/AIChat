"""Which way a Claude chat is charged (#658): the Claude subscription, as every
chat always was, or API credits, through an Anthropic API key the user added.

The route belongs to the chat and is fixed when the chat is made (New Chat or
Continue Here, Kelly's decision on #658): nothing about a chat ever changes
how its next message is charged. A key is only ever used by a chat made for
it, and the app supplies that key itself; a key in the environment or in
Claude Code's settings is never used by accident.

How the key reaches Claude Code
-------------------------------
Only through ``apiKeyHelper`` in the turn's single ``--settings``
(``api_settings``): a command that reads the kept key and prints it
(``platform_paths.api_key_helper_command``). Checked with Claude Code 2.1.325:
with the key in Claude Code's environment, a Bash command Claude ran saw it;
given through apiKeyHelper, it didn't. So never ``ANTHROPIC_API_KEY``, never
an environment (the app's own or the child's) and never a command line.

Checked twice, the first time before anything is sent
-----------------------------------------------------
Claude Code's answer to ``initialize`` (which every turn sends first, and
which comes before the message goes) has ``account``. Checked with 2.1.325:

* With the key in the environment: ``{"apiKeySource": "ANTHROPIC_API_KEY",
  "apiProvider": "firstParty", "tokenSource": "claude.ai"}``, and a top-level
  ``auth_conflict``.
* With the key from apiKeyHelper (how the app gives it): ``{"tokenSource":
  "apiKeyHelper", "apiProvider": "firstParty"}`` and *no* ``apiKeySource``
  (checked with 2.1.325 after Kelly's first Test Key was refused, #662).
  ``system/init`` does report ``apiKeySource: "apiKeyHelper"`` then;
  ``initialize`` names it only as the token's source. ``key_source`` reads
  both.
* With only the subscription: ``{"email", "organization", "subscriptionType",
  "apiProvider": "firstParty"}`` and no ``apiKeySource`` or ``tokenSource``.
* Other sign-ins, checked with 2.1.325 under a scratch ``CLAUDE_CONFIG_DIR``
  (only initialize sent, made-up values; #663): a settings file's
  ``env.ANTHROPIC_AUTH_TOKEN`` gives ``tokenSource: "ANTHROPIC_AUTH_TOKEN"``,
  ``CLAUDE_CODE_OAUTH_TOKEN`` (settings or environment) gives
  ``tokenSource: "CLAUDE_CODE_OAUTH_TOKEN"``, a settings ``ANTHROPIC_API_KEY``
  gives ``apiKeySource``, and no sign-in at all ``tokenSource: "none"``. Every
  one of them says ``apiProvider: "firstParty"``.

``account_problem`` reads that before the message is written, both ways: an
API chat must be on the app's own key (apiKeyHelper) at Anthropic itself
(firstParty), and a subscription chat must not report any key, any token but
a subscription sign-in (claude.ai, or ``claude setup-token``'s
CLAUDE_CODE_OAUTH_TOKEN), or another provider. ``source_problem`` repeats the
check on ``system/init``'s ``apiKeySource``, as the app always has.

Where the key goes
------------------
``firstParty`` doesn't say which host: checked with 2.1.325, a settings
file's ``env.ANTHROPIC_BASE_URL`` (the user's, a project's, or
``CLAUDE_CONFIG_DIR``'s) sent an API turn, key and all, to that host while
initialize still said firstParty. So an API turn's own ``--settings`` pins
the address (``api_settings``): its ``env`` sets ``ANTHROPIC_BASE_URL`` to
Anthropic's API, which overrides every other settings file (checked: a
local listener named in user settings got nothing, and the turn was
answered by Anthropic), and keeps TLS checking on.
"""
from __future__ import annotations

import datetime
import json
import math
import re
import time
from typing import Optional

ROUTE_SUBSCRIPTION = "subscription"
ROUTE_API = "api"
ROUTES = (ROUTE_SUBSCRIPTION, ROUTE_API)

#: What Claude Code's account says when the key came from apiKeyHelper.
HELPER_SOURCE = "apiKeyHelper"
#: Anthropic's own API (not Bedrock, Vertex or Foundry).
FIRST_PARTY = "firstParty"

#: How a key looks: Anthropic's keys start with this. Checked when one is
#: added, and what the scrubber hides.
KEY_PREFIX = "sk-ant-"
_KEY_SHAPE = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,300}")
_KEY_ANYWHERE = re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}", re.IGNORECASE)
HIDDEN_KEY = "sk-ant-(hidden)"

#: Where to add credit or see what's been spent.
CONSOLE_BILLING_URL = "https://console.anthropic.com/settings/billing"


def normalize_route(value) -> str:
    """A stored or passed route; anything that isn't exactly "api" is the
    subscription, so an unknown value can never start spending."""
    return ROUTE_API if value == ROUTE_API else ROUTE_SUBSCRIPTION


def route_label(route: str) -> str:
    return "API credits" if normalize_route(route) == ROUTE_API else "Claude subscription"


def looks_like_key(text: str) -> bool:
    """Whether pasted text has the shape of an Anthropic API key."""
    return isinstance(text, str) and bool(_KEY_SHAPE.fullmatch(text.strip()))


def key_ending(key: str) -> str:
    """The last four characters, to name a key by ("Key ending in 7Q2x")."""
    key = (key or "").strip()
    return key[-4:] if len(key) >= 8 else ""


# -- The kept key, for Tools, API Keys ------------------------------------------
#
# The key itself is only ever in the system's key store (platform_paths'
# named secrets). What API Keys shows about it (its last four characters and
# the day it was added) is kept beside the app's other files in a plain JSON
# file, so opening the dialog never has to read the key: on a Mac that can
# raise a keychain prompt (#658).

#: The plain file with the key's ending and when it was added; never the key.
API_KEY_INFO_FILE = "claude-api-key-info.json"

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")


def _info_path():
    from . import platform_paths
    return platform_paths.app_data_dir() / API_KEY_INFO_FILE


def has_api_key() -> bool:
    """Whether a Claude API key is kept, without reading it."""
    from . import platform_paths
    return platform_paths.has_secret(platform_paths.CLAUDE_API_KEY_SECRET)


def save_api_key(key: str, now: Optional[float] = None) -> None:
    """Keep ``key`` in the system's key store, replacing any kept before.
    Raises ValueError if it isn't shaped like a key, OSError if the key store
    won't keep it. The key's ending and today's date go in the info file; a
    failure to write that is let go, as the status then says "key added"."""
    from . import platform_paths
    save_named_api_key(key, platform_paths.CLAUDE_API_KEY_SECRET, _info_path(),
                       looks_like_key, 'Claude', KEY_PREFIX, now)


def save_named_api_key(key, secret_name, info_path, validate, label, prefix, now=None):
    """Both providers use the same protected secret and non-secret metadata path."""
    from . import platform_paths
    key = (key or "").strip()
    if not validate(key):
        raise ValueError(f"A {label} API key starts with {prefix}.")
    platform_paths.save_secret(secret_name, key)
    day = datetime.date.fromtimestamp(time.time() if now is None else now)
    try:
        from .provider_storage import atomic_write_json
        atomic_write_json(info_path, {"ending": key_ending(key), "added": day.isoformat()})
    except OSError:
        pass


def remove_api_key() -> None:
    """Forget the kept key and what was noted about it."""
    from . import platform_paths
    platform_paths.delete_secret(platform_paths.CLAUDE_API_KEY_SECRET)
    try:
        _info_path().unlink(missing_ok=True)
    except OSError:
        pass


def _key_info(path=None) -> Optional[tuple]:
    """(ending, date added) from the info file, or None if it's missing or
    damaged."""
    try:
        raw = json.loads((path if path is not None else _info_path()).read_text(encoding="utf-8"))
        ending, added = raw["ending"], datetime.date.fromisoformat(raw["added"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(ending, str) or not re.fullmatch(r"[A-Za-z0-9_-]{4}", ending):
        return None
    return ending, added


def api_key_status(now: Optional[float] = None) -> str:
    """API Keys' line about the Claude key: "Claude: No key", "Claude: key
    ending in 7Q2x, added November 16" (with the year when it isn't this
    one), or "Claude: key added" when the info file is missing or damaged.
    Never the key."""
    if not has_api_key():
        return "Claude: No key"
    return named_key_status('Claude', _key_info(), now)


def named_key_status(label, info, now=None):
    if info is None:
        return f"{label}: key added"
    ending, added = info
    today = datetime.date.fromtimestamp(time.time() if now is None else now)
    day = f"{_MONTHS[added.month - 1]} {added.day}"
    if added.year != today.year:
        day += f", {added.year}"
    return f"{label}: key ending in {ending}, added {day}"


def scrub(text):
    """``text`` with anything that looks like an API key hidden. Applied to
    Claude Code's stderr and error text before it reaches the window, a log or
    a bug report. Anything but a string is given back as it is."""
    if not isinstance(text, str):
        return text
    text = _KEY_ANYWHERE.sub(HIDDEN_KEY, text)
    return re.sub(r'\bsk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}',
                  'sk-(hidden)', text, flags=re.IGNORECASE)


# -- The settings an API chat's turns get --------------------------------------


#: Where an API turn's requests, and so the key, may go (#663).
API_BASE_URL = "https://api.anthropic.com"

#: The environment an API turn's ``--settings`` sets, overriding any other
#: settings file's (see "Where the key goes"): Anthropic's own address, TLS
#: certificates checked, and no extra certificate authority, so a proxy
#: that decrypts traffic can't be trusted into reading the key.
#:
#: Node reads NODE_EXTRA_CA_CERTS once, as it starts, before any settings
#: are applied, so the empty value here doesn't undo one Claude Code was
#: started with: ``api_environment`` takes that out of the child's own
#: environment (#663). The empty value stays for what's read after the
#: settings are applied: Claude Code's own handling of extra certificates
#: for its connections, and NODE_TLS_REJECT_UNAUTHORIZED, which Node reads
#: at each connection. It costs nothing; that Claude Code reads it there
#: isn't checked.
#:
#: Claude Code passes a settings file's ``env`` on to everything it runs,
#: so Bash commands, hooks and MCP servers in an API chat see these values
#: too: npm or node run there get no extra certificate authority. Settings
#: can't give a value to Claude Code alone.
API_ENV = {"ANTHROPIC_BASE_URL": API_BASE_URL, "NODE_TLS_REJECT_UNAUTHORIZED": "1",
           "NODE_EXTRA_CA_CERTS": ""}

#: Taken out of an API turn's own environment (``api_environment``): read
#: as Node starts, where the settings above come too late to change them.
API_UNSET_VARS = ("NODE_EXTRA_CA_CERTS", "NODE_TLS_REJECT_UNAUTHORIZED")


def api_environment(env: dict) -> dict:
    """``env`` (a turn's environment, already child_environment's) for an
    API turn's Claude Code process (#663): without the certificate
    variables Node reads at startup, so a certificate authority trusted
    there (a proxy that decrypts traffic) can't be given the key. A proxy
    (HTTPS_PROXY) is still used: the connection through it is TLS to
    Anthropic, checked against the usual certificate authorities."""
    # Any case: Windows' environment names are.
    return {name: value for name, value in env.items() if name.upper() not in API_UNSET_VARS}


def api_settings(name: Optional[str] = None) -> dict:
    """What an API chat's turn merges into its single ``--settings``
    (claude_mcp.merge_settings): the command that prints the kept key, and
    the address it may be sent to (``API_ENV``)."""
    from . import platform_paths
    return {"apiKeyHelper": platform_paths.api_key_helper_command(
        name or platform_paths.CLAUDE_API_KEY_SECRET), "env": dict(API_ENV)}


#: The smallest per-message budget passed: Claude Code checks the budget
#: after each request, so below about a cent a turn could do nothing at all.
MIN_BUDGET = 0.01


def budget_text(amount: float) -> str:
    """``--max-budget-usd``'s value: dollars, rounded down to the cent so
    the budget is never more than was reserved."""
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) \
            or not math.isfinite(amount) or amount < MIN_BUDGET:
        raise ValueError("Not a budget a turn can have.")
    return f"{math.floor(round(amount * 100, 6)) / 100:.2f}"


def budget_flags(amount: float):
    """``["--max-budget-usd", "5.00"]``. Checked with Claude Code 2.1.325: a
    turn over it ends with result subtype ``error_max_budget_usd``. It's
    checked after each request, so a turn can run over by one request."""
    return ["--max-budget-usd", budget_text(amount)]


# -- The checks ------------------------------------------------------------------

_NOTHING_SENT = "Nothing was sent, so nothing was charged."


def _account(init_body) -> Optional[dict]:
    account = init_body.get("account") if isinstance(init_body, dict) else None
    return account if isinstance(account, dict) else None


def _no_source(source) -> bool:
    return source is None or source in ("", "none")


def key_source(account: dict):
    """Where initialize's ``account`` says the key comes from, or None for
    none. ``apiKeySource`` when there is one; otherwise a ``tokenSource`` of
    apiKeyHelper, which is how a helper's key is reported (#662). Other
    token sources aren't keys; ``token_problem`` decides about them."""
    source = account.get("apiKeySource")
    if _no_source(source) and account.get("tokenSource") == HELPER_SOURCE:
        return HELPER_SOURCE
    return source


#: The token source of the subscription's own sign-in (Claude Code reports
#: it beside an API key; with the sign-in alone there's no tokenSource).
CLAUDE_AI_SOURCE = "claude.ai"
#: A subscription sign-in made with ``claude setup-token`` (#663): a
#: long-lived claude.ai token for the same plan (signin.py counts it as the
#: subscription). The app strips it from the environment it gives Claude
#: Code, so it arrives from a settings file's ``env``.
SETUP_TOKEN_SOURCE = "CLAUDE_CODE_OAUTH_TOKEN"
#: Token sources that are the subscription.
SUBSCRIPTION_TOKEN_SOURCES = (CLAUDE_AI_SOURCE, SETUP_TOKEN_SOURCE)


def token_problem_source(account: dict):
    """A ``tokenSource`` a subscription chat mustn't send with, or None
    (#663). Missing, "none", claude.ai or CLAUDE_CODE_OAUTH_TOKEN (``claude
    setup-token``'s sign-in) is the subscription (or no sign-in, which
    Claude Code refuses itself). Anything else, such as
    ANTHROPIC_AUTH_TOKEN from a settings file's ``env`` (checked with
    2.1.325), or a source this version doesn't know, is a token The Chat
    Place didn't choose and can't check the billing of, so it stops the
    message; apiKeyHelper is caught as a key first (``key_source``)."""
    source = account.get("tokenSource")
    if _no_source(source) or source in SUBSCRIPTION_TOKEN_SOURCES:
        return None
    return source


def account_problem(route: str, init_body) -> Optional[str]:
    """Why the message mustn't be sent, from Claude Code's answer to
    ``initialize``, or None. Read before the message is written, so every
    problem here really is "nothing was sent".

    API credits fail closed: no answer, no ``account``, an odd one, a key from
    anywhere but the app's helper, or a provider other than Anthropic's own
    API all stop the message.

    The subscription stops for any ``apiKeySource`` (a key in the environment
    or a user's own apiKeyHelper), any token but a subscription sign-in
    (``token_problem_source``), or another provider. An answer with no
    ``account`` at all is let through, as every turn was before #658: Claude
    Code versions older than the account field (it isn't in 2.1.286's
    answer) would otherwise never send anything, and ``source_problem`` on
    system/init still stands behind it, as it always has."""
    route = normalize_route(route)
    account = _account(init_body)
    if route == ROUTE_API:
        if account is None:
            return ("Claude Code didn't say how this message would be charged, so The Chat "
                    "Place didn't send it on API credits. " + _NOTHING_SENT
                    + " Update Claude Code (in a terminal, run: claude update), then send again.")
        source = key_source(account)
        provider = account.get("apiProvider")
        if provider != FIRST_PARTY:
            return (f"Claude Code is set up to use {_provider_words(provider)} rather than "
                    "Anthropic's own API, so The Chat Place didn't send this message on your "
                    "API key. " + _NOTHING_SENT)
        if source == HELPER_SOURCE:
            return None
        if _no_source(source):
            return ("Claude Code couldn't use the API key you added (it may have been removed, "
                    "or the system's key store couldn't be read), and would have charged your "
                    "Claude subscription instead. " + _NOTHING_SENT
                    + " Check the key in Tools, API Keys, then send again.")
        return (f"Claude Code would have used another API key ({_safe_word(source)}) rather "
                "than the one you added to The Chat Place, so the message wasn't sent. "
                + _NOTHING_SENT + " Remove that key from the environment or settings that "
                "set it, then send again.")
    if account is None:
        return None
    source = key_source(account)
    if not _no_source(source):
        return (f"Claude would have used an API key ({_safe_word(source)}) instead of your "
                "subscription login, so The Chat Place didn't send your message. "
                + _NOTHING_SENT + " Remove that key from the environment or settings that set "
                "it, then try again.")
    token = token_problem_source(account)
    if token is not None:
        return (f"Claude Code would have signed in with another token ({_safe_word(token)}) "
                "instead of your subscription login, so The Chat Place didn't send your "
                "message. " + _NOTHING_SENT + " Remove that token from the environment or "
                "settings that set it, then try again.")
    provider = account.get("apiProvider")
    if provider is not None and provider != FIRST_PARTY:
        return (f"Claude Code is set up to use {_provider_words(provider)} instead of your "
                "subscription login, so The Chat Place didn't send your message. "
                + _NOTHING_SENT)
    return None


def source_problem(route: str, api_key_source) -> Optional[str]:
    """The second check, on ``system/init``'s ``apiKeySource``, or None. The
    subscription's is the check every turn has always had
    (claude_cli.api_key_problem); API credits must be on the app's helper,
    and anything else (none included) stops the turn."""
    from .claude_cli import api_key_problem
    if normalize_route(route) != ROUTE_API:
        return api_key_problem(api_key_source)
    if api_key_source == HELPER_SOURCE:
        return None
    if _no_source(api_key_source):
        return ("Claude Code started this message on your Claude subscription, not on the "
                "API key you added, so The Chat Place stopped it before Claude answered. "
                "Check the key in Tools, API Keys, then send again.")
    return (f"Claude Code started this message on another API key "
            f"({_safe_word(api_key_source)}), not the one you added, so The Chat Place "
            "stopped it before Claude answered. Remove that key from the environment or "
            "settings that set it, then send again.")


def _safe_word(value) -> str:
    """A value from Claude Code, fit to say: a short name, never a key."""
    text = scrub(str(value)) if value is not None else "unknown"
    text = " ".join(text.split())
    return text[:40] or "unknown"


def _provider_words(provider) -> str:
    return {"bedrock": "Amazon Bedrock", "vertex": "Google Vertex AI",
            "foundry": "Microsoft Foundry"}.get(str(provider).lower() if provider else "",
                                                f"another provider ({_safe_word(provider)})")


# -- What went wrong on API credits, in words -----------------------------------

_CREDIT_LOW = re.compile(r"credit balance is too low", re.IGNORECASE)
_BAD_KEY = re.compile(r"invalid (x-)?api[ -]key|authentication_error|authentication_failed|"
                      r"invalid bearer token", re.IGNORECASE)

#: Said when Anthropic refuses the key: in a result's text, or as soon as
#: Claude Code starts retrying a refused request (``key_refused``).
KEY_REFUSED = ("Anthropic refused your Claude API key. Replace it in Tools, API Keys, then "
               "send again.")

#: The HTTP statuses of a refused key.
_REFUSED_STATUSES = (401, 403)
_AUTH_WORDS = re.compile(r"auth|invalid (x-)?api[ -]key|permission", re.IGNORECASE)


def key_refused(retry: Optional[dict]) -> bool:
    """Whether an ``api_retry`` (claude_cli's "retry" event data) is
    Anthropic refusing the key. Checked with Claude Code 2.1.325 and a
    made-up key: ``{"error_status": 401, "error": "authentication_failed"}``
    with no text, first after about three seconds, and Claude Code then
    retries about ten times over four minutes before giving up (#663). A
    status of 401 or 403, or an error that says authentication, is enough."""
    if not isinstance(retry, dict):
        return False
    status = retry.get("error_status")
    if type(status) is int and status in _REFUSED_STATUSES:
        return True
    error = retry.get("error")
    return isinstance(error, str) and bool(_AUTH_WORDS.search(error))


def overall_limit_text(provider: str, limit: Optional[float], others_held: bool) -> str:
    """What's said when a message used up what was left of the overall limit
    (``Reservation.overall_cap``), for Claude and Codex alike. Other chats'
    messages holding part of it may give some back when they finish."""
    said = f"; {money(limit)} of it was left for this message" if limit is not None else ""
    if others_held:
        return (f"Your overall limit for {provider} API credits ran out{said}, as other "
                "chats' messages were holding the rest. Send again once they finish, or "
                "raise the limit or reset the total in Tools, API Keys.")
    return (f"Your overall limit for {provider} API credits ran out{said}. Raise the limit "
            "or reset the total in Tools, API Keys to send more on API credits.")


def failure_text(subtype: str, text: str, cost_usd: Optional[float] = None,
                 limit: Optional[float] = None, overall: bool = False,
                 others_held: bool = False) -> Optional[str]:
    """A plain message for an API chat's turn that ended on a budget, credit
    or key problem, or None for any other ending. ``overall``: the limit was
    what was left of the overall one (``Reservation.overall_cap``)."""
    text = text or ""
    if subtype == "error_max_budget_usd":
        cost = (f" It cost {money(cost_usd)}, as a message can run over the limit by one "
                "request." if cost_usd else "")
        if overall:
            return f"Claude stopped.{cost} {overall_limit_text('Claude', limit, others_held)}"
        said = f" of {money(limit)}" if limit is not None else ""
        return (f"Claude stopped at your per-message limit{said} for API credits.{cost} "
                "Send another message to carry on, or change the limit in Tools, API Keys.")
    if _CREDIT_LOW.search(text):
        return ("Your Claude API key has no credit left. Messages before this one may have "
                "been charged. Add credit at console.anthropic.com, or carry on in a new chat "
                "on your Claude subscription.")
    if _BAD_KEY.search(text):
        return KEY_REFUSED
    return None


#: What's said when what a message cost isn't known (#663): one phrase that
#: names the cost, wherever it goes ("Budget replied, cost not known: …").
COST_NOT_KNOWN = "cost not known"


def cost_words(amount: Optional[float], complete: bool = True) -> str:
    """What an API turn cost, as said after it (#663): "6 cents", "nothing",
    "at least 6 cents" when a request after the last result wasn't costed
    (a Stop that didn't get its result, say), and "cost not known"
    (``COST_NOT_KNOWN``) when nothing about it is."""
    if amount is None or not isinstance(amount, (int, float)) or isinstance(amount, bool) \
            or not math.isfinite(amount) or amount < 0:
        return COST_NOT_KNOWN
    if not complete:
        return f"at least {money(amount)}" if amount >= 0.005 else COST_NOT_KNOWN
    return money(amount)


def cost_more(words: str) -> str:
    """``cost_words`` for what came after a cost already said: "6 cents
    more", but never "cost not known more" (#663)."""
    return words if words == COST_NOT_KNOWN else f"{words} more"


def cost_sentence(words: str, title: str = "") -> str:
    """``cost_words`` as a sentence of its own: "It cost 6 cents." or "Cost
    not known.", after ``title`` when there is one ("Budget: it cost 6
    cents.", "Budget: cost not known.")."""
    if words == COST_NOT_KNOWN:
        return f"{title}: {COST_NOT_KNOWN}." if title else "Cost not known."
    if words == "nothing was charged":
        return f"{title}: nothing was charged." if title else "Nothing was charged."
    if words.startswith("estimated, at most "):
        text = "The cost is " + words
    elif words.startswith("estimated "):
        text = "The cost is estimated at " + words[len("estimated "):]
    else:
        text = "It cost " + words
    return f"{title}: {text[0].lower() + text[1:]}." if title else text + "."


def money(amount: Optional[float]) -> str:
    """Dollars as said: "$4.20", "6 cents", "less than a cent", and
    "nothing" for exactly none (never "0 cents")."""
    if amount is None or not isinstance(amount, (int, float)) or not math.isfinite(amount):
        return "an unknown amount"
    if amount <= 0:
        return "nothing"
    if amount < 0.005:
        return "less than a cent"
    # Half a cent up, as people round (round() would make 0.005 "0 cents"),
    # with a hair more for floats such as 0.995 that are stored just under.
    cents = int(amount * 100 + 0.5 + 1e-9)
    if cents < 100:
        return f"{cents} cent" + ("" if cents == 1 else "s")
    return f"${cents // 100:,}.{cents % 100:02d}"
