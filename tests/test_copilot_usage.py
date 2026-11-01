import io
import json
import threading

import pytest

from thechatplace.providers import copilot_usage


def frame(message):
    content = json.dumps(message, separators=(",", ":")).encode()
    return f"Content-Length: {len(content)}\r\n\r\n".encode() + content


def messages(data):
    found = []
    while data:
        header, data = data.split(b"\r\n\r\n", 1)
        length = int(header.split(b":", 1)[1])
        found.append(json.loads(data[:length]))
        data = data[length:]
    return found


def test_usage_lines_show_chat_quota_and_unlimited_allowance():
    lines = copilot_usage.usage_lines({
        "chat": {"usedRequests": 3, "entitlementRequests": 200,
                 "remainingPercentage": 98.4},
        "premium_interactions": {"usedRequests": 2, "entitlementRequests": -1,
                                 "isUnlimitedEntitlement": True},
    })
    assert lines == [
        "Copilot account usage is shared across your chats.",
        "Chat messages: 2% used (3 of 200); 98% remaining; reset time not reported.",
        "Premium interactions: unlimited; 2 used; reset time not reported.",
    ]


def test_usage_lines_keep_reset_and_unknown_usage_explicit():
    lines = copilot_usage.usage_lines({
        "chat": {"usedRequests": 0, "entitlementRequests": 0},
        "completions": {"resetDate": "2026-11-01T07:52:47.526Z"},
        "premium_interactions": {"entitlementRequests": 200, "remainingPercentage": 87.5},
    })
    assert "Chat messages: no allowance; 0 used; reset time not reported." in lines
    assert lines[2].startswith(
        "Premium interactions: 12% used; 88% remaining")
    assert lines[3].startswith("Completions: usage not reported, resets ")
    assert copilot_usage.usage_lines({"completions": {}})[1] == \
        "Chat messages: not reported."


def test_usage_lines_reject_malformed_snapshots():
    with pytest.raises(copilot_usage.CopilotUsageError):
        copilot_usage.usage_lines({"chat": []})
    with pytest.raises(copilot_usage.CopilotUsageError):
        copilot_usage.usage_lines({1: {}})


def test_read_quota_uses_read_only_sdk_requests_and_closes_server():
    snapshots = {"chat": {"usedRequests": 3}}

    class Server:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(
                frame({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}})
                + frame({"jsonrpc": "2.0", "id": 2,
                         "result": {"quotaSnapshots": snapshots}}))
            self.closed = False

        def close(self):
            self.closed = True

    server = Server()
    assert copilot_usage.read_quota(lambda: server) == snapshots
    requests = messages(server.stdin.getvalue())
    assert [request["method"] for request in requests] == ["connect", "account.getQuota"]
    assert requests[1]["params"] == {}
    assert server.closed


def test_read_quota_closes_server_after_timeout(monkeypatch):
    class Server:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.stdout = self
            self.closed = False

        def readline(self, size):
            threading.Event().wait(0.1)
            return b""

        def close(self):
            self.closed = True

    server = Server()
    monkeypatch.setattr(copilot_usage, "REQUEST_TIMEOUT", 0.01)
    with pytest.raises(copilot_usage.CopilotUsageError, match="too long"):
        copilot_usage.read_quota(lambda: server)
    assert server.closed


def test_read_message_rejects_an_oversized_frame():
    connection = object.__new__(copilot_usage._SdkConnection)
    connection._server = type("Server", (), {
        "stdout": io.BytesIO(b"Content-Length: 1000001\r\n\r\n")})()
    with pytest.raises(copilot_usage.CopilotUsageError):
        connection._read_message()
