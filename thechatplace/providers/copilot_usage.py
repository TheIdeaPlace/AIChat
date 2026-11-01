"""Read GitHub Copilot account quota by asking Copilot CLI.

This starts ``copilot --headless --stdio`` briefly and asks it ``connect`` and
``account.getQuota``. Neither opens a chat. Copilot CLI 1.0.105's schema marks
both experimental, and connect internal, so a later CLI may change them: any
failure here says only that the report isn't available.
"""
from __future__ import annotations

from datetime import datetime
import json
import queue
import threading
import time

from .. import __version__, platform_paths, usage

MAX_FRAME_BYTES = 1_000_000
MAX_HEADER_BYTES = 8_192
#: For the whole read, starting Copilot included, so someone waiting on a
#: stalled connection isn't kept waiting long.
READ_TIMEOUT = 15
#: Anything larger is a damaged report, not a count.
MAX_COUNT = 10 ** 12


class CopilotUsageError(RuntimeError):
    """The account quota report could not be read safely."""


def _frame(message: dict) -> bytes:
    content = json.dumps(message, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(content)}\r\n\r\n".encode("ascii") + content


def _response(response: dict) -> dict:
    if "error" in response:
        raise CopilotUsageError("Copilot account usage isn't available")
    result = response.get("result")
    if not isinstance(result, dict):
        raise CopilotUsageError("Copilot returned an invalid usage report")
    return result


class _SdkConnection:
    """The SDK's framed JSON-RPC transport, limited to sequential reads."""

    def __init__(self, server):
        self._server = server
        self._incoming = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True,
                                        name="copilot-usage-reader")
        self._reader.start()

    def _read_message(self):
        stream = self._server.stdout
        size = 0
        length = None
        while True:
            line = stream.readline(MAX_HEADER_BYTES + 1)
            if not line:
                if size == 0:
                    return None
                raise CopilotUsageError("Copilot returned an invalid usage report")
            size += len(line)
            if size > MAX_HEADER_BYTES:
                raise CopilotUsageError("Copilot returned an invalid usage report")
            if line in (b"\r\n", b"\n"):
                break
            name, separator, value = line.partition(b":")
            if not separator:
                raise CopilotUsageError("Copilot returned an invalid usage report")
            if name.strip().lower() == b"content-length":
                value = value.strip()
                if length is not None or not value.isdigit():
                    raise CopilotUsageError("Copilot returned an invalid usage report")
                length = int(value)
        if length is None or length > MAX_FRAME_BYTES:
            raise CopilotUsageError("Copilot returned an invalid usage report")
        parts, received = [], 0
        while received < length:
            part = stream.read(length - received)
            if not part:
                raise CopilotUsageError("Copilot returned an invalid usage report")
            parts.append(part)
            received += len(part)
        try:
            message = json.loads(b"".join(parts).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise CopilotUsageError("Copilot returned an invalid usage report") from None
        if not isinstance(message, dict):
            raise CopilotUsageError("Copilot returned an invalid usage report")
        return message

    def _read(self):
        try:
            while True:
                message = self._read_message()
                if message is None:
                    break
                self._incoming.put(message)
        except (CopilotUsageError, OSError, ValueError):
            pass
        finally:
            self._incoming.put(None)

    def request(self, request_id: int, method: str, params: dict, deadline: float) -> dict:
        try:
            self._server.stdin.write(_frame({
                "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
            }))
            self._server.stdin.flush()
        except (OSError, ValueError):
            raise CopilotUsageError("Copilot account usage isn't available") from None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CopilotUsageError("Copilot account usage took too long to report")
            try:
                message = self._incoming.get(timeout=remaining)
            except queue.Empty:
                raise CopilotUsageError("Copilot account usage took too long to report") from None
            if message is None:
                raise CopilotUsageError("Copilot account usage isn't available")
            # A request from Copilot can carry the same id as ours.
            if message.get("id") == request_id and "method" not in message:
                return message


def read_quota(start=None) -> dict:
    """Read account quota without creating a chat or sending a prompt."""
    deadline = time.monotonic() + READ_TIMEOUT
    server = (start or platform_paths.start_copilot_sdk)()
    try:
        connection = _SdkConnection(server)
        connected = connection.request(1, "connect", {
            "supportedTaskKinds": ["agent", "client", "shell"],
            "clientInfo": {"editorName": "The Chat Place",
                           "editorVersion": __version__,
                           "extensionName": "The Chat Place",
                           "extensionVersion": __version__},
        }, deadline)
        _response(connected)
        result = _response(connection.request(2, "account.getQuota", {}, deadline))
        snapshots = result.get("quotaSnapshots")
        if not isinstance(snapshots, dict) or len(snapshots) > 32:
            raise CopilotUsageError("Copilot returned an invalid usage report")
        return snapshots
    finally:
        server.close()


def _reset_text(value, now):
    if not isinstance(value, str) or not value or len(value) > 100:
        return "; reset time not reported"
    try:
        reset = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        # A date already gone says nothing about the next reset.
        if not (now if now is not None else time.time()) < reset < 253402300800:
            return "; reset time not reported"
        return f", resets {usage.when(reset, now)}"
    except (ValueError, OverflowError, OSError):
        return "; reset time not reported"


def usage_lines(snapshots: dict, *, now=None) -> list:
    """Account-wide usage as words, preserving unknown or unlimited quotas."""
    if not isinstance(snapshots, dict) or len(snapshots) > 32:
        raise CopilotUsageError("Copilot returned an invalid usage report")
    if any(not isinstance(key, str) or not key or len(key) > 100
           or not isinstance(snapshot, dict) for key, snapshot in snapshots.items()):
        raise CopilotUsageError("Copilot returned an invalid usage report")
    labels = {
        "chat": "Chat messages",
        "premium_interactions": "Premium requests",
        "completions": "Completions",
    }
    keys = [key for key in ("chat", "premium_interactions", "completions") if key in snapshots]
    keys.extend(sorted(key for key in snapshots if key not in labels))
    lines = ["Copilot account usage is shared across your chats."]
    if "chat" not in snapshots:
        lines.append("Chat messages: not reported.")
    for key in keys:
        snapshot = snapshots[key]
        label = labels.get(key, " ".join(
            part for part in key.replace("_", " ").split() if part.isalnum()).capitalize())
        if not label:
            label = "Other Copilot usage"
        used = snapshot.get("usedRequests")
        used = used if type(used) is int and 0 <= used <= MAX_COUNT else None
        entitlement = snapshot.get("entitlementRequests")
        entitlement = (entitlement if type(entitlement) is int
                       and -1 <= entitlement <= MAX_COUNT else None)
        unlimited = snapshot.get("isUnlimitedEntitlement") is True or entitlement == -1
        remaining = snapshot.get("remainingPercentage")
        remaining = (float(remaining) if type(remaining) in (int, float)
                     and 0 <= remaining <= 100 else None)
        overage = snapshot.get("overage")
        overage = (overage if type(overage) in (int, float)
                   and 0 < overage <= MAX_COUNT else None)

        if unlimited:
            detail = "unlimited"
            if used is not None:
                detail += f"; {used:,} used"
        elif entitlement == 0:
            detail = "no allowance"
            if used is not None:
                detail += f"; {used:,} used"
        elif entitlement is not None and entitlement > 0:
            if remaining is None and used is not None:
                remaining = max(100 - used / entitlement * 100, 0.0)
            if remaining is not None:
                percent, left = round(100 - remaining), round(remaining)
                # One message left isn't "100% used; 0% remaining".
                if used is not None and used < entitlement:
                    percent, left = min(percent, 99), max(left, 1)
                detail = f"{percent}% used"
                if used is not None:
                    detail += f" ({used:,} of {entitlement:,})"
                detail += f"; {left}% remaining"
            else:
                detail = "usage not reported"
            if overage is not None:
                detail += f"; {overage:,g} past the allowance"
        elif remaining is not None:
            detail = f"{round(remaining)}% remaining"
            if used is not None:
                detail = f"{used:,} used; {detail}"
        elif used is not None:
            detail = f"{used:,} used; allowance not reported"
        else:
            detail = "usage not reported"
        # An unlimited allowance has nothing to reset.
        reset = "" if unlimited else _reset_text(snapshot.get("resetDate"), now)
        lines.append(f"{label}: {detail}{reset}.")
    if not keys:
        lines.append("Copilot didn't report any account usage limits.")
    return lines


def read_usage_lines(start=None, *, now=None) -> list:
    return usage_lines(read_quota(start), now=now)
