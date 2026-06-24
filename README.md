# NetSDK → RTSP Proxy

Re-publishes a Dahua NVR's NetSDK live stream as RTSP, **without transcoding**
(`ffmpeg -c copy`), so CPU and latency stay low.

```
Dahua device ──NetSDK──▶ relay ──raw DHAV──▶ ffmpeg -f dhav -c copy ──▶ MediaMTX ──RTSP──▶ VLC / ffplay / NVR
```

## Quick start
The fastest path is Docker (it bundles MediaMTX + ffmpeg):

```bash
# 1. Add the NetSDK wheel into ./NetSDK/, then extract it (see NetSDK/README.md):
./NetSDK/setup.sh

# 2. Configure your device:
cp .env.example .env          # then edit HOST / USERNAME / PASSWORD

# 3. Discover which channels/streams work -> generates/updates config/streams.yml
#    (one entry per channel×stream; valid ones enable: true).
docker compose run --rm relay parse

# 4. Edit config/streams.yml — enable the streams you want and rename RTSP paths
#    to taste — then bring up the full stack (publishes every enabled stream):
docker compose up -d

# 5. View: open the generated playlist (all enabled streams at once) in VLC:
vlc config/output.m3u8
# or a single stream (add user:pass@ if you set RTSP auth):
ffplay -rtsp_transport tcp rtsp://localhost:8554/cam3-main
```

## Why?

Because sometimes you don't have ONVIF or RTSP available, you only have the proprietary endpoints, and you want to use standard RTSP clients (VLC, ffplay, NVRs, etc.) without transcoding.

Some Chinese devices (Dahua, KBVision, Imou...etc) use a proprietary protocol from Dahua under the hood, you can access the live stream using some of their official apps like SmartPSS, DMSS, etc. This project wraps Dahua's **NetSDK** to re-publish the live stream as RTSP.

## Get the SDK (required, not bundled)
This project wraps Dahua's proprietary **NetSDK**, which is **not** included in this
repo (it isn't freely redistributable). Download it yourself, paste the wheel into
`./NetSDK/`, and run `./NetSDK/setup.sh`.

👉 Full instructions, the expected layout, and licensing details are in
**[NetSDK/README.md](NetSDK/README.md)**.

## Configure
Copy [.env.example](.env.example) to `.env` (git-ignored) and fill it in:
```
HOST=your-device-host
HOST_PORT=37777
USERNAME=...
PASSWORD=...
TARGET_HOST=0.0.0.0     # RTSP server bind/advertise host
TARGET_PORT=8554        # RTSP server port

# Optional — set BOTH to password-protect the RTSP stream (publish + view).
# Leave unset for open access.
TARGET_USERNAME=
TARGET_PASSWORD=
```

When `TARGET_USERNAME`/`TARGET_PASSWORD` are set, MediaMTX requires those
credentials to publish *and* to view, so clients must connect with
`rtsp://<user>:<pass>@<host>:<port>/<path>`. On startup the relay logs the exact
view URL (`Stream ready — view at: ...`).

## Local (dev)
Requires `uv`, `ffmpeg`, and `mediamtx` on PATH.
```bash
uv sync

# 1. Discover which channels/streams work -> generates/updates streams.yml
uv run python -m relay parse

# 2. Start the RTSP server (separate shell), then run every enabled stream:
mediamtx deploy/mediamtx.yml
uv run python -m relay run            # reads config/streams.yml (default)

# (or serve a single ad-hoc stream without a config file:)
uv run python -m relay stream --channel 3 --stream main --name cam3-main

# 3. View — open the generated playlist, or a single stream:
vlc config/output.m3u8
ffplay -rtsp_transport tcp rtsp://127.0.0.1:8554/cam3-main
```

## Docker
```bash
docker compose up --build        # runs `relay run` against the mounted streams.yml
# View (add user:pass@ if RTSP auth is enabled):
ffplay -rtsp_transport tcp rtsp://<host>:8554/cam3-main
```

Run `parse` mode in Docker instead (writes to the mounted ./config/streams.yml):
```bash
docker compose run --rm relay parse
```

The `./config` directory is bind-mounted into the container, so `config/streams.yml`
and `config/output.m3u8` are editable/viewable on the host. Only `config/README.md`
is committed; the generated files are git-ignored.

## Modes
- `parse [--config config/streams.yml] [--probe-concurrency 4]` — probes every
  channel × stream type briefly (concurrently, in batches), detects codec/resolution,
  and **non-destructively merges** results into `streams.yml`: adds newly-found
  streams, marks invalid ones `enable: false`, and leaves your hand-edits (renamed
  paths, manual enable/disable) intact. An unparseable `streams.yml` is regenerated.
- `run [--config config/streams.yml]` — logs in once and publishes **every
  `enable: true` stream** in the config, each at `rtsp://<host>:<TARGET_PORT>/<name>`,
  and writes `output.m3u8` (a playlist of all published streams) next to the config.
- `stream --channel N --stream main|sub|sub2 --name <path>` — serves one ad-hoc
  channel/stream without a config file.

## License
This project's own code is licensed under the [MIT License](LICENSE). It does **not**
cover the Dahua NetSDK, which is proprietary and must be obtained separately under
Dahua's own license — see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
