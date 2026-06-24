"""Build an M3U8 playlist from (name, url) pairs (pure; no SDK/ffmpeg imports)."""
from typing import List, Tuple


def build_m3u8(items: List[Tuple[str, str]]) -> str:
    """Return an extended M3U8 playlist. Players (VLC etc.) open it to get every
    stream at once, instead of copy-pasting individual URLs."""
    lines = ["#EXTM3U"]
    for name, url in items:
        lines.append(f"#EXTINF:-1,{name}")
        lines.append(url)
    return "\n".join(lines) + "\n"
