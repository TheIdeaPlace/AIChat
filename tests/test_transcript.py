import json

from thechatplace import transcript as t
from thechatplace.transcript import (TranscriptParser, TranscriptReader, parse_lines,
                                     read_transcript)

from records import (assistant_block, lines, other, text_block, thinking_block,
                     tool_result, tool_use_block, user_blocks, user_text)


def kinds(tr, show_activity=False):
    return [m.kind for m in tr.visible(show_activity)]


def test_plain_conversation_string_and_block_content():
    tr = parse_lines(lines(
        user_text("Hello there"),
        assistant_block(text_block("Hi! How can I help?"), "msg_1"),
        user_blocks([{"type": "text", "text": "Second question"}]),
        assistant_block(text_block("Answer two"), "msg_2"),
    ))
    assert [m.list_line() for m in tr.visible()] == [
        "You: Hello there", "Claude: Hi! How can I help?",
        "You: Second question", "Claude: Answer two"]
    assert tr.unreadable_lines == 0


def test_split_assistant_records_with_one_message_id_merge():
    tr = parse_lines(lines(
        user_text("q"),
        assistant_block(thinking_block(), "msg_1"),
        assistant_block(text_block("Part one."), "msg_1"),
        assistant_block(text_block("Part two."), "msg_1"),
    ))
    assert kinds(tr) == ["user", "assistant"]
    assert tr.visible()[1].text == "Part one.\n\nPart two."


def test_tool_calls_and_results_are_activity_only():
    tr = parse_lines(lines(
        user_text("Run the tests"),
        assistant_block(text_block("Let me run them."), "msg_1"),
        assistant_block(tool_use_block("Bash", {"command": "pytest -q",
                                                "description": "Run tests"}, "toolu_9"),
                        "msg_1"),
        tool_result("toolu_9", "3 passed"),
        assistant_block(text_block("All three pass."), "msg_2"),
    ))
    assert kinds(tr) == ["user", "assistant", "assistant"]
    assert kinds(tr, True) == ["user", "assistant", "tool", "tool_result", "assistant"]
    tool = tr.visible(True)[2]
    assert tool.list_line() == "Tool: Bash: Run tests"
    assert tr.visible(True)[3].text == "Bash returned: 3 passed"


def test_text_after_a_tool_call_in_same_message_is_a_new_message():
    tr = parse_lines(lines(
        assistant_block(text_block("Before."), "msg_1"),
        assistant_block(tool_use_block("Read", {"file_path": "a.txt"}, "toolu_1"), "msg_1"),
        assistant_block(text_block("After."), "msg_1"),
    ))
    assert [m.text for m in tr.visible()] == ["Before.", "After."]


def test_question_card_and_answer_read_as_words():
    question = {"questions": [{
        "question": "Which colour?", "header": "Colour", "multiSelect": False,
        "options": [{"label": "Red", "description": "warm"},
                    {"label": "Blue", "description": ""}]}]}
    tr = parse_lines(lines(
        assistant_block(tool_use_block("AskUserQuestion", question, "toolu_q"), "msg_1"),
        tool_result("toolu_q", 'Your questions have been answered: "Which colour?"="Red"',
                    toolUseResult={"questions": [], "answers": {"Which colour?": "Red"}}),
    ))
    visible = tr.visible()
    assert [m.kind for m in visible] == [t.QUESTION, t.ANSWER]
    assert visible[0].text == "Which colour?\nOptions: Red (warm); Blue."
    assert visible[0].list_line() == "Claude asked: Which colour?"
    assert visible[1].list_line() == "You answered: Which colour? — Red"


def test_question_answer_falls_back_to_result_text():
    tr = parse_lines(lines(
        assistant_block(tool_use_block("AskUserQuestion", {"questions": "bad"}, "q"), "m"),
        tool_result("q", "Your questions have been answered: blue"),
    ))
    assert tr.visible()[0].text.startswith("Claude asked a question")
    assert tr.visible()[1].text == "blue"


def test_permission_denial_is_shown():
    tr = parse_lines(lines(
        assistant_block(tool_use_block("Write", {"file_path": "x.txt"}, "toolu_w"), "m"),
        tool_result("toolu_w", "Claude requested permissions to write to x.txt, but you "
                               "haven't granted it yet.", is_error=True,
                    toolDenialKind="user-rejected"),
    ))
    visible = tr.visible()
    assert [m.kind for m in visible] == [t.DENIED]
    assert visible[0].list_line().startswith("Permission denied: Write: Claude requested")


def test_tool_error_without_denial_is_activity():
    tr = parse_lines(lines(
        assistant_block(tool_use_block("Bash", {"command": "false"}, "toolu_b"), "m"),
        tool_result("toolu_b", "exit 1", is_error=True),
    ))
    assert kinds(tr) == []
    assert tr.visible(True)[1].text == "Bash failed: exit 1"


def test_plan_error_interrupt():
    tr = parse_lines(lines(
        assistant_block(tool_use_block("ExitPlanMode", {"plan": "1. Do it"}, "p"), "m"),
        _api_error(),
        user_blocks([{"type": "text", "text": "[Request interrupted by user]"}]),
    ))
    assert kinds(tr) == [t.PLAN, t.ERROR, t.INTERRUPTED]
    assert tr.visible()[0].text == "1. Do it"


def _api_error():
    record = assistant_block(text_block("API Error: overloaded"), "m_err")
    record["isApiErrorMessage"] = True
    return record


def test_harness_events_meta_and_compaction_are_activity():
    tr = parse_lines(lines(
        user_text("<task-notification>\n<status>completed</status>\n</task-notification>"),
        user_text("Skill instructions here", isMeta=True),
        user_text("Earlier we did X", isCompactSummary=True),
        user_text("Real question"),
    ))
    assert kinds(tr) == [t.USER]
    activity = tr.visible(True)
    assert activity[0].kind == t.EVENT
    assert activity[0].text.startswith("task notification: completed")
    assert activity[1].kind == t.CONTEXT
    assert activity[2].text.startswith("Summary of the earlier conversation")


def test_system_reminder_is_stripped_from_user_text():
    tr = parse_lines(lines(user_text(
        "Fix it<system-reminder>internal</system-reminder>")))
    assert tr.visible()[0].text == "Fix it"


def test_sidechain_and_unknown_records_are_skipped_silently():
    side = user_text("subagent prompt")
    side["isSidechain"] = True
    tr = parse_lines(lines(
        other("attachment", attachment={"type": "x"}),
        other("custom-title", customTitle="t"),
        other("some-future-record", data=[1, 2]),
        side,
        user_text("Visible"),
    ))
    assert kinds(tr) == [t.USER]
    assert tr.unreadable_lines == 0


def test_malformed_lines_are_counted_not_raised():
    good = json.dumps(user_text("ok"))
    tr = parse_lines([
        "{not json", good, "[1,2,3]", "",
        json.dumps({"type": "user", "message": "not a dict"}),
        json.dumps({"type": "assistant", "message": {"content": 42}}),
        json.dumps({"type": "user", "message": {"content": {"odd": True}}}),
    ])
    assert kinds(tr) == [t.USER]
    assert tr.unreadable_lines == 5


def test_images_and_odd_blocks_do_not_crash():
    tr = parse_lines(lines(
        user_blocks([{"type": "image", "source": {}}, "stray string", {"type": "text"}]),
        assistant_block({"type": "redacted_thinking"}, "m"),
        assistant_block("not a dict", "m"),
    ))
    assert tr.visible()[0].text == "(image attached)"
    assert tr.unreadable_lines == 0


def test_last_reply():
    tr = parse_lines(lines(
        user_text("q"), assistant_block(text_block("final answer"), "m"),
        user_text("<ci-monitor-event>x</ci-monitor-event>")))
    assert tr.last_reply().text == "final answer"


def test_long_first_line_is_trimmed_and_full_text_kept():
    long = "word " * 200
    tr = parse_lines(lines(assistant_block(text_block(long + "\nsecond line"), "m")))
    message = tr.visible()[0]
    assert len(message.first_line()) <= 300
    assert message.full_text().startswith("Claude:\n")
    assert message.full_text().endswith("second line")


def test_empty_message_reads_empty():
    msg = t.ChatMessage(kind=t.USER, text="   ")
    assert msg.list_line() == "You: (empty)"


# -- reading files ------------------------------------------------------------


def test_reader_is_incremental_and_waits_for_whole_lines(tmp_path):
    path = tmp_path / "s.jsonl"
    first = json.dumps(user_text("one")) + "\n"
    path.write_text(first, encoding="utf-8")
    reader = TranscriptReader(path)
    assert reader.refresh() is True
    assert kinds(reader.transcript) == [t.USER]
    assert reader.refresh() is False

    partial = json.dumps(assistant_block(text_block("two"), "m"))
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(partial[:20])  # a line still being written
    assert reader.refresh() is False
    assert reader.transcript.unreadable_lines == 0
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(partial[20:] + "\n")
    assert reader.refresh() is True
    assert kinds(reader.transcript) == [t.USER, t.ASSISTANT]


def test_reader_merges_split_reply_across_refreshes(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps(assistant_block(text_block("A"), "m1")) + "\n",
                    encoding="utf-8")
    reader = TranscriptReader(path)
    reader.refresh()
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(assistant_block(text_block("B"), "m1")) + "\n")
    assert reader.refresh() is True
    assert [m.text for m in reader.transcript.visible()] == ["A\n\nB"]


def test_reader_starts_over_when_file_shrinks(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(lines(user_text("a"), user_text("b"))) + "\n", encoding="utf-8")
    reader = TranscriptReader(path)
    reader.refresh()
    path.write_text(json.dumps(user_text("c")) + "\n", encoding="utf-8")
    reader.refresh()
    assert [m.text for m in reader.transcript.visible()] == ["c"]


def test_reader_missing_file_is_not_an_error(tmp_path):
    reader = TranscriptReader(tmp_path / "gone.jsonl")
    assert reader.refresh() is False
    assert reader.transcript.messages == []


def test_reader_never_writes(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps(user_text("a")) + "\n", encoding="utf-8")
    before = path.stat().st_mtime_ns, path.read_bytes()
    read_transcript(path)
    assert (path.stat().st_mtime_ns, path.read_bytes()) == before


def test_invalid_utf8_is_replaced_not_fatal(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_bytes(b'{"type":"user","message":{"content":"caf\xff"}}\n')
    tr = read_transcript(path)
    assert tr.visible()[0].text.startswith("caf")


def test_parser_state_survives_feeds():
    parser = TranscriptParser()
    parser.feed(lines(assistant_block(tool_use_block("AskUserQuestion", {
        "questions": [{"question": "Go?", "options": [{"label": "Yes"}]}]}, "q1"), "m")))
    parser.feed(lines(tool_result("q1", "Your questions have been answered: Yes")))
    assert [m.kind for m in parser.transcript.messages] == [t.QUESTION, t.ANSWER]


# -- messages from other sessions (#123) ----------------------------------------


def peer_attachment(name, body, delivery_id="d-1", **origin_extra):
    """A message from another session that arrived while Claude was working,
    shaped like Claude Code 2.1.286's (synthetic content)."""
    origin = {"kind": "peer", "from": "bridge:session_X", "name": name,
              "fromMode": "prompting", "body": body, **origin_extra}
    return other("attachment", attachment={
        "type": "queued_command", "commandMode": "prompt", "delivery_id": delivery_id,
        "prompt": f'<cross-session-message from="bridge:session_X" from-name="{name}">\n'
                  f"{body}\n</cross-session-message>",
        "origin": origin}, isMeta=True)


def test_peer_message_mid_turn_is_shown_from_its_session():
    tr = parse_lines(lines(
        user_text("Ask the Surface"),
        peer_attachment("Coordinating Agent", "Roll call: SURFACE-TEST."),
        assistant_block(text_block("It answered."), "msg_1"),
    ))
    assert [m.list_line() for m in tr.visible()] == [
        "You: Ask the Surface",
        "From Coordinating Agent: Roll call: SURFACE-TEST.",
        "Claude: It answered."]
    peer = tr.visible()[1]
    assert peer.kind == t.PEER and peer.sender == "Coordinating Agent"
    assert peer.key == "d-1" and not peer.is_activity
    assert peer.full_text() == "From Coordinating Agent:\nRoll call: SURFACE-TEST."
    # Arriving mid-turn, it isn't a turn of its own.
    assert tr.turns == 1


def test_peer_message_without_body_falls_back_to_the_prompt():
    record = peer_attachment("coordinate", "")
    record["attachment"]["origin"].pop("name")
    record["attachment"]["prompt"] = ('<cross-session-message from="bridge:session_Y" '
                                      'from-name="Mac test">\nPR is up\n</cross-session-message>')
    tr = parse_lines(lines(record))
    assert [m.list_line() for m in tr.visible()] == ["From Mac test: PR is up"]


def test_other_attachments_are_still_skipped():
    tr = parse_lines(lines(
        other("attachment", attachment={"type": "deferred_tools_record", "entries": []}),
        other("attachment", attachment={"type": "queued_command", "prompt": "typed later",
                                        "origin": {"kind": "human"}}),
        other("attachment", attachment="not a dict"),
    ))
    assert tr.visible(show_activity=True) == [] and tr.unreadable_lines == 0


def test_peer_message_that_starts_a_turn_is_not_shown_as_yours():
    tr = parse_lines(lines(
        user_text('<cross-session-message from="bridge:session_X" from-name="Hub &amp; co" '
                  'from-mode="prompting">\nPlease reply with your hostname.\n'
                  "</cross-session-message>"),
        assistant_block(text_block("TEST-HOST"), "msg_1"),
    ))
    assert [m.list_line() for m in tr.visible()] == [
        "From Hub & co: Please reply with your hostname.", "Claude: TEST-HOST"]
    # It started a turn, so changes Claude makes for it belong to that turn.
    assert tr.turns == 1


def test_older_peer_wrapper_with_name_and_encoded_body():
    tr = parse_lines(lines(user_text(
        '<cross-session-message from="local_1" name="Review session" encoded="1">\n'
        "a &lt; b &amp;&amp; c\n</cross-session-message>")))
    assert [m.list_line() for m in tr.visible()] == ["From Review session: a < b && c"]


def test_peer_without_a_name_is_from_another_session_not_an_address():
    tr = parse_lines(lines(user_text(
        '<cross-session-message from="bridge:session_Z">hi</cross-session-message>')))
    assert tr.visible()[0].list_line() == "From another session: hi"
    record = peer_attachment("", "hello")
    record["attachment"]["origin"]["from"] = r"uds:\\.\pipe\cc-msg-1"
    record["attachment"]["prompt"] = '<cross-session-message from="x">hello</cross-session-message>'
    tr = parse_lines(lines(record))
    assert tr.visible()[0].list_line() == "From another session: hello"


def test_subagent_handback_is_not_a_message_from_another_session():
    """Same attachment shape as a peer message, but it's Claude's own helper reporting."""
    handback = other("attachment", attachment={
        "type": "queued_command", "commandMode": "prompt", "delivery_id": "d-9",
        "prompt": '<agent-message from="a6005b96fca1385c5">\nReport text\n</agent-message>',
        "origin": {"kind": "peer", "from": "a6005b96fca1385c5", "name": "general-purpose",
                   "body": "Report text", "handback": True, "senderTaskId": "a6005b96"}},
        isMeta=True)
    no_flags = json.loads(json.dumps(handback))
    del no_flags["attachment"]["origin"]["handback"]
    del no_flags["attachment"]["origin"]["senderTaskId"]
    tr = parse_lines(lines(handback, no_flags))
    assert tr.visible(show_activity=True) == [] and tr.unreadable_lines == 0


def test_empty_peer_message_adds_nothing():
    tr = parse_lines(lines(
        user_text('<cross-session-message from="b"></cross-session-message>'),
        user_text('<cross-session-message from="b"><system-reminder>x</system-reminder>'
                  "</cross-session-message>"),
        peer_attachment("Mac", "   ")))
    assert tr.visible(show_activity=True) == [] and tr.turns == 0


def test_typed_text_starting_with_the_tag_is_still_yours():
    tr = parse_lines(lines(user_text("<cross-session-message>what is this tag?")))
    assert [m.kind for m in tr.visible(show_activity=True)] != [t.PEER]


def test_manual_compaction_and_command_output_are_visible_without_activity():
    from records import compact_boundary, command_stdout
    boundary = compact_boundary()
    boundary["timestamp"] = "2026-01-01T12:01:00Z"
    tr = parse_lines(lines(boundary,
        user_text('Synthetic summary', isCompactSummary=True, isVisibleInTranscriptOnly=True),
        user_text('<local-command-caveat>synthetic</local-command-caveat>', isMeta=True),
        user_text('<command-name>/compact</command-name> <command-message>compact</command-message>'),
        command_stdout('Compacted ')))
    assert kinds(tr) == [t.COMPACTION, t.COMMAND_OUTPUT]
    assert tr.visible()[0].text == 'Conversation compacted (you asked), from 968,000 to 9,000 tokens.'
    assert tr.visible()[1].text == 'Compacted'
    assert tr.visible()[1].label == 'Claude Code'
    assert tr.context_tokens == 9_000 and tr.compactions == 1
    assert tr.last_reply().text == 'Compacted'


def test_command_output_preserves_plain_content_and_does_not_count_a_user_turn():
    from records import command_stdout
    tr = parse_lines(lines(command_stdout('Context: <5%>'), command_stdout('')))
    assert [(m.kind, m.text) for m in tr.visible()] == [(t.COMMAND_OUTPUT, 'Context: <5%>')]
    assert tr.turns == 0


def test_same_size_and_growing_rewrites_replace_the_incremental_history(tmp_path):
    import os
    from records import compact_boundary, command_stdout
    path = tmp_path / 'synthetic.jsonl'

    def write(text, second):
        # Real rewrites happen at distinct times. Windows file times are
        # coarse, so back-to-back test writes can share one, and an
        # unchanged stat rightly skips the file (#275). Give each write its
        # own time, as Claude Code's writes have.
        path.write_text(text)
        os.utime(path, ns=(second * 1_000_000_000, second * 1_000_000_000))

    original = lines(user_text('First'))[0] + '\n'
    replacement = lines(user_text('Other'))[0] + '\n'
    assert len(original) == len(replacement)
    write(original, 1_800_000_000)
    reader = TranscriptReader(path)
    assert reader.refresh()
    write(replacement, 1_800_000_001)
    assert reader.refresh() and reader.transcript.visible()[0].text == 'Other'
    rewritten = '\n'.join(lines(compact_boundary(), command_stdout('Compacted'))) + '\n'
    assert len(rewritten) > len(replacement)
    write(rewritten, 1_800_000_002)
    assert reader.refresh()
    assert kinds(reader.transcript) == [t.COMPACTION, t.COMMAND_OUTPUT]
    assert reader.transcript.context_tokens == 9_000
    assert not reader.refresh()
    write('', 1_800_000_003)
    assert reader.refresh() and not reader.transcript.messages


def test_malformed_compaction_metadata_does_not_hide_the_boundary():
    record = {'type': 'system', 'subtype': 'compact_boundary',
              'compactMetadata': {'trigger': [], 'postTokens': True}}
    tr = parse_lines(lines(record))
    assert tr.visible()[0].kind == t.COMPACTION
    assert tr.context_tokens == 0 and not tr.unreadable_lines


def test_command_output_can_be_the_last_message_without_an_assistant_reply(tmp_path):
    from records import command_stdout
    path = tmp_path / 'synthetic.jsonl'
    path.write_text('\n'.join(lines(user_text('Earlier'), command_stdout('Context: 9,000 tokens'))) + '\n')
    last = t.last_message_from_tail(path)
    assert last.kind == t.COMMAND_OUTPUT and last.label == 'Claude Code'


def test_growing_rewrite_detects_changed_prefix_with_an_unchanged_old_tail(tmp_path):
    from records import command_stdout
    path = tmp_path / 'synthetic.jsonl'
    record = user_text('First' + 'x' * 10000)
    original = lines(record)[0] + '\n'
    path.write_text(original)
    reader = TranscriptReader(path)
    assert reader.refresh()
    rewritten = original.replace('First', 'Other')
    assert original[-4096:] == rewritten[-4096:]
    path.write_text(rewritten + lines(command_stdout('Context updated'))[0] + '\n')
    assert reader.refresh()
    assert reader.transcript.visible()[0].text.startswith('Other')
    assert reader.transcript.visible()[-1].kind == t.COMMAND_OUTPUT


def test_rewrite_to_only_unknown_records_clears_the_visible_history(tmp_path):
    path = tmp_path / 'synthetic.jsonl'
    path.write_text(lines(user_text('Earlier'))[0] + '\n')
    reader = TranscriptReader(path)
    assert reader.refresh()
    path.write_text('{"type":"future_record"}\n')
    assert reader.refresh() and not reader.transcript.messages


def test_local_stdout_and_stderr_strip_terminal_codes_and_bound_display():
    from records import command_stdout
    body = '\x1b[1mCost report.\x1b[0m ' + 'x' * 100000
    tr = parse_lines(lines(command_stdout(body), user_text(
        '<local-command-stderr>\x1b[31mCommand failed.\x1b[0m</local-command-stderr>')))
    assert kinds(tr) == [t.COMMAND_OUTPUT, t.COMMAND_ERROR]
    assert len(tr.visible()[0].text) <= t.MAX_COMMAND_OUTPUT
    assert tr.visible()[0].text.endswith('[Command output shortened for display.]')
    assert '\x1b' not in tr.visible()[0].text
    assert tr.visible()[1].text == 'Command failed.'
    assert tr.visible()[1].label == 'Claude Code error'


def test_unchanged_and_touched_transcripts_do_not_reparse_or_reopen_repeatedly(tmp_path, monkeypatch):
    import builtins
    import os
    path = tmp_path / 'synthetic.jsonl'
    path.write_text(lines(user_text('Earlier'))[0] + '\n')
    reader = TranscriptReader(path)
    assert reader.refresh()
    parser = reader._parser
    original, opens = builtins.open, []
    def opened(file, *args, **kwargs):
        if isinstance(file, (str, type(path))) and type(path)(file) == path:
            opens.append(True)
        return original(file, *args, **kwargs)
    monkeypatch.setattr(builtins, 'open', opened)
    assert not reader.refresh() and not opens
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000000))
    assert not reader.refresh() and reader._parser is parser
    assert len(opens) == 1
    assert not reader.refresh() and len(opens) == 1
