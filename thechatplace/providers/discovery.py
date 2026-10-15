"""Merge discovery without letting one unavailable backend erase the list.

Used on a background worker. Cached snapshots are private copies because the
window fills presentation fields on the sessions it receives (#184) (#171).
"""
from __future__ import annotations

from copy import deepcopy
import threading
from typing import Iterable

from ..sessions import SORT_STATUS, SessionInfo, sort_sessions
from .base import SessionProvider, Snapshot


class _DiscoveryPending(Exception):
    """An in-flight refresh is not evidence of a provider outage."""


class DiscoveryRegistry:
    def __init__(self, providers: Iterable[SessionProvider]):
        self._providers = {}
        for provider in providers:
            if provider.provider_id in self._providers:
                raise ValueError(f"Duplicate provider: {provider.provider_id}")
            self._providers[provider.provider_id] = provider
        self._cache: dict[str, Snapshot] = {}
        self._failed: set[str] = set()
        self._lock = threading.Lock()
        self._jobs = {}

    PROVIDER_WAIT_SECONDS = 0.05

    def _foreign_snapshot(self, provider_id, provider, last_messages):
        # At most one worker per provider. A stalled RPC cannot accumulate timer
        # workers or hold up Claude updates, including optional history columns.
        job = self._jobs.get(provider_id)
        if job is None:
            done, result = threading.Event(), []
            def run():
                try:
                    snapshot = deepcopy(provider.collect())
                    self._validate(provider_id, snapshot)
                    if last_messages:
                        fill = getattr(provider, "fill_last_messages", None)
                        if fill is not None:
                            fill(snapshot.sessions)
                    result.append(snapshot)
                except Exception:  # backend errors never enter UI diagnostics
                    pass
                finally:
                    done.set()
            job = done, result
            self._jobs[provider_id] = job
            threading.Thread(target=run, daemon=True,
                             name=f"{provider_id}-discovery").start()
        done, result = job
        if not done.wait(self.PROVIDER_WAIT_SECONDS):
            raise _DiscoveryPending()
        del self._jobs[provider_id]
        if not result:
            raise ValueError("Provider discovery unavailable")
        return result[0]

    @staticmethod
    def _validate(provider_id: str, snapshot: Snapshot) -> None:
        if not isinstance(snapshot, Snapshot):
            raise ValueError("Invalid provider snapshot")
        if not isinstance(snapshot.sessions, list):
            raise ValueError("Invalid provider session list")
        if type(snapshot.unreadable_files) is not int or snapshot.unreadable_files < 0:
            raise ValueError("Invalid provider unreadable file count")
        for session in snapshot.sessions:
            if not isinstance(session, SessionInfo) or session.provider != provider_id:
                raise ValueError("Provider returned another backend's session")
            # Check fields used in every sort mode before replacing the last
            # good cache. A malformed backend must not break the merged list.
            if (any(not isinstance(value, str) for value in
                    (session.key, session.title, session.repo, session.state))
                    or type(session.last_activity_ms) is not int
                    or session.last_activity_ms < 0):
                raise ValueError("Invalid provider session metadata")
        if provider_id != "claude" and (snapshot.desktop_cli_ids or snapshot.cowork_cli_ids
                                         or snapshot.live or snapshot.desktop_groups.names
                                         or snapshot.desktop_groups.by_session
                                         or not snapshot.desktop_groups.read_ok):
            raise ValueError("Foreign provider returned Claude ownership metadata")

    def collect(self, order: str = SORT_STATUS, *, last_messages=False) -> Snapshot:
        # Serialize overlapping timer refreshes so an older failure cannot
        # overwrite newer successful data or duplicate failure announcements.
        with self._lock:
            return self._collect(order, last_messages)

    def _collect(self, order: str, last_messages) -> Snapshot:
        merged = Snapshot()
        failed = set()
        for provider_id, provider in self._providers.items():
            try:
                snapshot = (deepcopy(provider.collect()) if provider_id == "claude"
                            else self._foreign_snapshot(provider_id, provider, last_messages))
                self._validate(provider_id, snapshot)
                self._cache[provider_id] = snapshot
            except _DiscoveryPending:
                merged.stale_providers.add(provider_id)
                snapshot = self._cache.get(provider_id, Snapshot())
                if provider_id not in self._failed:
                    merged.pending_providers.add(provider_id)
                if provider_id in self._failed:
                    failed.add(provider_id)
                    merged.provider_errors[provider_id] = "Session discovery unavailable"
            except Exception:  # noqa: BLE001 - isolate backend and malformed data
                if provider_id == "claude":
                    # Preserve the original snapshot-failed path: previous rows,
                    # desktop groups and first-summary state belong to the UI.
                    raise
                failed.add(provider_id)
                merged.stale_providers.add(provider_id)
                # RPC error bodies can contain private paths or credentials.
                # UI diagnostics report the backend, never the raw exception.
                merged.provider_errors[provider_id] = "Session discovery unavailable"
                snapshot = self._cache.get(provider_id, Snapshot())
            snapshot = deepcopy(snapshot)
            merged.sessions.extend(snapshot.sessions)
            merged.unreadable_files += snapshot.unreadable_files
            if provider_id == "claude":
                merged.desktop_cli_ids = snapshot.desktop_cli_ids
                merged.cowork_cli_ids = snapshot.cowork_cli_ids
                merged.live = snapshot.live
                merged.desktop_groups = snapshot.desktop_groups
        merged.newly_failed_providers = failed - self._failed
        self._failed = failed
        merged.sessions = sort_sessions(merged.sessions, order)
        return merged
