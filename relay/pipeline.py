"""Wire the SDK raw callback to the ffmpeg DHAV publisher.

The SDK callback thread must return fast, so it only enqueues raw DHAV bytes onto a
bounded queue. A worker thread forwards them to ffmpeg (which demuxes DHAV itself),
restarting ffmpeg if its pipe breaks or the process dies.
"""
import logging
import queue
import threading

from relay.publisher import FfmpegPublisher

log = logging.getLogger(__name__)


class StreamPipeline:
    def __init__(self, rtsp_url: str, max_queue: int = 256, publisher=None):
        self.rtsp_url = rtsp_url
        self._q: "queue.Queue[bytes]" = queue.Queue(maxsize=max_queue)
        self._publisher = publisher or FfmpegPublisher(rtsp_url)
        self._worker = None
        self._stop = threading.Event()
        self._dropped = 0

    # called from the SDK callback thread
    def on_raw(self, data: bytes):
        try:
            self._q.put_nowait(data)
        except queue.Full:
            try:
                self._q.get_nowait()          # drop oldest
                self._q.put_nowait(data)
            except queue.Empty:
                pass
            self._dropped += 1
            if self._dropped % 100 == 1:
                log.warning("queue full; dropped %d chunks", self._dropped)

    def start(self):
        self._publisher.start()
        self._worker = threading.Thread(target=self._run, name="pipeline", daemon=True)
        self._worker.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                data = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            self._forward(data)

    def _forward(self, data):
        # Re-check mid-iteration: once we're stopping, never restart ffmpeg —
        # otherwise a shutdown that kills ffmpeg races the worker into reviving it.
        if self._stop.is_set():
            return
        if not self._publisher.is_alive():
            log.warning("ffmpeg died; restarting")
            self._publisher.start()
        if not self._publisher.write(data):
            log.warning("ffmpeg pipe broken; restarting")
            self._publisher.start()
            self._publisher.write(data)

    def stop(self):
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=5)
        self._publisher.stop()
