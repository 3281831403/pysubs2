"""
Internal storage for WebVTT-specific metadata.

WebVTT files can contain constructs that have no representation in the
SubStation data model (NOTE/STYLE/REGION blocks, cue identifiers and
cue settings). To preserve them on VTT -> VTT round-trips without
changing the public :class:`~pysubs2.ssaevent.SSAEvent` and
:class:`~pysubs2.ssafile.SSAFile` interfaces (and thus the JSON
serialization), the metadata is kept in registries keyed by the object
identity of the objects it belongs to.

:class:`SSAEvent` defines ``__eq__`` without ``__hash__`` and is thus
unhashable, so the registries use ``id()`` keys together with
:mod:`weakref.finalize` to drop entries when the owning object is
garbage-collected. The registries are an implementation detail of the
WebVTT format, other formats (SRT, ASS/SSA, JSON, ...) never see this
data.

"""

from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .ssaevent import SSAEvent
    from .ssafile import SSAFile


#: A WebVTT block (NOTE, STYLE or REGION), stored verbatim as raw lines
#: without the blank line that terminates the block.
WebVTTBlock = list[str]


@dataclass
class WebVTTEventMeta:
    """WebVTT metadata attached to a single :class:`SSAEvent`."""

    #: Cue identifier as written in the file (line preceding the timing line),
    #: or ``None`` when the cue has no explicit identifier.
    cue_id: str | None = None
    #: Cue settings as written after the timestamps (eg. ``align:middle line:85%``),
    #: or ``None`` when the cue has no settings.
    settings: str | None = None
    #: NOTE/STYLE/REGION blocks appearing between the previous cue (or the
    #: file header) and this cue, in file order, each as a list of raw lines.
    blocks_before: list[WebVTTBlock] = field(default_factory=list)
    #: True when the cue was read with HTML tags kept verbatim
    #: (``keep_html_tags`` / ``keep_unknown_html_tags``). On write,
    #: angle-bracket markup in the cue text must be preserved instead of
    #: being escaped as literal angle brackets.
    keep_markup: bool = False


@dataclass
class WebVTTFileMeta:
    """WebVTT metadata attached to an :class:`SSAFile`."""

    #: Lines of the WebVTT file header block (the ``WEBVTT`` signature line
    #: and any following header text lines, up to the first blank line).
    header: WebVTTBlock = field(default_factory=lambda: ["WEBVTT"])
    #: NOTE/STYLE/REGION blocks appearing after the last cue (or anywhere
    #: before the first cue when the file contains no cues), in file order.
    trailing_blocks: list[WebVTTBlock] = field(default_factory=list)


# id(obj) -> (metadata, finalizer). Entries are removed automatically
# when the owning object is garbage-collected.
_event_meta: dict[int, tuple[WebVTTEventMeta, "weakref.finalize"]] = {}
_file_meta: dict[int, tuple[WebVTTFileMeta, "weakref.finalize"]] = {}


def _drop(registry: dict[int, tuple[Any, "weakref.finalize"]], obj_id: int) -> None:
    registry.pop(obj_id, None)


def _set(
    registry: dict[int, tuple[Any, "weakref.finalize"]], obj: Any, meta: Any
) -> None:
    obj_id = id(obj)
    old = registry.get(obj_id)
    if old is not None:
        old[1].detach()
    finalizer = weakref.finalize(obj, _drop, registry, obj_id)
    registry[obj_id] = (meta, finalizer)


def _peek(registry: dict[int, tuple[Any, "weakref.finalize"]], obj: Any) -> Any | None:
    entry = registry.get(id(obj))
    return entry[0] if entry is not None else None


def set_event_meta(event: "SSAEvent", meta: WebVTTEventMeta) -> None:
    """Attach WebVTT metadata to an event."""
    _set(_event_meta, event, meta)


def peek_event_meta(event: "SSAEvent") -> WebVTTEventMeta | None:
    """Return the WebVTT metadata of an event, or None if it has none."""
    return _peek(_event_meta, event)


def get_file_meta(subs: "SSAFile") -> WebVTTFileMeta:
    """Return the WebVTT metadata of a file, creating an empty one on first access."""
    meta = _peek(_file_meta, subs)
    if meta is None:
        meta = WebVTTFileMeta()
        _set(_file_meta, subs, meta)
    return meta


def set_file_meta(subs: "SSAFile", meta: WebVTTFileMeta) -> None:
    """Attach WebVTT metadata to a file."""
    _set(_file_meta, subs, meta)


def peek_file_meta(subs: "SSAFile") -> WebVTTFileMeta | None:
    """Return the WebVTT metadata of a file, or None if it has none."""
    return _peek(_file_meta, subs)
