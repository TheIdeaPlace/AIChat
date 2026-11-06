"""Chat Options and Ask Aside (#468) in the window, on a frame that is never
shown, with speech recorded and no real Claude Code."""
import pytest

wx = pytest.importorskip("wx")

from thechatplace import platform_paths  # noqa: E402
from thechatplace.aside import AsideEvent  # noqa: E402
from thechatplace.chat_options import ChatOptions  # noqa: E402
from thechatplace.own_store import OwnSession, OwnSessionStore  # noqa: E402

from markers import msaa  # noqa: E402
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


@pytest.mark.parametrize("provider", ["claude", "codex", "copilot"])
def test_continue_here_hidden_controls_dont_answer_their_keys(frame, folder, provider):
    # On Windows a hidden control still answers its Alt key: Alt+B and Alt+G
    # opened the folder chooser and From GitHub on a dialog whose folder is
    # fixed, and for Codex and Copilot Alt+H opened Claude's Chat Options.
    from thechatplace.ui.dialogs import NewSessionDialog
    dialog = NewSessionDialog(frame, folder, continue_from="Desk", providers=(provider,),
                              provider=provider)
    try:
        for control in (dialog.browse_btn, dialog.github_btn, dialog.work_in_label,
                        dialog.work_in, dialog.branch_label, dialog.branch):
            assert not control.IsShown() and not control.IsEnabled()
        claude = provider == "claude"
        assert dialog.chat_options_btn.IsShown() == claude
        assert dialog.chat_options_btn.IsEnabled() == claude
        # A hidden label moves the focus on with its key: it has none to take.
        assert dialog.work_in_label.GetLabel() == "Work in:"
        assert dialog.branch_label.GetLabel() == "Branch (choose one, or type a new name):"
    finally:
        dialog.Destroy()


def test_new_session_disables_chat_options_along_with_hiding_it(frame, folder):
    from thechatplace.ui.dialogs import NewSessionDialog
    dialog = NewSessionDialog(frame, folder, providers=("claude", "codex", "copilot"),
                              provider="claude")
    try:
        for control in (dialog.browse_btn, dialog.github_btn, dialog.work_in):
            assert control.IsShown() and control.IsEnabled()
        assert dialog.work_in_label.GetLabel() == "Work &in:"
        assert dialog.branch_label.GetLabel().startswith("B&ranch")
        assert dialog.chat_options_btn.IsEnabled()
        for index, provider in [(1, "codex"), (2, "copilot"), (0, "claude"), (2, "copilot")]:
            dialog.provider_choice.SetSelection(index)
            dialog._on_provider()
            claude = provider == "claude"
            assert dialog.chat_options_btn.IsShown() == claude
            assert dialog.chat_options_btn.IsEnabled() == claude
            # These stay whichever provider it is.
            assert dialog.browse_btn.IsEnabled() and dialog.github_btn.IsEnabled()
    finally:
        dialog.Destroy()


@pytest.mark.parametrize("provider", ["codex", "copilot"])
def test_new_session_opened_on_another_provider_has_chat_options_disabled(frame, folder, provider):
    from thechatplace.ui.dialogs import NewSessionDialog
    dialog = NewSessionDialog(frame, folder, providers=("claude", "codex", "copilot"),
                              provider=provider)
    try:
        assert not dialog.chat_options_btn.IsShown()
        assert not dialog.chat_options_btn.IsEnabled()
    finally:
        dialog.Destroy()


def test_continue_heres_folder_box_grows_to_show_a_long_path(frame, folder):
    # With no scroll bar, a path that wraps needs the room to be seen in.
    from thechatplace.ui.dialogs import NewSessionDialog
    long_path = "C:\\" + "\\".join(["a-folder-with-a-long-name"] * 20)
    dialog = NewSessionDialog(frame, long_path, continue_from="Desk")
    try:
        one_line = dialog.title_text.GetBestSize().height
        char = dialog.folder.GetCharHeight()
        lines_needed = dialog.folder.GetTextExtent(long_path).width // (
            dialog.folder.GetClientSize().width)
        assert dialog.folder.GetMinSize().height >= one_line + lines_needed * char
        assert dialog.folder.GetValue() == long_path
    finally:
        dialog.Destroy()


def test_enter_in_continue_heres_folder_box_says_where_to_go(frame, folder, monkeypatch):
    from thechatplace.ui.dialogs import NewSessionDialog
    said = []
    dialog = NewSessionDialog(frame, folder, continue_from="Desk", feedback=said.append)

    def enter(focus):
        monkeypatch.setattr(dialog, "_focus", lambda: focus)
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(wx.WXK_RETURN)
        dialog._on_char_hook(event)
        return event.GetSkipped()
    try:
        assert not enter(dialog.folder)
        assert said == ["Tab to Title, or on to Start."]
        assert enter(dialog.title_text)
        assert len(said) == 1
    finally:
        dialog.Destroy()
    dialog = NewSessionDialog(frame, folder, feedback=said.append)
    try:
        assert enter(dialog.folder)  # New Session's box is typed in: Enter is Start's
        assert len(said) == 1
    finally:
        dialog.Destroy()


def _keys(dialog):
    return {name: label.GetLabel() for name, label in (
        ("model", dialog.model_label), ("effort", dialog.effort_label),
        ("mode", dialog.mode_label), ("branch", dialog.branch_label))}


def test_a_turned_off_setting_has_no_alt_key(frame, folder):
    # Alt+E and Alt+D on a disabled Effort and Model moved to Permission mode,
    # with real keys, under Copilot and under Codex while models load (#515).
    from thechatplace.ui.dialogs import NewSessionDialog
    dialog = NewSessionDialog(frame, folder, providers=("claude", "codex", "copilot"),
                              provider="claude")
    try:
        assert _keys(dialog)["model"] == "Mo&del:" and _keys(dialog)["effort"] == "&Effort:"
        assert _keys(dialog)["mode"] == "Permission m&ode:"
        dialog.provider_choice.SetSelection(2)  # Copilot, with no models known
        dialog._on_provider()
        assert not dialog.model.IsEnabled() and not dialog.effort_choice.IsEnabled()
        assert _keys(dialog)["model"] == "Model:" and _keys(dialog)["effort"] == "Effort:"
        assert _keys(dialog)["mode"] == "Permission m&ode:"  # agent or plan
        dialog.provider_choice.SetSelection(1)  # Codex, models not loaded
        dialog._on_provider()
        assert not dialog.model.IsEnabled() and not dialog.effort_choice.IsEnabled()
        assert _keys(dialog)["model"] == "Model:" and _keys(dialog)["effort"] == "Effort:"
        dialog.provider_choice.SetSelection(0)  # and back
        dialog._on_provider()
        assert _keys(dialog)["model"] == "Mo&del:" and _keys(dialog)["effort"] == "&Effort:"
    finally:
        dialog.Destroy()


def test_alt_keys_come_back_once_codexs_models_load(frame, folder):
    from types import SimpleNamespace
    from thechatplace.ui.dialogs import NewSessionDialog
    dialog = NewSessionDialog(frame, folder, providers=("codex",), provider="codex")
    try:
        assert _keys(dialog)["model"] == "Model:" and _keys(dialog)["effort"] == "Effort:"
        dialog._set_codex_models([SimpleNamespace(model="m1", label="M1", efforts=("low", "high"))],
                                 False)
        assert _keys(dialog)["model"] == "Mo&del:"
        assert _keys(dialog)["effort"] == "Effort:"  # the configured model has no list of its own
        dialog.model.SetSelection(1)
        dialog._on_codex_model()
        assert _keys(dialog)["effort"] == "&Effort:"
        dialog.model.SetSelection(0)
        dialog._on_codex_model()
        assert _keys(dialog)["effort"] == "Effort:"
    finally:
        dialog.Destroy()


def test_a_copilot_with_only_one_mode_has_no_key_for_it(frame, folder):
    from thechatplace.ui.dialogs import NewSessionDialog
    dialog = NewSessionDialog(frame, folder, providers=("copilot",), provider="copilot",
                              copilot_plan=False)
    try:
        assert not dialog.mode.IsEnabled()
        assert _keys(dialog)["mode"] == "Permission mode:"
    finally:
        dialog.Destroy()


def test_branch_has_its_alt_key_only_with_a_new_worktree(frame, folder):
    from types import SimpleNamespace
    from thechatplace.ui.dialogs import NewSessionDialog
    dialog = NewSessionDialog(frame, folder)
    dialog._repo_known = True
    dialog._repo = SimpleNamespace(branches=[], branch="main")
    try:
        dialog.work_in.SetSelection(0)  # the folder as it is
        dialog._on_work_in()
        assert not dialog.branch.IsEnabled()
        assert _keys(dialog)["branch"] == "Branch (choose one, or type a new name):"
        dialog.work_in.SetSelection(1)  # a new worktree
        dialog._on_work_in()
        assert _keys(dialog)["branch"] == "B&ranch (choose one, or type a new name):"
    finally:
        dialog.Destroy()


def test_continuing_has_no_alt_key_for_branch(frame, folder):
    from thechatplace.ui.dialogs import NewSessionDialog
    dialog = NewSessionDialog(frame, folder, continue_from="Desk")
    try:
        assert _keys(dialog)["branch"] == "Branch (choose one, or type a new name):"
        assert dialog.work_in_label.GetLabel() == "Work in:"
    finally:
        dialog.Destroy()


def test_continue_heres_folder_box_is_a_tab_stop_one_line_tall(frame, folder):
    # Tab went straight past a one-line read-only box; a multi-line one is
    # stopped on, as the app's other read-only boxes are.
    import os
    from thechatplace.ui.dialogs import NewSessionDialog
    # A short path, which fits on one line. A long one, like the test's own
    # folder, grows to show all of it: see the next check.
    dialog = NewSessionDialog(frame, os.path.abspath(os.sep), continue_from="Desk")
    try:
        assert dialog.folder.GetMinSize().height == dialog.title_text.GetBestSize().height
    finally:
        dialog.Destroy()
    long_path = os.path.join(folder, *["a folder with a long name"] * 12)
    dialog = NewSessionDialog(frame, long_path, continue_from="Desk")
    try:
        assert dialog.folder.GetMinSize().height > dialog.title_text.GetBestSize().height
    finally:
        dialog.Destroy()
    dialog = NewSessionDialog(frame, folder, continue_from="Desk")
    try:
        box = dialog.folder
        assert box.IsMultiLine() and not box.IsEditable()
        assert box.GetValue() == folder and box.GetName() == "Folder"
        # No scroll arrows crammed into the one line.
        assert box.HasFlag(wx.TE_NO_VSCROLL) and box.AcceptsFocusFromKeyboard()
    finally:
        dialog.Destroy()
    dialog = NewSessionDialog(frame, folder)
    try:
        assert isinstance(dialog.folder, wx.ComboBox) and dialog.folder.IsEditable()
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
            "None (Claude Code's default)",
            "code-reviewer (uses its own model, opus): Independent review of a change.",
            "Explore: Read-only search agent.", "gone-agent (not found in this folder)"]
        assert dialog.agent.GetStringSelection() == "gone-agent (not found in this folder)"
        assert list(dialog.style.GetStrings()) == [
            "Your setting (Default)", "Default", "Concise", "Explanatory"]
        dialog.agent.SetSelection(1)
        dialog.style.SetSelection(2)
        dialog.instructions.SetValue("  Answer in French.\nShort.  ")
        other = tmp_path / "Other"
        other.mkdir()
        assert dialog.add_folder(str(other))
        assert not dialog.add_folder(str(other))  # already there
        assert not dialog.add_folder(folder)      # the chat's own
        assert not dialog.add_folder(str(tmp_path / "missing"))
        assert dialog.options() == ChatOptions("code-reviewer", "Answer in French.\nShort.",
                                               "Concise")
        assert dialog.chosen_folders() == [str(other)]
        assert list(dialog.folders.GetStrings()) == [str(other)]
        dialog.remove_folder()
        assert dialog.chosen_folders() == [] and not dialog.remove_folder_btn.IsEnabled()
        assert said[-1].startswith("Removed ") and said[-1].endswith("None left.")
        # A row saying so, not an empty list read as "unknown" (#468).
        assert list(dialog.folders.GetStrings()) == ["No more folders."]
        dialog.remove_folder()
        assert said[-1] == "No more folders." and dialog.chosen_folders() == []
    finally:
        dialog.Destroy()


def _agent_dialog(frame, folder, said, agents, model=""):
    from thechatplace.chat_options import choices_from_init
    from thechatplace.ui.dialogs import ChatOptionsDialog
    return ChatOptionsDialog(frame, "Mine", folder, feedback=said.append, model=model,
                             choices=choices_from_init({"agents": agents}))


def test_ok_refuses_an_agent_whose_model_can_bill_credits(frame, folder):
    said = []
    dialog = _agent_dialog(frame, folder, said, [{"name": "wide", "model": "fable"},
                                                 {"name": "calm", "model": "sonnet"}])
    try:
        assert dialog.agent.GetString(1) == "wide (uses its own model, fable)"
        dialog.SetReturnCode(wx.ID_CANCEL)
        dialog.agent.SetSelection(1)
        dialog.on_ok()
        assert dialog.GetReturnCode() == wx.ID_CANCEL  # stays open
        assert said[-1].startswith("The agent wide uses its own model, fable")
        dialog.agent.SetSelection(2)
        dialog.on_ok()
        assert dialog.GetReturnCode() == wx.ID_OK
    finally:
        dialog.Destroy()


def test_ok_keeps_an_agent_on_the_model_the_chat_chose(frame, folder):
    said = []
    dialog = _agent_dialog(frame, folder, said, [{"name": "wide", "model": "fable"}],
                           model="fable")
    try:
        dialog.agent.SetSelection(1)
        dialog.on_ok()
        assert dialog.GetReturnCode() == wx.ID_OK and not said
    finally:
        dialog.Destroy()


def test_new_session_gives_chat_options_its_model(frame, folder, monkeypatch):
    from thechatplace.ui import dialogs
    from thechatplace.ui.dialogs import NewSessionDialog
    seen = {}

    class Opens(dialogs.ChatOptionsDialog):
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)
            super().__init__(*args, **kwargs)

        def ShowModal(self):
            return wx.ID_CANCEL
    monkeypatch.setattr(dialogs, "ChatOptionsDialog", Opens)
    dialog = NewSessionDialog(frame, folder, providers=("claude",), provider="claude")
    try:
        dialog.on_chat_options()
        assert seen["model"] == dialog.values()[4]
    finally:
        dialog.Destroy()


def test_long_instructions_stay_whole_and_ok_says_they_are_too_long(frame, folder):
    from thechatplace.chat_options import MAX_INSTRUCTIONS
    from thechatplace.ui.dialogs import ChatOptionsDialog
    said = []
    dialog = ChatOptionsDialog(frame, "Mine", folder, feedback=said.append)
    try:
        dialog.SetReturnCode(wx.ID_CANCEL)
        pasted = "x" * (MAX_INSTRUCTIONS + 500)
        dialog.instructions.SetValue(pasted)
        assert dialog.instructions.GetValue() == pasted  # nothing dropped
        dialog.on_ok()
        assert dialog.GetReturnCode() == wx.ID_CANCEL
        assert said[-1].startswith("Those instructions are 8,500 characters")
        dialog.instructions.SetValue("x" * MAX_INSTRUCTIONS)
        dialog.on_ok()
        assert dialog.GetReturnCode() == wx.ID_OK
    finally:
        dialog.Destroy()


def test_ctrl_enter_is_refused_as_ok_is(frame, folder):
    said = []
    dialog = _agent_dialog(frame, folder, said, [{"name": "wide", "model": "sonnet[1m]"}])
    try:
        dialog.SetReturnCode(wx.ID_CANCEL)
        dialog.agent.SetSelection(1)
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(wx.WXK_RETURN)
        event.SetControlDown(True)
        dialog._on_key(event)
        assert dialog.GetReturnCode() == wx.ID_CANCEL and "sonnet[1m]" in said[-1]
    finally:
        dialog.Destroy()


def test_a_whole_drive_is_refused_with_that_reason(frame, folder):
    import os
    from thechatplace.ui.dialogs import ChatOptionsDialog
    said = []
    dialog = ChatOptionsDialog(frame, "Mine", folder, feedback=said.append)
    try:
        assert not dialog.add_folder(os.path.abspath(os.sep))
        assert said[-1] == "A whole drive can't be added. Choose a folder inside it."
        assert dialog.chosen_folders() == []
    finally:
        dialog.Destroy()


def test_kept_choices_show_at_once_and_are_refreshed(frame, env, folder, monkeypatch):
    from thechatplace.chat_options import choices_from_init
    from thechatplace.ui import claude_extras, main_frame
    from thechatplace.ui.dialogs import ChatOptionsDialog
    old = choices_from_init({"agents": [{"name": "old-one"}]})
    frame._chat_choices_cache()[claude_extras._folder_key(folder)] = old
    said = []
    monkeypatch.setattr(ChatOptionsDialog, "IsShown", lambda self: True)
    dialog = ChatOptionsDialog(frame, "Mine", folder, choices=old, feedback=said.append,
                               load_choices=frame._load_chat_choices)
    try:
        assert "old-one" in dialog.agent.GetStrings()
        assert pump(lambda: any(s.startswith("Explore") for s in dialog.agent.GetStrings()))
        assert said[-1] == "This folder's agents and output styles were updated."
        assert frame._chat_choices_cache()[claude_extras._folder_key(folder)].agent_names() == [
            "code-reviewer", "Explore"]
        # Nothing new, or Claude Code can't say: the list stands, and nothing's said.
        said.clear()
        kept = list(dialog.agent.GetStrings())
        frame._load_chat_choices(folder, dialog._choices_loaded)
        pump(lambda: False, timeout=0.3)
        monkeypatch.setattr(main_frame, "fetch_chat_choices", lambda exe, cwd: None)
        frame._load_chat_choices(folder, dialog._choices_loaded)
        pump(lambda: False, timeout=0.3)
        assert said == [] and list(dialog.agent.GetStrings()) == kept
    finally:
        dialog.Destroy()


def test_the_first_answer_to_a_chats_choices_still_says_ready_or_why_not(frame, env, folder,
                                                                         monkeypatch):
    from thechatplace.ui import main_frame
    from thechatplace.ui.dialogs import ChatOptionsDialog
    said = []
    monkeypatch.setattr(ChatOptionsDialog, "IsShown", lambda self: True)
    monkeypatch.setattr(main_frame, "fetch_chat_choices", lambda exe, cwd: None)
    dialog = ChatOptionsDialog(frame, "Mine", folder, feedback=said.append,
                               load_choices=frame._load_chat_choices)
    try:
        assert pump(lambda: bool(said))
        assert said[-1].startswith("Couldn't get this folder's agents")
    finally:
        dialog.Destroy()


def test_focus_is_given_back_only_if_it_isnt_there_already(monkeypatch):
    # Setting it again had a screen reader say the row twice (#468).
    from thechatplace.ui.claude_extras import _give_focus_back

    class Spot:
        calls = 0

        def SetFocus(self):
            self.calls += 1
    spot = Spot()
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: spot))
    _give_focus_back(spot)
    assert spot.calls == 0
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: None))
    _give_focus_back(spot)
    assert spot.calls == 1
    _give_focus_back(None)  # there was nowhere to go back to


@msaa
def test_list_hints_reach_jaws_and_nvda(frame, folder):
    from thechatplace.ui.dialogs import AskAsideDialog, ChatOptionsDialog
    options = ChatOptionsDialog(frame, "Mine", folder)
    aside = AskAsideDialog(frame, "Mine", lambda question, on_event: None)
    try:
        assert options.folders.GetAccessible().GetName(0) == (
            wx.ACC_OK, "More folders, Delete removes one")
        assert aside.history.GetAccessible().GetName(0) == (
            wx.ACC_OK, "Questions and answers, Enter reads one")
        # A row is still read as itself.
        assert options.folders.GetAccessible().GetName(1)[1] == ""
    finally:
        options.Destroy()
        aside.Destroy()


def test_new_session_keeps_the_typed_message_if_the_command_cant_be_built(frame, env, folder,
                                                                          monkeypatch):
    from thechatplace.ui import main_frame
    copied = []
    monkeypatch.setattr(type(frame), "_copy_text", lambda self, text: copied.append(text) or True)

    class Fills(main_frame.NewSessionDialog):
        def ShowModal(self):
            self.set_folder(folder)
            self.message.SetValue("My long first message")
            return wx.ID_OK

    def refuse(*args, **kwargs):
        raise ValueError("Those instructions can't be passed to Claude Code.")
    monkeypatch.setattr(main_frame, "NewSessionDialog", Fills)
    monkeypatch.setattr(main_frame, "build_new_command", refuse)
    before = len(frame.store.all())
    frame.on_new_session()
    assert copied == ["My long first message"]
    assert "No chat was started: Those instructions" in env["feedback"][-1]
    assert "copied to the clipboard" in env["feedback"][-1]
    assert len(frame.store.all()) == before and FakeRunner.instances == []


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
    # The copy carries the source's recorded system prompt, so it's made afresh.
    assert command[command.index("--system-prompt-snapshot") + 1] == "off"
    new_id = command[command.index("--session-id") + 1]
    assert frame.store.get(new_id).prompt_fresh


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
        asks = dialog.on_ask

        def on_ask():
            # Where the copy ends is found off the UI thread first (#559).
            count = len(FakeAside.instances)
            asks()
            assert pump(lambda: len(FakeAside.instances) > count or not dialog.asking)
        dialog.on_ask = on_ask
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
        assert pump(lambda: dialog.answer.GetValue().endswith("Answer:\nmain"))
        assert dialog.history.GetString(0) == "Q: Which file did you change? A: asking…"
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
    # Always cut at its newest finished reply (#559), here its last.
    assert runner.command[-6:] == ["--resume", "own-1", "--resume-session-at",
                                   _reply_uuid(env, folder, "own-1"), "--fork-session",
                                   "--no-session-persistence"]
    assert env["spoken"][-1] == "Answer. main.py, line 4."
    assert copied == ["main.py, line 4."]
    assert FakeRunner.instances == []  # no turn of the chat's own


def test_the_same_question_twice_finishes_its_own_row(frame, env, monkeypatch):
    def steps(dialog):
        for row in (0, 1):
            dialog.question.SetValue("Which file?")
            dialog.on_ask()
            FakeAside.instances[-1].on_event(AsideEvent("done", "main.py"))
            assert pump(lambda: not dialog.asking)
            assert dialog.history.GetSelection() == row
        assert list(dialog.history.GetStrings()) == ["Q: Which file? A: main.py"] * 2
    select(frame, "Mine")
    _drive_aside(frame, monkeypatch, steps)


def test_a_wrapped_question_leaves_the_caret_on_the_answer(frame, env, monkeypatch):
    # On Windows XYToPosition counts displayed lines, which put it in the question.
    answer = "First line of the answer.\nSecond line."

    def steps(dialog):
        dialog.question.SetValue("Why did this happen when it did? " * 30)
        dialog.on_ask()
        FakeAside.instances[-1].on_event(AsideEvent("done", answer))
        assert pump(lambda: not dialog.asking)
        box = dialog.answer
        assert box.GetRange(box.GetInsertionPoint(), box.GetLastPosition()) == answer
    select(frame, "Mine")
    _drive_aside(frame, monkeypatch, steps)


def test_ask_aside_can_read_the_chats_more_folders(frame, tmp_path, monkeypatch):
    frame.store.update("own-1", allowed_dirs=[str(tmp_path)])
    select(frame, "Mine")
    _drive_aside(frame, monkeypatch, lambda d: (d.question.SetValue("q"), d.on_ask()))
    command = FakeAside.instances[-1].command
    assert command[command.index("--add-dir") + 1] == str(tmp_path)
    assert command[command.index("--tools") + 1] == "Read,Grep,Glob"


def test_a_desktop_chat_has_no_more_folders_to_give_an_aside(frame, monkeypatch):
    select(frame, "Desktop one")
    _drive_aside(frame, monkeypatch, lambda d: (d.question.SetValue("q"), d.on_ask()))
    assert "--add-dir" not in FakeAside.instances[-1].command


def _mid_turn(env, folder, cli="own-1"):
    """A transcript whose second turn is unfinished: the second message and a
    command with no result yet. Returns the first turn's last record."""
    from records import tool_use_block
    records = [user_text("Hello"), assistant_block(text_block("Hi"), "m1"),
               user_text("Run the tests"),
               assistant_block(tool_use_block("Bash", {"command": "sleep 45"}), "m2")]
    for before, record in zip(records, records[1:]):
        record["parentUuid"] = before["uuid"]
    add_transcript(env, folder, cli, records)
    return records[1]


def _reply_uuid(env, folder, cli):
    """The uuid of the last reply in a chat's transcript, as written."""
    import json
    path = env["projects"] / platform_paths.encode_cwd(folder) / f"{cli}.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [r for r in records if r["type"] == "assistant"][-1]["uuid"]


def _asked_until_read(dialog):
    dialog.question.SetValue("Which file?")
    dialog.on_ask()  # _drive_aside's waits for the transcript to be read


def _cut_command(end):
    return ["--resume", "own-1", "--resume-session-at", end["uuid"], "--fork-session",
            "--no-session-persistence"]


def test_ask_aside_during_a_turn_leaves_the_running_turn_out(frame, env, folder, monkeypatch):
    # #559: a copy holding the unfinished turn had Claude answer its message too.
    end = _mid_turn(env, folder)
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    select(frame, "Mine")
    _drive_aside(frame, monkeypatch, _asked_until_read)
    assert FakeAside.instances[-1].command[-6:] == _cut_command(end)


def test_ask_aside_after_a_turn_was_stopped_leaves_that_turn_out(frame, env, folder, monkeypatch):
    # Stop kills claude -p, so the transcript ends mid-command with no runner
    # left: the transcript decides, not whether a turn is running.
    end = _mid_turn(env, folder)
    assert "own-1" not in frame._runners
    select(frame, "Mine")
    _drive_aside(frame, monkeypatch, _asked_until_read)
    assert FakeAside.instances[-1].command[-6:] == _cut_command(end)


def test_ask_aside_just_after_send_cuts_at_the_last_finished_reply(frame, env, folder,
                                                                   monkeypatch):
    # The turn is running, but claude -p hasn't written your message yet: the
    # chat as written ends with a finished reply. The copy is still cut
    # there, so your message, written before Claude Code reads the chat
    # again, isn't in it unanswered.
    frame._runners["own-1"] = FakeRunner([], "", "", None)
    select(frame, "Mine")
    _drive_aside(frame, monkeypatch, _asked_until_read)
    command = FakeAside.instances[-1].command
    assert command[command.index("--resume-session-at") + 1] == _reply_uuid(env, folder, "own-1")


def test_ask_aside_on_a_desktop_chat_listed_idle_still_leaves_its_turn_out(frame, env, folder,
                                                                          monkeypatch):
    # The list's snapshot can be a refresh behind a turn that has started.
    from thechatplace.sessions import IDLE
    end = _mid_turn(env, folder, cli="cli-a")
    select(frame, "Desktop one")
    assert frame._selected_session().state == IDLE
    _drive_aside(frame, monkeypatch, _asked_until_read)
    command = FakeAside.instances[-1].command
    assert command[command.index("--resume-session-at") + 1] == end["uuid"]


def test_ask_aside_says_when_only_the_first_turn_is_unfinished(frame, env, folder, monkeypatch):
    from records import tool_use_block
    from thechatplace.aside import NOTHING_FINISHED
    records = [user_text("Run the tests"),
               assistant_block(tool_use_block("Bash", {"command": "sleep 45"}), "m1")]
    records[1]["parentUuid"] = records[0]["uuid"]
    add_transcript(env, folder, "own-1", records)
    seen = {}

    def steps(dialog):
        _asked_until_read(dialog)
        seen["answer"] = dialog.answer.GetValue()
        seen["row"] = dialog.history.GetString(0)
    select(frame, "Mine")
    _drive_aside(frame, monkeypatch, steps)
    assert FakeAside.instances == []  # nothing asked
    assert seen["answer"].endswith("Not answered. " + NOTHING_FINISHED)
    assert seen["row"].startswith("Q: Which file? A: not answered: Nothing in this chat has "
                                  "finished yet")


def test_ask_aside_says_when_the_chat_compacted_since_its_last_reply(frame, env, folder,
                                                                   monkeypatch):
    # #559: no place to cut a copy of it that Claude Code can find.
    from records import _base, tool_use_block
    from thechatplace.aside import COMPACTED_SINCE
    ask = user_text("Run the tests")
    summary = user_text("This session is being continued.", isCompactSummary=True)
    # Automatic, mid-turn, keeping the turn so far (Claude Code 2.1.229 on).
    kept = {"anchorUuid": summary["uuid"], "uuids": [ask["uuid"]], "allUuids": [ask["uuid"]]}
    records = [user_text("Hello"), assistant_block(text_block("Hi"), "m1"), ask,
               _base("system", subtype="compact_boundary", content="Conversation compacted",
                     compactMetadata={"trigger": "auto", "preservedMessages": kept,
                                      "preservedSegment": {"headUuid": ask["uuid"],
                                                           "anchorUuid": summary["uuid"],
                                                           "tailUuid": ask["uuid"]}}),
               summary, assistant_block(tool_use_block("Bash", {"command": "make"}), "m2")]
    add_transcript(env, folder, "own-1", records)
    seen = {}

    def steps(dialog):
        _asked_until_read(dialog)
        seen["answer"] = dialog.answer.GetValue()
    select(frame, "Mine")
    _drive_aside(frame, monkeypatch, steps)
    assert FakeAside.instances == []  # nothing asked
    assert seen["answer"].endswith("Not answered. " + COMPACTED_SINCE)


def test_stopping_an_aside_while_the_chat_is_read_asks_nothing(frame, env, folder, monkeypatch):
    from thechatplace.ui.dialogs import AskAsideDialog
    _mid_turn(env, folder)
    seen = {}

    def steps(dialog):
        dialog.question.SetValue("Which file?")
        AskAsideDialog.on_ask(dialog)  # not _drive_aside's, which waits
        AskAsideDialog.on_ask(dialog)  # Stop, at once
        seen["said_at_once"] = env["feedback"][-1]
        assert pump(lambda: not dialog.asking)
        pump(lambda: False, timeout=0.5)
    select(frame, "Mine")
    _drive_aside(frame, monkeypatch, steps)
    # "Stopped." comes later, as a runner's does, not in the middle of Stop.
    assert seen["said_at_once"] == "Asking Claude."
    assert FakeAside.instances == []
    assert env["feedback"][-1] == "Stopped asking."


def test_ask_aside_names_codex_and_its_plan(frame, monkeypatch):
    from types import SimpleNamespace
    from thechatplace.ui.dialogs import AskAsideDialog
    said = []
    dialog = AskAsideDialog(frame, "Codex example",
                            lambda question, on_event: SimpleNamespace(cancel=lambda: None),
                            provider="Codex", feedback=said.append)
    try:
        assert "your ChatGPT plan" in dialog.answer.GetValue()
        assert "Codex plan" not in dialog.answer.GetValue()
        dialog.question.SetValue("Which file?")
        dialog.on_ask()
        assert dialog.answer.GetValue().endswith("Asking Codex…")
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(wx.WXK_RETURN)
        event.SetControlDown(True)
        dialog._on_key(event)
        assert said[-1] == "Codex is still answering. Escape stops it."
    finally:
        dialog.stop()
        dialog.Destroy()


def _aside_on_the_menu(frame):
    menu, _actions = frame._session_menu()
    try:
        items = [item for item in menu.GetMenuItems() if "Aside" in item.GetItemLabel()]
        return [item.IsEnabled() for item in items]
    finally:
        menu.Destroy()


def test_the_lists_menu_offers_ask_aside_by_the_chats_capability(frame, monkeypatch):
    import dataclasses
    from thechatplace.providers.base import SessionCapabilities
    select(frame, "Mine")
    assert _aside_on_the_menu(frame) == [True]
    codex = dataclasses.replace(frame._selected_session(), provider="codex")
    monkeypatch.setattr(frame, "_selected_session", lambda: codex)
    monkeypatch.setattr(frame.sessions, "capabilities",
                        lambda info: SessionCapabilities(aside=True))
    assert _aside_on_the_menu(frame) == [True]
    monkeypatch.setattr(frame.sessions, "capabilities",
                        lambda info: SessionCapabilities(aside=False))
    assert _aside_on_the_menu(frame) == []
    # A Claude chat that can't (Cowork) is listed, heard as unavailable.
    claude = dataclasses.replace(codex, provider="claude")
    monkeypatch.setattr(frame, "_selected_session", lambda: claude)
    assert _aside_on_the_menu(frame) == [False]


def test_enter_on_a_question_reads_its_answer_from_the_dialogs_key_hook(frame, env,
                                                                        monkeypatch):
    # Windows gives a list box's Enter to the dialog before the list's own
    # handler sees it, so the dialog's char hook takes it (#468).
    seen = {}

    def steps(dialog):
        dialog.question.SetValue("Which file did you change?")
        dialog.on_ask()
        FakeAside.instances[-1].on_event(AsideEvent("done", "main.py, line 4."))
        assert pump(lambda: not dialog.asking)
        dialog.answer.ChangeValue("")
        dialog.history.SetSelection(0)
        monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: dialog.history))
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(wx.WXK_RETURN)
        dialog._on_key(event)
        seen["answer"] = dialog.answer.GetValue()
    select(frame, "Mine")
    _drive_aside(frame, monkeypatch, steps)
    assert seen["answer"].endswith("Answer:\nmain.py, line 4.")


def test_ask_aside_reads_a_desktop_session_without_resuming_it(frame, env, folder,
                                                               monkeypatch):
    select(frame, "Desktop one")
    _drive_aside(frame, monkeypatch, lambda d: (d.question.SetValue("q"), d.on_ask()))
    command = FakeAside.instances[-1].command
    assert command[command.index("--resume") + 1:] == [
        "cli-a", "--resume-session-at", _reply_uuid(env, folder, "cli-a"), "--fork-session",
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


def test_close_while_asking_stops_and_closes(frame, env, monkeypatch):
    seen = {}

    def steps(dialog):
        dialog.question.SetValue("long one")
        dialog.on_ask()
        dialog.on_close(stop_first=False)  # the Close button, or Alt+F4
        seen["cancelled"] = FakeAside.instances[-1].cancelled
        seen["closed"] = dialog._closed
    select(frame, "Mine")
    _drive_aside(frame, monkeypatch, steps)
    assert seen == {"cancelled": True, "closed": True}


def test_answers_stream_at_a_steady_pace(frame, env, monkeypatch):
    def steps(dialog):
        dialog.question.SetValue("q")
        dialog.on_ask()
        runner = FakeAside.instances[-1]
        runner.on_event(AsideEvent("text", "One"))
        assert pump(lambda: dialog.answer.GetValue().endswith("Answer:\nOne"))
        runner.on_event(AsideEvent("text", "One two"))  # too soon to show
        pump(lambda: False, timeout=0.2)
        assert dialog.answer.GetValue().endswith("Answer:\nOne")
        assert dialog.items[0].answer == "One two"
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


def test_ask_asides_list_says_none_yet_until_a_question_is_asked(frame, env, monkeypatch):
    # An empty list was read as "unknown" (#468).
    from thechatplace.ui.dialogs import ASIDE_NONE_YET
    seen = {}

    def steps(dialog):
        seen["before"] = list(dialog.history.GetStrings())
        monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: dialog.history))
        monkeypatch.setattr(dialog.answer, "SetFocus", lambda: seen.setdefault("answer", True))
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(wx.WXK_RETURN)
        dialog._on_key(event)  # Enter on "none yet": nothing to read
        dialog.question.SetValue("Which file?")
        dialog.on_ask()
        seen["after"] = list(dialog.history.GetStrings())
    select(frame, "Mine")
    _drive_aside(frame, monkeypatch, steps)
    assert seen["before"] == [ASIDE_NONE_YET]
    assert "answer" not in seen
    assert seen["after"] == ["Q: Which file? A: asking…"]


# -- a chat's questions stay while the app is open (#468) -----------------------------------


class _Asked:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


def _aside_dialog(frame, history, sent=None):
    from thechatplace.ui.dialogs import AskAsideDialog
    said = []
    answerers = []

    def ask(question, on_event):
        if sent is not None:
            sent.append(question)
        answerers.append(on_event)
        return _Asked()
    dialog = AskAsideDialog(frame, "Mine", ask, feedback=said.append, history=history)
    return dialog, answerers, said


def _ask_and_answer(dialog, answerers, question, answer):
    from thechatplace.aside import AsideEvent
    dialog.question.SetValue(question)
    dialog.on_ask()
    answerers[-1](AsideEvent("done", answer))


def test_coming_back_finds_the_chats_questions_and_the_next_carries_them(frame):
    history, sent = [], []
    dialog, answerers, _said = _aside_dialog(frame, history, sent)
    try:
        _ask_and_answer(dialog, answerers, "Which file?", "uploader.py")
    finally:
        dialog.settle()
        dialog.Destroy()
    assert sent == ["Which file?"]
    dialog, answerers, said = _aside_dialog(frame, history, sent)
    try:
        assert list(dialog.history.GetStrings()) == [history[0].row()]
        assert dialog.history.GetSelection() == 0
        assert "uploader.py" in dialog.answer.GetValue()
        assert dialog.clear_btn.IsEnabled()
        assert pump(lambda: said)
        assert said == ["1 earlier question in the list."]
        _ask_and_answer(dialog, answerers, "Why that one?", "It flushes.")
        assert "uploader.py" in sent[-1] and sent[-1].endswith("Why that one?")
        assert dialog.history.GetCount() == 2 and len(history) == 2
        assert history[1].question == "Why that one?"  # the row shows what you typed
    finally:
        dialog.settle()
        dialog.Destroy()


def test_a_question_running_as_the_dialog_closes_is_marked_stopped(frame):
    history = []
    dialog, _answerers, _said = _aside_dialog(frame, history)
    try:
        dialog.question.SetValue("Slow one?")
        dialog.on_ask()
        runner = dialog._runner
        assert history[0].asking and not dialog.clear_btn.IsEnabled()
    finally:
        dialog.settle()
        dialog.Destroy()
    assert runner.cancelled
    assert not history[0].asking and history[0].failed and history[0].answer == "Stopped."
    assert "asking" not in history[0].row()


def test_clear_list_empties_the_chats_questions(frame):
    from thechatplace.ui.dialogs import ASIDE_NONE_YET
    history = []
    dialog, answerers, said = _aside_dialog(frame, history)
    try:
        assert not dialog.clear_btn.IsEnabled()
        _ask_and_answer(dialog, answerers, "Which file?", "uploader.py")
        _ask_and_answer(dialog, answerers, "Why?", "It flushes.")
        assert dialog.clear_btn.IsEnabled()
        dialog.on_clear()
        assert history == []  # the window's own list, for next time
        assert list(dialog.history.GetStrings()) == [ASIDE_NONE_YET]
        assert said[-1] == "Cleared 2 questions."
        assert not dialog.clear_btn.IsEnabled()
        assert "uploader.py" not in dialog.answer.GetValue()
    finally:
        dialog.settle()
        dialog.Destroy()


def test_each_chat_has_its_own_questions(frame):
    from types import SimpleNamespace
    from thechatplace.aside import AsideItem, KEPT_QUESTIONS
    claude = SimpleNamespace(provider="claude", cli_session_id="same")
    codex = SimpleNamespace(provider="codex", cli_session_id="same")
    frame._aside_history(claude).append(AsideItem("Mine?"))
    assert frame._aside_history(codex) == []
    assert len(frame._aside_history(claude)) == 1
    frame._aside_history(claude).extend(AsideItem(str(n)) for n in range(KEPT_QUESTIONS + 5))
    assert len(frame._aside_history(claude)) == KEPT_QUESTIONS
    assert frame._aside_history(claude)[-1].question == str(KEPT_QUESTIONS + 4)


def test_asking_from_clear_list_moves_focus_off_it_first(frame, monkeypatch):
    # Ctrl+Enter works from anywhere, and Clear List turns off while asking.
    history = []
    dialog, answerers, _said = _aside_dialog(frame, history)
    moved = []
    try:
        _ask_and_answer(dialog, answerers, "One?", "First.")
        monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: dialog.clear_btn))
        monkeypatch.setattr(dialog.question, "SetFocus", lambda: moved.append(True))
        dialog.question.SetValue("Two?")
        dialog.on_ask()
        assert moved and not dialog.clear_btn.IsEnabled()
    finally:
        dialog.settle()
        dialog.Destroy()


def test_a_codex_question_too_long_stays_in_the_box_and_says_why(frame):
    from thechatplace.ui.dialogs import AskAsideDialog
    said, asked = [], []
    dialog = AskAsideDialog(frame, "Mine", lambda q, e: asked.append(q), feedback=said.append,
                            provider="Codex")
    try:
        dialog.question.SetValue("q" * 32001)
        dialog.on_ask()
        assert asked == [] and "at most 32,000" in said[-1]
        assert len(dialog.question.GetValue()) == 32001 and not dialog.items
    finally:
        dialog.Destroy()
