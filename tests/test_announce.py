

def test_unavailable_status_does_not_announce_a_finished_turn():
    from thechatplace.sessions import STATUS_UNAVAILABLE
    from thechatplace.announce import turn_end_text
    assert turn_end_text('Fixture', STATUS_UNAVAILABLE, '', 'Old reply', 'full') is None
