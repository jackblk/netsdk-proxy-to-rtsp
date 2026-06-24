"""parse mode: probe channels x stream types and emit copy-paste stream args."""
import logging
import threading
from dataclasses import dataclass
from typing import List, Optional

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


def _codec_label(codec: Optional[str]) -> str:
    return {"h264": "H.264", "h265": "H.265"}.get(codec or "", "?")


def format_streams_txt(results: List[ProbeResult], source: str = "") -> str:
    header = []
    if source:
        header.append(f"# Source: {source}")
    working = [r for r in results if r.ok]
    if not working:
        header.append("# No working streams found.")
        return "\n".join(header) + "\n"
    header.append("# Working streams (copy a full line below as `relay` arguments).")
    header.append("")
    lines = header
    for r in working:
        res = f"{r.width}x{r.height}" if r.width else "?"
        lines.append(f"# ch{r.channel} {r.stream}\t{_codec_label(r.codec)} {res}")
        lines.append(f"stream --channel {r.channel} --stream {r.stream} --name cam{r.channel}-{r.stream}")
        lines.append("")
    return "\n".join(lines)


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
