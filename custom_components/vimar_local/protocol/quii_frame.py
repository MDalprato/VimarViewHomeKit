"""
QUII / Qualvision media frame helpers (DH-style 20-byte header).

Offline RE of CPacket::FrameGet* / FrameIsAudio / FrameIsVideo in
liblive_player.so. See re/AUDIO_TALK.md.
"""
from __future__ import annotations

import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

FRAME_MAGIC = b"\x00\x00\x01"
FRAME_HDR_LEN = 0x14  # 20

# FrameGetCodec values accepted by FrameIsAudio (type not in {7,8})
AUDIO_CODECS = {0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0C, 0x0D}
# Observed live on Briton BAM-1045-WIFI
CODEC_G711A = 0x04

VIDEO_TYPES = {0, 1, 9, 0x0A, 0x0B}
VIDEO_CODECS = {1, 2, 0x0E} | set(range(0x10, 0x20))


@dataclass(frozen=True)
class QuiiFrame:
    raw: bytes
    frame_type: int
    codec: int
    length: int
    freq: int
    channels: int
    payload: bytes

    @property
    def is_audio(self) -> bool:
        return self.codec in AUDIO_CODECS and self.frame_type not in (7, 8)

    @property
    def is_video(self) -> bool:
        return self.frame_type in VIDEO_TYPES and (
            self.codec in VIDEO_CODECS or 0x10 <= self.codec <= 0x1F
        )


def parse_frame(blob: bytes) -> QuiiFrame | None:
    """Parse one DH/QV frame from decrypted media payload."""
    if len(blob) < FRAME_HDR_LEN or blob[:3] != FRAME_MAGIC:
        return None
    frame_type = blob[3] - 0xE0
    length = struct.unpack_from("<I", blob, 4)[0]
    codec = blob[0x0E]
    channels = blob[0x0F]
    freq = struct.unpack_from("<H", blob, 0x10)[0]
    payload = blob[FRAME_HDR_LEN : FRAME_HDR_LEN + length]
    if len(payload) < length:
        payload = blob[FRAME_HDR_LEN:]
    return QuiiFrame(
        raw=blob,
        frame_type=frame_type,
        codec=codec,
        length=length,
        freq=freq,
        channels=channels,
        payload=payload,
    )


def iter_frames(payloads: Iterable[bytes]) -> Iterable[QuiiFrame]:
    for p in payloads:
        fr = parse_frame(p)
        if fr is not None:
            yield fr


# --- G.711 A-law (ITU-T) -------------------------------------------------

def alaw_to_pcm16(sample: int) -> int:
    a = sample ^ 0x55
    sign = a & 0x80
    exp = (a >> 4) & 0x07
    data = ((a & 0x0F) << 4) + 8
    if exp != 0:
        data += 0x100
    if exp > 1:
        data <<= exp - 1
    return -data if sign else data


def alaw_bytes_to_pcm16le(buf: bytes) -> bytes:
    out = bytearray(len(buf) * 2)
    for i, b in enumerate(buf):
        struct.pack_into("<h", out, i * 2, max(-32768, min(32767, alaw_to_pcm16(b))))
    return bytes(out)


def pcm16_to_alaw_sample(sample: int) -> int:
    """Encode one signed 16-bit PCM sample to G.711 A-law."""
    sample = max(-32768, min(32767, int(sample)))
    sign = 0x80 if sample < 0 else 0
    if sample < 0:
        sample = -sample
    if sample > 32635:
        sample = 32635
    sample += 8
    exp = 7
    for mask in (0x4000, 0x2000, 0x1000, 0x0800, 0x0400, 0x0200, 0x0100):
        if sample & mask:
            break
        exp -= 1
    mant = (sample >> (exp + 3)) & 0x0F if exp else (sample >> 4) & 0x0F
    return (sign | (exp << 4) | mant) ^ 0x55


def pcm16le_to_alaw_bytes(pcm16le: bytes) -> bytes:
    n = len(pcm16le) // 2
    out = bytearray(n)
    for i in range(n):
        (sample,) = struct.unpack_from("<h", pcm16le, i * 2)
        out[i] = pcm16_to_alaw_sample(sample)
    return bytes(out)


def write_wav_mono16(path: Path, pcm16le: bytes, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16le)
