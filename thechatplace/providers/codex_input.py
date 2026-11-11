"""Explicit files, images and skills in Codex messages (#467).

The text remains a string so queues and failed-send recovery keep working.
Its native input items travel with it while queued; the text also names every
file, so recovery to an ordinary draft never silently loses a reference.
"""
from pathlib import Path

from ..attachments import media_type, sniffed_type


class CodexInput(str):
    def __new__(cls, text, parts=()):
        value = super().__new__(cls, text)
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


def build_input(text, paths=(), *, skills=()):
    if not isinstance(text, str) or len(paths) + len(skills) > 20:
        raise ValueError('Choose at most 20 attachments.')
    lines, parts = [text.strip()] if text.strip() else [], []
    for supplied in paths:
        path = Path(supplied).resolve(strict=True)
        if not path.is_file():
            raise ValueError('An attachment is not a file.')
        if media_type(str(path)) and sniffed_type(str(path)):
            parts.append({'type': 'localImage', 'path': str(path)})
            lines.append('Attached image: ' + str(path))
        else:
            lines.append('Attached file (read this file for the request): ' + str(path))
    for name, path in skills:
        lines.append('$' + name)
        parts.append({'type': 'skill', 'name': name, 'path': str(Path(path).resolve(strict=True))})
    return CodexInput('\n\n'.join(lines), parts)


def input_items(text):
    result = text.items() if isinstance(text, CodexInput) else [{'type': 'text', 'text': text}]
    validate_input(result)
    return result
