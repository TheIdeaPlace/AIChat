"""Adapt Codex's thread-scoped tool configuration to the shared dialog."""
import wx


class CodexConnectedTools:
    provider_name = 'Codex'
    reconnect_label = 'Read St&atus'
    can_turn_off = True

    def __init__(self, frame, info):
        self.frame, self.info = frame, info
        self.record = frame.store.get(info.cli_session_id, provider='codex') if info.is_own else None
        self.can_turn_off = self.record is not None
        self.closed = False
        self.busy = False

    def note(self):
        return ('Codex MCP servers configured for this folder. Apps and plugins remain unavailable. '
                'Changing a tool creates a new read-only copy of this chat with that selection; '
                'the original chat is kept. Enabled tools may read or change external services, '
                'outside the folder sandbox. Add servers and sign in using Codex CLI. '
                'Server questions that TCP cannot display are declined.')

    def toggle_label(self, server):
        return 'Copy with Tool &On' if server and server.off_for_chat else 'Copy with Tool &Off'

    def load(self, done):
        def finished(rows, error):
            if not self.closed:
                done(None if error else rows, rows if error else '')
        self.frame._codex_work(lambda: self.frame.sessions.connected_tools(self.info), finished)

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
        names = [name for name in names if name != server.name]
        if not off:
            names.append(server.name)
        consent = ('Enable ' + server.name + ' in a new copy? Its tools can read or change external '
                   'services outside the folder sandbox. Only enable a server you trust.' if not off else
                   'Create a new copy with ' + server.name + ' turned off?')
        if wx.MessageBox(consent, 'Copy Codex Chat with Connected Tools',
                         wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self.frame) != wx.YES:
            self.load(done)
            return
        source = self.info
        self.busy = True
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
