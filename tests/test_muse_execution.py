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
        self.session = {"sessionId": NATIVE_ID, "status": "idle", "activeTurnId": None,
                        "modelId": "synthetic-model-b", **policy()}
        # Shaped like installed 1.4.20's catalog (ids synthetic).
        self.catalog = [
            {"modelId": "synthetic-model-a", "displayLabel": "synthetic-model-a", "providerId": "meta",
             "profileId": "tbh", "isDefault": True,
             "variants": ["low", "medium", "high"], "defaultReasoningEffort": "medium"},
            {"modelId": "synthetic-model-b", "displayLabel": "synthetic-model-b", "providerId": "meta",
             "profileId": "tbh", "isDefault": False,
             "variants": ["low", "medium", "high", "max"], "defaultReasoningEffort": "max"}]
        # The session's view, oldest first; view/page reads it newest page first.
        self.view = [{"method": "session/approvalModeChanged", "params": {"sessionId": NATIVE_ID}}]
        self.last_turn = None
        self.approvals, self.questions = [], []
        self.error = None
        self.profile_available = True
        self.events = []
        self.receipts = []
        # Shaped like installed 1.4.20's skill/list rows (names synthetic).
        self.skills = [
            {"selector": "synthetic-plan", "displayName": "synthetic-plan", "source": "bundled",
             "description": "Plan a synthetic change."},
            {"selector": "acme:deploy", "displayName": "deploy", "source": "plugin", "pluginId": "acme",
             "description": "Deploy synthetically.", "argumentHint": "[target]"}]

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
        if method == "skill/list":
            assert params == {"sessionId": NATIVE_ID}
            return {"skills": copy.deepcopy(self.skills)}
        if method == "turn/start":
            assert set(params) == {"sessionId", "commandId", "input"}
            part = params["input"][0]
            if part["type"] == "skill":
                # Installed 1.4.20: the host resolves the selector; an unknown
                # one is skillNotFound (-32032) and no turn starts.
                assert set(part) <= {"type", "selector", "source", "arguments"}
                if not any(s["selector"] == part["selector"] and s["source"] == part["source"]
                           for s in self.skills):
                    raise MuseRequestError(-32032)
            else:
                assert part["type"] == "text" and part["text"].strip()
            for extra in params["input"][1:]:
                # Installed 1.4.20 takes images after the text; bad base64 is -32602.
                assert extra["type"] == "image" and set(extra) <= {
                    "type", "base64Data", "mediaType", "clientRef", "width", "height"}
                assert extra["base64Data"] and extra["mediaType"].startswith("image/")
            assert uuid.UUID(params["commandId"]).version == 7
            self.session.update(status="running", activeTurnId=params["commandId"])
            return {"commandId": params["commandId"], "turnId": params["commandId"], "status": "accepted",
                    "disposition": "started", "startedNewTurn": True}
        if method == "model/list":
            active = self.session["modelId"] if params.get("sessionId") == NATIVE_ID else None
            return {"providerId": "meta", "profileId": "tbh", "source": "providerCatalog",
                    "models": [{**m, "isActive": m["modelId"] == active, "contextLimit": None,
                                "outputLimit": None, "cost": None, "description": None,
                                "releaseDate": None} for m in self.catalog]}
        if method == "view/page":
            # Installed 1.4.20: backward pages run newest first, each page in
            # order, and nextCursor steps towards the start.
            assert params["direction"] == "backward" and params["sessionId"] == NATIVE_ID
            end = int(params["cursor"]) if params.get("cursor") else len(self.view)
            start = max(0, end - params["limit"])
            return {"events": copy.deepcopy(self.view[start:end]),
                    "nextCursor": str(start) if start else None}
        if method == "session/setReasoningEffort":
            # Installed 1.4.20 accepts any tier, even one the model doesn't offer.
            assert set(params) == {"sessionId", "commandId", "reasoningEffort"}
            assert uuid.UUID(params["commandId"]).version == 7
            self.view.append({"method": "session/reasoningEffortChanged",
                              "params": {"sessionId": NATIVE_ID, "reasoningEffort": params["reasoningEffort"],
                                         "source": "client"}})
            return {"commandId": params["commandId"], "status": "accepted"}
        if method == "session/setModel":
            # Installed 1.4.20 accepts any id, even one not in its catalog.
            assert set(params) == {"sessionId", "commandId", "model"}
            assert uuid.UUID(params["commandId"]).version == 7
            self.session["modelId"] = params["model"]["modelId"]
            return {"commandId": params["commandId"], "status": "accepted"}
        if method == "turn/steer":
            # Installed 1.4.20: accepted only for the turn still running,
            # else commandRejected (-32030); the ack names that turn.
            assert set(params) == {"sessionId", "commandId", "expectedTurnId", "input"}
            assert uuid.UUID(params["commandId"]).version == 7
            assert params["input"] and params["input"][0]["type"] == "text"
            if (self.session["status"] != "running"
                    or params["expectedTurnId"] != self.session["activeTurnId"]):
                raise MuseRequestError(-32030)
            return {"commandId": params["commandId"], "status": "accepted",
                    "turnId": params["expectedTurnId"]}
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

    def complete(self, terminal="completed", error_kind=None):
        self.last_turn = {"turnId": self.session["activeTurnId"], "terminal": terminal}
        if error_kind:
            # As installed 1.4.20 ends a turn on a model the account can't use.
            self.last_turn.update(reason="synthetic host text [request_id=x]",
                                  error={"kind": error_kind, "message": "synthetic host text",
                                         "retryable": False})
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
    assert provider.capabilities(own.to_info()) == SessionCapabilities(send=True, approvals=True, steer=True, aside=True, attachments=True)
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



def test_steer_reaches_the_running_turn_and_its_turn_counts_as_the_end(owned):
    """Native 1.4.20 records a steer as its own turn, id = the steer's
    commandId, after the steered one: that turn ending ends the chat's turn."""
    own = record(owned)
    saved = []
    owned.set_persistence(lambda item: saved.append(copy.deepcopy(item.provider_options["muse_command"])))
    owned.send(own, "Synthetic question")
    turn = owned.execution.session["activeTurnId"]
    steer = owned.steer(own, "Synthetic change of plan")
    method, params = owned.execution.messages[-1]
    assert method == "turn/steer" and params["expectedTurnId"] == turn
    assert params["input"] == [{"type": "text", "text": "Synthetic change of plan"}]
    assert steer in saved[-1]["steers"]  # saved before it went
    owned.execution.last_turn = {"turnId": steer, "terminal": "completed"}
    owned.execution.session.update(status="idle", activeTurnId=None)
    polled = owned.poll(own)
    assert polled["state"] == "idle" and polled["terminal"] == "completed"


def test_a_late_steer_is_refused_and_forgotten(owned):
    own = record(owned)
    owned.send(own, "Synthetic question")
    # Read as running, ended before the steer arrived: Muse refuses it.
    owned.execution.error = ("turn/steer", MuseRequestError(-32030))
    with pytest.raises(MuseHistoryError, match="already ended"):
        owned.steer(own, "Synthetic change of plan")
    assert not own.provider_options["muse_command"].get("steers")


def test_steer_needs_a_running_turn_and_sends_nothing_otherwise(owned):
    own = record(owned)
    with pytest.raises(MuseHistoryError, match="no turn running"):
        owned.steer(own, "Synthetic change of plan")
    assert not any(m == "turn/steer" for m, _ in owned.execution.messages)


def test_steer_is_a_capability_of_chats_started_here_only(owned):
    provider = MuseProvider(connection=object(), execution=owned.execution)
    own_info = record(owned).to_info()
    foreign = session_from_muse({"sessionId": "external-1", "name": "Synthetic"})
    assert provider.capabilities(own_info).steer is True
    assert provider.capabilities(foreign).steer is False


def test_muse_reasons_reach_the_window():
    """The window shows a worker's error only through safe_reason. Muse's
    messages are all the app's own words (MuseRequestError keeps just the
    code), so they're shown; without this every Muse refusal became generic."""
    from thechatplace.codex_diagnostics import safe_reason
    assert safe_reason(MuseHistoryError("That turn has already ended.")) == "That turn has already ended."
    assert safe_reason(MuseRequestError(-32030)) == str(MuseRequestError(-32030))
    assert safe_reason(MuseHistoryError("x" * 1001)) == ""
    assert safe_reason(MuseHistoryError("line\nbreak")) == ""



def test_models_lists_the_catalog_with_this_chats_model_marked(owned):
    own = record(owned)
    assert owned.models(own) == (("synthetic-model-a", "synthetic-model-a", False),
                                 ("synthetic-model-b", "synthetic-model-b", True))
    assert ("model/list", {"sessionId": NATIVE_ID}) in owned.execution.messages


def test_set_model_sends_the_catalog_entry_and_confirms_it(owned):
    own = record(owned)
    assert owned.set_model(own, "synthetic-model-a") == ("synthetic-model-a", "")
    method, params = [m for m in owned.execution.messages if m[0] == "session/setModel"][0]
    assert params["sessionId"] == NATIVE_ID
    assert params["model"] == {"modelId": "synthetic-model-a", "providerId": "meta", "profileId": "tbh"}
    assert owned.execution.session["modelId"] == "synthetic-model-a"


def test_set_model_refuses_a_model_muse_does_not_list(owned):
    """Installed 1.4.20 took "not-a-model" and kept it, leaving the chat with
    no usable model: only a listed id is ever sent."""
    own = record(owned)
    with pytest.raises(MuseHistoryError, match="doesn't offer"):
        owned.set_model(own, "not-a-model")
    assert not any(m == "session/setModel" for m, _ in owned.execution.messages)


def test_set_model_reports_a_change_muse_did_not_make(owned, monkeypatch):
    own = record(owned)
    answer = owned.execution.request
    def ignore_set(method, params):
        if method == "session/setModel":
            owned.execution.messages.append((method, params))
            return {"commandId": params["commandId"], "status": "accepted"}
        return answer(method, params)
    monkeypatch.setattr(owned.execution, "request", ignore_set)
    with pytest.raises(MuseHistoryError, match="didn't confirm"):
        owned.set_model(own, "synthetic-model-a")



def test_poll_says_why_a_turn_failed_by_kind_never_host_text(owned):
    own = record(owned)
    owned.send(own, "Synthetic question")
    owned.execution.complete("failed", error_kind="modelError")
    polled = owned.poll(own)
    assert polled["terminal"] == "failed" and polled["failure"] == "modelError"
    assert "synthetic host text" not in repr(polled)


def test_a_finished_turn_reports_no_failure(owned):
    own = record(owned)
    owned.send(own, "Synthetic question")
    owned.execution.complete()
    assert owned.poll(own)["failure"] == ""



def test_efforts_offer_this_models_tiers_and_say_its_default_until_one_is_set(owned):
    own = record(owned)
    choices, now = owned.efforts(own)
    assert choices == (("low", "low"), ("medium", "medium"), ("high", "high"), ("max", "maximum"))
    assert now == "max"  # synthetic-model-b's default; none set yet


def test_set_effort_sends_a_tier_this_model_offers_and_confirms_it(owned):
    own = record(owned)
    assert owned.set_effort(own, "low") == "low"
    assert "session/setReasoningEffort" in [m for m, _ in owned.execution.messages]
    assert owned.efforts(own)[1] == "low"


def test_set_effort_refuses_a_tier_the_model_does_not_offer(owned):
    """Installed 1.4.20 took "max" for a model without it; the next turn failed."""
    own = record(owned)
    owned.execution.session["modelId"] = "synthetic-model-a"
    with pytest.raises(MuseHistoryError, match="doesn't offer"):
        owned.set_effort(own, "max")
    assert not any(m == "session/setReasoningEffort" for m, _ in owned.execution.messages)


def test_the_latest_effort_is_found_pages_back(owned):
    own = record(owned)
    owned.set_effort(own, "high")
    owned.execution.view += [{"method": "item/delta", "params": {}}] * 450
    assert owned.efforts(own)[1] == "high"


def test_an_effort_beyond_the_page_bound_is_unknown(owned):
    own = record(owned)
    owned.set_effort(own, "high")
    owned.execution.view += [{"method": "item/delta", "params": {}}] * 2001
    assert owned.efforts(own)[1] is None


def test_changing_to_a_model_without_the_chats_effort_sets_its_default(owned):
    """Otherwise every turn after the switch would fail (seen natively)."""
    own = record(owned)
    owned.set_effort(own, "max")
    label, note = owned.set_model(own, "synthetic-model-a")
    assert label == "synthetic-model-a"
    assert owned.efforts(own)[1] == "medium"
    assert "medium" in note and "maximum" in note


def test_changing_model_keeps_an_effort_the_new_model_offers(owned):
    own = record(owned)
    owned.set_effort(own, "low")
    label, note = owned.set_model(own, "synthetic-model-a")
    assert note == "" and owned.efforts(own)[1] == "low"



def test_skills_lists_this_chats_skills(owned):
    own = record(owned)
    assert owned.skills(own) == (
        {"name": "synthetic-plan", "description": "Plan a synthetic change.", "argumentHint": "",
         "source": "bundled"},
        {"name": "acme:deploy", "description": "Deploy synthetically.", "argumentHint": "[target]",
         "source": "plugin"})


def _started(owned):
    return [p for m, p in owned.execution.messages if m == "turn/start"][-1]["input"]


@pytest.mark.parametrize("text,expected", [
    ("/synthetic-plan Outline it briefly.",
     [{"type": "skill", "selector": "synthetic-plan", "source": "bundled", "arguments": "Outline it briefly."}]),
    ("/acme:deploy\nstaging", [{"type": "skill", "selector": "acme:deploy", "source": "plugin",
                                 "arguments": "staging"}]),
    ("/synthetic-plan", [{"type": "skill", "selector": "synthetic-plan", "source": "bundled"}]),
    # Not a listed skill, or not at the start: sent as you typed it (as Muse does).
    ("/not-a-skill hello", [{"type": "text", "text": "/not-a-skill hello"}]),
    ("Use /synthetic-plan here", [{"type": "text", "text": "Use /synthetic-plan here"}]),
])
def test_a_message_starting_with_a_listed_skill_invokes_it(owned, text, expected):
    """Installed 1.4.20 passes "/name" in a text part to the model as words;
    only a skill part invokes the skill."""
    own = record(owned)
    owned.send(own, text)
    assert _started(owned) == expected


def test_only_a_slash_message_reads_the_skill_list(owned):
    own = record(owned)
    owned.send(own, "Plain synthetic message")
    assert not any(m == "skill/list" for m, _ in owned.execution.messages)


def test_a_skill_gone_since_listing_says_so(owned, monkeypatch):
    own = record(owned)
    listed = copy.deepcopy(owned.execution.skills)
    answer = owned.execution.request
    def stale_list(method, params):
        if method == "skill/list":
            owned.execution.messages.append((method, params))
            return {"skills": listed}
        return answer(method, params)
    monkeypatch.setattr(owned.execution, "request", stale_list)
    owned.execution.skills = []  # removed in Muse after the list was read
    with pytest.raises(MuseHistoryError, match="doesn't have that skill"):
        owned.send(own, "/synthetic-plan Go")
    assert own.provider_options["muse_command"]["state"] == "rejected"
