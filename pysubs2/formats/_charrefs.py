"""
Handling of HTML character references (``&amp;``, ``&#38;``, ...)
in SubRip and WebVTT subtitle text.

Both formats are line-oriented text formats where ``&``, ``<`` and ``>``
have special meaning, so text characters need to be represented as
character references on write and decoded back on read.

"""

from __future__ import annotations

import re

#: Matches a single HTML character reference, named or numeric.
_CHARREF = re.compile(r"&(?:#x[0-9a-fA-F]+|#[0-9]+|[a-zA-Z][a-zA-Z0-9]+);")

#: Matches an HTML-ish markup tag as recognized by the SubRip/WebVTT readers,
#: including WebVTT inline timestamp tags (``<00:00:10.000>``).
_MARKUP_TAG = re.compile(r"</?[a-zA-Z][^>]*>|<\d{1,2}:\d{2}:\d{2}\.\d{2,3}>")

#: Named character references defined for WebVTT/SubRip (the HTML spec
#: defines far more, these are the common, non-ambiguous ones).
_NAMED_REFS: dict[str, str] = {
    "amp": "&",
    "lt": "<",
    "gt": ">",
    "quot": '"',
    "apos": "'",
    "nbsp": " ",
}


def _decode_ref(match: re.Match[str]) -> str:
    ref = match.group(0)
    body = ref[1:-1]  # strip & and ;

    if body.startswith("#"):
        try:
            if body[1:2].lower() == "x":
                codepoint = int(body[2:], 16)
            else:
                codepoint = int(body[1:])
            # lone surrogates and non-characters are not useful in subtitle text,
            # pass the reference through unchanged
            if 0x00 <= codepoint <= 0x10FFFF and not 0xD800 <= codepoint <= 0xDFFF:
                return chr(codepoint)
        except (ValueError, OverflowError):
            pass
        return ref

    named = _NAMED_REFS.get(body.lower())
    if named is not None:
        return named

    # unknown named reference: leave as-is
    return ref


def decode_character_references(text: str) -> str:
    """
    Decode HTML character references (``&amp;``, ``&#60;``, ``&#x3E;`` ...)
    in given text. Unrecognized references are left unchanged.
    """
    return _CHARREF.sub(_decode_ref, text)


def escape_text(text: str, *, escape_lt_gt: bool) -> str:
    """
    Escape special characters in plain subtitle text for write to
    a markup-based text format.

    Arguments:
        text: Text with no markup tags.
        escape_lt_gt: If True, escape ``<`` and ``>`` as well as ``&``
            (WebVTT). If False, escape only ``&`` (SubRip, where angle
            brackets are used by its HTML-ish tags and are otherwise
            typically passed through).

    """
    text = text.replace("&", "&amp;")
    if escape_lt_gt:
        text = text.replace("<", "&lt;").replace(">", "&gt;")
    return text


def has_markup_tag(text: str) -> bool:
    """Return True if the text contains an HTML-ish markup tag."""
    return _MARKUP_TAG.search(text) is not None


def escape_text_preserving_tags(text: str) -> str:
    """
    Escape special characters for WebVTT while leaving recognized
    markup tags (``<i>``, ``</i>``, ``<font ...>`` ...) untouched.

    Angle brackets that do not form a markup tag (eg. a literal ``a < b``)
    are still escaped. Used when HTML tags were kept verbatim on read.
    """
    parts: list[str] = []
    pos = 0
    for match in _MARKUP_TAG.finditer(text):
        parts.append(escape_text(text[pos:match.start()], escape_lt_gt=True))
        parts.append(match.group(0))
        pos = match.end()
    parts.append(escape_text(text[pos:], escape_lt_gt=True))
    return "".join(parts)
