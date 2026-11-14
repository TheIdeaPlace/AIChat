"""Owned execution fixtures use installed Muse 1.4.20 MSP shapes.

No native process or real conversation is used. The fake validates the wire
contract, while these tests exercise ownership, state and durable ordering.
"""
import copy
import uuid

import pytest

from thechatplace.own_store import OwnSession
from thechatplace.providers.base import SessionCapabilities
from thechatplace.providers.muse import (
    MuseApproval, MuseOwnedChats, MuseProvider, approvals_from_muse, session_from_muse,
)
from thechatplace.providers.muse_connection import MuseHistoryError, MuseRequestError

NATIVE_ID = "019a84c2-a1f0-7e3b-9c1e-2f6a8b4d5e7f"


def policy():
    return {"approvalMode": {"mode": "promptUnmatched", "source": "startup", "lastCommandId": None},
            "permissionState": {"profileId": ":ask-me", "reviewerAuthority": "human", "reviewerAttached": True}}


def approval():
    return {"sessionId": NATIVE_ID, "approvalId": "permission-1", "toolName": "shell",
            "rawArgs": "Synthetic arguments", "subject": {"kind": "shell", "command": "Synthetic command"},
            "currentRequirementId": {"approvalId": "permission-1", "sourceIndex": 2},
            "availableChoices": [
                {"choiceId": "grant", "decision": "approved", "label": "Allow once", "scope": "once"},
                {"choiceId": "reject", "decision": "denied", "label": "Deny", "scope": "once"},
                {"choiceId": "keep", "decision": "approvedForSession", "label": "Always allow", "scope": "session"}]}


def question():
    return {"sessionId": NATIVE_ID, "userInputId": "question-1", "questions": [
        {"id": "folder", "header": "Folder", "question": "Which synthetic folder?",
         "options": [{"label": "One"}, {"label": "Two"}], "selection": {"mode": "single"}}]}


class Execution:
    closed = False

    def __init__(self):
        self.messages = []
        self.session = {"sessionId": NATIVE_ID, "status": "idle", "activeTurnId": None, **policy()}
        self.last_turn = None
        self.approvals, self.questions = [], []
        self.error = None
        self.profile_available = True
        self.events = []
        self.receipts = []

    def request(self, method, params):
        self.messages.append((method, copy.deepcopy(params)))
        if self.error and method == self.error[0]:
            raise self.error[1]
        if method.startswith("admission/"):
            # The installed 1.4.20 host refuses admission/* on Windows with
            # capabilityRequired: it belongs to peerMessaging, never requested.
            raise MuseRequestError(-32010)
        if method == "permissionProfile/list":
            assert params == {}
            return {"profiles": [{"profileId": ":ask-me", "label": "Ask Me", "available": self.profile_available,
                                  "reviewerAuthority": "human"}]}
        if method == "session/start":
            assert set(params) == {"commandId", "workspaceRoot", "approvalMode", "permissionProfile"}
            assert uuid.UUID(params["commandId"]).version == 7
            assert params["approvalMode"] == "promptUnmatched" and params["permissionProfile"] == ":ask-me"
            return {"session": copy.deepcopy(self.session), "viewCursor": "v:1"}
        if method == "session/read":
            assert params["sessionId"] == NATIVE_ID
            result = {"session": copy.deepcopy(self.session), "viewCursor": "v:1", "pendingRequests": [],
                      "history": {"mode": "inline", "items": []}}
            if self.last_turn:
                result["lastTurn"] = self.last_turn
            return result
        if method == "session/resume":
            assert params["sessionId"] == NATIVE_ID and params["autoRecover"] is False
            assert params["permissionProfile"] == ":ask-me" and uuid.UUID(params["commandId"]).version == 7
            self.session.update(status="idle", activeTurnId=None)
            self.session.update(policy())
            return {"session": copy.deepcopy(self.session), "viewCursor": "v:1", "pendingRequests": [],
                    "history": {"mode": "inline", "items": []}}
        if method == "turn/start":
            assert set(params) == {"sessionId", "commandId", "input"}
            assert params["input"][0]["type"] == "text" and params["input"][0]["text"].strip()
            assert uuid.UUID(params["commandId"]).version == 7
            self.session.update(status="running", activeTurnId=params["commandId"])
            return {"commandId": params["commandId"], "turnId": params["commandId"], "status": "accepted",
                    "disposition": "started", "startedNewTurn": True}
        if method == "turn/cancel":
            assert uuid.UUID(params["commandId"]).version == 7
            assert params["turnId"] == self.session["activeTurnId"]
            return {"commandId": params["commandId"], "turnId": params["turnId"], "status": "accepted"}
        if method == "approval/listPending":
            return {"approvals": copy.deepcopy(self.approvals), "userInputs": copy.deepcopy(self.questions)}
        for operation, field in [("approval/decide", "approvalId"),
                                 ("userInput/answer", "userInputId"), ("userInput/cancel", "userInputId")]:
            if method == operation:
                assert uuid.UUID(params["commandId"]).version == 7
                assert params["sessionId"] == NATIVE_ID and params[field]
                return {"commandId": params["commandId"], field: params[field], "status": "accepted", "terminal": True}
        raise AssertionError(method)

    def complete(self, terminal="completed"):
        self.last_turn = {"turnId": self.session["activeTurnId"], "terminal": terminal}
        self.session.update(status="idle", activeTurnId=None)

    def drain_events(self):
        result, self.events = self.events, []
        return result

    def respond_receipt(self, ident):
        self.receipts.append(ident)

    def close(self):
        self.closed = True


@pytest.fixture
def owned():
    execution = Execution()
    chats = MuseOwnedChats(execution)
    chats.set_persistence(lambda record: None)
    return chats


def record(owned):
    return owned.create("C:\\synthetic", "Synthetic chat")


def test_create_uses_native_contract_and_checks_human_catalog(owned):
    own = record(owned)
    assert own.provider == "muse" and own.started and own.cli_session_id == NATIVE_ID
    owned.execution.profile_available = False
    with pytest.raises(MuseHistoryError, match="profile is unavailable"):
        record(owned)
    assert len([m for m, _ in owned.execution.messages if m == "session/start"]) == 1


@pytest.mark.parametrize("cwd,title", [(" ", "Title"), ("C:\\synthetic", " ")])
def test_create_rejects_missing_folder_or_title(owned, cwd, title):
    with pytest.raises(MuseHistoryError):
        owned.create(cwd, title)
    assert not owned.execution.messages


def test_send_is_durable_before_wire_and_native_completion_is_required(owned):
    own = record(owned)
    saved = []
    owned.set_persistence(lambda item: saved.append(copy.deepcopy(item.provider_options["muse_command"])))
    key = owned.send(own, "Synthetic question")
    assert saved[0] == {"command_id": key, "turn_id": "", "state": "submitting"}
    assert saved[-1]["state"] == "running" and uuid.UUID(key).version == 7
    for _ in range(5):
        assert owned.poll(own)["state"] == "running"
    with pytest.raises(MuseHistoryError, match="already has a turn"):
        owned.send(own, "Another question")
    owned.settle(own)
    assert owned.in_flight(NATIVE_ID)
    owned.execution.complete()
    assert owned.poll(own)["state"] == "idle"
    assert not owned.in_flight(NATIVE_ID)
    assert own.provider_options["muse_command"]["state"] == "completed"
    assert owned.send(own, "Next question") != key


def test_send_refuses_without_durable_storage_and_does_not_submit(owned):
    own = record(owned)
    owned._persistence = None
    with pytest.raises(MuseHistoryError, match="save this turn before sending"):
        owned.send(own, "Synthetic question")
    assert "muse_command" not in own.provider_options
    assert not any(m == "turn/start" for m, _ in owned.execution.messages)


def test_save_failure_rolls_back_before_submission(owned):
    own = record(owned)
    owned.set_persistence(lambda item: (_ for _ in ()).throw(OSError("private detail")))
    with pytest.raises(MuseHistoryError, match="couldn't save") as error:
        owned.send(own, "Synthetic question")
    assert "private" not in str(error.value)
    assert "muse_command" not in own.provider_options
    assert not any(m == "turn/start" for m, _ in owned.execution.messages)


def test_ambiguous_send_stays_unknown_after_restart_until_matching_terminal(owned):
    own = record(owned)
    owned.execution.error = ("turn/start", MuseHistoryError("Unavailable"))
    with pytest.raises(MuseHistoryError):
        owned.send(own, "Synthetic question")
    key = own.provider_options["muse_command"]["command_id"]
    assert owned.poll(own)["state"] == "unknown"
    restarted = MuseOwnedChats(owned.execution)
    restarted.set_persistence(lambda item: None)
    assert restarted.poll(own)["state"] == "unknown"
    with pytest.raises(MuseHistoryError, match="outcome is unknown"):
        restarted.send(own, "Retry")
    owned.execution.last_turn = {"turnId": key, "terminal": "failed"}
    assert restarted.poll(own)["state"] == "idle"
    assert own.provider_options["muse_command"]["state"] == "completed"


def test_rejected_send_does_not_poison_connection_or_claim_running(owned):
    own = record(owned)
    owned.execution.error = ("turn/start", MuseRequestError(-32602))
    with pytest.raises(MuseRequestError):
        owned.send(own, "Synthetic question")
    assert not owned.in_flight(NATIVE_ID)
    assert own.provider_options["muse_command"]["state"] == "rejected"
    assert owned.poll(own)["state"] == "idle"


def test_stop_waits_for_confirmation_and_failed_stop_keeps_running(owned):
    own = record(owned)
    owned.send(own, "Synthetic question")
    owned.execution.error = ("turn/cancel", MuseHistoryError("Unavailable"))
    with pytest.raises(MuseHistoryError):
        owned.stop(own)
    assert owned.in_flight(NATIVE_ID)
    owned.execution.error = None
    owned.stop(own)
    assert owned.poll(own)["state"] == "cancelling"
    owned.execution.complete("cancelled")
    assert owned.poll(own)["state"] == "idle"


def test_prepare_only_owned_and_reopens_native_unloaded_chat(owned):
    own = record(owned)
    owned.execution.session["status"] = "notLoaded"
    assert owned.prepare(own)
    assert any(m == "session/resume" for m, _ in owned.execution.messages)
    with pytest.raises(MuseHistoryError, match="started here"):
        owned.prepare(OwnSession("foreign", "Other", "", provider="codex"))


def test_effective_auto_review_policy_blocks_send(owned):
    own = record(owned)
    owned.execution.session["permissionState"]["reviewerAuthority"] = "autoReview"
    with pytest.raises(MuseHistoryError, match="human permission review"):
        owned.send(own, "Synthetic question")
    assert not any(m == "turn/start" for m, _ in owned.execution.messages)


def test_parses_exact_native_approval_and_question_without_asking_for_admissions(owned):
    own = record(owned)
    owned.execution.approvals = [approval()]
    owned.execution.questions = [question()]
    pending = owned.pending_approvals(own)
    assert [p.kind for p in pending] == ["approval", "question"]
    assert not any(m.startswith("admission/") for m, _ in owned.execution.messages)
    assert pending[0].requirement == {"approvalId": "permission-1", "sourceIndex": 2}
    assert pending[1].questions[0]["id"] == "folder"


@pytest.mark.parametrize("change", [
    lambda raw: raw.update(sessionId="another-chat"),
    lambda raw: raw.update(currentRequirementId={"approvalId": "wrong", "sourceIndex": 1}),
    lambda raw: raw.update(availableChoices=[]),
    lambda raw: raw.update(subject={}),
])
def test_malformed_permissions_fail_closed(change):
    raw = approval()
    change(raw)
    with pytest.raises(MuseHistoryError):
        approvals_from_muse({"approvals": [raw], "userInputs": []}, NATIVE_ID)


def test_native_choice_and_requirement_are_sent_and_lasting_grants_refused(owned):
    own = record(owned)
    owned.execution.approvals = [approval()]
    pending = owned.pending_approvals(own)[0]
    owned.answer(own, pending, "grant")
    method, params = owned.execution.messages[-1]
    assert method == "approval/decide" and params["choiceId"] == "grant"
    assert params["approvalId"] == pending.request_id and params["requirementId"] == pending.requirement
    assert "decision" not in params
    with pytest.raises(MuseHistoryError, match="one-time"):
        owned.answer(own, pending, "keep")
    owned.execution.approvals[0]["currentRequirementId"]["sourceIndex"] = 3
    with pytest.raises(MuseHistoryError, match="changed"):
        owned.answer(own, pending, "grant")


def test_question_submits_actual_answer_and_cancellation_is_distinct(owned):
    own = record(owned)
    owned.execution.questions = [question()]
    request = owned.pending_approvals(own)[0]
    with pytest.raises(MuseHistoryError, match="Answer each"):
        owned.answer(own, request, "allow")
    answers = [{"questionId": "folder", "selectedLabel": "Two"}]
    owned.answer(own, request, "answer", answers)
    method, params = owned.execution.messages[-1]
    assert method == "userInput/answer" and params["answers"] == answers
    assert "answer" not in params and "cancelled" not in params
    owned.answer(own, request, "deny")
    assert owned.execution.messages[-1][0] == "userInput/cancel"


@pytest.mark.parametrize("answers", [
    [{"questionId": "wrong", "freeText": "Text"}],
    [{"questionId": "folder", "selectedLabel": "Invented"}],
    [{"questionId": "folder", "freeText": "x" * 501}],
    [{"questionId": "folder", "selectedLabels": ["One"]}],
    [{"questionId": "folder", "freeText": "Text", "selectedLabel": "One"}],
])
def test_invalid_question_answers_are_not_sent(owned, answers):
    own = record(owned)
    owned.execution.questions = [question()]
    request = owned.pending_approvals(own)[0]
    with pytest.raises(MuseHistoryError):
        owned.answer(own, request, "answer", answers)
    assert not any(m == "userInput/answer" for m, _ in owned.execution.messages)


def test_server_request_receipt_is_sent_only_after_request_recorded(owned):
    own = record(owned)
    owned.execution.approvals = [approval()]
    owned.execution.events = [{"jsonrpc": "2.0", "method": "approval/request", "id": "server-1", "params": approval()}]
    assert owned.poll(own)["pending"]
    assert owned.execution.receipts == ["server-1"]
    assert owned.poll(own)["pending"]
    assert owned.execution.receipts == ["server-1"]


def test_history_and_foreign_actions_remain_read_only(owned):
    provider = MuseProvider(connection=Execution(), execution=owned.execution)
    provider.set_owned_persistence(lambda record: None)
    foreign = session_from_muse({"sessionId": NATIVE_ID, "name": "Synthetic foreign chat"})
    assert provider.capabilities(foreign) == SessionCapabilities()
    own = provider.create_owned("C:\\synthetic", "Synthetic own chat")
    assert provider.capabilities(own.to_info()) == SessionCapabilities(send=True, approvals=True)
    assert provider.open_reader(foreign).connection is provider.connection
    assert provider.open_reader(own.to_info()).connection is owned.execution
    provider.close()


def test_unavailable_execution_never_creates(owned):
    provider = MuseProvider(connection=Execution())
    assert not provider.can_create()
    with pytest.raises(MuseHistoryError, match="unavailable"):
        provider.create_owned("C:\\synthetic", "Synthetic chat")
    provider.close()


def test_running_turn_polls_as_running_when_admissions_need_a_capability(owned):
    """Native 1.4.20 on Windows: admission/listPending answers
    capabilityRequired (-32010) because peerMessaging isn't granted. That
    must not turn a running turn into "unknown, execution unavailable"."""
    chat = record(owned)
    owned.send(chat, "Synthetic message")
    owned.execution.approvals = [approval()]
    polled = owned.poll(chat)
    assert polled["state"] == "waiting"
    assert [p.kind for p in polled["pending"]] == ["approval"]
    assert polled["notice"] == ""
    assert not any(m.startswith("admission/") for m, _ in owned.execution.messages)


def native_shell_approval():
    """Shaped like the request installed 1.4.20 sent for a network command:
    Reject is decision "abort", and the only lasting choice is a saved rule."""
    return {"sessionId": NATIVE_ID, "approvalId": "permission-2", "toolName": "powershell",
            "rawArgs": "Synthetic arguments",
            "subject": {"kind": "shell", "command": "synthetic-tool --flag"},
            "currentRequirementId": {"approvalId": "permission-2", "sourceIndex": 0},
            "availableChoices": [
                {"choiceId": "allow_once", "label": "Allow once", "decision": "approved", "scope": "once"},
                {"choiceId": "allow_local_prefix", "label": "Always allow in this workspace: synthetic-tool ...",
                 "decision": "approvedPolicyAmendment", "scope": "localPersistent",
                 "rulePreview": "Always allow in this workspace: synthetic-tool ..."},
                {"choiceId": "abort", "label": "Reject", "decision": "abort", "scope": "once",
                 "acceptsFeedback": True}],
            "protectedWrite": False, "judgeEscalated": False}


@pytest.mark.parametrize("decision,choice", [("deny", "abort"), ("allow", "allow_once"), ("abort", "abort")])
def test_native_shell_approval_decisions_reach_muse(owned, decision, choice):
    own = record(owned)
    owned.execution.approvals = [native_shell_approval()]
    request = owned.pending_approvals(own)[0]
    owned.answer(own, request, decision)
    method, params = owned.execution.messages[-1]
    assert method == "approval/decide" and params["choiceId"] == choice
    assert params["requirementId"] == {"approvalId": "permission-2", "sourceIndex": 0}


def test_deny_prefers_a_plain_denial_over_ending_the_turn(owned):
    raw = native_shell_approval()
    raw["availableChoices"].append({"choiceId": "reject", "label": "Deny", "decision": "denied", "scope": "once"})
    own = record(owned)
    owned.execution.approvals = [raw]
    owned.answer(own, owned.pending_approvals(own)[0], "deny")
    assert owned.execution.messages[-1][1]["choiceId"] == "reject"


def test_saved_rule_choice_is_never_sent(owned):
    own = record(owned)
    owned.execution.approvals = [native_shell_approval()]
    with pytest.raises(MuseHistoryError):
        owned.answer(own, owned.pending_approvals(own)[0], "allow_local_prefix")
    assert not any(m == "approval/decide" for m, _ in owned.execution.messages)


@pytest.mark.parametrize("terminal", ["completed", "cancelled", "failed"])
def test_poll_reports_how_the_native_turn_ended(owned, terminal):
    """Native 1.4.20 reports a stopped turn as lastTurn.terminal "cancelled";
    the window must not hear that as finished."""
    own = record(owned)
    owned.send(own, "Synthetic question")
    if terminal == "cancelled":
        owned.stop(own)
    owned.execution.complete(terminal)
    polled = owned.poll(own)
    assert polled["state"] == "idle" and polled["terminal"] == terminal
    assert own.provider_options["muse_command"]["terminal"] == terminal


@pytest.mark.parametrize("terminal", ["completed", "cancelled"])
def test_turn_end_saves_through_the_real_store(tmp_path, terminal):
    """The window saves through OwnSessionStore, which validates every key.
    A save it refused made poll() report "execution unavailable" for every
    completed turn, so exercise the real store, not a do-nothing callback."""
    from thechatplace.own_store import OwnSessionStore
    store = OwnSessionStore(tmp_path / "sessions.json")
    chats = MuseOwnedChats(Execution())
    chats.set_persistence(lambda item: store.update(
        item.cli_session_id, provider="muse", provider_options=dict(item.provider_options)))
    own = record(chats)
    store.add(own)
    chats.send(own, "Synthetic question")
    if terminal == "cancelled":
        chats.stop(own)
    chats.execution.complete(terminal)
    polled = chats.poll(own)
    assert polled["state"] == "idle" and polled["notice"] == ""
    saved = OwnSessionStore(tmp_path / "sessions.json").get(NATIVE_ID, "muse")
    assert saved.provider_options["muse_command"]["terminal"] == terminal


def test_a_failed_save_says_so_instead_of_execution_unavailable(owned):
    own = record(owned)
    owned.send(own, "Synthetic question")
    owned.execution.complete()
    def refuse(item):
        raise ValueError("refused")
    owned.set_persistence(refuse)
    polled = owned.poll(own)
    assert polled["state"] == "unknown"
    assert "couldn't save" in polled["notice"] and "unavailable" not in polled["notice"]


def test_stop_says_whether_anything_was_running(owned):
    """The window words Stop from Muse's answer, not its own guess."""
    own = record(owned)
    assert owned.stop(own) == "idle"
    assert not any(m == "turn/cancel" for m, _ in owned.execution.messages)
    owned.send(own, "Synthetic question")
    assert owned.stop(own) == "cancelling"
