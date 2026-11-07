"""The sound scheme gallery for The Chat Place: app-kit's ``soundgallery`` with this app's address,
certificate checks and schemes. No wx, so it can be tested.

The gallery isn't published yet, so until it is, browsing says it couldn't be reached. Nothing
here needs to change when it opens.
"""
from __future__ import annotations

from typing import Iterable, Optional

# Imported here, not only inside app-kit's verify, so PyInstaller bundles what checking the
# gallery's signature needs.
from Cryptodome.Hash import SHA256  # noqa: F401
from Cryptodome.PublicKey import ECC  # noqa: F401
from Cryptodome.Signature import DSS  # noqa: F401
from theideaplace_appkit import soundgallery as shared

from . import sound_events

#: ``--sound-gallery <folder or address>`` for testing against a gallery of your own.
base_override: Optional[str] = None

NEW = "new"
ADDED = "added"
UPDATE = "update"


def gallery() -> shared.Gallery:
    """The gallery to browse. Raises ValueError for an address that isn't allowed."""
    from .updater import _ssl_context
    return shared.Gallery(sound_events.APP, base_override, context=_ssl_context())


def state(entry: shared.Entry, schemes: Iterable[sound_events.Scheme]) -> str:
    """Whether a listed scheme is new to you, added, or added with a newer version listed."""
    for scheme in schemes:
        if scheme.source and scheme.source.get("gallery") == entry.id:
            return ADDED if scheme.source.get("sha256") == entry.sha256 else UPDATE
    return NEW


def added_as(entry: shared.Entry, schemes: Iterable[sound_events.Scheme]) -> Optional[sound_events.Scheme]:
    return next((s for s in schemes if s.source and s.source.get("gallery") == entry.id), None)


def row_label(entry: shared.Entry, schemes: Iterable[sound_events.Scheme]) -> str:
    """A row of the gallery's list: "Rain at Night, by Someone. 8 of 15 events have a sound."
    with "Added." or "Update available." after it."""
    text = f"{entry.name}, by {entry.author}."
    if entry.events_with_sound is not None:
        text += f" {entry.events_with_sound} of {len(sound_events.EVENTS)} events have a sound."
    status = state(entry, schemes)
    if status == ADDED:
        text += " Added."
    elif status == UPDATE:
        text += " Update available."
    return text


def details(entry: shared.Entry) -> str:
    text = f"{entry.description} " if entry.description else ""
    return text + f"By {entry.author}. Licence: {entry.license}."


def check() -> bool:
    """For ``--smoke-test``: the gallery's key loads, and a signature check runs and refuses a
    signature that isn't the key's."""
    ECC.import_key(shared.PUBLIC_KEY_PEM)
    return shared.verify(b"\x30\x06\x02\x01\x01\x02\x01\x01", b"smoke") is False
