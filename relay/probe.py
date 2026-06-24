"""parse mode: probe channels x stream types and detect codec/resolution."""
import logging
import threading
from dataclasses import dataclass
from typing import Optional

from relay.config import STREAM_TYPES
from relay.dhav import DhavParser
from relay.sdk_client import DahuaClient

log = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    channel: int
    stream: str
    ok: bool
    codec: Optional[str]
    width: int
    height: int


def probe_stream(client: DahuaClient, channel: int, stream: str, seconds: float) -> ProbeResult:
    """Briefly open one channel/stream and detect codec/resolution."""
    parser = DhavParser()
    got = threading.Event()

    def on_raw(data: bytes):
        for _ in parser.feed(data):
            if parser.codec and parser.resolution != (0, 0):
                got.set()

    try:
        handle = client.start_realplay(channel, STREAM_TYPES[stream], on_raw)
    except Exception as e:
        log.info("ch%s %s: cannot start (%s)", channel, stream, e)
        return ProbeResult(channel, stream, False, None, 0, 0)
    got.wait(timeout=seconds)
    client.stop_realplay(handle)
    ok = parser.codec is not None
    w, h = parser.resolution
    return ProbeResult(channel, stream, ok, parser.codec, w, h)
