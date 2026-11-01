"""Chat Options and Ask Aside (#468) in the window, on a frame that is never
shown, with speech recorded and no real Claude Code."""
import pytest

wx = pytest.importorskip("wx")

from thechatplace import platform_paths  # noqa: E402
from thechatplace.aside import AsideEvent  # noqa: E402
from thechatplace.chat_options import ChatOptions  # noqa: E402
from thechatplace.own_store import OwnSession, OwnSessionStore  # noqa: E402

from records import assistant_block, text_block, user_text  # noqa: E402
import fake_env  # noqa: E402
from fake_env import FakeRunner, add_desktop, add_transcript, now_ms, pump  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch, app):
    return fake_env.install(tmp_path, monkeypatch.setattr)


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "Work"
    path.mkdir()
    return str(path)


@pytest.fixture
def frame(env, folder, monkeypatch):
    from thechatplace.ui import main_frame
    from thechatplace.ui.main_frame import MainFrame
    FakeRunner.instances = []
    monkeypatch.setattr(main_frame, "TurnRunner", FakeRunner)
    monkeypatch.setattr(platform_paths, "find_claude",
                        lambda: platform_paths.ClaudeLookup("claude.exe"))
    add_desktop(env, "local_a", "cli-a", "Desktop one", cwd=folder)
    add_transcript(env, folder, "cli-a", [user_text("Which file?"),
                                          assistant_block(text_block("main.py"), "m1")])
    add_transcript(env, folder, "own-1", [user_text("Hello"),
                                          assistant_block(text_block("Hi"), "m1")])
    store = OwnSessionStore(env["tmp"] / "own.json")
    store.add(OwnSession("own-1", "Mine", folder, last_activity_ms=now_ms()))
    window = MainFrame(store=store, check_updates_at_start=False)
    assert pump(lambda: window.session_list.GetCount() == 2)
    yield window
    window._runners.clear()
    window.stop_timers()
    window._pool.shutdown(wait=True)
    wx.GetApp().ProcessPendingEvents()
    window.Destroy()
    wx.GetApp().ProcessPendingEvents()


def select(frame, title):
    for i in range(frame.session_list.GetCount()):
        if frame.session_list.GetString(i).startswith(title):
            frame.session_list.SetSelection(i)
            frame.session_list.SetFocus()
            return
    raise AssertionError(f"{title} not in list")


# -- New Session and the Chat Options dialog ------------------------------------------------


def test_new_session_has_chat_options_for_claude_only(frame, folder):
    from thechatplace.ui.dialogs import NewSessionDialog
    dialog = NewSessionDialog(frame, folder, providers=("claude", "codex"), provider="claude")
    try:
        assert dialog.chat_options_btn.IsShown()
        assert dialog.chat_options_btn.GetLabel() == "C&hat Options..."
        assert dialog.chat_options_summary.GetLabel() == "None chosen"
        dialog._chat_options = ChatOptions(agent="Explore")
        dialog._chat_folders = [folder]
        assert dialog.chat_options() == (ChatOptions(agent="Explore"), [folder])
        dialog.provider_choice.SetSelection(1)
        dialog._on_provider()
        assert not dialog.chat_options_btn.IsShown()
        assert dialog.chat_options() == (ChatOptions(), [])  # never for Codex
        order = list(dialog.GetChildren())
        assert order.index(dialog.mode) < order.index(dialog.chat_options_btn) < \
            order.index(dialog.message)
    finally:
        dialog.Destroy()


def test_chat_options_dialog_lists_the_folders_agents_and_styles(frame, folder, tmp_path):
    from thechatplace.ui.dialogs import ChatOptionsDialog
    said = []
    dialog = ChatOptionsDialog(frame, "Mine", folder,
                               options=ChatOptions("gone-agent", "Be brief.", ""),
                               load_choices=frame._load_chat_choices, feedback=said.append)
    try:
        # Before Claude Code answers: the chat's own agent is still there.
        assert dialog.agent.GetStringSelection() == "gone-agent"
        assert pump(lambda: dialog.agent.GetCount() > 2)
        assert list(dialog.agent.GetStrings()) == [
            "None (Claude Code's default)", "code-reviewer: Independent review of a change.",
            "Explore: Read-only search agent.", "gone-agent (not found in this folder)"]
        assert dialog.agent.GetStringSelection() == "gone-agent (not found in this folder)"
        assert list(dialog.style.GetStrings()) == [
            "Your setting (Default)", "Default", "Concise", "Explanatory"]
        dialog.agent.SetSelection(1)
        dialog.style.SetSelection(2)
        dialog.instructions.SetValue("  Answer in French.\r\nShort.  ")
        other = tmp_path / "Other"
        other.mkdir()
        assert dialog.add_folder(str(other))
        assert not dialog.add_folder(str(other))  # already there
        assert not dialog.add_folder(folder)      # the chat's own
        assert not dialog.add_folder(str(tmp_path / "missing"))
        assert dialog.options() == ChatOptions("code-reviewer", "Answer in French.\nShort.",
                                               "Concise")
        assert dialog.chosen_folders() == [str(other)]
        dialog.remove_folder()
        assert dialog.chosen_folders() == [] and not dialog.remove_folder_btn.IsEnabled()
        assert said[-1].startswith("Removed ") and said[-1].endswith("None left.")
    finally:
        dialog.Destroy()


def test_new_session_starts_with_its_chat_options(frame, env, folder, tmp_path, monkeypatch):
    from thechatplace.ui import main_frame
    other = str(tmp_path)

    class Fills(main_frame.NewSessionDialog):
        def ShowModal(self):
            self.set_folder(folder)
            self.message.SetValue("Review this")
            self._chat_options = ChatOptions("code-reviewer", "Be strict.", "Concise")
            self._chat_folders = [other]
            return wx.ID_OK
    monkeypatch.setattr(main_frame, "NewSessionDialog", Fills)
    frame.on_new_session()
    command = FakeRunner.instances[-1].command
    assert command[command.index("--agent") + 1] == "code-reviewer"
    assert "--append-system-prompt=Be strict." in command
    assert command[command.index("--settings") + 1] == '{"outputStyle": "Concise"}'
    assert command[command.index("--add-dir") + 1] == other
    assert "--system-prompt-snapshot" not in command
    session_id = command[command.index("--session-id") + 1]
    own = frame.store.get(session_id)
    assert own.chat_options() == ChatOptions("code-reviewer", "Be strict.", "Concise")
    assert own.allowed_dirs == [other] and own.allowed_dirs_at[other] > 0
    assert frame.session_heading.GetLabel().endswith(
        "Chat Place session on the default model, as code-reviewer.")


def test_continue_here_carries_its_chat_options(frame, folder, tmp_path, monkeypatch):
    from thechatplace.ui import main_frame

    class Fills(main_frame.NewSessionDialog):
        def ShowModal(self):
            self.message.SetValue("Carry on")
            self._chat_options = ChatOptions(instructions="Keep going.")
            return wx.ID_OK
    monkeypatch.setattr(main_frame, "NewSessionDialog", Fills)
    select(frame, "Desktop one")
    frame.on_continue_here()
    command = FakeRunner.instances[-1].command
    assert "--fork-session" in command and "--append-system-prompt=Keep going." in command


# -- File, Chat Options ---------------------------------------------------------------------


class _Chosen:
    """Stands in for ChatOptionsDialog: OK with these choices."""
    result = (ChatOptions(), [])
    seen = {}

    def __init__(self, parent, title, folder, **kwargs):
        _Chosen.seen = dict(kwargs, title=title, folder=folder)

    def ShowModal(self):
        return wx.ID_OK

    def options(self):
        return _Chosen.result[0]

    def chosen_folders(self):
        return list(_Chosen.result[1])

    def Destroy(self):
        pass


def test_chat_options_change_a_chat_from_its_next_turn(frame, env, tmp_path, monkeypatch):
    from thechatplace.ui import claude_extras
    monkeypatch.setattr(claude_extras, "ChatOptionsDialog", _Chosen)
    other = str(tmp_path)
    _Chosen.result = (ChatOptions("Explore", "Only read.", ""), [other])
    select(frame, "Mine")
    frame.on_chat_options()
    assert _Chosen.seen["existing"] and _Chosen.seen["title"] == "Mine"
    own = frame.store.get("own-1")
    # The chat has started, so its recorded system prompt is made afresh.
    assert own.chat_options() == ChatOptions("Explore", "Only read.", "", fresh=True)
    assert own.allowed_dirs == [other]
    assert env["feedback"][-1] == ("Mine: agent Explore, new instructions, 1 more folder, "
                                   "from its next message.")
    frame.on_open_session()
    frame.reply_text.SetValue("next")
    frame.on_send()
    command = FakeRunner.instances[-1].command
    assert command[command.index("--agent") + 1] == "Explore"
    assert command[command.index("--system-prompt-snapshot") + 1] == "off"
    assert command[-2:] == ["--resume", "own-1"]
    # Unchanged is said, and nothing is written.
    frame.on_chat_options()
    assert env["feedback"][-1] == "Chat options unchanged."


def test_chat_options_only_for_chats_started_here(frame, env, monkeypatch):
    from thechatplace.ui import claude_extras
    monkeypatch.setattr(claude_extras, "ChatOptionsDialog",
                        lambda *a, **k: pytest.fail("must not open"))
    select(frame, "Desktop one")
    frame.on_chat_options()
    assert "Desktop one is" in env["feedback"][-1]


def test_chat_options_are_in_the_file_menu_and_ask_aside_has_its_key(frame):
    labels = [item.GetItemLabel() for item in frame.GetMenuBar().GetMenu(0).GetMenuItems()]
    assert "Chat Options..." in labels
    assert "Ask Aside...\tCtrl+Shift+Y" in labels


# -- Ask Aside ------------------------------------------------------------------------------


class FakeAside:
    instances = []

    def __init__(self, command, cwd, question, on_event):
        self.command, self.cwd, self.question, self.on_event = command, cwd, question, on_event
        self.cancelled = False
        FakeAside.instances.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True
        self.on_event(AsideEvent("failed", "Stopped."))


def _drive_aside(frame, monkeypatch, steps):
    """Open Ask Aside, with ``steps(dialog)`` run instead of showing it."""
    from thechatplace.ui import claude_extras, main_frame
    FakeAside.instances = []
    monkeypatch.setattr(main_frame, "AsideRunner", FakeAside)
    seen = {}

    def show(dialog):
        seen["title"] = dialog.GetTitle()
        steps(dialog)
        return wx.ID_CANCEL
    monkeypatch.setattr(claude_extras.AskAsideDialog, "ShowModal", show)
    frame.on_ask_aside()
    return seen


def test_ask_aside_asks_a_read_only_copy_and_says_the_answer(frame, env, folder, monkeypatch):
    copied = []
    monkeypatch.setattr(type(frame), "_copy_text", lambda self, text: copied.append(text) or True)

    def steps(dialog):
        dialog.question.SetValue("Which file did you change?")
        dialog.on_ask()
        assert dialog.asking and dialog.ask_btn.GetLabel() == "&Stop"
        assert env["feedback"][-1] == "Asking Claude."
        runner = FakeAside.instances[-1]
        runner.on_event(AsideEvent("text", "main"))
        assert pump(lambda: dialog.history.GetString(0) == "Q: Which file did you change? "
                                                           "A: main")
        runner.on_event(AsideEvent("done", "main.py, line 4."))
        assert pump(lambda: not dialog.asking)
        assert dialog.answer.GetValue() == ("Question: Which file did you change?\n\n"
                                            "Answer:\nmain.py, line 4.")
        assert dialog.ask_btn.GetLabel() == "&Ask" and dialog.question.GetValue() == ""
        dialog.on_copy()
    select(frame, "Mine")
    seen = _drive_aside(frame, monkeypatch, steps)
    assert seen["title"] == "Ask Aside about Mine"
    runner = FakeAside.instances[-1]
    assert runner.question == "Which file did you change?" and runner.cwd == folder
    assert runner.command[-4:] == ["--resume", "own-1", "--fork-session",
                                   "--no-session-persistence"]
    assert env["spoken"][-1] == "Answer. main.py, line 4."
    assert copied == ["main.py, line 4."]
    assert FakeRunner.instances == []  # no turn of the chat's own


def test_ask_aside_reads_a_desktop_session_without_resuming_it(frame, env, monkeypatch):
    select(frame, "Desktop one")
    _drive_aside(frame, monkeypatch, lambda d: (d.question.SetValue("q"), d.on_ask()))
    command = FakeAside.instances[-1].command
    assert command[command.index("--resume") + 1:] == ["cli-a", "--fork-session",
                                                       "--no-session-persistence"]


def test_escape_while_asking_stops_and_keeps_the_dialog(frame, env, monkeypatch):
    def steps(dialog):
        dialog.question.SetValue("long one")
        dialog.on_ask()
        dialog.on_close()  # Escape, while asking
        assert FakeAside.instances[-1].cancelled
        assert pump(lambda: not dialog.asking)
        assert env["feedback"][-1] == "Stopped asking."
        assert dialog.history.GetString(0) == "Q: long one A: not answered: Stopped."
    select(frame, "Mine")
    _drive_aside(frame, monkeypatch, steps)


def test_ask_aside_needs_a_question_and_says_failures(frame, env, monkeypatch):
    def steps(dialog):
        dialog.on_ask()
        assert env["feedback"][-1] == "Type a question first." and not FakeAside.instances
        dialog.question.SetValue("q")
        dialog.on_ask()
        FakeAside.instances[-1].on_event(AsideEvent("failed", "Claude Code is too old."))
        assert pump(lambda: not dialog.asking)
    select(frame, "Mine")
    _drive_aside(frame, monkeypatch, steps)
    assert env["spoken"][-1] == "Not answered. Claude Code is too old."


def test_ask_aside_is_refused_where_it_cant_work(frame, env, monkeypatch):
    from thechatplace.ui import claude_extras
    monkeypatch.setattr(claude_extras.AskAsideDialog, "ShowModal",
                        lambda self: pytest.fail("must not open"))
    frame.store.add(OwnSession("own-2", "Not yet", frame.store.get("own-1").cwd,
                               started=False))
    frame.refresh_sessions(force=True)
    assert pump(lambda: frame.session_list.GetCount() == 3)
    select(frame, "Not yet")
    frame.on_ask_aside()
    assert env["feedback"][-1] == "Not yet has no conversation yet to ask about."
