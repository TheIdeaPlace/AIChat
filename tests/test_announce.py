

def test_unavailable_status_does_not_announce_a_finished_turn():
    from thechatplace.sessions import STATUS_UNAVAILABLE
    from thechatplace.announce import turn_end_text
    assert turn_end_text('Fixture', STATUS_UNAVAILABLE, '', 'Old reply', 'full') is None


def test_a_waiting_detail_that_ends_in_a_full_stop_is_said_with_one():
    """#291: "needs you: <detail>." must not come out with two full stops."""
    from thechatplace.announce import turn_end_text
    from thechatplace.sessions import NEEDS_YOU
    assert turn_end_text('Fixture', NEEDS_YOU, 'Approve the edit.', '', 'full') == (
        'Fixture needs you: Approve the edit.')


def test_a_waiting_question_is_said_without_a_full_stop_after_its_question_mark():
    from thechatplace.announce import turn_end_text
    from thechatplace.sessions import NEEDS_YOU
    assert turn_end_text('Fixture', NEEDS_YOU, 'Claude asks: Which file?', '', 'full') == (
        'Fixture needs you: Claude asks: Which file?')
    assert turn_end_text('Fixture', 'idle', '', '', 'full') == 'Fixture finished.'


def test_a_send_on_api_credits_says_so_in_its_read_back_and_names_the_chat():
    # #663: "on API credits" is part of the read-back, said once the message
    # really went; with no read-back, the notice alone names the chat.
    from thechatplace.announce import api_sent_text, sent_text
    assert sent_text('Budget', 'Add the totals.', 'full', True, api=True) == (
        'Sent on API credits to Budget: Add the totals.')
    assert sent_text('Budget', 'Add the totals.', 'full', False, api=True) == (
        'Sent on API credits to Budget.')
    assert sent_text('Budget', 'Add the totals.', 'silent', True, api=True) == (
        'Sent on API credits to Budget.')
    assert sent_text('Budget', 'x', 'full', True, queued=True, api=True) == (
        'Sent on API credits to Budget.')
    assert api_sent_text('Budget') == 'Sent on API credits to Budget.'
    # The subscription's are as they were.
    assert sent_text('Budget', 'Add the totals.', 'full', True) == 'Sent to Budget: Add the totals.'
    assert sent_text('Budget', 'x', 'full', False) == 'Sent. Budget is working.'


def test_messages_given_back_say_what_they_were_and_whether_thats_certain():
    # #663: a Send Now comes back on Stop as a steer does; without Claude
    # Code's echoes, whether it went in can't be told, and that's said.
    from thechatplace.announce import API_SENDING, untaken_text
    assert untaken_text('Budget', ['steer']) == (
        "Your steered message for Budget wasn't taken in. It's back in the message box.")
    assert untaken_text('Budget', ['now']) == (
        "Your Send Now message for Budget wasn't taken in. It's back in the message box.")
    assert untaken_text('Budget', ['steer', 'now']) == (
        "Your 2 messages for Budget weren't taken in. They're back in the message box.")
    assert untaken_text('Budget', ['steer'], sure=False) == (
        "Your steered message for Budget may not have been taken in. "
        "It's back in the message box.")
    assert untaken_text('Budget', ['now', 'now'], sure=False) == (
        "Your 2 messages for Budget may not have been taken in. They're back in the message box.")
    assert API_SENDING == "Sending on API credits…"


REPLY_WITH_HEADING = ("## What changed\nThe upload test failed one run in ten. It retries now.\n\n"
                      "## Next\nShip it.")


def test_a_reply_starting_with_a_heading_is_announced_with_a_stop_after_it():
    """#744: the heading ran into the text after it, and its #s were said."""
    from thechatplace.announce import reply_text, turn_end_text, aside_text, peer_text
    from thechatplace.speech import strip_for_speech
    summary = reply_text('Uploads', REPLY_WITH_HEADING, 'summary')
    assert summary == ('Uploads replied: What changed. The upload test failed one run in ten. '
                       '16 words.')  # the #s aren't words
    full = reply_text('Uploads', REPLY_WITH_HEADING, 'full')
    assert '#' not in full
    assert ' '.join(strip_for_speech(full).split()) == (
        'Uploads replied. What changed. The upload test failed one run in ten. It retries now. '
        'Next. Ship it.')
    assert turn_end_text('Uploads', 'idle', '', REPLY_WITH_HEADING, 'summary') == (
        'Uploads finished. What changed. The upload test failed one run in ten.')
    assert '#' not in turn_end_text('Uploads', 'idle', '', REPLY_WITH_HEADING, 'full')
    assert aside_text(REPLY_WITH_HEADING, 'summary').startswith('Answer: What changed. The upload')
    assert peer_text('Uploads', 'Laptop', REPLY_WITH_HEADING, 'summary') == (
        'Uploads: message from Laptop: What changed. The upload test failed one run in ten.')


def test_first_sentence_with_a_heading():
    from thechatplace.announce import first_sentence
    # The first sentence after a heading comes with it, if there's room.
    assert first_sentence(REPLY_WITH_HEADING) == (
        'What changed. The upload test failed one run in ten.')
    assert first_sentence('## What changed\n\n## Details\nAll of it.') == (
        'What changed. Details. All of it.')
    assert first_sentence('# Done!') == 'Done!'
    assert first_sentence('## \nJust text.') == 'Just text.'  # an empty heading
    # No room for the next sentence: the heading alone, then cut if too long.
    assert first_sentence('## What changed\nThe upload test.', limit=30) == 'What changed.'
    assert first_sentence('## ' + 'word ' * 60, limit=40).endswith('…')
    # Not a heading: as before. Indented four spaces is code, not a heading.
    assert first_sentence('#744 is fixed. More.') == '#744 is fixed.'
    assert first_sentence('     # comment\nrest of it.') == '# comment rest of it.'
    # Windows line ends, and a heading in bold.
    assert first_sentence('\r\n## Title ##\r\nBody text.') == 'Title. Body text.'
    assert first_sentence('## **Bold**\nx') == '**Bold.** x'  # speech drops the marks
