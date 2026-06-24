import relay  # noqa: F401
from relay.playlist import build_m3u8


def test_build_m3u8_lists_each_stream():
    out = build_m3u8([
        ("cam3-main", "rtsp://localhost:8554/cam3-main"),
        ("cam3-sub", "rtsp://localhost:8554/cam3-sub"),
    ])
    assert out.startswith("#EXTM3U\n")
    assert "#EXTINF:-1,cam3-main\nrtsp://localhost:8554/cam3-main\n" in out
    assert "#EXTINF:-1,cam3-sub\nrtsp://localhost:8554/cam3-sub\n" in out


def test_build_m3u8_empty():
    assert build_m3u8([]) == "#EXTM3U\n"
