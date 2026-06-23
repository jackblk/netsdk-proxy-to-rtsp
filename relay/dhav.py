"""Parse the Dahua DHAV raw stream into Annex-B NAL units and detect codec/resolution.

The parser is a stateful byte-stream consumer: feed() accepts arbitrary chunks
(as delivered by the SDK callback) and yields (codec, nal_bytes) tuples for every
complete NAL unit found. Codec is inferred from NAL header structure; resolution
is parsed from the H.264 SPS / H.265 SPS when first seen.
"""
import logging
from typing import Iterator, Optional, Tuple

log = logging.getLogger(__name__)

DHAV_MAGIC = b"DHAV"


def find_start_code(buf: bytes, start: int) -> Optional[Tuple[int, int]]:
    """Return (index, length) of the next Annex-B start code at/after `start`."""
    i = buf.find(b"\x00\x00\x01", start)
    if i < 0:
        return None
    if i >= 1 and buf[i - 1] == 0:
        return (i - 1, 4)
    return (i, 3)


def nal_type_h264(first_nal_byte: int) -> int:
    return first_nal_byte & 0x1F


def nal_type_h265(first_nal_byte: int) -> int:
    return (first_nal_byte >> 1) & 0x3F


def is_valid_nal_type(codec: str, first_nal_byte: int) -> bool:
    """Reject NALs that can't be real elementary-stream units.

    Dahua's DHAV per-frame header sometimes contains a byte sequence that looks
    like an Annex-B start code, producing a bogus NAL (observed as H.264 type 0).
    Filtering to the valid type ranges drops that garbage without touching real
    video NALs (H.264 SPS/PPS/SEI/slice are all <= 23)."""
    if codec == "h264":
        return 1 <= nal_type_h264(first_nal_byte) <= 23
    if codec == "h265":
        return nal_type_h265(first_nal_byte) <= 40
    return False


class DhavParser:
    def __init__(self):
        self._buf = bytearray()
        self.codec: Optional[str] = None
        self.resolution: Tuple[int, int] = (0, 0)

    def feed(self, data: bytes) -> Iterator[Tuple[str, bytes]]:
        """Append data; yield (codec, annexb_nal) for each complete NAL unit."""
        self._buf.extend(data)
        # Find first DHAV frame; everything inside is Annex-B once we strip headers.
        # Strategy: locate complete DHAV frames using the length field, extract their
        # payload (from first start code to frame end), and split into NAL units.
        while True:
            frame = self._take_frame()
            if frame is None:
                break
            payload = self._payload_of(frame)
            if payload is None:
                continue
            yield from self._emit_nals(payload)

    def _take_frame(self) -> Optional[bytes]:
        """Pop one complete DHAV frame from the buffer, or None if incomplete."""
        idx = self._buf.find(DHAV_MAGIC)
        if idx < 0:
            # No frame start yet; keep tail in case magic is split across reads.
            if len(self._buf) > 3:
                del self._buf[:-3]
            return None
        if idx > 0:
            del self._buf[:idx]  # discard bytes before the frame start
        if len(self._buf) < 16:
            return None
        # Total frame length is a 4-byte little-endian field at offset 12.
        length = int.from_bytes(self._buf[12:16], "little")
        if length < 16 or length > 50_000_000:
            # Bad length; resync past this magic.
            log.warning("DHAV bad length %d, resyncing", length)
            del self._buf[:4]
            return None
        if len(self._buf) < length:
            return None  # wait for the rest
        frame = bytes(self._buf[:length])
        del self._buf[:length]
        return frame

    def _payload_of(self, frame: bytes) -> Optional[bytes]:
        """Payload = bytes from the first Annex-B start code to before the 'dhav' tail."""
        sc = find_start_code(frame, 4)
        if sc is None:
            return None
        start = sc[0]
        end = len(frame)
        tail = frame.rfind(b"dhav")
        if tail > start:
            end = tail
        return frame[start:end]

    def _emit_nals(self, payload: bytes) -> Iterator[Tuple[str, bytes]]:
        pos = 0
        while True:
            sc = find_start_code(payload, pos)
            if sc is None:
                break
            nal_start = sc[0]
            nxt = find_start_code(payload, nal_start + sc[1])
            nal_end = nxt[0] if nxt else len(payload)
            nal = payload[nal_start:nal_end]
            hdr_off = nal_start + sc[1]
            if hdr_off < len(payload):
                self._classify(payload[hdr_off])
                self._maybe_resolution(nal, hdr_off - nal_start)
                if self.codec and is_valid_nal_type(self.codec, payload[hdr_off]):
                    yield (self.codec, nal)
            if nxt is None:
                break
            pos = nxt[0]

    def _classify(self, first_nal_byte: int):
        if self.codec:
            return
        # H.265 NAL: forbidden-zero bit 0, type in bits 1..6; H.264 type in low 5 bits.
        h265_type = nal_type_h265(first_nal_byte)
        h264_type = nal_type_h264(first_nal_byte)
        # VPS(32)/SPS(33)/PPS(34) are H.265-specific; SPS(7)/PPS(8) H.264.
        if h265_type in (32, 33, 34):
            self.codec = "h265"
        elif h264_type in (1, 5, 7, 8):
            self.codec = "h264"

    def _maybe_resolution(self, nal: bytes, hdr_off: int):
        if self.resolution != (0, 0) or not self.codec:
            return
        try:
            if self.codec == "h264" and nal_type_h264(nal[hdr_off]) == 7:
                # Skip the 1-byte H.264 NAL header; SPS RBSP starts at profile_idc.
                self.resolution = _h264_sps_resolution(nal[hdr_off + 1:])
            elif self.codec == "h265" and nal_type_h265(nal[hdr_off]) == 33:
                # Skip the 2-byte H.265 NAL header.
                self.resolution = _h265_sps_resolution(nal[hdr_off + 2:])
        except Exception:
            log.debug("resolution parse failed", exc_info=True)


# --- minimal SPS resolution parsers (Exp-Golomb bit reader) ---
class _BitReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def bit(self) -> int:
        b = (self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1
        self.pos += 1
        return b

    def bits(self, n: int) -> int:
        v = 0
        for _ in range(n):
            v = (v << 1) | self.bit()
        return v

    def ue(self) -> int:
        zeros = 0
        while self.bit() == 0:
            zeros += 1
        return (1 << zeros) - 1 + (self.bits(zeros) if zeros else 0)

    def se(self) -> int:
        k = self.ue()
        return (k + 1) // 2 if k % 2 else -(k // 2)


def _rbsp(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        if i + 2 < len(data) and data[i] == 0 and data[i + 1] == 0 and data[i + 2] == 3:
            out += b"\x00\x00"
            i += 3
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


def _skip_scaling_list(r: "_BitReader", size: int):
    """Consume a scaling list (delta-coded) without retaining it."""
    last_scale, next_scale = 8, 8
    for _ in range(size):
        if next_scale != 0:
            delta = r.se()
            next_scale = (last_scale + delta + 256) % 256
        last_scale = next_scale if next_scale != 0 else last_scale


def _h264_sps_resolution(sps_body: bytes) -> Tuple[int, int]:
    r = _BitReader(_rbsp(sps_body))
    profile = r.bits(8)
    r.bits(8)            # constraint flags + reserved
    r.bits(8)            # level_idc
    r.ue()               # seq_parameter_set_id
    chroma = 1
    if profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        chroma = r.ue()
        if chroma == 3:
            r.bit()       # separate_colour_plane_flag
        r.ue()            # bit_depth_luma_minus8
        r.ue()            # bit_depth_chroma_minus8
        r.bit()           # qpprime_y_zero_transform_bypass_flag
        if r.bit():       # seq_scaling_matrix_present_flag
            for i in range(8 if chroma != 3 else 12):
                if r.bit():   # seq_scaling_list_present_flag[i]
                    _skip_scaling_list(r, 16 if i < 6 else 64)
    r.ue()               # log2_max_frame_num
    poc_type = r.ue()
    if poc_type == 0:
        r.ue()
    elif poc_type == 1:
        r.bit(); r.se(); r.se()
        for _ in range(r.ue()):
            r.se()
    r.ue()               # max_num_ref_frames
    r.bit()              # gaps_in_frame_num
    w_mbs = r.ue() + 1
    h_map = r.ue() + 1
    frame_mbs_only = r.bit()
    if not frame_mbs_only:
        r.bit()
    r.bit()              # direct_8x8
    crop_l = crop_r = crop_t = crop_b = 0
    if r.bit():          # frame_cropping
        crop_l = r.ue(); crop_r = r.ue(); crop_t = r.ue(); crop_b = r.ue()
    width = w_mbs * 16 - (crop_l + crop_r) * 2
    height = (2 - frame_mbs_only) * h_map * 16 - (crop_t + crop_b) * 2
    return (width, height)


def _h265_sps_resolution(sps_body: bytes) -> Tuple[int, int]:
    r = _BitReader(_rbsp(sps_body))
    r.bits(4)            # sps_video_parameter_set_id
    max_sub = r.bits(3)  # sps_max_sub_layers_minus1
    r.bit()              # temporal_id_nesting
    # profile_tier_level
    r.bits(8)            # general_profile_space/tier/idc
    r.bits(32)           # general_profile_compatibility_flags
    r.bits(48)           # constraint flags
    r.bits(8)            # general_level_idc
    sub_profile = [r.bit() for _ in range(max_sub)]
    sub_level = [r.bit() for _ in range(max_sub)]
    if max_sub > 0:
        for _ in range(8 - max_sub):
            r.bits(2)
    for i in range(max_sub):
        if sub_profile[i]:
            r.bits(8); r.bits(32); r.bits(48)
        if sub_level[i]:
            r.bits(8)
    r.ue()               # sps_seq_parameter_set_id
    chroma = r.ue()
    if chroma == 3:
        r.bit()
    width = r.ue()
    height = r.ue()
    return (width, height)
