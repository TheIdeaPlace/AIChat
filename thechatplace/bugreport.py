"""Reporting a bug from inside the app (#28), modelled on QuickMail's.

The report is what you type (a summary, what happened, what you expected,
steps) plus a snapshot of non-sensitive facts about the app: versions, the
speech route and announcement level, which view and sort the list uses, and
how many sessions of each kind it lists. Never a session's title, folder,
message text, or anything that names a person or path: the report becomes a
public issue.

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
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import __version__, platform_paths

#: Where issues are filed. One place to change when the app moves.
REPO = "TheIdeaPlace/AIChat"
#: The longest the body may be once it's URL-encoded: browsers and the shell
#: cut longer URLs (GitHub's limit is about 8 KB); the clipboard has it all.
MAX_URL_BODY = 6000
_VERSION = re.compile(r"^\d+\.\d+[\w.+-]*( \(Claude Code\))?$")


@dataclass
class BugReport:
    summary: str
    what_happened: str
    expected: str = ""
    steps: str = ""
    #: (label, value) facts about the app, from ``environment``.
    environment: List[tuple] = field(default_factory=list)


def claude_code_version() -> str:
    """"2.1.286 (Claude Code)", or why it isn't known. Quick, hidden."""
    path = platform_paths.claude_executable()
    if not path:
        return "not found"
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=8,
                             creationflags=platform_paths.hidden_window_flags())
        first = (out.stdout or "").strip().splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unknown"
    # Only a version number: an error message could carry a path or a name.
    return first if _VERSION.match(first) else "unknown"


def _system_name() -> str:
    """The label for the operating system line."""
    return {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, "System")


def environment(speech, counts: Dict[str, int], claude_version: Optional[str] = None,
                speech_route: str = "") -> List[tuple]:
    """Facts for the report. ``speech`` is the SpeechSettings; ``counts`` how
    many sessions of each kind are listed; ``speech_route`` what the last
    screen-reader announcement did (#98), which is how a report like "it
    speaks in the wrong voice" can be answered. No names, titles or paths."""
    try:
        import wx
        wx_version = wx.version()
    except Exception:  # noqa: BLE001
        wx_version = "not loaded"
    frozen = bool(getattr(sys, "frozen", False))
    facts = [
        ("The Chat Place", f"{__version__} ({'installed build' if frozen else 'run from source'})"),
        (_system_name(), platform_paths.os_name()),
        ("Python", platform.python_version()),
        ("wxPython", wx_version),
        ("Claude Code", claude_version if claude_version is not None else claude_code_version()),
        ("Announcements", f"{speech.announce}, speech engine {speech.engine}"),
        # A group's name is yours and may name people or work: never in a report.
        ("Session list", "showing " + ("a group" if speech.session_view.startswith("group:")
                                       else speech.session_view)
         + f", sorted {speech.session_order}"),
        ("Reading", "formatted full messages" if speech.formatted_messages else "plain text",),
    ]
    if speech_route:
        facts.append(("Last announcement", speech_route))
    if counts:
        facts.append(("Sessions listed", ", ".join(f"{n} {kind}" for kind, n in counts.items())))
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
