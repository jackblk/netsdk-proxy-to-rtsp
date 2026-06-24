# Multi-camera config-driven daemon — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one process publish many RTSP streams off a single Dahua login, driven by an editable `streams.yml`, with `parse` generating/updating that file non-destructively.

**Architecture:** A shared-login daemon (`StreamManager`) logs in once and runs N independent `StreamPipeline`s (one ffmpeg each) — always-on. A SDK refactor lets one login hold many concurrent RealPlay sessions, dispatched by handle. `parse` probes the device and merges results into `streams.yml`. MediaMTX's runtime config is now generated with PyYAML instead of a shell heredoc.

**Tech Stack:** Python 3.11, ctypes/NetSDK, ffmpeg `-f dhav -c copy`, MediaMTX, PyYAML, uv, pytest.

## Global Constraints

- Python `>=3.11`.
- Dependencies: `python-dotenv`, `pyyaml` only — **do not add others** (PyYAML already installed by maintainer; do not edit `pyproject.toml`).
- `relay/streams_config.py` and `relay/mediamtx_config.py` must stay **free of SDK/ffmpeg imports** (pure, unit-testable without a device), mirroring `dhav.py`.
- Keep ctypes callback objects referenced for the SDK's lifetime (GC of a live callback crashes the SDK).
- The SDK callback thread must return fast — only enqueue, never block.
- Scratch/experiment scripts go in `tmp/` (git-ignored), never large heredocs.
- Run Python via `uv run`.
- Stream identity is `(channel, stream)`. `name` and `enable` are user-owned and must never be overwritten by `parse` except: `parse` may flip `enable` to `false` when a stream fails validation.

---

### Task 1: `streams_config` data model + load/save

**Files:**
- Create: `relay/streams_config.py`
- Test: `tests/test_streams_config.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces:
  - `StreamEntry(channel: int, stream: str, name: str, enable: bool = True, metadata: dict = {})` (dataclass)
  - `load(path: str) -> list[StreamEntry]` — returns `[]` if the file is missing or empty.
  - `save(path: str, entries: list[StreamEntry]) -> None` — writes `{"streams": [...]}` YAML, key order preserved, `metadata` omitted when empty.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_streams_config.py
import relay  # noqa: F401  (path bootstrap)
from relay.streams_config import StreamEntry, load, save


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "streams.yml"
    entries = [
        StreamEntry(channel=3, stream="main", name="cam3-main", enable=True,
                    metadata={"codec": "h264", "resolution": "960x576"}),
        StreamEntry(channel=3, stream="sub", name="cam3-sub", enable=False),
    ]
    save(str(p), entries)
    loaded = load(str(p))
    assert loaded == entries


def test_load_missing_file_returns_empty(tmp_path):
    assert load(str(tmp_path / "nope.yml")) == []


def test_save_omits_empty_metadata(tmp_path):
    p = tmp_path / "streams.yml"
    save(str(p), [StreamEntry(channel=1, stream="main", name="cam1-main")])
    text = p.read_text()
    assert "metadata" not in text
    assert "enable: true" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_streams_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'relay.streams_config'`

- [ ] **Step 3: Write minimal implementation**

```python
# relay/streams_config.py
"""Load/save/merge the editable streams.yml (pure; no SDK/ffmpeg imports)."""
from dataclasses import dataclass, field
from typing import List

import yaml


@dataclass
class StreamEntry:
    channel: int
    stream: str
    name: str
    enable: bool = True
    metadata: dict = field(default_factory=dict)


def load(path: str) -> List[StreamEntry]:
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return []
    out = []
    for item in (data.get("streams") or []):
        out.append(StreamEntry(
            channel=item["channel"],
            stream=item["stream"],
            name=item["name"],
            enable=item.get("enable", True),
            metadata=item.get("metadata") or {},
        ))
    return out


def _to_dict(e: StreamEntry) -> dict:
    d = {"channel": e.channel, "stream": e.stream, "name": e.name, "enable": e.enable}
    if e.metadata:
        d["metadata"] = e.metadata
    return d


def save(path: str, entries: List[StreamEntry]) -> None:
    data = {"streams": [_to_dict(e) for e in entries]}
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_streams_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add relay/streams_config.py tests/test_streams_config.py
git commit -m "feat: streams.yml data model with load/save"
```

---

### Task 2: `streams_config.merge` (non-destructive)

**Files:**
- Modify: `relay/streams_config.py`
- Test: `tests/test_streams_config.py`

**Interfaces:**
- Consumes: `StreamEntry` (Task 1).
- Produces: `merge(existing: list[StreamEntry], results) -> list[StreamEntry]` where each `result` is any object with attributes `channel: int`, `stream: str`, `ok: bool`, `codec: str | None`, `width: int`, `height: int` (this is the shape of `relay.probe.ProbeResult`). Mutates matching existing entries in place and appends new ones; existing order preserved, new entries appended in probe order.

Merge rules (exactly these mutations, nothing else):

| Situation | Action |
|---|---|
| New (channel,stream), `ok` | add `enable=True` + metadata |
| New (channel,stream), not `ok` | add `enable=False` |
| Existing, not `ok` | set `enable=False`; refresh metadata |
| Existing, `ok` | leave `enable`/`name`; refresh metadata only |
| Existing, not in results | untouched |

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_streams_config.py
from types import SimpleNamespace
from relay.streams_config import merge


def _r(channel, stream, ok, codec=None, width=0, height=0):
    return SimpleNamespace(channel=channel, stream=stream, ok=ok,
                           codec=codec, width=width, height=height)


def test_merge_adds_new_valid_and_invalid():
    out = merge([], [
        _r(3, "main", True, "h264", 960, 576),
        _r(3, "sub", False),
    ])
    by = {(e.channel, e.stream): e for e in out}
    assert by[(3, "main")].enable is True
    assert by[(3, "main")].name == "cam3-main"
    assert by[(3, "main")].metadata == {"codec": "h264", "resolution": "960x576"}
    assert by[(3, "sub")].enable is False


def test_merge_preserves_user_edits_on_valid_stream():
    existing = [StreamEntry(channel=3, stream="main", name="front-door", enable=False)]
    out = merge(existing, [_r(3, "main", True, "h265", 2880, 1620)])
    assert out[0].name == "front-door"          # user rename kept
    assert out[0].enable is False               # user disable kept
    assert out[0].metadata == {"codec": "h265", "resolution": "2880x1620"}


def test_merge_disables_now_invalid_existing():
    existing = [StreamEntry(channel=3, stream="main", name="cam3-main", enable=True)]
    out = merge(existing, [_r(3, "main", False)])
    assert out[0].enable is False


def test_merge_leaves_unprobed_existing_untouched():
    existing = [StreamEntry(channel=9, stream="main", name="cam9-main", enable=True)]
    out = merge(existing, [_r(3, "main", True, "h264", 960, 576)])
    keep = next(e for e in out if e.channel == 9)
    assert keep.enable is True and keep.name == "cam9-main"
    assert any(e.channel == 3 for e in out)     # new one appended
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_streams_config.py -k merge -v`
Expected: FAIL with `ImportError: cannot import name 'merge'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to relay/streams_config.py
def _meta_of(r) -> dict:
    meta = {}
    if r.codec:
        meta["codec"] = r.codec
    if r.width:
        meta["resolution"] = f"{r.width}x{r.height}"
    return meta


def merge(existing: List[StreamEntry], results) -> List[StreamEntry]:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_streams_config.py -v`
Expected: PASS (all tests, including Task 1's)

- [ ] **Step 5: Commit**

```bash
git add relay/streams_config.py tests/test_streams_config.py
git commit -m "feat: non-destructive merge of probe results into streams config"
```

---

### Task 3: `mediamtx_config` generator

**Files:**
- Create: `relay/mediamtx_config.py`
- Test: `tests/test_mediamtx_config.py`

**Interfaces:**
- Consumes: nothing (pure + a thin CLI).
- Produces: `build(base: dict, port: int, username: str, password: str) -> dict`. Sets `rtspAddress=f":{port}"`; when **both** creds truthy, adds `authInternalUsers` (publish+read user; localhost `any` for api/metrics/pprof). CLI: `python -m relay.mediamtx_config --base <path> --out <path>` reads `TARGET_PORT`/`TARGET_USERNAME`/`TARGET_PASSWORD` from env.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mediamtx_config.py
import relay  # noqa: F401
import yaml
from relay.mediamtx_config import build


def test_build_sets_rtsp_address_and_no_auth_when_creds_absent():
    out = build({"logLevel": "info"}, port=8554, username="", password="")
    assert out["rtspAddress"] == ":8554"
    assert "authInternalUsers" not in out


def test_build_adds_auth_when_both_creds_present():
    out = build({}, port=9000, username="bob", password="pw")
    users = out["authInternalUsers"]
    assert users[0]["user"] == "bob" and users[0]["pass"] == "pw"
    assert {"action": "publish"} in users[0]["permissions"]
    assert {"action": "read"} in users[0]["permissions"]
    assert users[1]["user"] == "any"
    assert users[1]["ips"] == ["127.0.0.1", "::1"]


def test_special_characters_in_creds_roundtrip_safely():
    out = build({}, port=8554, username="us'er:1", password="p@ss'w:rd")
    dumped = yaml.safe_dump(out)
    reloaded = yaml.safe_load(dumped)
    assert reloaded["authInternalUsers"][0]["user"] == "us'er:1"
    assert reloaded["authInternalUsers"][0]["pass"] == "p@ss'w:rd"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mediamtx_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'relay.mediamtx_config'`

- [ ] **Step 3: Write minimal implementation**

```python
# relay/mediamtx_config.py
"""Generate the runtime MediaMTX config (pure build + thin CLI). No SDK imports."""
import argparse
import os

import yaml


def build(base: dict, port: int, username: str, password: str) -> dict:
    cfg = dict(base)
    cfg["rtspAddress"] = f":{port}"
    if username and password:
        cfg["authInternalUsers"] = [
            {"user": username, "pass": password, "ips": [],
             "permissions": [{"action": "publish"}, {"action": "read"}]},
            {"user": "any", "ips": ["127.0.0.1", "::1"],
             "permissions": [{"action": "api"}, {"action": "metrics"},
                             {"action": "pprof"}]},
        ]
    return cfg


def main():
    ap = argparse.ArgumentParser(prog="relay.mediamtx_config")
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    with open(args.base) as f:
        base = yaml.safe_load(f) or {}
    cfg = build(
        base,
        port=int(os.environ.get("TARGET_PORT") or 8554),
        username=os.environ.get("TARGET_USERNAME") or "",
        password=os.environ.get("TARGET_PASSWORD") or "",
    )
    with open(args.out, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    print(f"mediamtx_config: wrote {args.out} "
          f"(auth {'enabled' if 'authInternalUsers' in cfg else 'disabled'})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mediamtx_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add relay/mediamtx_config.py tests/test_mediamtx_config.py
git commit -m "feat: generate MediaMTX runtime config with PyYAML"
```

---

### Task 4: SDK multi-RealPlay refactor

**Files:**
- Modify: `relay/sdk_client.py`
- Modify: `relay/probe.py` (caller: use returned handle)
- Modify: `relay/__main__.py:20-38` (`cmd_stream` caller: capture handle)
- Live validation script: `tmp/two_streams.py`

**Interfaces:**
- Produces (changed `DahuaClient` API):
  - `start_realplay(channel: int, play_type: int, on_raw: RawDataHandler) -> int` — now **returns the RealPlay handle**; supports multiple concurrent calls per login.
  - `stop_realplay(handle: int) -> None` — stops one session by handle.
  - `cleanup() -> None` — stops **all** sessions, then logout + SDK cleanup.
- Consumes: existing `login`, `init`.

This task is SDK-dependent — validated live (per AGENTS.md), not via unit tests. Requires a working `.env` and device; channel 3 is the known-good channel.

- [ ] **Step 1: Refactor `DahuaClient` for many sessions**

Replace the single-session fields/methods in `relay/sdk_client.py`. In `__init__`, replace `self._realplay_id`/`self._realdata_cb`/`self._on_raw` with:

```python
        # One callback object, registered for every RealPlay; dispatch by handle.
        self._realdata_cb = fRealDataCallBackEx2(self._raw_trampoline)
        self._handlers: dict[int, RawDataHandler] = {}
```

Replace `start_realplay`, `stop_realplay`, and the `_raw_trampoline`:

```python
    def start_realplay(self, channel: int, play_type: int, on_raw: RawDataHandler) -> int:
        """Start a headless preview session; return its handle. May be called many times."""
        handle = self._sdk.RealPlayEx(self._login_id, channel, 0, play_type)
        if handle == 0:
            raise RuntimeError(f"RealPlayEx failed for channel={channel} play_type={play_type}")
        self._handlers[handle] = on_raw
        ok = self._sdk.SetRealDataCallBackEx2(
            handle, self._realdata_cb, 0, EM_REALDATA_FLAG.RAW_DATA
        )
        if not ok:
            self._sdk.StopRealPlayEx(handle)
            del self._handlers[handle]
            raise RuntimeError("SetRealDataCallBackEx2 failed")
        log.info("RealPlay started: handle=%s channel=%s play_type=%s", handle, channel, play_type)
        return handle

    def stop_realplay(self, handle: int):
        if handle and handle in self._handlers:
            self._sdk.StopRealPlayEx(handle)
            del self._handlers[handle]

    # --- ctypes callbacks ---
    def _raw_trampoline(self, lRealHandle, dwDataType, pBuffer, dwBufSize, param, dwUser):
        # dwDataType == 0 => raw (DHAV) stream bytes (video+audio multiplexed).
        if dwDataType != 0 or dwBufSize <= 0:
            return
        handler = self._handlers.get(lRealHandle)
        if handler is None:
            return
        data = ctypes.string_at(pBuffer, dwBufSize)
        try:
            handler(data)
        except Exception:                       # never let an exception cross into the SDK
            log.exception("raw data handler error")
```

Update `cleanup` to stop all sessions:

```python
    def cleanup(self):
        for handle in list(self._handlers):
            self._sdk.StopRealPlayEx(handle)
        self._handlers.clear()
        self.logout()
        self._sdk.Cleanup()
```

Delete the old single-session `stop_realplay` body that referenced `self._realplay_id` and the `_on_raw` attribute.

- [ ] **Step 2: Update the `probe.py` caller**

In `relay/probe.py`, `probe_stream`, capture and pass the handle:

```python
    try:
        handle = client.start_realplay(channel, STREAM_TYPES[stream], on_raw)
    except Exception as e:
        log.info("ch%s %s: cannot start (%s)", channel, stream, e)
        return ProbeResult(channel, stream, False, None, 0, 0)
    got.wait(timeout=seconds)
    client.stop_realplay(handle)
```

- [ ] **Step 3: Update the `cmd_stream` caller**

In `relay/__main__.py`, `cmd_stream`, capture the handle (cleanup still stops all):

```python
    handle = client.start_realplay(args.channel, STREAM_TYPES[args.stream], pipeline.on_raw)
```

(`handle` is unused locally because `client.cleanup()` stops everything; keep the assignment for symmetry/log clarity, or drop it — either is fine.)

- [ ] **Step 4: Live-validate two concurrent sessions**

Write `tmp/two_streams.py`:

```python
"""Live check: one login, two concurrent RealPlay sessions both deliver bytes."""
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import relay  # noqa: F401
from dotenv import load_dotenv
from relay.config import Config, STREAM_TYPES
from relay.sdk_client import DahuaClient

load_dotenv()
cfg = Config.from_env(os.environ)
counts = {"main": 0, "sub": 0}

def mk(key):
    def cb(data):
        counts[key] += len(data)
    return cb

c = DahuaClient(); c.init(); c.login(cfg.host, cfg.port, cfg.username, cfg.password)
h1 = c.start_realplay(3, STREAM_TYPES["main"], mk("main"))
h2 = c.start_realplay(3, STREAM_TYPES["sub"], mk("sub"))
time.sleep(5)
c.cleanup()
print("bytes:", counts)
assert counts["main"] > 0 and counts["sub"] > 0, "both sessions must deliver data"
print("OK: two concurrent sessions delivered data")
```

Run: `uv run python tmp/two_streams.py`
Expected: prints non-zero byte counts for both and `OK: two concurrent sessions delivered data`. (If `sub` is unavailable on your device, use two different channels.)

- [ ] **Step 5: Confirm no regression in existing tests + commit**

Run: `uv run pytest -q`
Expected: PASS (existing suite unaffected; SDK code has no unit tests)

```bash
git add relay/sdk_client.py relay/probe.py relay/__main__.py
git commit -m "feat: support multiple concurrent RealPlay sessions per login"
```

---

### Task 5: `StreamManager` daemon

**Files:**
- Create: `relay/manager.py`
- Live validation script: `tmp/run_manager.py`

**Interfaces:**
- Consumes: `Config` (publish_url), `StreamEntry` (Task 1), `DahuaClient` (Task 4), `StreamPipeline`, `STREAM_TYPES`.
- Produces:
  - `StreamManager(cfg: Config, entries: list[StreamEntry])`
  - `.start()` — login once; for each `enable=True` entry, start a `StreamPipeline` + RealPlay; a per-stream failure is logged and skipped (others continue).
  - `.stop()` — stop all RealPlay sessions and pipelines, then `cleanup()`.
  - `.active_count -> int` — number of streams successfully started.

SDK-dependent → live-validated.

- [ ] **Step 1: Implement `StreamManager`**

```python
# relay/manager.py
"""Shared-login daemon: one DahuaClient login, many StreamPipelines (always-on)."""
import logging
from typing import List

from relay.config import Config, STREAM_TYPES
from relay.pipeline import StreamPipeline
from relay.sdk_client import DahuaClient
from relay.streams_config import StreamEntry

log = logging.getLogger(__name__)


class StreamManager:
    def __init__(self, cfg: Config, entries: List[StreamEntry]):
        self.cfg = cfg
        self._entries = [e for e in entries if e.enable]
        self._client = DahuaClient()
        self._sessions = []  # list[(handle, StreamPipeline)]

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    def start(self):
        self._client.init()
        self._client.login(self.cfg.host, self.cfg.port,
                           self.cfg.username, self.cfg.password)
        for e in self._entries:
            pipeline = StreamPipeline(self.cfg.publish_url(e.name))
            pipeline.start()
            try:
                handle = self._client.start_realplay(
                    e.channel, STREAM_TYPES[e.stream], pipeline.on_raw)
            except Exception:
                log.exception("ch%s %s: failed to start; skipping", e.channel, e.stream)
                pipeline.stop()
                continue
            self._sessions.append((handle, pipeline))
            log.info("streaming ch%s %s -> %s", e.channel, e.stream,
                     self.cfg.viewer_url(e.name))

    def stop(self):
        for handle, pipeline in self._sessions:
            self._client.stop_realplay(handle)
            pipeline.stop()
        self._sessions.clear()
        self._client.cleanup()
```

- [ ] **Step 2: Live-validate the manager**

Write `tmp/run_manager.py`:

```python
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import relay  # noqa: F401
from dotenv import load_dotenv
from relay.config import Config
from relay.manager import StreamManager
from relay.streams_config import StreamEntry

load_dotenv()
cfg = Config.from_env(os.environ)
entries = [
    StreamEntry(channel=3, stream="main", name="cam3-main", enable=True),
    StreamEntry(channel=3, stream="sub", name="cam3-sub", enable=True),
]
m = StreamManager(cfg, entries)
m.start()
print("active:", m.active_count)
time.sleep(8)
m.stop()
print("stopped")
```

Run (one terminal needs MediaMTX, or point publish at an existing RTSP target):
`uv run python tmp/run_manager.py`
Then from another shell verify both paths carry video:
`ffprobe -rtsp_transport tcp rtsp://<TARGET_HOST>:<TARGET_PORT>/cam3-main`
Expected: `active: 2`, and ffprobe shows a video stream on each path.

- [ ] **Step 3: Confirm tests still green + commit**

Run: `uv run pytest -q`
Expected: PASS

```bash
git add relay/manager.py
git commit -m "feat: StreamManager runs many streams off one login"
```

---

### Task 6: CLI — add `run`, rewire `parse`

**Files:**
- Modify: `relay/__main__.py`
- Modify: `relay/probe.py` (remove `format_streams_txt`)
- Delete: `tests/test_probe_format.py`

**Interfaces:**
- Consumes: `streams_config.load/save/merge` (Tasks 1-2), `StreamManager` (Task 5), `probe_stream` (existing).
- Produces:
  - `relay run [--config streams.yml]` — runs the daemon; exits non-zero with a clear log if no enabled streams.
  - `relay parse [--config streams.yml] [--streams main,sub] [--channels N] [--probe-seconds N]` — load → probe → merge → save; no more `streams.txt`.

- [ ] **Step 1: Remove `format_streams_txt` from `probe.py`**

Delete the `format_streams_txt` function and the now-unused `_codec_label` helper in `relay/probe.py`. Keep `ProbeResult` and `probe_stream`.

- [ ] **Step 2: Delete its test**

```bash
git rm tests/test_probe_format.py
```

- [ ] **Step 3: Rewire `__main__.py`**

Replace the imports and `cmd_parse`, add `cmd_run`, and wire the parsers. New top imports:

```python
import sys
from relay import streams_config
from relay.config import Config, STREAM_TYPES
from relay.manager import StreamManager
from relay.pipeline import StreamPipeline
from relay.probe import probe_stream
from relay.sdk_client import DahuaClient
```

`cmd_run`:

```python
def cmd_run(cfg: Config, args) -> int:
    entries = streams_config.load(args.config)
    if not any(e.enable for e in entries):
        log.error("No enabled streams in %s — nothing to do. Run `relay parse` first.",
                  args.config)
        return 1
    manager = StreamManager(cfg, entries)
    manager.start()
    log.info("Running %d stream(s) (RTSP auth %s)", manager.active_count,
             "enabled" if cfg.rtsp_auth_enabled else "disabled")
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    while not stop.is_set():
        time.sleep(0.5)
    log.info("Shutting down...")
    manager.stop()
    return 0
```

`cmd_parse` (rewired):

```python
def cmd_parse(cfg: Config, args) -> int:
    client = DahuaClient()
    client.init()
    client.login(cfg.host, cfg.port, cfg.username, cfg.password)
    channels = (range(args.channels) if args.channels
                else range(client.channel_count))
    stream_names = args.streams.split(",")
    results = []
    for ch in channels:
        for st in stream_names:
            log.info("Probing ch%s %s...", ch, st)
            results.append(probe_stream(client, ch, st, args.probe_seconds))
    client.cleanup()
    existing = streams_config.load(args.config)
    merged = streams_config.merge(existing, results)
    streams_config.save(args.config, merged)
    enabled = sum(1 for e in merged if e.enable)
    print(f"Wrote {args.config}: {len(merged)} stream(s), {enabled} enabled.")
    return 0
```

Parser wiring in `main()` — replace the `parse` subparser block and add `run`, and dispatch via exit code:

```python
    r = sub.add_parser("run", help="run all enabled streams from a config file")
    r.add_argument("--config", default="streams.yml")

    p = sub.add_parser("parse", help="probe channels/streams -> merge into streams.yml")
    p.add_argument("--config", default="streams.yml")
    p.add_argument("--streams", default="main,sub")
    p.add_argument("--channels", type=int, default=0, help="0 = use device channel count")
    p.add_argument("--probe-seconds", type=float, default=3.0)

    args = ap.parse_args()
    if args.cmd == "stream":
        cmd_stream(cfg, args)
        rc = 0
    elif args.cmd == "run":
        rc = cmd_run(cfg, args)
    else:
        rc = cmd_parse(cfg, args)
    sys.exit(rc)
```

- [ ] **Step 4: Verify `run` exits non-zero on empty config**

Run: `uv run python -m relay run --config /tmp/empty.yml; echo "exit=$?"`
Expected: logs "No enabled streams" and `exit=1`.

- [ ] **Step 5: Live end-to-end — parse then run**

Run: `uv run python -m relay parse --config tmp/streams.yml --channels 4`
Expected: writes `tmp/streams.yml` with discovered streams; channel 3 present + enabled.
Then: edit nothing, `uv run python -m relay run --config tmp/streams.yml` (needs MediaMTX up), verify viewer URLs work, Ctrl-C exits cleanly.

- [ ] **Step 6: Confirm full suite + commit**

Run: `uv run pytest -q`
Expected: PASS (test_probe_format removed; streams_config + mediamtx_config tests present)

```bash
git add relay/__main__.py relay/probe.py
git commit -m "feat: add `run` daemon command and rewire `parse` to streams.yml"
```

---

### Task 7: Deploy wiring

**Files:**
- Modify: `deploy/entrypoint.sh`
- Modify: `docker-compose.yml`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `relay.mediamtx_config` CLI (Task 3), `relay run` (Task 6).

- [ ] **Step 1: Simplify `entrypoint.sh`**

Replace the config-building section (the `cp`, the `MTX_RTSPADDRESS` export, and the whole `if [ -n "$TARGET_USERNAME" ] ... fi` heredoc block — current lines 4-37) with:

```bash
# Build the runtime MediaMTX config from the committed base using PyYAML
# (rtspAddress + optional auth block). No shell string-munging.
MTX_CONFIG=/tmp/mediamtx.runtime.yml
/app/.venv/bin/python -m relay.mediamtx_config \
  --base /app/deploy/mediamtx.yml --out "$MTX_CONFIG"
```

Keep the rest (start MediaMTX on `$MTX_CONFIG`, signal trap, `python -m relay "$@"`) unchanged.

- [ ] **Step 2: Verify the generator runs in-place**

Run (locally, simulating the container call):
`TARGET_PORT=8554 TARGET_USERNAME=bob TARGET_PASSWORD=pw uv run python -m relay.mediamtx_config --base deploy/mediamtx.yml --out /tmp/mtx.yml && cat /tmp/mtx.yml`
Expected: valid YAML with `rtspAddress: ':8554'` and an `authInternalUsers` block; rerun without the creds → no auth block.

- [ ] **Step 3: Update `docker-compose.yml`**

Set the default command to the daemon, mount the config, keep the single-stream example commented:

```yaml
services:
  relay:
    container_name: rtsp-relay
    build:
      context: .
      dockerfile: deploy/Dockerfile
    env_file: .env
    ports:
      - "${TARGET_PORT:-8554}:${TARGET_PORT:-8554}"
    volumes:
      # Host-editable stream config. Must exist before `up` (run `relay parse`
      # or `touch streams.yml`) or Docker will create a directory here.
      - ./streams.yml:/app/streams.yml
    restart: unless-stopped
    command: ["run"]
    # One-off single stream instead of the config-driven daemon:
    # command: ["stream", "--channel", "3", "--stream", "main", "--name", "cam3-main"]
```

- [ ] **Step 4: Add `streams.yml` to `.gitignore`**

Add this line to `.gitignore` (next to the existing `streams.txt`):

```
streams.yml
```

- [ ] **Step 5: Commit**

```bash
git add deploy/entrypoint.sh docker-compose.yml .gitignore
git commit -m "feat: compose runs config-driven daemon; entrypoint generates MediaMTX config via PyYAML"
```

---

## Self-Review

**Spec coverage:**
- Command model (`run`/`parse`/`stream`) → Task 6 ✓
- `streams.yml` schema + `metadata` node → Tasks 1, 2 ✓
- Non-destructive merge (5 rules) → Task 2 ✓
- Shared-login daemon, always-on, per-stream failure isolation → Task 5 ✓
- SDK multi-RealPlay → Task 4 ✓
- `mediamtx_config` generator + entrypoint simplification → Tasks 3, 7 ✓
- Dockerfile unchanged / compose `run` + mount / `.gitignore streams.yml` → Task 7 ✓
- Empty-config exits non-zero → Task 6 Step 4 ✓
- PyYAML not re-added; pure modules SDK-free → Global Constraints, Tasks 1/3 ✓
- Tests: streams_config round-trip + 5 merge rules + mediamtx auth/special-char → Tasks 1-3 ✓

**Placeholder scan:** none — every code step shows full code; commands have expected output.

**Type consistency:** `start_realplay(...) -> int` and `stop_realplay(handle)` used consistently (Tasks 4, 5). `StreamEntry` fields identical across Tasks 1, 2, 5. `merge(existing, results)` result-shape matches `ProbeResult` (channel/stream/ok/codec/width/height) used in Task 6's probe loop. `build(base, port, username, password)` consistent across Task 3 and entrypoint env mapping in Task 7.
