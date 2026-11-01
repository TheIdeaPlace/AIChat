"""Chat Options and Ask Aside (#468), without windows: the command lines,
their refusals, what's kept with a chat, and the aside runner on a fake
Claude Code."""
import json
import sys
import threading
from pathlib import Path

import pytest

from thechatplace import chat_options as co
from thechatplace.aside import (ASIDE_TOOLS, AsideItem, AsideRunner, COWORK_REFUSED,
                                build_aside_command)
from thechatplace.chat_options import ChatOptions, choices_from_init
from thechatplace.claude_cli import (ResumeRefused, build_fork_command, build_new_command,
                                     build_resume_command, child_environment)
from thechatplace.own_store import OwnSession, OwnSessionStore
from thechatplace import claude_cli as cli

EXE = "claude.exe"
FAKE_CLAUDE = Path(__file__).with_name("fake_claude.py")


# -- the options' flags ---------------------------------------------------------------------


def test_no_options_pass_nothing():
    assert ChatOptions().flags() == []
    assert not ChatOptions()
    assert build_new_command(EXE, "aaaa-1111", "", "auto", options=ChatOptions()) == \
        build_new_command(EXE, "aaaa-1111", "", "auto")


def test_each_option_becomes_its_flag():
    options = ChatOptions("code-reviewer", "Answer in French.\nKeep it short.", "Concise")
    flags = options.flags()
    assert flags[:2] == ["--agent", "code-reviewer"]
    # One argument, joined with "=", so the text can never read as a flag.
    assert flags[2] == "--append-system-prompt=Answer in French.\nKeep it short."
    assert flags[3:5] == ["--settings", json.dumps({"outputStyle": "Concise"})]
    assert "--system-prompt-snapshot" not in flags


def test_instructions_that_look_like_flags_stay_one_argument():
    command = build_new_command(EXE, "aaaa-1111", "", "auto",
                                options=ChatOptions(instructions="--model opus --bare"))
    assert "--append-system-prompt=--model opus --bare" in command
    assert "--bare" not in command and command.count("--model") == 0


def test_options_changed_after_the_first_turn_render_the_prompt_afresh():
    flags = ChatOptions(agent="Explore", fresh=True).flags()
    assert flags[-2:] == ["--system-prompt-snapshot", "off"]
    # Even with every option removed again: the recorded prompt still has them.
    assert ChatOptions(fresh=True).flags() == ["--system-prompt-snapshot", "off"]


@pytest.mark.parametrize("options", [
    ChatOptions(agent="-p"), ChatOptions(agent="two words"), ChatOptions(agent="a\nb"),
    ChatOptions(output_style="bad\nstyle"), ChatOptions(output_style=" padded"),
    ChatOptions(instructions="nul\0here"), ChatOptions(instructions="x" * 9000),
])
def test_values_that_could_not_come_from_the_dialog_are_refused(options):
    with pytest.raises(ValueError):
        options.flags()
    with pytest.raises(ValueError):
        build_resume_command(EXE, "aaaa-1111", "auto", {"aaaa-1111"}, set(), options=options)


def test_plugin_agents_and_styles_with_spaces_are_fine():
    assert co.is_safe_agent("my-plugin:reviewer")
    assert co.is_safe_style("My Style")
    assert ChatOptions(output_style="My Style").flags() == [
        "--settings", '{"outputStyle": "My Style"}']


def test_options_go_before_the_lists_that_run_to_the_next_flag():
    command = build_resume_command(EXE, "aaaa-1111", "auto", {"aaaa-1111"}, set(),
                                   allowed_tools=["Bash(git push:*)"],
                                   allowed_dirs=["C:\\Other" if sys.platform == "win32"
                                                 else "/tmp/other"],
                                   options=ChatOptions(agent="code-reviewer"))
    assert command.index("--agent") < command.index("--allowedTools") < command.index("--add-dir")
    assert command[-2:] == ["--resume", "aaaa-1111"]


def test_continue_here_carries_options_and_more_folders():
    folder = "C:\\Other" if sys.platform == "win32" else "/tmp/other"
    command = build_fork_command(EXE, "src-1", "new-1", "Copy", "auto",
                                 allowed_dirs=[folder],
                                 options=ChatOptions(instructions="Be brief."))
    assert "--append-system-prompt=Be brief." in command
    assert command[command.index("--add-dir") + 1] == folder
    start = command.index("--resume")
    assert command[start:start + 5] == ["--resume", "src-1", "--fork-session", "--session-id",
                                        "new-1"]


def test_summaries_and_heading():
    options = ChatOptions("code-reviewer", "x", "Concise")
    assert options.summary() == "agent code-reviewer, instructions, output style Concise"
    assert options.heading_part() == "as code-reviewer"
    assert ChatOptions(agent="a" * 40).heading_part() == ""
    assert ChatOptions().summary() == "none"


def test_what_a_change_says():
    change = co.OptionsChange(ChatOptions(), ChatOptions("Explore", "x", ""),
                              added_dirs=["C:\\A"])
    assert change.options_changed
    assert change.said("Build") == ("Build: agent Explore, new instructions, 1 more folder, "
                                    "from its next message.")
    removed = co.OptionsChange(ChatOptions("Explore", "x", "Concise"), ChatOptions())
    assert removed.said("Build") == ("Build: no agent, no instructions, your output style "
                                     "setting, from its next message.")


# -- what Claude Code offers ----------------------------------------------------------------


def test_choices_from_initialize():
    choices = choices_from_init({
        "agents": [{"name": "code-reviewer", "description": "Reviews.", "model": "opus"},
                   {"name": "-bad"}, {"name": "code-reviewer"}, "junk", {"description": "x"},
                   {"name": "Explore", "description": 5}],
        "available_output_styles": ["default", "Concise", "bad\nname", 3, "Concise"],
        "output_style": "Proactive"})
    assert choices.agent_names() == ["code-reviewer", "Explore"]
    assert choices.agents[0].model == "opus" and choices.agents[1].description == ""
    assert choices.styles == ("default", "Concise")
    assert choices.current_style == "Proactive"
    assert choices_from_init({"commands": []}) is None  # an older Claude Code
    assert choices_from_init(None) is None


def test_agent_label_is_cut_short():
    agent = co.Agent("code-reviewer", "Independent review " * 20)
    assert agent.label().startswith("code-reviewer: Independent review")
    assert len(agent.label()) <= 90 and agent.label().endswith("…")


def test_initialize_answer_kept_and_choices_fetched_without_a_turn(tmp_path):
    from test_claude_cli import FakeProcess, ev
    body = {"commands": [{"name": "mine"}],
            "agents": [{"name": "Explore", "description": "Search"}],
            "available_output_styles": ["default", "Learning"], "output_style": "default"}
    answer = ev(type="control_response", response={
        "subtype": "success", "request_id": cli.INIT_REQUEST_ID, "response": body})
    process = FakeProcess([answer])
    choices = cli.fetch_chat_choices(EXE, str(tmp_path), popen=lambda c, **k: process)
    assert choices.agent_names() == ["Explore"] and choices.styles == ("default", "Learning")
    sent = [json.loads(line) for line in process.written.decode("utf-8").splitlines()]
    assert [m["type"] for m in sent] == ["control_request"]  # no message: no turn
    parser = cli.StreamParser()
    parser.feed(answer)
    assert parser.init_body == body
    assert cli.fetch_chat_choices(EXE, str(tmp_path / "gone"),
                                  popen=lambda *a, **k: pytest.fail("must not start")) is None


# -- kept with the chat ---------------------------------------------------------------------


def test_options_round_trip_through_the_store(tmp_path):
    store = OwnSessionStore(tmp_path / "sessions.json")
    store.add(OwnSession("aaaa-1111", "T", str(tmp_path), agent="code-reviewer",
                         instructions="Line one\nLine two", output_style="Concise"))
    store.update("aaaa-1111", prompt_fresh=True)
    again = OwnSessionStore(tmp_path / "sessions.json").get("aaaa-1111")
    assert again.chat_options() == ChatOptions("code-reviewer", "Line one\nLine two",
                                               "Concise", fresh=True)


def test_an_older_file_without_the_fields_loads_with_none(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"version": 1, "sessions": [
        {"cli_session_id": "aaaa-1111", "title": "Old", "cwd": str(tmp_path)}]}),
        encoding="utf-8")
    own = OwnSessionStore(path).get("aaaa-1111")
    assert own.chat_options() == ChatOptions() and not own.prompt_fresh


def test_hand_edited_values_that_cant_be_passed_are_dropped(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"version": 1, "sessions": [
        {"cli_session_id": "aaaa-1111", "title": "Edited", "cwd": str(tmp_path),
         "agent": "--bare", "output_style": "a\nb", "instructions": "  keep\r\nme  ",
         "prompt_fresh": "yes"}]}), encoding="utf-8")
    own = OwnSessionStore(path).get("aaaa-1111")
    assert (own.agent, own.output_style, own.instructions, own.prompt_fresh) == \
        ("", "", "keep\nme", False)


def test_a_release_that_doesnt_know_the_fields_still_reads_the_chat(tmp_path):
    import legacy_016_own_store as legacy
    store = OwnSessionStore(tmp_path / "sessions.json")
    store.add(OwnSession("aaaa-1111", "T", str(tmp_path), model="opus",
                         agent="code-reviewer", instructions="x"))
    old = legacy.OwnSessionStore(tmp_path / "sessions.json").get("aaaa-1111")
    assert old is not None and old.model == "opus"


def test_another_providers_record_never_carries_claude_options(tmp_path):
    from thechatplace.own_store import _session_from_dict
    record = _session_from_dict({"cli_session_id": "c-1", "title": "C", "cwd": str(tmp_path),
                                 "provider": "codex", "agent": "Explore",
                                 "instructions": "x", "output_style": "Concise",
                                 "prompt_fresh": True})
    assert record.chat_options() == ChatOptions()


# -- Ask Aside: the command -----------------------------------------------------------------


def test_aside_is_a_fork_that_is_never_saved_and_can_only_read():
    command = build_aside_command(EXE, "dddd-2222", "haiku")
    assert command[-4:] == ["--resume", "dddd-2222", "--fork-session",
                            "--no-session-persistence"]
    assert command[command.index("--tools") + 1] == ASIDE_TOOLS == "Read,Grep,Glob"
    assert "--strict-mcp-config" in command and "--mcp-config" not in command
    assert command[command.index("--permission-prompts") + 1] == "none"
    assert command[command.index("--model") + 1] == "haiku"
    assert "--include-partial-messages" in command
    assert "--bare" not in command and "--session-id" not in command
    assert "--model" not in build_aside_command(EXE, "dddd-2222")


def test_aside_refusals():
    with pytest.raises(ResumeRefused):
        build_aside_command(EXE, "local_abc")
    with pytest.raises(ResumeRefused):
        build_aside_command(EXE, "../escape")
    with pytest.raises(ResumeRefused) as refused:
        build_aside_command(EXE, "cw-1", cowork_ids={"cw-1"})
    assert str(refused.value) == COWORK_REFUSED
    with pytest.raises(ValueError):
        build_aside_command(EXE, "dddd-2222", "sonnet[1m]")


def test_aside_rows_and_full_text():
    item = AsideItem("Which   file?")
    assert item.row() == "Q: Which file? A: asking…"
    item.answer, item.asking = "main.py\nonly", False
    assert item.row() == "Q: Which file? A: main.py only"
    assert item.full() == "Question: Which   file?\n\nAnswer:\nmain.py\nonly"
    item.failed, item.answer = True, "Stopped."
    assert item.row().endswith("A: not answered: Stopped.")


# -- Ask Aside: a fake Claude Code ----------------------------------------------------------


def run_aside(tmp_path, mode, cancel_after_start=False):
    log = tmp_path / "fake.log"
    env = child_environment({**child_environment(), "FAKE_CLAUDE_LOG": str(log),
                             "FAKE_CLAUDE_MODE": mode})
    events, done = [], threading.Event()

    def on_event(event):
        events.append(event)
        if event.kind in ("done", "failed"):
            done.set()
    runner = AsideRunner([sys.executable, str(FAKE_CLAUDE), "--fork-session"], str(tmp_path),
                         "What's the code word?", on_event, env=env)
    runner.start()
    if cancel_after_start:
        from test_claude_cli import wait_for_pid_file
        wait_for_pid_file(tmp_path / "grandchild.pid")
        runner.cancel()
    assert done.wait(30)
    runner.join(10)
    return events, (json.loads(log.read_text(encoding="utf-8")) if log.exists() else None)


def test_aside_streams_then_answers(tmp_path):
    events, seen = run_aside(tmp_path, "aside")
    # initialize first, the question once its answer came.
    assert seen["received"] == ["control_request", "user"]
    assert seen["prompt"] == "What's the code word?"
    assert [e.text for e in events if e.kind == "text"] == ["The code word ",
                                                           "The code word is ZEPHYR."]
    assert events[-1].kind == "done" and events[-1].text == "The code word is ZEPHYR."


def test_aside_says_plainly_when_there_is_no_conversation(tmp_path):
    events, _seen = run_aside(tmp_path, "missing")
    assert events[-1].kind == "failed"
    assert "couldn't find this chat's conversation" in events[-1].text


def test_stopping_an_aside_ends_its_process_tree(tmp_path):
    from thechatplace import platform_paths
    import time
    events, _seen = run_aside(tmp_path, "hang", cancel_after_start=True)
    assert events[-1].kind == "failed" and events[-1].text == "Stopped."
    grandchild = int((tmp_path / "grandchild.pid").read_text())
    deadline = time.time() + 5
    while platform_paths.pid_alive(grandchild) and time.time() < deadline:
        time.sleep(0.1)
    assert not platform_paths.pid_alive(grandchild)


def test_aside_in_a_missing_folder_fails_without_starting(tmp_path):
    events = []
    runner = AsideRunner([EXE], str(tmp_path / "gone"), "q", events.append,
                         popen=lambda *a, **k: pytest.fail("must not start"))
    runner.start()
    runner.join(10)
    assert events[-1].kind == "failed" and "doesn't exist" in events[-1].text


def test_aside_stops_before_asking_if_fable_would_answer(tmp_path):
    from test_claude_cli import FakeProcess, ev
    answer = ev(type="control_response", response={
        "subtype": "success", "request_id": cli.INIT_REQUEST_ID,
        "response": {"models": [{"value": "default", "resolvedModel": "claude-fable-5"}]}})
    process = FakeProcess([answer])
    events = []
    runner = AsideRunner([EXE, "--resume", "x"], str(tmp_path), "q", events.append,
                         popen=lambda c, **k: process)
    runner.start()
    runner.join(10)
    assert events[-1].kind == "failed" and "Fable" in events[-1].text
    assert "didn't ask your question" in events[-1].text
    sent = [json.loads(line) for line in (process.written or process.stdin.getvalue())
            .decode("utf-8").splitlines()]
    assert [m["type"] for m in sent] == ["control_request"]  # the question never went


# -- which chats have them ------------------------------------------------------------------


def test_capabilities_offer_aside_and_options_only_where_they_work():
    from thechatplace.providers.base import SessionCapabilities
    from thechatplace.providers.claude import ClaudeConversations
    from thechatplace.sessions import DESKTOP, KEPT, OWN, TERMINAL, SessionInfo
    claude = ClaudeConversations()

    def caps(source, **extra):
        return claude.capabilities(SessionInfo(source=source, key=f"{source}:x", title="T",
                                               cwd="", cli_session_id="x", **extra))
    assert caps(OWN).aside and caps(OWN).chat_options
    assert caps(DESKTOP).aside and not caps(DESKTOP).chat_options
    assert caps(TERMINAL).aside and not caps(TERMINAL).chat_options
    assert not caps(DESKTOP, cowork=True).aside
    assert not caps(KEPT).aside and not caps(KEPT).chat_options
    # Other providers get neither unless they say so.
    assert not SessionCapabilities().aside and not SessionCapabilities().chat_options


def test_properties_list_the_chat_options(tmp_path):
    from thechatplace import properties
    own = OwnSession("aaaa-1111", "T", str(tmp_path), agent="Explore",
                     instructions="Line one\nLine two", output_style="Concise")
    lines = properties.session_properties(own.to_info(), None, "", 0, own)
    assert "Agent: Explore" in lines and "Output style: Concise" in lines
    assert "Instructions for this chat: Line one Line two" in lines
    plain = properties.session_properties(own.to_info(), None, "", 0,
                                          OwnSession("aaaa-1111", "T", str(tmp_path)))
    assert {"Agent: none", "Output style: your setting",
            "Instructions for this chat: none"} <= set(plain)
