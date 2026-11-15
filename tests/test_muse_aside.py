"""Ask Aside for a Muse chat started here: a read-only side chat that never
asks you anything, shaped like installed Muse Code 1.4.20's session/sideChat.

Synthetic host only. Natively, a side chat sees the main chat's history,
leaves the main chat untouched, and is durable (listed as an ordinary chat),
which is why the window hides each one it makes.
"""
import copy
import threading
import uuid

import pytest

from test_muse_execution import NATIVE_ID, Execution, approval, owned, record  # noqa: F401 - fixture

from thechatplace.aside import NOTHING_FINISHED
from thechatplace.providers.muse import MuseAsideRunner, MuseProvider
from thechatplace.providers.muse_connection import MuseRequestError

SIDE_ID = "019a84c2-a1f0-7e3b-9c1e-0000000000aa"


class SideHost(Execution):
    """The main chat as Execution has it, plus one side chat."""

    def __init__(self):
        super().__init__()
        self.last_turn = {"turnId": str(uuid.uuid7()), "terminal": "completed"}  # a finished turn
        self.side = None
        self.side_approvals = []
        self.read_only = True  # whether setPermissionProfile takes
        self.side_reply = "Synthetic side answer."

    def request(self, method, params):
        if params.get("sessionId") != SIDE_ID:
            if method == "session/sideChat":
                self.messages.append((method, copy.deepcopy(params)))
                assert set(params) == {"sessionId", "commandId"} and params["sessionId"] == NATIVE_ID
                assert uuid.UUID(params["commandId"]).version == 7
                self.side = {"sessionId": SIDE_ID, "kind": "root", "status": "idle", "activeTurnId": None,
                             "sideFrom": {"sessionId": NATIVE_ID},
                             "approvalMode": {"mode": "onRequest"},
                             "permissionState": {"profileId": ":ask-me", "reviewerAuthority": "human",
                                                 "reviewerAttached": True}}
                return {"session": copy.deepcopy(self.side), "viewCursor": "v:1", "pendingRequests": [],
                        "history": {"mode": "inline", "items": []}}
            return super().request(method, params)
        self.messages.append((method, copy.deepcopy(params)))
        if method == "session/setPermissionProfile":
            assert params["profileId"] == ":read-only"
            if self.read_only:
                self.side["permissionState"] = {"profileId": ":read-only", "reviewerAuthority": "human",
                                                "reviewerAttached": True}
            return {"commandId": params["commandId"], "status": "accepted", "applyOutcome": "completed",
                    "permissionState": copy.deepcopy(self.side["permissionState"])}
        if method == "turn/start":
            assert params["input"] == [{"type": "text", "text": params["input"][0]["text"]}]
            self.side.update(status="running", activeTurnId=params["commandId"])
            return {"commandId": params["commandId"], "turnId": params["commandId"], "status": "accepted",
                    "disposition": "started", "startedNewTurn": True}
        if method == "approval/listPending":
            return {"approvals": copy.deepcopy(self.side_approvals), "userInputs": []}
        if method == "session/read":
            result = {"session": copy.deepcopy(self.side), "viewCursor": "v:2", "pendingRequests": []}
            turn = self.side.get("activeTurnId") or self.side.get("lastTurnId")
            if self.side["status"] == "running" and not self.side_approvals:
                # The side turn answers on the first read after it starts.
                self.side.update(status="idle", activeTurnId=None, lastTurnId=turn)
                result["session"] = copy.deepcopy(self.side)
            if self.side["status"] == "idle" and turn:
                result["lastTurn"] = {"turnId": turn, "terminal": "completed"}
            result["history"] = {"mode": "inline", "items": [
                {"kind": "agentMessage", "itemId": "a", "revision": 1, "status": "completed",
                 "turnId": turn, "messagePhase": "finalAnswer", "text": self.side_reply}] if turn else []}
            return result
        if method == "turn/cancel":
            self.side.update(status="idle", activeTurnId=None)
            return {"commandId": params["commandId"], "turnId": params["turnId"], "status": "accepted"}
        if method == "session/leave":
            return {"commandId": params["commandId"], "status": "accepted"}
        raise AssertionError(method)


@pytest.fixture
def host(owned):
    owned.execution = SideHost()
    return owned


def ask(chats, question="Synthetic side question?"):
    events, sides, finished = [], [], threading.Event()
    def on_event(event):
        events.append((event.kind, event.text))
        if event.kind in ("done", "failed"):
            finished.set()
    own = record(chats)
    chats.execution.messages.clear()
    runner = MuseAsideRunner(chats, own, question, on_event, on_side=sides.append, poll=0).start()
    assert finished.wait(5)
    return runner, events, sides, chats.execution.messages


def test_aside_asks_a_read_only_side_chat_and_leaves_it(host):
    _runner, events, sides, messages = ask(host)
    assert events[-1] == ("done", "Synthetic side answer.")
    assert sides == [SIDE_ID]  # given to the window, to hide
    methods = [m for m, _ in messages]
    assert methods.index("session/sideChat") < methods.index("session/setPermissionProfile") < methods.index("turn/start")
    assert methods[-1] == "session/leave"
    started = [p for m, p in messages if m == "turn/start"]
    assert len(started) == 1 and started[0]["sessionId"] == SIDE_ID  # never the main chat
    assert started[0]["input"] == [{"type": "text", "text": "Synthetic side question?"}]


def test_a_request_in_the_side_chat_is_cancelled_never_answered(host):
    host.execution.side_approvals = [dict(approval(), sessionId=SIDE_ID)]
    _runner, events, _sides, messages = ask(host)
    assert events[-1][0] == "failed" and "ask it in the chat" in events[-1][1].lower()
    methods = [m for m, _ in messages]
    assert "turn/cancel" in methods and "approval/decide" not in methods
    assert methods[-1] == "session/leave"


def test_nothing_is_asked_unless_read_only_is_confirmed(host):
    host.execution.read_only = False
    _runner, events, _sides, messages = ask(host)
    assert events[-1][0] == "failed" and "read-only" in events[-1][1]
    assert not any(m == "turn/start" for m, _ in messages)
    assert [m for m, _ in messages][-1] == "session/leave"


def test_a_chat_with_nothing_finished_has_nothing_to_ask_about(host):
    host.execution.last_turn = None
    _runner, events, _sides, messages = ask(host)
    assert events == [("failed", NOTHING_FINISHED)]
    assert not any(m == "session/sideChat" for m, _ in messages)


def test_an_unreadable_side_chat_is_refused(host, monkeypatch):
    answer = host.execution.request
    def other_side(method, params):
        result = answer(method, params)
        if method == "session/sideChat":
            result["session"]["sideFrom"] = {"sessionId": "another-chat"}
        return result
    monkeypatch.setattr(host.execution, "request", other_side)
    _runner, events, _sides, messages = ask(host)
    assert events[-1][0] == "failed"
    assert not any(m == "turn/start" for m, _ in messages)


def test_aside_is_a_capability_of_chats_started_here_only(owned):
    from thechatplace.providers.muse import session_from_muse
    provider = MuseProvider(connection=object(), execution=owned.execution)
    assert provider.capabilities(record(owned).to_info()).aside is True
    assert provider.capabilities(session_from_muse({"sessionId": "external-1", "name": "x"})).aside is False
