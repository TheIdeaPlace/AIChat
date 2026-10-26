"""Speaking through Orca on Linux (orca.py): its D-Bus remote controller, then
the app's window, and speech-dispatcher when neither takes the text. Made-up
D-Bus answers throughout, so these run on every platform."""
import inspect

from thechatplace import orca
from thechatplace.orca import NO_SERVICE, Orca, OrcaClient


UNKNOWN = (False, "GDBus.Error:org.freedesktop.DBus.Error.ServiceUnknown: The name "
                  "org.gnome.Orca1.Service was not provided by any .service files")


class FakeGio:
    """_gio_present that answers each call from a list, in turn."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, name, path, interface, text, timeout):
        self.calls.append((name, text, timeout))
        return self.answers.pop(0) if self.answers else (False, "no answer")


def test_presentmessage_speaks_through_orca_newest_name_first():
    gio = FakeGio((True, ""))
    client = OrcaClient(gio=gio, orca_pids=lambda: {10})
    assert client.present("Hello") is None
    assert [(name, text) for name, text, _s in gio.calls] == [("org.gnome.Orca1.Service", "Hello")]


def test_without_libgio_orca_cant_be_reached_and_no_command_is_run():
    """The text is never put on a command line (/proc is readable by other
    users): without libgio, speech-dispatcher speaks it instead."""
    client = OrcaClient(gio=lambda *a: None, orca_pids=lambda: {10})
    assert client.present("Hello") == "can't be reached: libgio couldn't be loaded"
    source = inspect.getsource(orca)
    assert "subprocess" not in source
    for tool in ("busctl", "gdbus", "dbus-send"):
        assert f'"{tool}"' not in source


def test_an_orca_without_the_service_is_asked_once():
    gio = FakeGio(UNKNOWN, UNKNOWN)
    client = OrcaClient(gio=gio, orca_pids=lambda: {10})
    assert client.present("One") == NO_SERVICE
    assert client.present("Two") == NO_SERVICE
    assert [name for name, _t, _s in gio.calls] == [
        "org.gnome.Orca1.Service", "org.gnome.Orca.Service"]  # each name once, then not again
    # A new Orca (another process) is asked afresh.
    client.orca_pids = lambda: {11}
    gio.answers = [(True, "")]
    assert client.present("Three") is None


def test_a_timeout_isnt_taken_for_an_old_orca():
    gio = FakeGio((False, "GDBus.Error:org.freedesktop.DBus.Error.NoReply: Timeout was reached"),
                  UNKNOWN)
    client = OrcaClient(gio=gio, orca_pids=lambda: {10})
    assert client.present("One") == "didn't answer on D-Bus"
    gio.answers = [(True, "")]
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


def test_the_name_that_worked_is_tried_first_next_time():
    gio = FakeGio((False, "no"), (True, ""), (True, ""))
    client = OrcaClient(gio=gio, orca_pids=lambda: {10})
    assert client.present("One") is None  # Orca1 failed, then Orca spoke
    assert client.present("Two") is None
    assert gio.calls[2][0] == "org.gnome.Orca.Service"


def test_both_calls_together_stay_within_the_deadline():
    now = [0.0]
    calls = []

    def gio(name, path, interface, text, timeout):
        calls.append(timeout)
        now[0] += timeout  # each call hangs until it times out
        return False, "Timeout was reached"
    client = OrcaClient(gio=gio, orca_pids=lambda: {10}, clock=lambda: now[0])
    assert client.present("Hello") == "didn't answer on D-Bus"
    assert now[0] <= orca.DEADLINE + 0.2
    assert len(calls) == 1  # the first used the whole deadline


def test_orca_is_looked_up_once_per_announcement():
    scans = []
    client = FakeClient()
    client.orca_pids = lambda: scans.append(1) or {10}
    outcome = Orca(client).speak("Hello", "auto", True)
    assert outcome.spoke == "D-Bus" and len(scans) == 1
