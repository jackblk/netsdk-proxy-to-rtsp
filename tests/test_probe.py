from pathlib import Path

import relay  # noqa: F401
from relay.probe import probe_streams

FIXTURE = Path(__file__).parent / "fixtures" / "ch3_main.dhav"


class FakeClient:
    """Feeds the real DHAV fixture for channel 3; fails RealPlay for others."""

    def __init__(self):
        self._data = FIXTURE.read_bytes()
        self._next = 0
        self.stopped = []

    def start_realplay(self, channel, play_type, on_raw):
        if channel != 3:
            raise RuntimeError("RealPlayEx failed")
        on_raw(self._data)          # deliver synchronously so detection is instant
        self._next += 1
        return self._next

    def stop_realplay(self, handle):
        self.stopped.append(handle)


def test_probe_streams_preserves_order_and_detects():
    pairs = [(3, "main"), (5, "main"), (3, "sub")]
    client = FakeClient()
    results = probe_streams(client, pairs, seconds=0.3, concurrency=2)

    assert [(r.channel, r.stream) for r in results] == pairs   # input order kept
    by = {(r.channel, r.stream): r for r in results}
    assert by[(3, "main")].ok is True
    assert by[(3, "main")].codec == "h264"
    assert by[(3, "main")].width == 960 and by[(3, "main")].height == 576
    assert by[(5, "main")].ok is False                         # start failed
    assert by[(3, "sub")].ok is True
    # Two channel-3 sessions were started and both stopped.
    assert len(client.stopped) == 2
