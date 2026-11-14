"""Account-only MSP, using synthetic pipes rather than a real sign-in."""
import json
import queue

import pytest

from thechatplace.muse_account import AccountConnection, MuseAccountError


HANDSHAKE = {"serverInfo": {"name": "muse", "version": "1.4.0"},
             "schema": {"version": 1, "fingerprint": "sha256:" + "0" * 64},
             "experimentalApi": True}


class Pipe:
    def __init__(self):
        self.lines = queue.Queue()

    def readline(self, limit):
        line = self.lines.get(timeout=3)
        return line[:limit]


class Host:
    def __init__(self, answer=None):
        self.stdout = Pipe()
        self.stdin = self
        self.messages = []
        self.closed = False
        self.initialized = False
        self.answer = answer or (lambda message: {"state": "loggedOut", "credentialRequired": True})

    def write(self, data):
        message = json.loads(data)
        self.messages.append(message)
        if message["method"] == "initialized":
            assert "id" not in message
            self.initialized = True
            return
        if message["method"] != "initialize" and not self.initialized:
            self.stdout.lines.put(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                "error": {"code": -32600, "message": "initialized notification required"}}).encode() + b"\n")
            return
        result = HANDSHAKE if message["method"] == "initialize" else self.answer(message)
        if result is not None:
            if isinstance(result, bytes):
                self.stdout.lines.put(result)
            else:
                self.stdout.lines.put(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                                                   "result": result}).encode() + b"\n")

    def flush(self):
        pass

    def close(self):
        self.closed = True
        self.stdout.lines.put(b"")


def test_handshake_and_account_read_never_create_a_session():
    host = Host()
    with AccountConnection("synthetic", start=lambda exe: host) as connection:
        assert connection.request("account/read") == {"state": "loggedOut", "credentialRequired": True}
        assert host.messages[0]["params"]["capabilities"] == {
            "experimentalApi": True, "userInputDialogs": False}
        with pytest.raises(MuseAccountError):
            connection.request("session/start", {})
        with pytest.raises(MuseAccountError):
            connection.request("account/loginStart", {"type": "apiKey", "apiKey": "PRIVATE"})
    assert [message["method"] for message in host.messages] == ["initialize", "initialized", "account/read"]
    assert host.closed


def test_login_code_notification_and_cancel_stay_on_one_connection():
    host = Host(lambda message: {"verificationUrl": "https://example.test/verify", "userCode": "SYNTHETIC"}
                if message["method"] == "account/loginStart" else {})
    with AccountConnection("synthetic", start=lambda exe: host) as connection:
        assert connection.request("account/loginStart", {"type": "deviceCode"})["userCode"] == "SYNTHETIC"
        host.stdout.lines.put(b'{"jsonrpc":"2.0","method":"account/loginCompleted","params":{"outcome":"granted"}}\n')
        event = connection.next_notification(timeout=1)
        assert event["method"] == "account/loginCompleted"
        connection.request("account/loginCancel")
    assert host.closed


@pytest.mark.parametrize("response", [b'not json\n', b'{"jsonrpc":"1.0","id":2,"result":{}}\n',
    b'{"jsonrpc":"2.0","id":2,"error":{"code":-32601,"message":"PRIVATE TOKEN"}}\n', b''])
def test_malformed_errors_and_eof_are_safe_and_close_host(response):
    host = Host(lambda message: response)
    with pytest.raises(MuseAccountError) as error:
        with AccountConnection("synthetic", start=lambda exe: host, timeout=.2) as connection:
            connection.request("account/read")
    assert "PRIVATE" not in str(error.value)
    assert host.closed


def test_timeout_closes_host_instead_of_leaking_a_waiting_reader():
    host = Host(lambda message: None)
    with pytest.raises(MuseAccountError, match="timed out"):
        with AccountConnection("synthetic", start=lambda exe: host, timeout=.02) as connection:
            connection.request("account/read")
    assert host.closed


def test_unsupported_schema_closes_before_any_account_operation(monkeypatch):
    monkeypatch.setitem(HANDSHAKE, "schema", {"version": 2, "fingerprint": "future"})
    host = Host()
    with pytest.raises(MuseAccountError, match="compatible"):
        AccountConnection("synthetic", start=lambda exe: host)
    assert host.closed
    assert len(host.messages) == 1


def test_no_experimental_account_support_is_an_explicit_limitation(monkeypatch):
    monkeypatch.setitem(HANDSHAKE, "experimentalApi", False)
    host = Host()
    with pytest.raises(MuseAccountError, match="account"):
        AccountConnection("synthetic", start=lambda exe: host)
    assert host.closed


def test_oversized_frame_is_bounded_and_safe():
    host = Host(lambda message: b"x" * 1025)
    with pytest.raises(MuseAccountError):
        with AccountConnection("synthetic", start=lambda exe: host, frame_limit=1024) as connection:
            connection.request("account/read")
    assert host.closed


def test_server_requests_cannot_obtain_client_tools():
    host = Host(lambda message: b'{"jsonrpc":"2.0","id":"server","method":"approval/request","params":{}}\n')
    with pytest.raises(MuseAccountError):
        with AccountConnection("synthetic", start=lambda exe: host) as connection:
            connection.request("account/read")
    assert host.closed


def test_closing_wakes_an_outstanding_account_request():
    import threading
    import time

    host = Host(lambda message: None)
    connection = AccountConnection("synthetic", start=lambda exe: host)
    errors = []

    def check():
        try:
            connection.request("account/read")
        except MuseAccountError as error:
            errors.append(error)

    thread = threading.Thread(target=check)
    thread.start()
    deadline = time.monotonic() + 2
    while len(host.messages) < 3 and time.monotonic() < deadline:
        time.sleep(.001)
    connection.close()
    thread.join(timeout=2)
    assert not thread.is_alive() and len(errors) == 1
    assert host.closed


def test_account_transport_with_real_synthetic_subprocess_pipes(tmp_path):
    import subprocess
    import sys

    script = tmp_path / "fake_muse.py"
    script.write_text(
        "import sys,json\n"
        f"handshake={HANDSHAKE!r}\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line)\n"
        " if request['method']=='initialized': continue\n"
        " result=handshake if request['method']=='initialize' else {'state':'loggedOut','credentialRequired':True}\n"
        " print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)\n",
        encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    class Owner:
        stdin, stdout = process.stdin, process.stdout

        def close(self):
            process.kill()
            process.wait(timeout=3)
            self.stdin.close()
            self.stdout.close()

    try:
        with AccountConnection("fake", start=lambda exe: Owner()) as connection:
            assert connection.request("account/read")["state"] == "loggedOut"
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
