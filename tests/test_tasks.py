"""Claude's task list (#231): read from the transcript, and said in words.

Every record is synthetic (tests/records.py); the shapes are Claude Code
2.1.296's TaskCreate, TaskUpdate and TodoWrite calls.
"""
from thechatplace import lists, tasklist
from thechatplace.claude_cli import TASK_LIST_VAR, TurnRunner
from thechatplace.transcript import TranscriptParser, parse_lines

from records import (assistant_block, lines, task_create, task_update, tool_result,
                     tool_use_block, user_text)


def _three_tasks():
    return [user_text("Fix the flaky test"),
            *task_create(1, "Find the cause", "toolu_c1", active_form="Finding the cause"),
            *task_create(2, "Write the fix", "toolu_c2", description="Change the retry loop",
                         active_form="Writing the fix"),
            *task_create(3, "Run the tests", "toolu_c3", active_form="Running the tests")]


def _call(name, tool_input, tool_id, content, **result):
    return [assistant_block(tool_use_block(name, tool_input, tool_id), message_id=f"msg_{tool_id}"),
            tool_result(tool_id, content, **result)]


def test_task_create_and_update_build_the_list_in_claudes_order():
    tr = parse_lines(lines(*_three_tasks(),
                           *task_update(1, "toolu_u1", status="in_progress"),
                           *task_update(1, "toolu_u2", status="completed"),
                           *task_update(2, "toolu_u3", status="in_progress")))
    assert [(t.task_id, t.subject, t.status) for t in tr.tasks] == [
        ("1", "Find the cause", "completed"),
        ("2", "Write the fix", "in_progress"),
        ("3", "Run the tests", "pending")]
    assert tr.tasks[1].description == "Change the retry loop"
    assert tr.tasks[1].active_form == "Writing the fix"
    assert tasklist.progress(tr.tasks) == "Task 2 of 3: Writing the fix. 1 done."


def test_a_failed_call_changes_nothing():
    tr = parse_lines(lines(
        *_three_tasks(),
        *_call("TaskUpdate", {"taskId": "1", "status": "completed"}, "toolu_bad",
               "<tool_use_error>No such task</tool_use_error>", is_error=True),
        *_call("TaskCreate", {"tasks": "[...]"}, "toolu_bad2",
               "<tool_use_error>InputValidationError</tool_use_error>", is_error=True),
        *_call("TaskUpdate", {"taskId": "2", "status": "completed"}, "toolu_f",
               "Could not update", toolUseResult={"success": False, "taskId": "2"})))
    assert [t.status for t in tr.tasks] == ["pending"] * 3
    assert len(tr.tasks) == 3


def test_deleted_unknown_and_odd_updates():
    tr = parse_lines(lines(*_three_tasks(),
                           *task_update(3, "toolu_d", status="deleted"),
                           *task_update(2, "toolu_x", status="paused"),  # unknown: ignored
                           *task_update(9, "toolu_y", status="completed"),  # never created
                           *task_update(1, "toolu_s", subject="Find the real cause")))
    assert [(t.subject, t.status) for t in tr.tasks] == [("Find the real cause", "pending"),
                                                         ("Write the fix", "pending")]


def test_an_id_only_in_the_result_text_and_odd_inputs_dont_crash():
    tr = parse_lines(lines(
        *_call("TaskCreate", {"subject": "Plan it"}, "toolu_t",
               "Task #7 created successfully: Plan it"),
        *_call("TaskCreate", {"subject": ["not", "text"]}, "toolu_o",
               "Task #8 created successfully", toolUseResult={"task": {"id": 8}}),
        *_call("TaskUpdate", {"taskId": None, "status": 3}, "toolu_p", "Updated",
               toolUseResult="a string, not a dict"),
        *_call("TodoWrite", {"todos": "not a list"}, "toolu_q", "ok")))
    assert [(t.task_id, t.subject) for t in tr.tasks] == [("7", "Plan it")]
    assert tr.unreadable_lines == 0


def test_todo_write_replaces_the_whole_list():
    tr = parse_lines(lines(
        user_text("Tidy up"),
        *_call("TodoWrite", {"todos": [
            {"content": "Sort imports", "status": "in_progress", "activeForm": "Sorting imports"},
            {"content": "Remove dead code", "status": "pending", "activeForm": "Removing dead code"},
            "not a todo", {"status": "pending"}]}, "toolu_w1", "Todos have been modified"),
        user_text("Carry on"),
        *_call("TodoWrite", {"todos": [
            {"content": "Remove dead code", "status": "in_progress"},
            {"content": "Sort imports", "status": "completed"}]}, "toolu_w2",
            "Todos have been modified")))
    assert [(t.subject, t.status) for t in tr.tasks] == [("Remove dead code", "in_progress"),
                                                         ("Sort imports", "completed")]
    # Kept its turn, and the turn it changed in is the second.
    assert tr.tasks[1].turn == 1 and tr.tasks[1].changed_turn == 2


def test_tasks_remember_the_turns_they_were_added_and_changed_in():
    tr = parse_lines(lines(*_three_tasks(), user_text("Go on"),
                           *task_update(2, "toolu_u", status="completed")))
    assert [(t.turn, t.changed_turn) for t in tr.tasks] == [(1, 1), (1, 2), (1, 1)]


def test_the_list_reads_the_same_fed_in_pieces():
    records = lines(*_three_tasks(), *task_update(1, "toolu_u", status="completed"))
    parser = TranscriptParser()
    for line in records:
        parser.feed([line])
    assert [(t.subject, t.status) for t in parser.transcript.tasks] == \
        [(t.subject, t.status) for t in parse_lines(records).tasks]


# -- in words -----------------------------------------------------------------


def test_progress_in_each_state():
    tr = parse_lines(lines(*_three_tasks()))
    assert tasklist.progress([]) == ""
    assert tasklist.progress(tr.tasks) == "0 of 3 tasks done. Next: Find the cause."
    tr.tasks[0].status = "completed"
    assert tasklist.progress(tr.tasks) == "1 of 3 tasks done. Next: Write the fix."
    tr.tasks[2].status = "in_progress"
    tr.tasks[2].active_form = ""  # no active form: the subject stands in
    assert tasklist.progress(tr.tasks) == "Task 3 of 3: Run the tests. 1 done."
    for task in tr.tasks:
        task.status = "completed"
    assert tasklist.progress(tr.tasks) == "All 3 tasks done."
    assert tasklist.progress(tr.tasks[:1]) == "All 1 task done."


def test_rows_say_the_name_first_and_detail_says_where_it_sits():
    tr = parse_lines(lines(*_three_tasks(), *task_update(2, "toolu_u", status="in_progress")))
    assert [tasklist.row(t) for t in tr.tasks] == [
        "Find the cause. To do", "Write the fix. In progress", "Run the tests. To do"]
    assert tasklist.detail(tr.tasks[1], 2, 3) == (
        "Write the fix\n\nIn progress. Task 2 of 3.\n\nNow: Writing the fix\n\nChange the retry loop")
    assert tasklist.detail(tr.tasks[0], 1, 3) == "Find the cause\n\nTo do. Task 1 of 3."


def test_what_is_said_when_tasks_are_done():
    tr = parse_lines(lines(*_three_tasks()))
    before = tasklist.statuses(tr.tasks)
    assert tasklist.done_text(tasklist.newly_done(before, tr.tasks), tr.tasks) is None
    tr.tasks[0].status = "completed"
    tr.tasks[1].status = "completed"
    finished = tasklist.newly_done(before, tr.tasks)
    assert tasklist.done_text(finished, tr.tasks) == "Done: Find the cause; Write the fix. 2 of 3."
    # Already done last time: not said again.
    assert tasklist.newly_done(tasklist.statuses(tr.tasks), tr.tasks) == []


# -- the Tasks tab ----------------------------------------------------------------


def test_the_tasks_tab_for_the_chat_and_for_one_turn():
    tr = parse_lines(lines(*_three_tasks(), user_text("Go on"),
                           *task_update(3, "toolu_u", status="in_progress")))

    def build(scope, selected=None):
        return lists.build(lists.TASKS, scope, tr.messages, selected, [], [], str, tasks=tr.tasks)

    chat = build(lists.SCOPE_CHAT)
    assert [i.row for i in chat] == ["Find the cause. To do", "Write the fix. To do",
                                     "Run the tests. In progress"]
    assert chat[2].copy_text == "Run the tests" and chat[2].detail_name == "Task 3 of 3"
    second_turn = next(m for m in tr.messages if m.text == "Go on")
    assert [i.row for i in build(lists.SCOPE_MESSAGE, second_turn)] == ["Run the tests. In progress"]
    assert len(build(lists.SCOPE_MESSAGE, tr.messages[0])) == 3
    assert lists.empty_text(lists.TASKS, lists.SCOPE_CHAT) == "No tasks in this chat."
    assert lists.empty_text(lists.TASKS, lists.SCOPE_MESSAGE) == "No tasks in this message's turn."
    assert lists.TITLES[lists.TASKS] == "Tasks"


# -- turning the list on for The Chat Place's own sessions -----------------------------


def test_the_turn_asks_for_the_task_list_only_when_the_setting_is_on():
    def env(**kwargs):
        return TurnRunner(["claude"], ".", "hi", lambda e: None, env={"PATH": "x"}, **kwargs)._env

    assert env(task_list=True)[TASK_LIST_VAR] == "1"
    assert TASK_LIST_VAR not in env(task_list=False)
    assert TASK_LIST_VAR not in env()
    # Off leaves a value the user set themselves alone.
    runner = TurnRunner(["claude"], ".", "hi", lambda e: None, env={TASK_LIST_VAR: "1"})
    assert runner._env[TASK_LIST_VAR] == "1"


def test_the_setting_is_on_unless_turned_off(tmp_path):
    from thechatplace.speech import SpeechSettings
    path = tmp_path / "speech.json"
    assert SpeechSettings().task_list is True
    for saved, expected in (("{}", True), ('{"task_list": false}', False),
                            ('{"task_list": "no"}', True), ('{"task_list": true}', True)):
        path.write_text(saved, encoding="utf-8")
        assert SpeechSettings.load(path).task_list is expected, saved
