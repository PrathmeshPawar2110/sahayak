"""A small synthetic retrieval corpus for the RAG tests, embedded with the fake embedder.

Schemes cover every filter case: central and state schemes (two states) and every lifecycle
status. Texts are invented; they only need distinctive words.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import Language, Scheme, SchemeChunk, SchemeLevel, SchemeStatus
from sahayak.rag.embeddings.fake import FakeEmbeddingProvider

VERIFIED_AT = datetime(2026, 10, 1, tzinfo=UTC)


@dataclass(frozen=True)
class CorpusScheme:
    id: str
    level: SchemeLevel
    state: str | None
    status: SchemeStatus


SCHEMES = [
    CorpusScheme("farmer-support", SchemeLevel.CENTRAL, None, SchemeStatus.ACTIVE),
    CorpusScheme("worker-pension", SchemeLevel.CENTRAL, None, SchemeStatus.PROVISIONAL),
    CorpusScheme("mh-women", SchemeLevel.STATE, "MH", SchemeStatus.ACTIVE),
    CorpusScheme("ka-housing", SchemeLevel.STATE, "KA", SchemeStatus.ACTIVE),
    CorpusScheme("old-loan", SchemeLevel.CENTRAL, None, SchemeStatus.DISCONTINUED),
    CorpusScheme("not-a-scheme", SchemeLevel.CENTRAL, None, SchemeStatus.ARCHIVED),
]

# (scheme_id, section, language, text)
CHUNKS = [
    (
        "farmer-support",
        "Benefits",
        Language.EN,
        "Farmers receive 6000 rupees income support yearly.",
    ),
    (
        "farmer-support",
        "Eligibility",
        Language.EN,
        "Landholding farmer families with cultivable land.",
    ),
    ("farmer-support", "लाभ", Language.HI, "किसान परिवार को हर साल 6000 रुपये मिलते हैं।"),
    (
        "worker-pension",
        "Benefits",
        Language.EN,
        "Unorganised workers get a monthly pension after 60.",
    ),
    (
        "worker-pension",
        "Contribution",
        Language.EN,
        "Workers contribute monthly; government matches.",
    ),
    (
        "mh-women",
        "Benefits",
        Language.EN,
        "Women receive 1500 rupees monthly support in Maharashtra.",
    ),
    ("mh-women", "लाभ", Language.MR, "महिलांना दरमहा 1500 रुपये मिळतात."),
    ("ka-housing", "Benefits", Language.EN, "Housing assistance for families building a house."),
    ("old-loan", "Benefits", Language.EN, "Farmers received a loan waiver, now closed."),
    ("not-a-scheme", "Notice", Language.EN, "Farmers income support press notice."),
]


def chunk_id(scheme_id: str, text: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{scheme_id}:{text}")


async def load_corpus(session: AsyncSession, embedder: FakeEmbeddingProvider) -> None:
    for scheme in SCHEMES:
        session.add(
            Scheme(
                id=scheme.id,
                name_en=scheme.id.replace("-", " ").title(),
                level=scheme.level,
                state=scheme.state,
                status=scheme.status,
                last_verified_at=VERIFIED_AT,
            )
        )
    await session.flush()
    vectors = await embedder.embed_passages(
        [f"{section}\n{text}" for _, section, _, text in CHUNKS]
    )
    for index, ((scheme_id, section, language, text), vector) in enumerate(
        zip(CHUNKS, vectors, strict=True)
    ):
        session.add(
            SchemeChunk(
                id=chunk_id(scheme_id, text),
                scheme_id=scheme_id,
                source_url=f"https://example.gov.in/{scheme_id}",
                section=section,
                language=language,
                chunk_index=index,
                text=text,
                token_count=len(text.split()),
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
                embedding=vector,
            )
        )
    await session.flush()
