"""Audio formats and conversions for the voice pipeline (TRD §7: MP3/WAV for the web, OGG/Opus
for WhatsApp).

Conversions use PyAV, which bundles FFmpeg's libraries, so neither the host nor the image needs an
`ffmpeg` binary (see D-042). They are CPU work: async code calls them through `asyncio.to_thread`.
"""

import io
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

import av
import av.error

# Formats Sahayak produces.
AudioFormat = Literal["mp3", "wav", "ogg"]
AUDIO_FORMATS: tuple[AudioFormat, ...] = ("mp3", "wav", "ogg")
MIME_TYPES: dict[AudioFormat, str] = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg"}

# Containers accepted from users (browser recordings, WhatsApp voice notes, files), by MIME type.
UPLOAD_MIME_TYPES: dict[str, str] = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "mp4": "audio/mp4",
    "flac": "audio/flac",
    "aac": "audio/aac",
}

# Everything Sahayak encodes is mono at this rate: it suits speech, and MP3 and Opus both support
# it, so encoded sentences can be joined without resampling.
SAMPLE_RATE = 24_000
# MP3 parts are joined by concatenation, so they carry no ID3 tag or Xing header (which would
# land in the middle of the joined file).
_MUXER_OPTIONS: dict[AudioFormat, dict[str, str]] = {
    "mp3": {"id3v2_version": "0", "write_xing": "0"}
}
_ENCODERS: dict[AudioFormat, tuple[str, str, int | None]] = {
    # format: (container, codec, bit rate)
    "mp3": ("mp3", "libmp3lame", 48_000),
    "wav": ("wav", "pcm_s16le", None),
    "ogg": ("ogg", "libopus", 32_000),
}


class AudioError(Exception):
    """Audio could not be decoded or encoded."""


@dataclass(frozen=True)
class Audio:
    data: bytes
    format: AudioFormat

    @property
    def mime(self) -> str:
        return MIME_TYPES[self.format]


def sniff(data: bytes) -> str | None:
    """The container of `data` from its first bytes (a key of `UPLOAD_MIME_TYPES`), or None."""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"\x1a\x45\xdf\xa3":  # EBML: WebM / Matroska
        return "webm"
    if data[4:8] == b"ftyp":
        return "mp4"
    if data[:4] == b"fLaC":
        return "flac"
    if data[:3] == b"ID3":
        return "mp3"
    if len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0:
        # An MPEG audio frame. ADTS (AAC) frames have the layer bits at 00, MP3 never does.
        return "aac" if data[1] & 0x06 == 0 else "mp3"
    return None


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap 16-bit little-endian mono PCM in a WAV header."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm)
    return buffer.getvalue()


def encode(parts: Sequence[bytes], to: AudioFormat) -> bytes:
    """Decode each part (any container FFmpeg reads) and encode them, in order, as one mono
    `to` file at `SAMPLE_RATE`."""
    container_format, codec, bit_rate = _ENCODERS[to]
    buffer = io.BytesIO()
    try:
        with av.open(
            buffer, mode="w", format=container_format, options=_MUXER_OPTIONS.get(to, {})
        ) as output:
            stream = output.add_stream(codec, rate=SAMPLE_RATE, layout="mono")
            if not isinstance(stream, av.AudioStream):  # pragma: no cover - audio codecs only
                raise AudioError(f"{codec} is not an audio codec")
            if bit_rate:
                stream.bit_rate = bit_rate
            target = stream.codec_context.format or "s16"
            samples = 0

            def write(frames: list[av.AudioFrame]) -> None:
                nonlocal samples
                for frame in frames:
                    frame.pts = samples
                    frame.time_base = Fraction(1, SAMPLE_RATE)
                    samples += frame.samples
                    output.mux(stream.encode(frame))

            for part in parts:
                # One resampler per part: parts may come at different rates (another voice).
                resampler = av.AudioResampler(format=target, layout="mono", rate=SAMPLE_RATE)
                with av.open(io.BytesIO(part), mode="r") as source:
                    for frame in source.decode(audio=0):
                        write(resampler.resample(frame))
                write(resampler.resample(None))
            output.mux(stream.encode(None))
    except (av.error.FFmpegError, ValueError) as exc:
        raise AudioError(f"could not convert audio to {to}: {type(exc).__name__}") from exc
    if samples == 0:
        raise AudioError("the audio has no samples")
    return buffer.getvalue()


def join(parts: Sequence[bytes], to: AudioFormat) -> bytes:
    """Join MP3 parts made by `encode(..., "mp3")` into one `to` file.

    MP3 is a sequence of independent frames, so MP3 parts are simply concatenated; other formats
    are re-encoded.
    """
    if not parts:
        raise AudioError("no audio to join")
    if to == "mp3":
        return b"".join(parts)
    return encode(parts, to)


def duration_sec(data: bytes) -> float:
    """Length of the audio in seconds, by decoding it."""
    try:
        with av.open(io.BytesIO(data), mode="r") as source:
            stream = source.streams.audio[0]
            samples = sum(frame.samples for frame in source.decode(stream))
            rate = stream.codec_context.sample_rate or SAMPLE_RATE
    except (av.error.FFmpegError, IndexError, ValueError) as exc:
        raise AudioError(f"could not read audio: {type(exc).__name__}") from exc
    return samples / rate
