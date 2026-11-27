"""Explicit files, images and skills in Codex messages (#467).

The text remains a string so queues and failed-send recovery keep working.
Its native input items travel with it while queued; the text also names every
file, so recovery to an ordinary draft never silently loses a reference.
"""
from pathlib import Path

from ..attachments import media_type, sniffed_type


class CodexInput(str):
    def __new__(cls, text, parts=(), *, original=None, draft=None, attachments=()):
        value = super().__new__(cls, text)
        value.original_text = original
        value.draft_text = draft
        value.attachment_paths = tuple(attachments)
        value._parts = tuple(tuple(sorted(part.items())) for part in parts)
        validate_input(value.items())
        return value

    def items(self):
        return [{'type': 'text', 'text': str(self)}, *(dict(part) for part in self._parts)]


def validate_input(items):
    if (not isinstance(items, list) or not 1 <= len(items) <= 21
            or not isinstance(items[0], dict) or set(items[0]) != {'type', 'text'}
            or items[0]['type'] != 'text' or not isinstance(items[0]['text'], str)
            or not items[0]['text'].strip() or len(items[0]['text']) > 32000):
        raise ValueError('Enter a message of at most 32000 characters and at most 20 attachments.')
    for part in items[1:]:
        if not isinstance(part, dict):
            raise ValueError('Invalid Codex attachment.')
        kind = part.get('type')
        if kind == 'localImage' and set(part) == {'type', 'path'}:
            path = part['path']
            if (not isinstance(path, str) or not Path(path).is_absolute()
                    or not Path(path).is_file() or not sniffed_type(path)):
                raise ValueError('An attached image is missing or is not a supported image.')
        elif kind == 'skill' and set(part) == {'type', 'path', 'name'}:
            if (not isinstance(part['name'], str) or not part['name'].strip()
                    or len(part['name']) > 256 or not isinstance(part['path'], str)
                    or not Path(part['path']).is_absolute() or not Path(part['path']).is_file()):
                raise ValueError('The selected Codex skill is no longer available.')
        else:
            raise ValueError('Unsupported Codex input item.')


class LongCodexInput(str):
    """An assembled message over the send limit (#625), carrying its native
    parts (images, skills) unvalidated for length: the window hands the
    text over in a file and sends the parts with the short prompt
    (CodexInput(prompt, parts)), so an attached image still reaches Codex."""

    def __new__(cls, text, parts=(), *, draft=None, attachments=()):
        value = super().__new__(cls, text)
        value.parts = tuple(dict(part) for part in parts)
        value.draft_text = draft
        value.attachment_paths = tuple(attachments)
        return value


def build_input(text, paths=(), *, skills=(), allow_long=False):
    """Assemble a message's text, attachment lines and native parts.

    ``allow_long``: an assembled message over the send limit (attachment
    lines included) comes back as a LongCodexInput instead of being
    refused, for the caller to hand over in a file."""
    if not isinstance(text, str) or len(paths) + len(skills) > 20:
        raise ValueError('Choose at most 20 attachments.')
    lines, parts = [text.strip()] if text.strip() else [], []
    attachments = []
    for supplied in paths:
        path = Path(supplied).resolve(strict=True)
        if not path.is_file():
            raise ValueError('An attachment is not a file.')
        attachments.append(str(path))
        if media_type(str(path)) and sniffed_type(str(path)):
            parts.append({'type': 'localImage', 'path': str(path)})
            lines.append('Attached image: ' + str(path))
        else:
            lines.append('Attached file (read this file for the request): ' + str(path))
    for name, path in skills:
        lines.append('$' + name)
        parts.append({'type': 'skill', 'name': name, 'path': str(Path(path).resolve(strict=True))})
    joined = '\n\n'.join(lines)
    from .codex_rpc import MAX_MESSAGE_CHARS
    if allow_long and len(joined) > MAX_MESSAGE_CHARS:
        return LongCodexInput(joined, parts, draft=text.strip(), attachments=attachments)
    return CodexInput(joined, parts, draft=text.strip(), attachments=attachments)


def input_items(text):
    result = text.items() if isinstance(text, CodexInput) else [{'type': 'text', 'text': text}]
    validate_input(result)
    return result


def prepend_context(text, context):
    """Keep native attachments when a deferred conversation precedes Send."""
    if not context:
        return text
    parts = text.items()[1:] if isinstance(text, CodexInput) else getattr(text, 'parts', ())
    joined = context + str(text)
    draft = getattr(text, 'draft_text', None)
    if draft is not None:
        draft = context + draft
    options = dict(draft=draft, attachments=getattr(text, 'attachment_paths', ()))
    return LongCodexInput(joined, parts, **options) if len(joined) > 32000 else CodexInput(joined, parts, **options)
