"""Send Reply to Another Chat (#248): what's sent, and where it may go."""
import pytest

from thechatplace import relay
from thechatplace.sessions import DESKTOP, OWN, TERMINAL, SessionInfo
from thechatplace.transcript import (ASSISTANT, ERROR, PLAN, QUESTION, USER, ChatMessage)


def _chat(key, title, provider="claude", source=OWN, activity=0, **extra):
    return SessionInfo(key=key, title=title, cwd="C:\\G\\Repo", source=source,
                       cli_session_id=key.split(":")[-1], provider=provider,
                       last_activity_ms=activity, **extra)


def test_the_selected_reply_is_sent_otherwise_the_latest():
    first = ChatMessage(ASSISTANT, "First answer", key="a1")
    plan = ChatMessage(PLAN, "The plan", key="p1")
    mine = ChatMessage(USER, "Thanks", key="u1")
    latest = ChatMessage(ASSISTANT, "Latest answer", key="a2", assistant_name="Codex")
    messages = [first, plan, mine, latest]
    assert relay.reply_to_send(messages, first) is first
    assert relay.reply_to_send(messages, plan) is plan
    assert relay.reply_to_send(messages, mine) is latest  # yours isn't a reply
    assert relay.reply_to_send(messages, None) is latest


def test_errors_questions_and_empty_replies_are_not_sent():
    error = ChatMessage(ERROR, "API error", key="e")
    question = ChatMessage(QUESTION, "Which branch?", key="q")
    blank = ChatMessage(ASSISTANT, "   ", key="b")
    assert relay.reply_to_send([error, question, blank], error) is None
    answer = ChatMessage(ASSISTANT, "An answer", key="a")
    assert relay.reply_to_send([answer, error, question, blank], blank) is answer


def test_the_message_says_which_agent_wrote_it_and_that_it_isnt_the_user():
    reply = ChatMessage(ASSISTANT, "\n  Found two bugs.\n\n", assistant_name="Codex")
    text = relay.message_text(reply, "Review  #247\n")
    first, rest = text.split("\n\n", 1)
    assert first == ('From Codex, another AI agent, forwarded by the user from their chat '
                     '"Review #247":')
    assert rest == "Found two bugs."
    assert relay.label("Claude", "").endswith('their chat "Untitled chat":')


def test_only_the_chat_places_own_sendable_chats_are_targets():
    source = _chat("own:src", "Builder")
    claude = _chat("own:c", "Reviewer", activity=10)
    codex = _chat("codex:x", "Codex review", provider="codex", activity=30)
    unsendable = _chat("codex:y", "Codex read only", provider="codex", activity=40)
    desktop = _chat("d", "Desktop", source=DESKTOP, activity=50)
    terminal = _chat("t", "Terminal", source=TERMINAL, activity=60)
    hidden = _chat("own:h", "Hidden", activity=70, hidden=True)
    archived = _chat("own:a", "Archived", activity=80, archived=True)
    sessions = [source, claude, codex, unsendable, desktop, terminal, hidden, archived]
    found = relay.targets(sessions, source.key, lambda s: s.key != unsendable.key)
    assert found == [codex, claude]  # most recently active first


def test_the_chat_that_sent_here_comes_first():
    sessions = [_chat("own:a", "A", activity=10), _chat("own:b", "B", activity=99),
                _chat("own:here", "Here")]
    found = relay.targets(sessions, "own:here", lambda s: True, partner_key="own:a")
    assert [s.title for s in found] == ["A", "B"]


def test_a_target_says_its_title_provider_folder_and_status():
    assert relay.describe(_chat("codex:x", "Codex review", provider="codex")) == \
        "Codex review, Codex, Repo, idle"


def test_codex_length_limit_matches_what_codex_refuses():
    from thechatplace.own_store import OwnSession
    from thechatplace.providers.codex_runtime import CodexChatRuntime

    limit = relay.MAX_LENGTH["codex"]
    assert relay.too_long("codex", "x" * limit) is None
    assert relay.too_long("claude", "x" * (limit * 2)) is None
    problem = relay.too_long("codex", "x" * (limit + 1))
    assert "32,000" in problem and "32,001" in problem
    record = OwnSession("n", "T", "C:\\G", provider="codex")
    with pytest.raises(ValueError):
        CodexChatRuntime.send(None, record, "x" * (limit + 1))


# -- a new chat, or an agent conversation (#378) --------------------------------------


def test_a_new_chat_is_offered_and_titled_by_where_the_reply_came_from():
    assert relay.new_target_label("codex") == "New Codex chat"
    assert relay.new_chat_title("  Build   it ") == "Forwarded from Build it"
    assert relay.new_chat_title("") == "Forwarded from Untitled chat"


def test_the_label_comes_off_only_while_it_is_unchanged():
    text = relay.message_text(ChatMessage(ASSISTANT, "Two bugs.", key="a",
                                          assistant_name="Codex"), "Reviewer")
    assert relay.split_label(text, "Codex", "Reviewer") == "Two bugs."
    assert relay.split_label(text + "\n\nFix the first.", "Codex", "Reviewer") == \
        "Two bugs.\n\nFix the first."
    assert relay.split_label("Please fix the two bugs.", "Codex", "Reviewer") is None
    assert relay.split_label(text.replace("Codex", "Claude", 1), "Codex", "Reviewer") is None
    assert relay.split_label(relay.label("Codex", "Reviewer"), "Codex", "Reviewer") is None


@pytest.mark.parametrize("source, kwargs, expected", [
    (_chat("c1", "Builder"), {}, None),
    (_chat("c1", "Builder", source=DESKTOP), {}, "Builder wasn't started in The Chat Place"),
    (_chat("c1", "Builder", provider="copilot"), {},
     "Builder wasn't started in The Chat Place"),
    (_chat("c1", "Builder"), {"can_edit": False}, "Builder wasn't started in The Chat Place"),
    (_chat("c1", "Builder"), {"in_conversation": True},
     "Builder is already in an agent conversation"),
    (_chat("c1", "Builder"), {"working": True},
     "Builder is still working; wait until it has finished"),
    (_chat("c1", "Builder"), {"blocked": "The file can't be read"}, "The file can't be read"),
])
def test_why_a_chat_cant_start_an_agent_conversation(source, kwargs, expected):
    options = {"can_edit": True, "in_conversation": False, "working": False, **kwargs}
    problem = relay.joint_problem(source, **options)
    assert (problem and problem.full) == expected
    if problem:
        assert "Builder" not in problem.short  # no title in the option's name


def test_why_a_target_cant_join_one():
    reviewer = _chat("codex:x", "Reviewer", "codex")
    assert relay.joint_target_problem(reviewer, False) is None
    assert relay.joint_target_problem(reviewer, True) == relay.Problem(
        "that chat is already in one", "Reviewer is already in an agent conversation")
    # Working, its turn would end inside the conversation as if it answered.
    assert relay.joint_target_problem(reviewer, False, working=True) == relay.Problem(
        "that chat is still working", "Reviewer is still working; wait until it has finished")
    assert relay.joint_target_problem(_chat("copilot:x", "Pilot", "copilot"), False).full == \
        "agent conversations are for Claude and Codex chats"


@pytest.mark.parametrize("sent, expected", [
    ("LABEL\n\nTwo bugs.", ("Two bugs.", "")),
    ("LABEL\n\nTwo bugs.\n\nFix the first.", ("Two bugs.", "Fix the first.")),
    ("LABEL\n\nTwo real bugs.", ("Two real bugs.", "")),   # the reply edited: still the reply
    ("Please fix the two bugs.", (None, "Please fix the two bugs.")),  # the label gone: yours
])
def test_what_is_the_reply_and_what_is_yours(sent, expected):
    label = relay.label("Codex", "Reviewer")
    assert relay.joint_message(sent.replace("LABEL", label), "Codex", "Reviewer",
                               "Two bugs.") == expected
