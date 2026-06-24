# Multi-camera config-driven daemon — design

**Date:** 2026-06-24
**Status:** Approved (pending spec review)

## Goal

Turn the relay from a single-stream bridge (`relay stream` = one channel/stream =
one process) into a config-driven service where **one process publishes many
streams off a single device login**, driven by an editable `streams.yml`.

Out of scope for this iteration (deferred): on-demand / lazy streaming (start a
stream only when a viewer connects). We start **always-on**; on-demand can be
layered on later.

## Decisions (resolved during brainstorming)

- **Shared-login daemon**, not per-path processes: one SDK login, many concurrent
  RealPlay sessions.
- **Always-on**: every `enable: true` stream runs from startup until shutdown.
- **YAML config** via **PyYAML** (new dependency — accepted; Python has no stdlib
  YAML). Comments are not preserved on rewrite (accepted).
- **`relay stream`** (single ad-hoc) is kept unchanged.
- **`parse`** writes/merges `streams.yml` (replacing the old `streams.txt`).
- No `login_limit` field (YAGNI).

## Command model

- **`relay run [--config streams.yml]`** *(new)* — the daemon. Loads config, logs in
  once, starts every `enable: true` stream, runs until SIGINT/SIGTERM. Defaults to
  `streams.yml` in the working directory. Becomes the default Docker command.
- **`relay parse [--config streams.yml] [--probe-seconds N]`** *(changed)* — probes the
  device and **non-destructively merges** results into `streams.yml`.
- **`relay stream --channel … --stream … --name …`** *(unchanged)* — one-off single
  stream.

## `streams.yml` schema

```yaml
streams:
  - channel: 3
    stream: main          # main | sub | sub2
    name: cam3-main        # RTSP path
    enable: true
    metadata:              # informational, written by parse; ignored by run
      codec: h264
      resolution: 960x576
  - channel: 3
    stream: sub
    name: cam3-sub
    enable: false          # parse could not validate it
```

- Stream identity = `(channel, stream)`.
- `name` and `enable` are user-owned (hand-editable).
- `metadata` is written by `parse`, purely informational; `run` ignores it.
- Top-level is just `streams:` (no `defaults`/`login_limit` this iteration).

## `parse` merge semantics (non-destructive)

Load existing file if present, probe the device, then apply **only** these mutations:

| Situation                          | Action                                              |
|------------------------------------|-----------------------------------------------------|
| New (channel,stream), valid        | add with `enable: true` + `metadata`                |
| New (channel,stream), invalid      | add with `enable: false`                            |
| Existing, now invalid              | set `enable: false`; refresh `metadata`             |
| Existing, valid                    | leave `enable` and `name` untouched; refresh `metadata` only |
| Existing, not probed this run      | leave entirely untouched                            |

Consequences: a user's `enable: false` on a valid stream stays false; renamed
`name`s are preserved. Comments/formatting are lost on rewrite (accepted).

## Architecture / components

### `relay/streams_config.py` (new, pure logic — TDD)

- `StreamEntry` dataclass: `channel: int`, `stream: str`, `name: str`,
  `enable: bool`, `metadata: dict` (optional, e.g. `{codec, resolution}`).
- `load(path) -> list[StreamEntry]` — parse YAML; tolerate a missing file (returns
  empty list).
- `save(path, entries)` — `yaml.safe_dump` in a stable key order.
- `merge(existing, probe_results) -> list[StreamEntry]` — implements the table above.

Kept separate from `config.py` so `config.py` stays focused on device/RTSP config
(AGENTS.md: small, single-purpose modules).

### `relay/sdk_client.py` (edit — multi-RealPlay)

Refactor `DahuaClient` from single-session to **one login, many RealPlay sessions**:

- One `fRealDataCallBackEx2` object, created once and ref-held (GC safety), registered
  for every RealPlay handle.
- `_handlers: dict[int, RawDataHandler]` — the trampoline receives `lRealHandle` as its
  first argument and **dispatches by handle** to the right `on_raw`. No per-stream
  callback objects to manage.
- `start_realplay(channel, play_type, on_raw) -> int` — returns the RealPlay handle.
- `stop_realplay(handle)` — stop one session and drop its handler.
- `cleanup()` — stop all sessions, logout, SDK cleanup.

Update the two existing single-session callers (`probe.py`, `cmd_stream`) to use the
returned handle.

### `relay/manager.py` (new — `StreamManager`)

Orchestrates the daemon:

1. `DahuaClient.init()` + `login()` once.
2. For each `enable: true` entry: create a `StreamPipeline` publishing to
   `cfg.publish_url(entry.name)`, start it, then `start_realplay(...)` routed to that
   pipeline's `on_raw`. Track `(handle, pipeline)` pairs.
3. Wait for shutdown signal.
4. Tear down: stop all pipelines, stop all RealPlay sessions, `cleanup()`.

Each stream remains an independent `StreamPipeline` + ffmpeg, exactly as today; the
manager just runs N of them off one login.

### `relay/probe.py` (edit)

Reuses existing `ProbeResult`. The merge consumes `ProbeResult`s; metadata fields map
to `metadata.codec` / `metadata.resolution`. Uses the new handle-based
`start_realplay`/`stop_realplay`.

### `relay/__main__.py` (edit)

- Add `run` subcommand (`--config`, default `streams.yml`) → build `StreamManager`, run.
- Rewire `parse`: `--config` (default `streams.yml`), `--probe-seconds`; load → probe →
  `merge` → `save`. Drop `streams.txt` output.
- `stream` subcommand unchanged in behavior.

### `relay/mediamtx_config.py` (new — runtime MediaMTX config generator)

Now that PyYAML is available, replace the fragile shell heredoc + manual single-quote
escaping in `entrypoint.sh` with proper YAML generation:

- `build(base: dict, port: int, username: str, password: str) -> dict` — pure function.
  Sets `rtspAddress = f":{port}"`; when **both** creds are non-empty, injects the
  `authInternalUsers` block (publish+read user, plus the localhost `any` user for
  api/metrics/pprof) as native Python data — special characters in creds need no
  escaping. Leaves the base untouched when creds are absent (open access).
- `python -m relay.mediamtx_config --base <path> --out <path>` — CLI wrapper: load base
  YAML, read `TARGET_PORT`/`TARGET_USERNAME`/`TARGET_PASSWORD` from env, `yaml.safe_dump`
  the result.

This makes the auth block testable (pure `build`) and removes the `MTX_RTSPADDRESS` env
indirection (`rtspAddress` is written directly into the generated file).

### Dependency

- `pyproject.toml`: `pyyaml` already added and `uv sync`'d by the maintainer — no action.

## Deploy changes

- `deploy/Dockerfile`: **no change** — keeps `CMD ["parse"]` as the image default.
- `docker-compose.yml`:
  - Set `command: ["run"]` so compose defaults to the multi-camera daemon
    (`run` → `streams.yml`). The single-stream `command: ["stream", …]` invocation is
    kept only as a **commented-out** example.
  - **Mount the config** so it's host-editable without a rebuild:
    `volumes: ["./streams.yml:/app/streams.yml"]` (WORKDIR is `/app`, so the daemon's
    default `streams.yml` resolves here).
  - **Footgun to document:** the host `./streams.yml` must exist before `up`, or Docker
    creates a *directory* at the mount point. Generate it first with `relay parse`
    (or `touch streams.yml`).
- `deploy/entrypoint.sh`: **simplified** — drop the `cp`, the `authInternalUsers` heredoc,
  and the `MTX_RTSPADDRESS` export; replace with a single
  `python -m relay.mediamtx_config --base deploy/mediamtx.yml --out /tmp/mediamtx.runtime.yml`
  call, then start MediaMTX against the generated file. Still forwards `"$@"` to
  `python -m relay`.
- `deploy/mediamtx.yml`: **no change** — MediaMTX auto-creates paths on publish, and the
  existing optional auth block already governs all paths.

## Error handling

- `run` with a missing/empty config: log clearly and exit non-zero (nothing to stream).
- Login failure: surfaced as today (raises, non-zero exit).
- Per-stream ffmpeg death/pipe break: handled by existing `StreamPipeline` restart logic,
  now per stream and independent.
- One stream failing to start RealPlay must not abort the others: log and continue;
  the daemon stays up for the streams that did start.

## Testing

- **TDD (pure):** `streams_config` load/save round-trip; all five `merge` rules
  (add-valid, add-invalid, existing-now-invalid, existing-valid-preserved,
  existing-unprobed-untouched). `mediamtx_config.build` — auth present vs. absent, and
  creds containing YAML-special characters (`'`, `:`, etc.) round-trip safely.
- **Live (per AGENTS.md):** `relay parse` then `relay run` against the device; channel 3
  is the known-good test channel. Verify multiple concurrent RealPlay sessions stream
  simultaneously off one login.
- Existing DHAV/config tests remain unchanged.
