"""Whether Codex is signed in to ChatGPT, from ``codex login status`` (#301)."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import List, Optional

from . import platform_paths
from .signin import _plain


@dataclass
class SignIn:
    known: bool
    signed_in: bool = False
    method: str = ""
    problem: str = ""
    missing: bool = False


def check(executable: Optional[str] = None, run=subprocess.run) -> SignIn:
    executable = executable or platform_paths.find_codex()
    if not executable:
        return SignIn(False, missing=True,
                      problem="Codex isn't installed. Install the Codex app or CLI, then restart.")
    try:
        out = run([executable, "login", "status"], capture_output=True, text=True,
                  encoding="utf-8", errors="replace", timeout=20,
                  env=platform_paths.codex_child_environment(),
                  creationflags=platform_paths.hidden_window_flags())
    except subprocess.TimeoutExpired:
        return SignIn(False, problem=f"Codex ({executable}) didn't answer in 20 seconds")
    except (OSError, subprocess.SubprocessError) as exc:
        return SignIn(False, problem=f"Codex ({executable}) couldn't be started: {exc}")
    text = _plain("\n".join(part for part in (out.stderr, out.stdout) if part)).strip()
    lines = tuple(line.strip() for line in text.splitlines() if line.strip())
    if "Logged in using ChatGPT" in lines:
        return SignIn(True, signed_in=True, method="ChatGPT")
    if "Not logged in" in lines:
        return SignIn(True)
    other = next((line.removeprefix("Logged in using ") for line in lines
                  if line.startswith("Logged in using ")), "")
    if other:
        # API-key status includes a masked key suffix. The UI only needs the
        # credential kind, and must not repeat even a partly redacted secret.
        if other.startswith("an API key"):
            other = "an API key"
        return SignIn(True, signed_in=True, method=other)
    said = lines[0][:200] if lines else ""
    problem = (f'Codex ({executable}) said "{said}"' if said else
               f"Codex ({executable}) gave no answer (exit code {out.returncode})")
    return SignIn(False, problem=problem)


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
