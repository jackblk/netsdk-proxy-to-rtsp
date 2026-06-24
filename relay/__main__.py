"""CLI: `python -m relay run|stream|parse ...`."""
import argparse
import logging
import os
import signal
import sys
import threading
import time

import relay  # noqa: F401
from dotenv import load_dotenv

from relay import streams_config
from relay.config import Config, STREAM_TYPES
from relay.manager import StreamManager
from relay.pipeline import StreamPipeline
from relay.playlist import build_m3u8
from relay.probe import probe_streams
from relay.sdk_client import DahuaClient

log = logging.getLogger(__name__)

DEFAULT_CONFIG = "config/streams.yml"


def _playlist_path(config_path: str) -> str:
    """output.m3u8 next to the config file."""
    return os.path.join(os.path.dirname(config_path) or ".", "output.m3u8")


def _serve_one(cfg: Config, channel: int, stream: str, name: str):
    """Log in, stream one channel/stream to RTSP, block until SIGINT/SIGTERM, clean up."""
    pipeline = StreamPipeline(cfg.publish_url(name))
    client = DahuaClient()
    client.init()
    client.login(cfg.host, cfg.port, cfg.username, cfg.password)
    pipeline.start()
    # cleanup() stops all sessions on shutdown, so the handle isn't tracked here.
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


def cmd_serve(cfg: Config, args) -> int:
    entries = streams_config.load(args.config)
    try:
        entry = streams_config.find_enabled(entries, args.name)
    except LookupError as e:
        log.error("serve: %s (in %s)", e, args.config)
        return 1
    _serve_one(cfg, entry.channel, entry.stream, entry.name)
    return 0


def _run_on_demand(cfg: Config, args, entries) -> int:
    """On-demand mode: MediaMTX runOnDemand starts streams on view. Here we only
    write the playlist (all enabled streams) and idle as the foreground process."""
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


def cmd_run(cfg: Config, args) -> int:
    entries = streams_config.load(args.config)
    if not any(e.enable for e in entries):
        log.error("No enabled streams in %s — nothing to do. Run `relay parse` first.",
                  args.config)
        return 1

    if cfg.on_demand:
        return _run_on_demand(cfg, args, entries)

    manager = StreamManager(cfg, entries)
    manager.start()
    log.info("Running %d stream(s) (RTSP auth %s)", manager.active_count,
             "enabled" if cfg.rtsp_auth_enabled else "disabled")

    playlist_path = _playlist_path(args.config)
    parent = os.path.dirname(playlist_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    items = [(e.name, cfg.viewer_url(e.name)) for e in manager.started]
    with open(playlist_path, "w") as f:
        f.write(build_m3u8(items))
    log.info("Playlist written to %s — open it in VLC to view all %d stream(s).",
             playlist_path, len(items))

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    while not stop.is_set():
        time.sleep(0.5)
    log.info("Shutting down...")
    manager.stop()
    return 0


def cmd_parse(cfg: Config, args) -> int:
    client = DahuaClient()
    client.init()
    client.login(cfg.host, cfg.port, cfg.username, cfg.password)
    channels = (range(args.channels) if args.channels
                else range(client.channel_count))
    stream_names = args.streams.split(",")
    pairs = [(ch, st) for ch in channels for st in stream_names]
    log.info("Probing %d stream(s) at concurrency %d...", len(pairs), args.probe_concurrency)
    results = probe_streams(client, pairs, args.probe_seconds, args.probe_concurrency)
    client.cleanup()
    existing = streams_config.load(args.config)
    merged = streams_config.merge(existing, results)
    streams_config.save(args.config, merged)
    enabled = sum(1 for e in merged if e.enable)
    print(f"Wrote {args.config}: {len(merged)} stream(s), {enabled} enabled.")
    return 0


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_dotenv()
    cfg = Config.from_env(os.environ)

    ap = argparse.ArgumentParser(prog="relay")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run all enabled streams from a config file")
    r.add_argument("--config", default=DEFAULT_CONFIG)

    s = sub.add_parser("stream", help="stream one channel/stream to RTSP")
    s.add_argument("--channel", type=int, required=True)
    s.add_argument("--stream", choices=STREAM_TYPES, default="main")
    s.add_argument("--name", required=True, help="RTSP path name")

    sv = sub.add_parser("serve", help="serve one stream by name (used by MediaMTX runOnDemand)")
    sv.add_argument("name", help="RTSP path name (matches a stream in the config)")
    sv.add_argument("--config", default=DEFAULT_CONFIG)

    p = sub.add_parser("parse", help="probe channels/streams -> merge into streams.yml")
    p.add_argument("--config", default=DEFAULT_CONFIG)
    p.add_argument("--streams", default="main,sub")
    p.add_argument("--channels", type=int, default=0, help="0 = use device channel count")
    p.add_argument("--probe-seconds", type=float, default=3.0)
    p.add_argument("--probe-concurrency", type=int, default=4,
                   help="how many streams to probe at once (default 4)")

    args = ap.parse_args()
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


if __name__ == "__main__":
    main()
