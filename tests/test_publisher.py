import relay  # noqa: F401  (path bootstrap)
from relay.publisher import FfmpegPublisher


def test_ffmpeg_started_in_own_session(monkeypatch):
    """ffmpeg must run in its own session so a SIGINT/SIGTERM to the relay
    process group doesn't kill it (we stop it explicitly instead)."""
    captured = {}

    class FakeProc:
        stdin = None

        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def poll(self):
            return None

    monkeypatch.setattr("relay.publisher.subprocess.Popen", FakeProc)
    pub = FfmpegPublisher("rtsp://x/y")
    pub.start()
    assert captured.get("start_new_session") is True
