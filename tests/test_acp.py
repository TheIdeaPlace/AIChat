"""What the generic ACP backend must do for any agent, and fail safely on."""
from __future__ import annotations

import json
import os

import pytest

from harness import MODE_AGENT, MODE_BAD, MODE_PLAN, Run, acp, fake_definition
from thechatplace.contracts import (AskPermission, Capability, Decision, IntegrationError, PermissionRequest, Refused,
                                    ReplyText, TurnStatus)
from thechatplace import policy
from thechatplace.integrations import acp as acp_module
from thechatplace.integrations.agents import AGENTS, COPILOT


@pytest.fixture
def setup(tmp_path):
    runs = []
    folder = tmp_path / "work"
    folder.mkdir()

    def make(integration, name="data"):
        data = tmp_path / name
        data.mkdir(exist_ok=True)
        run = Run(integration, data, folder)
        runs.append(run)
        return run

    yield make
    for run in runs:
        run.close()


def test_a_second_agent_needs_only_a_definition(setup):
    """The test of the whole design (#489): nothing but one row."""
    definition = fake_definition(id="brandnew", name="Brand New", agent_names=("Fake",))
    run = setup(acp("answer", definition=definition))
    info = run.start()
    assert info.provider == "Brand New" and info.ref.integration == "brandnew"
    run.service.send(info.ref, "Hi")
    assert run.end_turn().status is TurnStatus.COMPLETED


def test_two_agents_returning_one_id_stay_separate(setup, tmp_path):
    one = setup(acp("answer", "--fixed-id", definition=fake_definition(id="a", name="A")), "shared")
    two_integration = acp("answer", "--fixed-id", definition=fake_definition(id="b", name="B"))
    from thechatplace.service import ChatService
    two = ChatService([two_integration], one.service.store)
    first = one.start()
    second = two.start_chat("b", str(one.folder), "B chat", {})
    assert first.ref.native_id == second.ref.native_id == "same-id"
    assert first.ref != second.ref
    keys = one.service.store.owned_keys()
    assert {"a:same-id", "b:same-id"} <= keys
    two.close()


def test_protocol_v2_fails_clearly(setup):
    run = setup(acp("answer", "--protocol", "2"))
    with pytest.raises(IntegrationError, match="isn't supported"):
        run.start()


def test_an_agent_with_another_name_is_refused_when_names_are_listed(setup):
    run = setup(acp("answer", definition=fake_definition(agent_names=("Someone Else",))))
    with pytest.raises(IntegrationError, match="isn't supported"):
        run.start()


def test_signed_out_gives_the_hint_and_none_of_the_agents_text(setup):
    run = setup(acp("signedout"))
    with pytest.raises(IntegrationError) as caught:
        run.start()
    assert "Sign in to the fake." in str(caught.value)
    assert "user@example.com" not in str(caught.value)


def test_without_load_session_a_chat_says_it_cant_reopen(setup):
    run = setup(acp("answer", "--no-load"))
    info = run.start()
    run.service.send(info.ref, "Hi")
    run.end_turn()
    run.idle(info.ref)
    run.integration.close()
    # Reconnected, it can't load the chat: the turn fails with a reason.
    run.service.send(info.ref, "Again")
    ended = run.end_turn()
    assert ended.status is TurnStatus.FAILED and "can't reopen" in ended.message
    run.idle(info.ref)
    # And now the capability says what the behaviour said.
    assert Capability.SEND not in run.service.info(info.ref).capabilities


def test_a_reloaded_chat_drops_its_replayed_history(setup):
    run = setup(acp("answer"))
    info = run.start()
    run.service.send(info.ref, "One")
    run.end_turn()
    run.idle(info.ref)
    run.integration.close()
    run.service.send(info.ref, "Two")
    assert run.end_turn().status is TurnStatus.COMPLETED
    run.idle(info.ref)
    assert not any("replayed history" in t for t in run.text(info.ref))


def test_only_one_time_choices_are_offered_with_our_words(setup):
    run = setup(acp("ask"))
    info = run.start()
    run.service.send(info.ref, "Hi")
    request = run.wait(AskPermission).request
    assert request.decisions == (Decision.ALLOW_ONCE, Decision.DENY)
    # The agent's "Definitely safe" appears nowhere a decision is labelled.
    assert "Definitely safe" not in repr(request)
    run.service.answer_permission(info.ref, request, Decision.DENY)
    run.end_turn()


def test_permission_with_no_way_to_say_no_is_refused(setup):
    run = setup(acp("badperm"))
    info = run.start()
    run.service.send(info.ref, "Hi")
    run.end_turn()
    run.idle(info.ref)
    assert run.service.state(info.ref).pending_permission is None
    assert any("without a way to say no" in t for t in run.text(info.ref))


def test_an_unverified_agents_allow_carries_a_caution(setup):
    run = setup(acp("ask", definition=fake_definition(verified_versions=())))
    info = run.start()
    run.service.send(info.ref, "Hi")
    request = run.wait(AskPermission).request
    assert request.caution == policy.UNVERIFIED_CAUTION
    run.service.answer_permission(info.ref, request, Decision.DENY)
    run.end_turn()


def test_an_agents_other_requests_are_refused(setup, tmp_path, monkeypatch):
    log = tmp_path / "agent.log"
    run = setup(acp("clientreq", env={"FAKE_ACP_LOG": str(log)}))
    info = run.start()
    run.service.send(info.ref, "Hi")
    assert run.end_turn().status is TurnStatus.COMPLETED
    run.idle(info.ref)
    replies = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    refusal = next(m for m in replies if m.get("id") == "fs-1")
    assert refusal["error"]["code"] == -32601
    assert run.integration.drift.snapshot()["unknown"] == {"request:fs/read_text_file": 1}


@pytest.mark.parametrize("mode", ["autopilot", "allowall"])
def test_an_agent_that_stops_asking_mid_turn_is_stopped(setup, mode):
    run = setup(acp(mode))
    info = run.start()
    run.service.send(info.ref, "Hi")
    ended = run.end_turn()
    assert ended.status is TurnStatus.STOPPED
    assert "stop" in ended.message.lower()


def test_a_chat_starting_unsafe_isnt_started(setup):
    run = setup(acp("startbad"))
    with pytest.raises(Refused, match="without asking"):
        run.start()


def test_an_agent_ignoring_cancel_is_closed_after_the_grace(setup, monkeypatch):
    monkeypatch.setattr(acp_module, "STOP_GRACE_SECONDS", 0.2)
    run = setup(acp("deaf"))
    info = run.start()
    run.service.send(info.ref, "Hi")
    run.wait(ReplyText)
    run.service.stop(info.ref)
    assert run.end_turn(timeout=10).status is TurnStatus.STOPPED


def test_a_late_message_from_a_replaced_connection_is_dropped(setup):
    run = setup(acp("answer"))
    info = run.start()
    current = run.integration._connection
    live = run.integration._live[info.ref.native_id]
    told = []
    live.emit = told.append
    old = object()
    update = {"sessionId": info.ref.native_id, "update": {"sessionUpdate": "agent_message_chunk",
                                                         "content": {"type": "text", "text": "late"}}}
    run.integration._on_notification(old, "session/update", update)
    assert told == []
    run.integration._on_notification(current, "session/update", update)
    assert len(told) == 1
    live.emit = None


def test_an_old_connection_closing_leaves_its_replacement_alone(setup):
    run = setup(acp("answer"))
    run.start()
    old = run.integration._connection
    replacement = object()
    run.integration._connection = replacement
    run.integration._on_closed(old)
    assert run.integration._connection is replacement
    run.integration._connection = old


def test_two_requests_at_once_are_both_asked_and_answered_apart(setup):
    run = setup(acp("asktwo"))
    info = run.start()
    run.service.send(info.ref, "Hi")
    first, second = run.wait(AskPermission).request, run.wait(AskPermission).request
    assert "perm-1" in first.detail and "perm-2" in second.detail
    run.service.answer_permission(info.ref, second, Decision.DENY)
    assert run.service.state(info.ref).pending == [first]
    run.service.answer_permission(info.ref, first, Decision.ALLOW_ONCE)
    assert run.end_turn().status is TurnStatus.COMPLETED
    run.idle(info.ref)
    assert "assistant:yes no" in run.text(info.ref)


def test_allow_never_rests_on_the_agents_title_alone(setup):
    run = setup(acp("titleonly"))
    info = run.start()
    run.service.send(info.ref, "Hi")
    request = run.wait(AskPermission).request
    assert "A harmless typo fix" in request.detail  # shown, as its words
    assert request.decisions == (Decision.DENY,)
    run.service.answer_permission(info.ref, request, Decision.ALLOW_ONCE)
    run.end_turn()
    run.idle(info.ref)
    assert "assistant:Denied" in run.text(info.ref)


def test_a_call_is_described_in_full_and_can_be_allowed_only_then():
    describe = acp_module.AcpIntegration._describe_call
    detail, can_allow = describe({"title": "T", "rawInput": {"command": "ls", "dangerouslyDisableSandbox": True}})
    assert can_allow and "dangerouslyDisableSandbox: true" in detail and "command: ls" in detail
    detail, can_allow = describe({"title": "T", "rawInput": {"command": "x" * 9000 + "; rm -rf ~"}})
    assert not can_allow and "cut short" in detail
    detail, can_allow = describe({"title": "T", "locations": [{"path": "a.py"}]})
    assert can_allow and "File: a.py" in detail
    detail, can_allow = describe({"title": "T", "content": [{"type": "diff", "path": "a.py", "newText": "new"}]})
    assert can_allow and "newText: new" in detail
    assert describe({"title": "T"})[1] is False and describe(None) == ("", False)


def test_a_chosen_model_is_put_back_after_a_reconnect(setup, tmp_path):
    log = tmp_path / "agent.log"
    run = setup(acp("answer", env={"FAKE_ACP_LOG": str(log)}))
    info = run.start()
    run.service.set_option(info.ref, "model", "m2")
    assert run.service.store.get_chat(info.ref).options["model"] == "m2"
    run.integration.close()
    log.write_text("", encoding="utf-8")
    run.service.send(info.ref, "Hi")
    assert run.end_turn().status is TurnStatus.COMPLETED
    sent = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    methods = [m.get("method") for m in sent]
    put_back = next(m for m in sent if m.get("method") == "session/set_config_option")
    assert put_back["params"]["configId"] == "model" and put_back["params"]["value"] == "m2"
    assert methods.index("session/load") < methods.index("session/set_config_option") < methods.index("session/prompt")


def test_a_mode_is_set_through_the_option_the_agent_named(setup):
    integration = acp("answer")
    live = acp_module._Live("id", "folder")
    integration._read_config(live, [{"type": "select", "id": "posture", "category": "mode", "currentValue": MODE_AGENT}])
    assert live.mode == MODE_AGENT and live.mode_option == "posture"


def test_two_at_once_starting_it_share_one_program(setup):
    import threading
    started = []
    definition = fake_definition()
    real = acp("answer", definition=definition)
    spawn = real._start

    def counting(cwd):
        started.append(cwd)
        return spawn(cwd)
    real._start = counting
    run = setup(real)
    results = []
    threads = [threading.Thread(target=lambda: results.append(run.start())) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert len(results) == 3 and len(started) == 1


def test_a_program_still_starting_when_turned_off_isnt_kept(setup, tmp_path):
    import threading
    real = acp("answer")
    spawn = real._start
    release = threading.Event()

    def slow(cwd):
        release.wait(10)
        return spawn(cwd)
    real._start = slow
    run = setup(real)
    failure = []

    def start():
        try:
            run.start()
        except IntegrationError as error:
            failure.append(str(error))
    thread = threading.Thread(target=start)
    thread.start()
    real.close()
    release.set()
    thread.join(15)
    assert failure and "turned off" in failure[0]
    assert real._connection is None


def test_two_chats_can_run_at_once_on_one_agent(setup):
    run = setup(acp("answer"))
    one, two = run.start(), run.start()
    run.service.send(one.ref, "For one")
    run.service.send(two.ref, "For two")
    run.end_turn()
    run.end_turn()
    run.idle(one.ref)
    run.idle(two.ref)
    assert any("For one" in t and t.startswith("assistant") for t in run.text(one.ref))
    assert any("For two" in t and t.startswith("assistant") for t in run.text(two.ref))
    assert not any("For two" in t for t in run.text(one.ref))


def test_a_chat_set_to_plan_never_runs_after_failing_to_get_back_there(setup, monkeypatch):
    run = setup(acp("answer"))
    info = run.start(mode=MODE_PLAN)
    run.integration.close()
    chat = run.service.store.get_chat(info.ref)
    real = run.integration._set_mode

    def refuses(*args):
        raise IntegrationError("no")
    monkeypatch.setattr(run.integration, "_set_mode", refuses)
    run.service.send(info.ref, "Hi")
    assert run.end_turn().status is TurnStatus.FAILED
    run.idle(info.ref)
    assert chat.ref.native_id not in run.integration._live
    monkeypatch.setattr(run.integration, "_set_mode", real)


def test_garbage_lines_are_counted_and_harmless(setup):
    run = setup(acp("garbage"))
    info = run.start()
    run.service.send(info.ref, "Hi")
    assert run.end_turn().status is TurnStatus.COMPLETED
    assert run.integration.drift.snapshot()["malformed"]


def test_slash_commands_are_refused_before_anything_is_sent(setup, tmp_path):
    log = tmp_path / "agent.log"
    run = setup(acp("answer", env={"FAKE_ACP_LOG": str(log)}))
    info = run.start()
    with pytest.raises(Refused, match="slash"):
        run.service.send(info.ref, "/allow-all on")
    assert "allow-all" not in log.read_text(encoding="utf-8")


def test_options_show_only_what_policy_understands(setup):
    run = setup(acp("answer"))
    info = run.start()
    groups = {g.id: g for g in run.service.chat_options(info.ref)}
    assert set(groups) == {"mode", "model"}  # allow_all isn't one to offer
    # Our labels, and the unsafe mode isn't there, whatever it calls itself.
    assert [v.label for v in groups["mode"].values] == ["Agent", "Plan"]
    assert [v.id for v in groups["model"].values] == ["m1", "m2"]  # each only once
    run.service.set_option(info.ref, "mode", MODE_PLAN)
    assert run.service.store.get_chat(info.ref).options["mode"] == MODE_PLAN
    run.service.set_option(info.ref, "model", "m2")
    with pytest.raises(Refused):
        run.service.set_option(info.ref, "mode", MODE_BAD)
    with pytest.raises(Refused):
        run.service.set_option(info.ref, "allow_all", "on")


def test_every_listed_agent_has_what_a_definition_needs():
    assert len({a.id for a in AGENTS}) == len(AGENTS)
    for agent in AGENTS:
        assert agent.name and agent.program and agent.sign_in_hint and agent.billing


def test_copilot_cant_be_put_in_autopilot_and_its_billing_cant_be_moved():
    from thechatplace import platform_paths
    integration = acp_module.AcpIntegration(COPILOT)
    env = platform_paths.clean_environment(COPILOT.strip_env_names, COPILOT.strip_env_prefixes,
                                           {"github_token": "x", "COPILOT_PROVIDER_URL": "y", "PATH": "keep"})
    assert env == {"PATH": "keep"}
    live = acp_module._Live("id", "folder", mode="https://agentclientprotocol.com/protocol/session-modes#autopilot")
    assert integration._safety_problem(live)
    live.mode = next(iter(COPILOT.safe_modes))
    live.config["allow_all"] = "on"
    assert integration._safety_problem(live)
    live.config["allow_all"] = "off"
    assert integration._safety_problem(live) is None
