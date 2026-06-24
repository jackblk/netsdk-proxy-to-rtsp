"""CLI: `python -m relay stream ...` and `python -m relay parse ...`."""
import argparse
import logging
import os
import signal
import threading
import time

import relay  # noqa: F401
from dotenv import load_dotenv

from relay.config import Config, STREAM_TYPES
from relay.pipeline import StreamPipeline
from relay.probe import probe_stream, format_streams_txt
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


def cmd_parse(cfg: Config, args):
    client = DahuaClient()
    client.init()
    client.login(cfg.host, cfg.port, cfg.username, cfg.password)
    channels = (range(args.channels) if args.channels
                else range(client.channel_count))
    streams = args.streams.split(",")
    results = []
    for ch in channels:
        for st in streams:
            log.info("Probing ch%s %s...", ch, st)
            results.append(probe_stream(client, ch, st, args.probe_seconds))
    client.cleanup()
    source = f"{cfg.username}@{cfg.host}:{cfg.port}"
    txt = format_streams_txt(results, source=source)
    with open(args.out, "w") as f:
        f.write(txt)
    print(txt)
    print(f"\nWrote {args.out}")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_dotenv()
    cfg = Config.from_env(os.environ)

    ap = argparse.ArgumentParser(prog="relay")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stream", help="stream one channel/stream to RTSP")
    s.add_argument("--channel", type=int, required=True)
    s.add_argument("--stream", choices=STREAM_TYPES, default="main")
    s.add_argument("--name", required=True, help="RTSP path name")

    p = sub.add_parser("parse", help="probe all channels/streams -> streams.txt")
    p.add_argument("--streams", default="main,sub")
    p.add_argument("--channels", type=int, default=0, help="0 = use device channel count")
    p.add_argument("--probe-seconds", type=float, default=3.0)
    p.add_argument("--out", default="streams.txt")

    args = ap.parse_args()
    if args.cmd == "stream":
        cmd_stream(cfg, args)
    else:
        cmd_parse(cfg, args)


if __name__ == "__main__":
    main()
