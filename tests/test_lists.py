"""View, Lists (#279): what each tab lists, for this message or the whole
chat. Synthetic records only; nothing here needs a window."""
from thechatplace import lists
from thechatplace import transcript as t
from thechatplace.transcript import parse_lines

from records import assistant_block, lines, text_block, tool_result, tool_use_block, user_text

QUESTION = {"questions": [{
    "question": "Which colour?", "header": "Colour", "multiSelect": False,
    "options": [{"label": "Red", "description": "warm"}, {"label": "Blue", "description": ""}]}]}


def _chat():
    """Two turns: the first runs two commands (one fails) and asks a
    question; the second has code and a link."""
    return parse_lines(lines(
        user_text("Check the build"),
        assistant_block(tool_use_block("Bash", {"command": "make test",
                                                "description": "Run the tests"}, "b1"), "m1"),
        tool_result("b1", "\x1b[32m12 passed\x1b[0m"),
        assistant_block(tool_use_block("Bash", {"command": "make lint"}, "b2"), "m2"),
        tool_result("b2", "lint: 3 problems", is_error=True),
        assistant_block(tool_use_block("AskUserQuestion", QUESTION, "q1"), "m3"),
        tool_result("q1", 'Your questions have been answered: "Which colour?"="Red"',
                    toolUseResult={"questions": [], "answers": {"Which colour?": "Red"}}),
        assistant_block(text_block("All checked."), "m4"),
        user_text("Show me the code"),
        assistant_block(text_block("Here:\n```python\nprint(1)\n```\nSee "
                                   "[the docs](https://example.com/docs)."), "m5"),
    ))


def _build(tr, category, scope, selected=None):
    messages = tr.visible()
    selected = selected if selected is not None else messages[-1]
    return lists.build(category, scope, messages, selected, tr.edits, tr.commands,
                       lambda f: f.path)


def test_commands_are_recorded_with_their_output_and_turn():
    tr = _chat()
    first, second = tr.commands
    assert (first.command, first.description, first.output) == ("make test", "Run the tests",
                                                                "12 passed")  # colours gone
    assert first.finished and not first.failed and first.turn == 1
    assert (second.output, second.failed, second.turn) == ("lint: 3 problems", True, 1)


def test_messages_know_their_turn():
    tr = _chat()
    turns = {m.text.split("\n")[0]: m.turn for m in tr.visible()}
    assert turns["Check the build"] == 1 and turns["All checked."] == 1
    assert turns["Show me the code"] == 2 and turns["Here:"] == 2


def test_commands_newest_first_with_what_they_were_for():
    items = _build(_chat(), lists.COMMANDS, lists.SCOPE_CHAT)
    assert [i.row for i in items] == ["make lint. failed", "Run the tests. done"]
    assert items[0].detail == "Command:\nmake lint\n\nFailed. Output:\nlint: 3 problems"
    assert items[1].detail.startswith("Run the tests\n\nCommand:\nmake test\n\nOutput:\n12 passed")
    assert (items[0].copy_text, items[0].copy_what) == ("make lint", "the command")


def test_this_message_means_its_turn_for_commands_and_decisions():
    tr = _chat()
    latest = tr.visible()[-1]  # turn 2: no commands, no questions
    assert _build(tr, lists.COMMANDS, lists.SCOPE_MESSAGE, latest) == []
    assert _build(tr, lists.DECISIONS, lists.SCOPE_MESSAGE, latest) == []
    first_turn = tr.visible()[0]
    assert len(_build(tr, lists.COMMANDS, lists.SCOPE_MESSAGE, first_turn)) == 2
    assert len(_build(tr, lists.DECISIONS, lists.SCOPE_MESSAGE, first_turn)) == 1


def test_decisions_pair_a_question_with_its_answer():
    (item,) = _build(_chat(), lists.DECISIONS, lists.SCOPE_CHAT)
    assert item.row == "Which colour? You answered: Red"
    assert item.detail.endswith("Your answer:\nWhich colour? — Red")
    assert item.copy_what == "your answer"


def test_an_unanswered_question_says_so():
    tr = parse_lines(lines(user_text("Go"),
                           assistant_block(tool_use_block("AskUserQuestion", QUESTION, "q"), "m")))
    (item,) = _build(tr, lists.DECISIONS, lists.SCOPE_CHAT)
    assert item.row == "Which colour? Not answered"
    assert item.copy_what == "the question"


def test_code_and_links_for_the_message_or_the_chat():
    tr = _chat()
    (block,) = _build(tr, lists.CODE, lists.SCOPE_MESSAGE)
    assert block.row == "Python, 1 line: print(1)" and block.detail == "print(1)"
    (block,) = _build(tr, lists.CODE, lists.SCOPE_CHAT)
    assert block.row == "Python, 1 line: print(1). Claude, the latest message"
    (link,) = _build(tr, lists.LINKS, lists.SCOPE_MESSAGE)
    assert link.link.url == "https://example.com/docs" and link.copy_text == link.link.url
    # The first message has neither.
    first = tr.visible()[0]
    assert _build(tr, lists.CODE, lists.SCOPE_MESSAGE, first) == []
    assert _build(tr, lists.LINKS, lists.SCOPE_MESSAGE, first) == []


def test_a_message_scope_with_no_message_is_empty():
    tr = _chat()
    assert lists.build(lists.CODE, lists.SCOPE_MESSAGE, tr.visible(), None, [], [], str) == []


def test_empty_and_count_wording():
    assert lists.empty_text(lists.CODE, lists.SCOPE_MESSAGE) == "No code blocks in this message."
    assert lists.empty_text(lists.FILES, lists.SCOPE_MESSAGE) == "No files in this message's turn."
    assert lists.empty_text(lists.LINKS, lists.SCOPE_CHAT) == "No links in this chat."
    assert lists.count_text(lists.COMMANDS, 12, 12) == "Commands (12)"
    assert lists.count_text(lists.COMMANDS, 3, 12) == "Commands (3 of 12)"


def test_filtering_matches_every_word_in_the_row_or_full_text():
    item = lists.ListItem(row="Run the tests. done", detail="Command:\nmake test")
    assert item.matches("make tests") and item.matches("")
    assert not item.matches("lint")


def test_a_command_without_a_result_yet():
    tr = parse_lines(lines(user_text("Go"),
                           assistant_block(tool_use_block("Bash", {"command": "sleep 60"}, "s"), "m")))
    (item,) = _build(tr, lists.COMMANDS, lists.SCOPE_CHAT)
    assert item.row == "sleep 60. no result yet" and item.detail.endswith("No result yet.")


def test_other_tools_are_not_commands():
    tr = parse_lines(lines(user_text("Go"), assistant_block(
        tool_use_block("Read", {"file_path": "/x"}, "r"), "m"), tool_result("r", "text")))
    assert tr.commands == [] and [m.kind for m in tr.messages][-1] == t.TOOL_RESULT


def test_a_blank_command_is_not_listed():
    tr = parse_lines(lines(user_text("Go"),
                           assistant_block(tool_use_block("Bash", {"command": "  "}, "s"), "m")))
    assert tr.commands == []


def test_a_queued_selection_uses_its_own_text_and_the_latest_turn():
    tr = _chat()
    queued = t.ChatMessage(kind=t.QUEUED, text="```sh\nls\n```")  # not in the messages
    (block,) = lists.build(lists.CODE, lists.SCOPE_MESSAGE, tr.visible(), queued, [], [], str)
    assert block.detail == "ls"
    commands = lists.build(lists.COMMANDS, lists.SCOPE_MESSAGE, tr.visible(), queued, [],
                           tr.commands, str, turn=1)
    assert len(commands) == 2


def test_a_code_block_copy_says_which_block():
    (block,) = _build(_chat(), lists.CODE, lists.SCOPE_MESSAGE)
    assert block.copy_what == "Code block, Python, 1 line"
