"""The window's side of Ask Aside and Chat Options (#468), for Claude chats.

Ask Aside (Ctrl+Shift+Y) asks a question about the selected (or loaded)
Claude chat in a read-only copy that isn't saved (aside.py), so nothing is
added to the chat; Chat Options changes the agent, instructions, output style
and more folders of one of The Chat Place's own Claude chats for its later
turns (chat_options.py). New Session and Continue Here use the same dialog
before a chat starts.
"""
import os
import time

import wx

from .. import announce, platform_paths, sound_events
from ..aside import (COMPACTED, COMPACTED_SINCE, COWORK_REFUSED, NOTHING_FINISHED, AsideEvent,
                     AsideRunner, build_aside_command, finished_cut_from_file)
from ..chat_options import OptionsChange, choices_from_init
from ..claude_cli import ResumeRefused
from ..speech import speaker
from .dialogs import AskAsideDialog, ChatOptionsDialog


def _folder_key(folder: str) -> str:
    from .. import workplaces
    return workplaces.folder_key(folder or "")


def _alive(window):
    """``window`` while it's still there, else None (a dialog destroyed
    before something it started was said)."""
    return window if window else None


def _aside_with_cost(text: str, cost: str, failed: bool) -> str:
    """An aside's answer as said, with what it cost on API credits (#663):
    before the answer, as a reply's is, so a long one doesn't hide it:
    "Answer, 2 cents. …", "Answer, at no cost. …", "Answer, cost not
    known. …"."""
    from ..billing import cost_sentence
    if failed:
        return text if "It cost" in text else f"{text.rstrip()} {cost_sentence(cost)}"
    for head in ("Answer.", "Answer:"):
        if text.startswith(head):
            return f"Answer, {announce.cost_phrase(cost)}{head[-1]}{text[len(head):]}"
    return f"{text.rstrip()} {cost_sentence(cost)}"


def _give_focus_back(window) -> None:
    """Only if Windows hasn't put it back already: setting it again had a
    screen reader say the row twice (#468)."""
    if window and wx.Window.FindFocus() is not window:
        window.SetFocus()


class ClaudeExtras:
    # -- the agents and output styles Claude Code offers in a folder

    def _chat_choices_cache(self) -> dict:
        cache = getattr(self, "_chat_choices", None)
        if cache is None:
            cache = self._chat_choices = {}
        return cache

    def _remember_chat_choices(self, cwd: str, init_body) -> None:
        """Keep a turn's ``initialize`` answer's agents and styles for its
        folder, so Chat Options has them without asking Claude Code again."""
        choices = choices_from_init(init_body)
        if choices is not None and cwd:
            self._chat_choices_cache()[_folder_key(cwd)] = choices

    def _load_chat_choices(self, folder: str, done) -> None:
        """``done(choices)`` on the UI thread with the folder's agents and
        styles (None if Claude Code couldn't say): kept ones at once, and
        always asked for in the background (no turn, no cost), when ``done`` is
        called again. Kept ones alone would miss an agent added since the last
        turn, however long ago that was."""
        from . import main_frame
        cache = self._chat_choices_cache()
        key = _folder_key(folder)
        known = cache.get(key)
        if known is not None:
            wx.CallAfter(done, known)
        lookup = platform_paths.find_claude()
        if not lookup.path or not folder:
            if known is None:
                wx.CallAfter(done, None)
            return

        def work():
            choices = main_frame.fetch_chat_choices(lookup.path, folder)
            wx.CallAfter(finished, choices)

        def finished(choices):
            if self._gone():
                return
            if choices is not None:
                cache[key] = choices
            if choices is not None or known is None:
                done(choices)  # a failed refresh leaves the kept ones standing
        try:
            self._pool.submit(work)
        except RuntimeError:  # the pool is shut down: the window is closing
            pass

    def _aside_history(self, info) -> list:
        """This chat's Ask Aside questions, for as long as the app is open: in
        memory only, as an aside is throwaway, but closing the dialog
        shouldn't lose an answer you've just had. Claude Code's /btw keeps
        them about as long. Codex's chats use it too."""
        from ..aside import KEPT_QUESTIONS
        history = self._aside_histories.setdefault((info.provider, info.cli_session_id), [])
        del history[:-KEPT_QUESTIONS]
        return history

    # -- Chat, Chat Settings, Chat Options

    def on_chat_options(self):
        """Chat, Chat Settings, Chat Options (#468): agent, instructions, output style and
        more folders for one of The Chat Place's own Claude chats, from its
        next turn."""
        from .main_frame import kind_name
        info = self._selected_session()
        if info is None:
            self._feedback("No session selected.")
            return
        if info.provider in ('codex', 'codex-api'):
            self._change_codex_settings(info, "chat options")
            return
        if info.provider == "muse" and info.is_own and self._can_edit(info):
            # Checked with Muse Code 1.4.20: its protocol has no agent,
            # instructions or output style, and it refuses workspaceRoots
            # (dev-notes/muse-code-chat-menu.md). A chat started in Muse Code
            # gets the read-only refusal below instead.
            self._feedback("Muse Code doesn't offer chat options yet: it has no agent, "
                           "instructions or output style for its chats, and doesn't accept "
                           "more folders.")
            return
        if ((info.provider != "claude" or info.source == "kept")
                and not self._require_capability("chat_options", info)):
            return  # another provider's chat, or a kept copy: says why
        if not info.is_own:
            self._feedback(f"{info.title} is {kind_name(info)}: Chat Options are for Claude "
                           "chats started here.")
            return
        own = self.store.get(info.cli_session_id)
        if own is None:
            self._feedback(f"Couldn't find {info.title} in The Chat Place's sessions.")
            return
        before = own.chat_options()
        returning_to = wx.Window.FindFocus()
        dialog = ChatOptionsDialog(self, info.title, own.cwd, options=before,
                                   folders=list(own.allowed_dirs),
                                   choices=self._chat_choices_cache().get(_folder_key(own.cwd)),
                                   load_choices=self._load_chat_choices, existing=True,
                                   feedback=self._feedback, model=own.model,
                                   billing=own.billing)  # #658
        try:
            if dialog.ShowModal() != wx.ID_OK:
                _give_focus_back(returning_to)
                return
            after, folders = dialog.options(), dialog.chosen_folders()
        finally:
            dialog.Destroy()
        _give_focus_back(returning_to)
        own = self.store.get(info.cli_session_id)
        if own is None:
            self._feedback("This chat is no longer in The Chat Place.")
            return
        change = OptionsChange(before, after,
                               added_dirs=[f for f in folders if f not in own.allowed_dirs],
                               removed_dirs=[f for f in own.allowed_dirs if f not in folders])
        if not change.options_changed and not change.added_dirs and not change.removed_dirs:
            self._feedback("Chat options unchanged.")
            return
        now = int(time.time() * 1000)
        times = {f: t for f, t in own.allowed_dirs_at.items() if f in folders}
        times.update({f: now for f in change.added_dirs})
        changes = dict(allowed_dirs=folders, allowed_dirs_at=times)
        if change.options_changed:
            changes.update(agent=after.agent, instructions=after.instructions,
                           output_style=after.output_style)
            if self._session_exists(own) or own.fork_source:
                # Claude Code keeps the system prompt it recorded on the
                # chat's first turn, which would hide the change (#468). A
                # copy not yet started (a branch) inherits its source's
                # recorded prompt the same way.
                changes["prompt_fresh"] = True
        if not self._store_write(self.store.update, own.cli_session_id, **changes):
            return
        said = change.said(info.title)
        if own.cli_session_id in self._runners:
            said = said[:-1] + "; the turn running now carries on as it started."
        self._feedback(said)
        self._update_heading()

    # -- Chat, Ask Aside

    def on_ask_aside(self):
        """Chat, Ask Aside (Ctrl+Shift+Y, #468): a question about the
        selected (or loaded) Claude chat, answered without being added to it."""
        info = self._selected_session()
        if info is None:
            self._feedback("No session selected.")
            return
        if info.provider in ('codex', 'codex-api'):
            self._codex_ask_aside(info)
            return
        if info.provider == "muse":
            # Before the capability check: Muse grants "aside" to chats
            # started here, which must never reach the Claude path below.
            self._muse_ask_aside(info)
            return
        if info.provider == "claude" and info.cowork:
            self._feedback(COWORK_REFUSED)
            return
        if not self._require_capability("aside", info):
            return  # says why: another provider's chat, or a kept copy
        own = self.store.get(info.cli_session_id) if info.is_own else None
        if own is not None and not self._session_exists(own):
            self._feedback(f"{info.title} has no conversation yet to ask about.")
            return
        if own is None and info.transcript_path() is None:
            self._feedback(f"{info.title}'s conversation is no longer on disk, so there's "
                           "nothing to ask about.")
            return
        lookup = platform_paths.find_claude()
        if not lookup.path:
            wx.MessageBox(lookup.problem, "Ask Aside", wx.OK | wx.ICON_ERROR, self)
            return
        model = own.model if own is not None else ""
        # An aside on a Fable chat runs on Fable (#722): only on a plan that
        # includes it, said before the dialog opens. Usage credits are asked
        # about for each question (``ask`` below): an aside is often one
        # request, so asking first is the way to stop it.
        problem, _allow = self._fable_send_problem(own, plan_only=True)
        if problem:
            wx.MessageBox(problem, "Ask Aside", wx.OK | wx.ICON_WARNING, self)
            return
        cowork_ids = self._snapshot.cowork_cli_ids
        allowed_dirs = own.allowed_dirs if own is not None else ()

        def command_at(cut="", model=model):
            return build_aside_command(lookup.path, info.cli_session_id, model,
                                       cowork_ids=cowork_ids, allowed_dirs=allowed_dirs,
                                       resume_at=cut)
        try:
            command_at()  # refused now, before the dialog opens, rather than on asking
        except (ResumeRefused, ValueError) as exc:
            self._feedback(str(exc))
            return
        cwd = own.cwd if own is not None else info.cwd
        if not cwd or not os.path.isdir(cwd):
            self._feedback(f"{info.title}'s folder ({cwd or 'unknown'}) doesn't exist any more, "
                           "so Claude can't be asked about it.")
            return
        from . import main_frame
        # An aside about a chat on API credits is charged as the chat is
        # (#658): the same key, ledger and limits, and said like any send.
        api = own is not None and self._api_own(own.cli_session_id) is not None
        if api:
            problem = self._api_send_problem(own.cli_session_id)
            if problem is not None:
                if problem:
                    wx.MessageBox(problem, "Ask Aside", wx.OK | wx.ICON_WARNING, self)
                else:
                    self._feedback("Not asked.")
                return
        billing = ({k: v for k, v in self._billing_kwargs(own.cli_session_id).items()
                    if k != "chat_id"}  # an aside's chat is its command's
                   if api else {})
        # An aside's "Sent on API credits" is kept under a key of its own,
        # not the chat's: the chat's turn ending doesn't settle it, nor the
        # aside's ending the turn's (#663).
        notice_key = f"aside:{own.cli_session_id}" if api else ""

        def ask(question, on_event):
            # Each question is asked about on its own (#722): Fable's plan,
            # and usage credits once a Fable turn said they're in use.
            # The chat is read now, and its model then is the one the
            # question runs on, so what's checked is what runs: Change Model
            # chosen here, with the dialog still open, applies.
            now = self.store.get(own.cli_session_id) if own is not None else None
            problem, allow = self._fable_send_problem(now, aside=True)
            if problem is not None:
                return problem or "Not asked."
            model_now = now.model if now is not None else model

            def command_now(cut=""):
                return command_at(cut, model_now)

            def arrived(event):
                if event.kind == "sent":
                    # The question really went on API credits (#663):
                    # said now, from the dialog, not as Ask is pressed.
                    self._api_message_sent(own.cli_session_id, window=_alive(dialog),
                                           about=notice_key)
                    return
                try:
                    on_event(event)
                finally:
                    if notice_key and event.kind != "text":
                        # However it ended (answered, stopped, failed, the
                        # dialog closed, or nothing to say at this level),
                        # saying it again later would be stale (#663).
                        speaker.forget(notice_key)

            def start(at):
                return main_frame.AsideRunner(
                    at, cwd, question, lambda event: wx.CallAfter(arrived, event),
                    allow_overage=allow,
                    on_limits=lambda data, model: wx.CallAfter(
                        self._note_limits, info.cli_session_id, data, model),
                    **billing)

            def begin(at):
                runner = start(at)
                runner.start()
                return runner
            # Where the copy ends is decided from the transcript as each
            # question is asked (#559): not from whether a turn looks to be
            # running, since a stopped turn leaves the same unfinished end and
            # one just sent may not be written yet.
            try:
                return _AsideAtFinishedTurn(lambda: _finished_cut(info), command_now, begin,
                                            on_event).begin(self._pool.submit)
            except RuntimeError:  # the pool is shut down: the window is closing
                return "The Chat Place is closing, so Claude can't be asked now."

        def say(answer, failed=False, cost=""):
            # The answer to a key press: it may cut in (#468), as the reply
            # to what you just did; the level decides how much is said. On
            # API credits, what it cost goes with it (#663). From the
            # dialog's own window, for VoiceOver.
            text = (f"Not answered. {answer}" if failed
                    else announce.aside_text(answer, self.speech.announce))
            if text and cost:
                text = _aside_with_cost(text, cost, failed)
            self._sound(sound_events.ASIDE_FAILED if failed else sound_events.ASIDE_ANSWERED)
            if text:
                # With its cost, it settles the aside's "Sent on API
                # credits" (speech.Speaker, #663); without, that's said once
                # more after it if the answer cut it off.
                self._say(text, window=_alive(dialog), about=notice_key if cost else "")
            if cost and not self.speech.enabled:
                # Silent: the cost is still said, as Settings asks (#663).
                from ..billing import cost_sentence
                self._speak_announcement(cost_sentence(cost), interrupt=False,
                                         window=_alive(dialog), about=notice_key)

        def cost_words(event):
            """What an aside on API credits cost, as said, or "" (#663)."""
            if not api or not self.speech.api_say_cost or not getattr(event, "sent", False):
                return ""
            from ..billing import cost_words as words
            return words(event.cost_usd, event.cost_complete)
        returning_to = wx.Window.FindFocus()
        dialog = AskAsideDialog(self, info.title, ask, say=say, copy=self._copy_text,
                                feedback=lambda text: self._feedback(text, window=_alive(dialog)),
                                history=self._aside_history(info), cost_words=cost_words)
        try:
            dialog.ShowModal()
        finally:
            dialog.settle()
            dialog.Destroy()
            if notice_key:
                speaker.forget(notice_key)  # closed: the aside is over (#663)
        _give_focus_back(returning_to)


def _finished_cut(info):
    """Where an aside's copy of a Claude chat ends (``aside.finished_cut``),
    or None when its transcript isn't on disk. Read from the file each time:
    the open chat's reader can be a refresh behind."""
    path = info.transcript_path()
    return None if path is None else finished_cut_from_file(path)


class _AsideAtFinishedTurn:
    """One aside question (#559). The copy has to end with the chat's newest
    finished reply, or Claude answers an unfinished turn's message in it too,
    so the transcript is read off the UI thread to find where that is, then
    the copy is asked. It stands in for the runner: ``cancel`` stops it, and
    until the runner starts says "Stopped." itself, later, as a runner does.
    Everything after ``begin`` reaches ``on_event`` on the UI thread."""

    def __init__(self, read, command_at, start, on_event):
        self._read, self._command_at, self._start = read, command_at, start
        self._on_event = on_event
        self._runner = None
        self._stopped = False

    def begin(self, submit):
        submit(self._find_cut)
        return self

    def _find_cut(self):
        try:
            cut = self._read()
        except Exception:  # noqa: BLE001 - said in the dialog, below
            cut = None
        wx.CallAfter(self._ask, cut)

    def _ask(self, cut):
        if self._stopped:
            return  # stopped, or the dialog closed, while the chat was read
        if cut is None:
            self._on_event(AsideEvent("failed", "Couldn't read this chat's conversation to "
                                                "find its last finished reply."))
            return
        if not cut or cut == COMPACTED:
            self._on_event(AsideEvent("failed", COMPACTED_SINCE if cut else NOTHING_FINISHED))
            return
        try:
            command = self._command_at(cut)
        except (ResumeRefused, ValueError) as exc:
            self._on_event(AsideEvent("failed", str(exc)))
            return
        self._runner = self._start(command)

    def cancel(self):
        if self._stopped:
            return
        self._stopped = True
        if self._runner is not None:
            self._runner.cancel()
        else:
            # Later, as a runner's comes: the dialog may be closing, and
            # one said now would be spoken on the way out.
            wx.CallAfter(self._on_event, AsideEvent("failed", "Stopped."))


# Kept importable from here for tests that stand in for the runner.
__all__ = ["ClaudeExtras", "AsideRunner"]
