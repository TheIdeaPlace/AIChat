"""Whether Claude Code is signed in (#52), from ``claude auth status``.

That command answers in JSON, at once and at no cost:
``{"loggedIn": true, "authMethod": "claude.ai", "subscriptionType": "max",
"email": …, "orgName": …}``. Sessions on disk are listed either way; only
sending needs a sign-in. Signing in is ``claude auth login``, which opens
the browser, so it runs in its own window.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from . import platform_paths
from .claude_cli import child_environment

_PLANS = {"max": "Max", "pro": "Pro", "team": "Team", "enterprise": "Enterprise"}
_PROVIDERS = {"bedrock": "Amazon Bedrock", "vertex": "Google Vertex AI",
              "foundry": "Microsoft Foundry"}


@dataclass
class SignIn:
    known: bool  # False when Claude Code couldn't be asked
    signed_in: bool = False
    method: str = ""  # "claude.ai" for a Claude subscription
    provider: str = ""  # "firstParty", or "bedrock" and the like
    plan: str = ""
    email: str = ""
    problem: str = ""
    missing: bool = False  # no claude it can use at all: offer to install one
    install_help: str = ""  # with ``missing``, the install commands
    legacy_npm: str = ""  # Windows shim that can migrate itself with install --force

    @property
    def subscription(self) -> bool:
        """A Claude subscription: a sign-in, or a long-lived token made from
        one (``claude setup-token``)."""
        return self.method in ("claude.ai", "oauth_token")


def check(executable: Optional[str] = None, run=subprocess.run) -> SignIn:
    if not executable:
        lookup = platform_paths.find_claude()
        if not lookup.path:
            return SignIn(False, missing=True, problem=lookup.reason,
                          install_help=lookup.install_help, legacy_npm=lookup.legacy_npm)
        executable = lookup.path
    try:
        # The environment turns get: an API key or cloud provider set for
        # other tools is removed there, so it mustn't count here either.
        out = run([executable, "auth", "status", "--json"], capture_output=True, text=True,
                  encoding="utf-8", errors="replace", timeout=20,
                  env=child_environment(),
                  creationflags=platform_paths.hidden_window_flags())
    except subprocess.TimeoutExpired:
        return SignIn(False, problem=f"Claude Code ({executable}) didn't answer in 20 seconds")
    except (OSError, subprocess.SubprocessError) as exc:
        return SignIn(False, problem=f"Claude Code ({executable}) couldn't be started: {exc}")
    data = _json_in(_plain(out.stdout or ""))
    if data is None:
        return SignIn(False, problem=_no_answer(executable, out))
    return SignIn(True, signed_in=bool(data.get("loggedIn")),
                  method=str(data.get("authMethod") or ""),
                  provider=str(data.get("apiProvider") or ""),
                  plan=str(data.get("subscriptionType") or ""),
                  email=str(data.get("email") or ""))


def _json_in(text: str) -> Optional[dict]:
    """The answer in what ``claude auth status`` printed, or None.
    Some installs print a notice along with it, such as a warning about the
    npm install, and the answer was taken as unreadable when they did. A
    notice may hold braces or JSON of its own, so the answer is the first
    object starting a line that says whether Claude Code is signed in; an
    object inside something else isn't the answer."""
    decoder = json.JSONDecoder()
    for start in _LINE_STARTS.finditer(text):
        try:
            data = decoder.raw_decode(text, start.end() - 1)[0]
        except ValueError:
            continue
        if isinstance(data, dict) and "loggedIn" in data:
            return data
    return None


_LINE_STARTS = re.compile(r"^[\ufeff \t]*\{", re.MULTILINE)
#: Terminal control codes: CSI ("ESC [2G", cursor to column 2), OSC (a
#: window title, ended by BEL or ESC \) and other escapes ("ESC 7" and
#: "ESC 8" save and restore the cursor, "ESC ( B" picks a character set).
#: A user's claude printed "ESC [2G" right after its JSON answer, so the
#: answer was taken as unreadable; one printed just before the "{" would hide
#: it too. JSON can't hold a raw ESC, so none of the answer is ever removed.
_TERMINAL_CODES = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[ -/]*[0-~])")


def _plain(text: str) -> str:
    """``text`` without terminal control codes."""
    return _TERMINAL_CODES.sub("", text)


def _no_answer(executable: str, out) -> str:
    """Why ``claude auth status`` gave no answer we could use, from what it
    printed. A version from before ``auth status`` existed says "unknown
    command". An answer in JSON is spread over lines whose first is just
    "{", so when the JSON couldn't be read, quoting it would say nothing."""
    # A byte-order mark isn't whitespace to strip(), and looks like nothing;
    # nor is a terminal control code anything to read out.
    stdout, stderr = _plain(out.stdout or ""), _plain(out.stderr or "")
    lines = (stderr or stdout).replace("\ufeff", "").splitlines()
    said = next((line.strip() for line in lines if line.strip()), "")
    if "unknown" in said.lower() and ("command" in said.lower() or "option" in said.lower()):
        return (f"Claude Code ({executable}) is too old to say. Update it by running "
                "claude update in a terminal, then try again")
    if "{" in stdout:
        return (f"Claude Code ({executable}) gave an answer The Chat Place couldn't read "
                f"(exit code {out.returncode}). Run claude auth status in a terminal to see it")
    if said:
        return f"Claude Code ({executable}) said \"{said[:200]}\""
    return f"Claude Code ({executable}) gave no answer (exit code {out.returncode})"


def describe(status: SignIn) -> str:
    if status.missing:
        return "\n\n".join(part for part in (status.problem, status.install_help) if part)
    if not status.known:
        return f"Couldn't tell whether Claude Code is signed in: {status.problem}."
    if not status.signed_in:
        return ("Claude Code isn't signed in. Your sessions still show, but sending a message "
                "needs you to sign in.")
    who = f" as {status.email}" if status.email else ""
    if status.method == "oauth_token":
        return "Claude Code is using a long-lived token from your Claude subscription."
    if status.subscription:
        plan = _PLANS.get(status.plan.lower(), "")
        plan = f"your Claude {plan} plan" if plan else "your Claude subscription"
        return f"Claude Code is signed in to {plan}{who}."
    if status.method == "third_party":
        how = _PROVIDERS.get(status.provider.lower(), "a cloud provider")
    elif status.method == "api_key":
        how = "an Anthropic API key"
    else:
        how = "something other than your Claude account"
    return (f"Claude Code is set up to use {how}, not a Claude subscription. The Chat Place "
            "only runs turns on a subscription, so sign in with your Claude account.")


def login_command(executable: Optional[str] = None) -> Optional[List[str]]:
    executable = executable or platform_paths.claude_executable()
    return [executable, "auth", "login", "--claudeai"] if executable else None
