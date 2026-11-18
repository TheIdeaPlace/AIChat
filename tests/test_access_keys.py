"""Which control a dialog's access key reaches (#675), without wx: the rules
ui/mac_keys.py follows for Option+letter on a Mac, as Windows does for
Alt+letter, and which button key equivalents a Mac must lose."""
from thechatplace.access_keys import (BUTTON, CHECK, GROUP, LABEL, OTHER, RADIO, Stop, Target,
                                      access_key, duplicates, find, option_letter)
from thechatplace.ui import mac_a11y


def test_the_access_key_is_the_letter_after_a_single_ampersand():
    assert access_key("Sa&ve Limits") == "v"
    assert access_key("&Ask") == "a"
    assert access_key("Claude API &key:") == "k"
    assert access_key("R&eset Total...") == "e"


def test_a_doubled_ampersand_is_written_out_not_an_access_key():
    assert access_key("Bed && breakfast") == ""
    assert access_key("Bed && &breakfast") == "b"
    assert access_key("&&&Go") == "g"  # "&" written out, then G
    assert access_key("Salt &&") == ""


def test_no_access_key_without_a_character_after_the_ampersand():
    assert access_key("") == ""
    assert access_key("Close") == ""
    assert access_key("Trailing &") == ""
    assert access_key("Pick & choose") == ""  # a space isn't a key


def test_only_the_first_ampersand_counts_and_case_doesnt():
    assert access_key("&One &Two") == "o"
    assert access_key("Read as &Plain Text") == "p"
    assert access_key("Über &Ärger") == "ä"


def test_a_button_is_pressed_and_keeps_the_focus_where_it_was():
    stops = [Stop(LABEL, "k"), Stop(OTHER, focusable=True), Stop(BUTTON, "v", focusable=True)]
    assert find(stops, "v") == Target(2, None, BUTTON)
    assert find(stops, "V") == Target(2, None, BUTTON)


def test_a_label_moves_to_the_next_control_that_can_take_the_focus():
    stops = [Stop(LABEL, "k"),
             Stop(OTHER, focusable=False),  # a panel whose controls take it
             Stop(OTHER, focusable=True, usable=False),  # hidden or disabled
             Stop(OTHER, focusable=True)]
    assert find(stops, "k") == Target(0, 3, LABEL)


def test_a_label_reaches_a_button_check_box_or_group_after_it():
    assert find([Stop(LABEL, "s"), Stop(LABEL), Stop(BUTTON, "", focusable=True)], "s") \
        == Target(0, 2, LABEL)
    assert find([Stop(LABEL, "s"), Stop(GROUP, "", focusable=True)], "s") == Target(0, 1, LABEL)


def test_a_label_with_nothing_after_it_reaches_nothing():
    assert find([Stop(OTHER, focusable=True), Stop(LABEL, "z")], "z") is None
    assert find([Stop(LABEL, "z"), Stop(OTHER, focusable=True, usable=False)], "z") is None


def test_check_boxes_radio_buttons_and_groups_take_the_focus_themselves():
    stops = [Stop(CHECK, "c", focusable=True), Stop(RADIO, "r", focusable=True),
             Stop(GROUP, "w", focusable=True)]
    assert find(stops, "c") == Target(0, 0, CHECK)
    assert find(stops, "r") == Target(1, 1, RADIO)
    assert find(stops, "w") == Target(2, 2, GROUP)


def test_a_hidden_or_disabled_control_has_no_access_key():
    stops = [Stop(BUTTON, "t", usable=False), Stop(LABEL, "q")]
    assert find(stops, "t") is None


def test_where_two_controls_share_a_letter_the_first_in_tab_order_has_it():
    stops = [Stop(BUTTON, "a", usable=False), Stop(BUTTON, "a"), Stop(CHECK, "a")]
    assert find(stops, "a") == Target(1, None, BUTTON)
    assert duplicates(stops) == {"a"}


def test_duplicates_count_only_usable_controls_with_keys():
    assert duplicates([Stop(BUTTON, "a"), Stop(BUTTON, "a", usable=False)]) == set()
    assert duplicates([Stop(BUTTON, "a"), Stop(LABEL, "b"), Stop(CHECK, "b")]) == {"b"}
    assert duplicates([Stop(BUTTON), Stop(BUTTON)]) == set()


def test_no_letter_reaches_nothing():
    assert find([Stop(BUTTON, "a")], "") is None
    assert find([], "a") is None


def test_option_alone_and_a_letter_or_digit_names_a_key():
    assert option_letter(True, ord("V"), 0) == "v"
    assert option_letter(True, ord("7"), 0) == "7"
    # Another alphabet's letter comes as the character, not an ASCII code.
    assert option_letter(True, 0, ord("ж")) == "ж"


def test_other_modifiers_or_keys_name_none():
    assert option_letter(False, ord("V"), ord("v")) == ""  # Command, Control or Shift as well
    assert option_letter(True, 315, 0) == ""  # an arrow (WXK_UP): Alt+Up moves in some lists
    assert option_letter(True, ord("-"), 0) == ""
    assert option_letter(True, 13, 13) == ""  # Return
    assert option_letter(True, 0, 0) == ""


def test_a_buttons_command_letter_is_what_a_mac_must_drop():
    command = 1 << 20
    assert mac_a11y.is_command_letter("v", command)
    assert mac_a11y.is_command_letter("a", command | (1 << 19))  # Command+Option too
    assert mac_a11y.is_command_letter("7", command)


def test_return_escape_and_keys_without_command_stay():
    command = 1 << 20
    assert not mac_a11y.is_command_letter("\r", command)  # the default button
    assert not mac_a11y.is_command_letter("\x1b", command)  # Cancel's Escape
    assert not mac_a11y.is_command_letter("\x1b", 0)
    assert not mac_a11y.is_command_letter("", command)
    assert not mac_a11y.is_command_letter(None, command)
    assert not mac_a11y.is_command_letter(" ", command)
    assert not mac_a11y.is_command_letter("v", 0)
    assert not mac_a11y.is_command_letter("ab", command)


def test_the_native_calls_do_nothing_off_a_mac(monkeypatch):
    monkeypatch.setattr(mac_a11y, "IS_MACOS", False)
    assert mac_a11y.stop_button_command_keys() is False
    assert mac_a11y.drop_command_key(object()) is False
    assert mac_a11y.key_equivalent(object()) is None
