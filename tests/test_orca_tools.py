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
