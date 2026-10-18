import math
import struct

import pytest

from sahayak.voice import audio
from sahayak.voice.audio import AudioError, encode, join, pcm_to_wav, sniff


def tone(seconds: float, rate: int = 16_000, hz: float = 440.0) -> bytes:
    """A sine tone as 16-bit mono PCM."""
    count = round(seconds * rate)
    return b"".join(
        struct.pack("<h", round(8000 * math.sin(2 * math.pi * hz * i / rate))) for i in range(count)
    )


WAV = pcm_to_wav(tone(0.5), 16_000)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"RIFF\x00\x00\x00\x00WAVEfmt ", "wav"),
        (b"OggS\x00\x02", "ogg"),
        (b"\x1a\x45\xdf\xa3\x9f", "webm"),
        (b"\x00\x00\x00\x20ftypM4A ", "mp4"),
        (b"fLaC\x00", "flac"),
        (b"ID3\x04\x00", "mp3"),
        (b"\xff\xfb\x90\x00", "mp3"),
        (b"\xff\xf1\x50\x80", "aac"),
        (b"hello", None),
        (b"", None),
    ],
)
def test_sniff(data: bytes, expected: str | None) -> None:
    assert sniff(data) == expected


def test_pcm_to_wav_round_trips() -> None:
    assert sniff(WAV) == "wav"
    assert audio.duration_sec(WAV) == pytest.approx(0.5, abs=0.01)


@pytest.mark.parametrize("to", audio.AUDIO_FORMATS)
def test_encode_resamples_to_one_mono_file(to: audio.AudioFormat) -> None:
    other_rate = pcm_to_wav(tone(0.25, rate=22_050), 22_050)

    encoded = encode([WAV, other_rate], to)

    assert sniff(encoded) == to
    # Codecs pad a little (MP3 frames, Opus pre-skip).
    assert audio.duration_sec(encoded) == pytest.approx(0.75, abs=0.08)


@pytest.mark.parametrize("to", audio.AUDIO_FORMATS)
def test_joined_mp3_parts_decode_as_one(to: audio.AudioFormat) -> None:
    parts = [encode([WAV], "mp3"), encode([pcm_to_wav(tone(1.0), 16_000)], "mp3")]

    joined = join(parts, to)

    assert sniff(joined) == to
    # Each MP3 part carries its encoder's padding (about 50 ms).
    assert audio.duration_sec(joined) == pytest.approx(1.5, abs=0.15)


def test_mp3_parts_carry_no_tags() -> None:
    # A tag or Xing header would land in the middle of a joined file.
    part = encode([WAV], "mp3")
    assert not part.startswith(b"ID3")
    assert b"Xing" not in part
    assert b"Info" not in part[:200]


def test_bad_audio_raises_audio_error() -> None:
    with pytest.raises(AudioError):
        encode([b"not audio at all"], "mp3")
    with pytest.raises(AudioError):
        audio.duration_sec(b"not audio at all")
    with pytest.raises(AudioError):
        join([], "wav")


def test_audio_mime() -> None:
    assert audio.Audio(b"", "ogg").mime == "audio/ogg"
