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
        self._sessions = []  # list[(handle, StreamPipeline, StreamEntry)]

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    @property
    def started(self) -> List[StreamEntry]:
        """The entries whose RealPlay session started successfully."""
        return [entry for _, _, entry in self._sessions]

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
            self._sessions.append((handle, pipeline, e))
            log.info("streaming ch%s %s -> %s", e.channel, e.stream,
                     self.cfg.viewer_url(e.name))

    def stop(self):
        for handle, pipeline, _ in self._sessions:
            self._client.stop_realplay(handle)
            pipeline.stop()
        self._sessions.clear()
        self._client.cleanup()
