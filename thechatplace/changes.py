"""What the providers changed in files, from their recorded changes alone.

Claude Code records each Edit, MultiEdit and Write it makes, and the result
it sends back carries the change as a patch ("structuredPatch": hunks of
lines starting " ", "-" or "+"). That works for desktop sessions too and
needs no git. When a result has no patch (an older Claude Code, or one that
was cut short), the change is worked out from the tool's own input.
"""
from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Shared with GHManage through app-kit (#174): the hunks, and how their lines
# are marked when read.
from theideaplace_appkit import difftext
from theideaplace_appkit.difftext import Hunk

#: The tools that change files.
EDIT_TOOLS = frozenset({"Edit", "MultiEdit", "Write"})


@dataclass
class FileEdit:
    """One successful Edit, MultiEdit or Write (or a provider's equivalent)."""
    path: str
    tool: str
    turn: int  # which of your messages it answered: 1 for the first
    hunks: List[Hunk] = field(default_factory=list)
    created: bool = False
    #: The file was deleted (Codex and Copilot can; Claude's edit tools can't).
    deleted: bool = False
    #: The old path when a provider renamed the file.
    moved_from: str = ""

    @property
    def added(self) -> int:
        return sum(h.added for h in self.hunks)

    @property
    def removed(self) -> int:
        return sum(h.removed for h in self.hunks)


@dataclass
class FileChanges:
    """Everything done to one file, in order."""
    path: str
    edits: List[FileEdit]

    @property
    def added(self) -> int:
        return sum(e.added for e in self.edits)

    @property
    def removed(self) -> int:
        return sum(e.removed for e in self.edits)

    @property
    def created(self) -> bool:
        return bool(self.edits) and self.edits[0].created

    @property
    def deleted(self) -> bool:
        return bool(self.edits) and self.edits[-1].deleted

    def counts(self) -> str:
        """"40 lines added, 12 removed", "created, 10 lines", "deleted", "renamed"."""
        if self.deleted:
            # Its lines are gone, so how many were added or removed first
            # says little; created here and deleted again says what happened.
            return "created, then deleted" if self.created else "deleted"
        if self.created:
            lines = self.added - self.removed
            return f"created, {lines} line{'s' if lines != 1 else ''}"
        parts = ["renamed"] if any(e.moved_from for e in self.edits) else []
        if self.added:
            parts.append(f"{self.added} line{'s' if self.added != 1 else ''} added")
        if self.removed:
            word = "removed" if self.added else \
                f"line{'s' if self.removed != 1 else ''} removed"
            parts.append(f"{self.removed} {word}")
        return ", ".join(parts) or "no lines changed"


def edit_from_tool(name: str, tool_input: dict, result, turn: int) -> Optional[FileEdit]:
    """A FileEdit from a tool call and what it returned (``toolUseResult``),
    or None for a tool that doesn't change files."""
    if name not in EDIT_TOOLS:
        return None
    result = result if isinstance(result, dict) else {}
    path = str(result.get("filePath") or tool_input.get("file_path") or "")
    if not path:
        return None
    edit = FileEdit(path, name, turn, created=result.get("type") == "create")
    patch = result.get("structuredPatch")
    if isinstance(patch, list) and patch:
        for hunk in patch:
            if isinstance(hunk, dict) and isinstance(hunk.get("lines"), list):
                edit.hunks.append(Hunk(_number(hunk.get("oldStart")),
                                       _number(hunk.get("newStart")),
                                       [str(line) for line in hunk["lines"]]))
        return edit
    if name == "Write":
        content = result.get("content", tool_input.get("content"))
        lines = _lines(str(content or ""))
        if result.get("type") == "update" and result.get("originalFile") is not None:
            edit.hunks = _diff(str(result["originalFile"]), str(content or ""))
        elif lines:
            edit.hunks = [Hunk(0, 1, ["+" + line for line in lines])]
        return edit
    pairs = []
    if name == "Edit":
        pairs = [(tool_input.get("old_string"), tool_input.get("new_string"))]
    elif isinstance(tool_input.get("edits"), list):
        pairs = [(e.get("old_string"), e.get("new_string"))
                 for e in tool_input["edits"] if isinstance(e, dict)]
    for old, new in pairs:
        edit.hunks.extend(_diff(str(old or ""), str(new or "")))
    return edit


_HUNK = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
#: The same line's counts (1 when left out, as unified diffs allow).
_HUNK_COUNTS = re.compile(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def parse_unified_diff(text: str) -> List[Hunk]:
    """Single-file hunks, including Codex's pathless diffs.

    The shared parser uses header counts to keep stripped blank context
    lines while ignoring blank separators between or after hunks.
    """
    return difftext.parse_unified(text)


def _diff_path(header: str) -> str:
    """"--- a/C:/x/y.txt" -> "C:/x/y.txt"; "" for /dev/null, which Copilot
    writes with the prefix too ("+++ b/dev/null")."""
    path = header[4:].strip()
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return "" if path in ("/dev/null", "dev/null") else path


def parse_git_diff(text: str) -> List[Tuple[str, List[Hunk]]]:
    """Each file's path and hunks, from a git-style unified diff with one
    section per file ("diff --git", "---", "+++", "@@ -1,4 +1,5 @@"). The
    path is the new one, or the old one for a file deleted ("+++ /dev/null").
    Lines that aren't part of a hunk are skipped, never guessed at.

    A plain unified diff, without "diff --git" lines, also starts a file at
    a "---" line followed by "+++", but only once the hunk before has had
    every line its "@@" line counts: inside a hunk, "--- x" is a removed
    line that starts with dashes, such as an SQL comment (#405, #406)."""
    files: List[Tuple[str, List[Hunk]]] = []
    old = new = ""
    hunks: Optional[List[Hunk]] = None
    # Lines the current hunk still has, old and new; None when its "@@"
    # line didn't say, and then only "diff --git" starts another file. A
    # bare blank line isn't counted as context: Copilot's own counts take
    # the newline ending a file as one more line, which then isn't there,
    # and counting the blank after a diff would add a line that isn't.
    left: Optional[List[int]] = None
    lines = (text or "").replace("\r\n", "\n").split("\n")
    for index, line in enumerate(lines):
        between = (hunks is not None and left is not None and left[0] <= 0 and left[1] <= 0
                   and line.startswith("--- ") and index + 1 < len(lines)
                   and lines[index + 1].startswith("+++ "))
        if line.startswith("diff --git ") or between:
            hunks, old, new, left = None, "", "", None
        if line.startswith("diff --git "):
            continue
        if line.startswith("--- ") and hunks is None:
            old = _diff_path(line)
        elif line.startswith("+++ ") and hunks is None:
            new = _diff_path(line)
            hunks = []
            files.append((new or old, hunks))
        elif hunks is not None and line.startswith("@@"):
            match = _HUNK.match(line)
            hunks.append(Hunk(int(match.group(1)) if match else 0,
                              int(match.group(2)) if match else 0, []))
            counted = _HUNK_COUNTS.match(line)
            left = ([int(counted.group(1) or 1), int(counted.group(2) or 1)]
                    if counted else None)
        elif hunks and line[:1] in (" ", "+", "-", "\\"):
            hunks[-1].lines.append(line)
            if left is not None:
                if line[:1] in (" ", "-"):
                    left[0] -= 1
                if line[:1] in (" ", "+"):
                    left[1] -= 1
    return [(path, found) for path, found in files if path]


def by_file(edits: List[FileEdit]) -> List[FileChanges]:
    """The edits grouped by file, files in the order first changed."""
    files: Dict[str, FileChanges] = {}
    for edit in edits:
        key = os.path.normcase(os.path.normpath(edit.path))
        if key not in files:
            files[key] = FileChanges(edit.path, [])
        files[key].edits.append(edit)
    return list(files.values())


def summary_text(edits: List[FileEdit], limit: int = 5) -> str:
    """"Changed 3 files: main_frame.py, 40 lines added, 12 removed;
    README.md, 4 lines added; …" "" when nothing changed."""
    files = by_file(edits)
    if not files:
        return ""
    count = len(files)
    parts = [f"{os.path.basename(f.path)}, {f.counts()}" for f in files[:limit]]
    more = f"; and {count - limit} more" if count > limit else ""
    return f"Changed {count} file{'s' if count != 1 else ''}: {'; '.join(parts)}{more}."


def file_text(changes: FileChanges, style: str = difftext.DEFAULT) -> str:
    """One file's changes, to read by line: where each change is, then its
    lines, each marked in ``style`` (a ``difftext.STYLES`` value; the
    default reads "Removed:", "Added:" and "Unchanged:")."""
    out: List[str] = [f"{changes.path}: {changes.counts()}."]
    for edit in changes.edits:
        if edit.moved_from:
            out.append(f"Renamed from: {edit.moved_from}")
        out.extend(difftext.render(edit.hunks, style))
    return "\n".join(out)


def _diff(old: str, new: str) -> List[Hunk]:
    """Hunks from two texts whose place in the file isn't known."""
    text = "\n".join(difflib.unified_diff(_lines(old), _lines(new), lineterm="", n=1))
    hunks = difftext.parse_unified(text)
    for hunk in hunks:
        hunk.old_start = hunk.new_start = 0  # the lines of a fragment, not of the file
    return hunks


def _lines(text: str) -> List[str]:
    lines = text.replace("\r\n", "\n").split("\n") if text else []
    if lines and lines[-1] == "":
        lines.pop()  # the newline ending the last line doesn't start another
    return lines


def _number(value) -> int:
    return value if isinstance(value, int) and value > 0 else 0
