"""Adapt Codex's thread-scoped tool configuration to the shared dialog."""
import wx

NOTE_READ_ONLY = ("Codex MCP servers configured for this folder. This chat wasn't started in The "
                  "Chat Place, so tools can't be changed for it here, and how each server is "
                  "doing isn't shown. Add servers and sign in using Codex CLI.")

#: When Codex's failure has no words fit to show (safe_reason gave none).
COULD_NOT_SAY = 'Codex could not say.'


class CodexConnectedTools:
    provider_name = 'Codex'
    reconnect_label = 'Read St&atus'
    can_turn_off = True
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
        self.busy = False
        #: Selected servers no longer configured: no copy may name them.
        self.removed = set()

    def note(self):
        if not self.can_turn_off:
            return NOTE_READ_ONLY
        return ('Codex MCP servers configured for this folder. Apps and plugins remain unavailable. '
                'Changing a tool creates a new read-only copy of this chat with that selection; '
                'the original chat is kept. Enabled tools may read or change external services, '
                'outside the folder sandbox. Add servers and sign in using Codex CLI. '
                'Server questions that The Chat Place cannot display are declined.')

    def toggle_label(self, server):
        if not self.can_turn_off:
            return 'Turn &Off for This Chat'  # says why not when pressed
        return 'Copy with Tool &On' if server and server.off_for_chat else 'Copy with Tool &Off'

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
        selected = {} if record is None else {'selected': tuple(record.provider_options.get('mcp_servers', ()))}
        self.frame._codex_work(lambda: self.frame.sessions.connected_tools(info, **selected), finished)

    def sign_in(self, server, done):
        done('Sign in to ' + server.name + ' using Codex CLI, then reopen the chat and refresh.', '')

    def reconnect(self, server, done):
        self.load(done)

    def set_off(self, server, off, done):
        if self.busy:
            done(None, 'Still creating the Codex copy. Wait for it to finish.')
            return
        record = self.record
        if record is None or self.frame.store.get(record.cli_session_id, provider='codex') is not record:
            done(None, 'This chat is no longer in The Chat Place.')
            return
        names = list(record.provider_options.get('mcp_servers', []))
        names = [name for name in names if name != server.name and name not in self.removed]
        if not off:
            names.append(server.name)
        consent = ('Enable ' + server.name + ' in a new copy? Its tools can read or change external '
                   'services outside the folder sandbox. Only enable a server you trust.' if not off else
                   'Create a new copy without ' + server.name + "? It's no longer in Codex's configuration."
                   if server.missing else 'Create a new copy with ' + server.name + ' turned off?')
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
                self.frame._feedback('Saved ' + copy.title + '. No prompt was sent.')
                return
            self.record, self.info = copy, copy.to_info()
            self.frame.open_session(self.info)
            self.frame.refresh_sessions()
            self.frame._feedback('Opened ' + copy.title + '. The original chat is kept.')
            self.load(done)
        self.frame._codex_work(lambda: self.frame.sessions.copy_with_tools(source,
            source.title + ' (tools copy)', names), created)

    def close(self):
        self.closed = True
