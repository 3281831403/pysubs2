"""
pysubs2.formats.vtt tests

"""

import json
from pathlib import Path
from textwrap import dedent

from pysubs2 import SSAEvent, SSAFile, make_time


DATA_DIR = Path(__file__).parent.parent / "data"
FULL_METADATA_VTT = DATA_DIR / "vtt_full_metadata.vtt"


def test_simple_write() -> None:
    subs = SSAFile()

    e1 = SSAEvent()
    e1.start = 0
    e1.end = 60000
    e1.text = "An example subtitle."

    e2 = SSAEvent()
    e2.start = 60000
    e2.end = 120000
    e2.text = "Subtitle number\\Ntwo."

    e3 = SSAEvent()
    e3.start = 60000
    e3.end = 120000
    e3.text = "Invisible subtitle."
    e3.is_comment = True

    subs.append(e1)
    subs.append(e2)
    subs.append(e3)

    ref = dedent("""\
    WEBVTT
    
    1
    00:00:00.000 --> 00:01:00.000
    An example subtitle.

    2
    00:01:00.000 --> 00:02:00.000
    Subtitle number
    two.
    """)

    text = subs.to_string("vtt")
    assert text.strip() == ref.strip()


def test_writes_in_time_order() -> None:
    subs = SSAFile()

    e1 = SSAEvent()
    e1.start = 0
    e1.end = 60000
    e1.text = "An example subtitle."

    e2 = SSAEvent()
    e2.start = 60000
    e2.end = 120000
    e2.text = "Subtitle number\\Ntwo."

    subs.append(e2)
    subs.append(e1)

    ref = dedent("""\
    WEBVTT
    
    1
    00:00:00.000 --> 00:01:00.000
    An example subtitle.

    2
    00:01:00.000 --> 00:02:00.000
    Subtitle number
    two.
    """)

    text = subs.to_string("vtt")
    assert text.strip() == ref.strip()


def test_simple_read() -> None:
    text = dedent("""\
    WEBVTT
    
    1
    00:00:00.000 --> 00:01:00.000
    An example subtitle.

    2
    00:01:00.000 --> 00:02:00.000
    Subtitle number
    two.
    """)

    ref = SSAFile()
    ref.append(SSAEvent(start=0, end=make_time(m=1), text="An example subtitle."))
    ref.append(SSAEvent(start=make_time(m=1), end=make_time(m=2), text="Subtitle number\\Ntwo."))

    subs = SSAFile.from_string(text)
    assert subs.equals(ref)


def test_read_complex() -> None:
    # regression test for #30
    text = dedent("""\
    WEBVTT
    X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0
    
    00:50.099 --> 00:53.299 line:85% align:middle
    Cuidem do seu grupo.
    Cuidem de suas fileiras.
    
    01:54.255 --> 01:55.455 line:85% align:middle
    Parem!
    
    01:58.155 --> 01:59.555 line:85% align:middle
    E, parem!
    """)

    ref = SSAFile()
    ref.append(SSAEvent(start=make_time(s=50, ms=99), end=make_time(s=53, ms=299),
                        text=r"Cuidem do seu grupo.\NCuidem de suas fileiras."))
    ref.append(SSAEvent(start=make_time(m=1, s=54, ms=255), end=make_time(m=1, s=55, ms=455),
                        text="Parem!"))
    ref.append(SSAEvent(start=make_time(m=1, s=58, ms=155), end=make_time(m=1, s=59, ms=555),
                        text="E, parem!"))

    subs = SSAFile.from_string(text)
    assert subs.equals(ref)


def test_note_blocks_not_merged_into_cue_text() -> None:
    # NOTE blocks must be parsed as blocks, never concatenated with cue text
    text = dedent("""\
    WEBVTT

    NOTE This is a comment
    spanning two lines

    1
    00:00:01.000 --> 00:00:02.000 align:start position:10%
    First cue

    NOTE middle note

    cue-2
    00:00:03.000 --> 00:00:04.000 line:80%
    Second cue
    """)

    subs = SSAFile.from_string(text)
    assert len(subs) == 2
    assert subs[0].text == "First cue"
    assert subs[1].text == "Second cue"
    assert all("NOTE" not in e.text and "note" not in e.text for e in subs)


def test_cue_identifier_and_settings_kept_apart_from_text() -> None:
    text = dedent("""\
    WEBVTT

    my-cue-id
    00:00:01.000 --> 00:00:02.000 align:middle line:85% position:50%
    Cue text
    """)

    subs = SSAFile.from_string(text)
    assert len(subs) == 1
    assert subs[0].text == "Cue text"
    assert "my-cue-id" not in subs[0].text
    assert "align" not in subs[0].text

    out = subs.to_string("vtt")
    assert "my-cue-id\n00:00:01.000 --> 00:00:02.000 align:middle line:85% position:50%\n" in out


def test_character_references_decoded_on_read() -> None:
    text = dedent("""\
    WEBVTT

    00:00:01.000 --> 00:00:02.000
    &amp; &lt; &gt; &#60; &#x3E; &quot;
    """)

    subs = SSAFile.from_string(text)
    assert subs[0].text == '& < > < > "'


def test_character_references_escaped_on_vtt_write() -> None:
    subs = SSAFile()
    subs.append(SSAEvent(start=0, end=1000, text="Tom & Jerry <3 >"))
    out = subs.to_string("vtt")
    assert "Tom &amp; Jerry &lt;3 &gt;" in out
    # round-trips back to the original text
    assert SSAFile.from_string(out)[0].text == "Tom & Jerry <3 >"


def test_srt_export_has_no_metadata_and_restores_refs() -> None:
    subs = SSAFile.load(str(FULL_METADATA_VTT))
    srt = subs.to_string("srt")

    assert "NOTE" not in srt
    assert "STYLE" not in srt
    assert "REGION" not in srt
    assert "align:" not in srt
    assert "position:" not in srt
    # the named VTT cue identifiers are replaced by SRT sequence numbers
    assert "intro" not in srt and "trailer" not in srt
    # character references are restored to actual characters
    assert "Hello & welcome world" in srt
    assert "&amp;" not in srt and "&lt;" not in srt
    assert "<i>italics</i>" in srt


def test_ass_export_has_no_metadata_and_restores_refs() -> None:
    subs = SSAFile.load(str(FULL_METADATA_VTT))
    ass = subs.to_string("ass")

    assert "NOTE" not in ass
    assert "REGION" not in ass
    # the VTT STYLE block must not leak into the ASS Dialogue lines
    assert "::cue" not in ass
    assert "align:" not in ass
    assert "Hello & welcome world" in ass
    assert "&amp;" not in ass


def test_vtt_roundtrip_preserves_all_metadata() -> None:
    original = FULL_METADATA_VTT.read_text(encoding="utf-8")
    subs = SSAFile.load(str(FULL_METADATA_VTT))
    written = subs.to_string("vtt")

    assert written == original
    # writing again stays stable
    assert SSAFile.from_string(written).to_string("vtt") == original


def test_vtt_roundtrip_cues_have_decoded_text() -> None:
    subs = SSAFile.load(str(FULL_METADATA_VTT))
    texts = [e.text for e in subs]
    assert "Hello & welcome world" in texts
    assert any("Second & cue with" in t and r"{\i1}italics{\i0}" in t for t in texts)
    assert "Third & cue in a region" in texts


def test_srt_to_vtt_escapes_special_characters() -> None:
    srt = dedent("""\
    1
    00:00:01,000 --> 00:00:02,000
    Tom & Jerry <3 >

    2
    00:00:03,000 --> 00:00:04,000
    plain
    """)

    subs = SSAFile.from_string(srt, format_="srt")
    assert subs[0].text == "Tom & Jerry <3 >"

    vtt = subs.to_string("vtt")
    assert "Tom &amp; Jerry &lt;3 &gt;" in vtt
    # SRT output keeps the restored characters verbatim (SRT has no escaping)
    srt_again = subs.to_string("srt")
    assert "Tom & Jerry <3 >" in srt_again
    assert "&amp;" not in srt_again


def test_json_export_unchanged_without_vtt_metadata_keys() -> None:
    # The VTT metadata must not leak into the JSON representation
    subs = SSAFile.load(str(FULL_METADATA_VTT))
    data = json.loads(subs.to_string("json"))

    expected_event_keys = {
        "start", "end", "text", "marked", "layer", "style",
        "name", "marginl", "marginr", "marginv", "effect", "type",
    }
    assert set(data.keys()) == {"info", "styles", "events"}
    assert all(set(event.keys()) == expected_event_keys for event in data["events"])

    # JSON round-trip keeps the cue data intact
    again = SSAFile.from_string(subs.to_string("json"))
    assert again.equals(subs)


def test_json_output_identical_for_non_vtt_input() -> None:
    # A plain SRT file (no VTT metadata) must serialize exactly as before
    srt = dedent("""\
    1
    00:00:01,000 --> 00:00:02,000
    Hello &amp; goodbye

    2
    00:00:03,000 --> 00:00:04,000
    <i>styled</i>
    """)
    subs = SSAFile.from_string(srt, format_="srt")
    data = json.loads(subs.to_string("json"))
    assert data["events"][0]["text"] == "Hello & goodbye"
    assert data["events"][1]["text"] == r"{\i1}styled{\i0}"
    assert all(set(e.keys()) == {
        "start", "end", "text", "marked", "layer", "style",
        "name", "marginl", "marginr", "marginv", "effect", "type",
    } for e in data["events"])


def test_style_region_blocks_preserved_on_roundtrip() -> None:
    text = dedent("""\
    WEBVTT

    REGION
    id:fred
    width:40%

    STYLE
    ::cue {
      color: lime;
    }

    00:00:01.000 --> 00:00:02.000 region:fred
    cue in a region
    """)

    subs = SSAFile.from_string(text)
    assert len(subs) == 1
    assert subs[0].text == "cue in a region"
    out = subs.to_string("vtt")
    assert "REGION\nid:fred\nwidth:40%" in out
    assert "STYLE\n::cue {\n  color: lime;\n}" in out
    assert "region:fred" in out


def test_header_text_preserved() -> None:
    text = dedent("""\
    WEBVTT
    X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0

    00:00:01.000 --> 00:00:02.000
    cue
    """)
    subs = SSAFile.from_string(text)
    out = subs.to_string("vtt")
    assert out.startswith("WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0\n\n")


def test_empty_vtt_file() -> None:
    subs = SSAFile.from_string("WEBVTT\n")
    assert len(subs) == 0
    assert subs.to_string("vtt") == "WEBVTT\n\n"


def test_unknown_named_reference_passes_through_semantics() -> None:
    subs = SSAFile.from_string("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n&bogus;\n")
    assert subs[0].text == "&bogus;"
    # re-read after write yields the same text (escaping is transparent)
    assert SSAFile.from_string(subs.to_string("vtt"))[0].text == "&bogus;"


def test_keep_html_tags_preserves_markup_and_escapes_literal_angles() -> None:
    text = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<i>keep</i> a < b & c\n"

    subs = SSAFile.from_string(text, keep_html_tags=True)
    assert subs[0].text == "<i>keep</i> a < b & c"
    out = subs.to_string("vtt")
    # markup tags survive, literal angle brackets/ampersand are escaped
    assert "<i>keep</i> a &lt; b &amp; c" in out

    # default mode converts <i> to SSA tags and back, literals still escaped
    default_subs = SSAFile.from_string(text)
    assert default_subs[0].text == r"{\i1}keep{\i0} a < b & c"
    assert "<i>keep</i> a &lt; b &amp; c" in default_subs.to_string("vtt")


def test_keep_unknown_html_tags_preserves_font_markup() -> None:
    text = 'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<font color="red">x</font> & y\n'
    subs = SSAFile.from_string(text, keep_unknown_html_tags=True)
    out = subs.to_string("vtt")
    assert '<font color="red">x</font> &amp; y' in out


def test_blocks_before_comment_event_are_not_dropped() -> None:
    text = dedent("""\
    WEBVTT

    NOTE note attached to a hidden comment cue

    hidden
    00:00:01.000 --> 00:00:02.000
    invisible payload

    visible
    00:00:03.000 --> 00:00:04.000
    shown payload
    """)

    subs = SSAFile.from_string(text)
    assert len(subs) == 2
    # mark the first event as an SSA comment (excluded from text output)
    subs[0].is_comment = True

    out = subs.to_string("vtt")
    assert "NOTE note attached to a hidden comment cue" in out
    assert "invisible payload" not in out
    assert "shown payload" in out


def test_inline_timestamp_tag_preserved_and_ampersand_escaped() -> None:
    text = "WEBVTT\n\n00:00:01.000 --> 00:00:05.000\n<00:00:02.000>inline & timestamp\n"
    subs = SSAFile.from_string(text)
    assert subs[0].text == "<00:00:02.000>inline & timestamp"
    out = subs.to_string("vtt")
    assert "<00:00:02.000>inline &amp; timestamp" in out
    assert "&lt;00:00:02.000&gt;" not in out
