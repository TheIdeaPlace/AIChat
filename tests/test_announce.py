

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
