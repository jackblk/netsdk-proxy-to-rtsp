# Open problem: on-demand + VLC playlist cascade

**Date:** 2026-06-24
**Status:** Unresolved — design decision pending. On-demand mode itself is shipped and working.

## Symptom

In on-demand mode (`ON_DEMAND=true`), opening the combined `config/output.m3u8` in VLC is
destructive. The playlist lists every enabled stream:

```
#EXTM3U
#EXTINF:-1,cam2-main
rtsp://.../cam2-main
#EXTINF:-1,cam2-sub
rtsp://.../cam2-sub
...
```

When VLC connects to `cam2-main`, MediaMTX's `runOnDemand` starts the stream, but the
**cold start is slower than VLC's RTSP connect timeout**, so VLC gives up and **advances to
the next playlist entry** (`cam2-sub`). That spawns `cam2-sub`'s stream, and `cam2-main` is
torn down ~10s later (no viewers). Net effect: VLC cascades down the list and you can't
reliably land on the stream you wanted. Current manual workaround: keep clicking `cam2-main`.

## Root cause

Cold start to first decodable frame is the sum of:

```
uv run startup  +  NetSDK InitEx+Login  +  RealPlayEx  +  ffmpeg spawn/connect  +  wait for first IDR keyframe
   (~0.3–1s)          (~1–2s)              (fast)          (fast)                   (up to one GOP: ~1–4s)
```

MediaMTX *does* hold the viewer connection during startup (`runOnDemandStartTimeout`, default
10s), so the server side is fine — the problem is **VLC's client-side timeout is shorter than
this total**, and the combined playlist turns a slow connect into a destructive auto-advance.

The ~1–2s **login is in the cold path because we chose per-stream login (Option A)**: each
`runOnDemand` spawns an independent `relay serve` that logs in fresh. See the design spec:
[2026-06-24-on-demand-streaming-design.md](../specs/2026-06-24-on-demand-streaming-design.md).

## Dead end: placeholder stream

Considered having MediaMTX (`fallback`) or `relay serve` publish a "connecting…" placeholder
so VLC connects instantly, then swap to the real feed. **Not viable** because:

1. **One publisher per path** — placeholder and real `relay serve` can't both publish to the
   same path; sequencing them creates a publisher *gap* that itself triggers VLC's advance.
2. **No hot-swap** — placeholder SPS/PPS/resolution won't match the device's, so at the cut
   the decoder must re-init (stall / dropped session), not a clean transition.
3. **`-c copy` can't splice** — making placeholder→real one continuous stream requires
   transcoding the real feed too, which kills the project's no-transcode / low-CPU premise.
   (Even matching `metadata` resolution isn't enough — the exact SPS bitstream still differs.)
4. MediaMTX `fallback` is a **redirect**: VLC gets bounced to the placeholder and stays there;
   it does not auto-return when the real source appears.

A placeholder only pays off if the goal is literally an on-screen "loading" card, and that's a
transcode trade against the core design.

## Candidate fixes (smallest → biggest)

1. **Single-entry playlists** *(recommended first step)* — write one `cam2-main.m3u8` per
   stream (alongside/instead of the combined `output.m3u8`). VLC opening a one-entry playlist
   has nothing to advance to, so a slow connect just *waits* on that path instead of tearing
   it down for a sibling. No transcode, no glitch, no gap. Directly removes the *destructive*
   behavior even if cold start stays slow. (Pure logic in `playlist.py` + `cmd_run` /
   `_run_on_demand` write step — TDD-able.)
2. **Lower device GOP / I-frame interval** (device-side config, e.g. main-stream ~1s) — shrinks
   the keyframe wait, often the biggest/most variable chunk. No code; document as the
   recommended on-demand device setting.
3. **Drop `uv run` on the `runOnDemand` path only** — use the direct venv interpreter
   (`sys.executable`) for that one latency-critical command; entrypoint stays on `uv run`.
   Saves the per-spawn uv resolve.
4. **Warm-login hybrid (revisit Option B)** — the deepest fix: a shared-login daemon + control
   channel so login is out of the cold path. Removes the ~1–2s fixed cost but reintroduces the
   complexity (control server, dynamic pipeline state) we deliberately skipped in the spec.

## Decide-the-right-lever measurement (run first)

Before committing to lever 4, measure the actual cold start (needs the live device):

```bash
# shell A — start one stream manually and time it:
docker compose exec relay sh -c 'cd /app && time uv run --frozen python -m relay serve cam2-main --config /app/config/streams.yml'
# shell B — as soon as A launches, time until first frames (≈ what VLC must wait):
time ffprobe -rtsp_transport tcp -v error -show_entries stream=codec_type -of csv rtsp://bb:121124@localhost:8100/cam2-main
```

The `relay serve` log timestamps (`Logged in` vs `RealPlay started` vs first frame) reveal
whether the dominant cost is **login** (→ warm-login matters, lever 4) or **keyframe wait**
(→ lever 2 matters). The `ffprobe` wall-time ≈ VLC's required patience.

## Recommendation

Do **lever 1 (single-entry playlists)** as the immediate fix — it kills the destructive
cascade cheaply and safely. Then run the measurement to decide whether the residual cold-start
wait justifies **lever 4 (warm-login)** or just **lever 2 (device GOP)**.

## Context for a fresh session

- On-demand mode is implemented and merged (commits around 2026-06-24). Plan:
  [docs/superpowers/plans/2026-06-24-on-demand-streaming.md](../plans/2026-06-24-on-demand-streaming.md).
- Playlist generation is the pure `build_m3u8()` in [relay/playlist.py](../../../relay/playlist.py),
  written by `cmd_run` / `_run_on_demand` in [relay/__main__.py](../../../relay/__main__.py).
- A separate shutdown-race bug (ffmpeg restarted during teardown) was fixed the same day
  (`start_new_session=True` + `_stop` re-check in [relay/pipeline.py](../../../relay/pipeline.py)).
