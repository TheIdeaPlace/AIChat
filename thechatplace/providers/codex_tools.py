"""Explicit per-thread access to configured Codex MCP servers (#467)."""
from dataclasses import dataclass


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

    @property
    def label(self):
        return self.name

    def text(self):
        if self.missing:
            return (f"{self.name}: no longer in Codex's configuration, so this chat can't send. "
                    'Make a copy without it, or add it back to config.toml.')
        state = 'off for this chat' if self.off_for_chat else self.status
        return f'{self.name}: {state}, configured Codex MCP server, {len(self.tools)} tools'


def inventory(connection, info, allowed=(), *, status_optional=False):
    """``status_optional``: the chat isn't loaded here, so Codex may not answer
    for its thread, and the servers are listed with no status."""
    from .codex_rpc import CodexRpcError, CodexTurnRejected, codex_tool_overrides
    allowed = server_names(allowed)
    result = connection.request('config/read', {'cwd': info.cwd, 'includeLayers': False})
    config = result.get('config')
    if not isinstance(config, dict):
        raise CodexTurnRejected('mcp_tools')
    # Only the configuration's shape is checked here. A selected server that
    # has been removed refuses every turn, and this list is where it gets said
    # and where a copy without it is made, so it mustn't refuse too.
    codex_tool_overrides(config, ())
    names = config.get('mcp_servers') or {}
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
        rows.append(CodexTool(name, status, tools, name not in allowed))
    return rows
