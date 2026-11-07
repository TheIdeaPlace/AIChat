"""Files and images sent with a message (#22).

Checked with Claude Code 2.1.286 in a headless turn:

* An image goes in the message itself, as a base64 image block next to the
  text: Claude read the words in a test picture exactly, and the transcript
  keeps it as an image block.
* Any other file is named in the text as ``@"C:\\path\\file.txt"``. Claude Code
  reads it into the message before Claude sees it: no tool call, no
  permission prompt, even outside the session's folder, and quoting works for
  paths with spaces.

Images bigger than the API takes (5 MB) go as ``@"path"`` too; Claude Code
then reads them with its own tools. A queued message (sent during a turn)
carries everything as ``@"path"``, because what's queued is text.
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import List, Optional, Tuple

from . import platform_paths

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}
#: The API's limit for one image, which applies to the base64 data: a file
#: up to about 3.75 MB once encoded.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
#: The first bytes of each image type the API takes.
_SIGNATURES = [(b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"),
               (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif")]


def media_type(path: str) -> Optional[str]:
    """The image type by extension (for choosing which files to look at)."""
    return IMAGE_TYPES.get(os.path.splitext(path)[1].lower())


def sniffed_type(path: str) -> Optional[str]:
    """The image type from the file's first bytes, whatever its extension
    says; None if it isn't an image the API takes."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(16)
    except OSError:
        return None
    for signature, kind in _SIGNATURES:
        if head.startswith(signature):
            return kind
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def fits_inline(size: int) -> bool:
    """Whether a file this size is within the limit once base64-encoded."""
    return 4 * ((size + 2) // 3) <= MAX_IMAGE_BYTES


def mention(path: str) -> str:
    """``@"C:\\a b\\x.txt"``: a file Claude Code reads into the message."""
    return f'@"{path}"'


def describe(paths: List[str]) -> str:
    """"2 attachments: screenshot.png, log.txt"."""
    if not paths:
        return "No attachments"
    names = ", ".join(os.path.basename(p) for p in paths)
    return f"{len(paths)} attachment{'s' if len(paths) != 1 else ''}: {names}"


def build(text: str, paths: List[str], images_inline: bool = True) -> Tuple[str, List[dict]]:
    """The message text (with ``@"path"`` lines for files) and the image
    blocks to send with it. A file that's gone is left out and said."""
    blocks: List[dict] = []
    mentions: List[str] = []
    missing: List[str] = []
    for path in paths:
        if not os.path.isfile(path):
            missing.append(os.path.basename(path))
            continue
        kind = sniffed_type(path) if media_type(path) else None
        if images_inline and kind and fits_inline(os.path.getsize(path)):
            with open(path, "rb") as handle:
                data = base64.b64encode(handle.read()).decode("ascii")
            blocks.append({"type": "image",
                           "source": {"type": "base64", "media_type": kind, "data": data}})
        else:
            mentions.append(mention(path))
    lines = [text.rstrip()] if text.strip() else []
    if mentions:
        lines += ["", "Attached: " + " ".join(mentions)]
    if missing:
        lines += ["", "(Couldn't attach, no longer there: " + ", ".join(missing) + ")"]
    return "\n".join(lines).strip(), blocks


def remove_old_pastes(days: int = 30, now: Optional[float] = None) -> int:
    """Delete pasted pictures older than ``days``; how many went."""
    cutoff = (now if now is not None else time.time()) - days * 86400
    removed = 0
    try:
        for path in paste_folder().glob("Pasted image *.png"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                pass
    except OSError:
        pass
    return removed


def paste_folder() -> Path:
    """Where pasted images are kept: The Chat Place's own data folder."""
    return platform_paths.app_data_dir() / "pasted images"


def pasted_image_path(when: Optional[float] = None) -> Path:
    stamp = time.strftime("%Y-%m-%d %H-%M-%S", time.localtime(time.time() if when is None else when))
    folder = paste_folder()
    path = folder / f"Pasted image {stamp}.png"
    number = 2
    while path.exists():
        path = folder / f"Pasted image {stamp} ({number}).png"
        number += 1
    return path
