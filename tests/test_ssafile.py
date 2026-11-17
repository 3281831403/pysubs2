import re

import pytest

from pysubs2 import SSAEvent, SSAFile, SSAStyle, make_time


def test_repr_default() -> None:
    subs = SSAFile()
    ref = "<SSAFile with 0 events and 1 styles>"
    assert repr(subs) == ref


def test_repr_simple() -> None:
    subs = SSAFile()
    subs.append(SSAEvent(start=make_time(m=5), end=make_time(m=6)))
    subs.append(SSAEvent(start=make_time(m=125), end=make_time(m=126)))
    subs.append(SSAEvent(start=make_time(m=15), end=make_time(m=16)))
    subs.styles["style1"] = SSAStyle()
    subs.styles["style2"] = SSAStyle()
    ref = "<SSAFile with 3 events and 3 styles, last timestamp 2:06:00>"
    assert repr(subs) == ref


def test_shift() -> None:
    #TODO: write more tests
    subs = SSAFile()

    with pytest.raises(ValueError):
        subs.shift(frames=5)

    with pytest.raises(ValueError):
        subs.shift(fps=23.976)

    with pytest.raises(ValueError):
        subs.shift(frames=5, fps=-1)


def test_import_styles() -> None:
    red1 = SSAStyle()
    red2 = SSAStyle()
    green = SSAStyle()
    subs1 = SSAFile()
    subs2 = SSAFile()

    def prepare() -> None:
        subs1.styles = {}
        subs2.styles = {}
        subs1.styles["green"] = green
        subs1.styles["red"] = red1
        subs2.styles["red"] = red2

    prepare()
    subs2.import_styles(subs1)
    assert subs2.styles["green"] is green
    assert subs2.styles["red"] is red1

    prepare()
    subs2.import_styles(subs1, overwrite=False)
    assert subs2.styles["green"] is green
    assert subs2.styles["red"] is red2

    with pytest.raises(TypeError):
        subs2.import_styles({})  # type: ignore[arg-type]


def test_rename_style() -> None:
    subs = SSAFile()
    red = SSAStyle()
    green = SSAStyle()

    def prepare() -> None:
        subs.events = [SSAEvent(style="red"), SSAEvent(style="unrelated")]
        subs.styles = {"red": red, "green": green}

    prepare()
    subs.rename_style("red", "blue")
    assert "red" not in subs.styles
    assert subs.styles["blue"] is red
    assert subs[0].style == "blue"
    assert subs[1].style == "unrelated"

    prepare()
    with pytest.raises(ValueError):
        # cannot delete style via move
        subs.rename_style("red", "green")

    prepare()
    with pytest.raises(ValueError):
        subs.rename_style("red", "illegal,name")
    with pytest.raises(ValueError):
        subs.rename_style("red", "illegal\nname")
    with pytest.raises(KeyError):
        subs.rename_style("nonexistent-style", "blue")


def test_transform_framerate() -> None:
    subs = SSAFile()
    subs.append(SSAEvent(start=0, end=10))
    subs.append(SSAEvent(start=1000, end=1010))

    with pytest.raises(ValueError):
        subs.transform_framerate(1, 0)
    with pytest.raises(ValueError):
        subs.transform_framerate(1, -1)
    with pytest.raises(ValueError):
        subs.transform_framerate(0, 1)
    with pytest.raises(ValueError):
        subs.transform_framerate(-1, 1)

    subs.transform_framerate(10, 20)
    assert subs[0] == SSAEvent(start=0, end=5)
    assert subs[1] == SSAEvent(start=500, end=505)


def _karaoke_cs(text: str) -> list[int]:
    """Extract karaoke durations (in centiseconds) from ASS override tags."""
    return [int(m.group(1)) for m in re.finditer(r"\\(?:kf|ko|k|K)(\d+)", text)]


def test_transform_framerate_scales_inline_times() -> None:
    subs = SSAFile()
    subs.append(SSAEvent(
        start=0, end=1000,
        text=r"{\K50\ko20\kf10\k30\kt40\fad(200,300)"
             r"\fade(255,0,255,100,200,300,400)"
             r"\move(0,0,10,10,0,500)\t(0,500,\fscx120)}"))
    subs.transform_framerate(10, 20)  # ratio 0.5

    text = subs[0].text
    assert r"\K25" in text
    assert r"\ko10" in text
    assert r"\kf5" in text
    assert r"\k15" in text
    assert r"\kt20" in text
    assert r"\fad(100,150)" in text
    assert r"\fade(255,0,255,50,100,150,200)" in text
    assert r"\move(0,0,10,10,0,250)" in text
    assert r"\t(0,250,\fscx120)" in text


def test_transform_framerate_karaoke_covers_line_after_roundtrip() -> None:
    subs = SSAFile()
    subs.append(SSAEvent(
        start=0, end=3000,
        text=r"{\kf100}{\kf33}SUN{\kf4}{\kf42}DAY{\kf4}{\kf38}MON{\kf4}{\kf29}DAY{\kf4}{\kf42}CHU"))
    original = _karaoke_cs(subs[0].text)
    assert sum(original) == 300

    subs.transform_framerate(25, 23.976)

    # Write to ASS and read back; timestamps are stored with centisecond
    # precision, and the karaoke must still exactly cover the line.
    subs2 = SSAFile.from_string(subs.to_string("ass"))
    line2 = subs2[0]
    assert sum(_karaoke_cs(line2.text)) == (line2.end - line2.start) // 10

    # Each tag stays within a couple of centiseconds of its ideal value.
    ratio = 25 / 23.976
    for before, after in zip(original, _karaoke_cs(line2.text)):
        assert abs(after - before * ratio) <= 2


def test_transform_framerate_preserves_non_time_tags() -> None:
    text = (r"{\an5\pos(655.758,142.500)\1c&H4E4AFB&\3c&H4E4AFB&"
            r"\clip(m 0 0 l 100 0 100 100 0 100)\p4}m 0 0 l 100 0 100 100 0 100{\p0}")
    subs = SSAFile()
    subs.append(SSAEvent(start=0, end=1000, text=text))
    subs.transform_framerate(10, 20)
    assert subs[0].text == text


def test_shift_preserves_inline_times() -> None:
    text = r"{\k50\fad(200,300)\move(0,0,10,10,0,500)\t(0,500,\fscx120)}"
    subs = SSAFile()
    subs.append(SSAEvent(start=0, end=1000, text=text))
    subs.shift(ms=100)
    assert subs[0].start == 100
    assert subs[0].end == 1100
    assert subs[0].text == text


def test_transform_framerate_keeps_positive_durations_nonzero() -> None:
    subs = SSAFile()
    subs.append(SSAEvent(
        start=0, end=1000,
        text=r"{\fad(1,300)\move(0,0,10,10,1,300)\t(1,300,\fscx120)\k1}"))
    subs.transform_framerate(10, 20)  # ratio 0.5
    text = subs[0].text
    assert r"\fad(1,150)" in text
    assert r"\move(0,0,10,10,1,150)" in text
    assert r"\t(1,150,\fscx120)" in text
    assert r"\k1" in text


def test_insertion_of_wrong_type() -> None:
    subs = SSAFile()
    subs.append(SSAEvent())

    with pytest.raises(TypeError):
        subs.append(42)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        subs.insert(42)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        subs[0] = 42  # type: ignore[call-overload]


def test_slice_api() -> None:
    subs = SSAFile()
    subs[:] = [
        SSAEvent(text="A"),
        SSAEvent(text="B"),
        SSAEvent(text="C")
    ]
    assert subs[0].text == "A"
    assert subs[-1].text == "C"

    bc = subs[1:]
    assert len(bc) == 2
    assert bc[0].text == "B"
    assert bc[1].text == "C"

    del subs[1:]
    assert len(subs) == 1
    assert subs[0].text == "A"

    subs[:] = (SSAEvent(text=c) for c in "XYZ")
    assert len(subs) == 3
    assert subs[0].text == "X"
    assert subs[1].text == "Y"
    assert subs[2].text == "Z"
