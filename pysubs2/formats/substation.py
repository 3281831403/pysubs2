# mypy: disable-error-code="override"

import logging
import math
import re
import warnings
from typing import TYPE_CHECKING, Any, NotRequired, TextIO, TypedDict, Unpack, override

from ..common import SSA_ALIGNMENT, Alignment, Color
from ..ssaevent import SSAEvent
from ..ssastyle import SSAStyle
from ..time import TIMESTAMP, TIMESTAMP_SHORT, make_time, ms_to_times, timestamp_to_ms
from ..warnings import SubtitleAttributeWarning, TimestampOverflow, TimestampUnderflow
from .base import FormatBase

if TYPE_CHECKING:
    from ..ssafile import SSAFile


logger = logging.getLogger(__name__)


def ass_to_ssa_alignment(i: int) -> int:
    warnings.warn("ass_to_ssa_alignment function is deprecated, please use the Alignment enum", DeprecationWarning)
    return SSA_ALIGNMENT[i-1]

def ssa_to_ass_alignment(i: int) -> int:
    warnings.warn("ssa_to_ass_alignment function is deprecated, please use the Alignment enum", DeprecationWarning)
    return SSA_ALIGNMENT.index(i) + 1

SECTION_HEADING = re.compile(
    r"^.{,3}"  # allow 3 chars at start of line for BOM
    r"\["  # open square bracket
    r"[^]]*[a-z][^]]*"  # inside square brackets, at least one lowercase letter (this guards vs. uuencoded font data)
    r"]"  # close square bracket
)

ATTACHMENT_FILE_HEADING = re.compile(r"(fontname|filename):\s+(?P<name>\S+)")

STYLE_FORMAT_LINE = {
    "ass": "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic,"
           " Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment,"
           " MarginL, MarginR, MarginV, Encoding",
    "ssa": "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, TertiaryColour, BackColour, Bold, Italic,"
           " BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, AlphaLevel, Encoding"
}

STYLE_FIELDS = {
    "ass": ["fontname", "fontsize", "primarycolor", "secondarycolor", "outlinecolor", "backcolor", "bold", "italic",
            "underline", "strikeout", "scalex", "scaley", "spacing", "angle", "borderstyle", "outline", "shadow",
            "alignment", "marginl", "marginr", "marginv", "encoding"],
    "ssa": ["fontname", "fontsize", "primarycolor", "secondarycolor", "tertiarycolor", "backcolor", "bold", "italic",
            "borderstyle", "outline", "shadow", "alignment", "marginl", "marginr", "marginv", "alphalevel", "encoding"]
}

EVENT_FORMAT_LINE = {
    "ass": "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    "ssa": "Format: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
}

EVENT_FIELDS = {
    "ass": ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"],
    "ssa": ["marked", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"]
}

#: Largest timestamp allowed in SubStation, ie. 9:59:59.99.
MAX_REPRESENTABLE_TIME = make_time(h=10) - 10

def color_to_ass_rgba(c: Color) -> str:
    return f"&H{((c.a << 24) | (c.b << 16) | (c.g << 8) | c.r):08X}"

def color_to_ssa_rgb(c: Color) -> str:
    return f"{((c.b << 16) | (c.g << 8) | c.r)}"

def rgba_to_color(s: str) -> Color:
    if s.startswith("&"):
        # Example: "&HAABBCCDD" (this is not a typical "0xAABBCCDD" value; lint to replace with base=0 is misplaced)
        x = int(s[2:], base=16)  # noqa: FURB166
    else:
        x = int(s)
    r = x & 0xff
    g = (x >> 8) & 0xff
    b = (x >> 16) & 0xff
    a = (x >> 24) & 0xff
    return Color(r, g, b, a)

def is_valid_field_content(s: str) -> bool:
    """
    Returns True if string s can be stored in a SubStation field.

    Fields are written in CSV-like manner, thus commas and/or newlines
    are not acceptable in the string.

    """
    return "\n" not in s and "," not in s


def parse_tags(text: str, style: SSAStyle = SSAStyle.DEFAULT_STYLE,
               styles: dict[str, SSAStyle] | None = None,
               skip_empty_fragments: bool = False) -> list[tuple[str, SSAStyle]]:
    """
    Split text into fragments with computed SSAStyles.
    
    Returns list of tuples (fragment, style), where fragment is a part of text
    between two brace-delimited override sequences, and style is the computed
    styling of the fragment, ie. the original style modified by all override
    sequences before the fragment.
    
    Newline and non-breakable space overrides are left as-is.
    
    Supported override tags:
    
    - i, b, u, s
    - fn
    - r (with or without style name)
    
    """
    if styles is None:
        styles = {}
    
    fragments = SSAEvent.OVERRIDE_SEQUENCE.split(text)
    if len(fragments) == 1:
        if skip_empty_fragments and not text:
            return []
        else:
            return [(text, style)]
    
    def apply_overrides(all_overrides: str) -> SSAStyle:
        s = style.copy()
        for tag in re.findall(r"\\[ibusp][0-9]|\\r[a-zA-Z_0-9 ]*|\\fn[a-zA-Z_0-9 ]+", all_overrides):
            if tag == r"\r":
                s = style.copy() # reset to original line style
            elif tag.startswith(r"\r"):
                name = tag[2:]
                if name in styles:
                    # reset to named style
                    s = styles[name].copy()
            elif tag.startswith(r"\fn"):
                fontname = tag[3:]
                s.fontname = fontname
            else:
                if "i" in tag:
                    s.italic = "1" in tag
                elif "b" in tag:
                    s.bold = "1" in tag
                elif "u" in tag:
                    s.underline = "1" in tag
                elif "s" in tag:
                    s.strikeout = "1" in tag
                elif "p" in tag:
                    try:
                        scale = int(tag[2:])
                    except (ValueError, IndexError):
                        continue

                    s.drawing = scale > 0
        return s
    
    overrides = SSAEvent.OVERRIDE_SEQUENCE.findall(text)
    overrides_prefix_sum = ["".join(overrides[:i]) for i in range(len(overrides) + 1)]
    computed_styles = map(apply_overrides, overrides_prefix_sum)
    output = list(zip(fragments, computed_styles))
    if skip_empty_fragments:
        output = [(fragment, sty) for fragment, sty in output if fragment]
    return output


#: Karaoke duration tags. The argument is a duration in centiseconds.
KARAOKE_TAG = re.compile(r"\\(kf|ko|k|K)(\d+)")

#: Karaoke offset tag. The argument is a time in centiseconds.
KARAOKE_OFFSET_TAG = re.compile(r"\\kt(\d+)")

#: Fade in/out tag. Both arguments are durations in milliseconds.
FAD_TAG = re.compile(r"\\fad\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)")

#: Complex fade tag. The first three arguments are alpha values, the last
#: four are durations in milliseconds.
FADE_TAG = re.compile(
    r"\\fade\(\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*"
    r"(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\)"
)

#: Move tag. Coordinates must stay untouched; the optional trailing
#: t1/t2 arguments are times in milliseconds.
MOVE_TAG = re.compile(
    r"\\move\(\s*([^,()]+?)\s*,\s*([^,()]+?)\s*,\s*([^,()]+?)\s*,\s*([^,()]+?)\s*"
    r"(?:,\s*(-?\d+)\s*,\s*(-?\d+)\s*)?\)"
)

#: Leading t1/t2 times of a transform tag.
T_TAG_TIMES = re.compile(r"^\s*(-?\d+)\s*,\s*(-?\d+)")


def _round_half_away(x: float) -> int:
    """Round to the nearest integer, rounding halves away from zero."""
    if x >= 0:
        return int(math.floor(x + 0.5))
    return int(math.ceil(x - 0.5))


def _scale_duration(value: int, ratio: float) -> int:
    """Scale a duration, never crushing a positive value to zero."""
    scaled = _round_half_away(value * ratio)
    if value > 0 and scaled < 1:
        return 1
    return scaled


def _timestamp_cs(ms: int) -> int:
    """Convert a timestamp to the centiseconds in which ASS stores it."""
    if ms < 0:
        ms = 0
    elif ms > MAX_REPRESENTABLE_TIME:
        ms = MAX_REPRESENTABLE_TIME
    return (ms + 5) // 10


def _split_override_block(content: str) -> list[str]:
    """
    Split the inside of an override block into tags and literal fragments.

    A tag is a backslash followed by its name and an optional parenthesized
    argument list, which may contain nested parentheses and nested tags
    (eg. ``\\t(0,500,\\fscx120)`` or ``\\clip(m 0 0 l 100 0)``). Everything
    that does not start a tag is preserved as a literal fragment.
    """
    parts: list[str] = []
    i, n = 0, len(content)
    while i < n:
        if content[i] != "\\":
            j = content.find("\\", i)
            if j < 0:
                j = n
            parts.append(content[i:j])
            i = j
            continue

        start = i
        i += 1
        while i < n and content[i] not in "\\(":
            i += 1
        if i < n and content[i] == "(":
            depth = 1
            i += 1
            while i < n:
                if content[i] == "(":
                    depth += 1
                elif content[i] == ")":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
        parts.append(content[start:i])
    return parts


def _distribute(values: list[int], target: int) -> list[int]:
    """
    Distribute ``target`` proportionally to ``values``, using integers.

    The total of the result is exactly ``target``. Individual values differ
    from the ideal (non-integer) proportional value by less than one unit,
    with the exception of positive values which are kept nonzero when there
    is room for that without changing the total.
    """
    total = sum(values)
    if total <= 0 or target <= 0:
        return [0] * len(values)

    scaled = [(value * target) // total for value in values]
    remainder = target - sum(scaled)
    if remainder:
        order = sorted(
            range(len(values)),
            key=lambda i: (-((values[i] * target) % total), i),
        )
        for i in order[:remainder]:
            scaled[i] += 1

    # Do not crush a positive duration to zero if the total allows us to keep
    # it, taking a unit from the largest value.
    for i, value in enumerate(values):
        if value > 0 and scaled[i] == 0:
            donor = max(range(len(scaled)), key=lambda j: scaled[j])
            if scaled[donor] > 1:
                scaled[donor] -= 1
                scaled[i] = 1

    return scaled


def _scale_karaoke(values: list[int], ratio: float,
                   old_start: int, old_end: int,
                   new_start: int, new_end: int) -> list[int]:
    """
    Rescale karaoke centisecond durations.

    When the karaoke timings originally span the whole line, the rescaled
    values sum exactly to the rescaled line duration, so that the karaoke
    still covers the line after writing to ASS (where timestamps are rounded
    to centiseconds). Otherwise their total is scaled by the framerate ratio.
    """
    total = sum(values)
    if total <= 0:
        return list(values)

    old_duration = _timestamp_cs(old_end) - _timestamp_cs(old_start)
    new_duration = _timestamp_cs(new_end) - _timestamp_cs(new_start)

    if total == old_duration:
        target = max(new_duration, 0)
    else:
        # Not tied to the line duration; scale proportionally, but keep a
        # nonzero total nonzero.
        target = max(_round_half_away(total * ratio), 1)
    return _distribute(values, target)


def _transform_t_tag(tag: str, ratio: float) -> str:
    """Rescale the leading t1/t2 times of a ``\\t`` tag, leaving nested tags alone."""
    inner = tag[3:-1]  # strip leading "\t(" and trailing ")"
    m = T_TAG_TIMES.match(inner)
    if m is None:
        # No time arguments (eg. \t(\fscx120)); nothing to rescale.
        return tag

    t1, t2 = int(m.group(1)), int(m.group(2))
    # Splicing by span keeps whitespace and the rest of the tag (accel,
    # nested style modifiers) byte-identical.
    return ("\\t(" + inner[:m.start(1)] + str(_scale_duration(t1, ratio))
            + inner[m.end(1):m.start(2)] + str(_scale_duration(t2, ratio))
            + inner[m.end(2):] + ")")


def _transform_tag(tag: str, ratio: float, karaoke: list[int],
                   karaoke_index: list[int]) -> str:
    """Rescale time arguments of a single override tag."""
    m = KARAOKE_TAG.fullmatch(tag)
    if m:
        i = karaoke_index[0]
        karaoke_index[0] += 1
        return "\\" + m.group(1) + str(karaoke[i])

    m = KARAOKE_OFFSET_TAG.fullmatch(tag)
    if m:
        return "\\kt" + str(_scale_duration(int(m.group(1)), ratio))

    m = FAD_TAG.fullmatch(tag)
    if m:
        t1 = _scale_duration(int(m.group(1)), ratio)
        t2 = _scale_duration(int(m.group(2)), ratio)
        return f"\\fad({t1},{t2})"

    m = FADE_TAG.fullmatch(tag)
    if m:
        times = [_scale_duration(int(m.group(i)), ratio) for i in (4, 5, 6, 7)]
        return "\\fade({},{},{},{},{},{},{})".format(
            m.group(1), m.group(2), m.group(3), *times)

    m = MOVE_TAG.fullmatch(tag)
    if m:
        if m.group(5) is None:
            # No time arguments, only coordinates.
            return tag
        # Only the optional times are touched; coordinates stay byte-identical.
        t1_start, t1_end = m.span(5)
        t2_start, t2_end = m.span(6)
        new_t1 = str(_scale_duration(int(m.group(5)), ratio))
        new_t2 = str(_scale_duration(int(m.group(6)), ratio))
        return tag[:t1_start] + new_t1 + tag[t1_end:t2_start] + new_t2 + tag[t2_end:]

    if tag.startswith("\\t(") and tag.endswith(")"):
        return _transform_t_tag(tag, ratio)

    return tag


def transform_override_times(text: str, ratio: float,
                             old_start: int, old_end: int,
                             new_start: int, new_end: int) -> str:
    """
    Rescale times embedded in SubStation override tags by given ratio.

    This handles karaoke timing (``\\k``, ``\\K``, ``\\kf``, ``\\ko``, ``\\kt``)
    and the time arguments of ``\\move``, ``\\t``, ``\\fad`` and ``\\fade``.
    All other tag arguments (coordinates, colors, fonts, ``\\clip``, drawing
    commands, ...) are left untouched.
    """
    if ratio == 1.0 or "{" not in text:
        return text

    blocks = list(SSAEvent.OVERRIDE_SEQUENCE.finditer(text))
    if not blocks:
        return text

    karaoke: list[int] = []
    for block in blocks:
        for part in _split_override_block(block.group(0)[1:-1]):
            m = KARAOKE_TAG.fullmatch(part)
            if m:
                karaoke.append(int(m.group(2)))

    scaled_karaoke = _scale_karaoke(karaoke, ratio, old_start, old_end, new_start, new_end)
    karaoke_index = [0]

    parts: list[str] = []
    pos = 0
    for block in blocks:
        if block.start() > pos:
            parts.append(text[pos:block.start()])
        new_tags = [
            _transform_tag(part, ratio, scaled_karaoke, karaoke_index)
            for part in _split_override_block(block.group(0)[1:-1])
        ]
        parts.append("{" + "".join(new_tags) + "}")
        pos = block.end()
    if pos < len(text):
        parts.append(text[pos:])
    return "".join(parts)


NOTICE = "Script generated by pysubs2\nhttps://pypi.python.org/pypi/pysubs2"

class SubstationFormat(FormatBase):
    """SubStation Alpha (ASS, SSA) subtitle format implementation"""

    class ReaderArgs(TypedDict):
        pass

    class WriterArgs(TypedDict):
        header_notice: NotRequired[str]

    @staticmethod
    def ms_to_timestamp(requested_ms: int) -> str:
        """Convert ms to 'H:MM:SS.cc'"""
        if requested_ms < 0:
            warnings.warn("Underflow in SubStation timestamp, clamping to zero", TimestampUnderflow)
            requested_ms = 0
        elif requested_ms > MAX_REPRESENTABLE_TIME:
            warnings.warn("Overflow in SubStation timestamp, clamping to MAX_REPRESENTABLE_TIME", TimestampOverflow)
            requested_ms = MAX_REPRESENTABLE_TIME

        # Aegisub does rounding, see https://github.com/Aegisub/Aegisub/blob/6f546951b4f004da16ce19ba638bf3eedefb9f31/libaegisub/include/libaegisub/ass/time.h#L32
        round_ms = ((requested_ms + 5) - (requested_ms + 5) % 10)
        h, m, s, ms = ms_to_times(round_ms)
        cs = ms // 10

        return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"

    @classmethod
    @override
    def guess_format(cls, text: str) -> str | None:
        """See :meth:`pysubs2.formats.FormatBase.guess_format()`"""
        if re.search(r"V4\+ Styles", text, re.IGNORECASE):
            return "ass"
        elif re.search(r"V4 Styles", text, re.IGNORECASE):
            return "ssa"
        else:
            return None

    @classmethod
    @override
    def from_file(cls, subs: "SSAFile", fp: TextIO, format_: str, **kwargs: Unpack[ReaderArgs]) -> None:
        """See :meth:`pysubs2.formats.FormatBase.from_file()`"""
        subs.info.clear()
        subs.aegisub_project.clear()
        subs.styles.clear()
        subs.fonts_opaque.clear()
        subs.graphics_opaque.clear()

        inside_info_section = False
        inside_aegisub_section = False
        inside_font_section = False
        inside_graphic_section = False
        current_attachment_name = None
        current_attachment_lines_buffer: list[str] = []
        current_attachment_is_font = None

        for lineno, line in enumerate(fp, 1):
            line = line.strip()

            if SECTION_HEADING.match(line):
                logger.debug("at line %d: section heading %s", lineno, line)
                inside_info_section = "Info" in line
                inside_aegisub_section = "Aegisub" in line
                inside_font_section = "Fonts" in line
                inside_graphic_section = "Graphics" in line
            elif inside_info_section or inside_aegisub_section:
                if line.startswith(";"):
                    continue  # skip comments
                try:
                    k, v = line.split(":", 1)
                    if inside_info_section:
                        subs.info[k] = v.strip()
                    elif inside_aegisub_section:
                        subs.aegisub_project[k] = v.strip()
                except ValueError:
                    pass
            elif inside_font_section or inside_graphic_section:
                m = ATTACHMENT_FILE_HEADING.match(line)
                current_attachment_is_font = inside_font_section

                if current_attachment_name and (m or not line):
                    # flush last font/picture on newline or new font/picture name
                    attachment_data = current_attachment_lines_buffer[:]
                    if inside_font_section:
                        subs.fonts_opaque[current_attachment_name] = attachment_data
                    elif inside_graphic_section:
                        subs.graphics_opaque[current_attachment_name] = attachment_data
                    else:
                        raise NotImplementedError("Bad attachment section, expected [Fonts] or [Graphics]")
                    logger.debug("at line %d: finished attachment definition %s", lineno, current_attachment_name)
                    current_attachment_lines_buffer.clear()
                    current_attachment_name = None

                if m:
                    # start new font/picture
                    attachment_name = m.group("name")
                    current_attachment_name = attachment_name
                elif line:
                    # add non-empty line to current buffer
                    current_attachment_lines_buffer.append(line)
            elif line.startswith("Style:"):
                _, rest = line.split(":", 1)
                buf = rest.strip().split(",")
                name, *raw_fields = buf
                field_dict = {f: cls._string_to_field(f, v, format_) for f, v in zip(STYLE_FIELDS[format_], raw_fields)}
                sty = SSAStyle(**field_dict)
                subs.styles[name] = sty
            elif line.startswith(("Dialogue:", "Comment:")):
                ev_type, rest = line.split(":", 1)
                raw_fields = rest.strip().split(",", len(EVENT_FIELDS[format_])-1)
                field_dict = {f: cls._string_to_field(f, v, format_) for f, v in zip(EVENT_FIELDS[format_], raw_fields)}
                field_dict["type"] = ev_type
                ev = SSAEvent(**field_dict)
                subs.events.append(ev)

        # cleanup fonts/pictures
        if current_attachment_name:
            # flush last font on EOF or new section w/o newline
            attachment_data = current_attachment_lines_buffer[:]

            if current_attachment_is_font:
                subs.fonts_opaque[current_attachment_name] = attachment_data
            else:
                subs.graphics_opaque[current_attachment_name] = attachment_data

            logger.debug("at EOF: finished attachment definition %s", current_attachment_name)
            current_attachment_lines_buffer.clear()
            current_attachment_name = None

    @staticmethod
    def _string_to_field(field: str, value: str, format_: str) -> Any:
        """Parse SSAEvent/SSAStyle field value"""
        # Per issue #45, we should handle the case where there is extra whitespace around the values.
        # Extra whitespace is removed in non-string fields where it would break the parser otherwise,
        # and in font name (where it doesn't really make sense). It is preserved in Dialogue string
        # fields like Text, Name and Effect (to avoid introducing unnecessary change to parser output).

        match field:
            case "start" | "end":
                value = value.strip()
                if value.startswith("-"):
                    # handle negative timestamps
                    value = value[1:]
                    sign = -1
                else:
                    sign = 1

                m = TIMESTAMP.match(value)
                if m is None:
                    m = TIMESTAMP_SHORT.match(value)
                    if m is None:
                        raise ValueError(f"Failed to parse {field} timestamp: {value!r}")

                return sign * timestamp_to_ms(m.groups())
            case "primarycolor" | "secondarycolor" | "tertiarycolor" | "outlinecolor" | "backcolor":
                value = value.strip()
                try:
                    return rgba_to_color(value)
                except ValueError:
                    warnings.warn(f"Failed to parse {field}, using default", SubtitleAttributeWarning)
                    return getattr(SSAStyle.DEFAULT_STYLE, field)
            case "bold" | "underline" | "italic" | "strikeout":
                return value != "0"
            case "layer":
                # layer is not an SSAStyle attribute
                try:
                    return int(value)
                except ValueError:
                    warnings.warn(f"Failed to parse {field}, using default", SubtitleAttributeWarning)
                    return 0
            case "borderstyle" | "encoding" | "marginl" | "marginr" | "marginv" | "alphalevel":
                try:
                    return int(value)
                except ValueError:
                    warnings.warn(f"Failed to parse {field}, using default", SubtitleAttributeWarning)
                    # TODO - this is not entirely correct, the default for margin should be 0 if we're parsing SSAEvent
                    return getattr(SSAStyle.DEFAULT_STYLE, field)
            case "fontsize" | "scalex" | "scaley" | "spacing" | "angle" | "outline" | "shadow":
                try:
                    return float(value)
                except ValueError:
                    warnings.warn(f"Failed to parse {field}, using default", SubtitleAttributeWarning)
                    return getattr(SSAStyle.DEFAULT_STYLE, field)
            case "marked":
                return value.endswith("1")
            case "alignment":
                try:
                    if format_ == "ass":
                        return Alignment(int(value))
                    else:
                        return Alignment.from_ssa_alignment(int(value))
                except ValueError:
                    warnings.warn("Failed to parse alignment, using default", SubtitleAttributeWarning)
                    return getattr(SSAStyle.DEFAULT_STYLE, field)
            case "fontname":
                return value.strip()
            case _:
                return value

    @classmethod
    @override
    def to_file(cls, subs: "SSAFile", fp: TextIO, format_: str, **kwargs: Unpack[WriterArgs]) -> None:
        """See :meth:`pysubs2.formats.FormatBase.to_file()`"""
        header_notice: str = kwargs.get("header_notice", NOTICE)

        print("[Script Info]", file=fp)
        for line in header_notice.splitlines(False):
            print(";", line, file=fp)

        subs.info["ScriptType"] = "v4.00+" if format_ == "ass" else "v4.00"
        for k, v in subs.info.items():
            print(k, v, sep=": ", file=fp)

        if subs.aegisub_project:
            print("\n[Aegisub Project Garbage]", file=fp)
            for k, v in subs.aegisub_project.items():
                print(k, v, sep=": ", file=fp)

        print("\n[V4+ Styles]" if format_ == "ass" else "\n[V4 Styles]", file=fp)
        print(STYLE_FORMAT_LINE[format_], file=fp)
        for name, sty in subs.styles.items():
            fields = [cls._field_to_string(f, getattr(sty, f), sty, format_) for f in STYLE_FIELDS[format_]]
            print(f"Style: {name}", *fields, sep=",", file=fp)

        if subs.fonts_opaque:
            print("\n[Fonts]", file=fp)
            for font_name, font_lines in sorted(subs.fonts_opaque.items()):
                print(f"fontname: {font_name}", file=fp)
                for line in font_lines:
                    print(line, file=fp)
                print(file=fp)

        if subs.graphics_opaque:
            print("\n[Graphics]", file=fp)
            for picture_name, picture_lines in sorted(subs.graphics_opaque.items()):
                print(f"filename: {picture_name}", file=fp)
                for line in picture_lines:
                    print(line, file=fp)
                print(file=fp)

        print("\n[Events]", file=fp)
        print(EVENT_FORMAT_LINE[format_], file=fp)
        for ev in subs.events:
            fields = [cls._field_to_string(f, getattr(ev, f), ev, format_) for f in EVENT_FIELDS[format_]]
            print(ev.type, end=": ", file=fp)
            print(*fields, sep=",", file=fp)

    @classmethod
    def _field_to_string(cls, field: str, value: Any, line: SSAEvent | SSAStyle, format_: str) -> str:
        """Format SSAEvent/SSAStyle field value"""
        match field:
            case "start" | "end":
                return cls.ms_to_timestamp(value)
            case "marked":
                return f"Marked={value:d}"
            case "alignment":
                if isinstance(value, Alignment):
                    alignment = value
                else:
                    warnings.warn("The 'alignment' attribute of SSAStyle should be an Alignment instance, using plain int is deprecated", DeprecationWarning)
                    alignment = Alignment(value)

                if format_ == "ssa":
                    return str(alignment.to_ssa_alignment())
                else:
                    return str(alignment.value)

        match value:
            case bool():
                return "-1" if value else "0"
            case int():
                return str(value)
            case float():
                return str(int(value) if value.is_integer() else value)
            case str():
                return value
            case Color():
                if format_ == "ass":
                    return color_to_ass_rgba(value)
                else:
                    return color_to_ssa_rgb(value)

        raise TypeError(f"Unexpected type when writing a SubStation field {field!r} for line {line!r}")
