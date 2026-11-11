"""Explicit per-thread access to configured Codex MCP servers (#467)."""
from dataclasses import dataclass

#: A status that isn't already in words.
STATUS_WORDS = {'needs-auth': 'needs sign-in'}


def server_names(names):
    if (not isinstance(names, (list, tuple)) or len(names) > 100
            or any(not isinstance(name, str) or not name.strip() or len(name) > 200
                   or any(ord(c) < 32 or ord(c) == 127 for c in name) for name in names)
            or len(set(names)) != len(names)):
        raise ValueError('Invalid Codex connected tool selection')
    return tuple(names)


@dataclass(frozen=True)
class CodexTool:
    name: str
    status: str
    tools: tuple = ()
    off_for_chat: bool = True
    missing: bool = False
    is_session_tools: bool = False
    error: str = ''
    #: A chat started elsewhere: only that it's configured is known.
    read_only: bool = False
    app_id: str = ''
    display_name: str = ''
    install_url: str = ''

    @property
    def can_be_denied(self):
        return not self.is_session_tools

    @property
    def label(self):
        return self.display_name or self.name

    def text(self):
        if self.is_session_tools:
            return 'The Chat Place session tools: available while this app is running'
        if self.app_id:
            state = 'off for this chat' if self.off_for_chat else self.status
            return f'{self.label}: {state}, Codex app'
        if self.read_only:
            return f'{self.name}: {self.status}, configured Codex MCP server'
        if self.missing:
            return (f"{self.name}: no longer in Codex's configuration, so this chat can't send. "
                    'Make a copy without it, or add it back to config.toml.')
        # Said as Claude's list says it. ``status`` stays as it is: the dialog's
        # sign-in line goes by it, for either provider.
        state = 'off for this chat' if self.off_for_chat else STATUS_WORDS.get(self.status, self.status)
        count = len(self.tools)
        # "0 tools" for one that's off or needs signing in sounds like it offers
        # nothing, when it's only that nobody knows yet.
        said = f', {count} tool' + ('' if count == 1 else 's') if count or self.status == 'connected' and not self.off_for_chat else ''
        return f'{self.name}: {state}, configured Codex MCP server{said}'


def inventory(connection, info, allowed=(), *, read_only=False, status_optional=False):
    """The configured servers. ``read_only``: a chat started elsewhere, which
    this connection hasn't loaded. Codex 0.163 fails mcpServerStatus/list for
    such a thread ("Codex request failed"), and off or on means nothing for it
    here, so the rows come from the config alone. ``status_optional``: a chat
    of the app's own that isn't loaded, so Codex may not answer for its thread,
    and its servers are listed with no status."""
    from .codex_rpc import CodexRpcError, CodexTurnRejected, codex_tool_overrides
    allowed = server_names(allowed)
    result = connection.request('config/read', {'cwd': info.cwd, 'includeLayers': False})
    config = result.get('config')
    if not isinstance(config, dict):
        raise CodexTurnRejected('mcp_tools')
    if read_only:
        names = config.get('mcp_servers') or {}
        if not isinstance(names, dict):
            raise CodexTurnRejected('mcp_tools')
        return [CodexTool(name, 'configured', off_for_chat=False, read_only=True)
                for name in server_names(list(names))]
    # Only the configuration's shape is checked here. A selected server that
    # has been removed refuses every turn, and this list is where it gets said
    # and where a copy without it is made, so it mustn't refuse too.
    codex_tool_overrides(config, ())
    names = dict(config.get('mcp_servers') or {})
    if 'ccd_session_mgmt' in allowed:
        names['ccd_session_mgmt'] = {}
    allowed = tuple(n for n in allowed if n != 'codex_apps')
    try:
        result = connection.request('mcpServerStatus/list', {
            'threadId': info.cli_session_id, 'limit': 100, 'detail': 'toolsAndAuthOnly'})
    except CodexRpcError:
        if not status_optional:
            raise
        result = {'data': []}
    if not isinstance(result.get('data'), list) or result.get('nextCursor') not in (None, ''):
        raise CodexTurnRejected('mcp_tools')
    statuses = {row['name']: row for row in result['data']
                if isinstance(row, dict) and isinstance(row.get('name'), str)}
    # First, so the list opens on what's stopping the chat.
    rows = [CodexTool(name, 'removed', (), False, True) for name in allowed if name not in names]
    for name in server_names(list(names)):
        row = statuses.get(name, {})
        tools = row.get('tools')
        tools = tuple(str(k)[:200] for k in tools) if isinstance(tools, dict) else ()
        status = {'connected': 'connected', 'authenticationRequired': 'needs-auth',
                  'starting': 'still connecting', 'failed': 'failed'}.get(row.get('runtimeStatus'),
                      'not connected; refresh or reopen the chat')
        rows.append(CodexTool(name, status, tools, name not in allowed,
                             is_session_tools=name == "ccd_session_mgmt" and name in allowed))
    return rows


def app_ids(values):
    values = server_names(values)
    if any(v == '_default' or not all(c.isascii() and (c.isalnum() or c in '_-') for c in v) for v in values):
        raise ValueError('Invalid Codex app selection')
    return values


def app_overrides(config, selected):
    selected = app_ids(selected)
    configured = config.get('apps') or {}
    if not isinstance(configured, dict):
        raise ValueError('Invalid Codex app configuration')
    # Configured per-app enables would otherwise override the false default.
    result = {key: {'enabled': False} for key in configured if key != '_default'}
    result['_default'] = {'enabled': False}
    for identity in selected:
        result[identity] = {'enabled': True}
    return result


def verify_apps(connection, thread_id, selected):
    from .codex_rpc import CodexRpcError
    selected = app_ids(selected)
    if not selected:
        return  # features.apps is false and the MCP inventory is verified.
    rows = connection.request('app/installed', {'threadId': thread_id, 'forceRefresh': True}).get('apps')
    if (not isinstance(rows, list) or len(rows) > 1000 or
            any(not isinstance(r, dict) or not isinstance(r.get('id'), str) or
                type(r.get('enabled')) is not bool or type(r.get('callable')) is not bool for r in rows)):
        raise CodexRpcError('Codex app permissions could not be verified')
    if (len({r['id'] for r in rows}) != len(rows) or
            any((r['enabled'] or r['callable']) and r['id'] not in selected for r in rows) or
            any(not any(r['id'] == identity and r['enabled'] for r in rows) for identity in selected)):
        raise CodexRpcError('Codex app permissions do not match this chat')


def app_inventory(connection, info, selected=(), *, read_only=False):
    from .codex_rpc import CodexRpcError
    rows, seen, cursor = [], set(), None
    for _ in range(100):
        params = {'limit': 100, 'forceRefetch': True}
        if cursor:
            params['cursor'] = cursor
        page = connection.request('app/list', params)
        data = page.get('data')
        if not isinstance(data, list) or len(data) > 100:
            raise CodexRpcError('Codex app list is unavailable')
        for entry in data:
            if not isinstance(entry, dict):
                continue
            identity = entry.get('id')
            try:
                app_ids([identity])
            except ValueError:
                continue
            label = entry.get('name')
            if not isinstance(label, str) or not label:
                label = identity
            rows.append(CodexTool('app:' + identity,
                'available' if entry.get('isAccessible') is True else 'needs sign-in or installation',
                off_for_chat=not read_only and identity not in selected, read_only=read_only,
                app_id=identity, display_name=label[:200], install_url=entry.get('installUrl') or ''))
        cursor = page.get('nextCursor')
        if cursor is None:
            break
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise CodexRpcError('Invalid Codex app list cursor')
        seen.add(cursor)
    else:
        raise CodexRpcError('Codex app list is too large')
    known = {r.app_id for r in rows}
    rows.extend(CodexTool('app:' + identity, 'unavailable', off_for_chat=False, missing=True,
        app_id=identity, display_name=identity) for identity in selected if identity not in known)
    return rows
