# On-Demand Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `ON_DEMAND` deployment mode where MediaMTX's `runOnDemand` spawns a per-stream `relay serve <name>` process only while a viewer is connected, instead of the always-on daemon.

**Architecture:** Global `ON_DEMAND` toggle. When set, `relay.mediamtx_config` emits MediaMTX `runOnDemand` paths (one per enabled stream) pointing at `uv run … relay serve $MTX_PATH`; `cmd_run` skips the always-on manager and just writes the playlist + idles. Each `serve` process does its own NetSDK login, streams one channel, and exits when MediaMTX SIGINTs it.

**Tech Stack:** Python 3.11, uv, PyYAML, pytest, ctypes/NetSDK, ffmpeg `-f dhav -c copy`, MediaMTX.

## Global Constraints

- Run Python through `uv run` (not pip/requirements.txt).
- `streams_config.py`, `mediamtx_config.py`, `playlist.py` stay free of SDK/ffmpeg imports (pure, unit-tested).
- No new dependencies.
- TDD for pure logic; SDK/ffmpeg/end-to-end validated live (channel 3 is known-good).
- Keep `relay/` modules small and single-purpose.
- Stream identity is `(channel, stream)`; the RTSP path is `name`.
- Work on `main` (side project — no feature branch).
- Scratch/experiment scripts go in `tmp/` (git-ignored), not inline heredocs.

---

### Task 1: `Config.on_demand` from `ON_DEMAND` env

**Files:**
- Modify: `relay/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.on_demand: bool` (default `False`); parsed in `Config.from_env` from env key `ON_DEMAND`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
import pytest
from relay.config import Config

BASE = {"HOST": "h", "USERNAME": "u", "PASSWORD": "p"}

@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "Yes", "on"])
def test_on_demand_truthy(val):
    cfg = Config.from_env({**BASE, "ON_DEMAND": val})
    assert cfg.on_demand is True

@pytest.mark.parametrize("val", ["", "0", "false", "no", "off", "nope"])
def test_on_demand_falsy(val):
    cfg = Config.from_env({**BASE, "ON_DEMAND": val})
    assert cfg.on_demand is False

def test_on_demand_unset_defaults_false():
    cfg = Config.from_env(BASE)
    assert cfg.on_demand is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k on_demand -v`
Expected: FAIL (`Config` has no `on_demand` / unexpected keyword).

- [ ] **Step 3: Implement**

In `relay/config.py`, add the field to the dataclass (after `target_password`):

```python
    on_demand: bool = False
```

Add a module-level helper near the top (after `STREAM_TYPES`):

```python
_TRUTHY = {"1", "true", "yes", "on"}


def _is_truthy(val: str) -> bool:
    return (val or "").strip().lower() in _TRUTHY
```

In `from_env(...)`, add to the `cls(...)` call:

```python
            on_demand=_is_truthy(env.get("ON_DEMAND", "")),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all, including the existing `viewer_host` tests).

- [ ] **Step 5: Commit**

```bash
git add relay/config.py tests/test_config.py
git commit -m "feat: add Config.on_demand from ON_DEMAND env"
```

---

### Task 2: Pure `find_enabled(entries, name)` resolver

**Files:**
- Modify: `relay/streams_config.py`
- Test: `tests/test_streams_config.py`

**Interfaces:**
- Consumes: `StreamEntry` (existing: `channel, stream, name, enable, metadata`).
- Produces: `find_enabled(entries: list[StreamEntry], name: str) -> StreamEntry` — returns the enabled entry whose `name` matches; raises `LookupError` if no such name, or if the matching entry is disabled. Used by `cmd_serve` (Task 4).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_streams_config.py`:

```python
import pytest
from relay.streams_config import StreamEntry, find_enabled

def _entries():
    return [
        StreamEntry(channel=3, stream="main", name="cam3-main", enable=True),
        StreamEntry(channel=3, stream="sub", name="cam3-sub", enable=False),
    ]

def test_find_enabled_returns_matching_entry():
    e = find_enabled(_entries(), "cam3-main")
    assert e.channel == 3 and e.stream == "main"

def test_find_enabled_missing_name_raises():
    with pytest.raises(LookupError):
        find_enabled(_entries(), "nope")

def test_find_enabled_disabled_entry_raises():
    with pytest.raises(LookupError):
        find_enabled(_entries(), "cam3-sub")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_streams_config.py -k find_enabled -v`
Expected: FAIL (`cannot import name 'find_enabled'`).

- [ ] **Step 3: Implement**

In `relay/streams_config.py`, append:

```python
def find_enabled(entries: List[StreamEntry], name: str) -> StreamEntry:
    """Return the enabled entry with this name, or raise LookupError."""
    for e in entries:
        if e.name == name:
            if not e.enable:
                raise LookupError(f"stream {name!r} is disabled")
            return e
    raise LookupError(f"no stream named {name!r}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_streams_config.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add relay/streams_config.py tests/test_streams_config.py
git commit -m "feat: add find_enabled resolver to streams_config"
```

---

### Task 3: `mediamtx_config.build()` on-demand paths + CLI wiring

**Files:**
- Modify: `relay/mediamtx_config.py`
- Test: `tests/test_mediamtx_config.py`

**Interfaces:**
- Consumes: `relay.streams_config.load` (existing), `relay.config._is_truthy` (Task 1).
- Produces: `build(base, port, username, password, on_demand_names=None, run_on_demand_cmd=None, close_after="10s", start_timeout="10s") -> dict`. When `on_demand_names` is truthy, the returned dict has `pathDefaults` (with `runOnDemand`, `runOnDemandRestart: True`, `runOnDemandCloseAfter`, `runOnDemandStartTimeout`) and `paths` = `{name: {} for name in on_demand_names}`. When falsy/None, output is unchanged from the pre-on-demand behavior.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mediamtx_config.py`:

```python
from relay.mediamtx_config import build

def test_build_without_on_demand_unchanged():
    cfg = build({}, 8554, "", "")
    assert cfg == {"rtspAddress": ":8554"}
    assert "paths" not in cfg and "pathDefaults" not in cfg

def test_build_on_demand_emits_paths_and_defaults():
    cfg = build({}, 8554, "", "",
                on_demand_names=["cam3-main", "cam3-sub"],
                run_on_demand_cmd="uv run python -m relay serve $MTX_PATH")
    assert cfg["paths"] == {"cam3-main": {}, "cam3-sub": {}}
    pd = cfg["pathDefaults"]
    assert pd["runOnDemand"] == "uv run python -m relay serve $MTX_PATH"
    assert pd["runOnDemandRestart"] is True
    assert pd["runOnDemandCloseAfter"] == "10s"
    assert pd["runOnDemandStartTimeout"] == "10s"

def test_build_on_demand_empty_list_is_noop():
    cfg = build({}, 8554, "", "", on_demand_names=[])
    assert "paths" not in cfg and "pathDefaults" not in cfg

def test_build_on_demand_with_auth_keeps_auth_block():
    cfg = build({}, 8554, "user", "pass",
                on_demand_names=["cam3-main"],
                run_on_demand_cmd="cmd $MTX_PATH")
    assert "authInternalUsers" in cfg
    assert cfg["paths"] == {"cam3-main": {}}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mediamtx_config.py -v`
Expected: FAIL (`build()` got unexpected keyword `on_demand_names`).

- [ ] **Step 3: Implement `build()`**

Replace the `build` function in `relay/mediamtx_config.py` with:

```python
def build(base: dict, port: int, username: str, password: str,
          on_demand_names=None, run_on_demand_cmd=None,
          close_after: str = "10s", start_timeout: str = "10s") -> dict:
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
    if on_demand_names:
        cfg["pathDefaults"] = {
            "runOnDemand": run_on_demand_cmd,
            "runOnDemandRestart": True,
            "runOnDemandCloseAfter": close_after,
            "runOnDemandStartTimeout": start_timeout,
        }
        cfg["paths"] = {name: {} for name in on_demand_names}
    return cfg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_mediamtx_config.py -v`
Expected: PASS (all).

- [ ] **Step 5: Wire the CLI**

Replace the imports + `main()` in `relay/mediamtx_config.py`. At the top, add imports:

```python
import os
import sys

import relay  # noqa: F401  (puts project root on sys.path)
from relay import streams_config
from relay.config import _is_truthy
```

Replace `main()` with:

```python
def _run_on_demand_cmd(streams_config_path: str) -> str:
    """The MediaMTX runOnDemand command: cwd-independent uv invocation of `relay serve`."""
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(relay.__file__)))
    cfg_abs = os.path.abspath(streams_config_path)
    return (f"uv run --frozen --directory {project_dir} "
            f"{os.path.basename(sys.executable)} -m relay serve $MTX_PATH --config {cfg_abs}")


def main():
    ap = argparse.ArgumentParser(prog="relay.mediamtx_config")
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--streams-config", default="config/streams.yml")
    args = ap.parse_args()

    with open(args.base) as f:
        base = yaml.safe_load(f) or {}

    on_demand_names = None
    run_cmd = None
    if _is_truthy(os.environ.get("ON_DEMAND", "")):
        entries = streams_config.load(args.streams_config)
        on_demand_names = [e.name for e in entries if e.enable]
        run_cmd = _run_on_demand_cmd(args.streams_config)

    cfg = build(
        base,
        port=int(os.environ.get("TARGET_PORT") or 8554),
        username=os.environ.get("TARGET_USERNAME") or "",
        password=os.environ.get("TARGET_PASSWORD") or "",
        on_demand_names=on_demand_names,
        run_on_demand_cmd=run_cmd,
    )
    with open(args.out, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    mode = f"on-demand ({len(on_demand_names)} path(s))" if on_demand_names else "always-on"
    print(f"mediamtx_config: wrote {args.out} "
          f"(auth {'enabled' if 'authInternalUsers' in cfg else 'disabled'}, {mode})")
```

Note: `python -m relay serve` resolves via `PYTHONPATH=/app` (Docker) or repo-root cwd
(local); `--directory` pins the project so `uv` finds `pyproject.toml`. Using
`os.path.basename(sys.executable)` (e.g. `python` / `python3.11`) keeps the interpreter name
that `uv run` provides on its managed PATH.

- [ ] **Step 6: Verify CLI manually (both modes)**

Run (always-on, unchanged output):
```bash
uv run python -m relay.mediamtx_config --base deploy/mediamtx.yml --out tmp/mtx-always.yml
```
Expected: prints `… always-on`; `tmp/mtx-always.yml` has no `paths:`/`pathDefaults:`.

Run (on-demand, against an existing `config/streams.yml` with ≥1 enabled stream):
```bash
ON_DEMAND=true uv run python -m relay.mediamtx_config --base deploy/mediamtx.yml --out tmp/mtx-od.yml --streams-config config/streams.yml
```
Expected: prints `… on-demand (N path(s))`; `tmp/mtx-od.yml` has `pathDefaults.runOnDemand` containing `relay serve $MTX_PATH` and a `paths:` entry per enabled stream.

- [ ] **Step 7: Commit**

```bash
git add relay/mediamtx_config.py tests/test_mediamtx_config.py
git commit -m "feat: emit MediaMTX runOnDemand paths when ON_DEMAND is set"
```

---

### Task 4: `relay serve` command + `_serve_one` helper + `cmd_run` on-demand branch

**Files:**
- Modify: `relay/__main__.py`

**Interfaces:**
- Consumes: `Config.on_demand` (Task 1), `streams_config.find_enabled` (Task 2),
  `streams_config.load`, `StreamPipeline`, `DahuaClient`, `STREAM_TYPES`, `build_m3u8`.
- Produces: `serve` subcommand → `cmd_serve(cfg, args)`; refactored `_serve_one(cfg, channel, stream, name)`; `cmd_run` honoring `cfg.on_demand`.

This task is validated live (no pure-logic unit tests — it's SDK/ffmpeg glue). The existing
unit suite must still pass after the edits.

- [ ] **Step 1: Extract `_serve_one` and rewrite `cmd_stream` to use it**

In `relay/__main__.py`, add a helper and reduce `cmd_stream` to a thin wrapper:

```python
def _serve_one(cfg: Config, channel: int, stream: str, name: str):
    """Log in, stream one channel/stream to RTSP, block until SIGINT/SIGTERM, clean up."""
    pipeline = StreamPipeline(cfg.publish_url(name))
    client = DahuaClient()
    client.init()
    client.login(cfg.host, cfg.port, cfg.username, cfg.password)
    pipeline.start()
    client.start_realplay(channel, STREAM_TYPES[stream], pipeline.on_raw)
    log.info("Streaming ch%s %s (RTSP auth %s)", channel, stream,
             "enabled" if cfg.rtsp_auth_enabled else "disabled")
    log.info("Stream ready — view at: %s", cfg.viewer_url(name))

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    while not stop.is_set():
        time.sleep(0.5)
    log.info("Shutting down...")
    pipeline.stop()
    client.cleanup()


def cmd_stream(cfg: Config, args):
    _serve_one(cfg, args.channel, args.stream, args.name)
```

- [ ] **Step 2: Add `cmd_serve`**

Add after `cmd_stream`:

```python
def cmd_serve(cfg: Config, args) -> int:
    entries = streams_config.load(args.config)
    try:
        entry = streams_config.find_enabled(entries, args.name)
    except LookupError as e:
        log.error("serve: %s (in %s)", e, args.config)
        return 1
    _serve_one(cfg, entry.channel, entry.stream, entry.name)
    return 0
```

- [ ] **Step 3: Add the on-demand branch to `cmd_run`**

At the top of `cmd_run`, after loading entries and the "no enabled streams" guard, branch on
`cfg.on_demand` **before** building the manager:

```python
def cmd_run(cfg: Config, args) -> int:
    entries = streams_config.load(args.config)
    if not any(e.enable for e in entries):
        log.error("No enabled streams in %s — nothing to do. Run `relay parse` first.",
                  args.config)
        return 1

    if cfg.on_demand:
        return _run_on_demand(cfg, args, entries)

    manager = StreamManager(cfg, entries)
    # ... unchanged from here (start manager, write playlist from manager.started, idle) ...
```

Add the on-demand helper (writes the playlist for all enabled streams, then idles):

```python
def _run_on_demand(cfg: Config, args, entries) -> int:
    enabled = [e for e in entries if e.enable]
    playlist_path = _playlist_path(args.config)
    parent = os.path.dirname(playlist_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    items = [(e.name, cfg.viewer_url(e.name)) for e in enabled]
    with open(playlist_path, "w") as f:
        f.write(build_m3u8(items))
    log.info("On-demand mode: %d stream(s) will start when first viewed. "
             "Playlist written to %s.", len(enabled), playlist_path)
    for e in enabled:
        log.info("  on-demand %s -> %s", e.name, cfg.viewer_url(e.name))

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    while not stop.is_set():
        time.sleep(0.5)
    log.info("Shutting down...")
    return 0
```

- [ ] **Step 4: Register the `serve` subcommand**

In `main()`, after the `stream` subparser block, add:

```python
    sv = sub.add_parser("serve", help="serve one stream by name (used by MediaMTX runOnDemand)")
    sv.add_argument("name", help="RTSP path name (matches a stream in the config)")
    sv.add_argument("--config", default=DEFAULT_CONFIG)
```

And extend the dispatch in `main()`:

```python
    if args.cmd == "stream":
        cmd_stream(cfg, args)
        rc = 0
    elif args.cmd == "serve":
        rc = cmd_serve(cfg, args)
    elif args.cmd == "run":
        rc = cmd_run(cfg, args)
    else:
        rc = cmd_parse(cfg, args)
    sys.exit(rc)
```

- [ ] **Step 5: Run the full unit suite (no regressions)**

Run: `uv run pytest -q`
Expected: PASS (all existing tests; `cmd_run`/`cmd_stream`/`cmd_serve` are not unit-tested but imports must resolve).

Also smoke-test arg parsing without a device:
```bash
uv run python -m relay serve --help
```
Expected: shows the `serve` usage with positional `name` + `--config`.

- [ ] **Step 6: Commit**

```bash
git add relay/__main__.py
git commit -m "feat: add 'relay serve <name>' + on-demand cmd_run branch"
```

---

### Task 5: Entrypoint `--streams-config` + `uv run` conversion

**Files:**
- Modify: `deploy/entrypoint.sh`

- [ ] **Step 1: Edit the entrypoint**

In `deploy/entrypoint.sh`:

1. Pass the streams config to the generator and run it via `uv run`:

```bash
/app/.venv/bin/python -m relay.mediamtx_config \
  --base /app/deploy/mediamtx.yml --out "$MTX_CONFIG"
```
becomes
```bash
uv run --frozen python -m relay.mediamtx_config \
  --base /app/deploy/mediamtx.yml --out "$MTX_CONFIG" \
  --streams-config /app/config/streams.yml
```

2. Run the proxy via `uv run`:

```bash
/app/.venv/bin/python -m relay "$@" &
```
becomes
```bash
uv run --frozen python -m relay "$@" &
```

(Leave MediaMTX launch, the `sleep 1`, and the `trap` forwarding as-is.)

- [ ] **Step 2: Build the image**

Run: `docker compose build`
Expected: builds clean.

- [ ] **Step 3: Verify always-on still works (regression)**

Ensure `.env` does **not** set `ON_DEMAND` (or sets it falsy). With a valid `config/streams.yml`:
```bash
docker compose up -d
```
Then confirm a stream is live (auth-prefix the URL if RTSP auth is on):
```bash
ffprobe -rtsp_transport tcp -v error -show_streams rtsp://localhost:8554/cam3-main
```
Expected: stream info prints (always-on unchanged).

- [ ] **Step 4: Verify clean shutdown through `uv run`**

Run: `docker compose stop` (note timing) and check it exits promptly, not at the ~10s SIGKILL timeout:
```bash
docker compose logs --tail=20 relay
```
Expected: logs show `Shutting down...` (signal propagated through `uv` to Python). If it was SIGKILL'd, see the Risk note below.

- [ ] **Step 5: Commit**

```bash
git add deploy/entrypoint.sh
git commit -m "chore: entrypoint passes --streams-config and runs via uv"
```

**Risk note (read before Step 4):** if `docker compose stop` hangs to the timeout, `uv` isn't
forwarding `SIGTERM`. Mitigation: prefix the proxy command with `exec`-style signal handling —
launch as `uv run --frozen python -m relay "$@" &` is fine *if* uv forwards; if it does not,
fall back to keeping the proxy on `/app/.venv/bin/python -m relay "$@"` (revert just that one
line) while keeping the `mediamtx_config` call on `uv run`. Record which path was taken in the
commit message.

---

### Task 6: Live end-to-end validation of on-demand

**Files:** none (validation only). Use `tmp/` for any scratch scripts.

- [ ] **Step 1: Enable on-demand**

Set `ON_DEMAND=true` in `.env`. Ensure `config/streams.yml` has channel 3 enabled (run
`docker compose run --rm relay parse` first if needed).

- [ ] **Step 2: Start the stack**

Run: `docker compose up -d`
Then confirm **no** `relay serve` is running yet and the device stream is idle:
```bash
docker compose exec relay ps -ax
```
Expected: a `relay run` (idle, on-demand) process and MediaMTX, but no `relay serve`.

- [ ] **Step 3: Connect a viewer → stream spins up on demand**

Run: `ffprobe -rtsp_transport tcp -v error -show_streams rtsp://localhost:8554/cam3-main`
Expected: stream info prints within the start timeout (~a few seconds incl. login).
Concurrently/after, confirm a `relay serve cam3-main` process now exists:
```bash
docker compose exec relay ps -ax
```

- [ ] **Step 4: Disconnect → stream tears down ~10s later**

After ffprobe exits (no viewers), wait ~15s, then:
```bash
docker compose exec relay ps -ax
```
Expected: the `relay serve cam3-main` process is gone (MediaMTX SIGINT'd it after
`runOnDemandCloseAfter`).

- [ ] **Step 5: Reconnect → re-spawns**

Re-run the Step 3 ffprobe.
Expected: stream comes back up (a fresh `relay serve` spawns).

- [ ] **Step 6: Tear down**

```bash
docker compose down
```

No commit (validation only). If any step fails, fix the relevant task's code, re-commit there,
and re-run this task.

---

### Task 7: Documentation

**Files:**
- Modify: `README.md`, `.env.example`, `config/README.md`, `AGENTS.md`

- [ ] **Step 1: `.env.example`**

Add after the `TARGET_PASSWORD` block:

```bash
# Optional — set to true to run in ON-DEMAND mode: each enabled stream is pulled
# from the device only while a viewer is connected (MediaMTX runOnDemand), and torn
# down ~10s after the last viewer leaves. Unset/false = always-on (every enabled
# stream runs continuously).
ON_DEMAND=
```

- [ ] **Step 2: `README.md` — Modes section**

Add a `serve` bullet and an on-demand note to the "Modes" section:

```markdown
- `serve <name> [--config config/streams.yml]` — serves a single stream by name
  (resolved from the config). Invoked by MediaMTX's `runOnDemand`; not usually run by hand.
```

And under `run`, note the toggle:

```markdown
  Set `ON_DEMAND=true` to switch to **on-demand** mode: streams are not started up
  front; instead each enabled stream is pulled from the device only while a viewer is
  connected and is torn down ~10s after the last viewer disconnects. `output.m3u8`
  still lists every enabled stream (opening one triggers it).
```

- [ ] **Step 3: `README.md` — local dev note for on-demand**

In the "Local (dev)" section, note that on-demand requires the generated MediaMTX config (so
`runOnDemand` paths exist):

```markdown
For on-demand mode locally, generate the MediaMTX config so the runOnDemand paths exist:
`ON_DEMAND=true uv run python -m relay.mediamtx_config --base deploy/mediamtx.yml --out /tmp/mtx.yml --streams-config config/streams.yml`,
start `mediamtx /tmp/mtx.yml` from the repo root, then `ON_DEMAND=true uv run python -m relay run`.
```

- [ ] **Step 4: `config/README.md`**

Add a short paragraph noting `ON_DEMAND=true` makes the enabled streams lazy (started on first
view) rather than always-on.

- [ ] **Step 5: `AGENTS.md`**

- In the `__main__.py` bullet, add `serve` to the CLI subcommands list.
- In the `mediamtx_config.py` bullet, note it emits `runOnDemand` paths when `ON_DEMAND` is set.
- In the Config paragraph (`.env`), document `ON_DEMAND` (global on-demand toggle).
- Add a gotcha bullet:

```markdown
- **On-demand mode** (`ON_DEMAND=true`): MediaMTX `runOnDemand` spawns `relay serve <name>`
  per stream on first view (own login) and SIGINTs it ~10s after the last viewer. `cmd_run`
  then only writes `output.m3u8` and idles — MediaMTX is the engine. Always-on is the default.
```

- [ ] **Step 6: Commit**

```bash
git add README.md .env.example config/README.md AGENTS.md
git commit -m "docs: document ON_DEMAND on-demand streaming mode"
```

---

## Self-Review

- **Spec coverage:** Config toggle (Task 1), `find_enabled` (Task 2), `build()` + CLI runOnDemand (Task 3), `serve`/`_serve_one`/`cmd_run` branch (Task 4), entrypoint `--streams-config` + `uv run` (Task 5), live validation incl. clean-shutdown caveat (Tasks 5–6), docs (Task 7). All spec sections mapped.
- **Type consistency:** `build(... on_demand_names, run_on_demand_cmd, close_after, start_timeout)` used identically in Task 3 tests, impl, and CLI. `find_enabled(entries, name) -> StreamEntry` raising `LookupError` matches its use in `cmd_serve`. `Config.on_demand: bool` + `_is_truthy` reused by `mediamtx_config` CLI. `serve` positional `name` matches `cmd_serve`/`runOnDemand` `$MTX_PATH`.
- **No placeholders:** every code step shows complete code; commands have expected output.
