"""Linux: where things are, the terminal window, the desktop entry, speech
through speech-dispatcher, and what Orca reads (ATK). Most of it is plain
logic and runs everywhere; reading names back through ATK needs Linux."""
import os
import stat
import sys

import pytest

from thechatplace import platform_paths, speech
from thechatplace.ui import gtk_a11y

from markers import atk


# -- the terminal window (Claude Code Sign-in, installing Claude Code) --------------------

def found(*names):
    return lambda name: f"/usr/bin/{name}" if name in names else None


def test_a_screen_reader_friendly_terminal_comes_before_xterm():
    command = platform_paths.linux_terminal("/s.sh", found("xterm", "gnome-terminal"), "GNOME")
    assert command == ["/usr/bin/gnome-terminal", "--", "/s.sh"]
    assert platform_paths.linux_terminal("/s.sh", found("xterm"), "") == ["/usr/bin/xterm", "-e",
                                                                          "/s.sh"]
    assert platform_paths.linux_terminal("/s.sh", found(), "") is None


def test_the_desktops_own_terminal_comes_first():
    which = found("gnome-terminal", "konsole", "mate-terminal")
    assert platform_paths.linux_terminal("/s.sh", which, "KDE")[0] == "/usr/bin/konsole"
    assert platform_paths.linux_terminal("/s.sh", which, "MATE")[:2] == ["/usr/bin/mate-terminal",
                                                                         "-x"]
    # The user's own choice, where xdg-terminal-exec is set up, before all of them.
    assert platform_paths.linux_terminal(
        "/s.sh", found("xdg-terminal-exec", "konsole"), "KDE") == ["/usr/bin/xdg-terminal-exec",
                                                                   "/s.sh"]


def test_a_linux_terminal_window_waits_to_be_read(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(platform_paths.sys, "platform", "linux")
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(platform_paths, "linux_terminal",
                        lambda script: ["/usr/bin/gnome-terminal", "--", script])
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: started.append(argv))
    assert platform_paths.run_in_terminal(["/home/k/.local/bin/claude", "auth", "login"],
                                          "sign-in.command", "Signing in.",
                                          clear=["ANTHROPIC_API_KEY"]) is None
    script = (tmp_path / "sign-in.command").read_text()
    assert started == [["/usr/bin/gnome-terminal", "--", str(tmp_path / "sign-in.command")]]
    assert "unset ANTHROPIC_API_KEY" in script
    assert "/home/k/.local/bin/claude auth login" in script
    # Linux terminals close when the command ends: the result waits for Enter.
    assert script.rstrip().endswith("read -r -p 'Press Enter to close this window.' _")


def test_no_terminal_says_so_and_what_to_run(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_paths.sys, "platform", "linux")
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(platform_paths, "linux_terminal", lambda script: None)
    with pytest.raises(OSError, match="claude auth login"):
        platform_paths.run_in_terminal(["claude", "auth", "login"], "s.command", "Signing in.")


# -- processes, from /proc ----------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX owners")
def test_processes_are_this_users_only(tmp_path, monkeypatch):
    for pid, name in (("10", "orca"), ("11", "bash"), ("12", "orca")):
        (tmp_path / pid).mkdir()
        (tmp_path / pid / "comm").write_text(name + "\n")
    (tmp_path / "stat").write_text("cpu 1 2 3\n")
    (tmp_path / "self").mkdir()
    real_stat = os.stat

    class Owner:
        def __init__(self, uid):
            self.st_uid = uid
    # Process 12 is someone else's: another user's Orca can't speak to us.
    monkeypatch.setattr(os, "stat", lambda path, *a, **k: Owner(-2 if str(path).endswith("/12")
                                                                else os.getuid())
                        if str(path).startswith(str(tmp_path)) else real_stat(path, *a, **k))
    assert platform_paths._linux_processes(str(tmp_path)) == {10: "orca", 11: "bash"}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX owners")
def test_a_process_start_is_on_claude_codes_clock(tmp_path):
    (tmp_path / "42").mkdir()
    # Field 22 (starttime) is 500 ticks after boot; the name has a space and a bracket.
    fields = ["S"] + ["0"] * 18 + ["500"] + ["0"] * 10
    (tmp_path / "42" / "stat").write_text("42 (claude (x)) " + " ".join(fields) + "\n")
    (tmp_path / "stat").write_text("cpu 1\nbtime 1790000000\n")
    ticks = os.sysconf("SC_CLK_TCK")
    expected = int((1790000000 + 500 / ticks + 11_644_473_600) * 10_000_000)
    assert platform_paths._linux_process_start(42, str(tmp_path)) == expected
    assert platform_paths._linux_process_start(43, str(tmp_path)) is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX owners")
def test_a_linux_pid_file_is_matched_to_its_process(tmp_path):
    """Claude Code 2.1.302 on Linux and the Mac writes procStart as
    ``LC_ALL=C TZ=UTC ps -o lstart= -p <pid>``, not a FILETIME as on
    Windows. A pid file shaped like one (made-up values) matches the process
    that started that second, and not one that reused its pid later."""
    import calendar
    import json
    from thechatplace.sessions import load_live_status

    proc, sessions = tmp_path / "proc", tmp_path / "sessions"
    sessions.mkdir()
    ticks = os.sysconf("SC_CLK_TCK")
    boot = calendar.timegm((2026, 10, 26, 9, 0, 0, 0, 0, 0))
    (proc / "stat").parent.mkdir()
    (proc / "stat").write_text(f"cpu 1\nbtime {boot}\n")

    def process(pid, seconds_after_boot):
        (proc / str(pid)).mkdir()
        fields = ["S"] + ["0"] * 18 + [str(int(seconds_after_boot * ticks))] + ["0"] * 10
        (proc / str(pid) / "stat").write_text(f"{pid} (claude) " + " ".join(fields) + "\n")

    def pid_file(pid, session):
        (sessions / f"{pid}.json").write_text(json.dumps({
            "pid": pid, "sessionId": session, "status": "busy", "kind": "interactive",
            "procStart": "Mon Oct 26 16:29:20 2026", "startedAt": (boot + 26960) * 1000 + 900}))
    seven_hours_29_20 = 7 * 3600 + 29 * 60 + 20
    process(100, seven_hours_29_20 + 0.4)  # the Claude Code that wrote it
    process(200, seven_hours_29_20 + 12)   # its pid, reused 12 seconds later
    pid_file(100, "cli-1")
    pid_file(200, "cli-2")
    live = load_live_status(sessions, alive=lambda pid: True,
                            started=lambda pid: platform_paths._linux_process_start(pid, str(proc)))
    assert set(live) == {"cli-1"}


# -- a built copy's children get the system's libraries ------------------------------------

def test_the_bundled_library_path_isnt_passed_on(monkeypatch):
    monkeypatch.setattr(platform_paths.sys, "platform", "linux")
    monkeypatch.setattr(platform_paths.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/lib")
    platform_paths.restore_system_library_path()
    assert os.environ["LD_LIBRARY_PATH"] == "/opt/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in os.environ
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    platform_paths.restore_system_library_path()  # none before: none after
    assert "LD_LIBRARY_PATH" not in os.environ


# -- the desktop entry: the menu, and thechatplace:// links ---------------------------------

def test_the_desktop_entry_runs_the_appimage_with_the_link(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(platform_paths, "find_tool", lambda name: None)
    command = '"/home/k/Apps/TheChatPlace.AppImage" %u'
    platform_paths.write_desktop_entry(command)
    entry = tmp_path / "applications" / "thechatplace.desktop"
    text = entry.read_text()
    assert f"Exec={command}\n" in text
    assert "MimeType=x-scheme-handler/thechatplace;\n" in text
    assert "StartupWMClass=thechatplace\n" in text
    assert (tmp_path / "icons" / "hicolor" / "512x512" / "apps" / "thechatplace.png").is_file()
    assert platform_paths._desktop_entry_exec(entry) == command
    monkeypatch.setattr(platform_paths.sys, "platform", "linux")
    assert platform_paths.url_scheme_command() == command
    platform_paths.unregister_url_scheme()
    assert not entry.exists() and platform_paths.url_scheme_command() is None


def test_the_desktop_entry_is_made_the_links_handler(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(platform_paths, "find_tool", lambda name: f"/usr/bin/{name}")
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: ran.append(argv))
    platform_paths.write_desktop_entry('"/a/TheChatPlace.AppImage" %u')
    assert ["/usr/bin/xdg-mime", "default", "thechatplace.desktop",
            "x-scheme-handler/thechatplace"] in ran


# -- speech: speech-dispatcher voices ------------------------------------------------------

SPD_LIST = """\
                     NAME                 LANGUAGE                  VARIANT
                Afrikaans                       af                     none
        English (America)                    en-US                     none
   English (America)+Alex                    en-US                     Alex
   English (Great Britain)                   en-GB                     none
                   German                       de                     none
"""


def test_speech_dispatcher_voices_in_your_language_without_variants():
    options = speech._parse_speechd_voices(SPD_LIST, "en")
    assert [o.voice for o in options] == ["English (America)", "English (Great Britain)"]
    assert all(o.engine == "speechd" and o.has_rate for o in options)
    assert [o.voice for o in speech._parse_speechd_voices(SPD_LIST, "de")] == ["German"]


def test_the_voice_language_is_yours_or_english(monkeypatch):
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    assert speech._voice_language() == "en"
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    assert speech._voice_language() == "de"
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    assert speech._voice_language() == "en"


def test_linux_offers_orca_and_speech_dispatcher(monkeypatch):
    monkeypatch.setattr(speech.sys, "platform", "linux")
    monkeypatch.setattr(speech, "IS_LINUX", True)
    engines = [o.engine for o in speech.default_options()]
    assert engines == ["auto", "orca", "speechd"]
    assert speech.engine_script_names() == ["speak-engine-linux.sh"]
    assert (speech._script_dir() / "speak-engine-linux.sh").is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="the file mode is POSIX's")
def test_the_linux_speech_script_can_be_run():
    script = speech._script_dir() / "speak-engine-linux.sh"
    assert script.stat().st_mode & stat.S_IXUSR
    assert script.read_bytes().startswith(b"#!/usr/bin/env bash\n")


# -- what Orca reads for a message row (#11) -----------------------------------------------

def test_a_rows_description_is_the_rest_of_its_message():
    rest = gtk_a11y.rest_of
    assert rest("You: first line", "You: first line, and the rest.") == "and the rest."
    # The row shows the first line as written; the spoken text has it as words.
    assert rest("Claude: ## Plan", "Claude: Plan. Step one.") == "Step one."
    # Any provider's name, and an agent conversation's role after it.
    assert rest("Codex: ## Plan", "Codex: Plan. Step one.") == "Step one."
    assert rest("GitHub Copilot: Done…", "GitHub Copilot: Done. Tests pass.") == "Tests pass."
    assert rest("Codex (reviewer): Looks right", "Codex (reviewer): Looks right. One nit.") == \
        "One nit."
    # The word the row cuts is the first one the description says.
    assert rest("Claude: abc…", "Claude: abcdef more") == "abcdef more"
    # A "…" after a whole word cuts nothing.
    assert rest("Claude: The plan has three steps…",
                "Claude: The plan has three steps and more") == "and more"
    assert rest("Claude: Let me think…", "Claude: Let me think… Now, step one") == "Now, step one"
    assert rest("You: hello", "You: hello") == ""
    assert rest("Tool: x", "Claude: other") == "Claude: other"  # no match: all of it
    assert rest("Loading messages…", "") == ""


def test_off_linux_nothing_is_touched():
    if sys.platform.startswith("linux"):
        pytest.skip("this is Linux")
    assert gtk_a11y.set_label(object(), "x") is False
    assert gtk_a11y.set_role(object(), "label") is False
    assert gtk_a11y.announce(object(), "x") is False
    assert gtk_a11y.forget_labels(object()) == 0
    assert gtk_a11y.focus_web_view(object()) is False


# -- ATK itself (Linux) --------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    wx = pytest.importorskip("wx")
    yield wx.App(False)


@atk
def test_a_list_is_named_where_orca_lands(app):
    import wx
    from thechatplace.ui.a11y import set_accessible_name
    frame = wx.Frame(None)
    try:
        listbox = wx.ListBox(frame, choices=["One"])
        set_accessible_name(listbox, "Session list")
        # The tree view inside the scrolled window, not the scrolled window.
        assert gtk_a11y.get_label(listbox) == "Session list"
        text = wx.TextCtrl(frame, style=wx.TE_MULTILINE)
        set_accessible_name(text, "Your message")
        assert gtk_a11y.get_label(text) == "Your message"
    finally:
        frame.Destroy()


@atk
def test_the_selected_row_gets_the_rest_of_its_message(app):
    import wx
    from thechatplace.ui.a11y import refresh_list_item, set_list_items_accessible
    frame = wx.Frame(None)
    try:
        listbox = wx.ListBox(frame, choices=["You: first", "Claude: ## Plan"])
        full = ["You: first, and more.", "Claude: Plan. Step one."]
        set_list_items_accessible(listbox, lambda: "Messages in Garden",
                                  lambda index: full[index])
        assert gtk_a11y.get_label(listbox) == "Messages in Garden"
        listbox.SetSelection(1)
        refresh_list_item(listbox)
        assert gtk_a11y.get_row_description(listbox, 1) == "Step one."
    finally:
        frame.Destroy()


@atk
def test_status_bar_parts_are_labels_in_a_status_bar(app):
    import wx
    from thechatplace.ui.statusbar import StatusParts
    frame = wx.Frame(None, size=(800, 300))
    try:
        bar = frame.CreateStatusBar(1)
        parts = StatusParts(bar)
        text = parts.add_text("message", "Status", -3, "Ready.")
        parts.set("message", "Loaded Garden, 4 messages.")
        assert gtk_a11y.get_role(text) == gtk_a11y.role_number("label")
        assert gtk_a11y.get_label(text) == "Loaded Garden, 4 messages."
        assert gtk_a11y.get_role(bar) == gtk_a11y.role_number("statusbar")
        assert bar.GetStatusText(0) == ""  # the field would show through a button
    finally:
        frame.Destroy()
