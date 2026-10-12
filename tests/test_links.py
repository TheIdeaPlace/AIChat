"""Links to sessions (#144): building and parsing thechatplace:// links, the
hand-off from a second copy to the running one, and when Windows is told
about the scheme. Nothing here touches the real registry or %APPDATA%."""
import os
import sys
import time
import types

import pytest

from thechatplace import app, links, platform_paths, updater
from thechatplace.rendering import markdown_to_html
from thechatplace.sessions import DESKTOP, OWN, SessionInfo


def own(cli="0b5c1e2a-1111-4c6e-9f00-123456789abc", title="Hub probe", **extra):
    return SessionInfo(source=OWN, key=f"own:{cli}", title=title, cwd="C:\\G",
                       cli_session_id=cli, **extra)


def desktop(local="local_7d1f2e3a-2222-4b5c-8d00-abcdefabcdef", cli="cli-d", title="Quiet one",
            **extra):
    return SessionInfo(source=DESKTOP, key=local, title=title, cwd="C:\\G",
                       cli_session_id=cli, desktop_session_id=local, **extra)


# -- building and parsing -----------------------------------------------------------------


def test_each_kind_of_session_links_by_its_lasting_id():
    assert links.session_link(own()) == (
        "thechatplace://session/0b5c1e2a-1111-4c6e-9f00-123456789abc")
    assert links.session_link(desktop()) == (
        "thechatplace://session/local_7d1f2e3a-2222-4b5c-8d00-abcdefabcdef")
    assert links.session_link(desktop(cowork=True)).endswith("/local_7d1f2e3a-2222-4b5c-8d00-"
                                                            "abcdefabcdef")
    # An id that isn't a plain one never makes a link.
    assert links.session_link(own(cli="../x")) == ""
    assert links.session_link(desktop(local="")) == ""


def test_a_link_round_trips():
    for info in (own(), desktop()):
        assert links.parse_link(links.session_link(info)) == links.link_id(info)


@pytest.mark.parametrize("text", [
    "thechatplace://session/abc",
    "  thechatplace://session/abc\n",
    "THECHATPLACE://Session/abc",
    "thechatplace://session/abc/",
])
def test_good_links(text):
    assert links.parse_link(text) == "abc"


@pytest.mark.parametrize("text", [
    "", None, 42, "abc",
    "https://session/abc",
    "claude://claude.ai/epitaxy/local_abc",
    "thechatplace://",
    "thechatplace://session/",
    "thechatplace://session",
    "thechatplace:session/abc",
    "thechatplace://sessions/abc",
    "thechatplace://session/abc/def",
    "thechatplace://session/abc//",
    "thechatplace://session/-abc",
    "thechatplace://session/_abc",
    "thechatplace://session/..",
    "thechatplace://session/../../etc/passwd",
    "thechatplace://session/abc?send=hello",
    "thechatplace://session/abc#x",
    "thechatplace://session/a%20b",
    "thechatplace://session/a b",
    'thechatplace://session/abc" --smoke-test "C:\\x',
    "thechatplace://session/abc'",
    "thechatplace://session/abc\nthechatplace://session/def",
    "thechatplace://session/abc\x00",
    "thechatplace://session/" + "a" * 101,
    "thechatplace://session/" + "a" * 300,
    "thechatplace://session/ab.c",
    "thechatplace://\u017fession/abc",        # long s, which IGNORECASE alone takes for s
    "thechatplace://session/\u212aelvin",     # the Kelvin sign
    "thechatplace://sess\u0131on/abc",        # dotless i
    "thechatplace://session/\u0130d",         # I with a dot
])
def test_anything_else_is_refused(text):
    assert links.parse_link(text) is None


def test_scheme_is_recognised_whether_or_not_the_link_is_good():
    assert links.is_app_link("thechatplace://nonsense")
    assert links.is_app_link(" TheChatPlace:x")
    assert not links.is_app_link("https://thechatplace.example")
    assert not links.is_app_link(None)


def test_find_session_prefers_own_and_ignores_case():
    mine = own(cli="same-id")
    theirs = desktop(local="same-id")
    assert links.find_session([theirs, mine], "same-id") is mine
    assert links.find_session([theirs], "SAME-ID") is theirs
    assert links.find_session([theirs, mine], "other") is None
    assert links.find_session([theirs], "") is None
    # A desktop session isn't found by its Claude Code id, only by the
    # desktop app's, so one link always means one session.
    assert links.find_session([desktop(cli="cli-d")], "cli-d") is None


def test_a_link_on_the_command_line_drowns_out_everything_else():
    assert links.link_argument(["--smoke-test", "x.json"]) is None
    assert links.link_argument(["thechatplace://session/abc", "--smoke-test", "x"]) == (
        "thechatplace://session/abc")
    assert links.link_argument(["--x", " thechatplace://bad "]) == "thechatplace://bad"
    assert len(links.link_argument(["thechatplace://" + "a" * 5000])) <= 201


def test_markdown_link_escapes_the_title():
    assert links.markdown_link("Fix [the] build\\x", "thechatplace://session/a") == (
        "[Fix \\[the\\] build\\\\x](thechatplace://session/a)")
    assert links.markdown_link("two\nlines", "u") == "[two lines](u)"
    assert links.markdown_link("", "u") == "[Session](u)"


def test_copy_choices_best_first_markdown_then_bare():
    info = own(title="Hub probe")
    choices = links.copy_choices(info, remote_url="https://claude.ai/code/session_9")
    assert [c.label.split(",")[0] for c in choices] == [
        "Open in The Chat Place", "Its claude.ai address",
        "Open in The Chat Place", "Its claude.ai address"]
    assert choices[0].text == f"[Hub probe]({links.session_link(info)})"
    assert choices[0].spoken == ("Copied a Markdown link to Hub probe, to open it in The Chat "
                                 "Place.")
    assert choices[2].text == links.session_link(info)
    assert choices[3].text == "https://claude.ai/code/session_9"
    assert choices[3].spoken == "Copied Hub probe's claude.ai address."
    desk = links.copy_choices(desktop(), claude_url="claude://claude.ai/epitaxy/local_x")
    assert [c.label.split(",")[0] for c in desk] == [
        "Open in The Chat Place", "Open in Claude", "Open in The Chat Place", "Open in Claude"]
    assert desk[1].text == "[Quiet one](claude://claude.ai/epitaxy/local_x)"
    # A session Claude hasn't started yet may still change id: no Chat Place link.
    assert links.copy_choices(own(), chat_place=False) == []
    untitled = links.copy_choices(own(title=""))[0]
    assert untitled.text.startswith("[Session](")
    assert untitled.spoken.startswith("Copied a Markdown link to Session,")


def test_session_links_survive_the_formatted_view_and_others_dont():
    page = markdown_to_html("[a](thechatplace://session/abc) [b](thechatplace://session/../x) "
                            "[c](javascript:alert(1))")
    assert 'href="thechatplace://session/abc"' in page
    assert page.count("href=") == 1


# -- the hand-off to the running copy ---------------------------------------------------


def test_a_dropped_link_is_taken_once(tmp_path):
    folder = tmp_path / "links"
    platform_paths.drop_link("thechatplace://session/abc", folder)
    assert [p.suffix for p in folder.iterdir()] == [".link"]
    assert platform_paths.take_dropped_links(folder) == ["thechatplace://session/abc"]
    assert list(folder.iterdir()) == []
    assert platform_paths.take_dropped_links(folder) == []


def test_links_come_out_oldest_first(tmp_path):
    folder = tmp_path / "links"
    first = platform_paths.drop_link("thechatplace://session/one", folder)
    second = platform_paths.drop_link("thechatplace://session/two", folder)
    now = time.time()
    os.utime(first, (now - 5, now - 5))
    os.utime(second, (now - 1, now - 1))
    assert platform_paths.take_dropped_links(folder) == [
        "thechatplace://session/one", "thechatplace://session/two"]


def test_stale_big_and_odd_files_are_removed_unread(tmp_path):
    folder = tmp_path / "links"
    folder.mkdir()
    old = folder / "old.link"
    old.write_text("thechatplace://session/old", encoding="utf-8")
    then = time.time() - platform_paths.LINK_FILE_STALE_SECONDS - 5
    os.utime(old, (then, then))
    big = folder / "big.link"
    big.write_bytes(b"x" * (platform_paths.LINK_FILE_MAX_BYTES + 1))
    binary = folder / "bin.link"
    binary.write_bytes(b"\xff\xfe\x00")
    other = folder / "notes.txt"
    other.write_text("thechatplace://session/x", encoding="utf-8")
    writing = folder / "half.tmp"
    writing.write_text("thechatplace://session/half", encoding="utf-8")
    dead = folder / "dead.tmp"
    dead.write_text("x", encoding="utf-8")
    os.utime(dead, (then, then))
    (folder / "dir.link").mkdir()
    assert platform_paths.take_dropped_links(folder) == []
    left = sorted(p.name for p in folder.iterdir())
    # Not ours to delete: another name, a link being written, a folder.
    assert left == ["dir.link", "half.tmp", "notes.txt"]


def test_a_link_that_cant_be_deleted_is_read_once(tmp_path, monkeypatch):
    folder = tmp_path / "links"
    platform_paths.drop_link("thechatplace://session/abc", folder)

    def stuck(path):
        raise OSError("in use")
    monkeypatch.setattr(platform_paths.os, "remove", stuck)
    assert platform_paths.take_dropped_links(folder) == ["thechatplace://session/abc"]
    assert platform_paths.take_dropped_links(folder) == []
    assert platform_paths.take_dropped_links(folder) == []


def test_a_link_that_cant_be_claimed_is_left_unread(tmp_path, monkeypatch):
    folder = tmp_path / "links"
    platform_paths.drop_link("thechatplace://session/abc", folder)

    def refuse(source, target):
        raise OSError("in use")
    monkeypatch.setattr(platform_paths.os, "replace", refuse)
    assert platform_paths.take_dropped_links(folder) == []


def test_no_folder_no_links(tmp_path):
    assert platform_paths.take_dropped_links(tmp_path / "missing") == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX owners and modes")
def test_the_folder_is_private_on_a_mac(tmp_path):
    folder = tmp_path / "links"
    path = platform_paths.drop_link("thechatplace://session/abc", folder)
    assert folder.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600


def test_second_copy_hands_its_link_over(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path / "appdata")
    assert app.hand_link_to_running_copy("thechatplace://session/abc")
    assert platform_paths.take_dropped_links() == ["thechatplace://session/abc"]


def test_a_failed_hand_over_is_reported(tmp_path, monkeypatch):
    def refuse(text, folder=None):
        raise OSError("read-only")
    monkeypatch.setattr(platform_paths, "drop_link", refuse)
    assert not app.hand_link_to_running_copy("thechatplace://session/abc")


# -- Windows registration ---------------------------------------------------------------


def test_registry_command_quotes_the_program_and_the_link():
    assert links.scheme_command(r"C:\Users\k\AppData\Local\TheChatPlace\current\TheChatPlace.exe",
                                "win32") \
        == '"C:\\Users\\k\\AppData\\Local\\TheChatPlace\\current\\TheChatPlace.exe" "%1"'
    with pytest.raises(ValueError):
        links.scheme_command('C:\\a"b.exe', "win32")
    with pytest.raises(ValueError):
        links.scheme_command("", "win32")


def test_a_linux_desktop_entry_runs_the_appimage_with_the_link():
    assert links.scheme_command("/home/k/Apps/TheChatPlace.AppImage", "linux") \
        == "/home/k/Apps/TheChatPlace.AppImage %u"
    # Quoted only where it has to be: xdg-open can't run a quoted program.
    assert links.scheme_command("/home/k/My Apps/TheChatPlace.AppImage", "linux") \
        == '"/home/k/My Apps/TheChatPlace.AppImage" %u'
    # What an Exec line would need escaped is refused rather than escaped.
    for bad in ('/home/k/a"b.AppImage', "/home/k/$HOME.AppImage", "/home/k/a`b`.AppImage",
                "/home/k/a\\b.AppImage", "/home/k/100%.AppImage", ""):
        with pytest.raises(ValueError):
            links.scheme_command(bad, "linux")


def test_only_an_appimage_registers_on_linux():
    writes = []
    exe = "/home/k/Apps/TheChatPlace.AppImage"
    write = lambda command, icon: writes.append((command, icon))  # noqa: E731
    assert links.ensure_registered(False, exe, lambda: None, write, "linux") == "not installed"
    assert links.ensure_registered(True, exe, lambda: None, write, "linux") == "registered"
    assert writes == [(f"{exe} %u", "")]
    assert links.ensure_registered(True, exe, lambda: f"{exe} %u", write, "linux") == "current"
    assert len(writes) == 1


def test_only_an_installed_windows_copy_registers():
    writes = []
    exe = r"C:\Apps\TheChatPlace\current\TheChatPlace.exe"
    write = lambda command, icon: writes.append((command, icon))  # noqa: E731
    assert links.ensure_registered(False, exe, lambda: None, write, "win32") == "not installed"
    assert links.ensure_registered(True, exe, lambda: None, write, "darwin") == "not installed"
    assert writes == []
    assert links.ensure_registered(True, exe, lambda: None, write, "win32") == "registered"
    assert writes == [(f'"{exe}" "%1"', f'"{exe}",0')]
    # Already right: left alone. Pointing elsewhere (an old install): put right.
    assert links.ensure_registered(True, exe, lambda: f'"{exe}" "%1"', write, "win32") == "current"
    assert links.ensure_registered(True, exe, lambda: f'"{exe.lower()}" "%1"', write,
                                   "win32") == "current"
    assert len(writes) == 1
    assert links.ensure_registered(True, exe, lambda: '"C:\\old.exe" "%1"', write,
                                   "win32") == "registered"
    assert len(writes) == 2


def test_a_registry_that_refuses_is_reported_not_raised():
    def refuse(command, icon):
        raise OSError("access denied")
    assert links.ensure_registered(True, "C:\\a.exe", lambda: None, refuse,
                                   "win32").startswith("failed: access denied")
    assert links.ensure_registered(True, 'C:\\"a.exe', lambda: None, refuse,
                                   "win32").startswith("failed")


def test_uninstall_removes_only_this_copys_registration():
    removed = []
    exe = r"C:\Apps\TheChatPlace\current\TheChatPlace.exe"
    assert links.remove_registration(exe, lambda: f'"{exe.upper()}" "%1"',
                                     lambda: removed.append(1), "win32")
    assert not links.remove_registration(exe, lambda: '"C:\\Other\\x.exe" "%1"',
                                         lambda: removed.append(2), "win32")
    assert not links.remove_registration(exe, lambda: None, lambda: removed.append(3), "win32")
    assert removed == [1]


def test_installed_copy_check(monkeypatch):
    class Manager:
        def __init__(self, portable):
            self.portable = portable

        def get_is_portable(self):
            return self.portable
    assert updater.is_installed_copy("0.1.4", lambda url: Manager(False), frozen=True)
    assert not updater.is_installed_copy("0.1.4", lambda url: Manager(True), frozen=True)
    assert not updater.is_installed_copy("0.1.4", lambda url: Manager(False), frozen=False)

    def broken(url):
        raise RuntimeError("not installed")
    assert not updater.is_installed_copy("0.1.4", broken, frozen=True)


def test_source_runs_never_register(monkeypatch):
    started = []
    monkeypatch.setattr(app.threading, "Thread", lambda *a, **k: started.append(k) or
                        types.SimpleNamespace(start=lambda: None))
    monkeypatch.delattr(sys, "frozen", raising=False)
    app.register_links_in_background()
    assert started == []


class FakeVelopackApp:
    made = []

    def __init__(self):
        self.calls = []
        FakeVelopackApp.made.append(self)

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, args))
            return self
        return call


@pytest.fixture
def fake_velopack(monkeypatch):
    FakeVelopackApp.made = []
    monkeypatch.setitem(sys.modules, "velopack", types.SimpleNamespace(App=FakeVelopackApp))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater, "mac_update_blocker", lambda: "")
    return FakeVelopackApp


def test_velopack_never_reads_a_link_as_a_hook(fake_velopack):
    updater.bootstrap(['thechatplace://session/x', "--veloapp-uninstall"])
    calls = [name for name, _args in fake_velopack.made[0].calls]
    assert ("set_args", ([],)) in fake_velopack.made[0].calls
    assert calls[-1] == "run"
    updater.bootstrap(["--veloapp-install", "1.0.0"])
    assert "set_args" not in [name for name, _args in fake_velopack.made[1].calls]


@pytest.mark.skipif(sys.platform != "win32", reason="Velopack's hooks are Windows-only")
def test_install_and_uninstall_hooks_register_and_remove(fake_velopack, monkeypatch):
    updater.bootstrap([])
    hooks = {name: args[0] for name, args in fake_velopack.made[0].calls if args}
    done = []
    monkeypatch.setattr(links, "ensure_registered",
                        lambda installed, exe: done.append(("register", installed)) or "ok")
    monkeypatch.setattr(links, "remove_registration",
                        lambda exe: done.append(("remove",)) or True)
    hooks["on_after_install_fast_callback"]("0.1.4")
    hooks["on_after_update_fast_callback"]("0.1.5")
    hooks["on_before_uninstall_fast_callback"]("0.1.5")
    assert done == [("register", True), ("register", True), ("remove",)]

    def broken(*a):
        raise OSError("no")
    monkeypatch.setattr(links, "ensure_registered", broken)
    hooks["on_after_install_fast_callback"]("0.1.4")  # logged, never raised
