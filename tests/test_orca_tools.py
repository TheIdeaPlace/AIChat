"""The Orca CI job's own logic (#579), without Orca: the names audit's one
retry after a crash, which must never hide an unnamed control."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import orca_names  # noqa: E402
import orca_speech  # noqa: E402


def test_every_step_trigger_does_something():
    """A trigger with no action would leave its step waiting for nothing."""
    triggers = {step.trigger for step in orca_speech.STEPS if step.trigger}
    assert triggers - set(orca_speech.TRIGGERS) == set()


def _log(corrections, loaded=True):
    lines = [orca_speech.LOADED + " (cache cleared with clear_cache_single)"] if loaded else []
    return "\n".join(lines + [f"{orca_speech.CORRECTED} (frame), so it isn't"] * corrections)


def test_corrections_are_counted_by_step_and_a_jump_is_warned_about():
    results = [{"step": "quiet", "corrected": 0}, {"step": "busy", "corrected": 3},
               {"step": "a jump", "corrected": 12}]
    lines = orca_speech.orca_corrections(_log(15), results, ceiling=10)
    assert lines[0].startswith("Orca took a live object for dead 15 times")
    assert "      3  busy" in lines and "     12  a jump" in lines
    assert not any("quiet" in line for line in lines)
    warnings = [line for line in lines if line.startswith("::warning::")]
    assert len(warnings) == 1 and warnings[0].startswith("::warning::a jump: Orca took 12")


def test_an_orca_without_the_correction_says_so():
    lines = orca_speech.orca_corrections(_log(0, loaded=False), [])
    assert lines[0].startswith("Orca's dead objects weren't corrected")


class ChordLog:
    """Stands in for OrcaLog in press(): records each wait for Orca to handle
    a key in the same list as the keys sent, and says whether it was seen."""

    def __init__(self, sent, handled=True):
        self.sent, self.handled = sent, handled

    def size(self):
        return 0

    def wait_for(self, offset, pattern, timeout):
        self.sent.append(("wait", pattern))
        return self.handled


def _pressing(monkeypatch, handled=True):
    sent = []
    monkeypatch.setattr(orca_speech, "_xdotool", lambda *args: sent.append(args))
    monkeypatch.setattr(orca_speech.time, "sleep", lambda seconds: None)
    return sent, ChordLog(sent, handled)


def _handled(key):
    return ("wait", orca_speech._KEY_HANDLED.format(key=key))


def test_a_chord_is_held_a_modifier_at_a_time_and_orca_handles_each_first(monkeypatch):
    """All of "ctrl+shift+a" at once had Orca still on the modifiers when the
    A came, and the app's dialog opened before Orca handled it: Orca 46 then
    read the window and not the box the focus was in ("answer the tool")."""
    sent, log = _pressing(monkeypatch)
    orca_speech.press(["ctrl+shift+a"], log)
    assert sent == [("keydown", "ctrl"), _handled("Control_L"),
                    ("keydown", "shift"), _handled("Shift_L"),
                    ("key", "a"), ("keyup", "shift"), ("keyup", "ctrl")]


def test_a_modifier_orca_never_logs_still_presses_and_releases_the_chord(monkeypatch, capsys):
    sent, log = _pressing(monkeypatch, handled=False)
    orca_speech.press(["Ctrl+Shift+V"], log)
    assert [s for s in sent if s[0] != "wait"] == [("keydown", "Ctrl"), ("keydown", "Shift"),
                                                   ("key", "V"), ("keyup", "Shift"),
                                                   ("keyup", "Ctrl")]
    assert "didn't show it handling Ctrl" in capsys.readouterr().out


def test_a_chord_whose_key_fails_still_lets_go_of_its_modifiers(monkeypatch):
    sent = []

    def xdotool(*args):
        sent.append(args)
        if args[0] == "key":
            raise RuntimeError("xdotool failed")
    monkeypatch.setattr(orca_speech, "_xdotool", xdotool)
    log = ChordLog(sent)
    try:
        orca_speech.press(["alt+c"], log)
    except RuntimeError:
        pass
    assert sent[-1] == ("keyup", "alt")


def test_one_modifier_that_wont_let_go_doesnt_keep_the_others_held(monkeypatch):
    sent = []

    def xdotool(*args):
        sent.append(args)
        if args == ("keyup", "shift"):
            raise RuntimeError("xdotool failed")
    monkeypatch.setattr(orca_speech, "_xdotool", xdotool)
    monkeypatch.setattr(orca_speech.time, "sleep", lambda seconds: None)
    try:
        orca_speech.press(["ctrl+shift+a"], ChordLog(sent))
    except RuntimeError:
        pass
    else:
        raise AssertionError("the failed keyup was swallowed")
    assert sent[-1] == ("keyup", "ctrl")


def test_keys_on_their_own_are_sent_as_they_are(monkeypatch):
    sent, log = _pressing(monkeypatch)
    orca_speech.press(["Tab", "shift+Return", "z"], log)
    assert sent == [("key", "Tab"), ("keydown", "shift"), _handled("Shift_L"),
                    ("key", "Return"), ("keyup", "shift"), ("key", "z")]
    assert orca_speech._chord("plus") is None and orca_speech._chord("KP_Add") is None


def test_orcas_log_of_a_handled_key_is_recognized(tmp_path):
    """The line Orca 46 writes once it has handled a key (CI), and the form
    later Orca writes it in."""
    path = tmp_path / "orca.log"
    path.write_text("14:35:27.745 - SPEECH OUTPUT: 'left shift'\n"
                    "^^^^^ PROCESS ATSPI_KEY_PRESSED_EVENT: 'Shift_L' (50) ^^^^^\n"
                    "^^^^^ PROCESS ATSPI_KEY_PRESSED_EVENT : 'Control_L' (37) ^^^^^\n",
                    encoding="utf-8")
    log = orca_speech.OrcaLog(path)
    pattern = orca_speech._KEY_HANDLED.format
    assert log.wait_for(0, pattern(key="Shift_L"), timeout=0)
    assert log.wait_for(0, pattern(key="Control_L"), timeout=0)
    assert not log.wait_for(0, pattern(key="Alt_L"), timeout=0)
    # Released keys don't count: only Orca done with the press.
    path.write_text("^^^^^ PROCESS ATSPI_KEY_RELEASED_EVENT: 'Alt_L' (64) ^^^^^\n",
                    encoding="utf-8")
    assert not log.wait_for(0, pattern(key="Alt_L"), timeout=0)


def _window_from_key(focus, window, key, mods):
    """Orca 46's lines for a key press that found ``window`` newly active, as
    in the failed runs' debug logs."""
    return (f"14:35:27.773295 - FOCUS MANAGER: Focus [{focus}] is not in [frame: '{window}'] "
            "orca.start > event_manager._processKeyboardEvent > input_event.__init__ > "
            "focus_manager.set_active_window\n"
            f"14:35:27.781241 - FOCUS MANAGER: Focus [frame: '{window}'] is not in [frame: "
            f"'{window}'] focus_manager.set_locus_of_focus > script.locusOfFocusChanged > "
            "default.locusOfFocusChanged > focus_manager.set_active_window\n"
            f"14:35:27.814289 - KEYBINDINGS: '{key}' ({key}) mods: {mods} key-pressed-event "
            "matches []\n")


def test_only_a_chord_that_finds_a_new_window_is_counted():
    """Escape and Return closing a dialog find the window behind it this way
    some 40 times a run, harmlessly; only Ctrl+Shift+A was behind the
    unsaid focus (PR #729's first run, and two runs before it)."""
    escape = _window_from_key("text: 'Linux notes: your own answer'",
                              "Visual probe — The Chat Place", "Escape", 0)
    chord = _window_from_key("table cell: 'You: Make the uploader flush before it signals.'",
                             "trip-planner asks: Visual probe", "A", 5)
    assert orca_speech.windows_from_chords(escape) == 0
    assert orca_speech.windows_from_chords(chord) == 1
    assert orca_speech.windows_from_chords(escape + chord + escape) == 1


def test_a_failed_step_says_when_orca_took_a_window_from_a_chord(tmp_path):
    path = tmp_path / "orca.log"
    path.write_text(_window_from_key("table cell: 'You'", "trip-planner asks: Visual probe",
                                     "A", 5), encoding="utf-8")
    count = orca_speech.windows_from_chords(orca_speech.OrcaLog(path).text_since(0))
    assert count == 1
    step = {"step": "answer the tool", "keys": ["ctrl+shift+a"], "spoken": ["A"],
            "missing": ["Message"], "refused": [], "passed": False, "window_from_key": count}
    passed = dict(step, step="opened fine", missing=[], passed=True)
    text = orca_speech.report([step, passed], (46, 0), record=False)
    assert text.count("took the window for the focus") == 1


def test_crashes_are_told_from_failures_and_timeouts():
    assert orca_names.crashed(139) == "SIGSEGV"
    assert orca_names.crashed(-11) == "SIGSEGV"
    assert orca_names.crashed(134) == "SIGABRT"
    for code in (0, 1, 2, 124, 137):  # finished, failed, timed out, killed
        assert orca_names.crashed(code) is None


class FakeProbe:
    """Finishes surfaces in turn, crashing (exit 139) on those in ``crash``
    the given number of times."""

    def __init__(self, crash=None, unnamed=()):
        self.crash = dict(crash or {})
        self.unnamed = set(unnamed)
        self.calls = []

    def __call__(self, folder, names):
        self.calls.append(list(names))
        done = {}
        for name in names:
            if self.crash.get(name):
                self.crash[name] -= 1
                return 139, done
            orca = {"controls": [{"role": "push button", "name": "OK"}], "unnamed": []}
            if name in self.unnamed:
                orca["unnamed"] = [{"role": "push button", "name": ""}]
            (folder / f"{name}-linux-light.json").write_text(json.dumps({"orca": orca}),
                                                             encoding="utf-8")
            done[name] = {"png": f"{name}.png"}
        return 0, done


def test_a_crash_is_run_again_once_from_where_it_stopped(tmp_path, capsys):
    probe = FakeProbe(crash={"b": 1})
    code, problems = orca_names.run(tmp_path, ["a", "b", "c"], probe=probe)
    assert (code, problems) == (0, [])
    assert probe.calls == [["a", "b", "c"], ["b", "c"]]
    assert "crashed (SIGSEGV) on b" in capsys.readouterr().out
    unnamed, missing, seen = orca_names.check(tmp_path)
    assert (unnamed, missing, seen) == ([], [], 3)


def test_a_surface_that_crashes_twice_fails(tmp_path):
    probe = FakeProbe(crash={"b": 2})
    _code, problems = orca_names.run(tmp_path, ["a", "b", "c"], probe=probe)
    assert problems == ["b: the probe crashed (SIGSEGV) again, after running again once "
                        "for a crash on b"]
    assert len(probe.calls) == 2


def test_two_surfaces_that_crash_once_each_fail(tmp_path):
    """One rerun in the whole run, not one per surface."""
    probe = FakeProbe(crash={"a": 1, "c": 1})
    _code, problems = orca_names.run(tmp_path, ["a", "b", "c", "d"], probe=probe)
    assert problems == ["c: the probe crashed (SIGSEGV) again, after running again once "
                        "for a crash on a"]
    assert probe.calls == [["a", "b", "c", "d"], ["a", "b", "c", "d"]]


def test_a_crash_after_the_last_surface_fails(tmp_path):
    def probe(folder, names):
        return 139, {name: {"png": f"{name}.png"} for name in names}
    _code, problems = orca_names.run(tmp_path, ["a"], probe=probe)
    assert problems == ["the probe crashed (SIGSEGV) after its last surface, closing"]


def test_an_odd_exit_after_the_last_surface_fails(tmp_path):
    def probe(folder, names):
        return 2, {name: {"png": f"{name}.png"} for name in names}
    _code, problems = orca_names.run(tmp_path, ["a"], probe=probe)
    assert problems == ["the probe exited with 2 after its last surface"]


def test_an_unnamed_control_is_never_run_again(tmp_path):
    probe = FakeProbe(unnamed={"b"})
    code, problems = orca_names.run(tmp_path, ["a", "b"], probe=probe)
    assert (code, problems) == (0, [])
    assert probe.calls == [["a", "b"]]
    unnamed, _missing, _seen = orca_names.check(tmp_path)
    assert unnamed == ["b-linux-light: a push button with no name"]


def test_a_probe_that_stops_without_crashing_fails(tmp_path):
    def probe(folder, names):
        return 124, {}
    _code, problems = orca_names.run(tmp_path, ["a"], probe=probe)
    assert problems == ["the probe stopped (exit 124) before a"]


def test_a_surface_the_probe_couldnt_show_fails(tmp_path):
    def probe(folder, names):
        return 1, {"a": {"error": "Traceback\nno dialog appeared"}}
    _code, problems = orca_names.run(tmp_path, ["a"], probe=probe)
    assert problems == ["a: no dialog appeared"]
