"""The speaker queues utterances and stops all of them on an interruption."""
import json
import sys
import threading
import time

import pytest

from thechatplace import speech
from thechatplace.orca import Outcome as OrcaOutcome
from thechatplace.screen_readers import Outcome
from thechatplace.speech import Speaker, SpeechSettings

from markers import windows_screen_readers  # noqa: E402


class FakeEngine:
    """An engine process that 'speaks' until released or killed."""

    started = []

    def __init__(self, command, **kwargs):
        self.command = command
        self.config = json.loads(open(command[command.index("-ConfigPath") + 1]
                                      if "-ConfigPath" in command
                                      else command[command.index("--config") + 1],
                                      encoding="utf-8").read())
        self.done = threading.Event()
        # Set once the speaker is blocked waiting for this engine to finish,
        # so a test knows the speaker has recorded it as running and cannot
        # start the next utterance, without guessing with a sleep (#113).
        self.waiting = threading.Event()
        self.killed = False
        FakeEngine.started.append(self)

    def poll(self):
        return 0 if self.done.is_set() else None

    def wait(self, timeout=None):
        self.waiting.set()
        if not self.done.wait(timeout):
            raise TimeoutError
        return 0

    def kill(self):
        self.killed = True
        self.done.set()


def wait_until(condition, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


def text_of(engine):
    return open(engine.command[engine.command.index("-Path") + 1],
                encoding="utf-8").read()


class FakeReaders:
    """screen_readers.ScreenReaders with a chosen outcome; by default no
    screen reader is running, so the engine script speaks, as before #98."""

    def __init__(self, outcome=None):
        self.outcome = outcome or Outcome()
        self.calls = []

    def speak(self, text, engine, interrupt):
        self.calls.append((text, engine, interrupt))
        return self.outcome


def make(tmp_path, monkeypatch, readers=None):
    FakeEngine.started = []
    monkeypatch.setattr(speech.tempfile, "gettempdir", lambda: str(tmp_path))
    # Linux keeps the speech folder in the session's runtime folder (#230).
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(Speaker, "_command",
                        lambda self, t, c: ["engine", "-Path", str(t), "-ConfigPath", str(c)])
    return Speaker(popen=FakeEngine, screen_readers=readers or FakeReaders())


def log_lines(tmp_path, routes=False):
    lines = (tmp_path / "thechatplace-speak" / "speech.log").read_text(
        encoding="utf-8").splitlines()
    return [line for line in lines if (" route: " in line) == routes]


def test_confirmations_queue_instead_of_overlapping(tmp_path, monkeypatch):
    s = make(tmp_path, monkeypatch)
    settings = SpeechSettings()
    assert s.speak("first", settings, interrupt=False)
    assert s.speak("second", settings, interrupt=False)
    assert wait_until(lambda: len(FakeEngine.started) == 1)
    first = FakeEngine.started[0]
    assert first.waiting.wait(5)                 # the speaker is waiting on it
    # Second waits for the first. A short look for a second engine can only
    # catch more, never flake: with a correct speaker none can appear.
    assert not wait_until(lambda: len(FakeEngine.started) > 1, timeout=0.1)
    assert first.config["interrupt"] is False
    assert text_of(first) == "first"
    first.done.set()
    assert wait_until(lambda: len(FakeEngine.started) == 2)
    second = FakeEngine.started[1]
    assert text_of(second) == "second"
    second.done.set()
    assert wait_until(lambda: not s.busy())


def test_utterances_in_one_clock_tick_keep_their_own_files(tmp_path, monkeypatch):
    """On Windows time.time_ns() ticks every 15.6 ms, so file names built from
    it alone collided and the second utterance overwrote the first's text (#113)."""
    monkeypatch.setattr(speech.time, "time_ns", lambda: 1_000_000)
    s = make(tmp_path, monkeypatch)
    settings = SpeechSettings()
    s.speak("first", settings, interrupt=False)
    s.speak("second", settings, interrupt=False)
    assert wait_until(lambda: len(FakeEngine.started) == 1)
    first = FakeEngine.started[0]
    assert first.waiting.wait(5)
    assert text_of(first) == "first"
    first.done.set()
    assert wait_until(lambda: len(FakeEngine.started) == 2)
    second = FakeEngine.started[1]
    assert text_of(second) == "second"
    assert second.command != first.command
    second.done.set()
    assert wait_until(lambda: not s.busy())


def test_an_announcement_stops_everything_in_flight_and_queued(tmp_path, monkeypatch):
    s = make(tmp_path, monkeypatch)
    settings = SpeechSettings()
    s.speak("confirmation one", settings, interrupt=False)
    s.speak("confirmation two", settings, interrupt=False)
    assert wait_until(lambda: len(FakeEngine.started) == 1)
    first = FakeEngine.started[0]
    assert first.waiting.wait(5)                 # it is running, not starting
    s.speak("Session replied", settings, interrupt=True)
    assert first.killed                           # in-flight speech stopped
    assert wait_until(lambda: len(FakeEngine.started) == 2)
    announcement = FakeEngine.started[1]
    assert announcement.config["interrupt"] is True
    text_file = announcement.command[announcement.command.index("-Path") + 1]
    assert open(text_file, encoding="utf-8").read() == "Session replied"
    announcement.done.set()
    assert wait_until(lambda: not s.busy())
    assert len(FakeEngine.started) == 2           # the queued confirmation was dropped


@pytest.fixture(autouse=True)
def cancels(monkeypatch):
    """Never run a real spd-say: record the cancels instead."""
    calls = []
    monkeypatch.setattr(speech, "_cancel_speech_dispatcher",
                        lambda wait=False: calls.append("wait" if wait else 1))
    return calls


def test_stop_cancels_speech_dispatcher_only_when_an_engine_was_running(
        tmp_path, monkeypatch, cancels):
    monkeypatch.setattr(speech, "IS_LINUX", True)
    s = make(tmp_path, monkeypatch)
    s.stop()
    assert cancels == []
    s.speak("one", SpeechSettings(), interrupt=False)
    assert wait_until(lambda: len(FakeEngine.started) == 1)
    assert FakeEngine.started[0].waiting.wait(5)
    s.stop()
    assert cancels == [1]


def test_an_interrupting_announcement_cancels_before_it_speaks(tmp_path, monkeypatch, cancels):
    """The cancel is made on the speech thread, before the next utterance
    reaches speech-dispatcher (or Orca), so it can't land on it."""
    monkeypatch.setattr(speech, "IS_LINUX", True)
    s = make(tmp_path, monkeypatch)
    order = []
    monkeypatch.setattr(speech, "_cancel_speech_dispatcher",
                        lambda wait=False: order.append(("cancel", wait)))
    s.speak("one", SpeechSettings(), interrupt=False)
    assert wait_until(lambda: len(FakeEngine.started) == 1)
    assert FakeEngine.started[0].waiting.wait(5)
    original = FakeEngine.__init__

    def started(self, command, **kwargs):
        order.append(("engine", None))
        original(self, command, **kwargs)
    monkeypatch.setattr(FakeEngine, "__init__", started)
    s.speak("two", SpeechSettings(), interrupt=True)
    assert wait_until(lambda: len(FakeEngine.started) == 2)
    assert order == [("cancel", True), ("engine", None)]
    s.stop()


def test_speech_dispatcher_isnt_cancelled_when_orca_spoke_last(tmp_path, monkeypatch, cancels):
    """speech-dispatcher's cancel stops every client's speech, Orca's too,
    so it's made only when The Chat Place's own voice was cut off (#230)."""
    monkeypatch.setattr(speech, "IS_LINUX", True)
    readers = FakeReaders(OrcaOutcome(spoke="D-Bus", running=["orca"]))
    s = make(tmp_path, monkeypatch, readers)
    s.speak("one", SpeechSettings(engine="orca"), interrupt=True)
    assert wait_until(lambda: len(readers.calls) == 1)
    assert wait_until(lambda: not s.busy())
    s.speak("two", SpeechSettings(engine="orca"), interrupt=True)
    assert wait_until(lambda: len(readers.calls) == 2)
    s.stop()
    assert cancels == [] and FakeEngine.started == []


def test_speech_dispatcher_isnt_cancelled_when_its_voice_spoke_beside_orca(
        tmp_path, monkeypatch, cancels):
    """Orca 48 and earlier, from another window: Orca is running but can't
    take the text, so The Chat Place's own voice speaks it. Cutting that
    voice off mustn't cancel speech-dispatcher, which would cut Orca off too,
    say while it reads the row you just moved to (#230)."""
    monkeypatch.setattr(speech, "IS_LINUX", True)
    readers = FakeReaders(OrcaOutcome(running=["orca"], problem="not the active window"))
    s = make(tmp_path, monkeypatch, readers)
    s.speak("one", SpeechSettings(engine="orca"), interrupt=False)
    assert wait_until(lambda: len(FakeEngine.started) == 1)
    assert FakeEngine.started[0].waiting.wait(5)
    s.speak("two", SpeechSettings(engine="orca"), interrupt=True)
    assert FakeEngine.started[0].killed
    assert wait_until(lambda: len(FakeEngine.started) == 2)
    assert FakeEngine.started[1].waiting.wait(5)
    s.stop()
    assert cancels == []


def test_speech_dispatcher_is_cancelled_when_its_voice_spoke_without_orca(
        tmp_path, monkeypatch, cancels):
    """With Orca not running, the only speech to cancel is The Chat Place's."""
    monkeypatch.setattr(speech, "IS_LINUX", True)
    s = make(tmp_path, monkeypatch, FakeReaders(OrcaOutcome()))
    s.speak("one", SpeechSettings(engine="orca"), interrupt=False)
    assert wait_until(lambda: len(FakeEngine.started) == 1)
    assert FakeEngine.started[0].waiting.wait(5)
    s.stop()
    assert cancels == [1]


def test_stop_kills_every_running_engine(tmp_path, monkeypatch):
    s = make(tmp_path, monkeypatch)
    s.speak("one", SpeechSettings(), interrupt=False)
    assert wait_until(lambda: len(FakeEngine.started) == 1)
    assert FakeEngine.started[0].waiting.wait(5)
    s.stop()
    assert FakeEngine.started[0].killed
    assert wait_until(lambda: not s.busy())


def test_empty_text_is_not_spoken(tmp_path, monkeypatch):
    s = make(tmp_path, monkeypatch)
    assert s.speak("   ", SpeechSettings()) is False
    assert FakeEngine.started == []


def test_every_utterance_is_logged_in_its_own_file(tmp_path, monkeypatch):
    s = make(tmp_path, monkeypatch)
    s.speak("Sent. Hub is working.", SpeechSettings(), interrupt=False)
    s.speak("Hub replied. " + "word " * 40, SpeechSettings(engine="jaws"))
    assert wait_until(lambda: len(FakeEngine.started) >= 1)
    lines = log_lines(tmp_path)
    assert len(lines) == 2
    assert lines[0].endswith(" queue auto 21 chars: Sent. Hub is working.")
    assert " interrupt jaws " in lines[1] and lines[1].endswith("…")
    assert len(lines[1].split("chars: ", 1)[1]) == 80
    s.stop()


def test_speech_log_drops_its_older_half_when_too_big(tmp_path, monkeypatch):
    monkeypatch.setattr(speech, "LOG_LIMIT", 1000)
    s = make(tmp_path, monkeypatch)
    for i in range(60):
        s.speak(f"utterance {i}", SpeechSettings(), interrupt=False)
    text = (tmp_path / "thechatplace-speak" / "speech.log").read_text(encoding="utf-8")
    assert len(text.encode("utf-8")) < 1100
    assert text.splitlines()[-1].endswith("utterance 59")
    assert text.splitlines()[0][:4].isdigit()  # starts on a whole line
    s.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")
def test_stopping_speech_also_stops_what_the_script_started(tmp_path):
    """speak-engine.sh runs osascript as a child, so it can fall back to say
    when VoiceOver refuses. Killing only the script left osascript running
    to hand VoiceOver stale text after a newer announcement."""
    import os
    import subprocess
    import time
    from thechatplace.speech import _kill_quietly

    pid_file = tmp_path / "child.pid"
    script = f"sleep 30 & echo $! > {pid_file}; wait"
    process = subprocess.Popen(["/bin/bash", "-c", script], start_new_session=True)
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text().strip():
            break
        time.sleep(0.02)
    child = int(pid_file.read_text())
    _kill_quietly(process)
    process.wait(timeout=5)
    for _ in range(100):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("the script's child outlived it")


# --- JAWS and NVDA from the app's own process (#98) ---------------------------


@windows_screen_readers
def test_a_running_screen_reader_takes_the_announcement_and_no_voice_starts(
        tmp_path, monkeypatch):
    readers = FakeReaders(Outcome(spoke="nvda", running=["nvda"]))
    s = make(tmp_path, monkeypatch, readers)
    s.speak("Session replied", SpeechSettings(), interrupt=True)
    assert wait_until(lambda: readers.calls and not s.busy())
    assert readers.calls == [("Session replied", "auto", True)]
    assert FakeEngine.started == []
    assert s.last_route == "spoke through NVDA"
    assert log_lines(tmp_path, routes=True)[0].endswith(" route: spoke through NVDA")


@windows_screen_readers
def test_a_running_but_unreachable_screen_reader_gets_no_windows_voice_over_it(
        tmp_path, monkeypatch):
    """#98: NVDA was running, its client failed, and a Windows voice spoke
    over it. Now nothing speaks and the reason goes to on_problem."""
    readers = FakeReaders(Outcome(running=["nvda"],
                                  problems={"nvda": "didn't answer (Windows error 1722)"}))
    s = make(tmp_path, monkeypatch, readers)
    problems = []
    s.on_problem = problems.append
    s.speak("Session replied", SpeechSettings(), interrupt=True)
    assert wait_until(lambda: problems)
    assert wait_until(lambda: not s.busy())
    assert FakeEngine.started == []
    assert problems == ["not spoken: NVDA is running but didn't answer (Windows error 1722)"]


@windows_screen_readers
def test_locked_windows_speaks_nothing(tmp_path, monkeypatch):
    readers = FakeReaders(Outcome(running=["nvda"], locked=True))
    s = make(tmp_path, monkeypatch, readers)
    problems = []
    s.on_problem = problems.append
    s.speak("A private reply", SpeechSettings(engine="nvda"))
    assert wait_until(lambda: readers.calls and not s.busy())
    assert FakeEngine.started == [] and problems == []
    assert s.last_route == "not spoken: Windows is locked"


@windows_screen_readers
def test_no_screen_reader_running_uses_a_plain_windows_voice(tmp_path, monkeypatch):
    """The script then gets "onecore", so it can't reach for a screen reader
    itself, and no voice or rate, so the Windows voice speaks at its default."""
    s = make(tmp_path, monkeypatch)
    s.speak("Hello", SpeechSettings(engine="jaws", voice="ignored", rate_preset="fast"))
    assert wait_until(lambda: len(FakeEngine.started) == 1)
    config = FakeEngine.started[0].config
    assert (config["engine"], config["voice"], config["rate"]) == ("onecore", "", None)
    FakeEngine.started[0].done.set()
    assert wait_until(lambda: not s.busy())
    assert log_lines(tmp_path, routes=True)[0].endswith(
        "no screen reader running, so a Windows voice")
    assert s.last_route == "no screen reader running, so a Windows voice"


@windows_screen_readers
def test_a_chosen_windows_voice_never_asks_the_screen_readers(tmp_path, monkeypatch):
    readers = FakeReaders(Outcome(spoke="jaws", running=["jaws"]))
    s = make(tmp_path, monkeypatch, readers)
    s.speak("Hello", SpeechSettings(engine="onecore", voice="Zira", rate_preset="fast"))
    assert wait_until(lambda: len(FakeEngine.started) == 1)
    assert readers.calls == []
    config = FakeEngine.started[0].config
    assert (config["engine"], config["voice"], config["rate"]) == ("onecore", "Zira", 4.5)
    FakeEngine.started[0].done.set()
    assert wait_until(lambda: not s.busy())


@windows_screen_readers
def test_a_failing_screen_reader_bridge_never_raises_and_falls_back(tmp_path, monkeypatch):
    class Broken:
        def speak(self, *args):
            raise RuntimeError("boom")

    s = make(tmp_path, monkeypatch, Broken())
    assert s.speak("Hello", SpeechSettings())
    assert wait_until(lambda: len(FakeEngine.started) == 1)
    assert s.last_route == "screen readers couldn't be checked, so a Windows voice"
    FakeEngine.started[0].done.set()
    assert wait_until(lambda: not s.busy())


@windows_screen_readers
def test_a_confirmation_reaches_the_screen_reader_without_interrupting(tmp_path, monkeypatch):
    readers = FakeReaders(Outcome(spoke="jaws", running=["jaws"]))
    s = make(tmp_path, monkeypatch, readers)
    s.speak("Sent.", SpeechSettings(), interrupt=False)
    assert wait_until(lambda: len(readers.calls) == 1 and not s.busy())
    s.speak("Session replied", SpeechSettings(), interrupt=True)
    assert wait_until(lambda: len(readers.calls) == 2 and not s.busy())
    assert readers.calls == [("Sent.", "auto", False), ("Session replied", "auto", True)]


@windows_screen_readers
def test_a_problem_is_reported_once_until_speech_works_again(tmp_path, monkeypatch):
    unreachable = Outcome(running=["nvda"], problems={"nvda": "didn't answer (error 1722)"})
    readers = FakeReaders(unreachable)
    s = make(tmp_path, monkeypatch, readers)
    problems = []
    s.on_problem = problems.append
    for text in ("one", "two"):
        s.speak(text, SpeechSettings(), interrupt=False)
    assert wait_until(lambda: len(readers.calls) == 2 and not s.busy())
    assert len(problems) == 1 and s.last_problem == problems[0]
    readers.outcome = Outcome(spoke="nvda", running=["nvda"])
    s.speak("three", SpeechSettings())
    assert wait_until(lambda: len(readers.calls) == 3 and not s.busy())
    assert s.last_problem == ""
    readers.outcome = unreachable
    s.speak("four", SpeechSettings())
    assert wait_until(lambda: len(problems) == 2)


@windows_screen_readers
def test_locked_windows_is_not_a_problem_to_report(tmp_path, monkeypatch):
    readers = FakeReaders(Outcome(running=["nvda"], locked=True))
    s = make(tmp_path, monkeypatch, readers)
    s.speak("A reply", SpeechSettings())
    assert wait_until(lambda: readers.calls and not s.busy())
    assert s.last_problem == ""


@windows_screen_readers
def test_a_hung_screen_reader_does_not_stop_speech(tmp_path, monkeypatch):
    """A JAWS or NVDA call that never returns is given up on, and while it's
    still out, later announcements aren't sent (no pile of hung threads) and
    no Windows voice talks over the screen reader."""
    monkeypatch.setattr(speech, "SCREEN_READER_TIMEOUT", 0.2)
    monkeypatch.setattr(speech, "SCREEN_READER_FIRST_TIMEOUT", 0.2)
    release = threading.Event()

    class Hanging(FakeReaders):
        def speak(self, text, engine, interrupt):
            self.calls.append(text)
            release.wait(10)
            return Outcome(spoke="nvda", running=["nvda"])

    readers = Hanging()
    s = make(tmp_path, monkeypatch, readers)
    problems = []
    s.on_problem = problems.append
    s.speak("one", SpeechSettings())
    assert wait_until(lambda: problems)
    assert problems == [speech.NOT_ANSWERING]
    assert wait_until(lambda: not s.busy())
    s.speak("two", SpeechSettings())
    assert wait_until(lambda: len(log_lines(tmp_path, routes=True)) == 2)
    assert s.last_route == speech.NOT_ANSWERING
    assert problems == [speech.NOT_ANSWERING]  # one hang, one report
    assert readers.calls == ["one"] and FakeEngine.started == []
    release.set()
    assert wait_until(lambda: not s._reader_call.is_alive())
    s.speak("three", SpeechSettings())
    assert wait_until(lambda: s.last_route == "spoke through NVDA")
    assert readers.calls == ["one", "three"]


@windows_screen_readers
def test_the_first_call_gets_longer_for_its_one_time_setup(tmp_path, monkeypatch):
    """Importing comtypes and loading NVDA's client can be slow on a busy
    machine at start-up; that mustn't be reported as a hung screen reader."""
    monkeypatch.setattr(speech, "SCREEN_READER_TIMEOUT", 0.05)

    class SlowFirst(FakeReaders):
        def speak(self, text, engine, interrupt):
            self.calls.append(text)
            if len(self.calls) == 1:
                time.sleep(0.3)
            return Outcome(spoke="nvda", running=["nvda"])

    s = make(tmp_path, monkeypatch, SlowFirst())
    problems = []
    s.on_problem = problems.append
    s.speak("first", SpeechSettings())
    assert wait_until(lambda: s.last_route == "spoke through NVDA")
    assert problems == []
