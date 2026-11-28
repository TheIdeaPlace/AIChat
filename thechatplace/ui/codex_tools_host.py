"""Adapt Codex's thread-scoped tool configuration to the shared dialog.

Codex sets a chat's tools when the chat is made, so a tool can't be turned on
or off in the chat itself: Copy with Tool On or Off makes a new copy with that
choice (#468). The words here say so plainly, in the dialog's note, when
Delete is pressed in the list (#596). A loaded chat's tools still require a
copy (#597); standalone OAuth sign-in works without loading or changing a chat
(#598).
"""
import sys

import wx

from ..ui_text import key_name
from .codex_extras import copy_note

#: How each question before a tools copy starts, as Branch's does (#602).
COPY_LEAD = "A new copy of this chat will be made, and this chat is kept. "

#: The first sentence of the note says what changing a tool does (#596).
NOTE = ("In a Codex chat, a tool can't be turned on or off in the chat itself: Copy with Tool On "
        "or Off makes a new copy of the chat with that choice, and this chat stays as it is. "
        "These are the tools Codex can connect to: the servers set up in Codex, and apps from "
        "ChatGPT. A tool can reach websites and services outside this chat's folder, so turn on "
        "only tools you trust. Sign In works even with a tool off and doesn't turn it on. "
        "To add a server, use Codex in a terminal (codex mcp add). If a "
        "tool asks something The Chat Place can't show, it's declined for you.")

NOTE_READ_ONLY = ("These are the tools Codex can connect to in this chat's folder: the servers set "
                  "up in Codex, and apps from ChatGPT. This chat wasn't started in The Chat Place, "
                  "so its tools can't be changed here, and how each server is doing isn't shown. "
                  "Sign In connects your account without changing this chat's tools. "
                  "To add a server, use Codex in a terminal (codex mcp add).")

#: When Codex's failure has no words fit to show (safe_reason gave none).
COULD_NOT_SAY = 'Codex could not say.'

#: Sign In's failure with no known reason. Still a next step, as Details
#: promises (#729): Codex's own command shows its reason, which the window
#: doesn't, since it can carry the server's text.
COULD_NOT_START = ("Codex could not start signing in to this tool. To see Codex's reason, run "
                   "codex mcp login followed by the tool's name in a terminal.")


#: Said when you decline or close the sign-in page's question; focus then
#: goes back to Sign In (#729's acceptance test: it went to Refresh, as
#: after a sign-in).
SIGN_IN_NOT_OPENED = 'Sign-in page was not opened.'
APP_PAGE_NOT_OPENED = 'App page was not opened.'

#: Reconnect's failure with no known reason, with a next step (#729's
#: acceptance test), as COULD_NOT_START is for Sign In.
COULD_NOT_RECONNECT = ("Codex didn't say why. Close Connected Tools, open it again and press "
                       "Reconnect; if it still fails, run codex mcp list in a terminal to check "
                       "the server's settings.")


def sign_in_dialog(parent, server, url, feedback=None):
    """The question before Sign In opens a page (#598, #729): "Sign In to
    notion", or "Connect" for a ChatGPT app, opening on the Address box,
    whose one line says where the page is and what its whole address is.
    Nothing waits on the answer, so there's no Answer Later: Escape declines.
    The probe builds it here too, so its picture is the real one."""
    from ..claude_mcp import elicitation_from
    from .dialogs import ElicitationDialog
    if server.app_id:
        title = f'Connect {server.label}'
        message = (f'Open in Browser opens the app page above, where you can connect '
                   f'{server.label} to Codex. Decline leaves it unopened.')
    else:
        title = f'Sign In to {server.label}'
        message = (f'Open in Browser opens the address above, where you sign in to '
                   f'{server.label}. Decline leaves it unopened.')
    request = elicitation_from('app-sign-in' if server.app_id else 'sign-in',
                               {'mode': 'url', 'url': url, 'mcp_server_name': server.label,
                                'message': message})
    return ElicitationDialog(parent, '', request, feedback=feedback, title=title, can_wait=False)


def _reasons(*codes):
    from ..providers.codex_rpc import CodexSignInRefused
    return frozenset(CodexSignInRefused.REASONS[code] for code in codes)


#: Sign In refusals that aren't failures: there's nothing to sign in to.
_NOTHING_TO_SIGN_IN = _reasons('not_needed', 'token')


def _press(label, platform=None):
    """Copy with Tool On or Off, named for pressing: "Copy with Tool On
    (Alt+O)" (both labels have &O). A Mac's buttons have no Alt mnemonic, so
    there it's the button's name alone."""
    name = label.replace('&', '')
    if (platform or sys.platform) == 'darwin':
        return name
    return f"{name} ({key_name('Alt+O', platform)})"


class CodexConnectedTools:
    provider_name = 'Codex'
    reconnect_label = '&Reconnect'
    can_turn_off = True
    #: Delete in the list doesn't make a copy, as it would turn a tool off in
    #: a Claude chat: a whole new chat from one key is too much (#596).
    delete_toggles = False
    # Reading the status of a tool that's off means having it on, which for
    # Codex makes a copy: there's no turning it on in this chat (#468).
    turn_on_hint = ('Copy with Tool On makes a new copy of this chat with it on, '
                    'where you can read its status. This chat stays as it is.')
    #: The Connected Tools dialog, once it's open: it's the window in use, and
    #: a box parented on the window it disabled would come up behind it.
    dialog = None

    def __init__(self, frame, info):
        self.frame, self.info = frame, info
        self.record = frame.store.get(info.cli_session_id, provider='codex') if info.is_own else None
        self.can_turn_off = self.record is not None
        self.closed = False
        self._logins = {}
        self.busy = False
        self._refresh = False
        #: Selected servers no longer configured: no copy may name them.
        self.removed = set()

    def note(self):
        return NOTE if self.can_turn_off else NOTE_READ_ONLY

    def toggle_label(self, server):
        if server is not None and server.informational:
            # The "Apps unavailable" row isn't a tool: "Copy with Tool On"
            # there named a change it can't make (#729's review). Pressing it
            # says the row's notice. &O still, as the other labels have it.
            return 'Change T&ool'
        if not self.can_turn_off:
            # Not shown: the dialog hides and disables it in a chat started
            # elsewhere (#729's acceptance test), and Delete's box there is
            # titled "Tools", so this label is never seen or heard.
            return 'Turn &Off for This Chat'
        return 'Copy with Tool &On' if server and server.off_for_chat else 'Copy with Tool &Off'

    def delete_text(self, server, platform=None):
        """What Delete in the list says instead of making a copy (#596)."""
        return ('In a Codex chat, a tool changes only in a new copy: press '
                + _press(self.toggle_label(server), platform) + ' to make one.')

    def read_again(self):
        """Refresh was pressed: the next read asks Codex for its app catalog
        afresh, where opening the list takes what Codex already has (#729)."""
        self._refresh = True

    def load(self, done):
        refresh, self._refresh = self._refresh, False
        def finished(rows, error):
            if not error:
                self.removed = {row.name for row in rows or () if row.missing}
            if not self.closed:
                if error:
                    done(None, rows or COULD_NOT_SAY)
                else:
                    done(rows or [], '')
        record, info = self.record, self.info
        selected = {} if record is None else {'selected': tuple(record.provider_options.get('mcp_servers', ())),
                                               'apps': tuple(record.provider_options.get('apps', ()))}
        # Not _codex_work: the app catalog can take a minute a page, which on
        # the window's shared pool held up opening chats (#729's review). A
        # dialog reopened while this chat's list is still being read joins
        # that read, a Refresh's included; ``finished`` above does nothing
        # for a closed one.
        key = ('connected-tools', getattr(info, 'key', None) or info.cli_session_id,
               tuple(sorted(selected.items())))
        self.frame._codex_tools_load(key, lambda: self.frame.sessions.connected_tools(
            info, refresh=refresh, **selected), finished)

    @staticmethod
    def nothing_to_sign_in(server):
        """What Sign In says, without asking Codex, for a server whose
        configuration has nothing to sign in to: a stdio server
        ("unsupported") or one with a bearer token (``config_auth``; #729).
        "" otherwise."""
        from ..providers.codex_rpc import CodexSignInRefused
        from ..providers.codex_tools import NO_SIGN_IN
        code = NO_SIGN_IN.get(getattr(server, 'auth', '')) if not server.app_id else None
        return f"{server.label}: {CodexSignInRefused.REASONS[code]}" if code else ''

    def _ask_to_open(self, server, url):
        """Whether you chose Open in Browser for ``url`` (sign_in_dialog)."""
        dialog = sign_in_dialog(self.dialog or self.frame, server, url, feedback=self.frame._feedback)
        try:
            return dialog.ShowModal() == wx.ID_OK and dialog.action == 'accept'
        finally:
            dialog.Destroy()

    def sign_in(self, server, done):
        """``done(text, error, outcome)``: ``outcome`` says what happened,
        so the dialog knows where focus goes next without reading ``text``
        (#729's acceptance test and review): UNCHANGED (you declined the
        page, or there's nothing to sign in to) back to Sign In, OPENED (a
        page opened) to Refresh. An error goes back to Sign In."""
        from .dialogs import SIGN_IN_OPENED, SIGN_IN_UNCHANGED
        if server.app_id:
            from ..claude_mcp import is_web_url
            from ..platform_paths import open_url
            if not is_web_url(server.install_url):
                done(None, 'Codex did not provide a sign-in or installation page for this app.')
                return
            if not self._ask_to_open(server, server.install_url):
                done(APP_PAGE_NOT_OPENED, '', SIGN_IN_UNCHANGED)
                return
            try:
                open_url(server.install_url)
            except (OSError, ValueError):
                done(None, 'Could not open the app page in your browser.')
                return
            done('Press Refresh after connecting the app.', '', SIGN_IN_OPENED)
            return
        text = self.nothing_to_sign_in(server)
        if text:  # the dialog asks first; this is for any other caller
            done(text, '', SIGN_IN_UNCHANGED)
            return
        info = self.info
        attempt = object()
        self._logins[server.name] = attempt
        def started(result, error):
            if self.closed or self._logins.get(server.name) is not attempt:
                return
            if error and isinstance(result, str) and result in _NOTHING_TO_SIGN_IN:
                # Not a failure: there's nothing to sign in to. The row said
                # otherwise only if Codex's configuration changed after the
                # list was read.
                done(f"{server.label}: {result}", '', SIGN_IN_UNCHANGED)
                return
            if error or not isinstance(result, dict):
                # The app's own words for a known refusal (safe_reason gives
                # CodexSignInRefused's), never Codex's or the server's.
                done(None, result if error and isinstance(result, str) and result
                     else COULD_NOT_START)
                return
            from ..claude_mcp import is_web_url
            from ..platform_paths import open_url
            url, login_id = result.get('authorizationUrl'), result.get('loginId')
            if not isinstance(url, str) or not is_web_url(url):
                done(None, 'Codex did not return a usable sign-in address.')
                return
            accepted = self._ask_to_open(server, url)
            if self.closed:
                return  # nobody to tell, and no page opened
            if not accepted:
                done(SIGN_IN_NOT_OPENED, '', SIGN_IN_UNCHANGED)
                return
            try:
                open_url(url)
            except (OSError, ValueError):
                done(None, 'Could not open the sign-in page in your browser.')
                return
            done('Finish signing in in your browser. ' + ('Keep Connected Tools open; the result will be announced.'
                 if isinstance(login_id, str) and login_id else 'Press Refresh here when you have finished.'), '',
                 SIGN_IN_OPENED)
            if isinstance(login_id, str) and login_id:
                self._logins[server.name] = login_id
                self._poll_login(info, server, login_id, 90)
        self.frame._codex_work(lambda: self.frame.sessions.connected_tool_action(info, server.name, 'login'), started)

    def _poll_login(self, info, server, login_id, remaining):
        if self.closed or self.info is not info or self._logins.get(server.name) != login_id:
            return
        def checked(result, error):
            if self.closed or self.info is not info or self._logins.get(server.name) != login_id:
                return
            if error or result is False:
                self.frame._feedback('Codex could not complete sign-in to ' + server.label + '.')
            elif result is True:
                self.frame._feedback('Signed in to ' + server.label + '. Reconnect an enabled tool to use the sign-in.')
                if self.dialog:
                    # Not a fresh app catalog: a sign-in can't change it
                    # (#729); and not over something else under way.
                    self.dialog.refresh_when_free()
            elif remaining:
                wx.CallLater(2000, self._poll_login, info, server, login_id, remaining - 1)
            else:
                self.frame._feedback('Sign-in to ' + server.label + ' was not confirmed. Refresh to check its status.')
        self.frame._codex_work(lambda: self.frame.sessions.connected_tool_action(info, server.name, 'login_status', login_id), checked)

    def reconnect(self, server, done):
        if server.app_id or server.is_session_tools:
            self.load(done)
            return
        if self.record is None:
            # Said without asking Codex, which would refuse: a chat started
            # elsewhere has no connection here to reconnect.
            from ..providers.codex_rpc import CodexReconnectRefused
            done(None, CodexReconnectRefused.REASONS['not_own'])
            return
        def finished(result, error):
            if self.closed:
                return
            if error:
                # The app's own words for a known refusal (safe_reason gives
                # CodexReconnectRefused's), never Codex's or the server's.
                done(None, result if isinstance(result, str) and result else COULD_NOT_RECONNECT)
            else:
                done(result or [], '')
        self.frame._codex_work(lambda: self.frame.sessions.connected_tool_action(self.info, server.name, 'reconnect'), finished)

    def set_off(self, server, off, done):
        if self.busy:
            done(None, 'Still creating the Codex copy. Wait for it to finish.')
            return
        record = self.record
        if record is None or self.frame.store.get(record.cli_session_id, provider='codex') is not record:
            done(None, 'This chat is no longer in The Chat Place.')
            return
        if server.is_session_tools:
            done(None, 'The built-in session tools stay available while The Chat Place is running.')
            return
        apps = [a for a in record.provider_options.get('apps', [])
                if a != server.app_id and 'app:' + a not in self.removed]
        names = list(record.provider_options.get('mcp_servers', []))
        names = [name for name in names if name != server.name and name not in self.removed]
        if not off:
            (apps if server.app_id else names).append(server.app_id or server.name)
        # Every tools copy keeps the original's settings, as Rewind's and
        # Branch's do (codex_runtime.copy_with_tools; #596, decision 3), so
        # each question says so, as theirs do. Its tools are the ones you
        # chose (#602).
        # The question comes last, as Branch's does, so it's the last thing a
        # screen reader says before the buttons, and starts by saying a copy is
        # made, so "the new chat" means something. The tool's own warning comes
        # after the note: a tool is a program of its own, which Codex's file
        # rules may not cover, so it names files too.
        note = COPY_LEAD + copy_note(tools=False)
        consent = (note + " A tool can reach websites, services and files outside this chat's folder, "
                   'so turn on only a tool you trust. Make a new copy of this chat with ' + server.name
                   + ' on?' if not off else
                   server.name + " is no longer in Codex's configuration. " + note
                   + ' Make a new copy of this chat without ' + server.name + '?' if server.missing else
                   note + ' Make a new copy of this chat with ' + server.name + ' off?')
        if wx.MessageBox(consent, 'Copy Codex Chat with Connected Tools',
                         wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
                         self.dialog or self.frame) != wx.YES:
            self.load(done)
            return
        source = self.info
        self.busy = True
        self.frame._feedback('Creating the Codex copy.')
        def created(copy, error):
            self.busy = False
            if error:
                if not self.closed:
                    done(None, copy or 'Could not create the Codex copy.')
                return
            copy.forked_from = source.title
            if not self.frame._store_write(self.frame.store.add, copy):
                done(None, 'Codex made a copy, but The Chat Place could not save it. No prompt was sent.')
                return
            if self.closed:
                self.frame.refresh_sessions()
                self.frame._feedback('Saved ' + copy.title + '. No prompt was sent. '
                                     + copy_note(short=True))
                return
            self.record, self.info = copy, copy.to_info()
            self.frame.open_session(self.info)
            self.frame.refresh_sessions()
            self.frame._feedback('Opened ' + copy.title + '. ' + copy_note(short=True)
                                 + ' The original chat is kept.')
            self.load(done)
        self.frame._codex_work(lambda: self.frame.sessions.copy_with_tools(source,
            source.title + ' (tools copy)', names, apps, own=record), created)

    def close(self):
        self.closed = True
