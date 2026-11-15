"""Retry from Here for a Muse chat started here: a new chat forked from the
turn before your message, shaped like installed Muse Code 1.4.20.

Natively, session/fork with cutPoint.lastTurnId copies the history through
that completed turn, leaves the original untouched, and refuses an unfinished
turn (-32023 forkBoundaryInvalid). The fork starts in approval mode onRequest,
so it's set to promptUnmatched before anything can be sent. Synthetic only.
"""
import copy
import uuid

import pytest

from test_muse_execution import NATIVE_ID, REVIEWERS, Execution, owned, policy, record  # noqa: F401

from thechatplace.providers.muse_connection import MuseHistoryError, MuseRequestError

FORK_ID = "019a84c2-a1f0-7e3b-9c1e-0000000000f0"
TURN_A, TURN_B = str(uuid.uuid7()), str(uuid.uuid7())


class ForkHost(Execution):
    """The main chat with two finished turns, and the forks made from it."""

    def __init__(self):
        super().__init__()
        self.last_turn = {"turnId": TURN_B, "terminal": "completed"}
        self.items = [
            {"kind": "userMessage", "itemId": "u-a", "turnId": TURN_A, "revision": 1, "status": "completed",
             "text": "Synthetic first message."},
            {"kind": "agentMessage", "itemId": "r-a", "turnId": TURN_A, "revision": 1, "status": "completed",
             "text": "Synthetic first reply."},
            {"kind": "userMessage", "itemId": "u-b", "turnId": TURN_B, "revision": 1, "status": "completed",
             "text": "Synthetic second message."},
            {"kind": "agentMessage", "itemId": "r-b", "turnId": TURN_B, "revision": 1, "status": "completed",
             "text": "Synthetic second reply."}]
        self.fork = None
        self.refuse_cut = False

    def request(self, method, params):
        sid = params.get("sessionId")
        if sid == NATIVE_ID and method == "session/read" and not params.get("excludeItems"):
            result = super().request(method, params)
            result["history"] = {"mode": "inline", "snapshot": None, "items": copy.deepcopy(self.items)}
            return result
        if method == "session/fork":
            self.messages.append((method, copy.deepcopy(params)))
            assert sid == NATIVE_ID and uuid.UUID(params["commandId"]).version == 7
            assert set(params) <= {"sessionId", "commandId", "cutPoint", "excludeItems"}
            if self.refuse_cut:
                raise MuseRequestError(-32023)
            self.fork = {"sessionId": FORK_ID, "status": "idle", "activeTurnId": None, "kind": "root",
                         "forkedFrom": {"sessionId": NATIVE_ID},
                         "approvalMode": {"mode": "onRequest"},
                         "permissionState": copy.deepcopy(self.session["permissionState"])}
            return {"session": copy.deepcopy(self.fork), "viewCursor": "v:1", "pendingRequests": [],
                    "history": {"mode": "inline", "items": []}}
        if sid == FORK_ID:
            self.messages.append((method, copy.deepcopy(params)))
            if method == "session/setApprovalMode":
                assert params["mode"] == "promptUnmatched"
                self.fork["approvalMode"] = {"mode": "promptUnmatched"}
                return {"commandId": params["commandId"], "status": "accepted", "applyOutcome": "completed",
                        "effectiveMode": copy.deepcopy(self.fork["approvalMode"])}
            if method == "session/setPermissionProfile":
                self.fork.update(policy(params["profileId"]))
                return {"commandId": params["commandId"], "status": "accepted", "applyOutcome": "completed",
                        "permissionState": copy.deepcopy(self.fork["permissionState"])}
            if method == "session/read":
                return {"session": copy.deepcopy(self.fork), "viewCursor": "v:1", "pendingRequests": [],
                        "history": {"mode": "inline", "items": []}}
            raise AssertionError(method)
        return super().request(method, params)


@pytest.fixture
def host(owned):
    owned.execution = ForkHost()
    return owned


def key(item_id):
    return f"muse:{NATIVE_ID}:{item_id}"


def test_retry_forks_at_the_turn_before_your_message(host):
    own = record(host)
    copy_ = host.retry(own, key("u-b"), "Synthetic chat (retry)")
    fork = [p for m, p in host.execution.messages if m == "session/fork"]
    assert fork == [{"sessionId": NATIVE_ID, "commandId": fork[0]["commandId"],
                     "cutPoint": {"lastTurnId": TURN_A}, "excludeItems": True}]
    assert copy_.cli_session_id == FORK_ID and copy_.provider == "muse" and copy_.started
    assert copy_.title == "Synthetic chat (retry)" and copy_.cwd == own.cwd
    assert host.execution.fork["approvalMode"]["mode"] == "promptUnmatched"
    host.prepare(copy_)  # sending is allowed: the fork passes the policy check
    assert not any(m == "turn/start" for m, _ in host.execution.messages)  # nothing sent for you


def test_retry_from_the_first_message_starts_a_new_chat(host):
    own = record(host)
    copy_ = host.retry(own, key("u-a"), "Synthetic chat (retry)")
    assert not any(m == "session/fork" for m, _ in host.execution.messages)
    assert [m for m, _ in host.execution.messages].count("session/start") == 2  # the original and this
    assert copy_.cli_session_id != FORK_ID and copy_.title == "Synthetic chat (retry)"


def test_retry_keeps_the_originals_permission_profile(host):
    own = record(host)
    host.set_mode(own, ":read-only")
    copy_ = host.retry(own, key("u-b"), "Synthetic chat (retry)")
    assert host.execution.fork["permissionState"]["profileId"] == ":read-only"
    assert copy_.provider_options == {"muse_profile": ":read-only"}
    host.prepare(copy_)


@pytest.mark.parametrize("item", ["r-b", "not-there"])
def test_only_your_own_message_can_be_retried(host, item):
    own = record(host)
    with pytest.raises(MuseHistoryError, match="your own messages"):
        host.retry(own, key(item), "Synthetic chat (retry)")
    assert not any(m == "session/fork" for m, _ in host.execution.messages)


def test_another_chats_message_is_refused(host):
    own = record(host)
    with pytest.raises(MuseHistoryError):
        host.retry(own, "muse:another-chat:u-b", "Synthetic chat (retry)")
    assert not any(m == "session/fork" for m, _ in host.execution.messages)


def test_an_unfinished_turn_is_refused_with_why(host):
    own = record(host)
    host.execution.refuse_cut = True
    with pytest.raises(MuseHistoryError, match="hasn't finished"):
        host.retry(own, key("u-b"), "Synthetic chat (retry)")



@pytest.mark.parametrize("item,cut", [("r-a", TURN_A), ("u-b", TURN_B), ("r-b", TURN_B)])
def test_branch_copies_through_the_whole_turn_of_the_message(host, item, cut):
    """session/fork's cut is inclusive (native): a branch keeps the message's turn."""
    own = record(host)
    copy_ = host.branch(own, key(item), "Synthetic chat (branch)")
    fork = [p for m, p in host.execution.messages if m == "session/fork"]
    assert [f["cutPoint"] for f in fork] == [{"lastTurnId": cut}]
    assert copy_.cli_session_id == FORK_ID and copy_.title == "Synthetic chat (branch)"
    assert host.execution.fork["approvalMode"]["mode"] == "promptUnmatched"
    host.prepare(copy_)
    assert not any(m == "turn/start" for m, _ in host.execution.messages)


def test_branch_keeps_the_originals_permission_profile(host):
    own = record(host)
    host.set_mode(own, ":read-only")
    copy_ = host.branch(own, key("r-a"), "Synthetic chat (branch)")
    assert copy_.provider_options == {"muse_profile": ":read-only"}
    host.prepare(copy_)


@pytest.mark.parametrize("item", ["not-there"])
def test_only_your_messages_and_replies_can_be_branched_from(host, item):
    own = record(host)
    with pytest.raises(MuseHistoryError, match="one of Muse Code's replies"):
        host.branch(own, key(item), "Synthetic chat (branch)")
    assert not any(m == "session/fork" for m, _ in host.execution.messages)


def test_branching_from_an_unfinished_turn_is_refused_with_why(host):
    own = record(host)
    host.execution.refuse_cut = True
    with pytest.raises(MuseHistoryError, match="hasn't finished"):
        host.branch(own, key("r-b"), "Synthetic chat (branch)")
