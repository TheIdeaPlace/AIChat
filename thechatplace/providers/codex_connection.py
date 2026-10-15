"""Own one lazy read-only connection across discovery and history workers.

Reconnection happens on a later operation, after failed-process cleanup.
The operation that failed is never replayed. Startup and shutdown coordinate
without holding a lock across app-server RPC calls (#184) (#171).
"""
from __future__ import annotations

import threading
import time

from .codex_rpc import CodexRpcError, ReadOnlyCodexConnection


class ManagedCodexConnection:
    def __init__(self, executable=None, *, factory=None):
        self._executable = executable
        self._factory = factory or self._create
        self._condition = threading.Condition()
        self._connection = None
        self._connecting = False
        self._closed = False
        self._cleanup_failed = False

    @staticmethod
    def _create(executable):
        return ReadOnlyCodexConnection(executable, initialize=False)

    def request(self, method: str, params: dict) -> dict:
        if method not in ReadOnlyCodexConnection.METHODS:
            raise ValueError("Method is not supported by the read-only Codex connection")
        if not isinstance(params, dict):
            raise ValueError("Codex request parameters must be an object")
        return self._get().request(method, params)

    def _get(self):
        with self._condition:
            while self._connecting and not self._closed:
                self._condition.wait()
            if self._closed:
                raise CodexRpcError("Codex backend closed")
            previous = self._connection
            if previous is not None and not previous.closed:
                return previous
            self._connecting = True
        connection = None
        try:
            if previous is not None:
                try:
                    previous.close()  # finish cleanup before starting a replacement
                except Exception:  # noqa: BLE001 - keep failed process ownership
                    with self._condition:
                        self._cleanup_failed = True
                    raise CodexRpcError("Codex backend cleanup failed") from None
            with self._condition:
                self._connection = None
                if self._closed:
                    raise CodexRpcError("Codex backend closed")
                self._cleanup_failed = False  # previous cleanup has now succeeded
            connection = self._factory(self._executable)
            with self._condition:
                # Publish even a late result before rejecting it, so shutdown
                # retains ownership and can observe any cleanup failure.
                self._connection = connection
                self._condition.notify_all()
                if self._closed:
                    raise CodexRpcError("Codex backend closed")
            connection.initialize()
            with self._condition:
                if self._closed:
                    raise CodexRpcError("Codex backend closed")
            return connection
        except Exception:
            if connection is not None:
                try:
                    connection.close()
                except Exception:  # noqa: BLE001 - retain ownership and redact
                    with self._condition:
                        self._cleanup_failed = True
                    raise CodexRpcError("Codex backend cleanup failed") from None
                with self._condition:
                    self._connection = None
            raise
        finally:
            with self._condition:
                self._connecting = False
                self._condition.notify_all()

    def close(self):
        """Run off the UI thread; bound waiting for a slow process factory."""
        deadline = time.monotonic() + 10
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            while self._connecting and self._connection is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CodexRpcError("Codex startup cleanup timed out")
                self._condition.wait(remaining)
            connection = self._connection
        if connection is not None:
            try:
                connection.close()  # also interrupts an in-progress initialize
            except Exception:  # noqa: BLE001 - preserve failure across callers
                with self._condition:
                    self._cleanup_failed = True
        with self._condition:
            while self._connecting:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CodexRpcError("Codex startup cleanup timed out")
                self._condition.wait(remaining)
            if self._cleanup_failed:
                raise CodexRpcError("Codex backend cleanup failed")
