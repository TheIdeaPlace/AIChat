"""The permission rules in Claude Code's own settings that apply to a chat's
folder, read only, for File, Chat Permissions (#336).

The Chat Place never writes these files. Every turn it runs is Claude Code
itself, so these rules apply to its chats as they do in the desktop app and a
terminal. Checked with Claude Code 2.1.296 (``claude -p --debug``):

- your user settings (``~/.claude/settings.json``) apply everywhere;
- the chat folder's ``.claude/settings.local.json`` applies;
- the chat folder's shared ``.claude/settings.json`` applies, but its allow
  rules are ignored until the folder is trusted in Claude Code ("this
  workspace has not been trusted"); trust is recorded per exact folder;
- a parent folder's ``.claude`` settings don't apply to a subfolder;
- an administrator's managed settings apply everywhere.

No wx here, so the reading and the wording can be tested on their own.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import List, Optional, Tuple

from . import platform_paths
from .chat_permissions import describe_claude_rule

#: What each kind of rule means, as the list words it.
KINDS = (("allow", "Allowed without asking"), ("ask", "Always asks"), ("deny", "Never allowed"))

INTRO = ("From Claude Code's own settings. These apply to every Claude session working in this "
         "folder, here, in the desktop app or a terminal. The Chat Place only reads them; "
         "change them in Claude Code, for example with /permissions.")
NONE = "Claude Code's own settings allow nothing extra for this folder."


#: A settings file bigger than this isn't read: a project's own file is
#: small, and one from a cloned repository could be anything.
MAX_SIZE = 1024 * 1024

WAITING = ("Not applied until this exact folder is trusted in Claude Code (open a terminal in "
           "this folder, run claude once, and answer yes when it asks whether you trust the "
           "files in this folder): the allow rules in this folder's shared settings, ")
UNKNOWN = ("Applied only if this folder is trusted in Claude Code, and whether it is couldn't "
           "be checked: the allow rules in this folder's shared settings, ")


@dataclass(frozen=True)
class SettingsRule:
    kind: str          # allow, ask or deny
    rule: str
    source: str        # "your settings", "this folder's shared settings", ...
    path: str
    applied: Optional[bool] = True   # None: depends on trust that couldn't be checked


def _rules(path: Path) -> Optional[dict]:
    """A settings file's ``permissions``, or None when it's missing or unreadable."""
    try:
        if path.stat().st_size > MAX_SIZE:
            return None
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    permissions = data.get("permissions") if isinstance(data, dict) else None
    return permissions if isinstance(permissions, dict) else None


def rules_for(folder: str) -> Tuple[List[SettingsRule], List[str]]:
    """The rules that apply to ``folder``, in Claude Code's order of
    sources, and any files that couldn't be read."""
    trusted = platform_paths.claude_folder_trusted(folder)
    found, unreadable = [], []
    for source, path, shared in platform_paths.claude_settings_files(folder):
        if not path.is_file():
            continue
        permissions = _rules(path)
        if permissions is None:
            unreadable.append(str(path))
            continue
        for kind, _label in KINDS:
            entries = permissions.get(kind)
            for rule in entries if isinstance(entries, list) else []:
                # One line, whatever a cloned repository's file holds, so
                # no rule can pass for another line of the note.
                text = " ".join(rule.split()) if isinstance(rule, str) else ""
                if text:
                    # Claude Code ignores a shared project file's allow
                    # rules until the folder is trusted.
                    applied = True
                    if shared and kind == "allow":
                        applied = None if trusted is None else trusted
                    found.append(SettingsRule(kind, text, source, str(path), applied))
    return found, unreadable


def describe(rules: List[SettingsRule], unreadable: List[str] = ()) -> str:
    """The note Chat Permissions shows, one line per rule, readable by line."""
    lines = [INTRO]
    applied = [r for r in rules if r.applied is True]
    if not applied:
        lines.append(NONE)
    for kind, label in KINDS:
        for r in (r for r in applied if r.kind == kind):
            lines.append(f"{label}: {describe_claude_rule(r.rule)} {r.rule}. From {r.source}, "
                         f"{r.path}.")
    for state, words in ((False, WAITING), (None, UNKNOWN)):
        waiting = [r for r in rules if r.applied is state]
        if waiting:
            lines.append(words + waiting[0].path + ":")
            lines.extend(f"{describe_claude_rule(r.rule)} {r.rule}." for r in waiting)
    for path in unreadable:
        lines.append(f"Couldn't read {path}, so its rules aren't listed.")
    return "\n".join(lines)
