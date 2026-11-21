"""A message with attachments for a Muse Code chat started here.

Shaped as installed Muse Code 1.4.20 takes them (checked natively): an image
goes as an ``image`` input part (``base64Data``, ``mediaType``), which the
model sees; any other file goes as ``@"path"`` in the text, which Muse reads
with its file tool, inside the chat's folder or outside it. Bad base64 is
refused by Muse (-32602) before any turn starts.

The text stays a string (as CodexInput's does), so the window's send path,
announcements and failed-send recovery keep working.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

from ..attachments import fits_inline, media_type, sniffed_type

#: As many attachments as one message takes, as for Codex.
MAX_ATTACHMENTS = 20


class MuseInput(str):
    """The message text, carrying its image parts."""

    def __new__(cls, text, images=()):
        value = super().__new__(cls, text)
        value._images = tuple(dict(image) for image in images)
        return value

    @property
    def images(self) -> tuple:
        return tuple(dict(image) for image in self._images)


def _client_ref(name: str):
    # Muse takes 1 to 256 bytes of UTF-8 for an image's own name.
    return {"name": name} if 0 < len(name.encode("utf-8")) <= 256 else None


def build_input(text: str, paths=()) -> MuseInput:
    """``text`` with ``paths`` attached. Raises OSError for a file that's
    gone, ValueError for too many or a path that isn't a file."""
    if not isinstance(text, str) or len(paths) > MAX_ATTACHMENTS:
        raise ValueError(f"Choose at most {MAX_ATTACHMENTS} attachments.")
    lines = [text.strip()] if text.strip() else []
    images, mentions = [], []
    for supplied in paths:
        path = Path(supplied).resolve(strict=True)
        if not path.is_file():
            raise ValueError("An attachment is not a file.")
        kind = sniffed_type(str(path)) if media_type(str(path)) else None
        if kind and fits_inline(os.path.getsize(path)):
            image = {"type": "image", "mediaType": kind,
                     "base64Data": base64.b64encode(path.read_bytes()).decode("ascii")}
            ref = _client_ref(path.name)
            if ref:
                image["clientRef"] = ref
            images.append(image)
            lines.append("Attached image: " + path.name)
        else:
            mentions.append(f'@"{path}"')
    if mentions:
        lines.append("Attached: " + " ".join(mentions))
    return MuseInput("\n\n".join(lines), images)
