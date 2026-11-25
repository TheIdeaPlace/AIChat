"""Muse Code executable and sign-in status for Help."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse

from . import platform_paths
from .muse_account import AccountConnection, MuseAccountError


@dataclass
class SignIn:
    known: bool
    signed_in: Optional[bool] = None
    method: str = ""
    version: str = ""
    problem: str = ""
    missing: bool = False
    unusable: str = ""
    install_help: str = ""
    account_state: str = ""
    credential_required: bool = False
    label: str = ""
    executable: str = ""

    @property
    def needs_login(self) -> bool:
        """Offer browser login only when no higher-priority credential exists."""
        return (self.signed_in is False and self.credential_required
                and self.account_state == "loggedOut")


_VERSION = re.compile(r"\bmuse(?:\s+code)?\s+v?(\d+(?:\.\d+){1,3})\b",
                      re.IGNORECASE)


@dataclass(frozen=True)
class LoginPrompt:
    verification_url: str
    user_code: str


class LoginFlow:
    """A device-code login that keeps one account host alive throughout."""

    def __init__(self, executable: str, connection_factory=AccountConnection):
        self._connection = connection_factory(executable)
        self._started = False
        self._outcome = ""

    def start(self) -> LoginPrompt:
        try:
            result = self._connection.request("account/loginStart", {"type": "deviceCode"})
        except MuseAccountError:
            self.close()
            raise
        url = result.get("verificationUrl") if isinstance(result, dict) else None
        code = result.get("userCode") if isinstance(result, dict) else None
        try:
            parsed = urlparse(url) if isinstance(url, str) and len(url) <= 8192 else None
            safe_url = (parsed is not None and parsed.scheme == "https" and bool(parsed.hostname)
                        and not parsed.username and not parsed.password
                        and not any(ord(char) < 32 for char in url))
        except ValueError:
            safe_url = False
        if (not safe_url or not isinstance(code, str) or not (1 <= len(code) <= 128)
                or any(ord(char) < 32 for char in code)):
            self.close()
            raise MuseAccountError("Muse Code didn't provide a sign-in code.")
        self._started = True
        return LoginPrompt(url, code)

    def wait(self, cancel_event=None, timeout=600.) -> str:
        if self._outcome:
            return self._outcome
        if not self._started:
            return "failed"
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                return self.cancel()
            try:
                event = self._connection.next_notification(
                    timeout=min(1., max(0., deadline - time.monotonic())))
            except MuseAccountError:
                self._outcome = "failed"
                return self._outcome
            if not isinstance(event, dict) or event.get("method") != "account/loginCompleted":
                continue
            params = event.get("params")
            outcome = params.get("outcome") if isinstance(params, dict) else None
            if outcome in {"granted", "denied", "expired", "cancelled", "failed"}:
                self._outcome = outcome
                return outcome
            self._outcome = "failed"
            return self._outcome
        self._outcome = "timeout"
        return self._outcome

    def cancel(self) -> str:
        if self._outcome:
            return self._outcome
        try:
            if self._started:
                self._connection.request("account/loginCancel")
        except MuseAccountError:
            pass
        self._outcome = "cancelled"
        return self._outcome

    def close(self) -> None:
        try:
            self._connection.close()
        except Exception:
            pass


def _version(text: str) -> str:
    match = _VERSION.search(text or "")
    return match.group(1) if match else ""


def check(executable: Optional[str] = None, run=subprocess.run,
          connection_factory=None) -> SignIn:
    """Check identity and account status without workspace trust or inference."""
    if not executable:
        lookup = platform_paths.find_muse()
        if not lookup.path:
            return SignIn(False, signed_in=False, missing=not lookup.unusable,
                          unusable=lookup.unusable, problem=lookup.reason,
                          install_help=lookup.install_help)
        executable = lookup.path
    try:
        result = run([executable, "--version"], capture_output=True, text=True,
                     encoding="utf-8", errors="replace", timeout=10,
                     env=platform_paths.muse_child_environment(),
                     creationflags=platform_paths.hidden_window_flags())
    except subprocess.TimeoutExpired:
        return SignIn(False, signed_in=None,
                      problem=f"Muse Code ({executable}) didn't report its version in 10 seconds")
    except (OSError, subprocess.SubprocessError):
        return SignIn(False, signed_in=None,
                      problem="Muse Code couldn't be started")
    version = _version((result.stdout or "") + "\n" + (result.stderr or ""))
    if result.returncode != 0 or not version:
        return SignIn(False, signed_in=None,
                      problem=f"Muse Code ({executable}) couldn't report its version")
    factory = connection_factory or AccountConnection
    try:
        with factory(executable) as connection:
            account = connection.request("account/read")
    except MuseAccountError:
        return SignIn(False, signed_in=None, version=version,
                      problem="Muse Code couldn't report a compatible account status. "
                              "Update Muse Code and choose Tools, Sign In, Muse Code again")
    except (OSError, subprocess.SubprocessError):
        return SignIn(False, signed_in=None, version=version,
                      problem="Muse Code couldn't report its account status")
    if not isinstance(account, dict):
        return SignIn(False, signed_in=None, version=version,
                      problem="Muse Code returned an unreadable account status")
    state = account.get("state")
    label = account.get("label") if isinstance(account.get("label"), str) else ""
    raw_required = account.get("credentialRequired")
    if type(raw_required) is not bool:
        return SignIn(False, signed_in=None, version=version,
                      problem="Muse Code returned an unreadable account status")
    credential_required = raw_required
    if state == "accountLogin":
        return SignIn(True, signed_in=True, method=label or "Meta account", version=version,
                      account_state=state, credential_required=credential_required, label=label,
                      executable=executable)
    if state == "loggedOut":
        return SignIn(True, signed_in=False, version=version,
                      account_state=state, credential_required=credential_required,
                      executable=executable)
    if state == "apiKey":
        return SignIn(True, signed_in=False, method="an API key", version=version,
                      account_state=state, credential_required=credential_required,
                      executable=executable)
    if state == "envKey":
        return SignIn(True, signed_in=False, method="META_API_KEY", version=version,
                      account_state=state, credential_required=credential_required,
                      executable=executable)
    return SignIn(False, signed_in=None, version=version,
                  problem="Muse Code returned an unknown account status")


def describe(status: SignIn) -> str:
    if status.missing:
        suffix = "\n\n".join(part for part in (status.problem, status.install_help) if part)
        suffix = f"\n\n{suffix}" if suffix else ""
        return ("Muse Code wasn't found. Install Muse Code, then choose Tools, Sign In, Muse Code again."
                f"{suffix}")
    if status.unusable:
        return (f"Muse Code was found at {status.unusable}, but it isn't a native Muse Code "
                "program. Install the native Muse Code binary, then restart The Chat Place.")
    if not status.known:
        return f"Couldn't check Muse Code: {status.problem}."
    if status.signed_in is True:
        version = f" (version {status.version})" if status.version else ""
        return f"Muse Code is signed in{version}" + (f" as {status.label}." if status.label else ".")
    if status.signed_in is False:
        if status.method == "META_API_KEY":
            return ("Muse Code is using META_API_KEY from the environment. The Chat Place won't "
                    "use that override for its chats; sign in with Muse Code's browser flow.")
        if status.method == "an API key":
            return ("Muse Code is using a stored API key, which takes precedence over browser "
                    "sign-in. Manage that credential in Muse Code before checking again. "
                    "The Chat Place does not remove your stored credentials.")
        if not status.credential_required:
            return "Muse Code reports that its configured service does not require sign-in."
        return "Muse Code isn't signed in."
    version = f" (version {status.version})" if status.version else ""
    return (f"Muse Code is installed{version}, but its sign-in status is unknown. "
            "Update Muse Code and choose Tools, Sign In, Muse Code again.")


def login_command(executable: Optional[str] = None) -> Optional[List[str]]:
    executable = executable or platform_paths.find_muse().path
    return [executable] if executable else None
