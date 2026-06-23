import relay  # noqa: F401
from relay.probe import format_streams_txt, ProbeResult


def test_format_streams_txt_renders_working_streams():
    results = [
        ProbeResult(channel=3, stream="main", ok=True, codec="h265", width=2880, height=1620),
        ProbeResult(channel=3, stream="sub", ok=True, codec="h264", width=704, height=576),
        ProbeResult(channel=4, stream="main", ok=False, codec=None, width=0, height=0),
    ]
    txt = format_streams_txt(results, source="user@cam.example.tv:8100")
    # Lines are full `relay` arg lists, prefixed with the `stream` subcommand.
    assert "stream --channel 3 --stream main --name cam3-main" in txt
    assert "stream --channel 3 --stream sub --name cam3-sub" in txt
    assert "H.265 2880x1620" in txt
    # Source endpoint is recorded (password never appears).
    assert "# Source: user@cam.example.tv:8100" in txt
    # Failed streams are not emitted as runnable args.
    assert "--channel 4 --stream main" not in txt


def test_format_streams_txt_handles_empty():
    txt = format_streams_txt([], source="user@host:8100")
    assert "No working streams" in txt
    assert "# Source: user@host:8100" in txt
