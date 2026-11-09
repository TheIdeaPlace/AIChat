"""The Orca CI job's own logic (#579), without Orca: the names audit's one
retry after a crash, which must never hide an unnamed control."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import orca_names  # noqa: E402


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
    assert problems == ["b: the probe crashed (SIGSEGV) on it twice"]
    assert len(probe.calls) == 2


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
