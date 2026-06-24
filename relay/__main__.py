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
from relay.probe import probe_streams
from relay.sdk_client import DahuaClient

log = logging.getLogger(__name__)


def cmd_stream(cfg: Config, args):
    pipeline = StreamPipeline(cfg.publish_url(args.name))
    client = DahuaClient()
    client.init()
    client.login(cfg.host, cfg.port, cfg.username, cfg.password)
    pipeline.start()
    client.start_realplay(args.channel, STREAM_TYPES[args.stream], pipeline.on_raw)
    # cleanup() stops all sessions on shutdown, so the handle isn't tracked here.
    log.info("Streaming ch%s %s (RTSP auth %s)", args.channel, args.stream,
             "enabled" if cfg.rtsp_auth_enabled else "disabled")
    log.info("Stream ready — view at: %s", cfg.viewer_url(args.name))

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    while not stop.is_set():
        time.sleep(0.5)
    log.info("Shutting down...")
    pipeline.stop()
    client.cleanup()


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
    r.add_argument("--config", default="streams.yml")

    s = sub.add_parser("stream", help="stream one channel/stream to RTSP")
    s.add_argument("--channel", type=int, required=True)
    s.add_argument("--stream", choices=STREAM_TYPES, default="main")
    s.add_argument("--name", required=True, help="RTSP path name")

    p = sub.add_parser("parse", help="probe channels/streams -> merge into streams.yml")
    p.add_argument("--config", default="streams.yml")
    p.add_argument("--streams", default="main,sub")
    p.add_argument("--channels", type=int, default=0, help="0 = use device channel count")
    p.add_argument("--probe-seconds", type=float, default=3.0)
    p.add_argument("--probe-concurrency", type=int, default=4,
                   help="how many streams to probe at once (default 4)")

    args = ap.parse_args()
    if args.cmd == "stream":
        cmd_stream(cfg, args)
        rc = 0
    elif args.cmd == "run":
        rc = cmd_run(cfg, args)
    else:
        rc = cmd_parse(cfg, args)
    sys.exit(rc)


if __name__ == "__main__":
    main()
