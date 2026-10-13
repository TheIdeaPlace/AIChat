"""Links to sessions (#144): ``thechatplace://session/<id>``.

Copy Session Link puts one on the clipboard, and following one (from the
command line, a Windows or Mac link, or a link in a message) brings The Chat
Place forward with that session selected and its messages loaded.

A link is only ever a way to find a session. Following one does what Enter
on the session does, and no more: it never sends a message, runs a turn or
changes a conversation, so a link from anywhere is safe to follow; anything
that isn't exactly the form below is refused.

The id in a link is the one that stays put for the life of the session:

* one of The Chat Place's own sessions: its Claude Code session id (the
  transcript's name, a UUID);
* a Claude desktop app session, Code or Cowork: the desktop app's own id
  (``local_...``), the one its claude:// links use;
* a session started in a terminal (#158): its Claude Code session id, as
  for The Chat Place's own.

Nothing here imports wx, so it's all tested without a window.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional

from . import platform_paths

SCHEME = "thechatplace"
PREFIX = f"{SCHEME}://session/"
#: A plain id: letters, digits, "_" and "-", not starting with "-", at most
#: 100 characters (platform_paths' safe id). One trailing "/" is allowed,
#: because some programs add one to a link; nothing else (no query, no
#: fragment, no %-escapes, no spaces, quotes or dots).
_LINK = re.compile(r"thechatplace://session/([A-Za-z0-9][A-Za-z0-9_-]{0,99})/?",
                   re.IGNORECASE | re.ASCII)
#: Longer than any link of ours could be, so a huge argument isn't matched.
MAX_LINK_LENGTH = 200

NOT_OURS = "That link isn't one The Chat Place knows."
NO_SESSION = "No session with that link."


def is_app_link(text: object) -> bool:
    """Whether ``text`` is for The Chat Place at all (its scheme), right or
    wrong. Such a link goes to the app's own handler, never to the shell,
    which would only start The Chat Place again."""
    return isinstance(text, str) and text.strip().lower().startswith(f"{SCHEME}:")


def parse_link(text: object) -> Optional[str]:
    """The session id in a link, or None for anything that isn't exactly one
    of ours."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if len(text) > MAX_LINK_LENGTH:
        return None
    match = _LINK.fullmatch(text)
    return match.group(1) if match else None


def link_id(info) -> str:
    """The id a session's link carries ("" if it has none to give): Claude
    Code's id for The Chat Place's own sessions and terminal ones (#158),
    which have no other, and the desktop app's for its sessions."""
    if info.is_own or info.is_terminal:
        return info.cli_session_id if platform_paths.is_safe_id(info.cli_session_id) else ""
    return info.desktop_session_id if platform_paths.is_safe_id(info.desktop_session_id) else ""


def session_link(info) -> str:
    """The thechatplace:// link to a session, or "" if it can't have one."""
    session_id = link_id(info)
    return PREFIX + session_id if session_id else ""


def find_session(sessions: Iterable, session_id: str):
    """The session a link's id names, or None. The Chat Place's own sessions
    first (by their Claude Code id), then desktop app ones (by theirs), then
    terminal ones (#158, by their Claude Code id). Ids
    are compared ignoring case: they're UUIDs, and a program passing the
    link on may change its case."""
    wanted = (session_id or "").casefold()
    if not wanted:
        return None
    sessions = list(sessions)
    for info in sessions:
        if info.is_own and info.cli_session_id.casefold() == wanted:
            return info
    for info in sessions:
        if not info.is_own and info.desktop_session_id.casefold() == wanted:
            return info
    for info in sessions:
        if info.is_terminal and info.cli_session_id.casefold() == wanted:
            return info
    return None


def link_argument(argv: Iterable[str]) -> Optional[str]:
    """The first command-line argument that is a thechatplace: link (good or
    bad), or None. When there is one, every other argument is ignored: a
    link Windows passes in can't smuggle in another option."""
    for arg in argv:
        if is_app_link(arg):
            return arg.strip()[:MAX_LINK_LENGTH + 1]
    return None


# -- copying (Copy Session Link) -----------------------------------------------------------


def markdown_link(title: str, url: str) -> str:
    """``[title](url)``, with the title's brackets and backslashes escaped so
    it can't end the link text early, and line breaks made spaces."""
    title = " ".join(str(title or "").split()) or "Session"
    title = re.sub(r"([\\\[\]])", r"\\\1", title)
    return f"[{title}]({url})"


@dataclass(frozen=True)
class LinkChoice:
    label: str       # what the list says
    text: str        # what's copied
    spoken: str      # what's said once it's copied


def copy_choices(info, claude_url: str = "", remote_url: str = "",
                 chat_place: bool = True) -> List[LinkChoice]:
    """What Copy Session Link offers, best first: each link as a Markdown
    link, then the same link bare. The first is the default, so the usual
    copy is Ctrl+Shift+L then Enter.

    ``claude_url`` is the desktop app's claude:// link (desktop and Cowork
    sessions), ``remote_url`` a Chat Place session's claude.ai address (on
    Remote Control)."""
    title = " ".join(str(info.title or "").split()) or "Session"
    kinds = []  # (label, link, said for the Markdown link, said for the bare one)
    own_link = session_link(info) if chat_place else ""
    if own_link:
        kinds.append(("Open in The Chat Place", own_link,
                      f"Copied a Markdown link to {title}, to open it in The Chat Place.",
                      f"Copied the link to {title}, to open it in The Chat Place."))
    if claude_url:
        kinds.append(("Open in Claude", claude_url,
                      f"Copied a Markdown link to {title}, to open it in the Claude desktop "
                      "app.",
                      f"Copied the link to {title}, to open it in the Claude desktop app."))
    if remote_url:
        kinds.append(("Its claude.ai address", remote_url,
                      f"Copied a Markdown link to {title}'s claude.ai address.",
                      f"Copied {title}'s claude.ai address."))
    choices = [LinkChoice(f"{label}, as a Markdown link", markdown_link(title, url), said)
               for label, url, said, _bare in kinds]
    choices += [LinkChoice(f"{label}, the link only ({url})", url, bare)
                for label, url, _said, bare in kinds]
    return choices


# -- Windows and Linux registration (installed copies only) -------------------------------
#
# Linux (#144 on Linux): an AppImage is the installed copy, and its desktop
# entry (``~/.local/share/applications/thechatplace.desktop``) both puts it
# in the desktop's menu and claims thechatplace:// for it. A Mac registers
# through the app's Info.plist instead.

#: Characters a path can't have in a Windows command or a desktop entry's
#: Exec line without escaping, which isn't worth getting wrong.
_UNQUOTABLE = {"win32": '"', "linux": '"`$\\\n%'}


def scheme_command(executable: str, platform: str = sys.platform) -> str:
    """What Windows or Linux runs for a link: the app, quoted, and the link
    as its one argument. Raises ValueError for a path a quote could break
    out of."""
    platform = "linux" if platform.startswith("linux") else platform
    if not executable or any(c in executable for c in _UNQUOTABLE.get(platform, '"')):
        raise ValueError(f"can't register {executable!r}")
    if platform == "linux":
        # Quoted only when it must be: xdg-open, without a desktop of its
        # own to ask, can't start a quoted program.
        if re.search(r"[\s'<>~|&;*?#()]", executable):
            return f'"{executable}" %u'
        return f"{executable} %u"
    return f'"{executable}" "%1"'


def should_register(installed: bool, platform: str = sys.platform) -> bool:
    """Only an installed copy registers the scheme: on Windows the one
    Velopack installed, on Linux an AppImage. A portable copy or a source
    run would point every link at a copy that may be deleted or moved, and
    take links from the installed one."""
    return (platform == "win32" or platform.startswith("linux")) and installed


def _same_command(a: str, b: str, platform: str) -> bool:
    """Windows paths ignore case; Linux paths don't, so an AppImage moved to
    a folder whose name differs only in case is a different command."""
    return a.casefold() == b.casefold() if platform == "win32" else a == b


def ensure_registered(installed: bool, executable: str,
                      read: Optional[Callable[[], Optional[str]]] = None,
                      write: Optional[Callable[[str, str], None]] = None,
                      platform: str = sys.platform) -> str:
    """Register thechatplace:// for this user if it's missing or points
    somewhere else. Returns what happened: "not installed", "current",
    "registered" or "failed: ..."."""
    if not should_register(installed, platform):
        return "not installed"
    read = read or platform_paths.url_scheme_command
    write = write or platform_paths.register_url_scheme
    try:
        command = scheme_command(executable, platform)
    except ValueError as exc:
        return f"failed: {exc}"
    try:
        current = read()
        if current is not None and _same_command(current, command, platform):
            return "current"
        write(command, f'"{executable}",0' if platform == "win32" else "")
    except OSError as exc:
        return f"failed: {exc}"
    return "registered"


def remove_registration(executable: str,
                        read: Optional[Callable[[], Optional[str]]] = None,
                        remove: Optional[Callable[[], None]] = None,
                        platform: str = sys.platform) -> bool:
    """On uninstall: remove the registration if it's this copy's (it might
    be another install's, which is left alone). True if it was removed."""
    read = read or platform_paths.url_scheme_command
    remove = remove or platform_paths.unregister_url_scheme
    try:
        ours = scheme_command(executable, platform)
    except ValueError:
        return False
    current = read()
    if current is None or not _same_command(current, ours, platform):
        return False
    remove()
    return True
