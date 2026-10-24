import hashlib
import io
import tarfile
from pathlib import Path

import httpx
import pytest

from sahayak.evals.asr import (
    CLIPS_PATH,
    AsrClip,
    ClipFetchError,
    ClipResult,
    edit_distance,
    fetch_clips,
    lenient,
    load_clips,
    normalize,
    render_report,
    run_asr_eval,
)
from sahayak.voice.stt.base import STTError
from sahayak.voice.stt.fake import FakeSTTProvider, STTCall


def test_the_committed_clip_list() -> None:
    clips = load_clips(CLIPS_PATH)
    assert len(clips) == 50
    assert sum(c.language == "hi" for c in clips) == 25
    assert sum(c.language == "mr" for c in clips) == 25
    assert len({c.reference for c in clips}) == 50  # one recording per sentence


def test_load_clips_rejects_duplicates(tmp_path: Path) -> None:
    line = CLIPS_PATH.read_text(encoding="utf-8").splitlines()[0]
    path = tmp_path / "asr.jsonl"
    path.write_text(f"{line}\n{line}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_clips(path)


def test_normalize_and_lenient() -> None:
    assert normalize("मैंने कहा, “हाँ!” ठीक है।") == "मैंने कहा हाँ ठीक है"
    assert normalize("Hello, WORLD...") == "hello world"
    assert lenient("ज़मीन गाँव") == lenient("जमीन गांव")
    assert normalize("ज़मीन") != normalize("जमीन")


def test_edit_distance() -> None:
    assert edit_distance([], []) == 0
    assert edit_distance(["a", "b", "c"], ["a", "c"]) == 1
    assert edit_distance(["a"], ["x", "y"]) == 2
    assert edit_distance(list("kitten"), list("sitting")) == 3


def _clip(clip_id: str, reference: str, data: bytes, language: str = "hi") -> AsrClip:
    return AsrClip.model_validate(
        {
            "id": clip_id,
            "language": language,
            "source": "fleurs",
            "config": f"{language}_in",
            "split": "dev",
            "file": f"{clip_id}.wav",
            "sha256": hashlib.sha256(data).hexdigest(),
            "reference": reference,
        }
    )


def test_clip_scores() -> None:
    clip = _clip("hi001", "मेरे पास दो एकड़ ज़मीन है।", b"x")
    exact = ClipResult(clip, "मेरे पास दो एकड़ ज़मीन है", None, 10)
    variant = ClipResult(clip, "मेरे पास दो एकड़ जमीन है", None, 10)
    failed = ClipResult(clip, None, None, 10, error="STTError")
    assert exact.wer == 0
    assert variant.wer == pytest.approx(1 / 6)
    assert variant.lenient_word_errors == 0
    assert variant.char_errors == 1
    assert failed.wer == 1


def _archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(f"dev/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_fetch_clips_streams_the_archive_and_keeps_only_listed_files(tmp_path: Path) -> None:
    clips = [_clip("hi001", "एक", b"one"), _clip("hi002", "दो", b"two")]
    archive = _archive({"other.wav": b"zzz", "hi002.wav": b"two", "hi001.wav": b"one"})
    requests: list[str] = []

    def server(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, content=archive)

    client = httpx.Client(transport=httpx.MockTransport(server))
    template = "https://example.org/{config}/{split}.tar.gz"

    assert fetch_clips(clips, tmp_path, client=client, url_template=template) == 2
    assert requests == ["https://example.org/hi_in/dev.tar.gz"]
    assert (tmp_path / "fleurs" / "hi" / "hi001.wav").read_bytes() == b"one"
    assert not (tmp_path / "fleurs" / "hi" / "other.wav").exists()
    # Already there and intact: nothing is downloaded.
    assert fetch_clips(clips, tmp_path, client=client, url_template=template) == 0
    assert len(requests) == 1


def test_fetch_clips_checks_checksums_and_missing_files(tmp_path: Path) -> None:
    template = "https://example.org/{config}/{split}.tar.gz"

    def serving(files: dict[str, bytes]) -> httpx.Client:
        archive = _archive(files)
        return httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=archive))
        )

    clip = _clip("hi001", "एक", b"one")
    with pytest.raises(ClipFetchError, match="checksum"):
        fetch_clips(
            [clip], tmp_path, client=serving({"hi001.wav": b"tampered"}), url_template=template
        )
    with pytest.raises(ClipFetchError, match="not found"):
        fetch_clips([clip], tmp_path, client=serving({"x.wav": b"x"}), url_template=template)


async def test_run_asr_eval_scores_per_language(tmp_path: Path) -> None:
    clips = [
        _clip("hi001", "मेरे पास ज़मीन है", b"a"),
        _clip("mr001", "माझ्याकडे शेती आहे", b"b", language="mr"),
        _clip("mr002", "मी शेतकरी आहे", b"c", language="mr"),
    ]
    for clip in clips:
        clip.path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        clip.path(tmp_path).write_bytes(b"audio " + clip.id.encode())

    def transcribe(call: STTCall) -> str | Exception:
        return {
            b"audio hi001": "मेरे पास जमीन है",
            b"audio mr001": "माझ्याकडे शेती आहे",
        }.get(call.audio, STTError("bad audio"))

    stt = FakeSTTProvider(transcribe)
    seen: list[str] = []
    result = await run_asr_eval(stt, clips, tmp_path, on_result=lambda r: seen.append(r.clip.id))

    assert sorted(seen) == ["hi001", "mr001", "mr002"]
    assert [c.language_hint for c in stt.calls] == ["hi", "mr", "mr"]
    scores = result.by_language()
    assert scores["hi"].wer == pytest.approx(1 / 4)
    assert scores["hi"].lenient_wer == 0
    assert scores["mr"].failures == 1
    assert scores["mr"].wer == pytest.approx(3 / 6)
    metrics = result.metrics()
    assert metrics["clips"] == 3
    assert metrics["by_language"]["mr"]["failures"] == 1
    report = render_report(result, git_sha="abc", dataset=CLIPS_PATH)
    assert "| hi | 1 | 25.0% | 0.0% |" in report
    assert "(failed: STTError: bad audio)" in report

    unhinted = await run_asr_eval(FakeSTTProvider(transcribe), clips[:1], tmp_path, hint=False)
    assert unhinted.hinted is False
