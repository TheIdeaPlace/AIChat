"""Whether GitHub Copilot CLI is installed and signed in (#391).

Copilot CLI has ``copilot login`` but no status command, and nothing it
answers over ACP without a prompt checks the sign-in with GitHub:
``initialize`` and ``session/list`` don't, and ``session/new`` names the
account from what's stored and leaves an empty session folder behind
(probed on 1.0.103, #391). So this reads the account Copilot CLI records in
``config.json`` (``lastLoggedInUser``; no tokens are kept there) and never
starts Copilot at all. Whether the sign-in still works is only known from a
chat's first message.
"""
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import platform_paths

#: Copilot CLI's own record of who has signed in (``copilot login``).
CONFIG_FILE = "config.json"
#: The host ``copilot login`` uses unless told another (``--host``).
GITHUB = "https://github.com"


@dataclass
class SignIn:
    known: bool
    signed_in: bool = False
    login: str = ""
    host: str = ""
    problem: str = ""
    missing: bool = False
    #: A ``copilot`` was found, but not one The Chat Place runs (an npm
    #: copy's ``copilot.cmd`` or Node script).
    unusable: str = ""


def install_command() -> str:
    if sys.platform == "darwin":
        return "brew install copilot-cli"
    return "winget install GitHub.Copilot"


def _unusable_copilot(which=None) -> str:
    """A ``copilot`` on the PATH that find_copilot() turned down, usually
    npm's, which is refused like Claude Code's (#352)."""
    found = (which or shutil.which)("copilot")
    return str(found) if found else ""


def _read_config(path: Path) -> dict:
    """``config.json`` is JSON with whole-line ``//`` comments at the top
    ("This file is managed automatically")."""
    text = path.read_text(encoding="utf-8-sig")  # an editor may have added a BOM
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))
    data = json.loads(body) if body.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def check(executable: Optional[str] = None, home: Optional[Path] = None, which=None) -> SignIn:
    executable = executable or platform_paths.find_copilot()
    if not executable:
        unusable = _unusable_copilot(which)
        return SignIn(False, missing=True, unusable=unusable)
    path = (home or platform_paths.copilot_home()) / CONFIG_FILE
    try:
        data = _read_config(path)
    except FileNotFoundError:
        return SignIn(True)  # never started, so never signed in
    except (OSError, UnicodeDecodeError):
        return SignIn(False, problem=f"Copilot CLI's settings file ({path}) couldn't be read")
    except ValueError:
        return SignIn(False, problem=f"Copilot CLI's settings file ({path}) "
                                     "isn't in the expected form")
    user = data.get("lastLoggedInUser")
    if user is None:
        return SignIn(True)
    if not isinstance(user, dict):
        return SignIn(False, problem=f"Copilot CLI's settings file ({path}) "
                                     "isn't in the expected form")
    login, host = user.get("login"), user.get("host")
    if not isinstance(login, str) or not login.strip():
        return SignIn(True)
    login = login.strip()
    host = host.strip() if isinstance(host, str) and host.strip() else GITHUB
    # The last account is only signed in while Copilot still lists it: a
    # sign-out may leave lastLoggedInUser behind.
    users = data.get("loggedInUsers")
    if isinstance(users, list) and not any(
            isinstance(entry, dict) and entry.get("login") == login
            and _host_name(entry.get("host") or GITHUB) == _host_name(host)
            for entry in users):
        return SignIn(True)
    return SignIn(True, signed_in=True, login=login, host=host)


def _looks_like_npm(path: str) -> bool:
    lowered = path.replace("\\", "/").lower()
    return lowered.endswith((".cmd", ".ps1")) or "node_modules" in lowered or "/npm/" in lowered


def _host_name(host: str) -> str:
    return host.split("://", 1)[-1].rstrip("/")


def describe(status: SignIn) -> str:
    if status.missing:
        install = install_command()
        if status.unusable:
            kind = "; an npm install" if _looks_like_npm(status.unusable) else ""
            return ("Copilot CLI was found, but it's a copy The Chat Place doesn't run "
                    f"({status.unusable}{kind}). It needs the native Copilot CLI: "
                    f"install it with {install}, then restart The Chat Place.")
        return (f"Copilot CLI isn't installed. Install it with {install}, "
                "then restart The Chat Place.")
    if not status.known:
        return f"Couldn't tell whether Copilot CLI is signed in: {status.problem}."
    if status.signed_in:
        return f"Copilot CLI is signed in to {_host_name(status.host)} as {status.login}."
    # Provisional (#391): what a chat gets when Copilot isn't signed in is
    # still to be seen on a clean machine.
    return ("Copilot CLI isn't signed in. Copilot chats can still be read, but starting one "
            "or sending a message needs you to sign in to GitHub.")


def login_command(executable: Optional[str] = None) -> Optional[List[str]]:
    executable = executable or platform_paths.find_copilot()
    return [executable, "login"] if executable else None
