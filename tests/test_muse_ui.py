"""Muse Code sign-in UI behavior (tests precede implementation)."""

import time

import pytest

pytest_plugins = ["test_ui_hidden"]

wx = pytest.importorskip("wx")


def test_muse_sign_in_is_available_from_tools_sign_in_submenu(frame, monkeypatch):
    """Setup stays available alongside the other providers even when Muse is absent."""
    from thechatplace.ui import main_frame

    called = []
    monkeypatch.setattr(frame, "_check_muse_sign_in", lambda: called.append(True))
    bar = frame.GetMenuBar()
    tools_index = next(i for i in range(bar.GetMenuCount()) if bar.GetMenuLabelText(i) == "Tools")
    tools = bar.GetMenu(tools_index)
    sign_in = next(item.GetSubMenu() for item in tools.GetMenuItems()
                   if item.GetItemLabelText() == "Sign In")
    assert [item.GetItemLabelText() for item in sign_in.GetMenuItems()] == [
        "Claude Code...", "Codex...", "Copilot...", "Muse Code..."]
    assert frame.menu_items["muse_sign_in"].GetMenu() is sign_in
    frame.on_muse_sign_in()
    assert called == [True]


def test_muse_missing_status_uses_the_same_ok_message_box_as_copilot(frame, monkeypatch):
    from thechatplace.ui import main_frame
    from thechatplace import muse_signin

    shown = []
    monkeypatch.setattr(wx, "MessageBox",
                        lambda *args, **kwargs: shown.append((args, kwargs)) or wx.OK)
    monkeypatch.setattr(main_frame, "MessageDialog",
                        lambda *args, **kwargs: pytest.fail("missing Muse must use the native OK dialog"))
    status = muse_signin.SignIn(False, signed_in=False, missing=True,
                                problem="Muse Code wasn't found")
    status_text = "Muse Code wasn't found. Install Muse Code, then choose Muse Code Sign-in again."
    monkeypatch.setattr(main_frame.muse_signin, "describe", lambda value: status_text)
    frame._on_muse_sign_in_result(status)

    assert shown
    args, kwargs = shown[-1]
    assert args[1] == "Muse Code Sign-in"
    assert status_text in args[0]
    assert "muse --version" in args[0]
    assert args[2] == wx.OK | wx.ICON_WARNING
    assert args[3] is frame


def test_muse_sign_in_starts_device_flow_only_after_confirmation(frame, monkeypatch):
    from thechatplace.ui import main_frame
    from thechatplace import muse_signin

    answers = [wx.NO, wx.YES]
    monkeypatch.setattr(wx, "MessageBox", lambda *args, **kwargs: answers.pop(0))
    started = []
    monkeypatch.setattr(frame, "_start_muse_device_login", lambda status: started.append(status))
    status = muse_signin.SignIn(True, signed_in=False, credential_required=True,
                                account_state="loggedOut", executable="muse.exe")
    monkeypatch.setattr(main_frame.muse_signin, "describe", lambda value: "Muse Code isn't signed in.")

    frame._on_muse_sign_in_result(status)
    assert started == []
    frame._on_muse_sign_in_result(status)
    assert len(started) == 1 and started[0] is status


def test_muse_sign_in_check_ignores_results_after_close(frame, monkeypatch):
    from thechatplace.ui import main_frame

    frame._closing = True
    shown = []
    monkeypatch.setattr(wx, "MessageBox", lambda *args, **kwargs: shown.append(args))
    from thechatplace import muse_signin
    status = muse_signin.SignIn(False, signed_in=False, missing=True,
                                problem="Muse Code wasn't found")
    frame._on_muse_sign_in_result(status)
    assert shown == []


def test_muse_login_dialog_names_selectable_code_and_actions(app):
    from thechatplace.ui.dialogs import MuseLoginDialog

    opened = []
    dialog = MuseLoginDialog(None, "https://example.test/device", "ABCD-EFGH",
                             lambda: opened.append(True))
    try:
        assert dialog.url.GetValue() == "https://example.test/device"
        assert dialog.code.GetValue() == "ABCD-EFGH"
        assert dialog.url.GetName() == "Browser address"
        assert dialog.code.GetName() == "Device code"
        assert dialog.open_button.GetLabelText() == "Open Browser"
        assert dialog.cancel_button.GetLabelText() == "Cancel"
        dialog.open_button.ProcessEvent(wx.CommandEvent(wx.wxEVT_BUTTON,
                                                        dialog.open_button.GetId()))
        assert opened == [True]
    finally:
        dialog.Destroy()


def test_muse_status_check_does_not_start_a_duplicate_worker(frame, monkeypatch):
    frame._muse_signin_busy = True
    submitted = []
    monkeypatch.setattr(frame._pool, "submit", lambda *args, **kwargs: submitted.append(True))
    frame._check_muse_sign_in()
    assert submitted == []


def test_muse_dialog_restores_the_control_that_invoked_it(frame, monkeypatch):
    from thechatplace.ui import main_frame

    class Focus:
        def __init__(self):
            self.restored = False
        def IsShown(self): return True
        def IsEnabled(self): return True
        def SetFocus(self): self.restored = True

    focus = Focus()
    monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: focus))
    monkeypatch.setattr(frame, "_modal", lambda dialog: wx.ID_OK)
    frame._muse_modal(object())
    assert focus.restored


def test_muse_login_completion_rechecks_without_reprompting(frame, monkeypatch):
    checks = []
    monkeypatch.setattr(frame, "_check_muse_sign_in",
                        lambda report_only=False: checks.append(report_only))
    monkeypatch.setattr(wx, "CallAfter", lambda fn, *args: fn(*args))

    import threading
    operation = {"cancel": threading.Event(), "flow": None, "dialog": None}
    frame._muse_login_operation = operation
    frame._on_muse_login_done("granted", operation)
    assert checks == [True]


@pytest.mark.parametrize("state,method", [("apiKey", "an API key"), ("envKey", "META_API_KEY")])
def test_muse_key_states_show_guidance_without_a_login_offer(frame, monkeypatch, state, method):
    from thechatplace import muse_signin
    shown = []
    monkeypatch.setattr(wx, "MessageBox", lambda *args: shown.append(args) or wx.OK)
    monkeypatch.setattr(frame, "_muse_modal", lambda dialog: pytest.fail("read-only status dialog"))
    frame._on_muse_sign_in_result(muse_signin.SignIn(True, signed_in=False,
        account_state=state, method=method, credential_required=True))
    assert len(shown) == 1
    assert shown[0][2] == wx.OK | wx.ICON_WARNING


def test_muse_unknown_account_does_not_show_installation_commands(frame, monkeypatch):
    from thechatplace import muse_signin
    shown = []

    monkeypatch.setattr(wx, "MessageBox", lambda *args: shown.append(args[0]) or wx.OK)
    monkeypatch.setattr(frame, "_muse_modal", lambda dialog: pytest.fail("read-only status dialog"))
    frame._on_muse_sign_in_result(muse_signin.SignIn(False, problem="Update Muse Code and retry"))
    assert "Update Muse Code" in shown[0] and "install.ps1" not in shown[0]


@pytest.mark.parametrize("known,signed_in,state,required,report_only,icon", [
    (True, True, "accountLogin", True, False, wx.ICON_INFORMATION),
    (False, None, "", False, False, wx.ICON_WARNING),
    (True, False, "loggedOut", True, True, wx.ICON_WARNING),
    (True, False, "loggedOut", False, False, wx.ICON_INFORMATION),
])
def test_muse_status_notices_use_native_ok_and_restore_focus(
        frame, monkeypatch, known, signed_in, state, required, report_only, icon):
    from thechatplace import muse_signin
    from thechatplace.ui import main_frame
    shown, restored = [], []
    monkeypatch.setattr(wx, "MessageBox", lambda *args: shown.append(args) or wx.OK)
    monkeypatch.setattr(main_frame, "MessageDialog", lambda *a, **k: pytest.fail("read-only status dialog"))
    monkeypatch.setattr(frame, "_restore_muse_focus", lambda: restored.append(True))
    frame._on_muse_sign_in_result(muse_signin.SignIn(
        known, signed_in=signed_in, account_state=state, credential_required=required), report_only)
    assert len(shown) == 1 and shown[0][1:] == ("Muse Code Sign-in", wx.OK | icon, frame)
    assert restored == [True]


def test_muse_unavailable_before_login_uses_native_ok_warning(frame, monkeypatch):
    from thechatplace import muse_signin
    from thechatplace.ui import main_frame
    shown, restored = [], []
    monkeypatch.setattr(wx, "MessageBox", lambda *args: shown.append(args) or wx.OK)
    monkeypatch.setattr(main_frame, "MessageDialog", lambda *a, **k: pytest.fail("read-only error dialog"))
    monkeypatch.setattr(frame, "_restore_muse_focus", lambda: restored.append(True))
    frame._start_muse_device_login(muse_signin.SignIn(True))
    assert "no longer available" in shown[0][0]
    assert shown[0][2] == wx.OK | wx.ICON_WARNING
    assert restored == [True]


def test_muse_browser_failure_ok_notice_belongs_to_device_dialog(frame, monkeypatch):
    from thechatplace import platform_paths
    parent = object()
    shown = []

    def fail(url): raise OSError("private browser failure")

    monkeypatch.setattr(platform_paths, "open_url", fail)
    monkeypatch.setattr(wx, "MessageBox", lambda *args: shown.append(args) or wx.OK)
    assert frame._open_muse_browser("https://example.test/device", parent) is False
    assert shown[0][2] == wx.OK | wx.ICON_WARNING and shown[0][3] is parent
    assert "private" not in shown[0][0]


def test_muse_browser_retry_button_regains_focus_after_failure(app, monkeypatch):
    from thechatplace.ui.dialogs import MuseLoginDialog
    dialog = MuseLoginDialog(None, "https://example.test/device", "ABC", lambda: False)
    focused = []
    try:
        monkeypatch.setattr(dialog.open_button, "SetFocus", lambda: focused.append(True))
        dialog.open_button.ProcessEvent(wx.CommandEvent(wx.wxEVT_BUTTON, dialog.open_button.GetId()))
        assert dialog.open_button.IsEnabled() and focused == [True]
    finally:
        dialog.Destroy()


def test_muse_duplicate_activation_does_not_replace_focus_or_announce(frame, env, monkeypatch):
    import threading
    frame._muse_login_operation = {"cancel": threading.Event(), "flow": None, "dialog": None}
    calls = []
    monkeypatch.setattr(frame, "_check_muse_sign_in", lambda: calls.append(True))
    before = list(env["feedback"])
    frame.on_muse_sign_in()
    assert calls == [] and env["feedback"] == before


def test_muse_late_completion_after_cancel_rechecks_and_clears_operation(frame, monkeypatch):
    import threading
    checks = []
    monkeypatch.setattr(frame, "_check_muse_sign_in", lambda report_only=False: checks.append(report_only))
    monkeypatch.setattr(wx, "CallAfter", lambda fn, *args: fn(*args))

    class DestroyedDialog:
        def IsModal(self): raise RuntimeError("wrapped C/C++ object has been deleted")

    operation = {"cancel": threading.Event(), "flow": None, "dialog": DestroyedDialog()}
    frame._muse_login_operation = operation
    frame._on_muse_login_done("cancelled", operation)
    assert frame._muse_login_operation is None and checks == [True]


def test_muse_host_is_reachable_for_cleanup_before_prompt_callback_runs(frame, monkeypatch):
    import threading
    from thechatplace import muse_signin
    queued, callbacks = [], []
    closed = threading.Event()

    class Flow:
        def start(self): return muse_signin.LoginPrompt("https://example.test/device", "ABC")
        def close(self): closed.set()

    flow = Flow()
    monkeypatch.setattr(muse_signin, "LoginFlow", lambda executable: flow)
    monkeypatch.setattr(frame._pool, "submit", lambda fn: queued.append(fn))
    monkeypatch.setattr(wx, "CallAfter", lambda fn, *args: callbacks.append((fn, args)))
    status = muse_signin.SignIn(True, signed_in=False, credential_required=True,
                                account_state="loggedOut", executable="synthetic")
    frame._start_muse_device_login(status)
    queued.pop()()
    assert callbacks  # prompt is queued but its UI callback has not run
    frame._cancel_muse_login()
    assert closed.wait(2)
    assert frame._muse_login_operation["cancel"].is_set()


def test_muse_start_failure_closes_host_before_reporting_result(frame, monkeypatch):
    from thechatplace import muse_signin
    queued, closed_when_reported = [], []

    class Flow:
        closed = False
        def start(self): raise OSError("private failure")
        def close(self): self.closed = True

    flow = Flow()
    monkeypatch.setattr(muse_signin, "LoginFlow", lambda executable: flow)
    monkeypatch.setattr(frame._pool, "submit", lambda fn: queued.append(fn))
    monkeypatch.setattr(wx, "CallAfter", lambda *args: closed_when_reported.append(flow.closed))
    frame._start_muse_device_login(muse_signin.SignIn(True, executable="synthetic"))
    queued.pop()()
    assert closed_when_reported == [True]


def test_muse_permission_starts_review_on_request_and_uses_native_choice(app, monkeypatch):
    from thechatplace.ui.dialogs import MuseApprovalDialog
    queued, ended = [], []
    monkeypatch.setattr(wx, "CallAfter", lambda fn, *args: queued.append((fn, args)))
    dialog = MuseApprovalDialog(None, "Synthetic", "Run a synthetic command", "Details", "approval",
        choices=({"choiceId": "native-deny", "label": "Deny this request"},))
    try:
        monkeypatch.setattr(dialog, "EndModal", lambda result: ended.append(result))
        assert queued[0][0] == dialog.summary.SetFocus
        assert dialog.summary.GetName() == "Muse Code wants to run"
        assert dialog.decision is None
        dialog.choice_buttons[0].ProcessEvent(wx.CommandEvent(
            wx.wxEVT_BUTTON, dialog.choice_buttons[0].GetId()))
        assert dialog.decision == "native-deny" and ended == [wx.ID_OK]
    finally:
        dialog.Destroy()


def test_muse_questions_submit_only_actual_answers(app, monkeypatch):
    from thechatplace.ui.dialogs import MuseApprovalDialog
    monkeypatch.setattr(wx, "CallAfter", lambda *args: None)
    questions = (
        {"id": "single", "question": "Choose a scope", "selection": {"mode": "single"},
         "options": [{"label": "Focused", "description": "Run a few tests"}, {"label": "All"}]},
        {"id": "multiple", "question": "Choose platforms", "selection": {
            "mode": "multiple", "minSelections": 1, "maxSelections": 2},
         "options": [{"label": "Windows"}, {"label": "Mac"}]},
        {"id": "text", "question": "What name?", "selection": {"mode": "single"},
         "options": []},
    )
    dialog = MuseApprovalDialog(None, "Synthetic", "Questions need answers", "", "question",
                                questions=questions)
    try:
        ended = []
        monkeypatch.setattr(dialog, "EndModal", lambda result: ended.append(result))
        assert dialog.answer_controls[0][0].GetSelection() == wx.NOT_FOUND
        dialog.answer_controls[0][0].SetSelection(0)
        dialog.answer_controls[1][0].Check(1)
        dialog.answer_controls[2][1].SetValue("Synthetic answer")
        dialog._send_answers(None)
        assert dialog.answers == [
            {"questionId": "single", "selectedLabel": "Focused"},
            {"questionId": "multiple", "selectedLabels": ["Mac"]},
            {"questionId": "text", "freeText": "Synthetic answer"},
        ]
        assert dialog.decision == "answer" and ended == [wx.ID_OK]
    finally:
        dialog.Destroy()


def test_muse_unanswered_question_stays_pending(app, monkeypatch):
    from thechatplace.ui.dialogs import MuseApprovalDialog
    monkeypatch.setattr(wx, "CallAfter", lambda *args: None)
    notices = []
    monkeypatch.setattr(wx, "MessageBox", lambda *args: notices.append(args) or wx.OK)
    dialog = MuseApprovalDialog(None, "Synthetic", "Name this fixture", "", "question",
        questions=({"id": "name", "question": "Which name?", "selection": {"mode": "single"},
                    "options": []},))
    try:
        dialog._send_answers(None)
        assert notices and dialog.decision is None and dialog.answers is None
        assert dialog.GetEscapeId() == wx.ID_CANCEL
    finally:
        dialog.Destroy()


def test_muse_question_opens_and_says_a_typed_answer_is_too_long(app, monkeypatch):
    """The typed answer box is multi-line, which has no length limit on GTK:
    setting one asserted, so the question never opened on Linux (Orca, #701).
    Muse refuses an answer over 500 characters, so Send Answers says so."""
    from thechatplace.ui.dialogs import MUSE_ANSWER_MAX, MuseApprovalDialog
    monkeypatch.setattr(wx, "CallAfter", lambda *args: None)
    notices = []
    monkeypatch.setattr(wx, "MessageBox", lambda *args: notices.append(args) or wx.OK)
    dialog = MuseApprovalDialog(None, "Synthetic", "Name this fixture", "", "question",
        questions=({"id": "name", "question": "Which name?", "selection": {"mode": "single"},
                    "options": []},))
    try:
        ended = []
        monkeypatch.setattr(dialog, "EndModal", lambda result: ended.append(result))
        text = dialog.answer_controls[0][1]
        text.SetValue("x" * (MUSE_ANSWER_MAX + 1))
        dialog._send_answers(None)
        assert f"at most {MUSE_ANSWER_MAX} characters" in notices[-1][0]
        assert dialog.answers is None and ended == []
        text.SetValue("x" * MUSE_ANSWER_MAX)
        dialog._send_answers(None)
        assert dialog.answers == [{"questionId": "name", "freeText": "x" * MUSE_ANSWER_MAX}]
    finally:
        dialog.Destroy()


def test_muse_monitor_survives_selection_changes_and_quiet_polls(frame, monkeypatch):
    import threading
    from thechatplace.own_store import OwnSession
    from thechatplace.ui import codex_chats
    record = OwnSession("synthetic-muse", "Synthetic Muse", "C:\\work", provider="muse")
    frame.store.add(record)
    queued, callbacks, reads = [], [], []
    monkeypatch.setattr(frame._pool, "submit", lambda fn: queued.append(fn))
    monkeypatch.setattr(wx, "CallAfter", lambda fn, *args: callbacks.append((fn, args)))
    class InstantEvent:
        def is_set(self): return False
        def wait(self, timeout): return False
    monkeypatch.setattr(codex_chats.threading, "Event", InstantEvent)
    def poll(action, current):
        assert action == "poll" and current is record
        reads.append(True)
        frame._open_generation += 1
        return {"state": "idle" if len(reads) == 5 else "running", "pending": ()}
    monkeypatch.setattr(frame.sessions, "owned_action", poll)
    frame._poll_muse_owned(record)
    frame._poll_muse_owned(record)
    assert len(queued) == 1  # one monitor per native chat
    queued.pop()()
    assert len(reads) == 5  # no settlement after three quiet polls
    assert frame._muse_runtime == {}  # worker never updates UI state
    for callback, args in callbacks:
        callback(*args)
    assert frame._muse_runtime[record.cli_session_id]["state"] == "idle"
    assert record.cli_session_id not in frame._muse_monitors


def test_muse_unknown_state_is_persistent_and_late_callbacks_are_ignored(frame, monkeypatch):
    import threading
    from thechatplace.own_store import OwnSession
    record = OwnSession("synthetic-muse", "Synthetic Muse", "C:\\work", provider="muse")
    frame.store.add(record)
    token = threading.Event()
    frame._muse_monitors[record.cli_session_id] = token
    update = {"state": "unknown", "notice": "Muse Code's state is unknown."}
    frame._apply_muse_owned_update(record, update, token)
    assert frame._muse_runtime[record.cli_session_id] == update
    frame._apply_muse_owned_update(record, {"state": "idle"}, token)
    assert frame._muse_runtime[record.cli_session_id] == update


@pytest.mark.parametrize("terminal,said", [
    ("completed", "Synthetic Muse: Muse Code finished."),
    ("cancelled", "Synthetic Muse: Muse Code stopped."),
    ("failed", "Synthetic Muse: Muse Code's turn failed. Its messages so far are in the chat."),
    ("failed:modelError", "Synthetic Muse: Muse Code's turn failed: it couldn't use this chat's "
     "model, which your account may not have access to. Choose another with Chat, Chat "
     "Settings, Change Model."),
])
def test_muse_turn_end_says_how_it_ended(frame, monkeypatch, terminal, said):
    import threading
    from thechatplace.own_store import OwnSession
    record = OwnSession("synthetic-muse", "Synthetic Muse", r"C:\work", provider="muse")
    frame.store.add(record)
    token = threading.Event()
    frame._muse_monitors[record.cli_session_id] = token
    spoken = []
    monkeypatch.setattr(frame, "_say", lambda text, **kwargs: spoken.append(text))
    frame._apply_muse_owned_update(record, {"state": "cancelling", "pending": ()}, token)
    terminal, _, failure = terminal.partition(":")
    frame._apply_muse_owned_update(record, {"state": "idle", "pending": (), "terminal": terminal,
                                            "failure": failure}, token)
    assert spoken == [said]


def test_muse_monitor_keeps_watching_background_work_until_the_reply(frame, monkeypatch):
    """Muse 1.4.24 ends the turn that starts a workflow, its sub-agents ask
    permission afterwards, and Muse replies in a turn of its own. The monitor
    keeps going through all of that, and says "finished" only after the reply."""
    from thechatplace.own_store import OwnSession
    from thechatplace.ui import codex_chats
    record = OwnSession("synthetic-muse", "Synthetic Muse", r"C:\work", provider="muse")
    frame.store.add(record)
    queued, callbacks, waits, spoken = [], [], [], []
    monkeypatch.setattr(frame._pool, "submit", lambda fn: queued.append(fn))
    monkeypatch.setattr(wx, "CallAfter", lambda fn, *args: callbacks.append((fn, args)))
    monkeypatch.setattr(frame, "_say", lambda text, **kwargs: spoken.append(text))
    class InstantEvent:
        def is_set(self): return False
        def wait(self, timeout): waits.append(timeout); return False
    monkeypatch.setattr(codex_chats.threading, "Event", InstantEvent)
    updates = iter([
        {"state": "running", "pending": ()},
        {"state": "idle", "pending": (), "background": True, "terminal": "completed"},
        {"state": "waiting", "pending": (), "background": True},
        {"state": "idle", "pending": (), "background": True, "terminal": "completed"},
        {"state": "running", "pending": (), "background": False},
        {"state": "idle", "pending": (), "background": False, "terminal": "completed"},
    ])
    monkeypatch.setattr(frame.sessions, "owned_action", lambda action, current: next(updates))
    frame._poll_muse_owned(record)
    queued.pop()()
    assert next(updates, None) is None  # every update was polled, then it stopped
    assert waits == [2.5, 5, 5, 5, 2.5]  # less often while only background work runs
    for callback, args in callbacks:
        callback(*args)
    assert spoken == [
        "Synthetic Muse: Muse Code is working in the background. You'll hear its reply when the work is done.",
        "Synthetic Muse: Muse Code finished."]
    assert record.cli_session_id not in frame._muse_monitors


@pytest.mark.parametrize("update,said", [
    ({"state": "idle", "background": True}, "Muse Code is working in the background."),
    ({"state": "waiting", "background": True}, "Muse Code is waiting for you."),
    ({"state": "idle"}, "Ready."),
])
def test_muse_turn_status_says_background_work(frame, update, said):
    from thechatplace.own_store import OwnSession
    record = OwnSession("synthetic-muse", "Synthetic Muse", r"C:\work", provider="muse")
    frame._muse_runtime[record.cli_session_id] = update
    assert frame._muse_state_text(record.to_info()).startswith(said)
