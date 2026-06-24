"""Load/save/merge the editable streams.yml (pure; no SDK/ffmpeg imports)."""
import logging
import os
from dataclasses import dataclass, field
from typing import List

import yaml

log = logging.getLogger(__name__)


@dataclass
class StreamEntry:
    channel: int
    stream: str
    name: str
    enable: bool = True
    metadata: dict = field(default_factory=dict)


def load(path: str) -> List[StreamEntry]:
    """Read streams.yml. A missing or unparseable file yields []; for `parse`
    that means it gets regenerated/overwritten rather than crashing."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        return []
    except yaml.YAMLError as e:
        log.warning("%s is not valid YAML (%s); treating as empty — `parse` will overwrite it.",
                    path, e.__class__.__name__)
        return []
    if not isinstance(data, dict):
        if data is not None:
            log.warning("%s is not a mapping; treating as empty.", path)
        return []
    out = []
    for item in (data.get("streams") or []):
        if not isinstance(item, dict) or "channel" not in item or "stream" not in item:
            log.warning("skipping malformed stream entry in %s: %r", path, item)
            continue
        out.append(StreamEntry(
            channel=item["channel"],
            stream=item["stream"],
            name=item.get("name") or f"cam{item['channel']}-{item['stream']}",
            enable=item.get("enable", True),
            metadata=item.get("metadata") or {},
        ))
    return out


def find_enabled(entries: List[StreamEntry], name: str) -> StreamEntry:
    """Return the enabled entry with this name, or raise LookupError."""
    for e in entries:
        if e.name == name:
            if not e.enable:
                raise LookupError(f"stream {name!r} is disabled")
            return e
    raise LookupError(f"no stream named {name!r}")


def _to_dict(e: StreamEntry) -> dict:
    d = {"channel": e.channel, "stream": e.stream, "name": e.name, "enable": e.enable}
    if e.metadata:
        d["metadata"] = e.metadata
    return d


def save(path: str, entries: List[StreamEntry]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    data = {"streams": [_to_dict(e) for e in entries]}
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)


def _meta_of(r) -> dict:
    meta = {}
    if r.codec:
        meta["codec"] = r.codec
    if r.width:
        meta["resolution"] = f"{r.width}x{r.height}"
    return meta


def merge(existing: List[StreamEntry], results) -> List[StreamEntry]:
    """Non-destructively fold probe results into the existing config.

    `results` are objects with attributes channel/stream/ok/codec/width/height
    (the shape of relay.probe.ProbeResult). Existing entries keep their `name`
    and `enable` unless a stream fails validation (then `enable=False`); metadata
    is refreshed from the probe. New streams are appended in probe order.
    """
    by_key = {(e.channel, e.stream): e for e in existing}
    existing_keys = set(by_key)
    for r in results:
        key = (r.channel, r.stream)
        meta = _meta_of(r)
        if key in by_key:
            e = by_key[key]
            if not r.ok:
                e.enable = False
            if meta:
                e.metadata = meta
        else:
            by_key[key] = StreamEntry(
                channel=r.channel, stream=r.stream,
                name=f"cam{r.channel}-{r.stream}",
                enable=bool(r.ok), metadata=meta,
            )
    out = list(existing)  # preserve original order + in-place mutations
    for r in results:
        key = (r.channel, r.stream)
        if key not in existing_keys:
            out.append(by_key[key])
            existing_keys.add(key)
    return out
