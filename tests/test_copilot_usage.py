import io
from datetime import datetime, timezone
import json
import threading
import time

import pytest

from thechatplace.providers import copilot_usage


#: Before the reset dates below.
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()


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
        "Premium requests: unlimited; 2 used.",
    ]


def test_usage_lines_keep_reset_and_unknown_usage_explicit():
    lines = copilot_usage.usage_lines({
        "chat": {"usedRequests": 0, "entitlementRequests": 0},
        "completions": {"resetDate": "2026-11-01T07:52:47.526Z"},
        "premium_interactions": {"entitlementRequests": 200, "remainingPercentage": 87.5},
    }, now=NOW)
    assert "Chat messages: no allowance; 0 used; reset time not reported." in lines
    assert lines[2].startswith(
        "Premium requests: 12% used; 88% remaining")
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
    monkeypatch.setattr(copilot_usage, "READ_TIMEOUT", 0.01)
    with pytest.raises(copilot_usage.CopilotUsageError, match="too long"):
        copilot_usage.read_quota(lambda: server)
    assert server.closed


def test_read_message_rejects_an_oversized_frame():
    connection = object.__new__(copilot_usage._SdkConnection)
    connection._server = type("Server", (), {
        "stdout": io.BytesIO(b"Content-Length: 1000001\r\n\r\n")})()
    with pytest.raises(copilot_usage.CopilotUsageError):
        connection._read_message()


class FakeServer:
    """Copilot's side of the pipes: these replies, then the end of the file."""

    def __init__(self, *replies):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(b"".join(frame(reply) for reply in replies))
        self.closed = False

    def close(self):
        self.closed = True


CONNECTED = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}}


@pytest.mark.parametrize("replies", [
    # An older CLI refusing connect; one refusing the quota.
    [{"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "no"}}],
    [CONNECTED, {"jsonrpc": "2.0", "id": 2, "error": {"code": -32601, "message": "no"}}],
    # Copilot exiting before it replies.
    [],
    [CONNECTED],
    # A result that isn't a report.
    [CONNECTED, {"jsonrpc": "2.0", "id": 2, "result": []}],
    [CONNECTED, {"jsonrpc": "2.0", "id": 2, "result": {}}],
])
def test_read_quota_says_so_when_there_is_no_report_and_closes_the_server(replies):
    server = FakeServer(*replies)
    with pytest.raises(copilot_usage.CopilotUsageError):
        copilot_usage.read_quota(lambda: server)
    assert server.closed


def test_a_request_from_copilot_with_our_id_is_not_taken_for_the_reply():
    report = {"chat": {"usedRequests": 3}}
    server = FakeServer(
        {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
        CONNECTED,
        {"jsonrpc": "2.0", "id": 2, "result": {"quotaSnapshots": report}})
    assert copilot_usage.read_quota(lambda: server) == report


def test_the_timeout_covers_the_whole_read(monkeypatch):
    """Two requests share one deadline, not one each."""
    class Slow:
        def readline(self, size):
            threading.Event().wait(1)
            return b""

    server = FakeServer()
    server.stdout = Slow()
    monkeypatch.setattr(copilot_usage, "READ_TIMEOUT", 0.05)
    started = time.monotonic()
    with pytest.raises(copilot_usage.CopilotUsageError, match="too long"):
        copilot_usage.read_quota(lambda: server)
    assert time.monotonic() - started < 0.5


def line(snapshot, **keyword):
    return copilot_usage.usage_lines({"chat": snapshot}, **keyword)[1]


def test_a_reset_date_already_gone_is_not_reset_now():
    assert line({"entitlementRequests": 200, "remainingPercentage": 98,
                 "resetDate": "2020-01-01T00:00:00Z"}, now=NOW).endswith(
        "98% remaining; reset time not reported.")
    assert line({"entitlementRequests": 200, "remainingPercentage": 98,
                 "resetDate": "not a date"}, now=NOW).endswith("reset time not reported.")


def test_use_past_the_allowance_is_said():
    assert line({"usedRequests": 350, "entitlementRequests": 300, "remainingPercentage": 0,
                 "overage": 50}).startswith(
        "Chat messages: 100% used (350 of 300); 0% remaining; 50 past the allowance")


def test_rounding_never_says_all_used_with_some_left():
    assert line({"usedRequests": 199, "entitlementRequests": 200}).startswith(
        "Chat messages: 99% used (199 of 200); 1% remaining")
    assert line({"usedRequests": 1999, "entitlementRequests": 2000,
                 "remainingPercentage": 0.05}).startswith(
        "Chat messages: 99% used (1,999 of 2,000); 1% remaining")


def test_a_percentage_without_an_allowance_is_still_said():
    assert line({"remainingPercentage": 50}).startswith("Chat messages: 50% remaining")
    assert line({"usedRequests": 7, "remainingPercentage": 50}).startswith(
        "Chat messages: 7 used; 50% remaining")


@pytest.mark.parametrize("huge", [10 ** 400, float("inf"), float("nan")])
def test_a_damaged_number_is_ignored_not_raised(huge):
    assert line({"usedRequests": huge, "entitlementRequests": huge,
                 "remainingPercentage": huge, "overage": huge}).startswith(
        "Chat messages: usage not reported")
