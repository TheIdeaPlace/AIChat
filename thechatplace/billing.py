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

* With a key: ``{"apiKeySource": "apiKeyHelper", "apiProvider": "firstParty",
  "tokenSource": "claude.ai"}`` (``"ANTHROPIC_API_KEY"`` when the key came
  from the environment), and a top-level ``auth_conflict``.
* With only the subscription: ``{"email", "organization", "subscriptionType",
  "apiProvider": "firstParty"}`` and no ``apiKeySource``.

``account_problem`` reads that before the message is written, both ways: an
API chat must be on the app's own key (apiKeyHelper) at Anthropic itself
(firstParty), and a subscription chat must not report any key or another
provider. ``source_problem`` repeats the check on ``system/init``'s
``apiKeySource``, as the app always has.
"""
from __future__ import annotations

import math
import re
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
_KEY_ANYWHERE = re.compile(r"sk-ant-[A-Za-z0-9_\-]*", re.IGNORECASE)
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


def scrub(text):
    """``text`` with anything that looks like an API key hidden. Applied to
    Claude Code's stderr and error text before it reaches the window, a log or
    a bug report. Anything but a string is given back as it is."""
    if not isinstance(text, str) or "sk-ant-" not in text.lower():
        return text
    return _KEY_ANYWHERE.sub(HIDDEN_KEY, text)


# -- The settings an API chat's turns get --------------------------------------


def api_settings(name: Optional[str] = None) -> dict:
    """What an API chat's turn merges into its single ``--settings``
    (claude_mcp.merge_settings): the command that prints the kept key."""
    from . import platform_paths
    return {"apiKeyHelper": platform_paths.api_key_helper_command(
        name or platform_paths.CLAUDE_API_KEY_SECRET)}


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


def account_problem(route: str, init_body) -> Optional[str]:
    """Why the message mustn't be sent, from Claude Code's answer to
    ``initialize``, or None. Read before the message is written, so every
    problem here really is "nothing was sent".

    API credits fail closed: no answer, no ``account``, an odd one, a key from
    anywhere but the app's helper, or a provider other than Anthropic's own
    API all stop the message.

    The subscription stops for any ``apiKeySource`` (a key in the environment
    or a user's own apiKeyHelper) or another provider. An answer with no
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
        source = account.get("apiKeySource")
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
    source = account.get("apiKeySource")
    if not _no_source(source):
        return (f"Claude would have used an API key ({_safe_word(source)}) instead of your "
                "subscription login, so The Chat Place didn't send your message. "
                + _NOTHING_SENT + " Remove that key from the environment or settings that set "
                "it, then try again.")
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
_BAD_KEY = re.compile(r"invalid (x-)?api[ -]key|authentication_error|invalid bearer token",
                      re.IGNORECASE)


def failure_text(subtype: str, text: str, cost_usd: Optional[float] = None,
                 limit: Optional[float] = None) -> Optional[str]:
    """A plain message for an API chat's turn that ended on a budget, credit
    or key problem, or None for any other ending."""
    text = text or ""
    if subtype == "error_max_budget_usd":
        said = f" of {money(limit)}" if limit is not None else ""
        cost = (f" It cost {money(cost_usd)}, as a message can run over the limit by one "
                "request." if cost_usd else "")
        return (f"Claude stopped at your per-message limit{said} for API credits.{cost} "
                "Send another message to carry on, or change the limit in Tools, API Keys.")
    if _CREDIT_LOW.search(text):
        return ("Your Claude API key has no credit left. Messages before this one may have "
                "been charged. Add credit at console.anthropic.com, or carry on in a new chat "
                "on your Claude subscription.")
    if _BAD_KEY.search(text):
        return ("Anthropic refused your Claude API key. Replace it in Tools, API Keys, then "
                "send again.")
    return None


def money(amount: Optional[float]) -> str:
    """Dollars as said: "$4.20", "6 cents", "less than a cent" (never "0 cents")."""
    if amount is None or not isinstance(amount, (int, float)) or not math.isfinite(amount):
        return "an unknown amount"
    if amount < 0.005:
        return "less than a cent"
    # Half a cent up, as people round (round() would make 0.005 "0 cents"),
    # with a hair more for floats such as 0.995 that are stored just under.
    cents = int(amount * 100 + 0.5 + 1e-9)
    if cents < 100:
        return f"{cents} cent" + ("" if cents == 1 else "s")
    return f"${cents // 100:,}.{cents % 100:02d}"
