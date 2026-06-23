import relay  # noqa: F401
import pathlib
import pytest
from relay.dhav import (
    DhavParser, find_start_code, nal_type_h264, nal_type_h265, is_valid_nal_type,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "ch3_main.dhav"


def test_is_valid_nal_type_h264():
    assert not is_valid_nal_type("h264", 0x00)   # type 0: garbage from DHAV header
    assert is_valid_nal_type("h264", 0x67)       # SPS (7)
    assert is_valid_nal_type("h264", 0x68)       # PPS (8)
    assert is_valid_nal_type("h264", 0x65)       # IDR (5)
    assert is_valid_nal_type("h264", 0x41)       # non-IDR (1)
    assert not is_valid_nal_type("h264", 0x18)   # type 24 (STAP-A): not in raw ES


def test_is_valid_nal_type_h265():
    assert is_valid_nal_type("h265", 0x40)       # VPS (32)
    assert is_valid_nal_type("h265", 0x42)       # SPS (33)
    assert not is_valid_nal_type("h265", 41 << 1)  # type 41: reserved


def test_find_start_code_4byte():
    buf = b"\x00\x00\x00\x01\x67rest"
    assert find_start_code(buf, 0) == (0, 4)


def test_find_start_code_3byte():
    buf = b"\xaa\x00\x00\x01\x67"
    assert find_start_code(buf, 0) == (1, 3)


def test_find_start_code_none():
    assert find_start_code(b"\x01\x02\x03", 0) is None


def test_parser_emits_nals_from_real_capture():
    assert FIXTURE.exists(), "run scripts/capture_raw.py first (Task 4)"
    data = FIXTURE.read_bytes()
    p = DhavParser()
    nals = list(p.feed(data))
    assert len(nals) > 0
    # Every emitted unit is Annex-B framed and tagged with a codec.
    for codec, nal in nals:
        assert codec in ("h264", "h265")
        assert nal[:4] == b"\x00\x00\x00\x01" or nal[:3] == b"\x00\x00\x01"
    # Codec is consistent within one stream.
    assert len({c for c, _ in nals}) == 1
    # No garbage NALs (DHAV-header false start codes) leak through.
    for codec, nal in nals:
        sc = 4 if nal[:4] == b"\x00\x00\x00\x01" else 3
        assert is_valid_nal_type(codec, nal[sc])


def test_parser_handles_split_reads():
    """Feeding the same bytes in small chunks yields the same NAL payloads."""
    data = FIXTURE.read_bytes()
    whole = [nal for _, nal in DhavParser().feed(data)]
    p = DhavParser()
    chunked = []
    for i in range(0, len(data), 1000):
        chunked.extend(nal for _, nal in p.feed(data[i:i + 1000]))
    assert chunked == whole


def test_resolution_detected():
    data = FIXTURE.read_bytes()
    p = DhavParser()
    list(p.feed(data))
    w, h = p.resolution
    # Main stream: must be a sane camera resolution, not a desynced-parse artifact.
    assert w >= 320 and h >= 240, f"implausible resolution {w}x{h}"
    assert w % 2 == 0 and h % 2 == 0
