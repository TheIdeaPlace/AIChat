"""The Chat Place's own sessions: the ones it started, and so the only ones it
may send messages into.

Claude sessions stay in the version-1 ``sessions.json`` understood by 0.1.6.
Other providers use ``provider-sessions.json``, which older releases never
read. Each file is replaced atomically; mixed-format migration preserves an
exact backup and writes foreign records before removing them from the old file.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Dict, List, Optional

from . import platform_paths
from .provider_storage import PartitionedFile
from .claude_cli import EFFORT_LABELS, MODEL_LABELS, is_safe_rule
from .sessions import IDLE, OWN, SessionInfo


@dataclass
class OwnSession:
    cli_session_id: str
    title: str
    cwd: str
    permission_mode: str = "auto"
    created_ms: int = 0
    last_activity_ms: int = 0
    desktop_session_id: str = ""   # set only if the desktop app ever adopts it
    state: str = IDLE              # idle / needs you (working is never stored)
    detail: str = ""
    unread: bool = False
    #: False until the CLI has reported the session (its first turn got as far
    #: as system/init). Until then the session doesn't exist in Claude Code,
    #: and the next Send starts it again instead of resuming it.
    started: bool = True
    #: ``--model`` for every turn ("" is Claude Code's own default). Older
    #: stores don't have it and load as the default.
    model: str = ""
    #: ``--effort`` for every turn (#189), "" for Claude Code's own setting.
    effort: str = ""
    #: Permission rules chosen with "Allow for this session" (#187), given
    #: to every later turn as ``--allowedTools``.
    allowed_tools: List[str] = field(default_factory=list)
    #: Continued from another session (#189): its Claude Code id, used to
    #: start the copy again if the first turn never got going, and its title,
    #: for the heading.
    fork_source: str = ""
    forked_from: str = ""
    #: Remote Control (#72): "on" or "off" for this session, "" for the
    #: Settings default. The Remote Control session it was given, joined again
    #: every turn, and its claude.ai address.
    remote_control: str = ""
    bridge_session_id: str = ""
    remote_url: str = ""
    provider: str = "claude"
    provider_options: dict = field(default_factory=dict)

    @property
    def identity(self) -> tuple[str, str]:
        return self.provider, self.cli_session_id

    def to_info(self) -> SessionInfo:
        return SessionInfo(
            source=OWN,
            key=(f"own:{self.cli_session_id}" if self.provider == "claude"
                 else f"{self.provider}:{self.cli_session_id}"),
            title=self.title,
            cwd=self.cwd,
            cli_session_id=self.cli_session_id,
            desktop_session_id=self.desktop_session_id,
            last_activity_ms=self.last_activity_ms or self.created_ms,
            created_ms=self.created_ms,
            state=self.state,
            detail=self.detail,
            permission_mode=self.permission_mode,
            unread=self.unread,
            provider=self.provider,
        )


_FIELDS = {f.name for f in fields(OwnSession)}
_TYPES = {f.name: f.type for f in fields(OwnSession)}
_DEFAULTS = {f.name: f.default for f in fields(OwnSession)}


def _session_from_dict(item: dict) -> Optional[OwnSession]:
    """A stored session, or None. Wrong-typed optional fields fall back to
    their defaults; a wrong-typed id, title or folder drops the entry."""
    cli_id = item.get("cli_session_id")
    provider = item.get("provider", "claude")
    if provider not in ("claude", "codex"):
        return None
    if not isinstance(cli_id, str) or not platform_paths.is_safe_id(cli_id):
        return None
    if not isinstance(item.get("title"), str) or not isinstance(item.get("cwd"), str):
        return None
    values = {}
    for name in _FIELDS:
        if name not in item:
            continue
        if name == "provider_options":
            options = item[name]
            if not isinstance(options, dict):
                return None
            # Separate backend settings; never interpret Codex policy names as
            # Claude permission modes. Runtime model support needs validation.
            allowed = {"model", "effort", "sandbox", "approval_policy", "workspace_root"}
            if (provider == "claude" and options
                    or any(k not in allowed or not isinstance(v, str)
                           for k, v in options.items())):
                return None
            values[name] = dict(options)
            continue
        if name == "allowed_tools":
            # Only rules Claude Code itself could have suggested; anything
            # else in a hand-edited file is dropped.
            rules = item[name] if isinstance(item[name], list) else []
            values[name] = [r for r in rules if is_safe_rule(r)]
            continue
        value = item[name]
        kind = _TYPES[name]
        if kind in ("str", str):
            ok = isinstance(value, str)
        elif kind in ("bool", bool):
            ok = isinstance(value, bool)
        elif kind in ("int", int):
            ok = isinstance(value, int) and not isinstance(value, bool)
        else:
            ok = True
        values[name] = value if ok else _DEFAULTS[name]
    # Only a model the picker offers: anything else (a hand edit, or a model
    # that bills usage credits) would fail or cost extra on every turn.
    if values.get("model") not in MODEL_LABELS:
        values["model"] = ""
    if values.get("effort") not in EFFORT_LABELS:
        values["effort"] = ""
    if values.get("fork_source") and not platform_paths.is_safe_id(values["fork_source"]):
        values["fork_source"] = ""
    if provider == "codex":
        # These values drive only Claude runners. A foreign record must never
        # carry permissions or Remote Control into that legacy path.
        for name in ("model", "effort", "fork_source", "remote_control",
                     "bridge_session_id", "remote_url", "desktop_session_id"):
            values[name] = ""
        values["allowed_tools"] = []
        values["permission_mode"] = "auto"
    return OwnSession(**values)


class OwnSessionStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else platform_paths.app_data_dir() / "sessions.json"
        self._files = PartitionedFile(self.path)
        self._sessions: Dict[tuple[str, str], OwnSession] = {}
        self._needs_backup = False
        self.migration_backup: Optional[Path] = None
        self.load_error = ""
        self._save_blocked = False
        self.load()

    # -- persistence ----------------------------------------------------------

    def load(self) -> None:
        self._sessions = {}
        self.load_error = ""
        try:
            foreign = self._files.read_foreign()
        except (OSError, ValueError) as exc:
            self.load_error = f"Couldn't read provider sessions: {exc}. Changes won't be saved."
            self._save_blocked = True
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {"version": 1, "sessions": []}
        except OSError as exc:
            # Locked or unreadable right now (antivirus, a sync tool): the
            # data may be fine, so never save over it this run.
            self.load_error = (f"Couldn't read The Chat Place's session list ({self.path}): "
                               f"{exc}. Changes won't be saved until The Chat Place is "
                               "restarted and can read it.")
            self._save_blocked = True
            return
        except ValueError as exc:
            self._set_aside(f"isn't valid JSON ({exc})")
            if self._save_blocked:
                return
            raw = {"version": 1, "sessions": []}
        items = raw.get("sessions") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            self._set_aside("isn't in the expected format")
            if self._save_blocked:
                return
            raw, items = {"version": 1, "sessions": []}, []
        version = raw.get("version", 1)
        if type(version) is not int or version not in (1, 2):
            self.load_error = "Unsupported session store version. Changes won't be saved."
            self._save_blocked = True
            return
        self._needs_backup = version == 2
        mixed = False
        for item in items:
            if not isinstance(item, dict):
                if version == 2:
                    self._sessions = {}
                    self.load_error = "Invalid provider session store. Changes won't be saved."
                    self._save_blocked = True
                    return
                continue
            if item.get("provider", "claude") not in ("claude", "codex"):
                self.load_error = "Unsupported provider session. Changes won't be saved."
                self._save_blocked = True
                return
            session = _session_from_dict(item)
            if (version == 2 or item.get("provider", "claude") != "claude") and (
                    session is None or session.identity in self._sessions):
                self._sessions = {}
                self.load_error = "Invalid provider session store. Changes won't be saved."
                self._save_blocked = True
                return
            if session is not None:
                mixed |= session.provider != "claude"
                self._sessions[session.identity] = session
        if foreign is not None:
            entries = foreign.get("sessions")
            if foreign.get("version") != 2 or not isinstance(entries, list):
                self.load_error = "Invalid separate provider session store. Changes won't be saved."
                self._save_blocked = True
                return
            seen = set()
            for item in entries:
                session = _session_from_dict(item) if isinstance(item, dict) else None
                if session is None or session.provider == "claude" or session.identity in seen:
                    self.load_error = "Invalid separate provider session store. Changes won't be saved."
                    self._save_blocked = True
                    return
                seen.add(session.identity)
                existing = self._sessions.get(session.identity)
                if existing is not None and existing != session:
                    self.load_error = "Conflicting provider sessions. Changes won't be saved."
                    self._save_blocked = True
                    return
                self._sessions[session.identity] = session
        self._files.baseline(self._legacy_payload())
        # Evacuate the experimental mixed format before an older app can read
        # it again. Write the provider file first; a retry can merge duplicates.
        if version == 2 or mixed:
            self._needs_backup = True
            try:
                self.save(force_legacy=True)
            except OSError as exc:
                self.load_error = f"Couldn't separate provider sessions: {exc}. Changes won't be saved."
                self._save_blocked = True

    def _set_aside(self, why: str) -> None:
        """Move an unreadable store out of the way, so the next save can't
        overwrite whatever is in it, and say where it went."""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(f"{self.path.name}.bad-{stamp}")
        try:
            os.replace(self.path, backup)
            self.load_error = (f"The Chat Place's session list ({self.path}) {why}. It was "
                               f"moved to {backup.name} in the same folder, and The Chat Place "
                               "started a new list. Its sessions' transcripts are untouched.")
        except OSError as exc:
            self.load_error = (f"The Chat Place's session list ({self.path}) {why}, and it "
                               f"couldn't be moved aside ({exc}). Changes won't be saved "
                               "until it is fixed or removed.")
            self._save_blocked = True

    def _legacy_payload(self) -> dict:
        records = []
        for session in self._sessions.values():
            if session.provider == "claude":
                item = asdict(session)
                item.pop("provider")
                item.pop("provider_options")
                records.append(item)
        return {"version": 1, "sessions": records}

    def save(self, *, force_legacy: bool = False) -> None:
        if self._save_blocked:
            raise OSError(f"Not saving over the unreadable {self.path}.")
        if self._needs_backup and self.path.exists():
            self.migration_backup = platform_paths.backup_session_store(self.path)
        foreign = {"version": 2, "sessions": [asdict(s) for s in self._sessions.values()
                                                if s.provider != "claude"]}
        self._files.save(self._legacy_payload(), foreign, force_legacy=force_legacy)
        self._needs_backup = False

    # -- access -----------------------------------------------------------------

    def all(self, provider: Optional[str] = "claude") -> List[OwnSession]:
        """Legacy callers see only Claude; None explicitly requests all agents."""
        return [s for s in self._sessions.values()
                if provider is None or s.provider == provider]

    def get(self, cli_session_id: str, provider: str = "claude") -> Optional[OwnSession]:
        return self._sessions.get((provider, cli_session_id))

    def contains(self, cli_session_id: str, provider: str = "claude") -> bool:
        return (provider, cli_session_id) in self._sessions

    def add(self, session: OwnSession) -> None:
        if not platform_paths.is_safe_id(session.cli_session_id):
            raise ValueError("Not a valid session id.")
        validated = _session_from_dict(asdict(session))
        if validated is None or validated != session:
            raise ValueError("Not valid provider session settings.")
        now = int(time.time() * 1000)
        session.created_ms = session.created_ms or now
        session.last_activity_ms = session.last_activity_ms or now
        self._sessions[session.identity] = session
        self.save()

    def rename_id(self, old_id: str, new_id: str, provider: str = "claude") -> None:
        """Claude reported a different session id than the one we asked for."""
        if not platform_paths.is_safe_id(new_id):
            return
        session = self._sessions.get((provider, old_id))
        if session is None:
            return
        if new_id != old_id and (provider, new_id) in self._sessions:
            raise ValueError("Session identity already exists.")
        self._sessions.pop((provider, old_id))
        session.cli_session_id = new_id
        self._sessions[session.identity] = session
        self.save()

    def update(self, cli_session_id: str, *, provider: str = "claude", **changes) -> None:
        session = self.get(cli_session_id, provider)
        if session is None:
            return
        allowed = {name: value for name, value in changes.items()
                   if name in _FIELDS and name not in ("cli_session_id", "provider")}
        updated = replace(session, **allowed)
        validated = _session_from_dict(asdict(updated))
        if validated is None or validated != updated:
            raise ValueError("Not valid provider session settings.")
        for name, value in allowed.items():
            setattr(session, name, value)
        self.save()

    def remove(self, cli_session_id: str, provider: str = "claude") -> None:
        """Remove a session (File, Delete Session Permanently) (its transcript is left alone)."""
        if self._sessions.pop((provider, cli_session_id), None) is not None:
            self.save()
