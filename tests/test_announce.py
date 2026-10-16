

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
