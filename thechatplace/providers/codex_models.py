"""Read advertised model choices without starting a chat or inference (#184)."""
from dataclasses import dataclass

from .codex_rpc import CodexRpcError


@dataclass(frozen=True)
class CodexModel:
    model: str
    label: str
    efforts: tuple[str, ...]
    default_effort: str
    is_default: bool


def _text(value, limit=256):
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= limit
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def read_models(connection):
    """Return a complete, bounded catalog; partial pages never become choices.

    Discovery advertises choices, not account entitlement. Lifecycle and turn
    billing checks still run independently before any prompt is submitted.
    """
    models, cursors, names, ids = [], set(), set(), set()
    cursor = None
    for _ in range(100):
        page = connection.request('model/list', {'cursor': cursor, 'limit': 100,
                                                'includeHidden': False})
        if not isinstance(page, dict) or not isinstance(page.get('data'), list):
            raise CodexRpcError('Codex model catalog could not be verified')
        for row in page['data']:
            if (not isinstance(row, dict) or not _text(row.get('id'))
                    or not _text(row.get('model')) or not _text(row.get('displayName'), 512)
                    or type(row.get('hidden')) is not bool
                    or type(row.get('isDefault')) is not bool
                    or not isinstance(row.get('supportedReasoningEfforts'), list)
                    or not _text(row.get('defaultReasoningEffort'))):
                raise CodexRpcError('Codex model catalog could not be verified')
            if row['id'] in ids or row['model'] in names:
                raise CodexRpcError('Codex model catalog contains duplicate choices')
            ids.add(row['id'])
            names.add(row['model'])
            if len(ids) > 1000:
                raise CodexRpcError('Codex model catalog exceeds the supported size')
            efforts = []
            for option in row['supportedReasoningEfforts']:
                if (not isinstance(option, dict) or not _text(option.get('reasoningEffort'))
                        or option['reasoningEffort'] in efforts or len(efforts) >= 64):
                    raise CodexRpcError('Codex model efforts could not be verified')
                efforts.append(option['reasoningEffort'])
            if row['defaultReasoningEffort'] not in efforts:
                raise CodexRpcError('Codex default model effort could not be verified')
            modalities = row.get('inputModalities', ['text', 'image'])
            if (not isinstance(modalities, list)
                    or not all(_text(value) for value in modalities)):
                raise CodexRpcError('Codex model input support could not be verified')
            if not row['hidden'] and 'text' in modalities:
                models.append(CodexModel(row['model'], row['displayName'], tuple(efforts),
                                         row['defaultReasoningEffort'], row['isDefault']))
        cursor = page.get('nextCursor')
        if cursor is None:
            return tuple(models)
        if not _text(cursor, 4096) or cursor in cursors:
            raise CodexRpcError('Codex model catalog pagination could not be verified')
        cursors.add(cursor)
    raise CodexRpcError('Codex model catalog pagination exceeds the supported size')
