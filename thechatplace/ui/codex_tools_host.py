"""Adapt Codex's thread-scoped tool configuration to the shared dialog.

Codex sets a chat's tools when the chat is made, so a tool can't be turned on
or off in the chat itself: Copy with Tool On or Off makes a new copy with that
choice (#468). The words here say so plainly, in the dialog's note, when
Delete is pressed in the list and when Sign In can't work (#596). Whether
Codex can change a chat's tools in place is #597; signing in without such a
chat, #598.
"""
import re
import sys

import wx

from ..ui_text import key_name
from .codex_extras import copy_note

#: The first sentence of the note says what changing a tool does (#596).
NOTE = ("In a Codex chat, a tool can't be turned on or off in the chat itself: Copy with Tool On "
        "or Off makes a new copy of the chat with that choice, and this chat stays as it is. "
        "These are the tools Codex can connect to: the servers set up in Codex, and apps from "
        "ChatGPT. A tool can reach websites and services outside this chat's folder, so turn on "
        "only tools you trust. To add a server, use Codex in a terminal (codex mcp add). If a "
        "tool asks something The Chat Place can't show, it's declined for you.")

NOTE_READ_ONLY = ("These are the tools Codex can connect to in this chat's folder: the servers set "
                  "up in Codex, and apps from ChatGPT. This chat wasn't started in The Chat Place, "
                  "so its tools can't be changed here, and how each server is doing isn't shown. "
                  "To add a server or sign in to one, use Codex in a terminal (codex mcp add, "
                  "or codex mcp login and the server's name).")

#: When Codex's failure has no words fit to show (safe_reason gave none).
COULD_NOT_SAY = 'Codex could not say.'


def _press(label, platform=None):
    """Copy with Tool On or Off, named for pressing: "Copy with Tool On
    (Alt+O)" (both labels have &O). A Mac's buttons have no Alt mnemonic, so
    there it's the button's name alone."""
    name = label.replace('&', '')
    if (platform or sys.platform) == 'darwin':
        return name
    return f"{name} ({key_name('Alt+O', platform)})"


#: What ``codex mcp add`` accepts in a server's name (codex-cli 0.163: "use
#: letters, numbers, '-', '_', ':', '@', '/', '.'"). None of them means
#: anything to a shell, except '@' first in PowerShell (splatting).
_PLAIN_NAME = re.compile(r'[A-Za-z0-9_:/.-][A-Za-z0-9_:@/.-]*')


def _login_command(name, platform=None):
    """The Codex CLI command that signs in to server ``name`` (codex-cli
    0.163: ``codex mcp login <NAME>``). A name ``codex mcp add`` would take
    goes as it is; any other (hand-written in config.toml) is single-quoted,
    which PowerShell (Windows' terminal) and a Mac's or Linux's shell all
    take, each with its own way of putting a quote inside."""
    if _PLAIN_NAME.fullmatch(name):
        return 'codex mcp login ' + name
    inner = "''" if (platform or sys.platform) == 'win32' else "'\\''"
    return "codex mcp login '" + name.replace("'", inner) + "'"


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
        #: Selected servers no longer configured: no copy may name them.
        self.removed = set()

    def note(self):
        return NOTE if self.can_turn_off else NOTE_READ_ONLY

    def toggle_label(self, server):
        if not self.can_turn_off:
            return 'Turn &Off for This Chat'  # says why not when pressed
        return 'Copy with Tool &On' if server and server.off_for_chat else 'Copy with Tool &Off'

    def delete_text(self, server, platform=None):
        """What Delete in the list says instead of making a copy (#596)."""
        return ('In a Codex chat, a tool changes only in a new copy: press '
                + _press(self.toggle_label(server), platform) + ' to make one.')

    def sign_in_text(self, server, platform=None):
        """Why Sign In can't work for ``server`` here, and what to do instead.
        Codex signs in to a server only for a chat that has it on (its
        ``mcpServer/oauth/login`` takes the chat), #596."""
        if not self.can_turn_off:
            return ('This chat wasn\'t started in The Chat Place. To sign in to this tool, use Codex '
                    'in a terminal: ' + _login_command(server.name, platform) + '.')
        return ('Sign In works only in a chat with this tool on. Press '
                + _press('Copy with Tool &On', platform) + ' to make a copy with it on, '
                'then sign in from Connected Tools in that copy.')

    def load(self, done):
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
        self.frame._codex_work(lambda: self.frame.sessions.connected_tools(info, **selected), finished)

    def sign_in(self, server, done):
        if server.app_id:
            from ..claude_mcp import elicitation_from, is_web_url
            from ..platform_paths import open_url
            from .dialogs import ElicitationDialog
            if not is_web_url(server.install_url):
                done(None, 'Codex did not provide a sign-in or installation page for this app.')
                return
            request = elicitation_from('app-sign-in', {'mode': 'url', 'url': server.install_url,
                'mcp_server_name': server.label, 'message': 'Open this app page to connect it to Codex.'})
            dialog = ElicitationDialog(self.dialog or self.frame, self.info.title, request,
                feedback=self.frame._feedback)
            try:
                accepted = dialog.ShowModal() == wx.ID_OK and dialog.action == 'accept'
            finally:
                dialog.Destroy()
            if accepted:
                try:
                    open_url(server.install_url)
                except (OSError, ValueError):
                    done(None, 'Could not open the app page in your browser.')
                    return
            done('Press Refresh after connecting the app.' if accepted else 'App page was not opened.', '')
            return
        if not self.can_turn_off or server.off_for_chat:
            done(None, self.sign_in_text(server))
            return
        info = self.info
        attempt = object()
        self._logins[server.name] = attempt
        def started(result, error):
            if self.closed or self._logins.get(server.name) is not attempt:
                return
            if error or not isinstance(result, dict):
                done(None, 'Codex could not start signing in to this tool.')
                return
            from ..claude_mcp import elicitation_from, is_web_url
            from ..platform_paths import open_url
            from .dialogs import ElicitationDialog
            url, login_id = result.get('authorizationUrl'), result.get('loginId')
            if not isinstance(url, str) or not is_web_url(url):
                done(None, 'Codex did not return a usable sign-in address.')
                return
            request = elicitation_from('sign-in', {'mode': 'url', 'url': url,
                'mcp_server_name': server.name, 'message': 'Open this address to sign in to the connected tool.'})
            dialog = ElicitationDialog(self.dialog or self.frame, info.title, request, feedback=self.frame._feedback)
            try:
                accepted = dialog.ShowModal() == wx.ID_OK and dialog.action == 'accept'
            finally:
                dialog.Destroy()
            if not accepted or self.closed:
                done('Sign-in page was not opened.', '')
                return
            try:
                open_url(url)
            except (OSError, ValueError):
                done(None, 'Could not open the sign-in page in your browser.')
                return
            done('Finish signing in in your browser. ' + ('Keep Connected Tools open; the result will be announced.'
                 if isinstance(login_id, str) and login_id else 'Press Refresh here when you have finished.'), '')
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
                self.frame._feedback('Signed in to ' + server.label + '. Refresh shows its current status.')
                if self.dialog:
                    self.dialog.on_refresh()
            elif remaining:
                wx.CallLater(2000, self._poll_login, info, server, login_id, remaining - 1)
            else:
                self.frame._feedback('Sign-in to ' + server.label + ' was not confirmed. Refresh to check its status.')
        self.frame._codex_work(lambda: self.frame.sessions.connected_tool_action(info, server.name, 'login_status', login_id), checked)

    def reconnect(self, server, done):
        if server.app_id or server.is_session_tools:
            self.load(done)
            return
        def finished(result, error):
            if not self.closed:
                done(None, 'Codex could not reconnect this tool.') if error else done(result or [], '')
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
        # Every tools copy is made read-only with approval on request, as
        # Rewind's and Branch's are (codex_runtime.copy_with_tools), so each
        # question says so, as theirs do. It keeps the tools you chose (#602).
        # The question comes last, as Branch's does, so it's the last thing a
        # screen reader says before the buttons. The tool's own warning comes
        # after the note: a tool runs outside Codex's file rules.
        note = copy_note(tools=False)
        consent = (note + " A tool can reach websites and services outside this chat's folder, so "
                   'turn on only a tool you trust. Make a new copy of this chat with ' + server.name
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
            source.title + ' (tools copy)', names, apps), created)

    def close(self):
        self.closed = True
