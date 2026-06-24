"""parse mode: probe channels x stream types and detect codec/resolution."""
import logging
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from relay.config import STREAM_TYPES
from relay.dhav import DhavParser

log = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    channel: int
    stream: str
    ok: bool
    codec: Optional[str]
    width: int
    height: int


def _detected(parser: DhavParser) -> bool:
    return parser.codec is not None and parser.resolution != (0, 0)


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def probe_streams(client, pairs: List[Tuple[int, str]], seconds: float,
                  concurrency: int = 4) -> List[ProbeResult]:
    """Probe (channel, stream) pairs in concurrent batches; preserve input order.

    All probes in a batch run simultaneously (one RealPlay session each, off the
    shared login) and share a single wait, so total time is roughly
    ceil(len(pairs)/concurrency) * seconds instead of len(pairs) * seconds. The
    data arrives on the SDK's own callback threads, so no thread pool is needed —
    we just keep N parsers alive at once and wait. A batch exits early once every
    stream in it has been detected.
    """
    results = {}
    for batch in _chunks(pairs, max(1, concurrency)):
        active = []  # (channel, stream, parser, handle)
        for channel, stream in batch:
            parser = DhavParser()

            def on_raw(data, _p=parser):  # bind parser per iteration
                for _ in _p.feed(data):
                    pass

            try:
                handle = client.start_realplay(channel, STREAM_TYPES[stream], on_raw)
            except Exception as e:
                log.info("ch%s %s: cannot start (%s)", channel, stream, e)
                results[(channel, stream)] = ProbeResult(channel, stream, False, None, 0, 0)
                continue
            active.append((channel, stream, parser, handle))

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if all(_detected(p) for _, _, p, _ in active):
                break
            time.sleep(0.1)

        for channel, stream, parser, handle in active:
            client.stop_realplay(handle)
            w, h = parser.resolution
            results[(channel, stream)] = ProbeResult(
                channel, stream, parser.codec is not None, parser.codec, w, h)

    return [results[pair] for pair in pairs]
