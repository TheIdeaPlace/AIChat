"""Speaking through Orca on Linux (orca.py): its D-Bus remote controller, then
the app's window, and speech-dispatcher when neither takes the text. Made-up
D-Bus answers throughout, so these run on every platform."""
import subprocess

from thechatplace import orca
from thechatplace.orca import NO_SERVICE, Orca, OrcaClient


class Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def tools(*names):
    return lambda name: f"/usr/bin/{name}" if name in names else None


class FakeRun:
    """subprocess.run that answers each D-Bus call from a list, in turn."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        answer = self.answers.pop(0) if self.answers else Done(1, "", "no answer")
        if isinstance(answer, BaseException):
            raise answer
        return answer


UNKNOWN = Done(1, "", "Call failed: The name org.gnome.Orca1.Service was not provided by any "
                      ".service files")


def test_every_tool_and_service_name_is_tried_newest_first():
    commands = orca.present_message_commands("Hello", tools("busctl", "gdbus", "dbus-send"))
    assert [c[0] for c in commands] == ["/usr/bin/busctl", "/usr/bin/gdbus", "/usr/bin/dbus-send"] * 2
    # Orca 51's name first, then Orca 49 and 50's.
    assert "org.gnome.Orca1.Service" in commands[0] and "org.gnome.Orca.Service" in commands[3]
    assert commands[0][-3:] == ["PresentMessage", "s", "Hello"]
    assert commands[2][-1] == "string:Hello"


def test_gdbus_gets_the_text_as_a_string_whatever_it_looks_like():
    """gdbus reads its arguments as GVariant text, so "true" or "42" would
    have been sent as a boolean or a number."""
    command = orca.present_message_commands("it's true\n42", tools("gdbus"))[0]
    assert command[-1] == "'it\\'s true\\n42'"
    assert command[-2] == "org.gnome.Orca1.Service.PresentMessage"


def test_no_dbus_tool_means_orca_cant_be_reached():
    client = OrcaClient(gio=None, run=FakeRun(), find=tools(), orca_pids=lambda: {10})
    assert "busctl" in client.present("Hello")


def test_presentmessage_speaks_through_orca():
    run = FakeRun(Done(0, "b true\n"))
    client = OrcaClient(gio=None, run=run, find=tools("busctl"), orca_pids=lambda: {10})
    assert client.present("Hello") is None
    assert len(run.calls) == 1


def test_an_orca_without_the_service_is_asked_once():
    run = FakeRun(UNKNOWN, UNKNOWN)
    client = OrcaClient(gio=None, run=run, find=tools("busctl"), orca_pids=lambda: {10})
    assert client.present("One") == NO_SERVICE
    assert client.present("Two") == NO_SERVICE
    assert len(run.calls) == 2  # each name once, then not again
    # A new Orca (another process) is asked afresh.
    client.orca_pids = lambda: {11}
    run.answers = [Done(0, "(true,)")]
    assert client.present("Three") is None


def test_a_timeout_isnt_taken_for_an_old_orca():
    run = FakeRun(subprocess.TimeoutExpired("busctl", 5), Done(1, "", "Connection timed out"))
    client = OrcaClient(gio=None, run=run, find=tools("busctl"), orca_pids=lambda: {10})
    assert client.present("One") == "didn't answer on D-Bus"
    run.answers = [Done(0, "b true")]
    assert client.present("Two") is None


class FakeClient:
    def __init__(self, problem=None):
        self.problem, self.texts = problem, []

    def present(self, text, pids=None):
        self.texts.append(text)
        return self.problem


def test_orca_not_running_leaves_it_to_a_system_voice():
    reader = Orca(FakeClient(), processes=lambda: {"bash"})
    outcome = reader.speak("Hello", "auto", True)
    assert outcome.running == [] and outcome.voice_allowed
    assert outcome.describe() == "no screen reader running"


def test_orca_takes_it_on_dbus():
    client = FakeClient()
    outcome = Orca(client, processes=lambda: {"orca"}).speak("Hello", "auto", True)
    assert outcome.spoke == "D-Bus" and not outcome.voice_allowed
    assert outcome.describe() == "spoke through Orca (D-Bus)"
    assert client.texts == ["Hello"]


def test_an_older_orca_takes_it_from_the_active_window():
    reader = Orca(FakeClient(NO_SERVICE), processes=lambda: {"orca"})
    sent = []
    reader.window_announcer = lambda text, interrupt: sent.append((text, interrupt)) or True
    outcome = reader.speak("Sent.", "orca", False)
    assert outcome.spoke == "the window" and sent == [("Sent.", False)]


def test_in_the_background_an_older_orca_leaves_it_to_speech_dispatcher():
    """Not the Windows rule (#98): speech-dispatcher, which Orca speaks
    through too, says one thing at a time, so nothing talks over Orca."""
    reader = Orca(FakeClient(NO_SERVICE), processes=lambda: {"orca"})
    reader.window_announcer = lambda text, interrupt: False
    outcome = reader.speak("Garden finished.", "auto", True)
    assert outcome.running == ["orca"] and outcome.voice_allowed and not outcome.unreachable
    assert outcome.describe() == (
        "Orca is running but has no D-Bus remote controller (Orca 49 and later have one), "
        "and The Chat Place isn't the active window, so speech-dispatcher")


def test_a_failing_window_announcer_never_raises():
    reader = Orca(FakeClient(NO_SERVICE), processes=lambda: {"orca"})

    def broken(text, interrupt):
        raise RuntimeError("gone")
    reader.window_announcer = broken
    assert reader.speak("Hello", "auto", True).voice_allowed


# -- review findings (1, 4, 9) -------------------------------------------------------------

def test_the_text_is_cut_in_bytes_not_characters():
    text = "日本語" * 40_000  # 120,000 characters, 360,000 bytes
    cut = orca._truncated(text)
    assert len(cut.encode("utf-8")) <= orca.MESSAGE_LIMIT
    assert text.startswith(cut) and len(cut) > 30_000


def test_the_way_that_worked_is_tried_first_next_time():
    run = FakeRun(Done(1, "", "no"), Done(0, "b true"), Done(0, "b true"))
    client = OrcaClient(gio=None, run=run, find=tools("busctl", "gdbus"), orca_pids=lambda: {10})
    assert client.present("One") is None  # busctl failed, then gdbus spoke
    assert client.present("Two") is None
    assert run.calls[2][0] == "/usr/bin/gdbus"


def test_all_the_attempts_together_stay_within_the_deadline():
    now = [0.0]
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        now[0] += 0.8  # each call hangs 0.8 s
        raise subprocess.TimeoutExpired(argv[0], 0.8)
    client = OrcaClient(gio=None, run=run, find=tools("busctl", "gdbus", "dbus-send"),
                        orca_pids=lambda: {10}, clock=lambda: now[0])
    assert client.present("Hello") == "didn't answer on D-Bus"
    assert len(calls) == 2  # not six


def test_libgio_takes_the_text_without_a_command_line():
    calls = []

    def gio(name, path, interface, text, timeout):
        calls.append((name, text))
        return True, ""
    run = FakeRun()
    client = OrcaClient(gio=gio, run=run, find=tools("busctl"), orca_pids=lambda: {10})
    assert client.present("Hello") is None
    assert calls == [("org.gnome.Orca1.Service", "Hello")] and run.calls == []


def test_when_libgio_is_missing_a_dbus_tool_is_used():
    run = FakeRun(Done(0, "b true"))
    client = OrcaClient(gio=lambda *a: None, run=run, find=tools("busctl"), orca_pids=lambda: {10})
    assert client.present("Hello") is None and len(run.calls) == 1


def test_orca_is_looked_up_once_per_announcement():
    scans = []
    client = FakeClient()
    client.orca_pids = lambda: scans.append(1) or {10}
    outcome = Orca(client).speak("Hello", "auto", True)
    assert outcome.spoke == "D-Bus" and len(scans) == 1


def test_an_orca_without_the_service_is_asked_once_through_libgio_too():
    calls = []

    def gio(name, path, interface, text, timeout):
        calls.append(name)
        return False, "GDBus.Error:org.freedesktop.DBus.Error.ServiceUnknown: not provided"
    client = OrcaClient(gio=gio, run=FakeRun(), find=tools(), orca_pids=lambda: {10})
    assert client.present("One") == NO_SERVICE
    assert client.present("Two") == NO_SERVICE
    assert len(calls) == 2  # each name once, then not again


def test_the_libgio_name_that_worked_is_tried_first():
    calls = []

    def gio(name, path, interface, text, timeout):
        calls.append(name)
        return name == "org.gnome.Orca.Service", ""
    client = OrcaClient(gio=gio, run=FakeRun(), find=tools(), orca_pids=lambda: {10})
    assert client.present("One") is None
    assert client._working == ("gio", "org.gnome.Orca.Service")
    calls.clear()
    assert client.present("Two") is None
    assert calls == ["org.gnome.Orca.Service"]
