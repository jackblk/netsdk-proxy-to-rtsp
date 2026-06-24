# AGENTS.md

Guidance for working in this repo.

## What this is

A Linux service that bridges a Dahua NVR's proprietary **NetSDK** live stream to standard
**RTSP**, without transcoding. NetSDK delivers the device's native **DHAV** container; we
forward those bytes to `ffmpeg -f dhav -c copy` (ffmpeg demuxes DHAV natively), which
publishes to a bundled **MediaMTX** RTSP server.

```
Dahua device ──NetSDK──▶ relay ──raw DHAV──▶ ffmpeg -f dhav -c copy ──▶ MediaMTX ──RTSP──▶ VLC / ffplay / NVR
```

## Layout

- [relay/](relay/) — the Python package (`python -m relay`):
  - [config.py](relay/config.py) — env→`Config`; `STREAM_TYPES` maps `main/sub/sub2` → SDK play-type ints (0/3/4).
  - [sdk_client.py](relay/sdk_client.py) — `DahuaClient`: all ctypes/NetSDK use (init, login, headless RealPlay, raw callback, logout, cleanup).
  - [dhav.py](relay/dhav.py) — pure `DhavParser`: DHAV frames → `(codec, annexb_nal)`; codec + resolution detection. **Used only by `parse` mode** (not the streaming path). Fully unit-tested; no SDK/ffmpeg deps.
  - [publisher.py](relay/publisher.py) — `FfmpegPublisher`: spawns/monitors/restarts `ffmpeg -f dhav -c copy` (codec-agnostic; ffmpeg detects it).
  - [pipeline.py](relay/pipeline.py) — wires callback → bounded queue → ffmpeg (forwards raw DHAV bytes; backpressure: drop-oldest).
  - [manager.py](relay/manager.py) — `StreamManager`: the `run` daemon. One login, N concurrent `StreamPipeline`s (one ffmpeg each), always-on; a per-stream start failure is logged and skipped.
  - [streams_config.py](relay/streams_config.py) — pure `StreamEntry` + `load`/`save`/`merge` for `streams.yml`. No SDK/ffmpeg deps; `merge` is non-destructive (see Commands). Unit-tested.
  - [mediamtx_config.py](relay/mediamtx_config.py) — pure `build()` + CLI that generates the runtime MediaMTX config (rtspAddress + optional auth) via PyYAML. No SDK deps; used by the entrypoint.
  - [playlist.py](relay/playlist.py) — pure `build_m3u8()`: (name, url) pairs → M3U8 text. `run` writes `output.m3u8` (next to the config) so a player opens all streams at once.
  - [probe.py](relay/probe.py) — `parse` mode: probe channels×streams → `ProbeResult`s (merged into `streams.yml` by `streams_config`).
  - [__main__.py](relay/__main__.py) — CLI: `run`, `stream`, and `parse` subcommands, signal handling.
- `NetSDK/` — Dahua SDK (Python ctypes bindings + `Libs/linux64/*.so`). **Proprietary,
  NOT committed** (only its `README.md` + `setup.sh` are tracked; SDK files are git-ignored).
  Setup instructions live in [NetSDK/README.md](NetSDK/README.md). Do not edit the SDK.
- [scripts/capture_raw.py](scripts/capture_raw.py) — dump raw callback bytes to a `.dhav` file (used to make test fixtures).
- [tests/](tests/) — pytest; [tests/fixtures/ch3_main.dhav](tests/fixtures/ch3_main.dhav) is a real capture the DHAV tests run against.
- [deploy/](deploy/) — Dockerfile, entrypoint, mediamtx.yml. `docker-compose.yml` is at the repo **root**.
- Design/plan docs: [docs/superpowers/](docs/superpowers/).

## Commands

Uses **uv** (not pip/requirements.txt). Run Python through `uv run` (or the venv directly).

```bash
uv sync                                   # install deps
uv run pytest -q                          # run tests (all should pass)
uv run python -m relay parse              # probe -> merge into config/streams.yml (concurrent)
uv run python -m relay run                # publish enabled streams + write config/output.m3u8
uv run python -m relay stream --channel 3 --stream main --name cam3-main  # one ad-hoc stream
docker compose up --build                 # full stack (compose is at repo root; runs `run`)
```

`parse` is **non-destructive**: it adds newly-discovered streams, sets `enable: false` on
ones it can't validate, and refreshes `metadata` (codec/resolution) — but never overwrites a
user's `name` or their `enable` choice on a still-valid stream. Stream identity is
`(channel, stream)`. Comments in `streams.yml` are dropped on rewrite (PyYAML round-trip).

Runtime config lives in [config/](config/) (default `--config config/streams.yml`), which
docker-compose bind-mounts (`./config:/app/config`). Mounting the **directory** (not the
file) avoids Docker creating an empty dir when `streams.yml` doesn't exist yet. Only
`config/README.md` is committed; `streams.yml` + `output.m3u8` are git-ignored
([config/.gitignore](config/.gitignore)).

Config comes from `.env` (git-ignored): `HOST`, `HOST_PORT`, `USERNAME`, `PASSWORD`,
`TARGET_HOST`, `TARGET_PORT`. `TARGET_PORT` is the single RTSP port (MediaMTX listen +
publish target + exposed port; default 8554). Optional `TARGET_USERNAME` + `TARGET_PASSWORD`
enable RTSP auth: when **both** are set, the entrypoint adds a MediaMTX `authInternalUsers`
block requiring those creds to publish *and* view, and the proxy embeds them (URL-encoded)
in its publish/viewer URLs (`Config.publish_url`/`viewer_url`). Unset → open access.

## Non-obvious gotchas (learned the hard way)

- **DHAV is demuxed by ffmpeg, not by us.** The `RAW_DATA` callback yields Dahua's DHAV
  container. ffmpeg has a native `dhav` demuxer (`-f dhav`), so the streaming path just
  forwards raw bytes — it detects codec, parses frame headers, and assigns timestamps
  correctly. (We originally hand-parsed DHAV→Annex-B in Python; that was fragile — the
  per-frame header occasionally contains a false Annex-B start code, leaking garbage NALs
  that the decoder choked on as malformed SPS/SEI. `-f dhav` avoids all of it.)
- **`DhavParser` (in [dhav.py](relay/dhav.py)) is `parse`-mode only** — in-process codec/
  resolution detection so probing doesn't spawn ffmpeg per channel. Notes for that parser:
  frame layout is magic `DHAV`, little-endian total length at **offset 12**, Annex-B payload,
  `dhav` tail; `is_valid_nal_type` filters header-derived garbage NALs; and H.264 SPS parsing
  **must** skip scaling lists (`_skip_scaling_list`, Dahua sets `seq_scaling_matrix_present=1`)
  or resolution desyncs. (Could be replaced with `ffprobe -f dhav` for one DHAV implementation.)
- **Codec is per-stream, detected at runtime** (this device's "main" is H.264, not H.265).
  Never hardcode the codec.
- **SDK callback thread must return fast** — it only enqueues; all parsing/ffmpeg work happens
  on the pipeline worker thread. Keep ctypes callback objects referenced (GC would crash the SDK).
- **`动态库加载失败`** ("library load failed") prints on init because one non-critical `.so`
  fails to load; login/RealPlay still work. Harmless.
- **`.so` discovery**: `LD_LIBRARY_PATH=.../NetSDK/Libs/linux64` (set in the Dockerfile). The
  `NetSDK` package uses relative imports and has no `__init__.py`; `relay/__init__.py` puts the
  project root on `sys.path` so `from NetSDK.NetSDK import NetClient` resolves.
- **Process-tree footgun when testing**: `pkill -f "relay stream"` also matches the shell
  running it — don't. Target exact pids.
- **Viewer URL vs. bind address**: `TARGET_HOST` is MediaMTX's bind/advertise address, so
  it's usually `0.0.0.0` — which a player can't connect to. `Config.viewer_host` therefore
  rewrites `0.0.0.0`/`::`/`""` to `localhost` for logged viewer URLs and `output.m3u8`
  (publish URLs always stay on `127.0.0.1`). Set a real `TARGET_HOST` for remote viewing.

## Conventions

- TDD for pure logic (config, DHAV parser, `streams_config` load/save/merge, `mediamtx_config`
  build). SDK/ffmpeg/end-to-end are validated by running against a live device (channel 3 is
  the known-good test channel).
- Keep `relay/` modules small and single-purpose; `dhav.py`, `streams_config.py`, and
  `mediamtx_config.py` stay free of SDK/ffmpeg imports.
- Scratch/experiment scripts go in `tmp/` (git-ignored). `.dhav` captures, `streams.txt`, and
  the generated `config/streams.yml` + `config/output.m3u8` are git-ignored (except the
  committed test fixture and `config/README.md`).
