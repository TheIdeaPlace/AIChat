"""Reporting a bug from inside the app (#28), modelled on QuickMail's.

The report is what you type (a summary, what happened, what you expected,
steps) plus a snapshot of non-sensitive facts about the app: versions, each
provider's command line and whether it's turned on and signed in, recent
provider errors, the speech route and announcement level, which view and sort
the list uses, the display, and how many sessions of each provider and kind
it lists (#464). Never a session's title, folder, message text, or anything
that names a person or path: the report becomes a public issue, so every fact
is a known shape (a version number, a value the app defines, a count), never
text passed through.

QuickMail sends reports through a small relay (a Cloudflare Worker holding a
GitHub App key), so nobody needs a GitHub account. The Chat Place has no relay
yet: the dialog opens a GitHub "new issue" page with the report filled in,
and copies the full report to the clipboard in case the page is cut short. A
relay would add a POST with a timeout, this page as the fallback, and its
rate-limit message.
"""
from __future__ import annotations

import platform
import re
import subprocess
import sys
import threading
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import __version__, platform_paths

#: Where issues are filed. One place to change when the app moves.
REPO = "TheIdeaPlace/AIChat"
#: The longest the body may be once it's URL-encoded: browsers and the shell
#: cut longer URLs (GitHub's limit is about 8 KB); the clipboard has it all.
MAX_URL_BODY = 6000
#: What each provider's ``--version`` prints first, with the number in group
#: 1: "2.1.286 (Claude Code)", "codex-cli 0.163.0", "GitHub Copilot CLI
#: 1.0.105." Anything else, such as an error naming a path, is "unknown".
_VERSIONS = {
    "claude": re.compile(r"^(\d+\.\d+[\w.+-]{0,40})( \(Claude Code\))?$"),
    "codex": re.compile(r"^codex-cli (\d+\.\d+[\w.+-]{0,40})$"),
    "copilot": re.compile(r"^GitHub Copilot CLI (\d+\.\d+[\w.+-]{0,40}?)\.?$"),
}
#: The providers the report describes, named as their command lines.
CLI_NAMES = {"claude": "Claude Code", "codex": "Codex", "copilot": "Copilot CLI"}
#: How the session counts name a row's provider and kind.
_PROVIDER_NAMES = {"claude": "Claude", "codex": "Codex", "copilot": "Copilot",
                   "agents": "Agent conversations", "imported": "Imported"}
_KIND_NAMES = {"own": "Chat Place", "terminal": "terminal", "kept": "kept",
               "editor": "editor", "command_line": "command line",
               "app_server": "other app", "external": "other app"}
#: What a sign-in that couldn't be found out says.
UNKNOWN_SIGN_IN = "sign-in unknown"


@dataclass
class BugReport:
    summary: str
    what_happened: str
    expected: str = ""
    steps: str = ""
    #: (label, value) facts about the app, from ``environment``.
    environment: List[tuple] = field(default_factory=list)


def _executable(provider: str) -> Optional[str]:
    # Looked up when asked, so tests can stand in for each finder.
    finder = {"claude": platform_paths.claude_executable, "codex": platform_paths.find_codex,
              "copilot": platform_paths.find_copilot}[provider]
    return finder()


def cli_version(provider: str) -> str:
    """A provider's command-line version, "not found", or "unknown" when it
    printed anything else. Quick, hidden. Claude Code's keeps the words
    reports have always given, "2.1.286 (Claude Code)"."""
    try:
        path = _executable(provider)
    except Exception:  # noqa: BLE001 - a report must still open
        return "unknown"
    if not path:
        return "not found"
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=8,
                             creationflags=platform_paths.hidden_window_flags())
        first = (out.stdout or "").strip().splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unknown"
    # Only a version number: an error message could carry a path or a name.
    match = _VERSIONS[provider].match(first)
    if not match:
        return "unknown"
    return first if provider == "claude" else match.group(1)


def claude_code_version() -> str:
    """"2.1.286 (Claude Code)", or why it isn't known."""
    return cli_version("claude")


def claude_sign_in(status) -> str:
    """Claude Code's sign-in in the app's own words: never the account."""
    from .signin import _PLANS
    if status.missing:
        return "not installed"
    if not status.known:
        return UNKNOWN_SIGN_IN
    if not status.signed_in:
        return "not signed in"
    if status.subscription:
        plan = _PLANS.get(status.plan.lower(), "")
        return f"signed in ({plan} plan)" if plan else "signed in (subscription)"
    return "signed in, not with a subscription"


def codex_sign_in(status) -> str:
    if status.missing:
        return "not installed"
    if not status.known:
        return UNKNOWN_SIGN_IN
    if status.usable:
        return "signed in (ChatGPT)"
    if status.settings_problem:
        return "signed in (ChatGPT), but its settings use another model provider"
    if status.signed_in:
        return "signed in, not with ChatGPT"
    return "not signed in"


def copilot_sign_in(status) -> str:
    from .copilot_signin import GITHUB
    if status.missing:
        return "found, but not a copy The Chat Place runs" if status.unusable else "not installed"
    if not status.known:
        return UNKNOWN_SIGN_IN
    if not status.signed_in:
        return "not signed in"
    # A GitHub Enterprise host can name the company: only say it's another.
    return ("signed in" if status.host.rstrip("/").lower() == GITHUB
            else "signed in (another GitHub host)")


def gather_sign_ins(codex_on: bool, timeout: float = 10.0,
                    checks: Optional[Dict[str, Callable[[], str]]] = None) -> Dict[str, str]:
    """Each provider's sign-in for a report, asked all at once. Codex only
    when it's turned on, since asking starts Codex. Slow: call it off the UI
    thread. One that hasn't answered within ``timeout`` is unknown."""
    if checks is None:
        from . import codex_signin, copilot_signin, signin
        checks = {"claude": lambda: claude_sign_in(signin.check()),
                  "copilot": lambda: copilot_sign_in(copilot_signin.check())}
        if codex_on:
            checks["codex"] = lambda: codex_sign_in(codex_signin.check())
    answers: Dict[str, str] = {}

    def ask(provider, check):
        try:
            answers[provider] = check()
        except Exception:  # noqa: BLE001 - always answer
            answers[provider] = UNKNOWN_SIGN_IN
    # Daemon threads: a check that never returns mustn't keep the app open.
    threads = [threading.Thread(target=ask, args=item, daemon=True) for item in checks.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout)  # they run at once, so this is about ``timeout`` in all
    return {provider: answers.get(provider, UNKNOWN_SIGN_IN) for provider in checks}


def install_kind(frozen: Optional[bool] = None) -> str:
    """How this copy runs, with its update channel: installed or portable
    (Windows), the app (Mac) or the AppImage (Linux), or from source."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not frozen:
        return "run from source"
    from . import updater
    if updater.PORTABLE_COPIES_EXIST:
        kind = "installed" if updater.is_installed_copy(__version__) else "portable"
    else:
        kind = {"darwin": "app", "linux": "AppImage"}.get(sys.platform, "built app")
    return f"{kind}, {updater.CHANNEL} updates"


def session_counts(sessions) -> Dict[str, Dict[str, int]]:
    """How many listed sessions of each provider and kind (#464), archived
    ones left out. Claude always gives its desktop app, Chat Place and
    terminal counts, as reports always have; other providers only what they
    have."""
    from .sessions import DESKTOP, TERMINAL
    listed = [s for s in sessions if not s.archived]
    # Cowork ones are desktop app sessions too: split only when there are some.
    cowork = any(s.cowork for s in listed)
    desktop = "desktop app Code" if cowork else "desktop app"
    claude = {desktop: 0, "desktop app Cowork": 0} if cowork else {desktop: 0}
    claude.update({"Chat Place": 0, "terminal": 0})
    counts: Dict[str, Dict[str, int]] = {"Claude": claude}
    for info in listed:
        if info.provider in ("agents", "imported"):
            kind = ""  # the provider's name says what they are
        elif info.cowork:
            kind = "desktop app Cowork"
        elif info.source == DESKTOP:
            kind = desktop
        elif info.source == TERMINAL and info.provider == "copilot":
            kind = "Copilot CLI"
        else:
            kind = _KIND_NAMES.get(info.source, "other")
        kinds = counts.setdefault(_PROVIDER_NAMES.get(info.provider, "Other"), {})
        kinds[kind] = kinds.get(kind, 0) + 1
    return counts


def _counts_text(counts: Dict[str, Dict[str, int]]) -> str:
    """"Claude: 0 desktop app, 20 Chat Place, 0 terminal; Codex: 12 Chat Place"."""
    return "; ".join(
        f"{provider}: " + ", ".join(f"{n} {kind}".rstrip() for kind, n in kinds.items())
        for provider, kinds in counts.items())


def ago(seconds: float) -> str:
    """"3 minutes ago": how long since, which is enough, and safer than the time."""
    seconds = max(0, int(seconds))
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'' if n == 1 else 's'} ago"
    return "just now"


def _system_name() -> str:
    """The label for the operating system line."""
    return {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}.get(sys.platform, "System")


def environment(speech, counts: Dict[str, Dict[str, int]],
                claude_version: Optional[str] = None, speech_route: str = "",
                versions: Optional[Dict[str, str]] = None,
                sign_ins: Optional[Dict[str, str]] = None,
                install: str = "", display: str = "",
                now: Optional[float] = None) -> List[tuple]:
    """Facts for the report. ``speech`` is the SpeechSettings; ``counts``
    from ``session_counts``; ``speech_route`` what the last screen-reader
    announcement did (#98), which is how a report like "it speaks in the
    wrong voice" can be answered; ``versions`` and ``sign_ins`` by provider
    (``cli_version``, ``gather_sign_ins``); ``install`` from
    ``install_kind``; ``display`` the scale and High Contrast. No names,
    titles or paths."""
    try:
        import wx
        wx_version = wx.version()
    except Exception:  # noqa: BLE001
        wx_version = "not loaded"
    frozen = bool(getattr(sys, "frozen", False))
    versions = dict(versions or {})
    if claude_version is not None:
        versions["claude"] = claude_version
    sign_ins = sign_ins or {}
    install = install or ("installed build" if frozen else "run from source")
    facts = [
        ("The Chat Place", f"{__version__} ({install})"),
        (_system_name(), platform_paths.os_name()),
        ("Python", platform.python_version()),
        ("wxPython", wx_version),
    ]
    # One line for each provider (#464): which version, whether it's turned
    # on, and whether it's signed in, so "Copilot preview off" reads apart
    # from "not signed in" and from "signed in and failing".
    codex_state = {False: "turned off", True: "turned on"}.get(
        speech.codex_enabled, "turned on (automatic)")
    copilot_state = "preview on" if speech.copilot_enabled else "preview off"
    for provider, state in (("claude", ""), ("codex", codex_state), ("copilot", copilot_state)):
        version = versions.get(provider)
        if version is None:
            version = claude_code_version() if provider == "claude" else "unknown"
        sign_in = sign_ins.get(provider, "") if version != "not found" else ""
        facts.append((CLI_NAMES[provider],
                      "; ".join(part for part in (version, state, sign_in) if part)))
    facts += [
        ("Announcements", f"{speech.announce}, speech engine {speech.engine}"),
        # A group's name is yours and may name people or work: never in a report.
        ("Session list", "showing " + ("a group" if speech.session_view.startswith("group:")
                                       else speech.session_view)
         + f", sorted {speech.session_order}"),
        ("Reading", "formatted full messages" if speech.formatted_messages else "plain text",),
    ]
    if display:
        facts.append(("Display", display))
    if speech_route:
        facts.append(("Last announcement", speech_route))
    from .codex_diagnostics import recent_failures
    # Each provider's own recent failures, saying how long ago (#464): a
    # Codex failure no longer hides an earlier Copilot one.
    for provider, failures in recent_failures(now=now).items():
        for seconds, what in failures:
            facts.append((f"{provider} error", f"{ago(seconds)}: {what}"))
    if counts:
        facts.append(("Sessions listed", _counts_text(counts)))
    return facts


def report_text(report: BugReport) -> str:
    """The issue body, in Markdown."""
    parts = ["### What happened", report.what_happened.strip() or "(not given)", ""]
    if report.expected.strip():
        parts += ["### What I expected", report.expected.strip(), ""]
    if report.steps.strip():
        parts += ["### Steps to reproduce", report.steps.strip(), ""]
    if report.environment:
        parts += ["### Environment"] + [f"- {label}: {value}" for label, value in report.environment]
        parts.append("")
    parts.append("_Reported from The Chat Place's Help, Report a Bug._")
    return "\n".join(parts)


def new_issue_url(report: BugReport) -> str:
    """A GitHub "new issue" page with the report filled in (cut to fit a
    URL; the clipboard holds all of it)."""
    body = report_text(report)
    if len(urllib.parse.quote(body)) > MAX_URL_BODY:
        # Shorten your words, never the environment: halve the longest field
        # until it fits once encoded (non-ASCII text grows a lot).
        note = "\n…(cut short: the whole report is on your clipboard; paste it over this)"
        fields = {"what_happened": report.what_happened, "expected": report.expected,
                  "steps": report.steps}
        while len(urllib.parse.quote(body)) > MAX_URL_BODY:
            longest = max(fields, key=lambda k: len(fields[k]))
            if len(fields[longest]) < 40:
                break
            fields[longest] = fields[longest][: len(fields[longest]) // 2]
            body = report_text(BugReport(report.summary, fields["what_happened"] + note,
                                         fields["expected"], fields["steps"],
                                         report.environment))
    query = urllib.parse.urlencode({"title": report.summary.strip(), "body": body,
                                    "labels": "bug"})
    return f"https://github.com/{REPO}/issues/new?{query}"
