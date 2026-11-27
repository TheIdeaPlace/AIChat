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


def test_kept_focus_is_counted_and_named_only_in_failed_steps():
    """The #751 correction acts some 80 times a run, nearly all as Escape
    closes a dialog: the total is reported, and only failed steps by name."""
    text = "\n".join([orca_speech.KEY_LOADED] + [orca_speech.KEY_KEPT] * 3)
    results = [{"step": "dialog closed", "focus_kept": 2, "passed": True},
               {"step": "answer the tool", "focus_kept": 1, "passed": False},
               {"step": "quiet", "focus_kept": 0, "passed": False}]
    lines = orca_speech.key_focus_kept(text, results)
    assert lines[0].startswith("Orca had a window for the focus while handling a key 3 times")
    assert lines[1:] == ["      1  answer the tool (failed)"]


def test_an_orca_without_the_key_focus_correction_says_so():
    lines = orca_speech.key_focus_kept(orca_speech.LOADED, [])
    assert lines[0].startswith("Orca's quiet focus moves on a key weren't stopped")


def test_the_customization_writes_what_the_report_counts():
    """The report finds the correction by the customization's own words, so
    the two mustn't drift apart."""
    import ast
    tree = ast.parse((ROOT / "tools" / "orca-customizations.py").read_text(encoding="utf-8"))
    strings = [node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    strings += ["".join(part.value for part in node.values if isinstance(part, ast.Constant))
                for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)]
    for marker in (orca_speech.LOADED, orca_speech.CORRECTED, orca_speech.KEY_LOADED,
                   orca_speech.KEY_KEPT):
        assert any(text.startswith(marker) for text in strings), marker


def _load_customizations(monkeypatch, keep_quiet_move=False):
    """Runs tools/orca-customizations.py against stand-ins for the parts of
    Orca 46 it touches, and returns them."""
    import types
    roles = types.SimpleNamespace(DIALOG="dialog", FRAME="frame", WINDOW="window")
    atspi = types.SimpleNamespace(Role=roles, Accessible=types.SimpleNamespace(
        clear_cache_single=lambda obj: None))
    gi = types.ModuleType("gi")
    gi.require_version = lambda name, version: None
    repository = types.ModuleType("gi.repository")
    repository.Atspi = atspi
    gi.repository = repository
    messages = []
    debug = types.ModuleType("orca.debug")
    debug.debugFile, debug.LEVEL_INFO = None, 800
    debug.printMessage = lambda level, text, timestamp=False: messages.append(text)

    class AXObject:
        roles = {}

        @staticmethod
        def get_role(obj):
            return AXObject.roles.get(obj)

    ax_object = types.ModuleType("orca.ax_object")
    ax_object.AXObject = AXObject

    class FocusManager:
        locus, focused = None, None

        def get_locus_of_focus(self):
            return self.locus

        def find_focused_object(self):
            return self.focused

    manager = FocusManager()
    focus_manager = types.ModuleType("orca.focus_manager")
    focus_manager.getManager = lambda: manager
    seen = []

    class Script:
        def presentKeyboardEvent(self, event):
            # Orca 46's own: moves the focus quietly if it's on a window.
            seen.append(manager.find_focused_object())
            if event == "boom":
                raise RuntimeError(event)
            return True

    default = types.ModuleType("orca.scripts.default")
    default.Script = Script
    scripts = types.ModuleType("orca.scripts")
    scripts.default = default
    orca = types.ModuleType("orca")
    orca.debug, orca.focus_manager, orca.scripts = debug, focus_manager, scripts
    for name, module in {"gi": gi, "gi.repository": repository, "orca": orca,
                         "orca.debug": debug, "orca.ax_object": ax_object,
                         "orca.focus_manager": focus_manager, "orca.scripts": scripts,
                         "orca.scripts.default": default}.items():
        monkeypatch.setitem(sys.modules, name, module)
    if keep_quiet_move:
        monkeypatch.setenv("TCP_ORCA_KEEP_QUIET_MOVE", "1")
    else:
        monkeypatch.delenv("TCP_ORCA_KEEP_QUIET_MOVE", raising=False)
    source = (ROOT / "tools" / "orca-customizations.py").read_text(encoding="utf-8")
    exec(compile(source, "orca-customizations.py", "exec"), {"__name__": "orca_customizations"})
    return types.SimpleNamespace(script=Script(), manager=manager, roles=AXObject.roles,
                                 seen=seen, messages=messages)


def test_orca_46_is_kept_from_its_quiet_focus_move_on_a_key(monkeypatch):
    """With the focus on a dialog's frame as a key is handled, Orca 46 finds
    no focused object to move to quietly, so the control's own focus event
    is said (#751)."""
    orca = _load_customizations(monkeypatch)
    assert orca_speech.KEY_LOADED in orca.messages
    orca.manager.locus, orca.manager.focused = "dialog frame", "message box"
    orca.roles.update({"dialog frame": "frame", "message box": "text"})
    assert orca.script.presentKeyboardEvent("A") is True
    assert orca.seen == [None]
    assert orca.messages.count(orca_speech.KEY_KEPT + ", so the focused control's own event "
                               "is said") == 1
    # Put back afterwards, even when Orca's own fails.
    assert orca.manager.find_focused_object() == "message box"
    try:
        orca.script.presentKeyboardEvent("boom")
    except RuntimeError:
        pass
    assert orca.manager.find_focused_object() == "message box"


def test_a_focus_not_on_a_window_is_left_alone(monkeypatch):
    orca = _load_customizations(monkeypatch)
    orca.manager.locus, orca.manager.focused = "message box", "message box"
    orca.roles["message box"] = "text"
    orca.script.presentKeyboardEvent("Tab")
    assert orca.seen == ["message box"]
    assert not any(m.startswith(orca_speech.KEY_KEPT) for m in orca.messages)


def test_a_window_with_nothing_focused_writes_no_correction(monkeypatch):
    orca = _load_customizations(monkeypatch)
    orca.manager.locus = "main frame"
    orca.roles["main frame"] = "frame"
    orca.script.presentKeyboardEvent("Escape")
    assert not any(m.startswith(orca_speech.KEY_KEPT) for m in orca.messages)


def test_the_quiet_move_can_be_left_in_to_measure_it(monkeypatch):
    orca = _load_customizations(monkeypatch, keep_quiet_move=True)
    orca.manager.locus, orca.manager.focused = "dialog frame", "message box"
    orca.roles.update({"dialog frame": "frame", "message box": "text"})
    orca.script.presentKeyboardEvent("A")
    assert orca.seen == ["message box"]
    assert orca_speech.KEY_LOADED not in orca.messages


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
