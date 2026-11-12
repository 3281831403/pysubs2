# mypy: disable-error-code="override"

import re
import warnings
from collections.abc import Sequence
from typing import TYPE_CHECKING, TextIO, Unpack, override

from ..ssaevent import SSAEvent
from ..ssastyle import SSAStyle
from ..time import make_time
from ..warnings import PossibleMissedSubtitleWarning
from . import _vtt_meta
from ._charrefs import (
    decode_character_references,
    escape_text,
    escape_text_preserving_tags,
    has_markup_tag,
)
from .subrip import SubripFormat
from .substation import parse_tags

if TYPE_CHECKING:
    from ..ssafile import SSAFile


class WebVTTFormat(SubripFormat):
    """
    Web Video Text Tracks (WebVTT) subtitle format implementation.

    The reader parses the file according to the WebVTT block structure:
    the file header, NOTE/STYLE/REGION blocks, cue identifiers, cue
    timing lines with cue settings and cue payloads are recognized
    separately, so that NOTE blocks and cue settings never end up in
    subtitle text. The WebVTT-specific metadata (header, NOTE/STYLE/
    REGION blocks, per-cue identifier and settings) is preserved and
    written back verbatim on VTT -> VTT round-trips; other output
    formats (SRT, ASS/SSA, JSON, ...) only see the cue texts.

    Character references (``&amp;``, ``&#60;`` ...) are decoded on read
    and escaped on write, as required by the format.
    """
    TIMESTAMP = re.compile(r"(?:(\d{1,4}):)?(\d{2}):(\d{2})\.(\d{2,3})")
    TIMING_LINE = re.compile(
        r"^(?P<start>(?:\d{1,4}:)?\d{2}:\d{2}\.\d{2,3})"
        r"\s*-->\s*"
        r"(?P<end>(?:\d{1,4}:)?\d{2}:\d{2}\.\d{2,3})"
        r"(?P<settings>\s+.*)?$"
    )

    class ReaderArgs(SubripFormat.ReaderArgs):
        pass

    class WriterArgs(SubripFormat.WriterArgs):
        pass

    @staticmethod
    @override
    def ms_to_timestamp(ms: int) -> str:
        result = SubripFormat.ms_to_timestamp(ms)
        return result.replace(',', '.')

    @staticmethod
    @override
    def timestamp_to_ms(groups: Sequence[str]) -> int:
        _h, _m, _s, _ms = groups
        if not _h:
            h = 0
        else:
            h = int(_h.strip(":"))
        m, s, ms = map(int, (_m, _s, _ms))
        return make_time(h=h, m=m, s=s, ms=ms)

    @classmethod
    @override
    def guess_format(cls, text: str) -> str | None:
        """See :meth:`pysubs2.formats.FormatBase.guess_format()`"""
        if text.lstrip().startswith("WEBVTT"):
            return "vtt"
        else:
            return None

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    @classmethod
    @override
    def from_file(cls, subs: "SSAFile", fp: TextIO, format_: str,
                  **kwargs: Unpack[ReaderArgs]) -> None:
        """
        See :meth:`pysubs2.formats.SubripFormat.from_file()`.

        The file is parsed as WebVTT: NOTE/STYLE/REGION blocks, cue
        identifiers and cue settings are recognized and kept apart from
        cue payloads. The same HTML tag handling options as for SubRip
        are supported (``keep_html_tags``, ``keep_unknown_html_tags``).
        """
        keep_html_tags: bool = kwargs.get("keep_html_tags", False)
        keep_unknown_html_tags: bool = kwargs.get("keep_unknown_html_tags", False)

        lines = fp.read().splitlines()
        blocks = cls._split_blocks(lines)
        if not blocks:
            return

        # The first block is the header: the WEBVTT signature line plus
        # optional header text (eg. X-TIMESTAMP-MAP). It is stored verbatim.
        if blocks[0] and blocks[0][0].lstrip().startswith("WEBVTT"):
            header_block = blocks.pop(0)
        else:
            # malformed file without a proper header, still try to parse cues
            header_block = ["WEBVTT"]
        file_meta = _vtt_meta.WebVTTFileMeta(header=list(header_block))
        _vtt_meta.set_file_meta(subs, file_meta)

        pending_blocks: list[_vtt_meta.WebVTTBlock] = []

        for block in blocks:
            cue = cls._parse_cue_block(block, keep_html_tags, keep_unknown_html_tags)
            if cue is None:
                # NOTE/STYLE/REGION block (or unparseable content)
                pending_blocks.append(list(block))
                continue

            event, cue_id, settings = cue
            meta = _vtt_meta.WebVTTEventMeta(
                cue_id=cue_id,
                settings=settings,
                blocks_before=list(pending_blocks),
                keep_markup=keep_html_tags or keep_unknown_html_tags,
            )
            _vtt_meta.set_event_meta(event, meta)
            subs.events.append(event)
            pending_blocks.clear()

        _vtt_meta.get_file_meta(subs).trailing_blocks = pending_blocks

    @staticmethod
    def _split_blocks(lines: list[str]) -> list[list[str]]:
        """Split WebVTT file content into blocks separated by blank lines."""
        blocks: list[list[str]] = []
        current: list[str] = []
        for line in lines:
            if line.strip() == "":
                if current:
                    blocks.append(current)
                    current = []
            else:
                current.append(line)
        if current:
            blocks.append(current)
        return blocks

    @classmethod
    def _parse_cue_block(
        cls,
        block: list[str],
        keep_html_tags: bool,
        keep_unknown_html_tags: bool,
    ) -> tuple[SSAEvent, str | None, str | None] | None:
        """
        Parse one block as a WebVTT cue, returning (event, cue_id, settings).

        Returns None for blocks that are not cues (NOTE/STYLE/REGION).
        """
        # NOTE/STYLE/REGION blocks - never cues
        first_word = block[0].split(None, 1)[0] if block and block[0].strip() else ""
        if first_word in ("NOTE", "STYLE", "REGION"):
            return None

        timing_index: int | None = None
        timing_match: re.Match[str] | None = None
        for i, line in enumerate(block):
            match = cls.TIMING_LINE.match(line.strip())
            if match:
                timing_index = i
                timing_match = match
                break

        if timing_match is None or timing_index is None:
            # malformed block, treat as non-cue metadata rather than
            # merging it into some other cue's payload
            return None

        if timing_index > 1:
            warnings.warn(
                "Possible malformed WebVTT cue: more than one line before timing line, "
                "ignoring extra identifier lines",
                PossibleMissedSubtitleWarning,
            )

        cue_id: str | None
        if timing_index >= 1:
            cue_id = block[timing_index - 1].strip()
        else:
            cue_id = None

        settings_raw = timing_match.group("settings")
        if settings_raw and settings_raw.strip():
            settings: str | None = settings_raw.strip()
        else:
            settings = None

        start_match = cls.TIMESTAMP.match(timing_match.group("start"))
        end_match = cls.TIMESTAMP.match(timing_match.group("end"))
        assert start_match is not None and end_match is not None
        start = cls.timestamp_to_ms(start_match.groups())
        end = cls.timestamp_to_ms(end_match.groups())

        payload_lines = block[timing_index + 1:]
        text = cls._prepare_payload(
            payload_lines, keep_html_tags, keep_unknown_html_tags
        )
        return SSAEvent(start=start, end=end, text=text), cue_id, settings

    @staticmethod
    def _prepare_payload(
        lines: list[str],
        keep_html_tags: bool,
        keep_unknown_html_tags: bool,
    ) -> str:
        s = "\n".join(lines).strip()
        if not keep_html_tags:
            s = re.sub(r"< *i *>", r"{\\i1}", s)
            s = re.sub(r"< */ *i *>", r"{\\i0}", s)
            s = re.sub(r"< *s *>", r"{\\s1}", s)
            s = re.sub(r"< */ *s *>", r"{\\s0}", s)
            s = re.sub(r"< *u *>", r"{\\u1}", s)
            s = re.sub(r"< */ *u *>", r"{\\u0}", s)
            s = re.sub(r"< *b *>", r"{\\b1}", s)
            s = re.sub(r"< */ *b *>", r"{\\b0}", s)
        if not (keep_html_tags or keep_unknown_html_tags):
            s = re.sub(r"< */? *[a-zA-Z][^>]*>", "", s) # strip other HTML tags
        s = decode_character_references(s)
        s = re.sub(r"\n", r"\\N", s) # convert newlines
        return s

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    @classmethod
    @override
    def to_file(cls, subs: "SSAFile", fp: TextIO, format_: str,
                **kwargs: Unpack[WriterArgs]) -> None:
        """
        See :meth:`pysubs2.formats.SubripFormat.to_file()`.

        When the file was read from WebVTT, the original header,
        NOTE/STYLE/REGION blocks, cue identifiers and cue settings are
        written back. Cues without an explicit identifier are numbered
        sequentially, matching the historical output of this library.
        Special characters in cue text are escaped as character
        references.
        """
        apply_styles: bool = kwargs.get("apply_styles", True)
        keep_ssa_tags: bool = kwargs.get("keep_ssa_tags", False)

        visible_lines = cls._get_visible_lines(subs)
        file_meta = _vtt_meta.peek_file_meta(subs)

        # header
        if file_meta is not None:
            for line in file_meta.header:
                print(line, file=fp)
        else:
            print("WEBVTT", file=fp)
        print(file=fp)

        # blocks preceding the first cue (only possible for a file with
        # no cues in the read path; they are kept in trailing_blocks)
        def print_block(block: _vtt_meta.WebVTTBlock) -> None:
            for line in block:
                print(line, file=fp)
            print(file=fp)

        # Collect NOTE/STYLE/REGION blocks belonging to events that won't be
        # written (SSA comment lines). They are emitted with the next visible
        # cue, in original order, so the metadata is not silently dropped.
        visible_ids = {id(e) for e in visible_lines}
        deferred_blocks: list[_vtt_meta.WebVTTBlock] = []
        for event in subs.events:
            if id(event) in visible_ids:
                continue
            hidden_meta = _vtt_meta.peek_event_meta(event)
            if hidden_meta is not None:
                deferred_blocks.extend(hidden_meta.blocks_before)

        if not visible_lines and file_meta is not None:
            for block in deferred_blocks:
                print_block(block)
            for block in file_meta.trailing_blocks:
                print_block(block)
            return

        for lineno, line in enumerate(visible_lines, 1):
            meta = _vtt_meta.peek_event_meta(line)

            if meta is not None:
                for block in list(deferred_blocks) + list(meta.blocks_before):
                    print_block(block)
                deferred_blocks.clear()
                cue_id = meta.cue_id
                settings = meta.settings
            else:
                cue_id = None
                settings = None

            # Cues without an explicit identifier get sequential numbers,
            # preserving the historical pysubs2 VTT output.
            if cue_id is None:
                cue_id = str(lineno)

            start = cls.ms_to_timestamp(line.start)
            end = cls.ms_to_timestamp(line.end)
            timing = f"{start} --> {end}"
            if settings:
                timing = f"{timing} {settings}"
            text = cls._prepare_output_text(
                line.text,
                subs.styles.get(line.style, SSAStyle.DEFAULT_STYLE),
                apply_styles=apply_styles,
                keep_ssa_tags=keep_ssa_tags,
                keep_markup=meta.keep_markup if meta is not None else False,
            )

            print(cue_id, file=fp)
            print(timing, file=fp)
            print(text, end="\n\n", file=fp)

        if file_meta is not None:
            for block in file_meta.trailing_blocks:
                print_block(block)

    @staticmethod
    def _prepare_output_text(
        text: str,
        style: SSAStyle,
        *,
        apply_styles: bool,
        keep_ssa_tags: bool,
        keep_markup: bool,
    ) -> str:
        text = text.replace(r"\h", " ")
        text = text.replace(r"\n", "\n")
        text = text.replace(r"\N", "\n")

        def escaper(part: str) -> str:
            # Events read in a tag-keeping mode may carry real angle-bracket
            # markup; for everything else, angle brackets in the text are
            # literal characters and must be escaped.
            if keep_markup or has_markup_tag(part):
                return escape_text_preserving_tags(part)
            return escape_text(part, escape_lt_gt=True)

        body: list[str] = []
        if keep_ssa_tags:
            body.append(escaper(text))
        else:
            for fragment, sty in parse_tags(text, style):
                fragment = escaper(fragment)
                if apply_styles:
                    if sty.italic:
                        fragment = f"<i>{fragment}</i>"
                    if sty.underline:
                        fragment = f"<u>{fragment}</u>"
                    if sty.strikeout:
                        fragment = f"<s>{fragment}</s>"
                body.append(fragment)

        return re.sub("\n+", "\n", "".join(body).strip())

    @classmethod
    @override
    def _get_visible_lines(cls, subs: "SSAFile") -> list[SSAEvent]:
        visible_lines = super()._get_visible_lines(subs)
        visible_lines.sort(key=lambda e: e.start)
        return visible_lines
