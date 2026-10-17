"""Whether Codex app-server reports the ChatGPT account turns require (#301)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from . import platform_paths
from .providers.codex_rpc import ReadOnlyCodexConnection, is_chatgpt_account


@dataclass
class SignIn:
    known: bool
    signed_in: bool = False
    method: str = ""
    problem: str = ""
    missing: bool = False


def check(executable: Optional[str] = None, connection_factory=ReadOnlyCodexConnection) -> SignIn:
    executable = executable or platform_paths.find_codex()
    if not executable:
        return SignIn(False, missing=True,
                      problem="Codex isn't installed. Install the Codex app or CLI, then restart.")
    try:
        with connection_factory(executable) as connection:
            account = connection.request("account/read", {"refreshToken": True})
    except Exception as exc:  # app-server errors are already safe, public diagnostics
        said = str(exc).strip() or "it failed"
        return SignIn(False, problem=f"Codex ({executable}) couldn't report its account: {said}")
    if is_chatgpt_account(account):
        return SignIn(True, signed_in=True, method="ChatGPT")
    value = account.get("account") if isinstance(account, dict) else None
    if value is None:
        return SignIn(True)
    if isinstance(value, dict):
        kind = value.get("type")
        method = "an API key" if kind == "apiKey" else "another credential"
        return SignIn(True, signed_in=True, method=method)
    return SignIn(False, problem=f"Codex ({executable}) returned an unreadable account status")


def describe(status: SignIn) -> str:
    if status.missing:
        return status.problem
    if not status.known:
        return f"Couldn't tell whether Codex is signed in: {status.problem}."
    if status.signed_in and status.method == "ChatGPT":
        return "Codex is signed in to ChatGPT."
    if status.signed_in:
        return (f"Codex is signed in using {status.method}, not ChatGPT. The Chat Place only "
                "runs Codex turns with your ChatGPT sign-in.")
    return ("Codex isn't signed in. Existing chats can still appear, but sending a message "
            "needs you to sign in to ChatGPT.")


def login_command(executable: Optional[str] = None) -> Optional[List[str]]:
    executable = executable or platform_paths.find_codex()
    return [executable, "login"] if executable else None
